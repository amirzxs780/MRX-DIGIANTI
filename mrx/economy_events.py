# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY EVENTS — فاز ۹ (Module 24)
================================================
رویدادهای تصادفیِ سطح گروه (نه per-user) که چند ماژول رو هم‌زمان تحت تأثیر
قرار می‌دن. هر لحظه حداکثر یه رویداد فعاله. شروع رویداد به‌صورت Lazy چک
می‌شه (هر وقت get_active_event/maybe_trigger صدا زده بشه) — دقیقاً مثل الگوی
Market Tick در فاز ۳.

ماژول‌های دیگه (jobs/bank/pet/underground/shop) این تابع‌ها رو صدا می‌زنن:
    economy_events.income_multiplier(chat_id)
    economy_events.job_xp_multiplier(chat_id)
    economy_events.pet_xp_multiplier(chat_id)
    economy_events.bank_interest_multiplier(chat_id)
    economy_events.underground_reward_multiplier(chat_id)
    economy_events.rare_item_chance_multiplier(chat_id)
    economy_events.shop_price_multiplier(chat_id)
همه‌شون Fail-Safe-ان: اگه رویدادی فعال نباشه، ۱.۰ (بدون اثر) برمی‌گردونن.
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

logger = logging.getLogger("economy_events")

STATE_FILE = getattr(config, "ECONOMY_EVENTS_STATE_FILE", "economy_events_state.json")
EVENTS: dict = getattr(config, "ECONOMY_EVENTS", {})

_save_lock = asyncio.Lock()

# chat_id -> {"key": str, "ends_ts": float} | None
_active = defaultdict(lambda: None)
_last_trigger_ts = defaultdict(float)


def _host():
    import bot as host
    return host


def maybe_trigger(chat_id: int) -> str | None:
    """اگه الان وقتشه، یه رویداد جدید تصادفی شروع می‌کنه؛ متن اعلامیه رو
    برمی‌گردونه (یا None اگه چیزی شروع نشد)."""
    active = _active[chat_id]
    now = time.time()
    if active and now < active["ends_ts"]:
        return None  # یه رویداد از قبل فعاله

    if active and now >= active["ends_ts"]:
        _active[chat_id] = None  # رویداد قبلی تموم شده

    min_gap = getattr(config, "EVENT_MIN_GAP_MINUTES", 45) * 60
    if now - _last_trigger_ts[chat_id] < min_gap:
        return None

    if random.random() >= getattr(config, "EVENT_TRIGGER_CHANCE_PER_CHECK", 0.03):
        return None
    if not EVENTS:
        return None

    key = random.choice(list(EVENTS.keys()))
    ev = EVENTS[key]
    _active[chat_id] = {"key": key, "ends_ts": now + ev["duration_minutes"] * 60}
    _last_trigger_ts[chat_id] = now
    return f"{ev['label']} شروع شد! (تا {ev['duration_minutes']} دقیقه‌ی دیگه)"


def get_active_event(chat_id: int) -> dict | None:
    active = _active[chat_id]
    if not active:
        return None
    if time.time() >= active["ends_ts"]:
        _active[chat_id] = None
        return None
    return {**EVENTS[active["key"]], "key": active["key"], "ends_ts": active["ends_ts"]}


def _multiplier_for(chat_id: int, effect: str) -> float:
    ev = get_active_event(chat_id)
    if ev and ev["effect"] == effect:
        return ev["value"]
    return 1.0


def income_multiplier(chat_id: int) -> float:
    return _multiplier_for(chat_id, "income_multiplier")


def job_xp_multiplier(chat_id: int) -> float:
    return _multiplier_for(chat_id, "job_xp_multiplier")


def pet_xp_multiplier(chat_id: int) -> float:
    return _multiplier_for(chat_id, "pet_xp_multiplier")


def bank_interest_multiplier(chat_id: int) -> float:
    return _multiplier_for(chat_id, "bank_interest_multiplier")


def underground_reward_multiplier(chat_id: int) -> float:
    return _multiplier_for(chat_id, "underground_reward_multiplier")


def rare_item_chance_multiplier(chat_id: int) -> float:
    return _multiplier_for(chat_id, "rare_item_chance_multiplier")


def shop_price_multiplier(chat_id: int) -> float:
    """برای shop_discount: قیمت رو ضرب در این عدد کن (کمتر از ۱ یعنی تخفیف)."""
    ev = get_active_event(chat_id)
    if ev and ev["effect"] == "shop_discount":
        return ev["value"]
    return 1.0


# ---------------------------------------------------------------------------
# 📋 نمایش / Admin
# ---------------------------------------------------------------------------

async def event_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رویداد / /event"""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return

    announcement = maybe_trigger(chat.id)
    ev = get_active_event(chat.id)
    if announcement:
        await message.reply_text(f"🎉 {announcement}")
        return
    if ev:
        remaining = int((ev["ends_ts"] - time.time()) // 60) + 1
        await message.reply_text(f"{ev['label']} فعاله! ({remaining} دقیقه مونده)")
    else:
        await message.reply_text("الان هیچ رویداد فعالی نیست.")


async def force_event_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فورس رویداد [نوع] — فقط ادمین (manage_economy) / /forceevent <key>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not host.has_permission(update.effective_user.id, "manage_economy"):
        await message.reply_text("⛔ این دستور فقط برای ادمین‌های اقتصاده.")
        return

    args = context.args or []
    if not args or args[0] not in EVENTS:
        names = "، ".join(EVENTS.keys())
        await message.reply_text(f"استفاده: «فورس رویداد [نوع]». گزینه‌ها: {names}")
        return

    key = args[0]
    ev = EVENTS[key]
    _active[chat.id] = {"key": key, "ends_ts": time.time() + ev["duration_minutes"] * 60}
    _last_trigger_ts[chat.id] = time.time()
    await message.reply_text(f"✅ {ev['label']} به‌صورت دستی فعال شد.")
    await host.save_state()


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "active": {str(c): v for c, v in _active.items() if v},
        "last_trigger_ts": {str(c): ts for c, ts in _last_trigger_ts.items()},
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
            logger.warning(f"ذخیره‌ی state موتور Events ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور Events ناموفق بود: {e}")
        return
    for cid, v in data.get("active", {}).items():
        _active[int(cid)] = v
    for cid, ts in data.get("last_trigger_ts", {}).items():
        _last_trigger_ts[int(cid)] = ts
    logger.info("وضعیت موتور Events از فایل بارگذاری شد.")
