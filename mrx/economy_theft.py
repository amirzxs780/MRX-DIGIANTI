# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY THEFT + WANTED/JAIL — فاز ۴ (Module 8 + Module 9)
=======================================================================
یه مکانیک فانتزی/بازی‌ه، کاملاً با پول مجازی داخل ربات؛ هیچ عملیات واقعی‌ای
(هک، نفوذ، آسیب واقعی) انجام نمی‌ده. طبق فایل معماری، Wanted/Jail فایل جدا
نداره و همین‌جا (Module 9) پیاده‌سازی می‌شه.

قواعد کلیدی:
    • فقط از کیف‌پول (wallet) دزدی می‌شه، نه بانک (Bank Protection).
    • اگه هدف بیمه داشته باشه (economy_security)، هم شانس موفقیت دزد کمتر
      می‌شه، هم سقف ضرر هدف محدود می‌شه.
    • شکست خوردن در دزدی = Jail + Fine با هم.
    • همه‌ی پول از economy_core عبور می‌کنه.
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
import economy_security
import economy_missions

logger = logging.getLogger("economy_theft")

STATE_FILE = getattr(config, "ECONOMY_THEFT_STATE_FILE", "economy_theft_state.json")

_save_lock = asyncio.Lock()


def _host():
    import bot as host
    return host


# ---------------------------------------------------------------------------
# 💾 State
# ---------------------------------------------------------------------------

# chat_id -> uid -> {"level","xp","successes","fails","streak","best_streak"}
_thief_stats = defaultdict(dict)
_last_steal_ts = defaultdict(dict)         # chat_id -> uid -> ts
_steal_daily = defaultdict(dict)           # chat_id -> uid -> {"date","count"}
_wanted = defaultdict(dict)                # chat_id -> uid -> {"level": float, "last_update_ts": float}
_jail_until = defaultdict(dict)            # chat_id -> uid -> ts (0 یعنی آزاد)
_last_escape_ts = defaultdict(dict)        # chat_id -> uid -> ts


def _today_str() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _stats(chat_id: int, uid: int) -> dict:
    s = _thief_stats[chat_id].get(uid)
    if not s:
        s = {"level": 1, "xp": 0, "successes": 0, "fails": 0, "streak": 0, "best_streak": 0}
        _thief_stats[chat_id][uid] = s
    return s


def _xp_needed(level: int) -> int:
    return getattr(config, "THEFT_XP_PER_LEVEL_BASE", 120) * level


# ---------------------------------------------------------------------------
# 🚔 Wanted Level (با گذر زمان کمی Decay می‌شه)
# ---------------------------------------------------------------------------

def wanted_level(chat_id: int, uid: int) -> float:
    rec = _wanted[chat_id].get(uid)
    if not rec:
        return 0.0
    elapsed_hours = (time.time() - rec["last_update_ts"]) / 3600.0
    decay = elapsed_hours * getattr(config, "THEFT_WANTED_DECAY_PER_HOUR", 0.5)
    level = max(0.0, rec["level"] - decay)
    rec["level"] = level
    rec["last_update_ts"] = time.time()
    return level


def _bump_wanted(chat_id: int, uid: int, amount: float) -> None:
    current = wanted_level(chat_id, uid)  # این خودش Decay رو اعمال می‌کنه
    _wanted[chat_id][uid] = {"level": current + amount, "last_update_ts": time.time()}


# ---------------------------------------------------------------------------
# 🚔 Jail
# ---------------------------------------------------------------------------

def is_jailed(chat_id: int, uid: int) -> bool:
    return _jail_until[chat_id].get(uid, 0) > time.time()


def jail_remaining_seconds(chat_id: int, uid: int) -> float:
    return max(0.0, _jail_until[chat_id].get(uid, 0) - time.time())


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    minutes, sec = divmod(seconds, 60)
    if minutes > 0:
        return f"{minutes} دقیقه و {sec} ثانیه"
    return f"{sec} ثانیه"


async def _send_to_jail(chat_id: int, uid: int, minutes: float) -> None:
    current = _jail_until[chat_id].get(uid, 0)
    base = max(current, time.time())
    _jail_until[chat_id][uid] = base + minutes * 60


# ---------------------------------------------------------------------------
# 🦹 دزدی
# ---------------------------------------------------------------------------

def _daily_record(chat_id: int, uid: int) -> dict:
    rec = _steal_daily[chat_id].get(uid)
    today = _today_str()
    if not rec or rec.get("date") != today:
        rec = {"date": today, "count": 0}
        _steal_daily[chat_id][uid] = rec
    return rec


async def steal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دزدی (ریپلای روی پیام هدف) / /steal"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_theft"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    now = time.time()

    # 🧠 فاز ۹: Anti-Abuse سراسری
    try:
        import economy_antiabuse
        wait = economy_antiabuse.check_and_mark(chat.id, uid)
        if wait > 0:
            await message.reply_text(f"⏳ یکم آروم‌تر؛ {wait:.1f} ثانیه‌ی دیگه صبر کن.")
            return
    except Exception as e:
        logger.warning(f"چک Anti-Abuse سراسری ناموفق بود: {e}")

    if is_jailed(chat.id, uid):
        await message.reply_text(f"🚔 تو زندانی! {_fmt_duration(jail_remaining_seconds(chat.id, uid))} دیگه مونده. با «فرار» می‌تونی امتحان کنی.")
        return

    target_id = host._resolve_target_id(message)
    if not target_id:
        await message.reply_text("باید روی پیام یه نفر ریپلای کنی تا ازش دزدی کنی: «دزدی» (ریپلای).")
        return
    if target_id == uid:
        await message.reply_text("از خودت که نمی‌شه دزدی کرد 😅")
        return

    last = _last_steal_ts[chat.id].get(uid, 0)
    cooldown_s = getattr(config, "THEFT_COOLDOWN_MINUTES", 45) * 60
    remaining = cooldown_s - (now - last)
    if remaining > 0:
        await message.reply_text(f"⏳ هنوز {int(remaining // 60) + 1} دقیقه‌ی دیگه تا دزدی بعدی مونده.")
        return

    daily = _daily_record(chat.id, uid)
    limit = getattr(config, "THEFT_DAILY_LIMIT", 8)
    if daily["count"] >= limit:
        await message.reply_text(f"📆 امروز به سقف {limit} بار دزدی رسیدی.")
        return

    target_wallet = economy_core.get_wallet(chat.id, target_id)
    min_target = getattr(config, "THEFT_MIN_TARGET_WALLET", 200)
    if target_wallet < min_target:
        await message.reply_text("این بنده‌خدا چیز زیادی توی کیف‌پولش نداره؛ سراغ یکی دیگه برو.")
        return

    _last_steal_ts[chat.id][uid] = now
    daily["count"] += 1
    stats = _stats(chat.id, uid)

    # 🛡️ اثر بیمه‌ی هدف
    protection, max_loss = economy_security.protection_for(chat.id, target_id)

    level_bonus = min(
        stats["level"] * getattr(config, "THEFT_LEVEL_SUCCESS_BONUS_PER_LEVEL", 0.01),
        getattr(config, "THEFT_SUCCESS_CHANCE_CAP", 0.75) - getattr(config, "THEFT_BASE_SUCCESS_CHANCE", 0.42),
    )
    success_chance = getattr(config, "THEFT_BASE_SUCCESS_CHANCE", 0.42) + level_bonus
    success_chance *= (1 - protection)
    # ارتقای فاز ۴: منطقه‌ی زندگی *هدف* روی امنیتش اثر داره — دزدی از کسی که
    # توی محله‌ی فقیرنشین زندگی می‌کنه (امنیت کمتر) راحت‌تره، از VIP سخت‌تره.
    try:
        import economy_district
        success_chance *= economy_district.multiplier_for(chat.id, target_id, "crime")
    except Exception:
        pass
    success_chance = max(getattr(config, "THEFT_SUCCESS_CHANCE_MIN", 0.05), min(success_chance, getattr(config, "THEFT_SUCCESS_CHANCE_CAP", 0.75)))

    success = random.random() < success_chance
    lines: list[str] = []
    mission_events: list[str] = []

    if success:
        frac = random.uniform(config.THEFT_STEAL_MIN_FRACTION, config.THEFT_STEAL_MAX_FRACTION)
        amount = round(target_wallet * frac)
        if max_loss is not None:
            amount = min(amount, max_loss)
        amount = max(1, min(amount, target_wallet))

        await economy_core.remove_coins(chat.id, target_id, amount, kind="stolen_from", counterparty_id=uid, note="دزدی شد")
        new_wallet = await economy_core.add_coins(chat.id, uid, amount, kind="theft_success", counterparty_id=target_id, note="دزدی موفق")

        stats["successes"] += 1
        stats["streak"] += 1
        stats["best_streak"] = max(stats["best_streak"], stats["streak"])
        stats["xp"] += getattr(config, "THEFT_XP_PER_SUCCESS", 25)
        _bump_wanted(chat.id, uid, getattr(config, "THEFT_WANTED_INCREASE_ON_SUCCESS", 1))

        insured_note = " (بیمه‌ی هدف جلوی مقداری از ضرر رو گرفت)" if protection > 0 else ""
        lines.append(f"🦹 دزدی موفق بود! +{amount} {config.CURRENCY_EMOJI}{insured_note}")
        lines.append(f"💰 موجودی تو: {new_wallet} {config.CURRENCY_NAME}")
        try:
            mission_events += await economy_missions.record_progress(chat.id, uid, "steal_success", 1)
        except Exception as e:
            logger.warning(f"ثبت پیشرفت ماموریت steal_success ناموفق بود: {e}")
    else:
        stats["fails"] += 1
        stats["streak"] = 0
        stats["xp"] += getattr(config, "THEFT_XP_PER_FAIL", 5)
        _bump_wanted(chat.id, uid, getattr(config, "THEFT_WANTED_INCREASE_ON_FAIL", 2))

        jail_minutes = random.uniform(config.THEFT_FAIL_JAIL_MIN_MINUTES, config.THEFT_FAIL_JAIL_MAX_MINUTES)
        await _send_to_jail(chat.id, uid, jail_minutes)

        wallet_now = economy_core.get_wallet(chat.id, uid)
        fine = min(round(wallet_now * getattr(config, "THEFT_FAIL_FINE_FRACTION", 0.15)), getattr(config, "THEFT_FAIL_FINE_MAX", 2000), wallet_now)
        if fine > 0:
            await economy_core.remove_coins(chat.id, uid, fine, kind="theft_fine", note="جریمه‌ی دزدی ناموفق")

        lines.append("🚨 گیر افتادی!")
        lines.append(f"🚔 {int(jail_minutes)} دقیقه زندانی شدی.")
        if fine > 0:
            lines.append(f"💸 {fine} {config.CURRENCY_NAME} هم جریمه شدی.")
        lines.append("با «فرار» می‌تونی زودتر امتحان کنی بری بیرون.")

    level_events = []
    while stats["xp"] >= _xp_needed(stats["level"]):
        stats["xp"] -= _xp_needed(stats["level"])
        stats["level"] += 1
        level_events.append(f"⭐ Thief Level تو رفت {stats['level']}!")

    total = stats["successes"] + stats["fails"]
    rate = (stats["successes"] / total * 100) if total else 0
    lines.append(f"📊 Thief Level {stats['level']} — نرخ موفقیت: {rate:.0f}٪ — Streak: {stats['streak']}")
    lines.extend(level_events)
    lines.extend(mission_events)
    try:
        import economy_achievements
        lines.extend(economy_achievements.check_all(chat.id, uid))
    except Exception as e:
        logger.warning(f"چک دستاوردهای اقتصادی ناموفق بود: {e}")
    await message.reply_text("\n".join(lines))
    await host.save_state()


# ---------------------------------------------------------------------------
# 🚔 Wanted / Jail / Escape
# ---------------------------------------------------------------------------

async def wanted_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحت تعقیب / /wanted"""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    level = wanted_level(chat.id, uid)
    stats = _stats(chat.id, uid)
    if level <= 0:
        await message.reply_text("😇 الان تحت تعقیب نیستی.")
        return
    stars = "⭐" * min(5, max(1, round(level)))
    lines = [
        f"🚔 سطح تحت‌تعقیب بودن: {level:.1f} {stars}",
        f"🦹 Thief Level: {stats['level']} — موفقیت‌ها: {stats['successes']} — گیرافتادن: {stats['fails']}",
    ]
    if is_jailed(chat.id, uid):
        lines.append(f"⛓️ الان زندانی هستی ({_fmt_duration(jail_remaining_seconds(chat.id, uid))} مونده).")
    await message.reply_text("\n".join(lines))


async def jail_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زندان / /jail"""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    if not is_jailed(chat.id, uid):
        await message.reply_text("🔓 زندانی نیستی، آزادی.")
        return
    remaining = jail_remaining_seconds(chat.id, uid)
    await message.reply_text(
        f"⛓️ زندانی هستی؛ {_fmt_duration(remaining)} مونده.\nبا «فرار» می‌تونی زودتر امتحان کنی (ریسک داره)."
    )


async def escape_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فرار / /escape"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return

    uid = update.effective_user.id
    if not is_jailed(chat.id, uid):
        await message.reply_text("زندانی نیستی که بخوای فرار کنی.")
        return

    now = time.time()
    last = _last_escape_ts[chat.id].get(uid, 0)
    cooldown = getattr(config, "JAIL_ESCAPE_COOLDOWN_SECONDS", 120)
    remaining_cd = cooldown - (now - last)
    if remaining_cd > 0:
        await message.reply_text(f"⏳ هنوز {int(remaining_cd) + 1} ثانیه‌ی دیگه تا تلاش بعدی برای فرار مونده.")
        return
    _last_escape_ts[chat.id][uid] = now

    # 🗝️ فاز ۷: کیت فرار (Inventory) - اگه فعال باشه، فرار تضمینی موفقه
    guaranteed = False
    try:
        import economy_inventory
        if economy_inventory.has_guaranteed_escape(chat.id, uid):
            economy_inventory.consume_guaranteed_escape(chat.id, uid)
            guaranteed = True
    except Exception as e:
        logger.warning(f"چک کردن escape_kit ناموفق بود: {e}")

    if guaranteed or random.random() < getattr(config, "JAIL_ESCAPE_CHANCE", 0.35):
        _jail_until[chat.id][uid] = 0
        _bump_wanted(chat.id, uid, 1)
        note = " (با کیت فرار 🗝️)" if guaranteed else ""
        await message.reply_text(f"🏃 موفق شدی فرار کنی!{note} (ولی حالا یکم تحت‌تعقیب‌تر شدی)")
    else:
        extra = getattr(config, "JAIL_ESCAPE_FAIL_EXTRA_MINUTES", 10)
        await _send_to_jail(chat.id, uid, extra)
        await message.reply_text(f"🚫 فرار ناموفق بود! {extra} دقیقه به زمان زندانت اضافه شد.")
    await host.save_state()


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "thief_stats": {str(c): dict(v) for c, v in _thief_stats.items()},
        "last_steal_ts": {str(c): dict(v) for c, v in _last_steal_ts.items()},
        "steal_daily": {str(c): {str(u): d for u, d in v.items()} for c, v in _steal_daily.items()},
        "wanted": {str(c): {str(u): r for u, r in v.items()} for c, v in _wanted.items()},
        "jail_until": {str(c): dict(v) for c, v in _jail_until.items()},
        "last_escape_ts": {str(c): dict(v) for c, v in _last_escape_ts.items()},
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
            logger.warning(f"ذخیره‌ی state موتور Theft ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور Theft ناموفق بود: {e}")
        return

    for cid, v in data.get("thief_stats", {}).items():
        _thief_stats[int(cid)] = {int(u): s for u, s in v.items()}
    for cid, v in data.get("last_steal_ts", {}).items():
        _last_steal_ts[int(cid)] = {int(u): ts for u, ts in v.items()}
    for cid, v in data.get("steal_daily", {}).items():
        _steal_daily[int(cid)] = {int(u): d for u, d in v.items()}
    for cid, v in data.get("wanted", {}).items():
        _wanted[int(cid)] = {int(u): r for u, r in v.items()}
    for cid, v in data.get("jail_until", {}).items():
        _jail_until[int(cid)] = {int(u): ts for u, ts in v.items()}
    for cid, v in data.get("last_escape_ts", {}).items():
        _last_escape_ts[int(cid)] = {int(u): ts for u, ts in v.items()}

    logger.info("وضعیت موتور Theft از فایل بارگذاری شد.")
