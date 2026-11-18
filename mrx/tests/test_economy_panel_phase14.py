# -*- coding: utf-8 -*-
"""
DIGNANTI V18 — تست‌های فاز ۱۴ (بخشِ۱۵: بازطراحیِ پنل)
================================================================
این فاز پرریسک‌ترینِ کلِ V18 بود (معماریِ Dispatcherِ پنل بازطراحی شد)،
پس این تست‌ها عمداً رویِ یه‌چیز تمرکز دارن: **هیچ دکمه/sec/act قدیمی از
دست نرفته و هیچ sec جدید بدونِ Renderer نمونده.**

اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_panel_phase14 -v
"""
from __future__ import annotations

import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
TELEGRAM_STUB_DIR = os.path.join(TESTS_DIR, "_telegram_stub")

sys.path.insert(0, TELEGRAM_STUB_DIR)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, TESTS_DIR)

import _stub_bot  # noqa: E402
sys.modules["bot"] = _stub_bot

import economy_panel  # noqa: E402

# هر (sec, act) ایی که قبل از بازطراحی در CATEGORIES موجود بود - اگه یکی از
# این‌ها دیگه هیچ‌جای CATEGORIES نباشه، یعنی یه قابلیت گم شده.
PRE_EXISTING_SEC_VALUES = {
    "jobs", "bank", "market", "stock", "auction", "trade", "shop", "clothing",
    "black", "prop", "vehicle", "business", "partnership", "city", "district",
    "housing", "infra", "treasury", "inv", "pet", "marriage", "games", "ug",
    "theft", "lb", "macro", "statement", "analysis",
}
PRE_EXISTING_ACT_VALUES = {"daily", "deal", "missions", "ach"}

NEW_SEC_VALUES = {"dashboard", "group_dashboard", "events", "commodities"}


def _all_items():
    items = []
    for cat in economy_panel.CATEGORIES.values():
        items.extend(cat["items"])
    return items


class NoFeatureLostTest(unittest.TestCase):
    def test_every_pre_existing_sec_still_present(self):
        present_secs = {val for _, kind, val in _all_items() if kind == "sec"}
        missing = PRE_EXISTING_SEC_VALUES - present_secs
        self.assertEqual(missing, set(), f"این بخش‌ها از منو گم شدن: {missing}")

    def test_every_pre_existing_act_still_present(self):
        present_acts = {val for _, kind, val in _all_items() if kind == "act"}
        missing = PRE_EXISTING_ACT_VALUES - present_acts
        self.assertEqual(missing, set(), f"این اکشن‌ها از منو گم شدن: {missing}")

    def test_new_sections_added(self):
        present_secs = {val for _, kind, val in _all_items() if kind == "sec"}
        missing = NEW_SEC_VALUES - present_secs
        self.assertEqual(missing, set(), f"این بخش‌هایِ تازه هنوز به منو اضافه نشدن: {missing}")


class StructuralIntegrityTest(unittest.TestCase):
    def test_category_order_matches_categories_keys(self):
        self.assertEqual(set(economy_panel.CATEGORY_ORDER), set(economy_panel.CATEGORIES.keys()))

    def test_every_sec_item_has_a_renderer(self):
        sec_values = {val for _, kind, val in _all_items() if kind == "sec"}
        missing = sec_values - set(economy_panel._SECTION_RENDERERS.keys())
        self.assertEqual(missing, set(), f"این بخش‌ها Renderer ندارن و باز نمی‌شن: {missing}")

    def test_legacy_aliases_point_to_real_categories(self):
        for old_key, new_key in economy_panel._LEGACY_CATEGORY_ALIASES.items():
            self.assertIn(new_key, economy_panel.CATEGORIES, f"{old_key} -> {new_key} (ناموجود)")

    def test_old_category_keys_resolve_via_alias_or_still_exist(self):
        old_keys = {"earn", "money", "shop", "assets", "life", "risk", "stats"}
        for old_key in old_keys:
            resolved = economy_panel._LEGACY_CATEGORY_ALIASES.get(old_key, old_key)
            self.assertIn(
                resolved, economy_panel.CATEGORIES,
                f"دکمه‌ی قدیمیِ ecop:cat:{old_key} به جایی resolve نمی‌شه",
            )

    def test_exactly_eighteen_bakhsh15_categories_plus_two_legacy(self):
        # ۱۸ دسته‌ی خواسته‌شده‌ی بخشِ۱۵ + ۲ دسته‌ی جانبی برایِ قابلیت‌هایی که
        # توی اون ۱۸تا جا نمی‌شدن (life, risk)
        self.assertEqual(len(economy_panel.CATEGORIES), 20)

    def test_no_category_has_empty_items(self):
        for key, cat in economy_panel.CATEGORIES.items():
            self.assertTrue(len(cat["items"]) > 0, f"دسته‌ی {key} هیچ آیتمی نداره")

    def test_every_category_has_title_and_desc(self):
        for key, cat in economy_panel.CATEGORIES.items():
            self.assertIn("title", cat)
            self.assertIn("desc", cat)
            self.assertTrue(cat["title"])
            self.assertTrue(cat["desc"])


class SectionCategoryMappingTest(unittest.TestCase):
    def test_section_category_covers_every_sec(self):
        sec_values = {val for _, kind, val in _all_items() if kind == "sec"}
        missing = sec_values - set(economy_panel.SECTION_CATEGORY.keys())
        self.assertEqual(missing, set(), f"این بخش‌ها دکمه‌ی بازگشتِ درست ندارن: {missing}")


class DashboardLinesTest(unittest.TestCase):
    def test_personal_dashboard_lines_returns_nonempty_list(self):
        lines = economy_panel._personal_dashboard_lines(99099, 1)
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)
        for line in lines:
            self.assertIsInstance(line, str)


if __name__ == "__main__":
    unittest.main()
