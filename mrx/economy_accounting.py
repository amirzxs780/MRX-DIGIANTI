# -*- coding: utf-8 -*-
"""
DIGNANTI ECONOMY ACCOUNTING — فاز ۵ (V18: بانک و حسابداری)
=====================================================================
بخشِ «سیستمِ حسابداری و گزارش‌های مالی» (بخشِ ۱۳ نقشه‌ی V18). این ماژول
هیچ Stateی برایِ خودش نداره و هیچ پولی جابه‌جا نمی‌کنه - فقط از روی داده‌ی
واقعیِ ماژول‌هایِ دیگه (Ledgerِ economy_core، economy_business،
economy_bank) گزارش می‌سازه. به همین خاطر save_state/load_state هم نداره.

دقتِ گزارش (صادقانه، طبقِ خواسته‌ی «از محاسباتِ قابل‌توضیح تولید بشن»):
  • درآمد/هزینه/سودِخالص/گردشِ کل همیشه دقیقن - مستقیم از مبلغِ واقعیِ هر
    تراکنش توی Ledger جمع می‌زنن.
  • دسته‌بندیِ تراکنش‌ها طبقِ نوعِ نرمالایزشده‌ی economy_core (BUSINESS،
    SALE، REWARD، TAX، LOAN، ...) انجام می‌شه؛ برایِ اون‌هایی که نوعِ
    نرمالایزشده‌شون OTHER می‌شه (مثلِ کارکردن/حقوق/بورس/مزایده که Kindِ
    خامِ خودشون نرمالایزشده‌ی معادل ندارن)، یه دسته‌بندیِ کمکیِ تقریبی بر
    اساسِ شرحِ تراکنش (source) انجام می‌شه - این بخش Best-Effort‌ـه و
    صریحاً همینجوری اعلام می‌شه، نه قطعی.
  • Ledgerِ مرکزی سقفِ ۲۰٬۰۰۰ رکورد داره (economy_core.MAX_LEDGER_SIZE)؛
    اگه فعالیتِ یه کاربر توی یه دوره از ACCOUNTING_LEDGER_SCAN_LIMIT بیشتر
    باشه، گزارش ممکنه کاملِ دوره رو پوشش نده - این مورد صریحاً توی خروجی
    اعلام می‌شه (truncated).
"""

from __future__ import annotations

import time
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

import config
import economy_core

_PERIOD_ALIASES = {"کل": None, "همه": None}
_PERIOD_ALIASES.update({k: k for k in getattr(config, "ACCOUNTING_PERIODS", {})})


def _period_since_ts(period_key: str | None) -> float | None:
    if not period_key:
        return None
    seconds = getattr(config, "ACCOUNTING_PERIODS", {}).get(period_key)
    if seconds is None:
        return None
    return time.time() - seconds


def _categorize_other(source: str) -> str:
    """دسته‌بندیِ تقریبیِ تراکنش‌هایی که نوعِ نرمالایزشده‌شون OTHER شده،
    بر اساسِ شرحِ (source) که خودِ هر ماژول موقعِ ثبتِ تراکنش می‌نویسه."""
    s = source or ""
    if s.startswith("کار —") or s.startswith("حقوق"):
        return "شغل"
    if "سهام" in s or "IPO" in s:
        return "بورس سهام"
    if "مزایده" in s:
        return "مزایده"
    if "بدهی" in s:
        return "بدهیِ کسب‌وکار"
    if "سپرده" in s or "برداشت" in s:
        return "بانک"
    return "سایر"


def income_expense_report(chat_id: int, uid: int, period: str | None = None) -> dict:
    since_ts = _period_since_ts(period)
    limit = getattr(config, "ACCOUNTING_LEDGER_SCAN_LIMIT", 3000)
    txns = economy_core.get_transaction_history(chat_id, uid, limit=limit, since_ts=since_ts)

    income_by_cat: dict[str, int] = defaultdict(int)
    expense_by_cat: dict[str, int] = defaultdict(int)
    total_income = total_expense = 0

    for t in txns:
        amt = t["amount"]
        ttype = t.get("type", "OTHER")
        cat = ttype if ttype != "OTHER" else _categorize_other(t.get("source", ""))
        if amt > 0:
            income_by_cat[cat] += amt
            total_income += amt
        elif amt < 0:
            expense_by_cat[cat] += -amt
            total_expense += -amt

    return {
        "period": period,
        "since_ts": since_ts,
        "income_by_cat": dict(income_by_cat),
        "expense_by_cat": dict(expense_by_cat),
        "total_income": total_income,
        "total_expense": total_expense,
        "net": total_income - total_expense,
        "turnover": total_income + total_expense,
        "txn_count": len(txns),
        "truncated": len(txns) >= limit,
    }


def assets_breakdown(chat_id: int, uid: int) -> dict:
    wallet = economy_core.get_wallet(chat_id, uid)
    bank = economy_core.get_bank_balance(chat_id, uid)
    gross_net_worth = economy_core.get_net_worth(chat_id, uid)
    other_assets = gross_net_worth - wallet - bank
    return {
        "wallet": wallet, "bank": bank,
        "other_assets": other_assets, "gross_net_worth": gross_net_worth,
    }


def liabilities_breakdown(chat_id: int, uid: int) -> dict:
    business_debt = 0
    try:
        import economy_business
        business_debt = sum(b.get("debt", 0) for b in economy_business.owned(chat_id, uid))
    except Exception:
        pass
    loan_debt = 0
    try:
        import economy_bank
        loan_debt = economy_bank.total_active_loan_debt(chat_id, uid)
    except Exception:
        pass
    return {
        "business_debt": business_debt, "loan_debt": loan_debt,
        "total_liabilities": business_debt + loan_debt,
    }


def full_statement(chat_id: int, uid: int, period: str | None = None) -> dict:
    report = income_expense_report(chat_id, uid, period)
    assets = assets_breakdown(chat_id, uid)
    liabilities = liabilities_breakdown(chat_id, uid)
    report["assets"] = assets
    report["liabilities"] = liabilities
    report["real_net_worth"] = assets["gross_net_worth"] - liabilities["total_liabilities"]
    return report


def business_pnl(chat_id: int, uid: int) -> list[dict]:
    """سود و زیانِ تجمعی (از ابتدایِ تأسیس) برایِ هر کسب‌وکار - مستقیم از
    فیلدهایِ lifetime_revenue/lifetime_expenseِ خودِ economy_business."""
    import economy_business
    out = []
    for b in economy_business.owned(chat_id, uid):
        info = economy_business.TYPES.get(b["type"], {"name": b["type"]})
        revenue = b.get("lifetime_revenue", 0)
        expense = b.get("lifetime_expense", 0)
        out.append({
            "id": b["id"], "name": info.get("name", b["type"]),
            "lifetime_revenue": revenue, "lifetime_expense": expense,
            "lifetime_net": revenue - expense,
            "debt": b.get("debt", 0), "bankrupt": b.get("bankrupt", False),
            "current_value": economy_business.current_value(b),
        })
    return out


# ---------------------------------------------------------------------------
# 🖊 نمایش متنی
# ---------------------------------------------------------------------------

def format_statement_text(chat_id: int, uid: int, period: str | None = None) -> str:
    s = full_statement(chat_id, uid, period)
    period_label = period or "کل (از ابتدا)"
    lines = [f"🧾 صورت‌حسابِ مالی — دوره: {period_label}", "─────────────────"]

    lines.append(f"📈 درآمدِ کل: {s['total_income']:,} {config.CURRENCY_NAME}")
    for cat, amt in sorted(s["income_by_cat"].items(), key=lambda x: -x[1]):
        lines.append(f"   • {cat}: {amt:,}")

    lines.append(f"📉 هزینه‌ی کل: {s['total_expense']:,} {config.CURRENCY_NAME}")
    for cat, amt in sorted(s["expense_by_cat"].items(), key=lambda x: -x[1]):
        lines.append(f"   • {cat}: {amt:,}")

    net_emoji = "✅" if s["net"] >= 0 else "🔻"
    lines.append(
        f"{net_emoji} سودِ خالصِ این دوره: {s['net']:,} — گردشِ حساب: {s['turnover']:,} ({s['txn_count']} تراکنش)"
    )
    if s.get("truncated"):
        lines.append("⚠️ فعالیتت توی این دوره خیلی زیاد بوده؛ این گزارش ممکنه کاملِ دوره رو پوشش نده.")

    a, li = s["assets"], s["liabilities"]
    lines.append("")
    lines.append("💼 دارایی و بدهی:")
    lines.append(f"   کیف‌پول: {a['wallet']:,} — بانک: {a['bank']:,} — سایرِ دارایی‌ها: {a['other_assets']:,}")
    if li["total_liabilities"] > 0:
        lines.append(f"   بدهیِ کسب‌وکارها: {li['business_debt']:,} — بدهیِ وام: {li['loan_debt']:,}")
    lines.append(f"💎 ارزشِ خالصِ واقعی (با احتسابِ بدهی): {s['real_net_worth']:,} {config.CURRENCY_NAME}")
    return "\n".join(lines)


def format_business_pnl_text(chat_id: int, uid: int) -> str:
    rows = business_pnl(chat_id, uid)
    lines = ["📊 سود و زیانِ کسب‌وکارها (تجمعی از ابتدایِ تأسیس)", "─────────────────"]
    if not rows:
        lines.append("هیچ کسب‌وکاری نداری.")
        return "\n".join(lines)
    total_net = 0
    for r in rows:
        tag = " 🚫ورشکسته" if r["bankrupt"] else ""
        net_emoji = "✅" if r["lifetime_net"] >= 0 else "🔻"
        lines.append(
            f"#{r['id']} {r['name']}{tag}: درآمد {r['lifetime_revenue']:,} — هزینه {r['lifetime_expense']:,} "
            f"— {net_emoji} خالص: {r['lifetime_net']:,} — ارزشِ فعلی: {r['current_value']:,}"
        )
        total_net += r["lifetime_net"]
    lines.append("")
    lines.append(f"جمعِ سودِ خالصِ همه‌ی کسب‌وکارها: {total_net:,} {config.CURRENCY_NAME}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 📨 Command Handlers
# ---------------------------------------------------------------------------

async def statement_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """صورت‌حساب [روزانه|هفتگی|ماهانه|فصلی|کل] / /statement [period]"""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    period = None
    if args:
        key = args[0].strip()
        if key not in _PERIOD_ALIASES:
            options = "، ".join(getattr(config, "ACCOUNTING_PERIODS", {}).keys())
            await message.reply_text(f"دوره نامعتبره. گزینه‌ها: {options}، یا بدونِ آرگومان برایِ کل.")
            return
        period = _PERIOD_ALIASES[key]
    uid = update.effective_user.id
    await message.reply_text(format_statement_text(chat.id, uid, period))


async def business_pnl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """سود و زیان شرکت‌ها / /businesspnl"""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    await message.reply_text(format_business_pnl_text(chat.id, uid))
