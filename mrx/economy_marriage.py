# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY MARRIAGE — فاز ۶ (Module 12)
==================================================
ازدواج دوطرفه‌ست: «درخواست ازدواج» (ریپلای روی هدف) یه Proposal می‌سازه،
هدف باید با «ازدواج» (ریپلای روی پیشنهاددهنده) قبولش کنه. «خیانت» طبق
دستورالعمل شما پیاده‌سازی نشده (صراحتاً گفته بودید اگه پیاده بشه باید صرفاً
فانتزی/داستانی باشه؛ برای این فاز از پیچیده‌کردن بیشتر پرهیز کردم — در فاز
بعد در صورت درخواست اضافه می‌شه).
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
import economy_missions

logger = logging.getLogger("economy_marriage")

STATE_FILE = getattr(config, "ECONOMY_MARRIAGE_STATE_FILE", "economy_marriage_state.json")

_save_lock = asyncio.Lock()

# chat_id -> uid -> partner_uid (دوطرفه؛ هر دو سمت ثبت می‌شن)
_spouse = defaultdict(dict)
# chat_id -> couple_key ("min_max") -> {"level","xp","married_ts","gifts_count"}
_couples = defaultdict(dict)
# chat_id -> proposer_id -> {"target_id","ts"}
_pending_proposals = defaultdict(dict)
# chat_id -> uid -> ts
_last_divorce_ts = defaultdict(dict)


def _host():
    import bot as host
    return host


def _couple_key(a: int, b: int) -> str:
    return f"{min(a, b)}_{max(a, b)}"


def spouse_of(chat_id: int, uid: int) -> int | None:
    return _spouse[chat_id].get(uid)


def is_married(chat_id: int, uid: int) -> bool:
    return spouse_of(chat_id, uid) is not None


def _couple(chat_id: int, a: int, b: int) -> dict:
    key = _couple_key(a, b)
    rec = _couples[chat_id].get(key)
    if not rec:
        rec = {"level": 1, "xp": 0, "married_ts": time.time(), "gifts_count": 0}
        _couples[chat_id][key] = rec
    return rec


def _xp_needed(level: int) -> int:
    return getattr(config, "MARRIAGE_XP_PER_LEVEL_BASE", 200) * level


# ---------------------------------------------------------------------------
# 💍 درخواست ازدواج
# ---------------------------------------------------------------------------

async def propose_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """درخواست ازدواج (ریپلای) / /propose"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_marriage"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    if is_married(chat.id, uid):
        await message.reply_text("تو که همین الان هم متأهلی 😄 اول «طلاق» بگیر.")
        return

    last_divorce = _last_divorce_ts[chat.id].get(uid, 0)
    cooldown_s = getattr(config, "MARRIAGE_DIVORCE_COOLDOWN_HOURS", 12) * 3600
    remaining = cooldown_s - (time.time() - last_divorce)
    if remaining > 0:
        await message.reply_text(f"⏳ بعد از طلاق باید {int(remaining // 3600) + 1} ساعت دیگه صبر کنی.")
        return

    target_id = host._resolve_target_id(message)
    if not target_id:
        await message.reply_text("باید روی پیام یه نفر ریپلای کنی: «درخواست ازدواج» (ریپلای).")
        return
    if target_id == uid:
        await message.reply_text("نمی‌تونی به خودت پیشنهاد بدی 😅")
        return
    if is_married(chat.id, target_id):
        await message.reply_text("طرف مقابل قبلاً متأهله.")
        return

    cost = getattr(config, "MARRIAGE_RING_COST", 5000)
    try:
        await economy_core.remove_coins(chat.id, uid, cost, kind="marriage_proposal", note="هزینه‌ی حلقه و مراسم ازدواج")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ برای درخواست ازدواج به {cost} {config.CURRENCY_NAME} نیاز داری (حلقه + مراسم).")
        return

    _pending_proposals[chat.id][uid] = {"target_id": target_id, "ts": time.time()}
    await message.reply_text(
        f"💍 درخواست ازدواج ثبت شد! طرف مقابل باید روی همین پیام تو ریپلای کنه و بنویسه «ازدواج» تا قبول کنه.\n"
        f"⏳ این پیشنهاد تا {getattr(config, 'MARRIAGE_PROPOSAL_EXPIRY_HOURS', 24)} ساعت دیگه معتبره."
    )
    await host.save_state()


async def marry_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ازدواج (ریپلای روی پیشنهاددهنده، برای قبول کردن) / /marry"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return

    uid = update.effective_user.id
    if is_married(chat.id, uid):
        await message.reply_text("تو که همین الان هم متأهلی 😄")
        return

    proposer_id = host._resolve_target_id(message)
    if not proposer_id:
        await message.reply_text(
            "برای قبول کردن یه درخواست، باید روی پیام «درخواست ازدواج» طرف مقابل ریپلای کنی و بنویسی «ازدواج»."
        )
        return

    proposal = _pending_proposals[chat.id].get(proposer_id)
    if not proposal or proposal["target_id"] != uid:
        await message.reply_text("همچین درخواست ازدواجی برای تو پیدا نشد.")
        return

    expiry_s = getattr(config, "MARRIAGE_PROPOSAL_EXPIRY_HOURS", 24) * 3600
    if time.time() - proposal["ts"] > expiry_s:
        del _pending_proposals[chat.id][proposer_id]
        await message.reply_text("این درخواست منقضی شده. باید دوباره درخواست بده.")
        return

    del _pending_proposals[chat.id][proposer_id]
    _spouse[chat.id][uid] = proposer_id
    _spouse[chat.id][proposer_id] = uid
    _couple(chat.id, uid, proposer_id)  # رکورد Couple رو بساز

    lines = ["💒 تبریک! ازدواج رسمی شد! 🎉 با «همسر» می‌تونید وضعیتتون رو ببینید."]
    try:
        import economy_achievements
        lines.extend(economy_achievements.check_all(chat.id, uid))
        lines.extend(economy_achievements.check_all(chat.id, proposer_id))
    except Exception as e:
        logger.warning(f"چک دستاوردهای اقتصادی ناموفق بود: {e}")
    await message.reply_text("\n".join(lines))
    await host.save_state()


async def partner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """همسر / /partner"""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    partner_id = spouse_of(chat.id, uid)
    if not partner_id:
        await message.reply_text("😔 هنوز ازدواج نکردی. با «درخواست ازدواج» (ریپلای روی یه نفر) شروع کن.")
        return
    couple = _couple(chat.id, uid, partner_id)
    needed = _xp_needed(couple["level"])
    married_days = int((time.time() - couple["married_ts"]) / 86400)
    lines = [
        "💍 وضعیت ازدواج تو",
        f"👤 همسر: {partner_id}",
        f"💞 Couple Level: {couple['level']} ({couple['xp']}/{needed} XP)",
        f"🎁 تعداد هدیه‌ها: {couple['gifts_count']}",
        f"📅 مدت ازدواج: {married_days} روز",
    ]
    await message.reply_text("\n".join(lines))


async def divorce_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلاق / /divorce"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    partner_id = spouse_of(chat.id, uid)
    if not partner_id:
        await message.reply_text("مجرد هستی؛ طلاقی در کار نیست.")
        return
    del _spouse[chat.id][uid]
    del _spouse[chat.id][partner_id]
    now = time.time()
    _last_divorce_ts[chat.id][uid] = now
    _last_divorce_ts[chat.id][partner_id] = now
    await message.reply_text("💔 طلاق ثبت شد. قبل از ازدواج بعدی باید یه مدت صبر کنی.")
    await host.save_state()


async def gift_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هدیه [مبلغ] / /gift <amount> — به همسر."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_marriage"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    partner_id = spouse_of(chat.id, uid)
    if not partner_id:
        await message.reply_text("برای هدیه دادن باید ازدواج کرده باشی.")
        return

    args = context.args or []
    try:
        amount = int(args[0].replace(",", "")) if args else -1
    except ValueError:
        amount = -1
    if amount <= 0:
        await message.reply_text("استفاده: «هدیه [مبلغ]»")
        return

    try:
        await economy_core.transfer_coins(chat.id, uid, partner_id, amount, note="هدیه به همسر", kind="marriage_gift")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست ({economy_core.get_wallet(chat.id, uid)} {config.CURRENCY_NAME}).")
        return

    couple = _couple(chat.id, uid, partner_id)
    xp_gain = round(amount / 1000 * getattr(config, "MARRIAGE_GIFT_XP_PER_1000_COINS", 10))
    couple["xp"] += xp_gain
    couple["gifts_count"] += 1
    level_events = []
    while couple["xp"] >= _xp_needed(couple["level"]):
        couple["xp"] -= _xp_needed(couple["level"])
        couple["level"] += 1
        level_events.append(f"💞 Couple Level رفت {couple['level']}!")

    lines = [f"🎁 {amount} {config.CURRENCY_NAME} به همسرت هدیه دادی! (+{xp_gain} Couple XP)"]
    lines.extend(level_events)
    await message.reply_text("\n".join(lines))
    await host.save_state()


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "spouse": {str(c): dict(v) for c, v in _spouse.items()},
        "couples": {str(c): dict(v) for c, v in _couples.items()},
        "pending_proposals": {str(c): dict(v) for c, v in _pending_proposals.items()},
        "last_divorce_ts": {str(c): dict(v) for c, v in _last_divorce_ts.items()},
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
            logger.warning(f"ذخیره‌ی state موتور Marriage ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور Marriage ناموفق بود: {e}")
        return
    for cid, v in data.get("spouse", {}).items():
        _spouse[int(cid)] = {int(u): int(p) for u, p in v.items()}
    for cid, v in data.get("couples", {}).items():
        _couples[int(cid)] = v
    for cid, v in data.get("pending_proposals", {}).items():
        _pending_proposals[int(cid)] = {int(u): p for u, p in v.items()}
    for cid, v in data.get("last_divorce_ts", {}).items():
        _last_divorce_ts[int(cid)] = {int(u): ts for u, ts in v.items()}
    logger.info("وضعیت موتور Marriage از فایل بارگذاری شد.")
