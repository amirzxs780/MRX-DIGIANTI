# -*- coding: utf-8 -*-
"""
DIGIANTI PURGE ENGINE 1.0
==========================
موتور مرکزی پاکسازی پیام‌های گروه DIGIANTI.

معماری دقیقاً هم‌خانواده‌ی spam_engine.py / lock_engine.py / moderation_engine.py:
  - state این موتور (فقط آمار عملیات پاکسازی) توی فایل JSON مستقل خودش
    (purge_engine_state.json) نگه داشته می‌شه، بدون دست‌زدن به
    _collect_state/load_state غول‌پیکر bot.py.
  - Permission از همون تابع bot.has_permission(user_id, "moderate") گرفته
    می‌شه - سیستم Permission دومی ساخته نشده.
  - تشخیص لینک/تبلیغ/امتیاز اسپم از spam_engine (که خودش از lock_engine
    استفاده می‌کنه) گرفته می‌شه - منطق تشخیص اسپم/تبلیغ از صفر بازسازی نشده.
  - لاگ عملیات ادمین از همون host._log_admin_action (کانال لاگ فعلی پروژه)
    استفاده می‌کنه - سیستم لاگ موازی ساخته نشده.
  - Import از bot.py همیشه Lazy (داخل توابع) هست تا Circular Import رخ نده
    (چون bot.py این ماژول رو در سطح ماژول import می‌کنه).

نکته‌ی مهم درباره‌ی کش پیام‌ها:
  Telegram Bot API هیچ راهی برای «واکشی» محتوای پیام‌های قدیمی (از قبل
  فرستاده‌شده) در اختیار ربات‌ها نمی‌ذاره؛ ربات فقط پیام‌هایی که خودش
  Update‌شون رو دریافت کرده می‌تونه بشناسه. به همین دلیل، برای پاکسازی‌های
  فیلتردار (رسانه/لینک/متن/ربات/تکراری/اسپم/تبلیغات/هوشمند/پاکسازی کاربر)،
  این موتور یه کش کوتاه‌مدت و فقط‌حافظه‌ای از پیام‌های اخیر هر گروه نگه
  می‌داره (record_message - از bot.py صدا زده می‌شه). این کش با ری‌استارت
  ربات خالی می‌شه؛ این یه محدودیت شناخته‌شده و مستنده‌ست، نه باگ.
  در مقابل، پاکسازیِ ساده‌ی بدون فیلتر (فقط تعداد یا فقط زمان، مثل /purge 50
  یا حالت اصلیِ ریپلای‌محور /purge) نیازی به کش نداره و مثل قبل، مستقیم روی
  بازه‌ی Message ID عمل می‌کنه - همیشه کار می‌کنه، حتی بلافاصله بعد از
  ری‌استارت ربات.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

try:
    from zoneinfo import ZoneInfo
except Exception:  # پایتون خیلی قدیمی یا نبود ماژول zoneinfo
    ZoneInfo = None

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError

import config
import spam_engine

logger = logging.getLogger("purge_engine")

STATE_FILE = getattr(config, "PURGE_ENGINE_STATE_FILE", "purge_engine_state.json")

try:
    _TZ = ZoneInfo(getattr(config, "TIMEZONE", "Asia/Tehran")) if ZoneInfo else None
except Exception:
    _TZ = None


def _now_local() -> datetime.datetime:
    return datetime.datetime.now(_TZ)


# ---------------------------------------------------------------------------
# 🧠 ثابت‌ها و معادل‌های فارسی/انگلیسی
# ---------------------------------------------------------------------------

ALL_MEDIA_KINDS = {"photo", "video", "document", "sticker", "animation", "voice", "audio", "video_note"}

_KIND_LABELS = {
    "photo": "عکس", "video": "ویدیو", "document": "فایل", "sticker": "استیکر",
    "animation": "گیف", "voice": "ویس", "audio": "صوت", "video_note": "ویدیو‌نوت",
}

TEXT_TRIGGER_WORDS = {"text", "متن"}
SILENT_WORDS = {"silent", "بیصدا"}
PREVIEW_WORDS = {"preview", "پیشنمایش"}
LINKS_WORDS = {"links", "link", "لینک", "لینکها"}
BOTS_WORDS = {"bots", "bot", "ربات", "رباتها"}
DUP_WORDS = {"duplicates", "duplicate", "تکراری", "تکراریها"}
SPAM_WORDS = {"spam", "اسپم"}
ADS_WORDS = {"ads", "ad", "تبلیغات", "تبلیغ"}
SMART_WORDS = {"smart", "هوشمند"}
MEDIA_WORDS = {"media", "رسانه"}
PHOTO_WORDS = {"photo", "عکس", "عکسها"}
VIDEO_WORDS = {"video", "ویدیو", "ویدئو"}
FILE_WORDS = {"file", "فایل"}
STICKER_WORDS = {"sticker", "استیکر"}
GIF_WORDS = {"gif", "animation", "گیف"}
VOICE_WORDS = {"voice", "ویس"}
AUDIO_WORDS = {"audio", "صوت"}
HELP_WORDS = {"help", "راهنما", "کمک"}

_TIME_UNIT_WORDS = {
    "ساعت", "روز", "دقیقه",
    "h", "hour", "hours", "d", "day", "days", "m", "min", "mins", "minute", "minutes",
}
_TIME_TOKEN_RE = re.compile(
    r"^(\d+)(ساعت|روز|دقیقه|h|hour|hours|d|day|days|m|min|mins|minute|minutes)$",
    re.IGNORECASE,
)

_PERSIAN_DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def _strip_zwnj(s: str) -> str:
    return (s or "").replace("\u200c", "").replace("\u200d", "")


def _clean_token(s: str) -> str:
    return _strip_zwnj(s).translate(_PERSIAN_DIGIT_MAP)


# ---------------------------------------------------------------------------
# 💾 کش کوتاه‌مدت پیام‌های اخیر هر گروه (برای پاکسازی‌های فیلتردار)
# ---------------------------------------------------------------------------

_CACHE_MAXLEN = int(getattr(config, "PURGE_CACHE_MAX_MESSAGES", 6000))
_cache: dict[int, deque] = defaultdict(lambda: deque(maxlen=_CACHE_MAXLEN))

# آمار عملیات پاکسازی: chat_id -> {...}
_stats: dict[int, dict] = defaultdict(lambda: {
    "total_ops": 0, "total_deleted": 0, "daily": {}, "by_admin": {}, "last_purge": None,
})

# تاییدیه/پیش‌نمایش‌های در انتظار: token -> {...}
_pending: dict[str, dict] = {}
_token_seq = 0

_save_lock = asyncio.Lock()


def _classify_kind(message) -> str:
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "video", None):
        return "video"
    if getattr(message, "document", None):
        return "document"
    if getattr(message, "sticker", None):
        return "sticker"
    if getattr(message, "animation", None):
        return "animation"
    if getattr(message, "voice", None):
        return "voice"
    if getattr(message, "audio", None):
        return "audio"
    if getattr(message, "video_note", None):
        return "video_note"
    return "text"


def record_message(message, user, chat_id: int):
    """با هر پیامی که توی گروه رد می‌شه صدا زده می‌شه (از bot.track_chat_and_stats).
    هیچ‌وقت نباید خطا پرتاب کنه - ثبت آمار پاکسازی نباید کل ربات رو تحت تاثیر بذاره."""
    try:
        if message is None or chat_id is None:
            return
        mid = getattr(message, "message_id", None)
        if mid is None:
            return
        kind = _classify_kind(message)
        raw_text = message.text or message.caption or ""
        norm_text = spam_engine._normalize_text(raw_text) if raw_text else ""
        has_link = bool(spam_engine.find_links(raw_text)) if raw_text else False
        uid = getattr(user, "id", None) if user else None
        is_bot = bool(getattr(user, "is_bot", False)) if user else False
        name = (user.first_name or user.username or str(user.id)) if user else "کاربر"
        msg_date = getattr(message, "date", None)
        entry = {
            "id": mid,
            "date": msg_date.timestamp() if msg_date else time.time(),
            "user_id": uid,
            "is_bot": is_bot,
            "name": name,
            "kind": kind,
            "text": norm_text,
            "has_link": has_link,
        }
        _cache[chat_id].append(entry)
    except Exception as e:
        logger.warning(f"ثبت پیام در کش پاکسازی ناموفق بود (نادیده گرفته شد): {e}")


# ---------------------------------------------------------------------------
# 🧾 مشخصات یک عملیات پاکسازی (خروجی Parser)
# ---------------------------------------------------------------------------

@dataclass
class PurgeSpec:
    count: int | None = None
    time_seconds: int | None = None
    types: set = field(default_factory=set)
    links: bool = False
    bots: bool = False
    text_query: str | None = None
    duplicates: bool = False
    spam: bool = False
    ads: bool = False
    smart: bool = False
    preview: bool = False
    silent: bool = False
    clamped: bool = False

    def has_content_filters(self) -> bool:
        return bool(
            self.types or self.links or self.bots or self.text_query
            or self.duplicates or self.spam or self.ads or self.smart
        )


def _parse_time_token(tok: str):
    m = _TIME_TOKEN_RE.match(tok)
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2).lower()
    if unit in ("ساعت", "h", "hour", "hours"):
        return value * 3600
    if unit in ("روز", "d", "day", "days"):
        return value * 86400
    if unit in ("دقیقه", "m", "min", "mins", "minute", "minutes"):
        return value * 60
    return None


def _preprocess_tokens(args: list[str]) -> list[str]:
    """ارقام فارسی → انگلیسی، نیم‌فاصله حذف، و یکی‌کردن توکن‌های عدد+واحدِ
    جداشده با فاصله (مثل «1 ساعت» → «1ساعت») تا هم‌ارزِ «1ساعت» تشخیص داده بشه."""
    cleaned = [_clean_token(a) for a in args if _clean_token(a)]
    merged = []
    i = 0
    while i < len(cleaned):
        tok = cleaned[i]
        if tok.isdigit() and i + 1 < len(cleaned) and cleaned[i + 1].lower() in _TIME_UNIT_WORDS:
            merged.append(tok + cleaned[i + 1].lower())
            i += 2
            continue
        merged.append(tok)
        i += 1
    return merged


def parse_purge_args(args: list[str]) -> tuple[PurgeSpec, list[str]]:
    """ترتیب آرگومان‌ها مهم نیست (به‌جز «متن/text» که هرچی بعدش بیاد عبارت جستجوئه).
    برمی‌گردونه: (PurgeSpec, لیست خطاها)."""
    spec = PurgeSpec()
    errors: list[str] = []
    tokens = _preprocess_tokens(args)

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        low = tok.lower()

        if low in TEXT_TRIGGER_WORDS:
            rest = tokens[i + 1:]
            if not rest:
                errors.append("بعد از «متن» باید عبارت موردنظر رو بنویسی؛ مثال: پاکسازی متن تبلیغ")
            else:
                spec.text_query = spam_engine._normalize_text(" ".join(rest))
            i = len(tokens)
            continue
        if low in SILENT_WORDS:
            spec.silent = True
        elif low in PREVIEW_WORDS:
            spec.preview = True
        elif low in LINKS_WORDS:
            spec.links = True
        elif low in BOTS_WORDS:
            spec.bots = True
        elif low in DUP_WORDS:
            spec.duplicates = True
        elif low in SPAM_WORDS:
            spec.spam = True
        elif low in ADS_WORDS:
            spec.ads = True
        elif low in SMART_WORDS:
            spec.smart = True
        elif low in MEDIA_WORDS:
            spec.types |= set(ALL_MEDIA_KINDS)
        elif low in PHOTO_WORDS:
            spec.types.add("photo")
        elif low in VIDEO_WORDS:
            spec.types.add("video")
        elif low in FILE_WORDS:
            spec.types.add("document")
        elif low in STICKER_WORDS:
            spec.types.add("sticker")
        elif low in GIF_WORDS:
            spec.types.add("animation")
        elif low in VOICE_WORDS:
            spec.types.add("voice")
        elif low in AUDIO_WORDS:
            spec.types.add("audio")
        else:
            tsec = _parse_time_token(low)
            if tsec is not None:
                spec.time_seconds = tsec
            elif tok.isdigit():
                n = int(tok)
                if n <= 0:
                    errors.append("تعداد باید عدد مثبت باشه.")
                else:
                    spec.count = n
            else:
                errors.append(f"آرگومان ناشناخته: {tok}")
        i += 1

    return spec, errors


# ---------------------------------------------------------------------------
# 🔎 انتخاب پیام‌های کاندید بر اساس فیلترها (روی کش)
# ---------------------------------------------------------------------------

def _mark_duplicates(candidates: list[dict]):
    seen = set()
    for e in candidates:
        t = e["text"]
        if not t or len(t) < 3:
            e["is_dup"] = False
            continue
        if t in seen:
            e["is_dup"] = True
        else:
            seen.add(t)
            e["is_dup"] = False


def _mark_ads(chat_id: int, candidates: list[dict], threshold: int):
    for e in candidates:
        if not e["user_id"] or not e["text"]:
            e["is_ad"] = False
            continue
        score = spam_engine._ad_score(chat_id, e["user_id"], e["text"], e["has_link"], e.get("is_dup", False))
        e["is_ad"] = score >= threshold


def _mark_spam(chat_id: int, candidates: list[dict], threshold: int):
    for e in candidates:
        if not e["user_id"]:
            e["is_spam"] = False
            continue
        score = spam_engine.current_score(chat_id, e["user_id"])
        e["is_spam"] = score >= threshold or e.get("is_ad", False)


def _link_is_whitelisted(chat_id: int, text: str) -> bool:
    try:
        links = spam_engine.find_links(text)
        if not links:
            return False
        for link in links:
            domain = spam_engine.extract_domain(link)
            if not domain or not spam_engine.domain_is_whitelisted(chat_id, domain):
                return False
        return True
    except Exception:
        return False


def _mark_smart(chat_id: int, candidates: list[dict]):
    for e in candidates:
        risky_link = e["has_link"] and not _link_is_whitelisted(chat_id, e["text"])
        e["is_smart"] = bool(
            e["is_bot"] or risky_link or e.get("is_dup") or e.get("is_ad") or e.get("is_spam")
        )


def _message_matches(e: dict, spec: PurgeSpec) -> bool:
    if spec.types and e["kind"] not in spec.types:
        return False
    if spec.links and not e["has_link"]:
        return False
    if spec.bots and not e["is_bot"]:
        return False
    if spec.text_query and spec.text_query not in e["text"]:
        return False
    if spec.duplicates and not e.get("is_dup"):
        return False
    if spec.ads and not e.get("is_ad"):
        return False
    if spec.spam and not e.get("is_spam"):
        return False
    if spec.smart and not e.get("is_smart"):
        return False
    return True


def _gather_candidates(chat_id: int, spec: PurgeSpec, scope_user_id: int | None = None) -> list[dict]:
    cache = list(_cache.get(chat_id, ()))  # قدیمی → جدید
    now = time.time()

    if spec.time_seconds:
        floor = now - spec.time_seconds
        cache = [e for e in cache if e["date"] >= floor]
    elif not spec.count:
        default_window = float(getattr(config, "PURGE_TIME_LIMIT", 86400))
        floor = now - default_window
        cache = [e for e in cache if e["date"] >= floor]

    if scope_user_id is not None:
        cache = [e for e in cache if e["user_id"] == scope_user_id]

    if spec.duplicates or spec.smart:
        _mark_duplicates(cache)
    if spec.ads or spec.smart:
        _mark_ads(chat_id, cache, int(getattr(config, "PURGE_AD_SCORE_THRESHOLD", 40)))
    if spec.spam or spec.smart:
        _mark_spam(chat_id, cache, int(getattr(config, "PURGE_SPAM_SCORE_THRESHOLD", 20)))
    if spec.smart:
        _mark_smart(chat_id, cache)

    matched = [e for e in cache if _message_matches(e, spec)]
    if spec.count:
        matched = matched[-spec.count:]
    return matched


def _resolve_unfiltered_range(chat_id: int, spec: PurgeSpec, end_id: int):
    """برای پاکسازی بدون فیلتر محتوایی (فقط تعداد یا فقط زمان)؛ مستقیم روی
    بازه‌ی Message ID عمل می‌کنه - وابسته به کش نیست (مگر برای تخمین بازه‌ی
    زمانی) و همیشه (حتی بعد از ری‌استارت) برای حالت تعدادی کار می‌کنه."""
    if spec.count:
        start_id = max(1, end_id - spec.count)
        return start_id, end_id, None

    if spec.time_seconds:
        cache = list(_cache.get(chat_id, ()))
        floor = time.time() - spec.time_seconds
        ids_in_window = [e["id"] for e in cache if e["date"] >= floor]
        if not ids_in_window:
            return None, None, (
                "هیچ پیامی توی این بازه‌ی زمانی توی تاریخچه‌ی ذخیره‌شده‌ی ربات پیدا نشد "
                "(شاید ربات به‌تازگی ری‌استارت شده باشه)."
            )
        start_id = min(ids_in_window)
        note = None
        if cache and cache[0]["date"] > floor + 5:
            note = "⚠️ چون کش ربات محدوده‌ست، ممکنه بعضی پیام‌های قدیمی‌تر پاک نشده باشن."
        return start_id, end_id, note

    return None, None, None


def _build_breakdown(entries: list[dict]) -> dict:
    b = {"total": len(entries), "media": 0, "links": 0, "text": 0, "bots": 0}
    for e in entries:
        if e["kind"] in ALL_MEDIA_KINDS:
            b["media"] += 1
        if e["has_link"]:
            b["links"] += 1
        if e["kind"] == "text" and e["text"]:
            b["text"] += 1
        if e["is_bot"]:
            b["bots"] += 1
    return b


# ---------------------------------------------------------------------------
# 🗑 حذف واقعی پیام‌ها (Chunk 100تایی + مدیریت FloodWait/خطا)
# ---------------------------------------------------------------------------

async def _delete_chunk(context, chat_id: int, chunk: list[int], allow_retry: bool = True) -> bool:
    try:
        await context.bot.delete_messages(chat_id=chat_id, message_ids=chunk)
        return True
    except RetryAfter as e:
        if allow_retry:
            await asyncio.sleep(e.retry_after + 0.5)
            return await _delete_chunk(context, chat_id, chunk, allow_retry=False)
        logger.warning(f"[PurgeEngine] FloodWait دوباره تکرار شد؛ این بخش رد شد: {e}")
        return False
    except Forbidden as e:
        logger.warning(f"[PurgeEngine] دسترسی حذف پیام وجود نداره: {e}")
        return False
    except BadRequest as e:
        logger.warning(f"[PurgeEngine] حذف بخشی ناموفق (BadRequest): {e}")
        return False
    except TelegramError as e:
        logger.warning(f"[PurgeEngine] خطای تلگرام در حذف: {e}")
        return False
    except Exception as e:
        logger.warning(f"[PurgeEngine] خطای غیرمنتظره در حذف: {e}")
        return False


async def _execute_delete(context, chat_id: int, ids: list[int]) -> tuple[int, int]:
    seen = set()
    unique_ids = [i for i in ids if i and i > 0 and not (i in seen or seen.add(i))]
    deleted = 0
    failed = 0
    for i in range(0, len(unique_ids), 100):
        chunk = unique_ids[i:i + 100]
        ok = await _delete_chunk(context, chat_id, chunk)
        if ok:
            deleted += len(chunk)
        else:
            failed += len(chunk)
        if i + 100 < len(unique_ids):
            await asyncio.sleep(0.3)  # فاصله‌ی کوچیک بین Chunkها برای کاهش ریسک Rate Limit
    return deleted, failed


# ---------------------------------------------------------------------------
# 📊 آمار پاکسازی
# ---------------------------------------------------------------------------

def _today_str() -> str:
    return _now_local().strftime("%Y-%m-%d")


def _sum_last_days(daily: dict, days: int) -> int:
    total = 0
    today = _now_local().date()
    for i in range(days):
        d = (today - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        total += daily.get(d, 0)
    return total


def _format_ago(iso_str: str) -> str:
    try:
        dt = datetime.datetime.fromisoformat(iso_str)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso_str or "—"


def _record_stats(chat_id: int, admin_name: str, deleted: int):
    try:
        s = _stats[chat_id]
        s["total_ops"] += 1
        s["total_deleted"] += deleted
        today = _today_str()
        s["daily"][today] = s["daily"].get(today, 0) + deleted
        s["by_admin"][admin_name] = s["by_admin"].get(admin_name, 0) + deleted
        s["last_purge"] = _now_local().isoformat()
        if len(s["daily"]) > 120:  # اجازه نده لاگ روزانه تا ابد بزرگ بشه
            for k in sorted(s["daily"].keys())[:-90]:
                s["daily"].pop(k, None)
    except Exception as e:
        logger.warning(f"ثبت آمار پاکسازی ناموفق بود: {e}")


# ---------------------------------------------------------------------------
# 🎨 قالب‌بندی پیام‌ها
# ---------------------------------------------------------------------------

def _human_seconds(sec: int) -> str:
    if sec % 86400 == 0:
        return f"{sec // 86400} روز"
    if sec % 3600 == 0:
        return f"{sec // 3600} ساعت"
    return f"{max(1, sec // 60)} دقیقه"


def _describe_filters(spec: PurgeSpec) -> str:
    parts = []
    if spec.count:
        parts.append(f"{spec.count} پیام")
    if spec.time_seconds:
        parts.append(_human_seconds(spec.time_seconds))
    if spec.types:
        if spec.types == ALL_MEDIA_KINDS:
            parts.append("رسانه")
        else:
            parts.append(" و ".join(_KIND_LABELS.get(t, t) for t in sorted(spec.types)))
    if spec.links:
        parts.append("لینک")
    if spec.bots:
        parts.append("ربات‌ها")
    if spec.text_query:
        parts.append(f"متن: «{spec.text_query}»")
    if spec.duplicates:
        parts.append("تکراری‌ها")
    if spec.spam:
        parts.append("اسپم")
    if spec.ads:
        parts.append("تبلیغات")
    if spec.smart:
        parts.append("هوشمند")
    return " + ".join(parts) if parts else "بازه‌ی دستی"


def _format_report(deleted: int, failed: int, executor_name: str, elapsed: float,
                    filter_summary: str, note: str | None = None) -> str:
    lines = [
        "🧹 پاکسازی انجام شد",
        "",
        f"├ 🗑 حذف‌شده: {deleted}",
        f"├ ⚠️ ناموفق: {failed}",
        f"├ 👤 اجراکننده: {executor_name}",
        f"├ ⏱ زمان: {elapsed:.1f}s" if elapsed else "├ ⏱ زمان: -",
        f"└ 📌 نوع: {filter_summary}",
    ]
    if note:
        lines.append(note)
    return "\n".join(lines)


def _purge_help_text() -> str:
    return (
        "🧹 راهنمای کامل پاکسازی\n\n"
        "پایه:\n"
        "• /purge (ریپلای) › از پیام ریپلای‌شده تا الان\n"
        "• /purge 50 / 100 / 500 › تعداد اخیر\n"
        "• /purge 1h / 24h / 7d › بازه‌ی زمانی (یا: 1ساعت، 24ساعت، 7روز)\n\n"
        "بر اساس نوع پیام:\n"
        "• media/رسانه، photo/عکس، video/ویدیو، file/فایل،\n"
        "  sticker/استیکر، gif/گیف، voice/ویس، audio/صوت\n\n"
        "فیلترهای دیگر:\n"
        "• links/لینک • bots/ربات‌ها • duplicates/تکراری‌ها\n"
        "• spam/اسپم • ads/تبلیغات • smart/هوشمند\n"
        "• text <عبارت> / متن <عبارت>\n\n"
        "ترکیبی: /purge 1h links   یا   /purge 100 media\n\n"
        "حالت‌ها:\n"
        "• preview/پیش‌نمایش › قبل از حذف نشون می‌ده\n"
        "• silent/بی‌صدا › بدون گزارش توی چت\n\n"
        "کاربر خاص:\n"
        "• /purgeuser (ریپلای) [تعداد|بازه‌زمانی]\n\n"
        "آمار:\n"
        "• /purgestats\n\n"
        "همه‌ی دستورهای بالا معادل فارسی «پاکسازی ...» و «پاکسازی کاربر ...» "
        "و «آمار پاکسازی» رو هم دارن.\n"
        "⚠️ فیلترهای محتوایی (رسانه/لینک/متن/...) فقط روی پیام‌هایی کار می‌کنن "
        "که بعد از آخرین بالا اومدن ربات فرستاده شدن."
    )


# ---------------------------------------------------------------------------
# ✅ تاییدیه / پیش‌نمایش (Inline Buttons)
# ---------------------------------------------------------------------------

def _prune_pending():
    ttl = int(getattr(config, "PURGE_PENDING_TTL_SECONDS", 120))
    now = time.time()
    for token in [t for t, p in _pending.items() if now - p["created"] > ttl]:
        _pending.pop(token, None)


def _new_token(chat_id: int) -> str:
    global _token_seq
    _prune_pending()
    _token_seq += 1
    return f"{chat_id}-{_token_seq}-{int(time.time()) % 100000}"


async def _send_preview(update, context, chat_id: int, ids: list[int], entries: list[dict],
                         spec: PurgeSpec, note: str | None = None):
    token = _new_token(chat_id)
    _pending[token] = {"chat_id": chat_id, "ids": ids, "spec": spec, "created": time.time(), "note": note}
    b = _build_breakdown(entries) if entries else {"total": len(ids), "media": 0, "links": 0, "text": 0, "bots": 0}
    lines = ["🔎 پیش‌نمایش پاکسازی", "", f"🗑 تعداد قابل حذف: {b['total']}"]
    if entries:
        lines += [
            f"🖼 رسانه: {b['media']}",
            f"🔗 لینک: {b['links']}",
            f"💬 متن: {b['text']}",
            f"🤖 ربات: {b['bots']}",
        ]
    if spec.clamped:
        lines.append(f"ℹ️ تعداد به سقف مجاز ({getattr(config, 'PURGE_MAX_MESSAGES', 1000)}) محدود شد.")
    if note:
        lines.append(note)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تأیید پاکسازی", callback_data=f"purgeop:confirm:{token}"),
        InlineKeyboardButton("❌ لغو", callback_data=f"purgeop:cancel:{token}"),
    ]])
    await update.effective_message.reply_text("\n".join(lines), reply_markup=keyboard)


async def _send_confirmation(update, context, chat_id: int, ids: list[int], entries: list[dict],
                              spec: PurgeSpec, note: str | None = None):
    token = _new_token(chat_id)
    _pending[token] = {"chat_id": chat_id, "ids": ids, "spec": spec, "created": time.time(), "note": note}
    text = (
        "⚠️ هشدار پاکسازی\n\n"
        f"تعداد پیام‌های شناسایی‌شده:\n{len(ids)} پیام\n\n"
        "آیا مطمئن هستید؟"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تأیید", callback_data=f"purgeop:confirm:{token}"),
        InlineKeyboardButton("❌ لغو", callback_data=f"purgeop:cancel:{token}"),
    ]])
    await update.effective_message.reply_text(text, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# 🚀 اجرای واقعی پاکسازی (مشترک بین مسیر مستقیم و مسیر تاییدشده)
# ---------------------------------------------------------------------------

async def _run_purge(update, context, chat_id: int, ids: list[int], spec: PurgeSpec,
                      note: str | None = None, executor_name: str | None = None, edit_message=None):
    import bot as host

    start_ts = time.time()
    if not executor_name:
        u = update.effective_user
        executor_name = (u.first_name or u.username or str(u.id)) if u else "ادمین"

    deleted, failed = await _execute_delete(context, chat_id, ids)
    elapsed = time.time() - start_ts
    _record_stats(chat_id, executor_name, deleted)
    filter_summary = _describe_filters(spec)
    report = _format_report(deleted, failed, executor_name, elapsed, filter_summary, note)

    if not spec.silent:
        if edit_message is not None:
            try:
                await edit_message.edit_text(report)
            except Exception:
                try:
                    await context.bot.send_message(chat_id=chat_id, text=report)
                except Exception:
                    pass
        else:
            try:
                await context.bot.send_message(chat_id=chat_id, text=report)
            except Exception:
                pass
    elif edit_message is not None:
        try:
            await edit_message.delete()
        except Exception:
            pass

    if getattr(config, "PURGE_LOG_ENABLED", True):
        try:
            await host._log_admin_action(
                context, chat_id,
                f"🧹 Purge ({filter_summary})\n🗑️ حذف‌شده: {deleted}\n⚠️ ناموفق: {failed}\n🧑‍💼 توسط: {executor_name}",
            )
        except Exception as e:
            logger.warning(f"لاگ ادمین پاکسازی ناموفق بود: {e}")

    return deleted, failed


async def _dispatch_purge(update, context, chat_id: int, ids: list[int], entries: list[dict],
                           spec: PurgeSpec, note: str | None = None):
    if not ids:
        await update.effective_message.reply_text("📭 پیامی مطابق این فیلترها پیدا نشد.")
        return

    if spec.preview:
        if not getattr(config, "PURGE_PREVIEW_ENABLED", True):
            await update.effective_message.reply_text("⛔ حالت پیش‌نمایش غیرفعاله.")
            return
        await _send_preview(update, context, chat_id, ids, entries, spec, note)
        return

    threshold = int(getattr(config, "PURGE_CONFIRM_THRESHOLD", 300))
    if len(ids) > threshold:
        await _send_confirmation(update, context, chat_id, ids, entries, spec, note)
        return

    await _run_purge(update, context, chat_id, ids, spec, note=note)


# ---------------------------------------------------------------------------
# ⌨️ دستورات ورودی (از bot.py صدا زده می‌شن)
# ---------------------------------------------------------------------------

async def _legacy_purge_by_reply(update, context):
    """رفتار اصلی و بدون‌تغییرِ /purge: ریپلای روی یه پیام + /purge = پاک‌سازی از
    اونجا تا الان. عیناً همون منطق قبلی، فقط با مدیریت FloodWait بهتر."""
    import bot as host

    message = update.effective_message
    chat_id = update.effective_chat.id

    if not message.reply_to_message:
        await message.reply_text(
            "استفاده: رو پیامی که می‌خوای پاک‌سازی از اونجا شروع بشه ریپلای بزن و بنویس /purge\n"
            "یا: /purge <تعداد|بازه‌زمانی|فیلتر> — راهنمای کامل: /purge help"
        )
        return

    start_id = message.reply_to_message.message_id
    end_id = message.message_id
    ids_to_delete = list(range(start_id, end_id + 1))

    deleted, failed = await _execute_delete(context, chat_id, ids_to_delete)
    user = update.effective_user
    executor_name = (user.first_name or user.username or str(user.id)) if user else "ادمین"
    _record_stats(chat_id, executor_name, deleted)

    async def _delete_confirm_later(msg):
        try:
            await asyncio.sleep(3)
            await msg.delete()
        except Exception:
            pass

    try:
        confirm = await context.bot.send_message(chat_id=chat_id, text=f"🧹 حدود {deleted} پیام پاک شد.")
        asyncio.create_task(_delete_confirm_later(confirm))
    except Exception:
        pass

    if getattr(config, "PURGE_LOG_ENABLED", True):
        await host._log_admin_action(
            context, chat_id, f"🧹 Purge\n🗑️ حدود {deleted} پیام\n🧑‍💼 توسط: {executor_name}",
        )


async def purge_command(update, context):
    """/purge و «پاکسازی» - نقطه‌ی ورود اصلی. بدون آرگومان = رفتار قدیمی
    (ریپلای‌محور، بدون تغییر). با آرگومان = سیستم پاکسازی پیشرفته."""
    import bot as host

    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not getattr(config, "PURGE_ENABLED", True):
        await message.reply_text("⛔ سیستم پاکسازی غیرفعاله.")
        return
    if not host.has_permission(user.id, "moderate"):
        await message.reply_text("⛔ شما اجازه استفاده از سیستم پاکسازی را ندارید.")
        return

    args = list(context.args or [])

    if args and _clean_token(args[0]).lower() in HELP_WORDS:
        await message.reply_text(_purge_help_text())
        return

    if not args:
        await _legacy_purge_by_reply(update, context)
        return

    spec, errors = parse_purge_args(args)
    if errors:
        await message.reply_text("⚠️ ورودی نامعتبر:\n" + "\n".join(errors) + "\n\nراهنما: /purge help")
        return

    if spec.smart and not getattr(config, "PURGE_SMART_ENABLED", True):
        await message.reply_text("⛔ قابلیت «پاکسازی هوشمند» غیرفعاله.")
        return
    if spec.silent and not getattr(config, "PURGE_SILENT_ENABLED", True):
        await message.reply_text("⛔ حالت «بی‌صدا» غیرفعاله.")
        return
    if spec.preview and not getattr(config, "PURGE_PREVIEW_ENABLED", True):
        await message.reply_text("⛔ حالت «پیش‌نمایش» غیرفعاله.")
        return

    if spec.count is not None:
        max_allowed = int(getattr(config, "PURGE_MAX_MESSAGES", 1000))
        if spec.count > max_allowed:
            spec.count = max_allowed
            spec.clamped = True

    end_id = message.message_id

    if not spec.has_content_filters():
        start_id, end_id2, note = _resolve_unfiltered_range(chat.id, spec, end_id)
        if start_id is None:
            await message.reply_text(note or "بازه‌ی مشخص‌شده معتبر نیست؛ حداقل تعداد یا بازه‌ی زمانی رو مشخص کن.")
            return
        ids = list(range(start_id, end_id2 + 1))
        entries = [e for e in _cache.get(chat.id, ()) if start_id <= e["id"] <= end_id2]
        await _dispatch_purge(update, context, chat.id, ids, entries, spec, note)
        return

    entries = _gather_candidates(chat.id, spec, scope_user_id=None)
    ids = [e["id"] for e in entries]
    await _dispatch_purge(update, context, chat.id, ids, entries, spec, None)


async def purgeuser_command(update, context):
    """/purgeuser و «پاکسازی کاربر» - ریپلای روی پیام یه کاربر، پیام‌های همون
    کاربر رو (توی محدوده‌ی مشخص‌شده، از کش) پاک می‌کنه."""
    import bot as host

    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not getattr(config, "PURGE_ENABLED", True) or not getattr(config, "PURGE_USER_ENABLED", True):
        await message.reply_text("⛔ سیستم پاکسازی کاربر غیرفعاله.")
        return
    if not host.has_permission(user.id, "moderate"):
        await message.reply_text("⛔ شما اجازه استفاده از سیستم پاکسازی را ندارید.")
        return

    target = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
    if target is None:
        await message.reply_text(
            "استفاده: روی پیام کاربر ریپلای بزن و بنویس /purgeuser [تعداد|بازه‌زمانی]\n"
            "مثال: /purgeuser 100  یا  /purgeuser 24h"
        )
        return

    args = list(context.args or [])
    spec, errors = parse_purge_args(args)
    if errors:
        await message.reply_text("⚠️ ورودی نامعتبر:\n" + "\n".join(errors))
        return

    if spec.count is not None:
        max_allowed = int(getattr(config, "PURGE_MAX_MESSAGES", 1000))
        if spec.count > max_allowed:
            spec.count = max_allowed
            spec.clamped = True

    entries = _gather_candidates(chat.id, spec, scope_user_id=target.id)
    ids = [e["id"] for e in entries]
    if not ids:
        await message.reply_text(
            "📭 پیام قابل‌حذفی از این کاربر (توی تاریخچه‌ی ذخیره‌شده‌ی ربات) پیدا نشد."
        )
        return

    target_name = target.first_name or target.username or str(target.id)
    await _dispatch_purge(update, context, chat.id, ids, entries, spec, note=f"👤 هدف: {target_name}")


async def purgestats_command(update, context):
    """/purgestats و «آمار پاکسازی»."""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return

    stats = _stats.get(chat.id)
    if not stats or not stats.get("total_ops"):
        await message.reply_text("📭 هنوز عملیات پاکسازی‌ای توی این گروه ثبت نشده.")
        return

    today_count = stats["daily"].get(_today_str(), 0)
    week_count = _sum_last_days(stats["daily"], 7)
    top_admin, top_count = max(stats["by_admin"].items(), key=lambda kv: kv[1], default=("—", 0))
    last_str = _format_ago(stats.get("last_purge"))

    text = (
        "🧹 آمار پاکسازی\n\n"
        f"├ کل عملیات: {stats['total_ops']}\n"
        f"├ کل پیام حذف‌شده: {stats['total_deleted']}\n"
        f"├ امروز: {today_count}\n"
        f"├ این هفته: {week_count}\n"
        f"├ بیشترین عملیات توسط: {top_admin} ({top_count})\n"
        f"└ آخرین پاکسازی: {last_str}"
    )
    await message.reply_text(text)


async def purge_callback_handler(update, context):
    """دکمه‌های ✅ تأیید / ❌ لغو برای Preview و Confirmation."""
    import bot as host

    query = update.callback_query
    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 3:
        await query.answer()
        return
    _, action, token = parts

    clicker = update.effective_user
    if not clicker or not host.has_permission(clicker.id, "moderate"):
        await query.answer("⛔ شما اجازه‌ی این کار رو ندارید.", show_alert=True)
        return

    _prune_pending()
    pending = _pending.pop(token, None)
    if not pending:
        await query.answer("این عملیات دیگه معتبر نیست یا قبلاً انجام شده.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if action == "cancel":
        await query.answer("لغو شد.")
        try:
            await query.edit_message_text("❌ پاکسازی لغو شد.")
        except Exception:
            pass
        return

    if action == "confirm":
        await query.answer("در حال پاکسازی…")
        executor_name = clicker.first_name or clicker.username or str(clicker.id)
        await _run_purge(
            update, context, pending["chat_id"], pending["ids"], pending["spec"],
            note=pending.get("note"), executor_name=executor_name, edit_message=query.message,
        )
        return

    await query.answer()


# ---------------------------------------------------------------------------
# 💾 ذخیره/بارگذاری State (فقط آمار - کش پیام‌ها عمداً پرسیستنت نیست)
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {"stats": {str(cid): s for cid, s in _stats.items()}}


def _write_state_file(data: dict):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


async def save_state():
    async with _save_lock:
        try:
            await asyncio.to_thread(_write_state_file, _collect_state())
        except Exception as e:
            logger.warning(f"ذخیره‌ی وضعیت موتور پاکسازی ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن وضعیت موتور پاکسازی ناموفق بود: {e}")
        return
    try:
        for cid, s in raw.get("stats", {}).items():
            _stats[int(cid)] = {
                "total_ops": s.get("total_ops", 0),
                "total_deleted": s.get("total_deleted", 0),
                "daily": s.get("daily", {}),
                "by_admin": s.get("by_admin", {}),
                "last_purge": s.get("last_purge"),
            }
    except Exception as e:
        logger.warning(f"بارگذاری وضعیت موتور پاکسازی ناموفق بود: {e}")
