# -*- coding: utf-8 -*-
"""
DIGNANTI V18 — تست‌های فاز ۲ (Living Economy Engine): economy_supply + economy_macro
========================================================================================
اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_macro_supply -v

همون محدودیتِ Sandbox تست‌های آدیت رو داره (بدون python-telegram-bot واقعی) و
از همون استابِ bot (tests/_stub_bot.py) و استابِ telegram استفاده می‌کنه.
"""
from __future__ import annotations

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
import economy_macro  # noqa: E402
import economy_jobs  # noqa: E402

CHAT = 2002
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

    economy_macro._history.clear()
    economy_macro._last_snapshot_ts.clear()
    economy_macro._last_cumulative_gdp_total.clear()
    economy_macro._last_money_supply.clear()
    economy_macro._last_price_index.clear()

    economy_jobs._current_job.clear()

    journal_dir = tempfile.mkdtemp(prefix="dignanti_macro_test_journal_")
    economy_core.WALLET_JOURNAL_FILE = os.path.join(journal_dir, "wallet_journal.jsonl")


class SupplyEngineTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_unknown_commodity_raises(self):
        with self.assertRaises(economy_supply.UnknownCommodityError):
            economy_supply.get_price(CHAT, "not_a_real_commodity")

    def test_price_starts_at_base(self):
        base = config.COMMODITIES[COMMODITY]["base_price"]
        self.assertEqual(economy_supply.get_price(CHAT, COMMODITY), float(base))

    def test_demand_pushes_price_up(self):
        before = economy_supply.get_price(CHAT, COMMODITY)
        economy_supply.record_demand(CHAT, COMMODITY, qty=50)
        # یه Tick رو شبیه‌سازی کن (با عقب بردنِ last_tick_ts)
        economy_supply._last_tick_ts[CHAT] -= config.COMMODITY_TICK_INTERVAL_SECONDS * 2
        after = economy_supply.get_price(CHAT, COMMODITY)
        self.assertGreater(after, before)

    def test_supply_pushes_price_down_after_demand(self):
        economy_supply.record_demand(CHAT, COMMODITY, qty=80)
        economy_supply._last_tick_ts[CHAT] -= config.COMMODITY_TICK_INTERVAL_SECONDS * 2
        risen = economy_supply.get_price(CHAT, COMMODITY)

        economy_supply.record_supply(CHAT, COMMODITY, qty=200)
        economy_supply._last_tick_ts[CHAT] -= config.COMMODITY_TICK_INTERVAL_SECONDS * 2
        fallen = economy_supply.get_price(CHAT, COMMODITY)
        self.assertLess(fallen, risen)

    def test_price_never_exceeds_bounds(self):
        info = config.COMMODITIES[COMMODITY]
        for _ in range(500):
            economy_supply.record_demand(CHAT, COMMODITY, qty=1000)
            economy_supply._last_tick_ts[CHAT] -= config.COMMODITY_TICK_INTERVAL_SECONDS * 2
            price = economy_supply.get_price(CHAT, COMMODITY)
            self.assertLessEqual(price, float(info["max_price"]))
            self.assertGreaterEqual(price, float(info["min_price"]))

    def test_max_change_per_tick_is_bounded(self):
        economy_supply.record_demand(CHAT, COMMODITY, qty=10_000)
        before = economy_supply.get_price(CHAT, COMMODITY)
        economy_supply._last_tick_ts[CHAT] -= config.COMMODITY_TICK_INTERVAL_SECONDS  # دقیقاً یک Tick
        after = economy_supply.get_price(CHAT, COMMODITY)
        max_allowed = before * (1 + config.COMMODITY_MAX_CHANGE_PER_TICK) + 1e-6
        self.assertLessEqual(after, max_allowed)

    def test_shortage_detection(self):
        self.assertFalse(economy_supply.is_shortage(CHAT, COMMODITY))
        economy_supply.record_demand(CHAT, COMMODITY, qty=1000)
        self.assertTrue(economy_supply.is_shortage(CHAT, COMMODITY))

    def test_price_index_rises_with_demand(self):
        base_index = economy_supply.price_index(CHAT)
        self.assertAlmostEqual(base_index, 100.0, places=1)
        for key in config.COMMODITIES:
            economy_supply.record_demand(CHAT, key, qty=200)
        economy_supply._last_tick_ts[CHAT] -= config.COMMODITY_TICK_INTERVAL_SECONDS * 2
        after_index = economy_supply.price_index(CHAT)
        self.assertGreater(after_index, base_index)

    def test_groups_are_independent(self):
        other_chat = CHAT + 1
        economy_supply.record_demand(CHAT, COMMODITY, qty=500)
        economy_supply._last_tick_ts[CHAT] -= config.COMMODITY_TICK_INTERVAL_SECONDS * 2
        price_chat = economy_supply.get_price(CHAT, COMMODITY)
        price_other = economy_supply.get_price(other_chat, COMMODITY)
        self.assertNotEqual(price_chat, price_other)
        self.assertEqual(price_other, float(config.COMMODITIES[COMMODITY]["base_price"]))

    def test_save_load_round_trip(self):
        tmp_dir = tempfile.mkdtemp(prefix="dignanti_supply_state_")
        old_state_file = economy_supply.STATE_FILE
        economy_supply.STATE_FILE = os.path.join(tmp_dir, "supply_state.json")
        try:
            economy_supply.record_demand(CHAT, COMMODITY, qty=120)
            economy_supply._last_tick_ts[CHAT] -= config.COMMODITY_TICK_INTERVAL_SECONDS * 3
            price_before = economy_supply.get_price(CHAT, COMMODITY)

            economy_supply._write_state_file(economy_supply._collect_state())

            economy_supply._prices.clear()
            economy_supply._pressure.clear()
            economy_supply._history.clear()
            economy_supply._last_tick_ts.clear()
            economy_supply._total_demand.clear()
            economy_supply._total_supply.clear()

            economy_supply.load_state()
            price_after = round(economy_supply._prices[CHAT][COMMODITY], 2)
            self.assertEqual(price_before, price_after)
        finally:
            economy_supply.STATE_FILE = old_state_file


class MacroEngineTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    async def _add(self, uid, amount, kind):
        await economy_core.add_coins(CHAT, uid, amount, kind=kind)

    def test_money_supply_reflects_wallet_and_bank(self):
        asyncio_run(self._add(1, 1000, "REWARD"))
        asyncio_run(economy_core.deposit(CHAT, 1, 400))
        snap = economy_macro.force_snapshot(CHAT)
        self.assertEqual(snap["money_supply"], 1000.0)  # ۶۰۰ کیف‌پول + ۴۰۰ بانک

    def test_gdp_counts_only_real_production_types(self):
        asyncio_run(self._add(1, 500, "work"))        # باید توی GDP حساب بشه
        asyncio_run(self._add(1, 300, "BUSINESS"))    # باید توی GDP حساب بشه
        asyncio_run(self._add(1, 150, "REWARD"))      # جایزه‌ی صرف - نباید توی GDP حساب بشه
        asyncio_run(economy_core.transfer_coins(CHAT, 1, 2, 100))  # TRANSFER - نباید حساب بشه
        snap = economy_macro.force_snapshot(CHAT)
        self.assertEqual(snap["period_gdp"], 800.0)

    def test_gdp_growth_is_period_delta_not_cumulative(self):
        asyncio_run(self._add(1, 500, "work"))
        first = economy_macro.force_snapshot(CHAT)
        self.assertEqual(first["period_gdp"], 500.0)

        asyncio_run(self._add(1, 200, "work"))
        second = economy_macro.force_snapshot(CHAT)
        # فقط ۲۰۰ تای جدید باید توی دوره‌ی دوم حساب بشه، نه ۷۰۰ تجمعی
        self.assertEqual(second["period_gdp"], 200.0)

    def test_unemployment_rate(self):
        _stub_bot._known_members[CHAT] = {1, 2, 3, 4}
        economy_jobs._current_job[CHAT][1] = "nurse"
        economy_jobs._current_job[CHAT][2] = "engineer"
        snap = economy_macro.force_snapshot(CHAT)
        self.assertEqual(snap["population"], 4)
        self.assertEqual(snap["employed"], 2)
        self.assertAlmostEqual(snap["unemployment_rate"], 0.5, places=4)

    def test_no_population_gives_zero_unemployment_not_crash(self):
        snap = economy_macro.force_snapshot(CHAT)
        self.assertEqual(snap["population"], 0)
        self.assertEqual(snap["unemployment_rate"], 0.0)

    def test_maybe_snapshot_is_lazy(self):
        asyncio_run(self._add(1, 100, "JOB"))
        first = economy_macro.maybe_snapshot(CHAT)
        self.assertIsNotNone(first)
        # بلافاصله دوباره صدا بزن - باید None باشه (هنوز فاصله‌ی زمانی کافی نیست)
        second = economy_macro.maybe_snapshot(CHAT)
        self.assertIsNone(second)

    def test_inflation_reacts_to_commodity_price_index(self):
        economy_macro.force_snapshot(CHAT)  # baseline، price_index=100
        for key in config.COMMODITIES:
            economy_supply.record_demand(CHAT, key, qty=300)
        economy_supply._last_tick_ts[CHAT] -= config.COMMODITY_TICK_INTERVAL_SECONDS * 2
        snap = economy_macro.force_snapshot(CHAT)
        self.assertGreater(snap["inflation_rate"], 0.0)

    def test_groups_are_independent(self):
        other_chat = CHAT + 1
        asyncio_run(self._add(1, 1000, "work"))
        snap_chat = economy_macro.force_snapshot(CHAT)
        snap_other = economy_macro.force_snapshot(other_chat)
        self.assertEqual(snap_chat["period_gdp"], 1000.0)
        self.assertEqual(snap_other["period_gdp"], 0.0)

    def test_save_load_round_trip(self):
        tmp_dir = tempfile.mkdtemp(prefix="dignanti_macro_state_")
        old_state_file = economy_macro.STATE_FILE
        economy_macro.STATE_FILE = os.path.join(tmp_dir, "macro_state.json")
        try:
            asyncio_run(self._add(1, 777, "work"))
            snap = economy_macro.force_snapshot(CHAT)
            economy_macro._write_state_file(economy_macro._collect_state())

            economy_macro._history.clear()
            economy_macro._last_snapshot_ts.clear()
            economy_macro._last_cumulative_gdp_total.clear()
            economy_macro._last_money_supply.clear()
            economy_macro._last_price_index.clear()

            economy_macro.load_state()
            restored = economy_macro.get_history(CHAT, limit=5)
            self.assertEqual(len(restored), 1)
            self.assertEqual(restored[0]["period_gdp"], snap["period_gdp"])
        finally:
            economy_macro.STATE_FILE = old_state_file

    def test_dashboard_text_does_not_crash_and_mentions_status(self):
        asyncio_run(self._add(1, 500, "work"))
        text = economy_macro.format_dashboard_text(CHAT)
        self.assertIn("داشبورد اقتصاد کلان گروه", text)


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
