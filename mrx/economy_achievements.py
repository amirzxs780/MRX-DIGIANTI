# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY ACHIEVEMENTS + TITLES — فاز ۸ (Module 19 + Module 21)
===========================================================================
هیچ سیستم Achievement جدیدی نمی‌سازه — از همون موتور فعلی profile_engine
(که `bot._achievements[chat_id][uid]` رو مدیریت می‌کنه) استفاده می‌کنه، دقیقاً
مثل الگوی on_poll_created/on_shop_purchase که از قبل توی profile_engine.py
هست. این فایل فقط شرط‌های اقتصادی (Job Level/Property/City/Pet/...) رو چک
می‌کنه و در صورت برآورده‌شدن، با profile_engine._unlock آزادشون می‌کنه.

Titleها (Module 21) هم چیزی رو Persist نمی‌کنن؛ فقط لحظه‌ای از Net Worth
فعلی (economy_core.get_net_worth) محاسبه می‌شن — سبک و همیشه به‌روز.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

import config
import economy_core
import profile_engine

logger = logging.getLogger("economy_achievements")


def economy_title_for(chat_id: int, uid: int) -> str | None:
    """بالاترین Title اقتصادی که کاربر با Net Worth فعلیش شایسته‌شه (یا None)."""
    net_worth = economy_core.get_net_worth(chat_id, uid)
    for threshold, title in getattr(config, "ECONOMY_TITLES", []):
        if net_worth >= threshold:
            return title
    return None


def _unlock_many(chat_id: int, uid: int, keys: list[str]) -> list[str]:
    newly = [k for k in keys if profile_engine._unlock(chat_id, uid, k)]
    if newly:
        profile_engine.grant_rewards_for_unlocked(chat_id, uid, newly)
    return newly


def check_all(chat_id: int, uid: int) -> list[str]:
    """همه‌ی دستاوردهای اقتصادی رو چک می‌کنه؛ لیست متن‌های تبریکِ تازه‌آزادشده رو
    برمی‌گردونه (Fail-Safe کامل — هیچ‌وقت Exception به بیرون نمی‌ده)."""
    to_unlock: list[str] = []
    try:
        import economy_jobs
        key = economy_jobs.current_job_key(chat_id, uid)
        if key:
            to_unlock.append("econ_first_job")
            total_level = economy_jobs.total_job_level(chat_id, uid)
            if total_level >= 10:
                to_unlock.append("econ_job_level_10")
    except Exception as e:
        logger.warning(f"چک دستاورد Job ناموفق بود: {e}")

    try:
        import economy_property
        if economy_property.owned(chat_id, uid):
            to_unlock.append("econ_first_property")
    except Exception as e:
        logger.warning(f"چک دستاورد Property ناموفق بود: {e}")

    try:
        import economy_city
        if economy_city.has_city(chat_id, uid):
            to_unlock.append("econ_first_city")
    except Exception as e:
        logger.warning(f"چک دستاورد City ناموفق بود: {e}")

    try:
        import economy_pet
        pet = economy_pet.get_pet(chat_id, uid)
        if pet:
            to_unlock.append("econ_first_pet")
            if pet.get("evolved"):
                to_unlock.append("econ_pet_evolved")
    except Exception as e:
        logger.warning(f"چک دستاورد Pet ناموفق بود: {e}")

    try:
        import economy_marriage
        if economy_marriage.is_married(chat_id, uid):
            to_unlock.append("econ_first_marriage")
    except Exception as e:
        logger.warning(f"چک دستاورد Marriage ناموفق بود: {e}")

    try:
        import economy_market
        positions = economy_market._portfolio[chat_id][uid]
        if any(p.get("qty", 0) > 0 for p in positions.values()):
            to_unlock.append("econ_first_investment")
        stats = economy_market._trading[chat_id].get(uid)
        if stats and stats.get("total_profit", 0) > 0:
            to_unlock.append("econ_first_trade_win")
    except Exception as e:
        logger.warning(f"چک دستاورد Market ناموفق بود: {e}")

    try:
        import economy_theft
        stats = economy_theft._thief_stats[chat_id].get(uid)
        if stats:
            if stats.get("successes", 0) >= 1:
                to_unlock.append("econ_first_theft")
            if stats.get("level", 1) >= 10:
                to_unlock.append("econ_thief_level_10")
    except Exception as e:
        logger.warning(f"چک دستاورد Theft ناموفق بود: {e}")

    try:
        import economy_underground
        stats = economy_underground._stats[chat_id].get(uid)
        if stats and stats.get("level", 1) >= 10:
            to_unlock.append("econ_underground_10")
    except Exception as e:
        logger.warning(f"چک دستاورد Underground ناموفق بود: {e}")

    try:
        import economy_bank
        max_level = max(config.BANK_LEVELS.keys())
        if economy_bank._level(chat_id, uid) >= max_level:
            to_unlock.append("econ_bank_level_5")
    except Exception as e:
        logger.warning(f"چک دستاورد Bank ناموفق بود: {e}")

    try:
        net_worth = economy_core.get_net_worth(chat_id, uid)
        for ach_key, threshold in getattr(config, "ECONOMY_NET_WORTH_THRESHOLDS", {}).items():
            if net_worth >= threshold:
                to_unlock.append(ach_key)
    except Exception as e:
        logger.warning(f"چک دستاورد Net Worth ناموفق بود: {e}")

    newly = _unlock_many(chat_id, uid, to_unlock)
    labels = getattr(config, "ECONOMY_ACHIEVEMENTS", {})
    return [f"🏆 دستاورد جدید: {labels.get(k, {}).get('label', k)}" for k in newly]


async def networth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارزش خالص / /networth"""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    wallet = economy_core.get_wallet(chat.id, uid)
    bank = economy_core.get_bank_balance(chat.id, uid)
    net_worth = economy_core.get_net_worth(chat.id, uid)
    other = net_worth - wallet - bank
    title = economy_title_for(chat.id, uid)

    lines = [
        "💎 ارزش خالص تو",
        f"💰 کیف پول: {wallet:,} {config.CURRENCY_NAME}",
        f"🏦 بانک: {bank:,} {config.CURRENCY_NAME}",
        f"📊 سایر دارایی‌ها (سرمایه‌گذاری/ملک/شهر): {other:,} {config.CURRENCY_NAME}",
        f"💎 مجموع: {net_worth:,} {config.CURRENCY_NAME}",
    ]
    if title:
        lines.append(f"👑 عنوان: {title}")

    events = check_all(chat.id, uid)
    lines.extend(events)
    await message.reply_text("\n".join(lines))
