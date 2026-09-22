# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY BLACK MARKET — فاز ۷ (Module 15)
======================================================
یه موجودی محدود و چرخشیه (Lazy Tick مثل Market فاز ۳) از آیتم‌های کمیاب
(از همون ITEM_CATALOG فاز ۷/Module 17، rarity بالا). خرید از اینجا مستقیماً
به Inventory کاربر (economy_inventory) اضافه می‌شه. «فروش کالا» برعکسش رو
انجام می‌ده: از Inventory کاربر می‌گیره و به‌جاش سکه (با تخفیف، Money Sink)
می‌ده. «حراجی» یه مزایده‌ی ساده با Escrow امنه.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

import config
import economy_core
import economy_inventory

logger = logging.getLogger("economy_blackmarket")

STATE_FILE = getattr(config, "ECONOMY_BLACKMARKET_STATE_FILE", "economy_blackmarket_state.json")
CATALOG: dict = getattr(config, "ITEM_CATALOG", {})

_save_lock = asyncio.Lock()

# chat_id -> {"rotation_ts": float, "stock": {key: remaining_count}, "prices": {key: price}}
_rotation = defaultdict(dict)

# chat_id -> {"key","price","seller":None,"bids":[{"uid","amount"}],"ends_ts"} | None
_auction = defaultdict(lambda: None)


def _host():
    import bot as host
    return host


def _rotation_pool() -> list[str]:
    return [k for k, info in CATALOG.items() if info["rarity"] in ("RARE", "EPIC", "LEGENDARY", "MYTHIC")] or list(CATALOG.keys())


def _ensure_rotation(chat_id: int) -> dict:
    rec = _rotation[chat_id]
    interval = getattr(config, "BLACKMARKET_ROTATION_HOURS", 6) * 3600
    now = time.time()
    if rec and now - rec.get("rotation_ts", 0) < interval:
        return rec

    pool = _rotation_pool()
    count = min(getattr(config, "BLACKMARKET_ITEMS_PER_ROTATION", 3), len(pool))
    chosen = random.sample(pool, k=count) if pool else []
    stock = {}
    prices = {}
    for key in chosen:
        info = CATALOG[key]
        stock[key] = random.randint(getattr(config, "BLACKMARKET_STOCK_MIN", 1), getattr(config, "BLACKMARKET_STOCK_MAX", 5))
        mult = random.uniform(getattr(config, "BLACKMARKET_PRICE_MULTIPLIER_MIN", 0.8), getattr(config, "BLACKMARKET_PRICE_MULTIPLIER_MAX", 1.6))
        prices[key] = round(info["price"] * mult)
    rec = {"rotation_ts": now, "stock": stock, "prices": prices}
    _rotation[chat_id] = rec
    return rec


# ---------------------------------------------------------------------------
# 📋 نمایش
# ---------------------------------------------------------------------------

async def blackmarket_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بازار سیاه / /blackmarket"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_blackmarket"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    rec = _ensure_rotation(chat.id)
    interval = getattr(config, "BLACKMARKET_ROTATION_HOURS", 6) * 3600
    remaining = interval - (time.time() - rec["rotation_ts"])
    lines = ["🖤 بازار سیاه", ""]
    if not rec["stock"]:
        lines.append("فعلاً چیزی موجود نیست.")
    for key, qty in rec["stock"].items():
        if qty <= 0:
            continue
        info = CATALOG[key]
        lines.append(f"{info['name']} ({info['rarity']}) — {rec['prices'][key]:,} {config.CURRENCY_NAME} — موجودی: {qty}")
    lines.append("")
    lines.append(f"⏳ چرخش بعدی: {int(remaining // 3600)} ساعت و {int((remaining % 3600) // 60)} دقیقه‌ی دیگه")
    lines.append("خرید: «خرید کالا [کد]» — فروش از کیفت: «فروش کالا [کد] [تعداد]»")
    await message.reply_text("\n".join(lines))


async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خرید کالا [کد] / /buyblack <key>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_blackmarket"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «خرید کالا [کد]». با «بازار سیاه» موجودی رو ببین.")
        return
    key = args[0]
    rec = _ensure_rotation(chat.id)
    qty = rec["stock"].get(key, 0)
    if qty <= 0:
        await message.reply_text("این کالا الان توی بازار سیاه موجود نیست.")
        return

    uid = update.effective_user.id
    price = rec["prices"][key]
    try:
        await economy_core.remove_coins(chat.id, uid, price, kind="blackmarket_buy", note=f"خرید {CATALOG[key]['name']} از بازار سیاه")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. قیمت: {price:,} {config.CURRENCY_NAME}")
        return

    rec["stock"][key] = qty - 1
    economy_inventory.add_item(chat.id, uid, key, 1)
    await message.reply_text(f"✅ {CATALOG[key]['name']} از بازار سیاه خریداری شد!")
    await host.save_state()


async def sell_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فروش کالا [کد] [تعداد] / /sellblack <key> <qty>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return

    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «فروش کالا [کد] [تعداد]»")
        return
    key = args[0]
    info = CATALOG.get(key)
    if not info or not info.get("sellable", True):
        await message.reply_text("این کالا قابل‌فروش نیست.")
        return
    try:
        qty = int(args[1]) if len(args) > 1 else 1
    except ValueError:
        qty = -1
    if qty <= 0:
        await message.reply_text("تعداد نامعتبره.")
        return

    uid = update.effective_user.id
    if not economy_inventory.remove_item(chat.id, uid, key, qty):
        await message.reply_text(f"از {info['name']} به این تعداد نداری.")
        return

    fraction = getattr(config, "BLACKMARKET_SELL_FRACTION", 0.5)
    payout = round(info["price"] * fraction * qty)
    new_wallet = await economy_core.add_coins(chat.id, uid, payout, kind="blackmarket_sell", note=f"فروش {info['name']}")
    await message.reply_text(
        f"✅ {qty} عدد {info['name']} فروخته شد: +{payout} {config.CURRENCY_NAME}\n💰 موجودی: {new_wallet} {config.CURRENCY_NAME}"
    )
    await host.save_state()


async def my_items_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """کالای من — نمایش آیتم‌های Inventory (همون economy_inventory)."""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    text = economy_inventory.extra_inventory_text(chat.id, uid)
    await message.reply_text(text.strip() if text.strip() else "کیفت خالیه. با «بازار سیاه» یا «خرید [کد]» چیزی بردار.")


# ---------------------------------------------------------------------------
# 🔨 حراجی
# ---------------------------------------------------------------------------

def _resolve_auction_if_expired(chat_id: int) -> str | None:
    """اگه حراجی فعلی منقضی شده، می‌بندتش و متن نتیجه رو برمی‌گردونه (یا None)."""
    auction = _auction[chat_id]
    if not auction or time.time() < auction["ends_ts"]:
        return None
    if auction["bids"]:
        winner = max(auction["bids"], key=lambda b: b["amount"])
        economy_inventory.add_item(chat_id, winner["uid"], auction["key"], 1)
        _auction[chat_id] = None
        info = CATALOG[auction["key"]]
        return f"🔨 حراجی {info['name']} تموم شد! برنده: {winner['uid']} با {winner['amount']:,} {config.CURRENCY_NAME}"
    _auction[chat_id] = None
    return "🔨 حراجی بدون هیچ پیشنهادی تموم شد."


async def auction_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حراجی [مبلغ] / /auction <amount>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_blackmarket"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    result_text = _resolve_auction_if_expired(chat.id)
    if result_text:
        await message.reply_text(result_text)
        await host.save_state()
        return

    args = context.args or []
    uid = update.effective_user.id

    if not _auction[chat.id]:
        pool = [k for k in CATALOG if CATALOG[k]["rarity"] != "COMMON"] or list(CATALOG.keys())
        key = random.choice(pool)
        duration_min = getattr(config, "BLACKMARKET_AUCTION_DURATION_MINUTES", 60)
        _auction[chat.id] = {"key": key, "bids": [], "ends_ts": time.time() + duration_min * 60}
        info = CATALOG[key]
        await message.reply_text(
            f"🔨 حراجی جدید شروع شد: {info['name']} ({info['rarity']})\n"
            f"⏳ {duration_min} دقیقه وقت داری پیشنهاد بدی: «حراجی [مبلغ]»"
        )
        await host.save_state()
        return

    auction = _auction[chat.id]
    if not args:
        info = CATALOG[auction["key"]]
        current_top = max((b["amount"] for b in auction["bids"]), default=0)
        remaining = auction["ends_ts"] - time.time()
        await message.reply_text(
            f"🔨 حراجی فعلی: {info['name']} — بالاترین پیشنهاد: {current_top:,} {config.CURRENCY_NAME}\n"
            f"⏳ {int(remaining // 60)} دقیقه مونده. برای شرکت: «حراجی [مبلغ]»"
        )
        return

    try:
        amount = int(args[0].replace(",", ""))
    except ValueError:
        amount = -1
    current_top = max((b["amount"] for b in auction["bids"]), default=0)
    min_next = current_top + getattr(config, "BLACKMARKET_AUCTION_MIN_INCREMENT", 100)
    if amount < min_next:
        await message.reply_text(f"پیشنهادت باید حداقل {min_next:,} {config.CURRENCY_NAME} باشه.")
        return

    try:
        await economy_core.remove_coins(chat.id, uid, amount, kind="auction_bid", note="پیشنهاد حراجی")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست ({economy_core.get_wallet(chat.id, uid)} {config.CURRENCY_NAME}).")
        return

    # پیشنهاد قبلی همین کاربر (اگه بود) رو پس بده تا Escrow دوبل نشه
    for b in list(auction["bids"]):
        if b["uid"] == uid:
            await economy_core.add_coins(chat.id, uid, b["amount"], kind="auction_refund", note="بازگشت پیشنهاد قبلی")
            auction["bids"].remove(b)

    auction["bids"].append({"uid": uid, "amount": amount})
    await message.reply_text(f"✅ پیشنهادت ({amount:,} {config.CURRENCY_NAME}) ثبت شد!")
    await host.save_state()


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "rotation": {str(c): v for c, v in _rotation.items()},
        "auction": {str(c): v for c, v in _auction.items() if v},
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
            logger.warning(f"ذخیره‌ی state موتور Black Market ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور Black Market ناموفق بود: {e}")
        return
    for cid, v in data.get("rotation", {}).items():
        _rotation[int(cid)] = v
    for cid, v in data.get("auction", {}).items():
        _auction[int(cid)] = v
    logger.info("وضعیت موتور Black Market از فایل بارگذاری شد.")
