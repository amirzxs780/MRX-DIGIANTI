# -*- coding: utf-8 -*-
"""
DIGNANTI ECONOMY BUSINESS — فاز ۱۲ (ماژول جدید؛ از صفر ساخته شده)
======================================================================
معماری هم‌راستا با economy_property.py/economy_vehicle.py: هر کسب‌وکار یه
رکورد مستقل با id خودشه. برخلاف Property (که فقط یه نرخ ثابت داره)، اینجا
درآمد به سه چیز وابسته‌ست که خود بازیکن مدیریتشون می‌کنه:

  1) موجودی (Inventory) — بدون موجودی، فروش نداری (درآمد = ۰)
  2) کارمند (Employees) — هرکدوم درآمد رو درصدی بالا می‌بره، ولی حقوق می‌گیرن
  3) قیمت‌گذاری (Price Multiplier) — قیمت بالاتر یعنی سود بیشتر روی هر فروش
     ولی مشتری کمتر (فرمول non-linear، پس "قیمت رو بذار حداکثر" همیشه بهینه نیست)

مثل Property/Vehicle، محاسبه‌ی درآمد/هزینه Lazy انجام می‌شه (بر اساس زمان
سپری‌شده از آخرین Collect)، نه با یه Task زمان‌بندی‌شده — که با معماری بقیه‌ی
پروژه (بدون نیاز به APScheduler/Cronjob) هم‌خونه.

مدیریت ریسک مالی: اگه موجودی کاربر برای پرداخت هزینه‌های یه دوره کافی نباشه،
کسب‌وکار به‌جای بردن موجودی به منفی، وارد "بحران" می‌شه (کارمندا اخراج
می‌شن، قیمت ریست می‌شه) — دقیقاً طبق قانون کاربر: Negative Balance ممنوع.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

import config
import economy_core
import economy_missions

logger = logging.getLogger("economy_business")

STATE_FILE = getattr(config, "ECONOMY_BUSINESS_STATE_FILE", "economy_business_state.json")
TYPES: dict = getattr(config, "BUSINESS_TYPES", {})
MAX_HISTORY_PER_BUSINESS = 20

_save_lock = asyncio.Lock()

# chat_id -> uid -> list[{id, type, level, employees, inventory, price_mult,
#                          ad_until, last_collect_ts, last_tax_ts,
#                          lifetime_revenue, lifetime_expense, history}]
_businesses = defaultdict(lambda: defaultdict(list))
_next_id = defaultdict(dict)


def _host():
    import bot as host
    return host


def _new_id(chat_id: int, uid: int) -> int:
    n = _next_id[chat_id].get(uid, 0) + 1
    _next_id[chat_id][uid] = n
    return n


def _log_history(b: dict, event: str, note: str = "") -> None:
    hist = b.setdefault("history", [])
    hist.append({"ts": time.time(), "event": event, "note": note})
    if len(hist) > MAX_HISTORY_PER_BUSINESS:
        del hist[: len(hist) - MAX_HISTORY_PER_BUSINESS]


def owned(chat_id: int, uid: int) -> list[dict]:
    return _businesses[chat_id][uid]


def _find_owned(chat_id: int, uid: int, args: list) -> dict | None:
    if not args:
        return None
    try:
        b_id = int(args[0])
    except ValueError:
        return None
    return next((b for b in owned(chat_id, uid) if b["id"] == b_id), None)


# ---------------------------------------------------------------------------
# 💰 محاسبات مالی
# ---------------------------------------------------------------------------

def current_price(chat_id: int, uid: int, btype: str) -> int:
    """قیمت خرید نو، با احتساب ضریب منطقه (فاز ۴)."""
    base = float(TYPES[btype]["base_price"])
    try:
        import economy_district
        base *= economy_district.multiplier_for(chat_id, uid, "business_price")
    except Exception:
        pass
    return round(base)


def _is_ad_active(b: dict) -> bool:
    return bool(b.get("ad_until") and b["ad_until"] > time.time())


def hourly_revenue(b: dict) -> float:
    """درآمد ناخالص در ساعت، با شرایط *فعلی* (موجودی/کارمند/قیمت/تبلیغات).
    اگه موجودی صفر باشه، درآمد صفره — طبق طراحی، نه باگ."""
    if b["inventory"] <= 0:
        return 0.0
    info = TYPES[b["type"]]
    level_mult = 1 + (b["level"] - 1) * getattr(config, "BUSINESS_LEVEL_REVENUE_BONUS", 0.25)
    employee_mult = 1 + b["employees"] * info["employee_revenue_bonus_frac"]
    price_mult = b["price_mult"]
    # هرچی قیمت از ۱.۰ بالاتر بره، مشتری کمتر میاد (رابطه‌ی غیرخطی): روی ۱.۰
    # اثر خنثی، روی ۲.۰ فقط ۷۰٪ مشتری قبلی. زیر ۱.۰ هم مشتری بیشتر جذب می‌شه
    # ولی سود هر واحد کمتره - جمعاً یه بهینه‌ی داخلی (نه لزوماً در لبه‌ها) داره.
    customer_mult = max(0.3, min(1.3, 2 - price_mult))
    ad_mult = getattr(config, "BUSINESS_AD_REVENUE_MULTIPLIER", 1.3) if _is_ad_active(b) else 1.0
    return info["base_revenue_per_hour"] * level_mult * employee_mult * price_mult * customer_mult * ad_mult


def hourly_expense(b: dict) -> float:
    info = TYPES[b["type"]]
    rent = info["base_expense_per_hour"] * (1 + (b["level"] - 1) * 0.15)
    salaries = b["employees"] * info["employee_cost_per_hour"]
    return rent + salaries


def current_value(b: dict) -> int:
    """ارزش فعلی = قیمت پایه × Level-Multiplier + نصف ارزش موجودی انبار."""
    info = TYPES[b["type"]]
    level_mult = 1 + (b["level"] - 1) * 0.5
    inv_value = b["inventory"] * info["inventory_cost_per_unit"] * 0.5
    return round(info["base_price"] * level_mult + inv_value)


def upgrade_cost(b: dict) -> int:
    info = TYPES[b["type"]]
    mult = getattr(config, "BUSINESS_UPGRADE_COST_MULTIPLIER", 0.8)
    return round(info["base_price"] * mult * (b["level"] + 1))


def hire_cost(b: dict) -> int:
    """هزینه‌ی استخدام کارمند بعدی (یه‌بار مصرف، مثل بیعانه/آموزش) — جدا از
    حقوق ساعتی که از hourly_expense میاد."""
    info = TYPES[b["type"]]
    return round(info["employee_cost_per_hour"] * 20 * (b["employees"] + 1))


def tax_due(b: dict) -> int:
    info = TYPES[b["type"]]
    period_h = getattr(config, "BUSINESS_TAX_PERIOD_HOURS", 168)
    elapsed_h = (time.time() - b["last_tax_ts"]) / 3600.0
    periods = int(elapsed_h // period_h)
    if periods <= 0:
        return 0
    return round(current_value(b) * info["tax_rate"] * periods)


def _simulate_period(b: dict, elapsed_hours: float) -> tuple[float, float, float]:
    """درآمد/هزینه/موجودی‌مصرفی رو برای elapsed_hours ساعت (با نرخ فعلی، ثابت
    فرض‌شده روی کل بازه — همون ساده‌سازی‌ای که Property/City هم دارن)
    حساب می‌کنه. سقف BUSINESS_MAX_LAZY_HOURS داره تا کسی با غیبت طولانی
    بدهی/طلب کهکشانی جمع نکنه."""
    elapsed_hours = min(elapsed_hours, getattr(config, "BUSINESS_MAX_LAZY_HOURS", 72))
    if elapsed_hours <= 0:
        return 0.0, 0.0, 0.0
    info = TYPES[b["type"]]
    consumption_capacity_hours = (b["inventory"] / info["inventory_consumption_per_hour"]
                                   if info["inventory_consumption_per_hour"] > 0 else elapsed_hours)
    productive_hours = min(elapsed_hours, consumption_capacity_hours)
    revenue = hourly_revenue(b) * productive_hours
    expense = hourly_expense(b) * elapsed_hours  # اجاره/حقوق حتی بدون موجودی هم پرداخت می‌شه
    consumed = info["inventory_consumption_per_hour"] * productive_hours
    return revenue, expense, consumed


async def _collect_one(chat_id: int, uid: int, b: dict) -> tuple[int, int]:
    """سود/زیان از آخرین Collect رو تسویه می‌کنه. برمی‌گردونه (net, employees_fired).
    اگه موجودی کاربر برای هزینه‌ها کافی نباشه، به‌جای منفی‌شدن، کسب‌وکار وارد
    بحران می‌شه (طبق Negative-Balance Protection پروژه)."""
    now = time.time()
    elapsed_h = (now - b["last_collect_ts"]) / 3600.0
    if elapsed_h <= 0:
        return 0, 0
    revenue, expense, consumed = _simulate_period(b, elapsed_h)
    b["inventory"] = max(0, round(b["inventory"] - consumed))
    b["last_collect_ts"] = now
    b["lifetime_revenue"] = b.get("lifetime_revenue", 0) + revenue
    b["lifetime_expense"] = b.get("lifetime_expense", 0) + expense

    net = round(revenue - expense)
    employees_fired = 0
    if net >= 0:
        if net > 0:
            await economy_core.add_coins(chat_id, uid, net, kind="BUSINESS",
                                          note=f"سود {TYPES[b['type']]['name']} #{b['id']}")
        return net, 0

    # net منفیه: سعی کن از موجودی کاربر کم کنی؛ اگه نشد، بحران
    try:
        await economy_core.remove_coins(chat_id, uid, -net, kind="BUSINESS",
                                         note=f"زیان {TYPES[b['type']]['name']} #{b['id']}")
        return net, 0
    except economy_core.InsufficientFundsError:
        employees_fired = b["employees"]
        b["employees"] = 0
        b["price_mult"] = 1.0
        _log_history(b, "OTHER", f"بحران مالی: {employees_fired} کارمند اخراج شد (موجودی برای هزینه‌ها کافی نبود)")
        return 0, employees_fired


# ---------------------------------------------------------------------------
# 📊 Net Worth Source
# ---------------------------------------------------------------------------

def get_business_value(chat_id: int, uid: int) -> int:
    return sum(current_value(b) for b in owned(chat_id, uid))


economy_core.register_net_worth_source(get_business_value)


# ---------------------------------------------------------------------------
# 📋 لیست / خرید / فروش
# ---------------------------------------------------------------------------

async def business_types_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """کسب‌وکارها / /businesses — لیست انواع قابل‌خرید."""
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_business"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return
    uid = update.effective_user.id
    lines = ["🏢 انواع کسب‌وکار قابل‌خرید:", ""]
    for key, info in TYPES.items():
        price = current_price(chat.id, uid, key)
        lines.append(
            f"{info['name']} — قیمت: {price:,} {config.CURRENCY_NAME} — درآمد پایه: "
            f"{info['base_revenue_per_hour']}/h — ظرفیت کارمند: {info['max_employees']}"
        )
    lines.append("")
    lines.append("خرید: «خرید کسب‌وکار [نوع]» — کسب‌وکارهای خودت: «کسب‌وکار من»")
    await message.reply_text("\n".join(lines))


def _find_type(name: str) -> str | None:
    name = name.strip().lower()
    if name in TYPES:
        return name
    for key, info in TYPES.items():
        if name in info["name"].lower():
            return key
    return None


async def buy_business_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خرید کسب‌وکار [نوع] / /buybusiness <type>"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_business"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «خرید کسب‌وکار [نوع]». با «کسب‌وکارها» لیست رو ببین.")
        return
    uid = update.effective_user.id
    key = _find_type(" ".join(args))
    if not key:
        await message.reply_text("همچین نوع کسب‌وکاری نیست. با «کسب‌وکارها» لیست رو ببین.")
        return

    price = current_price(chat.id, uid, key)
    try:
        await economy_core.remove_coins(chat.id, uid, price, kind="BUSINESS", note=f"خرید {TYPES[key]['name']}")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. قیمت: {price:,} {config.CURRENCY_NAME}")
        return

    now = time.time()
    b = {
        "id": _new_id(chat.id, uid), "type": key, "level": 1, "employees": 0,
        "inventory": 0, "price_mult": 1.0, "ad_until": None,
        "last_collect_ts": now, "last_tax_ts": now, "purchased_ts": now,
        "lifetime_revenue": 0, "lifetime_expense": 0, "history": [],
    }
    _log_history(b, "BUSINESS", f"خرید نو به قیمت {price:,}")
    owned(chat.id, uid).append(b)

    lines = [f"✅ {TYPES[key]['name']} #{b['id']} خریداری شد! یادت نره موجودی بخری وگرنه فروش نداره: «خرید موجودی {b['id']} [تعداد]»"]
    try:
        lines += await economy_missions.record_progress(chat.id, uid, "business_buy", 1)
    except Exception as e:
        logger.warning(f"ثبت پیشرفت ماموریت business_buy ناموفق بود: {e}")
    await message.reply_text("\n".join(lines))
    await host.save_state()


async def sell_business_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فروش کسب‌وکار [شناسه] / /sellbusiness <id>"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    target = _find_owned(chat.id, uid, context.args or [])
    if not target:
        await message.reply_text("استفاده: «فروش کسب‌وکار [شناسه]». شناسه‌ها رو با «کسب‌وکار من» ببین.")
        return

    await _collect_one(chat.id, uid, target)
    resale_fraction = getattr(config, "BUSINESS_RESALE_FRACTION", 0.55)
    payout = round(current_value(target) * resale_fraction)
    owned(chat.id, uid).remove(target)
    new_wallet = await economy_core.add_coins(chat.id, uid, payout, kind="SALE",
                                               note=f"فروش {TYPES[target['type']]['name']} #{target['id']}")
    await message.reply_text(
        f"✅ {TYPES[target['type']]['name']} #{target['id']} فروخته شد: +{payout:,} {config.CURRENCY_NAME}.\n"
        f"💰 موجودی: {new_wallet:,} {config.CURRENCY_NAME}"
    )
    await host.save_state()


# ---------------------------------------------------------------------------
# 👷 کارمند / 📦 موجودی / 💲 قیمت / 📢 تبلیغات / ⭐ ارتقا
# ---------------------------------------------------------------------------

async def hire_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استخدام [شناسه] / /hire <id>"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    target = _find_owned(chat.id, uid, context.args or [])
    if not target:
        await message.reply_text("استفاده: «استخدام [شناسه]»")
        return
    info = TYPES[target["type"]]
    if target["employees"] >= info["max_employees"]:
        await message.reply_text(f"❌ ظرفیت کارمند پر شده (حداکثر {info['max_employees']} نفر).")
        return
    await _collect_one(chat.id, uid, target)  # قبل از تغییر شرایط، حساب قبلی تسویه بشه
    cost = hire_cost(target)
    try:
        await economy_core.remove_coins(chat.id, uid, cost, kind="OTHER", note="هزینه‌ی استخدام")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. هزینه‌ی استخدام: {cost:,} {config.CURRENCY_NAME}")
        return
    target["employees"] += 1
    _log_history(target, "BUSINESS", f"استخدام کارمند #{target['employees']} به هزینه‌ی {cost:,}")
    await message.reply_text(
        f"✅ یه کارمند جدید استخدام شد ({target['employees']}/{info['max_employees']}). "
        f"حقوق ساعتی الان: {round(hourly_expense(target))} {config.CURRENCY_NAME}/h"
    )
    await host.save_state()


async def fire_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اخراج [شناسه] / /fire <id>"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    target = _find_owned(chat.id, uid, context.args or [])
    if not target:
        await message.reply_text("استفاده: «اخراج [شناسه]»")
        return
    if target["employees"] <= 0:
        await message.reply_text("این کسب‌وکار الان هیچ کارمندی نداره.")
        return
    await _collect_one(chat.id, uid, target)
    target["employees"] -= 1
    _log_history(target, "BUSINESS", f"اخراج یه کارمند (الان {target['employees']} نفر)")
    await message.reply_text(f"✅ یه کارمند اخراج شد ({target['employees']} نفر باقی موند).")
    await host.save_state()


async def buy_inventory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خرید موجودی [شناسه] [تعداد] / /buyinventory <id> <qty>"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if len(args) < 2:
        await message.reply_text("استفاده: «خرید موجودی [شناسه] [تعداد]»")
        return
    uid = update.effective_user.id
    target = _find_owned(chat.id, uid, args)
    if not target:
        await message.reply_text("همچین کسب‌وکاری توی مالکیتت نیست.")
        return
    try:
        qty = int(args[1])
    except ValueError:
        await message.reply_text("تعداد نامعتبره.")
        return
    if qty <= 0:
        await message.reply_text("تعداد باید مثبت باشه.")
        return
    info = TYPES[target["type"]]
    room = info["inventory_capacity"] - target["inventory"]
    if room <= 0:
        await message.reply_text("❌ انبار پره.")
        return
    qty = min(qty, room)
    cost = round(qty * info["inventory_cost_per_unit"])
    try:
        await economy_core.remove_coins(chat.id, uid, cost, kind="OTHER", note="خرید موجودی")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. هزینه‌ی {qty} واحد: {cost:,} {config.CURRENCY_NAME}")
        return
    target["inventory"] += qty
    _log_history(target, "BUSINESS", f"خرید {qty} واحد موجودی به هزینه‌ی {cost:,}")
    await message.reply_text(
        f"📦 {qty} واحد موجودی خریداری شد ({target['inventory']}/{info['inventory_capacity']}). هزینه: {cost:,} {config.CURRENCY_NAME}"
    )
    await host.save_state()


async def set_price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قیمت کسب‌وکار [شناسه] [ضریب] — ضریب بین ۰.۵ تا ۲.۰"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if len(args) < 2:
        await message.reply_text("استفاده: «قیمت کسب‌وکار [شناسه] [ضریب بین 0.5 تا 2.0]»")
        return
    uid = update.effective_user.id
    target = _find_owned(chat.id, uid, args)
    if not target:
        await message.reply_text("همچین کسب‌وکاری توی مالکیتت نیست.")
        return
    try:
        mult = float(args[1])
    except ValueError:
        await message.reply_text("ضریب نامعتبره.")
        return
    lo = getattr(config, "BUSINESS_MIN_PRICE_MULT", 0.5)
    hi = getattr(config, "BUSINESS_MAX_PRICE_MULT", 2.0)
    if not (lo <= mult <= hi):
        await message.reply_text(f"❌ ضریب باید بین {lo} تا {hi} باشه.")
        return
    await _collect_one(chat.id, uid, target)  # قبل از تغییر قیمت، حساب قبلی با قیمت قبلی تسویه بشه
    target["price_mult"] = mult
    _log_history(target, "BUSINESS", f"تغییر ضریب قیمت به {mult}")
    await message.reply_text(f"✅ ضریب قیمت روی {mult} تنظیم شد. (درآمد تخمینی: {round(hourly_revenue(target))} {config.CURRENCY_NAME}/h)")
    await host.save_state()


async def advertise_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تبلیغات [شناسه] / /advertise <id>"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    target = _find_owned(chat.id, uid, context.args or [])
    if not target:
        await message.reply_text("استفاده: «تبلیغات [شناسه]»")
        return
    cost = round(TYPES[target["type"]]["base_price"] * getattr(config, "BUSINESS_AD_COST_FRACTION", 0.05))
    try:
        await economy_core.remove_coins(chat.id, uid, cost, kind="OTHER", note="کمپین تبلیغاتی")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. هزینه‌ی تبلیغات: {cost:,} {config.CURRENCY_NAME}")
        return
    duration_h = getattr(config, "BUSINESS_AD_DURATION_HOURS", 12)
    base = target["ad_until"] if _is_ad_active(target) else time.time()
    target["ad_until"] = base + duration_h * 3600
    _log_history(target, "BUSINESS", f"کمپین تبلیغاتی {duration_h} ساعته به هزینه‌ی {cost:,}")
    await message.reply_text(f"📢 تبلیغات شروع شد! درآمد تا {duration_h} ساعت آینده {getattr(config, 'BUSINESS_AD_REVENUE_MULTIPLIER', 1.3)}× می‌شه.")
    await host.save_state()


async def upgrade_business_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارتقای کسب‌وکار [شناسه] / /upgradebusiness <id>"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    target = _find_owned(chat.id, uid, context.args or [])
    if not target:
        await message.reply_text("استفاده: «ارتقای کسب‌وکار [شناسه]»")
        return
    cost = upgrade_cost(target)
    try:
        await economy_core.remove_coins(chat.id, uid, cost, kind="BUSINESS", note="ارتقای کسب‌وکار")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. هزینه‌ی ارتقا: {cost:,} {config.CURRENCY_NAME}")
        return
    await _collect_one(chat.id, uid, target)
    target["level"] += 1
    _log_history(target, "BUSINESS", f"ارتقا به Level {target['level']} به هزینه‌ی {cost:,}")
    await message.reply_text(f"⭐ {TYPES[target['type']]['name']} #{target['id']} رفت Level {target['level']}!")
    await host.save_state()


async def pay_business_tax_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مالیات کسب‌وکار [شناسه] / /businesstax <id>"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    target = _find_owned(chat.id, uid, context.args or [])
    if not target:
        await message.reply_text("استفاده: «مالیات کسب‌وکار [شناسه]»")
        return
    due = tax_due(target)
    if due <= 0:
        await message.reply_text("بدهی مالیاتی‌ای نداری.")
        return
    try:
        await economy_core.remove_coins(chat.id, uid, due, kind="TAX", note="مالیات کسب‌وکار")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. بدهی مالیاتی: {due:,} {config.CURRENCY_NAME}")
        return
    target["last_tax_ts"] = time.time()
    _log_history(target, "TAX", f"پرداخت مالیات {due:,}")
    await message.reply_text(f"💰 مالیات {TYPES[target['type']]['name']} #{target['id']} پرداخت شد ({due:,} {config.CURRENCY_NAME}).")
    await host.save_state()


# ---------------------------------------------------------------------------
# 📋 نمایش / داشبورد سود-زیان
# ---------------------------------------------------------------------------

async def my_business_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """کسب‌وکار من [شناسه] — بدون شناسه: لیست + جمع‌آوری سود همه‌شون.
    با شناسه: فقط تسویه‌ی همون یکی."""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    bs = owned(chat.id, uid)
    if not bs:
        await message.reply_text("هنوز هیچ کسب‌وکاری نداری. با «کسب‌وکارها» لیست انواع رو ببین.")
        return

    args = context.args or []
    targets = bs
    if args:
        one = _find_owned(chat.id, uid, args)
        if not one:
            await message.reply_text("همچین کسب‌وکاری توی مالکیتت نیست.")
            return
        targets = [one]

    total_net = 0
    crisis_notes = []
    for b in targets:
        net, fired = await _collect_one(chat.id, uid, b)
        total_net += net
        if fired:
            crisis_notes.append(f"⚠️ {TYPES[b['type']]['name']} #{b['id']}: بحران مالی، {fired} کارمند اخراج شد!")

    lines = ["🏢 کسب‌وکارهای تو:", ""]
    for b in bs:
        info = TYPES[b["type"]]
        due = tax_due(b)
        tax_note = f" — ⚠️مالیات: {due:,}" if due > 0 else ""
        ad_badge = "📢" if _is_ad_active(b) else ""
        lines.append(
            f"#{b['id']} {info['name']} Lv{b['level']} {ad_badge} — کارمند: {b['employees']}/{info['max_employees']} — "
            f"موجودی: {b['inventory']}/{info['inventory_capacity']} — قیمت: ×{b['price_mult']} — "
            f"درآمد: {round(hourly_revenue(b))}/h — هزینه: {round(hourly_expense(b))}/h{tax_note}"
        )
    lines.append("")
    if total_net > 0:
        lines.append(f"💰 سود این تسویه: +{total_net:,} {config.CURRENCY_NAME}")
    elif total_net < 0:
        lines.append(f"📉 زیان این تسویه: {total_net:,} {config.CURRENCY_NAME}")
    lines.extend(crisis_notes)
    await message.reply_text("\n".join(lines))
    await host.save_state()


async def business_profit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """سود کسب‌وکار [شناسه] — داشبورد سود/زیان کامل (Lifetime + تخمین ساعتی)."""
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    target = _find_owned(chat.id, uid, context.args or [])
    if not target:
        await message.reply_text("استفاده: «سود کسب‌وکار [شناسه]»")
        return
    info = TYPES[target["type"]]
    rev_h, exp_h = hourly_revenue(target), hourly_expense(target)
    lifetime_net = target.get("lifetime_revenue", 0) - target.get("lifetime_expense", 0)
    lines = [
        f"📊 داشبورد سود/زیان — {info['name']} #{target['id']}",
        "",
        f"درآمد ساعتی: {round(rev_h):,} {config.CURRENCY_NAME}/h",
        f"هزینه ساعتی: {round(exp_h):,} {config.CURRENCY_NAME}/h (اجاره + حقوق {target['employees']} کارمند)",
        f"سود خالص ساعتی: {round(rev_h - exp_h):,} {config.CURRENCY_NAME}/h",
        "",
        f"جمع درآمد از خرید تا الان: {round(target.get('lifetime_revenue', 0)):,}",
        f"جمع هزینه از خرید تا الان: {round(target.get('lifetime_expense', 0)):,}",
        f"سود/زیان خالص کلی: {round(lifetime_net):,}",
        "",
        f"ارزش فعلی: {current_value(target):,} {config.CURRENCY_NAME}",
    ]
    if target["inventory"] <= 0:
        lines.append("")
        lines.append("⚠️ موجودی صفره — درآمدت الان صفره! «خرید موجودی [شناسه] [تعداد]»")
    await message.reply_text("\n".join(lines))


async def business_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تاریخچه کسب‌وکار [شناسه]"""
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    target = _find_owned(chat.id, uid, context.args or [])
    if not target:
        await message.reply_text("استفاده: «تاریخچه کسب‌وکار [شناسه]»")
        return
    hist = target.get("history", [])
    if not hist:
        await message.reply_text("هنوز هیچ رویدادی برای این کسب‌وکار ثبت نشده.")
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
        "businesses": {str(c): {str(u): bs for u, bs in v.items()} for c, v in _businesses.items()},
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
            logger.warning(f"ذخیره‌ی state موتور Business ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور Business ناموفق بود: {e}")
        return
    for cid, v in data.get("businesses", {}).items():
        _businesses[int(cid)] = defaultdict(list, {int(u): bs for u, bs in v.items()})
    for cid, v in data.get("next_id", {}).items():
        _next_id[int(cid)] = {int(u): n for u, n in v.items()}
    logger.info("وضعیت موتور Business از فایل بارگذاری شد.")
