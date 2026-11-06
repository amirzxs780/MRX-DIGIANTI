# -*- coding: utf-8 -*-
"""
DIGNANTI ECONOMY MACRO ENGINE — فاز ۲ (V18 Living Economy Engine)
=====================================================================
بخشِ «شاخص‌های کلانِ اقتصادی» و «تورم/رکود» از درخواستِ V18. همه‌چیزِ اینجا
از روی داده‌ی واقعیِ ثبت‌شده محاسبه می‌شه — نه عددِ تصادفی/ساختگی:

  • عرضه‌ی پول (Money Supply) → مستقیم از host._wallet + economy_core._bank
  • GDP دوره‌ای → دلتای تجمعیِ انواعِ تراکنشِ «تولید/درآمدِ واقعی»
    (MACRO_GDP_TRANSACTION_TYPES در config.py) که خودِ economy_core
    ._source_totals (از طریقِ record_transaction) جمع می‌زنه — یعنی کارِ
    واقعی (work/salary)، سودِ کسب‌وکار (BUSINESS/SALE)، و درآمدِ ملک/شهر
    (property_income/city_income). عمداً جایزه‌های صرف (REWARD/
    mission_reward/daily) و شرط‌بندی/ترید (Zero-Sum) جزوِ GDP نیستن - این
    جداسازی دقیقاً همون چیزیه که سیگنالِ تورم رو معنادار می‌کنه (پایین‌تر).
  • نرخِ تورم → ترکیبِ (۱) تغییرِ شاخصِ قیمتِ کالاهای واقعی (CPI از
    economy_supply.price_index) و (۲) عدمِ تعادلِ رشدِ عرضه‌ی پول نسبت به
    رشدِ تولیدِ واقعی (اگه پول سریع‌تر از تولید رشد کنه، فشارِ تورمیِ
    واقعیه). فرمول کاملاً قابلِ‌توضیحه، نه تصادفی.
  • نرخِ بیکاری → نسبتِ اعضای شناخته‌شده‌ی گروه (host._known_members) که
    فعلاً economy_jobs.current_job_key ندارن.
  • شاخصِ رونق/اعتمادِ مصرف‌کننده → ترکیبِ وزن‌دارِ رشدِ GDP، اشتغال و
    پایداریِ تورم؛ هر جزء بینِ ۰ تا ۱۰۰ و فرمولش توضیح داده شده.

Snapshotها به‌صورتِ Lazy (مثلِ بازار/کالا) هر MACRO_SNAPSHOT_INTERVAL_MINUTES
دقیقه‌ی واقعی یه‌بار ثبت می‌شن؛ نیازی به Task پس‌زمینه‌ی جدا نیست — هر وقت
کسی داشبورد رو باز کنه (یا force_snapshot صدا زده بشه) چک می‌شه.

این ماژول فقط می‌خونه و گزارش می‌ده — هیچ پولی جابه‌جا نمی‌کنه، هیچ‌جا
host._wallet/economy_core._bank رو نمی‌نویسه.

نکته‌ی صداقتِ اسکوپ (طبقِ فازبندیِ توافق‌شده): این فاز فقط «موتورِ هسته»
رو می‌سازه (عرضه/تقاضا + کلانِ اقتصاد). سیم‌کشیِ کامل به کسب‌وکار/کارخانه/
بورسِ وابسته‌به‌سودآوریِ شرکت‌ها (بخش‌های ۴ و ۵ نقشه‌ی V18) و پنلِ سیاستِ
مالیاتیِ ادمین (بخش ۱۶) در فازهای بعدی انجام می‌شه؛ اینجا ادعای انجامِ
اون‌ها نمی‌شه.
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
import economy_core

logger = logging.getLogger("economy_macro")

STATE_FILE = getattr(config, "ECONOMY_MACRO_STATE_FILE", "economy_macro_state.json")
STATE_VERSION = 1

_save_lock = asyncio.Lock()


def _host():
    import bot as host
    return host


# ---------------------------------------------------------------------------
# 💾 State
# ---------------------------------------------------------------------------

# chat_id -> [snapshot dict, ...]  (قدیمی‌ترین اول)
_history: dict[int, list] = defaultdict(list)

# chat_id -> آخرین زمانی که Snapshot ثبت شد
_last_snapshot_ts: dict[int, float] = defaultdict(float)

# مقادیرِ تجمعیِ «لحظه‌ی آخرین Snapshot» — برای محاسبه‌ی دلتای دوره‌ای
_last_cumulative_gdp_total: dict[int, float] = defaultdict(float)
_last_money_supply: dict[int, float] = defaultdict(float)
_last_price_index: dict[int, float] = defaultdict(lambda: 100.0)


# ---------------------------------------------------------------------------
# 📥 ورودی‌های خام (از ماژول‌های دیگه، فقط خواندنی)
# ---------------------------------------------------------------------------

def _money_supply(chat_id: int) -> float:
    host = _host()
    wallet_total = sum(host._wallet.get(chat_id, {}).values())
    bank_total = sum(economy_core._bank.get(chat_id, {}).values())
    return float(wallet_total + bank_total)


def _population(chat_id: int) -> int:
    host = _host()
    members = host._known_members.get(chat_id, set())
    return len(members)


def _employed_count(chat_id: int) -> int:
    try:
        import economy_jobs
    except Exception:
        return 0
    host = _host()
    members = host._known_members.get(chat_id, set())
    count = 0
    for uid in members:
        try:
            if economy_jobs.current_job_key(chat_id, uid):
                count += 1
        except Exception:
            continue
    return count


def _cumulative_gdp_sources(chat_id: int) -> float:
    """مجموعِ تجمعیِ همه‌ی انواعِ تراکنشی که «تولید/درآمدِ واقعی» محسوب می‌شن،
    از ابتدای عمرِ اقتصادِ گروه. از economy_core._source_totals می‌خونه که
    خودِ record_transaction آپدیتش می‌کنه — هیچ عددی اینجا اختراع نمی‌شه."""
    sources = economy_core._source_totals.get(chat_id, {})
    gdp_types = getattr(config, "MACRO_GDP_TRANSACTION_TYPES", set())
    return float(sum(amount for kind, amount in sources.items() if kind in gdp_types))


def _commodity_price_index(chat_id: int) -> float:
    try:
        import economy_supply
        return economy_supply.price_index(chat_id)
    except Exception:
        return 100.0


# ---------------------------------------------------------------------------
# 🧮 محاسبه‌ی شاخص‌ها
# ---------------------------------------------------------------------------

def _compute_live(chat_id: int) -> dict:
    cumulative_gdp = _cumulative_gdp_sources(chat_id)
    money_supply = _money_supply(chat_id)
    price_idx = _commodity_price_index(chat_id)

    prev_gdp = _last_cumulative_gdp_total.get(chat_id, 0.0)
    prev_money = _last_money_supply.get(chat_id, money_supply)
    prev_price_idx = _last_price_index.get(chat_id, 100.0)

    period_gdp = max(0.0, cumulative_gdp - prev_gdp)
    if prev_gdp > 0:
        gdp_growth = (cumulative_gdp - prev_gdp) / prev_gdp
    else:
        gdp_growth = 1.0 if cumulative_gdp > 0 else 0.0

    money_growth = (money_supply - prev_money) / prev_money if prev_money > 0 else 0.0
    cpi_change = (price_idx - prev_price_idx) / prev_price_idx if prev_price_idx > 0 else 0.0

    # نرخِ تورمِ ترکیبی: ۶۰٪ وزن از تغییرِ CPI واقعی (economy_supply)،
    # ۴۰٪ وزن از عدمِ تعادلِ رشدِ عرضه‌ی پول نسبت به رشدِ تولیدِ واقعی.
    money_vs_gdp_pressure = money_growth - gdp_growth
    inflation_rate = round(0.6 * cpi_change + 0.4 * money_vs_gdp_pressure, 4)

    population = _population(chat_id)
    employed = _employed_count(chat_id)
    unemployment_rate = round(1 - (employed / population), 4) if population > 0 else 0.0

    avg_income = round(period_gdp / employed, 2) if employed > 0 else 0.0
    avg_wealth = round(money_supply / population, 2) if population > 0 else 0.0

    volume = economy_core.get_ledger_volume(chat_id)

    boom_th = getattr(config, "MACRO_BOOM_GROWTH_THRESHOLD", 0.12)
    rec_th = getattr(config, "MACRO_RECESSION_GROWTH_THRESHOLD", -0.08)
    if gdp_growth >= boom_th:
        status = "رونق"
    elif gdp_growth <= rec_th:
        status = "رکود"
    else:
        status = "عادی"

    target_inflation = getattr(config, "MACRO_TARGET_INFLATION_RATE", 0.02)
    inflation_gap = abs(inflation_rate - target_inflation)

    growth_score = max(0.0, min(100.0, 50 + gdp_growth * 250))
    employment_score = max(0.0, min(100.0, (1 - unemployment_rate) * 100))
    stability_score = max(0.0, min(100.0, 100 - inflation_gap * 1000))
    prosperity_index = round((growth_score + employment_score + stability_score) / 3, 1)

    wealth_trend_score = max(0.0, min(100.0, 50 + money_growth * 200))
    confidence_index = round((wealth_trend_score + employment_score + stability_score) / 3, 1)

    return {
        "timestamp": time.time(),
        "money_supply": round(money_supply, 2),
        "period_gdp": round(period_gdp, 2),
        "gdp_growth": round(gdp_growth, 4),
        "inflation_rate": inflation_rate,
        "price_index": round(price_idx, 2),
        "unemployment_rate": unemployment_rate,
        "population": population,
        "employed": employed,
        "avg_income": avg_income,
        "avg_wealth": avg_wealth,
        "transaction_volume": volume,
        "prosperity_index": prosperity_index,
        "consumer_confidence_index": confidence_index,
        "status": status,
    }


def maybe_snapshot(chat_id: int) -> dict | None:
    """اگه از آخرین Snapshot به‌اندازه‌ی کافی گذشته باشه، یه Snapshot تازه
    ثبت می‌کنه و برمی‌گردونه؛ وگرنه None (فقط Lazy‌ـه، هزینه‌ی اضافه نداره)."""
    interval = getattr(config, "MACRO_SNAPSHOT_INTERVAL_MINUTES", 60) * 60
    now = time.time()
    if _history.get(chat_id) and (now - _last_snapshot_ts.get(chat_id, 0.0)) < interval:
        return None

    snap = _compute_live(chat_id)
    _history[chat_id].append(snap)
    max_len = getattr(config, "MACRO_MAX_SNAPSHOTS", 200)
    if len(_history[chat_id]) > max_len:
        del _history[chat_id][0: len(_history[chat_id]) - max_len]

    _last_cumulative_gdp_total[chat_id] = _cumulative_gdp_sources(chat_id)
    _last_money_supply[chat_id] = snap["money_supply"]
    _last_price_index[chat_id] = snap["price_index"]
    _last_snapshot_ts[chat_id] = now
    return snap


def force_snapshot(chat_id: int) -> dict:
    """صرف‌نظر از فاصله‌ی زمانی، همین الان یه Snapshot جدید می‌سازه (برای
    ادمین/تست). رشدِ GDP/پول نسبت به آخرین Snapshotِ موجود محاسبه می‌شه."""
    _last_snapshot_ts[chat_id] = 0.0
    return maybe_snapshot(chat_id)


def get_snapshot(chat_id: int) -> dict:
    """آخرین Snapshot رو برمی‌گردونه (در صورتِ لزوم اول یکی تازه می‌سازه)."""
    maybe_snapshot(chat_id)
    if _history.get(chat_id):
        return _history[chat_id][-1]
    return _compute_live(chat_id)


def get_history(chat_id: int, limit: int = 20) -> list[dict]:
    return list(_history.get(chat_id, []))[-limit:]


# ---------------------------------------------------------------------------
# 🖊 نمایش متنی (برای پنل/ادمین)
# ---------------------------------------------------------------------------

def _fmt(n) -> str:
    try:
        return f"{float(n):,.0f}"
    except Exception:
        return str(n)


def _pct(n) -> str:
    try:
        return f"{float(n) * 100:+.1f}٪"
    except Exception:
        return str(n)


def format_dashboard_text(chat_id: int) -> str:
    snap = get_snapshot(chat_id)
    status_emoji = {"رونق": "🔥", "رکود": "📉", "عادی": "⚖️"}.get(snap["status"], "⚖️")
    lines = [
        "🌍 داشبورد اقتصاد کلان گروه",
        "─────────────────",
        f"{status_emoji} وضعیتِ کلی: {snap['status']}",
        "",
        f"💰 حجمِ کلِ پول در گردش: {_fmt(snap['money_supply'])} {config.CURRENCY_NAME}",
        f"📦 تولید ناخالصِ این دوره (GDP): {_fmt(snap['period_gdp'])} — رشد: {_pct(snap['gdp_growth'])}",
        f"📈 نرخِ تورم: {_pct(snap['inflation_rate'])}  (شاخصِ قیمتِ کالا: {snap['price_index']})",
        f"👥 جمعیتِ فعال: {snap['population']} — شاغل: {snap['employed']} — نرخِ بیکاری: {_pct(snap['unemployment_rate'])}",
        f"💵 میانگینِ درآمدِ دوره‌ای: {_fmt(snap['avg_income'])} — میانگینِ ثروت: {_fmt(snap['avg_wealth'])}",
        f"🧾 حجمِ کلِ تراکنش‌ها: {snap['transaction_volume']:,}",
        "",
        f"🌡 شاخصِ رونقِ اقتصادی: {snap['prosperity_index']}/۱۰۰",
        f"🙂 شاخصِ اعتمادِ مصرف‌کننده: {snap['consumer_confidence_index']}/۱۰۰",
    ]
    try:
        import economy_supply
        statuses = economy_supply.all_status(chat_id)
        shortages = [s["name"] for s in statuses.values() if s["shortage"]]
        if shortages:
            lines.append("")
            lines.append("⚠️ کمبودِ کالا: " + "، ".join(shortages))
    except Exception:
        pass
    lines.append("")
    lines.append(
        "این شاخص‌ها از روی تراکنش‌های واقعیِ ثبت‌شده‌ی گروه محاسبه می‌شن؛ "
        "هرچی فعالیتِ اقتصادیِ بیشتری باشه، دقیق‌تر می‌شن."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 💾 Persistence (همون الگوی fsync + backup مشترکِ پروژه)
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "version": STATE_VERSION,
        "history": {str(c): v for c, v in _history.items()},
        "last_snapshot_ts": {str(c): ts for c, ts in _last_snapshot_ts.items()},
        "last_cumulative_gdp_total": {str(c): v for c, v in _last_cumulative_gdp_total.items()},
        "last_money_supply": {str(c): v for c, v in _last_money_supply.items()},
        "last_price_index": {str(c): v for c, v in _last_price_index.items()},
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
            logger.warning(f"ذخیره‌ی state موتور Macro ناموفق بود: {e}")


def load_state():
    data = None
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"خوندن state موتور Macro ناموفق بود: {e}؛ تلاش برای بازیابی از بکاپ...")
            data = None
    if data is None:
        bak = STATE_FILE + ".bak"
        if os.path.exists(bak):
            try:
                with open(bak, encoding="utf-8") as f:
                    data = json.load(f)
                logger.warning("state اصلیِ Macro خراب/نامعتبر بود؛ از بکاپ بازیابی شد.")
            except Exception as e:
                logger.warning(f"خوندنِ بکاپِ Macro هم ناموفق بود: {e}")
                return
        else:
            return

    for cid_s, snaps in data.get("history", {}).items():
        _history[int(cid_s)] = list(snaps)
    for cid_s, ts in data.get("last_snapshot_ts", {}).items():
        _last_snapshot_ts[int(cid_s)] = float(ts)
    for cid_s, v in data.get("last_cumulative_gdp_total", {}).items():
        _last_cumulative_gdp_total[int(cid_s)] = float(v)
    for cid_s, v in data.get("last_money_supply", {}).items():
        _last_money_supply[int(cid_s)] = float(v)
    for cid_s, v in data.get("last_price_index", {}).items():
        _last_price_index[int(cid_s)] = float(v)

    logger.info("وضعیت موتور Macro از فایل بارگذاری شد.")
