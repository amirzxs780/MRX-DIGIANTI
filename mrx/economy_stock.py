# -*- coding: utf-8 -*-
"""
DIGNANTI ECONOMY STOCK EXCHANGE — فاز ۳ (V18 Living Economy: بازار سرمایه)
=====================================================================
بخشِ «بازار سرمایه و بورسِ پیشرفته»ی نقشه‌ی V18 (سهامِ شرکت‌های مجازی).
برخلافِ economy_market.py (دارایی‌های انتزاعی مثلِ BTC/GOLD با Random Walk)،
این ماژول سهامِ شرکت‌های *واقعیِ* بازیکن‌ها (کسب‌وکارهای economy_business.py)
رو قابلِ معامله می‌کنه.

منطقِ قیمت‌گذاری (دقیقاً طبقِ خواسته‌ی «هیچ قیمتِ تصادفی و بی‌منطقِ اقتصادی
تولید نکن»):
  1) عرضه‌ی اولیه (IPO): قیمتِ هر سهم = ارزشِ واقعیِ کسب‌وکار
     (economy_business.current_value) ÷ تعدادِ کلِ سهام.
  2) معامله‌ی ثانویه: مثلِ economy_market، خرید/فروش با مدلِ Impact-per-Trade
     (معامله‌ی بزرگ‌تر = اثرِ بیشتر، با سقفِ ضدِ دستکاری) قیمت رو جابه‌جا می‌کنه.
  3) بازگشتِ تدریجی به ارزشِ بنیادی (Reprice به‌صورتِ Lazy): هر
     STOCK_REPRICE_INTERVAL_SECONDS، قیمت به‌سمتِ ارزشِ واقعیِ *فعلیِ*
     کسب‌وکار (که از سودآوری/سطح/موجودی واقعی‌اش میاد) گرایش پیدا می‌کنه —
     یعنی اگه کسب‌وکار واقعاً سودآور بشه، سهامش هم واقعاً گرون می‌شه.

مثلِ همه‌ی ماژول‌های این پروژه، اینجا هم مستقیم wallet/bank دست‌کاری نمی‌شه؛
هر جابه‌جاییِ پول از economy_core.add_coins/remove_coins عبور می‌کنه.

جابه‌جاییِ پول (هم‌راستا با مدلِ موجودِ economy_market — نقدینگیِ انتزاعی،
نه Order-Book واقعی بینِ کاربرها):
  • خرید از بازارِ ثانویه → پول از خریدار کم می‌شه (Sink).
  • فروش در بازارِ ثانویه → پول به فروشنده اضافه می‌شه (Source).
  • IPO یه استثناست: پولِ سهامِ عرضه‌شده (treasury) به‌عنوانِ «سرمایه‌ی تازه»
    مستقیم به مالکِ شرکت واریز می‌شه - این دقیقاً معنایِ واقعیِ IPO است
    (شرکت از بازار سرمایه جمع می‌کنه).
  • تقسیمِ سود (Dividend): از کیفِ‌پولِ مالک کم و متناسب با سهمِ هر
    سهامدار (شاملِ خودِ مالک) پخش می‌شه.

وقتی کسب‌وکارِ زیرینِ یه شرکت فروخته/حذف بشه، شرکت در اولین دسترسیِ بعدی
به‌صورتِ خودکار Delist می‌شه (نیازی به تغییرِ economy_business.py نبود).
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
import economy_core

logger = logging.getLogger("economy_stock")

STATE_FILE = getattr(config, "ECONOMY_STOCK_STATE_FILE", "economy_stock_state.json")
STATE_VERSION = 1

_save_lock = asyncio.Lock()


def _host():
    import bot as host
    return host


class StockError(Exception):
    pass


# ---------------------------------------------------------------------------
# 💾 State
# ---------------------------------------------------------------------------

# chat_id -> company_id(int) -> {id, chat_id, owner_uid, business_id, business_type,
#   name, total_shares, share_price, ipo_price, ipo_ts, last_reprice_ts,
#   treasury_shares, dividends_paid_total, last_dividend_ts, delisted}
_companies: dict[int, dict] = defaultdict(dict)
_next_company_id: dict[int, int] = defaultdict(int)

# chat_id -> company_id -> uid -> تعداد سهم
_holdings: dict[int, dict] = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))

# chat_id -> company_id -> [price, price, ...]
_history: dict[int, dict] = defaultdict(lambda: defaultdict(list))


# ---------------------------------------------------------------------------
# 🏭 اتصال به کسب‌وکارِ واقعی
# ---------------------------------------------------------------------------

def _owner_business(chat_id: int, owner_uid: int, business_id: int):
    import economy_business
    return next((b for b in economy_business.owned(chat_id, owner_uid) if b["id"] == business_id), None)


def list_companies(chat_id: int) -> list[dict]:
    _ensure_all_repriced(chat_id)
    return [c for c in _companies[chat_id].values() if not c["delisted"]]


def get_company(chat_id: int, company_id: int) -> dict | None:
    return _companies[chat_id].get(company_id)


def is_business_public(chat_id: int, business_id: int) -> bool:
    return any(
        c["business_id"] == business_id and not c["delisted"]
        for c in _companies[chat_id].values()
    )


def eligible_for_ipo(chat_id: int, uid: int, business_id: int) -> tuple[bool, str]:
    import economy_business
    b = _owner_business(chat_id, uid, business_id)
    if not b:
        return False, "این کسب‌وکار پیدا نشد (مطمئن شو شناسه‌ی درست رو با «کسب‌وکار من» چک کردی)."
    if is_business_public(chat_id, business_id):
        return False, "این کسب‌وکار از قبل توی بورس لیست شده."
    value = economy_business.current_value(b)
    min_value = getattr(config, "STOCK_IPO_MIN_VALUE", 5000)
    if value < min_value:
        return False, f"ارزشِ کسب‌وکار باید حداقل {min_value:,} {config.CURRENCY_NAME} باشه (الان: {value:,})."
    return True, ""


async def go_public(chat_id: int, uid: int, business_id: int) -> dict:
    """عرضه‌ی اولیه (IPO). سرمایه‌ی حاصل از سهامِ عرضه‌شده مستقیم به مالک واریز می‌شه."""
    ok, reason = eligible_for_ipo(chat_id, uid, business_id)
    if not ok:
        raise StockError(reason)

    import economy_business
    b = _owner_business(chat_id, uid, business_id)
    value = economy_business.current_value(b)
    total_shares = getattr(config, "STOCK_TOTAL_SHARES", 1000)
    share_price = value / total_shares
    founder_frac = getattr(config, "STOCK_FOUNDER_RETAIN_FRACTION", 0.65)
    founder_shares = round(total_shares * founder_frac)
    treasury_shares = total_shares - founder_shares

    company_id = _next_company_id[chat_id] + 1
    _next_company_id[chat_id] = company_id

    company = {
        "id": company_id,
        "chat_id": chat_id,
        "owner_uid": uid,
        "business_id": business_id,
        "business_type": b["type"],
        "name": f"{economy_business.TYPES[b['type']]['name']} #{business_id}",
        "total_shares": total_shares,
        "share_price": round(share_price, 2),
        "ipo_price": round(share_price, 2),
        "ipo_ts": time.time(),
        "last_reprice_ts": time.time(),
        "treasury_shares": treasury_shares,
        "dividends_paid_total": 0,
        "last_dividend_ts": 0.0,  # هنوز سودی تقسیم نشده - اولین بار نباید منتظرِ Cooldown بمونه
        "delisted": False,
    }
    _companies[chat_id][company_id] = company
    _holdings[chat_id][company_id][uid] = float(founder_shares)
    _history[chat_id][company_id] = [company["share_price"]]

    raise_amount = round(treasury_shares * share_price)
    if raise_amount > 0:
        await economy_core.add_coins(
            chat_id, uid, raise_amount, kind="stock_ipo",
            note=f"سرمایه‌ی IPO شرکت #{company_id} ({company['name']})",
        )
    return company


# ---------------------------------------------------------------------------
# 💹 بازارِ ثانویه — خرید/فروش (مدلِ Impact-per-Trade، هم‌راستا با economy_market)
# ---------------------------------------------------------------------------

def _liquidity_of(chat_id: int, company_id: int) -> float:
    c = _companies[chat_id][company_id]
    return c["share_price"] * c["total_shares"] * getattr(config, "STOCK_LIQUIDITY_MULT", 8)


def _apply_price_impact(chat_id: int, company_id: int, trade_value: float, direction: int) -> None:
    c = _companies[chat_id][company_id]
    if trade_value <= 0:
        return
    liquidity = _liquidity_of(chat_id, company_id)
    if liquidity <= 0:
        return
    impact = min(trade_value / liquidity, getattr(config, "STOCK_MAX_IMPACT_PER_TRADE", 0.10))
    factor = 1 + (impact * direction)
    floor = max(0.01, c["ipo_price"] * 0.1)
    c["share_price"] = max(floor, c["share_price"] * factor)
    _append_history(chat_id, company_id, c["share_price"])


def _append_history(chat_id: int, company_id: int, price: float) -> None:
    hist = _history[chat_id][company_id]
    hist.append(price)
    hist_len = getattr(config, "STOCK_PRICE_HISTORY_LEN", 30)
    if len(hist) > hist_len:
        del hist[0: len(hist) - hist_len]


async def buy_shares(chat_id: int, uid: int, company_id: int, qty: int) -> dict:
    c = _companies[chat_id].get(company_id)
    if not c or c["delisted"]:
        raise StockError("این شرکت توی بورس نیست یا از لیست خارج شده.")
    if qty <= 0:
        raise StockError("تعدادِ سهم باید مثبت باشه.")
    ensure_repriced(chat_id, company_id)
    if qty > c["treasury_shares"]:
        raise StockError(f"فقط {c['treasury_shares']:.0f} سهم توی بازار موجوده.")

    price = c["share_price"]
    fee_pct = getattr(config, "STOCK_TRADING_FEE_PERCENT", 0.015)
    cost = round(qty * price * (1 + fee_pct))
    await economy_core.remove_coins(
        chat_id, uid, cost, kind="stock_buy", note=f"خریدِ سهامِ شرکت #{company_id}"
    )
    c["treasury_shares"] -= qty
    _holdings[chat_id][company_id][uid] = _holdings[chat_id][company_id].get(uid, 0.0) + qty
    _apply_price_impact(chat_id, company_id, qty * price, direction=+1)
    return {"qty": qty, "cost": cost, "price": price}


async def sell_shares(chat_id: int, uid: int, company_id: int, qty: int) -> dict:
    c = _companies[chat_id].get(company_id)
    if not c:
        raise StockError("این شرکت توی بورس نیست.")
    ensure_repriced(chat_id, company_id)
    held = _holdings[chat_id][company_id].get(uid, 0.0)
    if qty <= 0 or qty > held + 1e-9:
        raise StockError(f"فقط {held:.0f} سهم از این شرکت داری.")

    price = c["share_price"]
    fee_pct = getattr(config, "STOCK_TRADING_FEE_PERCENT", 0.015)
    gross = qty * price
    proceeds = round(gross * (1 - fee_pct))

    _holdings[chat_id][company_id][uid] = held - qty
    c["treasury_shares"] += qty
    _apply_price_impact(chat_id, company_id, gross, direction=-1)
    await economy_core.add_coins(
        chat_id, uid, proceeds, kind="stock_sell", note=f"فروشِ سهامِ شرکت #{company_id}"
    )
    return {"qty": qty, "proceeds": proceeds, "price": price}


# ---------------------------------------------------------------------------
# ⏱ Reprice بر اساسِ ارزشِ بنیادیِ واقعیِ کسب‌وکار (Lazy، مثلِ بقیه‌ی موتورها)
# ---------------------------------------------------------------------------

def ensure_repriced(chat_id: int, company_id: int) -> None:
    c = _companies[chat_id].get(company_id)
    if not c or c["delisted"]:
        return
    interval = getattr(config, "STOCK_REPRICE_INTERVAL_SECONDS", 900)
    now = time.time()
    elapsed = now - c["last_reprice_ts"]
    if elapsed < interval:
        return

    import economy_business
    b = _owner_business(chat_id, c["owner_uid"], c["business_id"])
    if b is None:
        # کسب‌وکارِ زیرین فروخته/حذف شده - شرکت از بورس Delist می‌شه.
        c["delisted"] = True
        c["last_reprice_ts"] = now
        return

    fundamental_price = economy_business.current_value(b) / c["total_shares"]
    max_change = getattr(config, "STOCK_REPRICE_MAX_CHANGE", 0.08)
    ticks = min(int(elapsed // interval), 20)
    for _ in range(ticks):
        diff = fundamental_price - c["share_price"]
        step = max(-c["share_price"] * max_change, min(c["share_price"] * max_change, diff))
        c["share_price"] = max(0.01, c["share_price"] + step)
    c["last_reprice_ts"] += ticks * interval
    _append_history(chat_id, company_id, c["share_price"])


def _ensure_all_repriced(chat_id: int) -> None:
    for company_id in list(_companies[chat_id].keys()):
        ensure_repriced(chat_id, company_id)


def get_price(chat_id: int, company_id: int) -> float:
    c = _companies[chat_id].get(company_id)
    if not c:
        raise StockError("شرکت پیدا نشد.")
    ensure_repriced(chat_id, company_id)
    return round(c["share_price"], 2)


# ---------------------------------------------------------------------------
# 💵 تقسیمِ سود (Dividend)
# ---------------------------------------------------------------------------

async def pay_dividends(chat_id: int, uid: int, company_id: int, total_amount: int) -> dict:
    """مالکِ شرکت بخشی از سودِ خالصِ کسب‌وکار رو بینِ سهامدارها (طبقِ سهمِ
    هرکس از سهامِ منتشرشده‌ی دستِ مردم، نه کلِ سهام) تقسیم می‌کنه."""
    c = _companies[chat_id].get(company_id)
    if not c or c["delisted"]:
        raise StockError("این شرکت توی بورس نیست.")
    if c["owner_uid"] != uid:
        raise StockError("فقط مالکِ شرکت می‌تونه سود تقسیم کنه.")
    if total_amount <= 0:
        raise StockError("مبلغ باید مثبت باشه.")
    min_gap_h = getattr(config, "STOCK_DIVIDEND_MIN_INTERVAL_HOURS", 24)
    if time.time() - c.get("last_dividend_ts", 0) < min_gap_h * 3600:
        remaining_h = min_gap_h - (time.time() - c.get("last_dividend_ts", 0)) / 3600
        raise StockError(f"هر {min_gap_h} ساعت فقط یه‌بار می‌شه سود تقسیم کرد (حدود {remaining_h:.1f} ساعتِ دیگه).")

    await economy_core.remove_coins(
        chat_id, uid, total_amount, kind="dividend_payout", note=f"تقسیمِ سودِ شرکت #{company_id}"
    )

    outstanding = c["total_shares"] - c["treasury_shares"]
    paid: dict[int, int] = {}
    if outstanding > 0:
        for holder_uid, shares in list(_holdings[chat_id][company_id].items()):
            if shares <= 0:
                continue
            share_amount = round(total_amount * (shares / outstanding))
            if share_amount > 0:
                await economy_core.add_coins(
                    chat_id, holder_uid, share_amount, kind="dividend_income",
                    note=f"سودِ سهامِ شرکت #{company_id}",
                )
                paid[holder_uid] = share_amount

    c["dividends_paid_total"] = c.get("dividends_paid_total", 0) + total_amount
    c["last_dividend_ts"] = time.time()
    return paid


# ---------------------------------------------------------------------------
# 📊 Portfolio / شاخص بازار / رتبه‌بندی سرمایه‌گذاران
# ---------------------------------------------------------------------------

def holdings_of(chat_id: int, uid: int) -> list[dict]:
    _ensure_all_repriced(chat_id)
    out = []
    for company_id, c in _companies[chat_id].items():
        qty = _holdings[chat_id][company_id].get(uid, 0.0)
        if qty > 0:
            out.append({
                "company_id": company_id, "name": c["name"], "qty": qty,
                "price": c["share_price"], "value": round(qty * c["share_price"]),
                "delisted": c["delisted"],
            })
    return out


def portfolio_value(chat_id: int, uid: int) -> int:
    _ensure_all_repriced(chat_id)
    total = 0.0
    for company_id, c in _companies[chat_id].items():
        qty = _holdings[chat_id][company_id].get(uid, 0.0)
        if qty > 0:
            total += qty * c["share_price"]
    return round(total)


economy_core.register_net_worth_source(portfolio_value)


def top_investors(chat_id: int, limit: int = 10) -> list[tuple[int, int]]:
    host = _host()
    members = host._known_members.get(chat_id, set())
    ranked = [(uid, portfolio_value(chat_id, uid)) for uid in members]
    ranked = [r for r in ranked if r[1] > 0]
    ranked.sort(key=lambda t: t[1], reverse=True)
    return ranked[:limit]


def market_index(chat_id: int) -> float:
    """شاخصِ کلِ بورس: میانگینِ وزن‌دار (با ارزشِ بازارِ هر شرکت) از نسبتِ
    قیمتِ فعلی به قیمتِ IPO، در مقیاسِ ۱۰۰."""
    companies = list_companies(chat_id)
    if not companies:
        return 100.0
    weighted_sum = 0.0
    total_weight = 0.0
    for c in companies:
        market_cap = c["share_price"] * c["total_shares"]
        ratio = (c["share_price"] / c["ipo_price"]) if c["ipo_price"] else 1.0
        weighted_sum += ratio * market_cap
        total_weight += market_cap
    if total_weight <= 0:
        return 100.0
    return round(weighted_sum / total_weight * 100, 2)


# ---------------------------------------------------------------------------
# 🖊 نمایش متنی
# ---------------------------------------------------------------------------

def format_exchange_text(chat_id: int) -> str:
    companies = list_companies(chat_id)
    idx = market_index(chat_id)
    lines = [f"🏢 بورسِ سهامِ شرکت‌ها — شاخصِ کل: {idx}", "─────────────────"]
    if not companies:
        lines.append("هنوز هیچ شرکتی توی بورس لیست نشده.")
        lines.append("با «عرضه اولیه [شناسه‌ی کسب‌وکار]» شرکتِ خودت رو ببر بورس (نیاز به ارزشِ حداقلِ "
                      f"{getattr(config, 'STOCK_IPO_MIN_VALUE', 5000):,} داره).")
        return "\n".join(lines)
    for c in companies:
        change_pct = ((c["share_price"] - c["ipo_price"]) / c["ipo_price"] * 100) if c["ipo_price"] else 0
        arrow = "🟢" if change_pct > 0 else ("🔴" if change_pct < 0 else "⚪")
        lines.append(
            f"{arrow} #{c['id']} {c['name']}: {c['share_price']:,.2f} {config.CURRENCY_NAME} "
            f"({change_pct:+.1f}٪ از IPO) — {c['treasury_shares']:.0f}/{c['total_shares']} سهم آزاد"
        )
    lines.append("")
    lines.append("خرید: «خرید سهام [شناسه شرکت] [تعداد]» — فروش: «فروش سهام [شناسه شرکت] [تعداد]»")
    return "\n".join(lines)


def format_portfolio_text(chat_id: int, uid: int) -> str:
    rows = holdings_of(chat_id, uid)
    total = portfolio_value(chat_id, uid)
    lines = ["💼 پرتفویِ سهامِ من", "─────────────────"]
    if not rows:
        lines.append("هنوز سهامی نداری. با «بورس» لیستِ شرکت‌ها رو ببین.")
        return "\n".join(lines)
    for r in rows:
        tag = " (Delist شده)" if r["delisted"] else ""
        lines.append(f"#{r['company_id']} {r['name']}{tag}: {r['qty']:.0f} سهم — ارزش: {r['value']:,}")
    lines.append("")
    lines.append(f"💰 ارزشِ کلِ پرتفوی: {total:,} {config.CURRENCY_NAME}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 📨 Command Handlers
# ---------------------------------------------------------------------------

def _group_only_or_none(update: Update):
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return None
    return chat


async def stocks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بورس / /stocks"""
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_stock"):
        await message.reply_text(economy_admin.module_off_message(chat.id, "economy_module_stock"))
        return
    await message.reply_text(format_exchange_text(chat.id))


async def ipo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرضه اولیه [شناسه کسب‌وکار] / /ipo <business_id>"""
    host = _host()
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_stock"):
        await message.reply_text(economy_admin.module_off_message(chat.id, "economy_module_stock"))
        return
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «عرضه اولیه [شناسه‌ی کسب‌وکار]». با «کسب‌وکار من» شناسه رو ببین.")
        return
    try:
        business_id = int(args[0])
    except ValueError:
        await message.reply_text("شناسه‌ی کسب‌وکار نامعتبره.")
        return
    uid = update.effective_user.id
    try:
        company = await go_public(chat.id, uid, business_id)
    except StockError as e:
        await message.reply_text(f"❌ {e}")
        return
    await message.reply_text(
        f"🎉 شرکت #{company['id']} ({company['name']}) وارد بورس شد!\n"
        f"قیمتِ هر سهم: {company['share_price']:,.2f} {config.CURRENCY_NAME}\n"
        f"{company['treasury_shares']} سهم توی بازار موجوده (از {company['total_shares']} سهمِ کل)."
    )
    await host.save_state()


async def buy_stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خرید سهام [شناسه شرکت] [تعداد] / /buystock <company_id> <qty>"""
    host = _host()
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_stock"):
        await message.reply_text(economy_admin.module_off_message(chat.id, "economy_module_stock"))
        return
    args = context.args or []
    if len(args) < 2:
        await message.reply_text("استفاده: «خرید سهام [شناسه شرکت] [تعداد سهم]»")
        return
    try:
        company_id, qty = int(args[0]), int(args[1])
    except ValueError:
        await message.reply_text("شناسه/تعداد نامعتبره.")
        return
    uid = update.effective_user.id
    try:
        result = await buy_shares(chat.id, uid, company_id, qty)
    except (StockError, economy_core.InsufficientFundsError) as e:
        await message.reply_text(f"❌ {e}")
        return
    await message.reply_text(
        f"✅ {result['qty']} سهم از شرکت #{company_id} خریدی (قیمت: {result['price']:,.2f}، "
        f"مجموع: {result['cost']:,} {config.CURRENCY_NAME})."
    )
    await host.save_state()


async def sell_stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فروش سهام [شناسه شرکت] [تعداد|all] / /sellstock <company_id> <qty|all>"""
    host = _host()
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_stock"):
        await message.reply_text(economy_admin.module_off_message(chat.id, "economy_module_stock"))
        return
    args = context.args or []
    if len(args) < 2:
        await message.reply_text("استفاده: «فروش سهام [شناسه شرکت] [تعداد|all]»")
        return
    try:
        company_id = int(args[0])
    except ValueError:
        await message.reply_text("شناسه‌ی شرکت نامعتبره.")
        return
    uid = update.effective_user.id
    if args[1].lower() in ("all", "همه"):
        qty = int(_holdings[chat.id][company_id].get(uid, 0))
    else:
        try:
            qty = int(args[1])
        except ValueError:
            await message.reply_text("تعداد نامعتبره.")
            return
    try:
        result = await sell_shares(chat.id, uid, company_id, qty)
    except StockError as e:
        await message.reply_text(f"❌ {e}")
        return
    await message.reply_text(
        f"✅ {result['qty']} سهم از شرکت #{company_id} فروختی: +{result['proceeds']:,} {config.CURRENCY_NAME}"
    )
    await host.save_state()


async def stock_portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """سهام من / /mystocks"""
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    await message.reply_text(format_portfolio_text(chat.id, uid))


async def pay_dividends_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تقسیم سود [شناسه شرکت] [مبلغ] / /dividend <company_id> <amount>"""
    host = _host()
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if len(args) < 2:
        await message.reply_text("استفاده: «تقسیم سود [شناسه شرکت] [مبلغ کل]»")
        return
    try:
        company_id, amount = int(args[0]), int(args[1])
    except ValueError:
        await message.reply_text("شناسه/مبلغ نامعتبره.")
        return
    uid = update.effective_user.id
    try:
        paid = await pay_dividends(chat.id, uid, company_id, amount)
    except (StockError, economy_core.InsufficientFundsError) as e:
        await message.reply_text(f"❌ {e}")
        return
    await message.reply_text(
        f"✅ {amount:,} {config.CURRENCY_NAME} سود بینِ {len(paid)} سهامدار تقسیم شد."
    )
    await host.save_state()


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "version": STATE_VERSION,
        "companies": {str(c): v for c, v in _companies.items()},
        "next_company_id": {str(c): v for c, v in _next_company_id.items()},
        "holdings": {
            str(c): {str(cid): dict(v) for cid, v in d.items()}
            for c, d in _holdings.items()
        },
        "history": {
            str(c): {str(cid): list(v) for cid, v in d.items()}
            for c, d in _history.items()
        },
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
            logger.warning(f"ذخیره‌ی state بورسِ سهام ناموفق بود: {e}")


def load_state():
    data = None
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"خوندن state بورسِ سهام ناموفق بود: {e}؛ تلاش برای بازیابی از بکاپ...")
            data = None
    if data is None:
        bak = STATE_FILE + ".bak"
        if os.path.exists(bak):
            try:
                with open(bak, encoding="utf-8") as f:
                    data = json.load(f)
                logger.warning("state اصلیِ بورسِ سهام خراب/نامعتبر بود؛ از بکاپ بازیابی شد.")
            except Exception as e:
                logger.warning(f"خوندنِ بکاپِ بورسِ سهام هم ناموفق بود: {e}")
                return
        else:
            return

    for cid_s, companies in data.get("companies", {}).items():
        cid = int(cid_s)
        for comp_id_s, c in companies.items():
            c = dict(c)
            c["id"] = int(c["id"])
            c["owner_uid"] = int(c["owner_uid"])
            c["business_id"] = int(c["business_id"])
            _companies[cid][int(comp_id_s)] = c

    for cid_s, v in data.get("next_company_id", {}).items():
        _next_company_id[int(cid_s)] = int(v)

    for cid_s, d in data.get("holdings", {}).items():
        cid = int(cid_s)
        for comp_id_s, holders in d.items():
            comp_id = int(comp_id_s)
            for uid_s, qty in holders.items():
                _holdings[cid][comp_id][int(uid_s)] = float(qty)

    for cid_s, d in data.get("history", {}).items():
        cid = int(cid_s)
        for comp_id_s, hist in d.items():
            _history[cid][int(comp_id_s)] = [float(x) for x in hist]

    logger.info("وضعیتِ بورسِ سهام از فایل بارگذاری شد.")
