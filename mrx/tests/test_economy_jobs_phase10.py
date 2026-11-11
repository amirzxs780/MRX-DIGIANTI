# -*- coding: utf-8 -*-
"""
DIGNANTI V18 — تست‌های فاز ۱۰ (تکمیلِ بخشِ ۹): مشاغلِ تازه
================================================================
اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_jobs_phase10 -v
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

import config  # noqa: E402

NEW_JOBS = [
    "salesperson", "farmer", "teacher", "accountant", "architect",
    "tech_specialist", "doctor", "lawyer", "project_manager",
]

OLD_JOBS = [
    "worker", "chef", "driver", "employee", "designer", "programmer",
    "engineer", "trader", "banker", "scientist", "jeweler", "manager", "capitalist",
]


class NewJobsTest(unittest.TestCase):
    def test_all_new_jobs_exist_with_full_schema(self):
        required_fields = {
            "name", "emoji", "tier", "base_salary", "energy_cost",
            "fail_chance", "req_total_level", "req_reputation", "ability",
        }
        for key in NEW_JOBS:
            self.assertIn(key, config.JOBS)
            info = config.JOBS[key]
            missing = required_fields - set(info.keys())
            self.assertEqual(missing, set(), f"{key} fields missing: {missing}")

    def test_existing_jobs_untouched(self):
        for key in OLD_JOBS:
            self.assertIn(key, config.JOBS)

    def test_tiers_are_valid(self):
        valid_tiers = {"COMMON", "UNCOMMON", "RARE", "EPIC", "LEGENDARY"}
        for key in NEW_JOBS:
            self.assertIn(config.JOBS[key]["tier"], valid_tiers)

    def test_no_duplicate_job_names(self):
        names = [info["name"] for info in config.JOBS.values()]
        self.assertEqual(len(names), len(set(names)))

    def test_requirements_increase_with_tier(self):
        tier_order = {"COMMON": 0, "UNCOMMON": 1, "RARE": 2, "EPIC": 3, "LEGENDARY": 4}
        common_jobs = [info for info in config.JOBS.values() if info["tier"] == "COMMON"]
        legendary_jobs = [info for info in config.JOBS.values() if info["tier"] == "LEGENDARY"]
        max_common_level = max(j["req_total_level"] for j in common_jobs)
        min_legendary_level = min(j["req_total_level"] for j in legendary_jobs)
        self.assertLess(max_common_level, min_legendary_level)

    def test_fail_chance_within_sane_bounds(self):
        for key in NEW_JOBS:
            fc = config.JOBS[key]["fail_chance"]
            self.assertGreaterEqual(fc, 0.0)
            self.assertLessEqual(fc, 0.5)


if __name__ == "__main__":
    unittest.main()
