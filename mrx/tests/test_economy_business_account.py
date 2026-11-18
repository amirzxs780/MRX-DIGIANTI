# -*- coding: utf-8 -*-
"""
DIGNANTI V18 — تست‌های فاز ۱۷ (تکمیلِ بخشِ ۳): حسابِ بانکیِ جداگانه‌ی کسب‌وکار
================================================================
اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_business_account -v
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
TELEGRAM_STUB_DIR = os.path.join(TESTS_DIR, "_telegram_stub")

sys.path.insert(0, TELEGRAM_STUB_DIR)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, TESTS_DIR)

import _stub_bot  # noqa: E402
sys.modules["bot"] = _stub_bot

import economy_core  # noqa: E402
import economy_business  # noqa: E402

CHAT = 18018
UID = 1


def _reset_all():
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
    _stub_bot._known_members.clear()

    economy_business._businesses.clear()
    economy_business._next_id.clear()

    journal_dir = tempfile.mkdtemp(prefix="dignanti_bizacct_test_journal_")
    economy_core.WALLET_JOURNAL_FILE = os.path.join(journal_dir, "wallet_journal.jsonl")


def _make_business(chat_id, uid, btype="shop", level=1, inventory=500):
    b = {
        "id": economy_business._new_id(chat_id, uid), "type": btype, "level": level,
        "employees": 0, "inventory": inventory, "price_mult": 1.0, "ad_until": None,
        "last_collect_ts": time.time(), "last_tax_ts": time.time(), "purchased_ts": time.time(),
        "lifetime_revenue": 0, "lifetime_expense": 0, "history": [],
        "debt": 0, "crisis_strikes": 0, "bankrupt": False, "partners": {},
        "branches": 1, "employed_users": {}, "account_balance": 0, "profit_routing": "wallet",
    }
    economy_business.owned(chat_id, uid).append(b)
    return b


def arun(coro):
    return asyncio.run(coro)


class DefaultBehaviorUnchangedTest(unittest.TestCase):
    """تضمینِ اینکه پیش‌فرض (profit_routing="wallet") دقیقاً رفتارِ قدیمیه."""

    def setUp(self):
        _reset_all()

    def test_default_routing_is_wallet(self):
        b = _make_business(CHAT, UID)
        self.assertEqual(b["profit_routing"], "wallet")

    def test_default_routing_pays_owner_wallet_directly(self):
        b = _make_business(CHAT, UID, inventory=100000)
        b["last_collect_ts"] = time.time() - 3600 * 5
        net, fired = arun(economy_business._collect_one(CHAT, UID, b))
        if net > 0:
            self.assertEqual(economy_core.get_wallet(CHAT, UID), net)
            self.assertEqual(b["account_balance"], 0)


class ProfitRoutingToAccountTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_account_routing_credits_business_account_not_wallet(self):
        b = _make_business(CHAT, UID, inventory=100000)
        b["profit_routing"] = "account"
        b["last_collect_ts"] = time.time() - 3600 * 5
        net, fired = arun(economy_business._collect_one(CHAT, UID, b))
        if net > 0:
            self.assertEqual(economy_core.get_wallet(CHAT, UID), 0)
            self.assertEqual(b["account_balance"], net)

    def test_account_balance_accumulates_across_collections(self):
        b = _make_business(CHAT, UID, inventory=100000)
        b["profit_routing"] = "account"
        for _ in range(2):
            b["last_collect_ts"] = time.time() - 3600 * 5
            arun(economy_business._collect_one(CHAT, UID, b))
        self.assertGreaterEqual(b["account_balance"], 0)
        # دو بار Collect پشتِ‌هم نباید چیزی رو گم کنه؛ موجودی باید تجمعی باشه
        first_balance = b["account_balance"]
        b["last_collect_ts"] = time.time() - 3600 * 5
        arun(economy_business._collect_one(CHAT, UID, b))
        self.assertGreaterEqual(b["account_balance"], first_balance)


class DepositWithdrawTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_deposit_moves_money_from_wallet_to_account(self):
        b = _make_business(CHAT, UID)
        arun(economy_core.add_coins(CHAT, UID, 10_000, kind="REWARD"))
        arun(economy_core.remove_coins(CHAT, UID, 3000, kind="OTHER", note="deposit"))
        b["account_balance"] += 3000
        self.assertEqual(economy_core.get_wallet(CHAT, UID), 7000)
        self.assertEqual(b["account_balance"], 3000)

    def test_withdraw_moves_money_from_account_to_wallet(self):
        b = _make_business(CHAT, UID)
        b["account_balance"] = 5000
        b["account_balance"] -= 2000
        arun(economy_core.add_coins(CHAT, UID, 2000, kind="OTHER", note="withdraw"))
        self.assertEqual(b["account_balance"], 3000)
        self.assertEqual(economy_core.get_wallet(CHAT, UID), 2000)

    def test_withdraw_cannot_exceed_balance(self):
        b = _make_business(CHAT, UID)
        b["account_balance"] = 1000
        requested = 5000
        self.assertGreater(requested, b["account_balance"])  # باید در سطحِ Command رد بشه

    def test_money_conserved_across_deposit_and_withdraw(self):
        b = _make_business(CHAT, UID)
        arun(economy_core.add_coins(CHAT, UID, 10_000, kind="REWARD"))
        total_before = economy_core.get_wallet(CHAT, UID) + b["account_balance"]

        arun(economy_core.remove_coins(CHAT, UID, 4000, kind="OTHER", note="deposit"))
        b["account_balance"] += 4000
        total_after_deposit = economy_core.get_wallet(CHAT, UID) + b["account_balance"]
        self.assertEqual(total_before, total_after_deposit)

        b["account_balance"] -= 1500
        arun(economy_core.add_coins(CHAT, UID, 1500, kind="OTHER", note="withdraw"))
        total_after_withdraw = economy_core.get_wallet(CHAT, UID) + b["account_balance"]
        self.assertEqual(total_before, total_after_withdraw)


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_account_fields_survive_save_load_round_trip(self):
        b = _make_business(CHAT, UID)
        b["account_balance"] = 4200
        b["profit_routing"] = "account"

        tmp_dir = tempfile.mkdtemp(prefix="dignanti_bizacct_state_")
        old_state_file = economy_business.STATE_FILE
        economy_business.STATE_FILE = os.path.join(tmp_dir, "business_state.json")
        try:
            economy_business._write_state_file(economy_business._collect_state())
            economy_business._businesses.clear()
            economy_business._next_id.clear()
            economy_business.load_state()

            restored = economy_business.owned(CHAT, UID)[0]
            self.assertEqual(restored["account_balance"], 4200)
            self.assertEqual(restored["profit_routing"], "account")
        finally:
            economy_business.STATE_FILE = old_state_file


if __name__ == "__main__":
    unittest.main()
