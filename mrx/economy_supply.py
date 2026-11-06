# -*- coding: utf-8 -*-
"""
DIGNANTI ECONOMY SUPPLY & DEMAND ENGINE — فاز ۲ (V18 Living Economy)
=====================================================================
بخش «عرضه و تقاضا»ی درخواستِ V18: یه موتورِ قیمت‌گذاریِ پویا برای کالاهای
پایه (COMMODITIES توی config.py) که قیمتِ هر کالا را واقعاً بر اساسِ رفتارِ
بازیکن‌ها (خرید/فروش/تولید/مصرف) تغییر می‌ده — نه یه Random Walk مستقلِ
بی‌ربط به بازیکن‌ها.

طرز کار:
  • هر خرید/مصرفِ یه کالا → record_demand(chat_id, key, qty) → فشارِ تقاضا
    بالا می‌ره → قیمت گرایش به افزایش پیدا می‌کنه.
  • هر فروش/تولید/عرضه‌ی یه کالا → record_supply(chat_id, key, qty) → فشارِ
    عرضه بالا می‌ره → قیمت گرایش به کاهش پیدا می‌کنه.
  • فشارها با گذشتِ زمان فروکش می‌کنن (Decay) تا اثرِ یه خریدِ قدیمی تا ابد
    روی قیمت نمونه و نوسان غیرمنطقی/تجمعیِ بی‌پایان رخ نده.
  • قیمت در هر Tick حداکثر COMMODITY_MAX_CHANGE_PER_TICK درصد به‌سمتِ
    «قیمتِ هدف» (بر اساسِ فشارِ خالصِ فعلی) حرکت می‌کنه، و همیشه بینِ
    min_price/max_price کلمپ می‌شه — دقیقاً طبقِ قانونِ «جلوگیری از
    نوسانات غیرمنطقی».
  • مثلِ بازار (فاز ۳)، Tick به‌صورت Lazy محاسبه می‌شه: نیازی به Task
    پس‌زمینه‌ی جدا نیست؛ هر وقت کسی به یه کالا سر بزنه، بر اساسِ زمانِ
    سپری‌شده Tickهای لازم اعمال می‌شن.

این ماژول هیچ‌وقت مستقیم پول جابه‌جا نمی‌کنه — فقط «قیمت» برمی‌گردونه.
جابه‌جاییِ واقعیِ پول (خرید/فروشِ کالا توسطِ فروشگاه/کسب‌وکار) همیشه باید از
economy_core عبور کنه؛ این ماژول فقط قیمتِ لحظه‌ای رو به اون فراخوان‌ها می‌ده.

یکپارچگی با فاز‌های بعدی (طبقِ نقشه‌ی V18): کارخانه‌ها/کسب‌وکارها (بخشِ ۵)
با خریدِ موادِ اولیه record_demand و با تولید/فروش record_supply صدا می‌زنن؛
این وظیفه‌ی ارتقای economy_business.py در فازِ بعدیه و هنوز سیمِ آن کشیده
نشده — ولی خودِ موتور الان کاملاً واقعی و تست‌شده کار می‌کنه.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from collections import defaultdict

import config

logger = logging.getLogger("economy_supply")

STATE_FILE = getattr(config, "ECONOMY_SUPPLY_STATE_FILE", "economy_supply_state.json")
STATE_VERSION = 1

COMMODITIES: dict = getattr(config, "COMMODITIES", {})

_save_lock = asyncio.Lock()


class UnknownCommodityError(KeyError):
    pass


# ---------------------------------------------------------------------------
# 💾 State
# ---------------------------------------------------------------------------

# chat_id -> commodity_key -> قیمتِ فعلی
_prices: dict[int, dict] = defaultdict(dict)

# chat_id -> commodity_key -> {"demand": float, "supply": float}  (فشارهای در حالِ فروکش)
_pressure: dict[int, dict] = defaultdict(lambda: defaultdict(lambda: {"demand": 0.0, "supply": 0.0}))

# chat_id -> commodity_key -> [price, price, ...]  (تاریخچه برای نمودار/روند)
_history: dict[int, dict] = defaultdict(lambda: defaultdict(list))

# chat_id -> آخرین زمانی که Tick اعمال شد
_last_tick_ts: dict[int, float] = defaultdict(float)

# chat_id -> commodity_key -> مجموعِ تجمعیِ واحدهای خریداری/عرضه‌شده (برای گزارش/آمار)
_total_demand: dict[int, dict] = defaultdict(lambda: defaultdict(int))
_total_supply: dict[int, dict] = defaultdict(lambda: defaultdict(int))


def _ensure_prices(chat_id: int) -> None:
    if _prices[chat_id]:
        return
    for key, info in COMMODITIES.items():
        base = float(info["base_price"])
        _prices[chat_id][key] = base
        _history[chat_id][key] = [base]
    _last_tick_ts[chat_id] = time.time()


# ---------------------------------------------------------------------------
# ⏱ Lazy Tick — قیمت‌ها را بر اساسِ فشارِ عرضه/تقاضای در حالِ فروکش آپدیت می‌کنه
# ---------------------------------------------------------------------------

def _apply_one_tick(chat_id: int) -> None:
    decay = getattr(config, "COMMODITY_PRESSURE_DECAY_PER_TICK", 0.90)
    max_change = getattr(config, "COMMODITY_MAX_CHANGE_PER_TICK", 0.06)
    hist_len = getattr(config, "COMMODITY_PRICE_HISTORY_LEN", 40)

    for key, info in COMMODITIES.items():
        p = _pressure[chat_id][key]
        p["demand"] *= decay
        p["supply"] *= decay
        net = p["demand"] - p["supply"]

        base = float(info["base_price"])
        if net >= 0:
            target = base * (1 + net * float(info.get("demand_sensitivity", 0.015)))
        else:
            target = base * (1 + net * float(info.get("supply_sensitivity", 0.012)))

        current = _prices[chat_id].get(key, base)
        diff = target - current
        step = max(-current * max_change, min(current * max_change, diff))
        new_price = current + step
        new_price = max(float(info["min_price"]), min(float(info["max_price"]), new_price))
        _prices[chat_id][key] = new_price

        hist = _history[chat_id][key]
        hist.append(new_price)
        if len(hist) > hist_len:
            del hist[0: len(hist) - hist_len]


def _ensure_ticked(chat_id: int) -> None:
    _ensure_prices(chat_id)
    interval = getattr(config, "COMMODITY_TICK_INTERVAL_SECONDS", 600)
    max_ticks = getattr(config, "COMMODITY_MAX_TICKS_PER_ACCESS", 50)
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
# 📈 عرضه و تقاضا — API عمومی
# ---------------------------------------------------------------------------

def record_demand(chat_id: int, key: str, qty: float = 1.0) -> None:
    """وقتی کاربر/کسب‌وکاری یه کالا رو خرید یا مصرف کرد صدا بزن. qty باید > 0 باشه."""
    if key not in COMMODITIES:
        raise UnknownCommodityError(f"کالای ناشناخته: {key}")
    if qty <= 0:
        return
    _ensure_ticked(chat_id)
    _pressure[chat_id][key]["demand"] += float(qty)
    _total_demand[chat_id][key] += qty


def record_supply(chat_id: int, key: str, qty: float = 1.0) -> None:
    """وقتی یه کالا تولید/عرضه/فروخته (توسطِ فروشنده) شد صدا بزن. qty باید > 0 باشه."""
    if key not in COMMODITIES:
        raise UnknownCommodityError(f"کالای ناشناخته: {key}")
    if qty <= 0:
        return
    _ensure_ticked(chat_id)
    _pressure[chat_id][key]["supply"] += float(qty)
    _total_supply[chat_id][key] += qty


def get_price(chat_id: int, key: str) -> float:
    if key not in COMMODITIES:
        raise UnknownCommodityError(f"کالای ناشناخته: {key}")
    _ensure_ticked(chat_id)
    return round(_prices[chat_id][key], 2)


def is_shortage(chat_id: int, key: str) -> bool:
    if key not in COMMODITIES:
        raise UnknownCommodityError(f"کالای ناشناخته: {key}")
    _ensure_ticked(chat_id)
    threshold = getattr(config, "COMMODITY_SHORTAGE_PRESSURE_THRESHOLD", 6.0)
    p = _pressure[chat_id][key]
    return (p["demand"] - p["supply"]) >= threshold


def get_status(chat_id: int, key: str) -> dict:
    if key not in COMMODITIES:
        raise UnknownCommodityError(f"کالای ناشناخته: {key}")
    _ensure_ticked(chat_id)
    info = COMMODITIES[key]
    price = _prices[chat_id][key]
    base = float(info["base_price"])
    change_pct = ((price - base) / base) * 100 if base else 0.0
    hist = _history[chat_id][key]
    if len(hist) >= 2:
        trend = "📈" if hist[-1] > hist[-2] else ("📉" if hist[-1] < hist[-2] else "➖")
    else:
        trend = "➖"
    p = _pressure[chat_id][key]
    return {
        "key": key,
        "name": info.get("name", key),
        "price": round(price, 2),
        "base_price": base,
        "min_price": info["min_price"],
        "max_price": info["max_price"],
        "change_pct": round(change_pct, 1),
        "trend": trend,
        "shortage": (p["demand"] - p["supply"]) >= getattr(config, "COMMODITY_SHORTAGE_PRESSURE_THRESHOLD", 6.0),
        "demand_pressure": round(p["demand"], 2),
        "supply_pressure": round(p["supply"], 2),
    }


def all_status(chat_id: int) -> dict[str, dict]:
    _ensure_ticked(chat_id)
    return {key: get_status(chat_id, key) for key in COMMODITIES}


def price_history(chat_id: int, key: str, limit: int = 20) -> list[float]:
    if key not in COMMODITIES:
        raise UnknownCommodityError(f"کالای ناشناخته: {key}")
    _ensure_ticked(chat_id)
    return list(_history[chat_id].get(key, []))[-limit:]


def price_index(chat_id: int) -> float:
    """شاخصِ قیمتِ کلی (CPI ساده): میانگینِ نسبتِ قیمتِ فعلی به قیمتِ پایه‌ی
    همه‌ی کالاها، در مقیاسِ ۱۰۰. اگه ۱۰۰ باشه یعنی اقتصاد دقیقاً سرِ
    قیمت‌های پایه‌ست. از این تابع economy_macro برای محاسبه‌ی تورمِ واقعی
    (نه ساختگی) استفاده می‌کنه."""
    _ensure_ticked(chat_id)
    if not COMMODITIES:
        return 100.0
    ratios = []
    for key, info in COMMODITIES.items():
        base = float(info["base_price"])
        if base:
            ratios.append(_prices[chat_id][key] / base)
    if not ratios:
        return 100.0
    return round(sum(ratios) / len(ratios) * 100, 2)


# ---------------------------------------------------------------------------
# 🖊 نمایش متنی (برای پنل)
# ---------------------------------------------------------------------------

def format_commodities_text(chat_id: int) -> str:
    statuses = all_status(chat_id)
    lines = [
        "📦 بازار کالا — عرضه و تقاضای زنده",
        "─────────────────",
        "هر خرید قیمت رو بالا می‌بره، هر فروش/تولید قیمت رو پایین می‌آره.",
        "",
    ]
    for status in statuses.values():
        shortage_tag = " ⚠️ کمبود" if status["shortage"] else ""
        sign = "+" if status["change_pct"] >= 0 else ""
        lines.append(
            f"{status['trend']} {status['name']}: {status['price']:,.0f} "
            f"({sign}{status['change_pct']}%){shortage_tag}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 💾 Persistence (همون الگوی fsync + backup که همه‌ی ماژول‌های دیگه دارن)
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "version": STATE_VERSION,
        "prices": {str(c): dict(v) for c, v in _prices.items()},
        "pressure": {str(c): {k: dict(v) for k, v in d.items()} for c, d in _pressure.items()},
        "history": {str(c): {k: list(v) for k, v in d.items()} for c, d in _history.items()},
        "last_tick_ts": {str(c): ts for c, ts in _last_tick_ts.items()},
        "total_demand": {str(c): dict(v) for c, v in _total_demand.items()},
        "total_supply": {str(c): dict(v) for c, v in _total_supply.items()},
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
            logger.warning(f"ذخیره‌ی state موتور Supply ناموفق بود: {e}")


def load_state():
    data = None
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"خوندن state موتور Supply ناموفق بود: {e}؛ تلاش برای بازیابی از بکاپ...")
            data = None
    if data is None:
        bak = STATE_FILE + ".bak"
        if os.path.exists(bak):
            try:
                with open(bak, encoding="utf-8") as f:
                    data = json.load(f)
                logger.warning("state اصلیِ Supply خراب/نامعتبر بود؛ از بکاپ بازیابی شد.")
            except Exception as e:
                logger.warning(f"خوندن بکاپِ Supply هم ناموفق بود: {e}")
                return
        else:
            return

    for cid_s, v in data.get("prices", {}).items():
        cid = int(cid_s)
        for key, price in v.items():
            if key in COMMODITIES:
                _prices[cid][key] = float(price)

    for cid_s, d in data.get("pressure", {}).items():
        cid = int(cid_s)
        for key, pv in d.items():
            if key in COMMODITIES:
                _pressure[cid][key] = {
                    "demand": float(pv.get("demand", 0.0)),
                    "supply": float(pv.get("supply", 0.0)),
                }

    for cid_s, d in data.get("history", {}).items():
        cid = int(cid_s)
        for key, hist in d.items():
            if key in COMMODITIES:
                _history[cid][key] = [float(x) for x in hist]

    for cid_s, ts in data.get("last_tick_ts", {}).items():
        _last_tick_ts[int(cid_s)] = float(ts)

    for cid_s, v in data.get("total_demand", {}).items():
        cid = int(cid_s)
        for key, qty in v.items():
            _total_demand[cid][key] = qty

    for cid_s, v in data.get("total_supply", {}).items():
        cid = int(cid_s)
        for key, qty in v.items():
            _total_supply[cid][key] = qty

    logger.info("وضعیت موتور Supply از فایل بارگذاری شد.")
