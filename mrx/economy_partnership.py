# -*- coding: utf-8 -*-
"""
DIGNANTI ECONOMY PARTNERSHIP — فاز ۹ (V18: قراردادهایِ شراکت)
=====================================================================
بخشِ ۱۱ («شراکت در کسب‌وکار»، «تعیین سهمِ شرکا»، «تقسیمِ سود»). این با
economy_stock (فازِ۳ب) فرق داره: اونجا باید کسب‌وکار IPO بشه (حداقل‌ارزش،
سهامِ قابلِ‌معامله در بازار)؛ اینجا مالک مستقیم و خصوصی بخشی از سودِ
*همین کسب‌وکارِ مشخص* رو به یه کاربرِ معین پیشنهاد می‌ده - بدونِ عمومی‌شدن.

پذیرشِ دوطرفه (طبقِ خواسته‌ی صریحِ بخشِ۱۱ «برای جلوگیری از سوءاستفاده،
پذیرشِ دوطرفه ... ضروری است»): پیشنهاد فقط وقتی روی کسب‌وکار اعمال می‌شه
که خودِ شریکِ پیشنهادی صریحاً قبول کنه؛ تا اون موقع هیچ تغییری در
economy_business اعمال نشده.

بعد از پذیرش، economy_business._collect_one (فازِ۹: یه هوکِ کوچیک) خودکار
سودِ هر جمع‌آوری رو طبقِ این درصدها تقسیم می‌کنه - جایی که هیچ‌کس مجبور
نیست دستی «تقسیم سود» بزنه (برخلافِ سهامِ economy_stock که دستیه).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

import config

logger = logging.getLogger("economy_partnership")

STATE_FILE = getattr(config, "ECONOMY_PARTNERSHIP_STATE_FILE", "economy_partnership_state.json")
STATE_VERSION = 1

_save_lock = asyncio.Lock()


class PartnershipError(Exception):
    pass


# chat_id -> proposal_id(int) -> {id, owner_uid, business_id, partner_uid,
#   equity, status: pending|accepted|rejected|cancelled, created_ts}
_proposals: dict[int, dict] = defaultdict(dict)
_next_proposal_id: dict[int, int] = defaultdict(int)


def _owner_business(chat_id: int, owner_uid: int, business_id: int):
    import economy_business
    return next((b for b in economy_business.owned(chat_id, owner_uid) if b["id"] == business_id), None)


def _pending_count_for(chat_id: int, owner_uid: int) -> int:
    return sum(
        1 for p in _proposals[chat_id].values()
        if p["owner_uid"] == owner_uid and p["status"] == "pending"
    )


def propose(chat_id: int, owner_uid: int, business_id: int, partner_uid: int, equity_percent: float) -> dict:
    if owner_uid == partner_uid:
        raise PartnershipError("نمی‌تونی به خودت شراکت پیشنهاد بدی.")
    b = _owner_business(chat_id, owner_uid, business_id)
    if not b:
        raise PartnershipError("این کسب‌وکار پیدا نشد (مطمئن شو شناسه درسته و مالکشی).")
    if equity_percent <= 0 or equity_percent >= 1:
        raise PartnershipError("درصدِ سهم باید بینِ ۰ تا ۱۰۰ باشه.")

    existing = b.get("partners", {})
    taken = sum(existing.values())
    min_owner_equity = getattr(config, "PARTNERSHIP_MIN_OWNER_EQUITY", 0.10)
    if (1 - taken - equity_percent) < min_owner_equity:
        max_available = 1 - taken - min_owner_equity
        raise PartnershipError(
            f"این سهم زیاده؛ مالک باید حداقل {min_owner_equity*100:.0f}٪ نگه داره. "
            f"حداکثرِ قابلِ‌پیشنهاد الان: {max(0, max_available)*100:.1f}٪"
        )

    max_partners = getattr(config, "PARTNERSHIP_MAX_PARTNERS_PER_BUSINESS", 5)
    if str(partner_uid) not in existing and len(existing) >= max_partners:
        raise PartnershipError(f"این کسب‌وکار از قبل {max_partners} شریک داره.")

    max_pending = getattr(config, "PARTNERSHIP_MAX_PENDING_PER_USER", 5)
    if _pending_count_for(chat_id, owner_uid) >= max_pending:
        raise PartnershipError(f"حداکثر {max_pending} پیشنهادِ معلقِ هم‌زمان مجازه.")

    proposal_id = _next_proposal_id[chat_id] + 1
    _next_proposal_id[chat_id] = proposal_id
    proposal = {
        "id": proposal_id, "owner_uid": owner_uid, "business_id": business_id,
        "partner_uid": partner_uid, "equity": equity_percent,
        "status": "pending", "created_ts": time.time(),
    }
    _proposals[chat_id][proposal_id] = proposal
    return proposal


def accept(chat_id: int, partner_uid: int, proposal_id: int) -> dict:
    p = _proposals[chat_id].get(proposal_id)
    if not p or p["status"] != "pending":
        raise PartnershipError("این پیشنهاد دیگه معتبر نیست.")
    if p["partner_uid"] != partner_uid:
        raise PartnershipError("این پیشنهاد برایِ تو نیست.")

    b = _owner_business(chat_id, p["owner_uid"], p["business_id"])
    if not b:
        p["status"] = "cancelled"
        raise PartnershipError("کسب‌وکارِ موردنظر دیگه وجود نداره.")

    partners = b.setdefault("partners", {})
    partners[str(partner_uid)] = p["equity"]
    p["status"] = "accepted"
    return p


def reject(chat_id: int, partner_uid: int, proposal_id: int) -> dict:
    p = _proposals[chat_id].get(proposal_id)
    if not p or p["status"] != "pending":
        raise PartnershipError("این پیشنهاد دیگه معتبر نیست.")
    if p["partner_uid"] != partner_uid:
        raise PartnershipError("این پیشنهاد برایِ تو نیست.")
    p["status"] = "rejected"
    return p


def cancel(chat_id: int, owner_uid: int, proposal_id: int) -> dict:
    p = _proposals[chat_id].get(proposal_id)
    if not p or p["status"] != "pending":
        raise PartnershipError("این پیشنهاد دیگه معتبر نیست.")
    if p["owner_uid"] != owner_uid:
        raise PartnershipError("فقط پیشنهاددهنده می‌تونه لغوش کنه.")
    p["status"] = "cancelled"
    return p


def pending_for(chat_id: int, partner_uid: int) -> list[dict]:
    return [p for p in _proposals[chat_id].values() if p["partner_uid"] == partner_uid and p["status"] == "pending"]


def my_proposals(chat_id: int, uid: int) -> list[dict]:
    return [p for p in _proposals[chat_id].values() if p["owner_uid"] == uid or p["partner_uid"] == uid]


# ---------------------------------------------------------------------------
# 🖊 نمایش متنی
# ---------------------------------------------------------------------------

def format_pending_text(chat_id: int, uid: int) -> str:
    rows = pending_for(chat_id, uid)
    lines = ["🤝 پیشنهادهایِ شراکتِ من", "─────────────────"]
    if not rows:
        lines.append("هیچ پیشنهادِ شراکتی برایِ تو نیست.")
        return "\n".join(lines)
    for p in rows:
        lines.append(f"#{p['id']} شراکت در کسب‌وکارِ #{p['business_id']} با سهمِ {p['equity']*100:.1f}٪")
    lines.append("")
    lines.append("قبول: «قبول شراکت [شناسه]» — رد: «رد شراکت [شناسه]»")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 📨 Command Handlers
# ---------------------------------------------------------------------------

def _host():
    import bot as host
    return host


def _group_only_or_none(update: Update):
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return None
    return chat


async def propose_partnership_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شراکت در کسب‌وکار [شناسه کسب‌وکار] [درصد] (با Reply رویِ شریکِ پیشنهادی)"""
    host = _host()
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not message.reply_to_message:
        await message.reply_text(
            "برایِ پیشنهادِ شراکت، رویِ پیامِ طرفِ‌مقابل Reply کن و بنویس: "
            "«شراکت در کسب‌وکار [شناسه] [درصد]»"
        )
        return
    args = context.args or []
    if len(args) < 2:
        await message.reply_text("استفاده: (با Reply) «شراکت در کسب‌وکار [شناسه] [درصد]»")
        return
    try:
        business_id = int(args[0])
        percent = float(args[1].replace("%", ""))
    except ValueError:
        await message.reply_text("شناسه/درصد نامعتبره.")
        return
    owner_uid = update.effective_user.id
    partner_uid = message.reply_to_message.from_user.id
    try:
        p = propose(chat.id, owner_uid, business_id, partner_uid, percent / 100)
    except PartnershipError as e:
        await message.reply_text(f"❌ {e}")
        return
    await message.reply_text(
        f"🤝 پیشنهادِ شراکتِ #{p['id']} ثبت شد: {percent:.1f}٪ از کسب‌وکارِ #{business_id}. "
        f"طرفِ‌مقابل باید با «قبول شراکت {p['id']}» قبول کنه."
    )
    await host.save_state()


async def accept_partnership_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قبول شراکت [شناسه] / /acceptpartnership <id>"""
    host = _host()
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «قبول شراکت [شناسه]»")
        return
    try:
        proposal_id = int(args[0])
    except ValueError:
        await message.reply_text("شناسه نامعتبره.")
        return
    uid = update.effective_user.id
    try:
        p = accept(chat.id, uid, proposal_id)
    except PartnershipError as e:
        await message.reply_text(f"❌ {e}")
        return
    await message.reply_text(
        f"✅ شراکت قبول شد! از الان {p['equity']*100:.1f}٪ از سودِ کسب‌وکارِ #{p['business_id']} خودکار به تو می‌رسه."
    )
    await host.save_state()


async def reject_partnership_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رد شراکت [شناسه] / /rejectpartnership <id>"""
    host = _host()
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «رد شراکت [شناسه]»")
        return
    try:
        proposal_id = int(args[0])
    except ValueError:
        await message.reply_text("شناسه نامعتبره.")
        return
    uid = update.effective_user.id
    try:
        reject(chat.id, uid, proposal_id)
    except PartnershipError as e:
        await message.reply_text(f"❌ {e}")
        return
    await message.reply_text("✅ پیشنهاد رد شد.")
    await host.save_state()


async def my_partnerships_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شراکت‌های من / /mypartnerships"""
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    await message.reply_text(format_pending_text(chat.id, uid))


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "version": STATE_VERSION,
        "proposals": {str(c): v for c, v in _proposals.items()},
        "next_proposal_id": {str(c): v for c, v in _next_proposal_id.items()},
    }


def _write_state_file(data: dict):
    tmp = STATE_FILE + ".tmp"
    if os.path.exists(STATE_FILE):
        try:
            shutil.copy2(STATE_FILE, STATE_FILE + ".bak")
        except Exception as e:
            logger.warning(f"بکاپ گرفتن از state ناموفق بود: {e}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_FILE)


async def save_state():
    async with _save_lock:
        try:
            await asyncio.to_thread(_write_state_file, _collect_state())
        except Exception as e:
            logger.warning(f"ذخیره‌ی state شراکت ناموفق بود: {e}")


def load_state():
    data = None
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"خوندن state شراکت ناموفق بود: {e}؛ تلاش برای بازیابی از بکاپ...")
            data = None
    if data is None:
        bak = STATE_FILE + ".bak"
        if os.path.exists(bak):
            try:
                with open(bak, encoding="utf-8") as f:
                    data = json.load(f)
                logger.warning("state اصلیِ شراکت خراب/نامعتبر بود؛ از بکاپ بازیابی شد.")
            except Exception as e:
                logger.warning(f"خوندنِ بکاپِ شراکت هم ناموفق بود: {e}")
                return
        else:
            return

    for cid_s, proposals in data.get("proposals", {}).items():
        cid = int(cid_s)
        for pid_s, p in proposals.items():
            p = dict(p)
            p["id"] = int(p["id"])
            p["owner_uid"] = int(p["owner_uid"])
            p["partner_uid"] = int(p["partner_uid"])
            p["business_id"] = int(p["business_id"])
            _proposals[cid][int(pid_s)] = p

    for cid_s, v in data.get("next_proposal_id", {}).items():
        _next_proposal_id[int(cid_s)] = int(v)

    logger.info("وضعیتِ شراکت از فایل بارگذاری شد.")
