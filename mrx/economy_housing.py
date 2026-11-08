# -*- coding: utf-8 -*-
"""
DIGNANTI ECONOMY HOUSING MARKET — فاز ۶ (V18: بازارِ مسکنِ پویا)
=====================================================================
بخشِ «سیستمِ بازارِ مسکن»ی بخشِ ۶ (املاکِ پیشرفته). economy_property.py از
قبل یه قیمت‌گذاریِ شخصی داره («هر ملکِ اضافه برایِ همون‌کاربر گرون‌تره»)؛
این ماژول چیزِ دیگه‌ایه: یه مولتیپلایرِ **گروهی/منطقه‌ای** که از رویِ
خرید/فروشِ واقعیِ *همه‌ی* اعضایِ گروه در هر منطقه (از economy_district.py)
ساخته می‌شه - دقیقاً همون «مناطقِ گران‌قیمت / در حالِ توسعه / کم‌تقاضا» و
«رونق/رکودِ بازارِ مسکن»ی که بخشِ ۶ خواسته بود.

طرزِ کار (دقیقاً هم‌خانواده با economy_supply.py از فازِ ۲ - فشارِ
تقاضا/عرضه با Decay، Lazy Tick، سقفِ تغییرِ هر Tick):
  • هر خریدِ ملک در یه منطقه → فشارِ تقاضایِ اون منطقه بالا می‌ره → مولتیپلایر
    گرایش به افزایش پیدا می‌کنه.
  • هر فروشِ ملک در یه منطقه → فشارِ عرضه بالا می‌ره → مولتیپلایر گرایش به
    کاهش پیدا می‌کنه.
  • مولتیپلایر همیشه بینِ HOUSING_MIN_MULTIPLIER و HOUSING_MAX_MULTIPLIER
    کلمپ می‌شه، و هر Tick حداکثر HOUSING_MAX_CHANGE_PER_TICK درصد حرکت
    می‌کنه - ضدِ نوسانِ غیرمنطقی.

economy_property.current_price() این مولتیپلایر رو (دقیقاً مثلِ مولتیپلایرِ
district/events موجود) توی محاسبه‌ی قیمتِ خرید ضرب می‌کنه؛ این ماژول خودش
هیچ پولی جابه‌جا نمی‌کنه.
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

logger = logging.getLogger("economy_housing")

STATE_FILE = getattr(config, "ECONOMY_HOUSING_STATE_FILE", "economy_housing_state.json")
STATE_VERSION = 1

_save_lock = asyncio.Lock()


def _districts() -> dict:
    return getattr(config, "DISTRICTS", {})


# chat_id -> district_key -> مولتیپلایرِ فعلی
_multipliers: dict[int, dict] = defaultdict(dict)
# chat_id -> district_key -> {"demand": float, "supply": float}
_pressure: dict[int, dict] = defaultdict(lambda: defaultdict(lambda: {"demand": 0.0, "supply": 0.0}))
# chat_id -> district_key -> [multiplier, ...]
_history: dict[int, dict] = defaultdict(lambda: defaultdict(list))
_last_tick_ts: dict[int, float] = defaultdict(float)


def _ensure_init(chat_id: int) -> None:
    if _multipliers[chat_id]:
        return
    for key in _districts():
        _multipliers[chat_id][key] = 1.0
        _history[chat_id][key] = [1.0]
    _last_tick_ts[chat_id] = time.time()


def _apply_one_tick(chat_id: int) -> None:
    decay = getattr(config, "HOUSING_PRESSURE_DECAY_PER_TICK", 0.90)
    max_change = getattr(config, "HOUSING_MAX_CHANGE_PER_TICK", 0.05)
    lo = getattr(config, "HOUSING_MIN_MULTIPLIER", 0.6)
    hi = getattr(config, "HOUSING_MAX_MULTIPLIER", 1.8)
    hist_len = getattr(config, "HOUSING_PRICE_HISTORY_LEN", 30)
    d_sens = getattr(config, "HOUSING_DEMAND_SENSITIVITY", 0.03)
    s_sens = getattr(config, "HOUSING_SUPPLY_SENSITIVITY", 0.025)

    for key in _districts():
        p = _pressure[chat_id][key]
        p["demand"] *= decay
        p["supply"] *= decay
        net = p["demand"] - p["supply"]

        target = 1 + net * d_sens if net >= 0 else 1 + net * s_sens
        target = max(lo, min(hi, target))

        current = _multipliers[chat_id].get(key, 1.0)
        diff = target - current
        step = max(-current * max_change, min(current * max_change, diff))
        new_mult = max(lo, min(hi, current + step))
        _multipliers[chat_id][key] = new_mult

        hist = _history[chat_id][key]
        hist.append(new_mult)
        if len(hist) > hist_len:
            del hist[0: len(hist) - hist_len]


def _ensure_ticked(chat_id: int) -> None:
    _ensure_init(chat_id)
    interval = getattr(config, "HOUSING_TICK_INTERVAL_SECONDS", 900)
    max_ticks = getattr(config, "HOUSING_MAX_TICKS_PER_ACCESS", 50)
    now = time.time()
    elapsed = now - _last_tick_ts[chat_id]
    ticks = int(elapsed // interval)
    if ticks <= 0:
        return
    ticks = min(ticks, max_ticks)
    for _ in range(ticks):
        _apply_one_tick(chat_id)
    _last_tick_ts[chat_id] += ticks * interval


# ---------------------------------------------------------------------------
# 📈 API عمومی
# ---------------------------------------------------------------------------

def record_purchase(chat_id: int, district_key: str, qty: int = 1) -> None:
    if district_key not in _districts() or qty <= 0:
        return
    _ensure_ticked(chat_id)
    _pressure[chat_id][district_key]["demand"] += float(qty)


def record_sale(chat_id: int, district_key: str, qty: int = 1) -> None:
    if district_key not in _districts() or qty <= 0:
        return
    _ensure_ticked(chat_id)
    _pressure[chat_id][district_key]["supply"] += float(qty)


def get_multiplier(chat_id: int, district_key: str) -> float:
    if district_key not in _districts():
        return 1.0
    _ensure_ticked(chat_id)
    return round(_multipliers[chat_id].get(district_key, 1.0), 4)


def _zone_label(mult: float) -> str:
    hot = getattr(config, "HOUSING_HOT_THRESHOLD", 1.15)
    dev = getattr(config, "HOUSING_DEVELOPING_THRESHOLD", 1.05)
    cold = getattr(config, "HOUSING_COLD_THRESHOLD", 0.90)
    if mult >= hot:
        return "🔥 داغ"
    if mult >= dev:
        return "📈 در حالِ توسعه"
    if mult <= cold:
        return "🧊 کم‌تقاضا"
    return "⚖️ عادی"


def get_status(chat_id: int, district_key: str) -> dict:
    _ensure_ticked(chat_id)
    mult = _multipliers[chat_id].get(district_key, 1.0)
    hist = _history[chat_id].get(district_key, [])
    if len(hist) >= 2:
        trend = "📈" if hist[-1] > hist[-2] else ("📉" if hist[-1] < hist[-2] else "➖")
    else:
        trend = "➖"
    info = _districts().get(district_key, {})
    return {
        "key": district_key, "name": info.get("name", district_key),
        "multiplier": round(mult, 3), "zone": _zone_label(mult), "trend": trend,
    }


def all_status(chat_id: int) -> dict[str, dict]:
    _ensure_ticked(chat_id)
    return {key: get_status(chat_id, key) for key in _districts()}


def housing_price_index(chat_id: int) -> float:
    """میانگینِ ساده‌ی مولتیپلایرِ همه‌ی مناطق، در مقیاسِ ۱۰۰ - ۱۰۰ یعنی
    کلِ بازارِ مسکنِ گروه رویِ تعادله."""
    _ensure_ticked(chat_id)
    mults = list(_multipliers[chat_id].values())
    if not mults:
        return 100.0
    return round(sum(mults) / len(mults) * 100, 2)


def format_housing_text(chat_id: int) -> str:
    statuses = all_status(chat_id)
    idx = housing_price_index(chat_id)
    lines = [f"🏘 بازارِ مسکن — شاخصِ کل: {idx}", "─────────────────"]
    if not statuses:
        lines.append("هنوز هیچ منطقه‌ای تعریف نشده.")
        return "\n".join(lines)
    for s in statuses.values():
        lines.append(f"{s['trend']} {s['name']}: ×{s['multiplier']} — {s['zone']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 📨 Command Handler
# ---------------------------------------------------------------------------

async def housing_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بازار مسکن / /housing"""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    await message.reply_text(format_housing_text(chat.id))


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "version": STATE_VERSION,
        "multipliers": {str(c): dict(v) for c, v in _multipliers.items()},
        "pressure": {str(c): {k: dict(v) for k, v in d.items()} for c, d in _pressure.items()},
        "history": {str(c): {k: list(v) for k, v in d.items()} for c, d in _history.items()},
        "last_tick_ts": {str(c): ts for c, ts in _last_tick_ts.items()},
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
            logger.warning(f"ذخیره‌ی state بازارِ مسکن ناموفق بود: {e}")


def load_state():
    data = None
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"خوندن state بازارِ مسکن ناموفق بود: {e}؛ تلاش برای بازیابی از بکاپ...")
            data = None
    if data is None:
        bak = STATE_FILE + ".bak"
        if os.path.exists(bak):
            try:
                with open(bak, encoding="utf-8") as f:
                    data = json.load(f)
                logger.warning("state اصلیِ بازارِ مسکن خراب/نامعتبر بود؛ از بکاپ بازیابی شد.")
            except Exception as e:
                logger.warning(f"خوندنِ بکاپِ بازارِ مسکن هم ناموفق بود: {e}")
                return
        else:
            return

    for cid_s, v in data.get("multipliers", {}).items():
        cid = int(cid_s)
        for key, mult in v.items():
            if key in _districts():
                _multipliers[cid][key] = float(mult)
    for cid_s, d in data.get("pressure", {}).items():
        cid = int(cid_s)
        for key, pv in d.items():
            if key in _districts():
                _pressure[cid][key] = {
                    "demand": float(pv.get("demand", 0.0)), "supply": float(pv.get("supply", 0.0)),
                }
    for cid_s, d in data.get("history", {}).items():
        cid = int(cid_s)
        for key, hist in d.items():
            if key in _districts():
                _history[cid][key] = [float(x) for x in hist]
    for cid_s, ts in data.get("last_tick_ts", {}).items():
        _last_tick_ts[int(cid_s)] = float(ts)

    logger.info("وضعیتِ بازارِ مسکن از فایل بارگذاری شد.")
