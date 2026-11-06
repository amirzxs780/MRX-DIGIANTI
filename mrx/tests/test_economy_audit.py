# -*- coding: utf-8 -*-
"""
DIGNANTI V17 - تست‌های خودکار Economy Core (فاز ۶/۹/۱۰ آدیت)
====================================================================
این تست‌ها هم‌زمانی (Race Condition)، Idempotency، و یکپارچگی داده رو روی
economy_core.py و فیکس‌های Phase 2/3 (profile_engine.grant_rewards_for_unlocked،
poll_engine._grant_vote_reward، economy_missions.record_progress) می‌سنجن.

اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_audit -v

محدودیت دانسته‌شده: این Sandbox به اینترنت/pip دسترسی نداره، پس
python-telegram-bot واقعی (طبق requirements.txt) قابل نصب نیست. چون bot.py
واقعی importش کاملاً به اون کتابخانه (+ یه Token واقعی + راه‌اندازی Application)
وابسته‌ست، این تست‌ها bot.py رو مستقیم import نمی‌کنن. به‌جاش:
  - یه استاب سبک از ماژول «bot» (tests/_stub_bot.py) و یه استاب مینیمال از
    پکیج «telegram» (tests/_telegram_stub/) می‌سازیم تا ماژول‌های اقتصادیِ
    واقعیِ پروژه (economy_core, profile_engine, poll_engine, economy_missions,
    economy_jobs, economy_games, ...) بدون تغییر، عیناً import و تست بشن.
  - دو مسیر فیکس‌شده که مستقیماً توی خودِ bot.py هستن (daily_command و
    econquick bulk-bonus) از این طریق مستقیماً اجرا نمی‌شن؛ ولی هر دو دقیقاً
    از همون مکانیزم economy_core.add_coins(idempotency_key=...) استفاده
    می‌کنن که اینجا مستقیم و با جزییات کامل تست شده (test_add_coins_idempotency_*
    و test_concurrent_duplicate_idempotency_key). این محدودیتِ پوشش توی گزارش
    نهایی (AUDIT_REPORT.md) صریحاً قید شده.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import shutil
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
TELEGRAM_STUB_DIR = os.path.join(TESTS_DIR, "_telegram_stub")

# استاب «telegram» باید قبل از پیدا شدنِ نسخه‌ی واقعی (اگه باشه) یا شکست import
# (اگه نباشه) توی sys.path بیاد.
sys.path.insert(0, TELEGRAM_STUB_DIR)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, TESTS_DIR)

import _stub_bot  # noqa: E402
sys.modules["bot"] = _stub_bot

import economy_core  # noqa: E402
import profile_engine  # noqa: E402
import poll_engine  # noqa: E402
import economy_missions  # noqa: E402


_JOURNAL_TEST_DIR = tempfile.mkdtemp(prefix="dignanti_journal_")


def _reset_economy_core_state():
    """ریست کامل state درون‌حافظه‌ای economy_core بین تست‌ها (White-Box) -
    هیچ تابع عمومی برای این کار نیست چون تا قبل از این آدیت لازم نبوده."""
    economy_core._bank.clear()
    economy_core._total_deposited.clear()
    economy_core._total_withdrawn.clear()
    economy_core._source_totals.clear()
    economy_core._sink_totals.clear()
    economy_core._net_worth_sources.clear()
    economy_core._frozen_chats.clear()
    economy_core._ledger.clear()
    economy_core._ledger_order.clear()
    economy_core._ledger_by_user.clear()
    economy_core._idempotency_index.clear()
    economy_core._rollback_locks = type(economy_core._rollback_locks)()
    _stub_bot._wallet.clear()
    _stub_bot._xp.clear()
    _stub_bot._achievements.clear()
    # فیکس (آدیتِ دوم - مسئله‌ی ۳): WALLET_JOURNAL_FILE رو هم مثلِ STATE_FILE
    # باید بینِ تست‌ها ایزوله کرد، وگرنه append های یه تست روی دیسکِ واقعیِ
    # پروژه می‌مونه و بینِ تست‌های بعدی لو می‌ره (White-Box مشابهِ بقیه‌ی ریست‌ها).
    economy_core.WALLET_JOURNAL_FILE = os.path.join(_JOURNAL_TEST_DIR, "wallet_journal.jsonl")
    if os.path.exists(economy_core.WALLET_JOURNAL_FILE):
        os.remove(economy_core.WALLET_JOURNAL_FILE)


CHAT = 1001


class EconomyCoreBasicsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _reset_economy_core_state()

    async def test_add_remove_round_trip_and_ledger(self):
        bal = await economy_core.add_coins(CHAT, 1, 500, kind="REWARD", note="t")
        self.assertEqual(bal, 500)
        bal = await economy_core.remove_coins(CHAT, 1, 200, kind="PURCHASE", note="t")
        self.assertEqual(bal, 300)
        history = economy_core.get_transaction_history(CHAT, 1)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["amount"], -200)  # جدیدترین اول
        self.assertEqual(history[1]["amount"], 500)

    async def test_remove_coins_never_negative(self):
        await economy_core.add_coins(CHAT, 1, 100, kind="REWARD")
        with self.assertRaises(economy_core.InsufficientFundsError):
            await economy_core.remove_coins(CHAT, 1, 101, kind="PURCHASE")
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 100)

    async def test_remove_coins_allow_bank_spills_over(self):
        await economy_core.add_coins(CHAT, 1, 50, kind="REWARD")
        await economy_core.add_bank_coins(CHAT, 1, 100, kind="misc")
        new_wallet = await economy_core.remove_coins(CHAT, 1, 120, kind="PURCHASE", allow_bank=True)
        self.assertEqual(new_wallet, 0)
        self.assertEqual(economy_core.get_bank_balance(CHAT, 1), 30)

    async def test_transfer_conserves_total(self):
        await economy_core.add_coins(CHAT, 1, 1000, kind="REWARD")
        await economy_core.transfer_coins(CHAT, 1, 2, 300, kind="TRANSFER")
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 700)
        self.assertEqual(economy_core.get_wallet(CHAT, 2), 300)

    async def test_transfer_insufficient_funds_leaves_state_untouched(self):
        await economy_core.add_coins(CHAT, 1, 100, kind="REWARD")
        with self.assertRaises(economy_core.InsufficientFundsError):
            await economy_core.transfer_coins(CHAT, 1, 2, 500, kind="TRANSFER")
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 100)
        self.assertEqual(economy_core.get_wallet(CHAT, 2), 0)

    async def test_admin_set_balance_records_delta_not_amount(self):
        await economy_core.add_coins(CHAT, 1, 40, kind="REWARD")
        await economy_core.admin_set_balance(CHAT, 1, 100, admin_id=999)
        history = economy_core.get_transaction_history(CHAT, 1)
        self.assertEqual(history[0]["amount"], 60)  # delta = 100-40
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 100)

    async def test_admin_adjust_balance_clamps_at_zero(self):
        await economy_core.add_coins(CHAT, 1, 10, kind="REWARD")
        new_bal = await economy_core.admin_adjust_balance(CHAT, 1, -1000, admin_id=999)
        self.assertEqual(new_bal, 0)
        self.assertGreaterEqual(economy_core.get_wallet(CHAT, 1), 0)

    async def test_economy_freeze_blocks_normal_ops_not_admin_ops(self):
        economy_core.set_frozen(CHAT, True)
        with self.assertRaises(economy_core.EconomyFrozenError):
            await economy_core.add_coins(CHAT, 1, 10, kind="REWARD")
        # ابزار ادمینی باید حتی حین فریز کار کنه (طبق مستندِ خودِ ماژول)
        await economy_core.admin_set_balance(CHAT, 1, 50, admin_id=999)
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 50)
        economy_core.set_frozen(CHAT, False)

    async def test_rollback_transaction_reverses_reward(self):
        rec = await economy_core.add_coins(CHAT, 1, 300, kind="REWARD")
        self.assertEqual(rec, 300)
        history = economy_core.get_transaction_history(CHAT, 1)
        txn_id = history[0]["transaction_id"]
        await economy_core.rollback_transaction(txn_id, reason="تست")
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 0)
        with self.assertRaises(economy_core.RollbackError):
            await economy_core.rollback_transaction(txn_id, reason="دوباره")


class IdempotencyTest(unittest.IsolatedAsyncioTestCase):
    """Phase 3: Idempotency و جلوگیری از Duplicate Reward."""

    def setUp(self):
        _reset_economy_core_state()

    async def test_add_coins_idempotency_key_blocks_sequential_duplicate(self):
        key = "achievement:1001:1:first_blood"
        await economy_core.add_coins(CHAT, 1, 100, kind="REWARD", idempotency_key=key)
        with self.assertRaises(economy_core.DuplicateTransactionError):
            await economy_core.add_coins(CHAT, 1, 100, kind="REWARD", idempotency_key=key)
        # فقط یه‌بار پرداخت شده باشه
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 100)

    async def test_add_coins_sync_idempotency_key_blocks_duplicate(self):
        key = "poll_reward:55:1"
        economy_core.add_coins_sync(CHAT, 1, 50, kind="REWARD", idempotency_key=key)
        with self.assertRaises(economy_core.DuplicateTransactionError):
            economy_core.add_coins_sync(CHAT, 1, 50, kind="REWARD", idempotency_key=key)
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 50)

    async def test_different_keys_both_paid(self):
        await economy_core.add_coins(CHAT, 1, 10, kind="REWARD", idempotency_key="mission:a")
        await economy_core.add_coins(CHAT, 1, 10, kind="REWARD", idempotency_key="mission:b")
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 20)

    async def test_concurrent_duplicate_idempotency_key_only_one_wins(self):
        """شبیه‌سازی «دابل‌تپ روی یه دکمه» یا «Retry شبکه‌ی تلگرام» - دو تا
        Task هم‌زمان دقیقاً با یه idempotency_key یکسان صدا می‌زنن. فقط یکی
        باید موفق بشه؛ اون‌یکی باید DuplicateTransactionError بگیره، نه اینکه
        جایزه رو دوبار بده."""
        key = "econquick_bonus:1001:777:42"

        async def attempt():
            try:
                await economy_core.add_coins(CHAT, 42, 20, kind="EVENT", idempotency_key=key)
                return "ok"
            except economy_core.DuplicateTransactionError:
                return "dup"

        results = await asyncio.gather(*(attempt() for _ in range(20)))
        self.assertEqual(results.count("ok"), 1, f"باید فقط یکی موفق بشه: {results}")
        self.assertEqual(results.count("dup"), 19)
        self.assertEqual(economy_core.get_wallet(CHAT, 42), 20)


class ConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    """Phase 6: تست‌های هم‌زمانی/Race Condition."""

    def setUp(self):
        _reset_economy_core_state()

    async def test_two_simultaneous_purchases_no_negative_balance(self):
        await economy_core.add_coins(CHAT, 1, 100, kind="REWARD")

        async def buy():
            try:
                await economy_core.remove_coins(CHAT, 1, 80, kind="PURCHASE")
                return "ok"
            except economy_core.InsufficientFundsError:
                return "insufficient"

        results = await asyncio.gather(buy(), buy())
        self.assertEqual(results.count("ok"), 1, "فقط یکی از دو خرید هم‌زمان باید موفق بشه")
        self.assertEqual(results.count("insufficient"), 1)
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 20)
        self.assertGreaterEqual(economy_core.get_wallet(CHAT, 1), 0)

    async def test_two_simultaneous_withdrawals_no_negative_bank(self):
        await economy_core.add_bank_coins(CHAT, 1, 100, kind="misc")

        async def withdraw():
            try:
                await economy_core.withdraw(CHAT, 1, 70)
                return "ok"
            except economy_core.InsufficientFundsError:
                return "insufficient"

        results = await asyncio.gather(withdraw(), withdraw())
        self.assertEqual(results.count("ok"), 1)
        self.assertGreaterEqual(economy_core.get_bank_balance(CHAT, 1), 0)
        self.assertEqual(economy_core.get_bank_balance(CHAT, 1), 30)

    async def test_many_concurrent_removes_never_go_negative(self):
        await economy_core.add_coins(CHAT, 1, 1000, kind="REWARD")

        async def spend():
            try:
                await economy_core.remove_coins(CHAT, 1, 150, kind="PURCHASE")
                return 150
            except economy_core.InsufficientFundsError:
                return 0

        results = await asyncio.gather(*(spend() for _ in range(20)))
        spent = sum(results)
        self.assertEqual(spent, 900)  # floor(1000/150)=6 بار موفق => 900
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 100)
        self.assertGreaterEqual(economy_core.get_wallet(CHAT, 1), 0)

    async def test_concurrent_transfers_conserve_total_money(self):
        await economy_core.add_coins(CHAT, 1, 500, kind="REWARD")
        await economy_core.add_coins(CHAT, 2, 500, kind="REWARD")
        total_before = economy_core.get_wallet(CHAT, 1) + economy_core.get_wallet(CHAT, 2)

        # انتقال هم‌زمان و دوطرفه (A->B و B->A) - تستِ Deadlock/ترتیب قفل
        await asyncio.gather(
            economy_core.transfer_coins(CHAT, 1, 2, 100, kind="TRANSFER"),
            economy_core.transfer_coins(CHAT, 2, 1, 50, kind="TRANSFER"),
            economy_core.transfer_coins(CHAT, 1, 2, 30, kind="TRANSFER"),
        )
        total_after = economy_core.get_wallet(CHAT, 1) + economy_core.get_wallet(CHAT, 2)
        self.assertEqual(total_before, total_after, "مجموع پول نباید توی انتقال‌های هم‌زمان تغییر کنه")
        self.assertGreaterEqual(economy_core.get_wallet(CHAT, 1), 0)
        self.assertGreaterEqual(economy_core.get_wallet(CHAT, 2), 0)

    async def test_concurrent_transfers_multiple_users_many_pairs(self):
        users = [1, 2, 3, 4, 5]
        for u in users:
            await economy_core.add_coins(CHAT, u, 200, kind="REWARD")
        total_before = sum(economy_core.get_wallet(CHAT, u) for u in users)

        import random
        random.seed(42)
        ops = []
        for _ in range(40):
            a, b = random.sample(users, 2)
            amt = random.randint(1, 50)
            ops.append(economy_core.transfer_coins(CHAT, a, b, amt, kind="TRANSFER"))
        results = await asyncio.gather(*ops, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                self.assertIsInstance(r, economy_core.InsufficientFundsError)

        total_after = sum(economy_core.get_wallet(CHAT, u) for u in users)
        self.assertEqual(total_before, total_after)
        for u in users:
            self.assertGreaterEqual(economy_core.get_wallet(CHAT, u), 0)

    async def test_concurrent_access_same_user_wallet_add_and_remove(self):
        await economy_core.add_coins(CHAT, 1, 100, kind="REWARD")

        async def adder():
            await economy_core.add_coins(CHAT, 1, 10, kind="REWARD")

        async def remover():
            try:
                await economy_core.remove_coins(CHAT, 1, 10, kind="PURCHASE")
            except economy_core.InsufficientFundsError:
                pass

        await asyncio.gather(*([adder() for _ in range(15)] + [remover() for _ in range(15)]))
        # موجودی هیچ‌وقت نباید منفی بشه، و باید دقیقاً = موجودی اولیه + مجموع
        # دلتاهای ثبت‌شده توی لِجر (یعنی هیچ تغییری بدونِ رکورد اتفاق نیفتاده
        # و هیچ تغییرِ ثبت‌شده‌ای هم گم نشده).
        self.assertGreaterEqual(economy_core.get_wallet(CHAT, 1), 0)
        history_amounts = [r["amount"] for r in economy_core.get_transaction_history(CHAT, 1, limit=1000)]
        self.assertEqual(economy_core.get_wallet(CHAT, 1), sum(history_amounts))

    async def test_repeated_achievement_unlock_pays_once(self):
        """شبیه‌سازی 'Repeated callbacks' / 'Simultaneous achievement unlocks'
        روی profile_engine.grant_rewards_for_unlocked (فیکس Phase 2)."""
        import config
        config.ACHIEVEMENT_REWARDS = {"first_blood": {"xp": 10, "coins": 50}}
        xp1, coins1 = profile_engine.grant_rewards_for_unlocked(CHAT, 1, ["first_blood"])
        xp2, coins2 = profile_engine.grant_rewards_for_unlocked(CHAT, 1, ["first_blood"])
        self.assertEqual(coins1, 50)
        self.assertEqual(coins2, 0, "اجرای دوباره با همون achievement key نباید دوباره سکه بده")
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 50)

    async def test_poll_reward_paid_once_even_with_duplicate_record_vote(self):
        """فیکس Phase 2/3 روی poll_engine: اگه (به هر دلیلی، مثلاً replay) همون
        رأیِ اول دوباره پردازش بشه، جایزه نباید دوبار پرداخت بشه."""
        poll = poll_engine.build_poll_record(CHAT, 999, {
            "question": "تست؟", "options": ["بله", "نه"],
            "duration_seconds": 0, "anonymous": False, "multi": False, "reward_coins": 25,
        })
        poll_engine.record_vote(poll, 1, 0, poll_id=777)
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 25)
        # صدازدن مستقیمِ _grant_vote_reward (bypass کردن is_first_vote) - شبیه‌سازیِ
        # باگ/ریپلی احتمالی - idempotency_key مرکزی باید جلوشو بگیره.
        poll_engine._grant_vote_reward(poll, 1, poll_id=777)
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 25, "جایزه‌ی رأی نباید دوبار پرداخت بشه")

    async def test_mission_reward_idempotent_across_period(self):
        import config
        config.MISSION_TEMPLATES = {
            "daily": [{"key": "work_1", "text": "یه بار کار کن", "action": "work", "target": 1,
                       "reward": {"coins": 15}}],
        }
        config.MISSION_ASSIGN_COUNT = {"daily": 1}
        await economy_missions.record_progress(CHAT, 1, "work", 1)
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 15)
        # پیشرفتِ اضافه‌ی همون اکشن بعد از claimed=True نباید دوباره جایزه بده
        await economy_missions.record_progress(CHAT, 1, "work", 1)
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 15)


class PersistenceTest(unittest.IsolatedAsyncioTestCase):
    """Phase 8: Persistence/Atomic Write/Backup/Corruption Recovery."""

    def setUp(self):
        _reset_economy_core_state()
        self.tmpdir = tempfile.mkdtemp(prefix="dignanti_test_")
        self._orig_state_file = economy_core.STATE_FILE
        economy_core.STATE_FILE = os.path.join(self.tmpdir, "economy_core_state.json")

    def tearDown(self):
        economy_core.STATE_FILE = self._orig_state_file
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    async def test_save_then_load_round_trip(self):
        await economy_core.add_coins(CHAT, 1, 777, kind="REWARD", idempotency_key="k1")
        await economy_core.add_bank_coins(CHAT, 1, 333, kind="misc")
        await economy_core.save_state()
        self.assertTrue(os.path.exists(economy_core.STATE_FILE))
        self.assertFalse(os.path.exists(economy_core.STATE_FILE + ".tmp"), "فایل .tmp نباید بعد از save باقی بمونه")

        _reset_economy_core_state()
        self.assertEqual(economy_core.get_bank_balance(CHAT, 1), 0)
        economy_core.load_state()
        self.assertEqual(economy_core.get_bank_balance(CHAT, 1), 333)
        # idempotency index هم باید سر پا بمونه (survive restart - Phase 3)
        with self.assertRaises(economy_core.DuplicateTransactionError):
            await economy_core.add_coins(CHAT, 1, 777, kind="REWARD", idempotency_key="k1")

    async def test_backup_created_on_second_save(self):
        await economy_core.add_coins(CHAT, 1, 10, kind="REWARD")
        await economy_core.save_state()
        await economy_core.add_coins(CHAT, 1, 10, kind="REWARD")
        await economy_core.save_state()
        self.assertTrue(os.path.exists(economy_core.STATE_FILE + ".bak"))

    async def test_corrupted_main_file_recovers_from_backup(self):
        await economy_core.add_bank_coins(CHAT, 1, 999, kind="misc")
        await economy_core.save_state()
        # یه تغییر دیگه که فایل اصلی رو عوض می‌کنه و بکاپِ معتبرِ قبلی رو می‌سازه
        await economy_core.add_bank_coins(CHAT, 1, 1, kind="misc")
        await economy_core.save_state()
        # حالا فایل اصلی رو «خراب» کن (شبیه‌سازیِ کرش وسط نوشتن)
        with open(economy_core.STATE_FILE, "w", encoding="utf-8") as f:
            f.write("{ این یه جیسون خرابه ")

        _reset_economy_core_state()
        economy_core.load_state()
        # باید از بکاپ (که قبل از خراب شدنِ فایل اصلی گرفته شده) بازیابی بشه،
        # نه اینکه کرش کنه یا state خالی برگرده.
        self.assertEqual(economy_core.get_bank_balance(CHAT, 1), 999)

    async def test_missing_state_file_starts_clean_without_crash(self):
        self.assertFalse(os.path.exists(economy_core.STATE_FILE))
        try:
            economy_core.load_state()
        except Exception as e:
            self.fail(f"load_state نباید وقتی فایلی نیست Exception بده: {e}")
        self.assertEqual(economy_core.get_bank_balance(CHAT, 1), 0)

    async def test_save_failure_does_not_corrupt_existing_file(self):
        """شبیه‌سازیِ 'Interrupted shutdown'/'Failed write': اگه نوشتنِ فایل جدید
        شکست بخوره، فایلِ معتبرِ قبلی نباید از دست بره یا نصفه‌نیمه بشه."""
        await economy_core.add_bank_coins(CHAT, 1, 555, kind="misc")
        await economy_core.save_state()
        with open(economy_core.STATE_FILE, encoding="utf-8") as f:
            good_content = f.read()

        import unittest.mock as mock
        with mock.patch("economy_core.os.replace", side_effect=OSError("disk full (شبیه‌سازی)")):
            with self.assertRaises(OSError):
                await asyncio.to_thread(economy_core._write_state_file, economy_core._collect_state())

        with open(economy_core.STATE_FILE, encoding="utf-8") as f:
            after_content = f.read()
        self.assertEqual(good_content, after_content, "فایل اصلی نباید بعد از یه os.replace شکست‌خورده عوض شده باشه")
        self.assertEqual(json.loads(after_content)["bank"][str(CHAT)]["1"], 555)


# ---------------------------------------------------------------------------
# 🆕 آدیتِ دوم (Final Critical Audit) — مسئله‌ی ۱: محافظت در برابرِ رول‌بکِ هم‌زمان
# ---------------------------------------------------------------------------

class RollbackConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _reset_economy_core_state()

    async def test_twenty_concurrent_rollbacks_only_one_succeeds(self):
        """طبقِ درخواستِ صریح: حداقل ۲۰ تلاشِ هم‌زمانِ رول‌بک روی یک تراکنش -
        باید دقیقاً یکی موفق بشه، بقیه RollbackError بگیرن، و اثرِ مالی فقط
        یک‌بار خنثی شده باشه (نه چندبار)."""
        await economy_core.add_coins(CHAT, 1, 500, kind="misc", note="موجودیِ اولیه")
        await economy_core.add_coins(CHAT, 1, 300, kind="misc", note="تراکنشی که رول‌بک می‌شه")
        history = economy_core.get_transaction_history(CHAT, 1, limit=1)
        txn_id = history[0]["transaction_id"]
        self.assertEqual(history[0]["status"], economy_core.TXN_COMPLETED)
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 800)

        results = await asyncio.gather(
            *[economy_core.rollback_transaction(txn_id, reason=f"تلاشِ همزمانِ #{i}") for i in range(20)],
            return_exceptions=True,
        )
        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, Exception)]

        self.assertEqual(len(successes), 1, "باید دقیقاً یکی از ۲۰ رول‌بکِ هم‌زمان موفق بشه")
        self.assertEqual(len(failures), 19)
        for e in failures:
            self.assertIsInstance(e, economy_core.RollbackError)

        self.assertEqual(economy_core.get_wallet(CHAT, 1), 500,
                          "فقط یک‌بار باید اثرِ مالیِ ۳۰۰ تایی خنثی شده باشه (نه ۲۰ بار)")
        self.assertEqual(economy_core._ledger[txn_id]["status"], economy_core.TXN_ROLLED_BACK)

    async def test_rollback_after_already_rolled_back_raises(self):
        await economy_core.add_coins(CHAT, 1, 200, kind="misc")
        history = economy_core.get_transaction_history(CHAT, 1, limit=1)
        txn_id = history[0]["transaction_id"]
        await economy_core.rollback_transaction(txn_id)
        with self.assertRaises(economy_core.RollbackError):
            await economy_core.rollback_transaction(txn_id)


# ---------------------------------------------------------------------------
# 🆕 آدیتِ دوم — مسئله‌ی ۲: پایداریِ Idempotency فراتر از Pruning شدنِ Ledger
# ---------------------------------------------------------------------------

class IdempotencyAfterPruningTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _reset_economy_core_state()
        self.tmpdir = tempfile.mkdtemp(prefix="dignanti_test_prune_")
        self._orig_state_file = economy_core.STATE_FILE
        economy_core.STATE_FILE = os.path.join(self.tmpdir, "economy_core_state.json")

    def tearDown(self):
        economy_core.STATE_FILE = self._orig_state_file
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    async def test_idempotency_survives_ledger_pruning_and_restart(self):
        """دقیقاً طبقِ سناریوی خواسته‌شده: یه تراکنش با idempotency_key بساز،
        رکوردش رو از Ledger «هرس‌شده» فرض کن (شبیه‌سازیِ اثرِ MAX_LEDGER_SIZE
        بدونِ نیاز به واقعاً ثبتِ ۲۰٬۰۰۰ تراکنشِ دیگه)، Restart کن (ری‌لود از
        فایل)، و دوباره همون عملیات رو امتحان کن - نباید دوباره پرداخت بشه."""
        key = "achievement:1001:1:first_blood"
        await economy_core.add_coins(CHAT, 1, 777, kind="reward", note="جایزه", idempotency_key=key)
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 777)

        # شبیه‌سازیِ Pruning: دقیقاً همون اثری که MAX_LEDGER_SIZE روی این رکوردِ
        # خاص می‌ذاشت (حذفِ کاملش از _ledger/_ledger_order/_ledger_by_user) -
        # ولی _idempotency_index (طبقِ فیکس) دست‌نخورده می‌مونه.
        history = economy_core.get_transaction_history(CHAT, 1, limit=1)
        txn_id = history[0]["transaction_id"]
        del economy_core._ledger[txn_id]
        economy_core._ledger_order.remove(txn_id)
        economy_core._ledger_by_user[CHAT][1].remove(txn_id)

        await economy_core.save_state()

        # «ری‌استارت»: فقط ساختارهای خودِ economy_core پاک و از فایل بازخوانی
        # می‌شن (Wallet دست‌نخورده می‌مونه - دقیقاً مثلِ معماریِ واقعی که Wallet
        # توی فایلِ جداگانه‌ی bot.py زندگی می‌کنه).
        economy_core._bank.clear()
        economy_core._ledger.clear()
        economy_core._ledger_order.clear()
        economy_core._ledger_by_user.clear()
        economy_core._idempotency_index.clear()
        economy_core.load_state()

        self.assertNotIn(txn_id, economy_core._ledger, "رکوردِ اصلی باید طبقِ شبیه‌سازیِ Pruning غایب باشه")
        with self.assertRaises(economy_core.DuplicateTransactionError):
            await economy_core.add_coins(CHAT, 1, 777, kind="reward", note="تلاشِ دوباره", idempotency_key=key)

        self.assertEqual(economy_core.get_wallet(CHAT, 1), 777, "نباید دوباره پرداخت شده باشه")

    async def test_migration_from_old_bare_string_idempotency_format(self):
        """فرمتِ قدیمی (قبل از نسخه‌ی ۳ - idempotency_index[key] = txn_id خام)
        باید به فرمتِ جدید (dict با status) مهاجرت کنه، با خوندنِ status واقعی
        از خودِ ledger وقتی هنوز موجوده."""
        txn_id = "deadbeef1234"
        ledger = {
            txn_id: {
                "transaction_id": txn_id, "chat_id": CHAT, "user_id": 1, "amount": 50,
                "currency": "coins", "type": "REWARD", "source": "", "timestamp": 0.0,
                "metadata": {}, "status": economy_core.TXN_COMPLETED, "idempotency_key": "old_key_1",
            }
        }
        data = {"version": 2, "ledger": ledger, "idempotency_index": {"old_key_1": txn_id}}
        migrated = economy_core.migrate_state(data)
        self.assertEqual(migrated["version"], economy_core.ECONOMY_STATE_VERSION)
        self.assertEqual(migrated["idempotency_index"]["old_key_1"],
                          {"transaction_id": txn_id, "status": economy_core.TXN_COMPLETED})

    async def test_migration_defaults_to_completed_when_ledger_record_already_gone(self):
        """اگه رکوردِ ledger قبل از Migration هم Prune شده بود (باگِ قدیمی از قبل
        اتفاق افتاده بود)، Migration نباید کلید رو دوباره قابل‌استفاده کنه -
        محافظه‌کارانه COMPLETED فرض می‌کنه (ترجیحِ امنیتی)."""
        data = {"version": 2, "ledger": {}, "idempotency_index": {"old_key_2": "some-missing-txn-id"}}
        migrated = economy_core.migrate_state(data)
        self.assertEqual(migrated["idempotency_index"]["old_key_2"]["status"], economy_core.TXN_COMPLETED)


# ---------------------------------------------------------------------------
# 🆕 آدیتِ دوم — مسئله‌ی ۳: هماهنگیِ Wallet و Ledger حینِ کرش (Wallet Journal)
# ---------------------------------------------------------------------------

class WalletJournalRecoveryTest(unittest.IsolatedAsyncioTestCase):
    """دقیقاً سناریوهای خواسته‌شده: قطع‌شدن بینِ تغییرِ موجودی و Persist شدنِ
    Ledger، و قطع‌شدن حینِ ذخیره‌سازیِ state؛ Restart و بازیابی؛ تأییدِ
    هماهنگیِ Wallet/Bank/Ledger؛ تأییدِ عدمِ پرداختِ دوباره."""

    def setUp(self):
        _reset_economy_core_state()
        self.tmpdir = tempfile.mkdtemp(prefix="dignanti_test_journal_")
        self._orig_state_file = economy_core.STATE_FILE
        economy_core.STATE_FILE = os.path.join(self.tmpdir, "economy_core_state.json")

    def tearDown(self):
        economy_core.STATE_FILE = self._orig_state_file
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _partial_restart_reload_ledger_only(self):
        """شبیه‌سازیِ دقیقِ آنچه واقعاً اتفاق می‌افته: فقط economy_core.load_state()
        دوباره اجرا می‌شه (_stub_bot._wallet دست‌نخورده می‌مونه - دقیقاً مثلِ
        bot.py واقعی که Wallet رو از فایلِ کاملاً جداگانه‌ی خودش می‌خونه)."""
        economy_core._bank.clear()
        economy_core._ledger.clear()
        economy_core._ledger_order.clear()
        economy_core._ledger_by_user.clear()
        economy_core._idempotency_index.clear()
        economy_core.load_state()

    async def test_crash_after_journal_write_before_ledger_file_save__no_double_pay(self):
        """جهتِ خطرناک: فایلِ Wallet جدیدتر از فایلِ Ledger (یعنی Ledger/Idempotency
        خبر نداره تراکنش انجام شده). باید توسطِ Journal ترمیم بشه و دیگه
        اجازه‌ی پرداختِ دوباره‌ی idempotency_key رو نده."""
        key1 = "daily_reward:1001:1:2026-11-05"
        await economy_core.add_coins(CHAT, 1, 100, kind="reward", idempotency_key=key1)
        await economy_core.save_state()  # Snapshotِ سالم: txn1 توی فایلِ Ledger هست

        key2 = "daily_reward:1001:1:2026-11-06"
        await economy_core.add_coins(CHAT, 1, 50, kind="reward", idempotency_key=key2)
        # 💥 کرش (شبیه‌سازی): save_state() بعدِ txn2 صدا زده نشده، پس فایلِ
        # بزرگِ Ledger هنوز فقط txn1 رو داره. Wallet Journal ولی (مستقل، fsync
        # شده همون لحظه) txn2 رو هم ثبت کرده.
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 150)
        self.assertTrue(os.path.exists(economy_core.WALLET_JOURNAL_FILE))

        self._partial_restart_reload_ledger_only()
        self.assertNotIn(key2, economy_core._idempotency_index,
                         "بعدِ ری‌لودِ خامِ فایلِ Ledger، txn2 نباید اونجا باشه")

        report = economy_core.reconcile_wallet_journal()
        self.assertEqual(report["recovered_ledger_entries"], 1)
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 150,
                          "موجودی نباید دوباره تغییر کنه - فقط باید با Journal هماهنگ بمونه")

        with self.assertRaises(economy_core.DuplicateTransactionError):
            await economy_core.add_coins(CHAT, 1, 50, kind="reward", idempotency_key=key2)
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 150, "نباید دوبار پرداخت شده باشه")

    async def test_crash_during_state_saving__stale_wallet_file_gets_corrected_not_replayed(self):
        """جهتِ دیگه: فایلِ Wallet عقب‌تر از چیزیه که واقعاً اتفاق افتاده (مثلاً
        خودِ نوشتنِ فایلِ Wallet وسطِ Save کرش کرده). Journal باید موجودیِ
        درست رو مستقیماً «ست» کنه، نه اینکه Delta رو دوباره جمع بزنه (که خودش
        می‌شد یه باگِ Double-Apply)."""
        await economy_core.add_coins(CHAT, 1, 100, kind="reward")
        await economy_core.save_state()
        await economy_core.add_coins(CHAT, 1, 50, kind="reward")  # wallet=150؛ هنوز Save نشده

        # 💥 شبیه‌سازیِ اینکه فایلِ Wallet جداگانه (bot.py) وسطِ نوشتن کرش کرده
        # و هنوز فقط ۱۰۰ رو داره - بعدِ «ری‌استارت»، bot.py این مقدارِ قدیمی
        # رو از فایلِ خودش می‌خونه.
        _stub_bot._wallet[CHAT][1] = 100

        self._partial_restart_reload_ledger_only()
        report = economy_core.reconcile_wallet_journal()

        self.assertEqual(report["corrected_wallets"], 1)
        self.assertEqual(
            economy_core.get_wallet(CHAT, 1), 150,
            "باید دقیقاً به مقدارِ ثبت‌شده توی Journal برگرده (نه با جمع‌زدنِ دوبارهٔ Delta - که می‌شد ۲۰۰)",
        )

    async def test_journal_truncated_to_bounded_size_after_successful_save(self):
        """Journal نباید بی‌نهایت رشد کنه؛ بعدِ هر save_state() موفق، به یه
        سقفِ ثابت کوتاه می‌شه (_WALLET_JOURNAL_KEEP_LINES)."""
        for i in range(10):
            await economy_core.add_coins(CHAT, 1, 1, kind="reward", note=f"ریز {i}")
        with open(economy_core.WALLET_JOURNAL_FILE, encoding="utf-8") as f:
            lines_before = f.readlines()
        self.assertEqual(len(lines_before), 10)

        import unittest.mock as mock
        with mock.patch.object(economy_core, "_WALLET_JOURNAL_KEEP_LINES", 3):
            await economy_core.save_state()

        with open(economy_core.WALLET_JOURNAL_FILE, encoding="utf-8") as f:
            lines_after = f.readlines()
        self.assertEqual(len(lines_after), 3, "باید به ۳ خطِ آخر کوتاه شده باشه")

    async def test_transfer_coins_journals_both_sides(self):
        """transfer_coins دو طرفه‌ست (دو رکوردِ Ledger جدا) - هر دو طرف باید
        توی Journal ثبت بشن، نه فقط فرستنده."""
        await economy_core.add_coins(CHAT, 1, 200, kind="reward")
        await economy_core.transfer_coins(CHAT, 1, 2, 80, note="انتقال")
        with open(economy_core.WALLET_JOURNAL_FILE, encoding="utf-8") as f:
            entries = [json.loads(line) for line in f if line.strip()]
        uids_journaled = {e["uid"] for e in entries}
        self.assertIn(1, uids_journaled)
        self.assertIn(2, uids_journaled)
        self.assertEqual(economy_core.get_wallet(CHAT, 1), 120)
        self.assertEqual(economy_core.get_wallet(CHAT, 2), 80)


if __name__ == "__main__":
    unittest.main(verbosity=2)
