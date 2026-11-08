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
import weakref
from collections import defaultdict
from typing import Callable

import config

logger = logging.getLogger("economy_core")

STATE_FILE = getattr(config, "ECONOMY_CORE_STATE_FILE", "economy_core_state.json")

# فیکس (آدیتِ دوم - مسئله‌ی ۳ - هماهنگیِ Wallet/Ledger حینِ کرش): فایلِ
# Wallet (توی bot.py) و فایلِ Ledger (همین‌جا) دو os.replace() کاملاً مستقل
# دارن؛ این فایل یه Write-Ahead Journal سبک (append-only، JSONL، هر خط
# fsync شده) هست که مستقل از هر دوِ اون فایل‌های بزرگ، هر تغییرِ Wallet رو
# همون لحظه که توی حافظه اعمال می‌شه ثبت می‌کنه. جزییاتِ کامل پایین‌تر کنار
# reconcile_wallet_journal().
WALLET_JOURNAL_FILE = getattr(config, "ECONOMY_WALLET_JOURNAL_FILE", STATE_FILE + ".wallet_journal.jsonl")
_WALLET_JOURNAL_KEEP_LINES = 500

# نسخه‌ی ساختار Stateِ Economy Core. هر بار ساختار داده عوض شد، این رو ببر بالا
# و منطق Migration مربوطه رو زیر migrate_state() اضافه کن. Migration باید
# Idempotent باشه (اجرای دوباره‌ش نباید داده رو خراب کنه).
# نسخه ۲ (فاز ۱ ارتقا): اضافه‌شدن Transaction Ledger واقعی (transaction_id,
# status, idempotency_key). داده‌ی قدیمی از دست نمی‌ره؛ صرفاً یک ledger خالی
# برای رکوردهای گذشته (که transaction_id نداشتن) ساخته می‌شه.
ECONOMY_STATE_VERSION = 3

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
#
# باگ قبلی: این یه dict معمولی بود، یعنی برای هر (chat_id, user_id) که
# حتی یه‌بار یه عملیات قفل‌دار انجام می‌داد، یه Lock برای همیشه توی حافظه
# می‌موند - با گذشت زمان و افزایش تعداد کاربرها، این دیکشنری فقط بزرگ‌تر
# می‌شد و هیچ‌وقت آزاد نمی‌شد (نشتی حافظه).
# فیکس: WeakValueDictionary. هر Lock فقط وقتی زنده می‌مونه که یه async-with
# فعال (یا در حال انتظار) روش باشه؛ به‌محض اینکه آخرین نگه‌دارنده‌ش آزادش
# کنه، خودش از دیکشنری پاک می‌شه (توسط GC رفرنس‌کانتینگِ CPython، بدون
# نیاز به هیچ Task پاک‌سازیِ دوره‌ایِ جدا).
_user_locks: "weakref.WeakValueDictionary[tuple[int, int], asyncio.Lock]" = weakref.WeakValueDictionary()

# فیکس (آدیتِ دوم - مسئله‌ی ۱): قفلِ جدا به‌ازای هر Transaction ID، مخصوصِ
# rollback_transaction. چرا جدا از _user_locks؟ چون _user_locks روی (chat_id,
# uid) قفل می‌کنه - این برای جلوگیری از رول‌بکِ دوبرابرِ *همون* تراکنش کافی
# نیست؛ باید دقیقاً روی خودِ transaction_id قفل بگیریم تا خواندنِ status،
# اعمالِ اثرِ معکوس، و تغییرِ status به ROLLED_BACK یک واحدِ atomic بشه (نه
# اینکه فقط موجودیِ کاربر Serialize بشه). همون الگوی WeakValueDictionary
# (بدون نشتی حافظه) برای این یکی هم تکرار شده.
_rollback_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()

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
# idempotency_key -> {"transaction_id": ..., "status": TXN_COMPLETED/TXN_ROLLED_BACK}
# (جلوگیری از Duplicate Reward/Double Spend)
#
# فیکس (آدیتِ دوم - مسئله‌ی ۲ - Idempotency بعد از Pruning شدنِ Ledger):
# قبلاً این فقط transaction_id رو نگه می‌داشت (dict[str, str])، و چک
# Duplicate وابسته بود به اینکه خودِ رکورد توی _ledger هنوز موجود باشه
# (`existing in _ledger and _ledger[existing]["status"] == TXN_COMPLETED`).
# ولی _ledger با MAX_LEDGER_SIZE=20000 هرس می‌شه (قدیمی‌ترین رکوردها حذف
# می‌شن) - این یعنی بعد از اینکه ۲۰٬۰۰۰ تراکنشِ دیگه (از هر کاربر/هر نوعی)
# بعد از یه تراکنشِ idempotency-dar اتفاق بیفته، رکوردش از _ledger حذف
# می‌شد، چک `existing in _ledger` نادرست False برمی‌گردوند، و همون
# idempotency_key دوباره قابل‌استفاده می‌شد - یعنی یه Achievement/Mission/
# Poll Reward می‌تونست، بعد از فشارِ کافی، دوباره پرداخت بشه. فیکس: این
# دیکشنری حالا خودش وضعیت (status) رو هم مستقیم نگه می‌داره - دیگه به
# وجودِ رکورد توی _ledger وابسته نیست، پس هیچ‌وقت (نه با Pruning، نه با
# Restart) خودش پاک نمی‌شه مگر با rollback صریح. توجه: برخلافِ _ledger، این
# دیکشنری Pruning نداره (قصداً - Pruning دقیقاً همون باگیه که داریم فیکس
# می‌کنیم)؛ رشدش کندِه (یه ورودی به‌ازای هر Claim، نه هر تراکنشِ خام) ولی
# بدونِ سقف - جزییات توی AUDIT_REPORT.md.
_idempotency_index: dict[str, dict] = {}


def _idempotency_check(idempotency_key: str | None) -> None:
    """اگه idempotency_key قبلاً به یک تراکنشِ COMPLETED منجر شده باشه،
    DuplicateTransactionError می‌زنه. مستقیم از _idempotency_index می‌خونه -
    هیچ وابستگی‌ای به اینکه رکورد توی _ledger (که Prune می‌شه) هنوز باشه یا
    نه نداره."""
    if not idempotency_key:
        return
    entry = _idempotency_index.get(idempotency_key)
    if entry and entry.get("status") == TXN_COMPLETED:
        raise DuplicateTransactionError(entry.get("transaction_id"))


def _idempotency_mark(idempotency_key: str | None, txn_id: str, status: str = TXN_COMPLETED) -> None:
    if idempotency_key:
        _idempotency_index[idempotency_key] = {"transaction_id": txn_id, "status": status}


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


def _rollback_lock_for(transaction_id: str) -> asyncio.Lock:
    lock = _rollback_locks.get(transaction_id)
    if lock is None:
        lock = asyncio.Lock()
        _rollback_locks[transaction_id] = lock
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


def _journal_append(chat_id: int, uid: int, txn_id: str, wallet_after: int, record: dict) -> None:
    """بعد از هر تغییرِ Wallet (همون لحظه، توی همون تابعی که قفلِ
    _lock_for رو گرفته - بدونِ هیچ await ای وسطش)، یه خطِ append-only فسینک‌شده
    به Journal اضافه می‌کنه. این مستقل از فایلِ بزرگِ Wallet (bot.py) و فایلِ
    بزرگِ Ledger (همین ماژول) - هر دوش فقط دوره‌ای (توی save_state) نوشته
    می‌شن - بلافاصله و به‌صورتِ تکی روی دیسکه. اگه خودِ نوشتنِ این خط شکست
    بخوره (مثلاً دیسک پر)، فقط Warning لاگ می‌شه و خودِ عملیاتِ مالی (که از
    قبل توی حافظه اعمال شده) خراب نمی‌شه - این فایل صرفاً برای Recovery
    کمکیه، نه مسیرِ اصلیِ درست‌کار کردنِ ربات."""
    try:
        line = json.dumps({
            "txn_id": txn_id,
            "chat_id": chat_id,
            "uid": uid,
            "wallet_after": wallet_after,
            "record": record,
            "ts": time.time(),
        }, ensure_ascii=False)
        with open(WALLET_JOURNAL_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        logger.warning(f"نوشتنِ Wallet Journal ناموفق بود: {e}")


def _journal_truncate() -> None:
    """بعد از ذخیره‌ی موفقِ فایلِ بزرگِ Ledger (که توسطِ bot.py.save_state همیشه
    *بعد از* نوشتنِ Sync فایلِ Wallet خودش صدا زده می‌شه)، بخشِ قدیمیِ Journal
    رو که دیگه لازم نیست کوتاه می‌کنه تا این فایل بی‌نهایت رشد نکنه.
    محافظه‌کارانه: صفر خط نگه نمی‌داریم، آخرین N خط رو نگه می‌داریم - چون از
    اینجا نمی‌تونیم ۱۰۰٪ مطمئن باشیم نوشتنِ فایلِ Wallet توسطِ bot.py (که در
    تابعِ دیگه‌ای و مستقل از این‌جا اجرا می‌شه) واقعاً موفق بوده یا فقط
    Warning خورده و رد شده."""
    try:
        if not os.path.exists(WALLET_JOURNAL_FILE):
            return
        with open(WALLET_JOURNAL_FILE, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= _WALLET_JOURNAL_KEEP_LINES:
            return
        tail = lines[-_WALLET_JOURNAL_KEEP_LINES:]
        tmp = WALLET_JOURNAL_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(tail)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, WALLET_JOURNAL_FILE)
    except Exception as e:
        logger.warning(f"کوتاه‌کردنِ Wallet Journal ناموفق بود: {e}")


async def save_state():
    async with _save_lock:
        try:
            await asyncio.to_thread(_write_state_file, _collect_state())
            await asyncio.to_thread(_journal_truncate)
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
    if version < 3:
        # نسخه‌ی ۲ -> ۳ (آدیتِ دوم - مسئله‌ی ۲): فرمتِ idempotency_index عوض
        # شد - از dict[str, str] (کلید -> transaction_id خام) به
        # dict[str, {"transaction_id":..., "status":...}] تا دیگه چک
        # Duplicate به وجودِ رکورد توی _ledger (که Prune می‌شه) وابسته نباشه.
        # اینجا فایل‌های قدیمی رو مهاجرت می‌کنیم: برای هر ورودیِ قدیمی که هنوز
        # رشته‌ست، سعی می‌کنیم status واقعی‌شو از خودِ ledger (اگه هنوز اون
        # تراکنش Prune نشده) بخونیم؛ اگه پیدا نشد (یعنی همین الان هم به‌خاطرِ
        # باگِ قدیمی Prune شده بود)، محافظه‌کارانه فرض می‌کنیم COMPLETEDه
        # (ترجیحِ امنیتی: جلوگیری از یه پرداختِ دوباره‌ی احتمالی، حتی به قیمتِ
        # اینکه شاید یه کلیدِ قبلاً rollback-شده‌ی خیلی قدیمی اشتباهاً یه بار
        # دیگه مسدود بمونه - این حالت فوق‌العاده نادره چون rollback هنوز
        # نادره، و خیلی کم‌خطرتر از پرداختِ تکراریه).
        old_ledger = data.get("ledger", {})
        new_index = {}
        for key, val in data.get("idempotency_index", {}).items():
            if isinstance(val, dict):
                new_index[key] = val  # از قبل فرمتِ جدیده (مثلاً Migration دوباره اجرا شده)
                continue
            txn_id = val  # فرمتِ قدیمی: مقدار همون transaction_id خام بود
            rec = old_ledger.get(txn_id)
            status = rec["status"] if rec else TXN_COMPLETED
            new_index[key] = {"transaction_id": txn_id, "status": status}
        data["idempotency_index"] = new_index
        data["version"] = 3
    # نسخه‌های بعدی این‌جا اضافه می‌شن: if version < 4: ...
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


def reconcile_wallet_journal() -> dict:
    """فیکس (آدیتِ دوم - مسئله‌ی ۳ - هماهنگیِ Wallet و Ledger حینِ کرش).

    ⚠️ ترتیبِ صدا زدن حیاتیه: این تابع باید دقیقاً *بعد از هر دوی* این مراحل
    صدا زده بشه:
        ۱) economy_core.load_state() (همین ماژول - Ledger/Idempotency Index
           از فایلِ خودش بارگذاری شده)
        ۲) bot.py بخشِ خودشو از فایلِ state‌ِ خودش بارگذاری کرده و دیکشنریِ
           _wallet رو پر کرده
    (در عمل: از داخلِ bot.py.load_state، بلافاصله بعدِ حلقه‌ای که
    `_wallet[int(cid)] = ...` می‌سازه.)

    چرا این فیکس لازمه؟ Wallet (فایلِ bot.py) و Ledger/Bank (فایلِ همین
    ماژول) دو os.replace() کاملاً مستقلن - bot.py.save_state اول فایلِ
    خودشو Sync می‌نویسه، بعد با asyncio.gather همه‌ی موتورها (از جمله
    economy_core) رو هم‌زمان صدا می‌زنه. اگه برنامه دقیقاً وسطِ این فرایند
    کرش کنه، دو حالتِ ناهماهنگ ممکنه:
        (الف) فایلِ Ledger جدیدتر از فایلِ Wallet: Ledger/Idempotency یه
              تراکنش رو "انجام‌شده" می‌دونه ولی فایلِ Wallet هنوز مبلغشو
              نداره -> کاربر جایزه/پولشو "گم" می‌کنه (باگِ آزاردهنده، ولی
              نه یه خطرِ امنیتی).
        (ب) فایلِ Wallet جدیدتر از فایلِ Ledger: کیف‌پول مبلغو داره ولی
              Ledger/Idempotency Index خبر نداره -> یه idempotency_key
              ممکنه اشتباهاً دوباره قبول بشه و دوباره پرداخت بشه (Double-Pay
              واقعی - این خطرناک‌تریه).

    راه‌حل: Wallet Journal (فایلِ append-only و fsync-per-line که در
    add_coins/remove_coins/transfer_coins/admin_set_balance/
    admin_adjust_balance/deposit/withdraw نوشته می‌شه، دقیقاً همون لحظه که
    Wallet توی حافظه تغییر می‌کنه - مستقل از هر دو فایلِ بزرگ) رو می‌خونه و:
        • برای هر رکوردی که توی Journal هست ولی توی _ledger (بارگذاری‌شده از
          فایلِ احتمالاً قدیمی‌ترِ Ledger) نیست -> رکوردِ Ledger/Idempotency
          رو مستقیماً از خودِ Journal بازسازی می‌کنه (حالتِ ب رو می‌بنده).
          توجه: این "اجرای دوباره‌ی عملیاتِ مالی" *نیست* - هیچ‌وقت
          add_coins/remove_coins دوباره صدا زده نمی‌شن؛ فقط رکوردِ از-قبل-
          اتفاق‌افتاده بازسازی می‌شه.
        • برای هر (chat_id, uid)، آخرین مقدارِ wallet_after ثبت‌شده توی
          Journal رو با مقدارِ فعلیِ _wallet مقایسه می‌کنه؛ اگه فرق داشت (یعنی
          فایلِ Wallet قدیمی‌تر بوده)، مستقیماً مقدارِ صحیح رو ست می‌کنه (نه
          اینکه Delta رو دوباره اضافه کنه - این دقیقاً همون چیزیه که دستورِ
          کار صراحتاً منع کرده: "هرگز یه عملیاتِ مالیِ از-قبل-اعمال‌شده رو
          دوباره Replay نکن").

    محدودیتِ صادقانه (طبقِ دستورِ صریح: ادعای Crash Consistency کامل ممنوعه
    مگر با تستِ Recovery اثبات بشه): این مکانیزم فقط پنجره‌ی بینِ آخرین
    Flush موفقِ Journal و لحظه‌ی کرش رو می‌پوشونه. اگه خودِ نوشتنِ یه خطِ
    Journal (fsync) شکست بخوره (مثلاً دیسک پر)، دقیقاً همون یک تراکنش از
    این محافظت بیرونه - این مورد با Warning لاگ می‌شه، نه با سکوت. همچنین
    این مکانیزم جایگزینِ فایل‌های بزرگِ Wallet/Ledger نیست، فقط یه لایه‌ی
    ترمیمِ تکمیلیه؛ معماریِ چندفایلیِ فعلی دست‌نخورده می‌مونه."""
    host = _host()
    entries = []
    try:
        if os.path.exists(WALLET_JOURNAL_FILE):
            with open(WALLET_JOURNAL_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        # خطِ آخر احتمالاً وسطِ نوشتن کرش کرده (fsync نیمه‌کاره
                        # نداریم چون fsync atomic نیست ولی نوشتنِ متن قبلش
                        # ممکنه قطع شده باشه) - نادیده بگیر، بی‌خطره.
                        continue
    except Exception as e:
        logger.warning(f"خوندنِ Wallet Journal برای Reconciliation ناموفق بود: {e}")
        return {"recovered_ledger_entries": 0, "corrected_wallets": 0}

    recovered = 0
    last_balance: dict[tuple[int, int], int] = {}
    for entry in entries:
        try:
            chat_id = int(entry["chat_id"])
            uid = int(entry["uid"])
            txn_id = entry["txn_id"]
            wallet_after = int(entry["wallet_after"])
            record = entry.get("record")
        except Exception:
            continue

        if record and txn_id not in _ledger:
            _ledger[txn_id] = record
            if txn_id not in _ledger_order:
                _ledger_order.append(txn_id)
            lst = _ledger_by_user[chat_id][uid]
            if txn_id not in lst:
                lst.append(txn_id)
            idem_key = record.get("idempotency_key")
            if idem_key:
                _idempotency_mark(idem_key, txn_id, record.get("status", TXN_COMPLETED))
            recovered += 1

        last_balance[(chat_id, uid)] = wallet_after

    corrected = 0
    for (chat_id, uid), expected in last_balance.items():
        actual = host._wallet[chat_id].get(uid, 0)
        if actual != expected:
            logger.warning(
                f"Wallet Journal Reconciliation: ناهماهنگیِ کیف‌پول پیدا شد "
                f"(chat={chat_id} uid={uid}): فایل={actual}، طبقِ Journal باید "
                f"{expected} باشه؛ اصلاح شد (بدونِ اجرای دوبارهٔ هیچ تراکنشی)."
            )
            host._wallet[chat_id][uid] = expected
            corrected += 1

    if recovered or corrected:
        logger.warning(
            f"Wallet Journal Reconciliation تکمیل شد: {recovered} رکوردِ Ledger "
            f"از Journal بازسازی شد، {corrected} موجودیِ کیف‌پول اصلاح شد."
        )
    return {"recovered_ledger_entries": recovered, "corrected_wallets": corrected}


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
    _idempotency_check(idempotency_key)

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
    _idempotency_mark(idempotency_key, txn_id, TXN_COMPLETED)

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


def get_transaction_history(chat_id: int, uid: int, limit: int = 50,
                             since_ts: float | None = None) -> list[dict]:
    """جدیدترین تراکنش‌های یک کاربر رو برمی‌گردونه (جدیدترین اول).
    اگه since_ts داده بشه (فاز ۵ V18 - برای گزارش‌های دوره‌ای در
    economy_accounting)، فقط تراکنش‌های از اون لحظه به بعد رو برمی‌گردونه؛
    چون ids به ترتیبِ زمانِ ثبت append می‌شن، همین که به یه رکوردِ قدیمی‌تر از
    since_ts برسیم، همه‌ی بقیه هم قدیمی‌ترن و می‌شه زود متوقف شد."""
    ids = _ledger_by_user.get(chat_id, {}).get(uid, [])
    if since_ts is None:
        return [_ledger[i] for i in reversed(ids[-limit:]) if i in _ledger]

    result = []
    for i in reversed(ids):
        rec = _ledger.get(i)
        if not rec:
            continue
        if rec["timestamp"] < since_ts:
            break
        result.append(rec)
        if len(result) >= limit:
            break
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
    شده RollbackError می‌زنه.

    فیکس (آدیتِ دوم - مسئله‌ی ۱ - محافظت در برابر رول‌بکِ هم‌زمان):
    قبلاً خواندنِ status=='COMPLETED' و نوشتنِ status='ROLLED_BACK' دو
    عملیاتِ جدا بودن با یک await (اثرِ مالی) وسطشون - یعنی اگه همین تابع
    برای یک transaction_id دوبار هم‌زمان صدا زده بشه، هر دو می‌تونستن
    status رو COMPLETED ببینن و هر دو اثرِ مالی رو خنثی کنن (پول اضافه
    برگردونده بشه). الان کل زنجیره‌ی «چک وضعیت -> اعمالِ اثرِ معکوس -> ثبتِ
    وضعیتِ جدید» زیرِ یک قفلِ اختصاصیِ همون transaction_id قفل می‌شه
    (_rollback_lock_for) - مستقل از اینکه رسیدنِ await به یه تعلیقِ واقعی
    منجر بشه یا نه (یعنی حتی اگه بعداً داخلِ remove_coins/add_coins یه I/O
    واقعی اضافه بشه، این قفل هنوزم درست کار می‌کنه - به جزییاتِ پیاده‌سازیِ
    فعلیِ asyncio.Lock وابسته نیست).

    اگه اعمالِ اثرِ معکوس (remove_coins/add_coins) شکست بخوره (مثلاً
    InsufficientFundsError چون کاربر از قبل پول رو خرج کرده)، status
    همونجوری COMPLETED می‌مونه - یعنی رول‌بک هنوز «قابل تلاشِ دوباره»ست،
    نه یه حالتِ نصفه‌ونیمه‌ی گمشده."""
    lock = _rollback_lock_for(transaction_id)
    async with lock:
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
        # توجه: اگه این await خطا بده (مثلاً موجودی کافی برای پس‌گرفتن نیست)،
        # status هنوز COMPLETEDه (پایین‌تر عوض می‌شه)، پس Exception بالا
        # می‌ره و هیچ تغییرِ نصفه‌ونیمه‌ای توی status/Ledger ثبت نمی‌شه.
        if amount > 0:
            await remove_coins(chat_id, uid, amount, kind="OTHER",
                                note=f"رول‌بک {transaction_id}: {reason}", allow_bank=True, bypass_freeze=True)
        elif amount < 0:
            await add_coins(chat_id, uid, -amount, kind="OTHER",
                             note=f"رول‌بک {transaction_id}: {reason}", bypass_freeze=True)

        record["status"] = TXN_ROLLED_BACK
        # اگه خودِ تراکنشِ اصلی یه idempotency_key داشته (مثلاً یه Achievement
        # Reward)، باید ایندکس رو هم هم‌زمان با status خودِ رکورد به‌روز کنیم -
        # وگرنه _idempotency_check هنوزم اون کلید رو COMPLETED می‌بینه و یه
        # اعطای دوباره‌ی همون جایزه (بعد از رول‌بک) رو اشتباهاً مسدود می‌کنه.
        _idempotency_mark(record.get("idempotency_key"), transaction_id, TXN_ROLLED_BACK)
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
            record = record_transaction(chat_id, uid, "OTHER", delta,
                                         note=note or f"admin_set_balance by {admin_id}")
            _journal_append(chat_id, uid, record["transaction_id"], new_amount, record)
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
            record = record_transaction(chat_id, uid, "OTHER", applied,
                                         note=note or f"admin_adjust_balance by {admin_id}")
            _journal_append(chat_id, uid, record["transaction_id"], new_amount, record)
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
        _idempotency_check(idempotency_key)
        host._wallet[chat_id][uid] += amount
        record = record_transaction(chat_id, uid, kind, amount, counterparty_id, note, idempotency_key)
        new_balance = host._wallet[chat_id][uid]
        _journal_append(chat_id, uid, record["transaction_id"], new_balance, record)
    return new_balance


def add_coins_sync(chat_id: int, uid: int, amount: int, kind: str = "misc",
                    note: str = "", counterparty_id: int | None = None,
                    idempotency_key: str | None = None, bypass_freeze: bool = False) -> int:
    """نسخه‌ی Sync از add_coins — برای مسیرهایی که از قبل sync بودن و تبدیل‌شون
    به async زنجیره‌ی بزرگی از تابع‌های بالادستیِ غیرمرتبط رو async می‌کرد
    (profile_engine.grant_rewards_for_unlocked و زنجیره‌ی ~۲۰ فراخوان‌کننده‌ی
    economy_achievements.check_all، و poll_engine._grant_vote_reward). این دقیقاً
    همون قانون معماری رو برآورده می‌کنه («هیچ Module خارجی‌ای مستقیم به
    host._wallet دست نزنه») بدون اینکه امضای توابع فعلی رو بشکنه.

    ایمنیِ Race Condition: asyncio تک‌رشته‌ایه و فقط سرِ یه `await` سوییچ Task
    می‌کنه. این تابع هیچ `await` ای نداره، پس از لحظه‌ی شروع تا پایانش
    به‌صورت یکپارچه (atomic) اجرا می‌شه — هیچ Task دیگه‌ای نمی‌تونه مثلاً بینِ
    خوندن و نوشتنِ moجودی وسطش بپره. این دقیقاً همون ضمانتی هست که
    `_lock_for` به نسخه‌ی async می‌ده (که داخلش هم هیچ await ای بینِ قفل‌گرفتن
    و آزادکردن نیست). **هشدار:** این تابع رو هرگز از داخل Thread/Executor
    (asyncio.to_thread و مشابه) صدا نزن — فقط از مسیر اصلی Event Loop."""
    if amount < 0:
        raise ValueError("add_coins_sync فقط مقدار غیرمنفی می‌گیره؛ برای کم‌کردن از remove_coins استفاده کن.")
    if amount == 0:
        return get_wallet(chat_id, uid)
    if is_frozen(chat_id) and not bypass_freeze:
        raise EconomyFrozenError("اقتصاد این گروه فعلاً فریزه.")
    _idempotency_check(idempotency_key)
    host = _host()
    host._wallet[chat_id][uid] += amount
    record = record_transaction(chat_id, uid, kind, amount, counterparty_id, note, idempotency_key)
    new_balance = host._wallet[chat_id][uid]
    _journal_append(chat_id, uid, record["transaction_id"], new_balance, record)
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
            record = record_transaction(chat_id, uid, kind, -amount, counterparty_id, note)
            new_balance = host._wallet[chat_id][uid]
            _journal_append(chat_id, uid, record["transaction_id"], new_balance, record)
            return new_balance

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
        record = record_transaction(chat_id, uid, kind, -amount, counterparty_id, note)
        new_balance = host._wallet[chat_id][uid]
        _journal_append(chat_id, uid, record["transaction_id"], new_balance, record)
        return new_balance


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
            rec_from = record_transaction(chat_id, from_uid, kind, -amount, to_uid, note)
            rec_to = record_transaction(chat_id, to_uid, kind, amount, from_uid, note)
            _journal_append(chat_id, from_uid, rec_from["transaction_id"],
                             host._wallet[chat_id][from_uid], rec_from)
            _journal_append(chat_id, to_uid, rec_to["transaction_id"],
                             host._wallet[chat_id][to_uid], rec_to)


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
        record = record_transaction(chat_id, uid, "deposit", -amount, note=note or "سپرده به بانک")
        _journal_append(chat_id, uid, record["transaction_id"], host._wallet[chat_id][uid], record)
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
        record = record_transaction(chat_id, uid, "withdraw", amount, note=note or "برداشت از بانک")
        _journal_append(chat_id, uid, record["transaction_id"], host._wallet[chat_id][uid], record)
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
