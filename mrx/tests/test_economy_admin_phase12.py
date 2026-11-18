# -*- coding: utf-8 -*-
"""
DIGNANTI V18 — تست‌های فاز ۱۲ (تکمیلِ بخشِ ۱۶): پنلِ مدیریتِ اقتصاد
================================================================
اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_admin_phase12 -v
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

import config  # noqa: E402
import economy_core  # noqa: E402
import economy_business  # noqa: E402
import economy_bank  # noqa: E402
import economy_admin  # noqa: E402

CHAT = 14014
UID = 1
UID2 = 2


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
    economy_bank._loans.clear()
    economy_bank._interest_multiplier.clear()
    economy_bank._credit_min_override.clear()

    journal_dir = tempfile.mkdtemp(prefix="dignanti_admin12_test_journal_")
    economy_core.WALLET_JOURNAL_FILE = os.path.join(journal_dir, "wallet_journal.jsonl")


def arun(coro):
    return asyncio.run(coro)


class SuspiciousTransactionsTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_no_large_transactions_found(self):
        arun(economy_core.add_coins(CHAT, UID, 100, kind="REWARD"))
        found = economy_core.find_suspicious_transactions(CHAT, threshold=50000, since_ts=0)
        self.assertEqual(found, [])

    def test_large_transaction_detected(self):
        arun(economy_core.add_coins(CHAT, UID, 100_000, kind="REWARD"))
        found = economy_core.find_suspicious_transactions(CHAT, threshold=50000, since_ts=0)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["amount"], 100_000)

    def test_old_transaction_outside_lookback_excluded(self):
        arun(economy_core.add_coins(CHAT, UID, 100_000, kind="REWARD"))
        future_since = time.time() + 10
        found = economy_core.find_suspicious_transactions(CHAT, threshold=50000, since_ts=future_since)
        self.assertEqual(found, [])


class GroupWideViewsTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_list_group_businesses_spans_all_members(self):
        _stub_bot._known_members[CHAT] = {UID, UID2}
        b1 = economy_business._new_id(CHAT, UID)
        economy_business.owned(CHAT, UID).append({
            "id": b1, "type": "shop", "level": 1, "employees": 0, "inventory": 0,
            "price_mult": 1.0, "ad_until": None, "last_collect_ts": time.time(),
            "last_tax_ts": time.time(), "purchased_ts": time.time(),
            "lifetime_revenue": 0, "lifetime_expense": 0, "history": [],
            "debt": 0, "crisis_strikes": 0, "bankrupt": False, "partners": {}, "branches": 1,
        })
        b2 = economy_business._new_id(CHAT, UID2)
        economy_business.owned(CHAT, UID2).append({
            "id": b2, "type": "factory", "level": 1, "employees": 0, "inventory": 0,
            "price_mult": 1.0, "ad_until": None, "last_collect_ts": time.time(),
            "last_tax_ts": time.time(), "purchased_ts": time.time(),
            "lifetime_revenue": 0, "lifetime_expense": 0, "history": [],
            "debt": 0, "crisis_strikes": 0, "bankrupt": False, "partners": {}, "branches": 1,
        })
        rows = economy_admin.list_group_businesses(CHAT)
        owners = {uid for uid, _ in rows}
        self.assertEqual(owners, {UID, UID2})

    def test_list_group_businesses_sorted_by_value_desc(self):
        _stub_bot._known_members[CHAT] = {UID}
        for btype in ("shop", "factory"):  # factory has higher base_price than shop
            bid = economy_business._new_id(CHAT, UID)
            economy_business.owned(CHAT, UID).append({
                "id": bid, "type": btype, "level": 1, "employees": 0, "inventory": 0,
                "price_mult": 1.0, "ad_until": None, "last_collect_ts": time.time(),
                "last_tax_ts": time.time(), "purchased_ts": time.time(),
                "lifetime_revenue": 0, "lifetime_expense": 0, "history": [],
                "debt": 0, "crisis_strikes": 0, "bankrupt": False, "partners": {}, "branches": 1,
            })
        rows = economy_admin.list_group_businesses(CHAT)
        values = [economy_business.current_value(b) for _, b in rows]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_list_group_debts_only_includes_indebted_users(self):
        _stub_bot._known_members[CHAT] = {UID, UID2}
        bid = economy_business._new_id(CHAT, UID)
        economy_business.owned(CHAT, UID).append({
            "id": bid, "type": "shop", "level": 1, "employees": 0, "inventory": 0,
            "price_mult": 1.0, "ad_until": None, "last_collect_ts": time.time(),
            "last_tax_ts": time.time(), "purchased_ts": time.time(),
            "lifetime_revenue": 0, "lifetime_expense": 0, "history": [],
            "debt": 5000, "crisis_strikes": 1, "bankrupt": False, "partners": {}, "branches": 1,
        })
        # UID2 has no debt
        rows = economy_admin.list_group_debts(CHAT)
        uids = [r[0] for r in rows]
        self.assertIn(UID, uids)
        self.assertNotIn(UID2, uids)

    def test_list_group_debts_combines_business_and_loan_debt(self):
        _stub_bot._known_members[CHAT] = {UID}
        bid = economy_business._new_id(CHAT, UID)
        economy_business.owned(CHAT, UID).append({
            "id": bid, "type": "shop", "level": 1, "employees": 0, "inventory": 0,
            "price_mult": 1.0, "ad_until": None, "last_collect_ts": time.time(),
            "last_tax_ts": time.time(), "purchased_ts": time.time(),
            "lifetime_revenue": 0, "lifetime_expense": 0, "history": [],
            "debt": 3000, "crisis_strikes": 1, "bankrupt": False, "partners": {}, "branches": 1,
        })
        economy_bank._loans[CHAT][UID].append({
            "id": 1, "principal": 2000, "total_owed": 2000, "remaining": 2000,
            "rate": 0.08, "term_days": 7, "installment_amount": 2000,
            "installments_total": 1, "installments_paid": 0,
            "next_due_ts": time.time() + 999999, "missed_streak": 0,
            "status": "ACTIVE", "opened_ts": time.time(), "history": [],
        })
        rows = economy_admin.list_group_debts(CHAT)
        uid, business_debt, loan_debt, total = rows[0]
        self.assertEqual(business_debt, 3000)
        self.assertEqual(loan_debt, 2000)
        self.assertEqual(total, 5000)


class BankAdminOverridesTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_default_interest_multiplier_is_one(self):
        self.assertEqual(economy_bank.interest_multiplier(CHAT), 1.0)

    def test_set_interest_multiplier_applies_and_clamps(self):
        applied = economy_bank.set_interest_multiplier(CHAT, 1.5)
        self.assertEqual(applied, 1.5)
        self.assertEqual(economy_bank.interest_multiplier(CHAT), 1.5)

        too_high = economy_bank.set_interest_multiplier(CHAT, 100)
        self.assertEqual(too_high, config.BANK_MAX_INTEREST_MULTIPLIER)

        too_low = economy_bank.set_interest_multiplier(CHAT, -5)
        self.assertEqual(too_low, config.BANK_MIN_INTEREST_MULTIPLIER)

    def test_default_credit_min_matches_old_config_constant(self):
        self.assertEqual(economy_bank.credit_min_for_loan(CHAT), config.CREDIT_SCORE_MIN_FOR_LOAN)

    def test_set_credit_min_applies_and_clamps(self):
        applied = economy_bank.set_credit_min_for_loan(CHAT, 600)
        self.assertEqual(applied, 600)
        self.assertEqual(economy_bank.credit_min_for_loan(CHAT), 600)

        too_high = economy_bank.set_credit_min_for_loan(CHAT, 5000)
        self.assertEqual(too_high, config.CREDIT_MIN_FOR_LOAN_CEILING)

    def test_groups_are_independent(self):
        other_chat = CHAT + 1
        economy_bank.set_interest_multiplier(CHAT, 2.0)
        self.assertEqual(economy_bank.interest_multiplier(CHAT), 2.0)
        self.assertEqual(economy_bank.interest_multiplier(other_chat), 1.0)

    def test_save_load_round_trip(self):
        economy_bank.set_interest_multiplier(CHAT, 1.8)
        economy_bank.set_credit_min_for_loan(CHAT, 550)

        tmp_dir = tempfile.mkdtemp(prefix="dignanti_bank_admin_state_")
        old_state_file = economy_bank.STATE_FILE
        economy_bank.STATE_FILE = os.path.join(tmp_dir, "bank_state.json")
        try:
            economy_bank._write_state_file(economy_bank._collect_state())
            economy_bank._interest_multiplier.clear()
            economy_bank._credit_min_override.clear()
            economy_bank.load_state()
            self.assertEqual(economy_bank.interest_multiplier(CHAT), 1.8)
            self.assertEqual(economy_bank.credit_min_for_loan(CHAT), 550)
        finally:
            economy_bank.STATE_FILE = old_state_file


if __name__ == "__main__":
    unittest.main()
