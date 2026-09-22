# -*- coding: utf-8 -*-
"""
🛡️ DIGIANTI — Professional Moderation & Punishment Engine
════════════════════════════════════════════════════════════════════════════
موتور مرکزی مدیریت (Warn / Mute / Ban / Risk / History / Log) که با:
  • دستورات دستی ادمین (bot.py)
  • Spam Engine (spam_engine.py)
  • Lock Engine (lock_engine.py)
یکپارچه می‌شه، بدون این‌که به منطق تشخیص تخلف اون دوتا دست بزنه.

این فایل دقیقاً از همون الگوی lock_engine.py / spam_engine.py پیروی می‌کنه:
ذخیره‌سازی مستقل (moderation_engine_state.json)، و import تنبل (lazy) از
ماژول bot به اسم host، تا از circular-import جلوگیری بشه و به
_collect_state/load_state غول‌پیکر bot.py دست‌درازی نشه.

معماری داخلی (منطقاً ماژولار، طبق فاز ۲۵ درخواست):
    ├── Warn Manager        -> add_warn / remove_warn / clear_warns
    ├── Mute Manager         -> mute / unmute / list_active_mutes
    ├── Ban Manager          -> ban / unban / list_active_bans
    ├── Risk Manager         -> compute_risk / risk_label
    ├── Escalation Manager   -> _resolve_escalation_step
    ├── Decay Manager        -> decay_tick
    ├── History Manager      -> _append_history / get_history
    ├── Log Manager          -> _send_mod_log
    └── Punishment Executor  -> punish() / log_auto_action()  (تک‌نقطه‌ی اجرا -> ضدتکرار)

هیچ‌کدوم از توابع این فایل نباید Exception رو بیرون بندازن و ربات رو Crash کنن؛
همه‌جا با try/except محافظت شده.
"""

import asyncio
import json
import logging
import os
import time
from collections import defaultdict

import config
from telegram import ChatPermissions

logger = logging.getLogger(__name__)

STATE_FILE = getattr(config, "MODERATION_ENGINE_STATE_FILE", "moderation_engine_state.json")

# ---------------------------------------------------------------------------
# 💾 حافظه‌ی داخلی (per chat -> per user)
# ---------------------------------------------------------------------------
# _DATA[chat_id][user_id] = {
#   "warns": [ {reason, admin_id, admin_name, ts, source} ... ]  (فقط فعال/معتبر)
#   "warn_total": int (تجمعی، برای آمار)
#   "mutes": [ {reason, admin_id, ts, until, duration_sec, source, active} ... ]
#   "mute_total": int
#   "bans": [ {reason, admin_id, ts, until, source, active} ... ]   until=None یعنی دائمی
#   "ban_total": int
#   "kick_total": int
#   "offense_index": int         -> اشاره‌گر فعلی روی MODERATION_ESCALATION_LADDER
#   "risk_score": float
#   "last_offense_ts": float
#   "history": [ {action, reason, admin, ts, duration, source} ... ]  (کلی، هر اکشنی)
# }
_DATA = defaultdict(lambda: defaultdict(dict))

# آمار اکشن هر ادمین: _ADMIN_STATS[chat_id][admin_id] = {"warn":n,"mute":n,"ban":n,"unmute":n,"unban":n}
_ADMIN_STATS = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))

# جلوگیری از Duplicate Action: event_id -> timestamp آخرین پردازش
_DEDUP = {}

_save_lock = asyncio.Lock()


def _now() -> float:
    return time.time()


def _user_record(chat_id: int, user_id: int) -> dict:
    rec = _DATA[chat_id][user_id]
    rec.setdefault("warns", [])
    rec.setdefault("warn_total", 0)
    rec.setdefault("mutes", [])
    rec.setdefault("mute_total", 0)
    rec.setdefault("bans", [])
    rec.setdefault("ban_total", 0)
    rec.setdefault("kick_total", 0)
    rec.setdefault("offense_index", 0)
    rec.setdefault("risk_score", 0.0)
    rec.setdefault("last_offense_ts", 0.0)
    rec.setdefault("history", [])
    return rec


def _append_history(chat_id: int, user_id: int, action: str, reason: str, admin_name: str,
                     duration: str | None, source: str):
    rec = _user_record(chat_id, user_id)
    rec["history"].append({
        "action": action, "reason": reason or "—", "admin": admin_name or "—",
        "ts": _now(), "duration": duration or "—", "source": source,
    })
    # فقط ۲۰۰ مورد آخر رو نگه می‌داریم تا فایل حالت بی‌نهایت بزرگ نشه
    if len(rec["history"]) > 200:
        rec["history"] = rec["history"][-200:]


def _bump_admin_stat(chat_id: int, admin_id: int | None, key: str):
    if not admin_id:
        return
    _ADMIN_STATS[chat_id][admin_id][key] += 1


# ---------------------------------------------------------------------------
# 🔁 Duplicate Action Protection
# ---------------------------------------------------------------------------

def _dedup_seen(event_id: str | None) -> bool:
    """اگه این event_id به‌تازگی پردازش شده باشه True برمی‌گردونه (یعنی: نادیده بگیر)."""
    if not event_id:
        return False
    window = getattr(config, "MODERATION_DEDUP_WINDOW_SECONDS", 8)
    now = _now()
    # پاک‌سازی سبک رکوردهای قدیمی
    stale = [k for k, ts in _DEDUP.items() if now - ts > window * 4]
    for k in stale:
        _DEDUP.pop(k, None)
    last = _DEDUP.get(event_id)
    _DEDUP[event_id] = now
    if last is not None and (now - last) <= window:
        return True
    return False


# ---------------------------------------------------------------------------
# 🛡️ Protection / Immunity
# ---------------------------------------------------------------------------

def is_protected(chat_id: int, user_id: int) -> bool:
    """اوونر/ادمین/VIP هیچ‌وقت نباید هدف Auto Moderation یا اشتباهی Warn/Mute/Ban بشن."""
    try:
        import bot as host
        if host.is_admin(user_id):
            return True
        if getattr(config, "MODERATION_VIP_PROTECTED", True):
            if user_id in host._vip_users.get(chat_id, set()):
                return True
    except Exception as e:
        logger.warning(f"چک Protection ناموفق بود: {e}")
    return False


def can_moderate(user_id: int, level: str = "moderate") -> bool:
    """سطح دسترسی رو از سیستم permission موجود پروژه (config.ADMIN_PERMISSIONS) می‌خونه."""
    try:
        import bot as host
        return host.has_permission(user_id, level)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 🧠 Risk Manager
# ---------------------------------------------------------------------------

def compute_risk(chat_id: int, user_id: int) -> int:
    rec = _user_record(chat_id, user_id)
    weights = getattr(config, "MODERATION_RISK_WEIGHTS", {})
    score = (
        len(rec["warns"]) * weights.get("warn", 8)
        + rec["mute_total"] * weights.get("mute", 18)
        + rec["ban_total"] * weights.get("ban", 35)
        + rec["kick_total"] * weights.get("kick", 15)
    )
    # امتیاز موتور اسپم (در صورت وجود) هم به‌عنوان ورودی اضافه می‌شه، بدون این‌که به اون موتور دست بزنیم
    try:
        import spam_engine
        score += spam_engine.current_score(chat_id, user_id) * weights.get("spam_violation", 5)
    except Exception:
        pass
    score = max(0, min(100, int(score)))
    rec["risk_score"] = score
    return score


def risk_label(score: int) -> str:
    thresholds = getattr(config, "MODERATION_RISK_THRESHOLDS", {"LOW": 20, "MEDIUM": 50, "HIGH": 75, "CRITICAL": 100})
    if score <= thresholds.get("LOW", 20):
        return "کم"
    if score <= thresholds.get("MEDIUM", 50):
        return "متوسط"
    if score <= thresholds.get("HIGH", 75):
        return "بالا"
    return "بحرانی"


# ---------------------------------------------------------------------------
# ⚖️ Escalation Manager
# ---------------------------------------------------------------------------

def _resolve_escalation_step(offense_count: int) -> dict:
    ladder = getattr(config, "MODERATION_ESCALATION_LADDER", [])
    if not ladder:
        return {"count": offense_count, "action": "warn"}
    step = ladder[-1]
    for item in ladder:
        if offense_count <= item["count"]:
            step = item
            break
    return step


# ---------------------------------------------------------------------------
# 📈 Decay Manager
# ---------------------------------------------------------------------------

def decay_tick():
    """باید دوره‌ای (مثلاً هر چند ساعت) صدا زده بشه. اگه کاربری مدتی تخلف نکرده باشه،
    offense_index و ریسکش به‌صورت تدریجی کم می‌شه (طبق MODERATION_DECAY_SCHEDULE)."""
    schedule = getattr(config, "MODERATION_DECAY_SCHEDULE", {})
    if not schedule:
        return
    now = _now()
    thresholds_days = sorted(schedule.keys())
    try:
        for chat_id, users in _DATA.items():
            for user_id, rec in users.items():
                last = rec.get("last_offense_ts", 0)
                if not last:
                    continue
                days_idle = (now - last) / 86400
                levels_to_drop = 0
                for d in thresholds_days:
                    if days_idle >= d:
                        levels_to_drop = schedule[d]
                if levels_to_drop:
                    rec["offense_index"] = max(0, rec["offense_index"] - levels_to_drop)
                    rec["risk_score"] = max(0.0, rec["risk_score"] - levels_to_drop * 10)
                    # جلوگیری از کاهش مکرر توی هر تیک: last_offense_ts رو کمی جلو می‌بریم
                    rec["last_offense_ts"] = last + (thresholds_days[0] * 86400)
    except Exception as e:
        logger.warning(f"Offense Decay ناموفق بود: {e}")


# ---------------------------------------------------------------------------
# 📢 Log Manager
# ---------------------------------------------------------------------------

_ACTION_EMOJI = {"warn": "⚠️", "mute": "🔇", "unmute": "🔊", "ban": "🚫", "unban": "✅", "kick": "👢"}
_ACTION_LABEL = {"warn": "اخطار", "mute": "میوت", "unmute": "آن‌میوت", "ban": "بن", "unban": "آن‌بن", "kick": "اخراج"}


async def _send_mod_log(context, chat_id: int, action: str, user_id: int, display_name: str,
                         reason: str, duration: str, admin_name: str, source: str):
    try:
        import bot as host
    except Exception:
        return
    emoji = _ACTION_EMOJI.get(action, "🛡️")
    label = _ACTION_LABEL.get(action, action)
    text = (
        f"🛡️ گزارش مدیریت\n\n"
        f"{emoji} اقدام: {label}\n\n"
        f"👤 کاربر: {display_name}\n"
        f"🆔 آیدی: {user_id}\n"
        f"📌 دلیل: {reason or '—'}\n"
        f"⏱ مدت: {duration or '—'}\n"
        f"🤖 منبع: {source}\n"
        f"👮 ادمین: {admin_name or '—'}"
    )
    try:
        await host._log_admin_action(context, chat_id, text)
    except Exception as e:
        logger.warning(f"ارسال Moderation Log ناموفق بود: {e}")


async def _auto_delete_later(context, chat_id: int, message_id: int, seconds: int):
    if not seconds:
        return
    try:
        await asyncio.sleep(seconds)
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass  # پیام قبلاً پاک شده یا دسترسی نیست؛ Bot نباید Crash کنه


def _fmt_duration(seconds: int | None) -> str:
    if not seconds:
        return "دائمی"
    if seconds % 86400 == 0:
        return f"{seconds // 86400} روز"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} ساعت"
    return f"{max(1, seconds // 60)} دقیقه"


def _sync_legacy(chat_id: int, user_id: int):
    """برای سازگاری کامل با بقیه‌ی پروژه (مثل کارت پروفایل که از _warnings می‌خونه)،
    شمارنده‌های قدیمی bot.py رو هم هم‌زمان با موتور جدید به‌روز نگه می‌داریم."""
    try:
        import bot as host
        rec = _user_record(chat_id, user_id)
        host._warnings[chat_id][user_id] = len(rec["warns"])
        host._mute_counts[chat_id][user_id] = rec["mute_total"]
    except Exception:
        pass


# ---------------------------------------------------------------------------
# ⚔️ Punishment Executor — تک‌نقطه‌ی اجرای واقعی اکشن‌ها (منبع ضدتکرار)
# ---------------------------------------------------------------------------

async def punish(context, chat_id: int, user_id: int, display_name: str, action: str, *,
                  reason: str = "", admin_id: int | None = None, admin_name: str | None = None,
                  source: str = "MANUAL", duration_seconds: int | None = None,
                  message=None, event_id: str | None = None, notify: bool = True) -> dict:
    """اکشن Warn/Mute/Ban/Kick رو واقعاً اجرا می‌کنه: تلگرام + تاریخچه + ریسک + لاگ + پیام کاربر.
    هیچ‌وقت Exception بیرون نمی‌ندازه. خروجی: {"ok": bool, "blocked": str|None, ...}"""
    if _dedup_seen(event_id):
        return {"ok": False, "blocked": "duplicate"}

    if action in ("warn", "mute", "ban", "kick") and is_protected(chat_id, user_id):
        return {"ok": False, "blocked": "protected"}

    rec = _user_record(chat_id, user_id)
    rec["last_offense_ts"] = _now()
    result = {"ok": True, "action": action}

    try:
        if action == "warn":
            rec["warns"].append({
                "reason": reason, "admin_id": admin_id, "admin_name": admin_name,
                "ts": _now(), "source": source,
            })
            rec["warn_total"] += 1
            _bump_admin_stat(chat_id, admin_id, "warn")
            duration_txt = None
            if notify:
                count = len(rec["warns"])
                text = f"⚠️ اخطار به {display_name}\n📌 دلیل: {reason or '—'}\n🔢 شماره اخطار: {count}"
                await _safe_send(context, chat_id, text, action, message)

        elif action == "mute":
            until = None if not duration_seconds else int(time.time()) + duration_seconds
            await context.bot.restrict_chat_member(
                chat_id=chat_id, user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            rec["mutes"].append({
                "reason": reason, "admin_id": admin_id, "ts": _now(), "until": until,
                "duration_sec": duration_seconds, "source": source, "active": True,
            })
            rec["mute_total"] += 1
            _bump_admin_stat(chat_id, admin_id, "mute")
            duration_txt = _fmt_duration(duration_seconds)
            try:
                import bot as host
                host._active_mutes[chat_id][user_id] = until or (int(time.time()) + 100 * 365 * 86400)
            except Exception:
                pass
            if notify:
                text = f"🔇 {display_name} میوت شد.\n📌 دلیل: {reason or '—'}\n⏱ مدت: {duration_txt}"
                await _safe_send(context, chat_id, text, action, message)

        elif action == "unmute":
            await context.bot.restrict_chat_member(
                chat_id=chat_id, user_id=user_id,
                permissions=ChatPermissions(can_send_messages=True),
            )
            for m in rec["mutes"]:
                m["active"] = False
            try:
                import bot as host
                host._active_mutes[chat_id].pop(user_id, None)
            except Exception:
                pass
            duration_txt = None
            _bump_admin_stat(chat_id, admin_id, "unmute")
            if notify:
                await _safe_send(context, chat_id, f"🔊 {display_name} آن‌میوت شد.", action, message)

        elif action == "ban":
            until = None if not duration_seconds else int(time.time()) + duration_seconds
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id, until_date=until)
            rec["bans"].append({
                "reason": reason, "admin_id": admin_id, "ts": _now(), "until": until,
                "source": source, "active": True,
            })
            rec["ban_total"] += 1
            _bump_admin_stat(chat_id, admin_id, "ban")
            duration_txt = _fmt_duration(duration_seconds)
            try:
                import bot as host
                host._active_mutes[chat_id].pop(user_id, None)
                host._banned_users[chat_id].add(user_id)
            except Exception:
                pass
            if notify:
                text = f"🚫 {display_name} بن شد.\n📌 دلیل: {reason or '—'}\n⏱ مدت: {duration_txt}"
                await _safe_send(context, chat_id, text, action, message)

        elif action == "unban":
            await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
            for b in rec["bans"]:
                b["active"] = False
            try:
                import bot as host
                host._banned_users[chat_id].discard(user_id)
            except Exception:
                pass
            duration_txt = None
            _bump_admin_stat(chat_id, admin_id, "unban")
            if notify:
                await _safe_send(context, chat_id, f"✅ {display_name} آن‌بن شد.", action, message)

        elif action == "kick":
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
            rec["kick_total"] += 1
            _bump_admin_stat(chat_id, admin_id, "kick")
            duration_txt = None
            if notify:
                await _safe_send(context, chat_id, f"👢 {display_name} اخراج شد.", action, message)
        else:
            return {"ok": False, "blocked": "unknown_action"}

    except Exception as e:
        logger.warning(f"اجرای {action} روی {user_id} ناموفق بود: {e}")
        return {"ok": False, "blocked": "telegram_error", "error": str(e)}

    _append_history(chat_id, user_id, action, reason, admin_name, duration_txt if action in ("warn", "mute", "ban") else None, source)
    _sync_legacy(chat_id, user_id)
    compute_risk(chat_id, user_id)

    await _send_mod_log(context, chat_id, action, user_id, display_name, reason,
                         duration_txt if action in ("warn", "mute", "ban") else "—",
                         admin_name, source)

    # اگه اکشن Warn بود، نردبان تصاعدی رو چک کن و در صورت رسیدن به آستانه، خودکار Mute/Ban بزن
    if action == "warn":
        await _check_escalation(context, chat_id, user_id, display_name, reason, source)

    return result


async def _safe_send(context, chat_id, text, action, message):
    try:
        sent = await context.bot.send_message(chat_id=chat_id, text=text)
        seconds = getattr(config, "MODERATION_AUTODELETE_SECONDS", {}).get(action)
        if seconds:
            asyncio.create_task(_auto_delete_later(context, chat_id, sent.message_id, seconds))
    except Exception as e:
        logger.warning(f"ارسال پیام مدیریتی ناموفق بود: {e}")
    if message is not None:
        try:
            del_seconds = getattr(config, "MODERATION_AUTODELETE_SECONDS", {}).get("violation")
            if del_seconds:
                asyncio.create_task(_auto_delete_later(context, chat_id, message.message_id, del_seconds))
        except Exception:
            pass


async def _check_escalation(context, chat_id, user_id, display_name, reason, source):
    """بعد از هر Warn، تعداد کل اخطارهای فعال رو با MODERATION_ESCALATION_LADDER می‌سنجه."""
    rec = _user_record(chat_id, user_id)
    count = len(rec["warns"])
    step = _resolve_escalation_step(count)
    action = step.get("action")
    if action == "mute" and count == step["count"]:
        minutes = step.get("minutes", 30)
        await punish(context, chat_id, user_id, display_name, "mute",
                     reason=f"تکمیل نردبان اخطار ({reason})", source=source,
                     duration_seconds=minutes * 60, event_id=f"esc-{chat_id}-{user_id}-{count}")
    elif action == "ban" and count == step["count"]:
        days = step.get("days", 0)
        await punish(context, chat_id, user_id, display_name, "ban",
                     reason=f"تکمیل نردبان اخطار ({reason})", source=source,
                     duration_seconds=(days * 86400) if days else None,
                     event_id=f"esc-{chat_id}-{user_id}-{count}")


# ---------------------------------------------------------------------------
# 🧩 Warn Manager — API سطح‌بالا برای دستورات دستی
# ---------------------------------------------------------------------------

async def warn(context, chat_id, user_id, display_name, *, reason="", admin_id=None,
                admin_name=None, source="MANUAL", message=None) -> dict:
    return await punish(context, chat_id, user_id, display_name, "warn",
                         reason=reason, admin_id=admin_id, admin_name=admin_name,
                         source=source, message=message)


def unwarn_one(chat_id, user_id) -> int:
    rec = _user_record(chat_id, user_id)
    if rec["warns"]:
        rec["warns"].pop()
    _sync_legacy(chat_id, user_id)
    return len(rec["warns"])


def clear_warns(chat_id, user_id):
    rec = _user_record(chat_id, user_id)
    rec["warns"] = []
    rec["offense_index"] = 0
    _sync_legacy(chat_id, user_id)


def warn_count(chat_id, user_id) -> int:
    return len(_user_record(chat_id, user_id)["warns"])


# ---------------------------------------------------------------------------
# 🔇 Mute Manager
# ---------------------------------------------------------------------------

async def mute(context, chat_id, user_id, display_name, *, duration_seconds=None, reason="",
                admin_id=None, admin_name=None, source="MANUAL", message=None) -> dict:
    if duration_seconds is None:
        rec = _user_record(chat_id, user_id)
        ladder = getattr(config, "MODERATION_MUTE_LADDER_MINUTES", [30])
        idx = min(rec["mute_total"], len(ladder) - 1)
        duration_seconds = ladder[idx] * 60
    return await punish(context, chat_id, user_id, display_name, "mute",
                         reason=reason, admin_id=admin_id, admin_name=admin_name,
                         source=source, duration_seconds=duration_seconds, message=message)


async def unmute(context, chat_id, user_id, display_name, *, admin_id=None, admin_name=None,
                  source="MANUAL") -> dict:
    return await punish(context, chat_id, user_id, display_name, "unmute",
                         admin_id=admin_id, admin_name=admin_name, source=source)


def list_active_mutes(chat_id) -> list:
    now = _now()
    out = []
    for user_id, rec in _DATA[chat_id].items():
        for m in reversed(rec.get("mutes", [])):
            if m.get("active") and (m.get("until") is None or m["until"] > now):
                out.append((user_id, m))
                break
    return out


# ---------------------------------------------------------------------------
# 🚫 Ban Manager
# ---------------------------------------------------------------------------

async def ban(context, chat_id, user_id, display_name, *, duration_seconds=None, reason="",
               admin_id=None, admin_name=None, source="MANUAL", message=None) -> dict:
    return await punish(context, chat_id, user_id, display_name, "ban",
                         reason=reason, admin_id=admin_id, admin_name=admin_name,
                         source=source, duration_seconds=duration_seconds, message=message)


async def unban(context, chat_id, user_id, display_name, *, admin_id=None, admin_name=None,
                 source="MANUAL") -> dict:
    return await punish(context, chat_id, user_id, display_name, "unban",
                         admin_id=admin_id, admin_name=admin_name, source=source)


def list_active_bans(chat_id) -> list:
    now = _now()
    out = []
    for user_id, rec in _DATA[chat_id].items():
        for b in reversed(rec.get("bans", [])):
            if b.get("active") and (b.get("until") is None or b["until"] > now):
                out.append((user_id, b))
                break
    return out


# ---------------------------------------------------------------------------
# ⏰ انقضای خودکار Mute/Ban موقت (شبیه lock_engine.check_temp_lock_expiry)
# ---------------------------------------------------------------------------

async def check_expirations(bot):
    if bot is None:
        return
    now = _now()
    try:
        for chat_id, users in list(_DATA.items()):
            for user_id, rec in list(users.items()):
                for m in rec.get("mutes", []):
                    if m.get("active") and m.get("until") and m["until"] <= now:
                        m["active"] = False
                        try:
                            await bot.restrict_chat_member(
                                chat_id=chat_id, user_id=user_id,
                                permissions=ChatPermissions(can_send_messages=True),
                            )
                        except Exception:
                            pass
                        try:
                            import bot as host
                            host._active_mutes[chat_id].pop(user_id, None)
                        except Exception:
                            pass
                for b in rec.get("bans", []):
                    if b.get("active") and b.get("until") and b["until"] <= now:
                        b["active"] = False
                        try:
                            await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
                        except Exception:
                            pass
                        try:
                            import bot as host
                            host._banned_users[chat_id].discard(user_id)
                        except Exception:
                            pass
    except Exception as e:
        logger.warning(f"چک انقضای Mute/Ban موتور مدیریت ناموفق بود: {e}")


# ---------------------------------------------------------------------------
# 🤖 هوک یکپارچه‌سازی با Spam Engine / Lock Engine (فقط ثبت مرکزی؛ بدون اجرای دوباره)
# ---------------------------------------------------------------------------

async def log_auto_action(chat_id: int, user_id: int, action: str, reason: str, source: str,
                           duration_seconds: int | None = None, admin_name: str = "AUTO"):
    """spam_engine و lock_engine بعد از اینکه خودشون اکشن رو (طبق منطق تشخیص خودشون) واقعاً
    زدن، این تابع رو صدا می‌زنن تا توی تاریخچه/ریسک/آمار مرکزی هم ثبت بشه. اینجا هیچ اکشن
    تلگرامی دوباره اجرا نمی‌شه (بنابراین Duplicate Action رخ نمی‌ده)."""
    if not getattr(config, "MODERATION_AUTO_INTEGRATION_ENABLED", True):
        return
    try:
        rec = _user_record(chat_id, user_id)
        rec["last_offense_ts"] = _now()
        if action == "warn":
            rec["warn_total"] += 1
        elif action == "mute":
            rec["mute_total"] += 1
        elif action == "ban":
            rec["ban_total"] += 1
        elif action == "kick":
            rec["kick_total"] += 1
        duration_txt = _fmt_duration(duration_seconds) if action in ("mute", "ban") else None
        _append_history(chat_id, user_id, action, reason, admin_name, duration_txt, source)
        compute_risk(chat_id, user_id)
    except Exception as e:
        logger.warning(f"ثبت اکشن خودکار ({source}) ناموفق بود: {e}")


def record_violation_score(chat_id: int, user_id: int, source: str):
    """برای تخلفاتی که خودشون منجر به Warn/Mute/Ban نمی‌شن (مثلاً فقط Log)، صرفاً روی
    Risk Score اثر بذاره."""
    try:
        rec = _user_record(chat_id, user_id)
        rec["last_offense_ts"] = _now()
        compute_risk(chat_id, user_id)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 📋 History & 📊 Stats — نمایش
# ---------------------------------------------------------------------------

def format_modhistory(chat_id: int, user_id: int, display_name: str) -> str:
    rec = _user_record(chat_id, user_id)
    score = compute_risk(chat_id, user_id)
    label = risk_label(score)
    limit = getattr(config, "MODERATION_HISTORY_DISPLAY_LIMIT", 12)

    lines = [
        "🛡️ تاریخچه مدیریت",
        "",
        f"👤 کاربر: {display_name}",
        f"🆔 آیدی: {user_id}",
        "",
        f"⚠️ اخطارها: {len(rec['warns'])} (کل: {rec['warn_total']})",
        f"🔇 میوت‌ها: {rec['mute_total']}",
        f"🚫 بن‌ها: {rec['ban_total']}",
        f"📊 ریسک: {score}/100 ({label})",
        "",
        "📜 تاریخچه:",
    ]
    hist = rec["history"][-limit:]
    if not hist:
        lines.append("— هیچ سابقه‌ای ثبت نشده —")
    else:
        for h in reversed(hist):
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(h["ts"]))
            lines.append(
                f"• {_ACTION_LABEL.get(h['action'], h['action'])} — {h['reason']} — "
                f"{h['admin']} — {when}" + (f" — {h['duration']}" if h.get("duration") not in (None, "—") else "")
            )
    return "\n".join(lines)


def format_modstats(chat_id: int) -> str:
    users = _DATA.get(chat_id, {})
    total_warns = sum(u["warn_total"] for u in users.values())
    total_mutes = sum(u["mute_total"] for u in users.values())
    total_bans = sum(u["ban_total"] for u in users.values())
    active_mutes = len(list_active_mutes(chat_id))
    active_bans = len(list_active_bans(chat_id))

    top = sorted(
        users.items(),
        key=lambda kv: (kv[1]["warn_total"] + kv[1]["mute_total"] * 2 + kv[1]["ban_total"] * 3),
        reverse=True,
    )[:5]
    try:
        import bot as host
        name_of = lambda uid: host._user_display_names.get(uid, str(uid))
    except Exception:
        name_of = str

    lines = [
        "📊 آمار مدیریت",
        "",
        f"⚠️ کل اخطارها: {total_warns}",
        f"🔇 کل میوت‌ها: {total_mutes}",
        f"🚫 کل بن‌ها: {total_bans}",
        f"🔇 میوت‌های فعال: {active_mutes}",
        f"🚫 بن‌های فعال: {active_bans}",
        "",
        "🏆 پرتخلف‌ترین اعضا:",
    ]
    if not top:
        lines.append("— موردی ثبت نشده —")
    else:
        for uid, rec in top:
            score = rec.get("risk_score", 0)
            lines.append(f"• {name_of(uid)} — اخطار:{rec['warn_total']} میوت:{rec['mute_total']} بن:{rec['ban_total']} (ریسک:{int(score)})")

    admin_rows = _ADMIN_STATS.get(chat_id, {})
    if admin_rows:
        lines.append("")
        lines.append("👮 اقدامات هر ادمین:")
        for admin_id, counts in admin_rows.items():
            try:
                import bot as host
                aname = host._user_display_names.get(admin_id, str(admin_id))
            except Exception:
                aname = str(admin_id)
            parts = ", ".join(f"{k}:{v}" for k, v in counts.items())
            lines.append(f"• {aname} — {parts}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 💾 Persistence (مستقل، طبق الگوی lock_engine.py / spam_engine.py)
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "data": {
            str(cid): {
                str(uid): rec for uid, rec in users.items()
            } for cid, users in _DATA.items()
        },
        "admin_stats": {
            str(cid): {str(aid): dict(counts) for aid, counts in admins.items()}
            for cid, admins in _ADMIN_STATS.items()
        },
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
            logger.warning(f"ذخیره‌ی وضعیت موتور مدیریت ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن وضعیت موتور مدیریت ناموفق بود: {e}")
        return
    try:
        for cid, users in raw.get("data", {}).items():
            for uid, rec in users.items():
                _DATA[int(cid)][int(uid)] = rec
        for cid, admins in raw.get("admin_stats", {}).items():
            for aid, counts in admins.items():
                for k, v in counts.items():
                    _ADMIN_STATS[int(cid)][int(aid)][k] = v
    except Exception as e:
        logger.warning(f"بارگذاری وضعیت موتور مدیریت ناموفق بود: {e}")
