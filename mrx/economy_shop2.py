# -*- coding: utf-8 -*-
"""
DIGIANTI SHOP 2.0 — فاز ۹ (Module 25)
==========================================
فروشگاه فعلی (config.SHOP_ITEMS + bot.buy_command) دست‌نخورده می‌مونه؛ این
فایل فقط یه «پیشنهاد ویژه‌ی روزانه» اضافه می‌کنه — بدون نیاز به هیچ Stateای:
آیتم امروز از یه Seed مبتنی بر (chat_id + تاریخ امروز) به‌صورت Deterministic
انتخاب می‌شه، یعنی همه‌ی اعضای گروه دقیقاً همون یه پیشنهاد رو می‌بینن، و فردا
خودش عوض می‌شه — بدون نیاز به Persist یا Cron جدا.

خرید هم چیز جدیدی نمی‌سازه: موقتاً یه Price Override (همون مکانیزم فعلی
`_shop_price_overrides` که «تنظیم قیمت فروشگاه» ادمین ازش استفاده می‌کنه) ست
می‌کنه، bot.buy_command واقعی رو صدا می‌زنه (همون منطق قدیمی: Timed Item یا
Badge، Transaction Log، Achievement Sync)، و بعدش Override رو برمی‌گردونه.
"""

from __future__ import annotations

import random
import time

from telegram import Update
from telegram.ext import ContextTypes

import config


def _host():
    import bot as host
    return host


def _today_str() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _daily_deal_for(chat_id: int) -> tuple[str, dict, int]:
    """(کلید آیتم، مشخصات آیتم، قیمت تخفیف‌خورده) رو برمی‌گردونه."""
    items = config.SHOP_ITEMS
    seed = f"{chat_id}-{_today_str()}"
    rng = random.Random(seed)
    key = rng.choice(list(items.keys()))
    item = items[key]
    discount = getattr(config, "DAILY_DEAL_DISCOUNT_FRACTION", 0.35)
    price = max(1, round(item["price"] * (1 - discount)))
    return key, item, price


async def daily_shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فروشگاه ویژه / /dailyshop"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not host.get_setting(chat.id, "economy_enabled"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    key, item, price = _daily_deal_for(chat.id)
    discount = getattr(config, "DAILY_DEAL_DISCOUNT_FRACTION", 0.35)
    lines = [
        "🛒 پیشنهاد ویژه‌ی امروز",
        "",
        f"{item['name']}",
        f"💰 قیمت عادی: {item['price']:,} {config.CURRENCY_NAME}",
        f"🔥 قیمت امروز: {price:,} {config.CURRENCY_NAME} ({discount*100:.0f}٪ تخفیف)",
        "",
        "با «خرید ویژه» بخرش — این پیشنهاد فردا عوض می‌شه.",
    ]
    await message.reply_text("\n".join(lines))


async def buy_daily_deal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خرید ویژه / /buydailydeal"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not host.get_setting(chat.id, "economy_enabled"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    key, item, price = _daily_deal_for(chat.id)
    overrides = host._shop_price_overrides[chat.id]
    prev_override = overrides.get(key)
    overrides[key] = price
    try:
        context.args = [key]
        await host.buy_command(update, context)
    finally:
        if prev_override is None:
            overrides.pop(key, None)
        else:
            overrides[key] = prev_override
