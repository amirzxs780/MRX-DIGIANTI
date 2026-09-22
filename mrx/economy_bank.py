# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY BANK — فاز ۳ (Module 4)
===========================================
لایه‌ی UI/منطق بانک، روی توابع فاز ۱ (economy_core.deposit/withdraw/
add_bank_coins/remove_bank_coins) سوار می‌شه. این فایل هرگز مستقیم موجودی
کسی رو دستکاری نمی‌کنه — همیشه از economy_core عبور می‌کنه.

قابلیت‌ها:
    • سپرده‌ی کوتاه‌مدت (همون Bank Balance فاز ۱) + سود روزانه‌ی Claim‌شدنی
    • سپرده‌ی بلندمدت: پول رو برای N روز قفل می‌کنه، سود ثابت بالاتر، برداشت
      زودهنگام = نیمی از سود (نه اصل پول) از دست می‌ره
    • کارمزد برداشت (Money Sink)
    • Bank Level / Upgrade — سطح بالاتر یعنی سود بیشتر + ظرفیت سپرده‌ی
      بلندمدت بیشتر
    • یکپارچه با economy_missions (action="deposit")
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

logger = logging.getLogger("economy_bank")

STATE_FILE = getattr(config, "ECONOMY_BANK_STATE_FILE", "economy_bank_state.json")

_save_lock = asyncio.Lock()


def _host():
    import bot as host
    return host


# ---------------------------------------------------------------------------
# 💾 State
# ---------------------------------------------------------------------------

_bank_level = defaultdict(dict)          # chat_id -> uid -> int
_last_interest_ts = defaultdict(dict)    # chat_id -> uid -> float
# chat_id -> uid -> list[{"id","amount","rate","opened_ts","unlock_ts","days","matured_credited"}]
_long_deposits = defaultdict(lambda: defaultdict(list))
_next_id = defaultdict(dict)             # chat_id -> uid -> int

# ---------- 💳 Loans / Credit Score — ارتقای فاز ۱۳ ----------
_credit_score = defaultdict(dict)        # chat_id -> uid -> int
# chat_id -> uid -> list[{id, principal, total_owed, remaining, rate, term_days,
#                         installment_amount, installments_total, installments_paid,
#                         next_due_ts, missed_streak, status, opened_ts, history}]
_loans = defaultdict(lambda: defaultdict(list))
_next_loan_id = defaultdict(dict)


def _level(chat_id: int, uid: int) -> int:
    return _bank_level[chat_id].get(uid, 1)


def _level_info(chat_id: int, uid: int) -> dict:
    return config.BANK_LEVELS.get(_level(chat_id, uid), config.BANK_LEVELS[1])


def _new_deposit_id(chat_id: int, uid: int) -> int:
    n = _next_id[chat_id].get(uid, 0) + 1
    _next_id[chat_id][uid] = n
    return n


def _new_loan_id(chat_id: int, uid: int) -> int:
    n = _next_loan_id[chat_id].get(uid, 0) + 1
    _next_loan_id[chat_id][uid] = n
    return n


# ---------------------------------------------------------------------------
# 💳 Credit Score — ارتقای فاز ۱۳
# ---------------------------------------------------------------------------

def get_credit_score(chat_id: int, uid: int) -> int:
    return _credit_score[chat_id].get(uid, getattr(config, "CREDIT_SCORE_DEFAULT", 600))


def _adjust_credit(chat_id: int, uid: int, delta: int) -> int:
    lo = getattr(config, "CREDIT_SCORE_MIN", 300)
    hi = getattr(config, "CREDIT_SCORE_MAX", 850)
    new_score = max(lo, min(hi, get_credit_score(chat_id, uid) + delta))
    _credit_score[chat_id][uid] = new_score
    return new_score


def max_loan_amount(chat_id: int, uid: int) -> int:
    """سقف وام بر اساس Credit Score؛ خطی بین امتیاز MIN تا MAX."""
    score = get_credit_score(chat_id, uid)
    lo = getattr(config, "CREDIT_SCORE_MIN", 300)
    hi = getattr(config, "CREDIT_SCORE_MAX", 850)
    frac = (score - lo) / max(1, (hi - lo))
    base = getattr(config, "LOAN_BASE_MAX_AMOUNT", 20000)
    max_mult = getattr(config, "LOAN_CREDIT_SCORE_MAX_MULTIPLIER", 3.0)
    mult = 0.2 + frac * (max_mult - 0.2)  # حتی با اعتبار خیلی پایین، حداقل ۲۰٪ سقف پایه ممکنه
    return round(base * mult)


def _active_loans(chat_id: int, uid: int) -> list[dict]:
    return [l for l in _loans[chat_id][uid] if l["status"] == "ACTIVE"]


async def _resolve_loans(chat_id: int, uid: int) -> list[str]:
    """چک Lazy برای همه‌ی وام‌های فعال: اگه قسط از موعد+Grace رد شده، جریمه‌ی
    دیرکرد + کسر اعتبار می‌خوره. اگه پشت‌سرهم به تعداد LOAN_DEFAULT_AFTER_MISSED_INSTALLMENTS
    قسط نده، وام نکول می‌شه (باقی بدهی بخشیده می‌شه، ولی اعتبار به‌شدت آسیب می‌بینه —
    مجازاتش سنگین‌تر از خودِ بدهیه، طبق فاز ۳۰: اقتصاد نباید Money Printer بشه ولی
    نکول‌کردن هم نباید راه فرار مجانی باشه)."""
    events = []
    now = time.time()
    grace_s = getattr(config, "LOAN_GRACE_PERIOD_HOURS", 48) * 3600
    period_s = getattr(config, "LOAN_INSTALLMENT_PERIOD_DAYS", 7) * 86400
    late_fee_frac = getattr(config, "LOAN_LATE_FEE_FRACTION", 0.05)
    max_missed = getattr(config, "LOAN_DEFAULT_AFTER_MISSED_INSTALLMENTS", 3)

    for loan in _active_loans(chat_id, uid):
        while now > loan["next_due_ts"] + grace_s and loan["status"] == "ACTIVE":
            late_fee = round(loan["installment_amount"] * late_fee_frac)
            loan["remaining"] += late_fee
            loan["missed_streak"] += 1
            _adjust_credit(chat_id, uid, -getattr(config, "CREDIT_SCORE_LATE_PENALTY", 25))
            loan["history"].append({"ts": now, "event": "LATE", "note": f"قسط عقب‌افتاده + {late_fee:,} جریمه"})
            events.append(f"⚠️ وام #{loan['id']}: قسط عقب افتاد، {late_fee:,} {config.CURRENCY_NAME} جریمه خورد.")
            loan["next_due_ts"] += period_s

            if loan["missed_streak"] >= max_missed:
                loan["status"] = "DEFAULTED"
                _adjust_credit(chat_id, uid, -getattr(config, "CREDIT_SCORE_DEFAULT_PENALTY", 120))
                loan["history"].append({"ts": now, "event": "DEFAULT", "note": "نکول کامل وام"})
                events.append(f"🚫 وام #{loan['id']} نکول شد! اعتبارت به‌شدت آسیب دید. تا مدتی وام جدید نمی‌گیری.")
                break
    return events


# ---------------------------------------------------------------------------
# 💰 سود سپرده‌ی کوتاه‌مدت
# ---------------------------------------------------------------------------

def pending_interest(chat_id: int, uid: int) -> int:
    balance = economy_core.get_bank_balance(chat_id, uid)
    if balance <= 0:
        return 0
    last = _last_interest_ts[chat_id].get(uid, 0)
    if last == 0:
        return 0  # اولین باره، مبنا از الان حساب می‌شه (رایگان اول کار نده)
    elapsed_days = min(
        (time.time() - last) / 86400.0,
        getattr(config, "BANK_INTEREST_CLAIM_MAX_DAYS", 14),
    )
    rate = getattr(config, "BANK_INTEREST_RATE_DAILY", 0.01) + _level_info(chat_id, uid)["interest_bonus"]
    try:
        import economy_events
        rate *= economy_events.bank_interest_multiplier(chat_id)
    except Exception as e:
        logger.warning(f"اعمال ضریب رویداد سود بانکی ناموفق بود: {e}")
    return round(balance * rate * elapsed_days)


async def _claim_interest_silent(chat_id: int, uid: int) -> int:
    """سود رو Claim می‌کنه و برمی‌گردونه (برای استفاده‌ی داخلی، مثلاً موقع نمایش بانک)."""
    amount = pending_interest(chat_id, uid)
    if amount > 0:
        await economy_core.add_bank_coins(chat_id, uid, amount, kind="bank_interest", note="سود سپرده‌ی کوتاه‌مدت")
    _last_interest_ts[chat_id][uid] = time.time()
    return amount


# ---------------------------------------------------------------------------
# 🔒 سپرده‌ی بلندمدت — بلوغ خودکار (Lazy Resolve، هر بار که کاربر با بانک کار داره چک می‌شه)
# ---------------------------------------------------------------------------

async def _resolve_matured(chat_id: int, uid: int) -> list[str]:
    events = []
    deposits = _long_deposits[chat_id][uid]
    now = time.time()
    for d in deposits:
        if d.get("matured_credited"):
            continue
        if now >= d["unlock_ts"]:
            payout = d["amount"] + round(d["amount"] * d["rate"])
            await economy_core.add_bank_coins(
                chat_id, uid, payout, kind="long_deposit_mature",
                note=f"بلوغ سپرده‌ی بلندمدت #{d['id']} ({d['days']} روزه)",
            )
            d["matured_credited"] = True
            events.append(f"🔓 سپرده‌ی بلندمدت #{d['id']} بالغ شد: +{payout} {config.CURRENCY_NAME}")
    return events


def _active_long_deposits(chat_id: int, uid: int) -> list[dict]:
    return [d for d in _long_deposits[chat_id][uid] if not d.get("matured_credited")]


# ---------------------------------------------------------------------------
# 📋 نمایش
# ---------------------------------------------------------------------------

def _fmt_remaining(unlock_ts: float) -> str:
    remaining = unlock_ts - time.time()
    if remaining <= 0:
        return "آماده‌ی برداشت"
    days = int(remaining // 86400)
    hours = int((remaining % 86400) // 3600)
    if days > 0:
        return f"{days} روز و {hours} ساعت دیگه"
    return f"{hours} ساعت دیگه"


async def bank_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بانک / حساب / /bank"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_bank"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    mature_events = await _resolve_matured(chat.id, uid)

    wallet = economy_core.get_wallet(chat.id, uid)
    bank_bal = economy_core.get_bank_balance(chat.id, uid)
    level = _level(chat.id, uid)
    info = _level_info(chat.id, uid)
    pending = pending_interest(chat.id, uid)
    active_deposits = _active_long_deposits(chat.id, uid)

    lines = [
        "╭━━━━━━━━━━━━━━━━━━━━╮",
        "🏦 بانک DIGIANTI",
        "╰━━━━━━━━━━━━━━━━━━━━╯",
        "",
        f"💰 کیف پول: {wallet} {config.CURRENCY_NAME}",
        f"🏦 بانک (کوتاه‌مدت): {bank_bal} {config.CURRENCY_NAME}",
        f"🎖️ سطح بانک: {level}",
        f"📈 نرخ سود روزانه: {(config.BANK_INTEREST_RATE_DAILY + info['interest_bonus']) * 100:.1f}٪",
    ]
    if pending > 0:
        lines.append(f"🎁 سود در انتظار Claim: {pending} {config.CURRENCY_NAME} (با «سود» بگیرش)")
    if active_deposits:
        lines.append("")
        lines.append(f"🔒 سپرده‌های بلندمدت فعال ({len(active_deposits)}/{info['max_long_deposits']}):")
        for d in active_deposits:
            lines.append(f"  • #{d['id']}: {d['amount']} {config.CURRENCY_NAME} — {_fmt_remaining(d['unlock_ts'])}")
    lines.extend(mature_events)
    await message.reply_text("\n".join(lines))
    await host.save_state()


def _parse_amount(args: list[str]) -> int | None:
    if not args:
        return None
    try:
        amount = int(args[0].replace(",", ""))
    except ValueError:
        return None
    return amount if amount > 0 else None


async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """سپرده [مبلغ] / سپرده بلندمدت [مبلغ] [روز] / /deposit <amount>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_bank"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    amount = _parse_amount(context.args)
    if amount is None:
        await message.reply_text(
            "استفاده: «سپرده [مبلغ]» یا /deposit <مبلغ>\n"
            "برای سپرده‌ی بلندمدت: «سپرده بلندمدت [مبلغ] [روز]» یا /longdeposit <مبلغ> <روز>"
        )
        return

    try:
        new_bank = await economy_core.deposit(chat.id, uid, amount)
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کیف‌پولت کافی نیست ({economy_core.get_wallet(chat.id, uid)} {config.CURRENCY_NAME}).")
        return

    if _last_interest_ts[chat.id].get(uid, 0) == 0:
        _last_interest_ts[chat.id][uid] = time.time()  # مبنای سود از همین اولین سپرده شروع بشه

    lines = [f"✅ {amount} {config.CURRENCY_NAME} سپرده شد.", f"🏦 موجودی بانک: {new_bank} {config.CURRENCY_NAME}"]
    try:
        lines += await economy_missions.record_progress(chat.id, uid, "deposit", 1)
    except Exception as e:
        logger.warning(f"ثبت پیشرفت ماموریت deposit ناموفق بود: {e}")
    await message.reply_text("\n".join(lines))
    await host.save_state()


async def long_deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """سپرده بلندمدت [مبلغ] [روز] / /longdeposit <amount> <days>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_bank"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    args = context.args or []
    durations = config.LONG_TERM_DEPOSIT_DURATIONS
    if len(args) < 2:
        options = "، ".join(f"{d} روز ({r*100:.0f}٪)" for d, r in sorted(durations.items()))
        await message.reply_text(
            f"استفاده: «سپرده بلندمدت [مبلغ] [روز]» یا /longdeposit <مبلغ> <روز>\nمدت‌های مجاز: {options}"
        )
        return

    amount = _parse_amount(args[:1])
    try:
        days = int(args[1])
    except ValueError:
        days = -1
    if amount is None or days not in durations:
        options = "، ".join(str(d) for d in sorted(durations))
        await message.reply_text(f"مبلغ نامعتبره یا مدت پشتیبانی نمی‌شه. مدت‌های مجاز: {options}")
        return

    info = _level_info(chat.id, uid)
    if len(_active_long_deposits(chat.id, uid)) >= info["max_long_deposits"]:
        await message.reply_text(
            f"❌ به سقف {info['max_long_deposits']} سپرده‌ی بلندمدت هم‌زمان رسیدی. "
            "با «ارتقای بانک» ظرفیت رو زیاد کن یا صبر کن یکی بالغ بشه."
        )
        return

    try:
        await economy_core.remove_bank_coins(
            chat.id, uid, amount, kind="long_deposit_open", note=f"باز کردن سپرده‌ی بلندمدت {days} روزه"
        )
    except economy_core.InsufficientFundsError:
        await message.reply_text(
            f"❌ موجودی بانکت کافی نیست ({economy_core.get_bank_balance(chat.id, uid)} {config.CURRENCY_NAME}). "
            "اول با «سپرده» پول رو به بانک بیار."
        )
        return

    rate = durations[days]
    now = time.time()
    deposit = {
        "id": _new_deposit_id(chat.id, uid),
        "amount": amount,
        "rate": rate,
        "opened_ts": now,
        "unlock_ts": now + days * 86400,
        "days": days,
        "matured_credited": False,
    }
    _long_deposits[chat.id][uid].append(deposit)
    await message.reply_text(
        f"🔒 سپرده‌ی بلندمدت #{deposit['id']} باز شد: {amount} {config.CURRENCY_NAME} برای {days} روز "
        f"(سود {rate*100:.0f}٪ در بلوغ = {round(amount*(1+rate))} {config.CURRENCY_NAME})"
    )
    await host.save_state()


async def long_withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """برداشت بلندمدت [شناسه] / /longwithdraw <id>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return

    uid = update.effective_user.id
    args = context.args or []
    if not args:
        active = _active_long_deposits(chat.id, uid)
        if not active:
            await message.reply_text("سپرده‌ی بلندمدت فعالی نداری.")
        else:
            ids = "، ".join(f"#{d['id']}" for d in active)
            await message.reply_text(f"شناسه رو مشخص کن: «برداشت بلندمدت [شناسه]». سپرده‌های فعال: {ids}")
        return
    try:
        dep_id = int(args[0])
    except ValueError:
        await message.reply_text("شناسه‌ی نامعتبر.")
        return

    deposits = _long_deposits[chat.id][uid]
    target = next((d for d in deposits if d["id"] == dep_id and not d.get("matured_credited")), None)
    if not target:
        await message.reply_text("همچین سپرده‌ی فعالی پیدا نشد.")
        return

    now = time.time()
    if now >= target["unlock_ts"]:
        payout = target["amount"] + round(target["amount"] * target["rate"])
        note = f"بلوغ سپرده‌ی بلندمدت #{dep_id}"
    else:
        penalty = getattr(config, "LONG_TERM_EARLY_WITHDRAW_PENALTY_PERCENT", 0.5)
        full_interest = round(target["amount"] * target["rate"])
        payout = target["amount"] + round(full_interest * (1 - penalty))
        note = f"برداشت زودهنگام سپرده‌ی بلندمدت #{dep_id} (جریمه)"

    target["matured_credited"] = True
    new_bank = await economy_core.add_bank_coins(chat.id, uid, payout, kind="long_deposit_close", note=note)
    early = now < target["unlock_ts"]
    lines = [
        f"✅ سپرده‌ی #{dep_id} بسته شد: +{payout} {config.CURRENCY_NAME}",
        f"🏦 موجودی بانک: {new_bank} {config.CURRENCY_NAME}",
    ]
    if early:
        lines.insert(1, "⚠️ چون زودتر از موعد برداشت کردی، فقط نصف سود تعلق‌گرفته رو گرفتی (اصل پولت کامل بود).")
    await message.reply_text("\n".join(lines))
    await host.save_state()


async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """برداشت [مبلغ] / /withdraw <amount> — از سپرده‌ی کوتاه‌مدت، با کارمزد."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_bank"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    amount = _parse_amount(context.args)
    if amount is None:
        await message.reply_text("استفاده: «برداشت [مبلغ]» یا /withdraw <مبلغ>")
        return

    fee_pct = getattr(config, "WITHDRAWAL_FEE_PERCENT", 0.02)
    fee = round(amount * fee_pct)
    total_needed = amount + fee
    bank_bal = economy_core.get_bank_balance(chat.id, uid)
    if bank_bal < total_needed:
        await message.reply_text(
            f"❌ موجودی بانکت کافی نیست. برای برداشت {amount} + {fee} کارمزد ({fee_pct*100:.0f}٪) "
            f"به {total_needed} {config.CURRENCY_NAME} نیاز داری (الان: {bank_bal})."
        )
        return

    await economy_core.remove_bank_coins(chat.id, uid, fee, kind="withdraw_fee", note="کارمزد برداشت از بانک")
    new_wallet = await economy_core.withdraw(chat.id, uid, amount)
    await message.reply_text(
        f"✅ {amount} {config.CURRENCY_NAME} برداشت شد (کارمزد: {fee} {config.CURRENCY_NAME}).\n"
        f"💰 موجودی کیف‌پول: {new_wallet} {config.CURRENCY_NAME}"
    )
    await host.save_state()


async def interest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """سود / /interest"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    amount = await _claim_interest_silent(chat.id, uid)
    if amount <= 0:
        await message.reply_text("فعلاً سودی برای Claim کردن نداری (یا موجودی بانکت صفره، یا هنوز زوده).")
        return
    new_bank = economy_core.get_bank_balance(chat.id, uid)
    await message.reply_text(f"🎁 سود بانکی: +{amount} {config.CURRENCY_NAME}\n🏦 موجودی بانک: {new_bank} {config.CURRENCY_NAME}")
    await host.save_state()


async def bank_upgrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارتقای بانک"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return

    uid = update.effective_user.id
    level = _level(chat.id, uid)
    next_level = level + 1
    if next_level not in config.BANK_LEVELS:
        await message.reply_text(f"🎖️ بانکت همین الان هم توی بالاترین سطحه ({level}).")
        return
    cost = config.BANK_LEVELS[next_level]["upgrade_cost"]
    try:
        await economy_core.remove_coins(chat.id, uid, cost, kind="bank_upgrade", note=f"ارتقای بانک به سطح {next_level}")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ برای ارتقا به سطح {next_level} به {cost} {config.CURRENCY_NAME} نیاز داری.")
        return

    _bank_level[chat.id][uid] = next_level
    info = config.BANK_LEVELS[next_level]
    lines = [
        f"🎉 بانکت رفت سطح {next_level}!",
        f"📈 سود روزانه‌ی اضافه: +{info['interest_bonus']*100:.1f}٪",
        f"🔒 ظرفیت سپرده‌ی بلندمدت: {info['max_long_deposits']}",
    ]
    try:
        import economy_achievements
        lines.extend(economy_achievements.check_all(chat.id, uid))
    except Exception as e:
        logger.warning(f"چک دستاوردهای اقتصادی ناموفق بود: {e}")
    await message.reply_text("\n".join(lines))
    await host.save_state()


# ---------------------------------------------------------------------------
# 💳 Loans — ارتقای فاز ۱۳
# ---------------------------------------------------------------------------

async def credit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اعتبار / /credit — امتیاز اعتباری + سقف وام + لیست وام‌های فعال."""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    events = await _resolve_loans(chat.id, uid)
    score = get_credit_score(chat.id, uid)
    lines = [
        f"💳 امتیاز اعتباری تو: {score}/850",
        f"💰 سقف وام فعلی: {max_loan_amount(chat.id, uid):,} {config.CURRENCY_NAME}",
    ]
    active = _active_loans(chat.id, uid)
    if active:
        lines.append("")
        lines.append(f"📋 وام‌های فعال ({len(active)}/{getattr(config, 'LOAN_MAX_ACTIVE', 2)}):")
        for l in active:
            due_in = l["next_due_ts"] - time.time()
            due_txt = "الان سررسیده" if due_in <= 0 else f"{round(due_in/3600)} ساعت دیگه"
            lines.append(
                f"  • #{l['id']}: باقی‌مانده {l['remaining']:,} — قسط {l['installment_amount']:,} — سررسید: {due_txt}"
            )
    lines.extend(events)
    await message.reply_text("\n".join(lines))
    await host.save_state()


async def take_loan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """وام [مبلغ] [مدت روز] / /loan <amount> <days> — مدت‌های معتبر رو با
    «اعتبار» یا بدون آرگومان می‌بینی."""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_bank"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    await _resolve_loans(chat.id, uid)
    term_options = getattr(config, "LOAN_TERM_OPTIONS", {7: 0.08, 14: 0.14, 30: 0.25})
    args = context.args or []
    if len(args) < 2:
        options_txt = "، ".join(f"{d} روزه (سود {r*100:.0f}٪)" for d, r in term_options.items())
        await message.reply_text(f"استفاده: «وام [مبلغ] [مدت روز]». مدت‌های معتبر: {options_txt}")
        return
    try:
        amount, term_days = int(args[0]), int(args[1])
    except ValueError:
        await message.reply_text("مبلغ/مدت نامعتبره.")
        return
    if term_days not in term_options:
        options_txt = "، ".join(str(d) for d in term_options)
        await message.reply_text(f"مدت نامعتبره. گزینه‌ها: {options_txt} روز")
        return
    if amount <= 0:
        await message.reply_text("مبلغ باید مثبت باشه.")
        return

    score = get_credit_score(chat.id, uid)
    min_score = getattr(config, "CREDIT_SCORE_MIN_FOR_LOAN", 400)
    if score < min_score:
        await message.reply_text(f"❌ امتیاز اعتباریت ({score}) کمتر از حد لازمه ({min_score}). با «اعتبار» وضعیتت رو ببین.")
        return
    if len(_active_loans(chat.id, uid)) >= getattr(config, "LOAN_MAX_ACTIVE", 2):
        await message.reply_text(f"❌ همین الان هم به سقف تعداد وام هم‌زمان رسیدی ({getattr(config, 'LOAN_MAX_ACTIVE', 2)} تا).")
        return
    cap = max_loan_amount(chat.id, uid)
    if amount > cap:
        await message.reply_text(f"❌ سقف وامت با این اعتبار {cap:,} {config.CURRENCY_NAME} است.")
        return

    rate = term_options[term_days]
    total_owed = round(amount * (1 + rate))
    period_days = getattr(config, "LOAN_INSTALLMENT_PERIOD_DAYS", 7)
    installments_total = max(1, round(term_days / period_days))
    installment_amount = round(total_owed / installments_total)
    now = time.time()
    loan = {
        "id": _new_loan_id(chat.id, uid), "principal": amount, "total_owed": total_owed,
        "remaining": total_owed, "rate": rate, "term_days": term_days,
        "installment_amount": installment_amount, "installments_total": installments_total,
        "installments_paid": 0, "next_due_ts": now + period_days * 86400,
        "missed_streak": 0, "status": "ACTIVE", "opened_ts": now,
        "history": [{"ts": now, "event": "OPEN", "note": f"وام {amount:,} برای {term_days} روز، سود {rate*100:.0f}٪"}],
    }
    _loans[chat.id][uid].append(loan)
    new_wallet = await economy_core.add_coins(chat.id, uid, amount, kind="LOAN", note=f"وام #{loan['id']}")
    await message.reply_text(
        f"✅ وام #{loan['id']} گرفتی: +{amount:,} {config.CURRENCY_NAME} (موجودی: {new_wallet:,}).\n"
        f"باید {installments_total} قسط {installment_amount:,}تایی بدی (هر {period_days} روز)، جمعاً {total_owed:,}."
    )
    await host.save_state()


async def pay_loan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پرداخت وام [شناسه] — قسط سررسیده رو پرداخت می‌کنه (یا باقی‌مانده اگه کمتر بود)."""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    events = await _resolve_loans(chat.id, uid)
    args = context.args or []
    if not args:
        active = _active_loans(chat.id, uid)
        if not active:
            await message.reply_text("وام فعالی نداری.")
            return
        ids = "، ".join(f"#{l['id']}" for l in active)
        await message.reply_text(f"شناسه رو مشخص کن: «پرداخت وام [شناسه]». وام‌های فعال: {ids}")
        return
    try:
        loan_id = int(args[0])
    except ValueError:
        await message.reply_text("شناسه‌ی نامعتبر.")
        return

    loan = next((l for l in _loans[chat.id][uid] if l["id"] == loan_id and l["status"] == "ACTIVE"), None)
    if not loan:
        await message.reply_text("همچین وام فعالی پیدا نشد.")
        return

    pay_amount = min(loan["installment_amount"], loan["remaining"])
    try:
        await economy_core.remove_coins(chat.id, uid, pay_amount, kind="LOAN", note=f"قسط وام #{loan_id}")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. مبلغ قسط: {pay_amount:,} {config.CURRENCY_NAME}")
        return

    loan["remaining"] -= pay_amount
    loan["installments_paid"] += 1
    loan["missed_streak"] = 0
    period_s = getattr(config, "LOAN_INSTALLMENT_PERIOD_DAYS", 7) * 86400
    loan["next_due_ts"] = time.time() + period_s
    _adjust_credit(chat.id, uid, getattr(config, "CREDIT_SCORE_ON_TIME_BONUS", 15))
    loan["history"].append({"ts": time.time(), "event": "PAYMENT", "note": f"قسط {pay_amount:,} پرداخت شد"})

    lines = [f"✅ قسط وام #{loan_id} پرداخت شد: -{pay_amount:,} {config.CURRENCY_NAME}"]
    if loan["remaining"] <= 0:
        loan["status"] = "PAID"
        lines.append(f"🎉 وام #{loan_id} کامل تسویه شد! امتیاز اعتباریت بهتر شد.")
    else:
        lines.append(f"باقی‌مانده: {loan['remaining']:,} {config.CURRENCY_NAME}")
    lines.extend(events)
    await message.reply_text("\n".join(lines))
    await host.save_state()


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "bank_level": {str(c): dict(v) for c, v in _bank_level.items()},
        "last_interest_ts": {str(c): dict(v) for c, v in _last_interest_ts.items()},
        "long_deposits": {
            str(c): {str(u): deposits for u, deposits in v.items()}
            for c, v in _long_deposits.items()
        },
        "next_id": {str(c): dict(v) for c, v in _next_id.items()},
        # فاز ۱۳ ارتقا: Loans/Credit Score
        "credit_score": {str(c): dict(v) for c, v in _credit_score.items()},
        "loans": {str(c): {str(u): loans for u, loans in v.items()} for c, v in _loans.items()},
        "next_loan_id": {str(c): dict(v) for c, v in _next_loan_id.items()},
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
            logger.warning(f"ذخیره‌ی state موتور Bank ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور Bank ناموفق بود: {e}")
        return

    for cid, v in data.get("bank_level", {}).items():
        _bank_level[int(cid)] = {int(u): lvl for u, lvl in v.items()}
    for cid, v in data.get("last_interest_ts", {}).items():
        _last_interest_ts[int(cid)] = {int(u): ts for u, ts in v.items()}
    for cid, v in data.get("long_deposits", {}).items():
        for uid, deposits in v.items():
            _long_deposits[int(cid)][int(uid)] = deposits
    for cid, v in data.get("next_id", {}).items():
        _next_id[int(cid)] = {int(u): n for u, n in v.items()}

    # فاز ۱۳ ارتقا: Loans/Credit Score (کلیدهای جدید؛ روی داده‌ی قدیمی که
    # این‌ها رو نداره، .get() با dict خالی امن عمل می‌کنه)
    for cid, v in data.get("credit_score", {}).items():
        _credit_score[int(cid)] = {int(u): s for u, s in v.items()}
    for cid, v in data.get("loans", {}).items():
        for uid, loans in v.items():
            _loans[int(cid)][int(uid)] = loans
    for cid, v in data.get("next_loan_id", {}).items():
        _next_loan_id[int(cid)] = {int(u): n for u, n in v.items()}

    logger.info("وضعیت موتور Bank از فایل بارگذاری شد.")
