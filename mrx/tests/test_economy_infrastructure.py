# -*- coding: utf-8 -*-
"""
DIGNANTI V18 — تست‌های فاز ۶ (زیرساختِ شهریِ گروه): economy_infrastructure
================================================================
اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_infrastructure -v
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
import economy_infrastructure  # noqa: E402

CHAT = 8008
UID = 1
UID2 = 2


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

    economy_infrastructure._infra.clear()

    journal_dir = tempfile.mkdtemp(prefix="dignanti_infra_test_journal_")
    economy_core.WALLET_JOURNAL_FILE = os.path.join(journal_dir, "wallet_journal.jsonl")


def arun(coro):
    return asyncio.run(coro)


class InfrastructureTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_starts_at_level_zero(self):
        rows = economy_infrastructure.list_all(CHAT)
        self.assertTrue(all(r["level"] == 0 for r in rows))
        self.assertEqual(len(rows), len(config.INFRASTRUCTURE_TYPES))

    def test_contribute_rejects_unknown_type(self):
        arun(economy_core.add_coins(CHAT, UID, 100_000, kind="REWARD"))
        with self.assertRaises(economy_infrastructure.InfrastructureError):
            arun(economy_infrastructure.contribute(CHAT, UID, "not_a_real_type", 1000))

    def test_contribute_requires_funds(self):
        with self.assertRaises(economy_core.InsufficientFundsError):
            arun(economy_infrastructure.contribute(CHAT, UID, "roads", 1000))

    def test_contribution_is_a_real_sink(self):
        arun(economy_core.add_coins(CHAT, UID, 5000, kind="REWARD"))
        arun(economy_infrastructure.contribute(CHAT, UID, "roads", 2000))
        self.assertEqual(economy_core.get_wallet(CHAT, UID), 3000)

    def test_progress_accumulates_without_leveling_up_prematurely(self):
        cost = config.INFRASTRUCTURE_TYPES["roads"]["base_cost"]
        arun(economy_core.add_coins(CHAT, UID, cost, kind="REWARD"))
        result = arun(economy_infrastructure.contribute(CHAT, UID, "roads", round(cost * 0.3)))
        self.assertFalse(result["leveled_up"])
        self.assertEqual(result["level"], 0)

    def test_reaching_full_cost_levels_up(self):
        cost = config.INFRASTRUCTURE_TYPES["roads"]["base_cost"]
        arun(economy_core.add_coins(CHAT, UID, cost, kind="REWARD"))
        result = arun(economy_infrastructure.contribute(CHAT, UID, "roads", cost))
        self.assertTrue(result["leveled_up"])
        self.assertEqual(result["level"], 1)

    def test_crowd_funding_across_multiple_users(self):
        cost = config.INFRASTRUCTURE_TYPES["school"]["base_cost"]
        half = round(cost / 2)
        arun(economy_core.add_coins(CHAT, UID, half, kind="REWARD"))
        arun(economy_core.add_coins(CHAT, UID2, cost, kind="REWARD"))

        r1 = arun(economy_infrastructure.contribute(CHAT, UID, "school", half))
        self.assertFalse(r1["leveled_up"])
        r2 = arun(economy_infrastructure.contribute(CHAT, UID2, "school", cost - half))
        self.assertTrue(r2["leveled_up"])
        self.assertEqual(r2["level"], 1)

    def test_next_level_cost_grows(self):
        key = "hospital"
        info = config.INFRASTRUCTURE_TYPES[key]
        cost_lv0 = economy_infrastructure.next_level_cost(key, 0)
        cost_lv1 = economy_infrastructure.next_level_cost(key, 1)
        self.assertEqual(cost_lv0, info["base_cost"])
        self.assertGreater(cost_lv1, cost_lv0)

    def test_cannot_exceed_max_level(self):
        key = "water_system"
        max_level = config.INFRASTRUCTURE_MAX_LEVEL
        e = economy_infrastructure._entry(CHAT, key)
        e["level"] = max_level
        arun(economy_core.add_coins(CHAT, UID, 1_000_000, kind="REWARD"))
        with self.assertRaises(economy_infrastructure.InfrastructureError):
            arun(economy_infrastructure.contribute(CHAT, UID, key, 1000))

    def test_quality_index_increases_with_levels(self):
        before = economy_infrastructure.quality_index(CHAT)
        for key, info in config.INFRASTRUCTURE_TYPES.items():
            e = economy_infrastructure._entry(CHAT, key)
            e["level"] = 5
        after = economy_infrastructure.quality_index(CHAT)
        self.assertGreater(after, before)
        self.assertLessEqual(after, 100.0)

    def test_estimated_population_grows_with_members_and_quality(self):
        _stub_bot._known_members[CHAT] = {1, 2, 3}
        base_pop = economy_infrastructure.estimated_population(CHAT)
        for key in config.INFRASTRUCTURE_TYPES:
            economy_infrastructure._entry(CHAT, key)["level"] = 10
        boosted_pop = economy_infrastructure.estimated_population(CHAT)
        self.assertGreater(boosted_pop, base_pop)

    def test_groups_are_independent(self):
        other_chat = CHAT + 1
        arun(economy_core.add_coins(CHAT, UID, 100_000, kind="REWARD"))
        arun(economy_infrastructure.contribute(CHAT, UID, "roads", 5000))
        self.assertGreater(economy_infrastructure._entry(CHAT, "roads")["total_invested"], 0)
        self.assertEqual(economy_infrastructure._entry(other_chat, "roads")["total_invested"], 0)

    def test_save_load_round_trip(self):
        arun(economy_core.add_coins(CHAT, UID, 100_000, kind="REWARD"))
        arun(economy_infrastructure.contribute(CHAT, UID, "roads", 5000))

        tmp_dir = tempfile.mkdtemp(prefix="dignanti_infra_state_")
        old_state_file = economy_infrastructure.STATE_FILE
        economy_infrastructure.STATE_FILE = os.path.join(tmp_dir, "infra_state.json")
        try:
            economy_infrastructure._write_state_file(economy_infrastructure._collect_state())
            economy_infrastructure._infra.clear()
            economy_infrastructure.load_state()
            restored = economy_infrastructure._entry(CHAT, "roads")
            self.assertEqual(restored["total_invested"], 5000)
            self.assertEqual(restored["contributors"][str(UID)], 5000)
        finally:
            economy_infrastructure.STATE_FILE = old_state_file

    def test_format_text_does_not_crash(self):
        text = economy_infrastructure.format_infrastructure_text(CHAT)
        self.assertIn("زیرساختِ شهریِ گروه", text)


if __name__ == "__main__":
    unittest.main()
