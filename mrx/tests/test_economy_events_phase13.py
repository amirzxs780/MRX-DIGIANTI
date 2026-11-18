# -*- coding: utf-8 -*-
"""
DIGNANTI V18 — تست‌های فاز ۱۳ (تکمیلِ بخشِ ۱۴): رویدادهایِ اقتصادیِ تازه
================================================================
اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_events_phase13 -v
"""
from __future__ import annotations

import os
import sys
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
import economy_events  # noqa: E402
import economy_supply  # noqa: E402
import economy_housing  # noqa: E402
import economy_business  # noqa: E402

CHAT = 15015
NEW_EVENT_KEYS = [
    "economic_boom", "tourism_boom", "housing_price_boom",
    "commodity_price_drop", "raw_material_shortage", "cheap_transport",
]


def _reset_all():
    economy_events._active.clear()
    economy_supply._prices.clear()
    economy_supply._pressure.clear()
    economy_supply._history.clear()
    economy_supply._last_tick_ts.clear()
    economy_housing._multipliers.clear()
    economy_housing._pressure.clear()
    economy_housing._history.clear()
    economy_housing._last_tick_ts.clear()


def _activate(chat_id, key, minutes=None):
    duration = minutes if minutes is not None else config.ECONOMY_EVENTS[key]["duration_minutes"]
    economy_events._active[chat_id] = {"key": key, "ends_ts": time.time() + duration * 60}


class NewEventsSchemaTest(unittest.TestCase):
    def test_all_new_events_exist_with_full_schema(self):
        required = {"label", "effect", "value", "duration_minutes"}
        for key in NEW_EVENT_KEYS:
            self.assertIn(key, config.ECONOMY_EVENTS)
            info = config.ECONOMY_EVENTS[key]
            self.assertEqual(required - set(info.keys()), set())

    def test_existing_events_untouched(self):
        for key in ("recession", "fuel_crisis", "demand_surge"):
            self.assertIn(key, config.ECONOMY_EVENTS)


class CommodityPriceEventTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_no_event_leaves_price_unchanged(self):
        self.assertEqual(economy_events.commodity_price_multiplier(CHAT), 1.0)
        price = economy_supply.get_price(CHAT, "food")
        self.assertEqual(price, float(config.COMMODITIES["food"]["base_price"]))

    def test_commodity_price_drop_lowers_price(self):
        base = economy_supply.get_price(CHAT, "food")
        _activate(CHAT, "commodity_price_drop")
        dropped = economy_supply.get_price(CHAT, "food")
        self.assertLess(dropped, base)
        self.assertAlmostEqual(dropped, base * 0.8, places=2)

    def test_raw_material_shortage_raises_price(self):
        base = economy_supply.get_price(CHAT, "raw_material")
        _activate(CHAT, "raw_material_shortage")
        raised = economy_supply.get_price(CHAT, "raw_material")
        self.assertGreater(raised, base)

    def test_event_affects_status_and_price_index_consistently(self):
        base_index = economy_supply.price_index(CHAT)
        _activate(CHAT, "commodity_price_drop")
        dropped_index = economy_supply.price_index(CHAT)
        self.assertLess(dropped_index, base_index)

        status = economy_supply.get_status(CHAT, "food")
        # get_status باید همون قیمتِ تحتِ‌تأثیرِ رویداد رو نشون بده، نه قیمتِ خام
        self.assertAlmostEqual(status["price"], economy_supply.get_price(CHAT, "food"), places=2)

    def test_expired_event_no_longer_affects_price(self):
        _activate(CHAT, "commodity_price_drop", minutes=0)
        economy_events._active[CHAT]["ends_ts"] = time.time() - 1  # منقضی‌اش کن
        price = economy_supply.get_price(CHAT, "food")
        self.assertEqual(price, float(config.COMMODITIES["food"]["base_price"]))


class HousingPriceEventTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_no_event_leaves_multiplier_unchanged(self):
        self.assertEqual(economy_housing.get_multiplier(CHAT, "regular"), 1.0)

    def test_housing_boom_raises_multiplier(self):
        base = economy_housing.get_multiplier(CHAT, "regular")
        _activate(CHAT, "housing_price_boom")
        boosted = economy_housing.get_multiplier(CHAT, "regular")
        self.assertAlmostEqual(boosted, base * 1.3, places=3)

    def test_housing_price_index_reflects_event(self):
        base_index = economy_housing.housing_price_index(CHAT)
        _activate(CHAT, "housing_price_boom")
        boosted_index = economy_housing.housing_price_index(CHAT)
        self.assertGreater(boosted_index, base_index)


class TourismEventTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def _make_business(self, chat_id, uid, btype):
        return {
            "id": 1, "type": btype, "level": 1, "employees": 0, "inventory": 500,
            "price_mult": 1.0, "ad_until": None, "last_collect_ts": time.time(),
            "last_tax_ts": time.time(), "purchased_ts": time.time(),
            "lifetime_revenue": 0, "lifetime_expense": 0, "history": [],
            "debt": 0, "crisis_strikes": 0, "bankrupt": False, "partners": {}, "branches": 1,
        }

    def test_tourism_boom_boosts_hotel_revenue(self):
        hotel = self._make_business(CHAT, 1, "hotel")
        base_revenue = economy_business.hourly_revenue(CHAT, hotel)
        _activate(CHAT, "tourism_boom")
        boosted_revenue = economy_business.hourly_revenue(CHAT, hotel)
        self.assertAlmostEqual(boosted_revenue, base_revenue * 1.5, places=2)

    def test_tourism_boom_does_not_affect_unrelated_business(self):
        shop = self._make_business(CHAT, 1, "shop")
        base_revenue = economy_business.hourly_revenue(CHAT, shop)
        _activate(CHAT, "tourism_boom")
        after_revenue = economy_business.hourly_revenue(CHAT, shop)
        self.assertAlmostEqual(base_revenue, after_revenue, places=4)


class EconomicBoomAndTransportEventTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_economic_boom_reuses_income_multiplier_hook(self):
        self.assertEqual(economy_events.income_multiplier(CHAT), 1.0)
        _activate(CHAT, "economic_boom")
        self.assertEqual(economy_events.income_multiplier(CHAT), 1.35)

    def test_cheap_transport_reduces_fuel_cost_multiplier(self):
        self.assertEqual(economy_events.fuel_cost_multiplier(CHAT), 1.0)
        _activate(CHAT, "cheap_transport")
        self.assertEqual(economy_events.fuel_cost_multiplier(CHAT), 0.7)


if __name__ == "__main__":
    unittest.main()
