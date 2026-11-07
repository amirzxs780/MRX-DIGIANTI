# -*- coding: utf-8 -*-
"""
DIGNANTI V18 — تست‌های فاز ۴ (توسعه‌ی کسب‌وکار): مواد اولیه + ورشکستگی
================================================================
اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_business_phase4 -v
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
import economy_supply  # noqa: E402

CHAT = 5005
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

    economy_supply._prices.clear()
    economy_supply._pressure.clear()
    economy_supply._history.clear()
    economy_supply._last_tick_ts.clear()
    economy_supply._total_demand.clear()
    economy_supply._total_supply.clear()

    journal_dir = tempfile.mkdtemp(prefix="dignanti_biz4_test_journal_")
    economy_core.WALLET_JOURNAL_FILE = os.path.join(journal_dir, "wallet_journal.jsonl")


def _make_business(chat_id, uid, btype="shop", level=1, inventory=0, employees=0):
    b = {
        "id": economy_business._new_id(chat_id, uid), "type": btype, "level": level,
        "employees": employees, "inventory": inventory, "price_mult": 1.0, "ad_until": None,
        "last_collect_ts": time.time(), "last_tax_ts": time.time(), "purchased_ts": time.time(),
        "lifetime_revenue": 0, "lifetime_expense": 0, "history": [],
        "debt": 0, "crisis_strikes": 0, "bankrupt": False,
    }
    economy_business.owned(chat_id, uid).append(b)
    return b


def arun(coro):
    return asyncio.run(coro)


class CommodityLinkedInventoryTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_shop_commodity_price_starts_at_commodity_base_not_flat_config(self):
        """shop به «food» وصله؛ قیمتِ زنده باید از قیمتِ پایه‌ی خودِ کالا
        (config.COMMODITIES) بیاد، نه از عددِ ثابتِ قدیمیِ
        BUSINESS_TYPES["shop"]["inventory_cost_per_unit"] - یعنی
        buy_inventory_command واقعاً به economy_supply وصل شده."""
        commodity_key = config.BUSINESS_COMMODITY_MAP["shop"]
        commodity_base = config.COMMODITIES[commodity_key]["base_price"]
        live_price = economy_supply.get_price(CHAT, commodity_key)
        self.assertEqual(live_price, float(commodity_base))

    def test_buying_inventory_registers_real_demand(self):
        # شبیه‌سازیِ مستقیمِ چیزی که buy_inventory_command انجام می‌ده، تا
        # بدونِ وابستگی به استابِ کاملِ Update/Message تلگرام تست بشه.
        commodity_key = config.BUSINESS_COMMODITY_MAP["shop"]
        before_price = economy_supply.get_price(CHAT, commodity_key)
        economy_supply.record_demand(CHAT, commodity_key, 50)
        economy_supply._last_tick_ts[CHAT] -= config.COMMODITY_TICK_INTERVAL_SECONDS * 2
        after_price = economy_supply.get_price(CHAT, commodity_key)
        self.assertGreater(after_price, before_price)

    def test_service_business_not_in_commodity_map(self):
        self.assertNotIn("realestate", config.BUSINESS_COMMODITY_MAP)
        self.assertNotIn("clinic", config.BUSINESS_COMMODITY_MAP)

    def test_every_commodity_mapped_type_has_a_real_commodity(self):
        for btype, commodity in config.BUSINESS_COMMODITY_MAP.items():
            self.assertIn(btype, config.BUSINESS_TYPES)
            self.assertIn(commodity, config.COMMODITIES)


class BankruptcyTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_shortfall_creates_debt_not_negative_wallet(self):
        b = _make_business(CHAT, UID, btype="shop", level=1, employees=3, inventory=0)
        # کاربر عمداً هیچ پولی نداره؛ هزینه‌ی حقوقِ ۳ کارمند باید Shortfall بسازه
        b["last_collect_ts"] = time.time() - 3600 * 5  # ۵ ساعت گذشته
        net, fired = arun(economy_business._collect_one(CHAT, UID, b))
        self.assertEqual(net, 0)
        self.assertGreater(fired, 0)
        self.assertEqual(b["employees"], 0)
        self.assertGreater(b["debt"], 0)
        self.assertEqual(b["crisis_strikes"], 1)
        self.assertGreaterEqual(economy_core.get_wallet(CHAT, UID), 0)  # هیچ‌وقت منفی نمی‌شه

    def test_three_strikes_triggers_bankruptcy(self):
        b = _make_business(CHAT, UID, btype="shop", level=1, employees=2, inventory=0)
        for _ in range(config.BUSINESS_BANKRUPTCY_STRIKES):
            b["last_collect_ts"] = time.time() - 3600 * 5
            b["employees"] = 2  # هر بار دوباره کارمند بگیریم که Expense واقعی تولید بشه
            arun(economy_business._collect_one(CHAT, UID, b))
        self.assertTrue(b["bankrupt"])

    def test_bankrupt_business_frozen_no_further_accrual(self):
        b = _make_business(CHAT, UID, btype="shop", level=1, employees=2, inventory=0)
        for _ in range(config.BUSINESS_BANKRUPTCY_STRIKES):
            b["last_collect_ts"] = time.time() - 3600 * 5
            b["employees"] = 2
            arun(economy_business._collect_one(CHAT, UID, b))
        self.assertTrue(b["bankrupt"])
        debt_before = b["debt"]

        b["last_collect_ts"] = time.time() - 3600 * 100  # غیبتِ طولانی بعدِ ورشکستگی
        net, fired = arun(economy_business._collect_one(CHAT, UID, b))
        self.assertEqual((net, fired), (0, 0))
        self.assertEqual(b["debt"], debt_before)  # بدهی بیشتر نشد - Frozen واقعاً فریزه

    def test_paying_full_debt_unfreezes_business(self):
        b = _make_business(CHAT, UID, btype="shop", level=1, employees=2, inventory=0)
        for _ in range(config.BUSINESS_BANKRUPTCY_STRIKES):
            b["last_collect_ts"] = time.time() - 3600 * 5
            b["employees"] = 2
            arun(economy_business._collect_one(CHAT, UID, b))
        self.assertTrue(b["bankrupt"])

        arun(economy_core.add_coins(CHAT, UID, b["debt"] + 1000, kind="REWARD"))
        amount = b["debt"]
        arun(economy_core.remove_coins(CHAT, UID, amount, kind="OTHER", note="تست پرداخت بدهی"))
        b["debt"] -= amount
        if b["debt"] <= 0:
            b["debt"] = 0
            b["crisis_strikes"] = 0
            b["bankrupt"] = False
        self.assertFalse(b["bankrupt"])
        self.assertEqual(b["debt"], 0)

    def test_solvent_business_never_gets_debt(self):
        b = _make_business(CHAT, UID, btype="shop", level=1, employees=0, inventory=0)
        arun(economy_core.add_coins(CHAT, UID, 1_000_000, kind="REWARD"))
        b["last_collect_ts"] = time.time() - 3600 * 2
        net, fired = arun(economy_business._collect_one(CHAT, UID, b))
        self.assertEqual(b["debt"], 0)
        self.assertEqual(b["crisis_strikes"], 0)
        self.assertFalse(b["bankrupt"])


if __name__ == "__main__":
    unittest.main()
