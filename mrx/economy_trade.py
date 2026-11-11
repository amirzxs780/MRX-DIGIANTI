# -*- coding: utf-8 -*-
"""
DIGNANTI ECONOMY TRADE — فاز ۸ (V18: تجارت، واردات و صادرات)
=====================================================================
بخشِ ۸ (تجارت و واردات/صادرات) + بخشِ ۱۱ («قرارداد تأمینِ کالا» که توی
هر دو بخش خواسته شده بود - یه ماژولِ واحد هر دو رو پوشش می‌ده).

تا این فاز، فقط کسب‌وکارها (economy_business) به کالاهایِ economy_supply
(فازِ۲) دسترسیِ مستقیم داشتن. این ماژول دو تا قابلیتِ جدید اضافه می‌کنه:

۱) خرید/فروشِ عمده - هر کاربری (نه فقط صاحبِ کسب‌وکار) می‌تونه مستقیم از
   بازارِ زنده کالا بخره/بفروشه. هزینه‌ی حمل‌ونقل/انبارداری (بخشِ۸) به‌صورتِ
   کارمزدِ درصدی روی قیمتِ لحظه‌ای اعمال می‌شه. خریدها/فروش‌ها دقیقاً مثلِ
   خریدِ موجودیِ کسب‌وکار (فازِ۴) روی economy_supply اثرِ واقعی می‌ذارن.

۲) قراردادِ تأمینِ کالا (OTC، دوطرفه) - فروشنده مقداری کالا رو که از قبل
   واقعاً در اختیار داره (نه از هیچ چیز تولیدشده) با قیمتِ ثابت به یه
   خریدارِ مشخص پیشنهاد می‌ده. مثلِ economy_auction، کالایِ پیشنهادی
   بلافاصله Escrow می‌شه (از holdingsِ فروشنده کم می‌شه) تا نشه همون کالا
   رو دوبار پیشنهاد داد؛ در صورتِ لغو، برمی‌گرده.

این ماژول هیچ‌وقت مستقیم wallet/bank یا holdings رو بدونِ عبور از
economy_core/خودش دست‌کاری نمی‌کنه.
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

logger = logging.getLogger("economy_trade")

STATE_FILE = getattr(config, "ECONOMY_TRADE_STATE_FILE", "economy_trade_state.json")
STATE_VERSION = 1

_save_lock = asyncio.Lock()


class TradeError(Exception):
    pass


def _commodities() -> dict:
    return getattr(config, "COMMODITIES", {})


# ---------------------------------------------------------------------------
# 💾 State
# ---------------------------------------------------------------------------

# chat_id -> uid -> commodity_key -> qty  (موجودیِ شخصیِ خریداری‌شده‌ی هر کاربر)
_holdings: dict[int, dict] = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))

# chat_id -> contract_id(int) -> {id, seller_uid, buyer_uid, commodity_key, qty,
#   unit_price, status: offered|accepted|cancelled, created_ts}
_contracts: dict[int, dict] = defaultdict(dict)
_next_contract_id: dict[int, int] = defaultdict(int)


def get_holding(chat_id: int, uid: int, key: str) -> float:
    return _holdings[chat_id][uid].get(key, 0.0)


def my_holdings(chat_id: int, uid: int) -> dict[str, float]:
    return {k: v for k, v in _holdings[chat_id][uid].items() if v > 0}


# ---------------------------------------------------------------------------
# 📦 خرید/فروشِ عمده (مستقیم از بازارِ زنده‌ی economy_supply)
# ---------------------------------------------------------------------------

async def bulk_buy(chat_id: int, uid: int, key: str, qty: int) -> dict:
    if key not in _commodities():
        raise TradeError(f"کالایِ ناشناخته: {key}")
    min_qty = getattr(config, "TRADE_MIN_BULK_QTY", 10)
    if qty < min_qty:
        raise TradeError(f"حداقلِ خریدِ عمده {min_qty} واحده.")

    import economy_supply
    price = economy_supply.get_price(chat_id, key)
    fee_pct = getattr(config, "TRADE_BULK_FEE_PERCENT", 0.02)
    base_cost = qty * price
    cost = round(base_cost * (1 + fee_pct))

    await economy_core.remove_coins(
        chat_id, uid, cost, kind="OTHER",
        note=f"خریدِ عمده‌ی {qty} واحد {_commodities()[key]['name']}",
    )
    economy_supply.record_demand(chat_id, key, qty)
    _holdings[chat_id][uid][key] = _holdings[chat_id][uid].get(key, 0.0) + qty
    # فاز ۱۰ V18 (تکمیلِ بخشِ ۱۰) — هزینه‌ی حمل‌ونقل/انبارداری که همیشه گرفته
    # می‌شد، الان به‌عنوانِ عوارض به خزانه‌ی عمومی می‌ره؛ هزینه‌ی خریدار ثابت موند.
    fee_amount = cost - round(base_cost)
    if fee_amount > 0:
        try:
            import economy_treasury
            economy_treasury.deposit(chat_id, fee_amount, source="عوارضِ حمل‌ونقلِ تجاری",
                                      note=f"خریدِ عمده‌ی {_commodities()[key]['name']}")
        except Exception as e:
            logger.warning(f"واریزِ عوارض به خزانه ناموفق بود: {e}")
    return {"qty": qty, "cost": cost, "price": price}


async def bulk_sell(chat_id: int, uid: int, key: str, qty: int) -> dict:
    if key not in _commodities():
        raise TradeError(f"کالایِ ناشناخته: {key}")
    min_qty = getattr(config, "TRADE_MIN_BULK_QTY", 10)
    if qty < min_qty:
        raise TradeError(f"حداقلِ فروشِ عمده {min_qty} واحده.")
    held = get_holding(chat_id, uid, key)
    if qty > held + 1e-9:
        raise TradeError(f"فقط {held:.0f} واحد {_commodities()[key]['name']} داری.")

    import economy_supply
    price = economy_supply.get_price(chat_id, key)
    fee_pct = getattr(config, "TRADE_BULK_FEE_PERCENT", 0.02)
    gross = qty * price
    proceeds = round(gross * (1 - fee_pct))

    _holdings[chat_id][uid][key] = held - qty
    economy_supply.record_supply(chat_id, key, qty)
    await economy_core.add_coins(
        chat_id, uid, proceeds, kind="OTHER",
        note=f"فروشِ عمده‌ی {qty} واحد {_commodities()[key]['name']}",
    )
    fee_amount = round(gross) - proceeds
    if fee_amount > 0:
        try:
            import economy_treasury
            economy_treasury.deposit(chat_id, fee_amount, source="عوارضِ حمل‌ونقلِ تجاری",
                                      note=f"فروشِ عمده‌ی {_commodities()[key]['name']}")
        except Exception as e:
            logger.warning(f"واریزِ عوارض به خزانه ناموفق بود: {e}")
    return {"qty": qty, "proceeds": proceeds, "price": price}


# ---------------------------------------------------------------------------
# 📜 قراردادِ تأمینِ کالا (OTC، دوطرفه، قیمتِ قفل‌شده)
# ---------------------------------------------------------------------------

def _active_offer_count(chat_id: int, seller_uid: int) -> int:
    return sum(
        1 for c in _contracts[chat_id].values()
        if c["seller_uid"] == seller_uid and c["status"] == "offered"
    )


def offer_contract(chat_id: int, seller_uid: int, buyer_uid: int,
                    key: str, qty: int, unit_price: int) -> dict:
    if key not in _commodities():
        raise TradeError(f"کالایِ ناشناخته: {key}")
    if qty <= 0 or unit_price <= 0:
        raise TradeError("تعداد و قیمت باید مثبت باشن.")
    if seller_uid == buyer_uid:
        raise TradeError("نمی‌تونی به خودت قرارداد پیشنهاد بدی.")
    max_active = getattr(config, "TRADE_CONTRACT_MAX_ACTIVE", 5)
    if _active_offer_count(chat_id, seller_uid) >= max_active:
        raise TradeError(f"حداکثر {max_active} قراردادِ پیشنهادیِ هم‌زمان مجازه.")

    held = get_holding(chat_id, seller_uid, key)
    if qty > held + 1e-9:
        raise TradeError(f"فقط {held:.0f} واحد {_commodities()[key]['name']} داری - اول با خریدِ عمده تهیه کن.")

    _holdings[chat_id][seller_uid][key] = held - qty  # Escrow

    contract_id = _next_contract_id[chat_id] + 1
    _next_contract_id[chat_id] = contract_id
    contract = {
        "id": contract_id, "seller_uid": seller_uid, "buyer_uid": buyer_uid,
        "commodity_key": key, "qty": qty, "unit_price": unit_price,
        "status": "offered", "created_ts": time.time(),
    }
    _contracts[chat_id][contract_id] = contract
    return contract


async def accept_contract(chat_id: int, buyer_uid: int, contract_id: int) -> dict:
    c = _contracts[chat_id].get(contract_id)
    if not c or c["status"] != "offered":
        raise TradeError("این قرارداد دیگه در دسترس نیست.")
    if c["buyer_uid"] != buyer_uid:
        raise TradeError("این قرارداد برایِ تو نیست.")

    total = round(c["qty"] * c["unit_price"])
    await economy_core.remove_coins(
        chat_id, buyer_uid, total, kind="OTHER",
        note=f"خریدِ قراردادیِ {c['qty']} واحد {_commodities()[c['commodity_key']]['name']}",
    )
    _holdings[chat_id][buyer_uid][c["commodity_key"]] = (
        _holdings[chat_id][buyer_uid].get(c["commodity_key"], 0.0) + c["qty"]
    )
    await economy_core.add_coins(
        chat_id, c["seller_uid"], total, kind="OTHER",
        note=f"فروشِ قراردادیِ {c['qty']} واحد {_commodities()[c['commodity_key']]['name']}",
    )
    c["status"] = "accepted"
    return c


def cancel_contract(chat_id: int, uid: int, contract_id: int) -> dict:
    c = _contracts[chat_id].get(contract_id)
    if not c or c["status"] != "offered":
        raise TradeError("این قرارداد دیگه در دسترس نیست.")
    if c["seller_uid"] != uid:
        raise TradeError("فقط فروشنده می‌تونه لغوش کنه.")
    _holdings[chat_id][uid][c["commodity_key"]] = (
        _holdings[chat_id][uid].get(c["commodity_key"], 0.0) + c["qty"]
    )
    c["status"] = "cancelled"
    return c


def offers_for_buyer(chat_id: int, buyer_uid: int) -> list[dict]:
    return [c for c in _contracts[chat_id].values() if c["buyer_uid"] == buyer_uid and c["status"] == "offered"]


def my_contracts(chat_id: int, uid: int) -> list[dict]:
    return [c for c in _contracts[chat_id].values() if c["seller_uid"] == uid or c["buyer_uid"] == uid]


# ---------------------------------------------------------------------------
# 🖊 نمایش متنی
# ---------------------------------------------------------------------------

def format_holdings_text(chat_id: int, uid: int) -> str:
    holdings = my_holdings(chat_id, uid)
    lines = ["📦 موجودیِ کالاهای من", "─────────────────"]
    if not holdings:
        lines.append("هیچ کالایی نداری. با «خرید عمده [کالا] [تعداد]» شروع کن.")
        return "\n".join(lines)
    for key, qty in holdings.items():
        name = _commodities().get(key, {}).get("name", key)
        lines.append(f"{name}: {qty:.0f} واحد")
    return "\n".join(lines)


def format_offers_text(chat_id: int, uid: int) -> str:
    offers = offers_for_buyer(chat_id, uid)
    lines = ["📜 پیشنهادهایِ قراردادی برایِ من", "─────────────────"]
    if not offers:
        lines.append("هیچ پیشنهادِ بازی برایِ تو نیست.")
        return "\n".join(lines)
    for c in offers:
        name = _commodities().get(c["commodity_key"], {}).get("name", c["commodity_key"])
        total = c["qty"] * c["unit_price"]
        lines.append(f"#{c['id']} {c['qty']:.0f} واحد {name} به قیمتِ واحدِ {c['unit_price']:,} (مجموع: {total:,})")
    lines.append("")
    lines.append("قبول: «قبول قرارداد [شناسه]»")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 📨 Command Handlers
# ---------------------------------------------------------------------------

def _host():
    import bot as host
    return host


def _group_only_or_none(update: Update):
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return None
    return chat


async def bulk_buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خرید عمده [کالا] [تعداد] / /bulkbuy <key> <qty>"""
    host = _host()
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if len(args) < 2:
        names = "، ".join(info["name"] for info in _commodities().values())
        await message.reply_text(f"استفاده: «خرید عمده [کالا] [تعداد]». کالاها: {names}")
        return
    key = args[0].strip()
    try:
        qty = int(args[1])
    except ValueError:
        await message.reply_text("تعداد نامعتبره.")
        return
    uid = update.effective_user.id
    try:
        result = await bulk_buy(chat.id, uid, key, qty)
    except (TradeError, economy_core.InsufficientFundsError) as e:
        await message.reply_text(f"❌ {e}")
        return
    await message.reply_text(
        f"✅ {result['qty']} واحد خریداری شد به قیمتِ کل {result['cost']:,} {config.CURRENCY_NAME}"
    )
    await host.save_state()


async def bulk_sell_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فروش عمده [کالا] [تعداد] / /bulksell <key> <qty>"""
    host = _host()
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if len(args) < 2:
        await message.reply_text("استفاده: «فروش عمده [کالا] [تعداد]»")
        return
    key = args[0].strip()
    try:
        qty = int(args[1])
    except ValueError:
        await message.reply_text("تعداد نامعتبره.")
        return
    uid = update.effective_user.id
    try:
        result = await bulk_sell(chat.id, uid, key, qty)
    except TradeError as e:
        await message.reply_text(f"❌ {e}")
        return
    await message.reply_text(
        f"✅ {result['qty']} واحد فروخته شد: +{result['proceeds']:,} {config.CURRENCY_NAME}"
    )
    await host.save_state()


async def holdings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """کالاهای من / /holdings"""
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    await message.reply_text(format_holdings_text(chat.id, uid))


async def offer_contract_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قرارداد تامین کالا [ریپلای یا @یوزرنیم] [کالا] [تعداد] [قیمت واحد]"""
    host = _host()
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not message.reply_to_message:
        await message.reply_text(
            "برایِ پیشنهادِ قرارداد، رویِ پیامِ طرفِ‌مقابل Reply کن و بنویس: "
            "«قرارداد تامین کالا [کالا] [تعداد] [قیمتِ واحد]»"
        )
        return
    args = context.args or []
    if len(args) < 3:
        await message.reply_text("استفاده: (با Reply) «قرارداد تامین کالا [کالا] [تعداد] [قیمتِ واحد]»")
        return
    key = args[0].strip()
    try:
        qty = int(args[1])
        unit_price = int(args[2].replace(",", ""))
    except ValueError:
        await message.reply_text("تعداد/قیمت نامعتبره.")
        return
    seller_uid = update.effective_user.id
    buyer_uid = message.reply_to_message.from_user.id
    try:
        c = offer_contract(chat.id, seller_uid, buyer_uid, key, qty, unit_price)
    except TradeError as e:
        await message.reply_text(f"❌ {e}")
        return
    await message.reply_text(
        f"📜 قراردادِ #{c['id']} پیشنهاد شد: {qty} واحد به قیمتِ واحدِ {unit_price:,} "
        f"(مجموع: {qty*unit_price:,}). طرفِ‌مقابل با «قبول قرارداد {c['id']}» می‌تونه قبول کنه."
    )
    await host.save_state()


async def accept_contract_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قبول قرارداد [شناسه] / /acceptcontract <id>"""
    host = _host()
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «قبول قرارداد [شناسه]»")
        return
    try:
        contract_id = int(args[0])
    except ValueError:
        await message.reply_text("شناسه نامعتبره.")
        return
    uid = update.effective_user.id
    try:
        c = await accept_contract(chat.id, uid, contract_id)
    except (TradeError, economy_core.InsufficientFundsError) as e:
        await message.reply_text(f"❌ {e}")
        return
    await message.reply_text(f"✅ قراردادِ #{c['id']} قبول شد. {c['qty']:.0f} واحد کالا به موجودیت اضافه شد.")
    await host.save_state()


async def my_contracts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قراردادهای من / /mycontracts"""
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    await message.reply_text(format_offers_text(chat.id, uid))


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "version": STATE_VERSION,
        "holdings": {
            str(c): {str(u): dict(v) for u, v in d.items()}
            for c, d in _holdings.items()
        },
        "contracts": {str(c): v for c, v in _contracts.items()},
        "next_contract_id": {str(c): v for c, v in _next_contract_id.items()},
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
            logger.warning(f"ذخیره‌ی state تجارت ناموفق بود: {e}")


def load_state():
    data = None
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"خوندن state تجارت ناموفق بود: {e}؛ تلاش برای بازیابی از بکاپ...")
            data = None
    if data is None:
        bak = STATE_FILE + ".bak"
        if os.path.exists(bak):
            try:
                with open(bak, encoding="utf-8") as f:
                    data = json.load(f)
                logger.warning("state اصلیِ تجارت خراب/نامعتبر بود؛ از بکاپ بازیابی شد.")
            except Exception as e:
                logger.warning(f"خوندنِ بکاپِ تجارت هم ناموفق بود: {e}")
                return
        else:
            return

    for cid_s, d in data.get("holdings", {}).items():
        cid = int(cid_s)
        for uid_s, v in d.items():
            uid = int(uid_s)
            for key, qty in v.items():
                _holdings[cid][uid][key] = float(qty)

    for cid_s, contracts in data.get("contracts", {}).items():
        cid = int(cid_s)
        for cont_id_s, c in contracts.items():
            c = dict(c)
            c["id"] = int(c["id"])
            c["seller_uid"] = int(c["seller_uid"])
            c["buyer_uid"] = int(c["buyer_uid"])
            _contracts[cid][int(cont_id_s)] = c

    for cid_s, v in data.get("next_contract_id", {}).items():
        _next_contract_id[int(cid_s)] = int(v)

    logger.info("وضعیتِ تجارت از فایل بارگذاری شد.")
