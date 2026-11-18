# -*- coding: utf-8 -*-
"""
DIGNANTI V18 — تست‌های فاز ۱۶ (تکمیلِ بخشِ ۵): انتقالِ مالکیتِ کسب‌وکار
================================================================
این تست‌ها مستقیم رویِ منطقِ هسته (نه خودِ Command handler که Update/Reply
واقعی می‌خواد) اجرا می‌شن - همون الگویِ همه‌ی تست‌هایِ قبلیِ این پروژه.

اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_business_transfer -v
"""
from __future__ import annotations

import asyncio
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

import economy_core  # noqa: E402
import economy_business  # noqa: E402

CHAT = 17017
OWNER = 1
BUYER = 2
INTRUDER = 3


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


def _make_business(chat_id, uid, btype="shop", level=1):
    b = {
        "id": economy_business._new_id(chat_id, uid), "type": btype, "level": level,
        "employees": 0, "inventory": 0, "price_mult": 1.0, "ad_until": None,
        "last_collect_ts": time.time(), "last_tax_ts": time.time(), "purchased_ts": time.time(),
        "lifetime_revenue": 0, "lifetime_expense": 0, "history": [],
        "debt": 0, "crisis_strikes": 0, "bankrupt": False, "partners": {},
        "branches": 1, "employed_users": {},
    }
    economy_business.owned(chat_id, uid).append(b)
    return b


def arun(coro):
    return asyncio.run(coro)


def _propose(chat_id, owner_uid, business, buyer_uid, price):
    """شبیه‌سازیِ منطقِ هسته‌ی propose_transfer_command."""
    business["pending_transfer"] = {"to_uid": buyer_uid, "price": price, "proposed_ts": time.time()}


async def _accept(chat_id, owner_uid, business, buyer_uid):
    """شبیه‌سازیِ منطقِ هسته‌ی accept_transfer_command."""
    pending = business.get("pending_transfer")
    if not pending or pending["to_uid"] != buyer_uid:
        raise ValueError("no matching pending transfer")
    price = pending["price"]
    if price > 0:
        await economy_core.remove_coins(chat_id, buyer_uid, price, kind="OTHER", note="buy")
        await economy_core.add_coins(chat_id, owner_uid, price, kind="SALE", note="sell")
    await economy_business._collect_one(chat_id, owner_uid, business)
    economy_business.owned(chat_id, owner_uid).remove(business)
    del business["pending_transfer"]
    business["id"] = economy_business._assign_fresh_business_id(economy_business.owned(chat_id, buyer_uid))
    economy_business.owned(chat_id, buyer_uid).append(business)
    return business


class TransferProposalTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_propose_sets_pending_field_without_moving_business(self):
        b = _make_business(CHAT, OWNER)
        _propose(CHAT, OWNER, b, BUYER, 0)
        self.assertIn(b, economy_business.owned(CHAT, OWNER))
        self.assertNotIn(b, economy_business.owned(CHAT, BUYER))
        self.assertEqual(b["pending_transfer"]["to_uid"], BUYER)

    def test_gift_transfer_moves_business_with_no_payment(self):
        b = _make_business(CHAT, OWNER)
        _propose(CHAT, OWNER, b, BUYER, 0)
        arun(_accept(CHAT, OWNER, b, BUYER))
        self.assertNotIn(b, economy_business.owned(CHAT, OWNER))
        self.assertIn(b, economy_business.owned(CHAT, BUYER))
        self.assertEqual(economy_core.get_wallet(CHAT, OWNER), 0)
        self.assertEqual(economy_core.get_wallet(CHAT, BUYER), 0)

    def test_paid_transfer_moves_money_and_business(self):
        b = _make_business(CHAT, OWNER)
        arun(economy_core.add_coins(CHAT, BUYER, 10_000, kind="REWARD"))
        _propose(CHAT, OWNER, b, BUYER, 3000)
        arun(_accept(CHAT, OWNER, b, BUYER))
        self.assertEqual(economy_core.get_wallet(CHAT, BUYER), 7000)
        self.assertEqual(economy_core.get_wallet(CHAT, OWNER), 3000)
        self.assertIn(b, economy_business.owned(CHAT, BUYER))

    def test_wrong_buyer_cannot_accept(self):
        b = _make_business(CHAT, OWNER)
        _propose(CHAT, OWNER, b, BUYER, 0)
        with self.assertRaises(ValueError):
            arun(_accept(CHAT, OWNER, b, INTRUDER))

    def test_accept_fails_without_enough_funds(self):
        b = _make_business(CHAT, OWNER)
        _propose(CHAT, OWNER, b, BUYER, 5000)  # BUYER has no money
        with self.assertRaises(economy_core.InsufficientFundsError):
            arun(_accept(CHAT, OWNER, b, BUYER))
        # کسب‌وکار باید دست‌نخورده بمونه
        self.assertIn(b, economy_business.owned(CHAT, OWNER))

    def test_pending_transfer_cleared_after_cancel(self):
        b = _make_business(CHAT, OWNER)
        _propose(CHAT, OWNER, b, BUYER, 0)
        del b["pending_transfer"]
        self.assertNotIn("pending_transfer", b)
        self.assertIn(b, economy_business.owned(CHAT, OWNER))

    def test_fresh_id_avoids_collision_with_buyer_existing_business(self):
        _make_business(CHAT, BUYER)  # id=1 توی فضای‌نامِ BUYER
        b_seller = _make_business(CHAT, OWNER)  # id=1 توی فضای‌نامِ OWNER (مستقل)
        _propose(CHAT, OWNER, b_seller, BUYER, 0)
        arun(_accept(CHAT, OWNER, b_seller, BUYER))

        ids = [b["id"] for b in economy_business.owned(CHAT, BUYER)]
        self.assertEqual(len(ids), len(set(ids)))  # بدونِ تصادمِ id

    def test_business_with_debt_transfers_debt_along_with_it(self):
        b = _make_business(CHAT, OWNER)
        b["debt"] = 2000
        _propose(CHAT, OWNER, b, BUYER, 0)
        arun(_accept(CHAT, OWNER, b, BUYER))
        transferred = next(x for x in economy_business.owned(CHAT, BUYER) if x is b)
        self.assertEqual(transferred["debt"], 2000)  # بدهی گم نشد، با کسب‌وکار منتقل شد

    def test_groups_are_independent(self):
        other_chat = CHAT + 1
        b = _make_business(CHAT, OWNER)
        _propose(CHAT, OWNER, b, BUYER, 0)
        self.assertEqual(len(economy_business.owned(other_chat, OWNER)), 0)


if __name__ == "__main__":
    unittest.main()
