# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY ENGINE 1.0
============================
فاز ۲ ارتقای Economy (طبق درخواست کاربر): Transaction History، Transfer Limits،
Daily Streak Bonus، Inventory با آیتم‌های زمان‌دار (Boost/VIP) با Equip/Expire
خودکار، Economy Stats، و ابزار ادمین giveitem/removeitem.

مثل profile_engine.py، این ماژول هم جدا از bot.py نگه داشته شده (همون الگوی
lock_engine/spam_engine/moderation_engine/profile_engine): state مستقل خودش رو
توی فایل JSON جدا ذخیره می‌کنه و در زمان اجرا `import bot as host` می‌کنه تا از
Import حلقه‌ای جلوگیری بشه. هیچ‌کدوم از bot._wallet، config.SHOP_ITEMS، یا
منطق /balance /daily /pay /shop /buy فعلی از نو ساخته نشده؛ فقط از داخل با هوک‌های
کوچیک در bot.py بهش وصل شده و ارتقا داده شده.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque

from telegram import Update
from telegram.ext import ContextTypes

import config

logger = logging.getLogger("economy_engine")

STATE_FILE = getattr(config, "ECONOMY_ENGINE_STATE_FILE", "economy_engine_state.json")
_HISTORY_LIMIT = getattr(config, "TRANSACTION_HISTORY_LIMIT", 200)

# ---------------------------------------------------------------------------
# 💾 State
# ---------------------------------------------------------------------------

# 🧾 Transactions: chat_id -> user_id -> deque[{id, ts, kind, amount, balance_after, counterparty, note}]
_transactions = defaultdict(lambda: defaultdict(lambda: deque(maxlen=_HISTORY_LIMIT)))
# chat_id -> شمارنده‌ی سراسری برای Transaction ID یکتا
_tx_seq = defaultdict(int)

# 💸 Transfer limits: chat_id -> user_id -> {"date": "YYYY-MM-DD", "count": n, "total": n}
_transfer_daily = defaultdict(dict)

# 📈 برای /economystats: مجموع کل واریزی/برداشتی هر کاربر (فراتر از لحظه‌ای بودن moجودی)
_total_earned = defaultdict(lambda: defaultdict(int))
_total_spent = defaultdict(lambda: defaultdict(int))

# 🎒 آیتم‌های زمان‌دار (Boost XP/Coin، VIP Pass و ...): chat_id -> user_id -> {item_key: expire_ts}
_timed_items = defaultdict(lambda: defaultdict(dict))

_save_lock = asyncio.Lock()


def _host():
    import bot as host
    return host


def _today_str() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "transactions": {
            str(c): {str(u): list(dq) for u, dq in v.items()}
            for c, v in _transactions.items()
        },
        "tx_seq": {str(c): n for c, n in _tx_seq.items()},
        "transfer_daily": {str(c): {str(u): d for u, d in v.items()} for c, v in _transfer_daily.items()},
        "total_earned": {str(c): dict(v) for c, v in _total_earned.items()},
        "total_spent": {str(c): dict(v) for c, v in _total_spent.items()},
        "timed_items": {str(c): {str(u): dict(items) for u, items in v.items()} for c, v in _timed_items.items()},
    }


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
            logger.warning(f"ذخیره‌ی state موتور اقتصاد ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور اقتصاد ناموفق بود: {e}")
        return

    for cid, v in data.get("transactions", {}).items():
        for uid, records in v.items():
            _transactions[int(cid)][int(uid)] = deque(records, maxlen=_HISTORY_LIMIT)
    for cid, n in data.get("tx_seq", {}).items():
        _tx_seq[int(cid)] = n
    for cid, v in data.get("transfer_daily", {}).items():
        _transfer_daily[int(cid)] = {int(u): d for u, d in v.items()}
    for cid, v in data.get("total_earned", {}).items():
        _total_earned[int(cid)] = defaultdict(int, {int(u): n for u, n in v.items()})
    for cid, v in data.get("total_spent", {}).items():
        _total_spent[int(cid)] = defaultdict(int, {int(u): n for u, n in v.items()})
    for cid, v in data.get("timed_items", {}).items():
        for uid, items in v.items():
            _timed_items[int(cid)][int(uid)] = dict(items)

    logger.info("وضعیت موتور اقتصاد از فایل بارگذاری شد.")


# ---------------------------------------------------------------------------
# 🧾 Transaction Ledger
# ---------------------------------------------------------------------------

def log_transaction(chat_id: int, uid: int, kind: str, amount: int, counterparty_id: int | None = None, note: str = "") -> None:
    """یه تراکنش ثبت می‌کنه. amount مثبت = واریز، منفی = برداشت.
    این تابع فقط دفترچه‌ی تاریخچه رو می‌نویسه؛ خودش موجودی رو تغییر نمی‌ده
    (تغییر موجودی همون‌جایی که الان هست (bot.py) انجام می‌شه، این فقط Log می‌کنه)."""
    host = _host()
    _tx_seq[chat_id] += 1
    record = {
        "id": _tx_seq[chat_id],
        "ts": time.time(),
        "kind": kind,
        "amount": amount,
        "balance_after": host._wallet[chat_id].get(uid, 0),
        "counterparty": counterparty_id,
        "note": note,
    }
    _transactions[chat_id][uid].append(record)
    if amount > 0:
        _total_earned[chat_id][uid] += amount
    elif amount < 0:
        _total_spent[chat_id][uid] += -amount


async def transactions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    records = list(_transactions[chat.id].get(uid, []))
    if not records:
        await message.reply_text("هنوز هیچ تراکنشی برات ثبت نشده.")
        return
    limit = getattr(config, "TRANSACTIONS_DISPLAY_COUNT", 15)
    labels = getattr(config, "TRANSACTION_KIND_LABELS", {})
    lines = ["🧾 آخرین تراکنش‌های تو:", ""]
    for rec in reversed(records[-limit:]):
        label = labels.get(rec["kind"], rec["kind"])
        sign = "+" if rec["amount"] >= 0 else ""
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(rec["ts"]))
        extra = f" ({rec['note']})" if rec.get("note") else ""
        lines.append(f"#{rec['id']} • {when} • {label}: {sign}{rec['amount']} {config.CURRENCY_EMOJI}{extra}")
    lines.append(f"\nموجودی فعلی: {host._wallet[chat.id].get(uid, 0)} {config.CURRENCY_NAME}")
    await message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# 💸 Transfer Limits
# ---------------------------------------------------------------------------

def check_transfer_limit(chat_id: int, uid: int, amount: int) -> str | None:
    """اگه انتقال مجاز نباشه، پیام خطا برمی‌گردونه؛ وگرنه None."""
    max_single = getattr(config, "TRANSFER_MAX_SINGLE", 2000)
    if amount > max_single:
        return f"❌ حداکثر مبلغ هر انتقال {max_single} {config.CURRENCY_NAME}ه."

    daily_count_limit = getattr(config, "TRANSFER_DAILY_LIMIT_COUNT", 10)
    daily_amount_limit = getattr(config, "TRANSFER_DAILY_LIMIT_AMOUNT", 5000)
    today = _today_str()
    rec = _transfer_daily[chat_id].get(uid)
    if not rec or rec.get("date") != today:
        rec = {"date": today, "count": 0, "total": 0}
    if rec["count"] >= daily_count_limit:
        return f"❌ به سقف روزانه‌ی تعداد انتقال ({daily_count_limit} بار) رسیدی."
    if rec["total"] + amount > daily_amount_limit:
        return f"❌ به سقف روزانه‌ی مجموع انتقال ({daily_amount_limit} {config.CURRENCY_NAME}) می‌رسی."
    return None


def record_transfer(chat_id: int, uid: int, amount: int) -> None:
    today = _today_str()
    rec = _transfer_daily[chat_id].get(uid)
    if not rec or rec.get("date") != today:
        rec = {"date": today, "count": 0, "total": 0}
    rec["count"] += 1
    rec["total"] += amount
    _transfer_daily[chat_id][uid] = rec


# ---------------------------------------------------------------------------
# 🎁 Daily Reward Streak Bonus
# ---------------------------------------------------------------------------

def daily_streak_bonus(chat_id: int, uid: int) -> int:
    """طبق config.DAILY_STREAK_BONUS، بر اساس Streak فعلی کاربر (bot._streaks)، بزرگ‌ترین
    آستانه‌ی رسیده رو به‌عنوان جایزه‌ی اضافه برمی‌گردونه (0 یعنی جایزه‌ای نداره)."""
    host = _host()
    current_streak = host._streaks[chat_id].get(uid, {}).get("current", 0)
    bonus_map = getattr(config, "DAILY_STREAK_BONUS", {})
    best = 0
    for threshold, bonus in sorted(bonus_map.items()):
        if current_streak >= threshold:
            best = bonus
    return best


# ---------------------------------------------------------------------------
# 🎒 Timed Items (Boost XP/Coin, VIP Pass) — Equip/Expire خودکار
# ---------------------------------------------------------------------------

def grant_timed_item(chat_id: int, uid: int, item_key: str, duration_hours: float) -> None:
    """آیتم زمان‌دار می‌ده؛ اگه از قبل فعال بوده، مدت رو تمدید می‌کنه (جمع می‌شه)."""
    now = time.time()
    items = _timed_items[chat_id][uid]
    current_expiry = items.get(item_key, now)
    base = max(now, current_expiry)
    items[item_key] = base + duration_hours * 3600


def _purge_expired(chat_id: int, uid: int) -> None:
    now = time.time()
    items = _timed_items[chat_id][uid]
    for key in [k for k, exp in items.items() if exp <= now]:
        del items[key]


def active_timed_items(chat_id: int, uid: int) -> dict:
    """آیتم‌های هنوز فعال رو برمی‌گردونه: {item_key: ثانیه‌ی باقی‌مونده}."""
    _purge_expired(chat_id, uid)
    now = time.time()
    return {k: round(exp - now) for k, exp in _timed_items[chat_id][uid].items()}


def has_active_item_type(chat_id: int, uid: int, item_type: str) -> bool:
    """آیا کاربر یه آیتم فعال از این type (مثلاً boost_xp) داره یا نه."""
    active_keys = active_timed_items(chat_id, uid)
    for key in active_keys:
        item = config.SHOP_ITEMS.get(key)
        if item and item.get("type") == item_type:
            return True
    return False


def apply_xp_multiplier(chat_id: int, uid: int, base_amount: int) -> int:
    if has_active_item_type(chat_id, uid, "boost_xp"):
        return round(base_amount * getattr(config, "XP_BOOST_MULTIPLIER", 2))
    return base_amount


def apply_coin_multiplier(chat_id: int, uid: int, base_amount: int) -> int:
    if has_active_item_type(chat_id, uid, "boost_coin"):
        return round(base_amount * getattr(config, "COIN_BOOST_MULTIPLIER", 2))
    return base_amount


def _format_remaining(seconds: int) -> str:
    if seconds <= 0:
        return "تموم‌شده"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours:
        return f"{hours} ساعت و {minutes} دقیقه"
    return f"{minutes} دقیقه"


def inventory_text(chat_id: int, uid: int) -> str:
    """متن /myitems و /inventory - آیتم‌های همیشگی (بج) + آیتم‌های زمان‌دار با زمان باقی‌مونده."""
    host = _host()
    owned = host._owned_badges[chat_id].get(uid, set())
    timed = active_timed_items(chat_id, uid)
    permanent = [k for k in owned if k in config.SHOP_ITEMS and "duration_hours" not in config.SHOP_ITEMS[k]]

    if not permanent and not timed:
        return "هنوز هیچ آیتمی نخریدی. با /shop فروشگاه رو ببین."

    lines = ["🎒 آیتم‌های تو:"]
    for key in permanent:
        lines.append(f"• {config.SHOP_ITEMS[key]['name']}")
    for key, remaining in timed.items():
        item = config.SHOP_ITEMS.get(key)
        name = item["name"] if item else key
        lines.append(f"• {name} — ⏱ {_format_remaining(remaining)} باقی‌مونده")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 📈 Economy Stats
# ---------------------------------------------------------------------------

async def economystats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not host.get_setting(chat.id, "economy_enabled"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    wallet = host._wallet[chat.id]
    total_coins = sum(wallet.values())
    total_tx = sum(len(dq) for dq in _transactions[chat.id].values())

    def top(d: dict, n: int = 5):
        return sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:n]

    def name_of(uid: int) -> str:
        return host._user_display_names.get(uid, str(uid))

    lines = [
        "📈 آمار اقتصاد گروه",
        "",
        f"{config.CURRENCY_EMOJI} مجموع سکه در گردش: {total_coins:,}",
        f"🧾 مجموع تراکنش‌های ثبت‌شده: {total_tx:,}",
        "",
        "🏆 پولدارترین‌ها:",
    ]
    for i, (uid, bal) in enumerate(top(wallet), 1):
        lines.append(f"{i}. {name_of(uid)} — {bal:,} {config.CURRENCY_NAME}")

    lines.append("\n💸 بیشترین خرج‌کن‌ها:")
    for i, (uid, spent) in enumerate(top(_total_spent[chat.id]), 1):
        lines.append(f"{i}. {name_of(uid)} — {spent:,} {config.CURRENCY_NAME}")

    lines.append("\n💰 بیشترین کسب‌کننده‌ها:")
    for i, (uid, earned) in enumerate(top(_total_earned[chat.id]), 1):
        lines.append(f"{i}. {name_of(uid)} — {earned:,} {config.CURRENCY_NAME}")

    await message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# 🎯 Admin item tools (giveitem/removeitem) — با همون permission فعلی manage_economy
# ---------------------------------------------------------------------------

async def giveitem_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not host.has_permission(update.effective_user.id, "manage_economy"):
        await message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    target_id = host._resolve_target_id(message)
    args = context.args or []
    item_key = args[-1] if args else None
    if target_id is None or not item_key or item_key not in config.SHOP_ITEMS:
        await message.reply_text(
            "استفاده: ریپلای روی پیام کسی + /giveitem <کد آیتم>  یا  /giveitem <آیدی> <کد آیتم>\n"
            "کدهای موجود: " + ", ".join(config.SHOP_ITEMS)
        )
        return
    item = config.SHOP_ITEMS[item_key]
    if "duration_hours" in item:
        grant_timed_item(chat.id, target_id, item_key, item["duration_hours"])
    else:
        host._owned_badges[chat.id][target_id].add(item_key)
    log_transaction(chat.id, target_id, "admin_add", 0, note=f"دریافت آیتم: {item['name']}")
    name = host._user_display_names.get(target_id, str(target_id))
    await message.reply_text(f"✅ {item['name']} به {name} داده شد.")
    await host.save_state()


async def removeitem_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        return
    if not host.has_permission(update.effective_user.id, "manage_economy"):
        await message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    target_id = host._resolve_target_id(message)
    args = context.args or []
    item_key = args[-1] if args else None
    if target_id is None or not item_key:
        await message.reply_text("استفاده: ریپلای روی پیام کسی + /removeitem <کد آیتم>  یا  /removeitem <آیدی> <کد آیتم>")
        return
    removed = False
    if item_key in host._owned_badges[chat.id].get(target_id, set()):
        host._owned_badges[chat.id][target_id].discard(item_key)
        removed = True
    if item_key in _timed_items[chat.id].get(target_id, {}):
        del _timed_items[chat.id][target_id][item_key]
        removed = True
    if not removed:
        await message.reply_text("این کاربر همچین آیتمی نداشت.")
        return
    item_name = config.SHOP_ITEMS.get(item_key, {}).get("name", item_key)
    log_transaction(chat.id, target_id, "admin_add", 0, note=f"حذف آیتم: {item_name}")
    name = host._user_display_names.get(target_id, str(target_id))
    await message.reply_text(f"✅ {item_name} از {name} گرفته شد.")
    await host.save_state()
