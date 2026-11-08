# -*- coding: utf-8 -*-
"""
DIGNANTI V18 — تست‌های فاز ۶ (بازارِ مسکنِ پویا): economy_housing
================================================================
اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_housing -v
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
import economy_housing  # noqa: E402

CHAT = 7007
DISTRICT = "regular"


def _reset_all():
    economy_housing._multipliers.clear()
    economy_housing._pressure.clear()
    economy_housing._history.clear()
    economy_housing._last_tick_ts.clear()


class HousingMarketTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_starts_at_neutral_multiplier(self):
        self.assertEqual(economy_housing.get_multiplier(CHAT, DISTRICT), 1.0)

    def test_unknown_district_returns_neutral(self):
        self.assertEqual(economy_housing.get_multiplier(CHAT, "not_a_real_district"), 1.0)

    def test_purchases_push_multiplier_up(self):
        before = economy_housing.get_multiplier(CHAT, DISTRICT)
        economy_housing.record_purchase(CHAT, DISTRICT, 30)
        economy_housing._last_tick_ts[CHAT] -= config.HOUSING_TICK_INTERVAL_SECONDS * 2
        after = economy_housing.get_multiplier(CHAT, DISTRICT)
        self.assertGreater(after, before)

    def test_sales_push_multiplier_down_after_purchases(self):
        economy_housing.record_purchase(CHAT, DISTRICT, 40)
        economy_housing._last_tick_ts[CHAT] -= config.HOUSING_TICK_INTERVAL_SECONDS * 2
        risen = economy_housing.get_multiplier(CHAT, DISTRICT)

        economy_housing.record_sale(CHAT, DISTRICT, 100)
        economy_housing._last_tick_ts[CHAT] -= config.HOUSING_TICK_INTERVAL_SECONDS * 2
        fallen = economy_housing.get_multiplier(CHAT, DISTRICT)
        self.assertLess(fallen, risen)

    def test_multiplier_never_exceeds_bounds(self):
        for _ in range(300):
            economy_housing.record_purchase(CHAT, DISTRICT, 500)
            economy_housing._last_tick_ts[CHAT] -= config.HOUSING_TICK_INTERVAL_SECONDS * 2
            mult = economy_housing.get_multiplier(CHAT, DISTRICT)
            self.assertLessEqual(mult, config.HOUSING_MAX_MULTIPLIER)
            self.assertGreaterEqual(mult, config.HOUSING_MIN_MULTIPLIER)

    def test_max_change_per_tick_is_bounded(self):
        economy_housing.record_purchase(CHAT, DISTRICT, 10_000)
        before = economy_housing.get_multiplier(CHAT, DISTRICT)
        economy_housing._last_tick_ts[CHAT] -= config.HOUSING_TICK_INTERVAL_SECONDS  # دقیقاً یک Tick
        after = economy_housing.get_multiplier(CHAT, DISTRICT)
        max_allowed = before * (1 + config.HOUSING_MAX_CHANGE_PER_TICK) + 1e-6
        self.assertLessEqual(after, max_allowed)

    def test_zone_classification_hot_vs_cold(self):
        hot_status = economy_housing.get_status(CHAT, DISTRICT)
        self.assertIn(hot_status["zone"], ("⚖️ عادی", "📈 در حالِ توسعه", "🔥 داغ", "🧊 کم‌تقاضا"))

        for _ in range(50):
            economy_housing.record_purchase(CHAT, DISTRICT, 200)
            economy_housing._last_tick_ts[CHAT] -= config.HOUSING_TICK_INTERVAL_SECONDS * 2
        status = economy_housing.get_status(CHAT, DISTRICT)
        self.assertEqual(status["zone"], "🔥 داغ")

    def test_districts_are_independent_within_a_group(self):
        economy_housing.record_purchase(CHAT, "vip", 200)
        economy_housing._last_tick_ts[CHAT] -= config.HOUSING_TICK_INTERVAL_SECONDS * 2
        vip_mult = economy_housing.get_multiplier(CHAT, "vip")
        poor_mult = economy_housing.get_multiplier(CHAT, "poor")
        self.assertGreater(vip_mult, poor_mult)

    def test_groups_are_independent(self):
        other_chat = CHAT + 1
        economy_housing.record_purchase(CHAT, DISTRICT, 200)
        economy_housing._last_tick_ts[CHAT] -= config.HOUSING_TICK_INTERVAL_SECONDS * 2
        self.assertGreater(economy_housing.get_multiplier(CHAT, DISTRICT), 1.0)
        self.assertEqual(economy_housing.get_multiplier(other_chat, DISTRICT), 1.0)

    def test_housing_price_index_starts_at_100(self):
        self.assertAlmostEqual(economy_housing.housing_price_index(CHAT), 100.0, places=1)

    def test_all_status_covers_every_district(self):
        statuses = economy_housing.all_status(CHAT)
        self.assertEqual(set(statuses.keys()), set(config.DISTRICTS.keys()))

    def test_save_load_round_trip(self):
        economy_housing.record_purchase(CHAT, DISTRICT, 50)
        economy_housing._last_tick_ts[CHAT] -= config.HOUSING_TICK_INTERVAL_SECONDS * 2
        mult_before = economy_housing.get_multiplier(CHAT, DISTRICT)

        tmp_dir = tempfile.mkdtemp(prefix="dignanti_housing_state_")
        old_state_file = economy_housing.STATE_FILE
        economy_housing.STATE_FILE = os.path.join(tmp_dir, "housing_state.json")
        try:
            economy_housing._write_state_file(economy_housing._collect_state())
            economy_housing._multipliers.clear()
            economy_housing._pressure.clear()
            economy_housing._history.clear()
            economy_housing._last_tick_ts.clear()
            economy_housing.load_state()
            mult_after = round(economy_housing._multipliers[CHAT][DISTRICT], 4)
            self.assertEqual(mult_before, mult_after)
        finally:
            economy_housing.STATE_FILE = old_state_file

    def test_format_text_does_not_crash(self):
        text = economy_housing.format_housing_text(CHAT)
        self.assertIn("بازارِ مسکن", text)


if __name__ == "__main__":
    unittest.main()
