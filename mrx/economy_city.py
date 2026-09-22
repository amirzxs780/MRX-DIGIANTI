# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY CITY — فاز ۵ (Module 10)
=============================================
هر کاربر یه شهر مستقل داره (per chat_id). ساختمان‌ها درآمد غیرفعال تولید
می‌کنن که مثل سود بانکی (فاز ۳) به‌صورت Lazy جمع می‌شه: هر وقت کاربر با
شهرش کار داشته باشه، بر اساس زمان سپری‌شده خودکار واریز می‌شه.

City Value (برای Net Worth) و Population فقط نمایشی/محاسبه‌شده‌ان، نیازی به
Persist جدا ندارن.
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

logger = logging.getLogger("economy_city")

STATE_FILE = getattr(config, "ECONOMY_CITY_STATE_FILE", "economy_city_state.json")
BUILDINGS: dict = getattr(config, "CITY_BUILDINGS", {})

_save_lock = asyncio.Lock()

# chat_id -> uid -> {"level": int, "buildings": {key: level}, "last_collect_ts": float}
_cities = defaultdict(dict)


def _host():
    import bot as host
    return host


def _city(chat_id: int, uid: int) -> dict | None:
    return _cities[chat_id].get(uid)


def has_city(chat_id: int, uid: int) -> bool:
    c = _city(chat_id, uid)
    return c is not None and c.get("level", 0) > 0


def _income_rate_per_hour(city: dict) -> tuple[int, int]:
    """(income, maintenance) در ساعت، بر اساس ساختمان‌های فعلی."""
    income = 0
    maintenance = 0
    for key, level in city["buildings"].items():
        info = BUILDINGS.get(key)
        if not info or level <= 0:
            continue
        income += info["income_per_level"] * level
        maintenance += info["maintenance_per_level"] * level
    return income, maintenance


def security_score(city: dict) -> int:
    total = 0
    for key, level in city["buildings"].items():
        info = BUILDINGS.get(key)
        if info:
            total += info.get("security_per_level", 0) * level
    return total


def city_value(city: dict) -> int:
    total = 0
    for key, level in city["buildings"].items():
        info = BUILDINGS.get(key)
        if info and level > 0:
            total += info["base_cost"] * level
    return total


def population(city: dict) -> int:
    building_count = sum(1 for lvl in city["buildings"].values() if lvl > 0)
    return city["level"] * 50 + building_count * 30 + sum(city["buildings"].values()) * 10


async def _collect_income(chat_id: int, uid: int) -> int:
    city = _city(chat_id, uid)
    if not city:
        return 0
    income_rate, maintenance_rate = _income_rate_per_hour(city)
    net_rate = income_rate - maintenance_rate
    now = time.time()
    elapsed_hours = min(
        (now - city["last_collect_ts"]) / 3600.0,
        getattr(config, "CITY_INCOME_MAX_ACCRUE_HOURS", 24),
    )
    city["last_collect_ts"] = now
    if net_rate <= 0 or elapsed_hours <= 0:
        return 0
    amount = round(net_rate * elapsed_hours)
    if amount > 0:
        await economy_core.add_coins(chat_id, uid, amount, kind="city_income", note="درآمد غیرفعال شهر")
    return amount


# ---------------------------------------------------------------------------
# 📊 Net Worth Source
# ---------------------------------------------------------------------------

def get_city_value(chat_id: int, uid: int) -> int:
    city = _city(chat_id, uid)
    return city_value(city) if city else 0


economy_core.register_net_worth_source(get_city_value)


# ---------------------------------------------------------------------------
# 📋 نمایش
# ---------------------------------------------------------------------------

async def city_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شهر / /city"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_city"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    if not has_city(chat.id, uid):
        await message.reply_text("هنوز شهری نساختی. با «ساخت شهر» شروع کن.")
        return

    collected = await _collect_income(chat.id, uid)
    city = _city(chat.id, uid)
    income_rate, maintenance_rate = _income_rate_per_hour(city)
    built_count = sum(1 for lvl in city["buildings"].values() if lvl > 0)

    lines = [
        "╭━━━━━━━━━━━━━━━━━━━━╮",
        "🏙️ شهر تو",
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        "",
        f"🏙️ City Level: {city['level']}",
        f"💰 City Value: {city_value(city):,} {config.CURRENCY_NAME}",
        f"👥 Population: {population(city):,}",
        f"🏗️ ساختمان‌های ساخته‌شده: {built_count}/{len(BUILDINGS)}",
        f"📈 درآمد ناخالص: {income_rate}/ساعت — 🔧 نگه‌داری: {maintenance_rate}/ساعت",
        f"🛡️ امنیت: {security_score(city)}",
    ]
    if collected > 0:
        lines.append(f"🎁 درآمد جمع‌شده: +{collected} {config.CURRENCY_NAME}")
    lines.append("")
    lines.append("لیست/ساخت ساختمان: «ساختمان‌ها» — ارتقای شهر: «ارتقای شهر»")
    await message.reply_text("\n".join(lines))
    await host.save_state()


async def build_city_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ساخت شهر / /foundcity — رایگان، فقط یه‌بار."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_city"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    if has_city(chat.id, uid):
        await message.reply_text("شهرت از قبل ساخته شده. با «شهر» ببینش.")
        return

    _cities[chat.id][uid] = {"level": 1, "buildings": {}, "last_collect_ts": time.time()}
    lines = ["🏙️ شهرت پایه‌گذاری شد! Level 1.\nبا «ساختمان‌ها» لیست ساختمان‌های قابل‌ساخت رو ببین."]
    try:
        import economy_achievements
        lines.extend(economy_achievements.check_all(chat.id, uid))
    except Exception as e:
        logger.warning(f"چک دستاوردهای اقتصادی ناموفق بود: {e}")
    await message.reply_text("\n".join(lines))
    await host.save_state()


async def upgrade_city_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارتقای شهر / /upgradecity"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return

    uid = update.effective_user.id
    if not has_city(chat.id, uid):
        await message.reply_text("اول با «ساخت شهر» شهرت رو بساز.")
        return

    city = _city(chat.id, uid)
    next_level = city["level"] + 1
    costs = config.CITY_LEVEL_UPGRADE_COST
    if next_level not in costs:
        await message.reply_text(f"🏙️ شهرت همین الان هم توی بالاترین سطحه ({city['level']}).")
        return
    cost = costs[next_level]
    try:
        await economy_core.remove_coins(chat.id, uid, cost, kind="city_upgrade", note=f"ارتقای شهر به سطح {next_level}")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ برای ارتقا به سطح {next_level} به {cost} {config.CURRENCY_NAME} نیاز داری.")
        return

    city["level"] = next_level
    unlocked = [info["name"] for info in BUILDINGS.values() if info["unlock_city_level"] == next_level]
    lines = [f"🎉 شهرت رفت سطح {next_level}!"]
    if unlocked:
        lines.append("🔓 ساختمان‌های تازه‌باز‌شده: " + "، ".join(unlocked))
    await message.reply_text("\n".join(lines))
    await host.save_state()


async def buildings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ساختمان‌ها [نام] — بدون آرگومان: لیست. با آرگومان: ساخت/ارتقای اون ساختمان."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_city"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    if not has_city(chat.id, uid):
        await message.reply_text("اول با «ساخت شهر» شهرت رو بساز.")
        return

    args = context.args or []
    if not args:
        city = _city(chat.id, uid)
        lines = ["🏗️ ساختمان‌های شهر:", ""]
        for key, info in BUILDINGS.items():
            level = city["buildings"].get(key, 0)
            locked = city["level"] < info["unlock_city_level"]
            if locked:
                lines.append(f"🔒 {info['name']} — نیاز به City Level {info['unlock_city_level']}")
            elif level == 0:
                lines.append(f"⬜ {info['name']} — هزینه‌ی ساخت: {info['base_cost']} {config.CURRENCY_NAME}")
            else:
                upgrade_cost = round(info["base_cost"] * (1 + level * config.CITY_BUILDING_UPGRADE_MULTIPLIER))
                lines.append(
                    f"✅ {info['name']} — Level {level} — درآمد: {info['income_per_level']*level}/ساعت — "
                    f"ارتقا: {upgrade_cost} {config.CURRENCY_NAME}"
                )
        lines.append("")
        lines.append("برای ساخت/ارتقا: «ساختمان‌ها [نام]» — مثلاً «ساختمان‌ها خانه»")
        await message.reply_text("\n".join(lines))
        return

    name = " ".join(args).strip()
    key = None
    for k, info in BUILDINGS.items():
        if name in (k, info["name"]) or name in info["name"]:
            key = k
            break
    if not key:
        await message.reply_text("همچین ساختمانی پیدا نشد. با «ساختمان‌ها» لیست رو ببین.")
        return

    info = BUILDINGS[key]
    city = _city(chat.id, uid)
    if city["level"] < info["unlock_city_level"]:
        await message.reply_text(f"🔒 این ساختمان نیاز به City Level {info['unlock_city_level']} داره.")
        return

    level = city["buildings"].get(key, 0)
    if level == 0:
        cost = info["base_cost"]
        verb = "ساخته"
    else:
        cost = round(info["base_cost"] * (1 + level * config.CITY_BUILDING_UPGRADE_MULTIPLIER))
        verb = "ارتقا داده"

    try:
        await economy_core.remove_coins(chat.id, uid, cost, kind="city_build", note=f"{verb} شدن {info['name']}")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. هزینه: {cost} {config.CURRENCY_NAME}")
        return

    city["buildings"][key] = level + 1
    lines = [f"✅ {info['name']} {verb} شد (Level {level + 1})."]
    try:
        lines += await economy_missions.record_progress(chat.id, uid, "city_build", 1)
    except Exception as e:
        logger.warning(f"ثبت پیشرفت ماموریت city_build ناموفق بود: {e}")
    try:
        import economy_achievements
        lines.extend(economy_achievements.check_all(chat.id, uid))
    except Exception as e:
        logger.warning(f"چک دستاوردهای اقتصادی ناموفق بود: {e}")
    await message.reply_text("\n".join(lines))
    await host.save_state()


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {str(c): {str(u): rec for u, rec in v.items()} for c, v in _cities.items()}


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
            logger.warning(f"ذخیره‌ی state موتور City ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور City ناموفق بود: {e}")
        return
    for cid, v in data.items():
        for uid, rec in v.items():
            rec["buildings"] = {k: int(lvl) for k, lvl in rec.get("buildings", {}).items()}
            _cities[int(cid)][int(uid)] = rec
    logger.info("وضعیت موتور City از فایل بارگذاری شد.")
