# -*- coding: utf-8 -*-
"""
DIGIANTI SMART LOCK ENGINE 2.0
================================
موتور مرکزی قفل‌های گروه DIGIANTI.

این ماژول عمداً از bot.py جدا نگه داشته شده تا:
  - ویرایش روی فایل غول‌پیکر bot.py به حداقل برسه (ریسک خرابکاری کمتر)،
  - state این موتور توی فایل JSON مستقل خودش ذخیره بشه (بدون دست‌زدن به
    ساختار _collect_state/load_state موجود توی bot.py)،
  - و در عین حال، به‌جای ساختن یه سیستم موازی و متناقض، از تمام ساختارهای
    فعلی پروژه (is_admin/has_permission، _vip_users، _friends/_enemies،
    _blacklist، _join_times، _chat_settings، _warn_and_maybe_mute،
    _log_admin_action، save_state) مستقیماً استفاده می‌کنه.

نکته‌ی معماری: چون bot.py در import-time این ماژول رو import می‌کنه، این
ماژول از bot.py در سطح ماژول import نمی‌کنه (import حلقه‌ای). به‌جاش، هر
تابعی که به globals بات نیاز داره، در زمان اجرا (نه در زمان import)
`import bot as host` می‌کنه؛ چون تا وقتی این توابع صدا زده بشن (از هندلرها)
bot.py قبلاً کامل لود شده.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
import datetime
from collections import defaultdict, deque

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes, ApplicationHandlerStop

import config

logger = logging.getLogger("lock_engine")

STATE_FILE = getattr(config, "LOCK_ENGINE_STATE_FILE", "lock_engine_state.json")

# ---------------------------------------------------------------------------
# 🔐 ثابت‌ها / تنظیمات پیش‌فرض موتور
# ---------------------------------------------------------------------------

ROLE_OWNER = "OWNER"
ROLE_ADMIN = "ADMIN"
ROLE_VIP = "VIP"
ROLE_FRIEND = "FRIEND"
ROLE_NORMAL = "NORMAL"
ROLE_ENEMY = "ENEMY"
ROLE_BLACKLIST = "BLACKLIST"

ROLE_ORDER = [ROLE_OWNER, ROLE_ADMIN, ROLE_VIP, ROLE_FRIEND, ROLE_NORMAL, ROLE_ENEMY, ROLE_BLACKLIST]

ROLE_LABELS = {
    ROLE_OWNER: "👑 مالک",
    ROLE_ADMIN: "🛡 ادمین",
    ROLE_VIP: "⭐ VIP",
    ROLE_FRIEND: "🤝 دوست",
    ROLE_NORMAL: "👤 عادی",
    ROLE_ENEMY: "☠️ دشمن",
    ROLE_BLACKLIST: "🚫 بلاک‌لیست",
}

# اکشن‌های ممکن برای هر قفل (اجزای قابل ترکیب)
ACTION_DELETE = "delete"
ACTION_WARN = "warn"
ACTION_MUTE = "mute"
ACTION_KICK = "kick"
ACTION_BAN = "ban"

ACTION_PRESETS = {
    "delete": [ACTION_DELETE],
    "warn": [ACTION_DELETE, ACTION_WARN],
    "mute": [ACTION_DELETE, ACTION_MUTE],
    "kick": [ACTION_DELETE, ACTION_KICK],
    "ban": [ACTION_DELETE, ACTION_BAN],
    "delete_warn": [ACTION_DELETE, ACTION_WARN],
    "delete_mute": [ACTION_DELETE, ACTION_MUTE],
    "delete_warn_mute": [ACTION_DELETE, ACTION_WARN, ACTION_MUTE],
    "auto": ["auto"],  # یعنی از Smart Escalation استفاده کن
}

# نردبان تشدید پیش‌فرض (Smart Escalation) - بر اساس تعداد تخلفِ همون نوع قفل توسط همون کاربر
DEFAULT_ESCALATION_LADDER = [
    {"count": 1, "action": ["delete"]},
    {"count": 2, "action": ["delete", "warn"]},
    {"count": 3, "action": ["delete", "mute"], "mute_minutes": 10},
    {"count": 4, "action": ["mute"], "mute_minutes": 60},
    {"count": 5, "action": ["ban"]},
]

DEFAULT_COOLDOWN_SECONDS = 25          # آنتی-ریپیت: توی این بازه فقط یه اخطار بفرست
DEFAULT_FLOOD_WINDOW_SECONDS = 15
DEFAULT_FLOOD_LIMIT = 6
DEFAULT_VIOLATION_LOG_RETENTION_DAYS = 30
DEFAULT_AUDIT_LOG_MAXLEN = 300
DEFAULT_VIOLATION_LOG_MAXLEN = 4000
SCORE_DECAY_PER_HOUR = 1.0
SCORE_WEIGHTS = {
    "links": 2, "forward": 2, "forward_channel": 2, "spam_words": 3,
    "flood": 4, "media": 2, "photo": 2, "video": 2, "document": 2,
    "audio": 2, "voice": 2, "gif": 2, "sticker": 1, "mention": 1,
    "hashtag": 1, "contact": 1, "location": 1, "poll": 1, "game": 1,
    "bot": 2, "edited": 1, "caps": 1, "long_message": 1, "inline": 2,
}

SCORE_LEVELS = [
    (0, 4, "🟢 SAFE"),
    (5, 9, "🟡 WATCH"),
    (10, 17, "🟠 WARNING"),
    (18, 29, "🔴 DANGEROUS"),
    (30, 10 ** 9, "🚨 CRITICAL"),
]

# ---------------------------------------------------------------------------
# 🔒 رجیستری انواع قفل
# هر قفل یا از یه کلید موجود توی _chat_settings استفاده می‌کنه (برای اینکه با
# /menu فعلی هم‌خوان بمونه و دوتا سیستم متناقض نسازیم)، یا کلید جدیدیه که به
# config.DEFAULT_SETTINGS اضافه شده (نگاه کن به بخش انتهای config.py).
# ---------------------------------------------------------------------------

LOCK_TYPES = {
    "links":           {"emoji": "🔗", "label": "لینک",              "setting_key": "lock_links"},
    "photo":           {"emoji": "🖼", "label": "عکس",                "setting_key": "lock_photo"},
    "video":           {"emoji": "🎥", "label": "ویدیو",              "setting_key": "lock_video"},
    "document":        {"emoji": "📄", "label": "فایل/سند",           "setting_key": "lock_document"},
    "audio":           {"emoji": "🎵", "label": "صدا (آهنگ)",         "setting_key": "lock_audio"},
    "voice":           {"emoji": "🎤", "label": "ویس",                "setting_key": "lock_voice"},
    "gif":             {"emoji": "🎞", "label": "گیف",                "setting_key": "lock_all_gifs"},
    "sticker":         {"emoji": "🧩", "label": "استیکر",             "setting_key": "lock_all_stickers"},
    "forward":         {"emoji": "🔁", "label": "فوروارد",            "setting_key": "lock_forward"},
    "forward_channel": {"emoji": "📺", "label": "فوروارد کانال",      "setting_key": "antiforward_channel"},
    "contact":         {"emoji": "👤", "label": "مخاطب",              "setting_key": "lock_contacts"},
    "location":        {"emoji": "📍", "label": "موقعیت مکانی",       "setting_key": "lock_location"},
    "poll":            {"emoji": "📊", "label": "نظرسنجی",            "setting_key": "lock_poll"},
    "game":            {"emoji": "🎮", "label": "بازی/تاس",           "setting_key": "lock_game"},
    "media":           {"emoji": "📱", "label": "رسانه (همه)",        "setting_key": "lock_media"},
    "mention":         {"emoji": "🏷", "label": "منشن/تگ",            "setting_key": "lock_tags"},
    "hashtag":         {"emoji": "#️⃣", "label": "هشتگ",               "setting_key": "lock_hashtags"},
    "edited":          {"emoji": "✏️", "label": "ویرایش پیام",        "setting_key": "lock_edited"},
    "bot":             {"emoji": "🤖", "label": "ربات دیگر",          "setting_key": "lock_bots"},
    "group":           {"emoji": "🔐", "label": "بستن گروه",          "setting_key": "lock_group"},
    "text":            {"emoji": "💬", "label": "متن",                "setting_key": "lock_text"},
    "long_message":    {"emoji": "🔢", "label": "پیام طولانی",        "setting_key": "lock_long_message"},
    "caps":            {"emoji": "🔠", "label": "کاپس‌لاک",           "setting_key": "anticaps"},
    "spam_words":      {"emoji": "🧹", "label": "کلمات/محتوای اسپم",  "setting_key": "antispam_words"},
    "inline":          {"emoji": "📨", "label": "محتوای اینلاین",     "setting_key": "lock_inline"},
}

# قفل‌هایی که در سطح «پایپ‌لاین پیام معمولی» شناسایی می‌شن (نه در welcome/edited جدا)
_CONTENT_ALIASES = {
    # کلید داخلی -> (attr روی message که وجودش یعنی این نوع محتواست)
    "photo": "photo", "video": "video", "document": "document",
    "audio": "audio", "voice": "voice", "gif": "animation",
    "sticker": "sticker", "contact": "contact", "location": "location",
    "poll": "poll", "game": "dice",
}
_MEDIA_KEYS = {"photo", "video", "document", "audio", "voice"}

LINK_RE = re.compile(
    r"(https?://\S+|t\.me/\S+|telegram\.me/\S+|www\.[^\s]+\.[a-z]{2,}|"
    r"@[a-zA-Z][a-zA-Z0-9_]{4,}|\b[a-zA-Z0-9-]+\[?\.\]?[a-zA-Z]{2,}\b|"
    r"\b\w+\s+dot\s+\w+\b)",
    re.IGNORECASE,
)
# برای استخراج «فقط دامنه» جهت چک وایت‌لیست/بلک‌لیست
DOMAIN_RE = re.compile(r"([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}")
MENTION_RE = re.compile(r"@\w{4,}")
HASHTAG_RE = re.compile(r"#\w+")

WARN_TEMPLATES = {
    "links": [
        "🔗 ارسال لینک در این گروه مجاز نیست.",
        "⚠️ لینک‌ها اینجا قفل هستن، پیامت پاک شد.",
        "🔒 این بخش نسبت به لینک حساسه؛ لطفاً ارسال نکن.",
    ],
    "default": [
        "⚠️ این نوع محتوا در این گروه قفل شده است.",
        "🔒 این بخش توسط مدیریت بسته شده است.",
        "🚫 ارسال این نوع پیام اینجا مجاز نیست.",
    ],
}

WEEKDAY_KEYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]

# ---------------------------------------------------------------------------
# 💾 State (در حافظه)
# ---------------------------------------------------------------------------

def _new_lock_cfg():
    return {
        "action": "auto",              # auto | delete | warn | mute | kick | ban | delete_warn | ...
        "mute_minutes": None,           # override؛ None یعنی طبق Escalation/پیش‌فرض
        "cooldown_seconds": DEFAULT_COOLDOWN_SECONDS,
        "new_member_only": False,
        "new_member_minutes": None,     # None یعنی از config.NEW_MEMBER_GRACE_MINUTES استفاده کن
        "old_member_exempt_minutes": 0,
        "role_bypass": {                # نقش‌هایی که از این قفل خاص مستثنی‌ان
            ROLE_OWNER: True, ROLE_ADMIN: True, ROLE_VIP: True,
            ROLE_FRIEND: False, ROLE_NORMAL: False,
            ROLE_ENEMY: False, ROLE_BLACKLIST: False,
        },
        "flood_enabled": False,
        "flood_limit": DEFAULT_FLOOD_LIMIT,
        "flood_window": DEFAULT_FLOOD_WINDOW_SECONDS,
        "threshold": None,               # برای long_message: تعداد کاراکتر
    }


_lock_config = defaultdict(lambda: defaultdict(_new_lock_cfg))
_exceptions = defaultdict(set)                       # chat_id -> set(user_id) معاف از همه‌ی قفل‌ها
_whitelist_domains = defaultdict(set)                # chat_id -> set(domain)
_blacklist_domains = defaultdict(set)                # chat_id -> set(domain)
_temp_locks = defaultdict(dict)                       # chat_id -> {lock_key: expire_ts}
_schedules = defaultdict(dict)                        # chat_id -> {lock_key: {"start","end","days"}}
_custom_profiles = defaultdict(dict)                  # chat_id -> {profile_name: {lock_key: bool}}
_emergency_snapshot = defaultdict(lambda: None)       # chat_id -> dict|None
_dry_run = defaultdict(bool)                          # chat_id -> bool
_edited_mode = defaultdict(lambda: "delete_any")      # chat_id -> "delete_any" | "smart"

_violations = defaultdict(lambda: defaultdict(lambda: {
    "count_by_type": defaultdict(int), "score": 0.0, "last_ts": 0.0,
    "last_type": None,
}))                                                    # chat_id -> user_id -> {...}

_flood_windows = defaultdict(lambda: defaultdict(lambda: defaultdict(deque)))  # chat->user->lock_key->deque(ts)
_last_notice = defaultdict(dict)                       # chat_id -> (user_id, lock_key) -> ts
_template_rotation = defaultdict(int)                  # lock_key -> index

_violation_log = defaultdict(lambda: deque(maxlen=DEFAULT_VIOLATION_LOG_MAXLEN))   # chat_id -> deque(entry)
_audit_log = defaultdict(lambda: deque(maxlen=DEFAULT_AUDIT_LOG_MAXLEN))            # chat_id -> deque(entry)

_bot_allowlist = defaultdict(set)                      # chat_id -> set(bot_user_id) مجاز با وجود lock_bots


# ---------------------------------------------------------------------------
# 🎛️ پروفایل‌های آماده
# ---------------------------------------------------------------------------

def _all_off():
    return {k: False for k in LOCK_TYPES}


PRESET_PROFILES = {
    "NORMAL": {**_all_off()},
    "STRICT": {**_all_off(), "links": True, "media": True, "gif": True, "sticker": True,
               "forward": True, "forward_channel": True, "mention": True, "hashtag": True,
               "contact": True, "location": True, "spam_words": True},
    "ANTI_SPAM": {**_all_off(), "links": True, "forward": True, "spam_words": True, "caps": True},
    "NIGHT": {**_all_off(), "media": True, "gif": True, "sticker": True, "links": True},
    "EMERGENCY": {**_all_off(), "group": True},
    "CHAT_MODE": {**_all_off(), "spam_words": True},
}


# ---------------------------------------------------------------------------
# کمکی‌های عمومی (بدون وابستگی به bot.py)
# ---------------------------------------------------------------------------

def _now() -> float:
    return time.time()


def get_lock_cfg(chat_id: int, lock_key: str) -> dict:
    return _lock_config[chat_id][lock_key]


def is_dry_run(chat_id: int) -> bool:
    return _dry_run[chat_id]


def set_dry_run(chat_id: int, value: bool):
    _dry_run[chat_id] = value


def _extract_domain(token: str) -> str | None:
    token = token.strip().lower()
    token = token.replace("[.]", ".").replace(" dot ", ".")
    token = re.sub(r"^https?://", "", token)
    token = re.sub(r"^www\.", "", token)
    token = token.lstrip("@")
    m = DOMAIN_RE.search(token)
    if m:
        return m.group(0).rstrip("/")
    return None


def find_links(text: str) -> list[str]:
    if not text:
        return []
    return LINK_RE.findall(text)


def domain_is_whitelisted(chat_id: int, domain: str) -> bool:
    if not domain:
        return False
    domain = domain.lower()
    global_wl = {d.lower() for d in getattr(config, "LINK_WHITELIST_DOMAINS", [])}
    for wl in (_whitelist_domains[chat_id] | global_wl):
        if domain == wl or domain.endswith("." + wl):
            return True
    return False


def domain_is_blacklisted(chat_id: int, domain: str) -> bool:
    if not domain:
        return False
    domain = domain.lower()
    for bl in _blacklist_domains[chat_id]:
        if domain == bl or domain.endswith("." + bl):
            return True
    return False


def _score_level_label(score: float) -> str:
    for lo, hi, label in SCORE_LEVELS:
        if lo <= score <= hi:
            return label
    return SCORE_LEVELS[-1][2]


def _decayed_score(entry: dict) -> float:
    elapsed_hours = max(0.0, (_now() - entry["last_ts"]) / 3600.0)
    decayed = entry["score"] - elapsed_hours * SCORE_DECAY_PER_HOUR
    return max(0.0, decayed)


# ---------------------------------------------------------------------------
# 🧑‍⚖️ نقش کاربر (از ساختارهای موجود بات استفاده می‌کنه، نه سیستم موازی)
# ---------------------------------------------------------------------------

def get_role(chat_id: int, user_id: int) -> str:
    import bot as host  # local import - جلوگیری از circular import

    admin_ids = list(getattr(config, "ADMIN_IDS", []))
    if admin_ids and user_id == admin_ids[0]:
        return ROLE_OWNER
    if host.is_admin(user_id):
        return ROLE_ADMIN
    if user_id in _exceptions[chat_id]:
        return ROLE_VIP  # استثنای دستی هم مثل VIP از همه‌چیز معافه
    if user_id in host._vip_users[chat_id]:
        return ROLE_VIP
    if user_id in host._blacklist[chat_id]:
        return ROLE_BLACKLIST
    if user_id in host._enemies[chat_id]:
        return ROLE_ENEMY
    if user_id in host._friends[chat_id]:
        return ROLE_FRIEND
    return ROLE_NORMAL


def is_bypassed(chat_id: int, user_id: int, lock_key: str) -> bool:
    role = get_role(chat_id, user_id)
    cfg = get_lock_cfg(chat_id, lock_key)
    return bool(cfg["role_bypass"].get(role, False))


# ---------------------------------------------------------------------------
# ⏱️ Temporary Lock / 🌙 Scheduled Lock
# ---------------------------------------------------------------------------

def set_temp_lock(chat_id: int, lock_key: str, seconds: int):
    _temp_locks[chat_id][lock_key] = _now() + seconds
    _set_setting(chat_id, lock_key, True)


def clear_temp_lock(chat_id: int, lock_key: str, turn_off: bool = True):
    _temp_locks[chat_id].pop(lock_key, None)
    if turn_off:
        _set_setting(chat_id, lock_key, False)


def temp_lock_remaining(chat_id: int, lock_key: str) -> int | None:
    exp = _temp_locks[chat_id].get(lock_key)
    if exp is None:
        return None
    remaining = int(exp - _now())
    return max(0, remaining)


async def check_temp_lock_expiry(bot=None):
    """باید دوره‌ای (مثلاً هر ۳۰-۶۰ ثانیه) صدا زده بشه - در _periodic_save_loop بات."""
    now = _now()
    for chat_id, locks in list(_temp_locks.items()):
        for lock_key, exp in list(locks.items()):
            if now >= exp:
                clear_temp_lock(chat_id, lock_key, turn_off=True)
                if bot is not None:
                    try:
                        label = LOCK_TYPES.get(lock_key, {}).get("label", lock_key)
                        await bot.send_message(chat_id=chat_id, text=f"⏱️ قفل موقت «{label}» به پایان رسید و خاموش شد.")
                    except Exception:
                        pass


def set_schedule(chat_id: int, lock_key: str, start: str, end: str, days: list[str] | None):
    _schedules[chat_id][lock_key] = {"start": start, "end": end, "days": days}


def clear_schedule(chat_id: int, lock_key: str):
    _schedules[chat_id].pop(lock_key, None)


def _schedule_active(chat_id: int, lock_key: str) -> bool:
    sched = _schedules[chat_id].get(lock_key)
    if not sched:
        return False
    try:
        import bot as host
        now_local = host._now_local()
    except Exception:
        now_local = datetime.datetime.now()

    if sched.get("days"):
        weekday_key = WEEKDAY_KEYS[now_local.weekday()]
        if weekday_key not in sched["days"]:
            return False

    start_h, start_m = map(int, sched["start"].split(":"))
    end_h, end_m = map(int, sched["end"].split(":"))
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    now_minutes = now_local.hour * 60 + now_local.minute

    if start_minutes <= end_minutes:
        return start_minutes <= now_minutes < end_minutes
    # بازه‌ای که از نیمه‌شب رد می‌شه (مثلاً ۲۳:۰۰ تا ۰۸:۰۰)
    return now_minutes >= start_minutes or now_minutes < end_minutes


def _set_setting(chat_id: int, lock_key: str, value: bool):
    import bot as host
    key = LOCK_TYPES.get(lock_key, {}).get("setting_key")
    if not key:
        return
    host._chat_settings[chat_id][key] = value


def _get_setting(chat_id: int, lock_key: str) -> bool:
    import bot as host
    key = LOCK_TYPES.get(lock_key, {}).get("setting_key")
    if not key:
        return False
    return host._chat_settings[chat_id].get(key, config.DEFAULT_SETTINGS.get(key, False))


def is_effectively_enabled(chat_id: int, lock_key: str) -> bool:
    return bool(
        _get_setting(chat_id, lock_key)
        or lock_key in _temp_locks[chat_id]
        or _schedule_active(chat_id, lock_key)
    )


# ---------------------------------------------------------------------------
# 🎯 Profiles
# ---------------------------------------------------------------------------

def apply_profile(chat_id: int, profile: dict):
    for lock_key, enabled in profile.items():
        if lock_key in LOCK_TYPES:
            _set_setting(chat_id, lock_key, bool(enabled))


def snapshot_current(chat_id: int) -> dict:
    return {k: is_effectively_enabled(chat_id, k) for k in LOCK_TYPES}


def save_custom_profile(chat_id: int, name: str):
    _custom_profiles[chat_id][name] = snapshot_current(chat_id)


def load_custom_profile(chat_id: int, name: str) -> bool:
    profile = _custom_profiles[chat_id].get(name)
    if profile is None:
        return False
    apply_profile(chat_id, profile)
    return True


def delete_custom_profile(chat_id: int, name: str) -> bool:
    return _custom_profiles[chat_id].pop(name, None) is not None


def enter_emergency(chat_id: int):
    if _emergency_snapshot[chat_id] is not None:
        return False  # از قبل توی Emergency هستیم
    _emergency_snapshot[chat_id] = snapshot_current(chat_id)
    apply_profile(chat_id, PRESET_PROFILES["EMERGENCY"])
    return True


def exit_emergency(chat_id: int) -> bool:
    snap = _emergency_snapshot[chat_id]
    if snap is None:
        return False
    apply_profile(chat_id, snap)
    _emergency_snapshot[chat_id] = None
    return True


def in_emergency(chat_id: int) -> bool:
    return _emergency_snapshot[chat_id] is not None


# ---------------------------------------------------------------------------
# 📈 Violation tracking / Escalation / Score
# ---------------------------------------------------------------------------

def _record_violation(chat_id: int, user_id: int, lock_key: str) -> int:
    """تخلف رو ثبت می‌کنه و تعداد تخلف از این نوع (برای این کاربر) رو برمی‌گردونه."""
    entry = _violations[chat_id][user_id]
    entry["score"] = _decayed_score(entry) + SCORE_WEIGHTS.get(lock_key, 2)
    entry["last_ts"] = _now()
    entry["last_type"] = lock_key
    entry["count_by_type"][lock_key] += 1
    return entry["count_by_type"][lock_key]


def get_user_lock_profile(chat_id: int, user_id: int) -> dict:
    entry = _violations[chat_id].get(user_id)
    if not entry:
        return {"total": 0, "by_type": {}, "score": 0.0, "level": _score_level_label(0), "last_type": None}
    total = sum(entry["count_by_type"].values())
    return {
        "total": total,
        "by_type": dict(entry["count_by_type"]),
        "score": round(_decayed_score(entry), 1),
        "level": _score_level_label(_decayed_score(entry)),
        "last_type": entry["last_type"],
        "last_ts": entry["last_ts"],
    }


def _escalation_ladder(chat_id: int) -> list:
    return getattr(config, "LOCK_ESCALATION_LADDER_OVERRIDE", {}).get(chat_id, DEFAULT_ESCALATION_LADDER)


def _resolve_action(chat_id: int, lock_key: str, violation_count: int) -> tuple[list, int | None]:
    """اکشن نهایی + مدت میوت (دقیقه، اگه لازم باشه) رو برمی‌گردونه."""
    cfg = get_lock_cfg(chat_id, lock_key)
    configured = cfg.get("action", "auto")
    if configured != "auto":
        return ACTION_PRESETS.get(configured, [ACTION_DELETE]), cfg.get("mute_minutes")

    ladder = _escalation_ladder(chat_id)
    step = ladder[-1]
    for item in ladder:
        if violation_count <= item["count"]:
            step = item
            break
    return step["action"], step.get("mute_minutes")


def _pick_template(lock_key: str) -> str:
    pool = WARN_TEMPLATES.get(lock_key, WARN_TEMPLATES["default"])
    idx = _template_rotation[lock_key] % len(pool)
    _template_rotation[lock_key] += 1
    return pool[idx]


def _cooldown_ok(chat_id: int, user_id: int, lock_key: str) -> bool:
    cfg = get_lock_cfg(chat_id, lock_key)
    key = (user_id, lock_key)
    last = _last_notice[chat_id].get(key, 0)
    if _now() - last < cfg["cooldown_seconds"]:
        return False
    _last_notice[chat_id][key] = _now()
    return True


# ---------------------------------------------------------------------------
# 🌊 Anti-flood per content type
# ---------------------------------------------------------------------------

def _is_flooding(chat_id: int, user_id: int, lock_key: str) -> bool:
    cfg = get_lock_cfg(chat_id, lock_key)
    if not cfg["flood_enabled"]:
        return False
    now = _now()
    window = _flood_windows[chat_id][user_id][lock_key]
    window.append(now)
    while window and now - window[0] > cfg["flood_window"]:
        window.popleft()
    return len(window) > cfg["flood_limit"]


# ---------------------------------------------------------------------------
# 📜 Log / Stats / Audit
# ---------------------------------------------------------------------------

def _log_violation(chat_id: int, user_id: int, message_id: int, lock_key: str, action: list, score: float):
    _violation_log[chat_id].append({
        "ts": _now(), "user_id": user_id, "message_id": message_id,
        "lock_type": lock_key, "action": "+".join(action), "score": round(score, 1),
    })


def log_audit(chat_id: int, admin_id: int, admin_name: str, action_desc: str):
    _audit_log[chat_id].append({
        "ts": _now(), "admin_id": admin_id, "admin_name": admin_name, "action": action_desc,
    })


def get_stats(chat_id: int) -> dict:
    now = _now()
    ranges = {"today": 86400, "week": 7 * 86400, "month": 30 * 86400, "total": None}
    result = {r: defaultdict(int) for r in ranges}
    user_totals = defaultdict(int)
    for entry in _violation_log[chat_id]:
        age = now - entry["ts"]
        user_totals[entry["user_id"]] += 1
        for r, seconds in ranges.items():
            if seconds is None or age <= seconds:
                result[r][entry["lock_type"]] += 1
    top_users = sorted(user_totals.items(), key=lambda kv: kv[1], reverse=True)[:5]
    top_locks = sorted(result["total"].items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {
        "by_range": {r: dict(v) for r, v in result.items()},
        "top_users": top_users,
        "top_locks": top_locks,
    }


def prune_logs(chat_id: int, retention_days: int = DEFAULT_VIOLATION_LOG_RETENTION_DAYS):
    cutoff = _now() - retention_days * 86400
    log = _violation_log[chat_id]
    while log and log[0]["ts"] < cutoff:
        log.popleft()


# ---------------------------------------------------------------------------
# 🧪 Auto-recovery: فراخوانی امن اکشن‌های تلگرام (بدون کرش، با یک Retry محدود)
# ---------------------------------------------------------------------------

# هر چت فقط هر یک ساعت یه بار هشدار «دسترسی ناکافی» می‌گیره (ضدِ اسپم‌شدن گروه با پیام تکراری)
_permission_alert_cooldown = defaultdict(float)
_PERMISSION_ALERT_COOLDOWN_SECONDS = 3600
_PERMISSION_ERROR_HINTS = (
    "not enough rights", "have no rights", "chat_admin_required",
    "message can't be deleted", "message to delete not found",
    "not enough right", "user_not_participant",
)


async def _alert_missing_permission(context, chat_id: int, action_label: str):
    """وقتی حذف/میوت/بن به‌خاطر کمبود دسترسی ربات (نه خطای موقت) شکست بخوره، یه پیام
    قابل‌مشاهده توی خودِ گروه می‌ده تا ادمین بفهمه مشکل از خودِ ربات (نه از تنظیمات)
    هست، نه اینکه فقط لاگ سرور (که خیلی‌ها بهش دسترسی/حوصله‌ی چک‌کردن ندارن)."""
    now = time.time()
    if now - _permission_alert_cooldown[chat_id] < _PERMISSION_ALERT_COOLDOWN_SECONDS:
        return
    _permission_alert_cooldown[chat_id] = now
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"⚠️ عملیات «{action_label}» ناموفق بود چون ربات دسترسی لازم رو توی این گروه نداره.\n"
                "لطفاً از تنظیمات گروه، ربات رو ادمین کن و مطمئن شو این دسترسی‌ها فعالن:\n"
                "• حذف پیام (Delete Messages)\n"
                "• مسدودسازی/محدودکردن کاربران (Ban/Restrict Users)\n\n"
                "بدون این دو دسترسی، قفل‌ها و آنتی‌اسپم فقط تشخیص می‌دن ولی نمی‌تونن پیام رو پاک/کاربر رو محدود کنن."
            ),
        )
    except Exception:
        pass


def _looks_like_permission_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(hint in msg for hint in _PERMISSION_ERROR_HINTS)


async def _safe_call(coro_factory, what: str, retries: int = 1, context=None, chat_id=None, alert_label: str | None = None):
    for attempt in range(retries + 1):
        try:
            return await coro_factory()
        except BadRequest as e:
            logger.warning(f"[LockEngine] {what} ناموفق (BadRequest): {e}")
            if context is not None and chat_id is not None and _looks_like_permission_error(e):
                await _alert_missing_permission(context, chat_id, alert_label or what)
            return None  # خطای پرمیشن/غیره - رتری فایده نداره
        except TelegramError as e:
            logger.warning(f"[LockEngine] {what} ناموفق (تلاش {attempt + 1}): {e}")
            if attempt >= retries:
                if context is not None and chat_id is not None and _looks_like_permission_error(e):
                    await _alert_missing_permission(context, chat_id, alert_label or what)
                return None
        except Exception as e:
            logger.warning(f"[LockEngine] {what} خطای غیرمنتظره: {e}")
            return None


# ---------------------------------------------------------------------------
# ⚡ Action Engine
# ---------------------------------------------------------------------------

async def _log_to_moderation_engine(chat_id, user_id, action, reason, duration_seconds):
    """اکشنی که Lock Engine خودش (طبق منطق تشخیص/نردبان تشدید خودش) واقعاً زده رو، فقط
    برای یکپارچه‌سازی تاریخچه/ریسک/آمار مرکزی، به moderation_engine اطلاع می‌ده. هیچ اکشن
    تلگرامی دوباره اجرا نمی‌شه؛ در نتیجه Duplicate Action اتفاق نمی‌افته. اگه moderation_engine
    در دسترس نباشه یا خطا بده، Lock Engine مثل قبل بدون مشکل کار می‌کنه."""
    try:
        import moderation_engine
        await moderation_engine.log_auto_action(
            chat_id, user_id, action, reason, source="LOCK_ENGINE", duration_seconds=duration_seconds,
        )
    except Exception as e:
        logger.warning(f"یکپارچه‌سازی با moderation_engine ناموفق بود: {e}")


async def _apply_action(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, display_name: str,
    actions: list, mute_minutes: int | None, message, lock_key: str, reason: str,
):
    import bot as host

    dry = is_dry_run(chat_id)

    if ACTION_DELETE in actions and message is not None:
        if dry:
            logger.info(f"🧪 LOCK TEST | User: {user_id} | Detected: {lock_key} | Action would be: DELETE")
        else:
            await _safe_call(lambda: message.delete(), "حذف پیام",
                              context=context, chat_id=chat_id, alert_label="حذف پیام")

    notice_allowed = _cooldown_ok(chat_id, user_id, lock_key)

    if ACTION_WARN in actions and notice_allowed:
        template = _pick_template(lock_key)
        if dry:
            logger.info(f"🧪 LOCK TEST | User: {user_id} | Detected: {lock_key} | Action would be: WARN")
        else:
            await _safe_call(
                lambda: host._warn_and_maybe_mute(chat_id, user_id, display_name, context, f"{template} ({reason})"),
                "اخطار",
            )

    if ACTION_MUTE in actions:
        minutes = mute_minutes or getattr(config, "MUTE_DURATION_MINUTES", 30)
        if dry:
            logger.info(f"🧪 LOCK TEST | User: {user_id} | Detected: {lock_key} | Action would be: MUTE {minutes}m")
        else:
            until = int(time.time()) + minutes * 60

            async def _do_mute():
                await context.bot.restrict_chat_member(
                    chat_id=chat_id, user_id=user_id,
                    permissions=ChatPermissions(can_send_messages=False), until_date=until,
                )

            result = await _safe_call(_do_mute, "میوت", context=context, chat_id=chat_id, alert_label="میوت کاربر")
            if result is not None or True:
                host._active_mutes[chat_id][user_id] = until
                if notice_allowed:
                    await _safe_call(
                        lambda: context.bot.send_message(
                            chat_id=chat_id,
                            text=f"🔇 {display_name} به‌خاطر «{LOCK_TYPES.get(lock_key, {}).get('label', lock_key)}» برای {minutes} دقیقه میوت شد.",
                        ),
                        "پیام میوت",
                    )
                await _log_to_moderation_engine(chat_id, user_id, "mute", f"{lock_key}: {reason}", minutes * 60)

    if ACTION_KICK in actions:
        if dry:
            logger.info(f"🧪 LOCK TEST | User: {user_id} | Detected: {lock_key} | Action would be: KICK")
        else:
            async def _do_kick():
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)

            await _safe_call(_do_kick, "اخراج", context=context, chat_id=chat_id, alert_label="اخراج کاربر")
            if notice_allowed:
                await _safe_call(
                    lambda: context.bot.send_message(chat_id=chat_id, text=f"👢 {display_name} از گروه اخراج شد."),
                    "پیام اخراج",
                )
            await _log_to_moderation_engine(chat_id, user_id, "kick", f"{lock_key}: {reason}", None)

    if ACTION_BAN in actions:
        if dry:
            logger.info(f"🧪 LOCK TEST | User: {user_id} | Detected: {lock_key} | Action would be: BAN")
        else:
            await _safe_call(lambda: context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id), "بن",
                              context=context, chat_id=chat_id, alert_label="بن کاربر")
            host._banned_users[chat_id].add(user_id)
            if notice_allowed:
                await _safe_call(
                    lambda: context.bot.send_message(chat_id=chat_id, text=f"🚫 {display_name} به‌خاطر تخلف تکراری بن شد."),
                    "پیام بن",
                )
            await _log_to_moderation_engine(chat_id, user_id, "ban", f"{lock_key}: {reason}", None)

    await _safe_call(
        lambda: host._log_admin_action(
            context, chat_id,
            f"🔐 Lock Engine\n{LOCK_TYPES.get(lock_key, {}).get('emoji', '🔒')} نوع: {LOCK_TYPES.get(lock_key, {}).get('label', lock_key)}\n"
            f"👤 {display_name}\n⚡ اکشن: {'+'.join(actions)}\n📝 {reason}"
            + (" (🧪 حالت تست - اجرا نشد)" if dry else ""),
        ),
        "لاگ ادمین",
    )


async def handle_violation(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, display_name: str,
    message, lock_key: str, reason: str,
):
    count = _record_violation(chat_id, user_id, lock_key)
    actions, mute_minutes = _resolve_action(chat_id, lock_key, count)
    score = get_user_lock_profile(chat_id, user_id)["score"]
    _log_violation(chat_id, user_id, message.message_id if message else 0, lock_key, actions, score)
    await _apply_action(context, chat_id, user_id, display_name, actions, mute_minutes, message, lock_key, reason)


# ---------------------------------------------------------------------------
# 🧠 تشخیص محتوا (Content-Aware)
# ---------------------------------------------------------------------------

def _detect_lock_key(message) -> list[str]:
    """همه‌ی نوع‌های محتوایی که این پیام باهاشون منطبقه رو برمی‌گردونه (می‌تونه چندتایی باشه:
    مثلاً عکس با کپشن لینک‌دار = ['photo', 'links']، اگه شرایطش برقرار باشه)."""
    hits = []
    for key, attr in _CONTENT_ALIASES.items():
        if getattr(message, attr, None):
            hits.append(key)
    if message.via_bot is not None:
        hits.append("inline")
    return hits


def _is_forwarded_from_channel(message) -> bool:
    import bot as host
    return host._is_forwarded_from_channel(message)


def _is_forwarded_any(message) -> bool:
    return bool(
        getattr(message, "forward_origin", None)
        or getattr(message, "forward_from", None)
        or getattr(message, "forward_from_chat", None)
    )


# ---------------------------------------------------------------------------
# 🚦 پایپ‌لاین اصلی (Message ↓ Permission ↓ Detection ↓ Rule ↓ Violation ↓ Action ↓ Log)
# ---------------------------------------------------------------------------

async def process_message(update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """اگه پیام به‌خاطر یکی از قفل‌ها هندل (حذف/اقدام) شد True برمی‌گردونه تا
    caller بتونه ApplicationHandlerStop بندازه. اگه چیزی پیدا نشد False."""
    import bot as host

    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user or chat.type not in ("group", "supergroup"):
        return False

    settings = host._chat_settings[chat.id]
    if not settings.get("bot_enabled", False):
        return False

    display_name = user.first_name or user.username or "کاربر"
    role = get_role(chat.id, user.id)

    # ---------- ربات دیگری که پیام می‌فرسته ----------
    if user.is_bot and is_effectively_enabled(chat.id, "bot") and user.id not in _bot_allowlist[chat.id]:
        if not is_bypassed(chat.id, user.id, "bot"):
            await handle_violation(context, chat.id, user.id, display_name, message, "bot", "ربات مجاز نیست")
            return True

    # ---------- بستن کامل گروه ----------
    if is_effectively_enabled(chat.id, "group") and role not in (ROLE_OWNER, ROLE_ADMIN, ROLE_VIP):
        if is_dry_run(chat.id):
            logger.info(f"🧪 LOCK TEST | User: {user.id} | Detected: group | Action would be: DELETE")
        else:
            await _safe_call(lambda: message.delete(), "حذف پیام (گروه بسته)",
                              context=context, chat_id=chat.id, alert_label="حذف پیام")
        _log_violation(chat.id, user.id, message.message_id, "group", ["delete"], 0)
        return True

    text = message.text or message.caption or ""

    # ---------- محتوای واقعی پیام (عکس/ویدیو/فایل/صدا/ویس/گیف/استیکر/مخاطب/موقعیت/نظرسنجی/بازی/اینلاین) ----------
    for lock_key in _detect_lock_key(message):
        if is_bypassed(chat.id, user.id, lock_key):
            continue
        cfg = get_lock_cfg(chat.id, lock_key)
        active = is_effectively_enabled(chat.id, lock_key) or (
            lock_key in _MEDIA_KEYS and is_effectively_enabled(chat.id, "media")
        )
        if cfg["new_member_only"] and active:
            grace = cfg["new_member_minutes"] or getattr(config, "NEW_MEMBER_GRACE_MINUTES", 10)
            join_time = host._join_times[chat.id].get(user.id)
            if not join_time or (_now() - join_time) > grace * 60:
                active = False
        if active and cfg["old_member_exempt_minutes"]:
            join_time = host._join_times[chat.id].get(user.id)
            if join_time and (_now() - join_time) >= cfg["old_member_exempt_minutes"] * 60:
                active = False
        if active:
            reason = f"ارسال {LOCK_TYPES.get(lock_key, {}).get('label', lock_key)}"
            await handle_violation(context, chat.id, user.id, display_name, message, lock_key, reason)
            return True
        if _is_flooding(chat.id, user.id, lock_key):
            await handle_violation(context, chat.id, user.id, display_name, message, lock_key, "فلود " + LOCK_TYPES[lock_key]["label"])
            return True

    # ---------- فوروارد از کانال (زیرمجموعه‌ی دقیق‌تر از فوروارد عادی) ----------
    if _is_forwarded_from_channel(message) and not is_bypassed(chat.id, user.id, "forward_channel"):
        if is_effectively_enabled(chat.id, "forward_channel"):
            await handle_violation(context, chat.id, user.id, display_name, message, "forward_channel", "فوروارد از کانال")
            return True

    # ---------- فوروارد عادی ----------
    if _is_forwarded_any(message) and not is_bypassed(chat.id, user.id, "forward"):
        if is_effectively_enabled(chat.id, "forward"):
            await handle_violation(context, chat.id, user.id, display_name, message, "forward", "فوروارد پیام")
            return True
        if _is_flooding(chat.id, user.id, "forward"):
            await handle_violation(context, chat.id, user.id, display_name, message, "forward", "فلود فوروارد")
            return True

    # ---------- لینک هوشمند (با وایت‌لیست/بلک‌لیست دامنه) ----------
    if text and not is_bypassed(chat.id, user.id, "links"):
        links = find_links(text)
        if links:
            domains = [d for d in (_extract_domain(t) for t in links) if d]
            blacklisted = any(domain_is_blacklisted(chat.id, d) for d in domains)
            whitelisted_all = domains and all(domain_is_whitelisted(chat.id, d) for d in domains)
            active = is_effectively_enabled(chat.id, "links")

            new_member_link_lock = False
            grace = getattr(config, "NEW_MEMBER_GRACE_MINUTES", 10)
            jt = host._join_times[chat.id].get(user.id)
            if settings.get("antinew_account_links", False) and jt and (_now() - jt) <= grace * 60:
                new_member_link_lock = True

            old_minutes = getattr(config, "LINK_ALLOWED_FOR_OLD_MEMBERS_MINUTES", 0)
            old_exempt = False
            if old_minutes and jt and (_now() - jt) >= old_minutes * 60:
                old_exempt = True

            should_block = blacklisted or (
                not whitelisted_all and not old_exempt and (active or new_member_link_lock)
            )
            if should_block:
                reason = "لینک از عضو تازه‌وارد" if new_member_link_lock and not active else "ارسال لینک"
                await handle_violation(context, chat.id, user.id, display_name, message, "links", reason)
                return True
            if active and _is_flooding(chat.id, user.id, "links"):
                await handle_violation(context, chat.id, user.id, display_name, message, "links", "فلود لینک")
                return True

    # ---------- منشن/تگ ----------
    if text and MENTION_RE.search(text) and is_effectively_enabled(chat.id, "mention") and not is_bypassed(chat.id, user.id, "mention"):
        await handle_violation(context, chat.id, user.id, display_name, message, "mention", "ارسال منشن/تگ")
        return True

    # ---------- هشتگ ----------
    if text and HASHTAG_RE.search(text) and is_effectively_enabled(chat.id, "hashtag") and not is_bypassed(chat.id, user.id, "hashtag"):
        await handle_violation(context, chat.id, user.id, display_name, message, "hashtag", "ارسال هشتگ")
        return True

    # ---------- پیام طولانی ----------
    if text and is_effectively_enabled(chat.id, "long_message") and not is_bypassed(chat.id, user.id, "long_message"):
        threshold = get_lock_cfg(chat.id, "long_message")["threshold"] or 500
        if len(text) > threshold:
            await handle_violation(context, chat.id, user.id, display_name, message, "long_message", "پیام بیش از حد طولانی")
            return True

    # ---------- قفل کامل متن ----------
    if message.text and is_effectively_enabled(chat.id, "text") and not is_bypassed(chat.id, user.id, "text"):
        await handle_violation(context, chat.id, user.id, display_name, message, "text", "ارسال پیام متنی")
        return True
    if message.text and _is_flooding(chat.id, user.id, "text"):
        await handle_violation(context, chat.id, user.id, display_name, message, "text", "فلود متن")
        return True

    return False


async def process_edited_message(update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """حالت ارتقایافته‌ی قفل ویرایش (#22). اگه mode == smart باشه، فقط وقتی نسخه‌ی
    جدید یکی از قفل‌های فعال (مثلاً لینک) رو نقض کنه حذف می‌شه؛ در غیر این صورت
    (delete_any، پیش‌فرض سازگار با رفتار قبلی پروژه) هر ویرایشی حذف می‌شه."""
    import bot as host

    message = update.edited_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or chat.type not in ("group", "supergroup"):
        return False

    settings = host._chat_settings[chat.id]
    if not settings.get("bot_enabled", False) or not settings.get("lock_edited", False):
        return False
    if is_bypassed(chat.id, user.id, "edited"):
        return False

    mode = _edited_mode[chat.id]
    if mode == "delete_any":
        await _safe_call(lambda: message.delete(), "حذف پیام ویرایش‌شده",
                          context=context, chat_id=chat.id, alert_label="حذف پیام")
        _log_violation(chat.id, user.id, message.message_id, "edited", ["delete"], 0)
        return True

    # حالت smart: با پایپ‌لاین اصلی چک کن (فقط تشخیص، بدون دوباره اسکن پیام غیرویرایشی)
    handled = await process_message(update.__class__(update.update_id, message=message), context)
    if handled:
        return True
    return False


# ---------------------------------------------------------------------------
# 🎨 پنل Inline
# ---------------------------------------------------------------------------

def build_panel(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    import bot as host

    bot_on = host._chat_settings[chat_id].get("bot_enabled", False)
    status = "🟢 فعال" if bot_on else "🔴 خاموش"

    lines = [
        "╭━━━━━━━━━━━━━━━━━━━━━━╮",
        "𓆩 𝐋𝐎𝐂𝐊 𝐂𝐄𝐍𝐓𝐄𝐑 𓆪",
        "╰━━━━━━━━━━━━━━━━━━━━━━╯",
        "",
        f"🔐 وضعیت سیستم: {status}",
        "",
    ]
    if not bot_on:
        # باگ اصلی: قفل‌ها می‌تونستن تک‌تک 🟢 بشن ولی چون سوییچ کلی خاموش بود
        # process_message() از همون خط اول برمی‌گشت و هیچ قفلی عملاً اجرا نمی‌شد.
        # این هشدار همینجا، رو دکمه‌ی زیرش، مشکل رو واضح می‌کنه.
        lines.append("⚠️ سیستم خاموشه؛ تا وقتی روشن نشه، قفل‌ها فقط نمایشی‌ان و هیچ‌کدوم واقعاً اجرا نمی‌شن.")
        lines.append("برای روشن‌کردن، روی دکمه‌ی «وضعیت سیستم» بزن.")
        lines.append("")
    keys = list(LOCK_TYPES.keys())
    for k in keys:
        info = LOCK_TYPES[k]
        on = is_effectively_enabled(chat_id, k)
        dot = "🟢" if on else "🔴"
        lines.append(f"{info['emoji']} {info['label']}  {dot}")

    buttons = [
        [InlineKeyboardButton(f"🔐 وضعیت سیستم: {'🟢 فعال (خاموش کن)' if bot_on else '🔴 خاموش (روشن کن)'}",
                               callback_data="lockpanel:toggle:__system__")],
    ]
    buttons += [
        [InlineKeyboardButton(f"{LOCK_TYPES[k]['emoji']} {LOCK_TYPES[k]['label']} {'🟢' if is_effectively_enabled(chat_id, k) else '🔴'}",
                               callback_data=f"lockpanel:toggle:{k}")]
        for k in keys
    ]
    buttons.append([
        InlineKeyboardButton("🛡️ استثناها", callback_data="lockpanel:menu:exceptions"),
        InlineKeyboardButton("⚡ اکشن‌ها", callback_data="lockpanel:menu:actions"),
    ])
    buttons.append([
        InlineKeyboardButton("⏱️ زمان‌بندی", callback_data="lockpanel:menu:schedule"),
        InlineKeyboardButton("📊 آمار", callback_data="lockpanel:menu:stats"),
    ])
    buttons.append([
        InlineKeyboardButton("🎯 پروفایل‌ها", callback_data="lockpanel:menu:profiles"),
        InlineKeyboardButton("❌ بستن", callback_data="lockpanel:close"),
    ])
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def panel_callback_handler(update, context: ContextTypes.DEFAULT_TYPE):
    import bot as host

    query = update.callback_query
    data = query.data or ""
    chat_id = query.message.chat.id
    user_id = query.from_user.id

    if not host.is_admin(user_id) and not host.has_permission(user_id, "moderate"):
        await query.answer("این دکمه فقط برای ادمین‌های مجازه.", show_alert=True)
        return

    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "close":
        await query.message.delete()
        await query.answer()
        return

    if action == "toggle" and len(parts) >= 3:
        lock_key = parts[2]

        # سوییچ کلی سیستم (bot_enabled) - قبلاً هیچ دکمه‌ای براش نبود، برای همین
        # قفل‌ها می‌تونستن تک‌تک روشن نشون داده بشن ولی چون این سوییچ خاموش بود
        # process_message() هیچ‌وقت واقعاً اجرا نمی‌شد. همین‌جا رفعش می‌کنیم.
        if lock_key == "__system__":
            new_val = not host._chat_settings[chat_id].get("bot_enabled", False)
            host._chat_settings[chat_id]["bot_enabled"] = new_val
            log_audit(chat_id, user_id, query.from_user.first_name or str(user_id),
                      f"وضعیت سیستم → {'روشن' if new_val else 'خاموش'}")
            await host.save_state()
            if new_val:
                try:
                    await host._send_on_sound_effect(context, chat_id, config.BOT_ON_SOUND, "Bot ON")
                except Exception:
                    pass
            text, markup = build_panel(chat_id)
            await _safe_call(lambda: query.edit_message_text(text, reply_markup=markup), "ویرایش پنل")
            await query.answer("✅ سیستم روشن شد؛ از این به بعد قفل‌های فعال واقعاً اجرا می‌شن." if new_val
                                else "⛔ سیستم خاموش شد؛ هیچ قفلی دیگه اجرا نمی‌شه.", show_alert=True)
            return

        new_val = not _get_setting(chat_id, lock_key)
        _set_setting(chat_id, lock_key, new_val)
        log_audit(chat_id, user_id, query.from_user.first_name or str(user_id),
                  f"{LOCK_TYPES[lock_key]['label']} → {'روشن' if new_val else 'خاموش'}")
        await host.save_state()
        text, markup = build_panel(chat_id)
        await _safe_call(lambda: query.edit_message_text(text, reply_markup=markup), "ویرایش پنل")
        if not host._chat_settings[chat_id].get("bot_enabled", False):
            await query.answer(
                f"{LOCK_TYPES[lock_key]['label']}: {'روشن شد' if new_val else 'خاموش شد'} "
                "— ولی سیستم کلاً خاموشه، پس این قفل هنوز اجرا نمی‌شه! اول «وضعیت سیستم» رو روشن کن.",
                show_alert=True,
            )
        else:
            await query.answer(f"{LOCK_TYPES[lock_key]['label']}: {'روشن شد' if new_val else 'خاموش شد'}")
        return

    if action == "menu":
        sub = parts[2] if len(parts) > 2 else ""
        if sub == "stats":
            stats = get_stats(chat_id)
            lines = ["📊 آمار قفل‌ها:", ""]
            for r_key, r_label in (("today", "امروز"), ("week", "این هفته"), ("month", "این ماه"), ("total", "کل")):
                total = sum(stats["by_range"][r_key].values())
                lines.append(f"• {r_label}: {total} تخلف")
            await query.answer()
            await query.message.reply_text("\n".join(lines))
            return
        if sub == "profiles":
            names = ", ".join(PRESET_PROFILES.keys())
            await query.answer()
            await query.message.reply_text(
                f"🎯 پروفایل‌های آماده:\n{names}\n\nفعال‌سازی: /lockprofile <نام>"
            )
            return
        if sub == "exceptions":
            excs = _exceptions[chat_id]
            await query.answer()
            await query.message.reply_text(
                "🛡️ استثناها:\n" + (", ".join(str(x) for x in excs) if excs else "خالی")
                + "\n\nافزودن: /lock exception add <آیدی>\nحذف: /lock exception remove <آیدی>"
            )
            return
        if sub == "actions":
            await query.answer()
            await query.message.reply_text(
                "⚡ تنظیم اکشن هر قفل:\n/lock <نوع> action <delete|warn|mute|kick|ban|auto>"
            )
            return
        if sub == "schedule":
            await query.answer()
            await query.message.reply_text(
                "⏱️ زمان‌بندی:\n/lock schedule <نوع> <شروع HH:MM> <پایان HH:MM> [روزها مثل MO,TU]\n"
                "لغو: /lock schedule <نوع> off"
            )
            return

    await query.answer()


# ---------------------------------------------------------------------------
# 💾 Persistence (فایل مستقل موتور قفل)
# ---------------------------------------------------------------------------

def _serialize_lock_config():
    return {
        str(cid): {lk: cfg for lk, cfg in inner.items()}
        for cid, inner in _lock_config.items()
    }


def collect_state() -> dict:
    return {
        "lock_config": _serialize_lock_config(),
        "exceptions": {str(c): list(v) for c, v in _exceptions.items()},
        "whitelist_domains": {str(c): list(v) for c, v in _whitelist_domains.items()},
        "blacklist_domains": {str(c): list(v) for c, v in _blacklist_domains.items()},
        "temp_locks": {str(c): v for c, v in _temp_locks.items()},
        "schedules": {str(c): v for c, v in _schedules.items()},
        "custom_profiles": {str(c): v for c, v in _custom_profiles.items()},
        "emergency_snapshot": {str(c): v for c, v in _emergency_snapshot.items() if v is not None},
        "dry_run": {str(c): v for c, v in _dry_run.items()},
        "edited_mode": {str(c): v for c, v in _edited_mode.items()},
        "violations": {
            str(c): {
                str(u): {
                    "count_by_type": dict(e["count_by_type"]), "score": e["score"],
                    "last_ts": e["last_ts"], "last_type": e["last_type"],
                } for u, e in inner.items()
            } for c, inner in _violations.items()
        },
        "violation_log": {str(c): list(v) for c, v in _violation_log.items()},
        "audit_log": {str(c): list(v) for c, v in _audit_log.items()},
        "bot_allowlist": {str(c): list(v) for c, v in _bot_allowlist.items()},
    }


def _write_state_file(data: dict):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


async def save_state():
    """صدا زده می‌شه از bot.py -> save_state() (async, چون اونجا هم async هست)."""
    import asyncio
    try:
        await asyncio.to_thread(_write_state_file, collect_state())
    except Exception as e:
        logger.warning(f"ذخیره‌ی state موتور قفل ناموفق بود: {e}")


def load_state():
    """صدا زده می‌شه از bot.py -> load_state() (sync)."""
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور قفل ناموفق بود: {e}")
        return

    for cid, locks in data.get("lock_config", {}).items():
        for lk, cfg in locks.items():
            base = _new_lock_cfg()
            base.update(cfg)
            _lock_config[int(cid)][lk] = base

    for cid, users in data.get("exceptions", {}).items():
        _exceptions[int(cid)] = set(users)
    for cid, doms in data.get("whitelist_domains", {}).items():
        _whitelist_domains[int(cid)] = set(doms)
    for cid, doms in data.get("blacklist_domains", {}).items():
        _blacklist_domains[int(cid)] = set(doms)
    for cid, locks in data.get("temp_locks", {}).items():
        _temp_locks[int(cid)] = locks
    for cid, sched in data.get("schedules", {}).items():
        _schedules[int(cid)] = sched
    for cid, profiles in data.get("custom_profiles", {}).items():
        _custom_profiles[int(cid)] = profiles
    for cid, snap in data.get("emergency_snapshot", {}).items():
        _emergency_snapshot[int(cid)] = snap
    for cid, v in data.get("dry_run", {}).items():
        _dry_run[int(cid)] = v
    for cid, v in data.get("edited_mode", {}).items():
        _edited_mode[int(cid)] = v
    for cid, users in data.get("violations", {}).items():
        for uid, e in users.items():
            entry = _violations[int(cid)][int(uid)]
            entry["count_by_type"] = defaultdict(int, e.get("count_by_type", {}))
            entry["score"] = e.get("score", 0.0)
            entry["last_ts"] = e.get("last_ts", 0.0)
            entry["last_type"] = e.get("last_type")
    for cid, entries in data.get("violation_log", {}).items():
        _violation_log[int(cid)] = deque(entries, maxlen=DEFAULT_VIOLATION_LOG_MAXLEN)
    for cid, entries in data.get("audit_log", {}).items():
        _audit_log[int(cid)] = deque(entries, maxlen=DEFAULT_AUDIT_LOG_MAXLEN)
    for cid, ids in data.get("bot_allowlist", {}).items():
        _bot_allowlist[int(cid)] = set(ids)

    logger.info("Lock Engine: وضعیت قبلی بارگذاری شد.")


# ---------------------------------------------------------------------------
# 📋 دستورات (Commands)
# ---------------------------------------------------------------------------

_DANGEROUS_LOCKS = {"group"}
_pending_confirmation = {}  # (chat_id, admin_id) -> {"cmd": str, "expires": ts}
_CONFIRM_WINDOW_SECONDS = 30


def _duration_to_seconds(text: str) -> int | None:
    m = re.fullmatch(r"(\d+)\s*(m|min|h|hour|d|day)", text.strip().lower())
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2)
    if unit in ("m", "min"):
        return value * 60
    if unit in ("h", "hour"):
        return value * 3600
    if unit in ("d", "day"):
        return value * 86400
    return None


def _resolve_lock_key(token: str) -> str | None:
    token = token.strip().lower()
    aliases = {
        "link": "links", "photos": "photo", "videos": "video", "docs": "document",
        "files": "document", "voices": "voice", "gifs": "gif", "stickers": "sticker",
        "fwd": "forward", "mentions": "mention", "tags": "mention", "tag": "mention",
        "hashtags": "hashtag", "media": "media", "medias": "media",
    }
    token = aliases.get(token, token)
    return token if token in LOCK_TYPES else None


def _require_admin(update) -> bool:
    import bot as host
    return host.is_admin(update.effective_user.id) or host.has_permission(update.effective_user.id, "moderate")


async def _deny(update):
    await update.effective_message.reply_text("🚫 این دستور فقط برای ادمین‌های مجازه.")


def _locks_status_text(chat_id: int) -> str:
    lines = ["╭━━━━━━━━━━━━━━━━━━━━━━╮", "𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 — Locks 𓆪", "╰━━━━━━━━━━━━━━━━━━━━━━╯", ""]
    for k, info in LOCK_TYPES.items():
        on = is_effectively_enabled(chat_id, k)
        extra = ""
        remaining = temp_lock_remaining(chat_id, k)
        if remaining:
            extra = f" (⏱️ {remaining // 60}m مانده)"
        lines.append(f"{info['emoji']} {info['label']}: {'🟢 روشن' if on else '🔴 خاموش'}{extra}")
    return "\n".join(lines)


async def locks_command(update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(_locks_status_text(update.effective_chat.id))


async def lock_command(update, context: ContextTypes.DEFAULT_TYPE):
    """/lock <type> [duration] | /lock <type> action <x> | /lock exception ... |
    /lock whitelist ... | /lock blacklist ... | /lock schedule ... | /lock profile ... |
    /lock emergency | /lock all"""
    import bot as host

    if not _require_admin(update):
        await _deny(update)
        return

    chat_id = update.effective_chat.id
    args = context.args or []
    if not args:
        text, markup = build_panel(chat_id)
        await update.effective_message.reply_text(text, reply_markup=markup)
        return

    sub = args[0].lower()

    # ---------- /lock exception add/remove <id> ----------
    if sub == "exception" and len(args) >= 3:
        action, uid_s = args[1].lower(), args[2]
        if not uid_s.isdigit():
            await update.effective_message.reply_text("آیدی عددی بده.")
            return
        uid = int(uid_s)
        if action == "add":
            _exceptions[chat_id].add(uid)
            await update.effective_message.reply_text(f"✅ کاربر {uid} از همه‌ی قفل‌ها مستثنی شد.")
        elif action == "remove":
            _exceptions[chat_id].discard(uid)
            await update.effective_message.reply_text(f"✅ استثنای کاربر {uid} حذف شد.")
        log_audit(chat_id, update.effective_user.id, update.effective_user.first_name or "?", f"exception {action} {uid}")
        await host.save_state()
        return

    # ---------- /lock bot allow/disallow <id> ----------
    if sub == "bot" and len(args) >= 3 and args[1].lower() in ("allow", "disallow"):
        action, bid_s = args[1].lower(), args[2]
        if not bid_s.isdigit():
            await update.effective_message.reply_text("آیدی عددی ربات رو بده.")
            return
        bid = int(bid_s)
        if action == "allow":
            _bot_allowlist[chat_id].add(bid)
            await update.effective_message.reply_text(f"✅ ربات {bid} به Allowlist اضافه شد (با وجود قفل ربات، اخراج نمی‌شه).")
        else:
            _bot_allowlist[chat_id].discard(bid)
            await update.effective_message.reply_text(f"✅ ربات {bid} از Allowlist حذف شد.")
        log_audit(chat_id, update.effective_user.id, update.effective_user.first_name or "?", f"bot-allowlist {action} {bid}")
        await host.save_state()
        return

    # ---------- /lock whitelist add/remove <domain> ----------
    if sub == "whitelist" and len(args) >= 3:
        action, domain = args[1].lower(), args[2].lower().strip()
        if action == "add":
            _whitelist_domains[chat_id].add(domain)
            await update.effective_message.reply_text(f"✅ «{domain}» به وایت‌لیست لینک اضافه شد.")
        elif action == "remove":
            _whitelist_domains[chat_id].discard(domain)
            await update.effective_message.reply_text(f"✅ «{domain}» از وایت‌لیست حذف شد.")
        log_audit(chat_id, update.effective_user.id, update.effective_user.first_name or "?", f"whitelist {action} {domain}")
        await host.save_state()
        return

    # ---------- /lock blacklist add/remove <domain> ----------
    if sub == "blacklist" and len(args) >= 3:
        action, domain = args[1].lower(), args[2].lower().strip()
        if action == "add":
            _blacklist_domains[chat_id].add(domain)
            await update.effective_message.reply_text(f"✅ «{domain}» به بلک‌لیست دامنه اضافه شد.")
        elif action == "remove":
            _blacklist_domains[chat_id].discard(domain)
            await update.effective_message.reply_text(f"✅ «{domain}» از بلک‌لیست دامنه حذف شد.")
        log_audit(chat_id, update.effective_user.id, update.effective_user.first_name or "?", f"domain-blacklist {action} {domain}")
        await host.save_state()
        return

    # ---------- /lock schedule <type> off | <type> <start> <end> [days] ----------
    if sub == "schedule" and len(args) >= 2:
        lock_key = _resolve_lock_key(args[1])
        if not lock_key:
            await update.effective_message.reply_text("نوع قفل نامعتبره.")
            return
        if len(args) >= 3 and args[2].lower() == "off":
            clear_schedule(chat_id, lock_key)
            await update.effective_message.reply_text(f"✅ زمان‌بندی «{LOCK_TYPES[lock_key]['label']}» حذف شد.")
            await host.save_state()
            return
        if len(args) >= 4:
            start, end = args[2], args[3]
            days = args[4].upper().split(",") if len(args) >= 5 else None
            if not re.fullmatch(r"\d{1,2}:\d{2}", start) or not re.fullmatch(r"\d{1,2}:\d{2}", end):
                await update.effective_message.reply_text("فرمت ساعت باید HH:MM باشه.")
                return
            set_schedule(chat_id, lock_key, start, end, days)
            await update.effective_message.reply_text(
                f"✅ «{LOCK_TYPES[lock_key]['label']}» از {start} تا {end}"
                + (f" (روزهای {','.join(days)})" if days else " (هر روز)") + " قفل خواهد بود."
            )
            log_audit(chat_id, update.effective_user.id, update.effective_user.first_name or "?", f"schedule {lock_key} {start}-{end}")
            await host.save_state()
            return
        await update.effective_message.reply_text("استفاده: /lock schedule <نوع> <شروع HH:MM> <پایان HH:MM> [MO,TU,...]")
        return

    # ---------- /lock profile save/load/delete <name> ----------
    if sub == "profile" and len(args) >= 2:
        action = args[1].lower()
        if action in ("save", "delete") and len(args) >= 3:
            name = args[2]
            if action == "save":
                save_custom_profile(chat_id, name)
                await update.effective_message.reply_text(f"✅ پروفایل «{name}» ذخیره شد.")
            else:
                ok = delete_custom_profile(chat_id, name)
                await update.effective_message.reply_text("✅ حذف شد." if ok else "پیدا نشد.")
            await host.save_state()
            return
        if action == "load" and len(args) >= 3:
            name = args[2]
            if name.upper() in PRESET_PROFILES:
                apply_profile(chat_id, PRESET_PROFILES[name.upper()])
                await update.effective_message.reply_text(f"✅ پروفایل آماده‌ی «{name}» اعمال شد.")
            elif load_custom_profile(chat_id, name):
                await update.effective_message.reply_text(f"✅ پروفایل «{name}» اعمال شد.")
            else:
                await update.effective_message.reply_text("پروفایلی با این اسم پیدا نشد.")
            await host.save_state()
            return
        await update.effective_message.reply_text("استفاده: /lock profile save|load|delete <نام>")
        return

    # ---------- /lock emergency ----------
    if sub == "emergency":
        ok = enter_emergency(chat_id)
        await update.effective_message.reply_text(
            "🚨 حالت اضطراری فعال شد." if ok else "از قبل توی حالت اضطراری هستیم."
        )
        log_audit(chat_id, update.effective_user.id, update.effective_user.first_name or "?", "emergency ON")
        await host.save_state()
        return

    # ---------- /lock all (با تایید) ----------
    if sub == "all":
        key = (chat_id, update.effective_user.id)
        pending = _pending_confirmation.get(key)
        if pending and pending["cmd"] == "lock_all" and _now() < pending["expires"]:
            for k in LOCK_TYPES:
                _set_setting(chat_id, k, True)
            _pending_confirmation.pop(key, None)
            await update.effective_message.reply_text("✅ همه‌ی قفل‌ها فعال شدن.")
            log_audit(chat_id, update.effective_user.id, update.effective_user.first_name or "?", "lock all")
            await host.save_state()
        else:
            _pending_confirmation[key] = {"cmd": "lock_all", "expires": _now() + _CONFIRM_WINDOW_SECONDS}
            await update.effective_message.reply_text(
                "⚠️ این کار همه‌ی قفل‌ها (از جمله بستن کامل گروه) رو فعال می‌کنه.\n"
                "برای تایید، تا ۳۰ ثانیه‌ی دیگه دوباره /lock all رو بفرست."
            )
        return

    # ---------- /lock <type> action <x> ----------
    if len(args) >= 3 and args[1].lower() == "action":
        lock_key = _resolve_lock_key(sub)
        if not lock_key:
            await update.effective_message.reply_text("نوع قفل نامعتبره.")
            return
        act = args[2].lower()
        if act not in ACTION_PRESETS:
            await update.effective_message.reply_text(
                "اکشن نامعتبره. یکی از این‌ها: " + ", ".join(ACTION_PRESETS.keys())
            )
            return
        get_lock_cfg(chat_id, lock_key)["action"] = act
        await update.effective_message.reply_text(f"✅ اکشن «{LOCK_TYPES[lock_key]['label']}» روی «{act}» تنظیم شد.")
        log_audit(chat_id, update.effective_user.id, update.effective_user.first_name or "?", f"{lock_key} action={act}")
        await host.save_state()
        return

    # ---------- /lock <type> newmember <minutes> ----------
    if len(args) >= 3 and args[1].lower() in ("newmember", "new_member"):
        lock_key = _resolve_lock_key(sub)
        if not lock_key or not args[2].isdigit():
            await update.effective_message.reply_text("استفاده: /lock <نوع> newmember <دقیقه>")
            return
        cfg = get_lock_cfg(chat_id, lock_key)
        cfg["new_member_only"] = True
        cfg["new_member_minutes"] = int(args[2])
        await update.effective_message.reply_text(
            f"✅ «{LOCK_TYPES[lock_key]['label']}» فقط برای {args[2]} دقیقه‌ی اول اعضای جدید اعمال می‌شه."
        )
        await host.save_state()
        return

    # ---------- /lock <type> cooldown <seconds> ----------
    if len(args) >= 3 and args[1].lower() == "cooldown" and args[2].isdigit():
        lock_key = _resolve_lock_key(sub)
        if not lock_key:
            await update.effective_message.reply_text("نوع قفل نامعتبره.")
            return
        get_lock_cfg(chat_id, lock_key)["cooldown_seconds"] = int(args[2])
        await update.effective_message.reply_text(f"✅ کول‌داون «{LOCK_TYPES[lock_key]['label']}» روی {args[2]} ثانیه تنظیم شد.")
        await host.save_state()
        return

    # ---------- /lock <type> flood <count> <seconds> ----------
    if len(args) >= 4 and args[1].lower() == "flood" and args[2].isdigit() and args[3].isdigit():
        lock_key = _resolve_lock_key(sub)
        if not lock_key:
            await update.effective_message.reply_text("نوع قفل نامعتبره.")
            return
        cfg = get_lock_cfg(chat_id, lock_key)
        cfg["flood_enabled"] = True
        cfg["flood_limit"] = int(args[2])
        cfg["flood_window"] = int(args[3])
        await update.effective_message.reply_text(
            f"✅ ضدفلود «{LOCK_TYPES[lock_key]['label']}»: بیش از {args[2]} پیام در {args[3]} ثانیه."
        )
        await host.save_state()
        return

    # ---------- /lock <type> threshold <chars>  (برای long_message) ----------
    if len(args) >= 3 and args[1].lower() == "threshold" and args[2].isdigit():
        lock_key = _resolve_lock_key(sub)
        if not lock_key:
            await update.effective_message.reply_text("نوع قفل نامعتبره.")
            return
        get_lock_cfg(chat_id, lock_key)["threshold"] = int(args[2])
        await update.effective_message.reply_text(f"✅ آستانه‌ی «{LOCK_TYPES[lock_key]['label']}» روی {args[2]} تنظیم شد.")
        await host.save_state()
        return

    # ---------- /lock edited mode smart|delete_any ----------
    if sub == "edited" and len(args) >= 3 and args[1].lower() == "mode":
        mode = args[2].lower()
        if mode not in ("smart", "delete_any"):
            await update.effective_message.reply_text("مقدار باید smart یا delete_any باشه.")
            return
        _edited_mode[chat_id] = mode
        await update.effective_message.reply_text(f"✅ حالت قفل ویرایش روی «{mode}» تنظیم شد.")
        await host.save_state()
        return

    # ---------- /lock <type> [duration] : فعال‌سازی ساده یا موقت ----------
    lock_key = _resolve_lock_key(sub)
    if not lock_key:
        await update.effective_message.reply_text(
            "نوع قفل نامعتبره. برای دیدن همه‌ی قفل‌ها: /locks"
        )
        return

    if lock_key in _DANGEROUS_LOCKS:
        key = (chat_id, update.effective_user.id)
        pending = _pending_confirmation.get(key)
        if not (pending and pending["cmd"] == f"lock_{lock_key}" and _now() < pending["expires"]):
            _pending_confirmation[key] = {"cmd": f"lock_{lock_key}", "expires": _now() + _CONFIRM_WINDOW_SECONDS}
            await update.effective_message.reply_text(
                f"⚠️ «{LOCK_TYPES[lock_key]['label']}» یه قفل حساسه. برای تایید، تا ۳۰ ثانیه‌ی دیگه دوباره همین دستور رو بفرست."
            )
            return
        _pending_confirmation.pop(key, None)

    if len(args) >= 2:
        seconds = _duration_to_seconds(args[1])
        if seconds:
            set_temp_lock(chat_id, lock_key, seconds)
            await update.effective_message.reply_text(
                f"⏱️ «{LOCK_TYPES[lock_key]['label']}» برای {args[1]} قفل شد."
            )
            log_audit(chat_id, update.effective_user.id, update.effective_user.first_name or "?", f"{lock_key} temp-lock {args[1]}")
            await host.save_state()
            return

    _set_setting(chat_id, lock_key, True)
    await update.effective_message.reply_text(f"🔒 «{LOCK_TYPES[lock_key]['label']}» قفل شد.")
    log_audit(chat_id, update.effective_user.id, update.effective_user.first_name or "?", f"{lock_key} → ON")
    await host.save_state()


async def unlock_command(update, context: ContextTypes.DEFAULT_TYPE):
    import bot as host

    if not _require_admin(update):
        await _deny(update)
        return
    chat_id = update.effective_chat.id
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("استفاده: /unlock <نوع> یا /unlock all یا /unlock emergency")
        return

    sub = args[0].lower()
    if sub == "emergency":
        ok = exit_emergency(chat_id)
        await update.effective_message.reply_text("✅ از حالت اضطراری خارج شدیم." if ok else "توی حالت اضطراری نیستیم.")
        log_audit(chat_id, update.effective_user.id, update.effective_user.first_name or "?", "emergency OFF")
        await host.save_state()
        return
    if sub == "all":
        for k in LOCK_TYPES:
            _set_setting(chat_id, k, False)
            clear_temp_lock(chat_id, k, turn_off=False)
        await update.effective_message.reply_text("✅ همه‌ی قفل‌ها خاموش شدن.")
        log_audit(chat_id, update.effective_user.id, update.effective_user.first_name or "?", "unlock all")
        await host.save_state()
        return

    lock_key = _resolve_lock_key(sub)
    if not lock_key:
        await update.effective_message.reply_text("نوع قفل نامعتبره.")
        return
    clear_temp_lock(chat_id, lock_key, turn_off=True)
    clear_schedule(chat_id, lock_key)
    await update.effective_message.reply_text(f"🔓 «{LOCK_TYPES[lock_key]['label']}» باز شد.")
    log_audit(chat_id, update.effective_user.id, update.effective_user.first_name or "?", f"{lock_key} → OFF")
    await host.save_state()


async def lockinfo_command(update, context: ContextTypes.DEFAULT_TYPE):
    import bot as host

    chat_id = update.effective_chat.id
    target_id, name, _r = host._parse_target_and_reason(update)
    if target_id is None:
        await update.effective_message.reply_text("ریپلای بزن یا: /lockinfo <آیدی>")
        return
    profile = get_user_lock_profile(chat_id, target_id)
    role = get_role(chat_id, target_id)
    lines = [
        f"👤 {name}", f"🆔 {target_id}", f"🎖 نقش: {ROLE_LABELS.get(role, role)}", "",
        f"📊 مجموع تخلفات: {profile['total']}",
    ]
    for lk, cnt in sorted(profile["by_type"].items(), key=lambda kv: -kv[1]):
        info = LOCK_TYPES.get(lk, {"emoji": "🔒", "label": lk})
        lines.append(f"{info['emoji']} {info['label']}: {cnt}")
    lines.append("")
    lines.append(f"🚨 امتیاز تخلف: {profile['score']} ({profile['level']})")
    if profile.get("last_type"):
        info = LOCK_TYPES.get(profile["last_type"], {"label": profile["last_type"]})
        lines.append(f"⏱ آخرین تخلف: {info['label']}")
    await update.effective_message.reply_text("\n".join(lines))


async def lockstats_command(update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    stats = get_stats(chat_id)
    lines = ["📊 آمار قفل‌های گروه", ""]
    for r_key, r_label in (("today", "امروز"), ("week", "این هفته"), ("month", "این ماه"), ("total", "کل")):
        lines.append(f"— {r_label} —")
        by_type = stats["by_range"][r_key]
        if not by_type:
            lines.append("چیزی ثبت نشده.")
        for lk, cnt in sorted(by_type.items(), key=lambda kv: -kv[1]):
            info = LOCK_TYPES.get(lk, {"emoji": "🔒", "label": lk})
            lines.append(f"{info['emoji']} {info['label']}: {cnt}")
        lines.append("")
    if stats["top_users"]:
        lines.append("👥 بیشترین تخلف‌کننده‌ها:")
        for uid, cnt in stats["top_users"]:
            lines.append(f"• {uid}: {cnt}")
    await update.effective_message.reply_text("\n".join(lines))


async def lockprofile_command(update, context: ContextTypes.DEFAULT_TYPE):
    """میانبر: /lockprofile <نام>  == /lock profile load <نام>"""
    context.args = ["profile", "load"] + list(context.args or [])
    await lock_command(update, context)


async def locktest_command(update, context: ContextTypes.DEFAULT_TYPE):
    import bot as host

    if not _require_admin(update):
        await _deny(update)
        return
    chat_id = update.effective_chat.id
    args = context.args or []
    if not args or args[0].lower() not in ("on", "off"):
        current = "روشن" if is_dry_run(chat_id) else "خاموش"
        await update.effective_message.reply_text(f"وضعیت فعلی حالت تست: {current}\nاستفاده: /locktest on یا /locktest off")
        return
    set_dry_run(chat_id, args[0].lower() == "on")
    await update.effective_message.reply_text(
        "🧪 حالت تست فعال شد؛ پیام‌ها فقط Log می‌شن و حذف/اکشن واقعی اجرا نمی‌شه."
        if args[0].lower() == "on" else "✅ حالت تست خاموش شد؛ اکشن‌ها دوباره واقعی اجرا می‌شن."
    )
    await host.save_state()


async def lockcheck_command(update, context: ContextTypes.DEFAULT_TYPE):
    """چک مستقیم دسترسی خودِ ربات توی این گروه (بدون نیاز به تست عملی/اکانت دوم).
    دلیل اصلی «قفل/اسپم فعاله ولی پاک نمی‌کنه» معمولاً همینه: ربات ادمین نیست یا
    دسترسی «حذف پیام»/«محدودکردن کاربران» رو نداره."""
    if not _require_admin(update):
        await _deny(update)
        return
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    try:
        member = await context.bot.get_chat_member(chat.id, context.bot.id)
    except Exception as e:
        await update.effective_message.reply_text(f"❌ نتونستم وضعیت ربات رو توی این گروه بگیرم: {e}")
        return

    status = getattr(member, "status", "unknown")
    if status not in ("administrator", "creator"):
        await update.effective_message.reply_text(
            f"❌ ربات توی این گروه ادمین نیست (وضعیت فعلی: {status}).\n"
            "قفل‌ها و آنتی‌اسپم فقط تشخیص می‌دن ولی هیچ اقدامی (حذف/میوت/بن) نمی‌تونن انجام بدن.\n"
            "از تنظیمات گروه، ربات رو ادمین کن."
        )
        return

    can_delete = bool(getattr(member, "can_delete_messages", False)) or status == "creator"
    can_restrict = bool(getattr(member, "can_restrict_members", False)) or status == "creator"

    lines = [f"👑 وضعیت ربات: {'سازنده' if status == 'creator' else 'ادمین'}"]
    lines.append(f"{'✅' if can_delete else '❌'} دسترسی حذف پیام (Delete Messages)")
    lines.append(f"{'✅' if can_restrict else '❌'} دسترسی محدودکردن/بن کاربر (Restrict/Ban Users)")

    if not can_delete or not can_restrict:
        lines.append("")
        lines.append(
            "🔧 برای رفع مشکل: توی تنظیمات ادمین‌های گروه، روی ربات بزن و دسترسی‌های بالا رو فعال کن."
        )
    else:
        lines.append("")
        lines.append("✅ همه‌چی از نظر دسترسی مرتبه؛ اگه بازم قفل/اسپم پاک نمی‌کنه، مطمئن شو «ربات روشن» و خودِ قفل موردنظر فعاله (با /locks چک کن).")

    await update.effective_message.reply_text("\n".join(lines))


async def lockconfig_command(update, context: ContextTypes.DEFAULT_TYPE):
    """نمای خلاصه‌ی تنظیمات پیشرفته‌ی یک قفل خاص."""
    chat_id = update.effective_chat.id
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("استفاده: /lockconfig <نوع قفل>")
        return
    lock_key = _resolve_lock_key(args[0])
    if not lock_key:
        await update.effective_message.reply_text("نوع قفل نامعتبره.")
        return
    cfg = get_lock_cfg(chat_id, lock_key)
    lines = [f"🧩 تنظیمات «{LOCK_TYPES[lock_key]['label']}»", ""]
    lines.append(f"وضعیت: {'روشن' if is_effectively_enabled(chat_id, lock_key) else 'خاموش'}")
    lines.append(f"اکشن: {cfg['action']}")
    lines.append(f"کول‌داون: {cfg['cooldown_seconds']} ثانیه")
    lines.append(f"فقط اعضای جدید: {'بله' if cfg['new_member_only'] else 'خیر'}")
    lines.append(f"ضدفلود: {'فعال' if cfg['flood_enabled'] else 'غیرفعال'}"
                 + (f" ({cfg['flood_limit']} در {cfg['flood_window']}s)" if cfg["flood_enabled"] else ""))
    remaining = temp_lock_remaining(chat_id, lock_key)
    if remaining:
        lines.append(f"قفل موقت: {remaining // 60} دقیقه مانده")
    sched = _schedules[chat_id].get(lock_key)
    if sched:
        lines.append(f"زمان‌بندی: {sched['start']} تا {sched['end']}" + (f" ({','.join(sched['days'])})" if sched.get("days") else ""))
    await update.effective_message.reply_text("\n".join(lines))
