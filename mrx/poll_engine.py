# -*- coding: utf-8 -*-
"""
DIGIANTI POLL ENGINE 1.0
=========================
فاز ۳ ارتقای Poll (طبق درخواست کاربر): Timer خودکار، Anonymous/Public،
Multiple/Single Choice، Reward، Anti-Abuse، History، و کنترل‌های ادمین.

همون معماری قبلی (profile_engine.py/economy_engine.py): جدا از bot.py، در
زمان اجرا `import bot as host` می‌کنه. متغیر خود جدول نظرسنجی‌های فعال
(`_polls`) همون‌جایی می‌مونه که همیشه بوده (bot.py) تا bot.py کمترین تغییر رو
بخوره؛ این ماژول فقط منطق ساخت/شمارش/بستن/تاریخچه/تایمر رو اضافه می‌کنه.
تاریخچه‌ی نظرسنجی‌های بسته‌شده (که قبلاً اصلاً وجود نداشت) توی فایل state
مستقل خودش نگه داشته می‌شه.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import defaultdict, deque

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

import config

logger = logging.getLogger("poll_engine")

STATE_FILE = getattr(config, "POLL_ENGINE_STATE_FILE", "poll_engine_state.json")
_HISTORY_LIMIT = getattr(config, "POLL_HISTORY_LIMIT", 200)

# ---------------------------------------------------------------------------
# 💾 State — فقط تاریخچه‌ی نظرسنجی‌های بسته‌شده این‌جا نگه داشته می‌شه.
# جدول نظرسنجی‌های فعال (host._polls) همون‌جایی می‌مونه که بود؛ persist شدنش
# از طریق serialize_active_polls/deserialize_active_polls انجام می‌شه که
# bot.py توی _collect_state/load_state خودش صداشون می‌زنه.
# ---------------------------------------------------------------------------

_history = deque(maxlen=_HISTORY_LIMIT)
_save_lock = asyncio.Lock()


def _host():
    import bot as host
    return host


# ---------------------------------------------------------------------------
# 💾 Persistence (فقط History)
# ---------------------------------------------------------------------------

def _write_state_file(data: dict):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


async def save_state():
    async with _save_lock:
        try:
            await asyncio.to_thread(_write_state_file, {"history": list(_history)})
        except Exception as e:
            logger.warning(f"ذخیره‌ی تاریخچه‌ی نظرسنجی ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن تاریخچه‌ی نظرسنجی ناموفق بود: {e}")
        return
    _history.clear()
    _history.extend(data.get("history", []))
    logger.info("تاریخچه‌ی نظرسنجی از فایل بارگذاری شد.")


# ---------------------------------------------------------------------------
# 💾 Serialize/Deserialize جدول نظرسنجی‌های فعال (برای state اصلی bot.py)
# ---------------------------------------------------------------------------

def serialize_active_polls(polls: dict) -> dict:
    out = {}
    for pid, poll in polls.items():
        p = dict(poll)
        # votes ممکنه مقدارش int (تک‌انتخابی) یا list (چندتایی) باشه؛ هردو JSON-safe هستن
        p["votes"] = {str(u): v for u, v in poll.get("votes", {}).items()}
        p["rewarded_voters"] = list(poll.get("rewarded_voters", []))
        out[str(pid)] = p
    return out


def deserialize_active_polls(data: dict) -> dict:
    out = {}
    for pid, p in data.items():
        poll = dict(p)
        poll["votes"] = {int(u): v for u, v in p.get("votes", {}).items()}
        poll["rewarded_voters"] = set(p.get("rewarded_voters", []))
        out[int(pid)] = poll
    return out


# ---------------------------------------------------------------------------
# 🧩 Parsing (/poll <سوال> | <گزینه۱> | <گزینه۲> ... [| زمان:1h] [| ناشناس] [| چندتایی] [| جایزه:20])
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(r"^(\d+)\s*(m|min|دقیقه|h|hour|ساعت|d|day|روز)$", re.IGNORECASE)


def _parse_duration(text: str) -> int | None:
    """'1m'/'5m'/'30m'/'1h'/'1d'/'10m'/'3h' و معادل فارسی‌شون رو به ثانیه تبدیل می‌کنه."""
    m = _DURATION_RE.match(text.strip())
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    if unit in ("m", "min", "دقیقه"):
        return n * 60
    if unit in ("h", "hour", "ساعت"):
        return n * 3600
    if unit in ("d", "day", "روز"):
        return n * 86400
    return None


def parse_poll_args(raw: str) -> dict:
    """متن بعد از /poll رو پارس می‌کنه. رفتار پیش‌فرض (بدون تنظیمات اضافه) دقیقاً
    مثل قبله: Single-choice، Public، بدون تایمر، بدون جایزه."""
    parts = [p.strip() for p in raw.split("|") if p.strip()]
    question = parts[0] if parts else raw.strip()
    rest = parts[1:]

    options, duration_seconds, anonymous, multi, reward_coins = [], None, False, False, 0

    dur_prefixes = getattr(config, "POLL_DURATION_PREFIXES", ["زمان:", "duration:"])
    reward_prefixes = getattr(config, "POLL_REWARD_PREFIXES", ["جایزه:", "reward:"])
    anon_kw = {w.lower() for w in getattr(config, "POLL_ANONYMOUS_KEYWORDS", ["ناشناس", "anonymous"])}
    multi_kw = {w.lower() for w in getattr(config, "POLL_MULTI_KEYWORDS", ["چندتایی", "multi"])}

    for part in rest:
        low = part.lower()
        matched_setting = False

        for pref in dur_prefixes:
            if low.startswith(pref.lower()):
                secs = _parse_duration(part[len(pref):])
                if secs:
                    duration_seconds = secs
                matched_setting = True
                break
        if matched_setting:
            continue

        for pref in reward_prefixes:
            if low.startswith(pref.lower()):
                digits = re.findall(r"\d+", part)
                if digits:
                    reward_coins = min(int(digits[0]), getattr(config, "POLL_REWARD_MAX", 500))
                matched_setting = True
                break
        if matched_setting:
            continue

        if low in anon_kw:
            anonymous = True
            continue
        if low in multi_kw:
            multi = True
            continue

        options.append(part)

    options = options[: config.POLL_MAX_OPTIONS]
    if len(options) < 2:
        options = ["✅ بله", "❌ نه"]

    return {
        "question": question,
        "options": options,
        "duration_seconds": duration_seconds,
        "anonymous": anonymous,
        "multi": multi,
        "reward_coins": reward_coins,
    }


# ---------------------------------------------------------------------------
# 🗳 Poll record
# ---------------------------------------------------------------------------

def build_poll_record(chat_id: int, creator_id: int, parsed: dict) -> dict:
    now = time.time()
    return {
        "chat_id": chat_id,
        "question": parsed["question"],
        "options": parsed["options"],
        "votes": {},
        "creator_id": creator_id,
        "created_at": now,
        "closing_at": (now + parsed["duration_seconds"]) if parsed["duration_seconds"] else None,
        "closed": False,
        "closed_at": None,
        "anonymous": parsed["anonymous"],
        "multi": parsed["multi"],
        "hide_results": False,
        "reward_coins": parsed["reward_coins"],
        "rewarded_voters": set(),
    }


def _vote_counts(poll: dict) -> list:
    counts = [0] * len(poll["options"])
    for v in poll["votes"].values():
        idxs = v if isinstance(v, list) else [v]
        for i in idxs:
            if 0 <= i < len(counts):
                counts[i] += 1
    return counts


def poll_text(poll: dict) -> str:
    options = poll["options"]
    header_flags = []
    if poll.get("multi"):
        header_flags.append("چندتایی")
    if poll.get("anonymous"):
        header_flags.append("ناشناس")
    flags_text = f" ({' • '.join(header_flags)})" if header_flags else ""

    lines = [f"🗳️ {poll['question']}{flags_text}"]

    if poll.get("hide_results") and not poll.get("closed"):
        total = len({u for u in poll["votes"]})
        lines.append("\n🙈 نتایج تا پایان نظرسنجی مخفیه.")
        lines.append(f"مجموع رأی‌دهنده‌ها: {total}")
    else:
        counts = _vote_counts(poll)
        total = sum(counts)
        for i, opt in enumerate(options):
            pct = int(counts[i] * 100 / total) if total else 0
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            lines.append(f"{opt}: {counts[i]} رأی ({pct}%)\n{bar}")
        lines.append(f"\nمجموع آرا: {total}")

    if poll.get("closing_at") and not poll.get("closed"):
        remaining = int(poll["closing_at"] - time.time())
        if remaining > 0:
            lines.append(f"⏱ زمان باقی‌مونده: {_format_seconds(remaining)}")

    if poll.get("closed"):
        lines.append("\n🔒 این نظرسنجی بسته شده.")

    return "\n".join(lines)


def _format_seconds(seconds: int) -> str:
    if seconds >= 3600:
        return f"{seconds // 3600} ساعت و {(seconds % 3600) // 60} دقیقه"
    if seconds >= 60:
        return f"{seconds // 60} دقیقه"
    return f"{seconds} ثانیه"


def poll_keyboard(poll_id: int, poll: dict) -> InlineKeyboardMarkup:
    if poll.get("closed"):
        return InlineKeyboardMarkup([])
    buttons = [
        [InlineKeyboardButton(opt, callback_data=f"vote:{poll_id}:{i}")] for i, opt in enumerate(poll["options"])
    ]
    buttons.append([InlineKeyboardButton("🔚 بستن نظرسنجی", callback_data=f"pollclose:{poll_id}")])
    return InlineKeyboardMarkup(buttons)


# ---------------------------------------------------------------------------
# ✅ Voting (Single/Multiple + Reward با جلوگیری از جایزه‌ی تکراری)
# ---------------------------------------------------------------------------

def record_vote(poll: dict, uid: int, idx: int) -> str:
    """رأی رو ثبت می‌کنه (تک‌انتخابی = جایگزین، چندتایی = Toggle) و در صورت فعال بودن
    Reward، فقط یک‌بار برای هر کاربر در این نظرسنجی جایزه می‌ده. متن answer برمی‌گردونه."""
    is_first_vote = uid not in poll["votes"]

    if poll.get("multi"):
        current = set(poll["votes"].get(uid, []))
        if idx in current:
            current.discard(idx)
            answer = "رأیت برداشته شد."
        else:
            current.add(idx)
            answer = "رأیت ثبت شد ✅"
        poll["votes"][uid] = sorted(current)
    else:
        poll["votes"][uid] = idx
        answer = "رأیت ثبت شد ✅"

    if is_first_vote:
        _grant_vote_reward(poll, uid)

    return answer


def _grant_vote_reward(poll: dict, uid: int) -> None:
    reward = poll.get("reward_coins", 0)
    if not reward or uid in poll.get("rewarded_voters", set()):
        return
    host = _host()
    host._wallet[poll["chat_id"]][uid] += reward
    poll.setdefault("rewarded_voters", set()).add(uid)
    try:
        import economy_engine
        economy_engine.log_transaction(poll["chat_id"], uid, "poll_reward", reward, note=poll["question"][:40])
    except Exception as e:
        logger.warning(f"ثبت تراکنش جایزه‌ی نظرسنجی ناموفق بود: {e}")


# ---------------------------------------------------------------------------
# 🔒 Closing / History / Timer
# ---------------------------------------------------------------------------

def _archive(poll: dict, poll_id: int, reason: str) -> None:
    counts = _vote_counts(poll)
    winner = None
    if counts:
        best = max(counts)
        if best > 0:
            winner_idx = counts.index(best)
            winner = poll["options"][winner_idx]
    _history.append({
        "poll_id": poll_id,
        "chat_id": poll["chat_id"],
        "creator_id": poll["creator_id"],
        "question": poll["question"],
        "options": poll["options"],
        "total_votes": len(poll["votes"]),
        "created_at": poll["created_at"],
        "closed_at": time.time(),
        "winner": winner,
        "reason": reason,
    })


def close_poll(poll_id: int, reason: str = "manual") -> dict | None:
    """نظرسنجی رو می‌بنده، توی تاریخچه ثبت می‌کنه، و از جدول فعال حذفش می‌کنه."""
    host = _host()
    poll = host._polls.get(poll_id)
    if not poll or poll.get("closed"):
        return None
    poll["closed"] = True
    poll["closed_at"] = time.time()
    _archive(poll, poll_id, reason)
    host._polls.pop(poll_id, None)
    return poll


async def check_expired_polls(bot) -> None:
    """توی _periodic_save_loop صدا زده می‌شه: نظرسنجی‌های تایمردار که وقتشون تموم شده رو می‌بنده."""
    host = _host()
    now = time.time()
    expired_ids = [
        pid for pid, poll in list(host._polls.items())
        if poll.get("closing_at") and not poll.get("closed") and poll["closing_at"] <= now
    ]
    for pid in expired_ids:
        chat_id = host._polls[pid]["chat_id"]
        poll = close_poll(pid, reason="timer")
        if not poll or not bot:
            continue
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=pid,
                text=poll_text(poll), reply_markup=None,
            )
        except Exception as e:
            logger.warning(f"آپدیت پیام نظرسنجی منقضی‌شده ناموفق بود: {e}")


# ---------------------------------------------------------------------------
# 👑 Admin Controls
# ---------------------------------------------------------------------------

def _can_manage(host, update: Update, poll: dict | None) -> bool:
    uid = update.effective_user.id
    if host.is_admin(uid) or host.has_permission(uid, "moderate"):
        return True
    return bool(poll and poll.get("creator_id") == uid)


def _resolve_poll_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    message = update.effective_message
    if message.reply_to_message:
        return message.reply_to_message.message_id
    if context.args:
        try:
            return int(context.args[0])
        except ValueError:
            return None
    return None


async def pollclose_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    host = _host()
    message = update.effective_message
    poll_id = _resolve_poll_id(update, context)
    poll = host._polls.get(poll_id) if poll_id else None
    if not poll:
        await message.reply_text("استفاده: روی پیام نظرسنجی ریپلای بزن و بنویس /pollclose (یا /pollclose <آیدی>)")
        return
    if not _can_manage(host, update, poll):
        await message.reply_text("فقط سازنده‌ی نظرسنجی یا ادمین می‌تونه ببنددش.")
        return
    closed = close_poll(poll_id, reason="admin")
    try:
        await context.bot.edit_message_text(
            chat_id=closed["chat_id"], message_id=poll_id, text=poll_text(closed), reply_markup=None
        )
    except BadRequest:
        pass
    await message.reply_text("✅ نظرسنجی بسته شد.")
    await host.save_state()


async def polldelete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف/لغو نظرسنجی بدون اعلام نتیجه (برخلاف Close که نتیجه رو نشون می‌ده)."""
    host = _host()
    message = update.effective_message
    poll_id = _resolve_poll_id(update, context)
    poll = host._polls.get(poll_id) if poll_id else None
    if not poll:
        await message.reply_text("استفاده: روی پیام نظرسنجی ریپلای بزن و بنویس /polldelete (یا /polldelete <آیدی>)")
        return
    if not _can_manage(host, update, poll):
        await message.reply_text("فقط سازنده‌ی نظرسنجی یا ادمین می‌تونه حذفش کنه.")
        return
    close_poll(poll_id, reason="cancelled")
    try:
        await context.bot.edit_message_text(
            chat_id=poll["chat_id"], message_id=poll_id,
            text=f"🗑 نظرسنجی «{poll['question']}» توسط ادمین لغو شد.", reply_markup=None,
        )
    except BadRequest:
        pass
    await message.reply_text("✅ نظرسنجی حذف شد.")
    await host.save_state()


async def pollreopen_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """آخرین نظرسنجی بسته‌شده‌ی این آیدی رو (اگه توی تاریخچه باشه) دوباره باز می‌کنه.
    توجه: چون بعد از بسته‌شدن از جدول فعال حذف می‌شه، فقط رأی‌های قبل از بستن حفظن؛
    این دستور نظرسنجی رو با صفر رأی (تازه) با همون سوال/گزینه‌ها دوباره می‌سازه."""
    host = _host()
    message = update.effective_message
    if not (host.is_admin(update.effective_user.id) or host.has_permission(update.effective_user.id, "moderate")):
        await message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    if not context.args:
        await message.reply_text("استفاده: /pollreopen <آیدی نظرسنجی‌ای که توی /pollhistory دیدی>")
        return
    try:
        target_pid = int(context.args[0])
    except ValueError:
        await message.reply_text("آیدی نامعتبره.")
        return
    record = next((h for h in _history if h["poll_id"] == target_pid), None)
    if not record:
        await message.reply_text("همچین نظرسنجی‌ای توی تاریخچه پیدا نشد.")
        return
    chat = update.effective_chat
    sent = await context.bot.send_message(chat_id=chat.id, text=f"🔓 نظرسنجی «{record['question']}» دوباره باز شد.\n\n")
    parsed = {
        "question": record["question"], "options": record["options"],
        "duration_seconds": None, "anonymous": False, "multi": False, "reward_coins": 0,
    }
    new_poll = build_poll_record(chat.id, update.effective_user.id, parsed)
    host._polls[sent.message_id] = new_poll
    await context.bot.edit_message_text(chat_id=chat.id, message_id=sent.message_id, text=poll_text(new_poll))
    await context.bot.edit_message_reply_markup(
        chat_id=chat.id, message_id=sent.message_id, reply_markup=poll_keyboard(sent.message_id, new_poll)
    )
    await host.save_state()


async def pollvoters_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    host = _host()
    message = update.effective_message
    poll_id = _resolve_poll_id(update, context)
    poll = host._polls.get(poll_id) if poll_id else None
    if not poll:
        await message.reply_text("استفاده: روی پیام نظرسنجی ریپلای بزن و بنویس /pollvoters")
        return
    if not _can_manage(host, update, poll):
        await message.reply_text("فقط سازنده‌ی نظرسنجی یا ادمین می‌تونه رأی‌دهنده‌ها رو ببینه.")
        return
    if poll.get("anonymous"):
        await message.reply_text("این نظرسنجی ناشناسه؛ حتی برای ادمین رأی‌دهنده‌ها نشون داده نمی‌شن.")
        return
    if not poll["votes"]:
        await message.reply_text("هنوز کسی رأی نداده.")
        return
    lines = ["👥 رأی‌دهنده‌ها:"]
    for uid, v in poll["votes"].items():
        name = host._user_display_names.get(uid, str(uid))
        idxs = v if isinstance(v, list) else [v]
        chosen = "، ".join(poll["options"][i] for i in idxs if 0 <= i < len(poll["options"]))
        lines.append(f"• {name} → {chosen}")
    await message.reply_text("\n".join(lines))


async def pollhide_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش نتایج زنده رو مخفی/آشکار می‌کنه (تا وقتی نظرسنجی بسته بشه)."""
    host = _host()
    message = update.effective_message
    poll_id = _resolve_poll_id(update, context)
    poll = host._polls.get(poll_id) if poll_id else None
    if not poll:
        await message.reply_text("استفاده: روی پیام نظرسنجی ریپلای بزن و بنویس /pollhide")
        return
    if not _can_manage(host, update, poll):
        await message.reply_text("فقط سازنده‌ی نظرسنجی یا ادمین می‌تونه این کارو بکنه.")
        return
    poll["hide_results"] = not poll.get("hide_results", False)
    try:
        await context.bot.edit_message_text(
            chat_id=poll["chat_id"], message_id=poll_id,
            text=poll_text(poll), reply_markup=poll_keyboard(poll_id, poll),
        )
    except BadRequest:
        pass
    state = "مخفی شد" if poll["hide_results"] else "آشکار شد"
    await message.reply_text(f"✅ نتایج زنده {state}.")
    await host.save_state()


async def pollhistory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not (host.is_admin(update.effective_user.id) or host.has_permission(update.effective_user.id, "moderate")):
        await message.reply_text("این دستور فقط برای ادمین‌های مجازه.")
        return
    records = [h for h in _history if h["chat_id"] == chat.id]
    if not records:
        await message.reply_text("هنوز هیچ نظرسنجی‌ای بسته نشده.")
        return
    limit = getattr(config, "POLL_HISTORY_DISPLAY_COUNT", 10)
    lines = ["📜 تاریخچه‌ی نظرسنجی‌های این گروه:", ""]
    for rec in list(reversed(records))[:limit]:
        creator = host._user_display_names.get(rec["creator_id"], str(rec["creator_id"]))
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(rec["closed_at"]))
        winner = f" — 🏆 برنده: {rec['winner']}" if rec.get("winner") else ""
        lines.append(f"#{rec['poll_id']} • {when} • «{rec['question']}» (سازنده: {creator}, {rec['total_votes']} رأی){winner}")
    await message.reply_text("\n".join(lines))
