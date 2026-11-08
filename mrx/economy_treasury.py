# -*- coding: utf-8 -*-
"""
DIGNANTI ECONOMY TREASURY — فاز ۷ (V18: مالیات و دولت)
=====================================================================
بخشِ ۱۰ (سیستمِ مالیات و دولت). قبل از این فاز، دو جا توی پروژه «مالیات»
محاسبه می‌شد ولی مقصد نداشت:
  • economy_business.pay_business_tax_command: مبلغ با remove_coins
    مستقیم از چرخه خارج می‌شد.
  • economy_property._collect_one: سهمِ مالیات از اجاره فقط از دستِ مالک
    کم می‌شد، هیچ‌وقت جایی وجود پیدا نمی‌کرد.
این ماژول یه خزانه‌ی عمومیِ واقعی برایِ هر گروه می‌سازه؛ هر دو مالیاتِ
بالا الان بهش واریز می‌شن - بدونِ اینکه مبلغِ پرداختیِ کاربر یا رفتارِ
دیده‌شده تغییر کنه، فقط مقصدِ پول مشخص شد.

مالیاتِ جدید (اختیاری، پیش‌فرض خاموش): economy_jobs می‌تونه قبل از
واریزِ درآمدِ کار/حقوق، collect_income_tax رو صدا بزنه. چون
TREASURY_DEFAULT_INCOME_TAX_RATE = 0.0، تا یه ادمین دستی با «تنظیم مالیات
[درصد]» یه نرخ بذاره، هیچ گروهی هیچ تغییرِ رفتاری نمی‌بینه - کاملاً
Backward-Compatible.

خرجِ خزانه (بخشِ ۱۰: «پروژه‌هایِ عمرانی»، «توسعه‌ی زیرساخت»): از طریقِ
spend() به‌صورتِ عمومی در دسترسِ ماژول‌هایِ دیگه‌ست؛ economy_infrastructure
یه تابعِ fund_from_treasury داره که دقیقاً همینو مصرف می‌کنه.

این ماژول هیچ‌وقت مستقیم wallet/bank کاربرها رو دست‌کاری نمی‌کنه - فقط
balanceِ خودِ خزانه (که مالِ هیچ کاربرِ خاصی نیست) رو تغییر می‌ده.
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

logger = logging.getLogger("economy_treasury")

STATE_FILE = getattr(config, "ECONOMY_TREASURY_STATE_FILE", "economy_treasury_state.json")
STATE_VERSION = 1

_save_lock = asyncio.Lock()


class TreasuryError(Exception):
    pass


# chat_id -> {"balance", "total_collected", "total_spent", "income_tax_rate", "history": [...]}
_treasury: dict[int, dict] = defaultdict(dict)


def _t(chat_id: int) -> dict:
    d = _treasury[chat_id]
    if not d:
        d["balance"] = 0
        d["total_collected"] = 0
        d["total_spent"] = 0
        d["income_tax_rate"] = getattr(config, "TREASURY_DEFAULT_INCOME_TAX_RATE", 0.0)
        d["history"] = []
    return d


def _log(chat_id: int, kind: str, amount: int, note: str) -> None:
    t = _t(chat_id)
    max_len = getattr(config, "TREASURY_HISTORY_MAX_LEN", 200)
    t["history"].append({"ts": time.time(), "kind": kind, "amount": amount, "note": note})
    if len(t["history"]) > max_len:
        del t["history"][0: len(t["history"]) - max_len]


# ---------------------------------------------------------------------------
# 💰 عملیاتِ پایه
# ---------------------------------------------------------------------------

def get_balance(chat_id: int) -> int:
    return _t(chat_id)["balance"]


def deposit(chat_id: int, amount: int, source: str, note: str = "") -> int:
    """واریزِ درآمد به خزانه (مالیات، عوارض، ...). amount باید مثبت باشه.
    برمی‌گردونه موجودیِ جدیدِ خزانه."""
    if amount <= 0:
        return get_balance(chat_id)
    t = _t(chat_id)
    t["balance"] += amount
    t["total_collected"] += amount
    _log(chat_id, f"income:{source}", amount, note)
    return t["balance"]


def spend(chat_id: int, amount: int, purpose: str, note: str = "") -> int:
    """خرجِ خزانه برایِ یه پروژه‌ی عمومی. اگه موجودیِ خزانه کافی نباشه،
    TreasuryError می‌ده و هیچ تغییری اعمال نمی‌شه."""
    if amount <= 0:
        raise TreasuryError("مبلغ باید مثبت باشه.")
    t = _t(chat_id)
    if t["balance"] < amount:
        raise TreasuryError(f"موجودیِ خزانه کافی نیست ({t['balance']:,} از {amount:,}).")
    t["balance"] -= amount
    t["total_spent"] += amount
    _log(chat_id, f"spend:{purpose}", -amount, note)
    return t["balance"]


# ---------------------------------------------------------------------------
# 🏛 مالیاتِ درآمد (اختیاری، پیش‌فرض خاموش - فقط ادمین روشنش می‌کنه)
# ---------------------------------------------------------------------------

def get_income_tax_rate(chat_id: int) -> float:
    return _t(chat_id)["income_tax_rate"]


def set_income_tax_rate(chat_id: int, rate: float) -> float:
    max_rate = getattr(config, "TREASURY_MAX_INCOME_TAX_RATE", 0.25)
    rate = max(0.0, min(max_rate, rate))
    _t(chat_id)["income_tax_rate"] = rate
    return rate


def collect_income_tax(chat_id: int, gross_amount: int, source: str) -> int:
    """gross_amount رو با نرخِ فعلیِ مالیاتِ درآمدِ گروه تقسیم می‌کنه: سهمِ
    مالیات به خزانه واریز می‌شه و باقی‌مونده (چیزی که باید واقعاً به کاربر
    پرداخت بشه) برگردونده می‌شه. با نرخِ پیش‌فرضِ صفر، خروجی دقیقاً همون
    gross_amount می‌مونه - هیچ رفتارِ موجودی عوض نمی‌شه."""
    rate = get_income_tax_rate(chat_id)
    if rate <= 0 or gross_amount <= 0:
        return gross_amount
    tax = round(gross_amount * rate)
    if tax <= 0:
        return gross_amount
    deposit(chat_id, tax, source=f"مالیاتِ درآمد ({source})", note=f"{int(rate*100)}٪ از {gross_amount:,}")
    return gross_amount - tax


# ---------------------------------------------------------------------------
# 🖊 نمایش متنی
# ---------------------------------------------------------------------------

def format_treasury_text(chat_id: int) -> str:
    t = _t(chat_id)
    lines = [
        "🏛 خزانه‌ی عمومیِ گروه", "─────────────────",
        f"💰 موجودیِ فعلی: {t['balance']:,} {config.CURRENCY_NAME}",
        f"📈 مجموعِ دریافتی: {t['total_collected']:,}",
        f"📉 مجموعِ هزینه‌شده: {t['total_spent']:,}",
        f"🧾 نرخِ مالیاتِ درآمد: {t['income_tax_rate']*100:.0f}٪",
        "",
        "منابع: مالیاتِ کسب‌وکار، مالیاتِ اجاره‌ی ملک"
        + (", مالیاتِ درآمدِ شغلی" if t["income_tax_rate"] > 0 else ""),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 📨 Command Handlers
# ---------------------------------------------------------------------------

async def treasury_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خزانه / /treasury"""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    await message.reply_text(format_treasury_text(chat.id))


async def set_tax_rate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تنظیم مالیات [درصد] / /settaxrate <percent> — فقط ادمینِ اقتصاد (طبقِ
    همون مجوزِ manage_economy که economy_admin.py استفاده می‌کنه)"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return

    if not host.has_permission(update.effective_user.id, "manage_economy"):
        await message.reply_text("❌ فقط ادمینِ اقتصاد می‌تونه نرخِ مالیات رو تنظیم کنه.")
        return

    args = context.args or []
    if not args:
        max_rate = getattr(config, "TREASURY_MAX_INCOME_TAX_RATE", 0.25) * 100
        await message.reply_text(f"استفاده: «تنظیم مالیات [درصد]» (بینِ ۰ تا {max_rate:.0f})")
        return
    try:
        percent = float(args[0].replace("%", ""))
    except ValueError:
        await message.reply_text("درصدِ نامعتبر.")
        return

    new_rate = set_income_tax_rate(chat.id, percent / 100)
    await message.reply_text(f"✅ نرخِ مالیاتِ درآمد روی {new_rate*100:.0f}٪ تنظیم شد.")
    await host.save_state()


async def spend_treasury_on_infrastructure_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خرج خزانه برای زیرساخت [نوع] [مبلغ] / /treasuryfund <type> <amount> — فقط ادمینِ اقتصاد"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not host.has_permission(update.effective_user.id, "manage_economy"):
        await message.reply_text("❌ فقط ادمینِ اقتصاد می‌تونه خزانه رو خرج کنه.")
        return

    args = context.args or []
    if len(args) < 2:
        await message.reply_text("استفاده: «خرج خزانه برای زیرساخت [نوع] [مبلغ]»")
        return

    import economy_infrastructure
    key = economy_infrastructure._resolve_type(args[0])
    if not key:
        await message.reply_text("این نوع زیرساخت رو نمی‌شناسم.")
        return
    try:
        amount = int(args[1].replace(",", ""))
    except ValueError:
        await message.reply_text("مبلغ نامعتبره.")
        return

    try:
        result = economy_infrastructure.fund_from_treasury(chat.id, key, amount)
    except (TreasuryError, economy_infrastructure.InfrastructureError) as e:
        await message.reply_text(f"❌ {e}")
        return

    lines = [f"✅ {amount:,} {config.CURRENCY_NAME} از خزانه برایِ {economy_infrastructure.TYPES[key]['name']} خرج شد."]
    if result["leveled_up"]:
        lines.append(f"🎉 {economy_infrastructure.TYPES[key]['name']} به Levelِ {result['level']} ارتقا پیدا کرد!")
    await message.reply_text("\n".join(lines))
    await host.save_state()


def _host():
    import bot as host
    return host


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {"version": STATE_VERSION, "treasury": {str(c): v for c, v in _treasury.items() if v}}


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
            logger.warning(f"ذخیره‌ی state خزانه ناموفق بود: {e}")


def load_state():
    data = None
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"خوندن state خزانه ناموفق بود: {e}؛ تلاش برای بازیابی از بکاپ...")
            data = None
    if data is None:
        bak = STATE_FILE + ".bak"
        if os.path.exists(bak):
            try:
                with open(bak, encoding="utf-8") as f:
                    data = json.load(f)
                logger.warning("state اصلیِ خزانه خراب/نامعتبر بود؛ از بکاپ بازیابی شد.")
            except Exception as e:
                logger.warning(f"خوندنِ بکاپِ خزانه هم ناموفق بود: {e}")
                return
        else:
            return

    for cid_s, t in data.get("treasury", {}).items():
        cid = int(cid_s)
        _treasury[cid] = {
            "balance": int(t.get("balance", 0)),
            "total_collected": int(t.get("total_collected", 0)),
            "total_spent": int(t.get("total_spent", 0)),
            "income_tax_rate": float(t.get("income_tax_rate", 0.0)),
            "history": list(t.get("history", [])),
        }

    logger.info("وضعیتِ خزانه از فایل بارگذاری شد.")
