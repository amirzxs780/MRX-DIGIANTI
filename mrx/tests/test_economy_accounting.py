# -*- coding: utf-8 -*-
"""
DIGNANTI V18 — تست‌های فاز ۵ (بانک و حسابداری): economy_accounting
================================================================
اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_accounting -v
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
import economy_accounting  # noqa: E402

CHAT = 6006
UID = 1
OTHER_UID = 2


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

    journal_dir = tempfile.mkdtemp(prefix="dignanti_acct_test_journal_")
    economy_core.WALLET_JOURNAL_FILE = os.path.join(journal_dir, "wallet_journal.jsonl")


def _make_business(chat_id, uid, btype="shop", level=1, revenue=0, expense=0, debt=0, bankrupt=False):
    b = {
        "id": economy_business._new_id(chat_id, uid), "type": btype, "level": level,
        "employees": 0, "inventory": 0, "price_mult": 1.0, "ad_until": None,
        "last_collect_ts": time.time(), "last_tax_ts": time.time(), "purchased_ts": time.time(),
        "lifetime_revenue": revenue, "lifetime_expense": expense, "history": [],
        "debt": debt, "crisis_strikes": 0, "bankrupt": bankrupt,
    }
    economy_business.owned(chat_id, uid).append(b)
    return b


def arun(coro):
    return asyncio.run(coro)


class IncomeExpenseReportTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_totals_are_accurate(self):
        arun(economy_core.add_coins(CHAT, UID, 1000, kind="work", note="کار — نجار"))
        arun(economy_core.add_coins(CHAT, UID, 500, kind="BUSINESS", note="سود فروشگاه #1"))
        arun(economy_core.remove_coins(CHAT, UID, 300, kind="TAX", note="مالیات"))

        report = economy_accounting.income_expense_report(CHAT, UID)
        self.assertEqual(report["total_income"], 1500)
        self.assertEqual(report["total_expense"], 300)
        self.assertEqual(report["net"], 1200)
        self.assertEqual(report["turnover"], 1800)
        self.assertEqual(report["txn_count"], 3)

    def test_other_type_gets_heuristic_subcategorized(self):
        arun(economy_core.add_coins(CHAT, UID, 1000, kind="work", note="کار — نجار"))
        arun(economy_core.add_coins(CHAT, UID, 2000, kind="salary", note="حقوق نجار"))
        report = economy_accounting.income_expense_report(CHAT, UID)
        self.assertIn("شغل", report["income_by_cat"])
        self.assertEqual(report["income_by_cat"]["شغل"], 3000)

    def test_stock_and_auction_categorized_correctly(self):
        arun(economy_core.add_coins(CHAT, UID, 10_000, kind="REWARD"))
        arun(economy_core.remove_coins(CHAT, UID, 500, kind="stock_buy", note="خریدِ سهامِ شرکت #1"))
        arun(economy_core.remove_coins(CHAT, UID, 200, kind="auction_bid", note="پیشنهاد روی مزایده‌ی #2"))
        report = economy_accounting.income_expense_report(CHAT, UID)
        self.assertEqual(report["expense_by_cat"].get("بورس سهام"), 500)
        self.assertEqual(report["expense_by_cat"].get("مزایده"), 200)

    def test_period_filter_excludes_old_transactions(self):
        # یه تراکنشِ «قدیمی» رو با Timestampِ دستکاری‌شده شبیه‌سازی کن
        arun(economy_core.add_coins(CHAT, UID, 5000, kind="REWARD", note="قدیمی"))
        old_ids = list(economy_core._ledger_by_user[CHAT][UID])
        for tid in old_ids:
            economy_core._ledger[tid]["timestamp"] = time.time() - 40 * 86400  # ۴۰ روزِ پیش

        arun(economy_core.add_coins(CHAT, UID, 777, kind="REWARD", note="جدید"))

        report_week = economy_accounting.income_expense_report(CHAT, UID, period="هفتگی")
        self.assertEqual(report_week["total_income"], 777)  # فقط تراکنشِ جدید

        report_all = economy_accounting.income_expense_report(CHAT, UID, period=None)
        self.assertEqual(report_all["total_income"], 5777)  # هر دو

    def test_zero_activity_gives_clean_zero_report(self):
        report = economy_accounting.income_expense_report(CHAT, UID)
        self.assertEqual(report["total_income"], 0)
        self.assertEqual(report["total_expense"], 0)
        self.assertEqual(report["net"], 0)
        self.assertEqual(report["txn_count"], 0)

    def test_groups_are_independent(self):
        other_chat = CHAT + 1
        arun(economy_core.add_coins(CHAT, UID, 1000, kind="REWARD"))
        report_here = economy_accounting.income_expense_report(CHAT, UID)
        report_other = economy_accounting.income_expense_report(other_chat, UID)
        self.assertEqual(report_here["total_income"], 1000)
        self.assertEqual(report_other["total_income"], 0)


class AssetsLiabilitiesTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_assets_breakdown_matches_wallet_and_bank(self):
        arun(economy_core.add_coins(CHAT, UID, 1000, kind="REWARD"))
        arun(economy_core.deposit(CHAT, UID, 400))
        a = economy_accounting.assets_breakdown(CHAT, UID)
        self.assertEqual(a["wallet"], 600)
        self.assertEqual(a["bank"], 400)
        self.assertEqual(a["gross_net_worth"], 1000)

    def test_liabilities_include_business_debt_and_loans(self):
        _make_business(CHAT, UID, debt=1500)
        li = economy_accounting.liabilities_breakdown(CHAT, UID)
        self.assertEqual(li["business_debt"], 1500)
        self.assertEqual(li["total_liabilities"], 1500)

    def test_real_net_worth_subtracts_liabilities(self):
        arun(economy_core.add_coins(CHAT, UID, 10_000, kind="REWARD"))
        _make_business(CHAT, UID, debt=3000)
        s = economy_accounting.full_statement(CHAT, UID)
        self.assertEqual(s["real_net_worth"], s["assets"]["gross_net_worth"] - 3000)
        self.assertLess(s["real_net_worth"], s["assets"]["gross_net_worth"])


class BusinessPnlTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_pnl_reflects_lifetime_fields_exactly(self):
        _make_business(CHAT, UID, btype="shop", revenue=5000, expense=2000)
        _make_business(CHAT, UID, btype="cafe", revenue=1000, expense=1500)
        rows = economy_accounting.business_pnl(CHAT, UID)
        self.assertEqual(len(rows), 2)
        shop_row = next(r for r in rows if r["name"] == config.BUSINESS_TYPES["shop"]["name"])
        self.assertEqual(shop_row["lifetime_net"], 3000)
        cafe_row = next(r for r in rows if r["name"] == config.BUSINESS_TYPES["cafe"]["name"])
        self.assertEqual(cafe_row["lifetime_net"], -500)

    def test_bankrupt_flag_surfaced_in_pnl(self):
        _make_business(CHAT, UID, btype="shop", revenue=100, expense=200, bankrupt=True, debt=50)
        rows = economy_accounting.business_pnl(CHAT, UID)
        self.assertTrue(rows[0]["bankrupt"])
        self.assertEqual(rows[0]["debt"], 50)

    def test_no_businesses_gives_empty_list(self):
        self.assertEqual(economy_accounting.business_pnl(CHAT, UID), [])


class FormattersTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_statement_text_does_not_crash(self):
        arun(economy_core.add_coins(CHAT, UID, 1000, kind="work", note="کار — نجار"))
        text = economy_accounting.format_statement_text(CHAT, UID)
        self.assertIn("صورت‌حسابِ مالی", text)
        self.assertIn("ارزشِ خالصِ واقعی", text)

    def test_statement_text_with_period(self):
        text = economy_accounting.format_statement_text(CHAT, UID, period="ماهانه")
        self.assertIn("ماهانه", text)

    def test_business_pnl_text_does_not_crash(self):
        _make_business(CHAT, UID, revenue=100, expense=50)
        text = economy_accounting.format_business_pnl_text(CHAT, UID)
        self.assertIn("سود و زیانِ کسب‌وکارها", text)


if __name__ == "__main__":
    unittest.main()
