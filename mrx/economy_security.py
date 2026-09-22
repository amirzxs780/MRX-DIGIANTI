# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY SECURITY / INSURANCE — فاز ۴ (Module 7)
=============================================================
این ماژول فقط یه چیز رو مدیریت می‌کنه: بیمه‌ی کاربر (Tier + انقضا).
هیچ پول مستقیمی جز هزینه‌ی خرید (که از economy_core.remove_coins می‌گیره)
جابه‌جا نمی‌کنه. اثر واقعی بیمه (کاهش شانس موفقیت دزد + سقف ضرر) توسط
economy_theft.py از طریق protection_for() خونده می‌شه — یعنی این دو ماژول
جفت‌شده‌ان اما Theft هیچ‌وقت مستقیم به _insurance دست نمی‌زنه.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

import config
import economy_core

logger = logging.getLogger("economy_security")

STATE_FILE = getattr(config, "ECONOMY_SECURITY_STATE_FILE", "economy_security_state.json")
TIERS: dict = getattr(config, "INSURANCE_TIERS", {})
TIER_ORDER: list = getattr(config, "INSURANCE_TIER_ORDER", list(TIERS.keys()))

_save_lock = asyncio.Lock()

# chat_id -> uid -> {"tier": key, "expires_ts": float}
_insurance = defaultdict(dict)


def _host():
    import bot as host
    return host


# ---------------------------------------------------------------------------
# 📖 خواندن وضعیت (برای استفاده‌ی خود این ماژول و economy_theft.py)
# ---------------------------------------------------------------------------

def get_active_tier(chat_id: int, uid: int) -> str | None:
    rec = _insurance[chat_id].get(uid)
    if not rec:
        return None
    if rec["expires_ts"] <= time.time():
        return None
    return rec["tier"]


def protection_for(chat_id: int, uid: int) -> tuple[float, int | None]:
    """(theft_protection_fraction, max_loss) رو برمی‌گردونه. اگه بیمه‌ای فعال
    نباشه، (0.0, None) — یعنی هیچ محافظتی نیست."""
    tier = get_active_tier(chat_id, uid)
    if not tier:
        return 0.0, None
    info = TIERS[tier]
    return info["theft_protection"], info["max_loss"]


def _expiry_text(expires_ts: float) -> str:
    remaining = expires_ts - time.time()
    if remaining <= 0:
        return "منقضی شده"
    hours = int(remaining // 3600)
    minutes = int((remaining % 3600) // 60)
    if hours > 0:
        return f"{hours} ساعت و {minutes} دقیقه‌ی دیگه"
    return f"{minutes} دقیقه‌ی دیگه"


# ---------------------------------------------------------------------------
# 📋 نمایش
# ---------------------------------------------------------------------------

async def insurance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بیمه / وضعیت بیمه / /insurance"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not host.get_setting(chat.id, "economy_enabled"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    active = get_active_tier(chat.id, uid)
    lines = ["🛡️ بیمه‌ی DIGIANTI", ""]
    if active:
        info = TIERS[active]
        rec = _insurance[chat.id][uid]
        lines.append(f"✅ بیمه‌ی فعلی: {info['name']}")
        lines.append(f"⏳ انقضا: {_expiry_text(rec['expires_ts'])}")
        lines.append(f"🛡️ کاهش شانس دزدی علیه تو: {info['theft_protection']*100:.0f}٪")
        lines.append(f"💰 سقف ضرر در هر دزدی: {info['max_loss']} {config.CURRENCY_NAME}")
    else:
        lines.append("❌ الان هیچ بیمه‌ی فعالی نداری.")
    lines.append("")
    lines.append("سطح‌های قابل خرید:")
    for key in TIER_ORDER:
        info = TIERS[key]
        lines.append(
            f"  {info['name']}: {info['cost']} {config.CURRENCY_NAME} — {info['duration_hours']} ساعت — "
            f"محافظت {info['theft_protection']*100:.0f}٪ — سقف ضرر {info['max_loss']}"
        )
    lines.append("")
    lines.append("با «خرید بیمه [نام]» (basic/advanced/premium/elite) بخر، یا با «ارتقای بیمه» برو سطح بعدی.")
    await message.reply_text("\n".join(lines))


def _find_tier(name: str) -> str | None:
    name = name.strip().lower()
    if name in TIERS:
        return name
    for key, info in TIERS.items():
        if name in info["name"].lower():
            return key
    return None


async def _apply_purchase(update: Update, tier_key: str) -> None:
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    uid = update.effective_user.id
    info = TIERS[tier_key]

    try:
        await economy_core.remove_coins(
            chat.id, uid, info["cost"], kind="insurance_buy", note=f"خرید بیمه‌ی {info['name']}"
        )
    except economy_core.InsufficientFundsError:
        await message.reply_text(
            f"❌ موجودی کافی نیست. بیمه‌ی {info['name']} {info['cost']} {config.CURRENCY_NAME} هزینه داره."
        )
        return

    _insurance[chat.id][uid] = {"tier": tier_key, "expires_ts": time.time() + info["duration_hours"] * 3600}
    await message.reply_text(
        f"✅ بیمه‌ی {info['name']} فعال شد! تا {info['duration_hours']} ساعت دیگه معتبره.\n"
        f"🛡️ محافظت دزدی: {info['theft_protection']*100:.0f}٪ — سقف ضرر: {info['max_loss']} {config.CURRENCY_NAME}"
    )
    await host.save_state()


async def buy_insurance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خرید بیمه [نام] / /buyinsurance <tier>"""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if not args:
        names = "، ".join(TIER_ORDER)
        await message.reply_text(f"استفاده: «خرید بیمه [نام]». گزینه‌ها: {names}")
        return
    tier_key = _find_tier(" ".join(args))
    if not tier_key:
        names = "، ".join(TIER_ORDER)
        await message.reply_text(f"همچین سطح بیمه‌ای نیست. گزینه‌ها: {names}")
        return
    await _apply_purchase(update, tier_key)


async def upgrade_insurance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارتقای بیمه / /upgradeinsurance — بدون آرگومان، می‌ره سطح بعدی."""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    current = get_active_tier(chat.id, uid)
    if current is None:
        next_key = TIER_ORDER[0]
    else:
        idx = TIER_ORDER.index(current)
        if idx + 1 >= len(TIER_ORDER):
            await message.reply_text(f"🎖️ بیمه‌ات همین الان هم توی بالاترین سطحه ({TIERS[current]['name']}).")
            return
        next_key = TIER_ORDER[idx + 1]
    await _apply_purchase(update, next_key)


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {str(c): {str(u): rec for u, rec in v.items()} for c, v in _insurance.items()}


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
            logger.warning(f"ذخیره‌ی state موتور Security ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور Security ناموفق بود: {e}")
        return
    for cid, v in data.items():
        _insurance[int(cid)] = {int(u): rec for u, rec in v.items()}
    logger.info("وضعیت موتور Security از فایل بارگذاری شد.")
