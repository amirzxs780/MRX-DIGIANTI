# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY MARKET & TRADING — فاز ۳ (Module 5 + Module 6)
===================================================================
یه بازار داخلی کاملاً مجازیه (BTC/ETH/GOLD/OIL/TECH/ENERGY/DIAMOND — هیچ‌کدوم
واقعی نیستن، فقط عدد داخل بازی‌ان). هر گروه (chat_id) قیمت‌های مستقل خودشو
داره. قیمت‌ها با یه مدل Random Walk + رویدادهای بازار (Bull/Bear/Crash/Boom/
Shock) به‌صورت Lazy Tick آپدیت می‌شن: هر وقت کسی به بازار سر بزنه، بر اساس
زمان گذشته، تعداد Tickِ لازم (با سقف MARKET_MAX_TICKS_PER_ACCESS) محاسبه و
اعمال می‌شه — نیازی به Task پس‌زمینه‌ی جدا نیست.

همه‌ی پول از economy_core عبور می‌کنه. ارزش Portfolio به‌عنوان یه Net Worth
Source به economy_core ثبت می‌شه (از Registry فاز ۱ استفاده می‌کنه).

«ترید [مبلغ]» (Module 6: Trading) یه مکانیک سریع و جدا از خرید/فروش دارایی
واقعیه: کاربر مبلغی رو Stake می‌کنه و فوری نتیجه (سود/ضرر) مشخص می‌شه؛ Trading
Level/XP/Streak/Win-Rate جدا از Portfolio ردیابی می‌شه.
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
import economy_core
import economy_missions

logger = logging.getLogger("economy_market")

STATE_FILE = getattr(config, "ECONOMY_MARKET_STATE_FILE", "economy_market_state.json")

ASSETS: dict = getattr(config, "MARKET_ASSETS", {})

_save_lock = asyncio.Lock()


def _host():
    import bot as host
    return host


# ---------------------------------------------------------------------------
# 💾 State
# ---------------------------------------------------------------------------

_prices = defaultdict(dict)             # chat_id -> symbol -> float
_history = defaultdict(lambda: defaultdict(list))   # chat_id -> symbol -> [float, ...]
_last_tick_ts = defaultdict(float)      # chat_id -> ts
_active_event = defaultdict(lambda: None)  # chat_id -> {"key","ticks_left"} | None

# chat_id -> uid -> symbol -> {"qty": float, "invested": int}
_portfolio = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))

# chat_id -> uid -> {"level","xp","wins","losses","total_profit","total_loss","streak","best_streak","last_ts"}
_trading = defaultdict(dict)
_trade_daily = defaultdict(dict)        # chat_id -> uid -> {"date","count"}


def _today_str() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _ensure_prices(chat_id: int) -> None:
    if _prices[chat_id]:
        return
    for symbol, info in ASSETS.items():
        _prices[chat_id][symbol] = float(info["base_price"])
        _history[chat_id][symbol] = [float(info["base_price"])]
    _last_tick_ts[chat_id] = time.time()


# ---------------------------------------------------------------------------
# ⏱ Lazy Tick — قیمت‌ها رو بر اساس زمان سپری‌شده آپدیت می‌کنه
# ---------------------------------------------------------------------------

def _apply_one_tick(chat_id: int) -> None:
    event = _active_event[chat_id]
    if event:
        event["ticks_left"] -= 1
        if event["ticks_left"] <= 0:
            _active_event[chat_id] = None
    else:
        if random.random() < getattr(config, "MARKET_EVENT_CHANCE_PER_TICK", 0.04):
            key = random.choice(list(config.MARKET_EVENTS.keys()))
            ev = config.MARKET_EVENTS[key]
            for symbol in ASSETS:
                _prices[chat_id][symbol] *= ev["multiplier"]
            _active_event[chat_id] = {"key": key, "ticks_left": ev["duration_ticks"]}

    hist_len = getattr(config, "MARKET_PRICE_HISTORY_LEN", 30)
    min_fraction = getattr(config, "MARKET_PRICE_MIN_FRACTION", 0.05)
    for symbol, info in ASSETS.items():
        vol = info["volatility"]
        change = random.uniform(-vol, vol)
        new_price = _prices[chat_id][symbol] * (1 + change)
        floor = info["base_price"] * min_fraction
        _prices[chat_id][symbol] = max(floor, new_price)
        hist = _history[chat_id][symbol]
        hist.append(_prices[chat_id][symbol])
        if len(hist) > hist_len:
            del hist[0: len(hist) - hist_len]


def _ensure_ticked(chat_id: int) -> None:
    _ensure_prices(chat_id)
    interval = getattr(config, "MARKET_TICK_INTERVAL_SECONDS", 300)
    now = time.time()
    elapsed = now - _last_tick_ts[chat_id]
    ticks = int(elapsed // interval)
    if ticks <= 0:
        return
    ticks = min(ticks, getattr(config, "MARKET_MAX_TICKS_PER_ACCESS", 50))
    for _ in range(ticks):
        _apply_one_tick(chat_id)
    _last_tick_ts[chat_id] += ticks * interval


def current_price(chat_id: int, symbol: str) -> float:
    _ensure_ticked(chat_id)
    return _prices[chat_id].get(symbol, ASSETS.get(symbol, {}).get("base_price", 0))


def _liquidity_of(symbol: str) -> float:
    info = ASSETS[symbol]
    return info["base_price"] * info.get("liquidity_mult", 100)


_volume_24h = defaultdict(dict)  # chat_id -> symbol -> تجمعی (بدون Decay دقیق؛ صرفاً برای نمایش نسبی «فعالیت بازار»)


def _apply_price_impact(chat_id: int, symbol: str, trade_value: float, direction: int) -> None:
    """ارتقای فاز ۱۴ — Supply/Demand واقعی: خریدن قیمت رو بالا می‌بره، فروختن
    پایین. اثر = ارزش معامله / نقدینگی نماد، با سقف MARKET_MAX_IMPACT_PER_TRADE."""
    if trade_value <= 0:
        return
    liquidity = _liquidity_of(symbol)
    if liquidity <= 0:
        return
    impact = min(trade_value / liquidity, getattr(config, "MARKET_MAX_IMPACT_PER_TRADE", 0.08))
    factor = 1 + (impact * direction)
    floor = ASSETS[symbol]["base_price"] * getattr(config, "MARKET_PRICE_MIN_FRACTION", 0.05)
    _prices[chat_id][symbol] = max(floor, _prices[chat_id][symbol] * factor)
    _volume_24h[chat_id][symbol] = _volume_24h[chat_id].get(symbol, 0) + trade_value


# ---------------------------------------------------------------------------
# 📊 Net Worth Source — ارزش Portfolio (فقط بار اول که این ماژول Import می‌شه ثبت می‌شه)
# ---------------------------------------------------------------------------

def get_portfolio_value(chat_id: int, uid: int) -> int:
    _ensure_ticked(chat_id)
    total = 0.0
    for symbol, pos in _portfolio[chat_id][uid].items():
        total += pos.get("qty", 0) * _prices[chat_id].get(symbol, 0)
    return round(total)


economy_core.register_net_worth_source(get_portfolio_value)


# ---------------------------------------------------------------------------
# 📋 نمایش بازار
# ---------------------------------------------------------------------------

async def market_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بازار / ارز / /market"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_market"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    _ensure_ticked(chat.id)
    lines = ["📈 بازار DIGIANTI", ""]
    event = _active_event[chat.id]
    if event:
        ev = config.MARKET_EVENTS[event["key"]]
        lines.append(f"{ev['label']} فعاله!")
        lines.append("")
    for symbol, info in ASSETS.items():
        price = _prices[chat.id][symbol]
        hist = _history[chat.id][symbol]
        prev = hist[-2] if len(hist) >= 2 else price
        change = 0 if prev == 0 else (price - prev) / prev * 100
        arrow = "🟢" if change > 0 else ("🔴" if change < 0 else "⚪")
        vol = _volume_24h.get(chat.id, {}).get(symbol, 0)
        vol_note = f" — حجم معاملات: {vol:,.0f}" if vol else ""
        lines.append(f"{arrow} {symbol} ({info['name']}): {price:,.2f} {config.CURRENCY_NAME} ({change:+.2f}٪){vol_note}")
    lines.append("")
    lines.append("جزئیات یه نماد: «قیمت [نماد]» — خرید: «خرید [نماد] [مبلغ]» — فروش: «فروش [نماد] [مقدار|all]»")
    await message.reply_text("\n".join(lines))


def _sparkline(hist: list[float]) -> str:
    if len(hist) < 2:
        return ""
    return "".join("▲" if hist[i] >= hist[i - 1] else "▼" for i in range(1, len(hist)))


async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قیمت [نماد] / /price <symbol>"""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if not args:
        symbols = "، ".join(ASSETS.keys())
        await message.reply_text(f"استفاده: «قیمت [نماد]». نمادهای موجود: {symbols}")
        return
    symbol = args[0].upper()
    if symbol not in ASSETS:
        symbols = "، ".join(ASSETS.keys())
        await message.reply_text(f"همچین نمادی نیست. نمادهای موجود: {symbols}")
        return
    _ensure_ticked(chat.id)
    price = _prices[chat.id][symbol]
    hist = _history[chat.id][symbol][-10:]
    uid = update.effective_user.id
    pos = _portfolio[chat.id][uid].get(symbol)
    lines = [
        f"{symbol} ({ASSETS[symbol]['name']})",
        f"💰 قیمت فعلی: {price:,.2f} {config.CURRENCY_NAME}",
        f"📊 روند اخیر: {_sparkline(hist)}",
    ]
    if pos and pos.get("qty", 0) > 0:
        qty = pos["qty"]
        avg = pos["invested"] / qty if qty else 0
        value = qty * price
        pl = value - pos["invested"]
        pl_pct = (pl / pos["invested"] * 100) if pos["invested"] else 0
        lines.append(f"🎒 دارایی تو: {qty:.4f} — میانگین خرید: {avg:,.2f} — ارزش الان: {value:,.0f} ({pl:+.0f} / {pl_pct:+.1f}٪)")
    await message.reply_text("\n".join(lines))


async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """سبد / /portfolio"""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    _ensure_ticked(chat.id)
    positions = {s: p for s, p in _portfolio[chat.id][uid].items() if p.get("qty", 0) > 0}
    if not positions:
        await message.reply_text("سبدت خالیه. با «خرید [نماد] [مبلغ]» شروع کن.")
        return
    lines = ["🎒 سبد سرمایه‌گذاری تو:", ""]
    total_value = 0
    total_invested = 0
    for symbol, pos in positions.items():
        price = _prices[chat.id][symbol]
        qty = pos["qty"]
        value = qty * price
        pl = value - pos["invested"]
        pl_pct = (pl / pos["invested"] * 100) if pos["invested"] else 0
        total_value += value
        total_invested += pos["invested"]
        lines.append(f"• {symbol}: {qty:.4f} — ارزش: {value:,.0f} ({pl:+.0f} / {pl_pct:+.1f}٪)")
    total_pl = total_value - total_invested
    lines.append("")
    lines.append(f"💎 ارزش کل سبد: {total_value:,.0f} {config.CURRENCY_NAME}")
    lines.append(f"📊 سود/زیان کل: {total_pl:+,.0f} {config.CURRENCY_NAME}")
    await message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# 🛒 خرید / فروش دارایی
# ---------------------------------------------------------------------------

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خرید [نماد] [مبلغ] — این تابع از bot.py's buy_command به‌عنوان Fallback صدا زده
    می‌شه (وقتی کد، آیتم فروشگاه نیست)."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_market"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return
    args = context.args or []
    if len(args) < 2:
        await message.reply_text("استفاده: «خرید [نماد] [مبلغ سکه]» یا /buy <نماد> <مبلغ>")
        return
    symbol = args[0].upper()
    if symbol not in ASSETS:
        await message.reply_text("همچین آیتم/نمادی پیدا نشد. برای فروشگاه: /shop — برای بازار: «بازار»")
        return
    try:
        amount = int(args[1].replace(",", ""))
    except ValueError:
        amount = -1
    if amount <= 0:
        await message.reply_text("مبلغ نامعتبره.")
        return

    uid = update.effective_user.id
    price = current_price(chat.id, symbol)
    try:
        new_wallet = await economy_core.remove_coins(chat.id, uid, amount, kind="market_buy", note=f"خرید {symbol}")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست ({economy_core.get_wallet(chat.id, uid)} {config.CURRENCY_NAME}).")
        return

    fee_pct = getattr(config, "MARKET_TRADING_FEE_PERCENT", 0.01)
    net_amount = amount * (1 - fee_pct)
    qty = net_amount / price if price else 0

    pos = _portfolio[chat.id][uid].setdefault(symbol, {"qty": 0.0, "invested": 0})
    pos["qty"] = pos.get("qty", 0) + qty
    pos["invested"] = pos.get("invested", 0) + amount
    _apply_price_impact(chat.id, symbol, amount, direction=+1)  # فاز ۱۴: خرید قیمت رو بالا می‌بره

    lines = [
        f"✅ {qty:.4f} {symbol} خریدی (قیمت: {price:,.2f}، کارمزد {fee_pct*100:.0f}٪).",
        f"💰 موجودی کیف‌پول: {new_wallet} {config.CURRENCY_NAME}",
    ]
    try:
        lines += await economy_missions.record_progress(chat.id, uid, "market_buy", 1)
    except Exception as e:
        logger.warning(f"ثبت پیشرفت ماموریت market_buy ناموفق بود: {e}")
    await message.reply_text("\n".join(lines))
    await host.save_state()


async def sell_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فروش [نماد] [مقدار|all] / /sell <symbol> <qty|all>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_market"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return
    args = context.args or []
    if len(args) < 2:
        await message.reply_text("استفاده: «فروش [نماد] [مقدار|all]» یا /sell <نماد> <مقدار|all>")
        return
    symbol = args[0].upper()
    if symbol not in ASSETS:
        await message.reply_text("همچین نمادی نیست. با «بازار» لیست نمادها رو ببین.")
        return

    uid = update.effective_user.id
    pos = _portfolio[chat.id][uid].get(symbol)
    if not pos or pos.get("qty", 0) <= 0:
        await message.reply_text(f"چیزی از {symbol} نداری که بفروشی.")
        return

    if args[1].lower() in ("all", "همه"):
        qty = pos["qty"]
    else:
        try:
            qty = float(args[1])
        except ValueError:
            await message.reply_text("مقدار نامعتبره.")
            return
    if qty <= 0 or qty > pos["qty"] + 1e-9:
        await message.reply_text(f"مقدار نامعتبره. الان {pos['qty']:.4f} {symbol} داری.")
        return

    price = current_price(chat.id, symbol)
    fee_pct = getattr(config, "MARKET_TRADING_FEE_PERCENT", 0.01)
    gross = qty * price
    proceeds = round(gross * (1 - fee_pct))

    cost_basis = (qty / pos["qty"]) * pos["invested"]
    pos["qty"] -= qty
    pos["invested"] = max(0, pos["invested"] - cost_basis)
    _apply_price_impact(chat.id, symbol, gross, direction=-1)  # فاز ۱۴: فروش قیمت رو پایین می‌بره

    new_wallet = await economy_core.add_coins(chat.id, uid, proceeds, kind="market_sell", note=f"فروش {symbol}")
    pl = proceeds - cost_basis
    await message.reply_text(
        f"✅ {qty:.4f} {symbol} فروختی: +{proceeds} {config.CURRENCY_NAME} (سود/زیان: {pl:+.0f})\n"
        f"💰 موجودی کیف‌پول: {new_wallet} {config.CURRENCY_NAME}"
    )
    await host.save_state()


# ---------------------------------------------------------------------------
# 💹 ترید سریع (Module 6: Trading) — جدا از خرید/فروش دارایی
# ---------------------------------------------------------------------------

def _trading_stats(chat_id: int, uid: int) -> dict:
    stats = _trading[chat_id].get(uid)
    if not stats:
        stats = {
            "level": 1, "xp": 0, "wins": 0, "losses": 0,
            "total_profit": 0, "total_loss": 0, "streak": 0, "best_streak": 0,
        }
        _trading[chat_id][uid] = stats
    return stats


def _trade_xp_needed(level: int) -> int:
    return getattr(config, "TRADE_XP_PER_LEVEL_BASE", 100) * level


async def trade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ترید [مبلغ] / /trade <amount>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_market"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    # 🧠 فاز ۹: Anti-Abuse سراسری
    try:
        import economy_antiabuse
        wait = economy_antiabuse.check_and_mark(chat.id, uid)
        if wait > 0:
            await message.reply_text(f"⏳ یکم آروم‌تر؛ {wait:.1f} ثانیه‌ی دیگه صبر کن.")
            return
    except Exception as e:
        logger.warning(f"چک Anti-Abuse سراسری ناموفق بود: {e}")
    args = context.args or []
    try:
        amount = int(args[0].replace(",", "")) if args else -1
    except ValueError:
        amount = -1
    min_a, max_a = getattr(config, "TRADE_MIN_AMOUNT", 50), getattr(config, "TRADE_MAX_AMOUNT", 5000)
    if amount <= 0:
        await message.reply_text(f"استفاده: «ترید [مبلغ]» یا /trade <مبلغ> (بین {min_a} و {max_a})")
        return
    if not (min_a <= amount <= max_a):
        await message.reply_text(f"مبلغ باید بین {min_a} و {max_a} {config.CURRENCY_NAME} باشه.")
        return

    now = time.time()
    stats = _trading_stats(chat.id, uid)
    cooldown = getattr(config, "TRADE_COOLDOWN_SECONDS", 600)
    remaining = cooldown - (now - stats.get("last_ts", 0))
    if remaining > 0:
        await message.reply_text(f"⏳ هنوز {int(remaining // 60) + 1} دقیقه‌ی دیگه تا ترید بعدی مونده.")
        return

    daily = _trade_daily[chat.id].get(uid)
    today = _today_str()
    if not daily or daily.get("date") != today:
        daily = {"date": today, "count": 0}
    limit = getattr(config, "TRADE_DAILY_LIMIT", 20)
    if daily["count"] >= limit:
        await message.reply_text(f"📆 امروز به سقف {limit} بار ترید رسیدی.")
        return

    try:
        await economy_core.remove_coins(chat.id, uid, amount, kind="trade_stake", note="Stake ترید")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست ({economy_core.get_wallet(chat.id, uid)} {config.CURRENCY_NAME}).")
        return

    daily["count"] += 1
    _trade_daily[chat.id][uid] = daily
    stats["last_ts"] = now

    win = random.random() < getattr(config, "TRADE_WIN_CHANCE", 0.48)
    critical = random.random() < getattr(config, "TRADE_CRITICAL_CHANCE", 0.05)

    if win:
        frac = random.uniform(config.TRADE_PROFIT_MIN_FRACTION, config.TRADE_PROFIT_MAX_FRACTION)
        if critical:
            frac *= getattr(config, "TRADE_CRITICAL_PROFIT_MULTIPLIER", 2.0)
        profit = round(amount * frac)
        # ⚡ فاز ۷: Boost عمومی سکه + Boost اختصاصی ترید (boost_trading)
        boost_note = ""
        try:
            import economy_engine
            profit = economy_engine.apply_coin_multiplier(chat.id, uid, profit)
            if economy_engine.has_active_item_type(chat.id, uid, "boost_trading"):
                profit = round(profit * getattr(config, "TRADING_BOOST_MULTIPLIER", 1.5))
                boost_note = " 📈"
        except Exception as e:
            logger.warning(f"اعمال Trading Boost ناموفق بود: {e}")
        payout = amount + profit
        stats["wins"] += 1
        stats["total_profit"] += profit
        stats["streak"] = stats.get("streak", 0) + 1
        stats["best_streak"] = max(stats.get("best_streak", 0), stats["streak"])
        new_wallet = await economy_core.add_coins(chat.id, uid, payout, kind="trade_win", note="برد ترید")
        header = "🎉 ترید موفق بود!" + boost_note + (" 💥 CRITICAL WIN!" if critical else "")
        body = f"+{profit} {config.CURRENCY_EMOJI} سود ({payout} برگشت خورد)"
    else:
        if critical:
            loss_frac = 1.0
        else:
            loss_frac = random.uniform(config.TRADE_LOSS_MIN_FRACTION, config.TRADE_LOSS_MAX_FRACTION)
        loss = round(amount * loss_frac)
        refund = amount - loss
        if refund > 0:
            await economy_core.add_coins(chat.id, uid, refund, kind="trade_partial_refund", note="بازگشت جزئی ترید")
        stats["losses"] += 1
        stats["total_loss"] += loss
        stats["streak"] = 0
        new_wallet = economy_core.get_wallet(chat.id, uid)
        header = "😓 ترید ضرر داد." + (" ☠️ CRITICAL LOSS!" if critical else "")
        body = f"-{loss} {config.CURRENCY_EMOJI} ضرر"

    xp_gain = getattr(config, "TRADE_XP_PER_TRADE", 15)
    stats["xp"] += xp_gain
    level_events = []
    while stats["xp"] >= _trade_xp_needed(stats["level"]):
        stats["xp"] -= _trade_xp_needed(stats["level"])
        stats["level"] += 1
        level_events.append(f"⭐ Trading Level تو رفت {stats['level']}!")

    total = stats["wins"] + stats["losses"]
    win_rate = (stats["wins"] / total * 100) if total else 0

    lines = [header, body, f"💰 موجودی: {new_wallet} {config.CURRENCY_NAME}"]
    lines.append(f"📊 Trading Level {stats['level']} — Win Rate: {win_rate:.0f}٪ — Streak: {stats['streak']}")
    lines.extend(level_events)
    try:
        lines += await economy_missions.record_progress(chat.id, uid, "trade", 1)
    except Exception as e:
        logger.warning(f"ثبت پیشرفت ماموریت trade ناموفق بود: {e}")
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
    return {
        "prices": {str(c): dict(v) for c, v in _prices.items()},
        "history": {str(c): {s: h for s, h in v.items()} for c, v in _history.items()},
        "last_tick_ts": {str(c): ts for c, ts in _last_tick_ts.items()},
        "active_event": {str(c): ev for c, ev in _active_event.items() if ev},
        "portfolio": {
            str(c): {str(u): {s: p for s, p in v2.items()} for u, v2 in v.items()}
            for c, v in _portfolio.items()
        },
        "trading": {str(c): {str(u): s for u, s in v.items()} for c, v in _trading.items()},
        "trade_daily": {str(c): {str(u): d for u, d in v.items()} for c, v in _trade_daily.items()},
        "volume_24h": {str(c): dict(v) for c, v in _volume_24h.items()},
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
            logger.warning(f"ذخیره‌ی state موتور Market ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور Market ناموفق بود: {e}")
        return

    for cid, v in data.get("prices", {}).items():
        _prices[int(cid)] = v
    for cid, v in data.get("history", {}).items():
        for s, h in v.items():
            _history[int(cid)][s] = h
    for cid, ts in data.get("last_tick_ts", {}).items():
        _last_tick_ts[int(cid)] = ts
    for cid, ev in data.get("active_event", {}).items():
        _active_event[int(cid)] = ev
    for cid, v in data.get("portfolio", {}).items():
        for uid, positions in v.items():
            for symbol, pos in positions.items():
                _portfolio[int(cid)][int(uid)][symbol] = pos
    for cid, v in data.get("trading", {}).items():
        for uid, s in v.items():
            _trading[int(cid)][int(uid)] = s
    for cid, v in data.get("trade_daily", {}).items():
        _trade_daily[int(cid)] = {int(u): d for u, d in v.items()}
    for cid, v in data.get("volume_24h", {}).items():
        _volume_24h[int(cid)] = dict(v)

    logger.info("وضعیت موتور Market از فایل بارگذاری شد.")
