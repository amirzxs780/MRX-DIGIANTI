# -*- coding: utf-8 -*-
"""
DIGNANTI ECONOMY DISTRICT — فاز ۴ (City System / منطقه‌های شهر)
==================================================================
هر کاربر توی یکی از منطقه‌های شهرِ مشترک زندگی می‌کنه (poor -> regular ->
downtown -> luxury -> vip). منطقه روی قیمت ملک/کسب‌وکار/جرم/هزینه‌ی خدمات و
Reputation اثر می‌ذاره. بقیه‌ی ماژول‌ها (Property/Business/Vehicle/...) با
صدا زدن multiplier_for(...) این اثر رو توی محاسبه‌ی خودشون اعمال می‌کنن —
این ماژول خودش مستقیم چیزی رو تغییر نمی‌ده (Separation of Concerns، مثل
Economy Core که خودش تراکنش نمی‌سازه، فقط API می‌ده).

معماری هم‌راستا با بقیه‌ی پروژه: _host() برای دسترسی دیرهنگام به bot.py،
Persist با فایل JSON مستقل، همه‌ی تغییرات مالی (هزینه‌ی جابه‌جایی) از
economy_core عبور می‌کنه.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

import config
import economy_core

logger = logging.getLogger("economy_district")

STATE_FILE = getattr(config, "ECONOMY_DISTRICT_STATE_FILE", "economy_district_state.json")
DISTRICTS: dict = getattr(config, "DISTRICTS", {})
DEFAULT_DISTRICT = getattr(config, "DISTRICT_DEFAULT", "poor")

_save_lock = asyncio.Lock()

# chat_id -> uid -> district_key. اگه کاربری اینجا نباشه یعنی هنوز جابه‌جا
# نشده -> پیش‌فرض DEFAULT_DISTRICT (نیازی به Migration نیست، چون نبودن رکورد
# خودش یعنی «هنوز توی منطقه‌ی پیش‌فرضه»).
_residency: dict[int, dict[int, str]] = defaultdict(dict)


def _host():
    import bot as host
    return host


def district_of(chat_id: int, uid: int) -> str:
    """کلید منطقه‌ی فعلی کاربر (پیش‌فرض: poor، برای همه‌ی کاربرای قدیمی/جدید)."""
    return _residency[chat_id].get(uid, DEFAULT_DISTRICT)


def multiplier_for(chat_id: int, uid: int, kind: str) -> float:
    """kind یکی از: property_price, rent, business_price, crime, service_cost.
    ماژول‌های دیگه (Property/Business/Vehicle/Crime) این رو صدا می‌زنن تا اثر
    منطقه رو توی قیمت/نرخ خودشون ضرب کنن. اگه کلید نامعتبر یا District تعریف
    نشده باشه، بی‌اثر (1.0) برمی‌گردونه — هیچ‌وقت نباید کرش کنه."""
    d = DISTRICTS.get(district_of(chat_id, uid))
    if not d:
        return 1.0
    return float(d.get(f"{kind}_mult", 1.0))


def reputation_bonus_of(chat_id: int, uid: int) -> int:
    d = DISTRICTS.get(district_of(chat_id, uid))
    return int(d.get("reputation_bonus", 0)) if d else 0


def _ordered_keys() -> list[str]:
    return sorted(DISTRICTS.keys(), key=lambda k: DISTRICTS[k].get("order", 0))


async def district_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منطقه / /district — منطقه‌ی فعلی + لیست منطقه‌ها + شرط/هزینه‌ی هرکدوم."""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_district"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    host = _host()
    uid = update.effective_user.id
    current = district_of(chat.id, uid)
    xp = host._xp[chat.id].get(uid, 0)
    level, _, _ = host._xp_progress(xp)

    lines = [f"🏙 منطقه‌ی فعلی تو: {DISTRICTS[current]['name']}", ""]
    for key in _ordered_keys():
        info = DISTRICTS[key]
        mark = "✅" if key == current else ("🔒" if level < info["min_level"] else "⬜")
        cost_text = "رایگان" if info["move_cost"] == 0 else f"{info['move_cost']:,} {config.CURRENCY_NAME}"
        lines.append(
            f"{mark} {info['name']} — نیاز Level {info['min_level']} — هزینه‌ی جابه‌جایی: {cost_text} "
            f"(قیمت ملک ×{info['property_price_mult']}، جرم ×{info['crime_mult']})"
        )
    lines.append("")
    lines.append("جابه‌جایی: «تغییر منطقه [نام]»")
    await message.reply_text("\n".join(lines))


def _find_district(name: str) -> str | None:
    name = name.strip().lower()
    if name in DISTRICTS:
        return name
    for key, info in DISTRICTS.items():
        if name in info["name"].lower():
            return key
    return None


async def move_district_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تغییر منطقه [نام] / /movedistrict <name>"""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_district"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «تغییر منطقه [نام]». با «منطقه» لیست رو ببین.")
        return

    host = _host()
    uid = update.effective_user.id
    key = _find_district(" ".join(args))
    if not key:
        await message.reply_text("همچین منطقه‌ای پیدا نشد. با «منطقه» لیست رو ببین.")
        return

    current = district_of(chat.id, uid)
    if key == current:
        await message.reply_text("همین الان همین‌جا زندگی می‌کنی.")
        return

    info = DISTRICTS[key]
    xp = host._xp[chat.id].get(uid, 0)
    level, _, _ = host._xp_progress(xp)
    if level < info["min_level"]:
        await message.reply_text(f"❌ برای رفتن به {info['name']} باید حداقل Level {info['min_level']} باشی.")
        return

    cost = info["move_cost"]
    if cost > 0:
        try:
            await economy_core.remove_coins(chat.id, uid, cost, kind="OTHER",
                                             note=f"جابه‌جایی به {info['name']}")
        except economy_core.InsufficientFundsError:
            await message.reply_text(f"❌ موجودی کافی نیست. هزینه‌ی جابه‌جایی: {cost:,} {config.CURRENCY_NAME}")
            return

    _residency[chat.id][uid] = key
    try:
        import profile_engine
        profile_engine.adjust_stat(chat.id, uid, "fame",
                                    info.get("reputation_bonus", 0) - DISTRICTS[current].get("reputation_bonus", 0))
    except Exception as e:
        logger.warning(f"آپدیت Fame بعد از جابه‌جایی منطقه ناموفق بود (نادیده گرفته شد): {e}")

    await message.reply_text(f"✅ حالا توی {info['name']} زندگی می‌کنی!")
    await host.save_state()


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {str(c): dict(v) for c, v in _residency.items()}


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
            logger.warning(f"ذخیره‌ی state موتور District ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور District ناموفق بود: {e}")
        return
    for cid, v in data.items():
        _residency[int(cid)] = {int(u): key for u, key in v.items() if key in DISTRICTS}
    logger.info("وضعیت موتور District از فایل بارگذاری شد.")
