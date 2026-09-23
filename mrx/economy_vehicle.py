# -*- coding: utf-8 -*-
"""
DIGNANTI ECONOMY VEHICLE — فاز ۸ (ماژول جدید؛ از صفر ساخته شده)
====================================================================
معماری دقیقاً هم‌راستا با economy_property.py (که همین الگو رو برای املاک
جا انداخته): هر خودرو یه رکورد مستقل با id خودشه، خرید/فروش/تعمیر/سوخت‌گیری/
بیمه از Economy Core عبور می‌کنن، و قیمت خرید به منطقه‌ی محل زندگی کاربر
(economy_district) وابسته‌ست.

هر خودرو این فیلدها رو داره:
  id, type, condition (0-100، سلامت/Durability)، fuel (0-100، درصد باک)،
  mileage (کیلومتر طی‌شده)، insured_until (timestamp یا None)،
  last_tax_ts، accident_count، history[]

فاز ۹ (Tuning) روی همین رکوردها یه فیلد "upgrades" اضافه می‌کنه؛ عمداً
اینجا Placeholder نذاشتم (طبق قانون کاربر: هیچ تابع بی‌استفاده/Placeholder).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

import config
import economy_core
import economy_missions

logger = logging.getLogger("economy_vehicle")

STATE_FILE = getattr(config, "ECONOMY_VEHICLE_STATE_FILE", "economy_vehicle_state.json")
TYPES: dict = getattr(config, "VEHICLE_TYPES", {})
TUNING: dict = getattr(config, "VEHICLE_TUNING", {})
MAX_HISTORY_PER_VEHICLE = 20

_save_lock = asyncio.Lock()

# chat_id -> uid -> list[{...}]
_vehicles = defaultdict(lambda: defaultdict(list))
_next_id = defaultdict(dict)
# چون خودرو مثل ملک درآمد ندازه، به‌جای last_collect_ts فقط last_drive_ts برای Cooldown لازمه
_last_drive_ts = defaultdict(lambda: defaultdict(int))


def _host():
    import bot as host
    return host


def _new_id(chat_id: int, uid: int) -> int:
    n = _next_id[chat_id].get(uid, 0) + 1
    _next_id[chat_id][uid] = n
    return n


def _extract_numbers(text: str) -> list:
    digit_map = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    return [int(n) for n in re.findall(r"\d+", (text or "").translate(digit_map))]


def _log_history(v: dict, event: str, note: str = "") -> None:
    hist = v.setdefault("history", [])
    hist.append({"ts": time.time(), "event": event, "note": note})
    if len(hist) > MAX_HISTORY_PER_VEHICLE:
        del hist[: len(hist) - MAX_HISTORY_PER_VEHICLE]


def owned(chat_id: int, uid: int) -> list[dict]:
    return _vehicles[chat_id][uid]


def _is_insured(v: dict) -> bool:
    return bool(v.get("insured_until") and v["insured_until"] > time.time())


def current_price(chat_id: int, uid: int, vtype: str) -> int:
    """قیمت خرید نو، با احتساب ضریب منطقه (فاز ۴) و تورم (ارتقای فاز ۱۵)."""
    info = TYPES[vtype]
    base = float(info["base_price"])
    try:
        import economy_district
        base *= economy_district.multiplier_for(chat_id, uid, "vehicle_price")
    except Exception:
        pass
    try:
        import economy_events
        base *= economy_events.price_inflation_multiplier(chat_id)
    except Exception:
        pass
    return round(base)


def current_value(v: dict) -> int:
    """ارزش فعلی = قیمت پایه × افت‌قیمت بر اساس Condition و کارکرد (Mileage).
    هرچی Condition پایین‌تر و Mileage بیشتر، افت بیشتر (طبق فاز ۸: افت قیمت).
    ارتقاها (فاز ۹) هم کمی به ارزش اضافه می‌کنن (ماشین تیون‌شده گرون‌تره)."""
    info = TYPES[v["type"]]
    condition_frac = v["condition"] / 100.0
    mileage_decay = max(0.5, 1 - (v["mileage"] / 10000) * 0.05)
    base_value = info["base_price"] * condition_frac * mileage_decay
    tuning_value = 0
    for cat, lvl in v.get("upgrades", {}).items():
        tuning_value += sum(_upgrade_cost(v["type"], cat, n) for n in range(1, lvl + 1))
    tuning_value *= 0.5  # نصف هزینه‌ی ارتقا به ارزش فروش اضافه می‌شه
    return round(base_value + tuning_value)


# ---------------------------------------------------------------------------
# 🔧 Tuning — فاز ۹
# ---------------------------------------------------------------------------

def _upgrade_cost(vtype: str, category: str, next_level: int) -> int:
    info = TYPES[vtype]
    cat = TUNING[category]
    return round(info["base_price"] * cat["cost_fraction"] * next_level)


def upgrade_level(v: dict, category: str) -> int:
    return v.get("upgrades", {}).get(category, 0)


def effective_top_speed(v: dict) -> int:
    info = TYPES[v["type"]]
    ups = v.get("upgrades", {})
    bonus = 0
    for cat in ("engine", "turbo", "exhaust"):
        lvl = ups.get(cat, 0)
        bonus += lvl * TUNING.get(cat, {}).get("speed_bonus_per_level", 0)
    return info["top_speed"] + bonus


def effective_fuel_consumption_per_100km(v: dict) -> float:
    info = TYPES[v["type"]]
    ups = v.get("upgrades", {})
    base = info["fuel_consumption_per_100km"]
    turbo_lvl = ups.get("turbo", 0)
    gearbox_lvl = ups.get("gearbox", 0)
    mult = 1 + turbo_lvl * TUNING.get("turbo", {}).get("fuel_increase_per_level", 0)
    mult -= gearbox_lvl * TUNING.get("gearbox", {}).get("fuel_reduction_per_level", 0)
    return max(base * 0.3, base * mult)


def effective_wear_per_100km(v: dict) -> float:
    base = getattr(config, "VEHICLE_WEAR_PER_100KM", 3)
    wheels_lvl = v.get("upgrades", {}).get("wheels", 0)
    reduction = wheels_lvl * TUNING.get("wheels", {}).get("wear_reduction_per_level", 0)
    return max(base * 0.3, base * (1 - reduction))


def effective_accident_chance_multiplier(v: dict) -> float:
    ups = v.get("upgrades", {})
    reduction = 0.0
    for cat in ("brakes", "tires", "gps"):
        lvl = ups.get(cat, 0)
        reduction += lvl * TUNING.get(cat, {}).get("accident_reduction_per_level", 0)
    return max(0.2, 1 - reduction)  # هیچ‌وقت خطر صفر نمی‌شه، حداکثر ۸۰٪ کاهش


def effective_damage_reduction(v: dict) -> float:
    lvl = v.get("upgrades", {}).get("armor", 0)
    return min(0.6, lvl * TUNING.get("armor", {}).get("damage_reduction_per_level", 0))


def effective_fine_reduction(v: dict) -> float:
    lvl = v.get("upgrades", {}).get("security", 0)
    return min(0.6, lvl * TUNING.get("security", {}).get("fine_reduction_per_level", 0))


def audio_happiness_bonus(v: dict) -> int:
    lvl = v.get("upgrades", {}).get("audio", 0)
    return lvl * TUNING.get("audio", {}).get("happiness_bonus_per_level", 0)


async def tune_vehicle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تیونینگ خودرو [شناسه] [دسته] — یک Level به اون دسته‌ی ارتقا اضافه
    می‌کنه (Engine/Turbo/Brakes/Tires/Gearbox/Exhaust/Paint/Wheels/Audio/
    Armor/GPS/Security). بدون دسته: لیست دسته‌ها + سطح فعلی + هزینه‌ی Level بعدی."""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «تیونینگ خودرو [شناسه]» یا «تیونینگ خودرو [شناسه] [دسته]» برای ارتقا.")
        return
    uid = update.effective_user.id
    target = _find_owned(chat.id, uid, args)
    if not target:
        await message.reply_text("همچین خودرویی توی مالکیتت نیست.")
        return

    if len(args) == 1:
        lines = [f"🔧 تیونینگ {TYPES[target['type']]['name']} #{target['id']}:", ""]
        for key, cat in TUNING.items():
            lvl = upgrade_level(target, key)
            if lvl >= cat["max_level"]:
                lines.append(f"{cat['name']}: Level {lvl}/{cat['max_level']} (حداکثر)")
            else:
                cost = _upgrade_cost(target["type"], key, lvl + 1)
                lines.append(f"{cat['name']}: Level {lvl}/{cat['max_level']} — ارتقا به {lvl+1}: {cost:,} {config.CURRENCY_NAME}")
        lines.append("")
        lines.append("ارتقا: «تیونینگ خودرو [شناسه] [دسته]» — مثلاً «تیونینگ خودرو 1 engine»")
        await message.reply_text("\n".join(lines))
        return

    category = args[1].strip().lower()
    matched = None
    for key, cat in TUNING.items():
        if category == key or category == cat["name"].lower():
            matched = key
            break
    if not matched:
        await message.reply_text("همچین دسته‌ی ارتقایی نیست. با «تیونینگ خودرو [شناسه]» لیست رو ببین.")
        return

    cat = TUNING[matched]
    lvl = upgrade_level(target, matched)
    if lvl >= cat["max_level"]:
        await message.reply_text(f"❌ {cat['name']} همین الان هم حداکثر Level ({cat['max_level']}) رو داره.")
        return
    cost = _upgrade_cost(target["type"], matched, lvl + 1)
    try:
        await economy_core.remove_coins(chat.id, uid, cost, kind="VEHICLE", note=f"تیونینگ {cat['name']}")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. هزینه: {cost:,} {config.CURRENCY_NAME}")
        return

    target.setdefault("upgrades", {})[matched] = lvl + 1
    _log_history(target, "VEHICLE", f"تیونینگ {cat['name']} به Level {lvl+1} به هزینه‌ی {cost:,}")
    await message.reply_text(f"✅ {cat['name']} {TYPES[target['type']]['name']} #{target['id']} رفت Level {lvl+1}.")
    await host.save_state()


def repair_cost(v: dict) -> int:
    info = TYPES[v["type"]]
    missing = 100 - v["condition"]
    per_point = getattr(config, "VEHICLE_REPAIR_COST_PER_POINT_FRACTION", 0.01)
    return round(info["base_price"] * per_point * missing)


def refuel_cost(chat_id: int, v: dict) -> int:
    info = TYPES[v["type"]]
    missing = 100 - v["fuel"]
    per_percent = getattr(config, "VEHICLE_FUEL_COST_PER_PERCENT_FRACTION", 0.003)
    cost = info["base_price"] * per_percent * missing
    try:
        import economy_events
        cost *= economy_events.fuel_cost_multiplier(chat_id)  # ارتقای فاز ۱۵: بحران سوخت
    except Exception:
        pass
    return round(cost)


def insurance_cost(v: dict) -> int:
    info = TYPES[v["type"]]
    return round(info["base_price"] * getattr(config, "VEHICLE_INSURANCE_COST_FRACTION", 0.03))


def tax_due(v: dict) -> int:
    """مالیات تجمیعی از آخرین پرداخت (لازی، مثل درآمد Property ولی برعکس:
    بدهیه نه درآمد). هر VEHICLE_TAX_PERIOD_HOURS یه دوره حساب می‌شه."""
    info = TYPES[v["type"]]
    period_h = getattr(config, "VEHICLE_TAX_PERIOD_HOURS", 168)
    elapsed_h = (time.time() - v["last_tax_ts"]) / 3600.0
    periods = int(elapsed_h // period_h)
    if periods <= 0:
        return 0
    return round(current_value(v) * info["tax_rate"] * periods)


def is_tax_overdue(v: dict) -> bool:
    """اگه بدهی مالیاتی از مهلت (Grace Period) رد شده باشه، رانندگی قفل می‌شه
    (به‌جای اینکه بدهی بی‌نهایت جمع بشه یا موجودی منفی بشه)."""
    if tax_due(v) <= 0:
        return False
    period_h = getattr(config, "VEHICLE_TAX_PERIOD_HOURS", 168)
    grace_h = getattr(config, "VEHICLE_TAX_GRACE_HOURS", 72)
    elapsed_h = (time.time() - v["last_tax_ts"]) / 3600.0
    return elapsed_h > (period_h + grace_h)


# ---------------------------------------------------------------------------
# 📊 Net Worth Source
# ---------------------------------------------------------------------------

def get_vehicle_value(chat_id: int, uid: int) -> int:
    return sum(current_value(v) for v in owned(chat_id, uid))


economy_core.register_net_worth_source(get_vehicle_value)


# ---------------------------------------------------------------------------
# 📋 نمایش / خرید
# ---------------------------------------------------------------------------

async def vehicle_types_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خودرو / /vehicle — لیست انواع خودروی قابل‌خرید."""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_vehicle"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    lines = ["🚗 انواع خودروی قابل‌خرید:", ""]
    for key, info in TYPES.items():
        price = current_price(chat.id, uid, key)
        lines.append(
            f"{info['name']} ({info['brand']}, {info['class']}) — قیمت: {price:,} {config.CURRENCY_NAME} — "
            f"سرعت: {info['top_speed']}km/h — شتاب: {info['accel']}"
        )
    lines.append("")
    lines.append("خرید: «خرید خودرو [نوع]» — دیدن خودروهای خودت: «خودرو من»")
    await message.reply_text("\n".join(lines))


def _find_type(name: str) -> str | None:
    name = name.strip().lower()
    if name in TYPES:
        return name
    for key, info in TYPES.items():
        if name in info["name"].lower() or name in info["class"].lower():
            return key
    return None


async def buy_vehicle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خرید خودرو [نوع] / /buyvehicle <type>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_vehicle"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «خرید خودرو [نوع]». با «خودرو» لیست انواع رو ببین.")
        return
    uid = update.effective_user.id
    key = _find_type(" ".join(args))
    if not key:
        await message.reply_text("همچین نوع خودرویی نیست. با «خودرو» لیست رو ببین.")
        return

    price = current_price(chat.id, uid, key)
    try:
        await economy_core.remove_coins(chat.id, uid, price, kind="VEHICLE", note=f"خرید {TYPES[key]['name']}")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. قیمت: {price:,} {config.CURRENCY_NAME}")
        return

    v = {
        "id": _new_id(chat.id, uid), "type": key, "condition": 100, "fuel": 100,
        "mileage": 0, "insured_until": None, "purchased_ts": time.time(),
        "last_tax_ts": time.time(), "accident_count": 0, "history": [], "upgrades": {},
    }
    _log_history(v, "VEHICLE", f"خرید نو به قیمت {price:,}")
    owned(chat.id, uid).append(v)

    lines = [f"✅ {TYPES[key]['name']} #{v['id']} خریداری شد!"]
    try:
        lines += await economy_missions.record_progress(chat.id, uid, "vehicle_buy", 1)
    except Exception as e:
        logger.warning(f"ثبت پیشرفت ماموریت vehicle_buy ناموفق بود: {e}")
    try:
        import economy_achievements
        lines.extend(economy_achievements.check_all(chat.id, uid))
    except Exception as e:
        logger.warning(f"چک دستاوردهای اقتصادی ناموفق بود: {e}")
    await message.reply_text("\n".join(lines))
    await host.save_state()


async def sell_vehicle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فروش خودرو [شناسه] / /sellvehicle <id> — به بازار (۶۰٪ ارزش فعلی)."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return

    uid = update.effective_user.id
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «فروش خودرو [شناسه]». شناسه‌ها رو با «خودرو من» ببین.")
        return
    try:
        v_id = int(args[0])
    except ValueError:
        await message.reply_text("شناسه‌ی نامعتبر.")
        return

    vs = owned(chat.id, uid)
    target = next((v for v in vs if v["id"] == v_id), None)
    if not target:
        await message.reply_text("همچین خودرویی توی مالکیتت نیست.")
        return

    resale_fraction = getattr(config, "VEHICLE_RESALE_BASE_FRACTION", 0.6)
    payout = round(current_value(target) * resale_fraction)
    vs.remove(target)
    new_wallet = await economy_core.add_coins(chat.id, uid, payout, kind="SALE",
                                               note=f"فروش {TYPES[target['type']]['name']} #{v_id}")
    await message.reply_text(
        f"✅ {TYPES[target['type']]['name']} #{v_id} فروخته شد: +{payout} {config.CURRENCY_NAME} "
        f"({resale_fraction*100:.0f}٪ ارزش فعلی، با احتساب افت قیمت).\n"
        f"💰 موجودی: {new_wallet} {config.CURRENCY_NAME}"
    )
    await host.save_state()


async def transfer_vehicle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """انتقال خودرو [شناسه] — هدیه‌ی مستقیم با ریپلای رو گیرنده."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not (message.reply_to_message and message.reply_to_message.from_user):
        await message.reply_text("استفاده: روی پیام گیرنده ریپلای بزن و بنویس «انتقال خودرو [شناسه]»")
        return
    numbers = _extract_numbers(message.text)
    if not numbers:
        await message.reply_text("شناسه‌ی خودرو رو هم بنویس: «انتقال خودرو [شناسه]»")
        return
    v_id = numbers[0]
    giver = update.effective_user
    receiver = message.reply_to_message.from_user
    if receiver.id == giver.id:
        await message.reply_text("نمی‌تونی به خودت خودرو منتقل کنی 😅")
        return

    vs = owned(chat.id, giver.id)
    target = next((v for v in vs if v["id"] == v_id), None)
    if not target:
        await message.reply_text("همچین خودرویی توی مالکیتت نیست.")
        return

    vs.remove(target)
    target["id"] = _new_id(chat.id, receiver.id)
    receiver_name = receiver.first_name or receiver.username or str(receiver.id)
    giver_name = giver.first_name or giver.username or str(giver.id)
    _log_history(target, "VEHICLE", f"انتقال (هدیه) از {giver_name} به {receiver_name}")
    owned(chat.id, receiver.id).append(target)
    economy_core.record_transaction(chat.id, giver.id, "VEHICLE", 0, counterparty_id=receiver.id,
                                     note=f"انتقال {TYPES[target['type']]['name']} به {receiver_name}")
    await message.reply_text(f"✅ {TYPES[target['type']]['name']} به {receiver_name} منتقل شد.")
    await host.save_state()


async def sell_vehicle_to_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فروش خودرو به [شناسه] [قیمت] — بازار دست دوم P2P، با ریپلای رو خریدار."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not (message.reply_to_message and message.reply_to_message.from_user):
        await message.reply_text("استفاده: روی پیام خریدار ریپلای بزن و بنویس «فروش خودرو به [شناسه] [قیمت]»")
        return
    numbers = _extract_numbers(message.text)
    if len(numbers) < 2:
        await message.reply_text("استفاده: «فروش خودرو به [شناسه] [قیمت]» (هر دو عدد لازمه)")
        return
    v_id, price = numbers[0], numbers[1]
    if price <= 0:
        await message.reply_text("قیمت باید مثبت باشه.")
        return

    seller = update.effective_user
    buyer = message.reply_to_message.from_user
    if buyer.id == seller.id:
        await message.reply_text("نمی‌تونی به خودت بفروشی 😅")
        return

    vs = owned(chat.id, seller.id)
    target = next((v for v in vs if v["id"] == v_id), None)
    if not target:
        await message.reply_text("همچین خودرویی توی مالکیتت نیست.")
        return

    try:
        await economy_core.transfer_coins(chat.id, buyer.id, seller.id, price, kind="SALE")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی خریدار کافی نیست (قیمت: {price:,} {config.CURRENCY_NAME}).")
        return

    vs.remove(target)
    target["id"] = _new_id(chat.id, buyer.id)
    buyer_name = buyer.first_name or buyer.username or str(buyer.id)
    seller_name = seller.first_name or seller.username or str(seller.id)
    _log_history(target, "SALE", f"فروش دست‌دوم از {seller_name} به {buyer_name} به قیمت {price:,}")
    owned(chat.id, buyer.id).append(target)
    await message.reply_text(
        f"✅ {TYPES[target['type']]['name']} به {buyer_name} فروخته شد. "
        f"{seller_name} +{price:,} {config.CURRENCY_NAME} گرفت."
    )
    await host.save_state()


# ---------------------------------------------------------------------------
# 🔧 تعمیر / ⛽ سوخت / 🛡 بیمه / 💰 مالیات
# ---------------------------------------------------------------------------

def _find_owned(chat_id: int, uid: int, args: list) -> dict | None:
    if not args:
        return None
    try:
        v_id = int(args[0])
    except ValueError:
        return None
    return next((v for v in owned(chat_id, uid) if v["id"] == v_id), None)


async def repair_vehicle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تعمیر خودرو [شناسه] / /repairvehicle <id> — Condition رو کامل می‌کنه."""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    target = _find_owned(chat.id, uid, context.args or [])
    if not target:
        await message.reply_text("استفاده: «تعمیر خودرو [شناسه]»")
        return
    if target["condition"] >= 100:
        await message.reply_text("این خودرو همین الان هم سالمه (Condition 100).")
        return
    cost = repair_cost(target)
    try:
        await economy_core.remove_coins(chat.id, uid, cost, kind="OTHER", note=f"تعمیر {TYPES[target['type']]['name']}")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. هزینه‌ی تعمیر: {cost:,} {config.CURRENCY_NAME}")
        return
    target["condition"] = 100
    _log_history(target, "VEHICLE", f"تعمیر کامل به هزینه‌ی {cost:,}")
    await message.reply_text(f"🔧 {TYPES[target['type']]['name']} #{target['id']} تعمیر شد (Condition: 100).")
    await host.save_state()


async def refuel_vehicle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """سوخت‌گیری [شناسه] / /refuel <id>"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    target = _find_owned(chat.id, uid, context.args or [])
    if not target:
        await message.reply_text("استفاده: «سوخت‌گیری [شناسه]»")
        return
    if TYPES[target["type"]]["fuel_consumption_per_100km"] == 0:
        await message.reply_text("این وسیله سوخت مصرف نمی‌کنه (مثل دوچرخه).")
        return
    if target["fuel"] >= 100:
        await message.reply_text("باک همین الان هم پره.")
        return
    cost = refuel_cost(chat.id, target)
    try:
        await economy_core.remove_coins(chat.id, uid, cost, kind="OTHER", note=f"سوخت‌گیری {TYPES[target['type']]['name']}")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. هزینه: {cost:,} {config.CURRENCY_NAME}")
        return
    target["fuel"] = 100
    await message.reply_text(f"⛽ باک {TYPES[target['type']]['name']} #{target['id']} پر شد.")
    await host.save_state()


async def insure_vehicle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بیمه خودرو [شناسه] / /insurevehicle <id>"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    target = _find_owned(chat.id, uid, context.args or [])
    if not target:
        await message.reply_text("استفاده: «بیمه خودرو [شناسه]»")
        return
    cost = insurance_cost(target)
    try:
        await economy_core.remove_coins(chat.id, uid, cost, kind="INSURANCE", note=f"بیمه‌ی {TYPES[target['type']]['name']}")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. هزینه‌ی بیمه: {cost:,} {config.CURRENCY_NAME}")
        return
    period_h = getattr(config, "VEHICLE_INSURANCE_PERIOD_HOURS", 168)
    now = time.time()
    base = target["insured_until"] if _is_insured(target) else now
    target["insured_until"] = base + period_h * 3600
    _log_history(target, "INSURANCE", f"بیمه‌ی {period_h} ساعته به هزینه‌ی {cost:,}")
    await message.reply_text(
        f"🛡 {TYPES[target['type']]['name']} #{target['id']} بیمه شد تا "
        f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(target['insured_until']))}."
    )
    await host.save_state()


async def pay_vehicle_tax_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مالیات خودرو [شناسه] / /vehicletax <id>"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    target = _find_owned(chat.id, uid, context.args or [])
    if not target:
        await message.reply_text("استفاده: «مالیات خودرو [شناسه]»")
        return
    due = tax_due(target)
    if due <= 0:
        await message.reply_text("بدهی مالیاتی‌ای نداری.")
        return
    try:
        await economy_core.remove_coins(chat.id, uid, due, kind="TAX", note=f"مالیات {TYPES[target['type']]['name']}")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. بدهی مالیاتی: {due:,} {config.CURRENCY_NAME}")
        return
    target["last_tax_ts"] = time.time()
    _log_history(target, "TAX", f"پرداخت مالیات {due:,}")
    await message.reply_text(f"💰 مالیات {TYPES[target['type']]['name']} #{target['id']} پرداخت شد ({due:,} {config.CURRENCY_NAME}).")
    await host.save_state()


# ---------------------------------------------------------------------------
# 🏁 رانندگی (Mileage + Fuel + Accident)
# ---------------------------------------------------------------------------

async def drive_vehicle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رانندگی [شناسه] / /drive <id> — یه سفر شبیه‌سازی‌شده: مسافت طی می‌شه،
    سوخت/Condition کم می‌شه، و شانسی برای تصادف هست (کمتر اگه بیمه داشته باشی
    یا توی منطقه‌ی امن‌تر زندگی کنی)."""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    target = _find_owned(chat.id, uid, context.args or [])
    if not target:
        await message.reply_text("استفاده: «رانندگی [شناسه]»")
        return

    cooldown_min = getattr(config, "VEHICLE_DRIVE_COOLDOWN_MINUTES", 15)
    last = _last_drive_ts[chat.id].get(target["id"], 0)
    remaining = cooldown_min * 60 - (time.time() - last)
    if remaining > 0:
        await message.reply_text(f"⏳ باید {round(remaining/60)} دقیقه‌ی دیگه صبر کنی.")
        return

    if is_tax_overdue(target):
        await message.reply_text("🚫 بدهی مالیاتیت خیلی عقب افتاده؛ اول با «مالیات خودرو [شناسه]» تسویه کن.")
        return

    info = TYPES[target["type"]]
    distance = getattr(config, "VEHICLE_DRIVE_DISTANCE_KM", 80)
    fuel_rate = effective_fuel_consumption_per_100km(target)  # فاز ۹: گیربکس/توربو رو حساب کن
    fuel_needed = (fuel_rate / 100.0) * distance
    if fuel_needed > target["fuel"]:
        await message.reply_text("⛽ سوخت کافی نداری. اول سوخت‌گیری کن.")
        return

    target["fuel"] = max(0, target["fuel"] - fuel_needed)
    wear = effective_wear_per_100km(target) * (distance / 100.0)  # فاز ۹: رینگ رو حساب کن
    target["condition"] = max(0, target["condition"] - wear)
    target["mileage"] += distance
    _last_drive_ts[chat.id][target["id"]] = time.time()

    lines = [f"🏁 {info['name']} #{target['id']} {distance}km رانندگی شد. (سوخت: {round(target['fuel'])}٪, Condition: {round(target['condition'])})"]

    happiness_bonus = audio_happiness_bonus(target)  # فاز ۹: سیستم صوتی
    if happiness_bonus:
        try:
            import profile_engine
            profile_engine.adjust_stat(chat.id, uid, "happiness", happiness_bonus)
        except Exception as e:
            logger.warning(f"آپدیت Happiness بعد از رانندگی ناموفق بود (نادیده گرفته شد): {e}")

    # شانس تصادف: پایه × ضریب جرم منطقه × ضریب Tuning (ترمز/لاستیک/GPS)، نصف اگه بیمه داشته باشی
    base_chance = getattr(config, "VEHICLE_BASE_ACCIDENT_CHANCE", 0.08)
    crime_mult = 1.0
    try:
        import economy_district
        crime_mult = economy_district.multiplier_for(chat.id, uid, "crime")
    except Exception:
        pass
    insured = _is_insured(target)
    tuning_mult = effective_accident_chance_multiplier(target)
    chance = base_chance * crime_mult * tuning_mult * (0.5 if insured else 1.0)
    if random.random() < chance:
        dmg = random.randint(
            getattr(config, "VEHICLE_ACCIDENT_DAMAGE_MIN", 10),
            getattr(config, "VEHICLE_ACCIDENT_DAMAGE_MAX", 30),
        )
        dmg = round(dmg * (1 - effective_damage_reduction(target)))  # فاز ۹: زره
        target["condition"] = max(0, target["condition"] - dmg)
        target["accident_count"] = target.get("accident_count", 0) + 1
        if insured:
            lines.append(f"💥 تصادف کردی! {dmg} واحد آسیب دیدی، ولی چون بیمه داشتی جریمه نشدی.")
            _log_history(target, "OTHER", f"تصادف (بیمه‌شده) — {dmg} آسیب")
        else:
            fine = random.randint(
                getattr(config, "VEHICLE_ACCIDENT_FINE_MIN", 300),
                getattr(config, "VEHICLE_ACCIDENT_FINE_MAX", 1500),
            )
            fine = round(fine * (1 - effective_fine_reduction(target)))  # فاز ۹: سیستم امنیتی
            try:
                await economy_core.remove_coins(chat.id, uid, fine, kind="FINE", note="جریمه‌ی تصادف")
                lines.append(f"💥 تصادف کردی! {dmg} واحد آسیب + {fine:,} {config.CURRENCY_NAME} جریمه (بیمه نداشتی).")
            except economy_core.InsufficientFundsError:
                lines.append(f"💥 تصادف کردی! {dmg} واحد آسیب. موجودی برای جریمه‌ی {fine:,} کافی نبود (نادیده گرفته شد).")
            _log_history(target, "FINE", f"تصادف — {dmg} آسیب + {fine:,} جریمه")

    await message.reply_text("\n".join(lines))
    await host.save_state()


async def my_vehicle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خودرو من — لیست خودروهای خودت."""
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    vs = owned(chat.id, uid)
    if not vs:
        await message.reply_text("هنوز هیچ خودرویی نداری. با «خودرو» لیست انواع رو ببین.")
        return

    lines = ["🚗 خودروهای تو:", ""]
    total_value = 0
    for v in vs:
        info = TYPES[v["type"]]
        value = current_value(v)
        total_value += value
        badge = "🛡" if _is_insured(v) else ""
        due = tax_due(v)
        tax_note = f" — ⚠️بدهی مالیاتی: {due:,}" if due > 0 else ""
        tuning_count = sum(v.get("upgrades", {}).values())
        tuning_note = f" — 🔧{tuning_count} ارتقا" if tuning_count else ""
        lines.append(
            f"#{v['id']} {info['name']} {badge} — Condition: {round(v['condition'])}٪ — "
            f"سوخت: {round(v['fuel'])}٪ — کارکرد: {round(v['mileage']):,}km — "
            f"سرعت: {effective_top_speed(v)}km/h — ارزش: {value:,} {config.CURRENCY_NAME}{tax_note}{tuning_note}"
        )
    lines.append("")
    lines.append(f"💎 ارزش کل خودروها: {total_value:,} {config.CURRENCY_NAME}")
    lines.append("")
    lines.append("رانندگی: «رانندگی [شناسه]» — تعمیر: «تعمیر خودرو [شناسه]» — سوخت: «سوخت‌گیری [شناسه]» — بیمه: «بیمه خودرو [شناسه]»")
    await message.reply_text("\n".join(lines))


async def vehicle_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تاریخچه خودرو [شناسه]"""
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    target = _find_owned(chat.id, uid, context.args or [])
    if not target:
        await message.reply_text("استفاده: «تاریخچه خودرو [شناسه]»")
        return
    hist = target.get("history", [])
    if not hist:
        await message.reply_text("هنوز هیچ رویدادی برای این خودرو ثبت نشده.")
        return
    lines = [f"📜 تاریخچه‌ی {TYPES[target['type']]['name']} #{target['id']}:", ""]
    for entry in reversed(hist[-15:]):
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry["ts"]))
        lines.append(f"• {ts} — {entry['event']}: {entry['note']}")
    await message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "vehicles": {str(c): {str(u): vs for u, vs in v.items()} for c, v in _vehicles.items()},
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
            logger.warning(f"ذخیره‌ی state موتور Vehicle ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور Vehicle ناموفق بود: {e}")
        return
    for cid, v in data.get("vehicles", {}).items():
        _vehicles[int(cid)] = defaultdict(list, {int(u): vs for u, vs in v.items()})
    for cid, v in data.get("next_id", {}).items():
        _next_id[int(cid)] = {int(u): n for u, n in v.items()}
    logger.info("وضعیت موتور Vehicle از فایل بارگذاری شد.")
