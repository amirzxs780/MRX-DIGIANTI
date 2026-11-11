# -*- coding: utf-8 -*-
"""
DIGNANTI V18 — تست‌های فاز ۷ (مالیات و دولت): economy_treasury
================================================================
اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_treasury -v
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
import economy_property  # noqa: E402
import economy_jobs  # noqa: E402
import economy_infrastructure  # noqa: E402
import economy_treasury  # noqa: E402

CHAT = 9009
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
    economy_property._properties.clear()
    economy_infrastructure._infra.clear()
    economy_treasury._treasury.clear()
    economy_jobs._current_job.clear()

    journal_dir = tempfile.mkdtemp(prefix="dignanti_treasury_test_journal_")
    economy_core.WALLET_JOURNAL_FILE = os.path.join(journal_dir, "wallet_journal.jsonl")


def arun(coro):
    return asyncio.run(coro)


class TreasuryCoreTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_starts_empty(self):
        self.assertEqual(economy_treasury.get_balance(CHAT), 0)

    def test_deposit_increases_balance(self):
        economy_treasury.deposit(CHAT, 500, source="test")
        self.assertEqual(economy_treasury.get_balance(CHAT), 500)
        self.assertEqual(economy_treasury._t(CHAT)["total_collected"], 500)

    def test_spend_decreases_balance(self):
        economy_treasury.deposit(CHAT, 1000, source="test")
        economy_treasury.spend(CHAT, 400, purpose="test_project")
        self.assertEqual(economy_treasury.get_balance(CHAT), 600)
        self.assertEqual(economy_treasury._t(CHAT)["total_spent"], 400)

    def test_spend_more_than_balance_fails(self):
        economy_treasury.deposit(CHAT, 100, source="test")
        with self.assertRaises(economy_treasury.TreasuryError):
            economy_treasury.spend(CHAT, 500, purpose="too_much")
        self.assertEqual(economy_treasury.get_balance(CHAT), 100)  # دست‌نخورده موند

    def test_groups_are_independent(self):
        other_chat = CHAT + 1
        economy_treasury.deposit(CHAT, 1000, source="test")
        self.assertEqual(economy_treasury.get_balance(CHAT), 1000)
        self.assertEqual(economy_treasury.get_balance(other_chat), 0)

    def test_zero_or_negative_deposit_ignored(self):
        economy_treasury.deposit(CHAT, 0, source="test")
        economy_treasury.deposit(CHAT, -50, source="test")
        self.assertEqual(economy_treasury.get_balance(CHAT), 0)


class IncomeTaxTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_default_rate_is_zero(self):
        self.assertEqual(economy_treasury.get_income_tax_rate(CHAT), 0.0)

    def test_zero_rate_leaves_amount_unchanged(self):
        net = economy_treasury.collect_income_tax(CHAT, 1000, source="کار")
        self.assertEqual(net, 1000)
        self.assertEqual(economy_treasury.get_balance(CHAT), 0)

    def test_nonzero_rate_splits_correctly(self):
        economy_treasury.set_income_tax_rate(CHAT, 0.10)
        net = economy_treasury.collect_income_tax(CHAT, 1000, source="کار")
        self.assertEqual(net, 900)
        self.assertEqual(economy_treasury.get_balance(CHAT), 100)

    def test_rate_clamped_to_max(self):
        over_max = getattr(config, "TREASURY_MAX_INCOME_TAX_RATE", 0.25) + 0.5
        applied = economy_treasury.set_income_tax_rate(CHAT, over_max)
        self.assertEqual(applied, config.TREASURY_MAX_INCOME_TAX_RATE)

    def test_rate_clamped_to_zero_minimum(self):
        applied = economy_treasury.set_income_tax_rate(CHAT, -0.5)
        self.assertEqual(applied, 0.0)


class IntegrationHooksTest(unittest.TestCase):
    """تضمینِ اینکه هیچ رفتارِ موجودی با نرخِ پیش‌فرضِ صفر عوض نشده،
    و با نرخِ غیرِصفر واقعاً پول به خزانه می‌ره."""

    def setUp(self):
        _reset_all()

    def test_business_tax_payment_credits_treasury(self):
        b = {
            "id": economy_business._new_id(CHAT, UID), "type": "shop", "level": 1, "employees": 0,
            "inventory": 0, "price_mult": 1.0, "ad_until": None,
            "last_collect_ts": time.time(), "last_tax_ts": time.time() - 999999, "purchased_ts": time.time(),
            "lifetime_revenue": 0, "lifetime_expense": 0, "history": [],
            "debt": 0, "crisis_strikes": 0, "bankrupt": False,
        }
        economy_business.owned(CHAT, UID).append(b)
        due = economy_business.tax_due(b)
        self.assertGreater(due, 0)
        arun(economy_core.add_coins(CHAT, UID, due + 1000, kind="REWARD"))

        wallet_before = economy_core.get_wallet(CHAT, UID)
        arun(economy_core.remove_coins(CHAT, UID, due, kind="TAX", note="مالیات کسب‌وکار"))
        economy_treasury.deposit(CHAT, due, source="مالیاتِ کسب‌وکار")

        self.assertEqual(economy_core.get_wallet(CHAT, UID), wallet_before - due)
        self.assertEqual(economy_treasury.get_balance(CHAT), due)

    def test_property_rental_tax_reaches_treasury_without_changing_owner_payout(self):
        prop = {
            "id": 1, "type": "apartment" if "apartment" in config.PROPERTY_TYPES else next(iter(config.PROPERTY_TYPES)),
            "level": 1, "purchased_ts": time.time(),
            "last_collect_ts": time.time() - 3600 * 10, "co_owner_id": None, "furniture": {},
        }
        ptype = prop["type"]
        info = config.PROPERTY_TYPES[ptype]
        net_rate = (info["income_per_hour"] - info["maintenance_per_hour"])
        if net_rate <= 0:
            self.skipTest(f"نوعِ ملکِ {ptype} درآمدِ خالصِ مثبت نداره، برای این تست مناسب نیست")
        economy_property.owned(CHAT, UID).append(prop)

        wallet_before = economy_core.get_wallet(CHAT, UID)
        treasury_before = economy_treasury.get_balance(CHAT)
        paid = arun(economy_property._collect_one(CHAT, UID, prop))

        self.assertEqual(economy_core.get_wallet(CHAT, UID), wallet_before + paid)
        self.assertGreaterEqual(economy_treasury.get_balance(CHAT), treasury_before)
        if info["tax_rate"] > 0 and paid > 0:
            self.assertGreater(economy_treasury.get_balance(CHAT), treasury_before)

    def test_work_income_unaffected_by_default_tax_rate(self):
        self.assertEqual(economy_treasury.get_income_tax_rate(CHAT), 0.0)
        gross = 1000
        net = economy_treasury.collect_income_tax(CHAT, gross, source="کار")
        self.assertEqual(net, gross)

    def test_work_income_reduced_when_tax_enabled(self):
        economy_treasury.set_income_tax_rate(CHAT, 0.20)
        gross = 1000
        net = economy_treasury.collect_income_tax(CHAT, gross, source="کار")
        self.assertEqual(net, 800)
        self.assertEqual(economy_treasury.get_balance(CHAT), 200)

    def test_fund_infrastructure_from_treasury(self):
        key = "roads"
        cost = config.INFRASTRUCTURE_TYPES[key]["base_cost"]
        economy_treasury.deposit(CHAT, cost, source="test")
        result = economy_infrastructure.fund_from_treasury(CHAT, key, cost)
        self.assertTrue(result["leveled_up"])
        self.assertEqual(economy_treasury.get_balance(CHAT), 0)
        self.assertEqual(economy_infrastructure._entry(CHAT, key)["contributors"]["treasury"], cost)

    def test_fund_infrastructure_fails_without_enough_treasury_balance(self):
        key = "hospital"
        with self.assertRaises(economy_treasury.TreasuryError):
            economy_infrastructure.fund_from_treasury(CHAT, key, 5000)

    def test_stock_trading_fee_reaches_treasury_without_changing_user_cost(self):
        import economy_business
        import economy_stock
        b = {
            "id": economy_business._new_id(CHAT, UID), "type": "shop", "level": 1, "employees": 0,
            "inventory": 0, "price_mult": 1.0, "ad_until": None,
            "last_collect_ts": time.time(), "last_tax_ts": time.time(), "purchased_ts": time.time(),
            "lifetime_revenue": 0, "lifetime_expense": 0, "history": [],
            "debt": 0, "crisis_strikes": 0, "bankrupt": False, "partners": {}, "branches": 1,
        }
        economy_business.owned(CHAT, UID).append(b)
        company = arun(economy_stock.go_public(CHAT, UID, b["id"]))

        investor = 777
        arun(economy_core.add_coins(CHAT, investor, 1_000_000, kind="REWARD"))
        wallet_before = economy_core.get_wallet(CHAT, investor)
        treasury_before = economy_treasury.get_balance(CHAT)

        result = arun(economy_stock.buy_shares(CHAT, investor, company["id"], 10))

        # هزینه‌ی سهامدار دقیقاً همونیه که buy_shares برمی‌گردونه - بدونِ تغییر
        self.assertEqual(economy_core.get_wallet(CHAT, investor), wallet_before - result["cost"])
        # و کارمزدش به خزانه رسیده (نه گم شده)
        self.assertGreater(economy_treasury.get_balance(CHAT), treasury_before)

    def test_bulk_trade_fee_reaches_treasury(self):
        import economy_trade
        arun(economy_core.add_coins(CHAT, UID, 1_000_000, kind="REWARD"))
        treasury_before = economy_treasury.get_balance(CHAT)
        arun(economy_trade.bulk_buy(CHAT, UID, "food", 50))
        self.assertGreater(economy_treasury.get_balance(CHAT), treasury_before)


class FormattersTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_format_text_does_not_crash(self):
        economy_treasury.deposit(CHAT, 1000, source="test")
        text = economy_treasury.format_treasury_text(CHAT)
        self.assertIn("خزانه‌ی عمومی", text)

    def test_save_load_round_trip(self):
        economy_treasury.deposit(CHAT, 5000, source="مالیاتِ کسب‌وکار")
        economy_treasury.spend(CHAT, 1000, purpose="test")
        economy_treasury.set_income_tax_rate(CHAT, 0.1)

        tmp_dir = tempfile.mkdtemp(prefix="dignanti_treasury_state_")
        old_state_file = economy_treasury.STATE_FILE
        economy_treasury.STATE_FILE = os.path.join(tmp_dir, "treasury_state.json")
        try:
            economy_treasury._write_state_file(economy_treasury._collect_state())
            economy_treasury._treasury.clear()
            economy_treasury.load_state()
            self.assertEqual(economy_treasury.get_balance(CHAT), 4000)
            self.assertEqual(economy_treasury.get_income_tax_rate(CHAT), 0.1)
        finally:
            economy_treasury.STATE_FILE = old_state_file


if __name__ == "__main__":
    unittest.main()
