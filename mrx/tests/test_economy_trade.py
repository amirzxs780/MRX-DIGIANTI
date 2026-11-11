# -*- coding: utf-8 -*-
"""
DIGNANTI V18 — تست‌های فاز ۸ (تجارت، واردات و صادرات): economy_trade
================================================================
اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_trade -v
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
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
import economy_supply  # noqa: E402
import economy_trade  # noqa: E402

CHAT = 10010
SELLER = 1
BUYER = 2
COMMODITY = "food"


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

    economy_supply._prices.clear()
    economy_supply._pressure.clear()
    economy_supply._history.clear()
    economy_supply._last_tick_ts.clear()
    economy_supply._total_demand.clear()
    economy_supply._total_supply.clear()

    economy_trade._holdings.clear()
    economy_trade._contracts.clear()
    economy_trade._next_contract_id.clear()

    journal_dir = tempfile.mkdtemp(prefix="dignanti_trade_test_journal_")
    economy_core.WALLET_JOURNAL_FILE = os.path.join(journal_dir, "wallet_journal.jsonl")


def arun(coro):
    return asyncio.run(coro)


class BulkTradeTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_bulk_buy_below_minimum_rejected(self):
        arun(economy_core.add_coins(CHAT, SELLER, 100_000, kind="REWARD"))
        with self.assertRaises(economy_trade.TradeError):
            arun(economy_trade.bulk_buy(CHAT, SELLER, COMMODITY, 1))  # زیرِ حداقل

    def test_bulk_buy_charges_price_plus_fee(self):
        arun(economy_core.add_coins(CHAT, SELLER, 100_000, kind="REWARD"))
        price = economy_supply.get_price(CHAT, COMMODITY)
        qty = 20
        result = arun(economy_trade.bulk_buy(CHAT, SELLER, COMMODITY, qty))
        expected_cost = round(qty * price * (1 + config.TRADE_BULK_FEE_PERCENT))
        self.assertEqual(result["cost"], expected_cost)
        self.assertEqual(economy_trade.get_holding(CHAT, SELLER, COMMODITY), qty)

    def test_bulk_buy_registers_real_demand(self):
        arun(economy_core.add_coins(CHAT, SELLER, 1_000_000, kind="REWARD"))
        before = economy_supply.get_price(CHAT, COMMODITY)
        arun(economy_trade.bulk_buy(CHAT, SELLER, COMMODITY, 200))
        economy_supply._last_tick_ts[CHAT] -= config.COMMODITY_TICK_INTERVAL_SECONDS * 2
        after = economy_supply.get_price(CHAT, COMMODITY)
        self.assertGreater(after, before)

    def test_bulk_sell_requires_holdings(self):
        with self.assertRaises(economy_trade.TradeError):
            arun(economy_trade.bulk_sell(CHAT, SELLER, COMMODITY, 20))

    def test_bulk_sell_pays_out_minus_fee(self):
        arun(economy_core.add_coins(CHAT, SELLER, 1_000_000, kind="REWARD"))
        arun(economy_trade.bulk_buy(CHAT, SELLER, COMMODITY, 50))
        wallet_before = economy_core.get_wallet(CHAT, SELLER)

        price = economy_supply.get_price(CHAT, COMMODITY)
        result = arun(economy_trade.bulk_sell(CHAT, SELLER, COMMODITY, 50))
        expected_proceeds = round(50 * price * (1 - config.TRADE_BULK_FEE_PERCENT))
        self.assertEqual(result["proceeds"], expected_proceeds)
        self.assertEqual(economy_core.get_wallet(CHAT, SELLER), wallet_before + expected_proceeds)
        self.assertEqual(economy_trade.get_holding(CHAT, SELLER, COMMODITY), 0)

    def test_unknown_commodity_rejected(self):
        arun(economy_core.add_coins(CHAT, SELLER, 100_000, kind="REWARD"))
        with self.assertRaises(economy_trade.TradeError):
            arun(economy_trade.bulk_buy(CHAT, SELLER, "not_a_real_commodity", 20))


class SupplyContractTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_offer_requires_sufficient_holdings(self):
        with self.assertRaises(economy_trade.TradeError):
            economy_trade.offer_contract(CHAT, SELLER, BUYER, COMMODITY, 20, 100)

    def test_offer_escrows_commodity_from_seller(self):
        arun(economy_core.add_coins(CHAT, SELLER, 1_000_000, kind="REWARD"))
        arun(economy_trade.bulk_buy(CHAT, SELLER, COMMODITY, 50))
        held_before = economy_trade.get_holding(CHAT, SELLER, COMMODITY)

        economy_trade.offer_contract(CHAT, SELLER, BUYER, COMMODITY, 30, 100)
        held_after = economy_trade.get_holding(CHAT, SELLER, COMMODITY)
        self.assertEqual(held_after, held_before - 30)

    def test_cannot_offer_to_self(self):
        arun(economy_core.add_coins(CHAT, SELLER, 1_000_000, kind="REWARD"))
        arun(economy_trade.bulk_buy(CHAT, SELLER, COMMODITY, 50))
        with self.assertRaises(economy_trade.TradeError):
            economy_trade.offer_contract(CHAT, SELLER, SELLER, COMMODITY, 10, 100)

    def test_accept_transfers_money_and_goods(self):
        arun(economy_core.add_coins(CHAT, SELLER, 1_000_000, kind="REWARD"))
        arun(economy_trade.bulk_buy(CHAT, SELLER, COMMODITY, 50))
        c = economy_trade.offer_contract(CHAT, SELLER, BUYER, COMMODITY, 20, 150)

        arun(economy_core.add_coins(CHAT, BUYER, 10_000, kind="REWARD"))
        seller_wallet_before = economy_core.get_wallet(CHAT, SELLER)

        result = arun(economy_trade.accept_contract(CHAT, BUYER, c["id"]))
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(economy_core.get_wallet(CHAT, BUYER), 10_000 - 20 * 150)
        self.assertEqual(economy_core.get_wallet(CHAT, SELLER), seller_wallet_before + 20 * 150)
        self.assertEqual(economy_trade.get_holding(CHAT, BUYER, COMMODITY), 20)

    def test_wrong_buyer_cannot_accept(self):
        arun(economy_core.add_coins(CHAT, SELLER, 1_000_000, kind="REWARD"))
        arun(economy_trade.bulk_buy(CHAT, SELLER, COMMODITY, 50))
        c = economy_trade.offer_contract(CHAT, SELLER, BUYER, COMMODITY, 20, 150)

        intruder = 999
        arun(economy_core.add_coins(CHAT, intruder, 100_000, kind="REWARD"))
        with self.assertRaises(economy_trade.TradeError):
            arun(economy_trade.accept_contract(CHAT, intruder, c["id"]))

    def test_accept_fails_without_enough_funds_and_contract_stays_offered(self):
        arun(economy_core.add_coins(CHAT, SELLER, 1_000_000, kind="REWARD"))
        arun(economy_trade.bulk_buy(CHAT, SELLER, COMMODITY, 50))
        c = economy_trade.offer_contract(CHAT, SELLER, BUYER, COMMODITY, 20, 150)
        # BUYER بدونِ پول
        with self.assertRaises(economy_core.InsufficientFundsError):
            arun(economy_trade.accept_contract(CHAT, BUYER, c["id"]))
        self.assertEqual(economy_trade._contracts[CHAT][c["id"]]["status"], "offered")

    def test_cancel_returns_commodity_to_seller(self):
        arun(economy_core.add_coins(CHAT, SELLER, 1_000_000, kind="REWARD"))
        arun(economy_trade.bulk_buy(CHAT, SELLER, COMMODITY, 50))
        c = economy_trade.offer_contract(CHAT, SELLER, BUYER, COMMODITY, 20, 150)
        held_before_cancel = economy_trade.get_holding(CHAT, SELLER, COMMODITY)

        economy_trade.cancel_contract(CHAT, SELLER, c["id"])
        self.assertEqual(economy_trade.get_holding(CHAT, SELLER, COMMODITY), held_before_cancel + 20)
        self.assertEqual(economy_trade._contracts[CHAT][c["id"]]["status"], "cancelled")

    def test_only_seller_can_cancel(self):
        arun(economy_core.add_coins(CHAT, SELLER, 1_000_000, kind="REWARD"))
        arun(economy_trade.bulk_buy(CHAT, SELLER, COMMODITY, 50))
        c = economy_trade.offer_contract(CHAT, SELLER, BUYER, COMMODITY, 20, 150)
        with self.assertRaises(economy_trade.TradeError):
            economy_trade.cancel_contract(CHAT, BUYER, c["id"])

    def test_max_active_offers_per_seller(self):
        arun(economy_core.add_coins(CHAT, SELLER, 1_000_000, kind="REWARD"))
        arun(economy_trade.bulk_buy(CHAT, SELLER, COMMODITY, 500))
        for _ in range(config.TRADE_CONTRACT_MAX_ACTIVE):
            economy_trade.offer_contract(CHAT, SELLER, BUYER, COMMODITY, 10, 100)
        with self.assertRaises(economy_trade.TradeError):
            economy_trade.offer_contract(CHAT, SELLER, BUYER, COMMODITY, 10, 100)

    def test_groups_are_independent(self):
        other_chat = CHAT + 1
        arun(economy_core.add_coins(CHAT, SELLER, 1_000_000, kind="REWARD"))
        arun(economy_trade.bulk_buy(CHAT, SELLER, COMMODITY, 50))
        self.assertGreater(economy_trade.get_holding(CHAT, SELLER, COMMODITY), 0)
        self.assertEqual(economy_trade.get_holding(other_chat, SELLER, COMMODITY), 0)

    def test_save_load_round_trip(self):
        arun(economy_core.add_coins(CHAT, SELLER, 1_000_000, kind="REWARD"))
        arun(economy_trade.bulk_buy(CHAT, SELLER, COMMODITY, 50))
        c = economy_trade.offer_contract(CHAT, SELLER, BUYER, COMMODITY, 20, 150)

        tmp_dir = tempfile.mkdtemp(prefix="dignanti_trade_state_")
        old_state_file = economy_trade.STATE_FILE
        economy_trade.STATE_FILE = os.path.join(tmp_dir, "trade_state.json")
        try:
            economy_trade._write_state_file(economy_trade._collect_state())
            economy_trade._holdings.clear()
            economy_trade._contracts.clear()
            economy_trade._next_contract_id.clear()
            economy_trade.load_state()

            self.assertEqual(economy_trade.get_holding(CHAT, SELLER, COMMODITY), 30)
            restored = economy_trade._contracts[CHAT][c["id"]]
            self.assertEqual(restored["buyer_uid"], BUYER)
            self.assertEqual(restored["status"], "offered")
        finally:
            economy_trade.STATE_FILE = old_state_file

    def test_text_formatters_do_not_crash(self):
        arun(economy_core.add_coins(CHAT, SELLER, 1_000_000, kind="REWARD"))
        arun(economy_trade.bulk_buy(CHAT, SELLER, COMMODITY, 50))
        economy_trade.offer_contract(CHAT, SELLER, BUYER, COMMODITY, 20, 150)
        text1 = economy_trade.format_holdings_text(CHAT, SELLER)
        text2 = economy_trade.format_offers_text(CHAT, BUYER)
        self.assertIn("موجودیِ کالاهای من", text1)
        self.assertIn("پیشنهادهایِ قراردادی", text2)


if __name__ == "__main__":
    unittest.main()
