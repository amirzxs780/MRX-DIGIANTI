# -*- coding: utf-8 -*-
"""
DIGNANTI V18 — تست‌های مزایده (economy_auction)
================================================================
اجرا (از ریشه‌ی پروژه - پوشه‌ی mrx):
    python3 -m unittest tests.test_economy_auction -v
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
import economy_auction  # noqa: E402

CHAT = 4004
SELLER = 1
BIDDER_A = 2
BIDDER_B = 3


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

    economy_auction._auctions.clear()
    economy_auction._next_auction_id.clear()
    economy_auction._auction_locks.clear()

    journal_dir = tempfile.mkdtemp(prefix="dignanti_auction_test_journal_")
    economy_core.WALLET_JOURNAL_FILE = os.path.join(journal_dir, "wallet_journal.jsonl")


def _make_business(chat_id, uid, btype="shop", level=1, inventory=0):
    b = {
        "id": economy_business._new_id(chat_id, uid), "type": btype, "level": level,
        "employees": 0, "inventory": inventory, "price_mult": 1.0, "ad_until": None,
        "last_collect_ts": time.time(), "last_tax_ts": time.time(), "purchased_ts": time.time(),
        "lifetime_revenue": 0, "lifetime_expense": 0, "history": [],
    }
    economy_business.owned(chat_id, uid).append(b)
    return b


def arun(coro):
    return asyncio.run(coro)


class AuctionEngineTest(unittest.TestCase):
    def setUp(self):
        _reset_all()

    def test_create_auction_escrows_item(self):
        b = _make_business(CHAT, SELLER)
        a = economy_auction.create_auction(CHAT, SELLER, "business", b["id"], 1000, 30)
        self.assertEqual(a["status"], "active")
        self.assertNotIn(b, economy_business.owned(CHAT, SELLER))  # دیگه جزوِ دارایی‌های فروشنده نیست

    def test_create_auction_rejects_unowned_item(self):
        with self.assertRaises(economy_auction.AuctionError):
            economy_auction.create_auction(CHAT, SELLER, "business", 999, 1000, 30)

    def test_create_auction_rejects_bad_duration(self):
        b = _make_business(CHAT, SELLER)
        with self.assertRaises(economy_auction.AuctionError):
            economy_auction.create_auction(CHAT, SELLER, "business", b["id"], 1000, 1)  # کمتر از حداقل

    def test_max_active_per_user(self):
        for _ in range(config.AUCTION_MAX_ACTIVE_PER_USER):
            b = _make_business(CHAT, SELLER)
            economy_auction.create_auction(CHAT, SELLER, "business", b["id"], 1000, 30)
        b_extra = _make_business(CHAT, SELLER)
        with self.assertRaises(economy_auction.AuctionError):
            economy_auction.create_auction(CHAT, SELLER, "business", b_extra["id"], 1000, 30)

    def test_seller_cannot_bid_on_own_auction(self):
        b = _make_business(CHAT, SELLER)
        a = economy_auction.create_auction(CHAT, SELLER, "business", b["id"], 1000, 30)
        arun(economy_core.add_coins(CHAT, SELLER, 100_000, kind="REWARD"))
        with self.assertRaises(economy_auction.AuctionError):
            arun(economy_auction.place_bid(CHAT, SELLER, a["id"], 1000))

    def test_bid_reserves_funds_immediately(self):
        b = _make_business(CHAT, SELLER)
        a = economy_auction.create_auction(CHAT, SELLER, "business", b["id"], 1000, 30)
        arun(economy_core.add_coins(CHAT, BIDDER_A, 5000, kind="REWARD"))
        arun(economy_auction.place_bid(CHAT, BIDDER_A, a["id"], 1000))
        # پول واقعاً از کیفِ‌پول کم شده (Reserve واقعی، نه فقط یه چک)
        self.assertEqual(economy_core.get_wallet(CHAT, BIDDER_A), 4000)

    def test_bid_below_minimum_rejected(self):
        b = _make_business(CHAT, SELLER)
        a = economy_auction.create_auction(CHAT, SELLER, "business", b["id"], 1000, 30)
        arun(economy_core.add_coins(CHAT, BIDDER_A, 5000, kind="REWARD"))
        with self.assertRaises(economy_auction.AuctionError):
            arun(economy_auction.place_bid(CHAT, BIDDER_A, a["id"], 500))  # زیرِ قیمتِ پایه

    def test_outbid_refunds_previous_bidder(self):
        b = _make_business(CHAT, SELLER)
        a = economy_auction.create_auction(CHAT, SELLER, "business", b["id"], 1000, 30)
        arun(economy_core.add_coins(CHAT, BIDDER_A, 10_000, kind="REWARD"))
        arun(economy_core.add_coins(CHAT, BIDDER_B, 10_000, kind="REWARD"))

        arun(economy_auction.place_bid(CHAT, BIDDER_A, a["id"], 1000))
        self.assertEqual(economy_core.get_wallet(CHAT, BIDDER_A), 9000)

        arun(economy_auction.place_bid(CHAT, BIDDER_B, a["id"], 1100))
        # بیدرِ A باید کاملاً پولش برگشته باشه
        self.assertEqual(economy_core.get_wallet(CHAT, BIDDER_A), 10_000)
        self.assertEqual(economy_core.get_wallet(CHAT, BIDDER_B), 10_000 - 1100)

    def test_increment_percent_enforced(self):
        b = _make_business(CHAT, SELLER)
        a = economy_auction.create_auction(CHAT, SELLER, "business", b["id"], 1000, 30)
        arun(economy_core.add_coins(CHAT, BIDDER_A, 10_000, kind="REWARD"))
        arun(economy_core.add_coins(CHAT, BIDDER_B, 10_000, kind="REWARD"))
        arun(economy_auction.place_bid(CHAT, BIDDER_A, a["id"], 1000))
        required = economy_auction.min_next_bid(economy_auction.get_auction(CHAT, a["id"]))
        with self.assertRaises(economy_auction.AuctionError):
            arun(economy_auction.place_bid(CHAT, BIDDER_B, a["id"], required - 1))

    def test_settle_sold_pays_seller_and_transfers_item(self):
        b = _make_business(CHAT, SELLER)
        a = economy_auction.create_auction(CHAT, SELLER, "business", b["id"], 1000, 10)
        arun(economy_core.add_coins(CHAT, BIDDER_A, 10_000, kind="REWARD"))
        arun(economy_auction.place_bid(CHAT, BIDDER_A, a["id"], 2000))

        # زمان مزایده رو به گذشته ببر تا منقضی بشه
        economy_auction._auctions[CHAT][a["id"]]["end_ts"] = time.time() - 1
        arun(economy_auction.maybe_settle_all(CHAT))

        settled = economy_auction.get_auction(CHAT, a["id"])
        self.assertEqual(settled["status"], "ended_sold")
        self.assertEqual(economy_core.get_wallet(CHAT, SELLER), 2000)
        winner_items = economy_business.owned(CHAT, BIDDER_A)
        self.assertEqual(len(winner_items), 1)
        self.assertEqual(winner_items[0]["type"], "shop")

    def test_settle_unsold_returns_item_to_seller(self):
        b = _make_business(CHAT, SELLER)
        a = economy_auction.create_auction(CHAT, SELLER, "business", b["id"], 1000, 10)
        economy_auction._auctions[CHAT][a["id"]]["end_ts"] = time.time() - 1
        arun(economy_auction.maybe_settle_all(CHAT))

        settled = economy_auction.get_auction(CHAT, a["id"])
        self.assertEqual(settled["status"], "ended_unsold")
        self.assertEqual(len(economy_business.owned(CHAT, SELLER)), 1)

    def test_fresh_id_avoids_collision_with_winner_existing_items(self):
        # برنده از قبل یه کسب‌وکار با id=1 داره؛ آیتمِ بُرده‌شده نباید همون id رو بگیره
        _make_business(CHAT, BIDDER_A)  # id=1 برای BIDDER_A
        b_seller = _make_business(CHAT, SELLER)  # id=1 برای SELLER (فضای‌نامِ جدا)
        a = economy_auction.create_auction(CHAT, SELLER, "business", b_seller["id"], 1000, 10)
        arun(economy_core.add_coins(CHAT, BIDDER_A, 10_000, kind="REWARD"))
        arun(economy_auction.place_bid(CHAT, BIDDER_A, a["id"], 1500))
        economy_auction._auctions[CHAT][a["id"]]["end_ts"] = time.time() - 1
        arun(economy_auction.maybe_settle_all(CHAT))

        ids = [it["id"] for it in economy_business.owned(CHAT, BIDDER_A)]
        self.assertEqual(len(ids), len(set(ids)))  # هیچ تصادمِ idای نباشه

    def test_cannot_cancel_after_bid_placed(self):
        b = _make_business(CHAT, SELLER)
        a = economy_auction.create_auction(CHAT, SELLER, "business", b["id"], 1000, 30)
        arun(economy_core.add_coins(CHAT, BIDDER_A, 10_000, kind="REWARD"))
        arun(economy_auction.place_bid(CHAT, BIDDER_A, a["id"], 1000))
        with self.assertRaises(economy_auction.AuctionError):
            arun(economy_auction.cancel_auction(CHAT, SELLER, a["id"]))

    def test_cancel_before_any_bid_returns_item(self):
        b = _make_business(CHAT, SELLER)
        a = economy_auction.create_auction(CHAT, SELLER, "business", b["id"], 1000, 30)
        arun(economy_auction.cancel_auction(CHAT, SELLER, a["id"]))
        self.assertEqual(len(economy_business.owned(CHAT, SELLER)), 1)
        self.assertEqual(economy_auction.get_auction(CHAT, a["id"])["status"], "cancelled")

    def test_only_seller_can_cancel(self):
        b = _make_business(CHAT, SELLER)
        a = economy_auction.create_auction(CHAT, SELLER, "business", b["id"], 1000, 30)
        with self.assertRaises(economy_auction.AuctionError):
            arun(economy_auction.cancel_auction(CHAT, BIDDER_A, a["id"]))

    def test_concurrent_bids_are_serialized_not_duplicated(self):
        """شبیه‌سازیِ دو Callback تقریباً هم‌زمان روی یه مزایده - نتیجه باید
        دقیقاً یه برنده باشه و پولِ هیچ‌کس دوبار کم/زیاد نشه."""
        b = _make_business(CHAT, SELLER)
        a = economy_auction.create_auction(CHAT, SELLER, "business", b["id"], 1000, 30)
        arun(economy_core.add_coins(CHAT, BIDDER_A, 10_000, kind="REWARD"))
        arun(economy_core.add_coins(CHAT, BIDDER_B, 10_000, kind="REWARD"))

        async def _go():
            await asyncio.gather(
                economy_auction.place_bid(CHAT, BIDDER_A, a["id"], 1000),
                economy_auction.place_bid(CHAT, BIDDER_B, a["id"], 1200),
            )

        arun(_go())
        final = economy_auction.get_auction(CHAT, a["id"])
        total_in_wallets_and_reserve = (
            economy_core.get_wallet(CHAT, BIDDER_A)
            + economy_core.get_wallet(CHAT, BIDDER_B)
            + final["current_bid"]
        )
        # مجموعِ پولِ دو نفر باید دقیقاً حفظ شده باشه (۱۰٬۰۰۰+۱۰٬۰۰۰ = ۲۰٬۰۰۰) - نه کم نه زیاد
        self.assertEqual(total_in_wallets_and_reserve, 20_000)
        self.assertIn(final["current_bidder"], (BIDDER_A, BIDDER_B))

    def test_groups_are_independent(self):
        other_chat = CHAT + 1
        b = _make_business(CHAT, SELLER)
        economy_auction.create_auction(CHAT, SELLER, "business", b["id"], 1000, 30)
        self.assertEqual(len(economy_auction.list_active(CHAT)), 1)
        self.assertEqual(len(economy_auction.list_active(other_chat)), 0)

    def test_save_load_round_trip(self):
        b = _make_business(CHAT, SELLER)
        a = economy_auction.create_auction(CHAT, SELLER, "business", b["id"], 1000, 30)
        arun(economy_core.add_coins(CHAT, BIDDER_A, 10_000, kind="REWARD"))
        arun(economy_auction.place_bid(CHAT, BIDDER_A, a["id"], 1500))

        tmp_dir = tempfile.mkdtemp(prefix="dignanti_auction_state_")
        old_state_file = economy_auction.STATE_FILE
        economy_auction.STATE_FILE = os.path.join(tmp_dir, "auction_state.json")
        try:
            economy_auction._write_state_file(economy_auction._collect_state())
            economy_auction._auctions.clear()
            economy_auction._next_auction_id.clear()
            economy_auction.load_state()

            restored = economy_auction.get_auction(CHAT, a["id"])
            self.assertEqual(restored["current_bid"], 1500)
            self.assertEqual(restored["current_bidder"], BIDDER_A)
            self.assertEqual(restored["asset"]["type"], "shop")
        finally:
            economy_auction.STATE_FILE = old_state_file

    def test_text_formatters_do_not_crash(self):
        b = _make_business(CHAT, SELLER)
        economy_auction.create_auction(CHAT, SELLER, "business", b["id"], 1000, 30)
        text1 = arun(economy_auction.format_active_text(CHAT))
        text2 = economy_auction.format_my_auctions_text(CHAT, SELLER)
        self.assertIn("مزایده‌های فعال", text1)
        self.assertIn("مزایده‌های من", text2)


if __name__ == "__main__":
    unittest.main()
