# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY PROPERTY — فاز ۵ (Module 11) + ارتقای فاز ۵ (Rent/Transfer/History)
========================================================================================
برخلاف City (یه شهر واحد per کاربر)، Property چندتا دارایی مجزا و قابل‌شمارشه:
هر ملک یه id جدا داره، جدا خرید/فروش/ارتقا می‌شه. درآمد هر ملک هم مثل City
به‌صورت Lazy (بر اساس زمان سپری‌شده) جمع می‌شه، با احتساب مالیات (Tax).

ارتقای فاز ۵ (این نسخه):
  * Property History — هر رویداد (خرید/ارتقا/فروش/انتقال/اجاره) روی خود
    رکورد ملک لاگ می‌شه (Migration-safe: ملک‌های قدیمی که "history" ندارن،
    موقع بارگذاری یه [] خالی می‌گیرن، هیچی خراب نمی‌شه).
  * انتقال مالکیت (هدیه/فروش مستقیم P2P) — بدون رفتن به «فروش به بازار» با
    ضریب کاهشی؛ کاربر می‌تونه مستقیم به بازیکن دیگه بفروشه/ببخشه.
  * اجاره — مالک ملکش رو اجاره می‌ده؛ مستأجر پول اجاره رو مستقیم (از طریق
    Economy Core) به مالک پرداخت می‌کنه و برای مدت مشخص "تصرف" ملک رو داره.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

import config
import economy_core
import economy_missions

logger = logging.getLogger("economy_property")

STATE_FILE = getattr(config, "ECONOMY_PROPERTY_STATE_FILE", "economy_property_state.json")
TYPES: dict = getattr(config, "PROPERTY_TYPES", {})
MAX_HISTORY_PER_PROPERTY = 20

_save_lock = asyncio.Lock()

# chat_id -> uid -> list[{"id","type","level","purchased_ts","last_collect_ts",
#                          "history":[{"ts","event","note"}], "rent_price":int|None,
#                          "tenant_id":int|None, "rent_until":float|None}]
_properties = defaultdict(lambda: defaultdict(list))
_next_id = defaultdict(dict)


def _host():
    import bot as host
    return host


def _new_id(chat_id: int, uid: int) -> int:
    n = _next_id[chat_id].get(uid, 0) + 1
    _next_id[chat_id][uid] = n
    return n


def _log_history(prop: dict, event: str, note: str = "") -> None:
    hist = prop.setdefault("history", [])
    hist.append({"ts": time.time(), "event": event, "note": note})
    if len(hist) > MAX_HISTORY_PER_PROPERTY:
        del hist[: len(hist) - MAX_HISTORY_PER_PROPERTY]


def owned(chat_id: int, uid: int) -> list[dict]:
    return _properties[chat_id][uid]


def shared_with_me(chat_id: int, uid: int) -> list[tuple[int, dict]]:
    """ارتقای فاز ۱۹ (Social/Family) — لیست (owner_id, property) که این کاربر
    Co-owner ازشونه (نه مالک اصلی، ولی توی جمع‌آوری درآمد سهیمه). فقط املاک
    خودِ کاربر که اسم همسرش (یا هرکسی) روش هدیه/Share شده."""
    result = []
    for owner_id, props in _properties.get(chat_id, {}).items():
        if owner_id == uid:
            continue
        for p in props:
            if p.get("co_owner_id") == uid:
                result.append((owner_id, p))
    return result


def owned_count_of_type(chat_id: int, uid: int, ptype: str) -> int:
    return sum(1 for p in owned(chat_id, uid) if p["type"] == ptype)


def current_price(chat_id: int, uid: int, ptype: str) -> int:
    info = TYPES[ptype]
    count = owned_count_of_type(chat_id, uid, ptype)
    growth = getattr(config, "PROPERTY_STACK_PRICE_GROWTH", 0.15)
    base = info["base_price"] * (1 + growth * count)
    # فاز ۴: قیمت ملک به منطقه‌ی محل زندگی کاربر وابسته‌ست (poor ارزون‌تر، vip گرون‌تر)
    try:
        import economy_district
        base *= economy_district.multiplier_for(chat_id, uid, "property_price")
    except Exception:
        pass
    try:
        import economy_events
        base *= economy_events.price_inflation_multiplier(chat_id)  # ارتقای فاز ۱۵: تورم
    except Exception:
        pass
    return round(base)


def current_value(prop: dict) -> int:
    info = TYPES[prop["type"]]
    return round(info["base_price"] * prop["level"])


async def _collect_one(chat_id: int, uid: int, prop: dict) -> int:
    info = TYPES[prop["type"]]
    net_rate = (info["income_per_hour"] - info["maintenance_per_hour"]) * prop["level"]
    now = time.time()
    elapsed_hours = min(
        (now - prop["last_collect_ts"]) / 3600.0,
        getattr(config, "PROPERTY_INCOME_MAX_ACCRUE_HOURS", 24),
    )
    prop["last_collect_ts"] = now
    if net_rate <= 0 or elapsed_hours <= 0:
        return 0
    gross = net_rate * elapsed_hours
    tax = gross * info["tax_rate"]
    amount = round(gross - tax)
    if amount > 0:
        await economy_core.add_coins(chat_id, uid, amount, kind="property_income", note=f"درآمد {info['name']} (پس از مالیات)")
    return amount


async def _collect_all(chat_id: int, uid: int) -> int:
    total = 0
    for prop in owned(chat_id, uid):
        total += await _collect_one(chat_id, uid, prop)
    return total


async def _collect_shared(chat_id: int, uid: int) -> int:
    """ارتقای فاز ۱۹: درآمد املاکی که این کاربر Co-owner‌شونه (نه مالک اصلی)
    رو هم جمع می‌کنه — مستقیم به کیف‌پول خودش (نه مالک اصلی) واریز می‌شه."""
    total = 0
    for _owner_id, prop in shared_with_me(chat_id, uid):
        total += await _collect_one(chat_id, uid, prop)
    return total


# ---------------------------------------------------------------------------
# 📊 Net Worth Source
# ---------------------------------------------------------------------------

def get_property_value(chat_id: int, uid: int) -> int:
    return sum(current_value(p) for p in owned(chat_id, uid))


economy_core.register_net_worth_source(get_property_value)


# ---------------------------------------------------------------------------
# 📋 نمایش / خرید / فروش / ارتقا
# ---------------------------------------------------------------------------

async def property_types_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """املاک / /property — لیست انواع ملک قابل‌خرید."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_property"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    lines = ["🏠 انواع املاک قابل‌خرید:", ""]
    for key, info in TYPES.items():
        price = current_price(chat.id, uid, key)
        net = info["income_per_hour"] - info["maintenance_per_hour"]
        lines.append(
            f"{info['name']} ({info['rarity']}) — قیمت: {price:,} {config.CURRENCY_NAME} — "
            f"درآمد خالص: {net}/ساعت — مالیات: {info['tax_rate']*100:.1f}٪"
        )
    lines.append("")
    lines.append("خرید: «خرید ملک [نوع]» — دیدن املاک خودت: «ملک من»")
    await message.reply_text("\n".join(lines))


def _find_type(name: str) -> str | None:
    name = name.strip().lower()
    if name in TYPES:
        return name
    for key, info in TYPES.items():
        if name in info["name"].lower():
            return key
    return None


async def buy_property_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خرید ملک [نوع] / /buyproperty <type>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_property"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «خرید ملک [نوع]». با «املاک» لیست انواع رو ببین.")
        return
    uid = update.effective_user.id
    key = _find_type(" ".join(args))
    if not key:
        await message.reply_text("همچین نوع ملکی نیست. با «املاک» لیست رو ببین.")
        return

    price = current_price(chat.id, uid, key)
    try:
        await economy_core.remove_coins(chat.id, uid, price, kind="property_buy", note=f"خرید {TYPES[key]['name']}")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. قیمت: {price:,} {config.CURRENCY_NAME}")
        return

    prop = {"id": _new_id(chat.id, uid), "type": key, "level": 1, "purchased_ts": time.time(),
            "last_collect_ts": time.time(), "co_owner_id": None}
    _log_history(prop, "PURCHASE", f"خرید اولیه به قیمت {price:,}")
    owned(chat.id, uid).append(prop)
    lines = [f"✅ {TYPES[key]['name']} #{prop['id']} خریداری شد!"]
    try:
        lines += await economy_missions.record_progress(chat.id, uid, "property_buy", 1)
    except Exception as e:
        logger.warning(f"ثبت پیشرفت ماموریت property_buy ناموفق بود: {e}")
    try:
        import economy_achievements
        lines.extend(economy_achievements.check_all(chat.id, uid))
    except Exception as e:
        logger.warning(f"چک دستاوردهای اقتصادی ناموفق بود: {e}")
    await message.reply_text("\n".join(lines))
    await host.save_state()


async def sell_property_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فروش ملک [شناسه] / /sellproperty <id>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return

    uid = update.effective_user.id
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «فروش ملک [شناسه]». شناسه‌ها رو با «ملک من» ببین.")
        return
    try:
        prop_id = int(args[0])
    except ValueError:
        await message.reply_text("شناسه‌ی نامعتبر.")
        return

    props = owned(chat.id, uid)
    target = next((p for p in props if p["id"] == prop_id), None)
    if not target:
        await message.reply_text("همچین ملکی توی مالکیتت نیست.")
        return

    await _collect_one(chat.id, uid, target)  # درآمد باقی‌مونده رو قبل از فروش تسویه کن
    resale_fraction = getattr(config, "PROPERTY_RESALE_FRACTION", 0.6)
    payout = round(current_value(target) * resale_fraction)
    _log_history(target, "SALE", f"فروش به بازار به قیمت {payout:,}")
    props.remove(target)
    new_wallet = await economy_core.add_coins(chat.id, uid, payout, kind="property_sell", note=f"فروش {TYPES[target['type']]['name']} #{prop_id}")
    await message.reply_text(
        f"✅ {TYPES[target['type']]['name']} #{prop_id} فروخته شد: +{payout} {config.CURRENCY_NAME} "
        f"({resale_fraction*100:.0f}٪ ارزش، طبیعیه که کمتر از قیمت خرید باشه).\n"
        f"💰 موجودی: {new_wallet} {config.CURRENCY_NAME}"
    )
    await host.save_state()


def _extract_numbers(text: str) -> list:
    digit_map = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    return [int(n) for n in re.findall(r"\d+", (text or "").translate(digit_map))]


async def transfer_property_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """انتقال ملک [شناسه] — با ریپلای روی کاربر گیرنده: ملک رو مفت بهش می‌بخشه
    (هدیه). هر دو طرف باید عضو همون گروه باشن. ملکی که الان اجاره‌ست قابل
    انتقال نیست (اول باید اجارش تموم بشه)."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not (message.reply_to_message and message.reply_to_message.from_user):
        await message.reply_text("استفاده: روی پیام گیرنده ریپلای بزن و بنویس «انتقال ملک [شناسه]»")
        return
    numbers = _extract_numbers(message.text)
    if not numbers:
        await message.reply_text("شناسه‌ی ملک رو هم بنویس: «انتقال ملک [شناسه]»")
        return
    prop_id = numbers[0]
    giver = update.effective_user
    receiver = message.reply_to_message.from_user
    if receiver.id == giver.id:
        await message.reply_text("نمی‌تونی به خودت ملک منتقل کنی 😅")
        return

    props = owned(chat.id, giver.id)
    target = next((p for p in props if p["id"] == prop_id), None)
    if not target:
        await message.reply_text("همچین ملکی توی مالکیتت نیست.")
        return
    if target.get("tenant_id"):
        await message.reply_text("❌ این ملک الان اجاره‌ست؛ اول باید اجارش تموم بشه.")
        return
    if target.get("co_owner_id"):
        await message.reply_text("❌ این ملک الان با همسرت مشترکه؛ اول با «لغو اشتراک ملک» لغوش کن.")
        return

    await _collect_one(chat.id, giver.id, target)  # قبل از انتقال، درآمد باقی‌مونده تسویه بشه
    props.remove(target)
    target["id"] = _new_id(chat.id, receiver.id)  # توی فهرست گیرنده، شناسه‌ی مستقل خودش رو می‌گیره
    receiver_name = receiver.first_name or receiver.username or str(receiver.id)
    giver_name = giver.first_name or giver.username or str(giver.id)
    _log_history(target, "PROPERTY", f"انتقال (هدیه) از {giver_name} به {receiver_name}")
    owned(chat.id, receiver.id).append(target)

    economy_core.record_transaction(chat.id, giver.id, "PROPERTY", 0, counterparty_id=receiver.id,
                                     note=f"انتقال {TYPES[target['type']]['name']} به {receiver_name}")
    await message.reply_text(f"✅ {TYPES[target['type']]['name']} به {receiver_name} منتقل شد.")
    await host.save_state()


async def sell_property_to_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فروش ملک به [شناسه] [قیمت] — با ریپلای روی خریدار. برخلاف «فروش ملک»
    (که فقط ۶۰٪ ارزش رو از بازار می‌گیری)، اینجا خودت با خریدار روی قیمت
    توافق می‌کنی؛ پول مستقیم بین دو کاربر جابه‌جا می‌شه (Economy Core)."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not (message.reply_to_message and message.reply_to_message.from_user):
        await message.reply_text("استفاده: روی پیام خریدار ریپلای بزن و بنویس «فروش ملک به [شناسه] [قیمت]»")
        return
    numbers = _extract_numbers(message.text)
    if len(numbers) < 2:
        await message.reply_text("استفاده: «فروش ملک به [شناسه] [قیمت]» (هر دو عدد لازمه)")
        return
    prop_id, price = numbers[0], numbers[1]
    if price <= 0:
        await message.reply_text("قیمت باید مثبت باشه.")
        return

    seller = update.effective_user
    buyer = message.reply_to_message.from_user
    if buyer.id == seller.id:
        await message.reply_text("نمی‌تونی به خودت بفروشی 😅")
        return

    props = owned(chat.id, seller.id)
    target = next((p for p in props if p["id"] == prop_id), None)
    if not target:
        await message.reply_text("همچین ملکی توی مالکیتت نیست.")
        return
    if target.get("tenant_id"):
        await message.reply_text("❌ این ملک الان اجاره‌ست؛ اول باید اجارش تموم بشه.")
        return
    if target.get("co_owner_id"):
        await message.reply_text("❌ این ملک الان با همسرت مشترکه؛ اول با «لغو اشتراک ملک» لغوش کن.")
        return

    try:
        await economy_core.transfer_coins(chat.id, buyer.id, seller.id, price, kind="SALE")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی خریدار کافی نیست (قیمت: {price:,} {config.CURRENCY_NAME}).")
        return

    await _collect_one(chat.id, seller.id, target)
    props.remove(target)
    target["id"] = _new_id(chat.id, buyer.id)
    buyer_name = buyer.first_name or buyer.username or str(buyer.id)
    seller_name = seller.first_name or seller.username or str(seller.id)
    _log_history(target, "SALE", f"فروش P2P از {seller_name} به {buyer_name} به قیمت {price:,}")
    owned(chat.id, buyer.id).append(target)

    await message.reply_text(
        f"✅ {TYPES[target['type']]['name']} به {buyer_name} فروخته شد. "
        f"{seller_name} +{price:,} {config.CURRENCY_NAME} گرفت."
    )
    await host.save_state()


async def rent_property_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اجاره ملک [شناسه] [قیمت] — ملک خودت رو برای اجاره لیست می‌کنی (قیمت
    به‌ازای هر دوره‌ی RENT_PERIOD_HOURS). برای برداشتن از لیست اجاره: قیمت 0
    بذار."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if len(args) < 2:
        await message.reply_text("استفاده: «اجاره ملک [شناسه] [قیمت]» — برای لغو، قیمت رو 0 بذار.")
        return
    try:
        prop_id, price = int(args[0]), int(args[1])
    except ValueError:
        await message.reply_text("شناسه/قیمت نامعتبره.")
        return

    uid = update.effective_user.id
    target = next((p for p in owned(chat.id, uid) if p["id"] == prop_id), None)
    if not target:
        await message.reply_text("همچین ملکی توی مالکیتت نیست.")
        return
    if price <= 0:
        target["rent_price"] = None
        await message.reply_text("✅ این ملک از لیست اجاره برداشته شد.")
    else:
        target["rent_price"] = price
        period = getattr(config, "RENT_PERIOD_HOURS", 24)
        await message.reply_text(
            f"✅ {TYPES[target['type']]['name']} #{prop_id} برای اجاره لیست شد: "
            f"{price:,} {config.CURRENCY_NAME} هر {period} ساعت.\n"
            f"مستأجر با «لیست اجاره» می‌بینتش و با ریپلای روی پیامت می‌نویسه «اجاره کردن {prop_id}»."
        )
    await host.save_state()


async def rentals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لیست اجاره — همه‌ی املاک قابل‌اجاره‌ی گروه (از همه‌ی کاربرا) رو نشون می‌ده."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    period = getattr(config, "RENT_PERIOD_HOURS", 24)
    lines = ["🏠 املاک قابل‌اجاره:", ""]
    found = False
    for owner_id, props in _properties.get(chat.id, {}).items():
        owner_name = host._user_display_names.get(owner_id, str(owner_id))
        for p in props:
            if p.get("rent_price") and not p.get("tenant_id"):
                found = True
                lines.append(
                    f"#{p['id']} {TYPES[p['type']]['name']} — مالک: {owner_name} — "
                    f"{p['rent_price']:,} {config.CURRENCY_NAME}/{period}h"
                )
    if not found:
        await message.reply_text("فعلاً هیچ ملکی برای اجاره لیست نشده.")
        return
    lines.append("")
    lines.append("اجاره کردن: روی پیام مالک ریپلای بزن و بنویس «اجاره کردن [شناسه]»")
    await message.reply_text("\n".join(lines))


async def rent_take_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اجاره کردن [شناسه] — با ریپلای روی مالک ملک. اجاره‌بها مستقیم به
    مالک پرداخت می‌شه و تا RENT_PERIOD_HOURS ساعت بعد، این ملک "تصرف"ت می‌مونه."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not (message.reply_to_message and message.reply_to_message.from_user):
        await message.reply_text("استفاده: روی پیام مالک ملک ریپلای بزن و بنویس «اجاره کردن [شناسه]»")
        return
    numbers = _extract_numbers(message.text)
    if not numbers:
        await message.reply_text("شناسه‌ی ملک رو هم بنویس: «اجاره کردن [شناسه]»")
        return
    prop_id = numbers[0]
    tenant = update.effective_user
    owner = message.reply_to_message.from_user
    if owner.id == tenant.id:
        await message.reply_text("نمی‌تونی از خودت اجاره کنی 😅")
        return

    target = next((p for p in owned(chat.id, owner.id) if p["id"] == prop_id), None)
    if not target or not target.get("rent_price"):
        await message.reply_text("همچین ملک قابل‌اجاره‌ای از این مالک پیدا نشد.")
        return
    if target.get("tenant_id") and target.get("rent_until", 0) > time.time():
        await message.reply_text("❌ این ملک همین الان اجاره‌ی یکی دیگه‌ست.")
        return

    price = target["rent_price"]
    try:
        await economy_core.transfer_coins(chat.id, tenant.id, owner.id, price, kind="OTHER")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. اجاره‌بها: {price:,} {config.CURRENCY_NAME}")
        return

    period_hours = getattr(config, "RENT_PERIOD_HOURS", 24)
    target["tenant_id"] = tenant.id
    target["rent_until"] = time.time() + period_hours * 3600
    tenant_name = tenant.first_name or tenant.username or str(tenant.id)
    owner_name = owner.first_name or owner.username or str(owner.id)
    _log_history(target, "PROPERTY", f"اجاره‌شده به {tenant_name} برای {period_hours} ساعت")
    await message.reply_text(
        f"✅ {TYPES[target['type']]['name']} #{prop_id} رو {period_hours} ساعت از {owner_name} اجاره کردی."
    )
    await host.save_state()


async def property_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تاریخچه ملک [شناسه] — رویدادهای اخیر یکی از ملک‌های خودت."""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «تاریخچه ملک [شناسه]»")
        return
    try:
        prop_id = int(args[0])
    except ValueError:
        await message.reply_text("شناسه‌ی نامعتبر.")
        return
    uid = update.effective_user.id
    target = next((p for p in owned(chat.id, uid) if p["id"] == prop_id), None)
    if not target:
        await message.reply_text("همچین ملکی توی مالکیتت نیست.")
        return
    hist = target.get("history", [])
    if not hist:
        await message.reply_text("هنوز هیچ رویدادی برای این ملک ثبت نشده.")
        return
    lines = [f"📜 تاریخچه‌ی {TYPES[target['type']]['name']} #{prop_id}:", ""]
    for entry in reversed(hist[-15:]):
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry["ts"]))
        lines.append(f"• {ts} — {entry['event']}: {entry['note']}")
    await message.reply_text("\n".join(lines))


async def share_property_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اشتراک ملک [شناسه] — ارتقای فاز ۱۹ (Social/Family): ملکت رو با همسرت
    مشترک می‌کنی؛ اون هم می‌تونه ازش درآمد جمع کنه (فروش/ارتقا/انتقال فقط
    دست خودِ مالک اصلی می‌مونه، تا مالکیت مبهم نشه)."""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «اشتراک ملک [شناسه]»")
        return
    try:
        prop_id = int(args[0])
    except ValueError:
        await message.reply_text("شناسه‌ی نامعتبر.")
        return
    target = next((p for p in owned(chat.id, uid) if p["id"] == prop_id), None)
    if not target:
        await message.reply_text("همچین ملکی توی مالکیتت نیست.")
        return

    import economy_marriage
    if not economy_marriage.is_married(chat.id, uid):
        await message.reply_text("❌ برای اشتراک‌گذاری ملک باید ازدواج کرده باشی.")
        return
    spouse_id = economy_marriage.spouse_of(chat.id, uid)

    target["co_owner_id"] = spouse_id
    _log_history(target, "PROPERTY", f"اشتراک‌گذاری با همسر (uid={spouse_id})")
    await message.reply_text(f"✅ {TYPES[target['type']]['name']} #{prop_id} الان با همسرت مشترکه؛ اونم می‌تونه ازش درآمد جمع کنه.")
    await host.save_state()


async def unshare_property_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لغو اشتراک ملک [شناسه]"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «لغو اشتراک ملک [شناسه]»")
        return
    try:
        prop_id = int(args[0])
    except ValueError:
        await message.reply_text("شناسه‌ی نامعتبر.")
        return
    target = next((p for p in owned(chat.id, uid) if p["id"] == prop_id), None)
    if not target or not target.get("co_owner_id"):
        await message.reply_text("همچین ملک مشترکی پیدا نشد.")
        return
    target["co_owner_id"] = None
    _log_history(target, "PROPERTY", "لغو اشتراک‌گذاری")
    await message.reply_text(f"✅ {TYPES[target['type']]['name']} #{prop_id} دیگه مشترک نیست.")
    await host.save_state()


async def my_property_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ملک من [شناسه] — بدون شناسه: لیست + جمع‌آوری درآمد. با شناسه: ارتقای اون ملک."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_property"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    args = context.args or []

    if args:
        try:
            prop_id = int(args[0])
        except ValueError:
            await message.reply_text("شناسه‌ی نامعتبر.")
            return
        target = next((p for p in owned(chat.id, uid) if p["id"] == prop_id), None)
        if not target:
            await message.reply_text("همچین ملکی توی مالکیتت نیست.")
            return
        info = TYPES[target["type"]]
        cost = round(info["base_price"] * (1 + target["level"] * getattr(config, "PROPERTY_UPGRADE_COST_MULTIPLIER", 0.9)))
        try:
            await economy_core.remove_coins(chat.id, uid, cost, kind="property_upgrade", note=f"ارتقای {info['name']} #{prop_id}")
        except economy_core.InsufficientFundsError:
            await message.reply_text(f"❌ موجودی کافی نیست. هزینه‌ی ارتقا: {cost:,} {config.CURRENCY_NAME}")
            return
        target["level"] += 1
        _log_history(target, "PROPERTY", f"ارتقا به Level {target['level']} به هزینه‌ی {cost:,}")
        await message.reply_text(f"⭐ {info['name']} #{prop_id} رفت Level {target['level']}!")
        await host.save_state()
        return

    collected = await _collect_all(chat.id, uid)
    collected_shared = await _collect_shared(chat.id, uid)  # فاز ۱۹: املاک مشترک همسر
    props = owned(chat.id, uid)
    shared = shared_with_me(chat.id, uid)
    if not props and not shared:
        await message.reply_text("هنوز هیچ ملکی نداری. با «املاک» لیست انواع رو ببین.")
        return

    lines = ["🏠 املاک تو:", ""]
    total_value = 0
    for p in props:
        info = TYPES[p["type"]]
        value = current_value(p)
        total_value += value
        shared_badge = " 👫" if p.get("co_owner_id") else ""
        lines.append(f"#{p['id']} {info['name']} — Level {p['level']} — ارزش: {value:,} {config.CURRENCY_NAME}{shared_badge}")
    if shared:
        lines.append("")
        lines.append("👫 املاک مشترکی که همسرت باهات به اشتراک گذاشته:")
        for owner_id, p in shared:
            info = TYPES[p["type"]]
            owner_name = host._user_display_names.get(owner_id, str(owner_id))
            lines.append(f"  #{p['id']} {info['name']} (مالک: {owner_name}) — Level {p['level']}")
    lines.append("")
    lines.append(f"💎 ارزش کل املاک: {total_value:,} {config.CURRENCY_NAME}")
    total_collected = collected + collected_shared
    if total_collected > 0:
        lines.append(f"🎁 درآمد جمع‌شده (پس از مالیات): +{total_collected} {config.CURRENCY_NAME}")
    lines.append("")
    lines.append("ارتقا: «ملک من [شناسه]» — فروش: «فروش ملک [شناسه]» — اشتراک با همسر: «اشتراک ملک [شناسه]»")
    await message.reply_text("\n".join(lines))
    await host.save_state()


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "properties": {
            str(c): {str(u): props for u, props in v.items()}
            for c, v in _properties.items()
        },
        "next_id": {str(c): dict(v) for c, v in _next_id.items()},
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
            logger.warning(f"ذخیره‌ی state موتور Property ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور Property ناموفق بود: {e}")
        return
    for cid, v in data.get("properties", {}).items():
        for uid, props in v.items():
            _properties[int(cid)][int(uid)] = props
    for cid, v in data.get("next_id", {}).items():
        _next_id[int(cid)] = {int(u): n for u, n in v.items()}
    logger.info("وضعیت موتور Property از فایل بارگذاری شد.")
