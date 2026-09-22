# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY LEADERBOARDS — فاز ۸ (Module 23)
======================================================
هیچ State جدیدی نگه نمی‌داره — مستقیم از دیکشنری‌های per-user موجود توی
بقیه‌ی ماژول‌های Economy می‌خونه و لحظه‌ای Rank می‌سازه. برای گروه‌های خیلی
بزرگ ممکنه کند بشه، ولی برای یه Bot گروه شخصی (طبق درخواست خودتون) کاملاً
کافیه؛ اگه لازم شد، فازهای بعد می‌تونن Cache روش اضافه کنن.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

import config
import economy_core

logger = logging.getLogger("economy_leaderboard")

CATEGORY_ALIASES = {
    "ثروت": "wealth", "بانک": "bank", "ترید": "trading", "تریدر": "trading",
    "شغل": "job", "شهر": "city", "املاک": "property", "پت": "pet",
    "دزد": "theft", "بازی": "game", "رپ": "reputation", "رپیوتیشن": "reputation",
    "دستاورد": "achievement",
}


def _known_users(chat_id: int) -> set[int]:
    host = economy_core._host()
    return set(host._wallet.get(chat_id, {}).keys())


def _category_values(chat_id: int, category: str) -> dict[int, float]:
    """uid -> عدد رتبه‌بندی، بسته به Category. هر بخش کاملاً Fail-Safe (اگه ماژول
    مربوطه در دسترس نبود یا خطا داد، دیکشنری خالی برمی‌گردونه، کل سیستم کرش نمی‌کنه)."""
    try:
        if category == "wealth":
            return {uid: economy_core.get_net_worth(chat_id, uid) for uid in _known_users(chat_id)}
        if category == "bank":
            return {uid: economy_core.get_bank_balance(chat_id, uid) for uid in _known_users(chat_id)}
        if category == "trading":
            import economy_market
            return {uid: s.get("total_profit", 0) for uid, s in economy_market._trading[chat_id].items()}
        if category == "job":
            import economy_jobs
            return {uid: economy_jobs.total_job_level(chat_id, uid) for uid in economy_jobs._job_progress[chat_id]}
        if category == "city":
            import economy_city
            return {uid: economy_city.city_value(c) for uid, c in economy_city._cities[chat_id].items()}
        if category == "property":
            import economy_property
            return {uid: economy_property.get_property_value(chat_id, uid) for uid in economy_property._properties[chat_id]}
        if category == "pet":
            import economy_pet
            return {uid: p.get("wins", 0) for uid, p in economy_pet._pets[chat_id].items()}
        if category == "theft":
            import economy_theft
            return {uid: s.get("successes", 0) for uid, s in economy_theft._thief_stats[chat_id].items()}
        if category == "game":
            import economy_games
            return {uid: s.get("wins", 0) for uid, s in economy_games._stats[chat_id].items()}
        if category == "reputation":
            import profile_engine
            return dict(profile_engine._reputation[chat_id])
        if category == "achievement":
            host = economy_core._host()
            return {uid: len(s) for uid, s in host._achievements.get(chat_id, {}).items()}
    except Exception as e:
        logger.warning(f"محاسبه‌ی رتبه‌بندی {category} ناموفق بود: {e}")
    return {}


def _format_board(host, chat_id: int, values: dict[int, float], limit: int) -> list[str]:
    ranking = sorted(values.items(), key=lambda kv: kv[1], reverse=True)
    ranking = [(uid, v) for uid, v in ranking if v > 0][:limit]
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, value) in enumerate(ranking):
        name = host._user_display_names.get(uid, str(uid))
        prefix = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{prefix} {name}: {value:,.0f}")
    return lines


async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """برترین‌ها [نوع] / /leaderboard <category>"""
    host = _host_module()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not host.get_setting(chat.id, "economy_enabled"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    args = context.args or []
    category = "wealth"
    if args:
        arg = args[0].strip()
        category = CATEGORY_ALIASES.get(arg, arg if arg in config.LEADERBOARD_CATEGORIES else "wealth")

    label = config.LEADERBOARD_CATEGORIES.get(category, "💰 ثروتمندترین")
    values = _category_values(chat.id, category)
    limit = getattr(config, "LEADERBOARD_DISPLAY_COUNT", 10)
    lines = [label, ""]
    board = _format_board(host, chat.id, values, limit)
    lines.extend(board if board else ["هنوز کسی توی این رده‌بندی نیست."])
    lines.append("")
    names = "، ".join(CATEGORY_ALIASES.keys())
    lines.append(f"دسته‌های دیگه: «برترین‌ها [نوع]» — مثلاً {names}")
    await message.reply_text("\n".join(lines))


async def richest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ثروتمندان / /richest"""
    context.args = []
    await leaderboard_command(update, context)


async def my_rank_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رتبه اقتصاد / /myrank — رتبه‌ی شخصی توی چند دسته‌ی کلیدی."""
    host = _host_module()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id

    lines = ["📊 رتبه‌ی تو توی اقتصاد گروه:", ""]
    for category in ("wealth", "job", "trading", "theft", "pet", "achievement"):
        values = _category_values(chat.id, category)
        if uid not in values or values[uid] <= 0:
            continue
        ranking = sorted(values.items(), key=lambda kv: kv[1], reverse=True)
        rank = next((i + 1 for i, (u, _v) in enumerate(ranking) if u == uid), None)
        label = config.LEADERBOARD_CATEGORIES.get(category, category)
        lines.append(f"{label}: رتبه‌ی {rank} از {len(ranking)} (مقدار: {values[uid]:,.0f})")

    if len(lines) <= 2:
        lines.append("هنوز توی هیچ رده‌بندی‌ای جایی نداری؛ شروع کن!")
    await message.reply_text("\n".join(lines))


def _host_module():
    import bot as host
    return host
