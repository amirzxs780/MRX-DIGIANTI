# -*- coding: utf-8 -*-
"""
DIGNANTI V18 — تست‌های فاز ۱۵ (تکمیلِ بخشِ ۹): استخدامِ کاربرانِ واقعی
================================================================
اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_employment -v
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
import economy_employment  # noqa: E402

CHAT = 16016
OWNER = 1
APPLICANT = 2
APPLICANT2 = 3


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

    economy_employment._applications.clear()
    economy_employment._next_app_id.clear()

    journal_dir = tempfile.mkdtemp(prefix="dignanti_employment_test_journal_")
    economy_core.WALLET_JOURNAL_FILE = os.path.join(journal_dir, "wallet_journal.jsonl")


def _make_business(chat_id, uid, btype="shop", level=1, inventory=500, employees=0, bankrupt=False):
    b = {
        "id": economy_business._new_id(chat_id, uid), "type": btype, "level": level,
        "employees": employees, "inventory": inventory, "price_mult": 1.0, "ad_until": None,
        "last_collect_ts": time.time(), "last_tax_ts": time.time(), "purchased_ts": time.time(),
        "lifetime_revenue": 0, "lifetime_expense": 0, "history": [],
        "debt": 0, "crisis_strikes": 0, "bankrupt": bankrupt, "partners": {}, "branches": 1,
        "employed_users": {},
    }
    economy_business.owned(chat_id, uid).append(b)
    return b


def arun(coro):
    return asyncio.run(coro)


class ApplicationFlowTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_cannot_apply_to_own_business(self):
        b = _make_business(CHAT, OWNER)
        with self.assertRaises(economy_employment.EmploymentError):
            economy_employment.apply(CHAT, OWNER, OWNER, b["id"])

    def test_apply_rejects_unowned_business(self):
        with self.assertRaises(economy_employment.EmploymentError):
            economy_employment.apply(CHAT, APPLICANT, OWNER, 999)

    def test_apply_rejects_bankrupt_business(self):
        b = _make_business(CHAT, OWNER, bankrupt=True)
        with self.assertRaises(economy_employment.EmploymentError):
            economy_employment.apply(CHAT, APPLICANT, OWNER, b["id"])

    def test_apply_rejects_full_capacity(self):
        b = _make_business(CHAT, OWNER, btype="shop")
        info = economy_business.TYPES["shop"]
        b["employees"] = info["max_employees"]
        with self.assertRaises(economy_employment.EmploymentError):
            economy_employment.apply(CHAT, APPLICANT, OWNER, b["id"])

    def test_business_untouched_until_accepted(self):
        b = _make_business(CHAT, OWNER)
        economy_employment.apply(CHAT, APPLICANT, OWNER, b["id"])
        self.assertEqual(b["employed_users"], {})
        self.assertEqual(b["employees"], 0)

    def test_accept_adds_employee_and_increments_count(self):
        b = _make_business(CHAT, OWNER)
        a = economy_employment.apply(CHAT, APPLICANT, OWNER, b["id"])
        economy_employment.accept(CHAT, OWNER, a["id"], salary_per_hour=5)
        self.assertIn(str(APPLICANT), b["employed_users"])
        self.assertEqual(b["employees"], 1)

    def test_only_owner_can_accept(self):
        b = _make_business(CHAT, OWNER)
        a = economy_employment.apply(CHAT, APPLICANT, OWNER, b["id"])
        with self.assertRaises(economy_employment.EmploymentError):
            economy_employment.accept(CHAT, APPLICANT2, a["id"], salary_per_hour=5)

    def test_wage_clamped_to_employee_cost_per_hour(self):
        b = _make_business(CHAT, OWNER, btype="shop")
        max_wage = economy_business.TYPES["shop"]["employee_cost_per_hour"]
        a = economy_employment.apply(CHAT, APPLICANT, OWNER, b["id"])
        economy_employment.accept(CHAT, OWNER, a["id"], salary_per_hour=max_wage * 10)
        self.assertEqual(b["employed_users"][str(APPLICANT)]["salary_per_hour"], max_wage)

    def test_reject_leaves_business_untouched(self):
        b = _make_business(CHAT, OWNER)
        a = economy_employment.apply(CHAT, APPLICANT, OWNER, b["id"])
        economy_employment.reject(CHAT, OWNER, a["id"])
        self.assertEqual(b["employed_users"], {})
        self.assertEqual(economy_employment._applications[CHAT][a["id"]]["status"], "rejected")

    def test_cannot_double_apply_when_already_employed(self):
        b = _make_business(CHAT, OWNER)
        a = economy_employment.apply(CHAT, APPLICANT, OWNER, b["id"])
        economy_employment.accept(CHAT, OWNER, a["id"], salary_per_hour=5)
        with self.assertRaises(economy_employment.EmploymentError):
            economy_employment.apply(CHAT, APPLICANT, OWNER, b["id"])

    def test_max_pending_applications_per_user(self):
        bids = [_make_business(CHAT, OWNER)["id"] for _ in range(config.EMPLOYMENT_MAX_PENDING_PER_USER + 1)]
        for bid in bids[:-1]:
            economy_employment.apply(CHAT, APPLICANT, OWNER, bid)
        with self.assertRaises(economy_employment.EmploymentError):
            economy_employment.apply(CHAT, APPLICANT, OWNER, bids[-1])

    def test_fire_employee_removes_and_decrements(self):
        b = _make_business(CHAT, OWNER)
        a = economy_employment.apply(CHAT, APPLICANT, OWNER, b["id"])
        economy_employment.accept(CHAT, OWNER, a["id"], salary_per_hour=5)
        economy_employment.fire_employee(CHAT, OWNER, b["id"], APPLICANT)
        self.assertNotIn(str(APPLICANT), b["employed_users"])
        self.assertEqual(b["employees"], 0)


class WagePayoutTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_employee_gets_paid_real_wage_on_collect(self):
        b = _make_business(CHAT, OWNER, btype="shop", level=1, inventory=100000)
        a = economy_employment.apply(CHAT, APPLICANT, OWNER, b["id"])
        max_wage = economy_business.TYPES["shop"]["employee_cost_per_hour"]
        economy_employment.accept(CHAT, OWNER, a["id"], salary_per_hour=max_wage)

        b["last_collect_ts"] = time.time() - 3600 * 5  # ۵ ساعتِ گذشته
        net, fired = arun(economy_business._collect_one(CHAT, OWNER, b))
        if net <= 0:
            self.skipTest("این Levelِ کسب‌وکار سودِ مثبت نداد")

        self.assertGreater(economy_core.get_wallet(CHAT, APPLICANT), 0)

    def test_total_payout_never_exceeds_net_profit_with_real_employee(self):
        b = _make_business(CHAT, OWNER, btype="shop", level=1, inventory=100000)
        a = economy_employment.apply(CHAT, APPLICANT, OWNER, b["id"])
        max_wage = economy_business.TYPES["shop"]["employee_cost_per_hour"]
        economy_employment.accept(CHAT, OWNER, a["id"], salary_per_hour=max_wage)

        b["last_collect_ts"] = time.time() - 3600 * 10
        net, fired = arun(economy_business._collect_one(CHAT, OWNER, b))
        if net <= 0:
            self.skipTest("سودِ مثبتی تولید نشد")

        total_paid = economy_core.get_wallet(CHAT, OWNER) + economy_core.get_wallet(CHAT, APPLICANT)
        self.assertEqual(total_paid, net)

    def test_no_employed_users_pays_owner_fully_unchanged(self):
        b = _make_business(CHAT, OWNER, btype="shop", level=1, inventory=100000)
        b["last_collect_ts"] = time.time() - 3600 * 5
        net, fired = arun(economy_business._collect_one(CHAT, OWNER, b))
        if net > 0:
            self.assertEqual(economy_core.get_wallet(CHAT, OWNER), net)
            self.assertEqual(economy_core.get_wallet(CHAT, APPLICANT), 0)

    def test_employed_user_and_partner_coexist_correctly(self):
        import economy_partnership
        b = _make_business(CHAT, OWNER, btype="shop", level=1, inventory=100000)
        a = economy_employment.apply(CHAT, APPLICANT, OWNER, b["id"])
        max_wage = economy_business.TYPES["shop"]["employee_cost_per_hour"]
        economy_employment.accept(CHAT, OWNER, a["id"], salary_per_hour=max_wage)

        p = economy_partnership.propose(CHAT, OWNER, b["id"], APPLICANT2, 0.3)
        economy_partnership.accept(CHAT, APPLICANT2, p["id"])

        b["last_collect_ts"] = time.time() - 3600 * 10
        net, fired = arun(economy_business._collect_one(CHAT, OWNER, b))
        if net <= 0:
            self.skipTest("سودِ مثبتی تولید نشد")

        total_paid = (
            economy_core.get_wallet(CHAT, OWNER)
            + economy_core.get_wallet(CHAT, APPLICANT)
            + economy_core.get_wallet(CHAT, APPLICANT2)
        )
        self.assertEqual(total_paid, net)  # هیچ پولی از هوا اضافه/کم نشد


class FormattersAndMiscTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_pending_for_owner_filters_correctly(self):
        b = _make_business(CHAT, OWNER)
        economy_employment.apply(CHAT, APPLICANT, OWNER, b["id"])
        self.assertEqual(len(economy_employment.pending_for_owner(CHAT, OWNER)), 1)
        self.assertEqual(len(economy_employment.pending_for_owner(CHAT, APPLICANT2)), 0)

    def test_groups_are_independent(self):
        other_chat = CHAT + 1
        b = _make_business(CHAT, OWNER)
        economy_employment.apply(CHAT, APPLICANT, OWNER, b["id"])
        self.assertEqual(len(economy_employment.pending_for_owner(CHAT, OWNER)), 1)
        self.assertEqual(len(economy_employment.pending_for_owner(other_chat, OWNER)), 0)

    def test_save_load_round_trip(self):
        b = _make_business(CHAT, OWNER)
        a = economy_employment.apply(CHAT, APPLICANT, OWNER, b["id"])

        tmp_dir = tempfile.mkdtemp(prefix="dignanti_employment_state_")
        old_state_file = economy_employment.STATE_FILE
        economy_employment.STATE_FILE = os.path.join(tmp_dir, "employment_state.json")
        try:
            economy_employment._write_state_file(economy_employment._collect_state())
            economy_employment._applications.clear()
            economy_employment._next_app_id.clear()
            economy_employment.load_state()

            restored = economy_employment._applications[CHAT][a["id"]]
            self.assertEqual(restored["applicant_uid"], APPLICANT)
            self.assertEqual(restored["status"], "pending")
        finally:
            economy_employment.STATE_FILE = old_state_file

    def test_format_text_does_not_crash(self):
        b = _make_business(CHAT, OWNER)
        economy_employment.apply(CHAT, APPLICANT, OWNER, b["id"])
        text = economy_employment.format_pending_text(CHAT, OWNER)
        self.assertIn("درخواست‌هایِ استخدامِ در انتظار", text)


if __name__ == "__main__":
    unittest.main()
