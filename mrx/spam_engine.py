# -*- coding: utf-8 -*-
"""
DIGIANTI SMART ANTI-SPAM & ANTI-BOT ENGINE 3.0
================================================
موتور مرکزی ضد اسپم / ضد ربات / کپچا / ضد رید DIGIANTI.

معماری دقیقاً هم‌خانواده‌ی lock_engine.py (نسخه‌ی ۲.۰):
  - state این موتور توی فایل JSON مستقل خودش (spam_engine_state.json) نگه داشته
    می‌شه (بدون دست‌زدن به _collect_state/load_state غول‌پیکر bot.py).
  - نقش‌ها (Owner/Admin/VIP/Friend/Normal/Enemy/Blacklist) از lock_engine.get_role
    گرفته می‌شن - سیستم Exception دوم و موازی ساخته نشده (Rule #0 / #20 / #52).
  - وایت‌لیست/بلک‌لیست دامنه هم از همون دیکشنری‌های lock_engine استفاده می‌کنه
    (lock_engine._whitelist_domains / _blacklist_domains) - یه منبع واحد برای
    دامنه‌ها، نه دو تا سیستم متناقض (Rule #4 / #52).
  - Import از bot.py و lock_engine.py همیشه Lazy (داخل توابع) هست تا Circular
    Import رخ نده (چون bot.py این ماژول رو در سطح ماژول import می‌کنه).
  - ترتیب پایپ‌لاین توی bot.py: LockEngine → SpamEngine → (بدون منطق قدیمی
    اضافه). یعنی اگه LockEngine پیامی رو گرفت، SpamEngine اصلاً صدا زده نمی‌شه؛
    و اگه SpamEngine گرفت، دیگه چک قدیمی دوباره روش اجرا نمی‌شه. این دقیقاً
    Rule #48 (یک پیام دو بار Delete/Mute نشه) رو تضمین می‌کنه.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import random
import re
import time
import unicodedata
from collections import defaultdict, deque

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

import config

logger = logging.getLogger("spam_engine")

STATE_FILE = getattr(config, "SPAM_ENGINE_STATE_FILE", "spam_engine_state.json")

# ---------------------------------------------------------------------------
# 🧠 ثابت‌ها
# ---------------------------------------------------------------------------

ACTION_DELETE, ACTION_WARN, ACTION_MUTE, ACTION_KICK, ACTION_BAN = (
    "delete", "warn", "mute", "kick", "ban",
)

# 𓆩 𝟏𝟕 — Spam Levels
SPAM_LEVELS = [
    (0, 19, "🟢 امن", []),
    (20, 39, "🟡 مشکوک", [ACTION_DELETE]),
    (40, 59, "🟠 اسپمر", [ACTION_DELETE, ACTION_WARN]),
    (60, 79, "🔴 ریسک بالا", [ACTION_DELETE, ACTION_MUTE]),
    (80, 10 ** 9, "⚫ بحرانی", [ACTION_DELETE, ACTION_BAN]),
]

# 𓆩 𝟑𝟏 — Risk Score
RISK_LEVELS = [(0, 20, "امن"), (21, 40, "کم"), (41, 60, "متوسط"), (61, 80, "بالا"), (81, 10 ** 9, "بحرانی")]

# 𓆩 𝟑𝟐 — Chat Security Score
CHAT_SECURITY_LEVELS = [
    (0, 20, "🟢 پایدار"), (21, 40, "🟡 مراقبت"), (41, 60, "🟠 هشدار"),
    (61, 80, "🔴 رید"), (81, 10 ** 9, "⚫ اضطراری"),
]

SCORE_DECAY_PER_HOUR = 2.0

# 𓆩 𝟏 — وزن‌های افزایش Spam Score
SCORE_WEIGHTS = {
    "flood": 6, "duplicate": 5, "link": 4, "ad": 8, "mention": 3, "hashtag": 2,
    "emoji": 2, "caps": 2, "words": 4, "new_account_behavior": 6, "media_flood": 5,
    "edit_spam": 4, "forward_flood": 4, "captcha_fail": 5,
}

DEFAULT_ESCALATION_LADDER = [  # 𓆩 𝟏𝟖 — Smart Escalation (تجمعی، نه per-type)
    {"count": 1, "action": [ACTION_DELETE, ACTION_WARN]},
    {"count": 2, "action": [ACTION_DELETE, ACTION_WARN]},
    {"count": 3, "action": [ACTION_DELETE, ACTION_MUTE], "mute_minutes": 10},
    {"count": 4, "action": [ACTION_MUTE], "mute_minutes": 60},
    {"count": 5, "action": [ACTION_KICK]},
]

# 𓆩 𝟐 / 𓆩 𝟐𝟔 — Flood پیش‌فرض هر نوع محتوا: (تعداد، بازه‌ی ثانیه)
FLOOD_DEFAULTS = {
    "text": (getattr(config, "FLOOD_LIMIT_MESSAGES", 5), getattr(config, "FLOOD_LIMIT_SECONDS", 10)),
    "media": (getattr(config, "MEDIA_FLOOD_LIMIT_MESSAGES", 4), getattr(config, "MEDIA_FLOOD_LIMIT_SECONDS", 10)),
    "photo": (4, 10), "video": (3, 15), "voice": (4, 10), "audio": (4, 10),
    "document": (3, 15), "contact": (2, 20), "location": (2, 20), "poll": (2, 20),
    "forward": (4, 15), "mixed": (8, 20),
}

MEMBER_PROTECTION_LEVELS = {
    "LOW":    {"link": True, "mention": False, "media": False, "flood_multiplier": 1.0, "ad": True},
    "NORMAL": {"link": True, "mention": True, "media": False, "flood_multiplier": 0.7, "ad": True},
    "HIGH":   {"link": True, "mention": True, "media": True, "flood_multiplier": 0.5, "ad": True},
    "STRICT": {"link": True, "mention": True, "media": True, "flood_multiplier": 0.3, "ad": True, "text": True},
}

# 𓆩 𝟑𝟔 — Quick Modes (تنظیمات پیشنهادی؛ روی chat_settings موجود اعمال می‌شن)
QUICK_MODES = {
    "NORMAL": {"antispam_words": True, "anticaps": True, "antiflood_text": True, "member_protection_level": "NORMAL"},
    "STRICT": {"antispam_words": True, "anticaps": True, "antiflood_text": True, "antispam_duplicate": True,
               "antispam_mentions": True, "antispam_ads": True, "member_protection_level": "HIGH"},
    "HIGH_SECURITY": {"antispam_words": True, "anticaps": True, "antiflood_text": True, "antispam_duplicate": True,
                       "antispam_mentions": True, "antispam_hashtags": True, "antispam_emoji": True,
                       "antispam_ads": True, "antispam_forward_flood": True, "captcha_enabled": True,
                       "member_protection_level": "STRICT"},
    "RAID_MODE": {"captcha_enabled": True, "antispam_words": True, "antispam_ads": True,
                  "member_protection_level": "STRICT"},
    "NIGHT_MODE": {"member_protection_level": "HIGH", "antispam_duplicate": True},
    "CHAT_MODE": {"antispam_words": True},
    "EMERGENCY": {"captcha_enabled": True, "antispam_words": True, "anticaps": True, "antiflood_text": True,
                  "antispam_duplicate": True, "antispam_mentions": True, "antispam_hashtags": True,
                  "antispam_ads": True, "antispam_forward_flood": True, "member_protection_level": "STRICT"},
}

AD_KEYWORDS = list(getattr(config, "AD_KEYWORDS", [])) or [
    "تبلیغ", "تبلیغات", "خرید فالوور", "فروش", "عضو شوید", "عضو شو", "لینک زیر",
    "کانال ما", "ربات ما", "سابسکرایب", "دنبال کن", "ممبر ارزان", "افزایش ممبر",
    "subscribe", "join now", "buy now", "discount", "for sale", "click here",
    "join our channel", "join our group", "check my channel", "check out my",
    "promo code", "referral", "free followers", "earn money", "make money fast",
]

AD_PATTERNS = [
    re.compile(r"(join|عضو)\s*(now|شو|شوید)", re.IGNORECASE),
    re.compile(r"(t\.me|telegram\.me)\/(joinchat|\+)", re.IGNORECASE),
    re.compile(r"@\w{4,}.{0,15}(بپیوندید|جوین|join)", re.IGNORECASE),
    re.compile(r"(\d{4,}\s*(تومان|toman|\$|usd))", re.IGNORECASE),
]

INVITE_LINK_RE = re.compile(r"(t\.me|telegram\.me)/(joinchat/|\+)\S+", re.IGNORECASE)
MENTION_RE = re.compile(r"@\w{4,}")
HASHTAG_RE = re.compile(r"#\w+")
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002700-\U000027BF]"
)
REPEATED_CHAR_RE = re.compile(r"(.)\1{2,}")

# ---------------------------------------------------------------------------
# 💾 State (در حافظه)
# ---------------------------------------------------------------------------

def _new_profile():
    return {
        "message_count": 0, "repeated_message_count": 0, "duplicate_media_count": 0,
        "link_count": 0, "mention_count": 0, "hashtag_count": 0, "emoji_count": 0,
        "violation_count": 0, "warning_count": 0, "spam_score": 0.0,
        "last_message_time": 0.0, "first_seen": 0.0, "last_violation": None,
        "cooldown_until": 0.0, "risk_score": 0.0,
    }


_profiles = defaultdict(lambda: defaultdict(_new_profile))              # chat -> user -> profile
_msg_windows = defaultdict(lambda: defaultdict(deque))                   # chat -> user -> deque[ts] (per-second/min محاسبه می‌شه)
_flood_windows = defaultdict(lambda: defaultdict(lambda: defaultdict(deque)))  # chat->user->category->deque[ts]
_recent_texts = defaultdict(lambda: defaultdict(lambda: deque(maxlen=6)))       # chat->user->deque[(norm_text, ts)]
_recent_media = defaultdict(lambda: defaultdict(lambda: deque(maxlen=6)))       # chat->user->deque[(file_uid, ts)]
_edit_counts = defaultdict(lambda: defaultdict(int))                      # chat->user-> تعداد ادیت اخیر

_chat_thresholds = defaultdict(dict)          # chat_id -> {"max_mentions":..,"max_hashtags":..,...}
_chat_config_overrides = defaultdict(dict)    # chat_id -> {"captcha_action":..,"captcha_max_attempts":..}
_dry_run = defaultdict(bool)
_pending_confirmation = {}

_security_log = defaultdict(lambda: deque(maxlen=4000))     # chat -> deque(entry)
_captcha_pending = defaultdict(dict)                         # chat -> user -> {"deadline","message_id","attempts","answer","invite_link_name"}
_captcha_stats = defaultdict(lambda: defaultdict(int))       # chat -> {"attempts":,"failures":,"passed":,"bots_blocked":}

_raid_state = defaultdict(lambda: {"until": 0.0, "prev": None, "risk": 0, "recent_usernames": deque(maxlen=30)})
_join_events = defaultdict(lambda: deque(maxlen=60))         # chat -> deque[(ts,user_id,username)]

_emergency_snapshot_spam = defaultdict(lambda: None)          # chat -> dict|None (فقط کلیدهای مخصوص این موتور)

DEFAULT_THRESHOLDS = {
    "max_mentions_per_message": getattr(config, "MAX_MENTIONS_PER_MESSAGE", 3),
    "max_mentions_per_minute": getattr(config, "MAX_MENTIONS_PER_MINUTE", 8),
    "max_hashtags": getattr(config, "MAX_HASHTAGS", 5),
    "max_emojis": getattr(config, "MAX_EMOJIS", 8),
    "max_emoji_ratio": getattr(config, "MAX_EMOJI_RATIO", 0.5),
    "duplicate_similarity": getattr(config, "DUPLICATE_SIMILARITY_THRESHOLD", 0.88),
    "duplicate_count_trigger": 3,
    "captcha_action": "kick",
    "captcha_max_attempts": 3,
}


def get_threshold(chat_id: int, key: str):
    return _chat_thresholds[chat_id].get(key, DEFAULT_THRESHOLDS.get(key))


def set_threshold(chat_id: int, key: str, value):
    _chat_thresholds[chat_id][key] = value


def get_profile(chat_id: int, user_id: int) -> dict:
    p = _profiles[chat_id][user_id]
    if p["first_seen"] == 0.0:
        p["first_seen"] = time.time()
    return p


def is_dry_run(chat_id: int) -> bool:
    return _dry_run[chat_id]


def set_dry_run(chat_id: int, value: bool):
    _dry_run[chat_id] = value


# ---------------------------------------------------------------------------
# 🧑‍⚖️ نقش (کاملاً reuse از lock_engine - سیستم Exception دوم ساخته نشد)
# ---------------------------------------------------------------------------

def get_role(chat_id: int, user_id: int) -> str:
    import lock_engine as lock
    return lock.get_role(chat_id, user_id)


def is_privileged(chat_id: int, user_id: int) -> bool:
    """Owner/Admin/VIP/Friend همیشه از Anti-Spam معاف‌ان (طابق Rule #21/#43)."""
    import lock_engine as lock
    role = get_role(chat_id, user_id)
    return role in (lock.ROLE_OWNER, lock.ROLE_ADMIN, lock.ROLE_VIP, lock.ROLE_FRIEND)


def is_enemy_or_blacklist(chat_id: int, user_id: int) -> str | None:
    import lock_engine as lock
    role = get_role(chat_id, user_id)
    if role in (lock.ROLE_ENEMY, lock.ROLE_BLACKLIST):
        return role
    return None


# ---------------------------------------------------------------------------
# 🔗 لینک/دامنه (کاملاً reuse از lock_engine - وایت‌لیست/بلک‌لیست واحد)
# ---------------------------------------------------------------------------

def find_links(text: str) -> list[str]:
    import lock_engine as lock
    return lock.find_links(text)


def extract_domain(token: str):
    import lock_engine as lock
    return lock._extract_domain(token)


def domain_is_whitelisted(chat_id: int, domain: str) -> bool:
    import lock_engine as lock
    return lock.domain_is_whitelisted(chat_id, domain)


def domain_is_blacklisted(chat_id: int, domain: str) -> bool:
    import lock_engine as lock
    return lock.domain_is_blacklisted(chat_id, domain)


def add_whitelist_domain(chat_id: int, domain: str):
    import lock_engine as lock
    lock._whitelist_domains[chat_id].add(domain.lower())


def remove_whitelist_domain(chat_id: int, domain: str):
    import lock_engine as lock
    lock._whitelist_domains[chat_id].discard(domain.lower())


def add_blacklist_domain(chat_id: int, domain: str):
    import lock_engine as lock
    lock._blacklist_domains[chat_id].add(domain.lower())


def remove_blacklist_domain(chat_id: int, domain: str):
    import lock_engine as lock
    lock._blacklist_domains[chat_id].discard(domain.lower())


def _extract_entity_links(message) -> list[str]:
    """𓆩 𝟐𝟗 — Hidden Spam: لینک‌های پنهون داخل Entity (text_link/url) نه فقط متن خام."""
    found = []
    for attr in ("entities", "caption_entities"):
        entities = getattr(message, attr, None) or []
        for ent in entities:
            etype = getattr(ent, "type", None)
            if etype == "text_link":
                url = getattr(ent, "url", None)
                if url:
                    found.append(url)
            elif etype == "url":
                # خودِ url توی متنه، توسط Regex معمولی هم گرفته می‌شه؛ اینجا صرفاً برای Entityهای HTML/Markdown
                pass
    return found


# ---------------------------------------------------------------------------
# 🧮 Spam Score / Risk Score (با Decay - کاربر برای همیشه اسپمر باقی نمی‌مونه)
# ---------------------------------------------------------------------------

def _decay(value: float, last_ts: float) -> float:
    elapsed_hours = max(0.0, (time.time() - last_ts) / 3600.0)
    return max(0.0, value - elapsed_hours * SCORE_DECAY_PER_HOUR)


def add_score(chat_id: int, user_id: int, reason: str, amount: int | None = None):
    p = get_profile(chat_id, user_id)
    p["spam_score"] = _decay(p["spam_score"], p["last_message_time"] or time.time()) + (
        amount if amount is not None else SCORE_WEIGHTS.get(reason, 3)
    )
    p["last_violation"] = reason
    p["violation_count"] += 1


def current_score(chat_id: int, user_id: int) -> float:
    p = get_profile(chat_id, user_id)
    return round(_decay(p["spam_score"], p["last_message_time"] or time.time()), 1)


def spam_level(score: float):
    for lo, hi, label, actions in SPAM_LEVELS:
        if lo <= score <= hi:
            return label, actions
    return SPAM_LEVELS[-1][2], SPAM_LEVELS[-1][3]


def risk_level_label(score: float) -> str:
    for lo, hi, label in RISK_LEVELS:
        if lo <= score <= hi:
            return label
    return RISK_LEVELS[-1][2]


def compute_user_risk_score(chat_id: int, user_id: int) -> int:
    """𓆩 𝟑𝟏 — Suspicious User Score: ترکیب رفتار Join + پیام + Flood + کپچا + تخلفات."""
    import bot as host

    p = get_profile(chat_id, user_id)
    score = 0.0
    score += min(40, current_score(chat_id, user_id) * 0.5)
    score += min(20, p["violation_count"] * 3)
    fails = _captcha_stats[chat_id].get(f"fail_{user_id}", 0)
    score += min(20, fails * 7)
    join_time = host._join_times[chat_id].get(user_id)
    if join_time and (time.time() - join_time) < 300 and p["message_count"] >= 5:
        score += 15  # رفتار مشکوک: پیام زیاد بلافاصله بعد از Join
    return int(min(100, score))


def chat_security_score(chat_id: int) -> tuple[int, str]:
    """𓆩 𝟑𝟐 — Chat Security Score."""
    import bot as host

    now = time.time()
    recent_joins = sum(1 for ts, *_ in _join_events[chat_id] if now - ts <= 300)
    recent_violations = sum(1 for e in _security_log[chat_id] if now - e["ts"] <= 3600)
    banned_recent = len(host._banned_users.get(chat_id, set())) if hasattr(host, "_banned_users") else 0
    captcha_fail_recent = _captcha_stats[chat_id].get("failures", 0)
    score = min(100, recent_joins * 3 + recent_violations * 2 + min(20, banned_recent) + min(15, captcha_fail_recent))
    if _raid_state[chat_id]["until"] > now:
        score = max(score, 65)
    for lo, hi, label in CHAT_SECURITY_LEVELS:
        if lo <= score <= hi:
            return score, label
    return score, CHAT_SECURITY_LEVELS[-1][2]


# ---------------------------------------------------------------------------
# 🌊 Flood generic + انواع مخصوص
# ---------------------------------------------------------------------------

def _flood_check(chat_id: int, user_id: int, category: str, limit: int, window: int) -> bool:
    now = time.time()
    dq = _flood_windows[chat_id][user_id][category]
    dq.append(now)
    while dq and now - dq[0] > window:
        dq.popleft()
    return len(dq) > limit


def _member_protection(chat_id: int) -> dict | None:
    import bot as host
    settings = host._chat_settings[chat_id]
    if not settings.get("member_protection_enabled", False):
        return None
    level = settings.get("member_protection_level", "NORMAL")
    return MEMBER_PROTECTION_LEVELS.get(level, MEMBER_PROTECTION_LEVELS["NORMAL"])


def _is_new_member(chat_id: int, user_id: int) -> bool:
    import bot as host
    jt = host._join_times[chat_id].get(user_id)
    if not jt:
        return False
    grace = getattr(config, "NEW_MEMBER_GRACE_MINUTES", 10)
    return (time.time() - jt) <= grace * 60


# ---------------------------------------------------------------------------
# 📨 Duplicate Spam (Normalization + Similarity)
# ---------------------------------------------------------------------------

def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = EMOJI_RE.sub("", text)
    text = REPEATED_CHAR_RE.sub(r"\1", text)          # سلاااام -> سلام
    text = re.sub(r"\s+", " ", text).strip().lower()
    text = re.sub(r"[^\w\s]", "", text)                # علامت‌های نگارشی حذف
    return text


def _is_duplicate_text(chat_id: int, user_id: int, text: str, threshold: float) -> bool:
    if not text or len(text) < 3:
        return False
    norm = _normalize_text(text)
    if not norm:
        return False
    now = time.time()
    history = _recent_texts[chat_id][user_id]
    is_dup = False
    for prev_norm, ts in history:
        if now - ts > 120:
            continue
        ratio = difflib.SequenceMatcher(None, norm, prev_norm).ratio()
        if ratio >= threshold:
            is_dup = True
            break
    history.append((norm, now))
    return is_dup


def _is_duplicate_media(chat_id: int, user_id: int, file_uid: str | None) -> bool:
    if not file_uid:
        return False
    now = time.time()
    history = _recent_media[chat_id][user_id]
    is_dup = any(uid == file_uid and now - ts <= 120 for uid, ts in history)
    history.append((file_uid, now))
    return is_dup


def _media_file_uid(message):
    if message.photo:
        return message.photo[-1].file_unique_id
    for attr in ("video", "document", "voice", "audio", "animation", "sticker"):
        obj = getattr(message, attr, None)
        if obj is not None:
            return getattr(obj, "file_unique_id", None)
    return None


# ---------------------------------------------------------------------------
# 📢 Advertisement Detection (Keyword + Pattern + Link + Frequency + Similarity)
# ---------------------------------------------------------------------------

def _ad_score(chat_id: int, user_id: int, text: str, has_link: bool, is_duplicate: bool) -> int:
    if not text:
        return 0
    lowered = text.lower()
    score = 0
    if any(kw.lower() in lowered for kw in AD_KEYWORDS):
        score += 40
    for pattern in AD_PATTERNS:
        if pattern.search(text):
            score += 25
            break
    if has_link:
        score += 20
    if is_duplicate:
        score += 20  # پیام تبلیغاتی که Copy/Paste و تکراریه، شک‌برانگیزتره
    if INVITE_LINK_RE.search(text):
        score += 25
    if _is_new_member(chat_id, user_id):
        score += 10  # عضو تازه‌وارد که فوری تبلیغ می‌کنه
    return min(100, score)


# ---------------------------------------------------------------------------
# 🚨 اقدامات ایمن روی تلگرام (reuse از الگوی lock_engine._safe_call)
# ---------------------------------------------------------------------------

async def _safe_call(coro_factory, what: str, retries: int = 1, context=None, chat_id=None, alert_label: str | None = None):
    for attempt in range(retries + 1):
        try:
            return await coro_factory()
        except BadRequest as e:
            logger.warning(f"[SpamEngine] {what} ناموفق (BadRequest): {e}")
            if context is not None and chat_id is not None:
                await _alert_missing_permission_if_relevant(context, chat_id, e, alert_label or what)
            return None
        except TelegramError as e:
            logger.warning(f"[SpamEngine] {what} ناموفق (تلاش {attempt + 1}): {e}")
            if attempt >= retries:
                if context is not None and chat_id is not None:
                    await _alert_missing_permission_if_relevant(context, chat_id, e, alert_label or what)
                return None
        except Exception as e:
            logger.warning(f"[SpamEngine] {what} خطای غیرمنتظره: {e}")
            return None


async def _alert_missing_permission_if_relevant(context, chat_id: int, e: Exception, action_label: str):
    """Reuse دقیق از هشدار دسترسی lock_engine (یه کول‌داون مشترک، تا کاربر دوبار
    هشدار مشابه از دو موتور مختلف نگیره)."""
    try:
        import lock_engine as lock
        if lock._looks_like_permission_error(e):
            await lock._alert_missing_permission(context, chat_id, action_label)
    except Exception:
        pass


def _log_security(chat_id: int, user_id: int, action: str, reason: str, msg_type: str = "text"):
    _security_log[chat_id].append({
        "ts": time.time(), "chat_id": chat_id, "user_id": user_id, "action": action,
        "reason": reason, "spam_score": current_score(chat_id, user_id),
        "risk_score": compute_user_risk_score(chat_id, user_id), "message_type": msg_type,
    })


WARN_TEMPLATES = [
    "⚠️ Spam Detected\n🛡️ پیام حذف شد.",
    "🛡️ Message Removed\nاین محتوا با قوانین ضدهرزنامه گروه سازگار نیست.",
    "🚫 فعالیت مشابه اسپم شناسایی شد؛ پیامت حذف شد.",
]
_template_idx = defaultdict(int)


def _pick_template(chat_id: int) -> str:
    idx = _template_idx[chat_id] % len(WARN_TEMPLATES)
    _template_idx[chat_id] += 1
    return WARN_TEMPLATES[idx]


def _resolve_action(chat_id: int, violation_count: int, forced: list | None = None):
    if forced:
        return forced, None
    ladder = getattr(config, "SPAM_ESCALATION_LADDER_OVERRIDE", {}).get(chat_id, DEFAULT_ESCALATION_LADDER)
    step = ladder[-1]
    for item in ladder:
        if violation_count <= item["count"]:
            step = item
            break
    return step["action"], step.get("mute_minutes")


async def _log_to_moderation_engine(chat_id, user_id, action, reason, duration_seconds):
    """اکشنی که Spam Engine خودش (طبق منطق تشخیص/تصمیم خودش) واقعاً زده رو، فقط برای
    یکپارچه‌سازی تاریخچه/ریسک/آمار مرکزی، به moderation_engine اطلاع می‌ده. هیچ اکشن
    تلگرامی دوباره اجرا نمی‌شه؛ در نتیجه Duplicate Action اتفاق نمی‌افته. اگه moderation_engine
    در دسترس نباشه یا خطا بده، Spam Engine مثل قبل بدون مشکل کار می‌کنه."""
    try:
        import moderation_engine
        await moderation_engine.log_auto_action(
            chat_id, user_id, action, reason, source="SPAM_ENGINE", duration_seconds=duration_seconds,
        )
    except Exception as e:
        logger.warning(f"یکپارچه‌سازی با moderation_engine ناموفق بود: {e}")


async def _apply_action(context, chat_id, user_id, display_name, actions, mute_minutes, message, reason):
    import bot as host

    dry = is_dry_run(chat_id)
    p = get_profile(chat_id, user_id)

    if ACTION_DELETE in actions and message is not None:
        if dry:
            logger.info(f"🧪 SPAM TEST | User: {user_id} | Reason: {reason} | Action would be: DELETE")
        else:
            await _safe_call(lambda: message.delete(), "حذف پیام اسپم",
                              context=context, chat_id=chat_id, alert_label="حذف پیام")

    if ACTION_WARN in actions:
        p["warning_count"] += 1
        template = _pick_template(chat_id)
        if dry:
            logger.info(f"🧪 SPAM TEST | User: {user_id} | Action would be: WARN")
        else:
            await _safe_call(
                lambda: host._warn_and_maybe_mute(chat_id, user_id, display_name, context,
                                                   f"{template}\n📝 دلیل: {reason}"),
                "اخطار اسپم",
            )

    if ACTION_MUTE in actions:
        minutes = mute_minutes or getattr(config, "MUTE_DURATION_MINUTES", 30)
        if dry:
            logger.info(f"🧪 SPAM TEST | User: {user_id} | Action would be: MUTE {minutes}m")
        else:
            until = int(time.time()) + minutes * 60

            async def _do_mute():
                await context.bot.restrict_chat_member(
                    chat_id=chat_id, user_id=user_id,
                    permissions=ChatPermissions(can_send_messages=False), until_date=until,
                )

            await _safe_call(_do_mute, "میوت اسپم", context=context, chat_id=chat_id, alert_label="میوت کاربر")
            host._active_mutes[chat_id][user_id] = until
            await _safe_call(
                lambda: context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🔇 {display_name} به‌خاطر فعالیت مشابه اسپم برای {minutes} دقیقه میوت شد.",
                ),
                "پیام میوت اسپم",
            )
            await _log_to_moderation_engine(chat_id, user_id, "mute", reason, minutes * 60)

    if ACTION_KICK in actions:
        if dry:
            logger.info(f"🧪 SPAM TEST | User: {user_id} | Action would be: KICK")
        else:
            async def _do_kick():
                await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)

            await _safe_call(_do_kick, "اخراج اسپمر", context=context, chat_id=chat_id, alert_label="اخراج کاربر")
            await _safe_call(
                lambda: context.bot.send_message(chat_id=chat_id, text=f"👢 {display_name} به‌خاطر اسپم اخراج شد."),
                "پیام اخراج اسپمر",
            )
            await _log_to_moderation_engine(chat_id, user_id, "kick", reason, None)

    if ACTION_BAN in actions:
        if dry:
            logger.info(f"🧪 SPAM TEST | User: {user_id} | Action would be: BAN")
        else:
            await _safe_call(lambda: context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id), "بن اسپمر",
                              context=context, chat_id=chat_id, alert_label="بن کاربر")
            host._banned_users[chat_id].add(user_id)
            await _safe_call(
                lambda: context.bot.send_message(chat_id=chat_id, text=f"🚫 {display_name} به‌خاطر اسپم شدید بن شد."),
                "پیام بن اسپمر",
            )
            await _log_to_moderation_engine(chat_id, user_id, "ban", reason, None)

    _log_security(chat_id, user_id, "+".join(actions), reason)
    await _safe_call(
        lambda: host._log_admin_action(
            context, chat_id,
            f"🛡 Anti-Spam Engine\n👤 {display_name}\n📝 {reason}\n⚡ اکشن: {'+'.join(actions)}"
            + (" (🧪 تست - اجرا نشد)" if dry else ""),
        ),
        "لاگ ادمین اسپم",
    )


async def handle_violation(context, chat_id, user_id, display_name, message, reason, forced_action=None, score_key=None):
    p = get_profile(chat_id, user_id)
    add_score(chat_id, user_id, score_key or reason)
    p["violation_count"] += 1
    actions, mute_minutes = _resolve_action(chat_id, p["violation_count"], forced_action)
    await _apply_action(context, chat_id, user_id, display_name, actions, mute_minutes, message, reason)


# ---------------------------------------------------------------------------
# 🚦 پایپ‌لاین اصلی
# ---------------------------------------------------------------------------

async def process_message(update, context: ContextTypes.DEFAULT_TYPE) -> bool:
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

    # 𓆩 𝟒𝟗 — Security Priority: Owner/Admin/VIP/Friend اول از همه معاف‌ان
    if is_privileged(chat.id, user.id):
        return False

    p = get_profile(chat.id, user.id)
    p["message_count"] += 1
    p["last_message_time"] = time.time()

    role_enemy = is_enemy_or_blacklist(chat.id, user.id)
    strict_multiplier = 0.5 if role_enemy == "ENEMY" else 1.0   # دشمن: آستانه سخت‌گیرتر (بند ۲۱)
    # Blacklist با همون سیستم فعلی Immediate Action داره (توی bot.py) - اینجا کاری نمی‌کنیم که دوبار Delete بشه

    text = message.text or message.caption or ""

    # ---------- 𓆩 𝟐 — Flood Protection (هر نوع محتوا جدا) ----------
    flood_category = None
    if message.text:
        flood_category = "text"
    elif message.sticker or message.animation:
        flood_category = None  # قبلاً توسط antisticker_flood موجود مدیریت می‌شه (Rule #52: Duplicate نساز)
    elif message.photo:
        flood_category = "photo"
    elif message.video:
        flood_category = "video"
    elif message.voice:
        flood_category = "voice"
    elif message.audio:
        flood_category = "audio"
    elif message.document:
        flood_category = "document"
    elif message.contact:
        flood_category = "contact"
    elif message.location:
        flood_category = "location"
    elif message.poll:
        flood_category = "poll"

    protection = _member_protection(chat.id) if _is_new_member(chat.id, user.id) else None

    if flood_category and settings.get(f"antispam_{flood_category}_flood", False):
        limit, window = FLOOD_DEFAULTS.get(flood_category, (5, 15))
        mult = strict_multiplier * (protection["flood_multiplier"] if protection else 1.0)
        limit = max(1, int(limit * mult))
        if _flood_check(chat.id, user.id, flood_category, limit, window):
            await handle_violation(context, chat.id, user.id, display_name, message,
                                    f"فلود {flood_category}", score_key="media_flood")
            return True

    # ---------- 𓆩 𝟐𝟕 — Mixed Flood (ترکیبی از انواع محتوا) ----------
    if settings.get("antispam_mixed_flood", False):
        if _flood_check(chat.id, user.id, "mixed_any", *FLOOD_DEFAULTS["mixed"]):
            await handle_violation(context, chat.id, user.id, display_name, message,
                                    "فلود ترکیبی (چند نوع محتوای مختلف پشت‌سرهم)", score_key="flood")
            return True

    # ---------- 𓆩 𝟑𝟎 — Forward Flood ----------
    if settings.get("antispam_forward_flood", False):
        is_fwd = bool(getattr(message, "forward_origin", None) or getattr(message, "forward_from", None)
                      or getattr(message, "forward_from_chat", None))
        if is_fwd and _flood_check(chat.id, user.id, "forward", *FLOOD_DEFAULTS["forward"]):
            await handle_violation(context, chat.id, user.id, display_name, message,
                                    "فوروارد پشت‌سرهم (فلود فوروارد)", score_key="forward_flood")
            return True

    # ---------- 𓆩 𝟑 — Duplicate Spam ----------
    if settings.get("antispam_duplicate", False):
        threshold = get_threshold(chat.id, "duplicate_similarity")
        media_uid = _media_file_uid(message)
        dup_text = _is_duplicate_text(chat.id, user.id, text, threshold) if text else False
        dup_media = _is_duplicate_media(chat.id, user.id, media_uid) if media_uid else False
        if dup_text or dup_media:
            p["repeated_message_count" if dup_text else "duplicate_media_count"] += 1
            await handle_violation(context, chat.id, user.id, display_name, message,
                                    "پیام/رسانه‌ی تکراری (Duplicate/Copy-Paste Spam)", score_key="duplicate")
            return True

    if not text:
        return False

    entity_links = _extract_entity_links(message)
    raw_links = find_links(text) + entity_links
    domains = [d for d in (extract_domain(t) for t in raw_links) if d]
    has_blacklisted_domain = any(domain_is_blacklisted(chat.id, d) for d in domains)
    has_whitelisted_only = bool(domains) and all(domain_is_whitelisted(chat.id, d) for d in domains)
    has_link = bool(raw_links)

    is_dup_for_ad = text in [t[0] for t in _recent_texts[chat.id][user.id]]  # تخمین سریع بدون محاسبه‌ی مجدد

    # ---------- 𓆩 𝟒 — Smart Link Spam (مجزا از Lock Engine؛ اینجا فقط Score/Flood لینکه، نه قفل کامل) ----------
    if has_link and not has_whitelisted_only:
        p["link_count"] += 1
        if has_blacklisted_domain:
            await handle_violation(context, chat.id, user.id, display_name, message,
                                    "لینک دامنه‌ی بلک‌لیست‌شده", score_key="link")
            return True
        if settings.get("antispam_link_flood", False) and _flood_check(chat.id, user.id, "link", 3, 30):
            await handle_violation(context, chat.id, user.id, display_name, message,
                                    "ارسال لینک پشت‌سرهم (Link Flood)", score_key="link")
            return True
        if protection and protection.get("link") and not settings.get("lock_links", False):
            # عضو تازه‌وارد که هنوز خارج از حالت lock_links کامل، لینک می‌فرسته
            await handle_violation(context, chat.id, user.id, display_name, message,
                                    "لینک از عضو تازه‌وارد (New Member Protection)", score_key="link")
            return True

    # ---------- 𓆩 𝟓 — Advertisement Detection ----------
    if settings.get("antispam_ads", False):
        ad_score = _ad_score(chat.id, user.id, text, has_link, is_dup_for_ad)
        if ad_score >= 55:
            await handle_violation(context, chat.id, user.id, display_name, message,
                                    f"محتوای تبلیغاتی مشکوک (امتیاز تبلیغ {ad_score})", score_key="ad")
            return True

    # ---------- 𓆩 𝟔 — Mention Spam ----------
    mentions = MENTION_RE.findall(text)
    if mentions:
        p["mention_count"] += len(mentions)
        max_per_msg = get_threshold(chat.id, "max_mentions_per_message")
        if settings.get("antispam_mentions", False):
            if len(mentions) > max_per_msg:
                await handle_violation(context, chat.id, user.id, display_name, message,
                                        f"منشن زیاد در یک پیام ({len(mentions)} > {max_per_msg})", score_key="mention")
                return True
            if _flood_check(chat.id, user.id, "mention", get_threshold(chat.id, "max_mentions_per_minute"), 60):
                await handle_violation(context, chat.id, user.id, display_name, message,
                                        "منشن پشت‌سرهم زیاد (Mention Flood)", score_key="mention")
                return True

    # ---------- 𓆩 𝟕 — Hashtag Spam ----------
    hashtags = HASHTAG_RE.findall(text)
    if hashtags and settings.get("antispam_hashtags", False):
        p["hashtag_count"] += len(hashtags)
        max_hashtags = get_threshold(chat.id, "max_hashtags")
        if len(hashtags) > max_hashtags or len(set(hashtags)) < len(hashtags) / 2 and len(hashtags) > 3:
            await handle_violation(context, chat.id, user.id, display_name, message,
                                    f"هشتگ بیش‌ازحد/تکراری ({len(hashtags)})", score_key="hashtag")
            return True

    # ---------- 𓆩 𝟖 — Emoji Spam ----------
    emojis = EMOJI_RE.findall(text)
    if emojis and settings.get("antispam_emoji", False):
        p["emoji_count"] += len(emojis)
        max_emojis = get_threshold(chat.id, "max_emojis")
        max_ratio = get_threshold(chat.id, "max_emoji_ratio")
        ratio = len(emojis) / max(1, len(text))
        if len(emojis) > max_emojis or ratio > max_ratio:
            await handle_violation(context, chat.id, user.id, display_name, message,
                                    f"استفاده‌ی زیاد از ایموجی ({len(emojis)})", score_key="emoji")
            return True

    # ---------- 𓆩 𝟗 — Caps Spam (Reuse دقیق از anticaps موجود، فقط از موتور جدید عبور می‌کنه) ----------
    if settings.get("anticaps", False) and host._is_caps_spam(text):
        await handle_violation(context, chat.id, user.id, display_name, message,
                                "استفاده‌ی زیاد از حروف بزرگ (Caps)", score_key="caps")
        return True

    # ---------- کلمات ممنوعه/لینک قدیمی (Reuse دقیق از antispam_words موجود) ----------
    if settings.get("antispam_words", False) and host._is_spam(text):
        await handle_violation(context, chat.id, user.id, display_name, message,
                                "کلمات/محتوای ممنوعه", score_key="words")
        return True

    # ---------- فلود متن (Reuse دقیق از antiflood_text موجود) ----------
    if settings.get("antiflood_text", False) and host._is_flooding(chat.id, user.id):
        mult = strict_multiplier * (protection["flood_multiplier"] if protection else 1.0)
        # پیش از حذف مطمئن می‌شیم واقعاً بر اساس آستانه‌ی سخت‌گیرتر (دشمن/عضو جدید) هم رد شده
        await handle_violation(context, chat.id, user.id, display_name, message,
                                "ارسال پیام‌های پشت‌سرهم (فلود متن)", score_key="flood")
        return True

    return False


async def process_edited_message(update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """𓆩 𝟐𝟖 — Edit Spam: کاربر با ادیت‌کردن نمی‌تونه سیستم رو دور بزنه."""
    import bot as host

    message = update.edited_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or chat.type not in ("group", "supergroup"):
        return False

    settings = host._chat_settings[chat.id]
    if not settings.get("bot_enabled", False) or not settings.get("antispam_edit", False):
        return False
    if is_privileged(chat.id, user.id):
        return False

    _edit_counts[chat.id][user.id] += 1
    display_name = user.first_name or user.username or "کاربر"

    # اگه بعد از ادیت محتوای جدید حاوی لینک/کلمات ممنوعه/تبلیغ شد -> اسپم محسوب می‌شه
    handled = await process_message(update.__class__(update.update_id, message=message), context)
    if handled:
        return True

    if _edit_counts[chat.id][user.id] >= 4 and _flood_check(chat.id, user.id, "edit", 4, 120):
        await handle_violation(context, chat.id, user.id, display_name, message,
                                "ادیت مکرر پیام (Edit Spam)", score_key="edit_spam")
        return True
    return False


# ---------------------------------------------------------------------------
# 🔐 SMART CAPTCHA 2.0 (بند ۱۲/۱۳) — Random Challenge + Max Attempts + Action قابل‌تنظیم
# ---------------------------------------------------------------------------

CAPTCHA_EMOJIS = ["🍎", "🍋", "🍇", "🍉", "🍓", "🍑", "🥝", "🍒"]


def captcha_is_exempt(chat_id: int, user_id: int) -> bool:
    """Owner/Admin/VIP/Friend از کپچا مستثنی‌ان (بند ۱۲)."""
    return is_privileged(chat_id, user_id)


def build_challenge(chat_id: int, user_id: int):
    """یه چالش تصادفی (Random Challenge) با ۴ دکمه می‌سازه، فقط یکی درسته."""
    options = random.sample(CAPTCHA_EMOJIS, 4)
    answer = random.choice(options)
    random.shuffle(options)
    buttons = [
        InlineKeyboardButton(emo, callback_data=f"spamcaptcha:{user_id}:{'1' if emo == answer else '0'}:{emo}")
        for emo in options
    ]
    # دکمه‌ها رو در ۲ ردیف می‌چینیم
    keyboard = [buttons[:2], buttons[2:]]
    text = f"🔐 برای تایید اینکه ربات نیستی، روی دکمه‌ی «{answer}» بزن."
    return text, InlineKeyboardMarkup(keyboard), answer


async def start_captcha(context, chat_id: int, user, invite_link_name=None):
    """جایگزین ارتقایافته‌ی بلوک قدیمی کپچا در welcome_new_member (bot.py)."""
    import bot as host

    if captcha_is_exempt(chat_id, user.id):
        return False  # کپچا لازم نیست - caller باید خوش‌آمد عادی رو بفرسته

    name = user.first_name or user.username or "دوست عزیز"
    await _safe_call(
        lambda: context.bot.restrict_chat_member(
            chat_id=chat_id, user_id=user.id, permissions=ChatPermissions(can_send_messages=False),
        ),
        "محدودسازی برای کپچا",
    )
    text, markup, answer = build_challenge(chat_id, user.id)
    sent = await _safe_call(
        lambda: context.bot.send_message(
            chat_id=chat_id,
            text=f"👋 {name} عزیز، خوش اومدی!\n\n{text}\n⏳ {config.CAPTCHA_TIMEOUT_MINUTES} دقیقه وقت داری.",
            reply_markup=markup,
        ),
        "ارسال چالش کپچا",
    )
    _captcha_pending[chat_id][user.id] = {
        "deadline": time.time() + config.CAPTCHA_TIMEOUT_MINUTES * 60,
        "message_id": sent.message_id if sent else None,
        "answer": answer,
        "attempts": 0,
        "invite_link_name": invite_link_name,
    }
    _captcha_stats[chat_id]["issued"] += 1
    return True


async def captcha_callback_handler(update, context: ContextTypes.DEFAULT_TYPE):
    """𓆩 𝟏𝟑 — Anti-Bypass: فقط خودِ کاربر هدف می‌تونه جواب بده؛ State پرسیستنته."""
    import bot as host

    query = update.callback_query
    parts = (query.data or "").split(":")
    if len(parts) < 4:
        await query.answer()
        return
    _, target_id_s, is_correct, chosen = parts
    target_id = int(target_id_s)
    chat_id = query.message.chat.id
    clicker_id = update.effective_user.id

    if clicker_id != target_id:
        await query.answer("این دکمه برای شما نیست.", show_alert=True)
        return

    pending = _captcha_pending[chat_id].get(target_id)
    if not pending:
        await query.answer("این چالش دیگه معتبر نیست.", show_alert=True)
        return

    if is_correct == "1":
        _captcha_pending[chat_id].pop(target_id, None)
        await _safe_call(
            lambda: context.bot.restrict_chat_member(
                chat_id=chat_id, user_id=target_id, permissions=ChatPermissions(can_send_messages=True),
            ),
            "آزادسازی بعد از کپچا",
        )
        await query.answer("✅ تایید شد! خوش اومدی 🎉")
        await _safe_call(lambda: query.message.delete(), "حذف پیام کپچا")
        _captcha_stats[chat_id]["passed"] += 1
        settings = host._chat_settings[chat_id]
        if settings.get("welcome_enabled", False):
            await host._send_welcome_message(context, chat_id, update.effective_user, pending.get("invite_link_name"))
        host._join_leave_stats[chat_id]["joins"] += 1
        await _safe_call(
            lambda: host._log_admin_action(
                context, chat_id, f"✅ کپچا با موفقیت رد شد: {update.effective_user.first_name or target_id}"
            ),
            "لاگ کپچا",
        )
        return

    # جواب غلط
    pending["attempts"] += 1
    _captcha_stats[chat_id]["failures"] += 1
    _captcha_stats[chat_id][f"fail_{target_id}"] = _captcha_stats[chat_id].get(f"fail_{target_id}", 0) + 1
    max_attempts = get_threshold(chat_id, "captcha_max_attempts")

    if pending["attempts"] >= max_attempts:
        _captcha_pending[chat_id].pop(target_id, None)
        action = get_threshold(chat_id, "captcha_action")
        await query.answer("❌ تعداد تلاش‌های مجاز تموم شد.", show_alert=True)
        await _safe_call(lambda: query.message.delete(), "حذف پیام کپچا (ناموفق)")
        await _apply_captcha_failure_action(context, chat_id, target_id,
                                             update.effective_user.first_name or str(target_id), action)
        return

    # چالش جدید بساز (Anti-Bypass: نمی‌تونه با تلاش دوباره روی همون دکمه‌ی قبلی حدس بزنه)
    text, markup, answer = build_challenge(chat_id, target_id)
    pending["answer"] = answer
    await query.answer(f"❌ اشتباه بود. تلاش {pending['attempts']}/{max_attempts}")
    await _safe_call(
        lambda: query.edit_message_text(
            f"❌ اشتباه بود ({pending['attempts']}/{max_attempts})\n\n{text}",
            reply_markup=markup,
        ),
        "به‌روزرسانی چالش کپچا",
    )


async def _apply_captcha_failure_action(context, chat_id, user_id, display_name, action):
    import bot as host

    if action == "mute":
        until = int(time.time()) + config.CAPTCHA_TIMEOUT_MINUTES * 60 * 3
        await _safe_call(
            lambda: context.bot.restrict_chat_member(
                chat_id=chat_id, user_id=user_id, permissions=ChatPermissions(can_send_messages=False), until_date=until,
            ),
            "میوت بعد از شکست کپچا",
        )
        host._active_mutes[chat_id][user_id] = until
    elif action == "ban":
        await _safe_call(lambda: context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id), "بن بعد از شکست کپچا")
    else:  # kick (پیش‌فرض - رفتار قبلی پروژه حفظ شد)
        async def _do_kick():
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
        await _safe_call(_do_kick, "اخراج بعد از شکست کپچا")

    _log_security(chat_id, user_id, action, "شکست کپچا (تعداد تلاش تموم شد)")
    await _safe_call(
        lambda: host._log_admin_action(context, chat_id, f"🔐 شکست کپچا\n👤 {display_name}\n⚡ اکشن: {action}"),
        "لاگ شکست کپچا",
    )


async def check_captcha_timeouts(bot=None):
    """ارتقایافته‌ی _check_captcha_timeouts قدیمی؛ اکشن حالا قابل‌تنظیمه (نه فقط Kick)."""
    now = time.time()
    for chat_id, pending_map in list(_captcha_pending.items()):
        for user_id, info in list(pending_map.items()):
            if info["deadline"] > now:
                continue
            pending_map.pop(user_id, None)
            action = get_threshold(chat_id, "captcha_action")
            if bot is not None:
                class _Ctx:
                    pass
                ctx = _Ctx()
                ctx.bot = bot
                await _apply_captcha_failure_action(ctx, chat_id, user_id, str(user_id), action)
                try:
                    if info.get("message_id"):
                        await bot.delete_message(chat_id=chat_id, message_id=info["message_id"])
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# 🚨 RAID DETECTION 2.0 + AUTO RAID MODE (بند ۱۴/۱۵/۱۶)
# ---------------------------------------------------------------------------

def _username_similarity_flag(chat_id: int) -> bool:
    """اگه چندتا از یوزرنیم‌های اخیر خیلی شبیه هم باشن (مثلاً user1234, user1235)، مشکوکه."""
    recent = [u for _, _, u in _join_events[chat_id] if u]
    if len(recent) < 4:
        return False
    suspicious_pairs = 0
    for i in range(len(recent) - 1):
        if difflib.SequenceMatcher(None, recent[i], recent[i + 1]).ratio() > 0.75:
            suspicious_pairs += 1
    return suspicious_pairs >= 3


def compute_raid_risk(chat_id: int) -> tuple[int, str]:
    now = time.time()
    window = getattr(config, "RAID_WINDOW_SECONDS", 30)
    recent = [e for e in _join_events[chat_id] if now - e[0] <= window]
    score = min(60, len(recent) * 5)
    if _username_similarity_flag(chat_id):
        score += 20
    # Flood بلافاصله بعد از Join
    quick_flooders = 0
    for ts, uid, _ in recent:
        p = _profiles[chat_id].get(uid)
        if p and p["message_count"] >= 4:
            quick_flooders += 1
    score += min(20, quick_flooders * 5)
    score = min(100, score)
    if score <= 20:
        label = "ریسک کم"
    elif score <= 50:
        label = "ریسک متوسط"
    elif score <= 75:
        label = "ریسک بالا"
    else:
        label = "رید بحرانی"
    return score, label


async def record_join(chat_id: int, user_id: int, username: str, context=None):
    import lock_engine as lock
    import bot as host

    _join_events[chat_id].append((time.time(), user_id, username or ""))
    risk, label = compute_raid_risk(chat_id)
    _raid_state[chat_id]["risk"] = risk

    threshold_score = 60
    already_raid = _raid_state[chat_id]["until"] > time.time()
    if risk >= threshold_score and not already_raid:
        await enter_raid_mode(chat_id, context, risk, label)


async def enter_raid_mode(chat_id: int, context, risk: int, label: str):
    import lock_engine as lock
    import bot as host

    settings = host._chat_settings[chat_id]
    duration = getattr(config, "RAID_MODE_DURATION_MINUTES", 15)
    _raid_state[chat_id]["until"] = time.time() + duration * 60
    _raid_state[chat_id]["prev"] = {
        "captcha_enabled": settings.get("captcha_enabled", False),
        "antispam_words": settings.get("antispam_words", False),
        "antispam_ads": settings.get("antispam_ads", False),
        "member_protection_enabled": settings.get("member_protection_enabled", False),
        "member_protection_level": settings.get("member_protection_level", "NORMAL"),
        "captcha_max_attempts": get_threshold(chat_id, "captcha_max_attempts"),
    }
    settings["captcha_enabled"] = True
    settings["antispam_words"] = True
    settings["antispam_ads"] = True
    settings["member_protection_enabled"] = True
    settings["member_protection_level"] = "STRICT"
    set_threshold(chat_id, "captcha_max_attempts", 1)
    lock.set_temp_lock(chat_id, "links", duration * 60)  # هماهنگ با Lock Engine 2.0

    if context is not None:
        await _safe_call(
            lambda: context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "🚨 حمله‌ی رید شناسایی شد (موتور ریسک ۲.۰)\n"
                    f"سطح خطر: {label} ({risk}/100)\n"
                    f"🔐 کپچا، ضدتبلیغ، محافظت عضو جدید و قفل لینک به‌صورت خودکار برای {duration} دقیقه سخت‌گیر شدن."
                ),
            ),
            "پیام هشدار رید",
        )
        await _safe_call(
            lambda: host._log_admin_action(context, chat_id, f"🚨 رید شناسایی شد — ریسک: {label} ({risk}/100)"),
            "لاگ رید",
        )
    await host.save_state()


async def check_raid_expiry(bot=None):
    import bot as host

    now = time.time()
    for chat_id, state in list(_raid_state.items()):
        if state["until"] and state["until"] <= now:
            prev = state["prev"]
            if prev is not None:
                s = host._chat_settings[chat_id]
                s["captcha_enabled"] = prev.get("captcha_enabled", False)
                s["antispam_words"] = prev.get("antispam_words", False)
                s["antispam_ads"] = prev.get("antispam_ads", False)
                s["member_protection_enabled"] = prev.get("member_protection_enabled", False)
                s["member_protection_level"] = prev.get("member_protection_level", "NORMAL")
                set_threshold(chat_id, "captcha_max_attempts", prev.get("captcha_max_attempts", 3))
            state["until"] = 0.0
            state["prev"] = None
            if bot is not None:
                try:
                    await bot.send_message(chat_id=chat_id, text="✅ حالت رید تموم شد؛ تمام تنظیمات به حالت قبل برگشت.")
                except Exception:
                    pass
            await host.save_state()


# ---------------------------------------------------------------------------
# 🌙 Night Mode (reuse زمان‌بند lock_engine - سیستم زمان‌بندی دوم ساخته نشد)
# ---------------------------------------------------------------------------

def set_night_mode(chat_id: int, start: str, end: str, days=None):
    import lock_engine as lock
    lock.set_schedule(chat_id, "spam_night_mode", start, end, days)


def clear_night_mode(chat_id: int):
    import lock_engine as lock
    lock.clear_schedule(chat_id, "spam_night_mode")


def is_night_mode_active(chat_id: int) -> bool:
    import lock_engine as lock
    return lock._schedule_active(chat_id, "spam_night_mode")


# ---------------------------------------------------------------------------
# 🚨 Emergency Mode (هماهنگ با Lock Engine 2.0 - یه Emergency واحد، نه دوتا)
# ---------------------------------------------------------------------------

_SPAM_EMERGENCY_KEYS = ["antispam_words", "anticaps", "antiflood_text", "antispam_duplicate",
                        "antispam_mentions", "antispam_hashtags", "antispam_ads",
                        "antispam_forward_flood", "captcha_enabled", "member_protection_enabled"]


def enter_emergency(chat_id: int) -> bool:
    import bot as host
    import lock_engine as lock

    if _emergency_snapshot_spam[chat_id] is not None:
        return False
    settings = host._chat_settings[chat_id]
    _emergency_snapshot_spam[chat_id] = {k: settings.get(k, False) for k in _SPAM_EMERGENCY_KEYS}
    _emergency_snapshot_spam[chat_id]["member_protection_level"] = settings.get("member_protection_level", "NORMAL")
    lock.enter_emergency(chat_id)  # قفل‌ها هم سخت‌گیر بشن (هماهنگی کامل با Lock Engine)
    # نکته: بعضی کلیدها (مثل antispam_words) بین دو موتور مشترک‌ان (از طریق
    # lock_engine.LOCK_TYPES["spam_words"]["setting_key"]). چون پروفایل EMERGENCY
    # موتور قفل بقیه‌ی قفل‌ها رو OFF می‌کنه، override زیر باید بعدش اجرا بشه تا
    # مقدار درست (ON) برای این موتور برقرار بمونه.
    for k in _SPAM_EMERGENCY_KEYS:
        settings[k] = True
    settings["member_protection_level"] = "STRICT"
    return True


def exit_emergency(chat_id: int) -> bool:
    import bot as host
    import lock_engine as lock

    snap = _emergency_snapshot_spam[chat_id]
    if snap is None:
        return False
    settings = host._chat_settings[chat_id]
    lock.exit_emergency(chat_id)
    for k in _SPAM_EMERGENCY_KEYS:
        settings[k] = snap.get(k, False)
    settings["member_protection_level"] = snap.get("member_protection_level", "NORMAL")
    _emergency_snapshot_spam[chat_id] = None
    return True


def apply_quick_mode(chat_id: int, mode: str) -> bool:
    import bot as host
    preset = QUICK_MODES.get(mode.upper())
    if not preset:
        return False
    settings = host._chat_settings[chat_id]
    for k, v in preset.items():
        settings[k] = v
    return True


# ---------------------------------------------------------------------------
# 📊 Statistics / User Security Profile / Admin Panel
# ---------------------------------------------------------------------------

def get_stats(chat_id: int) -> dict:
    now = time.time()
    ranges = {"today": 86400, "week": 7 * 86400, "month": 30 * 86400, "total": None}
    by_range = {r: defaultdict(int) for r in ranges}
    totals = defaultdict(int)
    top_users = defaultdict(int)
    for e in _security_log[chat_id]:
        age = now - e["ts"]
        totals[e["action"]] += 1
        top_users[e["user_id"]] += 1
        for r, seconds in ranges.items():
            if seconds is None or age <= seconds:
                by_range[r][e["reason"]] += 1
    return {
        "by_range": {r: dict(v) for r, v in by_range.items()},
        "captcha": dict(_captcha_stats[chat_id]),
        "top_users": sorted(top_users.items(), key=lambda kv: -kv[1])[:5],
        "raid_risk": _raid_state[chat_id]["risk"],
    }


def user_security_profile_text(chat_id: int, user_id: int, display_name: str) -> str:
    p = get_profile(chat_id, user_id)
    score = current_score(chat_id, user_id)
    label, _ = spam_level(score)
    risk = compute_user_risk_score(chat_id, user_id)
    fails = _captcha_stats[chat_id].get(f"fail_{user_id}", 0)
    captcha_status = "قبول‌شده" if fails == 0 else f"{fails} تلاش ناموفق"
    lines = [
        "╭━━━━━━━━━━━━━━━━━━━━━━╮",
        "𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪",
        "𝐔𝐒𝐄𝐑 𝐒𝐄𝐂𝐔𝐑𝐈𝐓𝐘 𝐏𝐑𝐎𝐅𝐈𝐋𝐄",
        "╰━━━━━━━━━━━━━━━━━━━━━━╯",
        "",
        f"👤 {display_name}",
        f"🆔 {user_id}",
        "",
        f"🛡️ ریسک: {risk_level_label(risk)} ({risk}/100)",
        f"📈 امتیاز اسپم: {score} ({label})",
        f"⚠️ تخلفات: {p['violation_count']}",
        f"📝 اخطارها: {p['warning_count']}",
        f"🌊 موارد فلود: {sum(1 for e in _security_log[chat_id] if e['user_id'] == user_id and 'فلود' in e['reason'])}",
        f"🔗 لینک‌های مسدودشده: {p['link_count']}",
        f"🔐 کپچا: {captcha_status}",
    ]
    return "\n".join(lines)


def security_panel_text(chat_id: int) -> str:
    import bot as host
    settings = host._chat_settings[chat_id]
    score, label = chat_security_score(chat_id)
    bot_on = settings.get("bot_enabled", False)
    lines = [
        "╭━━━━━━━━━━━━━━━━━━━━━━╮",
        "𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪",
        "𝐒𝐄𝐂𝐔𝐑𝐈𝐓𝐘 𝐄𝐍𝐆𝐈𝐍𝐄",
        "╰━━━━━━━━━━━━━━━━━━━━━━╯",
        "",
        f"🔐 وضعیت سیستم (سوییچ کلی ربات): {'🟢 فعال' if bot_on else '🔴 خاموش'}",
        f"🛡️ وضعیت امنیت گروه: {label} ({score}/100)",
        "",
        f"🤖 ضد بات: {'🟢 روشن' if settings.get('lock_bots', False) else '🔴 خاموش'}",
        f"🚫 ضد اسپم: {'🟢 روشن' if settings.get('antispam_words', False) else '🔴 خاموش'}"
        "  (همون «کلمات/محتوای اسپم» توی پنل قفل‌ها — یه تنظیم مشترکه، دوتا سیستم جدا نیست)",
        f"🔐 کپچا: {'🟢 روشن' if settings.get('captcha_enabled', False) else '🔴 خاموش'}",
        f"🚨 ضد رید: {'فعال (در حال رید)' if _raid_state[chat_id]['until'] > time.time() else 'آماده'}",
        f"👶 محافظت عضو جدید: {settings.get('member_protection_level', 'NORMAL') if settings.get('member_protection_enabled', False) else 'خاموش'}",
        "",
    ]
    if not bot_on:
        lines.append("⚠️ سوییچ کلی ربات خاموشه؛ تا وقتی روشن نشه، هیچ‌کدام از موارد بالا (نه قفل‌ها، نه ضداسپم) واقعاً اجرا نمی‌شن.")
        lines.append("برای روشن‌کردن: بنویس «ربات روشن» یا از پنل قفل‌ها (نوشتن «قفل») روی دکمه‌ی «وضعیت سیستم» بزن.")
        lines.append("")
    lines.append("𓆩 𝐌𝐑𝐗 𓆪")
    return "\n".join(lines)


def build_panel(chat_id: int):
    text = security_panel_text(chat_id)
    buttons = [
        [InlineKeyboardButton("🛡️ ضد اسپم", callback_data="secpanel:menu:antispam"),
         InlineKeyboardButton("🤖 ضد بات", callback_data="secpanel:menu:antibot")],
        [InlineKeyboardButton("🔐 کپچا", callback_data="secpanel:menu:captcha"),
         InlineKeyboardButton("🚨 ضد رید", callback_data="secpanel:menu:raid")],
        [InlineKeyboardButton("🔗 محافظت لینک", callback_data="secpanel:menu:link"),
         InlineKeyboardButton("📊 آمار", callback_data="secpanel:menu:stats")],
        [InlineKeyboardButton("⚙️ تنظیمات", callback_data="secpanel:menu:settings"),
         InlineKeyboardButton("🎯 آستانه‌ها", callback_data="secpanel:menu:thresholds")],
        [InlineKeyboardButton("👥 استثناها", callback_data="secpanel:menu:exceptions"),
         InlineKeyboardButton("📜 لاگ‌ها", callback_data="secpanel:menu:logs")],
        [InlineKeyboardButton("❌ بستن", callback_data="secpanel:close")],
    ]
    return text, InlineKeyboardMarkup(buttons)


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

    if action == "menu":
        sub = parts[2] if len(parts) > 2 else ""
        settings = host._chat_settings[chat_id]
        await query.answer()
        if sub == "stats":
            stats = get_stats(chat_id)
            lines = ["📊 آمار ضد اسپم", ""]
            for r_key, r_label in (("today", "امروز"), ("week", "این هفته"), ("month", "این ماه"), ("total", "کل")):
                total = sum(stats["by_range"][r_key].values())
                lines.append(f"• {r_label}: {total} مورد")
            lines.append("")
            lines.append(f"🔐 کپچا - صادرشده: {stats['captcha'].get('issued', 0)} | رد شده: {stats['captcha'].get('passed', 0)} | ناموفق: {stats['captcha'].get('failures', 0)}")
            lines.append(f"🚨 ریسک رید فعلی: {stats['raid_risk']}/100")
            await query.message.reply_text("\n".join(lines))
        elif sub == "settings" or sub == "antispam":
            lines = ["⚙️ تنظیمات فعلی ضد اسپم:"]
            for key in ["antispam_words", "anticaps", "antiflood_text", "antispam_duplicate",
                        "antispam_mentions", "antispam_hashtags", "antispam_emoji", "antispam_ads",
                        "antispam_forward_flood", "antispam_photo_flood", "antispam_video_flood"]:
                lines.append(f"{'🟢' if settings.get(key, False) else '🔴'} {key}")
            lines.append("\nتغییر: /antispam <نوع> on|off")
            await query.message.reply_text("\n".join(lines))
        elif sub == "antibot":
            await query.message.reply_text(
                f"🤖 ضد بات: {'🟢 روشن' if settings.get('lock_bots', False) else '🔴 خاموش'}\n"
                "این بخش با موتور قفل ۲.۰ (/lock bot) هماهنگه.\nفعال‌سازی: /antibot on"
            )
        elif sub == "captcha":
            await query.message.reply_text(
                f"🔐 کپچا: {'🟢 روشن' if settings.get('captcha_enabled', False) else '🔴 خاموش'}\n"
                f"اکشن شکست: {get_threshold(chat_id, 'captcha_action')}\n"
                f"حداکثر تلاش: {get_threshold(chat_id, 'captcha_max_attempts')}\n\n"
                "دستورات: /captcha on|off | /captcha action mute|kick|ban | /captcha attempts <عدد>"
            )
        elif sub == "raid":
            risk, label = compute_raid_risk(chat_id)
            active = _raid_state[chat_id]["until"] > time.time()
            await query.message.reply_text(
                f"🚨 وضعیت رید: {'فعال' if active else 'غیرفعال'}\nریسک فعلی: {label} ({risk}/100)\n\n"
                "دستورات: /raid status | /raid off"
            )
        elif sub == "link":
            await query.message.reply_text(
                "🔗 محافظت لینک از همون سیستم موتور قفل ۲.۰ استفاده می‌کنه.\n"
                "وایت‌لیست/بلک‌لیست دامنه (مشترک بین دو موتور):\n"
                "/whitelist add|remove <دامنه>\n/spamblacklist add|remove <دامنه>"
            )
        elif sub == "thresholds":
            await query.message.reply_text(
                "🎯 تنظیم آستانه‌ها:\n/spamconfig <کلید> <مقدار>\n"
                "کلیدها: max_mentions_per_message, max_mentions_per_minute, max_hashtags,\n"
                "max_emojis, max_emoji_ratio, duplicate_similarity, captcha_max_attempts"
            )
        elif sub == "exceptions":
            await query.message.reply_text(
                "👥 Exceptionها کاملاً مشترک با Lock Engine 2.0 هستن:\n/lock exception add|remove <آیدی>"
            )
        elif sub == "logs":
            recent = list(_security_log[chat_id])[-10:]
            if not recent:
                await query.message.reply_text("لاگی ثبت نشده.")
            else:
                lines = ["📜 ۱۰ رویداد امنیتی اخیر:"]
                for e in recent:
                    lines.append(f"👤 {e['user_id']} | {e['action']} | {e['reason']}")
                await query.message.reply_text("\n".join(lines))
        return

    await query.answer()


# ---------------------------------------------------------------------------
# ⌨️ دستورات
# ---------------------------------------------------------------------------

def _require_admin(update) -> bool:
    import bot as host
    return host.is_admin(update.effective_user.id) or host.has_permission(update.effective_user.id, "moderate")


async def _deny(update):
    await update.effective_message.reply_text("🚫 این دستور فقط برای ادمین‌های مجازه.")


ANTISPAM_KEYS = [
    "antispam_words", "anticaps", "antiflood_text", "antispam_duplicate", "antispam_mentions",
    "antispam_hashtags", "antispam_emoji", "antispam_ads", "antispam_forward_flood",
    "antispam_mixed_flood", "antispam_link_flood", "antispam_edit",
    "antispam_photo_flood", "antispam_video_flood", "antispam_voice_flood",
    "antispam_audio_flood", "antispam_document_flood", "antispam_contact_flood",
    "antispam_location_flood", "antispam_poll_flood",
]

_KEY_ALIASES = {
    "words": "antispam_words", "caps": "anticaps", "flood": "antiflood_text",
    "duplicate": "antispam_duplicate", "mentions": "antispam_mentions", "mention": "antispam_mentions",
    "hashtags": "antispam_hashtags", "hashtag": "antispam_hashtags", "emoji": "antispam_emoji",
    "ads": "antispam_ads", "ad": "antispam_ads", "forward": "antispam_forward_flood",
    "mixed": "antispam_mixed_flood", "linkflood": "antispam_link_flood", "edit": "antispam_edit",
    "photo": "antispam_photo_flood", "video": "antispam_video_flood", "voice": "antispam_voice_flood",
    "audio": "antispam_audio_flood", "document": "antispam_document_flood", "contact": "antispam_contact_flood",
    "location": "antispam_location_flood", "poll": "antispam_poll_flood",
}


async def antispam_command(update, context: ContextTypes.DEFAULT_TYPE):
    """/antispam  -> پنل | /antispam <نوع> on|off | /antispam mode <NORMAL|STRICT|...>"""
    import bot as host

    chat_id = update.effective_chat.id
    args = context.args or []
    if not args:
        text, markup = build_panel(chat_id)
        await update.effective_message.reply_text(text, reply_markup=markup)
        return
    if not _require_admin(update):
        await _deny(update)
        return

    if args[0].lower() == "mode" and len(args) >= 2:
        ok = apply_quick_mode(chat_id, args[1])
        await update.effective_message.reply_text(
            f"✅ حالت «{args[1].upper()}» اعمال شد." if ok else "این حالت وجود ندارد."
        )
        await host.save_state()
        return

    if len(args) >= 2 and args[1].lower() in ("on", "off"):
        key = _KEY_ALIASES.get(args[0].lower())
        if not key:
            await update.effective_message.reply_text("نوع نامعتبره.")
            return
        host._chat_settings[chat_id][key] = (args[1].lower() == "on")
        await update.effective_message.reply_text(f"✅ {key} → {args[1].upper()}")
        await host.save_state()
        return

    await update.effective_message.reply_text("استفاده: /antispam <نوع> on|off  یا  /antispam mode <NORMAL|STRICT|HIGH_SECURITY|...>")


async def antibot_command(update, context: ContextTypes.DEFAULT_TYPE):
    """هماهنگ با Lock Engine (lock_bots) - این دستور فقط یه میانبر واضحه."""
    import bot as host

    chat_id = update.effective_chat.id
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            f"🤖 ضد بات: {'🟢 روشن' if host._chat_settings[chat_id].get('lock_bots', False) else '🔴 خاموش'}\n"
            "فعال/غیرفعال: /antibot on یا /antibot off\n"
            "مدیریت لیست مجاز: /lock bot allow|disallow <آیدی>"
        )
        return
    if not _require_admin(update):
        await _deny(update)
        return
    if args[0].lower() in ("on", "off"):
        host._chat_settings[chat_id]["lock_bots"] = (args[0].lower() == "on")
        await update.effective_message.reply_text(f"✅ ضد بات → {'روشن' if args[0].lower() == 'on' else 'خاموش'}")
        await host.save_state()


async def captcha_command(update, context: ContextTypes.DEFAULT_TYPE):
    import bot as host

    chat_id = update.effective_chat.id
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            f"🔐 کپچا: {'🟢 روشن' if host._chat_settings[chat_id].get('captcha_enabled', False) else '🔴 خاموش'}\n"
            f"اکشن شکست: {get_threshold(chat_id, 'captcha_action')} | حداکثر تلاش: {get_threshold(chat_id, 'captcha_max_attempts')}\n\n"
            "دستورات: /captcha on|off | /captcha action mute|kick|ban | /captcha attempts <عدد>"
        )
        return
    if not _require_admin(update):
        await _deny(update)
        return
    sub = args[0].lower()
    if sub in ("on", "off"):
        host._chat_settings[chat_id]["captcha_enabled"] = (sub == "on")
        await update.effective_message.reply_text(f"✅ کپچا → {'روشن' if sub == 'on' else 'خاموش'}")
    elif sub == "action" and len(args) >= 2 and args[1].lower() in ("mute", "kick", "ban"):
        set_threshold(chat_id, "captcha_action", args[1].lower())
        await update.effective_message.reply_text(f"✅ اکشن شکست کپچا → {args[1].upper()}")
    elif sub == "attempts" and len(args) >= 2 and args[1].isdigit():
        set_threshold(chat_id, "captcha_max_attempts", int(args[1]))
        await update.effective_message.reply_text(f"✅ حداکثر تلاش کپچا → {args[1]}")
    else:
        await update.effective_message.reply_text("دستور نامعتبره.")
        return
    await host.save_state()


async def raid_command(update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args or []
    if not args or args[0].lower() == "status":
        risk, label = compute_raid_risk(chat_id)
        active = _raid_state[chat_id]["until"] > time.time()
        await update.effective_message.reply_text(
            f"🚨 وضعیت رید: {'فعال' if active else 'غیرفعال'}\nRisk فعلی: {label} ({risk}/100)"
        )
        return
    if not _require_admin(update):
        await _deny(update)
        return
    if args[0].lower() == "off":
        import bot as host
        await check_raid_expiry(host._bot_instance)
        _raid_state[chat_id]["until"] = 0.0
        await update.effective_message.reply_text("✅ حالت رید به‌صورت دستی خاموش شد.")


async def spamstats_command(update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    stats = get_stats(chat_id)
    lines = ["📊 آمار کامل Anti-Spam Engine", ""]
    for r_key, r_label in (("today", "امروز"), ("week", "این هفته"), ("month", "این ماه"), ("total", "کل")):
        lines.append(f"— {r_label} —")
        by_reason = stats["by_range"][r_key]
        if not by_reason:
            lines.append("چیزی ثبت نشده.")
        for reason, cnt in sorted(by_reason.items(), key=lambda kv: -kv[1])[:8]:
            lines.append(f"• {reason}: {cnt}")
        lines.append("")
    lines.append(f"🔐 کپچا — صادرشده: {stats['captcha'].get('issued', 0)} | قبول: {stats['captcha'].get('passed', 0)} | ناموفق: {stats['captcha'].get('failures', 0)}")
    lines.append(f"🚨 Raid Risk فعلی: {stats['raid_risk']}/100")
    if stats["top_users"]:
        lines.append("\n👥 بیشترین اسپمرها:")
        for uid, cnt in stats["top_users"]:
            lines.append(f"• {uid}: {cnt}")
    await update.effective_message.reply_text("\n".join(lines))


async def security_command(update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args or []
    if args and args[0].lower() == "emergency":
        if not _require_admin(update):
            await _deny(update)
            return
        if len(args) >= 2 and args[1].lower() == "off":
            ok = exit_emergency(chat_id)
            await update.effective_message.reply_text("✅ از حالت اضطراری خارج شدیم." if ok else "توی حالت اضطراری نیستیم.")
        else:
            ok = enter_emergency(chat_id)
            await update.effective_message.reply_text("🚨 حالت اضطراری (ضد اسپم + قفل‌ها) فعال شد." if ok else "از قبل توی حالت اضطراری هستیم.")
        import bot as host
        await host.save_state()
        return
    if args and args[0].lower() == "test":
        if not _require_admin(update):
            await _deny(update)
            return
        if len(args) >= 2 and args[1].lower() in ("on", "off"):
            set_dry_run(chat_id, args[1].lower() == "on")
            await update.effective_message.reply_text(f"🧪 حالت تست → {'روشن' if args[1].lower() == 'on' else 'خاموش'}")
            import bot as host
            await host.save_state()
        return
    text, markup = build_panel(chat_id)
    await update.effective_message.reply_text(text, reply_markup=markup)


async def security_user_command(update, context: ContextTypes.DEFAULT_TYPE):
    import bot as host

    chat_id = update.effective_chat.id
    if not _require_admin(update):
        await _deny(update)
        return
    target_id, name, _r = host._parse_target_and_reason(update)
    if target_id is None:
        await update.effective_message.reply_text("ریپلای بزن یا: /security_user <آیدی>")
        return
    await update.effective_message.reply_text(user_security_profile_text(chat_id, target_id, name))


async def spamconfig_command(update, context: ContextTypes.DEFAULT_TYPE):
    import bot as host

    chat_id = update.effective_chat.id
    if not _require_admin(update):
        await _deny(update)
        return
    args = context.args or []
    if len(args) < 2:
        lines = ["⚙️ آستانه‌های فعلی:"]
        for k in DEFAULT_THRESHOLDS:
            lines.append(f"{k}: {get_threshold(chat_id, k)}")
        lines.append("\nتغییر: /spamconfig <کلید> <مقدار>")
        await update.effective_message.reply_text("\n".join(lines))
        return
    key, raw_value = args[0], args[1]
    if key not in DEFAULT_THRESHOLDS:
        await update.effective_message.reply_text("کلید نامعتبره.")
        return
    try:
        value = float(raw_value) if "." in raw_value or key.endswith("ratio") else int(raw_value)
    except ValueError:
        value = raw_value
    set_threshold(chat_id, key, value)
    await update.effective_message.reply_text(f"✅ {key} → {value}")
    await host.save_state()


async def whitelist_command(update, context: ContextTypes.DEFAULT_TYPE):
    """میانبر مشترک با Lock Engine 2.0 (همون دیکشنری دامنه‌ی وایت‌لیست)."""
    import bot as host

    chat_id = update.effective_chat.id
    if not _require_admin(update):
        await _deny(update)
        return
    args = context.args or []
    if len(args) < 2 or args[0].lower() not in ("add", "remove"):
        await update.effective_message.reply_text("استفاده: /whitelist add|remove <دامنه>")
        return
    domain = args[1].lower().strip()
    if args[0].lower() == "add":
        add_whitelist_domain(chat_id, domain)
        await update.effective_message.reply_text(f"✅ «{domain}» به وایت‌لیست دامنه اضافه شد.")
    else:
        remove_whitelist_domain(chat_id, domain)
        await update.effective_message.reply_text(f"✅ «{domain}» از وایت‌لیست دامنه حذف شد.")
    await host.save_state()


async def spamblacklist_command(update, context: ContextTypes.DEFAULT_TYPE):
    """میانبر مشترک با Lock Engine 2.0 (همون دیکشنری دامنه‌ی بلک‌لیست) - جدا از بلک‌لیست کاربرِ فعلی پروژه‌ست."""
    import bot as host

    chat_id = update.effective_chat.id
    if not _require_admin(update):
        await _deny(update)
        return
    args = context.args or []
    if len(args) < 2 or args[0].lower() not in ("add", "remove"):
        await update.effective_message.reply_text("استفاده: /spamblacklist add|remove <دامنه>")
        return
    domain = args[1].lower().strip()
    if args[0].lower() == "add":
        add_blacklist_domain(chat_id, domain)
        await update.effective_message.reply_text(f"✅ «{domain}» به بلک‌لیست دامنه اضافه شد.")
    else:
        remove_blacklist_domain(chat_id, domain)
        await update.effective_message.reply_text(f"✅ «{domain}» از بلک‌لیست دامنه حذف شد.")
    await host.save_state()


# ---------------------------------------------------------------------------
# 💾 Persistence (فایل مستقل موتور اسپم - شامل State کپچا/رید که قبلاً اصلاً
# ذخیره نمی‌شدن؛ این خودش یه ارتقای Persistence نسبت به قبل هست - بند ۱۳/۲۳/۴۱)
# ---------------------------------------------------------------------------

def collect_state() -> dict:
    return {
        "profiles": {
            str(c): {str(u): dict(p, count_by_type=None) or {k: v for k, v in p.items()} for u, p in inner.items()}
            for c, inner in _profiles.items()
        },
        "chat_thresholds": {str(c): v for c, v in _chat_thresholds.items()},
        "dry_run": {str(c): v for c, v in _dry_run.items()},
        "security_log": {str(c): list(v) for c, v in _security_log.items()},
        "captcha_pending": {
            str(c): {str(u): v for u, v in inner.items()} for c, inner in _captcha_pending.items()
        },
        "captcha_stats": {str(c): dict(v) for c, v in _captcha_stats.items()},
        "raid_state": {
            str(c): {"until": v["until"], "prev": v["prev"], "risk": v["risk"]}
            for c, v in _raid_state.items()
        },
        "emergency_snapshot": {str(c): v for c, v in _emergency_snapshot_spam.items() if v is not None},
    }


def _write_state_file(data: dict):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


async def save_state():
    import asyncio
    try:
        await asyncio.to_thread(_write_state_file, collect_state())
    except Exception as e:
        logger.warning(f"ذخیره‌ی state موتور اسپم ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور اسپم ناموفق بود: {e}")
        return

    for cid, users in data.get("profiles", {}).items():
        for uid, p in users.items():
            base = _new_profile()
            base.update({k: v for k, v in p.items() if k in base})
            _profiles[int(cid)][int(uid)] = base
    for cid, v in data.get("chat_thresholds", {}).items():
        _chat_thresholds[int(cid)] = v
    for cid, v in data.get("dry_run", {}).items():
        _dry_run[int(cid)] = v
    for cid, entries in data.get("security_log", {}).items():
        _security_log[int(cid)] = deque(entries, maxlen=4000)
    for cid, users in data.get("captcha_pending", {}).items():
        _captcha_pending[int(cid)] = {int(u): v for u, v in users.items()}
    for cid, v in data.get("captcha_stats", {}).items():
        _captcha_stats[int(cid)] = defaultdict(int, v)
    for cid, v in data.get("raid_state", {}).items():
        _raid_state[int(cid)]["until"] = v.get("until", 0.0)
        _raid_state[int(cid)]["prev"] = v.get("prev")
        _raid_state[int(cid)]["risk"] = v.get("risk", 0)
    for cid, v in data.get("emergency_snapshot", {}).items():
        _emergency_snapshot_spam[int(cid)] = v

    logger.info("Spam Engine: وضعیت قبلی (شامل State کپچا/رید) بارگذاری شد.")
