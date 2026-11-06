# -*- coding: utf-8 -*-
"""
💬 DIGIANTI — Auto-Reply Engine (پاسخ‌دهی خودکار کلیدواژه‌ای)
════════════════════════════════════════════════════════════════════════════
نسخه‌ی حرفه‌ای‌شده‌ی «پاسخ خودکار». قبلاً فقط یه دیکشنری ثابت توی config.py
بود که با هر substring توی پیام match می‌شد (مثلاً کلیدواژه‌ی «سلام» حتی
توی کلمه‌ی «سلامتی» هم trigger می‌شد) و هیچ گروهی نمی‌تونست خودش عوضش کنه.

این فایل دقیقاً از همون الگوی lock_engine.py / moderation_engine.py پیروی
می‌کنه (ذخیره‌سازی مستقل، import تنبل bot به اسم host) و این قابلیت‌ها رو
اضافه می‌کنه، بدون این‌که رفتار قبلی رو خراب کنه:

  • هر گروه کلیدواژه‌های خودشو داره — مستقل و قابل‌مدیریت با ۳ دستور ساده
  • اگه گروهی دست نزده باشه، همون AUTO_REPLIES پیش‌فرضِ config.py فعاله
    (سازگاریِ کامل با رفتار قبلی، بدون نیاز به هیچ تنظیم اضافه)
  • هر کلیدواژه می‌تونه چند پاسخ داشته باشه → هر بار یکی تصادفی انتخاب می‌شه
    (پاسخ‌ها یکنواخت و ربات‌وار به نظر نمی‌رسن)
  • تطبیق «کلمه‌ی کامل» برای کلیدواژه‌های تک‌کلمه‌ای، تا دیگه وسط یه کلمه‌ی
    دیگه اشتباهی trigger نشه
  • کول‌داون کوتاه روی هر کلیدواژه (جلوی اسپم‌شدنِ خودِ پاسخ خودکار رو
    توی پیام‌های پشت‌سرهم می‌گیره)
  • سقف تعداد کلیدواژه و تعداد پاسخِ هر کلیدواژه (جلوگیری از شلوغ‌کاری)

روشن/خاموش بودنِ کل قابلیت همچنان دست همون سوییچ قبلیه:
get_setting(chat_id, "keyword_reply") — این فایل هیچ منطق جدیدی برای
روشن/خاموش کردن نمی‌سازه، فقط لایه‌ی «کدوم کلیدواژه‌ها» رو حرفه‌ای می‌کنه.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import shutil
import time
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

import config

logger = logging.getLogger("autoreply_engine")

STATE_FILE = getattr(config, "AUTOREPLY_ENGINE_STATE_FILE", "autoreply_engine_state.json")

MAX_TRIGGERS_PER_CHAT = getattr(config, "AUTOREPLY_MAX_TRIGGERS", 60)
MAX_REPLIES_PER_TRIGGER = getattr(config, "AUTOREPLY_MAX_VARIANTS", 5)
COOLDOWN_SECONDS = getattr(config, "AUTOREPLY_COOLDOWN_SECONDS", 12)

# _TRIGGERS[chat_id][keyword_lower] = [پاسخ۱, پاسخ۲, ...]
_TRIGGERS: dict[int, dict[str, list]] = defaultdict(dict)
_SEEDED: set[int] = set()

# کول‌داون فقط توی حافظه‌ست (نیازی به ماندگاری نداره؛ یه ری‌استارت فقط یعنی
# یه بار دیگه زودتر از موعد جواب می‌ده، چیز مهمی نیست)
_LAST_FIRE: dict[tuple, float] = {}

_save_lock = asyncio.Lock()
_WORD_RE = re.compile(r"[\w؀-ۿ]+")


def _host():
    import bot as host
    return host


def _ensure_seed(chat_id: int):
    """اولین باری که یه گروه استفاده می‌شه، کلیدواژه‌های پیش‌فرضِ config.py
    توش کپی می‌شه تا رفتار قبلی حفظ بشه؛ بعدش هر گروه مسیر خودشو می‌ره."""
    if chat_id in _SEEDED:
        return
    _SEEDED.add(chat_id)
    if not _TRIGGERS[chat_id]:
        for kw, reply in getattr(config, "AUTO_REPLIES", {}).items():
            _TRIGGERS[chat_id][kw.strip().lower()] = [reply]


def _tokenize(text: str) -> set:
    return set(_WORD_RE.findall(text.lower()))


def find_reply(chat_id: int, text: str) -> str | None:
    """اگه پیام با یکی از کلیدواژه‌های فعالِ این گروه match بشه (و کول‌داونش
    تموم شده باشه)، یه پاسخ (تصادفی از بین چندتا) برمی‌گردونه؛ وگرنه None."""
    _ensure_seed(chat_id)
    triggers = _TRIGGERS.get(chat_id) or {}
    if not triggers:
        return None
    lowered = text.lower()
    tokens = _tokenize(text)
    now = time.time()
    for keyword, replies in triggers.items():
        if not replies:
            continue
        is_single_word = " " not in keyword
        matched = (keyword in tokens) if is_single_word else (keyword in lowered)
        if not matched:
            continue
        cd_key = (chat_id, keyword)
        if now - _LAST_FIRE.get(cd_key, 0.0) < COOLDOWN_SECONDS:
            continue
        _LAST_FIRE[cd_key] = now
        return random.choice(replies)
    return None


# ---------------------------------------------------------------------------
# 🧰 مدیریت کلیدواژه‌ها (API سطح‌بالا)
# ---------------------------------------------------------------------------

def add_reply(chat_id: int, keyword: str, reply: str) -> int:
    """یه پاسخ به یه کلیدواژه اضافه می‌کنه (اگه کلیدواژه جدید باشه، می‌سازدش).
    خروجی: تعداد پاسخ‌هایی که الان این کلیدواژه داره."""
    _ensure_seed(chat_id)
    keyword = keyword.strip().lower()
    bucket = _TRIGGERS[chat_id].setdefault(keyword, [])
    bucket.append(reply.strip())
    return len(bucket)


def remove_trigger(chat_id: int, keyword: str) -> bool:
    _ensure_seed(chat_id)
    keyword = keyword.strip().lower()
    return _TRIGGERS[chat_id].pop(keyword, None) is not None


def list_triggers(chat_id: int) -> dict:
    _ensure_seed(chat_id)
    return dict(_TRIGGERS.get(chat_id, {}))


def trigger_count(chat_id: int) -> int:
    return len(list_triggers(chat_id))


# ---------------------------------------------------------------------------
# 📟 دستورات متنی ادمین
# ---------------------------------------------------------------------------

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """افزودن پاسخ خودکار [کلیدواژه] [متن پاسخ]"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not host.has_permission(update.effective_user.id, "menu"):
        await message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    args = context.args or []
    if len(args) < 2:
        await message.reply_text(
            "📝 استفاده: «افزودن پاسخ خودکار [کلیدواژه] [متن پاسخ]»\n"
            "مثال: افزودن پاسخ خودکار سلام سلام! خوش اومدی 👋\n\n"
            "نکته: اگه برای یه کلیدواژه چندبار این دستور رو بزنی، همه‌ی پاسخ‌ها "
            "ذخیره می‌شن و هر بار یکی‌شون تصادفی فرستاده می‌شه."
        )
        return

    keyword, reply = args[0].strip(), " ".join(args[1:]).strip()
    if not keyword or not reply:
        await message.reply_text("❌ هم کلیدواژه هم متن پاسخ لازمه.")
        return

    triggers = list_triggers(chat.id)
    existing = triggers.get(keyword.lower(), [])
    if len(existing) >= MAX_REPLIES_PER_TRIGGER:
        await message.reply_text(f"❌ کلیدواژه‌ی «{keyword}» همین الان {MAX_REPLIES_PER_TRIGGER} پاسخ داره (سقف مجاز).")
        return
    if keyword.lower() not in triggers and len(triggers) >= MAX_TRIGGERS_PER_CHAT:
        await message.reply_text(f"❌ تعداد کلیدواژه‌های این گروه به سقف ({MAX_TRIGGERS_PER_CHAT} مورد) رسیده.")
        return

    count = add_reply(chat.id, keyword, reply)
    await message.reply_text(
        f"✅ پاسخ خودکار برای «{keyword}» ثبت شد (الان {count} نوع پاسخ داره).\n"
        f"فعال‌سازیِ کلیِ پاسخ خودکار: ‎/menu → «💬 پاسخ‌دهی»"
    )
    await host.save_state()


async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف پاسخ خودکار [کلیدواژه]"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not host.has_permission(update.effective_user.id, "menu"):
        await message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    args = context.args or []
    if not args:
        await message.reply_text("📝 استفاده: «حذف پاسخ خودکار [کلیدواژه]»")
        return

    keyword = " ".join(args).strip()
    if remove_trigger(chat.id, keyword):
        await message.reply_text(f"🗑 کلیدواژه‌ی «{keyword}» و همه‌ی پاسخ‌هاش حذف شد.")
        await host.save_state()
    else:
        await message.reply_text("همچین کلیدواژه‌ای پیدا نشد. با «لیست پاسخ خودکار» لیست رو ببین.")


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لیست پاسخ خودکار"""
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return

    host = _host()
    enabled = host.get_setting(chat.id, "keyword_reply")
    triggers = list_triggers(chat.id)
    status = "✅ روشن" if enabled else "❌ خاموش (دست ادمینه، از ‎/menu)"
    lines = [f"💬 پاسخ‌دهی خودکار — وضعیت: {status}", ""]
    if not triggers:
        lines.append("هنوز هیچ کلیدواژه‌ای تعریف نشده.")
    else:
        for keyword, replies in triggers.items():
            lines.append(f"🔑 «{keyword}» — {len(replies)} پاسخ")
    lines.append("")
    lines.append(f"📊 {len(triggers)}/{MAX_TRIGGERS_PER_CHAT} کلیدواژه")
    lines.append("➕ افزودن: «افزودن پاسخ خودکار [کلیدواژه] [پاسخ]»")
    lines.append("🗑 حذف: «حذف پاسخ خودکار [کلیدواژه]»")
    await message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# 💾 Persistence (دقیقاً هم‌الگو با بقیه‌ی موتورها)
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {str(c): dict(kw_map) for c, kw_map in _TRIGGERS.items()}


def _write_state_file(data: dict):
    tmp = STATE_FILE + ".tmp"
    if os.path.exists(STATE_FILE):
        try:
            shutil.copy2(STATE_FILE, STATE_FILE + ".bak")
        except Exception as e:
            logger.warning(f"بکاپ گرفتن از state ناموفق بود: {e}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_FILE)


async def save_state():
    async with _save_lock:
        try:
            await asyncio.to_thread(_write_state_file, _collect_state())
        except Exception as e:
            logger.warning(f"ذخیره‌ی state موتور پاسخ خودکار ناموفق بود: {e}")


def load_state():
    data = None
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"خوندن state موتور پاسخ خودکار ناموفق بود: {e}؛ تلاش برای بازیابی از بکاپ...")
            data = None
    if data is None:
        bak = STATE_FILE + ".bak"
        if os.path.exists(bak):
            try:
                with open(bak, encoding="utf-8") as f:
                    data = json.load(f)
                logger.warning("state اصلی خراب/نامعتبر بود؛ از بکاپ بازیابی شد.")
            except Exception as e:
                logger.warning(f"خوندن بکاپ هم ناموفق بود: {e}")
                return
        else:
            return
    for cid, kw_map in data.items():
        _TRIGGERS[int(cid)] = dict(kw_map)
        _SEEDED.add(int(cid))  # گروهی که state داره یعنی قبلاً seed شده (یا عمداً خالیه)
    logger.info("وضعیت موتور پاسخ خودکار از فایل بارگذاری شد.")
