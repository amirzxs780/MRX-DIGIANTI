# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY PET — فاز ۶ (Module 13)
=============================================
هر کاربر یه پت فعال داره (برای سادگی و پایداری، به‌جای مجموعه‌ای از چند پت).
HP مثل Energy در Jobs به‌صورت زمانی Regen می‌شه. «فایت» هم PvE (در برابر
هیولای وحشی تصادفی) و هم PvP (اگه روی پیام یه نفر که خودش پت داره ریپلای
بشه) رو پشتیبانی می‌کنه — با Escrow امن برای شرط‌بندیِ PvP.

Tournament/League (از فیچرهای اختیاری اسپک) عمداً پیاده نشدن — نیاز به
زیرساخت Leaderboard سراسری دارن که در فاز ۸ ساخته می‌شه؛ فعلاً Win/Loss هر
پت ردیابی می‌شه تا همون‌جا قابل‌استفاده باشه.
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
import economy_missions

logger = logging.getLogger("economy_pet")

STATE_FILE = getattr(config, "ECONOMY_PET_STATE_FILE", "economy_pet_state.json")
SPECIES: dict = getattr(config, "PET_SPECIES", {})

_save_lock = asyncio.Lock()

# chat_id -> uid -> pet dict | None
_pets = defaultdict(dict)
_last_feed_ts = defaultdict(dict)
_last_train_ts = defaultdict(dict)
# chat_id -> uid -> set(species_key) — ارتقای فاز ۲۴ (Collections): هر گونه‌ای
# که کاربر تا حالا به فرزندی قبول کرده، حتی اگه الان اون پت رو نداشته باشه.
_species_seen = defaultdict(lambda: defaultdict(set))
_last_fight_ts = defaultdict(dict)
_fight_daily = defaultdict(dict)


def _host():
    import bot as host
    return host


def _today_str() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def get_pet(chat_id: int, uid: int) -> dict | None:
    return _pets[chat_id].get(uid)


def species_seen(chat_id: int, uid: int) -> set:
    """همه‌ی گونه‌هایی که این کاربر تا حالا به فرزندی قبول کرده (فاز ۲۴: Collections)."""
    return set(_species_seen[chat_id].get(uid, set()))


def _max_hp(pet: dict) -> int:
    info = SPECIES[pet["species"]]
    return round(info["hp"] * (1 + 0.08 * (pet["level"] - 1)))


def _current_hp(chat_id: int, uid: int) -> int:
    pet = get_pet(chat_id, uid)
    if not pet:
        return 0
    now = time.time()
    elapsed_minutes = (now - pet["last_hp_update_ts"]) / 60.0
    regen = elapsed_minutes * getattr(config, "PET_HP_REGEN_PER_MINUTE", 0.5)
    max_hp = _max_hp(pet)
    hp = min(max_hp, pet["hp"] + regen)
    pet["hp"] = hp
    pet["last_hp_update_ts"] = now
    return round(hp)


def _power(pet: dict) -> float:
    info = SPECIES[pet["species"]]
    scale = 1 + 0.06 * (pet["level"] - 1)
    return (info["atk"] * 1.2 + info["def"] + info["spd"] * 0.8 + info["intel"] * 0.5) * scale


def _xp_needed(level: int) -> int:
    return getattr(config, "PET_XP_PER_LEVEL_BASE", 100) * level


def _display_name(pet: dict) -> str:
    info = SPECIES[pet["species"]]
    return info["evolved_name"] if pet.get("evolved") else info["name"]


async def _grant_xp(chat_id: int, uid: int, amount: int) -> list[str]:
    pet = get_pet(chat_id, uid)
    events = []
    # ⚡ فاز ۷: Boost عمومی XP + Boost اختصاصی پت (boost_pet_xp)
    try:
        import economy_engine
        amount = economy_engine.apply_xp_multiplier(chat_id, uid, amount)
        if economy_engine.has_active_item_type(chat_id, uid, "boost_pet_xp"):
            amount = round(amount * getattr(config, "PET_XP_BOOST_MULTIPLIER", 1.5))
    except Exception as e:
        logger.warning(f"اعمال Pet XP Boost ناموفق بود: {e}")
    try:
        import economy_events
        ev_mult = economy_events.pet_xp_multiplier(chat_id)
        if ev_mult != 1.0:
            amount = round(amount * ev_mult)
    except Exception as e:
        logger.warning(f"اعمال ضریب رویداد Pet XP ناموفق بود: {e}")
    pet["xp"] += amount
    while pet["xp"] >= _xp_needed(pet["level"]):
        pet["xp"] -= _xp_needed(pet["level"])
        pet["level"] += 1
        events.append(f"⭐ {_display_name(pet)} رفت Level {pet['level']}!")
        info = SPECIES[pet["species"]]
        if not pet.get("evolved") and pet["level"] >= info["evolves_at_level"]:
            events.append(f"✨ {_display_name(pet)} می‌تونه تکامل پیدا کنه! با «ارتقای پت» تکاملش بده.")
    return events


# ---------------------------------------------------------------------------
# 📋 خرید / نمایش
# ---------------------------------------------------------------------------

async def pets_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پت‌ها / /pets — لیست انواع پت قابل‌خرید."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_pet"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return
    lines = ["🐾 انواع پت قابل‌خرید:", ""]
    for key, info in SPECIES.items():
        lines.append(
            f"{info['name']} ({info['rarity']}) — قیمت: {info['cost']:,} {config.CURRENCY_NAME} — "
            f"HP:{info['hp']} ATK:{info['atk']} DEF:{info['def']} SPD:{info['spd']} INT:{info['intel']}"
        )
    lines.append("")
    lines.append("خرید: «خرید پت [نوع]»")
    await message.reply_text("\n".join(lines))


async def buy_pet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خرید پت [نوع] / /buypet <type>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_pet"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    if get_pet(chat.id, uid):
        await message.reply_text("همین الان هم یه پت داری. فعلاً فقط یه پت هم‌زمان پشتیبانی می‌شه.")
        return

    args = context.args or []
    if not args:
        names = "، ".join(SPECIES.keys())
        await message.reply_text(f"استفاده: «خرید پت [نوع]». گزینه‌ها: {names}")
        return
    name = " ".join(args).strip().lower()
    key = name if name in SPECIES else next((k for k, i in SPECIES.items() if name in i["name"].lower()), None)
    if not key:
        await message.reply_text("همچین پتی نیست. با «پت‌ها» لیست رو ببین.")
        return

    info = SPECIES[key]
    try:
        await economy_core.remove_coins(chat.id, uid, info["cost"], kind="pet_buy", note=f"خرید {info['name']}")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. قیمت: {info['cost']:,} {config.CURRENCY_NAME}")
        return

    now = time.time()
    _pets[chat.id][uid] = {
        "species": key, "level": 1, "xp": 0, "hp": float(info["hp"]),
        "last_hp_update_ts": now, "evolved": False, "wins": 0, "losses": 0,
    }
    _species_seen[chat.id][uid].add(key)
    lines = [f"🎉 {info['name']} رو به فرزندی قبول کردی! با «پت» وضعیتشو ببین."]
    try:
        import economy_achievements
        lines.extend(economy_achievements.check_all(chat.id, uid))
    except Exception as e:
        logger.warning(f"چک دستاوردهای اقتصادی ناموفق بود: {e}")
    await message.reply_text("\n".join(lines))
    await host.save_state()


async def pet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پت / /pet"""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    pet = get_pet(chat.id, uid)
    if not pet:
        await message.reply_text("هنوز پتی نداری. با «پت‌ها» لیست رو ببین و با «خرید پت [نوع]» یکی بردار.")
        return
    hp = _current_hp(chat.id, uid)
    max_hp = _max_hp(pet)
    needed = _xp_needed(pet["level"])
    total = pet["wins"] + pet["losses"]
    rate = (pet["wins"] / total * 100) if total else 0
    lines = [
        f"🐾 {_display_name(pet)}",
        f"⭐ Level {pet['level']} ({pet['xp']}/{needed} XP)",
        f"❤️ HP: {hp}/{max_hp}",
        f"⚔️ قدرت: {_power(pet):.0f}",
        f"🏆 برد/باخت: {pet['wins']}/{pet['losses']} ({rate:.0f}٪)",
    ]
    info = SPECIES[pet["species"]]
    if not pet.get("evolved") and pet["level"] >= info["evolves_at_level"]:
        lines.append("✨ آماده‌ی تکامل! با «ارتقای پت» تکاملش بده.")
    await message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# 🍖 غذا / آموزش / تکامل
# ---------------------------------------------------------------------------

async def feed_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """غذا / /feedpet"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    pet = get_pet(chat.id, uid)
    if not pet:
        await message.reply_text("پتی نداری که بهش غذا بدی.")
        return

    now = time.time()
    last = _last_feed_ts[chat.id].get(uid, 0)
    cooldown = getattr(config, "PET_FEED_COOLDOWN_MINUTES", 20) * 60
    remaining = cooldown - (now - last)
    if remaining > 0:
        await message.reply_text(f"⏳ هنوز {int(remaining // 60) + 1} دقیقه‌ی دیگه تا غذای بعدی مونده.")
        return

    cost = getattr(config, "PET_FEED_COST", 150)
    try:
        await economy_core.remove_coins(chat.id, uid, cost, kind="pet_feed", note="غذای پت")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست ({cost} {config.CURRENCY_NAME}).")
        return

    _last_feed_ts[chat.id][uid] = now
    _current_hp(chat.id, uid)  # Regen رو اول اعمال کن
    max_hp = _max_hp(pet)
    pet["hp"] = min(max_hp, pet["hp"] + getattr(config, "PET_FEED_HP_RESTORE", 40))
    await message.reply_text(f"🍖 {_display_name(pet)} غذا خورد! ❤️ HP: {round(pet['hp'])}/{max_hp}")
    await host.save_state()


async def train_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """آموزش پت / /trainpet"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    if not get_pet(chat.id, uid):
        await message.reply_text("پتی نداری که آموزشش بدی.")
        return

    now = time.time()
    last = _last_train_ts[chat.id].get(uid, 0)
    cooldown = getattr(config, "PET_TRAIN_COOLDOWN_MINUTES", 40) * 60
    remaining = cooldown - (now - last)
    if remaining > 0:
        await message.reply_text(f"⏳ هنوز {int(remaining // 60) + 1} دقیقه‌ی دیگه تا آموزش بعدی مونده.")
        return

    cost = getattr(config, "PET_TRAIN_COST", 300)
    try:
        await economy_core.remove_coins(chat.id, uid, cost, kind="pet_train", note="آموزش پت")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست ({cost} {config.CURRENCY_NAME}).")
        return

    _last_train_ts[chat.id][uid] = now
    events = await _grant_xp(chat.id, uid, getattr(config, "PET_TRAIN_XP", 30))
    lines = [f"📚 {_display_name(get_pet(chat.id, uid))} آموزش دید! (+{config.PET_TRAIN_XP} XP)"]
    lines.extend(events)
    await message.reply_text("\n".join(lines))
    await host.save_state()


async def evolve_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارتقای پت / /evolvepet"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    pet = get_pet(chat.id, uid)
    if not pet:
        await message.reply_text("پتی نداری.")
        return
    info = SPECIES[pet["species"]]
    if pet.get("evolved"):
        await message.reply_text(f"{_display_name(pet)} همین الان هم تکامل‌یافته‌ست.")
        return
    if pet["level"] < info["evolves_at_level"]:
        await message.reply_text(f"هنوز آماده نیست. نیاز به Level {info['evolves_at_level']} داره (الان: {pet['level']}).")
        return
    pet["evolved"] = True
    lines = [f"✨🎉 تکامل انجام شد! {_display_name(pet)} حالا قوی‌تره!"]
    try:
        import economy_achievements
        lines.extend(economy_achievements.check_all(chat.id, uid))
    except Exception as e:
        logger.warning(f"چک دستاوردهای اقتصادی ناموفق بود: {e}")
    await message.reply_text("\n".join(lines))
    await host.save_state()


# ---------------------------------------------------------------------------
# ⚔️ فایت (PvE + PvP)
# ---------------------------------------------------------------------------

def _daily_fight_record(chat_id: int, uid: int) -> dict:
    rec = _fight_daily[chat_id].get(uid)
    today = _today_str()
    if not rec or rec.get("date") != today:
        rec = {"date": today, "count": 0}
        _fight_daily[chat_id][uid] = rec
    return rec


async def fight_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فایت [مبلغ] / /petfight <amount> — بدون ریپلای = PvE، با ریپلای روی یه نفر که پت داره = PvP."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_pet"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    pet = get_pet(chat.id, uid)
    if not pet:
        await message.reply_text("پتی نداری که بجنگه.")
        return

    args = context.args or []
    try:
        bet = int(args[0].replace(",", "")) if args else 0
    except ValueError:
        bet = -1
    if bet < 0:
        await message.reply_text("مبلغ شرط نامعتبره.")
        return

    now = time.time()
    last = _last_fight_ts[chat.id].get(uid, 0)
    cooldown = getattr(config, "PET_FIGHT_COOLDOWN_MINUTES", 20) * 60
    remaining = cooldown - (now - last)
    if remaining > 0:
        await message.reply_text(f"⏳ پتت هنوز خسته‌ست؛ {int(remaining // 60) + 1} دقیقه‌ی دیگه مونده.")
        return

    daily = _daily_fight_record(chat.id, uid)
    limit = getattr(config, "PET_FIGHT_DAILY_LIMIT", 15)
    if daily["count"] >= limit:
        await message.reply_text(f"📆 امروز به سقف {limit} بار مبارزه رسیدی.")
        return

    hp = _current_hp(chat.id, uid)
    max_hp = _max_hp(pet)
    min_fraction = getattr(config, "PET_FIGHT_MIN_HP_FRACTION", 0.2)
    if hp < max_hp * min_fraction:
        await message.reply_text(f"😿 {_display_name(pet)} خیلی خسته‌ست (HP: {hp}/{max_hp})؛ اول با «غذا» بهش برس.")
        return

    target_id = host._resolve_target_id(message)
    opponent_pet = get_pet(chat.id, target_id) if target_id and target_id != uid else None

    _last_fight_ts[chat.id][uid] = now
    daily["count"] += 1

    if opponent_pet:
        await _resolve_pvp(update, uid, target_id, pet, opponent_pet, bet)
    else:
        await _resolve_pve(update, uid, pet, bet)
    await host.save_state()


async def _resolve_pve(update: Update, uid: int, pet: dict, bet: int) -> None:
    chat = update.effective_chat
    message = update.effective_message
    if bet > 0:
        try:
            await economy_core.remove_coins(chat.id, uid, bet, kind="pet_fight_stake", note="شرط فایت پت")
        except economy_core.InsufficientFundsError:
            await message.reply_text(f"❌ موجودی کافی نیست برای شرط {bet}.")
            return

    my_power = _power(pet) * random.uniform(0.8, 1.2)
    enemy_power = _power(pet) * random.uniform(0.6, 1.15) * random.uniform(0.9, 1.3)  # هیولای وحشی تصادفی
    win = my_power >= enemy_power

    damage_fraction = random.uniform(config.PET_FIGHT_DAMAGE_MIN_FRACTION, config.PET_FIGHT_DAMAGE_MAX_FRACTION)
    max_hp = _max_hp(pet)
    pet["hp"] = max(0.0, pet["hp"] - max_hp * damage_fraction * (0.5 if win else 1.0))
    pet["last_hp_update_ts"] = time.time()

    lines = []
    if win:
        pet["wins"] += 1
        payout = bet * 2 if bet > 0 else 0
        reward = round(getattr(config, "PET_FIGHT_XP_WIN", 40) * 3) if bet == 0 else 0  # اگه شرط نبست، یکم سکه‌ی پایه بده
        total_payout = payout + reward
        if total_payout > 0:
            await economy_core.add_coins(chat.id, uid, total_payout, kind="pet_fight_win", note="برد فایت پت")
        lines.append(f"🏆 {_display_name(pet)} برد!" + (f" +{total_payout} {config.CURRENCY_EMOJI}" if total_payout else ""))
        events = await _grant_xp(chat.id, uid, getattr(config, "PET_FIGHT_XP_WIN", 40))
    else:
        pet["losses"] += 1
        lines.append(f"😿 {_display_name(pet)} باخت." + (f" شرط {bet} از دست رفت." if bet > 0 else ""))
        events = await _grant_xp(chat.id, uid, getattr(config, "PET_FIGHT_XP_LOSS", 10))

    lines.append(f"❤️ HP: {round(pet['hp'])}/{max_hp}")
    lines.extend(events)
    try:
        lines += await economy_missions.record_progress(chat.id, uid, "pet_fight", 1)
    except Exception as e:
        logger.warning(f"ثبت پیشرفت ماموریت pet_fight ناموفق بود: {e}")
    await message.reply_text("\n".join(lines))


async def _resolve_pvp(update: Update, uid: int, opponent_id: int, pet: dict, opponent_pet: dict, bet: int) -> None:
    chat = update.effective_chat
    message = update.effective_message

    if bet > 0:
        if not economy_core.can_afford(chat.id, uid, bet) or not economy_core.can_afford(chat.id, opponent_id, bet):
            await message.reply_text("برای PvP با شرط، هر دو طرف باید اون‌قدر موجودی داشته باشن.")
            return
        await economy_core.remove_coins(chat.id, uid, bet, kind="pet_pvp_stake", note="شرط PvP")
        await economy_core.remove_coins(chat.id, opponent_id, bet, kind="pet_pvp_stake", note="شرط PvP")

    my_power = _power(pet) * random.uniform(0.85, 1.15)
    their_power = _power(opponent_pet) * random.uniform(0.85, 1.15)
    i_win = my_power >= their_power

    max_hp_me = _max_hp(pet)
    max_hp_them = _max_hp(opponent_pet)
    dmg_frac = random.uniform(config.PET_FIGHT_DAMAGE_MIN_FRACTION, config.PET_FIGHT_DAMAGE_MAX_FRACTION)
    pet["hp"] = max(0.0, pet["hp"] - max_hp_me * dmg_frac * (0.5 if i_win else 1.0))
    opponent_pet["hp"] = max(0.0, opponent_pet["hp"] - max_hp_them * dmg_frac * (0.5 if not i_win else 1.0))
    pet["last_hp_update_ts"] = time.time()
    opponent_pet["last_hp_update_ts"] = time.time()

    if i_win:
        pet["wins"] += 1
        opponent_pet["losses"] += 1
        winner_id, loser_id = uid, opponent_id
    else:
        opponent_pet["wins"] += 1
        pet["losses"] += 1
        winner_id, loser_id = opponent_id, uid

    if bet > 0:
        await economy_core.add_coins(chat.id, winner_id, bet * 2, kind="pet_pvp_win", counterparty_id=loser_id, note="برد PvP")

    win_events = await _grant_xp(chat.id, winner_id, getattr(config, "PET_FIGHT_XP_WIN", 40))
    lose_events = await _grant_xp(chat.id, loser_id, getattr(config, "PET_FIGHT_XP_LOSS", 10))

    winner_pet = get_pet(chat.id, winner_id)
    lines = [
        f"⚔️ PvP: {_display_name(pet)} در برابر {_display_name(opponent_pet)}",
        f"🏆 برنده: {_display_name(winner_pet)}" + (f" (+{bet*2} {config.CURRENCY_EMOJI})" if bet > 0 else ""),
        f"❤️ HP تو: {round(pet['hp'])}/{max_hp_me} — ❤️ HP حریف: {round(opponent_pet['hp'])}/{max_hp_them}",
    ]
    lines.extend(win_events if winner_id == uid else lose_events)
    try:
        lines += await economy_missions.record_progress(chat.id, uid, "pet_fight", 1)
    except Exception as e:
        logger.warning(f"ثبت پیشرفت ماموریت pet_fight ناموفق بود: {e}")
    await message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "pets": {str(c): dict(v) for c, v in _pets.items()},
        "last_feed_ts": {str(c): dict(v) for c, v in _last_feed_ts.items()},
        "last_train_ts": {str(c): dict(v) for c, v in _last_train_ts.items()},
        "last_fight_ts": {str(c): dict(v) for c, v in _last_fight_ts.items()},
        "fight_daily": {str(c): {str(u): d for u, d in v.items()} for c, v in _fight_daily.items()},
        "species_seen": {str(c): {str(u): list(s) for u, s in v.items()} for c, v in _species_seen.items()},
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
            logger.warning(f"ذخیره‌ی state موتور Pet ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور Pet ناموفق بود: {e}")
        return
    for cid, v in data.get("pets", {}).items():
        _pets[int(cid)] = {int(u): p for u, p in v.items()}
    for cid, v in data.get("last_feed_ts", {}).items():
        _last_feed_ts[int(cid)] = {int(u): ts for u, ts in v.items()}
    for cid, v in data.get("last_train_ts", {}).items():
        _last_train_ts[int(cid)] = {int(u): ts for u, ts in v.items()}
    for cid, v in data.get("last_fight_ts", {}).items():
        _last_fight_ts[int(cid)] = {int(u): ts for u, ts in v.items()}
    for cid, v in data.get("fight_daily", {}).items():
        _fight_daily[int(cid)] = {int(u): d for u, d in v.items()}
    for cid, v in data.get("species_seen", {}).items():
        _species_seen[int(cid)] = defaultdict(set, {int(u): set(s) for u, s in v.items()})
    # سازگاری با داده‌ی قدیمی: اگه یه کاربر پت فعال داره ولی توی species_seen
    # نیست (چون قبل از این ارتقا ساخته شده)، گونه‌ی فعلیش رو اضافه کن.
    for cid, users in _pets.items():
        for uid, pet in users.items():
            if pet and pet.get("species"):
                _species_seen[cid][uid].add(pet["species"])
    logger.info("وضعیت موتور Pet از فایل بارگذاری شد.")
