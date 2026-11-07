# -*- coding: utf-8 -*-
"""
DIGNANTI ECONOMY AUCTION — فاز ۳ (V18 Living Economy: مزایده و حراج اقتصادی)
=====================================================================
بخشِ «مزایده و حراجِ اقتصادی»ی نقشه‌ی V18. سه نوع دارایی پشتیبانی می‌شه:
ملک (economy_property)، خودرو (economy_vehicle)، کسب‌وکار (economy_business).
کالا/سهام در فازهای بعدی اضافه می‌شن (مستند در گزارشِ تحویل).

امنیتِ مالی (دقیقاً طبقِ خواسته‌ی V18):
  • موجودیِ پیشنهاددهنده به‌صورتِ *واقعی* رزرو می‌شه: لحظه‌ی ثبتِ پیشنهاد،
    مبلغ با economy_core.remove_coins از کیف‌پولش کم می‌شه (نه فقط چک
    موجودی) - یعنی نمی‌تونه همون پول رو جای دیگه خرج کنه و بعداً برنده بشه.
  • اگه یکی پیشنهادِ بالاتر بده، مبلغِ پیشنهادِ قبلی فوراً به نفرِ قبلی
    برمی‌گرده (add_coins).
  • هر مزایده یه asyncio.Lock مستقل داره؛ یعنی حتی اگه یه Callback دوبار
    پشتِ‌هم بیاد (دابل‌تپ)، دو ثبتِ پیشنهاد به‌صورتِ کاملاً اتمیک و سریالی
    پردازش می‌شن (نه هم‌زمان روی State قدیمی) - از «ثبتِ چندباره‌ی پیشنهاد
    با Callback تکراری» دقیقاً همینجوری جلوگیری می‌شه.
  • خودِ آیتم هم لحظه‌ی ایجادِ مزایده از لیستِ دارایی‌های فروشنده خارج
    می‌شه (Escrow) تا نشه همزمان هم فروختش هم مزایده‌اش کرد.

برنده‌شدن/باختن (Settle) به‌صورتِ Lazy انجام می‌شه - هر وقت کسی لیستِ
مزایده‌ها رو ببینه یا پیشنهاد بده، اول مزایده‌های منقضی‌شده Settle می‌شن:
  • برنده داشت → مبلغِ رزروشده (که از قبل از کیفِ‌پولش کم شده) به فروشنده
    واریز می‌شه، آیتم با یه id تازه (توی فضای نام خریدار) به دارایی‌های
    برنده اضافه می‌شه.
  • برنده نداشت → آیتم بدونِ تغییر به دارایی‌های فروشنده برمی‌گرده.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

import config
import economy_core

logger = logging.getLogger("economy_auction")

STATE_FILE = getattr(config, "ECONOMY_AUCTION_STATE_FILE", "economy_auction_state.json")
STATE_VERSION = 1

_save_lock = asyncio.Lock()


def _host():
    import bot as host
    return host


class AuctionError(Exception):
    pass


# ---------------------------------------------------------------------------
# 💾 State
# ---------------------------------------------------------------------------

# chat_id -> auction_id(int) -> {id, chat_id, seller_uid, asset_type, asset,
#   label, start_price, current_bid, current_bidder, bids: [{uid,amount,ts}],
#   created_ts, end_ts, status: active|ended_sold|ended_unsold|cancelled}
_auctions: dict[int, dict] = defaultdict(dict)
_next_auction_id: dict[int, int] = defaultdict(int)

# (chat_id, auction_id) -> asyncio.Lock — ضدِ پردازشِ هم‌زمانِ دو پیشنهاد روی یه مزایده
_auction_locks: dict[tuple, asyncio.Lock] = defaultdict(asyncio.Lock)


# ---------------------------------------------------------------------------
# 🔌 Adapterهای نوعِ دارایی
# ---------------------------------------------------------------------------

def _adapter(asset_type: str):
    allowed = getattr(config, "AUCTION_ASSET_TYPES", {"property", "vehicle", "business"})
    if asset_type not in allowed:
        raise AuctionError(f"نوعِ دارایی «{asset_type}» برای مزایده پشتیبانی نمی‌شه.")
    if asset_type == "property":
        import economy_property as m
        return m
    if asset_type == "vehicle":
        import economy_vehicle as m
        return m
    if asset_type == "business":
        import economy_business as m
        return m
    raise AuctionError(f"نوعِ دارایی «{asset_type}» پشتیبانی نمی‌شه.")


def _asset_label(asset_type: str, asset: dict) -> str:
    try:
        adapter = _adapter(asset_type)
        type_name = adapter.TYPES.get(asset.get("type"), {}).get("name", asset.get("type", "؟"))
    except Exception:
        type_name = asset.get("type", "؟")
    return f"{type_name} (قبلاً #{asset.get('id', '؟')})"


def _assign_fresh_id(owned_list: list) -> int:
    """یه id آزاد توی فضای‌نامِ owned_list پیدا می‌کنه - برای وقتی که یه آیتم
    بینِ دو مالکِ متفاوت جابه‌جا می‌شه و نباید با id فعلیِ مالکِ جدید تصادم کنه."""
    existing = {item.get("id") for item in owned_list}
    new_id = 1
    while new_id in existing:
        new_id += 1
    return new_id


# ---------------------------------------------------------------------------
# 🏗 ایجادِ مزایده
# ---------------------------------------------------------------------------

def _active_count_for_seller(chat_id: int, uid: int) -> int:
    return sum(
        1 for a in _auctions[chat_id].values()
        if a["seller_uid"] == uid and a["status"] == "active"
    )


def create_auction(chat_id: int, seller_uid: int, asset_type: str, asset_id: int,
                    start_price: int, duration_minutes: int) -> dict:
    if start_price <= 0:
        raise AuctionError("قیمتِ پایه باید مثبت باشه.")
    min_d = getattr(config, "AUCTION_MIN_DURATION_MINUTES", 10)
    max_d = getattr(config, "AUCTION_MAX_DURATION_MINUTES", 1440)
    if not (min_d <= duration_minutes <= max_d):
        raise AuctionError(f"مدتِ مزایده باید بینِ {min_d} تا {max_d} دقیقه باشه.")
    max_active = getattr(config, "AUCTION_MAX_ACTIVE_PER_USER", 3)
    if _active_count_for_seller(chat_id, seller_uid) >= max_active:
        raise AuctionError(f"حداکثر {max_active} مزایده‌ی فعال هم‌زمان مجازه.")

    adapter = _adapter(asset_type)
    owned_list = adapter.owned(chat_id, seller_uid)
    item = next((it for it in owned_list if it.get("id") == asset_id), None)
    if item is None:
        raise AuctionError("این دارایی پیدا نشد (مطمئن شو شناسه درسته و مالکشی).")

    owned_list.remove(item)  # Escrow: تا پایانِ مزایده دیگه جزوِ دارایی‌های فروشنده نیست

    auction_id = _next_auction_id[chat_id] + 1
    _next_auction_id[chat_id] = auction_id
    now = time.time()
    auction = {
        "id": auction_id,
        "chat_id": chat_id,
        "seller_uid": seller_uid,
        "asset_type": asset_type,
        "asset": item,
        "label": _asset_label(asset_type, item),
        "start_price": start_price,
        "current_bid": 0,
        "current_bidder": None,
        "bids": [],
        "created_ts": now,
        "end_ts": now + duration_minutes * 60,
        "status": "active",
    }
    _auctions[chat_id][auction_id] = auction
    return auction


async def cancel_auction(chat_id: int, uid: int, auction_id: int) -> dict:
    """فقط فروشنده، و فقط تا وقتی هیچ پیشنهادی ثبت نشده، می‌تونه لغو کنه
    (بعد از اولینِ پیشنهاد، پولِ کسی گیرِ Escrow شده و لغوِ یک‌طرفه عادلانه نیست)."""
    lock = _auction_locks[(chat_id, auction_id)]
    async with lock:
        a = _auctions[chat_id].get(auction_id)
        if not a or a["status"] != "active":
            raise AuctionError("این مزایده فعال نیست.")
        if a["seller_uid"] != uid:
            raise AuctionError("فقط فروشنده می‌تونه این مزایده رو لغو کنه.")
        if a["bids"]:
            raise AuctionError("چون قبلاً پیشنهاد ثبت شده، دیگه نمی‌شه لغو کرد؛ صبر کن تمام بشه.")
        adapter = _adapter(a["asset_type"])
        adapter.owned(chat_id, uid).append(a["asset"])
        a["status"] = "cancelled"
        return a


# ---------------------------------------------------------------------------
# 💰 ثبتِ پیشنهاد (با رزروِ واقعیِ موجودی)
# ---------------------------------------------------------------------------

def min_next_bid(a: dict) -> int:
    if a["current_bid"] <= 0:
        return a["start_price"]
    inc = getattr(config, "AUCTION_MIN_BID_INCREMENT_PERCENT", 0.05)
    return round(a["current_bid"] * (1 + inc))


async def place_bid(chat_id: int, uid: int, auction_id: int, amount: int) -> dict:
    lock = _auction_locks[(chat_id, auction_id)]
    async with lock:
        await _settle_if_expired(chat_id, auction_id)
        a = _auctions[chat_id].get(auction_id)
        if not a or a["status"] != "active":
            raise AuctionError("این مزایده فعال نیست یا تمام شده.")
        if a["seller_uid"] == uid:
            raise AuctionError("نمی‌تونی روی مزایده‌ی خودت پیشنهاد بدی.")
        required = min_next_bid(a)
        if amount < required:
            raise AuctionError(f"پیشنهاد باید حداقل {required:,} {config.CURRENCY_NAME} باشه.")

        # اول پول پیشنهادِ جدید رو رزرو کن (ممکنه موجودی کافی نباشه و اینجا Fail بشه -
        # در اون صورت هنوز هیچ تغییری روی پیشنهادِ قبلی اعمال نشده، پس امن‌ـه)
        await economy_core.remove_coins(
            chat_id, uid, amount, kind="auction_bid",
            note=f"پیشنهاد روی مزایده‌ی #{auction_id} ({a['label']})",
        )

        prev_bidder, prev_amount = a["current_bidder"], a["current_bid"]
        if prev_bidder is not None and prev_amount > 0:
            await economy_core.add_coins(
                chat_id, prev_bidder, prev_amount, kind="auction_refund",
                note=f"بازگشتِ پیشنهادِ قبلیِ مزایده‌ی #{auction_id} (پیشنهادِ بالاتر ثبت شد)",
            )

        a["current_bid"] = amount
        a["current_bidder"] = uid
        a["bids"].append({"uid": uid, "amount": amount, "ts": time.time()})
        return a


# ---------------------------------------------------------------------------
# ⏱ پایانِ مزایده (Settle) — Lazy
# ---------------------------------------------------------------------------

async def _settle_if_expired(chat_id: int, auction_id: int) -> None:
    a = _auctions[chat_id].get(auction_id)
    if not a or a["status"] != "active":
        return
    if time.time() < a["end_ts"]:
        return

    if a["current_bidder"] is not None:
        winner_uid = a["current_bidder"]
        await economy_core.add_coins(
            chat_id, a["seller_uid"], a["current_bid"], kind="auction_sale",
            note=f"فروشِ مزایده‌ایِ {a['label']} (#{a['id']})",
        )
        adapter = _adapter(a["asset_type"])
        winner_list = adapter.owned(chat_id, winner_uid)
        asset = a["asset"]
        asset["id"] = _assign_fresh_id(winner_list)
        winner_list.append(asset)
        a["status"] = "ended_sold"
    else:
        adapter = _adapter(a["asset_type"])
        adapter.owned(chat_id, a["seller_uid"]).append(a["asset"])
        a["status"] = "ended_unsold"


async def maybe_settle_all(chat_id: int) -> None:
    """هر وقت کسی لیستِ مزایده‌ها رو می‌بینه صدا زده می‌شه - مزایده‌های منقضی‌شده
    رو Settle می‌کنه. سقفِ AUCTION_SWEEP_LOOKBACK_LIMIT برای جلوگیری از
    پردازشِ بی‌نهایت توی یه گروهِ خیلی شلوغ."""
    limit = getattr(config, "AUCTION_SWEEP_LOOKBACK_LIMIT", 500)
    ids = list(_auctions[chat_id].keys())[-limit:]
    for auction_id in ids:
        a = _auctions[chat_id].get(auction_id)
        if a and a["status"] == "active" and time.time() >= a["end_ts"]:
            lock = _auction_locks[(chat_id, auction_id)]
            async with lock:
                await _settle_if_expired(chat_id, auction_id)


# ---------------------------------------------------------------------------
# 📋 نمایش
# ---------------------------------------------------------------------------

def get_auction(chat_id: int, auction_id: int) -> dict | None:
    return _auctions[chat_id].get(auction_id)


def list_active(chat_id: int) -> list[dict]:
    return sorted(
        (a for a in _auctions[chat_id].values() if a["status"] == "active"),
        key=lambda a: a["end_ts"],
    )


def my_auctions(chat_id: int, uid: int) -> list[dict]:
    return [
        a for a in _auctions[chat_id].values()
        if a["seller_uid"] == uid or a["current_bidder"] == uid
    ]


def _fmt_remaining(a: dict) -> str:
    remaining = a["end_ts"] - time.time()
    if remaining <= 0:
        return "در حال پایان..."
    minutes = int(remaining // 60)
    if minutes < 60:
        return f"{minutes} دقیقه مونده"
    return f"{minutes // 60} ساعت و {minutes % 60} دقیقه مونده"


async def format_active_text(chat_id: int) -> str:
    await maybe_settle_all(chat_id)
    auctions = list_active(chat_id)
    lines = ["🔨 مزایده‌های فعال", "─────────────────"]
    if not auctions:
        lines.append("الان هیچ مزایده‌ی فعالی نیست.")
        lines.append("با «مزایده [نوع] [شناسه] [قیمت پایه] [مدت دقیقه]» یه آیتم بذار حراج.")
        return "\n".join(lines)
    for a in auctions:
        bid_txt = f"{a['current_bid']:,}" if a["current_bid"] else f"پایه: {a['start_price']:,}"
        lines.append(
            f"#{a['id']} {a['label']} — فعلی: {bid_txt} {config.CURRENCY_NAME} "
            f"({len(a['bids'])} پیشنهاد) — {_fmt_remaining(a)}"
        )
    lines.append("")
    lines.append("پیشنهاد بده: «پیشنهاد [شناسه مزایده] [مبلغ]»")
    return "\n".join(lines)


def format_my_auctions_text(chat_id: int, uid: int) -> str:
    rows = my_auctions(chat_id, uid)
    lines = ["🔨 مزایده‌های من", "─────────────────"]
    if not rows:
        lines.append("هنوز توی هیچ مزایده‌ای فروشنده یا بالاترین پیشنهاددهنده نیستی.")
        return "\n".join(lines)
    status_fa = {
        "active": "🟢 فعال", "ended_sold": "✅ فروخته‌شد",
        "ended_unsold": "⚪️ بدونِ خریدار تمام شد", "cancelled": "🚫 لغو شد",
    }
    for a in rows:
        role = "فروشنده" if a["seller_uid"] == uid else "پیشنهاددهنده"
        bid_txt = f"{a['current_bid']:,}" if a["current_bid"] else f"پایه: {a['start_price']:,}"
        lines.append(f"#{a['id']} {a['label']} ({role}) — {status_fa.get(a['status'], a['status'])} — {bid_txt}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 📨 Command Handlers
# ---------------------------------------------------------------------------

def _group_only_or_none(update: Update):
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return None
    return chat


_ASSET_TYPE_ALIASES = {
    "ملک": "property", "خونه": "property", "خانه": "property", "property": "property",
    "خودرو": "vehicle", "ماشین": "vehicle", "vehicle": "vehicle",
    "کسبوکار": "business", "کسب‌وکار": "business", "business": "business",
}


def _resolve_asset_type(text: str) -> str | None:
    return _ASSET_TYPE_ALIASES.get(text.strip())


async def auctions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مزایده‌ها / /auctions"""
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_auction"):
        await message.reply_text(economy_admin.module_off_message(chat.id, "economy_module_auction"))
        return
    await message.reply_text(await format_active_text(chat.id))


async def create_auction_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مزایده [نوع: ملک/خودرو/کسب‌وکار] [شناسه] [قیمت پایه] [مدت دقیقه] / /auction"""
    host = _host()
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_auction"):
        await message.reply_text(economy_admin.module_off_message(chat.id, "economy_module_auction"))
        return
    args = context.args or []
    if len(args) < 4:
        await message.reply_text(
            "استفاده: «مزایده [ملک|خودرو|کسب‌وکار] [شناسه] [قیمتِ پایه] [مدت به دقیقه]»"
        )
        return
    asset_type = _resolve_asset_type(args[0])
    if not asset_type:
        await message.reply_text("نوعِ دارایی باید یکی از این‌ها باشه: ملک، خودرو، کسب‌وکار.")
        return
    try:
        asset_id = int(args[1])
        start_price = int(args[2].replace(",", ""))
        duration = int(args[3])
    except ValueError:
        await message.reply_text("شناسه/قیمت/مدت نامعتبره.")
        return
    uid = update.effective_user.id
    try:
        a = create_auction(chat.id, uid, asset_type, asset_id, start_price, duration)
    except AuctionError as e:
        await message.reply_text(f"❌ {e}")
        return
    await message.reply_text(
        f"🔨 مزایده‌ی #{a['id']} برای {a['label']} ایجاد شد.\n"
        f"قیمتِ پایه: {a['start_price']:,} {config.CURRENCY_NAME} — مدت: {duration} دقیقه."
    )
    await host.save_state()


async def bid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پیشنهاد [شناسه مزایده] [مبلغ] / /bid <auction_id> <amount>"""
    host = _host()
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_auction"):
        await message.reply_text(economy_admin.module_off_message(chat.id, "economy_module_auction"))
        return
    args = context.args or []
    if len(args) < 2:
        await message.reply_text("استفاده: «پیشنهاد [شناسه مزایده] [مبلغ]»")
        return
    try:
        auction_id = int(args[0])
        amount = int(args[1].replace(",", ""))
    except ValueError:
        await message.reply_text("شناسه/مبلغ نامعتبره.")
        return
    uid = update.effective_user.id
    try:
        a = await place_bid(chat.id, uid, auction_id, amount)
    except (AuctionError, economy_core.InsufficientFundsError) as e:
        await message.reply_text(f"❌ {e}")
        return
    await message.reply_text(
        f"✅ پیشنهادِ {amount:,} {config.CURRENCY_NAME} روی مزایده‌ی #{auction_id} ثبت شد "
        f"({_fmt_remaining(a)})."
    )
    await host.save_state()


async def cancel_auction_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لغو مزایده [شناسه] / /cancelauction <id>"""
    host = _host()
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «لغو مزایده [شناسه]»")
        return
    try:
        auction_id = int(args[0])
    except ValueError:
        await message.reply_text("شناسه نامعتبره.")
        return
    uid = update.effective_user.id
    try:
        await cancel_auction(chat.id, uid, auction_id)
    except AuctionError as e:
        await message.reply_text(f"❌ {e}")
        return
    await message.reply_text("✅ مزایده لغو شد و دارایی به تو برگشت.")
    await host.save_state()


async def my_auctions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مزایده‌های من / /myauctions"""
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    await maybe_settle_all(chat.id)
    uid = update.effective_user.id
    await message.reply_text(format_my_auctions_text(chat.id, uid))


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "version": STATE_VERSION,
        "auctions": {str(c): v for c, v in _auctions.items()},
        "next_auction_id": {str(c): v for c, v in _next_auction_id.items()},
    }


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
            logger.warning(f"ذخیره‌ی state مزایده ناموفق بود: {e}")


def load_state():
    data = None
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"خوندن state مزایده ناموفق بود: {e}؛ تلاش برای بازیابی از بکاپ...")
            data = None
    if data is None:
        bak = STATE_FILE + ".bak"
        if os.path.exists(bak):
            try:
                with open(bak, encoding="utf-8") as f:
                    data = json.load(f)
                logger.warning("state اصلیِ مزایده خراب/نامعتبر بود؛ از بکاپ بازیابی شد.")
            except Exception as e:
                logger.warning(f"خوندنِ بکاپِ مزایده هم ناموفق بود: {e}")
                return
        else:
            return

    for cid_s, auctions in data.get("auctions", {}).items():
        cid = int(cid_s)
        for aid_s, a in auctions.items():
            a = dict(a)
            a["id"] = int(a["id"])
            a["seller_uid"] = int(a["seller_uid"])
            if a.get("current_bidder") is not None:
                a["current_bidder"] = int(a["current_bidder"])
            for b in a.get("bids", []):
                b["uid"] = int(b["uid"])
            _auctions[cid][int(aid_s)] = a

    for cid_s, v in data.get("next_auction_id", {}).items():
        _next_auction_id[int(cid_s)] = int(v)

    logger.info("وضعیتِ مزایده از فایل بارگذاری شد.")
