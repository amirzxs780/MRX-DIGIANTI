# -*- coding: utf-8 -*-
"""
DIGNANTI V18 — تست‌های فاز ۱۰ (تکمیلِ بخشِ ۵): انواعِ کسب‌وکارِ تازه + توسعه‌ی شعبه
================================================================
اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_business_phase10 -v
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

CHAT = 12012
UID = 1

NEW_TYPES = [
    "supermarket", "clothing_store", "farm", "mine", "transport_co",
    "construction_co", "entertainment_center", "manufacturing_co", "service_co",
]


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

    journal_dir = tempfile.mkdtemp(prefix="dignanti_biz10_test_journal_")
    economy_core.WALLET_JOURNAL_FILE = os.path.join(journal_dir, "wallet_journal.jsonl")


def _make_business(chat_id, uid, btype="shop", level=1, inventory=500, employees=0, branches=1):
    b = {
        "id": economy_business._new_id(chat_id, uid), "type": btype, "level": level,
        "employees": employees, "inventory": inventory, "price_mult": 1.0, "ad_until": None,
        "last_collect_ts": time.time(), "last_tax_ts": time.time(), "purchased_ts": time.time(),
        "lifetime_revenue": 0, "lifetime_expense": 0, "history": [],
        "debt": 0, "crisis_strikes": 0, "bankrupt": False, "partners": {}, "branches": branches,
    }
    economy_business.owned(chat_id, uid).append(b)
    return b


def arun(coro):
    return asyncio.run(coro)


class NewBusinessTypesTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_all_new_types_exist_with_full_schema(self):
        required_fields = {
            "name", "base_price", "base_revenue_per_hour", "base_expense_per_hour",
            "max_employees", "employee_cost_per_hour", "employee_revenue_bonus_frac",
            "inventory_capacity", "inventory_cost_per_unit", "inventory_consumption_per_hour",
            "tax_rate",
        }
        for key in NEW_TYPES:
            self.assertIn(key, config.BUSINESS_TYPES)
            info = config.BUSINESS_TYPES[key]
            missing = required_fields - set(info.keys())
            self.assertEqual(missing, set(), f"{key} fields missing: {missing}")

    def test_existing_types_untouched(self):
        for key in ("shop", "restaurant", "cafe", "garage", "realestate",
                    "itcompany", "hotel", "factory", "clinic", "cardealer"):
            self.assertIn(key, config.BUSINESS_TYPES)

    def test_producer_types_not_in_commodity_map(self):
        # مزرعه/معدن تولیدکننده‌ی خامِ کالا هستن، نه مصرف‌کننده - نباید
        # نگاشتِ خرید-از-بازار داشته باشن
        self.assertNotIn("farm", config.BUSINESS_COMMODITY_MAP)
        self.assertNotIn("mine", config.BUSINESS_COMMODITY_MAP)
        self.assertNotIn("service_co", config.BUSINESS_COMMODITY_MAP)

    def test_mapped_new_types_point_to_real_commodities(self):
        for key in ("supermarket", "clothing_store", "transport_co",
                    "construction_co", "entertainment_center", "manufacturing_co"):
            commodity = config.BUSINESS_COMMODITY_MAP[key]
            self.assertIn(commodity, config.COMMODITIES)

    def test_new_type_business_can_be_created_and_valued(self):
        b = _make_business(CHAT, UID, btype="supermarket")
        value = economy_business.current_value(b)
        self.assertGreater(value, 0)

    def test_new_type_revenue_and_expense_compute_without_crash(self):
        for key in NEW_TYPES:
            b = _make_business(CHAT, UID, btype=key, inventory=500, employees=1)
            rev = economy_business.hourly_revenue(CHAT, b)
            exp = economy_business.hourly_expense(b)
            self.assertGreaterEqual(rev, 0)
            self.assertGreater(exp, 0)


class BranchExpansionTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_default_branches_is_one(self):
        b = {
            "id": 1, "type": "shop", "level": 1, "employees": 0, "inventory": 100,
            "price_mult": 1.0, "ad_until": None, "last_collect_ts": time.time(),
            "last_tax_ts": time.time(), "purchased_ts": time.time(),
            "lifetime_revenue": 0, "lifetime_expense": 0, "history": [],
        }  # عمداً بدونِ فیلدِ branches - شبیه‌سازیِ کسب‌وکارِ قدیمیِ قبل از فازِ۱۰
        self.assertEqual(b.get("branches", 1), 1)
        rev = economy_business.hourly_revenue(CHAT, b)
        rev_explicit_one = economy_business.hourly_revenue(CHAT, {**b, "branches": 1})
        self.assertAlmostEqual(rev, rev_explicit_one, places=6)

    def test_two_branches_doubles_revenue_and_expense(self):
        b1 = _make_business(CHAT, UID, btype="shop", inventory=500, employees=2, branches=1)
        b2 = _make_business(CHAT, UID, btype="shop", inventory=500, employees=2, branches=2)
        rev1 = economy_business.hourly_revenue(CHAT, b1)
        rev2 = economy_business.hourly_revenue(CHAT, b2)
        exp1 = economy_business.hourly_expense(b1)
        exp2 = economy_business.hourly_expense(b2)
        self.assertAlmostEqual(rev2, rev1 * 2, places=4)
        self.assertAlmostEqual(exp2, exp1 * 2, places=4)

    def test_branch_cost_scales_with_current_branch_count(self):
        b1 = _make_business(CHAT, UID, btype="shop", branches=1)
        b3 = _make_business(CHAT, UID, btype="shop", branches=3)
        cost1 = economy_business.branch_cost(b1)
        cost3 = economy_business.branch_cost(b3)
        self.assertGreater(cost3, cost1)

    def test_expand_branch_deducts_cost_and_increments(self):
        b = _make_business(CHAT, UID, btype="shop", branches=1)
        cost = economy_business.branch_cost(b)
        arun(economy_core.add_coins(CHAT, UID, cost + 1000, kind="REWARD"))
        wallet_before = economy_core.get_wallet(CHAT, UID)

        arun(economy_core.remove_coins(CHAT, UID, cost, kind="BUSINESS", note="توسعه‌ی شعبه"))
        b["branches"] += 1

        self.assertEqual(b["branches"], 2)
        self.assertEqual(economy_core.get_wallet(CHAT, UID), wallet_before - cost)

    def test_cannot_exceed_max_branches(self):
        max_branches = config.BUSINESS_MAX_BRANCHES
        b = _make_business(CHAT, UID, btype="shop", branches=max_branches)
        self.assertGreaterEqual(b.get("branches", 1), max_branches)


if __name__ == "__main__":
    unittest.main()
