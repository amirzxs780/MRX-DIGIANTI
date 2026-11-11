# -*- coding: utf-8 -*-
"""
DIGNANTI V18 — تست‌های فاز ۹ (قراردادهایِ شراکت): economy_partnership
================================================================
اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_partnership -v
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
import economy_partnership  # noqa: E402

CHAT = 11011
OWNER = 1
PARTNER = 2
PARTNER2 = 3


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

    economy_partnership._proposals.clear()
    economy_partnership._next_proposal_id.clear()

    journal_dir = tempfile.mkdtemp(prefix="dignanti_partner_test_journal_")
    economy_core.WALLET_JOURNAL_FILE = os.path.join(journal_dir, "wallet_journal.jsonl")


def _make_business(chat_id, uid, btype="shop", level=1, inventory=500):
    b = {
        "id": economy_business._new_id(chat_id, uid), "type": btype, "level": level,
        "employees": 0, "inventory": inventory, "price_mult": 1.0, "ad_until": None,
        "last_collect_ts": time.time(), "last_tax_ts": time.time(), "purchased_ts": time.time(),
        "lifetime_revenue": 0, "lifetime_expense": 0, "history": [],
        "debt": 0, "crisis_strikes": 0, "bankrupt": False, "partners": {},
    }
    economy_business.owned(chat_id, uid).append(b)
    return b


def arun(coro):
    return asyncio.run(coro)


class ProposalFlowTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_cannot_propose_to_self(self):
        b = _make_business(CHAT, OWNER)
        with self.assertRaises(economy_partnership.PartnershipError):
            economy_partnership.propose(CHAT, OWNER, b["id"], OWNER, 0.2)

    def test_propose_rejects_unowned_business(self):
        with self.assertRaises(economy_partnership.PartnershipError):
            economy_partnership.propose(CHAT, OWNER, 999, PARTNER, 0.2)

    def test_propose_rejects_invalid_equity(self):
        b = _make_business(CHAT, OWNER)
        with self.assertRaises(economy_partnership.PartnershipError):
            economy_partnership.propose(CHAT, OWNER, b["id"], PARTNER, 0.0)
        with self.assertRaises(economy_partnership.PartnershipError):
            economy_partnership.propose(CHAT, OWNER, b["id"], PARTNER, 1.0)

    def test_propose_rejects_equity_leaving_owner_below_minimum(self):
        b = _make_business(CHAT, OWNER)
        too_much = 1 - config.PARTNERSHIP_MIN_OWNER_EQUITY + 0.05
        with self.assertRaises(economy_partnership.PartnershipError):
            economy_partnership.propose(CHAT, OWNER, b["id"], PARTNER, too_much)

    def test_business_untouched_until_accepted(self):
        b = _make_business(CHAT, OWNER)
        economy_partnership.propose(CHAT, OWNER, b["id"], PARTNER, 0.3)
        self.assertEqual(b["partners"], {})  # هنوز هیچ تغییری روی خودِ کسب‌وکار نیست

    def test_accept_applies_equity_to_business(self):
        b = _make_business(CHAT, OWNER)
        p = economy_partnership.propose(CHAT, OWNER, b["id"], PARTNER, 0.3)
        economy_partnership.accept(CHAT, PARTNER, p["id"])
        self.assertEqual(b["partners"][str(PARTNER)], 0.3)

    def test_only_target_partner_can_accept(self):
        b = _make_business(CHAT, OWNER)
        p = economy_partnership.propose(CHAT, OWNER, b["id"], PARTNER, 0.3)
        with self.assertRaises(economy_partnership.PartnershipError):
            economy_partnership.accept(CHAT, PARTNER2, p["id"])

    def test_reject_leaves_business_untouched(self):
        b = _make_business(CHAT, OWNER)
        p = economy_partnership.propose(CHAT, OWNER, b["id"], PARTNER, 0.3)
        economy_partnership.reject(CHAT, PARTNER, p["id"])
        self.assertEqual(b["partners"], {})
        self.assertEqual(economy_partnership._proposals[CHAT][p["id"]]["status"], "rejected")

    def test_cannot_accept_twice(self):
        b = _make_business(CHAT, OWNER)
        p = economy_partnership.propose(CHAT, OWNER, b["id"], PARTNER, 0.3)
        economy_partnership.accept(CHAT, PARTNER, p["id"])
        with self.assertRaises(economy_partnership.PartnershipError):
            economy_partnership.accept(CHAT, PARTNER, p["id"])

    def test_owner_can_cancel_pending_proposal(self):
        b = _make_business(CHAT, OWNER)
        p = economy_partnership.propose(CHAT, OWNER, b["id"], PARTNER, 0.3)
        economy_partnership.cancel(CHAT, OWNER, p["id"])
        self.assertEqual(economy_partnership._proposals[CHAT][p["id"]]["status"], "cancelled")
        with self.assertRaises(economy_partnership.PartnershipError):
            economy_partnership.accept(CHAT, PARTNER, p["id"])

    def test_multiple_partners_respect_min_owner_equity(self):
        b = _make_business(CHAT, OWNER)
        p1 = economy_partnership.propose(CHAT, OWNER, b["id"], PARTNER, 0.4)
        economy_partnership.accept(CHAT, PARTNER, p1["id"])
        # الان مالک ۶۰٪ داره؛ پیشنهادِ ۵۵٪ به نفرِ دوم باید رد بشه چون مالک به زیرِ حداقل می‌افته
        with self.assertRaises(economy_partnership.PartnershipError):
            economy_partnership.propose(CHAT, OWNER, b["id"], PARTNER2, 0.55)

    def test_max_partners_per_business(self):
        b = _make_business(CHAT, OWNER)
        uid = 100
        equity_each = (1 - config.PARTNERSHIP_MIN_OWNER_EQUITY) / (config.PARTNERSHIP_MAX_PARTNERS_PER_BUSINESS + 1)
        for _ in range(config.PARTNERSHIP_MAX_PARTNERS_PER_BUSINESS):
            p = economy_partnership.propose(CHAT, OWNER, b["id"], uid, equity_each)
            economy_partnership.accept(CHAT, uid, p["id"])
            uid += 1
        with self.assertRaises(economy_partnership.PartnershipError):
            economy_partnership.propose(CHAT, OWNER, b["id"], uid, 0.01)


class ProfitSplitTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_no_partners_pays_owner_fully(self):
        b = _make_business(CHAT, OWNER, btype="shop", level=1)
        b["last_collect_ts"] = time.time() - 3600 * 5
        net, fired = arun(economy_business._collect_one(CHAT, OWNER, b))
        if net > 0:
            self.assertEqual(economy_core.get_wallet(CHAT, OWNER), net)
            self.assertEqual(economy_core.get_wallet(CHAT, PARTNER), 0)

    def test_partner_receives_exact_share_of_profit(self):
        b = _make_business(CHAT, OWNER, btype="shop", level=1)
        p = economy_partnership.propose(CHAT, OWNER, b["id"], PARTNER, 0.3)
        economy_partnership.accept(CHAT, PARTNER, p["id"])

        b["last_collect_ts"] = time.time() - 3600 * 10
        net, fired = arun(economy_business._collect_one(CHAT, OWNER, b))
        if net <= 0:
            self.skipTest("این Levelِ کسب‌وکار سودِ مثبت نداد؛ برایِ این تست مناسب نیست")

        expected_partner_share = round(net * 0.3)
        self.assertEqual(economy_core.get_wallet(CHAT, PARTNER), expected_partner_share)
        self.assertEqual(economy_core.get_wallet(CHAT, OWNER), net - expected_partner_share)

    def test_total_payout_never_exceeds_net_profit(self):
        b = _make_business(CHAT, OWNER, btype="shop", level=1)
        p1 = economy_partnership.propose(CHAT, OWNER, b["id"], PARTNER, 0.3)
        economy_partnership.accept(CHAT, PARTNER, p1["id"])
        p2 = economy_partnership.propose(CHAT, OWNER, b["id"], PARTNER2, 0.2)
        economy_partnership.accept(CHAT, PARTNER2, p2["id"])

        b["last_collect_ts"] = time.time() - 3600 * 10
        net, fired = arun(economy_business._collect_one(CHAT, OWNER, b))
        if net <= 0:
            self.skipTest("سودِ مثبتی تولید نشد")

        total_paid = (
            economy_core.get_wallet(CHAT, OWNER)
            + economy_core.get_wallet(CHAT, PARTNER)
            + economy_core.get_wallet(CHAT, PARTNER2)
        )
        self.assertEqual(total_paid, net)  # هیچ پولی از هوا اضافه/کم نشد


class FormattersAndMiscTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_pending_for_filters_correctly(self):
        b = _make_business(CHAT, OWNER)
        economy_partnership.propose(CHAT, OWNER, b["id"], PARTNER, 0.2)
        self.assertEqual(len(economy_partnership.pending_for(CHAT, PARTNER)), 1)
        self.assertEqual(len(economy_partnership.pending_for(CHAT, PARTNER2)), 0)

    def test_groups_are_independent(self):
        other_chat = CHAT + 1
        b = _make_business(CHAT, OWNER)
        economy_partnership.propose(CHAT, OWNER, b["id"], PARTNER, 0.2)
        self.assertEqual(len(economy_partnership.pending_for(CHAT, PARTNER)), 1)
        self.assertEqual(len(economy_partnership.pending_for(other_chat, PARTNER)), 0)

    def test_save_load_round_trip(self):
        b = _make_business(CHAT, OWNER)
        p = economy_partnership.propose(CHAT, OWNER, b["id"], PARTNER, 0.25)

        tmp_dir = tempfile.mkdtemp(prefix="dignanti_partner_state_")
        old_state_file = economy_partnership.STATE_FILE
        economy_partnership.STATE_FILE = os.path.join(tmp_dir, "partner_state.json")
        try:
            economy_partnership._write_state_file(economy_partnership._collect_state())
            economy_partnership._proposals.clear()
            economy_partnership._next_proposal_id.clear()
            economy_partnership.load_state()

            restored = economy_partnership._proposals[CHAT][p["id"]]
            self.assertEqual(restored["partner_uid"], PARTNER)
            self.assertEqual(restored["equity"], 0.25)
            self.assertEqual(restored["status"], "pending")
        finally:
            economy_partnership.STATE_FILE = old_state_file

    def test_format_text_does_not_crash(self):
        b = _make_business(CHAT, OWNER)
        economy_partnership.propose(CHAT, OWNER, b["id"], PARTNER, 0.2)
        text = economy_partnership.format_pending_text(CHAT, PARTNER)
        self.assertIn("پیشنهادهایِ شراکتِ من", text)


if __name__ == "__main__":
    unittest.main()
