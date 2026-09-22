# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY UNDERGROUND — فاز ۷ (Module 16)
=====================================================
یه Roleplay World کاملاً فانتزیه: «دارک وب»/«جاسوسی»/«هک» فقط سه تم متفاوت
از یه مکانیک Risk/Reward هستن (مثل Theft در فاز ۴)، در برابر NPCهای خیالی.
هیچ عملیات واقعی هک/نفوذ/آسیب انجام نمی‌شه — فقط عدد و پیام داخل بازیه.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

import config
import economy_core
import economy_inventory

logger = logging.getLogger("economy_underground")

STATE_FILE = getattr(config, "ECONOMY_UNDERGROUND_STATE_FILE", "economy_underground_state.json")
CONTRACTS: dict = getattr(config, "UNDERGROUND_CONTRACTS", {})

_save_lock = asyncio.Lock()

# chat_id -> uid -> {"level","xp","reputation","successes","fails"}
_stats = defaultdict(dict)
_last_ts = defaultdict(dict)
_daily = defaultdict(dict)


def _host():
    import bot as host
    return host


def _today_str() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _stats_of(chat_id: int, uid: int) -> dict:
    s = _stats[chat_id].get(uid)
    if not s:
        s = {"level": 1, "xp": 0, "reputation": 0, "successes": 0, "fails": 0}
        _stats[chat_id][uid] = s
    return s


def _xp_needed(level: int) -> int:
    return getattr(config, "UNDERGROUND_XP_PER_LEVEL_BASE", 90) * level


async def _run_contract(update: Update, contract_key: str) -> None:
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_underground"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    contract = CONTRACTS[contract_key]
    uid = update.effective_user.id
    now = time.time()

    last = _last_ts[chat.id].get(uid, 0)
    cooldown = getattr(config, "UNDERGROUND_COOLDOWN_MINUTES", 35) * 60
    remaining = cooldown - (now - last)
    if remaining > 0:
        await message.reply_text(f"⏳ هنوز {int(remaining // 60) + 1} دقیقه‌ی دیگه تا قرارداد بعدی مونده.")
        return

    rec = _daily[chat.id].get(uid)
    today = _today_str()
    if not rec or rec.get("date") != today:
        rec = {"date": today, "count": 0}
        _daily[chat.id][uid] = rec
    limit = getattr(config, "UNDERGROUND_DAILY_LIMIT", 10)
    if rec["count"] >= limit:
        await message.reply_text(f"📆 امروز به سقف {limit} بار قرارداد زیرزمینی رسیدی.")
        return

    _last_ts[chat.id][uid] = now
    rec["count"] += 1
    stats = _stats_of(chat.id, uid)

    success = random.random() < contract["success_chance"]
    lines = [f"{contract['label']}"]

    if success:
        reward = random.randint(contract["reward_min"], contract["reward_max"])
        try:
            import economy_engine
            reward = economy_engine.apply_coin_multiplier(chat.id, uid, reward)
        except Exception as e:
            logger.warning(f"اعمال Coin Boost در Underground ناموفق بود: {e}")
        try:
            import economy_events
            ug_mult = economy_events.underground_reward_multiplier(chat.id)
            if ug_mult != 1.0:
                reward = round(reward * ug_mult)
        except Exception as e:
            logger.warning(f"اعمال ضریب رویداد Underground ناموفق بود: {e}")
        new_wallet = await economy_core.add_coins(chat.id, uid, reward, kind="underground_success", note=contract["label"])
        stats["successes"] += 1
        stats["reputation"] += 1
        stats["xp"] += contract["xp"]
        lines.append(f"✅ قرارداد موفق بود! +{reward} {config.CURRENCY_EMOJI}")
        lines.append(f"💰 موجودی: {new_wallet} {config.CURRENCY_NAME}")

        rare_chance = getattr(config, "UNDERGROUND_RARE_ITEM_CHANCE", 0.08)
        try:
            import economy_events
            rare_chance *= economy_events.rare_item_chance_multiplier(chat.id)
        except Exception as e:
            logger.warning(f"اعمال ضریب رویداد Rare Item ناموفق بود: {e}")
        if random.random() < rare_chance:
            rare_pool = [k for k, info in economy_inventory.CATALOG.items() if info["rarity"] in ("RARE", "EPIC")]
            if rare_pool:
                item_key = random.choice(rare_pool)
                economy_inventory.add_item(chat.id, uid, item_key, 1)
                lines.append(f"🎁 یه آیتم کمیاب پیدا کردی: {economy_inventory.CATALOG[item_key]['name']}!")
    else:
        wallet_now = economy_core.get_wallet(chat.id, uid)
        fine = round(wallet_now * contract["fail_fine_fraction"])
        fine = min(fine, wallet_now)
        if fine > 0:
            await economy_core.remove_coins(chat.id, uid, fine, kind="underground_fail", note=f"{contract['label']} — شکست")
        stats["fails"] += 1
        stats["xp"] += round(contract["xp"] * 0.3)
        lines.append("❌ قرارداد شکست خورد و ردت رو زدن.")
        if fine > 0:
            lines.append(f"💸 {fine} {config.CURRENCY_NAME} از دست دادی.")

    level_events = []
    while stats["xp"] >= _xp_needed(stats["level"]):
        stats["xp"] -= _xp_needed(stats["level"])
        stats["level"] += 1
        level_events.append(f"⭐ Underground Level تو رفت {stats['level']}!")
    lines.extend(level_events)

    total = stats["successes"] + stats["fails"]
    rate = (stats["successes"] / total * 100) if total else 0
    lines.append(f"🌑 Underground Level {stats['level']} — Reputation: {stats['reputation']} — نرخ موفقیت: {rate:.0f}٪")
    if success:
        try:
            import economy_achievements
            lines.extend(economy_achievements.check_all(chat.id, uid))
        except Exception as e:
            logger.warning(f"چک دستاوردهای اقتصادی ناموفق بود: {e}")
    await message.reply_text("\n".join(lines))
    await host.save_state()


async def darkweb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دارک وب / /darkweb"""
    await _run_contract(update, "darkweb")


async def spy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """جاسوس / /spy"""
    await _run_contract(update, "spy")


async def hacker_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هکر / /hacker"""
    await _run_contract(update, "hacker")


async def random_mission_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ماموریت زیرزمینی / /undergroundmission — یکی از سه نوع قرارداد رو تصادفی انتخاب می‌کنه."""
    key = random.choice(list(CONTRACTS.keys()))
    await _run_contract(update, key)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قرارداد / /contract — وضعیت + راهنما."""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    stats = _stats_of(chat.id, uid)
    total = stats["successes"] + stats["fails"]
    rate = (stats["successes"] / total * 100) if total else 0
    lines = [
        "🌑 دنیای زیرزمینی",
        f"⭐ Level {stats['level']} ({stats['xp']}/{_xp_needed(stats['level'])} XP)",
        f"💎 Reputation: {stats['reputation']}",
        f"📊 موفقیت: {stats['successes']}/{total} ({rate:.0f}٪)",
        "",
        "قراردادها: «دارک وب» «جاسوس» «هکر» یا «ماموریت زیرزمینی» (تصادفی)",
    ]
    await message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "stats": {str(c): dict(v) for c, v in _stats.items()},
        "last_ts": {str(c): dict(v) for c, v in _last_ts.items()},
        "daily": {str(c): {str(u): d for u, d in v.items()} for c, v in _daily.items()},
    }


def _write_state_file(data: dict):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


async def save_state():
    async with _save_lock:
        try:
            await asyncio.to_thread(_write_state_file, _collect_state())
        except Exception as e:
            logger.warning(f"ذخیره‌ی state موتور Underground ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور Underground ناموفق بود: {e}")
        return
    for cid, v in data.get("stats", {}).items():
        _stats[int(cid)] = {int(u): s for u, s in v.items()}
    for cid, v in data.get("last_ts", {}).items():
        _last_ts[int(cid)] = {int(u): ts for u, ts in v.items()}
    for cid, v in data.get("daily", {}).items():
        _daily[int(cid)] = {int(u): d for u, d in v.items()}
    logger.info("وضعیت موتور Underground از فایل بارگذاری شد.")
