# -*- coding: utf-8 -*-
"""
DIGNANTI V18 — تست‌های فاز ۱۱ (هوشِ اقتصادی و تحلیل): economy_analysis
================================================================
اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_analysis -v
"""
from __future__ import annotations

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
import economy_supply  # noqa: E402
import economy_analysis  # noqa: E402

CHAT = 13013
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
    economy_bank._loans.clear()

    economy_supply._prices.clear()
    economy_supply._pressure.clear()
    economy_supply._history.clear()
    economy_supply._last_tick_ts.clear()
    economy_supply._total_demand.clear()
    economy_supply._total_supply.clear()

    journal_dir = tempfile.mkdtemp(prefix="dignanti_analysis_test_journal_")
    economy_core.WALLET_JOURNAL_FILE = os.path.join(journal_dir, "wallet_journal.jsonl")


def _make_business(chat_id, uid, btype="shop", inventory=500, revenue=0, expense=0, debt=0, bankrupt=False, strikes=0):
    b = {
        "id": economy_business._new_id(chat_id, uid), "type": btype, "level": 1,
        "employees": 0, "inventory": inventory, "price_mult": 1.0, "ad_until": None,
        "last_collect_ts": time.time(), "last_tax_ts": time.time(), "purchased_ts": time.time(),
        "lifetime_revenue": revenue, "lifetime_expense": expense, "history": [],
        "debt": debt, "crisis_strikes": strikes, "bankrupt": bankrupt, "partners": {}, "branches": 1,
    }
    economy_business.owned(chat_id, uid).append(b)
    return b


class BusinessInsightsTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_no_businesses_gives_no_insights(self):
        self.assertEqual(economy_analysis.business_insights(CHAT, UID), [])

    def test_bankrupt_business_flagged(self):
        _make_business(CHAT, UID, bankrupt=True, debt=5000)
        insights = economy_analysis.business_insights(CHAT, UID)
        self.assertTrue(any("ورشکسته" in i for i in insights))

    def test_crisis_strike_flagged(self):
        _make_business(CHAT, UID, strikes=1)
        insights = economy_analysis.business_insights(CHAT, UID)
        self.assertTrue(any("بحرانِ مالی" in i for i in insights))

    def test_expenses_exceeding_revenue_flagged(self):
        _make_business(CHAT, UID, revenue=1000, expense=1500)
        insights = economy_analysis.business_insights(CHAT, UID)
        self.assertTrue(any("هزینه‌هایِ تجمعی" in i for i in insights))

    def test_healthy_business_with_more_revenue_not_flagged_for_losses(self):
        _make_business(CHAT, UID, revenue=5000, expense=1000)
        insights = economy_analysis.business_insights(CHAT, UID)
        self.assertFalse(any("هزینه‌هایِ تجمعی" in i for i in insights))

    def test_low_inventory_flagged(self):
        # shop مصرفِ موجودیِ ساعتی داره؛ با موجودیِ خیلی کم باید هشدار بده
        _make_business(CHAT, UID, inventory=1)
        insights = economy_analysis.business_insights(CHAT, UID)
        self.assertTrue(any("موجودیِ انبار" in i for i in insights))

    def test_ample_inventory_not_flagged(self):
        _make_business(CHAT, UID, inventory=100000)
        insights = economy_analysis.business_insights(CHAT, UID)
        self.assertFalse(any("موجودیِ انبار" in i for i in insights))

    def test_debt_without_bankruptcy_flagged_as_working_capital_need(self):
        _make_business(CHAT, UID, debt=1000, bankrupt=False)
        insights = economy_analysis.business_insights(CHAT, UID)
        self.assertTrue(any("سرمایه‌ی در گردش" in i for i in insights))

    def test_bankrupt_business_not_double_flagged_for_debt(self):
        _make_business(CHAT, UID, debt=1000, bankrupt=True)
        insights = economy_analysis.business_insights(CHAT, UID)
        debt_lines = [i for i in insights if "سرمایه‌ی در گردش" in i]
        self.assertEqual(len(debt_lines), 0)  # فقط پیامِ ورشکستگی باید باشه


class CommodityInsightsTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_no_shortage_gives_no_insights(self):
        self.assertEqual(economy_analysis.commodity_insights(CHAT), [])

    def test_shortage_detected_and_reported(self):
        economy_supply.record_demand(CHAT, "food", 1000)
        insights = economy_analysis.commodity_insights(CHAT)
        self.assertTrue(any("غذا" in i or "food" in i.lower() for i in insights))


class LoanInsightsTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_no_loans_gives_no_insights(self):
        self.assertEqual(economy_analysis.loan_insights(CHAT, UID), [])

    def test_due_loan_flagged(self):
        economy_bank._loans[CHAT][UID].append({
            "id": 1, "principal": 1000, "total_owed": 1080, "remaining": 1080,
            "rate": 0.08, "term_days": 7, "installment_amount": 1080,
            "installments_total": 1, "installments_paid": 0,
            "next_due_ts": time.time() - 100, "missed_streak": 0,
            "status": "ACTIVE", "opened_ts": time.time() - 999999, "history": [],
        })
        insights = economy_analysis.loan_insights(CHAT, UID)
        self.assertTrue(any("سررسید" in i for i in insights))

    def test_future_loan_not_flagged(self):
        economy_bank._loans[CHAT][UID].append({
            "id": 1, "principal": 1000, "total_owed": 1080, "remaining": 1080,
            "rate": 0.08, "term_days": 7, "installment_amount": 1080,
            "installments_total": 1, "installments_paid": 0,
            "next_due_ts": time.time() + 999999, "missed_streak": 0,
            "status": "ACTIVE", "opened_ts": time.time(), "history": [],
        })
        self.assertEqual(economy_analysis.loan_insights(CHAT, UID), [])


class CombinedAndFormattingTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_generate_insights_combines_all_sources(self):
        _make_business(CHAT, UID, bankrupt=True, debt=2000)
        economy_bank._loans[CHAT][UID].append({
            "id": 1, "principal": 500, "total_owed": 540, "remaining": 540,
            "rate": 0.08, "term_days": 7, "installment_amount": 540,
            "installments_total": 1, "installments_paid": 0,
            "next_due_ts": time.time() - 10, "missed_streak": 0,
            "status": "ACTIVE", "opened_ts": time.time(), "history": [],
        })
        insights = economy_analysis.generate_insights(CHAT, UID)
        self.assertTrue(any("ورشکسته" in i for i in insights))
        self.assertTrue(any("سررسید" in i for i in insights))

    def test_format_text_clean_state(self):
        text = economy_analysis.format_insights_text(CHAT, UID)
        self.assertIn("تحلیلِ اقتصادیِ من", text)
        self.assertIn("هیچ هشدار", text)

    def test_format_text_with_issues_does_not_crash(self):
        _make_business(CHAT, UID, bankrupt=True, debt=1000)
        text = economy_analysis.format_insights_text(CHAT, UID)
        self.assertIn("تحلیلِ اقتصادیِ من", text)
        self.assertIn("ورشکسته", text)

    def test_groups_are_independent(self):
        other_chat = CHAT + 1
        _make_business(CHAT, UID, bankrupt=True, debt=1000)
        self.assertTrue(economy_analysis.business_insights(CHAT, UID))
        self.assertFalse(economy_analysis.business_insights(other_chat, UID))


if __name__ == "__main__":
    unittest.main()
