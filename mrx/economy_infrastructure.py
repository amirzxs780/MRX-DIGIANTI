# -*- coding: utf-8 -*-
"""
DIGNANTI ECONOMY INFRASTRUCTURE — فاز ۶ (V18: شهرسازی و زیرساختِ اقتصادی)
=====================================================================
بخشِ ۷ (شهرسازی و زیرساختِ اقتصادی). economy_city.py از قبل هست ولی شهرِ
*شخصیِ* هرکاربره (هرکس شهرِ خودشو می‌سازه). این ماژول چیزِ دیگه‌ایه: یه
زیرساختِ **مشترکِ کلِ گروه** که همه‌ی اعضا می‌تونن توش مشارکتِ مالی کنن -
دقیقاً مفهومِ «نیروگاه/جاده/بیمارستان/مدرسه» ایِ بخشِ ۷ که متعلق به یه نفر
نیست، متعلق به کلِ گروهه.

مدلِ مالی (صادقانه، طبقِ وضعیتِ فعلیِ پروژه): چون هنوز خزانه‌ی عمومی/سیستمِ
دولت (بخشِ ۱۰) پیاده نشده، کمک‌هایِ مالی مستقیم مصرف می‌شن (Sink واقعی -
دقیقاً مثلِ خریدِ موجودیِ یه کسب‌وکار یا هزینه‌ی نگهداریِ ملک؛ پول واقعاً از
اقتصاد خارج می‌شه، نه به حساب کسی می‌ره). وقتی بخشِ ۱۰ ساخته بشه، این ماژول
باید به‌جایِ remove_coinsِ مستقیم، از خزانه‌ی عمومی تأمین بشه.

هر زیرساخت یه Level داره (۰ تا INFRASTRUCTURE_MAX_LEVEL)؛ مجموعِ مشارکت‌ها
جمع می‌شه و هر وقت به هزینه‌ی Levelِ بعدی برسه، خودکار ارتقا پیدا می‌کنه
(Crowd-Funding واقعی، نه فقط یه نفر هزینه‌ی کامل رو بده).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

import config
import economy_core

logger = logging.getLogger("economy_infrastructure")

STATE_FILE = getattr(config, "ECONOMY_INFRASTRUCTURE_STATE_FILE", "economy_infrastructure_state.json")
STATE_VERSION = 1
TYPES: dict = getattr(config, "INFRASTRUCTURE_TYPES", {})

_save_lock = asyncio.Lock()


class InfrastructureError(Exception):
    pass


def _host():
    import bot as host
    return host


# ---------------------------------------------------------------------------
# 💾 State
# ---------------------------------------------------------------------------

# chat_id -> key -> {"level": int, "progress": int, "total_invested": int,
#                     "contributors": {uid(str): amount}}
_infra: dict[int, dict] = defaultdict(dict)


def _entry(chat_id: int, key: str) -> dict:
    d = _infra[chat_id]
    if key not in d:
        d[key] = {"level": 0, "progress": 0, "total_invested": 0, "contributors": {}}
    return d[key]


def next_level_cost(key: str, current_level: int) -> int:
    info = TYPES[key]
    return round(info["base_cost"] * (1 + info.get("cost_growth", 0.5) * current_level))


def get_entry(chat_id: int, key: str) -> dict | None:
    if key not in TYPES:
        return None
    return _entry(chat_id, key)


def list_all(chat_id: int) -> list[dict]:
    out = []
    for key, info in TYPES.items():
        e = _entry(chat_id, key)
        max_level = getattr(config, "INFRASTRUCTURE_MAX_LEVEL", 10)
        maxed = e["level"] >= max_level
        out.append({
            "key": key, "name": info["name"], "level": e["level"],
            "progress": e["progress"],
            "cost_for_next": None if maxed else next_level_cost(key, e["level"]),
            "total_invested": e["total_invested"], "maxed": maxed,
        })
    return out


async def contribute(chat_id: int, uid: int, key: str, amount: int) -> dict:
    if key not in TYPES:
        raise InfrastructureError(f"نوعِ زیرساختِ «{key}» وجود نداره.")
    if amount <= 0:
        raise InfrastructureError("مبلغ باید مثبت باشه.")

    e = _entry(chat_id, key)
    max_level = getattr(config, "INFRASTRUCTURE_MAX_LEVEL", 10)
    if e["level"] >= max_level:
        raise InfrastructureError(f"{TYPES[key]['name']} به حداکثر Level ({max_level}) رسیده.")

    await economy_core.remove_coins(
        chat_id, uid, amount, kind="OTHER",
        note=f"مشارکت در زیرساختِ {TYPES[key]['name']}",
    )

    e["progress"] += amount
    e["total_invested"] += amount
    uid_key = str(uid)
    e["contributors"][uid_key] = e["contributors"].get(uid_key, 0) + amount

    leveled_up = False
    while e["level"] < max_level:
        cost = next_level_cost(key, e["level"])
        if e["progress"] < cost:
            break
        e["progress"] -= cost
        e["level"] += 1
        leveled_up = True

    return {
        "key": key, "level": e["level"], "progress": e["progress"],
        "cost_for_next": None if e["level"] >= max_level else next_level_cost(key, e["level"]),
        "leveled_up": leveled_up,
    }


def fund_from_treasury(chat_id: int, key: str, amount: int) -> dict:
    """فاز ۷ V18: برخلافِ contribute() که از کیفِ‌پولِ شخصیِ یه عضو کم
    می‌کنه، این از خزانه‌ی عمومیِ گروه (economy_treasury) خرج می‌کنه -
    دقیقاً «پروژه‌هایِ عمرانیِ» بخشِ ۱۰. هر کاربری نمی‌تونه صدا بزنه؛ فراخوان
    (economy_treasury.spend_on_infrastructure_command) خودش قبلش مجوزِ
    manage_economy رو چک می‌کنه."""
    if key not in TYPES:
        raise InfrastructureError(f"نوعِ زیرساختِ «{key}» وجود نداره.")
    if amount <= 0:
        raise InfrastructureError("مبلغ باید مثبت باشه.")

    e = _entry(chat_id, key)
    max_level = getattr(config, "INFRASTRUCTURE_MAX_LEVEL", 10)
    if e["level"] >= max_level:
        raise InfrastructureError(f"{TYPES[key]['name']} به حداکثر Level ({max_level}) رسیده.")

    import economy_treasury
    economy_treasury.spend(chat_id, amount, purpose=f"زیرساخت:{key}", note=TYPES[key]["name"])

    e["progress"] += amount
    e["total_invested"] += amount
    e["contributors"]["treasury"] = e["contributors"].get("treasury", 0) + amount

    leveled_up = False
    while e["level"] < max_level:
        cost = next_level_cost(key, e["level"])
        if e["progress"] < cost:
            break
        e["progress"] -= cost
        e["level"] += 1
        leveled_up = True

    return {
        "key": key, "level": e["level"], "progress": e["progress"],
        "cost_for_next": None if e["level"] >= max_level else next_level_cost(key, e["level"]),
        "leveled_up": leveled_up,
    }


def quality_index(chat_id: int) -> float:
    """شاخصِ کیفیتِ زیرساختِ گروه، ۰ تا ۱۰۰ - میانگینِ وزن‌دارِ Levelِ همه‌ی
    زیرساخت‌ها نسبت به حداکثر. مستقل نگه داشته شده (به عمد به هیچ فرمولِ
    تست‌شده‌ی دیگه‌ای مثلِ economy_macro وصل نشده) تا فازهایِ بعدی بتونن
    با خیالِ راحت بهش وصل بشن."""
    max_level = getattr(config, "INFRASTRUCTURE_MAX_LEVEL", 10)
    if not TYPES or max_level <= 0:
        return 0.0
    total_weight = sum(info.get("quality_weight", 1.0) for info in TYPES.values())
    if total_weight <= 0:
        return 0.0
    score = 0.0
    for key, info in TYPES.items():
        e = _entry(chat_id, key)
        weight = info.get("quality_weight", 1.0)
        score += weight * (e["level"] / max_level)
    return round(score / total_weight * 100, 1)


def estimated_population(chat_id: int) -> int:
    """تخمینِ جمعیتِ گروه: تعدادِ اعضایِ شناخته‌شده + پاداشِ کیفیتِ زیرساخت.
    یه تخمینِ ساده و قابل‌توضیحه، نه یه شمارشِ دقیقِ جمعیت‌شناسی."""
    host = _host()
    members = host._known_members.get(chat_id, set())
    base = len(members) * 50
    bonus = round(quality_index(chat_id) * 5)
    return base + bonus


# ---------------------------------------------------------------------------
# 🖊 نمایش متنی
# ---------------------------------------------------------------------------

def format_infrastructure_text(chat_id: int) -> str:
    rows = list_all(chat_id)
    lines = [
        f"🏗 زیرساختِ شهریِ گروه — کیفیت: {quality_index(chat_id)}/۱۰۰ — جمعیتِ تخمینی: {estimated_population(chat_id):,}",
        "─────────────────",
    ]
    for r in rows:
        if r["maxed"]:
            lines.append(f"{r['name']}: Lv{r['level']} (حداکثر)")
        else:
            lines.append(
                f"{r['name']}: Lv{r['level']} — پیشرفت تا Levelِ بعد: "
                f"{r['progress']:,}/{r['cost_for_next']:,}"
            )
    lines.append("")
    lines.append("مشارکت: «مشارکت در زیرساخت [نوع] [مبلغ]»")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 📨 Command Handlers
# ---------------------------------------------------------------------------

_TYPE_ALIASES = {
    "نیروگاه": "power_plant", "برق": "power_plant",
    "جاده": "roads", "حمل و نقل": "roads", "حمل‌ونقل": "roads",
    "بیمارستان": "hospital",
    "مدرسه": "school",
    "آب": "water_system", "آبرسانی": "water_system", "آب‌رسانی": "water_system",
}


def _resolve_type(text: str) -> str | None:
    key = text.strip()
    if key in TYPES:
        return key
    return _TYPE_ALIASES.get(key)


async def infrastructure_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زیرساخت / /infrastructure"""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    await message.reply_text(format_infrastructure_text(chat.id))


async def contribute_infrastructure_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مشارکت در زیرساخت [نوع] [مبلغ] / /infracontribute <type> <amount>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if len(args) < 2:
        names = "، ".join(info["name"] for info in TYPES.values())
        await message.reply_text(f"استفاده: «مشارکت در زیرساخت [نوع] [مبلغ]». انواع: {names}")
        return
    key = _resolve_type(args[0])
    if not key:
        await message.reply_text("این نوع زیرساخت رو نمی‌شناسم. با «زیرساخت» لیست رو ببین.")
        return
    try:
        amount = int(args[1].replace(",", ""))
    except ValueError:
        await message.reply_text("مبلغ نامعتبره.")
        return
    uid = update.effective_user.id
    try:
        result = await contribute(chat.id, uid, key, amount)
    except (InfrastructureError, economy_core.InsufficientFundsError) as e:
        await message.reply_text(f"❌ {e}")
        return
    lines = [f"✅ {amount:,} {config.CURRENCY_NAME} به {TYPES[key]['name']} کمک شد."]
    if result["leveled_up"]:
        lines.append(f"🎉 {TYPES[key]['name']} به Levelِ {result['level']} ارتقا پیدا کرد!")
    await message.reply_text("\n".join(lines))
    await host.save_state()


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {"version": STATE_VERSION, "infra": {str(c): v for c, v in _infra.items()}}


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
            logger.warning(f"ذخیره‌ی state زیرساخت ناموفق بود: {e}")


def load_state():
    data = None
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"خوندن state زیرساخت ناموفق بود: {e}؛ تلاش برای بازیابی از بکاپ...")
            data = None
    if data is None:
        bak = STATE_FILE + ".bak"
        if os.path.exists(bak):
            try:
                with open(bak, encoding="utf-8") as f:
                    data = json.load(f)
                logger.warning("state اصلیِ زیرساخت خراب/نامعتبر بود؛ از بکاپ بازیابی شد.")
            except Exception as e:
                logger.warning(f"خوندنِ بکاپِ زیرساخت هم ناموفق بود: {e}")
                return
        else:
            return

    for cid_s, d in data.get("infra", {}).items():
        cid = int(cid_s)
        for key, e in d.items():
            if key in TYPES:
                _infra[cid][key] = {
                    "level": int(e.get("level", 0)),
                    "progress": int(e.get("progress", 0)),
                    "total_invested": int(e.get("total_invested", 0)),
                    "contributors": {str(k): int(v) for k, v in e.get("contributors", {}).items()},
                }

    logger.info("وضعیتِ زیرساخت از فایل بارگذاری شد.")
