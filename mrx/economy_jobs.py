# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY JOBS & INCOME — فاز ۲ (Module 1: درآمد + Module 2: Job System)
=================================================================================
این فاز اولین Moduleِ «بازی»ایه که روی economy_core.py (فاز ۱) سوار می‌شه:
هر تغییر پولی (کار کردن، حقوق) دقیقاً از economy_core.add_coins عبور می‌کنه،
نه مستقیم از bot._wallet. همون الگوی بقیه‌ی Engineها (state مستقل، import
لغزنده‌ی bot، Atomic Save) رو دنبال می‌کنه.

قابلیت‌ها:
    • Work ("هاپ هاپ"/"کار"//work): Base Income + Random Bonus + Streak Bonus
      (از economy_engine.daily_streak_bonus موجود، بدون ساختن Streak جدید) +
      Job Bonus + Energy/Fatigue + Cooldown + Daily Limit + Job XP
    • Job System: انتخاب/ترک/ارتقای شغل، ۱۳ شغل در ۵ Tier، Level/XP جدا برای
      هر شغل، Unlock بر اساس Total Job Level + Reputation (از profile_engine
      فعلی، بدون ساختن Reputation جدید)
    • Salary ("حقوق"//salary): پرداخت دوره‌ای/passive جدا از Work
    • با economy_missions.py یکپارچه: هر Work/Salary/Level-Up به‌صورت خودکار
      به‌عنوان Mission Progress ثبت می‌شه (بدون این‌که economy_missions چیزی
      از Job بدونه — فقط action رو صدا می‌کنیم)
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

logger = logging.getLogger("economy_jobs")

STATE_FILE = getattr(config, "ECONOMY_JOBS_STATE_FILE", "economy_jobs_state.json")

JOBS: dict = getattr(config, "JOBS", {})

_save_lock = asyncio.Lock()


def _host():
    import bot as host
    return host


# ---------------------------------------------------------------------------
# 💾 State
# ---------------------------------------------------------------------------

# شغل فعلی: chat_id -> user_id -> job_key | None
_current_job = defaultdict(dict)

# پیشرفت هر شغلی که کاربر تا حالا گرفته (حتی شغل‌های قبلی، برای Total Level):
# chat_id -> user_id -> job_key -> {"level": int, "xp": int}
_job_progress = defaultdict(lambda: defaultdict(dict))

# انرژی: chat_id -> user_id -> {"value": float, "ts": float}
_energy = defaultdict(dict)

# آخرین کار: chat_id -> user_id -> ts
_last_work_ts = defaultdict(dict)

# سقف روزانه‌ی کار: chat_id -> user_id -> {"date": str, "count": int, "earned": int}
_work_daily = defaultdict(dict)

# آخرین حقوق گرفته‌شده: chat_id -> user_id -> ts
_last_salary_ts = defaultdict(dict)

TIER_ORDER = ["COMMON", "UNCOMMON", "RARE", "EPIC", "LEGENDARY"]
TIER_EMOJI = {"COMMON": "⚪", "UNCOMMON": "🟢", "RARE": "🔵", "EPIC": "🟣", "LEGENDARY": "🟡"}


def _today_str() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


# ---------------------------------------------------------------------------
# ⚡ Energy / Fatigue
# ---------------------------------------------------------------------------

def _get_energy(chat_id: int, uid: int) -> float:
    rec = _energy[chat_id].get(uid)
    now = time.time()
    if not rec:
        _energy[chat_id][uid] = {"value": float(config.ENERGY_MAX), "ts": now}
        return float(config.ENERGY_MAX)
    elapsed_minutes = (now - rec["ts"]) / 60.0
    regen = elapsed_minutes * getattr(config, "ENERGY_REGEN_PER_MINUTE", 1.0)
    value = min(config.ENERGY_MAX, rec["value"] + regen)
    rec["value"] = value
    rec["ts"] = now
    return value


def _spend_energy(chat_id: int, uid: int, amount: float) -> None:
    _get_energy(chat_id, uid)  # سینک اول (Regen رو اعمال کن)
    rec = _energy[chat_id][uid]
    rec["value"] = max(0.0, rec["value"] - amount)


# ---------------------------------------------------------------------------
# 💼 Job data helpers
# ---------------------------------------------------------------------------

def current_job_key(chat_id: int, uid: int) -> str | None:
    return _current_job[chat_id].get(uid)


def _progress_of(chat_id: int, uid: int, job_key: str) -> dict:
    prog = _job_progress[chat_id][uid]
    if job_key not in prog:
        prog[job_key] = {"level": 1, "xp": 0}
    return prog[job_key]


def total_job_level(chat_id: int, uid: int) -> int:
    """مجموع Level همه‌ی شغل‌هایی که کاربر تا حالا داشته - معیار Unlock شغل‌های بالاتر."""
    return sum(p.get("level", 1) for p in _job_progress[chat_id][uid].values())


def xp_needed_for_level(level: int) -> int:
    return getattr(config, "JOB_XP_PER_LEVEL_BASE", 80) * level


def job_title_for_level(level: int) -> str:
    titles = getattr(config, "JOB_LEVEL_TITLES", {})
    best = ""
    for threshold, title in sorted(titles.items()):
        if level >= threshold:
            best = title
    return best


def eligibility_reason(chat_id: int, uid: int, job_key: str) -> str | None:
    """اگه کاربر واجد شرایط این شغل نیست، دلیلش رو برمی‌گردونه؛ وگرنه None."""
    import profile_engine
    job = JOBS.get(job_key)
    if not job:
        return "این شغل وجود نداره."
    need_level = job.get("req_total_level", 0)
    need_rep = job.get("req_reputation", 0)
    have_level = total_job_level(chat_id, uid)
    have_rep = profile_engine.reputation_of(chat_id, uid)
    if have_level < need_level:
        return f"نیاز به مجموع Level شغلی حداقل {need_level} داری (الان: {have_level})."
    if have_rep < need_rep:
        return f"نیاز به Reputation حداقل {need_rep} داری (الان: {have_rep})."
    return None


def salary_for(chat_id: int, uid: int, job_key: str) -> int:
    job = JOBS[job_key]
    level = _progress_of(chat_id, uid, job_key)["level"]
    growth = getattr(config, "JOB_LEVEL_SALARY_GROWTH", 0.12)
    return round(job["base_salary"] * (1 + growth * (level - 1)))


# ---------------------------------------------------------------------------
# 📈 Job XP / Level-Up
# ---------------------------------------------------------------------------

async def _grant_job_xp(chat_id: int, uid: int, job_key: str, amount: int) -> list[str]:
    """XP به شغل فعلی می‌ده و در صورت لازم Level Up می‌کنه. متن‌های Level-Up رو
    برمی‌گردونه (ممکنه چندتا Level هم‌زمان بالا بره)."""
    events: list[str] = []
    prog = _progress_of(chat_id, uid, job_key)
    prog["xp"] += amount
    job = JOBS[job_key]
    leveled = False
    while prog["xp"] >= xp_needed_for_level(prog["level"]):
        prog["xp"] -= xp_needed_for_level(prog["level"])
        prog["level"] += 1
        leveled = True
        title = job_title_for_level(prog["level"])
        title_txt = f" — عنوان جدید: {title}" if title else ""
        events.append(
            f"⭐ شغلت ({job['emoji']} {job['name']}) رفت Level {prog['level']}{title_txt}!"
        )
    if leveled:
        try:
            completed = await economy_missions.record_progress(chat_id, uid, "job_levelup", 1)
            events.extend(completed)
        except Exception as e:
            logger.warning(f"ثبت پیشرفت ماموریت job_levelup ناموفق بود: {e}")
    return events


# ---------------------------------------------------------------------------
# 📋 نمایش
# ---------------------------------------------------------------------------

def _job_line(job_key: str, job: dict, unlocked: bool, reason: str | None, is_current: bool) -> str:
    tier_dot = TIER_EMOJI.get(job["tier"], "⚪")
    mark = " ✅ (شغل فعلی)" if is_current else ""
    if unlocked:
        return f"{tier_dot} {job['emoji']} {job['name']} — حقوق پایه: {job['base_salary']} {config.CURRENCY_NAME}{mark}"
    return f"{tier_dot} {job['emoji']} {job['name']} — 🔒 {reason}"


async def jobs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مشاغل / /jobs — لیست همه‌ی شغل‌ها به ترتیب Tier، با وضعیت قفل/باز."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_jobs"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    current = current_job_key(chat.id, uid)
    lines = ["💼 مشاغل DIGIANTI", ""]
    for tier in TIER_ORDER:
        jobs_in_tier = [(k, v) for k, v in JOBS.items() if v["tier"] == tier]
        if not jobs_in_tier:
            continue
        lines.append(f"── {TIER_EMOJI[tier]} {tier} ──")
        for key, job in jobs_in_tier:
            reason = eligibility_reason(chat.id, uid, key)
            lines.append(_job_line(key, job, reason is None, reason, key == current))
        lines.append("")
    lines.append("با «انتخاب شغل [نام]» یا /choosejob <نام> یه شغل رو انتخاب کن.")
    await message.reply_text("\n".join(lines).rstrip())


async def job_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شغل / /job — وضعیت شغل فعلی کاربر."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_jobs"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    key = current_job_key(chat.id, uid)
    if not key:
        await message.reply_text(
            "هنوز هیچ شغلی انتخاب نکردی. با «مشاغل» یا /jobs لیست رو ببین و با «انتخاب شغل [نام]» یکی رو بردار."
        )
        return
    job = JOBS[key]
    prog = _progress_of(chat.id, uid, key)
    needed = xp_needed_for_level(prog["level"])
    title = job_title_for_level(prog["level"])
    energy = round(_get_energy(chat.id, uid))
    lines = [
        f"{job['emoji']} شغل فعلی: {job['name']} ({job['tier']})",
        f"⭐ Level: {prog['level']}" + (f" — {title}" if title else ""),
        f"📈 XP: {prog['xp']}/{needed}",
        f"💰 حقوق فعلی: {salary_for(chat.id, uid, key)} {config.CURRENCY_NAME}",
        f"⚡ انرژی: {energy}/{config.ENERGY_MAX}",
        f"✨ ویژگی: {job['ability']}",
        f"📊 مجموع Level کل شغل‌ها: {total_job_level(chat.id, uid)}",
    ]
    await message.reply_text("\n".join(lines))


def _find_job_by_name(name: str) -> str | None:
    name = name.strip()
    for key, job in JOBS.items():
        if name in (job["name"], key):
            return key
    return None


async def choosejob_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """انتخاب شغل [نام] / /choosejob <نام>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_jobs"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «انتخاب شغل [نام شغل]» یا /choosejob <نام>\nبرای دیدن لیست: «مشاغل»")
        return

    uid = update.effective_user.id
    name = " ".join(args)
    key = _find_job_by_name(name)
    if not key:
        await message.reply_text("همچین شغلی پیدا نشد. با «مشاغل» لیست کامل رو ببین.")
        return

    reason = eligibility_reason(chat.id, uid, key)
    if reason:
        await message.reply_text(f"❌ هنوز واجد شرایط {JOBS[key]['name']} نیستی: {reason}")
        return

    _progress_of(chat.id, uid, key)  # مطمئن شو رکورد Level/XP این شغل ساخته شده
    _current_job[chat.id][uid] = key
    job = JOBS[key]
    await message.reply_text(f"✅ شغل جدیدت: {job['emoji']} {job['name']} شد. با «کار» یا /work شروع کن.")
    try:
        completed = await economy_missions.record_progress(chat.id, uid, "choosejob", 1)
        for text in completed:
            await message.reply_text(text)
    except Exception as e:
        logger.warning(f"ثبت پیشرفت ماموریت choosejob ناموفق بود: {e}")
    await host.save_state()


async def quitjob_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ترک شغل / /quitjob — شغل فعلی رو ول می‌کنه (Level/XP همون شغل حفظ می‌مونه)."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return

    uid = update.effective_user.id
    key = current_job_key(chat.id, uid)
    if not key:
        await message.reply_text("شغلی نداری که ترکش کنی.")
        return
    job = JOBS[key]
    _current_job[chat.id][uid] = None
    await message.reply_text(
        f"✅ شغل {job['emoji']} {job['name']} رو ترک کردی. پیشرفتت (Level/XP) حفظ شده و هر وقت خواستی می‌تونی برگردی."
    )
    await host.save_state()


async def joblevel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """سطح شغل / /joblevel — جزئیات Level تمام شغل‌هایی که تا حالا داشته."""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    prog = _job_progress[chat.id][uid]
    if not prog:
        await message.reply_text("هنوز هیچ شغلی نداشتی.")
        return
    current = current_job_key(chat.id, uid)
    lines = ["📊 سابقه‌ی شغلی تو:", ""]
    for key, p in prog.items():
        job = JOBS.get(key)
        if not job:
            continue
        mark = " ✅" if key == current else ""
        needed = xp_needed_for_level(p["level"])
        lines.append(f"{job['emoji']} {job['name']}: Level {p['level']} ({p['xp']}/{needed} XP){mark}")
    lines.append(f"\n📈 مجموع Level کل: {total_job_level(chat.id, uid)}")
    await message.reply_text("\n".join(lines))


# «ارتقای شغل» به‌صورت خودکار روی Work اتفاق می‌افته؛ این دستور فقط وضعیت رو نشون می‌ده
promote_command = joblevel_command


# ---------------------------------------------------------------------------
# 💵 Work — Module 1: درآمد
# ---------------------------------------------------------------------------

def _daily_record(chat_id: int, uid: int) -> dict:
    rec = _work_daily[chat_id].get(uid)
    today = _today_str()
    if not rec or rec.get("date") != today:
        rec = {"date": today, "count": 0, "earned": 0}
        _work_daily[chat_id][uid] = rec
    return rec


async def work_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هاپ هاپ / کار / /work — عمل اصلی کسب درآمد."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_jobs"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    now = time.time()

    # 🧠 فاز ۹: Anti-Abuse سراسری (جلوی تعویض سریع بین چند Command رو می‌گیره)
    try:
        import economy_antiabuse
        wait = economy_antiabuse.check_and_mark(chat.id, uid)
        if wait > 0:
            await message.reply_text(f"⏳ یکم آروم‌تر؛ {wait:.1f} ثانیه‌ی دیگه صبر کن.")
            return
    except Exception as e:
        logger.warning(f"چک Anti-Abuse سراسری ناموفق بود: {e}")

    # 🔁 Cooldown
    last = _last_work_ts[chat.id].get(uid, 0)
    cooldown_s = getattr(config, "WORK_COOLDOWN_MINUTES", 30) * 60
    remaining = cooldown_s - (now - last)
    if remaining > 0:
        minutes = int(remaining // 60) + 1
        await message.reply_text(f"⏳ هنوز خسته‌ای؛ {minutes} دقیقه‌ی دیگه دوباره کار کن.")
        return

    # 📆 Daily Limit (Anti-Abuse / Anti-Farming)
    daily = _daily_record(chat.id, uid)
    limit = getattr(config, "WORK_DAILY_LIMIT", 40)
    if daily["count"] >= limit:
        await message.reply_text(f"📆 امروز به سقف {limit} بار کار رسیدی؛ فردا دوباره بیا.")
        return

    # ⚡ Energy / Fatigue
    key = current_job_key(chat.id, uid)
    job = JOBS.get(key) if key else None
    energy_cost = job["energy_cost"] if job else getattr(config, "ENERGY_COST_NO_JOB", 10)
    energy = _get_energy(chat.id, uid)
    if energy < energy_cost:
        minutes_needed = int((energy_cost - energy) / getattr(config, "ENERGY_REGEN_PER_MINUTE", 1.0)) + 1
        await message.reply_text(
            f"😴 انرژیت کمه ({round(energy)}/{config.ENERGY_MAX})؛ حدود {minutes_needed} دقیقه‌ی دیگه استراحت کن."
        )
        return

    # 💰 محاسبه‌ی درآمد
    if job:
        base = salary_for(chat.id, uid, key)
    else:
        base = random.randint(config.WORK_INCOME_NO_JOB_MIN, config.WORK_INCOME_NO_JOB_MAX)

    lucky_used = False
    try:
        import economy_inventory
        if economy_inventory.has_lucky_work(chat.id, uid):
            economy_inventory.consume_lucky_work(chat.id, uid)
            lucky_used = True
    except Exception as e:
        logger.warning(f"چک کردن lucky_charm ناموفق بود: {e}")

    # 🎁 فاز ۹: شاید همین الان یه رویداد جدید سطح گروه شروع بشه
    event_announcement = None
    try:
        import economy_events
        event_announcement = economy_events.maybe_trigger(chat.id)
    except Exception as e:
        logger.warning(f"چک رویداد اقتصادی ناموفق بود: {e}")

    failed = (not lucky_used) and job is not None and random.random() < job.get("fail_chance", 0)
    reward = base
    bonus_lines = []
    if lucky_used:
        bonus_lines.append("🍀 خرگوش خوش‌شانسی جلوی شکست رو گرفت!")

    if not failed and random.random() < getattr(config, "WORK_RANDOM_BONUS_CHANCE", 0.15):
        bonus = random.randint(config.WORK_RANDOM_BONUS_MIN, config.WORK_RANDOM_BONUS_MAX)
        reward += bonus
        bonus_lines.append(f"🎁 جایزه‌ی شانسی: +{bonus}")

    streak_bonus = 0
    if not failed:
        try:
            import economy_engine
            streak_bonus = economy_engine.daily_streak_bonus(chat.id, uid)
        except Exception:
            streak_bonus = 0
        if streak_bonus:
            reward += streak_bonus
            bonus_lines.append(f"🔥 جایزه‌ی Streak: +{streak_bonus}")

    if failed:
        reward = round(reward * getattr(config, "WORK_FAIL_COIN_FRACTION", 0.35))

    # ⚡ فاز ۷: Boost عمومی سکه (boost_coin از فروشگاه، سیستم فعلی economy_engine)
    try:
        import economy_engine
        reward = economy_engine.apply_coin_multiplier(chat.id, uid, reward)
    except Exception as e:
        logger.warning(f"اعمال Coin Boost ناموفق بود: {e}")

    # 🎁 فاز ۹: ضریب رویداد سطح گروه (Double Income / Bonus Hour)
    try:
        import economy_events
        ev_mult = economy_events.income_multiplier(chat.id)
        if ev_mult != 1.0:
            reward = round(reward * ev_mult)
            bonus_lines.append(f"🎁 رویداد فعال: ضریب درآمد ×{ev_mult}")
    except Exception as e:
        logger.warning(f"اعمال ضریب رویداد ناموفق بود: {e}")

    _spend_energy(chat.id, uid, energy_cost)
    _last_work_ts[chat.id][uid] = now
    daily["count"] += 1
    daily["earned"] += reward

    new_balance = await economy_core.add_coins(
        chat.id, uid, reward, kind="work",
        note=(f"کار — {job['name']}" if job else "کار — بدون شغل"),
    )

    xp_gain = getattr(config, "WORK_XP_BASE", 12)
    # ⚡ فاز ۷: Boost عمومی XP + Boost اختصاصی شغل (job_boost)
    try:
        import economy_engine
        xp_gain = economy_engine.apply_xp_multiplier(chat.id, uid, xp_gain)
        if economy_engine.has_active_item_type(chat.id, uid, "boost_job"):
            xp_gain = round(xp_gain * getattr(config, "JOB_BOOST_MULTIPLIER", 1.5))
            bonus_lines.append("💼 تقویت شغل فعاله!")
    except Exception as e:
        logger.warning(f"اعمال Job/XP Boost ناموفق بود: {e}")
    try:
        import economy_events
        job_ev_mult = economy_events.job_xp_multiplier(chat.id)
        if job_ev_mult != 1.0:
            xp_gain = round(xp_gain * job_ev_mult)
    except Exception as e:
        logger.warning(f"اعمال ضریب رویداد Job XP ناموفق بود: {e}")
    level_events: list[str] = []
    if job:
        level_events = await _grant_job_xp(chat.id, uid, key, xp_gain)

    mission_events: list[str] = []
    try:
        mission_events += await economy_missions.record_progress(chat.id, uid, "work", 1)
        mission_events += await economy_missions.record_progress(chat.id, uid, "earn", reward)
    except Exception as e:
        logger.warning(f"ثبت پیشرفت ماموریت work/earn ناموفق بود: {e}")

    if failed:
        header = "😓 کار امروزت خیلی خوب پیش نرفت..."
    elif job:
        header = f"{job['emoji']} کار کردی به‌عنوان {job['name']}!"
    else:
        header = "💵 یه کار موقت انجام دادی."

    lines = [header, f"+{reward} {config.CURRENCY_EMOJI}"]
    if event_announcement:
        lines.insert(0, f"🎉 {event_announcement}")
    lines.extend(bonus_lines)
    lines.append(f"💰 موجودی: {new_balance} {config.CURRENCY_NAME}")
    lines.append(f"⚡ انرژی باقی‌مونده: {round(_get_energy(chat.id, uid))}/{config.ENERGY_MAX}")
    lines.extend(level_events)
    lines.extend(mission_events)
    try:
        import economy_achievements
        lines.extend(economy_achievements.check_all(chat.id, uid))
    except Exception as e:
        logger.warning(f"چک دستاوردهای اقتصادی ناموفق بود: {e}")
    await message.reply_text("\n".join(lines))
    await host.save_state()


async def income_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """درآمد / /income — خلاصه‌ی وضعیت کاری امروز، بدون کار کردن."""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    daily = _daily_record(chat.id, uid)
    key = current_job_key(chat.id, uid)
    limit = getattr(config, "WORK_DAILY_LIMIT", 40)

    now = time.time()
    last = _last_work_ts[chat.id].get(uid, 0)
    cooldown_s = getattr(config, "WORK_COOLDOWN_MINUTES", 30) * 60
    remaining = cooldown_s - (now - last)
    cooldown_txt = "آماده‌ی کار ✅" if remaining <= 0 else f"{int(remaining // 60) + 1} دقیقه‌ی دیگه"

    lines = [
        "💵 وضعیت درآمد امروزت:",
        f"🔁 تعداد کار امروز: {daily['count']}/{limit}",
        f"💰 درآمد از راه کار امروز: {daily['earned']} {config.CURRENCY_NAME}",
        f"⏳ وضعیت Cooldown: {cooldown_txt}",
        f"⚡ انرژی: {round(_get_energy(chat.id, uid))}/{config.ENERGY_MAX}",
    ]
    if key:
        lines.append(f"💼 شغل فعلی: {JOBS[key]['emoji']} {JOBS[key]['name']}")
    else:
        lines.append("💼 شغل فعلی: نداری (با «مشاغل» یکی انتخاب کن)")
    await message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# 🏦 Salary — دریافت دوره‌ای/Passive، جدا از Work
# ---------------------------------------------------------------------------

async def salary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حقوق / /salary — دریافت دوره‌ای حقوق شغل فعلی (جدا از کار کردن)."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_jobs"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    key = current_job_key(chat.id, uid)
    if not key:
        await message.reply_text("شغلی نداری که حقوق بگیری. اول با «مشاغل» یه شغل انتخاب کن.")
        return

    now = time.time()
    last = _last_salary_ts[chat.id].get(uid, 0)
    cooldown_s = getattr(config, "SALARY_COOLDOWN_HOURS", 6) * 3600
    remaining = cooldown_s - (now - last)
    if remaining > 0:
        hours = int(remaining // 3600) + 1
        await message.reply_text(f"⏳ حقوقت هنوز واریز نشده؛ حدود {hours} ساعت دیگه دوباره امتحان کن.")
        return

    job = JOBS[key]
    amount = salary_for(chat.id, uid, key)
    _last_salary_ts[chat.id][uid] = now
    new_balance = await economy_core.add_coins(
        chat.id, uid, amount, kind="salary", note=f"حقوق {job['name']}"
    )
    lines = [
        f"🏦 حقوقت به‌عنوان {job['emoji']} {job['name']} واریز شد: +{amount} {config.CURRENCY_EMOJI}",
        f"💰 موجودی: {new_balance} {config.CURRENCY_NAME}",
    ]
    try:
        completed = await economy_missions.record_progress(chat.id, uid, "salary_claim", 1)
        lines.extend(completed)
    except Exception as e:
        logger.warning(f"ثبت پیشرفت ماموریت salary_claim ناموفق بود: {e}")
    await message.reply_text("\n".join(lines))
    await host.save_state()


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "current_job": {str(c): {str(u): k for u, k in v.items()} for c, v in _current_job.items()},
        "job_progress": {
            str(c): {str(u): dict(jobs) for u, jobs in v.items()}
            for c, v in _job_progress.items()
        },
        "energy": {str(c): {str(u): dict(v2) for u, v2 in v.items()} for c, v in _energy.items()},
        "last_work_ts": {str(c): dict(v) for c, v in _last_work_ts.items()},
        "work_daily": {str(c): {str(u): dict(v2) for u, v2 in v.items()} for c, v in _work_daily.items()},
        "last_salary_ts": {str(c): dict(v) for c, v in _last_salary_ts.items()},
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
            logger.warning(f"ذخیره‌ی state موتور Jobs ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور Jobs ناموفق بود: {e}")
        return

    for cid, v in data.get("current_job", {}).items():
        _current_job[int(cid)] = {int(u): k for u, k in v.items()}
    for cid, v in data.get("job_progress", {}).items():
        for uid, jobs in v.items():
            _job_progress[int(cid)][int(uid)] = jobs
    for cid, v in data.get("energy", {}).items():
        _energy[int(cid)] = {int(u): v2 for u, v2 in v.items()}
    for cid, v in data.get("last_work_ts", {}).items():
        _last_work_ts[int(cid)] = {int(u): ts for u, ts in v.items()}
    for cid, v in data.get("work_daily", {}).items():
        _work_daily[int(cid)] = {int(u): v2 for u, v2 in v.items()}
    for cid, v in data.get("last_salary_ts", {}).items():
        _last_salary_ts[int(cid)] = {int(u): ts for u, ts in v.items()}

    logger.info("وضعیت موتور Jobs از فایل بارگذاری شد.")
