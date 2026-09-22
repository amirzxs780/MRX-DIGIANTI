# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY CORE — فاز ۱
==============================
این ماژول «مرکز پول» کل DIGIANTI Economy هست. طبق درخواست معماری:

    هیچ Module جدیدی نباید مستقیماً wallet[user] += / -= انجام بده؛
    همه باید از Economy Core عبور کنن.

همون الگوی lock_engine / spam_engine / moderation_engine / profile_engine /
economy_engine رو دنبال می‌کنه: state مستقل خودشو توی فایل JSON جدا نگه
می‌داره، در زمان اجرا `import bot as host` می‌کنه (جلوگیری از Import حلقه‌ای)،
و به هیچ‌کدوم از Handlerها/Stateهای فعلی (`_wallet`, `/balance`, `/pay`,
`/shop`, ...) دست نمی‌زنه — فقط یه لایه‌ی Adapter/Integration روش می‌ذاره.

این فاز چیزی رو از bot.py حذف نمی‌کنه؛ فقط دو خط اضافه شده (import +
هوک توی load_state/save_state) که پایین‌تر توضیح داده شده.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📦 این فاز شامل:
    • توابع مرکزی پول: get_balance / add_coins / remove_coins /
      transfer_coins / deposit / withdraw / can_afford / get_net_worth /
      get_wallet / get_bank_balance / record_transaction
    • Bank state جدید (فاز ۴ روش /bank /deposit /withdraw می‌سازه؛ اینجا
      فقط منطق و ذخیره‌سازیش آماده می‌شه)
    • Lock per-user برای جلوگیری از Race Condition روی دستورهای هم‌زمان
    • Atomic Save (tmp + os.replace) + Backup فایل قبلی + بازیابی از Backup
      در صورت خراب بودن فایل اصلی
    • Migration Versioning (ECONOMY_STATE_VERSION) — Idempotent
    • Net Worth Registry: ماژول‌های بعدی (Property/City/Pet/Market) بدون
      اینکه Core مجبور باشه اونا رو Import کنه، می‌تونن مقدار خودشونو به
      Net Worth اضافه کنن (register_net_worth_source)
    • Anti-Abuse پایه: عدم امکان موجودی منفی، جلوگیری از Self-Transfer
      با مبلغ صفر/منفی
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from collections import defaultdict
from typing import Callable

import config

logger = logging.getLogger("economy_core")

STATE_FILE = getattr(config, "ECONOMY_CORE_STATE_FILE", "economy_core_state.json")

# نسخه‌ی ساختار Stateِ Economy Core. هر بار ساختار داده عوض شد، این رو ببر بالا
# و منطق Migration مربوطه رو زیر migrate_state() اضافه کن. Migration باید
# Idempotent باشه (اجرای دوباره‌ش نباید داده رو خراب کنه).
# نسخه ۲ (فاز ۱ ارتقا): اضافه‌شدن Transaction Ledger واقعی (transaction_id,
# status, idempotency_key). داده‌ی قدیمی از دست نمی‌ره؛ صرفاً یک ledger خالی
# برای رکوردهای گذشته (که transaction_id نداشتن) ساخته می‌شه.
ECONOMY_STATE_VERSION = 2

# انواع رسمی تراکنش (طبق مستند معماری). هر ماژولی که kind/type جدید لازم
# داره باید از همین لیست استفاده کنه؛ مقادیر ناشناخته به OTHER نگاشت می‌شن.
TRANSACTION_TYPES = {
    "REWARD", "JOB", "MISSION", "PURCHASE", "SALE", "TRANSFER", "TAX",
    "FINE", "BANK_DEPOSIT", "BANK_WITHDRAW", "INVESTMENT", "LOAN",
    "INSURANCE", "PROPERTY", "VEHICLE", "BUSINESS", "EVENT", "OTHER",
}

# انواعی که Internal محسوب می‌شن (طبق قانون: Transfer/Deposit/Withdraw پول
# جدید به اقتصاد اضافه نمی‌کنن، فقط جابه‌جایی داخلی‌ان). economy_balance.py
# می‌تونه از این‌ست برای فیلتر کردن «تولید پول واقعی» استفاده کنه.
INTERNAL_TRANSACTION_TYPES = {"TRANSFER", "BANK_DEPOSIT", "BANK_WITHDRAW"}

TXN_COMPLETED = "COMPLETED"
TXN_ROLLED_BACK = "ROLLED_BACK"

# سقف تعداد رکورد Ledger که در حافظه/فایل نگه داشته می‌شه (جلوگیری از رشد
# بی‌نهایت فایل State). قدیمی‌ترین رکوردها هرس می‌شن؛ برای حسابرسی طولانی‌مدت
# باید به Export/DB جدا منتقل بشه (خارج از اسکوپ همین فاز).
MAX_LEDGER_SIZE = 20000


# ---------------------------------------------------------------------------
# 💾 State
# ---------------------------------------------------------------------------

# 🏦 موجودی بانکی هر کاربر: chat_id -> user_id -> coins
_bank = defaultdict(lambda: defaultdict(int))

# مجموع کل واریز/برداشت از بانک (برای آمار/Achievement بعدی)
_total_deposited = defaultdict(lambda: defaultdict(int))
_total_withdrawn = defaultdict(lambda: defaultdict(int))

_save_lock = asyncio.Lock()

# 📊 فاز ۹ (Module 26: Balance Engine) — مجموع Source/Sink به تفکیک kind، در
# سطح کل گروه (نه per-user). economy_balance.py از این‌ها برای گزارش «سلامت
# اقتصاد» استفاده می‌کنه. خودِ record_transaction این‌ها رو آپدیت می‌کنه، پس
# هیچ ماژولی مجبور نیست جداگونه چیزی صدا بزنه.
_source_totals = defaultdict(lambda: defaultdict(int))   # chat_id -> kind -> مجموع مثبت
_sink_totals = defaultdict(lambda: defaultdict(int))      # chat_id -> kind -> مجموع منفی (قدرمطلق)

# 🔒 Lock per (chat_id, user_id) برای عملیات حساس پولی (جلوگیری از Race
# Condition وقتی دو تا Command هم‌زمان روی موجودی یه نفر کار می‌کنن).
# دیکشنری خودش نیاز به Lock نداره چون asyncio تک‌ترده و ساختنِ یه کلید جدید
# هیچ‌وقت بین دو await قطع نمی‌شه.
_user_locks: dict[tuple[int, int], asyncio.Lock] = {}

# 📊 Net Worth Registry — ماژول‌های بعدی (Property/City/Pet/Market/...) با
# register_net_worth_source(fn) خودشونو ثبت می‌کنن. fn باید sync و سریع باشه
# (فقط خوندن از حافظه، نه I/O). این باعث می‌شه Economy Core مجبور نباشه
# اون ماژول‌ها رو Import کنه (جلوگیری از Import حلقه‌ای و Coupling).
_net_worth_sources: list[Callable[[int, int], int]] = []

# 🧊 Economy Freeze — فاز ۲۶ (Admin Center): وقتی یه گروه فریزه، عملیات مالی
# معمولی (add/remove/transfer) مسدود می‌شن؛ ابزارهای ادمینی (admin_set_balance/
# admin_adjust_balance/rollback_transaction) عمداً مستثنی‌ان چون ادمین ممکنه
# لازم باشه دقیقاً حین فریز، اصلاح دستی انجام بده.
_frozen_chats: set = set()

# 🧾 Transaction Ledger واقعی (فاز ۱/۲ ارتقا)
# transaction_id -> رکورد کامل تراکنش
_ledger: dict[str, dict] = {}
# ترتیب درج (برای هرس‌کردن قدیمی‌ترین‌ها وقتی از MAX_LEDGER_SIZE رد شد)
_ledger_order: list[str] = []
# chat_id -> user_id -> [transaction_id, ...] به ترتیب زمانی (برای تاریخچه‌ی سریع)
_ledger_by_user: defaultdict[int, defaultdict[int, list]] = defaultdict(lambda: defaultdict(list))
# idempotency_key -> transaction_id (جلوگیری از Duplicate Reward/Double Spend)
_idempotency_index: dict[str, str] = {}


class InsufficientFundsError(Exception):
    """وقتی کاربر پول کافی (توی کیف‌پول یا بانک، بسته به عملیات) نداشته باشه."""


class DuplicateTransactionError(Exception):
    """وقتی همون idempotency_key قبلاً یک تراکنش موفق ثبت کرده باشه."""

    def __init__(self, transaction_id: str):
        super().__init__(f"تراکنش تکراری؛ transaction_id قبلی: {transaction_id}")
        self.transaction_id = transaction_id


class TransactionNotFoundError(Exception):
    pass


class RollbackError(Exception):
    pass


class EconomyFrozenError(Exception):
    """وقتی اقتصاد یه گروه فریز شده و کسی می‌خواد عملیات مالی معمولی انجام بده."""


def is_frozen(chat_id: int) -> bool:
    return chat_id in _frozen_chats


def set_frozen(chat_id: int, value: bool) -> None:
    if value:
        _frozen_chats.add(chat_id)
    else:
        _frozen_chats.discard(chat_id)


def register_net_worth_source(fn: Callable[[int, int], int]) -> None:
    """ماژول‌های بعدی (شهر، ملک، پت، سرمایه‌گذاری، ...) یه تابع
    fn(chat_id, user_id) -> int می‌دن که مقدار داراییِ اون ماژول رو برمی‌گردونه.
    """
    _net_worth_sources.append(fn)


def _host():
    import bot as host
    return host


def _lock_for(chat_id: int, uid: int) -> asyncio.Lock:
    key = (chat_id, uid)
    lock = _user_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[key] = lock
    return lock


# ---------------------------------------------------------------------------
# 💾 Persistence — Atomic Save + Backup + Recovery + Migration
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "version": ECONOMY_STATE_VERSION,
        "bank": {str(c): dict(v) for c, v in _bank.items()},
        "total_deposited": {str(c): dict(v) for c, v in _total_deposited.items()},
        "total_withdrawn": {str(c): dict(v) for c, v in _total_withdrawn.items()},
        "source_totals": {str(c): dict(v) for c, v in _source_totals.items()},
        "sink_totals": {str(c): dict(v) for c, v in _sink_totals.items()},
        # فاز ۱/۲ ارتقا: Ledger واقعی + Idempotency Index
        "ledger": _ledger,
        "ledger_order": _ledger_order,
        "idempotency_index": _idempotency_index,
        "frozen_chats": list(_frozen_chats),
    }


def _write_state_file(data: dict):
    tmp = STATE_FILE + ".tmp"
    # Backup از نسخه‌ی قبلی قبل از جایگزینی (در صورت خراب‌شدن نسخه‌ی جدید
    # وسط نوشتن، نسخه‌ی قبلی از دست نمی‌ره).
    if os.path.exists(STATE_FILE):
        try:
            shutil.copy2(STATE_FILE, STATE_FILE + ".bak")
        except Exception as e:
            logger.warning(f"بکاپ گرفتن از state بانک ناموفق بود: {e}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_FILE)  # Atomic روی همه‌ی سیستم‌عامل‌های POSIX/Windows


async def save_state():
    async with _save_lock:
        try:
            await asyncio.to_thread(_write_state_file, _collect_state())
        except Exception as e:
            logger.warning(f"ذخیره‌ی state موتور Economy Core ناموفق بود: {e}")


def migrate_state(data: dict) -> dict:
    """Migration رو اینجا اضافه کن. باید Idempotent باشه (اجرای دوباره
    نباید داده رو خراب کنه) — همیشه بر اساس data["version"] فعلی تصمیم بگیر،
    نه اینکه فرض کنی تابع فقط یه بار اجرا می‌شه."""
    version = data.get("version", 0)
    if version < 1:
        # نسخه‌ی ۰ -> ۱: فقط اضافه‌شدنِ بانک به Economy، داده‌ی قدیمی‌ای برای
        # مهاجرت نیست (بانک قبلاً وجود نداشته). صرفاً ورژن رو ست می‌کنیم.
        data["version"] = 1
    if version < 2:
        # نسخه‌ی ۱ -> ۲: اضافه‌شدن Transaction Ledger واقعی. رکوردهای قدیمی
        # (که با record_transaction ساده ثبت شده بودن) توی economy_engine
        # می‌مونن دست‌نخورده؛ اینجا فقط ساختار خالی ledger ساخته می‌شه تا از
        # این به بعد هر تراکنش جدید transaction_id/status داشته باشه.
        data.setdefault("ledger", {})
        data.setdefault("ledger_order", [])
        data.setdefault("idempotency_index", {})
        data["version"] = 2
    # نسخه‌های بعدی این‌جا اضافه می‌شن: if version < 3: ...
    return data


def _load_from_file(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"خوندن {path} ناموفق بود: {e}")
        return None


def load_state():
    data = _load_from_file(STATE_FILE)
    if data is None:
        # فایل اصلی نبود یا خراب بود -> تلاش برای بازیابی از بکاپ
        backup = _load_from_file(STATE_FILE + ".bak")
        if backup is not None:
            logger.warning("state اصلی Economy Core خراب/نامعتبر بود؛ از بکاپ بازیابی شد.")
            data = backup
        else:
            return  # نه فایل اصلی نه بکاپ — شروع تمیز (کاربر جدید/فاز جدید)

    try:
        data = migrate_state(data)
    except Exception as e:
        logger.error(f"Migration فایل Economy Core ناموفق بود، از state خالی استفاده می‌شه: {e}")
        return

    for cid, v in data.get("bank", {}).items():
        _bank[int(cid)] = defaultdict(int, {int(u): c for u, c in v.items()})
    for cid, v in data.get("total_deposited", {}).items():
        _total_deposited[int(cid)] = defaultdict(int, {int(u): n for u, n in v.items()})
    for cid, v in data.get("total_withdrawn", {}).items():
        _total_withdrawn[int(cid)] = defaultdict(int, {int(u): n for u, n in v.items()})
    for cid, v in data.get("source_totals", {}).items():
        _source_totals[int(cid)] = defaultdict(int, v)
    for cid, v in data.get("sink_totals", {}).items():
        _sink_totals[int(cid)] = defaultdict(int, v)

    _ledger.clear()
    _ledger.update(data.get("ledger", {}))
    _ledger_order.clear()
    _ledger_order.extend(data.get("ledger_order", list(_ledger.keys())))
    _idempotency_index.clear()
    _idempotency_index.update(data.get("idempotency_index", {}))
    _frozen_chats.clear()
    _frozen_chats.update(data.get("frozen_chats", []))
    for txn_id in _ledger_order:
        rec = _ledger.get(txn_id)
        if not rec:
            continue
        _ledger_by_user[rec["chat_id"]][rec["user_id"]].append(txn_id)

    logger.info("وضعیت Economy Core (بانک + Ledger) از فایل بارگذاری شد.")


# ---------------------------------------------------------------------------
# 🧾 Ledger — Wrapper روی economy_engine (که Transaction History رو نگه می‌داره)
# ---------------------------------------------------------------------------

def _normalize_type(kind: str) -> str:
    """kind/type ورودی رو به یکی از TRANSACTION_TYPES رسمی نگاشت می‌کنه.
    مقادیر قدیمی مثل 'activity', 'daily', 'admin_add', 'shop', 'misc' که از
    قبل توی bot.py استفاده می‌شدن رو هم پوشش می‌ده تا چیزی نشکنه."""
    if not kind:
        return "OTHER"
    k = kind.strip().upper()
    if k in TRANSACTION_TYPES:
        return k
    legacy_map = {
        "ACTIVITY": "REWARD", "DAILY": "REWARD", "MISC": "OTHER",
        "SHOP": "PURCHASE", "ADMIN_ADD": "OTHER", "TRANSFER": "TRANSFER",
        "DEPOSIT": "BANK_DEPOSIT", "WITHDRAW": "BANK_WITHDRAW",
    }
    return legacy_map.get(k, "OTHER")


def create_transaction(chat_id: int, user_id: int, type_: str, amount: int, *,
                        target_user_id: int | None = None, currency: str = "coins",
                        source: str = "", metadata: dict | None = None,
                        idempotency_key: str | None = None) -> dict:
    """رکورد یک تراکنش رو توی Ledger مرکزی ثبت می‌کنه (transaction_id تولید
    می‌کنه) و برمی‌گردونه. این تابع فقط «ثبت» می‌کنه، خودش موجودی رو تغییر
    نمی‌ده — آن کار وظیفه‌ی add_coins/remove_coins/transfer_coins/... است که
    از داخل خودشون این تابع رو صدا می‌زنن. اگه idempotency_key تکراری باشه و
    قبلاً یک تراکنش COMPLETED با همون کلید ثبت شده باشه، DuplicateTransactionError
    می‌زنه (فراخوان باید قبل از اعمال مجدد مقدار، این خطا رو چک کنه)."""
    if idempotency_key:
        existing = _idempotency_index.get(idempotency_key)
        if existing and existing in _ledger and _ledger[existing]["status"] == TXN_COMPLETED:
            raise DuplicateTransactionError(existing)

    txn_id = uuid.uuid4().hex
    record = {
        "transaction_id": txn_id,
        "user_id": user_id,
        "target_user_id": target_user_id,
        "chat_id": chat_id,
        "amount": amount,
        "currency": currency,
        "type": _normalize_type(type_),
        "source": source,
        "timestamp": time.time(),
        "metadata": metadata or {},
        "status": TXN_COMPLETED,
        "idempotency_key": idempotency_key,
    }
    _ledger[txn_id] = record
    _ledger_order.append(txn_id)
    _ledger_by_user[chat_id][user_id].append(txn_id)
    if idempotency_key:
        _idempotency_index[idempotency_key] = txn_id

    # هرس کردن قدیمی‌ترین رکوردها اگه از سقف رد شدیم (جلوگیری از رشد بی‌نهایت)
    while len(_ledger_order) > MAX_LEDGER_SIZE:
        old_id = _ledger_order.pop(0)
        old = _ledger.pop(old_id, None)
        if old:
            lst = _ledger_by_user.get(old["chat_id"], {}).get(old["user_id"])
            if lst and old_id in lst:
                lst.remove(old_id)

    return record


def get_transaction(transaction_id: str) -> dict | None:
    return _ledger.get(transaction_id)


def get_transaction_history(chat_id: int, uid: int, limit: int = 50) -> list[dict]:
    """جدیدترین تراکنش‌های یک کاربر رو برمی‌گردونه (جدیدترین اول)."""
    ids = _ledger_by_user.get(chat_id, {}).get(uid, [])
    result = [_ledger[i] for i in reversed(ids[-limit:]) if i in _ledger]
    return result


def get_ledger_volume(chat_id: int, since_ts: float | None = None) -> int:
    """تعداد کل تراکنش‌های ثبت‌شده‌ی این گروه (فاز ۲۶: Transaction Volume)."""
    count = 0
    for uid_map in [_ledger_by_user.get(chat_id, {})]:
        for txn_ids in uid_map.values():
            if since_ts is None:
                count += len(txn_ids)
            else:
                count += sum(1 for tid in txn_ids if tid in _ledger and _ledger[tid]["timestamp"] >= since_ts)
    return count


def find_suspicious_transactions(chat_id: int, threshold: int, since_ts: float, limit: int = 10) -> list[dict]:
    """تراکنش‌های بزرگ اخیر این گروه (فاز ۲۶: Suspicious Activity — یه هیوریستیک
    ساده، نه تشخیص Fraud واقعی؛ صرفاً چیزی که ادمین باید نگاهش بندازه)."""
    found = []
    for uid_map in [_ledger_by_user.get(chat_id, {})]:
        for txn_ids in uid_map.values():
            for tid in txn_ids:
                rec = _ledger.get(tid)
                if rec and rec["timestamp"] >= since_ts and abs(rec["amount"]) >= threshold:
                    found.append(rec)
    found.sort(key=lambda r: r["timestamp"], reverse=True)
    return found[:limit]


async def rollback_transaction(transaction_id: str, reason: str = "") -> dict:
    """یک تراکنش COMPLETED رو برمی‌گردونه (اثر مالی‌شو خنثی می‌کنه) و
    خودش رو ROLLED_BACK علامت می‌زنه. یک رکورد جدید هم برای خودِ رول‌بک
    توی Ledger ثبت می‌شه (قابل حسابرسی). رول‌بک روی چیزی که قبلاً رول‌بک
    شده RollbackError می‌زنه."""
    record = _ledger.get(transaction_id)
    if record is None:
        raise TransactionNotFoundError(f"تراکنشی با شناسه‌ی {transaction_id} پیدا نشد.")
    if record["status"] != TXN_COMPLETED:
        raise RollbackError(f"تراکنش {transaction_id} در وضعیت {record['status']} است و قابل رول‌بک نیست.")

    chat_id, uid, amount = record["chat_id"], record["user_id"], record["amount"]
    ttype = record["type"]

    # اثر مالی معکوس رو اعمال کن. برای TRANSFER، دو طرف هر دو رکورد جدا دارن
    # (یکی منفی برای فرستنده، یکی مثبت برای گیرنده)؛ رول‌بک هرکدوم فقط سهم
    # همون رکورد رو برمی‌گردونه، نه کل تراکنش دو طرفه رو.
    if amount > 0:
        await remove_coins(chat_id, uid, amount, kind="OTHER",
                            note=f"رول‌بک {transaction_id}: {reason}", allow_bank=True, bypass_freeze=True)
    elif amount < 0:
        await add_coins(chat_id, uid, -amount, kind="OTHER",
                         note=f"رول‌بک {transaction_id}: {reason}", bypass_freeze=True)

    record["status"] = TXN_ROLLED_BACK
    create_transaction(chat_id, uid, "OTHER", -amount if amount else 0,
                        source="rollback",
                        metadata={"rolled_back_transaction_id": transaction_id, "reason": reason,
                                  "original_type": ttype})
    return record


async def admin_set_balance(chat_id: int, uid: int, new_amount: int, note: str = "",
                             admin_id: int | None = None) -> int:
    """برای Commandهای ادمینی مثل /setcoins: موجودی کیف‌پول رو دقیقاً روی
    new_amount ست می‌کنه (نه Delta). این هم مثل بقیه از Lock/Ledger عبور
    می‌کنه تا Race Condition و بی‌ردی (Audit) نداشته باشیم."""
    if new_amount < 0:
        raise ValueError("موجودی نمی‌تونه منفی باشه.")
    host = _host()
    async with _lock_for(chat_id, uid):
        current = host._wallet[chat_id].get(uid, 0)
        delta = new_amount - current
        host._wallet[chat_id][uid] = new_amount
        if delta:
            record_transaction(chat_id, uid, "OTHER", delta,
                                note=note or f"admin_set_balance by {admin_id}")
        return new_amount


async def admin_adjust_balance(chat_id: int, uid: int, delta: int, note: str = "",
                                admin_id: int | None = None) -> int:
    """برای Commandهای ادمینی مثل /addcoins: مقدار delta (می‌تونه منفی باشه)
    رو به موجودی اضافه می‌کنه، ولی هرگز اجازه نمی‌ده منفی بشه (طبق قانون
    Negative Balance Protection)."""
    host = _host()
    async with _lock_for(chat_id, uid):
        current = host._wallet[chat_id].get(uid, 0)
        new_amount = max(0, current + delta)
        applied = new_amount - current
        host._wallet[chat_id][uid] = new_amount
        if applied:
            record_transaction(chat_id, uid, "OTHER", applied,
                                note=note or f"admin_adjust_balance by {admin_id}")
        return new_amount


def record_transaction(chat_id: int, uid: int, kind: str, amount: int,
                        counterparty_id: int | None = None, note: str = "",
                        idempotency_key: str | None = None) -> dict:
    """Wrapper سازگار با نسخه‌ی قبلی: هم Ledger واقعی (create_transaction) رو
    آپدیت می‌کنه، هم economy_engine.log_transaction (برای /transactions فعلی
    که فرمتش رو می‌خونه) رو صدا می‌زنه تا چیزی توی UI فعلی نشکنه."""
    import economy_engine
    economy_engine.log_transaction(chat_id, uid, kind, amount, counterparty_id, note)
    record = create_transaction(chat_id, uid, kind, amount, target_user_id=counterparty_id,
                                 source=note, idempotency_key=idempotency_key)
    # فاز ۹: Balance Engine — تجمیع Source/Sink سطح گروه
    if amount > 0:
        _source_totals[chat_id][kind] += amount
    elif amount < 0:
        _sink_totals[chat_id][kind] += -amount
    return record


# ---------------------------------------------------------------------------
# 💰 Wallet — Read
# ---------------------------------------------------------------------------

def get_wallet(chat_id: int, uid: int) -> int:
    """موجودی کیف‌پول (نقد در دسترس، همون _wallet فعلی توی bot.py)."""
    host = _host()
    return host._wallet[chat_id].get(uid, 0)


# نام مستعار طبق نام‌گذاری درخواستی
get_balance = get_wallet


def get_bank_balance(chat_id: int, uid: int) -> int:
    return _bank[chat_id].get(uid, 0)


def can_afford(chat_id: int, uid: int, amount: int, include_bank: bool = False) -> bool:
    if amount < 0:
        return False
    total = get_wallet(chat_id, uid)
    if include_bank:
        total += get_bank_balance(chat_id, uid)
    return total >= amount


def get_net_worth(chat_id: int, uid: int) -> int:
    """Net Worth = Wallet + Bank + هر منبع دیگه‌ای که ماژول‌های بعدی
    (Property/City/Pet/Investments/...) با register_net_worth_source ثبت کردن."""
    total = get_wallet(chat_id, uid) + get_bank_balance(chat_id, uid)
    for source in _net_worth_sources:
        try:
            total += int(source(chat_id, uid) or 0)
        except Exception as e:
            logger.warning(f"یکی از منابع Net Worth خطا داد: {e}")
    return total


# ---------------------------------------------------------------------------
# 💰 Wallet — Write (تنها نقطه‌ی مجاز برای تغییر _wallet)
# ---------------------------------------------------------------------------

async def add_coins(chat_id: int, uid: int, amount: int, kind: str = "misc",
                     note: str = "", counterparty_id: int | None = None,
                     idempotency_key: str | None = None, bypass_freeze: bool = False) -> int:
    """amount باید >= 0 باشه. موجودی جدید رو برمی‌گردونه. اگه idempotency_key
    داده بشه و قبلاً یک بار با همین کلید موفق اجرا شده باشه، DuplicateTransactionError
    می‌زنه و هیچ مبلغی دوباره اضافه نمی‌شه (جلوگیری از Duplicate Reward مثل
    گرفتن دوباره‌ی پاداش یک Mission/Achievement). bypass_freeze فقط برای
    ابزارهای داخلی ادمینی مثل rollback_transaction استفاده می‌شه."""
    if amount < 0:
        raise ValueError("add_coins فقط مقدار غیرمنفی می‌گیره؛ برای کم‌کردن از remove_coins استفاده کن.")
    if amount == 0:
        return get_wallet(chat_id, uid)
    if is_frozen(chat_id) and not bypass_freeze:
        raise EconomyFrozenError("اقتصاد این گروه فعلاً فریزه.")
    host = _host()
    async with _lock_for(chat_id, uid):
        if idempotency_key:
            existing = _idempotency_index.get(idempotency_key)
            if existing and existing in _ledger and _ledger[existing]["status"] == TXN_COMPLETED:
                raise DuplicateTransactionError(existing)
        host._wallet[chat_id][uid] += amount
        record_transaction(chat_id, uid, kind, amount, counterparty_id, note, idempotency_key)
        new_balance = host._wallet[chat_id][uid]
    return new_balance


async def remove_coins(chat_id: int, uid: int, amount: int, kind: str = "misc",
                        note: str = "", counterparty_id: int | None = None,
                        allow_bank: bool = False, bypass_freeze: bool = False) -> int:
    """amount باید >= 0 باشه. اگه موجودی کافی نباشه InsufficientFundsError
    می‌زنه (هرگز موجودی منفی نمی‌شه). موجودی جدیدِ کیف‌پول رو برمی‌گردونه.
    اگه allow_bank=True باشه و کیف‌پول کافی نباشه، مابقی از بانک هم کم می‌شه.
    bypass_freeze فقط برای ابزارهای داخلی ادمینی استفاده می‌شه."""
    if amount < 0:
        raise ValueError("remove_coins فقط مقدار غیرمنفی می‌گیره.")
    if amount == 0:
        return get_wallet(chat_id, uid)
    if is_frozen(chat_id) and not bypass_freeze:
        raise EconomyFrozenError("اقتصاد این گروه فعلاً فریزه.")
    host = _host()
    async with _lock_for(chat_id, uid):
        wallet_bal = host._wallet[chat_id].get(uid, 0)
        if wallet_bal >= amount:
            host._wallet[chat_id][uid] = wallet_bal - amount
            record_transaction(chat_id, uid, kind, -amount, counterparty_id, note)
            return host._wallet[chat_id][uid]

        if not allow_bank:
            raise InsufficientFundsError(
                f"موجودی کافی نیست: {wallet_bal} < {amount}"
            )

        bank_bal = _bank[chat_id].get(uid, 0)
        remainder = amount - wallet_bal
        if bank_bal < remainder:
            raise InsufficientFundsError(
                f"موجودی کیف‌پول + بانک کافی نیست: {wallet_bal + bank_bal} < {amount}"
            )
        host._wallet[chat_id][uid] = 0
        _bank[chat_id][uid] = bank_bal - remainder
        record_transaction(chat_id, uid, kind, -amount, counterparty_id, note)
        return host._wallet[chat_id][uid]


async def transfer_coins(chat_id: int, from_uid: int, to_uid: int, amount: int,
                          note: str = "", kind: str = "transfer") -> None:
    """انتقال اتمیک بین دو کاربر. هر دو Lock رو به ترتیب ثابت (بر اساس uid)
    می‌گیره تا Deadlock پیش نیاد. اگه موجودی کافی نباشه InsufficientFundsError
    می‌زنه و هیچ تغییری اعمال نمی‌شه."""
    if amount <= 0:
        raise ValueError("مبلغ انتقال باید مثبت باشه.")
    if from_uid == to_uid:
        raise ValueError("نمی‌شه به خودت انتقال بدی.")
    if is_frozen(chat_id):
        raise EconomyFrozenError("اقتصاد این گروه فعلاً فریزه.")

    host = _host()
    first, second = sorted([from_uid, to_uid])
    async with _lock_for(chat_id, first):
        async with _lock_for(chat_id, second):
            balance = host._wallet[chat_id].get(from_uid, 0)
            if balance < amount:
                raise InsufficientFundsError(f"موجودی کافی نیست: {balance} < {amount}")
            host._wallet[chat_id][from_uid] = balance - amount
            host._wallet[chat_id][to_uid] = host._wallet[chat_id].get(to_uid, 0) + amount
            record_transaction(chat_id, from_uid, kind, -amount, to_uid, note)
            record_transaction(chat_id, to_uid, kind, amount, from_uid, note)


# ---------------------------------------------------------------------------
# 🏦 Bank — Deposit / Withdraw (خود دستورهای /bank فاز ۴ می‌سازه؛ اینجا فقط منطق)
# ---------------------------------------------------------------------------

async def deposit(chat_id: int, uid: int, amount: int, note: str = "") -> int:
    """از کیف‌پول به بانک. موجودی جدید بانک رو برمی‌گردونه."""
    if amount <= 0:
        raise ValueError("مبلغ سپرده باید مثبت باشه.")
    host = _host()
    async with _lock_for(chat_id, uid):
        wallet_bal = host._wallet[chat_id].get(uid, 0)
        if wallet_bal < amount:
            raise InsufficientFundsError(f"موجودی کیف‌پول کافی نیست: {wallet_bal} < {amount}")
        host._wallet[chat_id][uid] = wallet_bal - amount
        _bank[chat_id][uid] = _bank[chat_id].get(uid, 0) + amount
        _total_deposited[chat_id][uid] += amount
        record_transaction(chat_id, uid, "deposit", -amount, note=note or "سپرده به بانک")
        return _bank[chat_id][uid]


async def withdraw(chat_id: int, uid: int, amount: int, note: str = "") -> int:
    """از بانک به کیف‌پول. موجودی جدید کیف‌پول رو برمی‌گردونه."""
    if amount <= 0:
        raise ValueError("مبلغ برداشت باید مثبت باشه.")
    host = _host()
    async with _lock_for(chat_id, uid):
        bank_bal = _bank[chat_id].get(uid, 0)
        if bank_bal < amount:
            raise InsufficientFundsError(f"موجودی بانک کافی نیست: {bank_bal} < {amount}")
        _bank[chat_id][uid] = bank_bal - amount
        host._wallet[chat_id][uid] = host._wallet[chat_id].get(uid, 0) + amount
        _total_withdrawn[chat_id][uid] += amount
        record_transaction(chat_id, uid, "withdraw", amount, note=note or "برداشت از بانک")
        return host._wallet[chat_id][uid]


# ---------------------------------------------------------------------------
# 🏦 Bank — عملیات مستقیم روی موجودی بانکی (فاز ۳: سود، کارمزد، سپرده‌ی بلندمدت، ...)
# این توابع طبق همون اصل معماری اضافه شدن: economy_bank.py هرگز مستقیم به
# _bank[...] دست نمی‌زنه، همیشه از این‌جا عبور می‌کنه — دقیقاً مثل wallet.
# ---------------------------------------------------------------------------

async def add_bank_coins(chat_id: int, uid: int, amount: int, kind: str = "misc",
                          note: str = "", counterparty_id: int | None = None) -> int:
    """amount باید >= 0 باشه (مثلاً سود بانکی، یا بازگشت سپرده‌ی بلندمدت به بانک).
    موجودی جدید بانک رو برمی‌گردونه."""
    if amount < 0:
        raise ValueError("add_bank_coins فقط مقدار غیرمنفی می‌گیره.")
    if amount == 0:
        return get_bank_balance(chat_id, uid)
    async with _lock_for(chat_id, uid):
        _bank[chat_id][uid] = _bank[chat_id].get(uid, 0) + amount
        record_transaction(chat_id, uid, kind, amount, counterparty_id, note)
        return _bank[chat_id][uid]


async def remove_bank_coins(chat_id: int, uid: int, amount: int, kind: str = "misc",
                             note: str = "", counterparty_id: int | None = None) -> int:
    """amount باید >= 0 باشه (مثلاً قفل‌کردن پول برای سپرده‌ی بلندمدت). اگه موجودی
    بانک کافی نباشه InsufficientFundsError می‌زنه. موجودی جدید بانک رو برمی‌گردونه."""
    if amount < 0:
        raise ValueError("remove_bank_coins فقط مقدار غیرمنفی می‌گیره.")
    if amount == 0:
        return get_bank_balance(chat_id, uid)
    async with _lock_for(chat_id, uid):
        bal = _bank[chat_id].get(uid, 0)
        if bal < amount:
            raise InsufficientFundsError(f"موجودی بانک کافی نیست: {bal} < {amount}")
        _bank[chat_id][uid] = bal - amount
        record_transaction(chat_id, uid, kind, -amount, counterparty_id, note)
        return _bank[chat_id][uid]
