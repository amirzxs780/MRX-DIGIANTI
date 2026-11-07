# -*- coding: utf-8 -*-
"""
DIGNANTI V18 — تست‌های فاز ۳ (بازار سرمایه): economy_stock
================================================================
اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_stock -v
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
import economy_stock  # noqa: E402

CHAT = 3003
OWNER = 1
INVESTOR = 2
INVESTOR2 = 3


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

    economy_stock._companies.clear()
    economy_stock._next_company_id.clear()
    economy_stock._holdings.clear()
    economy_stock._history.clear()

    # register_net_worth_source تجمعی روی ماژوله - هر Import دوباره یه‌بار اضافه
    # می‌کنه؛ برای تست، به یه نمونه از هرکدوم محدودش می‌کنیم تا net worth دوبل نشه.
    seen = set()
    deduped = []
    for fn in economy_core._net_worth_sources:
        if fn not in seen:
            seen.add(fn)
            deduped.append(fn)
    economy_core._net_worth_sources[:] = deduped

    journal_dir = tempfile.mkdtemp(prefix="dignanti_stock_test_journal_")
    economy_core.WALLET_JOURNAL_FILE = os.path.join(journal_dir, "wallet_journal.jsonl")


def _make_business(chat_id, uid, btype="shop", level=1, inventory=0, employees=0):
    b = {
        "id": economy_business._new_id(chat_id, uid), "type": btype, "level": level,
        "employees": employees, "inventory": inventory, "price_mult": 1.0, "ad_until": None,
        "last_collect_ts": time.time(), "last_tax_ts": time.time(), "purchased_ts": time.time(),
        "lifetime_revenue": 0, "lifetime_expense": 0, "history": [],
    }
    economy_business.owned(chat_id, uid).append(b)
    return b


def arun(coro):
    return asyncio.run(coro)


class StockExchangeTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_ineligible_when_value_too_low(self):
        b = _make_business(CHAT, OWNER, btype="shop", level=1)  # value بالای ۵۰۰۰ هست، پس دستی چک کنیم
        # کسب‌وکارِ کوچیک بسازیم که مطمئناً زیر آستانه باشه: مقدار base_price shop=20000 خودش بالاست،
        # پس آستانه رو موقتاً بالا می‌بریم تا رد شدنش رو تست کنیم.
        old_min = config.STOCK_IPO_MIN_VALUE
        config.STOCK_IPO_MIN_VALUE = 10_000_000
        try:
            ok, reason = economy_stock.eligible_for_ipo(CHAT, OWNER, b["id"])
            self.assertFalse(ok)
            self.assertIn("ارزش", reason)
        finally:
            config.STOCK_IPO_MIN_VALUE = old_min

    def test_go_public_creates_company_and_pays_owner(self):
        b = _make_business(CHAT, OWNER, btype="shop", level=1)  # current_value = 20000
        company = arun(economy_stock.go_public(CHAT, OWNER, b["id"]))
        self.assertEqual(company["total_shares"], config.STOCK_TOTAL_SHARES)
        expected_price = 20000 / config.STOCK_TOTAL_SHARES
        self.assertAlmostEqual(company["share_price"], round(expected_price, 2), places=2)

        founder_frac = config.STOCK_FOUNDER_RETAIN_FRACTION
        expected_founder_shares = round(config.STOCK_TOTAL_SHARES * founder_frac)
        self.assertEqual(
            economy_stock._holdings[CHAT][company["id"]][OWNER], float(expected_founder_shares)
        )

        treasury = config.STOCK_TOTAL_SHARES - expected_founder_shares
        expected_raise = round(treasury * expected_price)
        self.assertEqual(economy_core.get_wallet(CHAT, OWNER), expected_raise)

    def test_cannot_ipo_twice(self):
        b = _make_business(CHAT, OWNER, btype="shop")
        arun(economy_stock.go_public(CHAT, OWNER, b["id"]))
        ok, reason = economy_stock.eligible_for_ipo(CHAT, OWNER, b["id"])
        self.assertFalse(ok)
        self.assertIn("قبل", reason)

    def test_buy_shares_reduces_treasury_and_costs_coins(self):
        b = _make_business(CHAT, OWNER, btype="shop")
        company = arun(economy_stock.go_public(CHAT, OWNER, b["id"]))
        cid = company["id"]
        arun(economy_core.add_coins(CHAT, INVESTOR, 100_000, kind="REWARD"))

        treasury_before = economy_stock._companies[CHAT][cid]["treasury_shares"]
        result = arun(economy_stock.buy_shares(CHAT, INVESTOR, cid, 10))
        self.assertEqual(result["qty"], 10)
        self.assertEqual(economy_stock._holdings[CHAT][cid][INVESTOR], 10.0)
        self.assertEqual(economy_stock._companies[CHAT][cid]["treasury_shares"], treasury_before - 10)

        fee_pct = config.STOCK_TRADING_FEE_PERCENT
        expected_cost = round(10 * result["price"] * (1 + fee_pct))
        self.assertEqual(result["cost"], expected_cost)

    def test_buy_more_than_treasury_fails(self):
        b = _make_business(CHAT, OWNER, btype="shop")
        company = arun(economy_stock.go_public(CHAT, OWNER, b["id"]))
        cid = company["id"]
        arun(economy_core.add_coins(CHAT, INVESTOR, 10_000_000, kind="REWARD"))
        too_many = economy_stock._companies[CHAT][cid]["treasury_shares"] + 1
        with self.assertRaises(economy_stock.StockError):
            arun(economy_stock.buy_shares(CHAT, INVESTOR, cid, too_many))

    def test_buying_pushes_price_up(self):
        b = _make_business(CHAT, OWNER, btype="shop")
        company = arun(economy_stock.go_public(CHAT, OWNER, b["id"]))
        cid = company["id"]
        arun(economy_core.add_coins(CHAT, INVESTOR, 1_000_000, kind="REWARD"))
        before = economy_stock.get_price(CHAT, cid)
        arun(economy_stock.buy_shares(CHAT, INVESTOR, cid, 50))
        after = economy_stock.get_price(CHAT, cid)
        self.assertGreater(after, before)

    def test_selling_pushes_price_down(self):
        b = _make_business(CHAT, OWNER, btype="shop")
        company = arun(economy_stock.go_public(CHAT, OWNER, b["id"]))
        cid = company["id"]
        arun(economy_core.add_coins(CHAT, INVESTOR, 1_000_000, kind="REWARD"))
        arun(economy_stock.buy_shares(CHAT, INVESTOR, cid, 50))
        risen = economy_stock.get_price(CHAT, cid)
        arun(economy_stock.sell_shares(CHAT, INVESTOR, cid, 50))
        fallen = economy_stock.get_price(CHAT, cid)
        self.assertLess(fallen, risen)

    def test_sell_more_than_held_fails(self):
        b = _make_business(CHAT, OWNER, btype="shop")
        company = arun(economy_stock.go_public(CHAT, OWNER, b["id"]))
        cid = company["id"]
        with self.assertRaises(economy_stock.StockError):
            arun(economy_stock.sell_shares(CHAT, INVESTOR, cid, 5))

    def test_reprice_moves_toward_fundamental_value(self):
        b = _make_business(CHAT, OWNER, btype="shop", level=1)
        company = arun(economy_stock.go_public(CHAT, OWNER, b["id"]))
        cid = company["id"]
        before = economy_stock._companies[CHAT][cid]["share_price"]

        # ارزشِ واقعیِ کسب‌وکار رو با ارتقای سطح بالا ببر (شبیه‌سازیِ سودآوریِ واقعی)
        b["level"] = 5
        economy_stock._companies[CHAT][cid]["last_reprice_ts"] -= config.STOCK_REPRICE_INTERVAL_SECONDS * 2

        after = economy_stock.get_price(CHAT, cid)
        self.assertGreater(after, before)

    def test_business_sold_auto_delists_company(self):
        b = _make_business(CHAT, OWNER, btype="shop")
        company = arun(economy_stock.go_public(CHAT, OWNER, b["id"]))
        cid = company["id"]
        # شبیه‌سازیِ فروش/حذفِ کسب‌وکار: از لیستِ owned حذفش کن
        economy_business.owned(CHAT, OWNER).remove(b)
        economy_stock._companies[CHAT][cid]["last_reprice_ts"] -= config.STOCK_REPRICE_INTERVAL_SECONDS * 2

        economy_stock.ensure_repriced(CHAT, cid)
        self.assertTrue(economy_stock._companies[CHAT][cid]["delisted"])
        self.assertNotIn(cid, [c["id"] for c in economy_stock.list_companies(CHAT)])

    def test_dividends_split_proportionally_and_respect_cooldown(self):
        b = _make_business(CHAT, OWNER, btype="shop")
        company = arun(economy_stock.go_public(CHAT, OWNER, b["id"]))
        cid = company["id"]
        arun(economy_core.add_coins(CHAT, INVESTOR, 1_000_000, kind="REWARD"))
        arun(economy_stock.buy_shares(CHAT, INVESTOR, cid, 100))
        arun(economy_core.add_coins(CHAT, OWNER, 100_000, kind="REWARD"))

        owner_wallet_before = economy_core.get_wallet(CHAT, OWNER)
        investor_wallet_before = economy_core.get_wallet(CHAT, INVESTOR)

        paid = arun(economy_stock.pay_dividends(CHAT, OWNER, cid, 10_000))
        self.assertIn(INVESTOR, paid)
        self.assertIn(OWNER, paid)

        outstanding = company["total_shares"] - economy_stock._companies[CHAT][cid]["treasury_shares"] + 100
        # (treasury کم شده چون ۱۰۰ سهم خریداری شده؛ outstanding واقعی رو از روی holdings بگیریم)
        owner_shares = economy_stock._holdings[CHAT][cid][OWNER]
        investor_shares = economy_stock._holdings[CHAT][cid][INVESTOR]
        real_outstanding = owner_shares + investor_shares
        expected_investor_cut = round(10_000 * (investor_shares / real_outstanding))
        self.assertEqual(paid[INVESTOR], expected_investor_cut)

        # Cooldown: بلافاصله دوباره امتحان کن - باید Error بده
        with self.assertRaises(economy_stock.StockError):
            arun(economy_stock.pay_dividends(CHAT, OWNER, cid, 1000))

    def test_only_owner_can_pay_dividends(self):
        b = _make_business(CHAT, OWNER, btype="shop")
        company = arun(economy_stock.go_public(CHAT, OWNER, b["id"]))
        with self.assertRaises(economy_stock.StockError):
            arun(economy_stock.pay_dividends(CHAT, INVESTOR, company["id"], 1000))

    def test_portfolio_value_and_top_investors(self):
        b = _make_business(CHAT, OWNER, btype="shop")
        company = arun(economy_stock.go_public(CHAT, OWNER, b["id"]))
        cid = company["id"]
        arun(economy_core.add_coins(CHAT, INVESTOR, 1_000_000, kind="REWARD"))
        arun(economy_core.add_coins(CHAT, INVESTOR2, 1_000_000, kind="REWARD"))
        arun(economy_stock.buy_shares(CHAT, INVESTOR, cid, 100))
        arun(economy_stock.buy_shares(CHAT, INVESTOR2, cid, 20))

        _stub_bot._known_members[CHAT] = {OWNER, INVESTOR, INVESTOR2}
        ranking = economy_stock.top_investors(CHAT, limit=5)
        ranked_uids = [uid for uid, _ in ranking]
        self.assertIn(OWNER, ranked_uids)
        self.assertIn(INVESTOR, ranked_uids)
        # مالک (با سهام بنیان‌گذاری) باید پرتفویِ بزرگ‌تری از سرمایه‌گذارِ ۱۰۰-سهمی داشته باشه
        self.assertGreater(economy_stock.portfolio_value(CHAT, OWNER), economy_stock.portfolio_value(CHAT, INVESTOR2))

    def test_market_index_starts_at_100_and_moves(self):
        b = _make_business(CHAT, OWNER, btype="shop")
        company = arun(economy_stock.go_public(CHAT, OWNER, b["id"]))
        cid = company["id"]
        self.assertAlmostEqual(economy_stock.market_index(CHAT), 100.0, places=1)
        arun(economy_core.add_coins(CHAT, INVESTOR, 1_000_000, kind="REWARD"))
        arun(economy_stock.buy_shares(CHAT, INVESTOR, cid, 80))
        self.assertGreater(economy_stock.market_index(CHAT), 100.0)

    def test_groups_are_independent(self):
        other_chat = CHAT + 1
        b1 = _make_business(CHAT, OWNER, btype="shop")
        arun(economy_stock.go_public(CHAT, OWNER, b1["id"]))
        self.assertEqual(len(economy_stock.list_companies(CHAT)), 1)
        self.assertEqual(len(economy_stock.list_companies(other_chat)), 0)

    def test_save_load_round_trip(self):
        b = _make_business(CHAT, OWNER, btype="shop")
        company = arun(economy_stock.go_public(CHAT, OWNER, b["id"]))
        cid = company["id"]
        arun(economy_core.add_coins(CHAT, INVESTOR, 1_000_000, kind="REWARD"))
        arun(economy_stock.buy_shares(CHAT, INVESTOR, cid, 15))

        tmp_dir = tempfile.mkdtemp(prefix="dignanti_stock_state_")
        old_state_file = economy_stock.STATE_FILE
        economy_stock.STATE_FILE = os.path.join(tmp_dir, "stock_state.json")
        try:
            economy_stock._write_state_file(economy_stock._collect_state())

            economy_stock._companies.clear()
            economy_stock._next_company_id.clear()
            economy_stock._holdings.clear()
            economy_stock._history.clear()

            economy_stock.load_state()
            restored = economy_stock._companies[CHAT][cid]
            self.assertEqual(restored["business_id"], b["id"])
            self.assertEqual(economy_stock._holdings[CHAT][cid][INVESTOR], 15.0)
        finally:
            economy_stock.STATE_FILE = old_state_file

    def test_exchange_and_portfolio_text_do_not_crash(self):
        b = _make_business(CHAT, OWNER, btype="shop")
        arun(economy_stock.go_public(CHAT, OWNER, b["id"]))
        text1 = economy_stock.format_exchange_text(CHAT)
        text2 = economy_stock.format_portfolio_text(CHAT, OWNER)
        self.assertIn("بورسِ سهامِ شرکت‌ها", text1)
        self.assertIn("پرتفویِ سهامِ من", text2)


if __name__ == "__main__":
    unittest.main()
