# -*- coding: utf-8 -*-
"""
ربات مدیریت گروه تلگرام - پنل کامل با دکمه‌های شیشه‌ای (/menu)

قابلیت‌ها: پاسخ خودکار کلیدواژه‌ای + هوش مصنوعی (Groq)، ضداسپم پیشرفته
(لینک/فلود/کاپس‌لاک/فوروارد کانال/استیکر و گیف)، هشدار و میوت تصاعدی،
بن/آن‌بن، ارتقا/عزل ادمین، پاک‌سازی پیام‌ها، تایید عضو جدید (ضد ربات اسپمی)،
سیستم دوست/دشمن، بلاک‌لیست، آمار فعالیت، اطلاع‌رسانی همگانی، و ذخیره‌سازی
دائمی همه‌چیز روی دیسک. جزئیات کامل توی README.md هست.

نصب:
    pip install -r requirements.txt

اجرا:
    export BOT_TOKEN="توکن ربات از BotFather"
    python bot.py
"""

import asyncio
import datetime
import calendar
import json
import logging
import os
import random
import re
import time
from collections import defaultdict, deque

try:
    from zoneinfo import ZoneInfo
except Exception:  # پایتون خیلی قدیمی یا نبود ماژول zoneinfo
    ZoneInfo = None

from telegram import (
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

try:
    from telegram import MessageOriginChannel
except ImportError:
    MessageOriginChannel = None

import config
import lock_engine
import spam_engine
import moderation_engine
import profile_engine
import economy_engine
import economy_core
import economy_jobs
import economy_missions
import economy_bank
import economy_market
import economy_security
import economy_theft
import economy_city
import economy_property
import economy_district
import economy_vehicle
import economy_business
import economy_marriage
import economy_pet
import economy_games
import economy_inventory
import economy_blackmarket
import economy_underground
import economy_achievements
import economy_leaderboard
import economy_events
import economy_shop2
import economy_balance
import economy_antiabuse
import economy_admin
import economy_panel
import poll_engine
import purge_engine

# ---------- تایم‌زون نمایش تاریخ/ساعت (برای پیام‌های ورود/خروج و...) ----------
# اگه zoneinfo یا دیتابیس tzdata (مثلاً روی بعضی نسخه‌های ویندوز) در دسترس نباشه،
# بی‌سروصدا به ساعت سیستمی خود سرور برمی‌گرده (fail-safe، دقیقاً مثل رفتار قبلی پروژه).
try:
    _TZ = ZoneInfo(getattr(config, "TIMEZONE", "Asia/Tehran")) if ZoneInfo else None
except Exception:
    _TZ = None


def _now_local() -> datetime.datetime:
    return datetime.datetime.now(_TZ)


try:
    from groq import Groq

    # timeout پایین‌تر باعث می‌شه اگه گروک دیر جواب بده، سریع‌تر خطا بگیریم به‌جای معطلی طولانی
    _ai_client = (
        Groq(api_key=config.GROQ_API_KEY, timeout=config.AI_TIMEOUT_SECONDS, max_retries=1)
        if config.GROQ_API_KEY
        else None
    )
except ImportError:
    _ai_client = None

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# httpx به‌ازای هر درخواست به تلگرام یه خط لاگ INFO چاپ می‌کنه (مثلاً همون خط
# "HTTP Request: POST .../sendMessage" که توی پاورشل می‌بینی) - این یه خطا نیست،
# فقط شلوغش می‌کنه؛ سطحش رو می‌بریم بالا تا فقط لاگ‌های خود ربات دیده بشن.
logging.getLogger("httpx").setLevel(logging.WARNING)


async def _safe_reply(message, text: str, **kwargs):
    """مثل message.reply_text ولی اگه پیامی که قراره روش ریپلای بشه قبلش پاک شده باشه
    (مثلاً به‌خاطر تاخیر پاسخ AI یا حذف توسط ضداسپم)، به‌جای کرش کردن، پیام رو بدون
    ریپلای می‌فرسته."""
    try:
        return await message.reply_text(text, **kwargs)
    except BadRequest as e:
        low = str(e).lower()
        if "not found" in low or "can't be replied" in low or "message to reply not found" in low:
            try:
                return await message.get_bot().send_message(
                    chat_id=message.chat_id, text=text, **kwargs
                )
            except Exception as e2:
                logger.warning(f"ارسال پیام جایگزین (بدون ریپلای) هم ناموفق بود: {e2}")
                return None
        logger.warning(f"خطای ارسال پیام: {e}")
        return None


async def global_error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    """جلوی چاپ traceback خام توی کنسول رو می‌گیره و یه لاگ خلاصه و قابل‌فهم می‌ده."""
    logger.warning(f"خطای پردازش‌نشده در آپدیت: {context.error}")

# برای ردیابی فلود: chat_id -> user_id -> deque از timestamp پیام‌ها
_message_log = defaultdict(lambda: defaultdict(deque))

# برای ثبت چت‌هایی که ربات توشون فعاله (برای /notify همگانی)
_known_chats = set()

# آمار فعالیت: chat_id -> user_id -> تعداد پیام
_message_counts = defaultdict(lambda: defaultdict(int))
# آخرین نامی که از هر کاربر دیدیم (برای نمایش توی لیست تاپ)
_user_display_names = {}

# تعداد اخطارهای هر کاربر: chat_id -> user_id -> تعداد
_warnings = defaultdict(lambda: defaultdict(int))

# لیست دوست/دشمن: chat_id -> set(user_id)
_enemies = defaultdict(set)
_friends = defaultdict(set)

# کلماتی که با ریپلای زدنشون کاربر دشمن/دوست می‌شه (هر گروه لیست خودشو داره)
_enemy_trigger_words = defaultdict(lambda: {config.ENEMY_TRIGGER_WORD})
_friend_trigger_words = defaultdict(lambda: {config.FRIEND_TRIGGER_WORD})

# ---------- ☠️ TERMINATOR 2.0 ----------
# پروفایل مستقل هر Target: chat_id -> user_id -> {username, name, added_date, message_count,
# response_count, threat_score, response_mode, cooldown_seconds, last_response_time,
# recent_responses, last_message_time, is_boss}
_terminator_targets = defaultdict(dict)
# زمان چندتا پیام اخیر هر Target (فقط توی حافظه، برای تشخیص Rapid/Flood؛ با ری‌استارت پاک می‌شه - بی‌اهمیته)
_terminator_recent_times = defaultdict(lambda: defaultdict(lambda: deque(maxlen=6)))
# حالت ترمیناتور هر گروه: "active" (پاسخ می‌ده) یا "silent" (فقط آمار ثبت می‌کنه، پاسخ نمی‌ده)
_terminator_mode = defaultdict(lambda: "active")
# مد پاسخ‌دهیِ پیش‌فرضِ گروه (اگه خودِ Target مد اختصاصی نداشته باشه از این استفاده می‌شه)
_terminator_default_mode = defaultdict(lambda: config.DEFAULT_TERMINATOR_MODE)
# انقضای خودکار دشمنی (/enemy 24h): chat_id -> user_id -> timestamp انقضا (نبودنِ کلید = دائمی)
_enemy_expiry = defaultdict(dict)
# پروفایل مستقل هر Friend: chat_id -> user_id -> {username, name, added_date, message_count,
# response_count, last_message_time, recent_responses}
_friend_profiles = defaultdict(dict)

# حالت پاسخ هوش مصنوعی هر گروه: "off" / "all" / "non_admins" / "admins_only"
_ai_reply_mode = defaultdict(lambda: config.AI_REPLY_MODE_DEFAULT)

# حد اخطار و مدت میوت هر گروه (قابل تغییر با /menu)
_warn_limit = defaultdict(lambda: config.WARNING_LIMIT_BEFORE_MUTE)
_mute_minutes = defaultdict(lambda: config.MUTE_DURATION_MINUTES)

# چند بار هر کاربر تا الان میوت شده (برای میوت تصاعدی: هر بار مدت دوبرابر می‌شه): chat_id -> user_id -> تعداد
_mute_counts = defaultdict(lambda: defaultdict(int))

# میوت‌های فعال الان: chat_id -> {user_id: زمان پایان میوت (timestamp)}
_active_mutes = defaultdict(dict)

# عضوهای جدیدی که منتظر تایید (زدن دکمه) هستن: chat_id -> {user_id: {"deadline": ts, "message_id": mid}}

# کاربرای ویژه (VIP): از همه‌ی قفل‌ها/ضداسپم مستثنا هستن، مثل ادمین: chat_id -> set(user_id)
_vip_users = defaultdict(set)

# لیستی که خودمون از کاربرای بن‌شده نگه می‌داریم (چون تلگرام API لیست بن رو مستقیم نمی‌ده): chat_id -> set(user_id)
_banned_users = defaultdict(set)

# نگه‌داری شیء ربات برای استفاده در تسک‌های پس‌زمینه‌ای (خارج از هندلرهای عادی)
_bot_instance = None

# تنظیمات قابل روشن/خاموش هر گروه (شامل سیستم ترمیناتور): chat_id -> {key: bool}
_chat_settings = defaultdict(lambda: dict(config.DEFAULT_SETTINGS))

# زمان عضو شدن هر کاربر توی هر گروه (برای تشخیص اعضای تازه‌وارد): chat_id -> {user_id: timestamp}
_join_times = defaultdict(dict)

# ---------- سیستم ورود/خروج حرفه‌ای ----------
# متن سفارشی خوش‌آمدگویی/ترک که مالک با /welcome_text یا /leave_text ست کرده: chat_id -> str
# اگه ست نشده باشه (کلید توی دیکشنری نیست)، از استخر پیام‌های تصادفی توی config.py استفاده می‌شه.
_custom_welcome_text = {}
_custom_leave_text = {}
# تمِ انتخابیِ هر گروه برای پیام‌های ورود/خروج: chat_id -> کلید توی config.MESSAGE_THEMES
_chat_theme = {}
# آمار ورود/خروج هر گروه، برای /groupstats
_join_leave_stats = defaultdict(lambda: {"joins": 0, "bot_joins": 0, "leaves": 0, "kicks": 0, "unbans": 0})
# جلوگیری از پیام دوتایی وقتی اخراج (بن + آن‌بن خودکار تلگرام) دو تا رویداد جدا تولید می‌کنه:
# chat_id -> {user_id: زمان آخرین پیام خروجی که فرستادیم}
_recent_leave_announcement = defaultdict(dict)
_LEAVE_DEDUP_SECONDS = 20

# ردیابی فلود استیکر/گیف: chat_id -> user_id -> deque از timestamp
_media_log = defaultdict(lambda: defaultdict(deque))

# بلاک‌لیست دستی کاربرا: chat_id -> set(user_id)
_blacklist = defaultdict(set)

# همه‌ی اعضایی که تا الان توی هر گروه دیده‌شدن (پیام دادن یا عضو شدن) - برای «تگ همه»: chat_id -> set(user_id)
_known_members = defaultdict(set)

# ---------- افزودنی‌های حرفه‌ای: پروفایل / XP / لقب / اخطار حرفه‌ای / ضد رید ----------

# امتیاز XP هر کاربر: chat_id -> user_id -> xp
_xp = defaultdict(lambda: defaultdict(int))
# آخرین باری که هر کاربر XP گرفته (برای کول‌داون ضدفارم‌کردن XP با اسپم): chat_id -> user_id -> ts
_last_xp_time = defaultdict(dict)
# لقب اختصاصی هر کاربر (با /setlqab): chat_id -> user_id -> str
_custom_titles = defaultdict(dict)
# زمان انقضای هر اخطار فعال (برای اخطار حرفه‌ای): chat_id -> user_id -> [ts, ts, ...]
_warning_expiry = defaultdict(lambda: defaultdict(list))
# آخرین دلیل اخطار هر کاربر (برای نمایش توی پروفایل/دستور اخطار): chat_id -> user_id -> str
_last_warning_reason = defaultdict(dict)


# ---------- اقتصاد داخلی گروه ----------
# موجودی سکه‌ی هر کاربر: chat_id -> user_id -> coins
_wallet = defaultdict(lambda: defaultdict(int))
# آخرین باری که هر کاربر جایزه‌ی روزانه گرفته: chat_id -> user_id -> ts
_last_daily = defaultdict(dict)
# آیتم‌های خریداری‌شده از فروشگاه: chat_id -> user_id -> set(item_key)
_owned_badges = defaultdict(lambda: defaultdict(set))
# قیمت‌های سفارشی‌شده‌ی آیتم‌های فروشگاه توسط ادمین هر گروه: chat_id -> {item_key: price}
_shop_price_overrides = defaultdict(dict)
# آیتم‌هایی که ادمین از فروشگاه یه گروه خاص مخفی کرده: chat_id -> set(item_key)
_shop_hidden_items = defaultdict(set)

# ---------- نظرسنجی (در حافظه؛ با ری‌استارت شدن ربات نظرسنجی‌های فعال پاک می‌شن) ----------
# poll_message_id -> {"chat_id","question","options","votes": {user_id: idx}}
_polls = {}

# ---------- قرعه‌کشی (در حافظه؛ فقط یکی هم‌زمان توی هر گروه) ----------
# chat_id -> {"message_id","end_ts","participants": set(user_id),"prize"}
_lotteries = {}

# ---------- تنظیمات اقتصاد/XP/قرعه‌کشی که ادمین از پنل ویژه می‌تونه در زمان اجرا (بدون
# دست‌زدن به config.py) برای هر گروه جدا تنظیم کنه: chat_id -> {key: value} ----------
def _default_econ_settings() -> dict:
    return {
        "daily_min": config.DAILY_REWARD_MIN,
        "daily_max": config.DAILY_REWARD_MAX,
        "activity_min": config.ACTIVITY_COIN_MIN,
        "activity_max": config.ACTIVITY_COIN_MAX,
        "xp_min": config.XP_PER_MESSAGE_MIN,
        "xp_max": config.XP_PER_MESSAGE_MAX,
        "lottery_minutes": config.LOTTERY_DEFAULT_MINUTES,
    }


_econ_settings = defaultdict(_default_econ_settings)

# ---------- پنل شخصی پیوی برای کاربرای عادی ----------
# فعال/غیرفعال بودن پاسخ هوش مصنوعی توی پیوی، برای هر کاربر جدا: user_id -> bool
_dm_ai_enabled = defaultdict(bool)
# آخرین عنوانی که از هر گروه دیدیم (برای نمایش توی پنل پیوی/انتخاب گروه): chat_id -> title
_chat_titles = {}
# گروهی که هر ادمین توی پنل پیوی (/menu یا /adminpanel در پیوی) انتخاب کرده: admin_user_id -> chat_id
_admin_selected_chat = {}

LINK_PATTERN = re.compile(r"(https?://|t\.me/|@\w{4,})", re.IGNORECASE)
TAG_PATTERN = re.compile(r"@\w{4,}")
HASHTAG_PATTERN = re.compile(r"#\w+")

# ---------- ذخیره‌سازی دائمی (تا با ری‌استارت شدن ربات، تنظیمات از بین نره) ----------

_save_lock = asyncio.Lock()


def _collect_state() -> dict:
    """همه‌ی دیکشنری‌های سراسری رو به یه ساختار قابل ذخیره توی JSON تبدیل می‌کنه."""
    return {
        "known_chats": list(_known_chats),
        "message_counts": {str(cid): dict(v) for cid, v in _message_counts.items()},
        "user_display_names": {str(uid): name for uid, name in _user_display_names.items()},
        "warnings": {str(cid): dict(v) for cid, v in _warnings.items()},
        "enemies": {str(cid): list(v) for cid, v in _enemies.items()},
        "friends": {str(cid): list(v) for cid, v in _friends.items()},
        "terminator_targets": {
            str(cid): {str(uid): profile for uid, profile in inner.items()}
            for cid, inner in _terminator_targets.items()
        },
        "terminator_mode": {str(cid): mode for cid, mode in _terminator_mode.items()},
        "terminator_default_mode": {str(cid): mode for cid, mode in _terminator_default_mode.items()},
        "enemy_expiry": {str(cid): {str(uid): ts for uid, ts in inner.items()} for cid, inner in _enemy_expiry.items()},
        "friend_profiles": {
            str(cid): {str(uid): profile for uid, profile in inner.items()}
            for cid, inner in _friend_profiles.items()
        },
        "enemy_trigger_words": {str(cid): list(v) for cid, v in _enemy_trigger_words.items()},
        "friend_trigger_words": {str(cid): list(v) for cid, v in _friend_trigger_words.items()},
        "ai_reply_mode": {str(cid): mode for cid, mode in _ai_reply_mode.items()},
        "chat_settings": {str(cid): dict(v) for cid, v in _chat_settings.items()},
        "warn_limit": {str(cid): v for cid, v in _warn_limit.items()},
        "mute_minutes": {str(cid): v for cid, v in _mute_minutes.items()},
        "mute_counts": {str(cid): dict(v) for cid, v in _mute_counts.items()},
        "active_mutes": {str(cid): dict(v) for cid, v in _active_mutes.items()},
        "blacklist": {str(cid): list(v) for cid, v in _blacklist.items()},
        "known_members": {str(cid): list(v) for cid, v in _known_members.items()},
        "vip_users": {str(cid): list(v) for cid, v in _vip_users.items()},
        "banned_users": {str(cid): list(v) for cid, v in _banned_users.items()},
        "text_length_totals": {str(cid): dict(v) for cid, v in _text_length_totals.items()},
        "text_message_counts": {str(cid): dict(v) for cid, v in _text_message_counts.items()},
        "media_counts": {str(cid): dict(v) for cid, v in _media_counts.items()},
        "hour_activity": {str(cid): dict(v) for cid, v in _hour_activity.items()},
        "weekday_activity": {str(cid): dict(v) for cid, v in _weekday_activity.items()},
        "xp": {str(cid): dict(v) for cid, v in _xp.items()},
        "daily_activity": {
            str(cid): {str(u): days for u, days in inner.items()} for cid, inner in _daily_activity.items()
        },
        "activity_score": {str(cid): dict(v) for cid, v in _activity_score.items()},
        "streaks": {str(cid): {str(u): s for u, s in inner.items()} for cid, inner in _streaks.items()},
        "last_message_time": {str(cid): {str(u): t for u, t in v.items()} for cid, v in _last_message_time.items()},
        "message_type_counts": {
            str(cid): {str(u): dict(types) for u, types in inner.items()} for cid, inner in _message_type_counts.items()
        },
        "user_hour_activity": {
            str(cid): {str(u): dict(hrs) for u, hrs in inner.items()} for cid, inner in _user_hour_activity.items()
        },
        "achievements": {
            str(cid): {str(u): list(a) for u, a in inner.items()} for cid, inner in _achievements.items()
        },
        "username_to_id": dict(_username_to_id),
        "custom_titles": {str(cid): {str(u): t for u, t in v.items()} for cid, v in _custom_titles.items()},
        "warning_expiry": {str(cid): {str(u): list(v) for u, v in inner.items()} for cid, inner in _warning_expiry.items()},
        "last_warning_reason": {str(cid): {str(u): r for u, r in v.items()} for cid, v in _last_warning_reason.items()},
        "wallet": {str(cid): dict(v) for cid, v in _wallet.items()},
        "last_daily": {str(cid): {str(u): t for u, t in v.items()} for cid, v in _last_daily.items()},
        "owned_badges": {
            str(cid): {str(u): list(items) for u, items in inner.items()} for cid, inner in _owned_badges.items()
        },
        "shop_price_overrides": {str(cid): dict(v) for cid, v in _shop_price_overrides.items()},
        "shop_hidden_items": {str(cid): list(v) for cid, v in _shop_hidden_items.items()},
        "econ_settings": {str(cid): dict(v) for cid, v in _econ_settings.items()},
        "dm_ai_enabled": {str(uid): v for uid, v in _dm_ai_enabled.items()},
        "chat_titles": {str(cid): t for cid, t in _chat_titles.items()},
        "join_times": {str(cid): {str(u): t for u, t in v.items()} for cid, v in _join_times.items()},
        "custom_welcome_text": {str(cid): t for cid, t in _custom_welcome_text.items()},
        "custom_leave_text": {str(cid): t for cid, t in _custom_leave_text.items()},
        "chat_theme": {str(cid): t for cid, t in _chat_theme.items()},
        "join_leave_stats": {str(cid): dict(v) for cid, v in _join_leave_stats.items()},
        # فاز ۳: جدول نظرسنجی‌های فعال (سؤال/گزینه/رأی/تایمر/...) - قبلاً اصلاً ذخیره نمی‌شد
        "polls": poll_engine.serialize_active_polls(_polls),
    }


def _write_state_file(data: dict):
    tmp_path = config.STATE_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp_path, config.STATE_FILE)


async def save_state():
    async with _save_lock:
        try:
            await asyncio.to_thread(_write_state_file, _collect_state())
        except Exception as e:
            logger.warning(f"ذخیره‌ی وضعیت ناموفق بود: {e}")
    # هر موتور (lock/spam/moderation/profile/economy/poll) state خودشو توی فایل
    # مستقل خودش نگه می‌داره. قبلاً این ۶ تا پشت‌سرهم await می‌شدن، یعنی هر کدوم
    # باید منتظر تموم‌شدن I/O قبلیش می‌موند و کل save_state() (که بعد از تقریباً
    # هر دستور تغییردهنده‌ی وضعیت صدا زده می‌شه) به‌اندازه‌ی مجموع I/O همه‌شون طول
    # می‌کشید. چون هرکدوم lock/فایل مستقل خودشونو دارن و به هم وابسته نیستن،
    # الان با gather هم‌زمان اجرا می‌شن تا تأخیر کلی save_state() کم بشه.
    results = await asyncio.gather(
        lock_engine.save_state(),
        spam_engine.save_state(),
        moderation_engine.save_state(),
        profile_engine.save_state(),
        economy_engine.save_state(),
        economy_core.save_state(),
        economy_jobs.save_state(),
        economy_missions.save_state(),
        economy_bank.save_state(),
        economy_market.save_state(),
        economy_security.save_state(),
        economy_theft.save_state(),
        economy_city.save_state(),
        economy_property.save_state(),
        economy_district.save_state(),
        economy_vehicle.save_state(),
        economy_business.save_state(),
        economy_admin.save_state(),
        economy_marriage.save_state(),
        economy_pet.save_state(),
        economy_games.save_state(),
        economy_inventory.save_state(),
        economy_blackmarket.save_state(),
        economy_underground.save_state(),
        economy_events.save_state(),
        poll_engine.save_state(),
        purge_engine.save_state(),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"ذخیره‌ی state یکی از موتورها ناموفق بود: {r}")


def load_state():
    """موقع بالا اومدن ربات، اگه فایل ذخیره‌شده از قبل باشه، همه‌چیز رو برمی‌گردونه."""
    lock_engine.load_state()
    spam_engine.load_state()
    moderation_engine.load_state()
    profile_engine.load_state()
    economy_engine.load_state()
    economy_core.load_state()
    economy_jobs.load_state()
    economy_missions.load_state()
    economy_bank.load_state()
    economy_market.load_state()
    economy_security.load_state()
    economy_theft.load_state()
    economy_city.load_state()
    economy_property.load_state()
    economy_district.load_state()
    economy_vehicle.load_state()
    economy_business.load_state()
    economy_admin.load_state()
    economy_marriage.load_state()
    economy_pet.load_state()
    economy_games.load_state()
    economy_inventory.load_state()
    economy_blackmarket.load_state()
    economy_underground.load_state()
    economy_events.load_state()
    poll_engine.load_state()
    purge_engine.load_state()
    if not os.path.exists(config.STATE_FILE):
        return
    try:
        with open(config.STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن وضعیت ذخیره‌شده ناموفق بود: {e}")
        return

    _known_chats.update(data.get("known_chats", []))

    for cid, counts in data.get("message_counts", {}).items():
        _message_counts[int(cid)] = defaultdict(int, {int(u): c for u, c in counts.items()})

    _user_display_names.update({int(u): n for u, n in data.get("user_display_names", {}).items()})

    for cid, counts in data.get("warnings", {}).items():
        _warnings[int(cid)] = defaultdict(int, {int(u): c for u, c in counts.items()})

    for cid, ids in data.get("enemies", {}).items():
        _enemies[int(cid)] = set(ids)
    for cid, ids in data.get("friends", {}).items():
        _friends[int(cid)] = set(ids)
    for cid, words in data.get("enemy_trigger_words", {}).items():
        _enemy_trigger_words[int(cid)] = set(words) or {config.ENEMY_TRIGGER_WORD}
    for cid, words in data.get("friend_trigger_words", {}).items():
        _friend_trigger_words[int(cid)] = set(words) or {config.FRIEND_TRIGGER_WORD}

    for cid, inner in data.get("terminator_targets", {}).items():
        _terminator_targets[int(cid)] = {int(uid): profile for uid, profile in inner.items()}
    for cid, mode in data.get("terminator_mode", {}).items():
        _terminator_mode[int(cid)] = mode if mode in ("active", "silent") else "active"
    for cid, mode in data.get("terminator_default_mode", {}).items():
        _terminator_default_mode[int(cid)] = mode if mode in config.TERMINATOR_RESPONSE_MODES else config.DEFAULT_TERMINATOR_MODE
    for cid, inner in data.get("enemy_expiry", {}).items():
        _enemy_expiry[int(cid)] = {int(uid): ts for uid, ts in inner.items()}
    for cid, inner in data.get("friend_profiles", {}).items():
        _friend_profiles[int(cid)] = {int(uid): profile for uid, profile in inner.items()}

    for cid, mode in data.get("ai_reply_mode", {}).items():
        _ai_reply_mode[int(cid)] = mode

    for cid, settings in data.get("chat_settings", {}).items():
        merged = dict(config.DEFAULT_SETTINGS)
        merged.update(settings)
        _chat_settings[int(cid)] = merged

    for cid, v in data.get("warn_limit", {}).items():
        _warn_limit[int(cid)] = v
    for cid, v in data.get("mute_minutes", {}).items():
        _mute_minutes[int(cid)] = v

    for cid, counts in data.get("mute_counts", {}).items():
        _mute_counts[int(cid)] = defaultdict(int, {int(u): c for u, c in counts.items()})

    for cid, mutes in data.get("active_mutes", {}).items():
        _active_mutes[int(cid)] = {int(u): until for u, until in mutes.items()}

    for cid, ids in data.get("blacklist", {}).items():
        _blacklist[int(cid)] = set(ids)

    for cid, ids in data.get("known_members", {}).items():
        _known_members[int(cid)] = set(ids)

    for cid, ids in data.get("vip_users", {}).items():
        _vip_users[int(cid)] = set(ids)
    for cid, ids in data.get("banned_users", {}).items():
        _banned_users[int(cid)] = set(ids)

    for cid, totals in data.get("text_length_totals", {}).items():
        _text_length_totals[int(cid)] = defaultdict(int, {int(u): c for u, c in totals.items()})
    for cid, counts in data.get("text_message_counts", {}).items():
        _text_message_counts[int(cid)] = defaultdict(int, {int(u): c for u, c in counts.items()})
    for cid, counts in data.get("media_counts", {}).items():
        _media_counts[int(cid)] = defaultdict(int, {int(u): c for u, c in counts.items()})
    for cid, hours in data.get("hour_activity", {}).items():
        _hour_activity[int(cid)] = defaultdict(int, {int(h): c for h, c in hours.items()})
    for cid, days in data.get("weekday_activity", {}).items():
        _weekday_activity[int(cid)] = defaultdict(int, {int(d): c for d, c in days.items()})

    for cid, counts in data.get("xp", {}).items():
        _xp[int(cid)] = defaultdict(int, {int(u): c for u, c in counts.items()})

    for cid, inner in data.get("daily_activity", {}).items():
        per_chat_daily = defaultdict(dict)
        for u, days in inner.items():
            per_chat_daily[int(u)] = days
        _daily_activity[int(cid)] = per_chat_daily
    for cid, counts in data.get("activity_score", {}).items():
        _activity_score[int(cid)] = defaultdict(int, {int(u): c for u, c in counts.items()})
    for cid, inner in data.get("streaks", {}).items():
        _streaks[int(cid)] = {int(u): s for u, s in inner.items()}
    for cid, inner in data.get("last_message_time", {}).items():
        _last_message_time[int(cid)] = {int(u): t for u, t in inner.items()}
    for cid, inner in data.get("message_type_counts", {}).items():
        per_chat = defaultdict(lambda: defaultdict(int))
        for u, types in inner.items():
            per_chat[int(u)] = defaultdict(int, types)
        _message_type_counts[int(cid)] = per_chat
    for cid, inner in data.get("user_hour_activity", {}).items():
        per_chat_hours = defaultdict(lambda: defaultdict(int))
        for u, hrs in inner.items():
            per_chat_hours[int(u)] = defaultdict(int, {int(h): c for h, c in hrs.items()})
        _user_hour_activity[int(cid)] = per_chat_hours
    for cid, inner in data.get("achievements", {}).items():
        per_chat_ach = defaultdict(set)
        for u, a in inner.items():
            per_chat_ach[int(u)] = set(a)
        _achievements[int(cid)] = per_chat_ach
    _username_to_id.update(data.get("username_to_id", {}))

    for cid, titles in data.get("custom_titles", {}).items():
        _custom_titles[int(cid)] = {int(u): t for u, t in titles.items()}
    for cid, inner in data.get("warning_expiry", {}).items():
        _warning_expiry[int(cid)] = defaultdict(list, {int(u): list(v) for u, v in inner.items()})
    for cid, reasons in data.get("last_warning_reason", {}).items():
        _last_warning_reason[int(cid)] = {int(u): r for u, r in reasons.items()}

    for cid, coins in data.get("wallet", {}).items():
        _wallet[int(cid)] = defaultdict(int, {int(u): c for u, c in coins.items()})
    for cid, times in data.get("last_daily", {}).items():
        _last_daily[int(cid)] = {int(u): t for u, t in times.items()}
    for cid, inner in data.get("owned_badges", {}).items():
        _owned_badges[int(cid)] = defaultdict(set, {int(u): set(items) for u, items in inner.items()})

    for cid, prices in data.get("shop_price_overrides", {}).items():
        _shop_price_overrides[int(cid)] = {k: v for k, v in prices.items()}

    for cid, items in data.get("shop_hidden_items", {}).items():
        _shop_hidden_items[int(cid)] = set(items)

    for cid, settings in data.get("econ_settings", {}).items():
        merged = _default_econ_settings()
        merged.update(settings)
        _econ_settings[int(cid)] = merged

    for uid, v in data.get("dm_ai_enabled", {}).items():
        _dm_ai_enabled[int(uid)] = v

    _chat_titles.update({int(cid): t for cid, t in data.get("chat_titles", {}).items()})

    for cid, times in data.get("join_times", {}).items():
        _join_times[int(cid)] = {int(u): t for u, t in times.items()}

    _custom_welcome_text.update({int(cid): t for cid, t in data.get("custom_welcome_text", {}).items()})
    _custom_leave_text.update({int(cid): t for cid, t in data.get("custom_leave_text", {}).items()})
    _chat_theme.update(
        {int(cid): t for cid, t in data.get("chat_theme", {}).items() if t in config.MESSAGE_THEMES}
    )

    for cid, stats in data.get("join_leave_stats", {}).items():
        base = {"joins": 0, "bot_joins": 0, "leaves": 0, "kicks": 0, "unbans": 0}
        base.update(stats)
        _join_leave_stats[int(cid)] = base

    # فاز ۳: جدول نظرسنجی‌های فعال (تا با ری‌استارت ربات نظرسنجی‌های بازِ در حال شمارش از بین نرن)
    _polls.update(poll_engine.deserialize_active_polls(data.get("polls", {})))

    logger.info("وضعیت قبلی از فایل بارگذاری شد.")


def get_setting(chat_id: int, key: str) -> bool:
    return _chat_settings[chat_id].get(key, config.DEFAULT_SETTINGS.get(key, False))


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def has_permission(user_id: int, permission: str) -> bool:
    """چک می‌کنه که این ادمین اجازه‌ی این کار خاص رو داره یا نه.
    اگه آیدیش توی ADMIN_PERMISSIONS نباشه، یعنی دسترسی کامل داره (پیش‌فرض)."""
    if user_id not in config.ADMIN_IDS:
        return False
    allowed = config.ADMIN_PERMISSIONS.get(user_id)
    if allowed is None:
        return True
    return permission in allowed


# ---------- توابع کمکی افزودنی‌های حرفه‌ای (XP/Level/لقب/اخطار حرفه‌ای/لاگ) ----------

def _xp_level(xp: int) -> int:
    return xp // config.XP_PER_LEVEL


def _xp_progress(xp: int):
    """برمی‌گردونه: (level, مقدار XP داخل همین level, XP لازم برای هر level)."""
    level = _xp_level(xp)
    into_level = xp - level * config.XP_PER_LEVEL
    return level, into_level, config.XP_PER_LEVEL


def _auto_title_for_level(level: int) -> str:
    best = ""
    for threshold in sorted(config.LEVEL_AUTO_TITLES):
        if level >= threshold:
            best = config.LEVEL_AUTO_TITLES[threshold]
    return best


def _display_title(chat_id: int, user_id: int) -> str:
    """لقب اختصاصی (اگه ست شده باشه) وگرنه لقب خودکار بر اساس Level."""
    custom = _custom_titles[chat_id].get(user_id)
    if custom:
        return custom
    xp = _xp[chat_id].get(user_id, 0)
    level, _, _ = _xp_progress(xp)
    return _auto_title_for_level(level)


def _prune_warnings(chat_id: int, user_id: int) -> int:
    """اخطارهای منقضی‌شده (بعد از PRO_WARNING_VALIDITY_HOURS) رو حذف و تعداد معتبر فعلی رو برمی‌گردونه."""
    now = time.time()
    valid = [ts for ts in _warning_expiry[chat_id][user_id] if ts > now]
    _warning_expiry[chat_id][user_id] = valid
    _warnings[chat_id][user_id] = len(valid)
    return len(valid)


async def _log_admin_action(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str):
    """اگه ADMIN_LOG_CHANNEL_ID توی config.py تنظیم شده باشه، این اکشن مدیریتی رو به کانال لاگ می‌فرسته."""
    log_channel = getattr(config, "ADMIN_LOG_CHANNEL_ID", None)
    if not log_channel:
        return
    try:
        await context.bot.send_message(
            chat_id=log_channel,
            text=f"📋 چت: {chat_id}\n{text}",
        )
    except Exception as e:
        logger.warning(f"ارسال به کانال لاگ ناموفق بود: {e}")


async def _send_on_sound_effect(context: ContextTypes.DEFAULT_TYPE, chat_id: int, sound_path: str, label: str):
    """اگه فایل MP3 مربوط به sound_path وجود داشته باشه، به‌صورت Audio به chat_id می‌فرسته.
    این تابع مخصوص ساند افکت «ربات روشن» و «ترمیناتور روشن»ه و کاملاً مستقل و ایزوله‌ست:
    هیچ خطایی (فایل نبودن، خطای ارسال به تلگرام و ...) نباید باعث Fail شدن روشن‌شدن بشه،
    برای همین همه‌چیز اینجا try/except می‌شه و فقط Log می‌کنیم.
    """
    try:
        if not sound_path or not os.path.exists(sound_path):
            logger.warning(f"ساند افکت {label} پیدا نشد: {sound_path}")
            return
        with open(sound_path, "rb") as audio_file:
            await context.bot.send_audio(chat_id=chat_id, audio=audio_file)
    except Exception as e:
        logger.warning(f"ارسال ساند افکت {label} ناموفق بود: {e}")


# ---------- دستورات متنی فارسی (روشن/خاموش با نوشتن یه عبارت، بدون نیاز به دستور) ----------
# همه‌ی این محرک‌ها توی یه هندلر واحد جمع شدن. قبلاً هرکدوم هندلر جدا با فیلتر یکسان
# (هر پیام متنی غیردستوری) داشتن که توی یه گروه با هم تداخل می‌کردن؛ چون
# python-telegram-bot توی هر گروه فقط اولین هندلری که فیلترش مچ بشه رو اجرا می‌کنه،
# بقیه‌شون هیچ‌وقت اجرا نمی‌شدن. با یکی‌کردنشون توی یه تابع، این باگ رفع شد.

async def control_words_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not message.text:
        return
    text = message.text.strip()
    chat_id = update.effective_chat.id

    # ۱) سوییچ کلی ربات
    if text in (config.BOT_ON_WORD, config.BOT_OFF_WORD):
        if not has_permission(update.effective_user.id, "menu"):
            await message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
            raise ApplicationHandlerStop
        _chat_settings[chat_id]["bot_enabled"] = text == config.BOT_ON_WORD
        state = "روشن ✅" if _chat_settings[chat_id]["bot_enabled"] else "خاموش ⛔"
        await message.reply_text(f"ربات {state} شد.")
        await save_state()
        if text == config.BOT_ON_WORD:
            await _send_on_sound_effect(context, chat_id, config.BOT_ON_SOUND, "Bot ON")
        raise ApplicationHandlerStop

    # ۲) روشن/خاموش کردن سیستم دوست/دشمن (ترمیناتور)
    if text in (config.TERMINATOR_ON_WORD, config.TERMINATOR_OFF_WORD):
        if not has_permission(update.effective_user.id, "manage_relationships"):
            await message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
            raise ApplicationHandlerStop
        _chat_settings[chat_id]["terminator_enabled"] = text == config.TERMINATOR_ON_WORD
        state = "روشن ✅" if _chat_settings[chat_id]["terminator_enabled"] else "خاموش ⛔"
        await message.reply_text(f"سیستم ترمیناتور (دوست/دشمن) {state} شد.")
        await save_state()
        if text == config.TERMINATOR_ON_WORD:
            await _send_on_sound_effect(context, chat_id, config.TERMINATOR_ON_SOUND, "Terminator ON")
        raise ApplicationHandlerStop

    # ۳) روشن/خاموش کردن هر قابلیت دیگه با نوشتن «<کلیدواژه> روشن/خاموش»
    for key, keyword in config.SETTING_KEYWORDS.items():
        if text == f"{keyword} روشن" or text == f"{keyword} خاموش":
            if not has_permission(update.effective_user.id, "menu"):
                await message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
                raise ApplicationHandlerStop
            value = text.endswith("روشن")
            _chat_settings[chat_id][key] = value
            state = "روشن ✅" if value else "خاموش ⛔"
            label = config.SETTING_LABELS.get(key, key)
            await message.reply_text(f"{label} {state} شد.")
            await save_state()
            raise ApplicationHandlerStop

    # ۴) «تگ همه» - تگ دسته‌جمعی اعضای شناخته‌شده‌ی گروه
    if text == config.TAG_ALL_TRIGGER_WORD:
        chat = update.effective_chat
        if not chat or chat.type not in ("group", "supergroup"):
            return
        if not has_permission(update.effective_user.id, "tag_members"):
            await message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
            raise ApplicationHandlerStop
        if not get_setting(chat_id, "tag_members_enabled"):
            await message.reply_text(
                "قابلیت تگ دسته‌جمعی خاموشه. از /menu (بخش «🏷️ تگ دسته‌جمعی اعضا») روشنش کن."
            )
            raise ApplicationHandlerStop
        await _run_tag_all(chat_id, context, caption=None)
        raise ApplicationHandlerStop

    # ۵) پروفایل من / امتیاز من / رتبه من / سطح من (برای همه‌ی اعضا آزاده)
    if text in ("پروفایل من", "امتیاز من", "رتبه من", "سطح من", "لول من"):
        await _send_profile(update, context)
        raise ApplicationHandlerStop

    # ۶) تنظیم لقب <متن> / حذف لقب (برای همه‌ی اعضا آزاده)
    if text.startswith("تنظیم لقب "):
        await _set_custom_title(update, context, text[len("تنظیم لقب "):])
        raise ApplicationHandlerStop
    if text == "حذف لقب":
        await _set_custom_title(update, context, None)
        raise ApplicationHandlerStop

    # ۷) اقتصاد داخلی: موجودی من / روزانه / فروشگاه / آیتم های من (برای همه‌ی اعضا آزاده)
    if text in ("موجودی من", "موجودی"):
        await balance_command(update, context)
        raise ApplicationHandlerStop
    if text == "روزانه":
        await daily_command(update, context)
        raise ApplicationHandlerStop
    if text == "فروشگاه":
        await shop_command(update, context)
        raise ApplicationHandlerStop
    if text in ("آیتم های من", "آیتم‌های من"):
        await myitems_command(update, context)
        raise ApplicationHandlerStop

    # ۸) بقیه‌ی دستورات هم با کلمه‌ی فارسی کار می‌کنن (لیست کامل توی /help)
    # از طولانی‌ترین عبارت به کوتاه‌ترین امتحان می‌کنیم تا کلیدهای چندکلمه‌ای
    # (مثل «قرعه کشی» یا «تنظیم حد اخطار») درست تشخیص داده بشن.
    words = text.split()
    alias_func = None
    matched_len = 0
    for n in range(min(_ALIAS_MAX_WORDS, len(words)), 0, -1):
        phrase = " ".join(words[:n])
        if phrase in PERSIAN_COMMAND_ALIASES:
            alias_func = PERSIAN_COMMAND_ALIASES[phrase]
            matched_len = n
            break
    if alias_func:
        context.args = words[matched_len:]
        await alias_func(update, context)
        raise ApplicationHandlerStop


# ---------- ۱) پاسخ خودکار ----------

async def ask_ai(user_text: str) -> str | None:
    """پیام رو به گروک می‌فرسته و پاسخ متنی برمی‌گردونه. در صورت خطا None برمی‌گردونه.
    برای کمینه کردن تاخیر: ورودی بلند رو کوتاه می‌کنیم، سقف توکن خروجی می‌ذاریم و
    یه timeout سخت‌گیرانه روی خود کلاینت (بالای فایل) تنظیم شده."""
    if _ai_client is None:
        return None
    text = user_text.strip()
    if len(text) > config.AI_MAX_INPUT_CHARS:
        text = text[: config.AI_MAX_INPUT_CHARS]
    try:
        response = await asyncio.to_thread(
            _ai_client.chat.completions.create,
            model=config.AI_MODEL,
            messages=[
                {"role": "system", "content": config.AI_SYSTEM_INSTRUCTION},
                {"role": "user", "content": text},
            ],
            max_tokens=config.AI_MAX_TOKENS,
            temperature=config.AI_TEMPERATURE,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.warning(f"خطای AI API: {e}")
        return None


async def auto_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not message.text:
        return

    settings = _chat_settings[message.chat_id]
    if not settings.get("bot_enabled", False):
        return

    if settings.get("keyword_reply", False):
        text_lower = message.text.lower()
        for keyword, reply in config.AUTO_REPLIES.items():
            if keyword.lower() in text_lower:
                await _safe_reply(message, reply)
                return

    # تصمیم بگیر که با توجه به حالت انتخاب‌شده، به این کاربر (ادمین یا نه) AI جواب بده یا نه
    ai_mode = _ai_reply_mode[message.chat_id]
    sender_is_admin = is_admin(update.effective_user.id)
    should_ai_reply = (
        ai_mode == "all"
        or (ai_mode == "non_admins" and not sender_is_admin)
        or (ai_mode == "admins_only" and sender_is_admin)
    )

    if should_ai_reply and _ai_client is not None:
        # notice تایپینگ رو منتظرش نمی‌مونیم (fire-and-forget) تا یه رفت‌وبرگشت شبکه‌ی
        # اضافه قبل از شروع درخواست AI باعث تاخیر نشه
        asyncio.create_task(context.bot.send_chat_action(chat_id=message.chat_id, action="typing"))
        ai_response = await ask_ai(message.text)
        if ai_response:
            await _safe_reply(message, ai_response)


# ---------- ۲) مدیریت گروه ----------

def _format_duration_fa(seconds: float) -> str:
    """مدت‌زمان رو به شکل خوانای فارسی برمی‌گردونه، مثلاً «۲ روز و ۳ ساعت»."""
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} روز")
    if hours:
        parts.append(f"{hours} ساعت")
    if not days and minutes:
        parts.append(f"{minutes} دقیقه")
    if not parts:
        parts.append("کمتر از یک دقیقه")
    return " و ".join(parts)


def _get_theme(chat_id: int) -> dict:
    """تمِ فعال این گروه رو برمی‌گردونه (پیش‌فرض: digianti اگه هنوز چیزی انتخاب نشده یا
    مقدار ذخیره‌شده دیگه معتبر نباشه)."""
    theme_key = _chat_theme.get(chat_id, config.DEFAULT_MESSAGE_THEME)
    return config.MESSAGE_THEMES.get(theme_key, config.MESSAGE_THEMES[config.DEFAULT_MESSAGE_THEME])


def _welcome_leave_placeholders(user, extra: dict = None) -> dict:
    """جای‌گزین‌های مشترک پیام‌های ورود/خروج رو از روی اطلاعات واقعی کاربر می‌سازه.
    اگه یوزرنیم یا اسمی در دسترس نباشه، مقدار جایگزین امن استفاده می‌شه (پیام هیچ‌وقت خراب نمی‌شه)."""
    full_name = getattr(user, "full_name", None) or user.first_name or user.username or str(user.id)
    username_line = f"┃ 🔗 یوزرنیم: @{user.username}\n" if getattr(user, "username", None) else ""
    now = _now_local()
    data = {
        "full_name": full_name,
        "username_line": username_line,
        "user_id": user.id,
        "date": now.strftime("%Y/%m/%d"),
        "time": now.strftime("%H:%M:%S"),
        "member_count": config.MEMBER_COUNT_UNKNOWN,
        "duration_line": "",
    }
    if extra:
        data.update(extra)
    return data


async def _get_member_count_safe(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    try:
        return await context.bot.get_chat_member_count(chat_id)
    except Exception as e:
        logger.info(f"گرفتن تعداد اعضای گروه ممکن نبود (بی‌اهمیت): {e}")
        return config.MEMBER_COUNT_UNKNOWN


def _safe_format(template: str, data: dict) -> str:
    """قالب رو با داده‌ها پر می‌کنه. اگه توی یه متن سفارشی (که مالک با /welcome_text یا
    /leave_text ست کرده) یه جای‌گزین اشتباه یا ناشناخته باشه، به‌جای کرش یا رد نشدن کل
    پیام، همون متن خام برگردونده می‌شه تا پیام حتماً فرستاده بشه."""
    try:
        return template.format(**data)
    except Exception as e:
        logger.warning(f"فرمت‌کردن قالب پیام ورود/خروج ناموفق بود، متن خام فرستاده می‌شه: {e}")
        return template


async def _send_group_message_with_optional_photo(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user, text: str
):
    """اول با عکس پروفایل کاربر (اگه داشت) امتحان می‌کنه، وگرنه پیام متنی ساده می‌فرسته.
    کاملاً fail-safe: نبود عکس یا هر خطای دیگه‌ای باعث نمی‌شه خودِ پیام اصلاً نره."""
    photo_id = None
    try:
        photos = await context.bot.get_user_profile_photos(user.id, limit=1)
        if photos and photos.photos:
            photo_id = photos.photos[0][-1].file_id
    except Exception as e:
        logger.info(f"گرفتن عکس پروفایل ممکن نبود (بی‌اهمیت): {e}")

    if photo_id:
        try:
            await context.bot.send_photo(chat_id=chat_id, photo=photo_id, caption=text)
            return
        except Exception as e:
            logger.warning(f"فرستادن پیام با عکس پروفایل ناموفق بود، پیام متنی ساده می‌فرستم: {e}")

    await context.bot.send_message(chat_id=chat_id, text=text)


async def _send_welcome_message(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user, invite_link_name: str = None
):
    """پیام خوش‌آمدگویی رو می‌سازه و می‌فرسته. هم از ورود مستقیم و هم بعد از تایید موفق
    کپچا صدا زده می‌شه، تا این منطق دقیقاً یه‌جا باشه (قبلاً توی دو جا کپی شده بود).
    اگه invite_link_name داده بشه (یعنی کاربر از یه لینک دعوت اختصاصی اومده)، یه پیام
    مخصوصِ همون حالت فرستاده می‌شه - مگر اینکه ادمین متن سفارشی ست کرده باشه."""
    member_count = await _get_member_count_safe(context, chat_id)
    theme = _get_theme(chat_id)
    data = _welcome_leave_placeholders(
        user, {"member_count": member_count, "invite_link_name": invite_link_name or ""}
    )
    custom = _custom_welcome_text.get(chat_id)
    if custom:
        template = custom
    elif invite_link_name:
        template = theme["welcome_invite_link"]
    else:
        template = random.choice(theme["welcome"])
    text = _safe_format(template, data)
    try:
        await _send_group_message_with_optional_photo(context, chat_id, user, text)
    except Exception as e:
        logger.warning(f"فرستادن پیام خوش‌آمدگویی ناموفق بود: {e}")


async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if result is None:
        return
    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    became_member = old_status in (
        ChatMemberStatus.LEFT,
        ChatMemberStatus.BANNED,
    ) and new_status == ChatMemberStatus.MEMBER

    if not became_member:
        return

    user = result.new_chat_member.user
    chat_id = result.chat.id
    # اگه قبلاً هم عضو بوده و دوباره اومده، این یه Join تازه حساب می‌شه (زمان قبلی بازنویسی می‌شه)
    _join_times[chat_id][user.id] = time.time()
    if not user.is_bot:
        _known_members[chat_id].add(user.id)
        _user_display_names[user.id] = user.first_name or user.username or str(user.id)
    settings = _chat_settings[chat_id]

    if not settings.get("bot_enabled", False):
        return

    # قفل ربات: هر ربات دیگه‌ای که اضافه بشه، بلافاصله اخراج می‌شه (مگر توی Allowlist باشه)
    if user.is_bot and settings.get("lock_bots", False) and user.id not in lock_engine._bot_allowlist[chat_id]:
        try:
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user.id)
            await context.bot.unban_chat_member(chat_id=chat_id, user_id=user.id, only_if_banned=True)
        except Exception as e:
            logger.warning(f"اخراج ربات ناموفق بود: {e}")
        return

    # ربات دیگه‌ای اضافه شد ولی قفل ربات خاموشه: یه پیام مخصوص ربات (نه خوش‌آمد شخصی انسانی)
    if user.is_bot:
        _join_leave_stats[chat_id]["bot_joins"] += 1
        if settings.get("welcome_enabled", False):
            data = _welcome_leave_placeholders(user)
            text = _safe_format(_get_theme(chat_id)["bot_join"], data)
            try:
                await context.bot.send_message(chat_id=chat_id, text=text)
            except Exception as e:
                logger.warning(f"فرستادن پیام ورود ربات ناموفق بود: {e}")
        await _log_admin_action(context, chat_id, f"🤖 ورود ربات: {user.first_name or user.id} (آیدی {user.id})")
        return

    name = user.first_name or user.username or "دوست عزیز"

    invite_link = getattr(result, "invite_link", None)
    invite_link_name = None
    if invite_link is not None:
        invite_link_name = getattr(invite_link, "name", None) or getattr(invite_link, "invite_link", None)

    if settings.get("captcha_enabled", False):
        started = await spam_engine.start_captcha(context, chat_id, user, invite_link_name)
        if started:
            return  # پیام خوش‌آمدگویی بعد از تایید موفق چالش کپچا فرستاده می‌شه
        # started == False یعنی کاربر مستثنی بود (Owner/Admin/VIP/Friend) - خوش‌آمد عادی ادامه پیدا می‌کنه

    if settings.get("welcome_enabled", False):
        await _send_welcome_message(context, chat_id, user, invite_link_name)

    _join_leave_stats[chat_id]["joins"] += 1
    link_note = f" | از طریق لینک دعوت: {invite_link_name}" if invite_link_name else ""
    await _log_admin_action(context, chat_id, f"👋 ورود: {name} (آیدی {user.id}){link_note}")


async def member_left_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """وقتی عضوی از گروه می‌ره (خودش لفت می‌ده، توسط ادمین اخراج/بن می‌شه، یا آن‌بن می‌شه) رسیدگی می‌کنه.
    (تاریخ/ساعت با تایم‌زون config.TIMEZONE نمایش داده می‌شه)"""
    result = update.chat_member
    if result is None:
        return
    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    user = result.new_chat_member.user
    chat_id = result.chat.id
    settings = _chat_settings[chat_id]

    # آن‌بن‌شدن (از بن درومده، ولی هنوز دوباره عضو نشده) - این «خروج» واقعی نیست، فقط آمارش رو نگه می‌داریم.
    # نکته‌ی فنی: چون «اخراج» توی Bot API با ban و بلافاصله unban پیاده می‌شه، معمولاً بعد از
    # هر اخراج یه رویداد آن‌بن هم می‌رسه؛ به همین خاطر این حالت رو ساکت (بدون پیام/لاگ) نگه می‌داریم.
    if old_status == ChatMemberStatus.BANNED and new_status == ChatMemberStatus.LEFT:
        _join_leave_stats[chat_id]["unbans"] += 1
        return

    left_group = old_status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.RESTRICTED,
    ) and new_status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED)

    if not left_group:
        return

    if user.is_bot or not settings.get("bot_enabled", False):
        return

    # جلوگیری از پیام دوتایی (مثلاً وقتی خودِ اخراج، دو رویداد پشت‌سرهم برای یه کاربر تولید می‌کنه)
    now_ts = time.time()
    last_announced = _recent_leave_announcement[chat_id].get(user.id, 0)
    if now_ts - last_announced < _LEAVE_DEDUP_SECONDS:
        return
    _recent_leave_announcement[chat_id][user.id] = now_ts

    # تشخیص خروج خودخواسته در برابر اخراج/بن توسط ادمین:
    # - new_status == BANNED یعنی قطعاً بن/اخراج شده.
    # - اگه از‌طرف یه نفر دیگه (نه خودِ کاربر) اتفاق افتاده باشه هم اخراج حساب می‌شه.
    actor = getattr(result, "from_user", None)
    is_kicked = new_status == ChatMemberStatus.BANNED or (actor is not None and actor.id != user.id)

    join_ts = _join_times[chat_id].pop(user.id, None)
    duration_line = ""
    if join_ts:
        duration_line = f"┃ ⌛ مدت حضور: {_format_duration_fa(now_ts - join_ts)}\n"

    member_count = await _get_member_count_safe(context, chat_id)
    data = _welcome_leave_placeholders(user, {"member_count": member_count, "duration_line": duration_line})

    if is_kicked:
        _join_leave_stats[chat_id]["kicks"] += 1
        template = random.choice(_get_theme(chat_id)["leave_kicked"])
        event_label = "👢 اخراج/بن"
    else:
        _join_leave_stats[chat_id]["leaves"] += 1
        template = _custom_leave_text.get(chat_id) or random.choice(_get_theme(chat_id)["leave_self"])
        event_label = "🚪 خروج خودخواسته"

    if settings.get("leave_enabled", False):
        text = _safe_format(template, data)
        try:
            await context.bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            logger.warning(f"ارسال پیام ترک گروه ناموفق بود: {e}")

    await _log_admin_action(context, chat_id, f"{event_label}: {data['full_name']} (آیدی {user.id})")


async def _check_captcha_timeouts():
    """ارتقایافته به Smart CAPTCHA 2.0: اکشن شکست حالا per-chat قابل‌تنظیمه
    (mute/kick/ban به‌جای اخراج ثابت قبلی)، با تعداد تلاش محدود و State Persistent."""
    await spam_engine.check_captcha_timeouts(_bot_instance)


# ---------- ضد رید (حمله‌ی عضوگیری ناگهانی) ----------

async def _raid_watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """🚨 Raid Detection 2.0: تشخیص حالا بر اساس Risk Score (تعداد Join + شباهت
    یوزرنیم‌ها + فلود بلافاصله بعد از Join) انجام می‌شه، نه فقط شمارش ساده
    (جزئیات کامل و Auto Raid Mode در spam_engine.py). رفتار «محدودسازی عضو جدید
    حین حالت رید» از نسخه‌ی قبلی حفظ شده."""
    result = update.chat_member
    if result is None:
        return
    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    became_member = old_status in (
        ChatMemberStatus.LEFT,
        ChatMemberStatus.BANNED,
    ) and new_status == ChatMemberStatus.MEMBER
    if not became_member:
        return

    chat_id = result.chat.id
    settings = _chat_settings[chat_id]
    if not settings.get("bot_enabled", False):
        return

    user = result.new_chat_member.user
    already_raid = spam_engine._raid_state[chat_id]["until"] > time.time()
    await spam_engine.record_join(chat_id, user.id, user.username, context)

    if already_raid and getattr(config, "RAID_RESTRICT_NEW_JOINS", True) and not user.is_bot:
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user.id,
                permissions=ChatPermissions(can_send_messages=False),
            )
        except Exception as e:
            logger.warning(f"محدودسازی عضو در حالت رید ناموفق بود: {e}")


async def _check_raid_expiry():
    """بعد از تموم‌شدن مدت حالت رید، تمام تنظیمات (کپچا/ضدتبلیغ/محافظت عضو/قفل لینک)
    دقیقاً به حالت قبل برمی‌گردن (جزئیات در spam_engine.check_raid_expiry)."""
    await spam_engine.check_raid_expiry(_bot_instance)


async def _check_enemy_expiry():
    """دشمنی‌های موقتی (/addenemy 24h یا «دشمن 24h») که زمانشون تموم شده رو خودکار لغو می‌کنه."""
    now = time.time()
    for chat_id, inner in list(_enemy_expiry.items()):
        for user_id, until in list(inner.items()):
            if until and until <= now:
                _enemies[chat_id].discard(user_id)
                inner.pop(user_id, None)
                if _bot_instance:
                    name = _user_display_names.get(user_id, str(user_id))
                    try:
                        await _bot_instance.send_message(
                            chat_id=chat_id, text=f"⏱ دشمنی موقتِ {name} تموم شد و خودکار لغو شد."
                        )
                    except Exception:
                        pass


async def _activity_cleanup_retention():
    """آمار روزانه‌ی خام قدیمی‌تر از ACTIVITY_HISTORY_RETENTION_DAYS رو پاک می‌کنه (Data Retention).
    آمار کلی/تجمیعی (XP، Activity Score، Streak، تعداد پیام‌ها و...) دست‌نخورده می‌مونه - فقط
    ریزداده‌ی روزانه که برای لیدربرد امروز/هفته/ماه لازمه پاک می‌شه، نه کل تاریخچه‌ی کاربر."""
    try:
        cutoff = (_now_local() - datetime.timedelta(days=config.ACTIVITY_HISTORY_RETENTION_DAYS)).strftime("%Y-%m-%d")
        removed = 0
        for chat_id, inner in _daily_activity.items():
            for uid, days in inner.items():
                for d in [d for d in days if d < cutoff]:
                    del days[d]
                    removed += 1
        if removed:
            logger.info(f"پاکسازی دوره‌ای Activity History: {removed} رکورد روزانه‌ی قدیمی حذف شد.")
    except Exception as e:
        logger.warning(f"پاکسازی Activity History ناموفق بود (نادیده گرفته شد): {e}")


def _is_spam(text: str) -> bool:
    lowered = text.lower()
    if any(word.lower() in lowered for word in config.BANNED_WORDS):
        return True
    if config.BLOCK_LINKS and LINK_PATTERN.search(text):
        whitelist = getattr(config, "LINK_WHITELIST_DOMAINS", [])
        if whitelist and any(domain.lower() in lowered for domain in whitelist):
            return False
        return True
    return False


def _is_flooding(chat_id: int, user_id: int) -> bool:
    now = time.time()
    log = _message_log[chat_id][user_id]
    log.append(now)
    while log and now - log[0] > config.FLOOD_LIMIT_SECONDS:
        log.popleft()
    return len(log) > config.FLOOD_LIMIT_MESSAGES


def _is_media_flooding(chat_id: int, user_id: int) -> bool:
    now = time.time()
    log = _media_log[chat_id][user_id]
    log.append(now)
    while log and now - log[0] > config.MEDIA_FLOOD_LIMIT_SECONDS:
        log.popleft()
    return len(log) > config.MEDIA_FLOOD_LIMIT_MESSAGES


def _is_caps_spam(text: str) -> bool:
    if len(text) < config.CAPS_MIN_LENGTH:
        return False
    letters = [c for c in text if c.isalpha()]
    if len(letters) < config.CAPS_MIN_LENGTH:
        return False
    upper_count = sum(1 for c in letters if c.isupper())
    return (upper_count / len(letters)) >= config.CAPS_RATIO_THRESHOLD


def _is_forwarded_from_channel(message) -> bool:
    if MessageOriginChannel is not None and isinstance(
        getattr(message, "forward_origin", None), MessageOriginChannel
    ):
        return True
    # سازگاری با نسخه‌های قدیمی‌تر کتابخونه
    forward_chat = getattr(message, "forward_from_chat", None)
    return bool(forward_chat and getattr(forward_chat, "type", None) == "channel")


def _is_new_member_link_spam(chat_id: int, user_id: int, text: str) -> bool:
    join_time = _join_times[chat_id].get(user_id)
    if join_time is None:
        return False
    if time.time() - join_time > config.NEW_MEMBER_GRACE_MINUTES * 60:
        return False
    return bool(LINK_PATTERN.search(text))


async def _warn_and_maybe_mute(
    chat_id: int, target_id: int, display_name: str, context: ContextTypes.DEFAULT_TYPE, reason: str,
    *, admin_id: int | None = None, admin_name: str | None = None, source: str = "MANUAL",
):
    """نقطه‌ی واحد اخطار: هم دستور دستی /warn، هم Spam Engine و هم Lock Engine از همین
    تابع استفاده می‌کنن (host._warn_and_maybe_mute). از نسخه‌ی قبلی (که فقط یک بار Mute
    می‌کرد) به موتور مدیریت حرفه‌ای (نردبان تصاعدی Warn -> Mute -> Ban، قابل‌تنظیم از
    config.MODERATION_ESCALATION_LADDER) وصل شده؛ رفتار قدیمی (اخطار قدیمی دستی) دست‌نخورده
    باقی می‌مونه چون moderation_engine شمارنده‌های قدیمی (_warnings, _mute_counts) رو هم
    هم‌زمان به‌روز نگه می‌داره."""
    validity_hours = getattr(config, "PRO_WARNING_VALIDITY_HOURS", 24)
    _warning_expiry[chat_id][target_id].append(time.time() + validity_hours * 3600)
    _last_warning_reason[chat_id][target_id] = reason
    await moderation_engine.warn(
        context, chat_id, target_id, display_name,
        reason=reason, admin_id=admin_id, admin_name=admin_name, source=source,
    )
    await save_state()


async def moderate_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or chat.type not in ("group", "supergroup"):
        return

    _known_chats.add(chat.id)

    settings = _chat_settings[chat.id]
    if not settings.get("bot_enabled", False):
        return

    # ---------- 🔐 DIGIANTI SMART LOCK ENGINE 2.0 ----------
    # موتور مرکزی قفل: لینک هوشمند، عکس/ویدیو/فایل/صدا/ویس/گیف/استیکر (کامل)،
    # فوروارد (عادی + کانال)، مخاطب، موقعیت، منشن/تگ، هشتگ، نظرسنجی/بازی، متن/پیام
    # طولانی، اینلاین، ربات دیگر، بستن گروه، محافظت عضو تازه‌وارد، Exception/Whitelist،
    # Action Engine + Escalation، کول‌داون/ضدتکرار، قفل موقت/زمان‌بندی‌شده، آمار و لاگ.
    # (جزئیات کامل در lock_engine.py)
    if await lock_engine.process_message(update, context):
        raise ApplicationHandlerStop

    # ---------- 🛡️ DIGIANTI SMART ANTI-SPAM & ANTI-BOT ENGINE 3.0 ----------
    # Spam Profile per کاربر، Flood هر نوع محتوا، Duplicate Spam، لینک/تبلیغ/منشن/
    # هشتگ/ایموجی هوشمند، محافظت عضو جدید، Escalation، و Reuse دقیق از
    # antispam_words/anticaps/antiflood_text قبلی (بدون Duplicate). جزئیات کامل
    # در spam_engine.py.
    if await spam_engine.process_message(update, context):
        raise ApplicationHandlerStop

    # ادمین‌ها و کاربرای ویژه (VIP) رو مستثنا می‌کنیم (برای بقیه‌ی چک‌های قدیمی‌تر زیر)
    if is_admin(user.id) or user.id in _vip_users[chat.id]:
        return

    # بلاک‌لیست دستی - همیشه فعاله، مستقل از بقیه‌ی تنظیمات (البته وقتی ربات کلاً روشنه)
    if user.id in _blacklist[chat.id]:
        try:
            await message.delete()
        except Exception as e:
            logger.warning(f"حذف پیام کاربر بلاک‌شده ناموفق بود: {e}")
        raise ApplicationHandlerStop

    # ضدفلود استیکر/گیف (فقط اسپم پشت‌سرهم؛ قفل کامل استیکر/گیف الان توسط Lock Engine بالاتر مدیریت می‌شه)
    if message.sticker:
        if settings.get("antisticker_flood", False) and _is_media_flooding(chat.id, user.id):
            try:
                await message.delete()
                logger.info(f"استیکر فلود حذف شد از کاربر {user.id} در چت {chat.id}")
            except Exception as e:
                logger.warning(f"حذف پیام ناموفق بود: {e}")
            await _warn_and_maybe_mute(chat.id, user.id, user.first_name or user.username or "کاربر", context, "ارسال استیکر پشت سر هم")
            raise ApplicationHandlerStop
        return

    if message.animation:
        if settings.get("antisticker_flood", False) and _is_media_flooding(chat.id, user.id):
            try:
                await message.delete()
                logger.info(f"گیف فلود حذف شد از کاربر {user.id} در چت {chat.id}")
            except Exception as e:
                logger.warning(f"حذف پیام ناموفق بود: {e}")
            await _warn_and_maybe_mute(chat.id, user.id, user.first_name or user.username or "کاربر", context, "ارسال گیف پشت سر هم")
            raise ApplicationHandlerStop
        return

    # به این نقطه فقط برای متن‌های خیلی معمولی می‌رسیم؛ لینک/کلمات‌ممنوعه/کاپس‌لاک/فلود
    # متن الان همه توسط Anti-Spam Engine 3.0 (بالاتر، قبل از این تابع) با Reuse دقیق از
    # _is_spam/_is_caps_spam/_is_flooding مدیریت می‌شن - این‌جا دیگه کاری نمونده.


async def edited_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قفل ویرایش پیام (ارتقایافته توسط Lock Engine 2.0، بخش #22) + Edit Spam
    (Anti-Spam Engine 3.0، بخش #28: کاربر نتونه با ادیت‌کردن دور بزنه).
    پیش‌فرض دقیقاً مثل قبل (delete_any: هر ویرایشی حذف می‌شه)؛ با
    /lock edited mode smart می‌شه به «فقط اگه محتوای جدید یکی از قفل‌های فعال
    رو نقض کنه حذف شو» تغییرش داد."""
    handled = await lock_engine.process_edited_message(update, context)
    if handled:
        return
    await spam_engine.process_edited_message(update, context)


# ---------- ۴) سیستم دوست/دشمن (ترمیناتور) ----------

_DURATION_TOKEN_RE = re.compile(r"^(\d+)\s*(m|h|d)$", re.IGNORECASE)


def _parse_duration_token(token: str):
    """چیزی مثل 24h / 2d / 30m رو به ثانیه تبدیل می‌کنه. اگه فرمتش درست نباشه None برمی‌گردونه."""
    if not token:
        return None
    m = _DURATION_TOKEN_RE.match(token.strip())
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2).lower()
    mult = {"m": 60, "h": 3600, "d": 86400}[unit]
    return value * mult


def _format_duration_short(seconds: int) -> str:
    if seconds >= 86400 and seconds % 86400 == 0:
        return f"{seconds // 86400} روز"
    if seconds >= 3600:
        return f"{seconds // 3600} ساعت"
    return f"{max(1, seconds // 60)} دقیقه"


def _match_trigger_with_duration(text: str, words: set):
    """چک می‌کنه متن دقیقاً یکی از کلمه‌های trigger هست یا نه؛ اگه بعدش یه توکن مدت‌زمان
    (مثل 24h) هم اومده باشه (مثلاً «دشمن 24h»)، اون رو هم به ثانیه برمی‌گردونه."""
    if text in words:
        return True, None
    for w in words:
        prefix = w + " "
        if text.startswith(prefix):
            duration = _parse_duration_token(text[len(prefix):])
            if duration is not None:
                return True, duration
    return False, None


async def friend_enemy_control_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """با ریپلای زدن کلمات کلیدی روی پیام یه کاربر، اونو دوست/دشمن می‌کنه.
    (روشن/خاموش کردن ترمیناتور جدا شده و توی control_words_handler مدیریت می‌شه.)"""
    message = update.effective_message
    chat = update.effective_chat
    if not message or not message.text:
        return

    if not _chat_settings[chat.id].get("bot_enabled", False):
        return

    text = message.text.strip()

    # همه‌ی دستورات این هندلر نیاز به ریپلای روی پیام یه کاربر دارن
    if not message.reply_to_message or not message.reply_to_message.from_user:
        return

    enemy_words = _enemy_trigger_words[chat.id]
    friend_words = _friend_trigger_words[chat.id]
    is_enemy_trigger, enemy_duration = _match_trigger_with_duration(text, enemy_words)
    is_friend_trigger = text in friend_words
    is_enemy_remove = text == config.ENEMY_REMOVE_WORD
    is_friend_remove = text == config.FRIEND_REMOVE_WORD

    if not (is_enemy_trigger or is_friend_trigger or is_enemy_remove or is_friend_remove):
        return

    # علامت‌گذاری دوست/دشمن فقط برای ادمین‌های مجاز
    if not has_permission(update.effective_user.id, "manage_relationships"):
        await message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        raise ApplicationHandlerStop

    target = message.reply_to_message.from_user
    if target.id == update.effective_user.id:
        return  # نمی‌تونه خودشو دشمن/دوست کنه

    if is_enemy_trigger:
        _enemies[chat.id].add(target.id)
        _friends[chat.id].discard(target.id)
        name = target.first_name or target.username or "این کاربر"
        expiry_note = ""
        if enemy_duration:
            _enemy_expiry[chat.id][target.id] = time.time() + enemy_duration
            expiry_note = f" (موقت، {_format_duration_short(enemy_duration)} دیگه خودکار لغو می‌شه)"
        else:
            _enemy_expiry[chat.id].pop(target.id, None)
        await message.reply_text(f"⚔️ {name} از این به بعد دشمن اعلام شد.{expiry_note}")
        await save_state()
        raise ApplicationHandlerStop

    if is_enemy_remove:
        _enemies[chat.id].discard(target.id)
        _enemy_expiry[chat.id].pop(target.id, None)
        await message.reply_text("دشمنی لغو شد.")
        await save_state()
        raise ApplicationHandlerStop

    if is_friend_trigger:
        _friends[chat.id].add(target.id)
        _enemies[chat.id].discard(target.id)
        name = target.first_name or target.username or "این کاربر"
        await message.reply_text(f"🤝 {name} از این به بعد دوست اعلام شد.")
        await save_state()
        raise ApplicationHandlerStop

    if is_friend_remove:
        _friends[chat.id].discard(target.id)
        await message.reply_text("دوستی لغو شد.")
        await save_state()
        raise ApplicationHandlerStop


def _parse_target_from_command(update: Update):
    """آیدی هدف رو یا از ریپلای می‌گیره یا از آرگومان دستور (/addenemy 12345)."""
    message = update.effective_message
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        return u.id, (u.first_name or u.username or str(u.id))
    if update.message and update.message.text:
        parts = update.message.text.split()
        if len(parts) >= 2 and parts[1].isdigit():
            return int(parts[1]), parts[1]
    return None, None


async def enemy_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "manage_relationships"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    target_id, name = _parse_target_from_command(update)
    if target_id is None:
        await update.effective_message.reply_text(
            "استفاده: ریپلای رو پیام کاربر بزن، یا: /addenemy <آیدی عددی>\n"
            "برای دشمنی موقت: ریپلای + /addenemy 24h  (یا: /addenemy <آیدی> 24h) - واحدها: m/h/d"
        )
        return
    _enemies[chat_id].add(target_id)
    _friends[chat_id].discard(target_id)
    parts = (update.message.text or "").split() if update.message else []
    duration = _parse_duration_token(parts[-1]) if parts else None
    expiry_note = ""
    if duration:
        _enemy_expiry[chat_id][target_id] = time.time() + duration
        expiry_note = f" (موقت، {_format_duration_short(duration)} دیگه خودکار لغو می‌شه)"
    else:
        _enemy_expiry[chat_id].pop(target_id, None)
    await update.effective_message.reply_text(f"⚔️ {name} به لیست دشمن‌ها اضافه شد.{expiry_note}")
    await save_state()


async def friend_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "manage_relationships"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    target_id, name = _parse_target_from_command(update)
    if target_id is None:
        await update.effective_message.reply_text(
            "استفاده: ریپلای رو پیام کاربر بزن، یا: /addfriend <آیدی عددی>"
        )
        return
    _friends[chat_id].add(target_id)
    _enemies[chat_id].discard(target_id)
    await update.effective_message.reply_text(f"🤝 {name} به لیست دوست‌ها اضافه شد.")
    await save_state()


async def enemy_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "manage_relationships"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    target_id, name = _parse_target_from_command(update)
    if target_id is None:
        await update.effective_message.reply_text(
            "استفاده: ریپلای رو پیام کاربر بزن، یا: /removeenemy <آیدی عددی>"
        )
        return
    _enemies[chat_id].discard(target_id)
    _enemy_expiry[chat_id].pop(target_id, None)
    await update.effective_message.reply_text(f"{name} از لیست دشمن‌ها حذف شد.")
    await save_state()


async def friend_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "manage_relationships"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    target_id, name = _parse_target_from_command(update)
    if target_id is None:
        await update.effective_message.reply_text(
            "استفاده: ریپلای رو پیام کاربر بزن، یا: /removefriend <آیدی عددی>"
        )
        return
    _friends[chat_id].discard(target_id)
    await update.effective_message.reply_text(f"{name} از لیست دوست‌ها حذف شد.")
    await save_state()


async def enemies_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ids = _enemies[chat_id]
    if not ids:
        await update.effective_message.reply_text("لیست دشمن‌ها خالیه.")
        return
    names = [_user_display_names.get(uid, str(uid)) for uid in ids]
    await update.effective_message.reply_text("⚔️ دشمن‌ها:\n" + "\n".join(names))


async def friends_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ids = _friends[chat_id]
    if not ids:
        await update.effective_message.reply_text("لیست دوست‌ها خالیه.")
        return
    lines = []
    for uid in ids:
        name = _user_display_names.get(uid, str(uid))
        profile = _friend_profiles[chat_id].get(uid)
        if profile:
            lines.append(f"{name} — {profile['message_count']} پیام")
        else:
            lines.append(name)
    await update.effective_message.reply_text(
        "🤝 دوست‌ها:\n" + "\n".join(lines) + "\n\n(برای جزئیات بیشتر: ریپلای + /friendinfo)"
    )


def _terminator_build_profile_text(chat_id: int, target_id: int) -> str:
    profile = _terminator_targets[chat_id].get(target_id)
    name = _user_display_names.get(target_id, str(target_id))
    is_enemy = target_id in _enemies[chat_id]
    if profile is None:
        status = "⚔️ دشمنه ولی هنوز پیامی ازش ثبت نشده." if is_enemy else "توی لیست دشمن‌ها نیست."
        return f"☠️ پروفایل ترمیناتور\n\n👤 {name}\n🆔 {target_id}\n\n{status}"

    level = _terminator_threat_level(profile["threat_score"])
    level_label = config.TERMINATOR_LEVEL_LABELS.get(level, str(level))
    mode = profile.get("response_mode") or f"{_terminator_default_mode[chat_id]} (پیش‌فرض گروه)"
    mode_label = config.TERMINATOR_MODE_LABELS.get(profile.get("response_mode") or _terminator_default_mode[chat_id], mode)
    cooldown = profile.get("cooldown_seconds") or config.TERMINATOR_COOLDOWN_SECONDS
    added = time.strftime("%Y/%m/%d", time.localtime(profile["added_date"]))
    expiry = _enemy_expiry[chat_id].get(target_id)
    expiry_line = f"⏱ انقضا: {_format_duration_short(max(0, int(expiry - time.time())))} دیگه\n" if expiry else "⏱ انقضا: دائمی\n"
    boss_line = "\n☠️ وضعیت: BOSS TARGET\n" if profile.get("is_boss") else ""

    return (
        "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "𓆩 ☠️ 𝐓𝐀𝐑𝐆𝐄𝐓 𝐏𝐑𝐎𝐅𝐈𝐋𝐄 𓆪\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"👤 {profile['name']}\n"
        f"🔗 {'@' + profile['username'] if profile.get('username') else '—'}\n"
        f"🆔 {target_id}\n"
        f"📅 اضافه شده: {added}\n"
        f"💬 پیام‌های شناسایی‌شده: {profile['message_count']}\n"
        f"⚡ پاسخ‌های ارسالی: {profile['response_count']}\n"
        f"🔥 Threat: {level_label} (امتیاز {profile['threat_score']})\n"
        f"🎭 مد پاسخ: {mode_label}\n"
        f"⏱ کول‌داون: {cooldown} ثانیه\n"
        f"{expiry_line}"
        f"{boss_line}\n"
        "    𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪"
    )


async def enemy_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/enemyinfo (با ریپلای روی پیام یه دشمن) - پروفایل کامل Target رو نشون می‌ده."""
    message = update.effective_message
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply_text("ریپلای رو پیام دشمنی که می‌خوای اطلاعاتش رو ببینی بزن.")
        return
    target = message.reply_to_message.from_user
    await message.reply_text(_terminator_build_profile_text(update.effective_chat.id, target.id))


def _friend_build_profile_text(chat_id: int, friend_id: int) -> str:
    profile = _friend_profiles[chat_id].get(friend_id)
    name = _user_display_names.get(friend_id, str(friend_id))
    is_friend = friend_id in _friends[chat_id]
    if profile is None:
        status = "🤝 دوسته ولی هنوز پیامی ازش ثبت نشده." if is_friend else "توی لیست دوست‌ها نیست."
        return f"🤝 پروفایل دوست\n\n👤 {name}\n🆔 {friend_id}\n\n{status}"

    added = time.strftime("%Y/%m/%d", time.localtime(profile["added_date"]))
    last_seen = (
        time.strftime("%Y/%m/%d %H:%M", time.localtime(profile["last_message_time"]))
        if profile["last_message_time"]
        else "—"
    )
    return (
        "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "𓆩 🤝 𝐅𝐑𝐈𝐄𝐍𝐃 𝐏𝐑𝐎𝐅𝐈𝐋𝐄 𓆪\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"👤 {profile['name']}\n"
        f"🔗 {'@' + profile['username'] if profile.get('username') else '—'}\n"
        f"🆔 {friend_id}\n"
        f"📅 دوست شده از: {added}\n"
        f"💬 پیام‌های ثبت‌شده: {profile['message_count']}\n"
        f"💚 پاسخ‌های گرم ارسالی: {profile['response_count']}\n"
        f"🕐 آخرین پیام: {last_seen}\n\n"
        "    𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪"
    )


async def friend_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/friendinfo (با ریپلای روی پیام یه دوست) - پروفایل کامل اون دوست رو نشون می‌ده."""
    message = update.effective_message
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply_text("ریپلای رو پیام دوستی که می‌خوای اطلاعاتش رو ببینی بزن.")
        return
    target = message.reply_to_message.from_user
    await message.reply_text(_friend_build_profile_text(update.effective_chat.id, target.id))


async def target_mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "manage_relationships"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    message = update.effective_message
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply_text("ریپلای رو پیام دشمنی که می‌خوای مد پاسخش رو عوض کنی بزن.")
        return
    args = context.args or []
    if not args or args[0].lower() not in config.TERMINATOR_RESPONSE_MODES:
        await message.reply_text(
            "استفاده: ریپلای + /targetmode <مد>\nمدهای معتبر: " + ", ".join(config.TERMINATOR_RESPONSE_MODES)
        )
        return
    target = message.reply_to_message.from_user
    profile = _terminator_get_target(update.effective_chat.id, target)
    profile["response_mode"] = args[0].lower()
    await message.reply_text(
        f"✅ مد پاسخ ترمیناتور برای {profile['name']} شد: {config.TERMINATOR_MODE_LABELS[args[0].lower()]}"
    )
    await save_state()


async def target_cooldown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "manage_relationships"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    message = update.effective_message
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply_text("ریپلای رو پیام دشمنی که می‌خوای کول‌داونش رو عوض کنی بزن.")
        return
    args = context.args or []
    if not args or not args[0].isdigit():
        await message.reply_text("استفاده: ریپلای + /targetcooldown <ثانیه>")
        return
    target = message.reply_to_message.from_user
    profile = _terminator_get_target(update.effective_chat.id, target)
    profile["cooldown_seconds"] = max(1, int(args[0]))
    await message.reply_text(f"✅ کول‌داون ترمیناتور برای {profile['name']} شد: {profile['cooldown_seconds']} ثانیه")
    await save_state()


def _terminator_stats_text(chat_id: int) -> str:
    targets = _terminator_targets[chat_id]
    total_messages = sum(p["message_count"] for p in targets.values())
    total_responses = sum(p["response_count"] for p in targets.values())
    active_targets = sum(1 for uid in targets if uid in _enemies[chat_id])
    extreme_targets = sum(
        1 for uid, p in targets.items() if uid in _enemies[chat_id] and _terminator_threat_level(p["threat_score"]) >= 4
    )
    most_targeted_line = "—"
    if targets:
        top_id, top_profile = max(targets.items(), key=lambda kv: kv[1]["message_count"])
        if top_profile["message_count"] > 0:
            most_targeted_line = top_profile["name"]

    mode_state = "🟢 فعال (Active)" if _terminator_mode[chat_id] == "active" else "🔇 ساکت (Silent)"

    return (
        "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "☠️ 𝐓𝐄𝐑𝐌𝐈𝐍𝐀𝐓𝐎𝐑\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"⚙️ وضعیت: {mode_state}\n\n"
        f"🎯 Targets: {len(_enemies[chat_id])}\n"
        f"💬 Messages Detected: {total_messages}\n"
        f"⚡ Responses Sent: {total_responses}\n\n"
        f"🔥 Active Targets: {active_targets}\n"
        f"☠️ Extreme Targets: {extreme_targets}\n\n"
        f"🏆 Most Targeted:\n{most_targeted_line}\n\n"
        "    𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪"
    )


async def terminator_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/terminator active | silent | stats | mode <mد> - کنترل اصلی سیستم ترمیناتور.
    (روشن/خاموش کلی سیستم جدا از این دستوره: «ترمیناتور روشن/خاموش»)"""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    sub = args[0].lower() if args else ""

    if sub == "stats":
        await update.effective_message.reply_text(_terminator_stats_text(chat.id))
        return

    if sub in ("active", "silent"):
        if not has_permission(update.effective_user.id, "manage_relationships"):
            await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
            return
        _terminator_mode[chat.id] = sub
        label = "فعال 🟢 (پاسخ می‌ده)" if sub == "active" else "ساکت 🔇 (فقط تشخیص/آمار، بدون پاسخ)"
        await update.effective_message.reply_text(f"✅ حالت ترمیناتور شد: {label}")
        await save_state()
        return

    if sub == "mode":
        if not has_permission(update.effective_user.id, "manage_relationships"):
            await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
            return
        if len(args) < 2 or args[1].lower() not in config.TERMINATOR_RESPONSE_MODES:
            await update.effective_message.reply_text(
                "استفاده: /terminator mode <مد>\nمدهای معتبر: " + ", ".join(config.TERMINATOR_RESPONSE_MODES)
            )
            return
        _terminator_default_mode[chat.id] = args[1].lower()
        await update.effective_message.reply_text(
            f"✅ مد پیش‌فرض پاسخ‌های ترمیناتور این گروه شد: {config.TERMINATOR_MODE_LABELS[args[1].lower()]}"
        )
        await save_state()
        return

    current_mode = "فعال 🟢" if _terminator_mode[chat.id] == "active" else "ساکت 🔇"
    default_mode_label = config.TERMINATOR_MODE_LABELS.get(_terminator_default_mode[chat.id], _terminator_default_mode[chat.id])
    await update.effective_message.reply_text(
        "☠️ راهنمای دستور /terminator:\n\n"
        f"وضعیت فعلی: {current_mode} | مد پیش‌فرض: {default_mode_label}\n\n"
        "/terminator active — پاسخ‌دهی رو روشن کن\n"
        "/terminator silent — فقط تشخیص/آمار، بدون پاسخ\n"
        "/terminator stats — آمار کامل ترمیناتور\n"
        "/terminator mode <مد> — مد پیش‌فرض پاسخ‌های گروه رو عوض کن\n"
        "(برای هر Target جدا: ریپلای + /targetmode یا /targetcooldown)\n\n"
        "کلیدهای مد معتبر: " + ", ".join(config.TERMINATOR_RESPONSE_MODES)
    )


def _build_relations_text(chat_id: int) -> str:
    """متن یکجای لیست دوست‌ها و دشمن‌های یه گروه رو می‌سازه."""
    enemy_ids = _enemies[chat_id]
    friend_ids = _friends[chat_id]
    parts = ["⚔️🤝 لیست دوست‌ها و دشمن‌ها:\n"]
    parts.append(f"🤝 دوست‌ها ({len(friend_ids)}):")
    if friend_ids:
        parts.append("\n".join(f"• {_user_display_names.get(uid, str(uid))}" for uid in friend_ids))
    else:
        parts.append("خالیه.")
    parts.append(f"\n⚔️ دشمن‌ها ({len(enemy_ids)}):")
    if enemy_ids:
        parts.append("\n".join(f"• {_user_display_names.get(uid, str(uid))}" for uid in enemy_ids))
    else:
        parts.append("خالیه.")
    status = "روشن ✅" if get_setting(chat_id, "terminator_enabled") else "خاموش ⛔"
    parts.append(f"\nوضعیت سیستم ترمیناتور: {status}")
    return "\n".join(parts)


async def relations_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/relations - لیست دوست‌ها و دشمن‌ها رو یکجا نشون می‌ده."""
    await update.effective_message.reply_text(_build_relations_text(update.effective_chat.id))


def _get_command_arg_text(update: Update) -> str | None:
    """متن بعد از دستور رو برمی‌گردونه، مثلاً /addenemyword گراز -> 'گراز'."""
    if not update.message or not update.message.text:
        return None
    parts = update.message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return None
    return parts[1].strip()


async def enemyword_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "manage_relationships"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    word = _get_command_arg_text(update)
    if not word:
        await update.effective_message.reply_text("استفاده: /addenemyword <کلمه>")
        return
    _enemy_trigger_words[update.effective_chat.id].add(word)
    await update.effective_message.reply_text(
        f"«{word}» به کلمات دشمن‌کننده اضافه شد. حالا با ریپلای و نوشتن این کلمه، طرف دشمن اعلام می‌شه."
    )
    await save_state()


async def enemyword_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "manage_relationships"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    word = _get_command_arg_text(update)
    if not word:
        await update.effective_message.reply_text("استفاده: /removeenemyword <کلمه>")
        return
    _enemy_trigger_words[update.effective_chat.id].discard(word)
    await update.effective_message.reply_text(f"«{word}» از کلمات دشمن‌کننده حذف شد.")
    await save_state()


async def friendword_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "manage_relationships"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    word = _get_command_arg_text(update)
    if not word:
        await update.effective_message.reply_text("استفاده: /addfriendword <کلمه>")
        return
    _friend_trigger_words[update.effective_chat.id].add(word)
    await update.effective_message.reply_text(
        f"«{word}» به کلمات دوست‌کننده اضافه شد. حالا با ریپلای و نوشتن این کلمه، طرف دوست اعلام می‌شه."
    )
    await save_state()


async def friendword_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "manage_relationships"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    word = _get_command_arg_text(update)
    if not word:
        await update.effective_message.reply_text("استفاده: /removefriendword <کلمه>")
        return
    _friend_trigger_words[update.effective_chat.id].discard(word)
    await update.effective_message.reply_text(f"«{word}» از کلمات دوست‌کننده حذف شد.")
    await save_state()


async def enemywords_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    words = _enemy_trigger_words[update.effective_chat.id]
    await update.effective_message.reply_text("⚔️ کلمات دشمن‌کننده:\n" + "\n".join(sorted(words)))


async def friendwords_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    words = _friend_trigger_words[update.effective_chat.id]
    await update.effective_message.reply_text("🤝 کلمات دوست‌کننده:\n" + "\n".join(sorted(words)))


def _friend_get_profile(chat_id: int, user) -> dict:
    """پروفایل مستقل یه Friend رو برمی‌گردونه (اگه نبود، می‌سازتش)."""
    profile = _friend_profiles[chat_id].get(user.id)
    if profile is None:
        profile = {
            "username": user.username,
            "name": user.first_name or user.username or str(user.id),
            "added_date": time.time(),
            "message_count": 0,
            "response_count": 0,
            "last_message_time": 0,
            "recent_responses": [],
        }
        _friend_profiles[chat_id][user.id] = profile
    else:
        profile["username"] = user.username
        profile["name"] = user.first_name or user.username or str(user.id)
    return profile


def _terminator_get_target(chat_id: int, user) -> dict:
    """پروفایل ترمیناتور یه کاربر رو برمی‌گردونه (اگه نبود، می‌سازتش)."""
    profile = _terminator_targets[chat_id].get(user.id)
    if profile is None:
        profile = {
            "username": user.username,
            "name": user.first_name or user.username or str(user.id),
            "added_date": time.time(),
            "message_count": 0,
            "response_count": 0,
            "threat_score": 0,
            "response_mode": None,  # None یعنی از مد پیش‌فرض گروه استفاده کن
            "cooldown_seconds": None,  # None یعنی از کول‌داون پیش‌فرض گروه استفاده کن
            "last_response_time": 0,
            "recent_responses": [],
            "last_message_time": 0,
            "is_boss": False,
            "last_action_level": 0,  # آخرین سطحی که اقدام خودکار (اخطار/بن) براش اجرا شده
        }
        _terminator_targets[chat_id][user.id] = profile
    else:
        profile["username"] = user.username
        profile["name"] = user.first_name or user.username or str(user.id)
        profile.setdefault("last_action_level", 0)  # سازگاری با پروفایل‌های قدیمیِ ذخیره‌شده روی دیسک
    return profile


def _terminator_threat_level(score: int) -> int:
    level = 1
    for lvl, threshold in sorted(config.TERMINATOR_THREAT_THRESHOLDS.items()):
        if score >= threshold:
            level = lvl
    return level


def _terminator_message_kind(message) -> str:
    if getattr(message, "sticker", None):
        return "sticker"
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "video_note", None):
        return "video_note"
    if getattr(message, "video", None):
        return "video"
    if getattr(message, "animation", None):
        return "animation"
    if getattr(message, "voice", None):
        return "voice"
    if getattr(message, "audio", None):
        return "audio"
    if getattr(message, "document", None):
        return "document"
    if getattr(message, "contact", None):
        return "contact"
    if getattr(message, "poll", None):
        return "poll"
    if getattr(message, "dice", None):
        return "dice"
    if getattr(message, "venue", None):
        return "venue"
    if getattr(message, "location", None):
        return "live_location" if getattr(message.location, "live_period", None) else "location"
    # نکته: تلگرام برای پیام‌های Game (message.game) توی python-telegram-bot فیلتر مستقیم و
    # پرکاربردی نداره؛ این نوع خیلی نادره و پیاده نشد (مستندشده، نه جعل‌شده).
    text = message.text or message.caption or ""
    if text and LINK_PATTERN.search(text):
        return "link"
    return "text"


def _terminator_avoid_repeat(profile: dict, candidates: list) -> str:
    if not candidates:
        candidates = ["😐 ..."]
    recent = profile.get("recent_responses", [])
    fresh = [c for c in candidates if c not in recent]
    pool = fresh if fresh else candidates
    choice = random.choice(pool)
    recent.append(choice)
    n = getattr(config, "TERMINATOR_ANTI_REPEAT_COUNT", 5)
    profile["recent_responses"] = recent[-n:]
    return choice


def _terminator_pick_mode(chat_id: int, profile: dict) -> str:
    mode = profile.get("response_mode") or _terminator_default_mode[chat_id]
    if mode == "random" or mode not in config.TERMINATOR_RESPONSE_MODES:
        candidates = [m for m in config.TERMINATOR_RESPONSE_MODES if m != "random"]
        mode = random.choice(candidates)
    return mode


def _terminator_pick_local_response(chat_id: int, profile: dict, level: int, kind: str, text: str) -> str:
    # اولویت ۱: Triggerهای اختصاصی (فقط برای پیام متنی)
    if kind in ("text", "link") and text:
        lowered = text.lower()
        trigger_candidates = []
        for trigger, replies in config.TERMINATOR_TRIGGERS.items():
            if trigger in lowered:
                trigger_candidates.extend(replies)
        if trigger_candidates:
            return _terminator_avoid_repeat(profile, trigger_candidates)

    # اولویت ۲: نوع پیام (برای پیام‌های غیرمتنی مثل عکس/استیکر/لینک)
    candidates = []
    if kind != "text":
        candidates.extend(config.TERMINATOR_TYPE_RESPONSES.get(kind, []))

    # اولویت ۳: سطح Threat + Response Mode (همیشه به استخر اضافه می‌شن)
    mode = _terminator_pick_mode(chat_id, profile)
    candidates.extend(config.TERMINATOR_LEVEL_RESPONSES.get(level, []))
    candidates.extend(config.TERMINATOR_MODE_RESPONSES.get(mode, []))
    if profile.get("is_boss"):
        candidates.extend(config.TERMINATOR_BOSS_RESPONSES)

    return _terminator_avoid_repeat(profile, candidates)


async def _terminator_ai_response(user_text: str):
    """پاسخ هوشمند AI مخصوص ترمیناتور. اگه AI در دسترس نباشه یا خطا بده، None برمی‌گردونه
    تا فراخوان به پاسخ‌های محلی برگرده - هیچ‌وقت این تابع Crash نمی‌کنه."""
    if _ai_client is None or not user_text:
        return None
    text = user_text.strip()
    if len(text) > config.AI_MAX_INPUT_CHARS:
        text = text[: config.AI_MAX_INPUT_CHARS]
    try:
        # 🚨 سقف زمانی قطعی و مستقل از کتابخانه‌ی Groq: قبلاً اگه کلاینت AI به هر دلیلی
        # (DNS کند، شبکه قطع، باگ SDK) به تایم‌اوت داخلی‌اش پایبند نمی‌موند، این await
        # می‌تونست چند ده ثانیه یا بیشتر معلق بمونه. چون این تابع همیشه از داخل
        # enemy_friend_auto_reply_handler صدا زده می‌شه (جایی که کول‌داون از قبل رزرو
        # شده - به کامنت بالای همون تابع نگاه کن)، یه تعلیق طولانی اینجا دقیقاً همون
        # علامتِ «به‌جای ریپلای فوری، بعد چند دقیقه فقط یه پیام جواب می‌گیره» رو می‌سازه.
        # wait_for تضمین می‌کنه این تابع حداکثر بعد از TERMINATOR_AI_HARD_TIMEOUT_SECONDS
        # برگرده (موفق یا None)، صرف‌نظر از رفتار داخلی کتابخانه.
        response = await asyncio.wait_for(
            asyncio.to_thread(
                _ai_client.chat.completions.create,
                model=config.AI_MODEL,
                messages=[
                    {"role": "system", "content": config.TERMINATOR_AI_SYSTEM_INSTRUCTION},
                    {"role": "user", "content": text},
                ],
                max_tokens=config.TERMINATOR_AI_MAX_TOKENS,
                temperature=config.TERMINATOR_AI_TEMPERATURE,
            ),
            timeout=config.TERMINATOR_AI_HARD_TIMEOUT_SECONDS,
        )
        content = response.choices[0].message.content
        return content.strip() if content else None
    except asyncio.TimeoutError:
        logger.info(
            f"پاسخ هوشمند ترمیناتور بعد از {config.TERMINATOR_AI_HARD_TIMEOUT_SECONDS} ثانیه "
            "جواب نداد، به پاسخ محلی برمی‌گردیم."
        )
        return None
    except Exception as e:
        logger.info(f"پاسخ هوشمند ترمیناتور ناموفق بود، به پاسخ محلی برمی‌گردیم: {e}")
        return None


async def enemy_friend_auto_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قلب سیستم ترمیناتور: اگه فرستنده دوست باشه پیام گرم می‌گیره، اگه دشمن (Target) باشه
    بسته به Threat Level/Response Mode/نوع پیام یه پاسخ طعنه‌آمیز هوشمند و بدون تکرار می‌گیره."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not user:
        return

    if not _chat_settings[chat.id].get("bot_enabled", False):
        return

    if not get_setting(chat.id, "terminator_enabled"):
        return

    # 👑 Priority: Owner/Admin/VIP همیشه مستثنا هستن، حتی اگه اشتباهی توی Enemy list باشن
    if is_admin(user.id) or user.id in _vip_users[chat.id]:
        return

    if user.id in _friends[chat.id]:
        profile = _friend_get_profile(chat.id, user)
        profile["message_count"] += 1
        profile["last_message_time"] = time.time()
        reply_text = _terminator_avoid_repeat(profile, list(config.FRIEND_REPLIES))
        try:
            await message.reply_text(reply_text)
            profile["response_count"] += 1
        except Exception as e:
            logger.info(f"پاسخ خودکار دوست ناموفق بود: {e}")
        raise ApplicationHandlerStop

    if user.id not in _enemies[chat.id]:
        return

    # لایه‌ی امنِ اضافه برای انقضای دشمنی (کار اصلی رو تسک پس‌زمینه‌ی دوره‌ای انجام می‌ده)
    expiry = _enemy_expiry[chat.id].get(user.id)
    if expiry and expiry <= time.time():
        _enemies[chat.id].discard(user.id)
        _enemy_expiry[chat.id].pop(user.id, None)
        return

    try:
        profile = _terminator_get_target(chat.id, user)
        profile["message_count"] += 1

        now = time.time()
        recent_times = _terminator_recent_times[chat.id][user.id]
        recent_times.append(now)
        is_rapid = len(recent_times) >= 2 and (now - recent_times[-2]) < 3
        is_flood = (
            len(recent_times) >= config.TERMINATOR_SPAM_MESSAGE_THRESHOLD
            and (now - recent_times[0]) < config.TERMINATOR_SPAM_WINDOW_SECONDS
        )

        if is_flood:
            profile["threat_score"] += config.TERMINATOR_SCORE_FLOOD_MESSAGE
        elif is_rapid:
            profile["threat_score"] += config.TERMINATOR_SCORE_RAPID_MESSAGE
        else:
            profile["threat_score"] += config.TERMINATOR_SCORE_NORMAL_MESSAGE

        level = _terminator_threat_level(profile["threat_score"])
        profile["is_boss"] = level >= 5
        profile["last_message_time"] = now

        cooldown = profile.get("cooldown_seconds") or config.TERMINATOR_COOLDOWN_SECONDS
        if is_flood:
            cooldown = max(cooldown, config.TERMINATOR_SPAM_COOLDOWN_SECONDS)

        in_cooldown = now - profile.get("last_response_time", 0) < cooldown
        is_silent = _terminator_mode[chat.id] == "silent"

        # 🐛 رفع باگِ اصلیِ «به‌جای ریپلای فوری به هر پیام، بعد چند دقیقه فقط به یکی
        # جواب می‌ده»: قبلاً last_response_time فقط بعد از موفقیتِ ارسال پاسخ (بعد از
        # await AI که می‌تونست چند ثانیه طول بکشه) ست می‌شد. یعنی تا وقتی جواب اولی
        # نرسیده بود، هر پیام بعدیِ همون Target هم in_cooldown=False می‌دید و خودش یه
        # فراخوانی AI جدا و هم‌زمان راه می‌نداخت - چندتا Task هم‌زمان روی همون کاربر،
        # هرکدوم منتظر AI، کول‌داون هم عملاً بی‌اثر می‌شد. نتیجه: صف پیام‌های معلق
        # که کلاینت AI/شبکه رو تحت فشار می‌ذاشت و در عمل فقط یکی از پاسخ‌ها بعد
        # از تاخیر زیاد (وقتی همه‌ی Taskها باهم رقابت می‌کردن) به کاربر می‌رسید.
        # رفعش: کول‌داون رو همینجا و قبل از هر await رزرو می‌کنیم، پس هر پیامی که توی
        # پنجره‌ی کول‌داون برسه (حتی پیام‌هایی که وقتی AI هنوز جواب نداده می‌رسن) بلافاصله
        # (بدون هیچ await) کنار گذاشته می‌شه، نه اینکه خودش یه فراخوانی AI جدید بسازه.
        if not in_cooldown and not is_silent:
            profile["last_response_time"] = now

            kind = _terminator_message_kind(message)
            text_for_ai = message.text or message.caption or ""

            reply_text = None
            if kind in ("text", "link") and get_setting(chat.id, "terminator_ai_enabled"):
                reply_text = await _terminator_ai_response(text_for_ai)

            if not reply_text:
                reply_text = _terminator_pick_local_response(chat.id, profile, level, kind, text_for_ai)

            try:
                await message.reply_text(reply_text)
                profile["response_count"] += 1
            except Exception as e:
                logger.warning(f"ارسال پاسخ ترمیناتور ناموفق بود: {e}")

        # ☠️ اقدام خودکار سطح‌بالا: هرچی Threat Level بالاتر بره، جواب‌ها تندتر می‌شن
        # (از روی TERMINATOR_LEVEL_RESPONSES) و از یه سطح به بعد (پیش‌فرض ۴=اخطار،
        # ۵=بن) یه اقدام مدیریتی واقعی هم می‌خوره - نه فقط شوخی. هر سطح فقط یه‌بار
        # اقدامش اجرا می‌شه (last_action_level جلوی تکرار در هر پیام رو می‌گیره).
        action = config.TERMINATOR_LEVEL_ACTIONS.get(level)
        if action and profile.get("last_action_level", 0) < level and not is_admin(user.id):
            profile["last_action_level"] = level
            try:
                level_label = config.TERMINATOR_LEVEL_LABELS.get(level, str(level))
                reason = config.TERMINATOR_ACTION_REASON.format(level=level, label=level_label)
                display_name = profile.get("name") or user.first_name or str(user.id)
                if action == "warn":
                    await moderation_engine.warn(
                        context, chat.id, user.id, display_name,
                        reason=reason, source="TERMINATOR", message=message,
                    )
                elif action == "ban":
                    await moderation_engine.ban(
                        context, chat.id, user.id, display_name,
                        reason=reason, source="TERMINATOR", message=message,
                    )
            except Exception as e:
                logger.warning(f"اقدام خودکار ترمیناتور (سطح {level}) ناموفق بود: {e}")
    except Exception as e:
        logger.warning(f"پردازش ترمیناتور با خطا مواجه شد (نادیده گرفته شد): {e}")

    raise ApplicationHandlerStop


# ---------- تگ دسته‌جمعی اعضا ----------

def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def _run_tag_all(chat_id: int, context: ContextTypes.DEFAULT_TYPE, caption: str | None) -> int:
    """همه‌ی اعضایی که ربات تا الان توی این گروه دیده رو، دسته‌دسته (برای رعایت محدودیت
    طول پیام و نرخ ارسال تلگرام) با منشن واقعی (tg://user) تگ می‌کنه. تعداد تگ‌شده‌ها رو برمی‌گردونه."""
    members = sorted(_known_members[chat_id])
    if _bot_instance is not None:
        members = [uid for uid in members if uid != _bot_instance.id]
    if not members:
        await context.bot.send_message(
            chat_id=chat_id,
            text="هنوز عضوی برای تگ کردن شناسایی نشده. وقتی افراد بیشتری پیام بدن یا عضو بشن، لیست کامل‌تر می‌شه.",
        )
        return 0

    batch_size = max(1, config.TAG_BATCH_SIZE)
    total = len(members)
    for i in range(0, total, batch_size):
        chunk = members[i : i + batch_size]
        mentions = []
        for uid in chunk:
            name = _html_escape(_user_display_names.get(uid, str(uid)))
            mentions.append(f'<a href="tg://user?id={uid}">{name}</a>')
        text = " ".join(mentions)
        if i == 0 and caption:
            text = f"{_html_escape(caption)}\n\n{text}"
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"ارسال تگ ناموفق بود: {e}")
        if i + batch_size < total:
            await asyncio.sleep(config.TAG_BATCH_DELAY_SECONDS)
    return total


async def tag_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/tag [متن دلخواه] - همه‌ی اعضای شناخته‌شده‌ی گروه رو تگ می‌کنه."""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not has_permission(update.effective_user.id, "tag_members"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    if not get_setting(chat.id, "tag_members_enabled"):
        await update.effective_message.reply_text(
            "قابلیت تگ دسته‌جمعی خاموشه. از /menu (بخش «🏷️ تگ دسته‌جمعی اعضا») روشنش کن."
        )
        return
    caption = _get_command_arg_text(update)
    await context.bot.send_chat_action(chat_id=chat.id, action="typing")
    await _run_tag_all(chat.id, context, caption)


# ---------- ۵) آمار فعالیت گروه ----------

# مجموع طول پیام‌های متنی هر کاربر (برای میانگین): chat_id -> user_id -> مجموع کاراکتر
_text_length_totals = defaultdict(lambda: defaultdict(int))
_text_message_counts = defaultdict(lambda: defaultdict(int))

# تعداد پیام‌های مدیا/استیکر هر کاربر: chat_id -> user_id -> تعداد
_media_counts = defaultdict(lambda: defaultdict(int))

# فعالیت بر اساس ساعت/روز هفته (برای کل گروه): chat_id -> hour/weekday -> تعداد
_hour_activity = defaultdict(lambda: defaultdict(int))
_weekday_activity = defaultdict(lambda: defaultdict(int))

WEEKDAY_NAMES_FA = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]

# ---------- 📊 DIGIANTI SMART ACTIVITY SYSTEM (روی آمار/XP بالا ساخته شده، جایگزینش نمی‌کنه) ----------
# آمار روزانه‌ی هر کاربر - پایه‌ی لیدربرد بازه‌ای/Streak/روند فعالیت:
# chat_id -> user_id -> "YYYY-MM-DD" -> {"messages": int, "score": int}
_daily_activity = defaultdict(lambda: defaultdict(dict))
# امتیاز کلی فعالیت (Activity Score) - جدا از XP: chat_id -> user_id -> امتیاز
_activity_score = defaultdict(lambda: defaultdict(int))
# کول‌داون ضدسوءاستفاده‌ی مخصوص Activity Score (جدا از کول‌داون XP)
_last_activity_score_time = defaultdict(dict)
# Streak هر کاربر: chat_id -> user_id -> {"current": int, "best": int, "last_date": "YYYY-MM-DD"}
_streaks = defaultdict(dict)
# آخرین زمان فعالیت هر کاربر (برای تشخیص عضو فعال/کم‌فعالیت/غیرفعال)
_last_message_time = defaultdict(dict)
# آمار انواع پیام هر کاربر: chat_id -> user_id -> {"text": n, "photo": n, ...}
_message_type_counts = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
# توزیع ساعتی فعالیت هر کاربر (برای شناسایی الگوی رفتاری واقعی - Night Owl/Early Bird)
_user_hour_activity = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
# دستاوردهای آزادشده‌ی هر کاربر: chat_id -> user_id -> set(achievement_key)
_achievements = defaultdict(lambda: defaultdict(set))
# تاریخچه‌ی کوتاه آخرین فعالیت‌ها برای /activity: chat_id -> user_id -> deque[(ts, kind)]
_activity_history = defaultdict(lambda: defaultdict(lambda: deque(maxlen=config.ACTIVITY_HISTORY_LOG_SIZE)))
# نگاشت یوزرنیم -> آیدی عددی (برای /activity @username بدون نیاز به ریپلای)
_username_to_id = {}
# آخرین باری که پاکسازی دوره‌ای Activity History اجرا شد (throttle - هر ۶ ساعت یه‌بار، نه هر ۳۰ ثانیه)
_last_retention_cleanup = 0
_last_moderation_decay = 0


def _activity_today_str() -> str:
    return _now_local().strftime("%Y-%m-%d")


def _activity_week_dates(offset_weeks: int = 0) -> list:
    """تاریخ ۷ روز هفته‌ی جاری (یا offset_weeks هفته قبل‌تر)، با شروع از دوشنبه."""
    now = _now_local()
    start = now - datetime.timedelta(days=now.weekday(), weeks=offset_weeks)
    return [(start + datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]


def _activity_month_dates(offset_months: int = 0) -> list:
    now = _now_local()
    year, month = now.year, now.month - offset_months
    while month <= 0:
        month += 12
        year -= 1
    days_in_month = calendar.monthrange(year, month)[1]
    end_day = now.day if offset_months == 0 else days_in_month
    return [f"{year:04d}-{month:02d}-{d:02d}" for d in range(1, end_day + 1)]


def _activity_sum(chat_id: int, uid: int, dates: list, field: str = "messages") -> int:
    bucket = _daily_activity[chat_id].get(uid, {})
    return sum(bucket.get(d, {}).get(field, 0) for d in dates)


def _activity_update_streak(chat_id: int, uid: int, today: str) -> tuple:
    """Streak رو آپدیت می‌کنه و (اولین‌پیام‌امروزه؟, current, best) برمی‌گردونه."""
    streak = _streaks[chat_id].setdefault(uid, {"current": 0, "best": 0, "last_date": None})
    if streak["last_date"] == today:
        return False, streak["current"], streak["best"]
    yesterday = (_now_local() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    streak["current"] = streak["current"] + 1 if streak["last_date"] == yesterday else 1
    streak["best"] = max(streak["best"], streak["current"])
    streak["last_date"] = today
    return True, streak["current"], streak["best"]


def _activity_check_achievements(chat_id: int, uid: int) -> list:
    """آستانه‌ها رو چک می‌کنه و دستاوردهای تازه‌آزادشده رو برمی‌گردونه (بدون اسکن سنگین رتبه‌ای)."""
    unlocked = _achievements[chat_id][uid]
    types = _message_type_counts[chat_id][uid]
    level, _into, _needed = _xp_progress(_xp[chat_id].get(uid, 0))
    metrics = {
        "messages": _message_counts[chat_id].get(uid, 0),
        "streak": _streaks[chat_id].get(uid, {}).get("current", 0),
        "voice": types.get("voice", 0),
        "media": sum(types.get(k, 0) for k in ("photo", "video", "animation", "sticker", "video_note")),
        "score": _activity_score[chat_id].get(uid, 0),
        # افزوده‌شده در فاز ۱ (Profile Engine) - سیستم آستانه‌ای فعلی همینطور کار می‌کنه
        "level": level,
        "coins": _wallet[chat_id].get(uid, 0),
    }
    newly = []
    for key, meta in config.ACTIVITY_ACHIEVEMENTS.items():
        if key in unlocked:
            continue
        if metrics.get(meta["metric"], 0) >= meta["threshold"]:
            unlocked.add(key)
            newly.append(key)
    if newly:
        # جایزه‌ی XP/Coin طبق config.ACHIEVEMENT_REWARDS (فاز ۱) - بدون تغییر رفتار قبلی
        profile_engine.grant_rewards_for_unlocked(chat_id, uid, newly)
    return newly


def _activity_intelligence_tags(chat_id: int, uid: int) -> list:
    """بر اساس داده‌ی واقعیِ ثبت‌شده (نه حدس)، الگوی رفتاری کاربر رو تعیین می‌کنه."""
    tags = []
    hours = _user_hour_activity[chat_id][uid]
    total = sum(hours.values())
    if total >= 10:
        night = sum(hours.get(h, 0) for h in (22, 23, 0, 1, 2, 3, 4, 5))
        morning = sum(hours.get(h, 0) for h in range(5, 10))
        if night / total >= 0.4:
            tags.append("🌙 شب‌زنده‌دار (Night Owl)")
        elif morning / total >= 0.4:
            tags.append("☀️ سحرخیز (Early Bird)")

    trend = _activity_trend_percent(chat_id, uid)
    if trend is not None:
        if trend >= 20:
            tags.append(f"📈 فعالیت رو به افزایش (+{trend}%)")
        elif trend <= -20:
            tags.append(f"📉 فعالیت رو به کاهش ({trend}%)")

    if _streaks[chat_id].get(uid, {}).get("current", 0) >= 3:
        tags.append("⚡ فوق‌فعال (Highly Active)")
    return tags


def _activity_trend_percent(chat_id: int, uid: int):
    this_week = _activity_sum(chat_id, uid, _activity_week_dates(0))
    last_week = _activity_sum(chat_id, uid, _activity_week_dates(1))
    if last_week == 0:
        return None
    return round((this_week - last_week) / last_week * 100)


def _activity_group_trend_percent(chat_id: int):
    this_week = sum(_activity_sum(chat_id, uid, _activity_week_dates(0)) for uid in _known_members[chat_id])
    last_week = sum(_activity_sum(chat_id, uid, _activity_week_dates(1)) for uid in _known_members[chat_id])
    if last_week == 0:
        return None
    return round((this_week - last_week) / last_week * 100)


def _activity_group_tiers(chat_id: int):
    now = time.time()
    active = low = inactive = 0
    for uid in _known_members[chat_id]:
        last = _last_message_time[chat_id].get(uid)
        if last is None:
            inactive += 1
            continue
        days_since = (now - last) / 86400
        if days_since <= config.ACTIVITY_LOW_THRESHOLD_DAYS:
            active += 1
        elif days_since <= config.ACTIVITY_INACTIVE_THRESHOLD_DAYS:
            low += 1
        else:
            inactive += 1
    return active, low, inactive


def _activity_leaderboard(chat_id: int, period: str, metric: str, limit: int = None) -> list:
    """period: today|week|month|alltime ; metric: messages|xp|score"""
    limit = limit or config.ACTIVITY_LEADERBOARD_SIZE
    if period == "alltime":
        source = {"xp": _xp, "messages": _message_counts, "score": _activity_score}.get(metric, _xp)
        ranking = sorted(source[chat_id].items(), key=lambda kv: kv[1], reverse=True)
        return [(uid, val) for uid, val in ranking if val > 0][:limit]

    dates = {
        "today": [_activity_today_str()],
        "week": _activity_week_dates(0),
        "month": _activity_month_dates(0),
    }.get(period, [_activity_today_str()])
    field = "score" if metric == "score" else "messages"  # لیدربرد بازه‌ای XP نداریم (XP تجمعیه، نه روزانه)
    totals = []
    for uid, days in _daily_activity[chat_id].items():
        total = sum(days.get(d, {}).get(field, 0) for d in dates)
        if total > 0:
            totals.append((uid, total))
    totals.sort(key=lambda kv: kv[1], reverse=True)
    return totals[:limit]


def _activity_progress_bar(fraction: float, width: int = 10) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled)


def _activity_track_message(chat_id: int, user, message) -> list:
    """قلب سیستم فعالیت هوشمند: نوع پیام، امتیاز، Streak، تاریخچه و دستاورد رو آپدیت می‌کنه.
    فراخوان (track_chat_and_stats) این رو توی try/except صدا می‌زنه، پس اینجا نیازی به
    مدیریت خطای اضافه نیست - هر Exception بالا می‌ره و بی‌خطر بلعیده می‌شه."""
    uid = user.id
    now = time.time()
    today = _activity_today_str()
    kind = _terminator_message_kind(message)  # همون تشخیص نوع پیامِ سیستم ترمیناتور - تکراری نساختیم

    _last_message_time[chat_id][uid] = now
    _message_type_counts[chat_id][uid][kind] += 1
    _user_hour_activity[chat_id][uid][_now_local().hour] += 1
    _activity_history[chat_id][uid].append((now, kind))

    day_bucket = _daily_activity[chat_id][uid].setdefault(today, {"messages": 0, "score": 0})
    day_bucket["messages"] += 1

    is_first_today, streak_current, _streak_best = _activity_update_streak(chat_id, uid, today)

    # ---- Activity Score: ضدسوءاستفاده با کول‌داون جدا از XP ----
    last_score_time = _last_activity_score_time[chat_id].get(uid, 0)
    if now - last_score_time >= config.ACTIVITY_SCORE_COOLDOWN_SECONDS:
        _last_activity_score_time[chat_id][uid] = now
        weight = config.ACTIVITY_SCORE_WEIGHTS.get(kind, config.ACTIVITY_SCORE_WEIGHTS.get("other", 1))
        gain = weight
        if is_first_today:
            gain += config.ACTIVITY_DAILY_BONUS
            gain += min(streak_current * config.ACTIVITY_STREAK_BONUS_PER_DAY, config.ACTIVITY_STREAK_BONUS_CAP)
        _activity_score[chat_id][uid] += gain
        day_bucket["score"] += gain

    return _activity_check_achievements(chat_id, uid)


async def track_chat_and_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """این هندلر دو کار رو با هم انجام می‌ده (قبلاً دو هندلر جدا با فیلترهای هم‌پوشان روی
    یه گروه بودن که باعث می‌شد فقط اولی اجرا بشه و دومی هیچ‌وقت کار نکنه - این باگ رفع شد):
    ۱) ثبت چت برای /notify (برای هر نوع پیامی، توی گروه یا خصوصی)
    ۲) ثبت آمار فعالیت + شناسایی عضو برای «تگ همه» (فقط توی گروه‌ها)
    """
    if update.effective_chat:
        _known_chats.add(update.effective_chat.id)
        if update.effective_chat.type in ("group", "supergroup") and update.effective_chat.title:
            _chat_titles[update.effective_chat.id] = update.effective_chat.title

    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not user or not chat or chat.type not in ("group", "supergroup"):
        return

    # کش کوتاه‌مدت پاکسازی: مستقل از تنظیم bot_enabled، چون ابزار مدیریتیه
    purge_engine.record_message(message, user, chat.id)

    if not user.is_bot:
        _known_members[chat.id].add(user.id)
        _user_display_names[user.id] = user.first_name or user.username or str(user.id)
        if user.username:
            _username_to_id[user.username.lower()] = user.id

    settings = _chat_settings[chat.id]
    if not settings.get("bot_enabled", False):
        return

    if settings.get("stats_enabled", False):
        _message_counts[chat.id][user.id] += 1

        if message.date:
            _hour_activity[chat.id][message.date.hour] += 1
            _weekday_activity[chat.id][message.date.weekday()] += 1

        is_media = bool(
            message.sticker
            or message.animation
            or message.photo
            or message.video
            or message.voice
            or message.document
            or message.video_note
        )
        if is_media:
            _media_counts[chat.id][user.id] += 1

        text = message.text or message.caption
        if text:
            _text_length_totals[chat.id][user.id] += len(text)
            _text_message_counts[chat.id][user.id] += 1

        if not user.is_bot:
            try:
                newly_unlocked = _activity_track_message(chat.id, user, message)
                if newly_unlocked:
                    display_name = user.first_name or user.username or "کاربر"
                    labels = [config.ACTIVITY_ACHIEVEMENTS[k]["label"] for k in newly_unlocked]
                    asyncio.create_task(
                        context.bot.send_message(
                            chat_id=chat.id,
                            text=f"🏅 {display_name} دستاورد جدید گرفت: " + "، ".join(labels),
                        )
                    )
            except Exception as e:
                # فعالیت هوشمند هیچ‌وقت نباید کل ربات رو Crash کنه (طبق قانون Fail-Safe)
                logger.warning(f"ثبت سیستم فعالیت هوشمند ناموفق بود (نادیده گرفته شد): {e}")

    # جوایز فعالیت: XP و/یا سکه (هرکدوم جدا از /menu روشن/خاموش می‌شه، با یه کول‌داون مشترک)
    xp_on = settings.get("xp_enabled", False)
    econ_on = settings.get("economy_enabled", False)
    if (xp_on or econ_on) and not user.is_bot:
        last = _last_xp_time[chat.id].get(user.id, 0)
        now = time.time()
        if now - last >= config.XP_MESSAGE_COOLDOWN_SECONDS:
            _last_xp_time[chat.id][user.id] = now
            es = _econ_settings[chat.id]
            if xp_on:
                old_level, _, _ = _xp_progress(_xp[chat.id][user.id])
                gained = random.randint(min(es["xp_min"], es["xp_max"]), max(es["xp_min"], es["xp_max"]))
                gained = economy_engine.apply_xp_multiplier(chat.id, user.id, gained)  # فاز ۲: تقویت XP فروشگاهی
                _xp[chat.id][user.id] += gained
                profile_engine.record_xp_gain(chat.id, user.id, gained, reason="activity")  # فاز ۳: تاریخچه‌ی XP
                new_level, _, _ = _xp_progress(_xp[chat.id][user.id])
                if new_level > old_level:
                    title = _auto_title_for_level(new_level)
                    display_name = user.first_name or user.username or "کاربر"
                    xp_r, coin_r = await profile_engine.grant_level_reward(chat.id, user.id, new_level)  # فاز ۳
                    reward_note = ""
                    if xp_r or coin_r:
                        reward_note = f" (جایزه: +{xp_r} XP, +{coin_r} {config.CURRENCY_NAME})" if xp_r and coin_r \
                            else (f" (جایزه: +{xp_r} XP)" if xp_r else f" (جایزه: +{coin_r} {config.CURRENCY_NAME})")
                    asyncio.create_task(
                        context.bot.send_message(
                            chat_id=chat.id,
                            text=f"🏆 تبریک {display_name}! به Level {new_level} رسیدی. {title}{reward_note}",
                        )
                    )
            if econ_on:
                coin_gain = random.randint(min(es["activity_min"], es["activity_max"]), max(es["activity_min"], es["activity_max"]))
                coin_gain = economy_engine.apply_coin_multiplier(chat.id, user.id, coin_gain)  # فاز ۲: تقویت سکه‌ی فروشگاهی
                coin_gain = round(coin_gain * profile_engine.wellbeing_multiplier(chat.id, user.id))  # فاز ۱۷: اثر ملایم Needs
                if coin_gain:
                    await economy_core.add_coins(chat.id, user.id, coin_gain, kind="REWARD", note="activity")


async def _build_profile_text(chat_id: int, user) -> str:
    uid = user.id
    name = user.first_name or user.username or str(uid)
    _prune_warnings(chat_id, uid)
    xp = _xp[chat_id].get(uid, 0)
    level, into_level, needed = _xp_progress(xp)
    title = _display_title(chat_id, uid)
    msg_count = _message_counts[chat_id].get(uid, 0)
    warn_count = _warnings[chat_id].get(uid, 0)
    warn_limit = _warn_limit[chat_id]
    if uid in _enemies[chat_id]:
        relation = "دشمن ⚔️"
    elif uid in _friends[chat_id]:
        relation = "دوست 🤝"
    else:
        relation = "عادی"
    join_ts = _join_times[chat_id].get(uid)
    join_str = time.strftime("%Y-%m-%d", time.localtime(join_ts)) if join_ts else "نامشخص"

    ranking = sorted(_xp[chat_id].items(), key=lambda kv: kv[1], reverse=True)
    rank = next((i + 1 for i, (u, _x) in enumerate(ranking) if u == uid), None)
    rank_text = f"#{rank}" if rank else "—"

    badges = _owned_badges[chat_id].get(uid, set())
    badge_line = ""
    if badges:
        badge_emojis = " ".join(
            config.SHOP_ITEMS[b]["emoji"] for b in badges if b in config.SHOP_ITEMS
        )
        badge_line = f"🎖️ بج‌ها: {badge_emojis}\n"

    economy_line = ""
    if get_setting(chat_id, "economy_enabled"):
        bal = _wallet[chat_id].get(uid, 0)
        economy_line = f"{config.CURRENCY_EMOJI} موجودی: {bal} {config.CURRENCY_NAME}\n"

    return (
        f"👤 پروفایل {name}\n"
        f"—————————————\n"
        f"🏷️ لقب: {title or '—'}\n"
        f"{badge_line}"
        f"🏆 Level {level} | XP: {xp} ({into_level}/{needed})\n"
        f"{economy_line}"
        f"📊 رتبه‌ی گروه (بر اساس XP): {rank_text}\n"
        f"💬 تعداد پیام ثبت‌شده: {msg_count}\n"
        f"⚠️ اخطار فعال: {warn_count} از {warn_limit}\n"
        f"🤝 وضعیت رابطه: {relation}\n"
        f"📅 تاریخ ورود به گروه: {join_str}"
    )


async def _send_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("پروفایل فقط توی گروه قابل مشاهده‌ست.")
        return
    target_user = message.reply_to_message.from_user if message.reply_to_message else update.effective_user
    text = profile_engine.build_profile_card(chat.id, update.effective_user.id, target_user)
    await message.reply_text(text)
    # نیازی به save_state فوری نیست؛ _periodic_save_loop به‌صورت دوره‌ای ذخیره می‌کنه
    # (این دستور احتمالاً پرتکرارترین Commandه، پس از Write سنگین روی هر بار دیدن پروفایل پرهیز می‌کنیم)


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_profile(update, context)


async def rank_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_profile(update, context)


async def level_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_profile(update, context)


async def _set_custom_title(update: Update, context: ContextTypes.DEFAULT_TYPE, new_title: str | None):
    message = update.effective_message
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    user = update.effective_user
    if new_title is None:
        _custom_titles[chat.id].pop(user.id, None)
        await message.reply_text("لقب اختصاصیت حذف شد؛ از الان لقب خودکار سطحت نشون داده می‌شه.")
        await save_state()
        return
    new_title = new_title.strip()
    if not new_title:
        await message.reply_text("استفاده: /setlqab <لقب دلخواه>")
        return
    if len(new_title) > config.CUSTOM_TITLE_MAX_LENGTH:
        await message.reply_text(f"لقب نباید بیشتر از {config.CUSTOM_TITLE_MAX_LENGTH} کاراکتر باشه.")
        return
    low = new_title.lower()
    if any(bad.lower() in low for bad in config.CUSTOM_TITLE_BANNED_WORDS):
        await message.reply_text("این لقب مجاز نیست.")
        return
    impersonation_error = profile_engine.validate_nickname(user.id, new_title)
    if impersonation_error:
        await message.reply_text(impersonation_error)
        return
    _custom_titles[chat.id][user.id] = new_title
    await message.reply_text(f"✅ لقبت روی «{new_title}» تنظیم شد.")
    await save_state()


async def setlqab_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = _get_command_arg_text(update)
    if not arg:
        await update.effective_message.reply_text("استفاده: /setlqab <لقب دلخواه>")
        return
    await _set_custom_title(update, context, arg)


async def removelqab_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_custom_title(update, context, None)


async def resetlqab_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ادمین می‌تونه لقب یکی دیگه رو ریست کنه (با ریپلای)."""
    if not has_permission(update.effective_user.id, "moderate"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    target_id, name, _ = _parse_target_and_reason(update)
    if target_id is None:
        await update.effective_message.reply_text("استفاده: ریپلای رو پیام کاربر بزن و بنویس /resetlqab")
        return
    _custom_titles[chat_id].pop(target_id, None)
    await update.effective_message.reply_text(f"لقب {name} ریست شد.")
    await save_state()


async def topxp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    ranking = sorted(_xp[chat.id].items(), key=lambda kv: kv[1], reverse=True)[: config.TOP_XP_COUNT]
    if not ranking:
        await update.effective_message.reply_text(
            "هنوز کسی XP نگرفته. مطمئن شو «سیستم XP، Level و پروفایل» توی /menu روشنه."
        )
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 برترین‌های گروه (بر اساس XP):"]
    for i, (uid, xp) in enumerate(ranking):
        name = _user_display_names.get(uid, str(uid))
        level, _, _ = _xp_progress(xp)
        prefix = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{prefix} {name} — Lv.{level} | {xp} XP")
    await update.effective_message.reply_text("\n".join(lines))


def _resolve_activity_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هدف /activity رو مشخص می‌کنه: ریپلای > یوزرنیم (@user) > آیدی عددی > خودِ فرستنده.
    برای یوزرنیم/آیدی، برمی‌گردونه (uid, name) یا (None, None) اگه پیدا نشه."""
    message = update.effective_message
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        return u.id, (u.first_name or u.username or str(u.id))

    args = context.args or []
    if args:
        token = args[0].lstrip("@")
        if token.isdigit():
            uid = int(token)
            return uid, _user_display_names.get(uid, str(uid))
        uid = _username_to_id.get(token.lower())
        if uid:
            return uid, _user_display_names.get(uid, token)
        return None, None  # یوزرنیم داده شده ولی پیدا نشده

    user = update.effective_user
    return user.id, (user.first_name or user.username or str(user.id))


async def activity_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/activity [@username | آیدی عددی] یا با ریپلای - کارت کامل فعالیت رو نشون می‌ده."""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not get_setting(chat.id, "stats_enabled"):
        await message.reply_text("آمار فعالیت گروه خاموشه. از /menu بخش «آمار فعالیت گروه» روشنش کن.")
        return

    uid, name = _resolve_activity_target(update, context)
    if uid is None:
        await message.reply_text("این یوزرنیم رو نمی‌شناسم؛ باید حداقل یه پیام توی گروه فرستاده باشه، یا ریپلای بزن.")
        return

    await message.reply_text(_activity_card_text(chat.id, uid, name))


def _activity_card_text(chat_id: int, uid: int, name: str) -> str:
    xp = _xp[chat_id].get(uid, 0)
    level, into_level, needed = _xp_progress(xp)
    msg_count = _message_counts[chat_id].get(uid, 0)
    score = _activity_score[chat_id].get(uid, 0)
    streak = _streaks[chat_id].get(uid, {"current": 0, "best": 0})

    ranking = sorted(_xp[chat_id].items(), key=lambda kv: kv[1], reverse=True)
    rank = next((i + 1 for i, (u, _x) in enumerate(ranking) if u == uid), None)
    rank_text = f"#{rank}" if rank else "—"

    last_active = _last_message_time[chat_id].get(uid)
    last_active_text = (
        time.strftime("%Y/%m/%d %H:%M", time.localtime(last_active)) if last_active else "—"
    )

    today_msgs = _activity_sum(chat_id, uid, [_activity_today_str()])
    daily_fraction = today_msgs / config.ACTIVITY_DAILY_GOAL_MESSAGES if config.ACTIVITY_DAILY_GOAL_MESSAGES else 0
    week_xp_estimate = _activity_sum(chat_id, uid, _activity_week_dates(0), field="score")
    weekly_fraction = week_xp_estimate / config.ACTIVITY_WEEKLY_GOAL_XP if config.ACTIVITY_WEEKLY_GOAL_XP else 0

    types = _message_type_counts[chat_id].get(uid, {})
    type_labels = {
        "text": "📝 متن", "photo": "🖼 عکس", "video": "🎥 ویدیو", "sticker": "🎭 استیکر",
        "voice": "🎤 ویس", "audio": "🎵 آهنگ", "animation": "🎞 گیف", "document": "📄 فایل",
        "link": "🔗 لینک", "location": "📍 لوکیشن", "contact": "📞 مخاطب",
    }
    type_lines = [f"{type_labels[k]}: {v}" for k, v in sorted(types.items(), key=lambda kv: -kv[1]) if k in type_labels and v > 0][:5]

    tags = _activity_intelligence_tags(chat_id, uid)
    tags_line = ("\n" + "\n".join(tags)) if tags else ""

    unlocked = _achievements[chat_id].get(uid, set())
    ach_line = f"\n🏅 دستاوردها: {len(unlocked)}/{len(profile_engine.all_achievement_labels())}" if unlocked else ""

    join_ts = _join_times[chat_id].get(uid)
    join_line = f"📅 عضو از: {time.strftime('%Y/%m/%d', time.localtime(join_ts))}\n" if join_ts else ""

    return (
        "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "𓆩 📊 𝐀𝐂𝐓𝐈𝐕𝐈𝐓𝐘 𓆪\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"👤 {name}\n"
        f"{join_line}\n"
        f"💬 پیام‌ها: {msg_count}\n"
        f"⭐ XP: {xp}\n"
        f"🏆 Level: {level} (تا بعدی: {needed - into_level} XP)\n"
        f"🥇 رتبه: {rank_text}\n\n"
        f"🔥 Streak: {streak['current']} روز (رکورد: {streak['best']})\n"
        f"🏅 Activity Score: {score}\n\n"
        f"🎯 هدف روزانه ({today_msgs}/{config.ACTIVITY_DAILY_GOAL_MESSAGES} پیام):\n"
        f"{_activity_progress_bar(daily_fraction)} {min(100, round(daily_fraction * 100))}%\n\n"
        + (("┏━━━━━━━━━━━━━━━━━━━━━━┓\n" + "\n".join(f"┃ {l}" for l in type_lines) + "\n┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n") if type_lines else "")
        + f"🕐 آخرین فعالیت:\n{last_active_text}"
        f"{ach_line}"
        f"{tags_line}\n\n"
        "    𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪"
    )


ACTIVITY_PERIOD_LABELS = {"today": "امروز", "week": "این هفته", "month": "این ماه", "alltime": "کل دوران"}
ACTIVITY_METRIC_LABELS = {"messages": "پیام", "xp": "XP", "score": "امتیاز فعالیت"}


def _leaderboard_text(chat_id: int, period: str, metric: str, requester_id: int = None) -> str:
    ranking = _activity_leaderboard(chat_id, period, metric)
    if not ranking:
        return "هنوز داده‌ای برای این بازه ثبت نشده."

    medals = ["🥇", "🥈", "🥉"]
    lines = [
        "╭━━━━━━━━━━━━━━━━━━━━━━╮",
        "𓆩 🏆 𝐓𝐎𝐏 𝐀𝐂𝐓𝐈𝐕𝐄 𓆪",
        "╰━━━━━━━━━━━━━━━━━━━━━━╯",
        "",
        f"({ACTIVITY_PERIOD_LABELS[period]} — {ACTIVITY_METRIC_LABELS[metric]})",
        "",
    ]
    for i, (uid, val) in enumerate(ranking):
        name = _user_display_names.get(uid, str(uid))
        prefix = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{prefix} {name} — {val}")

    if requester_id:
        requester_rank = next((i + 1 for i, (u, _v) in enumerate(ranking) if u == requester_id), None)
        if requester_rank:
            lines.append(f"\n⚡ رتبه‌ی تو: #{requester_rank}")

    lines.append("\n    𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪")
    return "\n".join(lines)


def _streaks_leaderboard_text(chat_id: int) -> str:
    ranking = sorted(_streaks[chat_id].items(), key=lambda kv: kv[1].get("current", 0), reverse=True)
    ranking = [(uid, s) for uid, s in ranking if s.get("current", 0) > 0][: config.ACTIVITY_LEADERBOARD_SIZE]
    if not ranking:
        return "هنوز هیچکس Streak فعالی نداره."
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🔥 استریک‌های فعال:\n"]
    for i, (uid, s) in enumerate(ranking):
        name = _user_display_names.get(uid, str(uid))
        prefix = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{prefix} {name} — {s['current']} روز (رکورد {s['best']})")
    return "\n".join(lines)


def _achievements_text(chat_id: int, uid: int, name: str) -> str:
    unlocked = _achievements[chat_id].get(uid, set())
    all_labels = profile_engine.all_achievement_labels()
    lines = [f"🏅 دستاوردهای {name} ({len(unlocked)}/{len(all_labels)}):", ""]
    for key, label in all_labels.items():
        mark = "✅" if key in unlocked else "▫️"
        lines.append(f"{mark} {label}")
    return "\n".join(lines)


async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/leaderboard [today|week|month|alltime] [messages|xp|score]"""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not get_setting(chat.id, "stats_enabled"):
        await update.effective_message.reply_text("آمار فعالیت گروه خاموشه. از /menu بخش «آمار فعالیت گروه» روشنش کن.")
        return

    args = [a.lower() for a in (context.args or [])]
    period = next((a for a in args if a in ACTIVITY_PERIOD_LABELS), "alltime")
    metric = next((a for a in args if a in ACTIVITY_METRIC_LABELS), "xp")
    if period != "alltime" and metric == "xp":
        metric = "messages"  # XP تجمعیه، برای امروز/هفته/ماه معنی نداره - پیش‌فرض رو می‌ذاریم پیام

    text = _leaderboard_text(chat.id, period, metric, update.effective_user.id)
    await update.effective_message.reply_text(text)


async def achievements_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/achievements [@username|آیدی] یا با ریپلای - دستاوردهای آزادشده رو نشون می‌ده."""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid, name = _resolve_activity_target(update, context)
    if uid is None:
        await update.effective_message.reply_text("این یوزرنیم رو نمی‌شناسم.")
        return
    await update.effective_message.reply_text(_achievements_text(chat.id, uid, name))


async def activity_filter_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/activityfilter <inactive|low|messages|xp|streak|score> - فقط ادمین. لیست فیلترشده‌ی اعضا."""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not has_permission(update.effective_user.id, "menu"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    args = context.args or []
    mode = args[0].lower() if args else ""
    valid = ("inactive", "low", "messages", "xp", "streak", "score")
    if mode not in valid:
        await update.effective_message.reply_text("استفاده: /activityfilter <" + "|".join(valid) + ">")
        return

    members = _known_members[chat.id]
    now = time.time()

    if mode in ("inactive", "low"):
        threshold = config.ACTIVITY_INACTIVE_THRESHOLD_DAYS if mode == "inactive" else config.ACTIVITY_LOW_THRESHOLD_DAYS
        result = []
        for uid in members:
            last = _last_message_time[chat.id].get(uid)
            days_since = (now - last) / 86400 if last else None
            if mode == "inactive" and (days_since is None or days_since > config.ACTIVITY_INACTIVE_THRESHOLD_DAYS):
                result.append((uid, days_since))
            elif mode == "low" and days_since is not None and config.ACTIVITY_LOW_THRESHOLD_DAYS < days_since <= config.ACTIVITY_INACTIVE_THRESHOLD_DAYS:
                result.append((uid, days_since))
        result.sort(key=lambda kv: (kv[1] is None, -(kv[1] or 0)))
        lines = [f"🔴 غیرفعال‌ها (بیش از {threshold} روز):" if mode == "inactive" else "🟡 کم‌فعالیت‌ها:"]
        for uid, days_since in result[:30]:
            name = _user_display_names.get(uid, str(uid))
            d_text = f"{int(days_since)} روز پیش" if days_since is not None else "هیچ‌وقت"
            lines.append(f"• {name} — آخرین فعالیت: {d_text}")
        if len(result) > 30:
            lines.append(f"... و {len(result) - 30} نفر دیگه")
    else:
        source = {"messages": _message_counts, "xp": _xp, "score": _activity_score}.get(mode)
        if mode == "streak":
            ranking = sorted(
                ((uid, _streaks[chat.id].get(uid, {}).get("current", 0)) for uid in members),
                key=lambda kv: kv[1], reverse=True,
            )
        else:
            ranking = sorted(source[chat.id].items(), key=lambda kv: kv[1], reverse=True)
        lines = [f"📊 مرتب‌شده بر اساس {mode}:"]
        for uid, val in ranking[:20]:
            if val <= 0:
                continue
            name = _user_display_names.get(uid, str(uid))
            lines.append(f"• {name} — {val}")

    await update.effective_message.reply_text("\n".join(lines))


# ---------- اقتصاد داخلی گروه ----------

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not get_setting(chat.id, "economy_enabled"):
        await update.effective_message.reply_text("اقتصاد گروه خاموشه. از /menu بخش «اقتصاد و نظرسنجی» روشنش کن.")
        return
    message = update.effective_message
    target_user = message.reply_to_message.from_user if message.reply_to_message else update.effective_user
    bal = _wallet[chat.id].get(target_user.id, 0)
    name = target_user.first_name or target_user.username or "کاربر"
    await message.reply_text(f"{config.CURRENCY_EMOJI} موجودی {name}: {bal} {config.CURRENCY_NAME}")


async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not get_setting(chat.id, "economy_enabled"):
        await update.effective_message.reply_text("اقتصاد گروه خاموشه. از /menu بخش «اقتصاد و نظرسنجی» روشنش کن.")
        return
    user = update.effective_user
    now = time.time()
    last = _last_daily[chat.id].get(user.id, 0)
    cooldown = config.DAILY_COOLDOWN_HOURS * 3600
    if now - last < cooldown:
        remaining_min = int((cooldown - (now - last)) // 60)
        await update.effective_message.reply_text(
            f"جایزه‌ی روزانه رو قبلاً گرفتی. {remaining_min} دقیقه‌ی دیگه دوباره امتحان کن."
        )
        return
    es = _econ_settings[chat.id]
    reward = random.randint(min(es["daily_min"], es["daily_max"]), max(es["daily_min"], es["daily_max"]))
    bonus = economy_engine.daily_streak_bonus(chat.id, user.id)  # فاز ۲: جایزه‌ی اضافه بر اساس Streak پیاپی
    total_reward = reward + bonus
    await economy_core.add_coins(chat.id, user.id, total_reward, kind="REWARD",
                                  note=f"daily +{bonus} جایزه‌ی Streak" if bonus else "daily")
    _last_daily[chat.id][user.id] = now
    bonus_line = f"\n🔥 جایزه‌ی Streak: +{bonus} {config.CURRENCY_EMOJI}" if bonus else ""
    await update.effective_message.reply_text(
        f"🎁 جایزه‌ی روزانه گرفتی: {reward} {config.CURRENCY_EMOJI}{bonus_line}\n"
        f"موجودی الان: {_wallet[chat.id][user.id]} {config.CURRENCY_NAME}"
    )
    await save_state()


_PERSIAN_DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def _normalize_digits(text: str) -> str:
    """ارقام فارسی (۰-۹) رو به انگلیسی تبدیل می‌کنه تا همه‌جا قابل پردازش باشن."""
    return text.translate(_PERSIAN_DIGIT_MAP) if text else text


async def pay_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """انتقال سکه بین اعضا. با ریپلای: /pay <مقدار> یا نوشتن «انتقال سکه <مقدار>».
    بدون ریپلای: /pay <آیدی> <مقدار>. عدد فارسی و انگلیسی هر دو قبول می‌شه."""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not get_setting(chat.id, "economy_enabled"):
        await update.effective_message.reply_text("اقتصاد گروه خاموشه. از /menu بخش «اقتصاد و نظرسنجی» روشنش کن.")
        return
    message = update.effective_message
    sender = update.effective_user
    raw_text = _normalize_digits(message.text or "")
    numbers = [int(n) for n in re.findall(r"\d+", raw_text)]

    target_id = None
    amount = None
    name = None

    if message.reply_to_message and message.reply_to_message.from_user:
        target_user = message.reply_to_message.from_user
        target_id = target_user.id
        name = target_user.first_name or target_user.username or str(target_id)
        if numbers:
            amount = numbers[0]
    elif len(numbers) >= 2:
        target_id = numbers[0]
        amount = numbers[1]
        name = _user_display_names.get(target_id, str(target_id))

    if target_id is None or not amount or amount <= 0:
        await message.reply_text(
            "استفاده: روی پیام کسی ریپلای بزن و بنویس «انتقال سکه <مقدار>» (یا /pay <مقدار>)\n"
            "یا بدون ریپلای: /pay <آیدی عددی> <مقدار>"
        )
        return
    if target_id == sender.id:
        await message.reply_text("نمی‌تونی به خودت پول بفرستی 😅")
        return

    balance = _wallet[chat.id].get(sender.id, 0)
    if balance < amount:
        await message.reply_text(f"موجودیت کافی نیست. موجودی فعلی: {balance} {config.CURRENCY_NAME}")
        return
    limit_error = economy_engine.check_transfer_limit(chat.id, sender.id, amount)  # فاز ۲: سقف انتقال روزانه
    if limit_error:
        await message.reply_text(limit_error)
        return

    try:
        await economy_core.transfer_coins(chat.id, sender.id, target_id, amount, kind="TRANSFER")
    except economy_core.InsufficientFundsError:
        balance = _wallet[chat.id].get(sender.id, 0)
        await message.reply_text(f"موجودیت کافی نیست. موجودی فعلی: {balance} {config.CURRENCY_NAME}")
        return
    economy_engine.record_transfer(chat.id, sender.id, amount)
    sender_name = sender.first_name or sender.username or "کاربر"
    await message.reply_text(f"✅ {amount} {config.CURRENCY_EMOJI} از {sender_name} به {name} منتقل شد.")
    await save_state()


async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not get_setting(chat.id, "economy_enabled"):
        await update.effective_message.reply_text("اقتصاد گروه خاموشه. از /menu بخش «اقتصاد و نظرسنجی» روشنش کن.")
        return
    lines = [f"🛒 فروشگاه گروه (واحد پول: {config.CURRENCY_NAME} {config.CURRENCY_EMOJI})"]
    overrides = _shop_price_overrides[chat.id]
    hidden = _shop_hidden_items[chat.id]
    for key, item in config.SHOP_ITEMS.items():
        if key in hidden:
            continue
        price = overrides.get(key, item["price"])
        lines.append(f"• {item['name']} — {price} {config.CURRENCY_EMOJI}  →  /buy {key}")
    if len(lines) == 1:
        lines.append("فعلاً هیچ آیتمی فعال نیست.")
    await update.effective_message.reply_text("\n".join(lines))


async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not get_setting(chat.id, "economy_enabled"):
        await update.effective_message.reply_text("اقتصاد گروه خاموشه. از /menu بخش «اقتصاد و نظرسنجی» روشنش کن.")
        return
    if not context.args:
        await update.effective_message.reply_text("استفاده: /buy <کد آیتم> (لیست آیتم‌ها با /shop)")
        return
    key = context.args[0]
    item = config.SHOP_ITEMS.get(key)
    if not item or key in _shop_hidden_items[chat.id]:
        # فاز ۳: اگه کد، آیتم فروشگاه نبود ولی نماد بازار (BTC/ETH/...) بود،
        # به‌جای رد کردن، به economy_market.buy_command پاس می‌دیم (بدون این‌که
        # رفتار قبلی /buy <کد فروشگاه> کوچیک‌ترین تغییری بکنه).
        if key.upper() in economy_market.ASSETS:
            await economy_market.buy_command(update, context)
            return
        # فاز ۷: اگه نماد بازار هم نبود، ببین یه کد Inventory 2.0 (Consumable/
        # Collectible) هست یا نه — بازم بدون اینکه رفتار قبلی رو تغییر بده.
        if key in economy_inventory.CATALOG:
            await economy_inventory.buy_command(update, context)
            return
        await update.effective_message.reply_text("همچین آیتمی توی فروشگاه نیست. با /shop لیست رو ببین.")
        return
    user = update.effective_user
    is_timed = "duration_hours" in item  # فاز ۲: آیتم‌های زمان‌دار (Boost/VIP) قابل تمدیدن، قابل خرید تکراری
    if not is_timed and key in _owned_badges[chat.id][user.id]:
        await update.effective_message.reply_text("این آیتم رو قبلاً خریدی.")
        return
    price = _shop_price_overrides[chat.id].get(key, item["price"])
    # فاز ۹: تخفیف رویداد «Shop Discount» (اگه فعال باشه) روی قیمت نهایی اعمال می‌شه
    try:
        price = round(price * economy_events.shop_price_multiplier(chat.id))
    except Exception:
        pass
    balance = _wallet[chat.id].get(user.id, 0)
    if balance < price:
        await update.effective_message.reply_text(f"موجودیت کافی نیست. موجودی فعلی: {balance} {config.CURRENCY_NAME}")
        return
    try:
        await economy_core.remove_coins(chat.id, user.id, price, kind="PURCHASE", note=item["name"])
    except economy_core.InsufficientFundsError:
        balance = _wallet[chat.id].get(user.id, 0)
        await update.effective_message.reply_text(f"موجودیت کافی نیست. موجودی فعلی: {balance} {config.CURRENCY_NAME}")
        return
    if is_timed:
        economy_engine.grant_timed_item(chat.id, user.id, key, item["duration_hours"])
    else:
        _owned_badges[chat.id][user.id].add(key)
    profile_engine.on_shop_purchase(chat.id, user.id, price)
    await update.effective_message.reply_text(f"✅ {item['name']} رو خریدی! توی پروفایلت نشون داده می‌شه.")
    await save_state()


async def myitems_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    # فاز ۲: حالا آیتم‌های زمان‌دار (Boost/VIP) هم با زمان باقی‌مونده نشون داده می‌شن
    text = economy_engine.inventory_text(chat.id, update.effective_user.id)
    # فاز ۷: آیتم‌های قابل‌جمع‌شدن (Consumable/Pet Item/Collectible) هم اضافه می‌شن
    text += economy_inventory.extra_inventory_text(chat.id, update.effective_user.id)
    await update.effective_message.reply_text(text)


# ---------- پنل ویژه‌ی ادمین: تنظیم دستی سکه/XP/لول (تکی یا همه‌ی گروه) + تنظیمات اقتصاد/قرعه‌کشی ----------
# دسترسی این بخش با permission جدا "manage_economy" کنترل می‌شه (توی config.py، ADMIN_PERMISSIONS)

def _require_econ_admin(user_id: int) -> bool:
    return has_permission(user_id, "manage_economy")


def _resolve_target_id(message) -> int | None:
    """آیدی هدف رو از ریپلای یا اولین عدد توی متن پیام برمی‌گردونه."""
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    raw = _normalize_digits(message.text or "")
    numbers = re.findall(r"\d+", raw)
    return int(numbers[0]) if numbers else None


def _last_int_arg(message, allow_negative: bool = False) -> int | None:
    raw = _normalize_digits(message.text or "")
    pattern = r"-?\d+" if allow_negative else r"\d+"
    numbers = re.findall(pattern, raw)
    if not numbers:
        return None
    # وقتی ریپلای نیست، اولین عدد آیدیه و عدد دوم مقداره؛ وقتی ریپلایه، تنها عدد موجود مقداره
    if message.reply_to_message and message.reply_to_message.from_user:
        return int(numbers[0])
    return int(numbers[1]) if len(numbers) >= 2 else None


async def setcoins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ادمین موجودی سکه‌ی یه نفر رو دقیقاً روی یه عدد ست می‌کنه.
    استفاده: ریپلای + /setcoins <مقدار>  یا  /setcoins <آیدی> <مقدار>"""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not _require_econ_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    message = update.effective_message
    target_id = _resolve_target_id(message)
    amount = _last_int_arg(message)
    if target_id is None or amount is None or amount < 0:
        await message.reply_text("استفاده: ریپلای روی پیام کسی + /setcoins <مقدار>  یا  /setcoins <آیدی> <مقدار>")
        return
    await economy_core.admin_set_balance(chat.id, target_id, amount,
                                          note="تنظیم دستی موجودی توسط ادمین",
                                          admin_id=update.effective_user.id)
    name = _user_display_names.get(target_id, str(target_id))
    await message.reply_text(f"✅ موجودی {name} روی {amount} {config.CURRENCY_NAME} تنظیم شد.")
    await save_state()


async def addcoins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ادمین یه مقدار (می‌تونه منفی هم باشه) به سکه‌ی یه نفر اضافه/کم می‌کنه.
    استفاده: ریپلای + /addcoins <مقدار>  یا  /addcoins <آیدی> <مقدار>"""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not _require_econ_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    message = update.effective_message
    target_id = _resolve_target_id(message)
    delta = _last_int_arg(message, allow_negative=True)
    if target_id is None or delta is None:
        await message.reply_text("استفاده: ریپلای روی پیام کسی + /addcoins <مقدار>  یا  /addcoins <آیدی> <مقدار>")
        return
    await economy_core.admin_adjust_balance(chat.id, target_id, delta,
                                             note="تنظیم دستی موجودی توسط ادمین",
                                             admin_id=update.effective_user.id)
    name = _user_display_names.get(target_id, str(target_id))
    await message.reply_text(f"✅ موجودی {name} الان: {_wallet[chat.id][target_id]} {config.CURRENCY_NAME}")
    await save_state()


async def setcoinsall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ادمین موجودی سکه‌ی همه‌ی اعضای شناخته‌شده‌ی گروه رو یکجا روی یه عدد ست می‌کنه."""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not _require_econ_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    raw = _normalize_digits(" ".join(context.args or []))
    if not raw.isdigit():
        await update.effective_message.reply_text("استفاده: /setcoinsall <مقدار>")
        return
    amount = int(raw)
    members = _known_members[chat.id]
    admin_id = update.effective_user.id
    for uid in members:
        await economy_core.admin_set_balance(chat.id, uid, amount, note="setcoinsall", admin_id=admin_id)
    await update.effective_message.reply_text(
        f"✅ موجودی همه‌ی {len(members)} عضو شناخته‌شده روی {amount} {config.CURRENCY_NAME} تنظیم شد."
    )
    await save_state()


async def addcoinsall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ادمین یه مقدار (می‌تونه منفی هم باشه) به سکه‌ی همه‌ی اعضا یکجا اضافه/کم می‌کنه."""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not _require_econ_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    raw = _normalize_digits(" ".join(context.args or []))
    numbers = re.findall(r"-?\d+", raw)
    if not numbers:
        await update.effective_message.reply_text("استفاده: /addcoinsall <مقدار>")
        return
    delta = int(numbers[0])
    members = _known_members[chat.id]
    admin_id = update.effective_user.id
    for uid in members:
        await economy_core.admin_adjust_balance(chat.id, uid, delta, note="addcoinsall", admin_id=admin_id)
    await update.effective_message.reply_text(f"✅ به موجودی همه‌ی {len(members)} عضو، {delta} {config.CURRENCY_NAME} اعمال شد.")
    await save_state()


async def setxp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ادمین XP یه نفر رو دقیقاً روی یه عدد ست می‌کنه (لول ازش محاسبه می‌شه)."""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not _require_econ_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    message = update.effective_message
    target_id = _resolve_target_id(message)
    amount = _last_int_arg(message)
    if target_id is None or amount is None or amount < 0:
        await message.reply_text("استفاده: ریپلای روی پیام کسی + /setxp <مقدار>  یا  /setxp <آیدی> <مقدار>")
        return
    _xp[chat.id][target_id] = amount
    name = _user_display_names.get(target_id, str(target_id))
    level, _, _ = _xp_progress(amount)
    await message.reply_text(f"✅ XP مربوط به {name} روی {amount} تنظیم شد (Level {level}).")
    await save_state()


async def addxp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ادمین یه مقدار (می‌تونه منفی هم باشه) به XP یه نفر اضافه/کم می‌کنه."""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not _require_econ_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    message = update.effective_message
    target_id = _resolve_target_id(message)
    delta = _last_int_arg(message, allow_negative=True)
    if target_id is None or delta is None:
        await message.reply_text("استفاده: ریپلای روی پیام کسی + /addxp <مقدار>  یا  /addxp <آیدی> <مقدار>")
        return
    _xp[chat.id][target_id] = max(0, _xp[chat.id].get(target_id, 0) + delta)
    name = _user_display_names.get(target_id, str(target_id))
    level, _, _ = _xp_progress(_xp[chat.id][target_id])
    await message.reply_text(f"✅ XP مربوط به {name} الان: {_xp[chat.id][target_id]} (Level {level}).")
    await save_state()


async def setxpall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ادمین XP همه‌ی اعضا رو یکجا روی یه عدد ست می‌کنه."""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not _require_econ_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    raw = _normalize_digits(" ".join(context.args or []))
    if not raw.isdigit():
        await update.effective_message.reply_text("استفاده: /setxpall <مقدار>")
        return
    amount = int(raw)
    members = _known_members[chat.id]
    for uid in members:
        _xp[chat.id][uid] = amount
    await update.effective_message.reply_text(f"✅ XP همه‌ی {len(members)} عضو شناخته‌شده روی {amount} تنظیم شد.")
    await save_state()


async def addxpall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ادمین یه مقدار (می‌تونه منفی هم باشه) به XP همه‌ی اعضا یکجا اضافه/کم می‌کنه."""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not _require_econ_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    raw = _normalize_digits(" ".join(context.args or []))
    numbers = re.findall(r"-?\d+", raw)
    if not numbers:
        await update.effective_message.reply_text("استفاده: /addxpall <مقدار>")
        return
    delta = int(numbers[0])
    members = _known_members[chat.id]
    for uid in members:
        _xp[chat.id][uid] = max(0, _xp[chat.id].get(uid, 0) + delta)
    await update.effective_message.reply_text(f"✅ به XP همه‌ی {len(members)} عضو، {delta} اعمال شد.")
    await save_state()


async def setlevel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ادمین لول یه نفر رو مستقیم تنظیم می‌کنه (پشت‌صحنه XP معادلش ست می‌شه)."""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not _require_econ_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    message = update.effective_message
    target_id = _resolve_target_id(message)
    level = _last_int_arg(message)
    if target_id is None or level is None or level < 0:
        await message.reply_text("استفاده: ریپلای روی پیام کسی + /setlevel <لول>  یا  /setlevel <آیدی> <لول>")
        return
    _xp[chat.id][target_id] = level * config.XP_PER_LEVEL
    name = _user_display_names.get(target_id, str(target_id))
    await message.reply_text(f"✅ لول {name} روی {level} تنظیم شد.")
    await save_state()


async def setlevelall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ادمین لول همه‌ی اعضا رو یکجا تنظیم می‌کنه."""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not _require_econ_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    raw = _normalize_digits(" ".join(context.args or []))
    if not raw.isdigit():
        await update.effective_message.reply_text("استفاده: /setlevelall <لول>")
        return
    level = int(raw)
    members = _known_members[chat.id]
    for uid in members:
        _xp[chat.id][uid] = level * config.XP_PER_LEVEL
    await update.effective_message.reply_text(f"✅ لول همه‌ی {len(members)} عضو شناخته‌شده روی {level} تنظیم شد.")
    await save_state()


_ECON_SETTING_KEYS = (
    "daily_min", "daily_max", "activity_min", "activity_max", "xp_min", "xp_max", "lottery_minutes",
)


async def seteco_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تنظیمات خودکار اقتصاد این گروه (پاداش روزانه، سکه‌ی فعالیت، XP هر پیام، مدت پیش‌فرض
    قرعه‌کشی) رو نشون می‌ده یا تغییر می‌ده. استفاده: /seteco  (نمایش) یا  /seteco <کلید> <عدد>"""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not _require_econ_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    es = _econ_settings[chat.id]
    if len(context.args or []) < 2:
        lines = ["⚙️ تنظیمات فعلی اقتصاد خودکار این گروه:"]
        lines += [f"• {k} = {v}" for k, v in es.items()]
        lines.append("\nبرای تغییر: /seteco <کلید> <عدد>\nکلیدهای مجاز: " + ", ".join(_ECON_SETTING_KEYS))
        await update.effective_message.reply_text("\n".join(lines))
        return
    key = context.args[0]
    if key not in _ECON_SETTING_KEYS:
        await update.effective_message.reply_text("کلید نامعتبره. کلیدهای مجاز: " + ", ".join(_ECON_SETTING_KEYS))
        return
    raw = _normalize_digits(context.args[1])
    if not raw.isdigit():
        await update.effective_message.reply_text("مقدار باید یه عدد صحیح (≥۰) باشه.")
        return
    es[key] = int(raw)
    await update.effective_message.reply_text(f"✅ {key} برای این گروه روی {raw} تنظیم شد.")
    await save_state()


async def setshopprice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قیمت یه آیتم فروشگاه رو فقط برای این گروه عوض می‌کنه. استفاده: /setshopprice <کد آیتم> <قیمت>"""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not _require_econ_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    args = context.args or []
    if len(args) < 2 or args[0] not in config.SHOP_ITEMS:
        await update.effective_message.reply_text(
            "استفاده: /setshopprice <کد آیتم> <قیمت جدید>\nکدهای موجود: " + ", ".join(config.SHOP_ITEMS)
        )
        return
    raw = _normalize_digits(args[1])
    if not raw.isdigit():
        await update.effective_message.reply_text("قیمت باید عدد باشه.")
        return
    key = args[0]
    _shop_price_overrides[chat.id][key] = int(raw)
    await update.effective_message.reply_text(
        f"✅ قیمت {config.SHOP_ITEMS[key]['name']} توی این گروه روی {raw} {config.CURRENCY_EMOJI} تنظیم شد."
    )
    await save_state()


# ---------- نظرسنجی (/poll) ----------

def _poll_text(question: str, options: list, votes: dict) -> str:
    counts = [0] * len(options)
    for v in votes.values():
        if 0 <= v < len(options):
            counts[v] += 1
    total = sum(counts)
    lines = [f"🗳️ {question}"]
    for i, opt in enumerate(options):
        pct = int(counts[i] * 100 / total) if total else 0
        bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
        lines.append(f"{opt}: {counts[i]} رأی ({pct}%)\n{bar}")
    lines.append(f"\nمجموع آرا: {total}")
    return "\n".join(lines)


def _poll_keyboard(poll_id: int, options: list) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(opt, callback_data=f"vote:{poll_id}:{i}")] for i, opt in enumerate(options)
    ]
    buttons.append([InlineKeyboardButton("🔚 بستن نظرسنجی", callback_data=f"pollclose:{poll_id}")])
    return InlineKeyboardMarkup(buttons)


async def poll_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not get_setting(chat.id, "poll_enabled"):
        await update.effective_message.reply_text("نظرسنجی خاموشه. از /menu بخش «اقتصاد و نظرسنجی» روشنش کن.")
        return
    arg = _get_command_arg_text(update)
    if not arg:
        await update.effective_message.reply_text(
            "استفاده: /poll <سوال> | <گزینه۱> | <گزینه۲> ...\n"
            "یا فقط: /poll <سوال> (پیش‌فرض بله/خیر)\n\n"
            "تنظیمات اختیاری (هرکدوم رو با | اضافه کن):\n"
            f"• زمان:<مدت> — مثلاً زمان:1h — {config.POLL_DURATION_EXAMPLES}\n"
            "• ناشناس — رأی‌دهنده‌ها مخفی می‌مونن\n"
            "• چندتایی — انتخاب چند گزینه هم‌زمان\n"
            "• جایزه:<عدد> — هر رأی این‌قدر سکه جایزه می‌گیره (فقط یک‌بار برای هر نفر)"
        )
        return

    # فاز ۳: پارس شدن توسط poll_engine؛ بدون هیچ‌کدوم از تنظیمات اضافه، رفتار دقیقاً مثل قبله
    parsed = poll_engine.parse_poll_args(arg)
    poll = poll_engine.build_poll_record(chat.id, update.effective_user.id, parsed)

    sent = await context.bot.send_message(chat_id=chat.id, text=poll_engine.poll_text(poll))
    _polls[sent.message_id] = poll
    try:
        await sent.edit_reply_markup(reply_markup=poll_engine.poll_keyboard(sent.message_id, poll))
    except Exception as e:
        logger.warning(f"ساخت دکمه‌های نظرسنجی ناموفق بود: {e}")
    profile_engine.on_poll_created(chat.id, update.effective_user.id)
    await save_state()


async def poll_vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""

    if data.startswith("pollclose:"):
        poll_id = int(data.split(":")[1])
        poll = _polls.get(poll_id)
        if not poll:
            await query.answer("این نظرسنجی دیگه در دسترس نیست.")
            return
        if not is_admin(query.from_user.id) and not has_permission(query.from_user.id, "moderate") and poll.get("creator_id") != query.from_user.id:
            await query.answer("فقط سازنده‌ی نظرسنجی یا ادمین می‌تونه ببنددش.", show_alert=True)
            return
        await query.answer("نظرسنجی بسته شد.")
        closed = poll_engine.close_poll(poll_id, reason="manual")
        try:
            await query.edit_message_text(poll_engine.poll_text(closed))
        except Exception:
            pass
        await save_state()
        return

    _, poll_id_str, idx_str = data.split(":")
    poll_id = int(poll_id_str)
    idx = int(idx_str)
    poll = _polls.get(poll_id)
    if not poll:
        await query.answer("این نظرسنجی دیگه در دسترس نیست.")
        return
    is_new_vote = query.from_user.id not in poll["votes"]
    answer_text = poll_engine.record_vote(poll, query.from_user.id, idx)  # فاز ۳: تک/چندتایی + جایزه، همه‌جا از این‌جا
    if is_new_vote:
        profile_engine.on_poll_voted(poll["chat_id"], query.from_user.id)
    await query.answer(answer_text)
    try:
        await query.edit_message_text(
            poll_engine.poll_text(poll),
            reply_markup=poll_engine.poll_keyboard(poll_id, poll),
        )
    except Exception as e:
        logger.warning(f"آپدیت نظرسنجی ناموفق بود: {e}")


# ---------- قرعه‌کشی (/lottery) ----------

def _lottery_text(prize, participants_count: int, minutes_left) -> str:
    prize_line = f"🎁 جایزه: {prize}\n" if prize else ""
    return (
        f"🎉 قرعه‌کشی فعاله!\n{prize_line}"
        f"👥 شرکت‌کننده‌ها: {participants_count}\n"
        f"⏳ حدود {minutes_left} دقیقه‌ی دیگه فرصت داری.\n"
        f"روی دکمه‌ی زیر بزن تا وارد قرعه‌کشی بشی."
    )


def _lottery_keyboard(message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎉 شرکت در قرعه‌کشی", callback_data=f"lotteryjoin:{message_id}")]]
    )


async def lottery_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "moderate"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if chat.id in _lotteries:
        await update.effective_message.reply_text("یه قرعه‌کشی همین الان توی این گروه فعاله.")
        return

    args = context.args or []
    minutes = _econ_settings[chat.id]["lottery_minutes"]
    prize = None
    if args:
        if args[0].isdigit():
            minutes = max(1, int(args[0]))
            prize = " ".join(args[1:]) or None
        else:
            prize = " ".join(args)

    sent = await context.bot.send_message(chat_id=chat.id, text=_lottery_text(prize, 0, minutes))
    try:
        await sent.edit_reply_markup(reply_markup=_lottery_keyboard(sent.message_id))
    except Exception as e:
        logger.warning(f"ساخت دکمه‌ی قرعه‌کشی ناموفق بود: {e}")
    _lotteries[chat.id] = {
        "message_id": sent.message_id,
        "end_ts": time.time() + minutes * 60,
        "participants": set(),
        "prize": prize,
    }


async def lottery_end_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "moderate"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    if chat_id not in _lotteries:
        await update.effective_message.reply_text("هیچ قرعه‌کشی فعالی توی این گروه نیست.")
        return
    await _finish_lottery(chat_id, context.bot)


async def lottery_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, msg_id_str = (query.data or "").split(":")
    msg_id = int(msg_id_str)
    chat_id = query.message.chat_id
    lot = _lotteries.get(chat_id)
    if not lot or lot["message_id"] != msg_id:
        await query.answer("این قرعه‌کشی دیگه فعال نیست.")
        return
    user = query.from_user
    if user.id in lot["participants"]:
        await query.answer("قبلاً شرکت کردی!")
        return
    lot["participants"].add(user.id)
    _user_display_names[user.id] = user.first_name or user.username or str(user.id)
    await query.answer("✅ شرکت کردی، موفق باشی!")
    minutes_left = max(0, int((lot["end_ts"] - time.time()) // 60))
    try:
        await query.edit_message_text(
            _lottery_text(lot["prize"], len(lot["participants"]), minutes_left),
            reply_markup=_lottery_keyboard(msg_id),
        )
    except Exception as e:
        logger.warning(f"آپدیت قرعه‌کشی ناموفق بود: {e}")


async def _finish_lottery(chat_id: int, bot):
    lot = _lotteries.pop(chat_id, None)
    if not lot:
        return
    if not lot["participants"]:
        text = "🎉 قرعه‌کشی تموم شد ولی هیچ‌کس شرکت نکرد."
    else:
        winner_id = random.choice(list(lot["participants"]))
        winner_name = _user_display_names.get(winner_id, str(winner_id))
        prize_line = f"\n🎁 جایزه: {lot['prize']}" if lot["prize"] else ""
        text = (
            f"🎉 قرعه‌کشی تموم شد!\n"
            f"🏆 برنده: {winner_name}{prize_line}\n"
            f"👥 مجموع شرکت‌کننده‌ها: {len(lot['participants'])}"
        )
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        logger.warning(f"اعلام برنده‌ی قرعه‌کشی ناموفق بود: {e}")


async def _check_lottery_expiry():
    if not _bot_instance:
        return
    now = time.time()
    for chat_id, lot in list(_lotteries.items()):
        if lot["end_ts"] <= now:
            await _finish_lottery(chat_id, _bot_instance)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return

    target_user = (
        message.reply_to_message.from_user
        if message.reply_to_message
        else update.effective_user
    )
    uid = target_user.id
    count = _message_counts[chat.id].get(uid, 0)
    media_count = _media_counts[chat.id].get(uid, 0)
    text_count = _text_message_counts[chat.id].get(uid, 0)
    avg_length = (
        _text_length_totals[chat.id].get(uid, 0) / text_count if text_count else 0
    )
    name = target_user.first_name or target_user.username or "کاربر"

    await message.reply_text(
        f"📊 آمار {name}:\n"
        f"• تعداد کل پیام‌ها: {count}\n"
        f"• میانگین طول پیام متنی: {avg_length:.0f} کاراکتر\n"
        f"• تعداد مدیا/استیکر: {media_count}"
    )


async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return

    counts = _message_counts[chat.id]
    if not counts:
        await message.reply_text("هنوز آماری ثبت نشده.")
        return

    top_users = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[
        : config.TOP_ACTIVE_COUNT
    ]
    lines = ["🏆 فعال‌ترین اعضای گروه:"]
    for i, (user_id, count) in enumerate(top_users, start=1):
        name = _user_display_names.get(user_id, str(user_id))
        lines.append(f"{i}. {name} — {count} پیام")
    await message.reply_text("\n".join(lines))


async def group_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return

    counts = _message_counts[chat.id]
    total_messages = sum(counts.values())
    total_members = len(counts)

    lines = [
        "📈 آمار کلی گروه:",
        f"• تعداد کل پیام‌های ثبت‌شده: {total_messages}",
        f"• تعداد اعضای فعال (حداقل یه پیام): {total_members}",
    ]

    hours = _hour_activity[chat.id]
    if hours:
        busiest_hour = max(hours.items(), key=lambda kv: kv[1])[0]
        lines.append(f"• فعال‌ترین ساعت: {busiest_hour}:00 تا {busiest_hour + 1}:00")

    weekdays = _weekday_activity[chat.id]
    if weekdays:
        busiest_day = max(weekdays.items(), key=lambda kv: kv[1])[0]
        lines.append(f"• فعال‌ترین روز هفته: {WEEKDAY_NAMES_FA[busiest_day]}")

    jl = _join_leave_stats[chat.id]
    if any(jl.values()):
        lines.append("\n👋 ورود/خروج (از وقتی ربات این ویژگی رو داره):")
        lines.append(f"• ورود اعضای جدید: {jl['joins']}")
        lines.append(f"• خروج خودخواسته: {jl['leaves']}")
        lines.append(f"• اخراج/بن: {jl['kicks']}")
        if jl["bot_joins"]:
            lines.append(f"• ورود ربات‌های دیگه: {jl['bot_joins']}")
        if jl["unbans"]:
            lines.append(f"• آن‌بن: {jl['unbans']}")

    if get_setting(chat.id, "stats_enabled"):
        active, low, inactive = _activity_group_tiers(chat.id)
        msgs_today = sum(_activity_sum(chat.id, uid, [_activity_today_str()]) for uid in _known_members[chat.id])
        msgs_week = sum(_activity_sum(chat.id, uid, _activity_week_dates(0)) for uid in _known_members[chat.id])
        msgs_month = sum(_activity_sum(chat.id, uid, _activity_month_dates(0)) for uid in _known_members[chat.id])
        total_xp = sum(_xp[chat.id].values())
        streak_users = sum(1 for s in _streaks[chat.id].values() if s.get("current", 0) >= 1)
        growth = _activity_group_trend_percent(chat.id)
        growth_text = f"{'+' if growth is not None and growth >= 0 else ''}{growth}%" if growth is not None else "داده کافی نیست"

        lines.append(
            "\n╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
            "𓆩 📊 𝐆𝐑𝐎𝐔𝐏 𝐀𝐂𝐓𝐈𝐕𝐈𝐓𝐘 𓆪\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
            f"👥 اعضای شناخته‌شده: {len(_known_members[chat.id])}\n\n"
            f"💬 امروز: {msgs_today}\n"
            f"📆 این هفته: {msgs_week}\n"
            f"🗓 این ماه: {msgs_month}\n\n"
            f"🟢 فعال: {active}\n"
            f"🟡 کم‌فعالیت: {low}\n"
            f"🔴 غیرفعال: {inactive}\n\n"
            f"⭐ مجموع XP: {total_xp}\n"
            f"🔥 کاربران با Streak فعال: {streak_users}\n"
            f"📈 روند (این هفته نسبت به هفته قبل): {growth_text}\n\n"
            "    𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪"
        )

    await message.reply_text("\n".join(lines))


_PLACEHOLDER_PREVIEW_DATA = {
    "full_name": "نمونه کاربر",
    "username_line": "┃ 🔗 یوزرنیم: @sample\n",
    "user_id": 123456789,
    "date": "1404/06/19",
    "time": "12:00:00",
    "member_count": 42,
    "duration_line": "┃ ⌛ مدت حضور: 2 روز و 3 ساعت\n",
}


async def welcome_text_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """متن سفارشی خوش‌آمدگویی این گروه رو تنظیم/حذف می‌کنه.
    استفاده: /welcome_text <متن دلخواه>  یا  /welcome_text ریست (برگشت به پیام‌های تصادفی پیش‌فرض)"""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not has_permission(update.effective_user.id, "menu"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    message = update.effective_message
    raw = (message.text or "").split(maxsplit=1)
    text = raw[1].strip() if len(raw) > 1 else ""
    if not text or text in ("ریست", "reset"):
        _custom_welcome_text.pop(chat.id, None)
        await message.reply_text("✅ متن خوش‌آمدگویی این گروه به حالت پیش‌فرض (تصادفی) برگشت.")
        await save_state()
        return
    preview = _safe_format(text, _PLACEHOLDER_PREVIEW_DATA)
    _custom_welcome_text[chat.id] = text
    await message.reply_text(
        "✅ متن خوش‌آمدگویی این گروه تنظیم شد. پیش‌نمایش با داده‌ی نمونه:\n\n" + preview
    )
    await save_state()


async def leave_text_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """متن سفارشی خروج (فقط برای خروج خودخواسته، نه اخراج/بن) این گروه رو تنظیم/حذف می‌کنه.
    استفاده: /leave_text <متن دلخواه>  یا  /leave_text ریست (برگشت به پیام‌های تصادفی پیش‌فرض)"""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not has_permission(update.effective_user.id, "menu"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    message = update.effective_message
    raw = (message.text or "").split(maxsplit=1)
    text = raw[1].strip() if len(raw) > 1 else ""
    if not text or text in ("ریست", "reset"):
        _custom_leave_text.pop(chat.id, None)
        await message.reply_text("✅ متن خروج این گروه به حالت پیش‌فرض (تصادفی) برگشت.")
        await save_state()
        return
    preview = _safe_format(text, _PLACEHOLDER_PREVIEW_DATA)
    _custom_leave_text[chat.id] = text
    await message.reply_text(
        "✅ متن خروجِ خودخواسته‌ی این گروه تنظیم شد (پیام اخراج/بن جدا و ثابت می‌مونه).\n"
        "پیش‌نمایش با داده‌ی نمونه:\n\n" + preview
    )
    await save_state()


async def welcome_theme_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تمِ ظاهری پیام‌های ورود/خروج این گروه رو نشون می‌ده یا عوض می‌کنه.
    استفاده: /welcome_theme  (نمایش لیست) یا  /welcome_theme <کلید تم>"""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not has_permission(update.effective_user.id, "menu"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    current = _chat_theme.get(chat.id, config.DEFAULT_MESSAGE_THEME)
    args = context.args or []
    if not args:
        lines = ["🎨 تم‌های موجود برای پیام‌های ورود/خروج:"]
        for key, theme in config.MESSAGE_THEMES.items():
            mark = "✅" if key == current else "▫️"
            lines.append(f"{mark} {theme['label']} → /welcome_theme {key}")
        await update.effective_message.reply_text("\n".join(lines))
        return
    key = args[0].strip().lower()
    if key not in config.MESSAGE_THEMES:
        await update.effective_message.reply_text(
            "همچین تمی نداریم. کلیدهای معتبر: " + ", ".join(config.MESSAGE_THEMES)
        )
        return
    _chat_theme[chat.id] = key
    await update.effective_message.reply_text(f"✅ تم پیام‌های ورود/خروج این گروه شد: {config.MESSAGE_THEMES[key]['label']}")
    await save_state()


async def welcome_toggle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """میانبر روی همون تنظیم welcome_enabled (دقیقاً معادل روشن/خاموش‌کردنش از /menu).
    استفاده: /welcome on  یا  /welcome off"""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not has_permission(update.effective_user.id, "menu"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    args = [a.lower() for a in (context.args or [])]
    if not args or args[0] not in ("on", "off", "روشن", "خاموش"):
        state_now = "روشنه ✅" if _chat_settings[chat.id].get("welcome_enabled", False) else "خاموشه ❌"
        await update.effective_message.reply_text(f"وضعیت فعلی خوش‌آمدگویی: {state_now}\nاستفاده: /welcome on یا /welcome off")
        return
    new_state = args[0] in ("on", "روشن")
    _chat_settings[chat.id]["welcome_enabled"] = new_state
    await update.effective_message.reply_text("✅ خوش‌آمدگویی روشن شد." if new_state else "✅ خوش‌آمدگویی خاموش شد.")
    await save_state()


async def leave_toggle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """میانبر روی همون تنظیم leave_enabled. استفاده: /leave on  یا  /leave off"""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not has_permission(update.effective_user.id, "menu"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    args = [a.lower() for a in (context.args or [])]
    if not args or args[0] not in ("on", "off", "روشن", "خاموش"):
        state_now = "روشنه ✅" if _chat_settings[chat.id].get("leave_enabled", False) else "خاموشه ❌"
        await update.effective_message.reply_text(f"وضعیت فعلی پیام ترک گروه: {state_now}\nاستفاده: /leave on یا /leave off")
        return
    new_state = args[0] in ("on", "روشن")
    _chat_settings[chat.id]["leave_enabled"] = new_state
    await update.effective_message.reply_text("✅ پیام ترک گروه روشن شد." if new_state else "✅ پیام ترک گروه خاموش شد.")
    await save_state()


async def groups_overview_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمای کلی از همه‌ی گروه‌هایی که ربات توشونه (فقط برای ادمین، معمولاً توی چت خصوصی استفاده می‌شه)."""
    if not has_permission(update.effective_user.id, "menu"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return

    if not _known_chats:
        await update.effective_message.reply_text("ربات هنوز توی هیچ گروهی فعالیتی نداشته.")
        return

    lines = ["📈 گروه‌های شناخته‌شده:"]
    for chat_id in _known_chats:
        settings = _chat_settings[chat_id]
        state = "روشن ✅" if settings.get("bot_enabled", False) else "خاموش ⛔"
        total_messages = sum(_message_counts[chat_id].values())
        try:
            chat_info = await context.bot.get_chat(chat_id)
            title = chat_info.title or str(chat_id)
        except Exception:
            title = str(chat_id)
        lines.append(f"• {title} — ربات: {state} — {total_messages} پیام ثبت‌شده")

    await update.effective_message.reply_text("\n".join(lines))


# ---------- ۶.۵) اخطار/میوت دستی ----------

def _parse_target_and_reason(update: Update):
    """آیدی هدف + دلیل رو یا از ریپلای+متن بعدش، از آرگومان اول (آیدی عددی)، یا از
    @username (با استفاده از دیکشنری _username_to_id) + بقیه می‌گیره."""
    message = update.effective_message
    parts = message.text.split() if message.text else []
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        target_id = u.id
        name = u.first_name or u.username or str(u.id)
        reason = " ".join(parts[1:]) if len(parts) > 1 else None
        return target_id, name, reason
    if len(parts) >= 2 and parts[1].isdigit():
        target_id = int(parts[1])
        name = _user_display_names.get(target_id, parts[1])
        reason = " ".join(parts[2:]) if len(parts) > 2 else None
        return target_id, name, reason
    if len(parts) >= 2 and parts[1].startswith("@"):
        uname = parts[1][1:].lower()
        target_id = _username_to_id.get(uname)
        if target_id is not None:
            name = _user_display_names.get(target_id, parts[1])
            reason = " ".join(parts[2:]) if len(parts) > 2 else None
            return target_id, name, reason
    return None, None, None


_DURATION_RE = re.compile(r"^(\d+)([mhd])$", re.IGNORECASE)


def _parse_duration_seconds(token: str | None):
    """'10m' / '2h' / '7d' -> ثانیه. اگه فرمت نامعتبر بود None برمی‌گردونه."""
    if not token:
        return None
    m = _DURATION_RE.match(token.strip())
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2).lower()
    if value <= 0:
        return None
    multiplier = {"m": 60, "h": 3600, "d": 86400}[unit]
    return value * multiplier


async def warn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "moderate"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    target_id, name, reason = _parse_target_and_reason(update)
    if target_id is None:
        await update.effective_message.reply_text(
            "استفاده: ریپلای رو پیام کاربر بزن، یا: /warn <آیدی> [دلیل اختیاری]"
        )
        return
    await _warn_and_maybe_mute(
        chat_id, target_id, name, context, reason or "اخطار دستی از طرف ادمین",
        admin_id=update.effective_user.id, admin_name=update.effective_user.first_name, source="MANUAL",
    )


async def setwarnlimit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setwarnlimit <عدد> - تعداد اخطار قبل از میوت رو برای این گروه، به هر عددی که بخوای تنظیم می‌کنه."""
    if not has_permission(update.effective_user.id, "moderate"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    if not context.args or not context.args[0].isdigit() or int(context.args[0]) < 1:
        await update.effective_message.reply_text(
            f"استفاده: /setwarnlimit <عدد بزرگ‌تر از صفر>\nمقدار فعلی: {_warn_limit[update.effective_chat.id]}"
        )
        return
    value = int(context.args[0])
    _warn_limit[update.effective_chat.id] = value
    await update.effective_message.reply_text(f"✅ حد اخطار قبل از میوت روی {value} تنظیم شد.")
    await save_state()


async def setmutetime_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setmutetime <دقیقه> - مدت میوت پایه رو برای این گروه، به هر عددی (به دقیقه) که بخوای تنظیم می‌کنه.
    (هر بار که یه نفر دوباره میوت بشه، این مدت طبق MAX_MUTE_MINUTES دوبرابر می‌شه - این رفتار عوض نشده)"""
    if not has_permission(update.effective_user.id, "moderate"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    if not context.args or not context.args[0].isdigit() or int(context.args[0]) < 1:
        await update.effective_message.reply_text(
            f"استفاده: /setmutetime <عدد به دقیقه، بزرگ‌تر از صفر>\nمقدار فعلی: {_mute_minutes[update.effective_chat.id]} دقیقه"
        )
        return
    value = int(context.args[0])
    _mute_minutes[update.effective_chat.id] = value
    await update.effective_message.reply_text(f"✅ مدت میوت پایه روی {value} دقیقه تنظیم شد.")
    await save_state()


async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/mute <ریپلای یا آیدی یا @username> [10m|2h|7d یا دقیقه‌ی خام] [دلیل]
    بدون مدت مشخص: میوت تصاعدی طبق config.MODERATION_MUTE_LADDER_MINUTES."""
    if not has_permission(update.effective_user.id, "moderate"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    target_id, name, extra = _parse_target_and_reason(update)
    if target_id is None:
        await update.effective_message.reply_text(
            "استفاده: ریپلای رو پیام کاربر بزن، یا: /mute <آیدی/@username> [10m|2h|7d] [دلیل]"
        )
        return
    if moderation_engine.is_protected(chat_id, target_id):
        await update.effective_message.reply_text("این کاربر محافظت‌شده‌ست (ادمین/VIP) و نمی‌شه میوتش کرد.")
        return

    duration_seconds = None
    reason = extra
    if extra:
        first_tok = extra.strip().split()[0]
        duration_seconds = _parse_duration_seconds(first_tok)
        if duration_seconds is None and first_tok.isdigit():
            duration_seconds = int(first_tok) * 60  # سازگاری با سینتکس قدیمی (فقط عدد = دقیقه)
        if duration_seconds is not None:
            reason = " ".join(extra.strip().split()[1:]) or None

    result = await moderation_engine.mute(
        context, chat_id, target_id, name,
        duration_seconds=duration_seconds, reason=reason or "میوت دستی",
        admin_id=update.effective_user.id, admin_name=update.effective_user.first_name,
        source="MANUAL",
    )
    if not result.get("ok"):
        await update.effective_message.reply_text(
            "میوت انجام نشد؛ مطمئن شو ربات دسترسی Restrict Members داره."
        )
        return
    await save_state()


async def unmute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "moderate"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    target_id, name, _ = _parse_target_and_reason(update)
    if target_id is None:
        await update.effective_message.reply_text(
            "استفاده: ریپلای رو پیام کاربر بزن، یا: /unmute <آیدی/@username>"
        )
        return
    result = await moderation_engine.unmute(
        context, chat_id, target_id, name,
        admin_id=update.effective_user.id, admin_name=update.effective_user.first_name, source="MANUAL",
    )
    if not result.get("ok"):
        await update.effective_message.reply_text("آن‌میوت انجام نشد؛ مطمئن شو ربات دسترسی Restrict Members داره.")
        return
    await save_state()


async def resetwarnings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "moderate"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    target_id, name, _ = _parse_target_and_reason(update)
    if target_id is None:
        await update.effective_message.reply_text(
            "استفاده: ریپلای رو پیام کاربر بزن، یا: /resetwarnings <آیدی>"
        )
        return
    _warnings[chat_id][target_id] = 0
    _warning_expiry[chat_id][target_id] = []
    _mute_counts[chat_id][target_id] = 0
    moderation_engine.clear_warns(chat_id, target_id)
    await update.effective_message.reply_text(f"اخطارها و سابقه‌ی میوت {name} صفر شد.")
    await save_state()


async def unwarn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """یه اخطار از کاربر کم می‌کنه (بدون صفر کردن کامل)."""
    if not has_permission(update.effective_user.id, "moderate"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    target_id, name, _ = _parse_target_and_reason(update)
    if target_id is None:
        await update.effective_message.reply_text(
            "استفاده: ریپلای رو پیام کاربر بزن، یا: /unwarn <آیدی>"
        )
        return
    if _warning_expiry[chat_id][target_id]:
        _warning_expiry[chat_id][target_id].pop()
    _prune_warnings(chat_id, target_id)
    count = moderation_engine.unwarn_one(chat_id, target_id)
    limit = _warn_limit[chat_id]
    await update.effective_message.reply_text(
        f"یه اخطار از {name} کم شد. الان: {count} از {limit}"
    )
    await save_state()


async def warnings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    message = update.effective_message
    target_user = (
        message.reply_to_message.from_user if message.reply_to_message else update.effective_user
    )
    count = _prune_warnings(chat_id, target_user.id)
    name = target_user.first_name or target_user.username or "کاربر"
    limit = _warn_limit[chat_id]
    text = f"⚠️ {name}: {count} از {limit} اخطار معتبر."
    reason = _last_warning_reason[chat_id].get(target_user.id)
    if reason and count:
        text += f"\nآخرین دلیل: {reason}"
    await message.reply_text(text)


async def muted_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    now = time.time()
    active = {uid: until for uid, until in _active_mutes[chat_id].items() if until > now}
    if not active:
        await update.effective_message.reply_text("الان کسی میوت نیست.")
        return
    lines = ["🔇 کاربرای میوت‌شده الان:"]
    for uid, until in active.items():
        remaining_min = int((until - now) / 60) + 1
        name = _user_display_names.get(uid, str(uid))
        lines.append(f"• {name} — {remaining_min} دقیقه مونده")
    await update.effective_message.reply_text("\n".join(lines))


# ---------- بن / آن‌بن ----------

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/ban <ریپلای یا آیدی یا @username> [7d|30d] [دلیل] — بدون مدت = بن دائمی."""
    if not has_permission(update.effective_user.id, "moderate"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    target_id, name, extra = _parse_target_and_reason(update)
    if target_id is None:
        await update.effective_message.reply_text(
            "استفاده: ریپلای رو پیام کاربر بزن، یا: /ban <آیدی/@username> [7d|30d] [دلیل]"
        )
        return
    if moderation_engine.is_protected(chat_id, target_id):
        await update.effective_message.reply_text("این کاربر محافظت‌شده‌ست (ادمین/VIP) و نمی‌شه بنش کرد.")
        return

    duration_seconds = None
    reason = extra
    if extra:
        first_tok = extra.strip().split()[0]
        duration_seconds = _parse_duration_seconds(first_tok)
        if duration_seconds is not None:
            reason = " ".join(extra.strip().split()[1:]) or None

    if name and not str(name).isdigit():
        _user_display_names[target_id] = name

    result = await moderation_engine.ban(
        context, chat_id, target_id, name,
        duration_seconds=duration_seconds, reason=reason or "بن دستی",
        admin_id=update.effective_user.id, admin_name=update.effective_user.first_name,
        source="MANUAL",
    )
    if not result.get("ok"):
        await update.effective_message.reply_text("بن انجام نشد؛ مطمئن شو ربات دسترسی Ban Users داره.")
        return
    await save_state()


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "moderate"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    target_id, name, _ = _parse_target_and_reason(update)
    if target_id is None:
        await update.effective_message.reply_text("استفاده: /unban <آیدی عددی/@username>")
        return
    result = await moderation_engine.unban(
        context, chat_id, target_id, name,
        admin_id=update.effective_user.id, admin_name=update.effective_user.first_name, source="MANUAL",
    )
    if not result.get("ok"):
        await update.effective_message.reply_text("آن‌بن انجام نشد.")
        return
    await save_state()


async def banlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ids = _banned_users[chat_id]
    if not ids:
        await update.effective_message.reply_text("لیست بن خالیه.")
        return
    names = [_user_display_names.get(uid, str(uid)) for uid in ids]
    await update.effective_message.reply_text("🚫 لیست بن‌شده‌ها:\n" + "\n".join(names))


async def clearbanlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """توجه: این فقط لیستی که خودمون نگه می‌داریم رو پاک می‌کنه، کسی رو آن‌بن نمی‌کنه."""
    if not has_permission(update.effective_user.id, "moderate"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    _banned_users[chat_id] = set()
    await update.effective_message.reply_text(
        "لیست بن (فقط نمایشی) پاک شد. توجه: هیچ‌کس آن‌بن نشد، فقط این لیست خالی شد."
    )
    await save_state()


# ---------- 🛡️ Professional Moderation Engine: تاریخچه، آمار و لیست‌های زنده ----------

async def modhistory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/modhistory <ریپلای یا آیدی یا @username> - تاریخچه‌ی کامل Warn/Mute/Ban + Risk Score."""
    chat_id = update.effective_chat.id
    message = update.effective_message
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        target_id, name = u.id, (u.first_name or u.username or str(u.id))
    else:
        target_id, name, _ = _parse_target_and_reason(update)
        if target_id is None:
            target_id, name = update.effective_user.id, update.effective_user.first_name
    text = moderation_engine.format_modhistory(chat_id, target_id, name)
    await message.reply_text(text)


async def modstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/modstats - آمار کلی مدیریت گروه (Warn/Mute/Ban، فعال‌ها، پرتخلف‌ترین‌ها، آمار ادمین‌ها)."""
    chat_id = update.effective_chat.id
    await update.effective_message.reply_text(moderation_engine.format_modstats(chat_id))


async def mutes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/mutes - لیست میوت‌های فعال با دلیل و زمان باقی‌مونده (نسخه‌ی کامل‌تر /muted)."""
    chat_id = update.effective_chat.id
    now = time.time()
    active = moderation_engine.list_active_mutes(chat_id)
    if not active:
        await update.effective_message.reply_text("الان کسی میوت نیست.")
        return
    lines = ["🔇 کاربرای میوت‌شده الان:"]
    for uid, m in active:
        name = _user_display_names.get(uid, str(uid))
        until = m.get("until")
        remaining = "دائمی" if not until else f"{int((until - now) / 60) + 1} دقیقه مونده"
        lines.append(f"• {name} — {remaining} — دلیل: {m.get('reason') or '—'}")
    await update.effective_message.reply_text("\n".join(lines))


async def bans_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/bans - لیست بن‌های فعال با دلیل و نوع (موقت/دائم) (نسخه‌ی کامل‌تر /banlist)."""
    chat_id = update.effective_chat.id
    now = time.time()
    active = moderation_engine.list_active_bans(chat_id)
    if not active:
        await update.effective_message.reply_text("لیست بن خالیه.")
        return
    lines = ["🚫 کاربرای بن‌شده الان:"]
    for uid, b in active:
        name = _user_display_names.get(uid, str(uid))
        until = b.get("until")
        kind = "دائمی" if not until else f"تا {int((until - now) / 86400) + 1} روز دیگه"
        lines.append(f"• {name} — {kind} — دلیل: {b.get('reason') or '—'}")
    await update.effective_message.reply_text("\n".join(lines))


async def setmodconfig_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setmodconfig - نمایش تنظیمات فعلی موتور مدیریت (نردبان تخلف، آستانه‌های ریسک و...).
    برای تغییر این مقادیر، طبق قرارداد پروژه (مثل ADMIN_PERMISSIONS و SHOP_ITEMS)، بلوک
    MODERATION_* رو توی config.py ویرایش کن؛ بعد از سیو، فقط کافیه ربات ری‌استارت بشه."""
    if not has_permission(update.effective_user.id, "moderate"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    ladder = getattr(config, "MODERATION_ESCALATION_LADDER", [])
    ladder_txt = "\n".join(
        f"  {i+1}. تخلف #{s['count']} → {s['action'].upper()}"
        + (f" ({s.get('minutes')} دقیقه)" if s.get("minutes") else "")
        + (f" ({s.get('days')} روز)" if s.get("days") else "")
        for i, s in enumerate(ladder)
    )
    thresholds = getattr(config, "MODERATION_RISK_THRESHOLDS", {})
    text = (
        "⚙️ تنظیمات فعلی Moderation Engine\n\n"
        "📈 نردبان تصاعدی تخلف:\n" + (ladder_txt or "  —") + "\n\n"
        f"📊 آستانه‌های Risk Score: {thresholds}\n\n"
        "برای تغییر، بخش MODERATION_* رو توی config.py ویرایش کن و ربات رو ری‌استارت کن."
    )
    await update.effective_message.reply_text(text)


# ---------- ارتقا / عزل ادمین ----------

async def promote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "manage_admins"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    target_id, name, title = _parse_target_and_reason(update)
    if target_id is None:
        await update.effective_message.reply_text(
            "استفاده: ریپلای رو پیام کاربر بزن، یا: /promote <آیدی> [لقب دلخواه]"
        )
        return

    # چک اولیه: خود ربات باید توی این گروه ادمین باشه و دقیقاً دسترسی
    # «Add New Admins» (can_promote_members) بهش داده شده باشه - این یه دسترسی جداست
    # و صرفاً «ادمین بودن» ربات کافی نیست، باید موقع ادمین کردن ربات این گزینه‌ی خاص هم تیک بخوره.
    try:
        bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
        if getattr(bot_member, "can_promote_members", False) is not True:
            await update.effective_message.reply_text(
                "ربات دسترسی «Add New Admins» رو نداره.\n"
                "توی تنظیمات ادمین‌های گروه، برو روی خود ربات و مطمئن شو گزینه‌ی "
                "«Add New Admins» (اضافه کردن ادمین جدید) براش روشنه - صرفاً ادمین بودن کافی نیست، "
                "باید همین گزینه‌ی خاص هم فعال باشه."
            )
            return
    except Exception as e:
        logger.warning(f"چک دسترسی ربات ناموفق بود: {e}")

    try:
        await context.bot.promote_chat_member(
            chat_id=chat_id,
            user_id=target_id,
            can_delete_messages=True,
            can_restrict_members=True,
            can_invite_users=True,
            can_pin_messages=True,
            can_manage_chat=True,
            can_promote_members=True,
        )
        if title:
            try:
                await context.bot.set_chat_administrator_custom_title(
                    chat_id=chat_id, user_id=target_id, custom_title=title[:16]
                )
            except Exception as e:
                logger.warning(f"تنظیم لقب ادمین ناموفق بود: {e}")
        text = f"👑 {name} ادمین گروه شد."
        if title:
            text += f" (لقب: {title[:16]})"
        await update.effective_message.reply_text(text)
        await _log_admin_action(
            context, chat_id, f"👑 Promote\n👤 {name}\n🧑‍💼 توسط: {update.effective_user.first_name}"
        )
    except Exception as e:
        logger.warning(f"ارتقا ناموفق بود: {e}")
        await update.effective_message.reply_text(f"ارتقا انجام نشد. خطای تلگرام: {e}")


async def demote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "manage_admins"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    target_id, name, _ = _parse_target_and_reason(update)
    if target_id is None:
        await update.effective_message.reply_text(
            "استفاده: ریپلای رو پیام کاربر بزن، یا: /demote <آیدی>"
        )
        return
    try:
        await context.bot.promote_chat_member(
            chat_id=chat_id,
            user_id=target_id,
            can_delete_messages=False,
            can_restrict_members=False,
            can_invite_users=False,
            can_pin_messages=False,
            can_manage_chat=False,
        )
        await update.effective_message.reply_text(f"{name} از ادمینی عزل شد.")
        await _log_admin_action(
            context, chat_id, f"⬇️ Demote\n👤 {name}\n🧑‍💼 توسط: {update.effective_user.first_name}"
        )
    except Exception as e:
        logger.warning(f"عزل ناموفق بود: {e}")
        await update.effective_message.reply_text("عزل انجام نشد.")


# ---------- پاک‌سازی پیام‌ها ----------
# منطق کامل (Parser فیلترها، کش پیام‌ها، Preview/Confirmation، آمار، حذف امن) توی
# purge_engine.py پیاده شده - اینجا فقط Handlerهای نازک ثبت‌شده در PTB هستن.
# بدون آرگومان، رفتار قبلیِ /purge (ریپلای‌محور) دقیقاً حفظ شده.

async def purge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """با ریپلای روی یه پیام و زدن /purge (یا «پاکسازی»)، همه‌ی پیام‌های بین اون تا
    الان پاک می‌شه. با آرگومان (تعداد/زمان/نوع/لینک/متن/...) سیستم پاکسازی پیشرفته
    فعال می‌شه. جزئیات کامل: /purge help."""
    await purge_engine.purge_command(update, context)


async def purge_preview_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معادل «پیش‌نمایش پاکسازی» / «/purge preview [فیلترهای دیگه]»."""
    context.args = ["preview"] + list(context.args or [])
    await purge_engine.purge_command(update, context)


async def purgeuser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """با ریپلای روی پیام یه کاربر، پیام‌های همون کاربر رو پاک می‌کنه.
    /purgeuser [تعداد|بازه‌زمانی] یا «پاکسازی کاربر [تعداد|بازه‌زمانی]»."""
    await purge_engine.purgeuser_command(update, context)


async def purgestats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """آمار عملیات‌های پاکسازی این گروه. /purgestats یا «آمار پاکسازی»."""
    await purge_engine.purgestats_command(update, context)


# ---------- کاربر ویژه (VIP) ----------

async def vip_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "moderate"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    target_id, name = _parse_target_from_command(update)
    if target_id is None:
        await update.effective_message.reply_text(
            "استفاده: ریپلای رو پیام کاربر بزن، یا: /addvip <آیدی عددی>"
        )
        return
    _vip_users[chat_id].add(target_id)
    await update.effective_message.reply_text(f"⭐ {name} کاربر ویژه شد (از قفل‌ها/ضداسپم مستثناست).")
    await save_state()


async def vip_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "moderate"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    target_id, name = _parse_target_from_command(update)
    if target_id is None:
        await update.effective_message.reply_text(
            "استفاده: ریپلای رو پیام کاربر بزن، یا: /removevip <آیدی عددی>"
        )
        return
    _vip_users[chat_id].discard(target_id)
    await update.effective_message.reply_text(f"{name} از کاربرای ویژه حذف شد.")
    await save_state()


async def vip_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ids = _vip_users[chat_id]
    if not ids:
        await update.effective_message.reply_text("لیست کاربرای ویژه خالیه.")
        return
    names = [_user_display_names.get(uid, str(uid)) for uid in ids]
    await update.effective_message.reply_text("⭐ کاربرای ویژه:\n" + "\n".join(names))


# ---------- سکوت زمان‌دار متنی («سکوت ۲۶» به‌جای /mute) ----------

TIMED_MUTE_PATTERN = re.compile(r"^سکوت\s+(\d+)$")


async def timed_mute_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    if not message or not message.text or chat.type not in ("group", "supergroup"):
        return
    if not _chat_settings[chat.id].get("bot_enabled", False):
        return
    m = TIMED_MUTE_PATTERN.match(message.text.strip())
    if not m:
        return
    if not message.reply_to_message or not message.reply_to_message.from_user:
        return
    if not has_permission(update.effective_user.id, "moderate"):
        await message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        raise ApplicationHandlerStop

    minutes = int(m.group(1))
    target = message.reply_to_message.from_user
    try:
        until = int(time.time()) + minutes * 60
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=target.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )
        _active_mutes[chat.id][target.id] = until
        name = target.first_name or target.username or "کاربر"
        await message.reply_text(f"🔇 {name} برای {minutes} دقیقه میوت شد.")
        await save_state()
    except Exception as e:
        logger.warning(f"میوت زمان‌دار ناموفق بود: {e}")
    raise ApplicationHandlerStop


# ---------- تازه‌سازی لینک گروه ----------

async def newlink_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "menu"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    try:
        new_link = await context.bot.export_chat_invite_link(chat_id)
        await update.effective_message.reply_text(f"🔗 لینک جدید گروه (لینک قبلی باطل شد):\n{new_link}")
    except Exception as e:
        logger.warning(f"تازه‌سازی لینک ناموفق بود: {e}")
        await update.effective_message.reply_text(
            "تازه‌سازی لینک انجام نشد؛ مطمئن شو ربات دسترسی «Invite Users via Link» داره."
        )


# ---------- امکانات جانبی/سرگرمی ----------

async def joke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _ai_client is None:
        await update.effective_message.reply_text("این قابلیت نیاز به تنظیم کلید Groq داره.")
        return
    joke = await ask_ai(config.AI_JOKE_PROMPT)
    await update.effective_message.reply_text(joke or "چیزی به ذهنم نرسید، دوباره امتحان کن 😅")


async def fortune_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _ai_client is None:
        await update.effective_message.reply_text("این قابلیت نیاز به تنظیم کلید Groq داره.")
        return
    fortune = await ask_ai(config.AI_FORTUNE_PROMPT)
    await update.effective_message.reply_text(fortune or "فال امروز رو نتونستم بگیرم، دوباره امتحان کن 😅")


# ---------- ۶) بلاک‌لیست دستی ----------

async def blacklist_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "manage_blacklist"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    target_id, name = _parse_target_from_command(update)
    if target_id is None:
        await update.effective_message.reply_text(
            "استفاده: ریپلای رو پیام کاربر بزن، یا: /blacklist_add <آیدی عددی>"
        )
        return
    _blacklist[chat_id].add(target_id)
    await update.effective_message.reply_text(f"🚫 {name} به بلاک‌لیست اضافه شد.")
    await save_state()


async def blacklist_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "manage_blacklist"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat_id = update.effective_chat.id
    target_id, name = _parse_target_from_command(update)
    if target_id is None:
        await update.effective_message.reply_text(
            "استفاده: ریپلای رو پیام کاربر بزن، یا: /blacklist_remove <آیدی عددی>"
        )
        return
    _blacklist[chat_id].discard(target_id)
    await update.effective_message.reply_text(f"{name} از بلاک‌لیست حذف شد.")
    await save_state()


# ---------- ۷) منوی تنظیمات (روشن/خاموش با دکمه) ----------

# زیربخش‌های داخلی برای دسته‌هایی که تعداد قابلیت‌شون زیاده (قفل‌ها/ضداسپم)، تا صفحه شلوغ نشه.
# مجموع کلیدهای هر زیربخش، دقیقاً همون کلیدهای دسته‌ی اصلی توی config.SETTING_CATEGORIES هست؛
# هیچ قابلیتی حذف/اضافه نشده، فقط برای نمایش گروه‌بندی شده.
_SETTING_SUBGROUPS = {
    "locks": {
        "messages": {
            "title": "💬 پیام و متن",
            "keys": ["antispam_words", "lock_tags", "lock_hashtags", "lock_text", "lock_long_message", "lock_inline"],
        },
        "media": {
            "title": "🖼 رسانه",
            "keys": [
                "lock_media", "lock_photo", "lock_video", "lock_document",
                "lock_audio", "lock_voice", "lock_all_stickers", "lock_all_gifs",
            ],
        },
        "links": {
            "title": "🔗 لینک و اشتراک‌گذاری",
            "keys": ["lock_links", "lock_contacts", "lock_location", "lock_forward", "lock_poll", "lock_game"],
        },
        "advanced": {
            "title": "🔧 پیشرفته",
            "keys": ["lock_bots", "lock_edited", "lock_group"],
        },
    },
    "antispam": {
        "quick": {
            "title": "🚀 محافظت سریع",
            "keys": [
                "antiflood_text", "anticaps", "antisticker_flood",
                "antiforward_channel", "antinew_account_links", "member_protection_enabled",
            ],
        },
        "smart": {
            "title": "🧠 تشخیص هوشمند",
            "keys": [
                "antispam_duplicate", "antispam_mentions", "antispam_hashtags",
                "antispam_emoji", "antispam_ads", "antispam_edit",
            ],
        },
        "advanced": {
            "title": "🔧 پیشرفته (فلود رسانه)",
            "keys": [
                "antispam_forward_flood", "antispam_mixed_flood", "antispam_link_flood",
                "antispam_photo_flood", "antispam_video_flood", "antispam_voice_flood",
                "antispam_audio_flood", "antispam_document_flood", "antispam_contact_flood",
                "antispam_location_flood", "antispam_poll_flood",
            ],
        },
    },
}

# نگاشت معکوس: هر کلید توی کدوم (دسته، زیربخش) هست — برای اینکه بعد از toggle، همون زیرصفحه دوباره نشون داده بشه
_KEY_TO_SUBGROUP = {
    key: (cat_key, sub_key)
    for cat_key, subs in _SETTING_SUBGROUPS.items()
    for sub_key, sub in subs.items()
    for key in sub["keys"]
}


def _subgroup_progress(chat_id: int, cat_key: str, sub_key: str):
    keys = _SETTING_SUBGROUPS[cat_key][sub_key]["keys"]
    settings = _chat_settings[chat_id]
    active = sum(1 for k in keys if settings.get(k, False))
    return active, len(keys)


# نگاشت معکوس: هر کلید تنظیمات بولی توی کدوم دسته‌ست (برای اینکه بعد از toggle، همون زیرمنو رو دوباره نشون بدیم)
_KEY_TO_CATEGORY = {
    key: cat_key
    for cat_key, cat in config.SETTING_CATEGORIES.items()
    for key in cat["keys"]
}

# دسته‌هایی که شمارش «فعال/کل» براشون معنی نداره (بدون کلید بولی، بلکه زیرپنل اختصاصی دارن)
_CATEGORIES_WITHOUT_TOGGLES = {"moderation", "econ_admin"}

# کلیدهای امنیتی (قفل‌ها + ضداسپم) که برای محاسبه‌ی «درصد امنیت» داشبورد استفاده می‌شن
_SECURITY_KEYS = tuple(
    key
    for cat_key in ("locks", "antispam")
    for key in config.SETTING_CATEGORIES.get(cat_key, {}).get("keys", [])
)


def _pair_rows(buttons: list) -> list:
    """یه لیست از دکمه‌ها رو به ردیف‌های دوتایی (دو ستونه) تبدیل می‌کنه."""
    rows = []
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i + 2])
    return rows


def _category_progress(chat_id: int, cat_key: str):
    """تعداد قابلیت‌های فعال از کل قابلیت‌های بولی یه دسته رو برمی‌گردونه: (active, total)."""
    cat = config.SETTING_CATEGORIES.get(cat_key, {})
    keys = cat.get("keys", [])
    if not keys:
        return None
    settings = _chat_settings[chat_id]
    active = sum(1 for k in keys if settings.get(k, False))
    return active, len(keys)


def _status_dot(active: int, total: int) -> str:
    if total == 0:
        return "⚪"
    if active == 0:
        return "🔴"
    if active == total:
        return "🟢"
    return "🟡"


def _security_score(chat_id: int) -> int:
    if not _SECURITY_KEYS:
        return 0
    settings = _chat_settings[chat_id]
    active = sum(1 for k in _SECURITY_KEYS if settings.get(k, False))
    return round((active / len(_SECURITY_KEYS)) * 100)


def _progress_bar(fraction: float, width: int = 10) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled)


# ---------- ورودی دنباله‌دار پنل (Pending Panel Input) ----------
# بعضی دکمه‌های پنل (افزودن دشمن، بن سریع، ویرایش متن خوش‌آمد و ...) نیاز به یه ورودی متنی دارن
# که توی دکمه‌ی شیشه‌ای نمی‌شه گرفت. به‌جای ConversationHandler سنگین، یه دیکشنری ساده نگه می‌داریم:
# کلید = user_id، مقدار = اکشن مورد نظر + گروه هدف + زمان انقضا. بعد از ارسال پیام بعدی توسط
# همون کاربر (یا لغو/انقضا)، پاک می‌شه. منطق دستورات اصلی (permission، پیام‌ها) دست‌نخورده می‌مونه؛
# این فقط یه راه دومه برای همون توابع.
_PENDING_INPUT_TTL_SECONDS = 180
_pending_panel_input: dict = {}

_PANEL_ASK_PROMPTS = {
    "friend_add": "🤝 کی رو می‌خوای دوست کنی؟\nپیام طرف رو فوروارد کن یا آیدی عددیش رو بفرست.",
    "friend_remove": "کدوم دوست حذف بشه؟\nفوروارد کن یا آیدی عددی بفرست.",
    "enemy_add": "⚔️ کی رو می‌خوای دشمن کنی؟\nفوروارد کن یا آیدی عددی بفرست. برای دشمنی موقت، بعدش مدت رو هم بنویس، مثلاً: 12345 24h",
    "enemy_remove": "کدوم دشمن حذف بشه؟\nفوروارد کن یا آیدی عددی بفرست.",
    "vip_add": "⭐ کی VIP بشه؟\nفوروارد کن یا آیدی عددی بفرست.",
    "vip_remove": "کدوم VIP حذف بشه؟\nفوروارد کن یا آیدی عددی بفرست.",
    "blacklist_add": "🛑 کی بره توی بلاک‌لیست؟\nفوروارد کن یا آیدی عددی بفرست.",
    "blacklist_remove": "کی از بلاک‌لیست بیاد بیرون؟\nفوروارد کن یا آیدی عددی بفرست.",
    "promote": "👑 کی ادمین بشه؟\nفوروارد کن یا آیدی عددی بفرست؛ برای لقب دلخواه، بعدش بنویس، مثلاً: 12345 مدیر ارشد",
    "demote": "کدوم ادمین عزل بشه؟\nفوروارد کن یا آیدی عددی بفرست.",
    "mute_quick": "🔇 کی میوت بشه؟\nفوروارد یا آیدی + اختیاری مدت/دلیل، مثلاً: 12345 30m توهین",
    "ban_quick": "🚫 کی بن بشه؟\nفوروارد یا آیدی + اختیاری مدت/دلیل، مثلاً: 12345 7d اسپم",
    "notify": "📢 متن اطلاعیه رو بفرست تا برای همه‌ی گروه‌ها ارسال بشه.",
    "welcome_text": "✏️ متن خوش‌آمدگویی این گروه رو بفرست (یا «ریست» برای پیش‌فرض).",
    "leave_text": "✏️ متن خروج این گروه رو بفرست (یا «ریست» برای پیش‌فرض).",
}


def _panel_ask_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="pnd:cancel")]])


def _set_pending_input(user_id: int, chat_id: int, action: str):
    _pending_panel_input[user_id] = {
        "action": action,
        "chat_id": chat_id,
        "expires": time.time() + _PENDING_INPUT_TTL_SECONDS,
    }


def _pop_pending_input(user_id: int):
    info = _pending_panel_input.pop(user_id, None)
    if not info:
        return None
    if info["expires"] < time.time():
        return None
    return info


def _parse_panel_target(message):
    """آیدی هدف + بقیه‌ی متن (مدت/دلیل/لقب) رو از یه پیام‌ ورودیِ پنل در میاره:
    فوروارد از یه نفر، آیدی عددی، یا @username (اگه قبلاً دیده شده باشه)."""
    fwd_user = getattr(message, "forward_from", None)
    if fwd_user:
        rest = (message.text or message.caption or "").strip() or None
        return fwd_user.id, (fwd_user.first_name or fwd_user.username or str(fwd_user.id)), rest
    text = (message.text or message.caption or "").strip()
    if not text:
        return None, None, None
    tokens = text.split()
    first = tokens[0]
    rest = " ".join(tokens[1:]) or None
    if first.isdigit():
        target_id = int(first)
        return target_id, _user_display_names.get(target_id, first), rest
    if first.startswith("@"):
        uname = first[1:].lower()
        target_id = _username_to_id.get(uname)
        if target_id is not None:
            return target_id, _user_display_names.get(target_id, first), rest
    return None, None, None


async def _execute_pending_panel_action(update: Update, context: ContextTypes.DEFAULT_TYPE, info: dict):
    message = update.effective_message
    user = update.effective_user
    action = info["action"]
    chat_id = info["chat_id"]

    async def _reply(text: str):
        await message.reply_text(text)

    if action in ("friend_add", "friend_remove", "enemy_add", "enemy_remove",
                  "vip_add", "vip_remove", "blacklist_add", "blacklist_remove",
                  "promote", "demote", "mute_quick", "ban_quick"):
        target_id, name, rest = _parse_panel_target(message)
        if target_id is None:
            await _reply("متوجه نشدم. پیام طرف رو فوروارد کن یا آیدی عددیش رو بفرست. (لغو شد؛ دوباره از پنل بزن)")
            return

    if action == "friend_add":
        if not has_permission(user.id, "manage_relationships"):
            await _reply("این اکشن فقط برای ادمین‌های مجازه.")
            return
        _friends[chat_id].add(target_id)
        _enemies[chat_id].discard(target_id)
        await _reply(f"🤝 {name} به لیست دوست‌ها اضافه شد.")
        await save_state()

    elif action == "friend_remove":
        if not has_permission(user.id, "manage_relationships"):
            await _reply("این اکشن فقط برای ادمین‌های مجازه.")
            return
        _friends[chat_id].discard(target_id)
        await _reply(f"{name} از لیست دوست‌ها حذف شد.")
        await save_state()

    elif action == "enemy_add":
        if not has_permission(user.id, "manage_relationships"):
            await _reply("این اکشن فقط برای ادمین‌های مجازه.")
            return
        _enemies[chat_id].add(target_id)
        _friends[chat_id].discard(target_id)
        duration = _parse_duration_token(rest.split()[-1]) if rest else None
        if duration:
            _enemy_expiry[chat_id][target_id] = time.time() + duration
            note = f" (موقت، {_format_duration_short(duration)} دیگه خودکار لغو می‌شه)"
        else:
            _enemy_expiry[chat_id].pop(target_id, None)
            note = ""
        await _reply(f"⚔️ {name} به لیست دشمن‌ها اضافه شد.{note}")
        await save_state()

    elif action == "enemy_remove":
        if not has_permission(user.id, "manage_relationships"):
            await _reply("این اکشن فقط برای ادمین‌های مجازه.")
            return
        _enemies[chat_id].discard(target_id)
        _enemy_expiry[chat_id].pop(target_id, None)
        await _reply(f"{name} از لیست دشمن‌ها حذف شد.")
        await save_state()

    elif action == "vip_add":
        if not has_permission(user.id, "moderate"):
            await _reply("این اکشن فقط برای ادمین‌های مجازه.")
            return
        _vip_users[chat_id].add(target_id)
        await _reply(f"⭐ {name} کاربر ویژه شد (از قفل‌ها/ضداسپم مستثناست).")
        await save_state()

    elif action == "vip_remove":
        if not has_permission(user.id, "moderate"):
            await _reply("این اکشن فقط برای ادمین‌های مجازه.")
            return
        _vip_users[chat_id].discard(target_id)
        await _reply(f"{name} از کاربرای ویژه حذف شد.")
        await save_state()

    elif action == "blacklist_add":
        if not has_permission(user.id, "manage_blacklist"):
            await _reply("این اکشن فقط برای ادمین‌های مجازه.")
            return
        _blacklist[chat_id].add(target_id)
        await _reply(f"🚫 {name} به بلاک‌لیست اضافه شد.")
        await save_state()

    elif action == "blacklist_remove":
        if not has_permission(user.id, "manage_blacklist"):
            await _reply("این اکشن فقط برای ادمین‌های مجازه.")
            return
        _blacklist[chat_id].discard(target_id)
        await _reply(f"{name} از بلاک‌لیست حذف شد.")
        await save_state()

    elif action == "promote":
        if not has_permission(user.id, "manage_admins"):
            await _reply("این اکشن فقط برای ادمین‌های مجازه.")
            return
        title = rest[:16] if rest else None
        try:
            bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
            if getattr(bot_member, "can_promote_members", False) is not True:
                await _reply(
                    "ربات دسترسی «Add New Admins» رو توی این گروه نداره؛ اول اونو فعال کن."
                )
                return
        except Exception as e:
            logger.warning(f"چک دسترسی ربات ناموفق بود: {e}")
        try:
            await context.bot.promote_chat_member(
                chat_id=chat_id, user_id=target_id,
                can_delete_messages=True, can_restrict_members=True, can_invite_users=True,
                can_pin_messages=True, can_manage_chat=True, can_promote_members=True,
            )
            if title:
                try:
                    await context.bot.set_chat_administrator_custom_title(
                        chat_id=chat_id, user_id=target_id, custom_title=title
                    )
                except Exception as e:
                    logger.warning(f"تنظیم لقب ادمین ناموفق بود: {e}")
            text = f"👑 {name} ادمین گروه شد."
            if title:
                text += f" (لقب: {title})"
            await _reply(text)
            await _log_admin_action(context, chat_id, f"👑 Promote (از پنل)\n👤 {name}\n🧑‍💼 توسط: {user.first_name}")
        except Exception as e:
            logger.warning(f"ارتقا ناموفق بود: {e}")
            await _reply(f"ارتقا انجام نشد. خطای تلگرام: {e}")

    elif action == "demote":
        if not has_permission(user.id, "manage_admins"):
            await _reply("این اکشن فقط برای ادمین‌های مجازه.")
            return
        try:
            await context.bot.promote_chat_member(
                chat_id=chat_id, user_id=target_id,
                can_delete_messages=False, can_restrict_members=False, can_invite_users=False,
                can_pin_messages=False, can_manage_chat=False,
            )
            await _reply(f"{name} از ادمینی عزل شد.")
            await _log_admin_action(context, chat_id, f"⬇️ Demote (از پنل)\n👤 {name}\n🧑‍💼 توسط: {user.first_name}")
        except Exception as e:
            logger.warning(f"عزل ناموفق بود: {e}")
            await _reply("عزل انجام نشد.")

    elif action == "mute_quick":
        if not has_permission(user.id, "moderate"):
            await _reply("این اکشن فقط برای ادمین‌های مجازه.")
            return
        if moderation_engine.is_protected(chat_id, target_id):
            await _reply("این کاربر محافظت‌شده‌ست (ادمین/VIP) و نمی‌شه میوتش کرد.")
            return
        duration_seconds, reason = None, rest
        if rest:
            first_tok = rest.strip().split()[0]
            duration_seconds = _parse_duration_seconds(first_tok)
            if duration_seconds is None and first_tok.isdigit():
                duration_seconds = int(first_tok) * 60
            if duration_seconds is not None:
                reason = " ".join(rest.strip().split()[1:]) or None
        result = await moderation_engine.mute(
            context, chat_id, target_id, name,
            duration_seconds=duration_seconds, reason=reason or "میوت از پنل",
            admin_id=user.id, admin_name=user.first_name, source="PANEL",
        )
        if not result.get("ok"):
            await _reply("میوت انجام نشد؛ مطمئن شو ربات دسترسی Restrict Members داره.")
            return
        await _reply(f"🔇 {name} میوت شد.")
        await save_state()

    elif action == "ban_quick":
        if not has_permission(user.id, "moderate"):
            await _reply("این اکشن فقط برای ادمین‌های مجازه.")
            return
        if moderation_engine.is_protected(chat_id, target_id):
            await _reply("این کاربر محافظت‌شده‌ست (ادمین/VIP) و نمی‌شه بنش کرد.")
            return
        duration_seconds, reason = None, rest
        if rest:
            first_tok = rest.strip().split()[0]
            duration_seconds = _parse_duration_seconds(first_tok)
            if duration_seconds is not None:
                reason = " ".join(rest.strip().split()[1:]) or None
        if name and not str(name).isdigit():
            _user_display_names[target_id] = name
        result = await moderation_engine.ban(
            context, chat_id, target_id, name,
            duration_seconds=duration_seconds, reason=reason or "بن از پنل",
            admin_id=user.id, admin_name=user.first_name, source="PANEL",
        )
        if not result.get("ok"):
            await _reply("بن انجام نشد؛ مطمئن شو ربات دسترسی Ban Users داره.")
            return
        await _reply(f"🚫 {name} بن شد.")
        await save_state()

    elif action == "notify":
        if not has_permission(user.id, "notify"):
            await _reply("این اکشن فقط برای ادمین‌های مجازه.")
            return
        text = (message.text or "").strip()
        if not text:
            await _reply("متن خالی بود؛ لغو شد.")
            return
        sent, failed = 0, 0
        for cid in list(_known_chats):
            try:
                await context.bot.send_message(chat_id=cid, text=f"📢 اطلاعیه:\n{text}")
                sent += 1
            except Exception as e:
                logger.warning(f"ارسال به چت {cid} ناموفق بود: {e}")
                failed += 1
        await _reply(f"ارسال شد به {sent} چت. ({failed} مورد ناموفق)")

    elif action in ("welcome_text", "leave_text"):
        if not has_permission(user.id, "menu"):
            await _reply("این اکشن فقط برای ادمین‌های مجازه.")
            return
        text = (message.text or "").strip()
        store = _custom_welcome_text if action == "welcome_text" else _custom_leave_text
        label = "خوش‌آمدگویی" if action == "welcome_text" else "خروج"
        if not text or text in ("ریست", "reset"):
            store.pop(chat_id, None)
            await _reply(f"✅ متن {label} این گروه به حالت پیش‌فرض (تصادفی) برگشت.")
            await save_state()
            return
        preview = _safe_format(text, _PLACEHOLDER_PREVIEW_DATA)
        store[chat_id] = text
        await _reply(f"✅ متن {label} این گروه تنظیم شد. پیش‌نمایش:\n\n{preview}")
        await save_state()


async def pending_panel_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اگه کاربر یه اکشن پنل معلق داشته باشه (بعد از زدن دکمه‌ای مثل «افزودن دشمن»)،
    همین پیام بعدیش رو به‌جای پردازش عادی، به‌عنوان ورودی همون اکشن مصرف می‌کنه."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    info = _pending_panel_input.get(user.id)
    if not info:
        return
    if info["expires"] < time.time():
        _pending_panel_input.pop(user.id, None)
        return
    if message.chat.type != "private" and message.chat_id != info["chat_id"]:
        return
    _pending_panel_input.pop(user.id, None)
    await _execute_pending_panel_action(update, context, info)
    raise ApplicationHandlerStop


def _build_main_menu_markup(chat_id: int) -> InlineKeyboardMarkup:
    buttons = []
    for cat_key, cat in config.SETTING_CATEGORIES.items():
        progress = _category_progress(chat_id, cat_key)
        if progress is None:
            label = cat["title"]
        else:
            active, total = progress
            label = f"{_status_dot(active, total)} {cat['title']} • {active}/{total}"
        buttons.append(InlineKeyboardButton(label, callback_data=f"panel:{cat_key}"))

    rows = _pair_rows(buttons)
    rows.append(
        [
            InlineKeyboardButton("📊 وضعیت گروه", callback_data="menu_status"),
            InlineKeyboardButton("📖 راهنمای دستورات", callback_data="menu_help"),
        ]
    )
    rows.append([InlineKeyboardButton("❌ بستن", callback_data="menu_close")])
    return InlineKeyboardMarkup(rows)


def _build_help_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔙 بازگشت", callback_data="menu_back"),
                InlineKeyboardButton("❌ بستن", callback_data="menu_close"),
            ]
        ]
    )


# ═══════════════════════════════════════════════════════════════════════════
# 📖 HELP CENTER — سیستم راهنمای بخش‌بندی‌شده (جایگزین متن غول‌پیکر قبلی)
# هر بخش دستورهای خودش رو (فارسی + انگلیسی با هم) جدا نشون می‌ده؛ به‌جای یه
# متن یکجا که هم از سقف طول پیام تلگرام رد می‌شد هم پیدا کردن یه دستور توش
# سخت بود، الان با دکمه بین بخش‌ها جابه‌جا می‌شی.
# ═══════════════════════════════════════════════════════════════════════════

HELP_HEADER = (
    "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
    "      𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪\n"
    "       𝐇𝐄𝐋𝐏 𝐂𝐄𝐍𝐓𝐄𝐑\n"
    "╰━━━━━━━━━━━━━━━━━━━━━━╯\n"
    "        𝐎𝐖𝐍𝐄𝐑 : 𝐌𝐑𝐗\n"
)

# key: (عنوان دکمه, فقط‌ادمین؟, متن بخش)
HELP_SECTIONS = {
    "general": ("🌐 همگانی", False,
        "┏━━━『 🌐 همگانی 』━━━┓\n"
        "┃ /start «شروع» • /help «راهنما»\n"
        "┃ /stats «آمار» • /top «فعال ترین ها»\n"
        "┃ /groupstats «آمار گروه»\n"
        "┃ /relations «روابط» › یکجای دوست/دشمن\n"
        "┃ /enemies «دشمن ها» • /friends «دوست ها»\n"
        "┃ /enemywords «کلمات دشمن»\n"
        "┃ /friendwords «کلمات دوست»\n"
        "┃ /warnings «اخطارهای من»\n"
        "┃ /muted «میوت شده ها» • /banlist «لیست بن»\n"
        "┃ /viplist «لیست وی آی پی»\n"
        "┃ /joke «جوک» • /fortune «فال»\n"
        "┗━━━━━━━━━━━━━━━━━━━━┛"),
    "activity": ("📊 فعالیت هوشمند", False,
        "┏━━━『 📊 𝐒𝐌𝐀𝐑𝐓 𝐀𝐂𝐓𝐈𝐕𝐈𝐓𝐘 』━━━┓\n"
        "┃ /activity «فعالیت من» [@یوزرنیم|آیدی]\n"
        "┃ › XP، لول، رتبه، Streak، امتیاز، الگوی رفتاری\n"
        "┃ /leaderboard «برترین های فعالیت»\n"
        "┃ [today|week|month|alltime] [messages|xp|score]\n"
        "┃ /achievements «دستاوردهای من» [@یوزرنیم|آیدی]\n"
        "┃ /activityfilter «فیلتر فعالیت» (ادمین)\n"
        "┃ inactive|low|messages|xp|streak|score\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
    "profile": ("👤 پروفایل • XP • لقب", False,
        "┏━━━『 👤 پروفایل • XP • لقب 』━━━┓\n"
        "┃ /profile «پروفایل من» › پروفایل کامل\n"
        "┃ /rank «رتبه من» • /level «سطح من»\n"
        "┃ /toplevel «برترین ها» › بر اساس XP\n"
        "┃ /setlqab «تنظیم لقب» <لقب>\n"
        "┃ /removelqab «حذف لقب»\n"
        "┃ /lqab «لقب» • /toplqab «لیست لقب ها»\n"
        "┃ /rep «احترام» <ریپلای>\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
    "poll": ("🗳️ نظرسنجی • قرعه‌کشی", False,
        "┏━━━『 🗳️ نظرسنجی • قرعه‌کشی 』━━━┓\n"
        "┃ /poll «نظرسنجی جدید» <سوال> | <گزینه۱> | ...\n"
        "┃ › بدون گزینه = بله / خیر\n"
        "┃ /pollclose «بستن نظرسنجی»\n"
        "┃ /polldelete «حذف نظرسنجی»\n"
        "┃ /pollreopen «بازکردن نظرسنجی»\n"
        "┃ /pollvoters «رای دهندگان»\n"
        "┃ /pollhide «مخفی نظرسنجی»\n"
        "┃ /pollhistory «تاریخچه نظرسنجی»\n"
        "┃ /lottery «قرعه کشی» [دقیقه] [جایزه]\n"
        "┃ /lottery_end «پایان قرعه کشی»\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
    "friend_enemy": ("⚔️ دوست • دشمن", False,
        "┏━━━『 ⚔️ دوست • دشمن 』━━━┓\n"
        "┃ /addenemy «دشمن کن» • /addfriend «دوست کن»\n"
        "┃ /removeenemy «لغو دشمن کن»\n"
        "┃ /removefriend «لغو دوست کن»\n"
        "┃ /addenemyword «اضافه کلمه دشمن»\n"
        "┃ /removeenemyword «حذف کلمه دشمن»\n"
        "┃ /addfriendword «اضافه کلمه دوست»\n"
        "┃ /removefriendword «حذف کلمه دوست»\n"
        "┃ › ریپلای + «دشمن»/«دوست» هم ثبت می‌کنه\n"
        "┃ › دشمنی موقت: «دشمن 24h» (واحد m/h/d)\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
    "welcome": ("👋 ورود • خروج", False,
        "┏━━━『 👋 ورود • خروج 』━━━┓\n"
        "┃ /welcome «خوشامد» on|off\n"
        "┃ /leave «ترک» on|off\n"
        "┃ /welcome_theme «تم خوشامد» [کلید تم]\n"
        "┃ /welcome_text «متن خوشامد» <متن یا «ریست»>\n"
        "┃ /leave_text «متن ترک» <متن یا «ریست»>\n"
        "┃ جای‌گزین‌های متن: {full_name} {username_line}\n"
        "┃ {user_id} {date} {time} {member_count}\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
    "tag": ("🏷️ تگ دسته‌جمعی", False,
        "┏━━━『 🏷️ تگ دسته‌جمعی 』━━━┓\n"
        "┃ /tag «تگ» [متن دلخواه]\n"
        "┃ › یا نوشتن «تگ همه»\n"
        "┃ ⚠️ فعال‌سازی از /menu\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
    "modctl": ("👮 مدیریت کاربر", True,
        "┏━━━『 👮 مدیریت کاربر 』━━━┓\n"
        "┃ /warn «اخطار» • /unwarn «لغو اخطار»\n"
        "┃ /resetwarnings «ریست اخطارها»\n"
        "┃ /setwarnlimit «تنظیم حد اخطار» <عدد>\n"
        "┃ /setmutetime «تنظیم مدت میوت» <دقیقه>\n"
        "┃ /mute «سکوت» • /unmute «رفع سکوت»\n"
        "┃ › «سکوت ۲۶» با ریپلای\n"
        "┃ /ban «بن» • /unban «رفع بن»\n"
        "┃ /clearbanlist «پاک کردن لیست بن»\n"
        "┃ /addvip «وی‌آی‌پی کن» • /removevip «حذف وی‌آی‌پی»\n"
        "┃ /blacklist_add «افزودن لیست سیاه»\n"
        "┃ /blacklist_remove «حذف لیست سیاه»\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
    "adminx": ("👑 ادمین‌ها", True,
        "┏━━━『 👑 ادمین‌ها 』━━━┓\n"
        "┃ /promote «ارتقا» <آیدی یا ریپلای> [لقب]\n"
        "┃ /demote «عزل» <آیدی یا ریپلای>\n"
        "┗━━━━━━━━━━━━━━━━━━━━┛"),
    "group": ("🧹 گروه", True,
        "┏━━━『 🧹 گروه 』━━━┓\n"
        "┃ /purge «پاکسازی» › راهنمای کامل: /purge help\n"
        "┃ /purgeuser «پاکسازی کاربر» (ریپلای)\n"
        "┃ /purgestats «آمار پاکسازی»\n"
        "┃ /newlink «لینک جدید»\n"
        "┃ /notify «اطلاعیه» <متن>\n"
        "┃ /groups «لیست گروه ها»\n"
        "┗━━━━━━━━━━━━━━━━━━━━┛"),
    "terminator": ("☠️ Terminator 2.0", True,
        "┏━━━『 ☠️ TERMINATOR 2.0 』━━━┓\n"
        "┃ /terminator «ترمیناتور» active|silent|stats|mode\n"
        "┃ /enemyinfo «اطلاعات دشمن» <ریپلای>\n"
        "┃ /friendinfo «اطلاعات دوست» <ریپلای>\n"
        "┃ /targetmode «مد هدف» <ریپلای> <مد>\n"
        "┃ /targetcooldown «کول داون هدف» <ریپلای> <ثانیه>\n"
        "┃ مدها: sarcastic, funny, cold, smart, roast, random\n"
        "┃ AI هوشمند: /menu → «دوست/دشمن» → «پاسخ هوشمند AI»\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
    "locks": ("🔒 قفل‌ها • ضداسپم", True,
        "┏━━━『 🔒 قفل‌ها • ضداسپم 』━━━┓\n"
        "┃ /lock «قفل» • /unlock «بازکردن قفل»\n"
        "┃ /locks «لیست قفل ها» • /lockinfo «اطلاعات قفل»\n"
        "┃ /lockstats «آمار قفل» • /lockconfig «تنظیم قفل»\n"
        "┃ /lockprofile «پروفایل قفل» • /lockcheck «چک دسترسی»\n"
        "┃ /antispam «ضد اسپم» • /antibot «ضد بات»\n"
        "┃ /captcha «کپچا» • /raid «ضد ریید»\n"
        "┃ /security «امنیت» • /security_user «امنیت کاربر»\n"
        "┃ /spamconfig «تنظیم اسپم» • /spamstats «آمار اسپم»\n"
        "┃ /whitelist «لیست سفید» • /spamblacklist «لیست سیاه»\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
    "econ_admin_manual": ("🛠 پنل دستی سکه/XP", True,
        "┏━━━『 🛠 پنل ویژه ادمین 』━━━┓\n"
        "┃ /setcoins «تنظیم سکه» • /addcoins «افزودن سکه»\n"
        "┃ /setcoinsall «تنظیم سکه همه» • /addcoinsall\n"
        "┃ /setxp «تنظیم XP» • /addxp «افزودن XP»\n"
        "┃ /setxpall «تنظیم XP همه» • /addxpall\n"
        "┃ /setlevel «تنظیم لول» • /setlevelall\n"
        "┃ /seteco «تنظیم اقتصاد» [کلید عدد]\n"
        "┃ /setshopprice «تنظیم قیمت فروشگاه» <کد> <قیمت>\n"
        "┃ ⚠️ فقط با دسترسی manage_economy\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
    "reports": ("📋 مدیریت پیشرفته", True,
        "┏━━━『 📋 مدیریت پیشرفته • گزارش‌ها 』━━━┓\n"
        "┃ /modhistory «تاریخچه مدیریت» <ریپلای>\n"
        "┃ /modstats «آمار مدیریت»\n"
        "┃ /mutes «لیست میوت های فعال»\n"
        "┃ /bans «لیست بن های فعال»\n"
        "┃ /setmodconfig «تنظیمات مدیریت»\n"
        "┃ /giveitem «دادن آیتم» • /removeitem «حذف آیتم»\n"
        "┃ /adminpanel «پنل ادمین»\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
}

# اقتصاد چون خیلی بزرگه (۱۷ زیربخش)، زیرمنوی جدای خودشو داره.
ECONOMY_SECTIONS = {
    "eco_basic": ("🛍️ فروشگاه پایه", False,
        "┏━━━『 🛍️ فروشگاه و کیف‌پول پایه 』━━━┓\n"
        "┃ /balance «موجودی من» • /daily «روزانه»\n"
        "┃ /pay «انتقال» <مقدار> (ریپلای یا آیدی)\n"
        "┃ /shop «فروشگاه» • /buy «خرید» <کد آیتم>\n"
        "┃ /myitems «آیتم های من» یا «کیف»/«اینونتوری»\n"
        "┃ /transactions «تراکنش ها»\n"
        "┃ /economystats «آمار اقتصاد»\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
    "eco_income": ("💵 درآمد و شغل", False,
        "┏━━━『 💵 درآمد و شغل 』━━━┓\n"
        "┃ /work «کار» یا «هاپ هاپ»\n"
        "┃ /income «درآمد» › وضعیت امروز\n"
        "┃ /salary «حقوق» › دریافت حقوق شغل\n"
        "┃ /jobs «مشاغل» › لیست شغل‌ها\n"
        "┃ /job «شغل» › وضعیت شغل فعلی\n"
        "┃ /choosejob «انتخاب شغل» <نام>\n"
        "┃ /quitjob «ترک شغل»\n"
        "┃ /joblevel «سطح شغل» یا «ارتقای شغل»\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
    "eco_missions": ("🎯 ماموریت", False,
        "┏━━━『 🎯 ماموریت 』━━━┓\n"
        "┃ /missions «ماموریت» یا «ماموریت‌ها»\n"
        "┗━━━━━━━━━━━━━━━━━━━━┛"),
    "eco_bank": ("🏦 بانک", False,
        "┏━━━『 🏦 بانک 』━━━┓\n"
        "┃ /bank «بانک» یا «حساب»\n"
        "┃ /deposit «سپرده» <مبلغ>\n"
        "┃ /longdeposit «سپرده بلندمدت» <مبلغ> <روز>\n"
        "┃ /withdraw «برداشت» <مبلغ>\n"
        "┃ /longwithdraw «برداشت بلندمدت» <شناسه>\n"
        "┃ /interest «سود»\n"
        "┃ /bankupgrade «ارتقای بانک»\n"
        "┗━━━━━━━━━━━━━━━━━━━━┛"),
    "eco_market": ("📈 بازار و ترید", False,
        "┏━━━『 📈 بازار و ترید 』━━━┓\n"
        "┃ /market «بازار» یا «ارز»\n"
        "┃ /price «قیمت» <نماد>\n"
        "┃ /buy «خرید» <نماد> <مبلغ>\n"
        "┃ /sell «فروش» <نماد> <مقدار|all>\n"
        "┃ /portfolio «سبد»\n"
        "┃ /trade «ترید» <مبلغ>\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
    "eco_insurance": ("🛡️ بیمه", False,
        "┏━━━『 🛡️ بیمه 』━━━┓\n"
        "┃ /insurance «بیمه» یا «وضعیت بیمه»\n"
        "┃ /buyinsurance «خرید بیمه» <نام>\n"
        "┃ /upgradeinsurance «ارتقای بیمه»\n"
        "┗━━━━━━━━━━━━━━━━━━━━┛"),
    "eco_theft": ("🦹 دزدی و زندان", False,
        "┏━━━『 🦹 دزدی و زندان 』━━━┓\n"
        "┃ /steal «دزدی» (ریپلای روی هدف)\n"
        "┃ /wanted «تحت تعقیب»\n"
        "┃ /jail «زندان»\n"
        "┃ /escape «فرار»\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
    "eco_city": ("🏙️ شهر", False,
        "┏━━━『 🏙️ شهر 』━━━┓\n"
        "┃ /city «شهر»\n"
        "┃ /foundcity «ساخت شهر»\n"
        "┃ /upgradecity «ارتقای شهر»\n"
        "┃ /build «ساختمان‌ها» [نام ساختمان]\n"
        "┗━━━━━━━━━━━━━━━━━━━━┛"),
    "eco_property": ("🏠 املاک", False,
        "┏━━━『 🏠 املاک 』━━━┓\n"
        "┃ /property «املاک»\n"
        "┃ /buyproperty «خرید ملک» <نوع>\n"
        "┃ /sellproperty «فروش ملک» <شناسه>\n"
        "┃ /myproperty «ملک من» [شناسه]\n"
        "┗━━━━━━━━━━━━━━━━━━━━┛"),
    "eco_marriage": ("💍 ازدواج", False,
        "┏━━━『 💍 ازدواج 』━━━┓\n"
        "┃ /propose «درخواست ازدواج» (ریپلای)\n"
        "┃ /marry «ازدواج» (ریپلای، برای قبول)\n"
        "┃ /partner «همسر»\n"
        "┃ /divorce «طلاق»\n"
        "┃ /gift «هدیه» <مبلغ>\n"
        "┗━━━━━━━━━━━━━━━━━━━━┛"),
    "eco_pet": ("🐾 پت", False,
        "┏━━━『 🐾 پت 』━━━┓\n"
        "┃ /pet «پت» • /pets «پت‌ها»\n"
        "┃ /buypet «خرید پت» <نوع>\n"
        "┃ /feedpet «غذا» • /trainpet «آموزش پت»\n"
        "┃ /evolvepet «ارتقای پت»\n"
        "┃ /petfight «فایت» [مبلغ] (ریپلای = PvP)\n"
        "┗━━━━━━━━━━━━━━━━━━━━┛"),
    "eco_games": ("🎮 بازی‌ها", False,
        "┏━━━『 🎮 بازی‌ها 』━━━┓\n"
        "┃ /dice «تاس» <مبلغ>\n"
        "┃ /coinflip «شیر یا خط» <مبلغ> <شیر|خط>\n"
        "┃ /guess «حدس» <عدد ۱-۱۰> <مبلغ>\n"
        "┃ /xo «اکس او» <مبلغ> (ریپلای) یا <خونه‌ی ۱-۹>\n"
        "┃ /game «بازی» › راهنما + آمار خودت\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
    "eco_blackmarket": ("🖤 بازار سیاه", False,
        "┏━━━『 🖤 بازار سیاه 』━━━┓\n"
        "┃ /blackmarket «بازار سیاه»\n"
        "┃ /buyblack «خرید کالا» <کد>\n"
        "┃ /sellblack «فروش کالا» <کد> [تعداد]\n"
        "┃ /auction «حراجی» [مبلغ]\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
    "eco_underground": ("🌑 دنیای زیرزمینی", False,
        "┏━━━『 🌑 دنیای زیرزمینی 』━━━┓\n"
        "┃ /darkweb «دارک وب» • /spy «جاسوس»\n"
        "┃ /hacker «هکر»\n"
        "┃ /undergroundmission «ماموریت زیرزمینی»\n"
        "┃ /contract «قرارداد» › وضعیت خودت\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
    "eco_inventory": ("🎒 اینونتوری", False,
        "┏━━━『 🎒 اینونتوری 』━━━┓\n"
        "┃ /myitems «کیف» یا «اینونتوری» یا «آیتم های من»\n"
        "┃ /useitem «استفاده از» <کد آیتم>\n"
        "┗━━━━━━━━━━━━━━━━━━━━┛"),
    "eco_leaderboard": ("🏆 دستاورد و رتبه", False,
        "┏━━━『 🏆 دستاورد و رتبه 』━━━┓\n"
        "┃ /achievements «دستاوردها»\n"
        "┃ /networth «ارزش خالص»\n"
        "┃ /richest «ثروتمندان»\n"
        "┃ /ecoleaderboard «برترین‌ها» [نوع]\n"
        "┃ › نوع: ثروت،بانک،ترید،شغل،شهر،املاک،\n"
        "┃        پت،دزد،بازی،رپ،دستاورد\n"
        "┃ /myrank «رتبه اقتصاد»\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
    "eco_events": ("🎁 رویداد و فروشگاه ویژه", False,
        "┏━━━『 🎁 رویداد و فروشگاه ویژه 』━━━┓\n"
        "┃ /event «رویداد» › رویداد فعال گروه\n"
        "┃ /dailyshop «فروشگاه ویژه» › پیشنهاد امروز\n"
        "┃ /buydailydeal «خرید ویژه»\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
    "eco_admin": ("⚙️ مدیریت اقتصاد", True,
        "┏━━━『 ⚙️ مدیریت اقتصاد (ادمین) 』━━━┓\n"
        "┃ «منوی اقتصاد» › نمای کلی درختی\n"
        "┃ /economymodules «ماژول‌های اقتصاد» [نام] [روشن|خاموش]\n"
        "┃ /inspecteconomy «بازرسی اقتصاد» (ریپلای)\n"
        "┃ /reseteconomy «ریست اقتصاد کاربر» (ریپلای) تایید\n"
        "┃ /economyhealth «سلامت اقتصاد»\n"
        "┃ /forceevent «فورس رویداد» <نوع>\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛"),
}


def _help_footer() -> str:
    return "\n\n      𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪\n       𝐁𝐘 𝐌𝐑𝐗 𓂀"


def _build_help_top_markup(user_id: int) -> InlineKeyboardMarkup:
    admin = is_admin(user_id)
    rows, row = [], []
    for key, (label, admin_only, _text) in HELP_SECTIONS.items():
        if admin_only and not admin:
            continue
        row.append(InlineKeyboardButton(label, callback_data=f"help_sec:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("💰 اقتصاد (زیرمنو)", callback_data="help_eco")])
    rows.append([InlineKeyboardButton("❌ بستن", callback_data="menu_close")])
    return InlineKeyboardMarkup(rows)


def _build_help_eco_markup(user_id: int) -> InlineKeyboardMarkup:
    admin = is_admin(user_id)
    rows, row = [], []
    for key, (label, admin_only, _text) in ECONOMY_SECTIONS.items():
        if admin_only and not admin:
            continue
        row.append(InlineKeyboardButton(label, callback_data=f"help_ecosec:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton("🔙 بازگشت", callback_data="help_top"),
        InlineKeyboardButton("❌ بستن", callback_data="menu_close"),
    ])
    return InlineKeyboardMarkup(rows)


def _build_help_leaf_markup(back_to: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔙 بازگشت", callback_data=back_to),
        InlineKeyboardButton("❌ بستن", callback_data="menu_close"),
    ]])


def _help_top_text() -> str:
    return HELP_HEADER + "        ✦ یه بخش رو انتخاب کن ✦\n\nهر دکمه دستورهای همون بخش رو (فارسی + انگلیسی) نشون می‌ده."



def _build_category_markup(chat_id: int, cat_key: str, sub_key: str = None) -> InlineKeyboardMarkup:
    cat = config.SETTING_CATEGORIES[cat_key]
    settings = _chat_settings[chat_id]
    rows = []

    subgroups = _SETTING_SUBGROUPS.get(cat_key)
    if subgroups and sub_key is None:
        # صفحه‌ی فرود: انتخاب زیربخش (تا لیست طولانی، یک‌جا شلوغ نشه)
        sub_buttons = []
        for sk, sub in subgroups.items():
            active, total = _subgroup_progress(chat_id, cat_key, sk)
            sub_buttons.append(
                InlineKeyboardButton(
                    f"{_status_dot(active, total)} {sub['title']} • {active}/{total}",
                    callback_data=f"sub:{cat_key}:{sk}",
                )
            )
        rows.extend(_pair_rows(sub_buttons))
    elif subgroups and sub_key is not None:
        # زیرصفحه‌ی یک گروه خاص (مثلاً «رسانه» داخل قفل‌ها)
        if cat_key in _BOT_ENABLED_DEPENDENT_CATEGORIES and not settings.get("bot_enabled", False):
            rows.append(
                [InlineKeyboardButton(
                    "⚠️ سوییچ کلی ربات خاموشه — بزن روشن کن",
                    callback_data=f"botswitch:{cat_key}:{sub_key}",
                )]
            )
        keys = subgroups[sub_key]["keys"]
        toggle_buttons = []
        for key in keys:
            label = config.SETTING_LABELS[key]
            icon = "🟢" if settings.get(key, False) else "⚪"
            toggle_buttons.append(InlineKeyboardButton(f"{icon} {label}", callback_data=f"toggle:{key}"))
        rows.extend(_pair_rows(toggle_buttons))
        active, total = _subgroup_progress(chat_id, cat_key, sub_key)
        rows.append(
            [InlineKeyboardButton(f"{_status_dot(active, total)} {active} / {total} فعال", callback_data="noop")]
        )
        rows.append(
            [
                InlineKeyboardButton("🔙 بازگشت", callback_data=f"panel:{cat_key}"),
                InlineKeyboardButton("🏠 اصلی", callback_data="menu_back"),
                InlineKeyboardButton("❌ بستن", callback_data="menu_close"),
            ]
        )
        return InlineKeyboardMarkup(rows)
    else:
        toggle_buttons = []
        for key in cat["keys"]:
            label = config.SETTING_LABELS[key]
            icon = "🟢" if settings.get(key, False) else "⚪"
            toggle_buttons.append(InlineKeyboardButton(f"{icon} {label}", callback_data=f"toggle:{key}"))
        rows.extend(_pair_rows(toggle_buttons))

    if cat_key in _BOT_ENABLED_DEPENDENT_CATEGORIES and not settings.get("bot_enabled", False):
        rows.insert(
            0,
            [InlineKeyboardButton(
                "⚠️ سوییچ کلی ربات خاموشه — بزن روشن کن",
                callback_data=f"botswitch:{cat_key}",
            )],
        )

    if cat_key == "core":
        ai_mode = _ai_reply_mode[chat_id]
        rows.append(
            [
                InlineKeyboardButton(
                    config.AI_REPLY_MODE_LABELS.get(ai_mode, ai_mode),
                    callback_data="cycle:ai_reply_mode",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton("📊 فعالیت من", callback_data="act:me"),
                InlineKeyboardButton("🏆 لیدربرد", callback_data="act:leaderboard"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton("🔥 استریک‌ها", callback_data="act:streaks"),
                InlineKeyboardButton("🏅 دستاوردهای من", callback_data="act:achievements"),
            ]
        )

    if cat_key == "moderation":
        now = time.time()
        active_warns = sum(1 for c in _warnings[chat_id].values() if c > 0)
        active_mutes = sum(1 for until in _active_mutes[chat_id].values() if until > now)
        banned_count = len(_banned_users[chat_id])
        vip_count = len(_vip_users[chat_id])
        blacklist_count = len(_blacklist[chat_id])
        rows.append(
            [
                InlineKeyboardButton(f"⚠️ اخطار فعال: {active_warns} نفر", callback_data="noop"),
                InlineKeyboardButton(f"🔇 میوت فعال: {active_mutes} نفر", callback_data="noop"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(f"🚫 بن‌شده: {banned_count} نفر", callback_data="noop"),
                InlineKeyboardButton(f"⭐ VIP: {vip_count} نفر", callback_data="noop"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(f"🛑 بلاک‌لیست: {blacklist_count} مورد", callback_data="noop"),
                InlineKeyboardButton(f"👥 اعضا: {len(_known_members[chat_id])} نفر", callback_data="noop"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    f"⚠️ حد اخطار: {_warn_limit[chat_id]} — تغییر: /setwarnlimit عدد",
                    callback_data="noop",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    f"🔇 مدت میوت پایه: {_mute_minutes[chat_id]} دقیقه — تغییر: /setmutetime دقیقه",
                    callback_data="noop",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton("🔇 میوت سریع", callback_data="ask:mute_quick"),
                InlineKeyboardButton("🚫 بن سریع", callback_data="ask:ban_quick"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton("⭐ افزودن VIP", callback_data="ask:vip_add"),
                InlineKeyboardButton("➖ حذف VIP", callback_data="ask:vip_remove"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton("🛑 افزودن بلاک‌لیست", callback_data="ask:blacklist_add"),
                InlineKeyboardButton("➖ حذف بلاک‌لیست", callback_data="ask:blacklist_remove"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton("👑 ارتقا به ادمین", callback_data="ask:promote"),
                InlineKeyboardButton("⬇️ عزل ادمین", callback_data="ask:demote"),
            ]
        )
        rows.append([InlineKeyboardButton("📢 اطلاع‌رسانی همگانی", callback_data="ask:notify")])

    if cat_key == "relationships":
        run_mode_label = "🟢 اجرا: Active (پاسخ می‌ده)" if _terminator_mode[chat_id] == "active" else "🔇 اجرا: Silent (فقط تشخیص/آمار)"
        default_mode = _terminator_default_mode[chat_id]
        mode_label = "🎭 مد: " + config.TERMINATOR_MODE_LABELS.get(default_mode, default_mode)
        rows.append(
            [
                InlineKeyboardButton(
                    f"📋 نمایش لیست دوست/دشمن ({len(_friends[chat_id])}/{len(_enemies[chat_id])})",
                    callback_data="show_relations",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(f"🎯 Targets: {len(_enemies[chat_id])}", callback_data="term:stats"),
                InlineKeyboardButton(f"❤️ Friends: {len(_friends[chat_id])}", callback_data="show_relations"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(run_mode_label, callback_data="cycle:terminator_run_mode"),
                InlineKeyboardButton(mode_label, callback_data="cycle:terminator_mode"),
            ]
        )
        rows.append([InlineKeyboardButton("📊 آمار کامل ترمیناتور", callback_data="term:stats")])
        rows.append(
            [
                InlineKeyboardButton("➕ افزودن دشمن", callback_data="ask:enemy_add"),
                InlineKeyboardButton("➕ افزودن دوست", callback_data="ask:friend_add"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton("➖ حذف دشمن", callback_data="ask:enemy_remove"),
                InlineKeyboardButton("➖ حذف دوست", callback_data="ask:friend_remove"),
            ]
        )

    if cat_key == "tagging":
        tag_on = "🟢 فعال" if settings.get("tag_members_enabled", False) else "⚪ غیرفعال"
        rows.append(
            [
                InlineKeyboardButton(f"🏷️ وضعیت تگ: {tag_on}", callback_data="noop"),
                InlineKeyboardButton(f"👥 اعضا: {len(_known_members[chat_id])} نفر", callback_data="noop"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    f"⚙️ دسته‌ها: {config.TAG_BATCH_SIZE} نفر هر {config.TAG_BATCH_DELAY_SECONDS}s",
                    callback_data="noop",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    f"📖 دستور: /tag [متن] یا نوشتن «{config.TAG_ALL_TRIGGER_WORD}»",
                    callback_data="noop",
                )
            ]
        )

    if cat_key == "profile":
        xp_map = _xp[chat_id]
        active_users = len(xp_map)
        highest_level = max((_xp_level(x) for x in xp_map.values()), default=0)
        total_xp = sum(xp_map.values())
        rows.append(
            [
                InlineKeyboardButton(f"👥 کاربران دارای XP: {active_users}", callback_data="noop"),
                InlineKeyboardButton(f"🏆 بالاترین Level: {highest_level}", callback_data="noop"),
            ]
        )
        rows.append([InlineKeyboardButton(f"✨ مجموع XP گروه: {total_xp}", callback_data="noop")])
        rows.append(
            [
                InlineKeyboardButton("👤 پروفایل من", callback_data="xp:profile"),
                InlineKeyboardButton("🏆 نفرات برتر", callback_data="xp:top"),
            ]
        )

    if cat_key == "economy":
        wallet = _wallet[chat_id]
        total_coins = sum(wallet.values())
        holders = sum(1 for v in wallet.values() if v > 0)
        rows.append(
            [
                InlineKeyboardButton(f"🪙 کل سکه در گردش: {total_coins}", callback_data="noop"),
                InlineKeyboardButton(f"👥 دارای موجودی: {holders} نفر", callback_data="noop"),
            ]
        )
        rows.append([InlineKeyboardButton("🏆 پولدارترین اعضا", callback_data="econ:top")])

    if cat_key == "welcome":
        welcome_on = "🟢 روشن" if settings.get("welcome_enabled", False) else "⚪ خاموش"
        leave_on = "🟢 روشن" if settings.get("leave_enabled", False) else "⚪ خاموش"
        rows.append(
            [
                InlineKeyboardButton(f"👋 خوش‌آمد: {welcome_on}", callback_data="noop"),
                InlineKeyboardButton(f"🚪 پیام خروج: {leave_on}", callback_data="noop"),
            ]
        )
        theme = _get_theme(chat_id)
        theme_key = _chat_theme.get(chat_id, config.DEFAULT_MESSAGE_THEME)
        rows.append(
            [
                InlineKeyboardButton(
                    f"🎨 تم پیام‌ها: {theme.get('label', theme_key)} (بزن برای تعویض)",
                    callback_data="cycle:welcome_theme",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton("✏️ ویرایش متن خوش‌آمد", callback_data="ask:welcome_text"),
                InlineKeyboardButton("✏️ ویرایش متن خروج", callback_data="ask:leave_text"),
            ]
        )

    progress = _category_progress(chat_id, cat_key)
    if progress is not None:
        active, total = progress
        rows.append(
            [
                InlineKeyboardButton(
                    f"{_status_dot(active, total)} {active} / {total} فعال",
                    callback_data="noop",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton("🔙 بازگشت", callback_data="menu_back"),
            InlineKeyboardButton("🏠 اصلی", callback_data="menu_back"),
            InlineKeyboardButton("❌ بستن", callback_data="menu_close"),
        ]
    )
    return InlineKeyboardMarkup(rows)


_ECON_KEY_ORDER = [
    "daily_min", "daily_max", "activity_min", "activity_max", "xp_min", "xp_max", "lottery_minutes",
]
_ECON_STEP = {
    "daily_min": 5, "daily_max": 5,
    "activity_min": 1, "activity_max": 1,
    "xp_min": 1, "xp_max": 1,
    "lottery_minutes": 1,
}
_ECON_FLOOR = {
    "daily_min": 0, "daily_max": 0,
    "activity_min": 0, "activity_max": 0,
    "xp_min": 0, "xp_max": 0,
    "lottery_minutes": 1,
}
_ECON_CEIL = {
    "daily_min": 100000, "daily_max": 100000,
    "activity_min": 1000, "activity_max": 1000,
    "xp_min": 1000, "xp_max": 1000,
    "lottery_minutes": 1440,
}
_ECON_LABELS = {
    "daily_min": "🪙 حداقل پاداش روزانه",
    "daily_max": "🪙 حداکثر پاداش روزانه",
    "activity_min": "⚡ حداقل سکه‌ی فعالیت هر پیام",
    "activity_max": "⚡ حداکثر سکه‌ی فعالیت هر پیام",
    "xp_min": "✨ حداقل XP هر پیام",
    "xp_max": "✨ حداکثر XP هر پیام",
    "lottery_minutes": "🎁 مدت پیش‌فرض قرعه کشی (دقیقه)",
}
_SHOP_PRICE_STEP = 10
_SHOP_PRICE_FLOOR = 5


def _econ_admin_header(chat_id: int) -> str:
    title = _chat_titles.get(chat_id, str(chat_id))
    return (
        "╭━━〔 🛠 ADMIN CENTER 〕━━╮\n"
        "┃ اقتصاد • XP • قرعه‌کشی\n"
        f"┃ گروه: {title}\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "با دکمه‌های ➖ / ➕ مقدارها رو تنظیم کن.\n"
        "برای سکه/XP/لول یک نفر خاص یا همه‌ی اعضا با هم، از «راهنمای دستورات دستی» استفاده کن."
    )


def _build_econ_admin_markup(chat_id: int, view: str = "main") -> InlineKeyboardMarkup:
    if view == "shop":
        return _build_econ_shop_markup(chat_id)
    if view == "stats":
        return _build_econ_stats_markup(chat_id)
    if view == "quick":
        return _build_econ_quick_markup(chat_id)

    es = _econ_settings[chat_id]
    rows = []
    for key in _ECON_KEY_ORDER:
        step = _ECON_STEP[key]
        rows.append(
            [
                InlineKeyboardButton("➖", callback_data=f"econset:{key}:-{step}"),
                InlineKeyboardButton(f"{_ECON_LABELS[key]}: {es[key]}", callback_data="noop"),
                InlineKeyboardButton("➕", callback_data=f"econset:{key}:{step}"),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton("🛒 مدیریت فروشگاه", callback_data="econv:shop"),
            InlineKeyboardButton("📊 آمار اقتصاد گروه", callback_data="econv:stats"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton("🎁 اقدامات سریع", callback_data="econv:quick"),
            InlineKeyboardButton("♻️ بازنشانی به پیش فرض", callback_data="econreset:ask"),
        ]
    )
    rows.append([InlineKeyboardButton("📖 راهنمای دستورات دستی (سکه/XP/لول یک نفره)", callback_data="econ_help")])
    rows.append(
        [
            InlineKeyboardButton("🔙 بازگشت", callback_data="menu_back"),
            InlineKeyboardButton("❌ بستن", callback_data="menu_close"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _build_econ_shop_markup(chat_id: int) -> InlineKeyboardMarkup:
    overrides = _shop_price_overrides[chat_id]
    hidden = _shop_hidden_items[chat_id]
    rows = []
    for key, item in config.SHOP_ITEMS.items():
        price = overrides.get(key, item["price"])
        state_label = "🙈 مخفیه، بزن نمایان شه" if key in hidden else "👁 نمایانه، بزن مخفی شه"
        rows.append(
            [
                InlineKeyboardButton("➖", callback_data=f"econshop:{key}:-{_SHOP_PRICE_STEP}"),
                InlineKeyboardButton(f"{item['name']}: {price} {config.CURRENCY_EMOJI}", callback_data="noop"),
                InlineKeyboardButton("➕", callback_data=f"econshop:{key}:{_SHOP_PRICE_STEP}"),
            ]
        )
        rows.append([InlineKeyboardButton(state_label, callback_data=f"econshop:toggle:{key}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت به پنل اقتصاد", callback_data="econv:main")])
    return InlineKeyboardMarkup(rows)


def _build_econ_stats_markup(chat_id: int) -> InlineKeyboardMarkup:
    wallet = _wallet[chat_id]
    total_coins = sum(wallet.values())
    members_with_coins = sum(1 for v in wallet.values() if v > 0)
    top = sorted(wallet.items(), key=lambda kv: kv[1], reverse=True)[:5]
    rows = [
        [InlineKeyboardButton(f"💰 مجموع سکه‌ی در گردش: {total_coins} {config.CURRENCY_NAME}", callback_data="noop")],
        [InlineKeyboardButton(f"👥 اعضای دارای موجودی: {members_with_coins} نفر", callback_data="noop")],
        [InlineKeyboardButton(f"👥 کل اعضای شناخته‌شده: {len(_known_members[chat_id])} نفر", callback_data="noop")],
    ]
    if top:
        rows.append([InlineKeyboardButton("🏆 پولدارترین اعضا:", callback_data="noop")])
        for uid, bal in top:
            name = _user_display_names.get(uid, str(uid))
            rows.append([InlineKeyboardButton(f"{name}: {bal} {config.CURRENCY_NAME}", callback_data="noop")])
    else:
        rows.append([InlineKeyboardButton("هنوز هیچکس سکه‌ای نداره.", callback_data="noop")])
    rows.append([InlineKeyboardButton("🔙 بازگشت به پنل اقتصاد", callback_data="econv:main")])
    return InlineKeyboardMarkup(rows)


def _build_econ_quick_markup(chat_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🎁 جایزه‌ی +۲۰ به همه‌ی اعضا", callback_data="econquick:bonus20")],
        [InlineKeyboardButton("🎁 جایزه‌ی +۵۰ به همه‌ی اعضا", callback_data="econquick:bonus50")],
        [InlineKeyboardButton("🎉 شروع قرعه کشی سریع (۵ دقیقه)", callback_data="econquick:lottery5")],
        [InlineKeyboardButton("🔙 بازگشت به پنل اقتصاد", callback_data="econv:main")],
    ]
    return InlineKeyboardMarkup(rows)


async def _handle_econ_admin_callback(query, chat_id: int, data: str):
    """همه‌ی دکمه‌های پنل ویژه‌ی ادمین (econv:/econset:/econshop:/econreset:/econquick:) رو مدیریت می‌کنه."""
    parts = data.split(":")
    prefix = parts[0]

    if prefix == "econv":
        view = parts[1] if len(parts) > 1 else "main"
        await query.answer()
        await query.edit_message_text(_econ_admin_header(chat_id), reply_markup=_build_econ_admin_markup(chat_id, view))
        return

    if prefix == "econset":
        key = parts[1] if len(parts) > 1 else ""
        if key not in _ECON_KEY_ORDER:
            await query.answer()
            return
        try:
            delta = int(parts[2])
        except (IndexError, ValueError):
            await query.answer()
            return
        es = _econ_settings[chat_id]
        new_value = max(_ECON_FLOOR[key], min(_ECON_CEIL[key], es[key] + delta))
        es[key] = new_value
        await query.answer(f"{_ECON_LABELS[key]}: {new_value}")
        await query.edit_message_reply_markup(reply_markup=_build_econ_admin_markup(chat_id, "main"))
        await save_state()
        return

    if prefix == "econreset":
        action = parts[1] if len(parts) > 1 else ""
        if action == "ask":
            await query.answer()
            confirm_rows = [
                [
                    InlineKeyboardButton("✅ بله، بازنشانی کن", callback_data="econreset:yes"),
                    InlineKeyboardButton("✖️ انصراف", callback_data="econreset:no"),
                ]
            ]
            await query.edit_message_text(
                "مطمئنی می‌خوای تنظیمات خودکار اقتصاد این گروه (پاداش روزانه، سکه‌ی فعالیت، XP، مدت قرعه‌کشی) "
                "به مقدار پیش‌فرض برگرده؟",
                reply_markup=InlineKeyboardMarkup(confirm_rows),
            )
            return
        if action == "yes":
            _econ_settings[chat_id] = _default_econ_settings()
            await query.answer("✅ بازنشانی شد.")
            await query.edit_message_text(_econ_admin_header(chat_id), reply_markup=_build_econ_admin_markup(chat_id, "main"))
            await save_state()
            return
        await query.answer("انصراف داده شد.")
        await query.edit_message_text(_econ_admin_header(chat_id), reply_markup=_build_econ_admin_markup(chat_id, "main"))
        return

    if prefix == "econshop":
        action = parts[1] if len(parts) > 1 else ""
        if action == "toggle":
            key = parts[2] if len(parts) > 2 else ""
            if key not in config.SHOP_ITEMS:
                await query.answer()
                return
            hidden = _shop_hidden_items[chat_id]
            if key in hidden:
                hidden.discard(key)
                await query.answer("👁 نمایان شد.")
            else:
                hidden.add(key)
                await query.answer("🙈 مخفی شد.")
            await query.edit_message_reply_markup(reply_markup=_build_econ_shop_markup(chat_id))
            await save_state()
            return
        key = action
        if key not in config.SHOP_ITEMS:
            await query.answer()
            return
        try:
            delta = int(parts[2])
        except (IndexError, ValueError):
            await query.answer()
            return
        overrides = _shop_price_overrides[chat_id]
        current_price = overrides.get(key, config.SHOP_ITEMS[key]["price"])
        new_price = max(_SHOP_PRICE_FLOOR, current_price + delta)
        overrides[key] = new_price
        await query.answer(f"{config.SHOP_ITEMS[key]['name']}: {new_price} {config.CURRENCY_EMOJI}")
        await query.edit_message_reply_markup(reply_markup=_build_econ_shop_markup(chat_id))
        await save_state()
        return

    if prefix == "econquick":
        action = parts[1] if len(parts) > 1 else ""
        members = _known_members[chat_id]
        if action in ("bonus20", "bonus50"):
            amount = 20 if action == "bonus20" else 50
            for uid in members:
                await economy_core.add_coins(chat_id, uid, amount, kind="EVENT", note="econquick bonus")
            await query.answer(f"✅ به {len(members)} نفر، {amount} {config.CURRENCY_NAME} داده شد.", show_alert=True)
            await save_state()
            return
        if action == "lottery5":
            if chat_id in _lotteries:
                await query.answer("همین الان یه قرعه‌کشی فعاله توی این گروه.", show_alert=True)
                return
            minutes = 5
            try:
                bot = query.get_bot()
                sent = await bot.send_message(chat_id=chat_id, text=_lottery_text(None, 0, minutes))
                try:
                    await sent.edit_reply_markup(reply_markup=_lottery_keyboard(sent.message_id))
                except Exception as e:
                    logger.warning(f"ساخت دکمه‌ی قرعه‌کشی سریع ناموفق بود: {e}")
                _lotteries[chat_id] = {
                    "message_id": sent.message_id,
                    "end_ts": time.time() + minutes * 60,
                    "participants": set(),
                    "prize": None,
                }
                await query.answer("🎉 قرعه‌کشی شروع شد.")
            except Exception as e:
                logger.warning(f"شروع قرعه‌کشی سریع از پنل ناموفق بود: {e}")
                await query.answer("مشکلی پیش اومد.", show_alert=True)
            return
        await query.answer()
        return

    await query.answer()


ECON_ADMIN_HELP_TEXT = (
    "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
    "      𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪\n"
    "       𝐄𝐂𝐎 𝐀𝐃𝐌𝐈𝐍\n"
    "╰━━━━━━━━━━━━━━━━━━━━━━╯\n"
    "        𝐎𝐖𝐍𝐄𝐑 : 𝐌𝐑𝐗\n"
    "     ✦ پنل ویژه اقتصاد ✦\n\n"

    "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
    "   ⚠️ دسترسی ویژه ادمین\n"
    "   فقط داخل گروه\n"
    "   نیازمند: manage_economy\n"
    "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"

    "┏━━━『 🎛 تنظیمات با دکمه (بدون تایپ) 』━━━┓\n"
    "┃ توی خود پنل، پاداش روزانه، سکه‌ی فعالیت،\n"
    "┃ XP هر پیام و مدت قرعه کشی رو با دکمه های\n"
    "┃ ➖ / ➕ کم و زیاد کن. برای فروشگاه هم دکمه\n"
    "┃ قیمت و مخفی/نمایان کردن هست. اینا دیگه\n"
    "┃ نیاز به دستور ندارن.\n"
    "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"

    "┏━━━『 💰 سکه • یک نفر 』━━━┓\n"
    "┃ ریپلای + /setcoins «تنظیم سکه» <مقدار>\n"
    "┃ › موجودی را دقیقاً روی عدد می‌گذارد\n"
    "┃ ریپلای + /addcoins «اضافه سکه» <مقدار>\n"
    "┃ › اضافه / کم می‌کند\n"
    "┃ › مقدار منفی هم قابل استفاده است\n"
    "┃ بدون ریپلای:\n"
    "┃ /setcoins <آیدی عددی> <مقدار>\n"
    "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"

    "┏━━━『 💰 سکه • همه اعضا 』━━━┓\n"
    "┃ /setcoinsall «تنظیم سکه همه» <مقدار>\n"
    "┃ /addcoinsall «اضافه سکه همه» <مقدار>\n"
    "┃ › اعمال همزمان برای همه اعضا\n"
    "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"

    "┏━━━『 ✨ XP • لول • یک نفر 』━━━┓\n"
    "┃ ریپلای + /setxp «تنظیم ایکسپی» <مقدار>\n"
    "┃ ریپلای + /addxp «اضافه ایکسپی» <مقدار>\n"
    "┃ ریپلای + /setlevel «تنظیم لول» <لول>\n"
    "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"

    "┏━━━『 ✨ XP • لول • همه اعضا 』━━━┓\n"
    "┃ /setxpall «تنظیم ایکسپی همه» <مقدار>\n"
    "┃ /addxpall «اضافه ایکسپی همه» <مقدار>\n"
    "┃ /setlevelall «تنظیم لول همه» <لول>\n"
    "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"

    "┏━━━『 ⚙️ اقتصاد خودکار گروه 』━━━┓\n"
    "┃ /seteco «تنظیم اقتصاد»\n"
    "┃ › نمایش تنظیمات فعلی\n"
    "┃ /seteco «تنظیم اقتصاد» <کلید> <عدد>\n"
    "┃ › تغییر مقدار (یا از دکمه های پنل)\n"
    "┃ کلیدها:\n"
    "┃ daily_min • daily_max\n"
    "┃ activity_min • activity_max\n"
    "┃ xp_min • xp_max\n"
    "┃ lottery_minutes\n"
    "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"

    "┏━━━『 🛒 فروشگاه 』━━━┓\n"
    "┃ /setshopprice «تنظیم قیمت فروشگاه» <کد> <قیمت>\n"
    "┃ › تغییر قیمت آیتم فروشگاه (یا از پنل → «مدیریت فروشگاه»)\n"
    "┃ › مخفی/نمایان کردن آیتم فقط از پنل ممکنه\n"
    "┃ › همه‌شون فقط برای همین گروه\n"
    "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"

    "┏━━━『 🎁 اقدامات سریع (فقط از پنل) 』━━━┓\n"
    "┃ پنل → «اقدامات سریع»:\n"
    "┃ جایزه‌ی آنی +۲۰ یا +۵۰ به همه\n"
    "┃ شروع سریع قرعه کشی ۵ دقیقه‌ای\n"
    "┃ پنل → «♻️ بازنشانی به پیش فرض»:\n"
    "┃ برگردوندن همه‌ی تنظیمات خودکار به حالت اول\n"
    "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"

    "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
    "   𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 • 𝐄𝐂𝐎𝐍𝐎𝐌𝐘\n"
    "   مدیریت حرفه‌ای اقتصاد\n"
    "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"

    "      𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪\n"
    "       𝐁𝐘 𝐌𝐑𝐗 𓂀"
)


def _menu_header_text(chat_id: int) -> str:
    settings = _chat_settings[chat_id]
    bot_state = "🟢 ACTIVE" if settings.get("bot_enabled", False) else "🔴 OFF"
    ai_mode = config.AI_REPLY_MODE_LABELS.get(_ai_reply_mode[chat_id], _ai_reply_mode[chat_id])
    title = _chat_titles.get(chat_id, "نامشخص")
    members = len(_known_members[chat_id])
    security = _security_score(chat_id)
    return (
        "╭━━━〔 🤖 DIGIANTI 〕━━━╮\n"
        "┃  GROUP CONTROL CENTER\n"
        f"┃  گروه: {title}\n"
        "┃\n"
        f"┃  🤖 ربات        {bot_state}\n"
        f"┃  👥 اعضا        {members} نفر\n"
        f"┃  🛡 امنیت       {security}%\n"
        f"┃  🧠 {ai_mode}\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"🤝 دوست: {len(_friends[chat_id])}  •  ⚔️ دشمن: {len(_enemies[chat_id])}\n\n"
        "⚡ یه بخش رو برای مدیریت انتخاب کن:"
    )


# دسته‌هایی که واقعاً وابسته به سوییچ کلی ربات (bot_enabled) هستن: اگه این سوییچ
# خاموش باشه، تغییر تک‌تک قفل‌ها/ضداسپم توی این صفحه‌ها هیچ اثر عملی نداره (چون
# process_message/lock_engine از همون اول خاموشی سیستم برمی‌گردن). این همون چیزیه
# که باعث می‌شد پنل /menu «کار نکنه» ولی پنل «قفل» (لاک‌اینجین) که این هشدار رو
# داشت، کار کنه - این دو تا حالا سینک شدن.
_BOT_ENABLED_DEPENDENT_CATEGORIES = {"locks", "antispam"}


def _bot_enabled_warning_lines(chat_id: int, cat_key: str) -> list:
    if cat_key not in _BOT_ENABLED_DEPENDENT_CATEGORIES:
        return []
    if _chat_settings[chat_id].get("bot_enabled", False):
        return []
    return [
        "┃",
        "┃ ⚠️ سوییچ کلی ربات خاموشه؛ تا وقتی روشن نشه، این",
        "┃ تنظیمات فقط نمایشی‌ان و عملاً هیچ‌کدوم اجرا نمی‌شن.",
        "┃ با دکمه‌ی «روشن کردن ربات» زیر همین صفحه روشنش کن.",
    ]


def _category_header_text(chat_id: int, cat_key: str, sub_key: str = None) -> str:
    cat = config.SETTING_CATEGORIES[cat_key]
    warn_lines = _bot_enabled_warning_lines(chat_id, cat_key)
    if sub_key is not None:
        sub = _SETTING_SUBGROUPS[cat_key][sub_key]
        active, total = _subgroup_progress(chat_id, cat_key, sub_key)
        lines = [
            f"╭━━〔 {sub['title']} 〕━━╮",
            f"┃ {cat['title']} ›",
            f"┃ {_status_dot(active, total)} {active} از {total} قابلیت فعاله",
        ]
        lines.extend(warn_lines)
        lines.append("╰━━━━━━━━━━━━━━━━━━━━━━╯")
        return "\n".join(lines)
    lines = [
        f"╭━━〔 {cat['title']} 〕━━╮",
    ]
    progress = _category_progress(chat_id, cat_key)
    if progress is not None:
        active, total = progress
        lines.append(f"┃ {_status_dot(active, total)} {active} از {total} قابلیت فعاله")
    else:
        lines.append("┃ روی هرکدوم بزن تا وارد بشی")
    lines.extend(warn_lines)
    lines.append("╰━━━━━━━━━━━━━━━━━━━━━━╯")
    return "\n".join(lines)


def _group_status_text(chat_id: int) -> str:
    title = _chat_titles.get(chat_id, "نامشخص")
    security = _security_score(chat_id)
    total_keys = len(config.SETTING_LABELS)
    settings = _chat_settings[chat_id]
    active_keys = sum(1 for k in config.SETTING_LABELS if settings.get(k, False))

    lines = [
        "╭━━〔 📊 GROUP STATUS 〕━━╮",
        f"┃ گروه: {title}",
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n",
    ]
    for cat_key, cat in config.SETTING_CATEGORIES.items():
        progress = _category_progress(chat_id, cat_key)
        if progress is None:
            continue
        active, total = progress
        lines.append(f"{_status_dot(active, total)} {cat['title']} — {active}/{total}")

    lines.append("")
    lines.append(f"🛡 امنیت گروه (قفل‌ها + ضداسپم)\n{_progress_bar(security / 100)} {security}%")
    lines.append(f"⚙️ کل قابلیت‌های فعال: {active_keys} / {total_keys}")
    return "\n".join(lines)


def _group_picker_text() -> str:
    return (
        "╭━━〔 🤖 DIGIANTI 〕━━╮\n"
        "┃  GROUP CONTROL CENTER\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "🏠 کدوم گروه رو می‌خوای مدیریت کنی؟"
    )


def _group_picker_markup(chats) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(_chat_titles.get(cid, str(cid)), callback_data=f"adm:pick:{cid}")
        for cid in chats
    ]
    rows = _pair_rows(buttons)
    rows.append([InlineKeyboardButton("❌ بستن", callback_data="menu_close")])
    return InlineKeyboardMarkup(rows)


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not has_permission(update.effective_user.id, "menu"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    chat = update.effective_chat
    if chat.type == "private":
        groups = sorted(_known_chats)
        if not groups:
            await update.effective_message.reply_text(
                "هنوز هیچ گروهی شناخته نشده. اول ربات رو به یه گروه اضافه و ادمینش کن."
            )
            return
        await update.effective_message.reply_text(_group_picker_text(), reply_markup=_group_picker_markup(groups))
        return
    await update.effective_message.reply_text(
        _menu_header_text(chat.id),
        reply_markup=_build_main_menu_markup(chat.id),
    )


def _cycle_value(current, options):
    """مقدار بعدی رو از یه لیست گزینه‌ها برمی‌گردونه (با چرخش به اول اگه به آخر رسید)."""
    if current not in options:
        return options[0]
    return options[(options.index(current) + 1) % len(options)]


async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    # راهنمای دستورات (/help) برای همه کاربرها عمومیه (نه فقط ادمین‌های
    # مجاز پنل /menu)، پس این چهار مسیر باید قبل از چک دسترسی «menu» جواب
    # داده بشن. «بستن» هم چون فقط پیام خود ربات رو پاک می‌کنه، برای همه آزاده.
    if query.data == "menu_close":
        await query.answer()
        try:
            await query.message.delete()
        except Exception as e:
            logger.warning(f"بستن راهنما/منو ناموفق بود: {e}")
        return

    if query.data == "help_top":
        await query.answer()
        await query.edit_message_text(_help_top_text(), reply_markup=_build_help_top_markup(user.id))
        return

    if query.data and query.data.startswith("help_sec:"):
        key = query.data.split(":", 1)[1]
        section = HELP_SECTIONS.get(key)
        await query.answer()
        if not section:
            return
        label, admin_only, body = section
        if admin_only and not is_admin(user.id):
            await query.answer("این بخش فقط برای ادمین‌هاست.", show_alert=True)
            return
        await query.edit_message_text(
            HELP_HEADER + "\n" + body,
            reply_markup=_build_help_leaf_markup("help_top"),
        )
        return

    if query.data == "help_eco":
        await query.answer()
        await query.edit_message_text(
            HELP_HEADER + "\n        💰 راهنمای اقتصاد — یه بخش رو انتخاب کن",
            reply_markup=_build_help_eco_markup(user.id),
        )
        return

    if query.data and query.data.startswith("help_ecosec:"):
        key = query.data.split(":", 1)[1]
        section = ECONOMY_SECTIONS.get(key)
        await query.answer()
        if not section:
            return
        label, admin_only, body = section
        if admin_only and not is_admin(user.id):
            await query.answer("این بخش فقط برای ادمین‌هاست.", show_alert=True)
            return
        await query.edit_message_text(
            HELP_HEADER + "\n" + body,
            reply_markup=_build_help_leaf_markup("help_eco"),
        )
        return

    if not has_permission(user.id, "menu"):
        await query.answer("این دکمه فقط برای ادمین‌های مجازه.", show_alert=True)
        return

    if query.data and query.data.startswith("adm:pick:"):
        picked_chat_id = int(query.data.split(":", 2)[2])
        _admin_selected_chat[user.id] = picked_chat_id
        await query.answer()
        await query.edit_message_text(_menu_header_text(picked_chat_id), reply_markup=_build_main_menu_markup(picked_chat_id))
        return

    is_private = query.message.chat.type == "private"
    if is_private:
        chat_id = _admin_selected_chat.get(user.id)
        if chat_id is None:
            groups = sorted(_known_chats)
            await query.answer()
            if not groups:
                await query.edit_message_text("هنوز هیچ گروهی شناخته نشده.")
                return
            await query.edit_message_text(_group_picker_text(), reply_markup=_group_picker_markup(groups))
            return
    else:
        chat_id = query.message.chat_id

    if query.data == "pnd:cancel":
        _pending_panel_input.pop(user.id, None)
        await query.answer("لغو شد.")
        await query.edit_message_text(_menu_header_text(chat_id), reply_markup=_build_main_menu_markup(chat_id))
        return

    if query.data.startswith("ask:"):
        action = query.data.split(":", 1)[1]
        prompt = _PANEL_ASK_PROMPTS.get(action)
        if not prompt:
            await query.answer()
            return
        _set_pending_input(user.id, chat_id, action)
        await query.answer()
        await query.edit_message_text(
            f"{prompt}\n\n(۱۸۰ ثانیه وقت داری؛ پیام بعدیت همینجا توی همین گروه - یا همینجا توی پیوی اگه از پیوی مدیریت می‌کنی - به‌عنوان جواب حساب می‌شه)",
            reply_markup=_panel_ask_markup(),
        )
        return

    if query.data == "menu_back":
        await query.answer()
        if is_private:
            groups = sorted(_known_chats)
            await query.edit_message_text(_group_picker_text(), reply_markup=_group_picker_markup(groups))
        else:
            await query.edit_message_text(_menu_header_text(chat_id), reply_markup=_build_main_menu_markup(chat_id))
        return

    if query.data == "menu_help":
        await query.answer()
        await query.edit_message_text(_help_top_text(), reply_markup=_build_help_top_markup(user.id))
        return

    if query.data == "menu_status":
        await query.answer()
        await query.edit_message_text(_group_status_text(chat_id), reply_markup=_build_help_markup())
        return

    if query.data == "noop":
        await query.answer()
        return

    if query.data == "show_relations":
        await query.answer()
        await query.edit_message_text(
            _build_relations_text(chat_id),
            reply_markup=_build_help_markup(),
        )
        return

    if query.data == "econ_help":
        await query.answer()
        await query.edit_message_text(ECON_ADMIN_HELP_TEXT, reply_markup=_build_help_markup())
        return

    if query.data.startswith("act:"):
        action = query.data.split(":", 1)[1]
        await query.answer()
        if not get_setting(chat_id, "stats_enabled"):
            await query.message.reply_text("آمار فعالیت گروه خاموشه. از همین پنل، بخش «هسته‌ی ربات» روشنش کن.")
            return
        if action == "me":
            text = _activity_card_text(chat_id, user.id, user.first_name or user.username or str(user.id))
        elif action == "leaderboard":
            text = _leaderboard_text(chat_id, "alltime", "xp", user.id)
        elif action == "streaks":
            text = _streaks_leaderboard_text(chat_id)
        elif action == "achievements":
            text = _achievements_text(chat_id, user.id, user.first_name or user.username or str(user.id))
        else:
            return
        await query.message.reply_text(text)
        return

    if query.data == "term:stats":
        await query.answer()
        await query.edit_message_text(_terminator_stats_text(chat_id), reply_markup=_build_help_markup())
        return

    if query.data == "cycle:terminator_run_mode":
        if not has_permission(user.id, "manage_relationships"):
            await query.answer("این بخش فقط برای ادمین‌های مجاز به «manage_relationships»ست.", show_alert=True)
            return
        _terminator_mode[chat_id] = "silent" if _terminator_mode[chat_id] == "active" else "active"
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=_build_category_markup(chat_id, "relationships"))
        await save_state()
        return

    if query.data == "cycle:terminator_mode":
        if not has_permission(user.id, "manage_relationships"):
            await query.answer("این بخش فقط برای ادمین‌های مجاز به «manage_relationships»ست.", show_alert=True)
            return
        order = config.TERMINATOR_RESPONSE_MODES
        current = _terminator_default_mode[chat_id]
        next_index = (order.index(current) + 1) % len(order) if current in order else 0
        _terminator_default_mode[chat_id] = order[next_index]
        await query.answer(config.TERMINATOR_MODE_LABELS.get(order[next_index], order[next_index]))
        await query.edit_message_reply_markup(reply_markup=_build_category_markup(chat_id, "relationships"))
        await save_state()
        return

    if query.data == "xp:profile":
        await query.answer()
        text = profile_engine.build_profile_card(chat_id, user.id, user)
        await query.edit_message_text(text, reply_markup=_build_help_markup())
        return

    if query.data == "xp:top":
        await query.answer()
        ranking = sorted(_xp[chat_id].items(), key=lambda kv: kv[1], reverse=True)[: config.TOP_XP_COUNT]
        if not ranking:
            text = "هنوز کسی XP نگرفته."
        else:
            medals = ["🥇", "🥈", "🥉"]
            lines = ["🏆 برترین‌های گروه (بر اساس XP):"]
            for i, (uid, xp) in enumerate(ranking):
                name = _user_display_names.get(uid, str(uid))
                level, _into, _needed = _xp_progress(xp)
                prefix = medals[i] if i < 3 else f"{i + 1}."
                lines.append(f"{prefix} {name} — Lv.{level} | {xp} XP")
            text = "\n".join(lines)
        await query.edit_message_text(text, reply_markup=_build_help_markup())
        return

    if query.data == "econ:top":
        await query.answer()
        wallet = _wallet[chat_id]
        top = sorted(wallet.items(), key=lambda kv: kv[1], reverse=True)[:10]
        if not top:
            text = "هنوز هیچکس سکه‌ای نداره."
        else:
            medals = ["🥇", "🥈", "🥉"]
            lines = ["🏆 پولدارترین اعضای گروه:"]
            for i, (uid, bal) in enumerate(top):
                name = _user_display_names.get(uid, str(uid))
                prefix = medals[i] if i < 3 else f"{i + 1}."
                lines.append(f"{prefix} {name} — {bal} {config.CURRENCY_NAME}")
            text = "\n".join(lines)
        await query.edit_message_text(text, reply_markup=_build_help_markup())
        return

    if query.data.startswith(("econv:", "econset:", "econshop:", "econreset:", "econquick:")):
        if not has_permission(user.id, "manage_economy"):
            await query.answer("این بخش فقط برای ادمین‌های مجاز به «manage_economy»ست.", show_alert=True)
            return
        await _handle_econ_admin_callback(query, chat_id, query.data)
        return

    if query.data.startswith("panel:"):
        cat_key = query.data.split(":", 1)[1]
        cat = config.SETTING_CATEGORIES.get(cat_key)
        if not cat:
            await query.answer()
            return
        if cat_key == "econ_admin":
            if not has_permission(user.id, "manage_economy"):
                await query.answer("این بخش فقط برای ادمین‌های مجاز به «manage_economy»ست.", show_alert=True)
                return
            await query.answer()
            await query.edit_message_text(
                _econ_admin_header(chat_id),
                reply_markup=_build_econ_admin_markup(chat_id, "main"),
            )
            return
        await query.answer()
        await query.edit_message_text(
            _category_header_text(chat_id, cat_key),
            reply_markup=_build_category_markup(chat_id, cat_key),
        )
        return

    if query.data.startswith("sub:"):
        parts = query.data.split(":", 2)
        if len(parts) < 3:
            await query.answer()
            return
        _, cat_key, sub_key = parts
        subgroups = _SETTING_SUBGROUPS.get(cat_key)
        if not subgroups or sub_key not in subgroups:
            await query.answer()
            return
        await query.answer()
        await query.edit_message_text(
            _category_header_text(chat_id, cat_key, sub_key),
            reply_markup=_build_category_markup(chat_id, cat_key, sub_key),
        )
        return

    if query.data.startswith("botswitch:"):
        parts = query.data.split(":", 2)
        redraw_cat = parts[1] if len(parts) > 1 else None
        redraw_sub = parts[2] if len(parts) > 2 else None
        if not redraw_cat or redraw_cat not in config.SETTING_CATEGORIES:
            await query.answer()
            return
        new_val = not _chat_settings[chat_id].get("bot_enabled", False)
        _chat_settings[chat_id]["bot_enabled"] = new_val
        await query.answer(
            "✅ ربات روشن شد؛ از این به بعد تنظیمات این صفحه واقعاً اجرا می‌شن."
            if new_val else "⛔ ربات خاموش شد؛ هیچ تنظیمی دیگه اجرا نمی‌شه.",
            show_alert=True,
        )
        await query.edit_message_text(
            _category_header_text(chat_id, redraw_cat, redraw_sub),
            reply_markup=_build_category_markup(chat_id, redraw_cat, redraw_sub),
        )
        await save_state()
        return

    if query.data == "cycle:ai_reply_mode":
        current = _ai_reply_mode[chat_id]
        order = config.AI_REPLY_MODE_ORDER
        next_index = (order.index(current) + 1) % len(order) if current in order else 0
        _ai_reply_mode[chat_id] = order[next_index]
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=_build_category_markup(chat_id, "core"))
        await save_state()
        return

    if query.data == "cycle:welcome_theme":
        order = list(config.MESSAGE_THEMES.keys())
        current = _chat_theme.get(chat_id, config.DEFAULT_MESSAGE_THEME)
        next_index = (order.index(current) + 1) % len(order) if current in order else 0
        _chat_theme[chat_id] = order[next_index]
        await query.answer(config.MESSAGE_THEMES[order[next_index]]["label"])
        await query.edit_message_reply_markup(reply_markup=_build_category_markup(chat_id, "welcome"))
        await save_state()
        return

    if not query.data or not query.data.startswith("toggle:"):
        await query.answer()
        return

    key = query.data.split(":", 1)[1]
    if key not in config.SETTING_LABELS:
        await query.answer()
        return

    current = _chat_settings[chat_id].get(key, False)
    _chat_settings[chat_id][key] = not current
    cat_key = _KEY_TO_CATEGORY.get(key)
    sub_info = _KEY_TO_SUBGROUP.get(key)

    await query.answer()
    if sub_info:
        redraw_cat, redraw_sub = sub_info
        await query.edit_message_reply_markup(reply_markup=_build_category_markup(chat_id, redraw_cat, redraw_sub))
    elif cat_key:
        await query.edit_message_reply_markup(reply_markup=_build_category_markup(chat_id, cat_key))
    await save_state()


async def adminpanel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """میانبر برای پنل مدیریت؛ دقیقاً هم‌معنی /menu ست (برای دسترسی راحت‌تر از پیوی)."""
    await menu_command(update, context)


# ---------- پنل شخصی پیوی برای کاربرای عادی (/panel) ----------

def _user_known_group_ids(user_id: int) -> list:
    return [cid for cid in sorted(_known_chats) if user_id in _known_members.get(cid, set())]


def _dm_panel_text(user_id: int) -> str:
    ai_state = "🟢 روشن" if _dm_ai_enabled[user_id] else "🔴 خاموش"
    return (
        "╭━━〔 🙋 پنل شخصی 〕━━╮\n"
        f"┃ 🧠 هوش مصنوعی در پیوی: {ai_state}\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "از دکمه‌های زیر استفاده کن:"
    )


def _dm_panel_markup(user_id: int) -> InlineKeyboardMarkup:
    ai_label = "🔴 خاموش کردن هوش مصنوعی" if _dm_ai_enabled[user_id] else "🟢 روشن کردن هوش مصنوعی"
    rows = [
        [InlineKeyboardButton(ai_label, callback_data="dm:toggle_ai")],
        [InlineKeyboardButton("👤 پروفایل من در گروه‌ها", callback_data="dm:profiles")],
        [InlineKeyboardButton("❌ بستن", callback_data="dm:close")],
    ]
    return InlineKeyboardMarkup(rows)


async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat or chat.type != "private":
        await update.effective_message.reply_text("این پنل فقط توی پیام خصوصی با ربات کار می‌کنه.")
        return
    user = update.effective_user
    await update.effective_message.reply_text(
        _dm_panel_text(user.id),
        reply_markup=_dm_panel_markup(user.id),
    )


async def dm_panel_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    data = query.data or ""

    if data == "dm:close":
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    if data == "dm:toggle_ai":
        turning_on = not _dm_ai_enabled[user.id]
        if turning_on and not _user_known_group_ids(user.id):
            await query.answer(
                "این قابلیت فقط برای کسایی فعاله که توی یکی از گروه‌های ربات دیده شدن.",
                show_alert=True,
            )
            return
        _dm_ai_enabled[user.id] = turning_on
        await query.answer("✅ فعال شد" if turning_on else "⛔ غیرفعال شد")
        await query.edit_message_text(_dm_panel_text(user.id), reply_markup=_dm_panel_markup(user.id))
        await save_state()
        return

    if data == "dm:back":
        await query.answer()
        await query.edit_message_text(_dm_panel_text(user.id), reply_markup=_dm_panel_markup(user.id))
        return

    if data == "dm:profiles":
        await query.answer()
        groups = _user_known_group_ids(user.id)
        if not groups:
            await query.edit_message_text(
                "توی هیچ گروهی که ربات می‌شناستت دیده نشدی.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="dm:back")]]),
            )
            return
        rows = [
            [InlineKeyboardButton(_chat_titles.get(cid, str(cid)), callback_data=f"dm:profile:{cid}")]
            for cid in groups[:25]
        ]
        rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="dm:back")])
        await query.edit_message_text("گروهت رو انتخاب کن:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("dm:profile:"):
        cid = int(data.split(":", 2)[2])
        await query.answer()
        text = profile_engine.build_profile_card(cid, user.id, user)
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="dm:profiles")]]),
        )
        return

    await query.answer()


async def dm_ai_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پاسخ هوش مصنوعی توی پیوی ربات، فقط برای کاربرایی که از /panel روشنش کرده باشن."""
    message = update.effective_message
    user = update.effective_user
    if not message or not message.text or not user or user.is_bot:
        return
    if not _dm_ai_enabled[user.id]:
        return
    if _ai_client is None:
        return
    try:
        await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
    except Exception:
        pass
    ai_response = await ask_ai(message.text)
    if ai_response:
        await _safe_reply(message, ai_response)


# ---------- ۳) اطلاع‌رسانی ----------

async def notify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not has_permission(user.id, "notify"):
        await update.effective_message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return

    text = " ".join(context.args) if context.args else None
    if not text:
        await update.effective_message.reply_text(
            "استفاده: /notify متن پیام\nمثال: /notify فردا سرویس از ساعت ۱۰ صبح در دسترسه"
        )
        return

    sent, failed = 0, 0
    for chat_id in list(_known_chats):
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"📢 اطلاعیه:\n{text}")
            sent += 1
        except Exception as e:
            logger.warning(f"ارسال به چت {chat_id} ناموفق بود: {e}")
            failed += 1

    await update.effective_message.reply_text(
        f"ارسال شد به {sent} چت. ({failed} مورد ناموفق)"
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat and chat.type == "private" and not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "سلام! این ربات یه ربات مدیریت گروهه.\n"
            "اگه توی یکی از گروه‌های ربات عضوی، از /panel برای پنل شخصیت (هوش مصنوعی/پروفایل) استفاده کن.\n"
            f"برای ارتباط با مالک ربات: {config.OWNER_CONTACT}"
        )
        return
    await update.effective_message.reply_text(
        "سلام! من فعالم 🤖\nبرای مشاهده‌ی دستورات از /help استفاده کن."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user_id = update.effective_user.id

    # این ربات شخصیه: اگه یه غریبه توی پیوی /help بزنه، دستورات مدیریتی نشونش نمی‌دیم.
    # اگه فکر می‌کنی ادمینی ولی این پیام رو می‌بینی، یعنی آیدی عددی‌ت توی config.py
    # (لیست ADMIN_IDS) نیست - آیدی عددیت رو از @userinfobot بگیر و اونجا اضافه کن.
    if chat and chat.type == "private" and not is_admin(user_id):
        logger.info(f"کاربر {user_id} (غیرادمین) توی پیوی /help زد.")
        await update.effective_message.reply_text(
            "سلام! این ربات یه ربات مدیریت گروهه.\n"
            "اگه توی یکی از گروه‌های ربات عضوی، از /panel برای پنل شخصیت (هوش مصنوعی/پروفایل) استفاده کن.\n"
            f"برای ارتباط با مالک ربات: {config.OWNER_CONTACT}"
        )
        return

    # به‌جای یه متن غول‌پیکر یکجا (که هم از سقف طول پیام تلگرام رد می‌شد هم پیدا
    # کردن یه دستور توش سخت بود)، لیست بخش‌ها رو با دکمه می‌فرستیم؛ با زدن هر
    # دکمه فقط دستورهای همون بخش (فارسی + انگلیسی با هم) نشون داده می‌شه.
    await update.effective_message.reply_text(
        _help_top_text(),
        reply_markup=_build_help_top_markup(user_id),
    )

async def _periodic_save_loop():
    global _last_retention_cleanup, _last_moderation_decay
    while True:
        await asyncio.sleep(config.STATE_SAVE_INTERVAL_SECONDS)
        await save_state()
        await _check_captcha_timeouts()
        await _check_raid_expiry()
        await _check_lottery_expiry()
        await _check_enemy_expiry()
        await lock_engine.check_temp_lock_expiry(_bot_instance)
        await moderation_engine.check_expirations(_bot_instance)
        await poll_engine.check_expired_polls(_bot_instance)  # فاز ۳: بستن خودکار نظرسنجی‌های تایمردار
        if time.time() - _last_retention_cleanup > 6 * 3600:
            _last_retention_cleanup = time.time()
            await _activity_cleanup_retention()
        if time.time() - _last_moderation_decay > 6 * 3600:
            _last_moderation_decay = time.time()
            moderation_engine.decay_tick()


async def _post_init(application: Application):
    global _bot_instance
    _bot_instance = application.bot
    application.bot_data["periodic_save_task"] = asyncio.create_task(_periodic_save_loop())


async def _post_shutdown(application: Application):
    task = application.bot_data.get("periodic_save_task")
    if task:
        task.cancel()
    await save_state()  # آخرین ذخیره قبل از خاموش شدن کامل


# ---------- کلمات فارسی معادل دستورها (استفاده در control_words_handler) ----------
# اسم هر کلید دقیقاً یه کلمه‌ست (بدون فاصله، برای ترکیب‌ها از نیم‌فاصله ‌استفاده شده)
# چون parts[0] توی _parse_target_and_reason/_get_command_arg_text همیشه به‌عنوان
# «کلمه‌ی فرمان» کنار گذاشته می‌شه، هر چی بعدش بیاد (دلیل/دقیقه/آیدی) درست پردازش می‌شه.
# نکته: کلماتی که همین الان به‌عنوان SETTING_KEYWORDS یا trigger اختصاصی استفاده شدن
# (مثل «پروفایل من»، «روزانه»، «فروشگاه»، «نظرسنجی») اینجا تکرار نشدن تا تداخل نشه.
PERSIAN_COMMAND_ALIASES = {
    # عمومی
    "شروع": start_command,
    "راهنما": help_command,
    "آمار": stats_command,
    "فعال ترین ها": top_command,
    "آمار گروه": group_stats_command,
    "فعالیت من": activity_command,
    "برترین های فعالیت": leaderboard_command,
    "دستاوردهای من": achievements_command,
    "روابط": relations_list_command,
    "دشمن ها": enemies_list_command,
    "دوست ها": friends_list_command,
    "اطلاعات دشمن": enemy_info_command,
    "اطلاعات دوست": friend_info_command,
    "مد هدف": target_mode_command,
    "کول داون هدف": target_cooldown_command,
    "کلمات دشمن": enemywords_list_command,
    "کلمات دوست": friendwords_list_command,
    "اخطارهای من": warnings_command,
    "میوت شده ها": muted_list_command,
    "لیست بن": banlist_command,
    "لیست وی آی پی": vip_list_command,
    "جوک": joke_command,
    "فال": fortune_command,
    # پروفایل/XP اضافی
    "برترین ها": topxp_command,
    "ریست لقب": resetlqab_command,
    # اقتصاد اضافی
    "انتقال": pay_command,
    "خرید": buy_command,
    # نظرسنجی/قرعه‌کشی
    "نظرسنجی جدید": poll_command,
    "قرعه کشی": lottery_command,
    "پایان قرعه کشی": lottery_end_command,
    # مدیریت کاربر (فقط ادمین‌های مجاز - خود دستورها این چک رو دارن)
    "اخطار": warn_command,
    "لغو اخطار": unwarn_command,
    "صفر کردن اخطار": resetwarnings_command,
    "میوت": mute_command,
    "ان میوت": unmute_command,
    "بن": ban_command,
    "ان بن": unban_command,
    "ارتقا": promote_command,
    "عزل": demote_command,
    "پاکسازی": purge_command,
    "پاکسازی کاربر": purgeuser_command,
    "پیش‌نمایش پاکسازی": purge_preview_command,
    "پیشنمایش پاکسازی": purge_preview_command,
    "پیش نمایش پاکسازی": purge_preview_command,
    "آمار پاکسازی": purgestats_command,
    "اضافه وی آی پی": vip_add_command,
    "حذف وی آی پی": vip_remove_command,
    "اضافه بلک لیست": blacklist_add_command,
    "حذف بلک لیست": blacklist_remove_command,
    "دشمن کن": enemy_add_command,
    "دوست کن": friend_add_command,
    "لغو دشمن کن": enemy_remove_command,
    "لغو دوست کن": friend_remove_command,
    "اضافه کلمه دشمن": enemyword_add_command,
    "حذف کلمه دشمن": enemyword_remove_command,
    "اضافه کلمه دوست": friendword_add_command,
    "حذف کلمه دوست": friendword_remove_command,
    "تنظیم حد اخطار": setwarnlimit_command,
    "تنظیم زمان میوت": setmutetime_command,
    "لینک جدید": newlink_command,
    "اطلاعیه": notify_command,
    "لیست گروه ها": groups_overview_command,
    "پاکسازی بن لیست": clearbanlist_command,
    "متن خوشامد": welcome_text_command,
    "متن ترک": leave_text_command,
    "تم خوشامد": welcome_theme_command,
    # پنل ویژه ادمین (سکه/XP/لول/اقتصاد/فروشگاه)
    "تنظیم سکه": setcoins_command,
    "اضافه سکه": addcoins_command,
    "تنظیم سکه همه": setcoinsall_command,
    "اضافه سکه همه": addcoinsall_command,
    "تنظیم ایکسپی": setxp_command,
    "اضافه ایکسپی": addxp_command,
    "تنظیم ایکسپی همه": setxpall_command,
    "اضافه ایکسپی همه": addxpall_command,
    "تنظیم لول": setlevel_command,
    "تنظیم لول همه": setlevelall_command,
    "تنظیم اقتصاد": seteco_command,
    "تنظیم قیمت فروشگاه": setshopprice_command,
    # پنل‌ها
    "پنل": menu_command,
    "پنل مدیریت": menu_command,
    "پنل من": panel_command,
    "پنل ادمین": adminpanel_command,

    # فعالیت اضافی
    "فیلتر فعالیت": activity_filter_command,

    # دوست/دشمن اضافی
    "ترمیناتور": terminator_command,

    # تگ
    "تگ": tag_command,

    # مدیریت پیشرفته (تاریخچه و آمار)
    "تاریخچه مدیریت": modhistory_command,
    "آمار مدیریت": modstats_command,
    "لیست میوت های فعال": mutes_command,
    "لیست بن های فعال": bans_command,
    "تنظیمات مدیریت": setmodconfig_command,

    # قفل‌ها (lock_engine)
    "قفل": lock_engine.lock_command,
    "بازکردن قفل": lock_engine.unlock_command,
    "لیست قفل ها": lock_engine.locks_command,
    "اطلاعات قفل": lock_engine.lockinfo_command,
    "آمار قفل": lock_engine.lockstats_command,
    "تنظیم قفل": lock_engine.lockconfig_command,
    "پروفایل قفل": lock_engine.lockprofile_command,
    "تست قفل": lock_engine.locktest_command,
    "چک دسترسی": lock_engine.lockcheck_command,

    # ضداسپم (spam_engine)
    "ضد اسپم": spam_engine.antispam_command,
    "ضد بات": spam_engine.antibot_command,
    "کپچا": spam_engine.captcha_command,
    "ضد ریید": spam_engine.raid_command,
    "آمار اسپم": spam_engine.spamstats_command,
    "امنیت": spam_engine.security_command,
    "امنیت کاربر": spam_engine.security_user_command,
    "تنظیم اسپم": spam_engine.spamconfig_command,
    "لیست سفید": spam_engine.whitelist_command,
    "بلک لیست اسپم": spam_engine.spamblacklist_command,

    # پروفایل/لقب پیشرفته (profile_engine)
    "لقب": profile_engine.lqab_command,
    "لیست لقب ها": profile_engine.toplqab_command,
    "احترام": profile_engine.rep_command,
    "وضعیت من": profile_engine.stats_command,
    "پرستیژ": profile_engine.prestige_command,
    "کالکشن من": profile_engine.collections_command,
    "غذا خوردن": profile_engine.eat_command,
    "استراحت": profile_engine.rest_command,
    "منطقه": economy_district.district_command,
    "تغییر منطقه": economy_district.move_district_command,

    # خودرو (economy_vehicle) — فاز ۸
    "خودرو": economy_vehicle.vehicle_types_command,
    "خرید خودرو": economy_vehicle.buy_vehicle_command,
    "فروش خودرو": economy_vehicle.sell_vehicle_command,
    "خودرو من": economy_vehicle.my_vehicle_command,
    "انتقال خودرو": economy_vehicle.transfer_vehicle_command,
    "فروش خودرو به": economy_vehicle.sell_vehicle_to_command,
    "تعمیر خودرو": economy_vehicle.repair_vehicle_command,
    "سوخت گیری": economy_vehicle.refuel_vehicle_command,
    "سوخت‌گیری": economy_vehicle.refuel_vehicle_command,
    "بیمه خودرو": economy_vehicle.insure_vehicle_command,
    "مالیات خودرو": economy_vehicle.pay_vehicle_tax_command,
    "رانندگی": economy_vehicle.drive_vehicle_command,
    "تاریخچه خودرو": economy_vehicle.vehicle_history_command,
    "تیونینگ خودرو": economy_vehicle.tune_vehicle_command,

    # کسب‌وکار (economy_business) — فاز ۱۲
    "کسب‌وکارها": economy_business.business_types_command,
    "خرید کسب‌وکار": economy_business.buy_business_command,
    "فروش کسب‌وکار": economy_business.sell_business_command,
    "کسب‌وکار من": economy_business.my_business_command,
    "استخدام": economy_business.hire_command,
    "اخراج": economy_business.fire_command,
    "خرید موجودی": economy_business.buy_inventory_command,
    "قیمت کسب‌وکار": economy_business.set_price_command,
    "تبلیغات": economy_business.advertise_command,
    "ارتقای کسب‌وکار": economy_business.upgrade_business_command,
    "مالیات کسب‌وکار": economy_business.pay_business_tax_command,
    "سود کسب‌وکار": economy_business.business_profit_command,
    "تاریخچه کسب‌وکار": economy_business.business_history_command,

    # اقتصاد پیشرفته (economy_engine)
    "تراکنش ها": economy_engine.transactions_command,
    "آمار اقتصاد": economy_engine.economystats_command,
    "دادن آیتم": economy_engine.giveitem_command,
    "حذف آیتم": economy_engine.removeitem_command,

    # 🛍 پنل کاربریِ اقتصاد (economy_panel) — دسترسیِ همه‌ی کاربرا، نه فقط ادمین
    "پنل اقتصاد من": economy_panel.eco_panel_command,
    "پنل اقتصاد": economy_panel.eco_panel_command,
    "منوی اقتصاد من": economy_panel.eco_panel_command,

    # فاز ۲: درآمد و شغل (economy_jobs)
    "هاپ هاپ": economy_jobs.work_command,
    "کار": economy_jobs.work_command,
    "درآمد": economy_jobs.income_command,
    "حقوق": economy_jobs.salary_command,
    "شغل": economy_jobs.job_command,
    "مشاغل": economy_jobs.jobs_command,
    "انتخاب شغل": economy_jobs.choosejob_command,
    "ترک شغل": economy_jobs.quitjob_command,
    "ارتقای شغل": economy_jobs.promote_command,
    "سطح شغل": economy_jobs.joblevel_command,

    # فاز ۲: ماموریت (economy_missions)
    "ماموریت": economy_missions.missions_command,
    "ماموریت ها": economy_missions.missions_command,
    "ماموریت‌ها": economy_missions.missions_command,

    # فاز ۳: بانک (economy_bank)
    "بانک": economy_bank.bank_command,
    "حساب": economy_bank.bank_command,
    "سپرده": economy_bank.deposit_command,
    "سپرده بلندمدت": economy_bank.long_deposit_command,
    "برداشت": economy_bank.withdraw_command,
    "برداشت بلندمدت": economy_bank.long_withdraw_command,
    "سود": economy_bank.interest_command,
    "ارتقای بانک": economy_bank.bank_upgrade_command,
    "اعتبار": economy_bank.credit_command,
    "وام": economy_bank.take_loan_command,
    "پرداخت وام": economy_bank.pay_loan_command,

    # فاز ۳: بازار و ترید (economy_market)
    "بازار": economy_market.market_command,
    "ارز": economy_market.market_command,
    "قیمت": economy_market.price_command,
    "فروش": economy_market.sell_command,
    "سبد": economy_market.portfolio_command,
    "ترید": economy_market.trade_command,

    # فاز ۴: بیمه (economy_security)
    "بیمه": economy_security.insurance_command,
    "وضعیت بیمه": economy_security.insurance_command,
    "خرید بیمه": economy_security.buy_insurance_command,
    "ارتقای بیمه": economy_security.upgrade_insurance_command,

    # فاز ۴: دزدی + زندان (economy_theft)
    "دزدی": economy_theft.steal_command,
    "ریپلای دزدی": economy_theft.steal_command,
    "تحت تعقیب": economy_theft.wanted_command,
    "زندان": economy_theft.jail_command,
    "فرار": economy_theft.escape_command,

    # فاز ۵: شهر (economy_city)
    "شهر": economy_city.city_command,
    "ساخت شهر": economy_city.build_city_command,
    "ارتقای شهر": economy_city.upgrade_city_command,
    "ساختمان ها": economy_city.buildings_command,
    "ساختمان‌ها": economy_city.buildings_command,

    # فاز ۵: املاک (economy_property)
    "املاک": economy_property.property_types_command,
    "خرید ملک": economy_property.buy_property_command,
    "فروش ملک": economy_property.sell_property_command,
    "ملک من": economy_property.my_property_command,
    "اشتراک ملک": economy_property.share_property_command,
    "لغو اشتراک ملک": economy_property.unshare_property_command,
    "انتقال ملک": economy_property.transfer_property_command,
    "فروش ملک به": economy_property.sell_property_to_command,
    "اجاره ملک": economy_property.rent_property_command,
    "لیست اجاره": economy_property.rentals_command,
    "اجاره کردن": economy_property.rent_take_command,
    "تاریخچه ملک": economy_property.property_history_command,

    # فاز ۶: ازدواج (economy_marriage)
    "درخواست ازدواج": economy_marriage.propose_command,
    "ازدواج": economy_marriage.marry_command,
    "همسر": economy_marriage.partner_command,
    "طلاق": economy_marriage.divorce_command,
    "هدیه": economy_marriage.gift_command,

    # فاز ۶: پت (economy_pet)
    "پت": economy_pet.pet_command,
    "پت ها": economy_pet.pets_list_command,
    "پت‌ها": economy_pet.pets_list_command,
    "خرید پت": economy_pet.buy_pet_command,
    "غذا": economy_pet.feed_command,
    "آموزش پت": economy_pet.train_command,
    "ارتقای پت": economy_pet.evolve_command,
    "فایت": economy_pet.fight_command,

    # فاز ۶: بازی‌ها (economy_games)
    "تاس": economy_games.dice_command,
    "شیر یا خط": economy_games.coinflip_command,
    "حدس": economy_games.guess_command,
    "اکس او": economy_games.xo_command,
    "بازی": economy_games.game_command,

    # فاز ۷: کیف/اینونتوری (تکمیل myitems_command فعلی) + استفاده از آیتم
    "کیف": myitems_command,
    "اینونتوری": myitems_command,
    "استفاده از": economy_inventory.use_item_command,
    "ظرفیت انبار": economy_inventory.capacity_command,

    # فاز ۷: بازار سیاه (economy_blackmarket)
    "بازار سیاه": economy_blackmarket.blackmarket_command,
    "خرید کالا": economy_blackmarket.buy_command,
    "فروش کالا": economy_blackmarket.sell_command,
    "کالای من": economy_blackmarket.my_items_command,
    "حراجی": economy_blackmarket.auction_command,

    # فاز ۷: دنیای زیرزمینی (economy_underground)
    "دارک وب": economy_underground.darkweb_command,
    "قرارداد": economy_underground.status_command,
    "جاسوس": economy_underground.spy_command,
    "هکر": economy_underground.hacker_command,
    "ماموریت زیرزمینی": economy_underground.random_mission_command,

    # فاز ۸: دستاورد + Net Worth + Leaderboard (economy_achievements / economy_leaderboard)
    "ارزش خالص": economy_achievements.networth_command,
    "دستاوردها": achievements_command,
    "ثروتمندان": economy_leaderboard.richest_command,
    "برترین‌ها": economy_leaderboard.leaderboard_command,
    "رتبه اقتصاد": economy_leaderboard.my_rank_command,

    # فاز ۹: رویداد + فروشگاه ویژه + سلامت اقتصاد (economy_events / economy_shop2 / economy_balance)
    "رویداد": economy_events.event_command,
    "فورس رویداد": economy_events.force_event_command,
    "فروشگاه ویژه": economy_shop2.daily_shop_command,
    "خرید ویژه": economy_shop2.buy_daily_deal_command,
    "سلامت اقتصاد": economy_balance.economy_health_command,

    # فاز ۱۰: منو + Toggle ماژول‌ها + Admin (economy_admin)
    "منوی اقتصاد": economy_admin.economy_menu_command,
    "ماژول های اقتصاد": economy_admin.economy_modules_command,
    "ماژول‌های اقتصاد": economy_admin.economy_modules_command,
    "بازرسی اقتصاد": economy_admin.inspect_economy_command,
    "ریست اقتصاد کاربر": economy_admin.reset_user_economy_command,
    "فریز اقتصاد": economy_admin.freeze_economy_command,
    "رفع فریز اقتصاد": economy_admin.unfreeze_economy_command,
    "بررسی تراکنش": economy_admin.lookup_transaction_command,
    "برگشت تراکنش": economy_admin.rollback_transaction_command,
    "گزارش ادمین": economy_admin.admin_log_command,
    "راهنمای اقتصاد": economy_admin.economy_help_command,

    # نظرسنجی پیشرفته (poll_engine)
    "بستن نظرسنجی": poll_engine.pollclose_command,
    "حذف نظرسنجی": poll_engine.polldelete_command,
    "بازکردن نظرسنجی": poll_engine.pollreopen_command,
    "رای دهندگان": poll_engine.pollvoters_command,
    "مخفی نظرسنجی": poll_engine.pollhide_command,
    "تاریخچه نظرسنجی": poll_engine.pollhistory_command,
}

# طولانی‌ترین کلید (بر حسب تعداد کلمه) توی دیکشنری بالا - برای تشخیص عبارت‌های چندکلمه‌ای لازمه
_ALIAS_MAX_WORDS = max((len(k.split()) for k in PERSIAN_COMMAND_ALIASES), default=1)


def main():
    if config.BOT_TOKEN == "":
        raise SystemExit(
            "توکن ربات تنظیم نشده. متغیر محیطی BOT_TOKEN رو ست کن یا در config.py مقداردهی کن."
        )

    load_state()  # اگه از قبل چیزی ذخیره شده، برش‌گردون

    # کانکشن‌پول بزرگ‌تر + تایم‌اوت معقول، چون با concurrent_updates ممکنه هم‌زمان چندتا
    # درخواست به تلگرام بره (چند گروه با هم فعال باشن) و pool پیش‌فرض (۱ کانکشن) گلوگاه می‌شه.
    request = HTTPXRequest(
        connection_pool_size=16,
        pool_timeout=10.0,
        connect_timeout=10.0,
        read_timeout=15.0,
        write_timeout=15.0,
    )

    app: Application = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .request(request)
        .concurrent_updates(True)  # آپدیت‌های چند گروه/چند کاربر رو هم‌زمان پردازش کن، نه صف پشت سر هم
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    app.add_error_handler(global_error_handler)

    # دستورات پایه
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("notify", notify_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("top", top_command))
    app.add_handler(CommandHandler("groupstats", group_stats_command))
    app.add_handler(CommandHandler("activity", activity_command))
    app.add_handler(CommandHandler("leaderboard", leaderboard_command))
    app.add_handler(CommandHandler("achievements", achievements_command))
    app.add_handler(CommandHandler("activityfilter", activity_filter_command))
    app.add_handler(CommandHandler("welcome_text", welcome_text_command))
    app.add_handler(CommandHandler("leave_text", leave_text_command))
    app.add_handler(CommandHandler("welcome_theme", welcome_theme_command))
    app.add_handler(CommandHandler("welcome", welcome_toggle_command))
    app.add_handler(CommandHandler("leave", leave_toggle_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("addenemy", enemy_add_command))
    app.add_handler(CommandHandler("addfriend", friend_add_command))
    app.add_handler(CommandHandler("removeenemy", enemy_remove_command))
    app.add_handler(CommandHandler("removefriend", friend_remove_command))
    app.add_handler(CommandHandler("enemies", enemies_list_command))
    app.add_handler(CommandHandler("friends", friends_list_command))
    app.add_handler(CommandHandler("enemyinfo", enemy_info_command))
    app.add_handler(CommandHandler("friendinfo", friend_info_command))
    app.add_handler(CommandHandler("terminator", terminator_command))
    app.add_handler(CommandHandler("targetmode", target_mode_command))
    app.add_handler(CommandHandler("targetcooldown", target_cooldown_command))
    app.add_handler(CommandHandler("blacklist_add", blacklist_add_command))
    app.add_handler(CommandHandler("blacklist_remove", blacklist_remove_command))
    app.add_handler(CommandHandler("addenemyword", enemyword_add_command))
    app.add_handler(CommandHandler("removeenemyword", enemyword_remove_command))
    app.add_handler(CommandHandler("addfriendword", friendword_add_command))
    app.add_handler(CommandHandler("removefriendword", friendword_remove_command))
    app.add_handler(CommandHandler("enemywords", enemywords_list_command))
    app.add_handler(CommandHandler("friendwords", friendwords_list_command))
    app.add_handler(CommandHandler("warn", warn_command))
    app.add_handler(CommandHandler("mute", mute_command))
    app.add_handler(CommandHandler("unmute", unmute_command))
    app.add_handler(CommandHandler("resetwarnings", resetwarnings_command))
    app.add_handler(CommandHandler("warnings", warnings_command))
    app.add_handler(CommandHandler("muted", muted_list_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("promote", promote_command))
    app.add_handler(CommandHandler("demote", demote_command))
    app.add_handler(CommandHandler("purge", purge_command))
    app.add_handler(CommandHandler("purgeuser", purgeuser_command))
    app.add_handler(CommandHandler("purgestats", purgestats_command))
    app.add_handler(CommandHandler("groups", groups_overview_command))
    app.add_handler(CommandHandler("banlist", banlist_command))
    app.add_handler(CommandHandler("clearbanlist", clearbanlist_command))
    app.add_handler(CommandHandler("unwarn", unwarn_command))
    app.add_handler(CommandHandler("addvip", vip_add_command))
    app.add_handler(CommandHandler("removevip", vip_remove_command))
    app.add_handler(CommandHandler("viplist", vip_list_command))
    app.add_handler(CommandHandler("newlink", newlink_command))
    app.add_handler(CommandHandler("joke", joke_command))
    app.add_handler(CommandHandler("fortune", fortune_command))
    app.add_handler(CommandHandler("tag", tag_command))
    app.add_handler(CommandHandler("relations", relations_list_command))
    app.add_handler(CommandHandler("setwarnlimit", setwarnlimit_command))
    app.add_handler(CommandHandler("setmutetime", setmutetime_command))

    # ---------- 🛡️ DIGIANTI PROFESSIONAL MODERATION ENGINE ----------
    app.add_handler(CommandHandler("modhistory", modhistory_command))
    app.add_handler(CommandHandler("modstats", modstats_command))
    app.add_handler(CommandHandler("mutes", mutes_command))
    app.add_handler(CommandHandler("bans", bans_command))
    app.add_handler(CommandHandler("setmodconfig", setmodconfig_command))

    # ---------- 🔐 DIGIANTI SMART LOCK ENGINE 2.0 ----------
    app.add_handler(CommandHandler("lock", lock_engine.lock_command))
    app.add_handler(CommandHandler("unlock", lock_engine.unlock_command))
    app.add_handler(CommandHandler("locks", lock_engine.locks_command))
    app.add_handler(CommandHandler("lockinfo", lock_engine.lockinfo_command))
    app.add_handler(CommandHandler("lockstats", lock_engine.lockstats_command))
    app.add_handler(CommandHandler("lockconfig", lock_engine.lockconfig_command))
    app.add_handler(CommandHandler("lockprofile", lock_engine.lockprofile_command))
    app.add_handler(CommandHandler("locktest", lock_engine.locktest_command))
    app.add_handler(CommandHandler("lockcheck", lock_engine.lockcheck_command))
    app.add_handler(CallbackQueryHandler(lock_engine.panel_callback_handler, pattern=r"^lockpanel:"))

    # ---------- 🛡️ DIGIANTI SMART ANTI-SPAM & ANTI-BOT ENGINE 3.0 ----------
    app.add_handler(CommandHandler("antispam", spam_engine.antispam_command))
    app.add_handler(CommandHandler("antibot", spam_engine.antibot_command))
    app.add_handler(CommandHandler("captcha", spam_engine.captcha_command))
    app.add_handler(CommandHandler("raid", spam_engine.raid_command))
    app.add_handler(CommandHandler("spamstats", spam_engine.spamstats_command))
    app.add_handler(CommandHandler("security", spam_engine.security_command))
    app.add_handler(CommandHandler("security_user", spam_engine.security_user_command))
    app.add_handler(CommandHandler("spamconfig", spam_engine.spamconfig_command))
    app.add_handler(CommandHandler("whitelist", spam_engine.whitelist_command))
    app.add_handler(CommandHandler("spamblacklist", spam_engine.spamblacklist_command))
    app.add_handler(CallbackQueryHandler(spam_engine.captcha_callback_handler, pattern=r"^spamcaptcha:"))
    app.add_handler(CallbackQueryHandler(spam_engine.panel_callback_handler, pattern=r"^secpanel:"))
    app.add_handler(CallbackQueryHandler(purge_engine.purge_callback_handler, pattern=r"^purgeop:"))

    # پروفایل / XP / لقب اختصاصی
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("rank", rank_command))
    app.add_handler(CommandHandler("level", level_command))
    app.add_handler(CommandHandler("toplevel", topxp_command))
    app.add_handler(CommandHandler("setlqab", setlqab_command))
    app.add_handler(CommandHandler("removelqab", removelqab_command))
    app.add_handler(CommandHandler("resetlqab", resetlqab_command))
    app.add_handler(CommandHandler("lqab", profile_engine.lqab_command))
    app.add_handler(CommandHandler("toplqab", profile_engine.toplqab_command))
    app.add_handler(CommandHandler("rep", profile_engine.rep_command))
    app.add_handler(CommandHandler("mystats", profile_engine.stats_command))
    app.add_handler(CommandHandler("prestige", profile_engine.prestige_command))
    app.add_handler(CommandHandler("collections", profile_engine.collections_command))
    app.add_handler(CommandHandler("eat", profile_engine.eat_command))
    app.add_handler(CommandHandler("rest", profile_engine.rest_command))
    app.add_handler(CommandHandler("district", economy_district.district_command))
    app.add_handler(CommandHandler("movedistrict", economy_district.move_district_command))
    app.add_handler(CommandHandler("vehicle", economy_vehicle.vehicle_types_command))
    app.add_handler(CommandHandler("buyvehicle", economy_vehicle.buy_vehicle_command))
    app.add_handler(CommandHandler("sellvehicle", economy_vehicle.sell_vehicle_command))
    app.add_handler(CommandHandler("myvehicle", economy_vehicle.my_vehicle_command))
    app.add_handler(CommandHandler("transfervehicle", economy_vehicle.transfer_vehicle_command))
    app.add_handler(CommandHandler("sellvehicleto", economy_vehicle.sell_vehicle_to_command))
    app.add_handler(CommandHandler("repairvehicle", economy_vehicle.repair_vehicle_command))
    app.add_handler(CommandHandler("refuel", economy_vehicle.refuel_vehicle_command))
    app.add_handler(CommandHandler("insurevehicle", economy_vehicle.insure_vehicle_command))
    app.add_handler(CommandHandler("vehicletax", economy_vehicle.pay_vehicle_tax_command))
    app.add_handler(CommandHandler("drive", economy_vehicle.drive_vehicle_command))
    app.add_handler(CommandHandler("vehiclehistory", economy_vehicle.vehicle_history_command))
    app.add_handler(CommandHandler("tunevehicle", economy_vehicle.tune_vehicle_command))
    app.add_handler(CommandHandler("businesses", economy_business.business_types_command))
    app.add_handler(CommandHandler("buybusiness", economy_business.buy_business_command))
    app.add_handler(CommandHandler("sellbusiness", economy_business.sell_business_command))
    app.add_handler(CommandHandler("mybusiness", economy_business.my_business_command))
    app.add_handler(CommandHandler("hire", economy_business.hire_command))
    app.add_handler(CommandHandler("fire", economy_business.fire_command))
    app.add_handler(CommandHandler("buyinventory", economy_business.buy_inventory_command))
    app.add_handler(CommandHandler("setbusinessprice", economy_business.set_price_command))
    app.add_handler(CommandHandler("advertise", economy_business.advertise_command))
    app.add_handler(CommandHandler("upgradebusiness", economy_business.upgrade_business_command))
    app.add_handler(CommandHandler("businesstax", economy_business.pay_business_tax_command))
    app.add_handler(CommandHandler("businessprofit", economy_business.business_profit_command))
    app.add_handler(CommandHandler("businesshistory", economy_business.business_history_command))

    # اقتصاد داخلی
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("daily", daily_command))
    app.add_handler(CommandHandler("pay", pay_command))
    app.add_handler(CommandHandler("shop", shop_command))
    app.add_handler(CommandHandler("buy", buy_command))
    app.add_handler(CommandHandler("myitems", myitems_command))
    app.add_handler(CommandHandler("inventory", myitems_command))  # فاز ۲: هم‌نام‌تر با درخواست، همون Handler
    app.add_handler(CommandHandler("transactions", economy_engine.transactions_command))
    app.add_handler(CommandHandler("economystats", economy_engine.economystats_command))
    app.add_handler(CommandHandler("giveitem", economy_engine.giveitem_command))
    app.add_handler(CommandHandler("removeitem", economy_engine.removeitem_command))

    # فاز ۲: Job System + Income + Missions (economy_jobs.py / economy_missions.py)
    app.add_handler(CommandHandler("work", economy_jobs.work_command))
    app.add_handler(CommandHandler("income", economy_jobs.income_command))
    app.add_handler(CommandHandler("salary", economy_jobs.salary_command))
    app.add_handler(CommandHandler("jobs", economy_jobs.jobs_command))
    app.add_handler(CommandHandler("job", economy_jobs.job_command))
    app.add_handler(CommandHandler("choosejob", economy_jobs.choosejob_command))
    app.add_handler(CommandHandler("quitjob", economy_jobs.quitjob_command))
    app.add_handler(CommandHandler("joblevel", economy_jobs.joblevel_command))
    app.add_handler(CommandHandler("missions", economy_missions.missions_command))

    # فاز ۳: Bank + Market + Trading (economy_bank.py / economy_market.py)
    app.add_handler(CommandHandler("bank", economy_bank.bank_command))
    app.add_handler(CommandHandler("deposit", economy_bank.deposit_command))
    app.add_handler(CommandHandler("longdeposit", economy_bank.long_deposit_command))
    app.add_handler(CommandHandler("longwithdraw", economy_bank.long_withdraw_command))
    app.add_handler(CommandHandler("withdraw", economy_bank.withdraw_command))
    app.add_handler(CommandHandler("interest", economy_bank.interest_command))
    app.add_handler(CommandHandler("bankupgrade", economy_bank.bank_upgrade_command))
    app.add_handler(CommandHandler("credit", economy_bank.credit_command))
    app.add_handler(CommandHandler("loan", economy_bank.take_loan_command))
    app.add_handler(CommandHandler("payloan", economy_bank.pay_loan_command))
    app.add_handler(CommandHandler("market", economy_market.market_command))
    app.add_handler(CommandHandler("price", economy_market.price_command))
    app.add_handler(CommandHandler("sell", economy_market.sell_command))
    app.add_handler(CommandHandler("portfolio", economy_market.portfolio_command))
    app.add_handler(CommandHandler("trade", economy_market.trade_command))

    # فاز ۴: بیمه + دزدی + زندان (economy_security.py / economy_theft.py)
    app.add_handler(CommandHandler("insurance", economy_security.insurance_command))
    app.add_handler(CommandHandler("buyinsurance", economy_security.buy_insurance_command))
    app.add_handler(CommandHandler("upgradeinsurance", economy_security.upgrade_insurance_command))
    app.add_handler(CommandHandler("steal", economy_theft.steal_command))
    app.add_handler(CommandHandler("wanted", economy_theft.wanted_command))
    app.add_handler(CommandHandler("jail", economy_theft.jail_command))
    app.add_handler(CommandHandler("escape", economy_theft.escape_command))

    # فاز ۵: شهر + املاک (economy_city.py / economy_property.py)
    app.add_handler(CommandHandler("city", economy_city.city_command))
    app.add_handler(CommandHandler("foundcity", economy_city.build_city_command))
    app.add_handler(CommandHandler("upgradecity", economy_city.upgrade_city_command))
    app.add_handler(CommandHandler("build", economy_city.buildings_command))
    app.add_handler(CommandHandler("property", economy_property.property_types_command))
    app.add_handler(CommandHandler("buyproperty", economy_property.buy_property_command))
    app.add_handler(CommandHandler("sellproperty", economy_property.sell_property_command))
    app.add_handler(CommandHandler("myproperty", economy_property.my_property_command))
    app.add_handler(CommandHandler("shareproperty", economy_property.share_property_command))
    app.add_handler(CommandHandler("unshareproperty", economy_property.unshare_property_command))
    app.add_handler(CommandHandler("transferproperty", economy_property.transfer_property_command))
    app.add_handler(CommandHandler("sellpropertyto", economy_property.sell_property_to_command))
    app.add_handler(CommandHandler("rentproperty", economy_property.rent_property_command))
    app.add_handler(CommandHandler("rentals", economy_property.rentals_command))
    app.add_handler(CommandHandler("takerent", economy_property.rent_take_command))
    app.add_handler(CommandHandler("propertyhistory", economy_property.property_history_command))

    # فاز ۶: ازدواج + پت + بازی‌ها (economy_marriage.py / economy_pet.py / economy_games.py)
    app.add_handler(CommandHandler("propose", economy_marriage.propose_command))
    app.add_handler(CommandHandler("marry", economy_marriage.marry_command))
    app.add_handler(CommandHandler("partner", economy_marriage.partner_command))
    app.add_handler(CommandHandler("divorce", economy_marriage.divorce_command))
    app.add_handler(CommandHandler("gift", economy_marriage.gift_command))
    app.add_handler(CommandHandler("pet", economy_pet.pet_command))
    app.add_handler(CommandHandler("pets", economy_pet.pets_list_command))
    app.add_handler(CommandHandler("buypet", economy_pet.buy_pet_command))
    app.add_handler(CommandHandler("feedpet", economy_pet.feed_command))
    app.add_handler(CommandHandler("trainpet", economy_pet.train_command))
    app.add_handler(CommandHandler("evolvepet", economy_pet.evolve_command))
    app.add_handler(CommandHandler("petfight", economy_pet.fight_command))
    app.add_handler(CommandHandler("dice", economy_games.dice_command))
    app.add_handler(CommandHandler("coinflip", economy_games.coinflip_command))
    app.add_handler(CommandHandler("guess", economy_games.guess_command))
    app.add_handler(CommandHandler("xo", economy_games.xo_command))
    app.add_handler(CommandHandler("game", economy_games.game_command))

    # فاز ۷: Inventory 2.0 + Black Market + Underground
    app.add_handler(CommandHandler("useitem", economy_inventory.use_item_command))
    app.add_handler(CommandHandler("inventorycapacity", economy_inventory.capacity_command))
    app.add_handler(CommandHandler("blackmarket", economy_blackmarket.blackmarket_command))
    app.add_handler(CommandHandler("buyblack", economy_blackmarket.buy_command))
    app.add_handler(CommandHandler("sellblack", economy_blackmarket.sell_command))
    app.add_handler(CommandHandler("auction", economy_blackmarket.auction_command))
    app.add_handler(CommandHandler("darkweb", economy_underground.darkweb_command))
    app.add_handler(CommandHandler("spy", economy_underground.spy_command))
    app.add_handler(CommandHandler("hacker", economy_underground.hacker_command))
    app.add_handler(CommandHandler("undergroundmission", economy_underground.random_mission_command))
    app.add_handler(CommandHandler("contract", economy_underground.status_command))

    # فاز ۸: دستاورد + Net Worth + Leaderboard (economy_achievements.py / economy_leaderboard.py)
    app.add_handler(CommandHandler("networth", economy_achievements.networth_command))
    app.add_handler(CommandHandler("ecoleaderboard", economy_leaderboard.leaderboard_command))
    app.add_handler(CommandHandler("richest", economy_leaderboard.richest_command))
    app.add_handler(CommandHandler("myrank", economy_leaderboard.my_rank_command))

    # فاز ۹: Events + Shop 2.0 + Balance Engine (economy_events.py / economy_shop2.py / economy_balance.py)
    app.add_handler(CommandHandler("event", economy_events.event_command))
    app.add_handler(CommandHandler("forceevent", economy_events.force_event_command))
    app.add_handler(CommandHandler("dailyshop", economy_shop2.daily_shop_command))
    app.add_handler(CommandHandler("buydailydeal", economy_shop2.buy_daily_deal_command))
    app.add_handler(CommandHandler("economyhealth", economy_balance.economy_health_command))

    # فاز ۱۰: منو + Toggle ماژول‌ها + Admin (economy_admin.py)
    # 🛍 پنل کاربریِ اقتصاد (جدا از پنل مدیریتیِ ادمین) — با /eco یا دکمه‌ی زیر باز می‌شه
    app.add_handler(CommandHandler("eco", economy_panel.eco_panel_command))
    app.add_handler(CommandHandler("economy", economy_panel.eco_panel_command))
    app.add_handler(CallbackQueryHandler(economy_panel.handle_callback, pattern=r"^ecop:"))
    app.add_handler(CommandHandler("economymenu", economy_admin.economy_menu_command))
    app.add_handler(CommandHandler("economymodules", economy_admin.economy_modules_command))
    app.add_handler(CommandHandler("inspecteconomy", economy_admin.inspect_economy_command))
    app.add_handler(CommandHandler("reseteconomy", economy_admin.reset_user_economy_command))
    app.add_handler(CommandHandler("freezeeconomy", economy_admin.freeze_economy_command))
    app.add_handler(CommandHandler("unfreezeeconomy", economy_admin.unfreeze_economy_command))
    app.add_handler(CommandHandler("txlookup", economy_admin.lookup_transaction_command))
    app.add_handler(CommandHandler("txrollback", economy_admin.rollback_transaction_command))
    app.add_handler(CommandHandler("adminlog", economy_admin.admin_log_command))
    app.add_handler(CommandHandler("economyhelp", economy_admin.economy_help_command))

    # پنل ویژه‌ی ادمین: سکه/XP/لول (تکی یا همه) + تنظیمات اقتصاد/فروشگاه
    app.add_handler(CommandHandler("setcoins", setcoins_command))
    app.add_handler(CommandHandler("addcoins", addcoins_command))
    app.add_handler(CommandHandler("setcoinsall", setcoinsall_command))
    app.add_handler(CommandHandler("addcoinsall", addcoinsall_command))
    app.add_handler(CommandHandler("setxp", setxp_command))
    app.add_handler(CommandHandler("addxp", addxp_command))
    app.add_handler(CommandHandler("setxpall", setxpall_command))
    app.add_handler(CommandHandler("addxpall", addxpall_command))
    app.add_handler(CommandHandler("setlevel", setlevel_command))
    app.add_handler(CommandHandler("setlevelall", setlevelall_command))
    app.add_handler(CommandHandler("seteco", seteco_command))
    app.add_handler(CommandHandler("setshopprice", setshopprice_command))

    # پنل شخصی پیوی برای کاربر عادی + میانبر پنل مدیریت از پیوی
    app.add_handler(CommandHandler("panel", panel_command))
    app.add_handler(CommandHandler("adminpanel", adminpanel_command))

    # نظرسنجی و قرعه‌کشی
    app.add_handler(CommandHandler("poll", poll_command))
    app.add_handler(CommandHandler("pollclose", poll_engine.pollclose_command))
    app.add_handler(CommandHandler("polldelete", poll_engine.polldelete_command))
    app.add_handler(CommandHandler("pollreopen", poll_engine.pollreopen_command))
    app.add_handler(CommandHandler("pollvoters", poll_engine.pollvoters_command))
    app.add_handler(CommandHandler("pollhide", poll_engine.pollhide_command))
    app.add_handler(CommandHandler("pollhistory", poll_engine.pollhistory_command))
    app.add_handler(CommandHandler("lottery", lottery_command))
    app.add_handler(CommandHandler("lottery_end", lottery_end_command))
    app.add_handler(CallbackQueryHandler(poll_vote_callback, pattern=r"^(vote:|pollclose:)"))
    app.add_handler(CallbackQueryHandler(lottery_join_callback, pattern=r"^lotteryjoin:"))

    # دکمه‌ی تایید عضو جدید (ضد ربات اسپمی)

    # دکمه‌های منوی تنظیمات (toggle:/panel: و غیره) + دکمه‌های پنل شخصی پیوی (dm:)
    #
    # 🐛 باگ اصلیِ «دکمه‌ها هیچ واکنشی نشون نمی‌دن» (رفع‌شده):
    # این pattern قبلاً خیلی از prefix‌هایی که خودِ تابع menu_callback_handler
    # داخلش چک می‌کرد رو نداشت. توی python-telegram-bot اگه callback_data یه
    # دکمه با هیچ‌کدوم از pattern‌های ثبت‌شده مچ نشه، اصلاً هیچ هندلری صدا زده
    # نمی‌شه (نه خطا، نه لاگ، نه پاسخ) - دقیقاً همون چیزی که حس می‌شد «دکمه کار
    # نمی‌کنه». دکمه‌های زیر قربانی همین باگ بودن:
    #   sub:          -> زیرمنوهای «قفل‌ها» و «ضداسپم» (پیام/رسانه/لینک/پیشرفته/
    #                    سریع/هوشمند و ...) => کل بخش قفل‌ها و کل بخش ضداسپم
    #   ask:          -> ویرایش متن خوش‌آمد/خروج، افزودن/حذف VIP و بلک‌لیست،
    #                    ارتقا/عزل ادمین، اطلاع‌رسانی همگانی، افزودن/حذف دوست
    #                    و دشمن، میوت/بن سریع
    #   menu_status   -> دکمه‌ی «وضعیت گروه»
    #   xp:           -> «پروفایل من» و «نفرات برتر» توی بخش پروفایل
    #   econ:top      -> «پولدارترین اعضا» توی بخش اقتصاد
    #   term:stats    -> «آمار کامل ترمیناتور» / «Targets»
    #   pnd:cancel    -> دکمه‌ی «لغو» روی ورودی‌های معلق پنل
    app.add_handler(
        CallbackQueryHandler(
            menu_callback_handler,
            pattern="^(toggle:|menu_close|menu_back|menu_help|menu_status|cycle:|panel:|sub:|ask:|xp:|econ:top|term:stats|pnd:cancel|show_relations|noop|adm:pick:|econ_help|econv:|econset:|econshop:|econreset:|econquick:|act:|help_top|help_eco|help_sec:|help_ecosec:)",
        )
    )
    app.add_handler(CallbackQueryHandler(dm_panel_callback_handler, pattern="^dm:"))

    # ---------------------------------------------------------------
    # نکته‌ی مهم درباره‌ی گروه‌های هندلر (group=):
    # python-telegram-bot توی هر گروه، فقط اولین هندلری که فیلترش با آپدیت مچ بشه
    # رو اجرا می‌کنه و بقیه‌ی هندلرهای همون گروه رو کلاً نادیده می‌گیره - حتی اگه اون
    # هندلر اول داخلش تصمیم بگیره کاری نکنه. قبلاً چندتا هندلر فیلتر یکسان/همپوشان
    # داشتن و توی یه گروه بودن (مثلاً ثبت آمار و ثبت چت، یا سکوت‌زمان‌دار و دوست/دشمن)
    # که باعث می‌شد یکیشون همیشه اجرا نشه. برای رفع کامل این باگ، از این به بعد هر
    # هندلر متنی/محتوایی یه شماره گروه اختصاصی و یکتا داره.
    # ---------------------------------------------------------------

    # ورودی دنباله‌دار پنل (افزودن دشمن/دوست، بن/میوت سریع، ویرایش متن و ...) - باید زودتر از همه چک بشه
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, pending_panel_input_handler), group=-20
    )

    # کنترل کلمات فارسی (روشن/خاموش ربات، ترمیناتور، هر تنظیم دیگه، «تگ همه») - زودتر از همه
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, control_words_handler), group=-10
    )

    # ضد رید - باید قبل از خوش‌آمدگویی چک بشه که آیا الان تحت حمله‌ی عضوگیری هستیم یا نه
    app.add_handler(ChatMemberHandler(_raid_watch, ChatMemberHandler.CHAT_MEMBER), group=-6)

    # عضو جدید (نیازمنده که ربات مجوز chat_member updates داشته باشه)
    app.add_handler(ChatMemberHandler(welcome_new_member, ChatMemberHandler.CHAT_MEMBER))

    # ترک/اخراج عضو از گروه (پیام لفت با تاریخ و ساعت دقیق) - شماره‌ی گروه جدا از خوش‌آمدگویی
    app.add_handler(ChatMemberHandler(member_left_handler, ChatMemberHandler.CHAT_MEMBER), group=-1)

    # ثبت چت برای نوتیفیکیشن + ثبت آمار پیام‌ها + شناسایی عضو برای «تگ همه» (یه هندلر واحد)
    app.add_handler(MessageHandler(filters.ALL, track_chat_and_stats), group=-5)

    # مدیریت گروه (اسپم/فلود/کاپس/فوروارد/استیکر/رسانه/مخاطب/موقعیت) - فقط توی گروه‌ها
    app.add_handler(
        MessageHandler(
            (
                filters.TEXT
                | filters.CAPTION
                | filters.Sticker.ALL
                | filters.ANIMATION
                | filters.CONTACT
                | filters.LOCATION
                | filters.PHOTO
                | filters.VIDEO
                | filters.Document.ALL
                | filters.VOICE
                | filters.AUDIO
                | filters.POLL
                | filters.Dice.ALL
            )
            & filters.ChatType.GROUPS,
            moderate_group_message,
        ),
        group=0,
    )

    # قفل ویرایش پیام (پیام‌های edited) - گروه اختصاصی، چون فیلترهای متنی بالا ممکنه
    # روی پیام‌های edited هم مچ بشن و اگه هم‌گروه بودن، این هندلر هیچ‌وقت اجرا نمی‌شد
    app.add_handler(
        MessageHandler(filters.UpdateType.EDITED_MESSAGE & filters.ChatType.GROUPS, edited_message_handler),
        group=1,
    )

    # سکوت زمان‌دار متنی («سکوت ۲۶»)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            timed_mute_text_handler,
        ),
        group=2,
    )

    # کنترل سیستم دوست/دشمن (علامت‌گذاری با ریپلای)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            friend_enemy_control_handler,
        ),
        group=3,
    )

    # پاسخ خودکار دوست/دشمن (ترمیناتور) - به هر نوع پیامی که بفرستن (متن/عکس/ویدیو/استیکر/ویس/فایل/لوکیشن/...)
    app.add_handler(
        MessageHandler(
            (
                filters.TEXT
                | filters.Sticker.ALL
                | filters.PHOTO
                | filters.VIDEO
                | filters.VIDEO_NOTE
                | filters.ANIMATION
                | filters.VOICE
                | filters.AUDIO
                | filters.Document.ALL
                | filters.LOCATION
                | filters.CONTACT
                | filters.POLL
                | filters.Dice.ALL
            )
            & ~filters.COMMAND
            & filters.ChatType.GROUPS,
            enemy_friend_auto_reply_handler,
        ),
        group=4,
    )

    # پاسخ خودکار - روی پیام‌های متنی که دستور نیستن (فقط توی گروه؛ پیوی رو dm_ai_reply_handler جدا مدیریت می‌کنه)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, auto_reply_handler),
        group=5,
    )

    # پاسخ هوش مصنوعی توی پیوی ربات (فقط برای کاربرایی که از /panel روشنش کرده باشن)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, dm_ai_reply_handler),
        group=6,
    )

    logger.info("ربات در حال اجراست...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    # چرا این تغییر لازم بود (ریشه‌ی واقعی مشکلِ «پنل‌ها هماهنگ نیستن»):
    #
    # همه‌ی ماژول‌های دیگه (lock_engine، spam_engine، moderation_engine،
    # economy_*، profile_engine، poll_engine، purge_engine - در مجموع ۸۹ جا)
    # برای دسترسی به حافظه‌ی بات (_chat_settings, _vip_users, _friends,
    # _enemies, _blacklist, ...) و توابعش، داخل خودشون می‌نویسن:
    #     import bot as host
    # این کار عمدیه (برای فرار از circular import در زمان بارگذاری)، ولی یه
    # پیش‌فرض ظریف داره: باید یه ماژول واقعاً به اسم «bot» توی sys.modules
    # وجود داشته باشه.
    #
    # وقتی این فایل مستقیم با «python bot.py» اجرا می‌شه، پایتون خودِ این
    # فایل رو به اسم «__main__» بار می‌کنه، نه «bot». در نتیجه، اولین باری که
    # مثلاً lock_engine.py می‌نویسه `import bot as host`، پایتون هیچ ماژول
    # «bot»ـی پیدا نمی‌کنه و کل این فایل رو یه بار دیگه، از صفر، جداگانه اجرا
    # می‌کنه تا اون رو بسازه. یعنی یه کپی کاملاً جدا و خالی از _chat_settings،
    # _vip_users، _friends/_enemies، _blacklist و بقیه‌ی متغیرهای این فایل
    # ساخته می‌شه - کاملاً جدا از نسخه‌ای که هندلرهای واقعی تلگرام (که همینجا
    # زیر __main__ تعریف شدن) دارن باهاش کار می‌کنن.
    #
    # نتیجه دقیقاً همون چیزیه که گزارش شد: هر پنل/دستوری که مستقیم روی
    # متغیرهای همین فایل کار می‌کنه (مثل toggle توی /menu) یه حافظه می‌بینه،
    # و هر پنل/دستوری که از یه ماژول دیگه میاد و با `host.` بهش دسترسی پیدا
    # می‌کنه (پنل قفل، خودِ اجرای واقعی قفل‌ها روی پیام‌ها، و در واقع تمام
    # قابلیت‌های اقتصاد/ضداسپم/مدیریت هم) یه حافظه‌ی کاملاً دیگه می‌بینه - دو
    # دنیای موازی که هیچ‌وقت با هم sync نمی‌شن، هرچقدر هم کد lock_engine درست
    # باشه.
    #
    # فیکس: به‌جای اینکه خودِ __main__ مستقیماً main() رو اجرا کنه، این فایل
    # رو دوباره، این‌بار با همون اسمی که بقیه‌ی ماژول‌ها صداش می‌زنن («bot»)،
    # import می‌کنیم و اجرای واقعی رو کامل می‌سپاریم به اون نسخه. این‌جوری
    # فقط یه نسخه‌ی واحد از این فایل («bot») توی حافظه‌ی پایتون وجود داره و
    # همه (هندلرهای تلگرام + همه‌ی ماژول‌های دیگه) دقیقاً به همون یه حافظه‌ی
    # مشترک دسترسی دارن. روش اجرا (python bot.py) هیچ تغییری نمی‌کنه.
    import bot as _bot_singleton
    _bot_singleton.main()
