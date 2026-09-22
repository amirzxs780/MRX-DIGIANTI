# -*- coding: utf-8 -*-
"""
DIGIANTI PROFILE ENGINE 1.0
============================
فاز ۱ ارتقای Profile / XP / Level / Nickname (طبق درخواست کاربر).

این ماژول عمداً از bot.py جدا نگه داشته شده، دقیقاً به همون دلیلی که
lock_engine.py / spam_engine.py / moderation_engine.py جدا هستن:
  - ویرایش روی bot.py غول‌پیکر به حداقل می‌رسه (ریسک خرابکاری کمتر)،
  - state این موتور توی فایل JSON مستقل خودش ذخیره می‌شه،
  - و به‌جای ساختن سیستم موازی، از ساختارهای فعلی پروژه (bot._xp، bot._wallet،
    bot._achievements، bot._owned_badges، bot._custom_titles، bot._streaks،
    bot._join_times، bot.is_admin، bot.has_permission، bot._vip_users،
    bot._xp_progress، bot._parse_target_and_reason، bot.save_state) مستقیماً
    استفاده می‌کنه. هیچ قابلیت موجودی دوباره ساخته نشده؛ فقط ارتقا داده شده.

نکته‌ی معماری: چون bot.py در import-time این ماژول رو import می‌کنه، این
ماژول از bot.py در سطح ماژول import نمی‌کنه (import حلقه‌ای). به‌جاش، هر
تابعی که به globals بات نیاز داره، در زمان اجرا `import bot as host` می‌کنه.
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

logger = logging.getLogger("profile_engine")

STATE_FILE = getattr(config, "PROFILE_ENGINE_STATE_FILE", "profile_engine_state.json")

# ---------------------------------------------------------------------------
# 💾 State (درون‌حافظه‌ای، مثل بقیه‌ی پروژه؛ با save_state/load_state پایدار می‌شه)
# ---------------------------------------------------------------------------

# 💎 Reputation: chat_id -> user_id -> امتیاز
_reputation = defaultdict(lambda: defaultdict(int))
# chat_id -> giver_id -> {target_id(str): last_ts}  (کول‌داون هدف)
_rep_last_given = defaultdict(lambda: defaultdict(dict))
# chat_id -> giver_id -> {"date": "YYYY-MM-DD", "count": n}  (سقف روزانه)
_rep_daily = defaultdict(dict)

# 👁 Profile Views: chat_id -> user_id -> تعداد بازدید
_profile_views = defaultdict(lambda: defaultdict(int))
# chat_id -> user_id -> آخرین زمانی که پروفایلش دیده شد
_profile_last_viewed = defaultdict(dict)

# 🗳 Poll counters (برای دستاوردهای Poll - مستقل از خود سیستم Poll که در bot.py می‌مونه)
_poll_votes_count = defaultdict(lambda: defaultdict(int))
_polls_created_count = defaultdict(lambda: defaultdict(int))

# 🛒 Economy counters (برای دستاورد big_spender - جمع خرج در فروشگاه)
_total_spent = defaultdict(lambda: defaultdict(int))

# 🎖 بج اصلی انتخابی کاربر برای نمایش در پروفایل: chat_id -> user_id -> badge_key
_primary_badge = defaultdict(dict)

# ---------------------------------------------------------------------------
# 🎭 PHASE 3 — RPG Stats مستقل (Prestige/Fame/Crime/Trust/Happiness/Health/
# Energy/Hunger/Stress). هرکدوم چه‌بسا از قبل به‌صورت پراکنده جایی مصرف بشن؛
# اینجا محل مرکزی و مستقل نگه‌داری‌شون هست — دقیقاً مثل Reputation که از قبل
# این‌طوری (مستقل از XP) پیاده‌سازی شده بود.
# ---------------------------------------------------------------------------

# chat_id -> user_id -> value، برای هر استت به‌صورت جدا
_stats: dict[str, defaultdict] = {
    "prestige": defaultdict(lambda: defaultdict(int)),
    "fame": defaultdict(lambda: defaultdict(int)),
    "crime": defaultdict(lambda: defaultdict(int)),
    "trust": defaultdict(lambda: defaultdict(int)),
    "happiness": defaultdict(lambda: defaultdict(int)),
    "health": defaultdict(lambda: defaultdict(int)),
    "energy": defaultdict(lambda: defaultdict(int)),
    "hunger": defaultdict(lambda: defaultdict(int)),
    "stress": defaultdict(lambda: defaultdict(int)),
}

# مقدار پیش‌فرض هر استت برای کاربری که هنوز رکورد نداره
STAT_DEFAULTS = {
    "prestige": 0, "fame": 0, "crime": 0, "trust": 50,
    "happiness": 100, "health": 100, "energy": 100, "hunger": 0, "stress": 0,
}

# محدوده‌ی مجاز هر استت. None یعنی سقف/کف نداره (فقط >= 0 اجباریه).
STAT_BOUNDS = {
    "prestige": (0, None), "fame": (0, None), "crime": (0, None),
    "trust": (0, 100), "happiness": (0, 100), "health": (0, 100),
    "energy": (0, 100), "hunger": (0, 100), "stress": (0, 100),
}

# استت‌هایی که زیرمجموعه‌ی «Player Needs» محسوب می‌شن (فاز ۱۷) و کلاً با یک
# سوییچ می‌شه خاموش‌شون کرد بدون این‌که به بقیه‌ی سیستم (Reputation/Crime/...) دست بخوره.
NEEDS_STATS = {"happiness", "health", "energy", "hunger", "stress"}

# ⏱ آخرین باری که Decay طبیعیِ Needs (فاز ۱۷) روی این کاربر اعمال شده
_last_needs_update: dict[int, dict[int, float]] = defaultdict(dict)

# 📜 تاریخچه‌ی XP: chat_id -> user_id -> [(timestamp, amount, reason), ...]
# فقط چند تای آخر نگه داشته می‌شه (جلوگیری از رشد بی‌رویه‌ی فایل State).
_xp_history: dict[int, dict[int, list]] = defaultdict(lambda: defaultdict(list))
MAX_XP_HISTORY_PER_USER = 30

_save_lock = asyncio.Lock()


def _host():
    """دسترسی دیرهنگام (runtime) به bot.py تا Import حلقه‌ای پیش نیاد."""
    import bot as host
    return host


def _needs_enabled() -> bool:
    """فاز ۱۷: سیستم Needs باید کلاً قابل خاموش‌شدن باشه، بدون خراب کردن
    بقیه‌ی استت‌ها (Prestige/Fame/Crime/Trust که Needs محسوب نمی‌شن)."""
    return getattr(config, "NEEDS_ENABLED", False)


def _apply_needs_decay(chat_id: int, uid: int) -> None:
    """فاز ۱۷: با گذشت زمان، Hunger/Stress بالا می‌ره و Happiness کم می‌شه —
    ملایم (چند واحد در ساعت) و فقط وقتی NEEDS_ENABLED روشنه. کاربر باید با
    «غذا خوردن»/«استراحت» این‌ها رو جبران کنه. Health دست‌نخورده می‌مونه
    (فعلاً هیچ منبعی مستقیم بهش آسیب نمی‌زنه، پس افت خودکارش بی‌معنیه)."""
    if not _needs_enabled():
        return
    now = time.time()
    last = _last_needs_update[chat_id].get(uid, now)
    elapsed_h = (now - last) / 3600.0
    _last_needs_update[chat_id][uid] = now
    if elapsed_h <= 0:
        return
    elapsed_h = min(elapsed_h, 72)  # سقف ۷۲ ساعت، مثل بقیه‌ی سیستم‌های Lazy پروژه

    hunger_rate = getattr(config, "NEEDS_HUNGER_DECAY_PER_HOUR", 2.0)
    stress_rate = getattr(config, "NEEDS_STRESS_DECAY_PER_HOUR", 1.5)
    happiness_rate = getattr(config, "NEEDS_HAPPINESS_DECAY_PER_HOUR", 1.0)

    adjust_stat(chat_id, uid, "hunger", round(hunger_rate * elapsed_h))
    adjust_stat(chat_id, uid, "stress", round(stress_rate * elapsed_h))
    adjust_stat(chat_id, uid, "happiness", -round(happiness_rate * elapsed_h))


def wellbeing_multiplier(chat_id: int, uid: int) -> float:
    """ترکیب Hunger/Stress/Happiness به یه ضریب ملایم (۰.۸ تا ۱.۰) که فعالیت‌های
    اقتصادی می‌تونن توی محاسبه‌ی پاداش استفاده کنن. عمداً بازه‌ش کوچیکه
    (حداکثر ۲۰٪ افت) که «باعث خراب شدن تجربه‌ی بازی نشه» (طبق قانون فاز ۱۷)؛
    وقتی NEEDS_ENABLED خاموشه همیشه ۱.۰ برمی‌گرده (بی‌اثر)."""
    if not _needs_enabled():
        return 1.0
    _apply_needs_decay(chat_id, uid)
    hunger = get_stat(chat_id, uid, "hunger")
    stress = get_stat(chat_id, uid, "stress")
    happiness = get_stat(chat_id, uid, "happiness")
    penalty = (hunger / 100 * 0.10) + (stress / 100 * 0.10) - (happiness / 100 * 0.05)
    return max(0.8, min(1.05, 1.0 - penalty))


_last_rest_ts: dict[int, dict[int, float]] = defaultdict(dict)


async def eat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """غذا خوردن / /eat — Hunger رو کم می‌کنه (هزینه‌ی نقدی داره)."""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not _needs_enabled():
        await message.reply_text("سیستم Needs (گرسنگی/استرس/شادی) توی این گروه خاموشه.")
        return
    uid = update.effective_user.id
    _apply_needs_decay(chat.id, uid)
    if get_stat(chat.id, uid, "hunger") <= 0:
        await message.reply_text("گرسنه نیستی.")
        return
    cost = getattr(config, "NEEDS_EAT_COST", 150)
    import economy_core
    try:
        await economy_core.remove_coins(chat.id, uid, cost, kind="OTHER", note="غذا")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. هزینه‌ی غذا: {cost:,} {config.CURRENCY_NAME}")
        return
    relief = getattr(config, "NEEDS_EAT_HUNGER_RELIEF", 40)
    new_hunger = adjust_stat(chat.id, uid, "hunger", -relief)
    await message.reply_text(f"🍔 غذا خوردی! گرسنگی الان: {new_hunger}/100")
    await host.save_state()


async def rest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استراحت / /rest — Stress رو کم می‌کنه و کمی Happiness می‌ده (رایگان، با Cooldown)."""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not _needs_enabled():
        await message.reply_text("سیستم Needs (گرسنگی/استرس/شادی) توی این گروه خاموشه.")
        return
    uid = update.effective_user.id
    _apply_needs_decay(chat.id, uid)
    cooldown_min = getattr(config, "NEEDS_REST_COOLDOWN_MINUTES", 30)
    last = _last_rest_ts[chat.id].get(uid, 0)
    remaining = cooldown_min * 60 - (time.time() - last)
    if remaining > 0:
        await message.reply_text(f"⏳ باید {round(remaining/60)} دقیقه‌ی دیگه صبر کنی.")
        return
    _last_rest_ts[chat.id][uid] = time.time()
    stress_relief = getattr(config, "NEEDS_REST_STRESS_RELIEF", 25)
    happiness_bonus = getattr(config, "NEEDS_REST_HAPPINESS_BONUS", 10)
    new_stress = adjust_stat(chat.id, uid, "stress", -stress_relief)
    new_happiness = adjust_stat(chat.id, uid, "happiness", happiness_bonus)
    await message.reply_text(f"😌 استراحت کردی! استرس: {new_stress}/100 — شادی: {new_happiness}/100")
    await host.save_state()


def get_stat(chat_id: int, uid: int, stat: str) -> int:
    """مقدار فعلی یک RPG Stat رو برمی‌گردونه (یا مقدار پیش‌فرضش). برای
    "energy": به‌جای نگه‌داری یه شمارنده‌ی مستقل، مستقیم از سیستم انرژی‌ای
    که economy_jobs.py از قبل داره (Regen زمانی، مقیاس ۰-۱۰۰) می‌خونه —
    طبق قانون کاربر که قابلیت موجود رو دوباره نساز؛ این یعنی فقط یک
    Source of Truth برای Energy داریم."""
    if stat == "energy":
        try:
            import economy_jobs
            return round(economy_jobs._get_energy(chat_id, uid))
        except Exception:
            pass
    if stat not in _stats:
        raise ValueError(f"استت ناشناخته: {stat}")
    store = _stats[stat]
    if chat_id in store and uid in store[chat_id]:
        return store[chat_id][uid]
    return STAT_DEFAULTS[stat]


def adjust_stat(chat_id: int, uid: int, stat: str, delta: int) -> int:
    """مقدار یک RPG Stat رو delta می‌ده (می‌تونه منفی باشه) و توی محدوده‌ی
    STAT_BOUNDS نگه‌ش می‌داره (Clamp). مقدار جدید رو برمی‌گردونه. اگه stat
    عضو NEEDS_STATS باشه و NEEDS_ENABLED خاموش باشه، هیچ تغییری اعمال
    نمی‌شه و همون مقدار فعلی (پیش‌فرض) برگردونده می‌شه — طبق قانون فاز ۱۷
    که این سیستم باید کاملاً قابل غیرفعال‌سازی باشه. "energy" مستقیم روی
    economy_jobs اعمال می‌شه (همون منطق get_stat)."""
    if stat == "energy":
        if not _needs_enabled():
            return get_stat(chat_id, uid, "energy")
        try:
            import economy_jobs
            current = economy_jobs._get_energy(chat_id, uid)
            new_value = max(0.0, min(float(config.ENERGY_MAX), current + delta))
            economy_jobs._energy[chat_id][uid] = {"value": new_value, "ts": __import__("time").time()}
            return round(new_value)
        except Exception:
            pass
    if stat not in _stats:
        raise ValueError(f"استت ناشناخته: {stat}")
    if stat in NEEDS_STATS and not _needs_enabled():
        return STAT_DEFAULTS[stat]
    current = get_stat(chat_id, uid, stat)
    new_value = current + delta
    low, high = STAT_BOUNDS[stat]
    if low is not None:
        new_value = max(low, new_value)
    if high is not None:
        new_value = min(high, new_value)
    _stats[stat][chat_id][uid] = new_value
    return new_value


def set_stat(chat_id: int, uid: int, stat: str, value: int) -> int:
    """مقدار یک RPG Stat رو دقیقاً ست می‌کنه (نه Delta). برای Adminها/Migration."""
    if stat not in _stats:
        raise ValueError(f"استت ناشناخته: {stat}")
    low, high = STAT_BOUNDS[stat]
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    _stats[stat][chat_id][uid] = value
    return value


def all_stats_of(chat_id: int, uid: int) -> dict:
    """دیکشنری همه‌ی RPG Statهای کاربر (برای Dashboard/Profile Card)."""
    return {stat: get_stat(chat_id, uid, stat) for stat in _stats}


def wealth_of(chat_id: int, uid: int) -> int:
    """Wealth = Net Worth از Economy Core. مقدار مستقل ذخیره نمی‌شه تا
    دو منبع حقیقت (Source of Truth) برای یک عدد نداشته باشیم — طبق همون
    اصل معماری پروژه (قانون ۵: قابلیت موجود رو دوباره نساز)."""
    try:
        import economy_core
        return economy_core.get_net_worth(chat_id, uid)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# 📜 XP History — فاز ۳
# ---------------------------------------------------------------------------

def record_xp_gain(chat_id: int, uid: int, amount: int, reason: str = "") -> None:
    """هر بار XP کاربری تغییر کرد (مثبت یا منفی)، اینجا لاگ می‌شه. تاریخچه به
    آخرین MAX_XP_HISTORY_PER_USER مورد محدود می‌مونه."""
    if not amount:
        return
    hist = _xp_history[chat_id][uid]
    hist.append((time.time(), amount, reason))
    if len(hist) > MAX_XP_HISTORY_PER_USER:
        del hist[: len(hist) - MAX_XP_HISTORY_PER_USER]


def get_xp_history(chat_id: int, uid: int) -> list:
    """جدیدترین تغییرات XP کاربر (جدیدترین اول)."""
    return list(reversed(_xp_history[chat_id].get(uid, [])))


# ---------------------------------------------------------------------------
# 🎖 Prestige — فاز ۳
# ---------------------------------------------------------------------------

def prestige_requirement() -> int:
    return int(getattr(config, "PRESTIGE_LEVEL_REQUIREMENT", 50))


def can_prestige(chat_id: int, uid: int) -> bool:
    host = _host()
    xp = host._xp[chat_id].get(uid, 0)
    level, _, _ = host._xp_progress(xp)
    return level >= prestige_requirement()


def prestige_up(chat_id: int, uid: int) -> int:
    """اگه کاربر به Level لازم رسیده باشه، Prestige رو یکی زیاد می‌کنه، XP رو
    صفر می‌کنه (شروع دوباره‌ی مسیر Level) و مقدار جدید Prestige رو برمی‌گردونه.
    اگه شرایط برقرار نباشه PermissionError می‌زنه. این تابع Idempotent نیست
    (هر صدازدنِ موفق یک بار Prestige اضافه می‌کنه) پس فراخوان باید can_prestige
    رو قبلش چک کنه."""
    if not can_prestige(chat_id, uid):
        raise PermissionError(
            f"برای Prestige باید حداقل به Level {prestige_requirement()} برسی."
        )
    host = _host()
    record_xp_gain(chat_id, uid, -host._xp[chat_id].get(uid, 0), reason="prestige_reset")
    host._xp[chat_id][uid] = 0
    return adjust_stat(chat_id, uid, "prestige", 1)


# ---------------------------------------------------------------------------
# 🏆 Level Rewards — فاز ۳ (config.LEVEL_REWARDS مثل ACHIEVEMENT_REWARDS)
# ---------------------------------------------------------------------------

async def grant_level_reward(chat_id: int, uid: int, new_level: int) -> tuple[int, int]:
    """اگه توی config.LEVEL_REWARDS برای این Level جایزه تعریف شده باشه،
    XP/Coin جایزه می‌ده. Coin از Economy Core عبور می‌کنه (نه Direct Wallet).
    برمی‌گردونه (xp_reward, coin_reward)."""
    rewards = getattr(config, "LEVEL_REWARDS", {})
    reward = rewards.get(new_level)
    if not reward:
        return 0, 0
    host = _host()
    xp_reward = int(reward.get("xp", 0) or 0)
    coin_reward = int(reward.get("coins", 0) or 0)
    if xp_reward:
        host._xp[chat_id][uid] += xp_reward
        record_xp_gain(chat_id, uid, xp_reward, reason=f"level_{new_level}_reward")
    if coin_reward:
        import economy_core
        try:
            await economy_core.add_coins(chat_id, uid, coin_reward, kind="REWARD",
                                          note=f"جایزه‌ی رسیدن به Level {new_level}",
                                          idempotency_key=f"levelreward:{chat_id}:{uid}:{new_level}")
        except economy_core.DuplicateTransactionError:
            # این Level قبلاً جایزه‌ش گرفته شده (مثلاً به‌خاطر Prestige دوباره
            # به همین Level رسیده) — طبق قانون Anti-Duplicate-Reward، دوباره داده نمی‌شه.
            coin_reward = 0
    return xp_reward, coin_reward


def _today_str() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "reputation": {str(c): dict(v) for c, v in _reputation.items()},
        "rep_last_given": {str(c): {str(g): dict(t) for g, t in v.items()} for c, v in _rep_last_given.items()},
        "rep_daily": {str(c): dict(v) for c, v in _rep_daily.items()},
        "profile_views": {str(c): dict(v) for c, v in _profile_views.items()},
        "profile_last_viewed": {str(c): dict(v) for c, v in _profile_last_viewed.items()},
        "poll_votes_count": {str(c): dict(v) for c, v in _poll_votes_count.items()},
        "polls_created_count": {str(c): dict(v) for c, v in _polls_created_count.items()},
        "total_spent": {str(c): dict(v) for c, v in _total_spent.items()},
        "primary_badge": {str(c): dict(v) for c, v in _primary_badge.items()},
        # فاز ۳: RPG Stats + XP History
        "stats": {stat: {str(c): dict(v) for c, v in store.items()} for stat, store in _stats.items()},
        "xp_history": {str(c): {str(u): h for u, h in v.items()} for c, v in _xp_history.items()},
        # فاز ۱۷: زمان‌بندی Needs
        "last_needs_update": {str(c): dict(v) for c, v in _last_needs_update.items()},
        "last_rest_ts": {str(c): dict(v) for c, v in _last_rest_ts.items()},
    }


def _write_state_file(data: dict):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


async def save_state():
    """صدا زده می‌شه از bot.py -> save_state()."""
    async with _save_lock:
        try:
            await asyncio.to_thread(_write_state_file, _collect_state())
        except Exception as e:
            logger.warning(f"ذخیره‌ی state موتور پروفایل ناموفق بود: {e}")


def load_state():
    """صدا زده می‌شه از bot.py -> load_state()."""
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور پروفایل ناموفق بود: {e}")
        return

    for cid, v in data.get("reputation", {}).items():
        _reputation[int(cid)] = defaultdict(int, {int(u): s for u, s in v.items()})
    for cid, v in data.get("rep_last_given", {}).items():
        _rep_last_given[int(cid)] = defaultdict(dict, {int(g): {str(t): ts for t, ts in tt.items()} for g, tt in v.items()})
    for cid, v in data.get("rep_daily", {}).items():
        _rep_daily[int(cid)] = {int(u): d for u, d in v.items()}
    for cid, v in data.get("profile_views", {}).items():
        _profile_views[int(cid)] = defaultdict(int, {int(u): n for u, n in v.items()})
    for cid, v in data.get("profile_last_viewed", {}).items():
        _profile_last_viewed[int(cid)] = {int(u): ts for u, ts in v.items()}
    for cid, v in data.get("poll_votes_count", {}).items():
        _poll_votes_count[int(cid)] = defaultdict(int, {int(u): n for u, n in v.items()})
    for cid, v in data.get("polls_created_count", {}).items():
        _polls_created_count[int(cid)] = defaultdict(int, {int(u): n for u, n in v.items()})
    for cid, v in data.get("total_spent", {}).items():
        _total_spent[int(cid)] = defaultdict(int, {int(u): n for u, n in v.items()})
    for cid, v in data.get("primary_badge", {}).items():
        _primary_badge[int(cid)] = {int(u): b for u, b in v.items()}

    # فاز ۳: RPG Stats + XP History (کلید جدید — روی داده‌ی قدیمی که این
    # فیلدها رو نداره، .get() با dict خالی امن عمل می‌کنه و پیش‌فرض‌ها
    # همون STAT_DEFAULTS می‌مونن)
    for stat, per_chat in data.get("stats", {}).items():
        if stat not in _stats:
            continue
        for cid, v in per_chat.items():
            _stats[stat][int(cid)] = defaultdict(int, {int(u): n for u, n in v.items()})
    for cid, v in data.get("xp_history", {}).items():
        _xp_history[int(cid)] = defaultdict(list, {int(u): [tuple(e) for e in h] for u, h in v.items()})
    for cid, v in data.get("last_needs_update", {}).items():
        _last_needs_update[int(cid)] = {int(u): ts for u, ts in v.items()}
    for cid, v in data.get("last_rest_ts", {}).items():
        _last_rest_ts[int(cid)] = {int(u): ts for u, ts in v.items()}

    logger.info("وضعیت موتور پروفایل از فایل بارگذاری شد.")


# ---------------------------------------------------------------------------
# 🏅 Achievement rewards (روی همون bot._achievements موجود کار می‌کنه)
# ---------------------------------------------------------------------------

def grant_rewards_for_unlocked(chat_id: int, uid: int, unlocked_keys: list) -> tuple[int, int]:
    """برای دستاوردهای تازه‌آزادشده، طبق config.ACHIEVEMENT_REWARDS، XP/Coin جایزه می‌ده.
    برمی‌گردونه (مجموع xp اضافه‌شده, مجموع coin اضافه‌شده)."""
    if not unlocked_keys:
        return 0, 0
    host = _host()
    rewards = getattr(config, "ACHIEVEMENT_REWARDS", {})
    total_xp = total_coins = 0
    for key in unlocked_keys:
        r = rewards.get(key)
        if not r:
            continue
        xp_gain = int(r.get("xp", 0) or 0)
        coin_gain = int(r.get("coins", 0) or 0)
        if xp_gain:
            host._xp[chat_id][uid] += xp_gain
            total_xp += xp_gain
        if coin_gain:
            host._wallet[chat_id][uid] += coin_gain
            total_coins += coin_gain
    return total_xp, total_coins


def _unlock(chat_id: int, uid: int, key: str) -> bool:
    """کلید دستاورد رو (اگه قبلاً نبوده) روی همون bot._achievements آزاد می‌کنه."""
    host = _host()
    unlocked = host._achievements[chat_id][uid]
    if key in unlocked:
        return False
    unlocked.add(key)
    return True


def check_rank_and_membership_achievements(chat_id: int, uid: int) -> list:
    """دستاوردهای رتبه (Top 10 / Top 3) و عضو اولیه (Early Member) - چون به مرتب‌سازی
    کل گروه نیاز دارن، هر پیام چک نمی‌شن؛ در لحظه‌ی مشاهده‌ی پروفایل/رتبه چک می‌شن."""
    host = _host()
    newly = []

    ranking = sorted(host._xp[chat_id].items(), key=lambda kv: kv[1], reverse=True)
    rank = next((i + 1 for i, (u, _x) in enumerate(ranking) if u == uid), None)
    if rank is not None:
        for key, meta in getattr(config, "RANK_ACHIEVEMENTS", {}).items():
            if rank <= meta["rank_threshold"] and _unlock(chat_id, uid, key):
                newly.append(key)

    threshold = getattr(config, "EARLY_MEMBER_THRESHOLD", 50)
    join_ranking = sorted(host._join_times[chat_id].items(), key=lambda kv: kv[1])
    join_rank = next((i + 1 for i, (u, _t) in enumerate(join_ranking) if u == uid), None)
    if join_rank is not None and join_rank <= threshold and "early_member" in getattr(config, "ACHIEVEMENT_REWARDS", {}):
        if _unlock(chat_id, uid, "early_member"):
            newly.append("early_member")

    if newly:
        grant_rewards_for_unlocked(chat_id, uid, newly)
    return newly


def on_poll_created(chat_id: int, uid: int) -> list:
    _polls_created_count[chat_id][uid] += 1
    count = _polls_created_count[chat_id][uid]
    newly = []
    for key, meta in getattr(config, "POLL_ACHIEVEMENTS", {}).items():
        threshold = meta.get("created_threshold")
        if threshold is not None and count >= threshold and _unlock(chat_id, uid, key):
            newly.append(key)
    if newly:
        grant_rewards_for_unlocked(chat_id, uid, newly)
    return newly


def on_poll_voted(chat_id: int, uid: int) -> list:
    _poll_votes_count[chat_id][uid] += 1
    count = _poll_votes_count[chat_id][uid]
    newly = []
    for key, meta in getattr(config, "POLL_ACHIEVEMENTS", {}).items():
        threshold = meta.get("votes_threshold")
        if threshold is not None and count >= threshold and _unlock(chat_id, uid, key):
            newly.append(key)
    if newly:
        grant_rewards_for_unlocked(chat_id, uid, newly)
    return newly


def on_shop_purchase(chat_id: int, uid: int, price: int) -> list:
    _total_spent[chat_id][uid] += max(0, int(price))
    newly = []
    big_threshold = getattr(config, "BIG_SPENDER_THRESHOLD", 1000)
    if _total_spent[chat_id][uid] >= big_threshold and _unlock(chat_id, uid, "big_spender"):
        newly.append("big_spender")
    if newly:
        grant_rewards_for_unlocked(chat_id, uid, newly)
    return newly


# ---------------------------------------------------------------------------
# 🎖 Badge sync (نقشی/دستاوردی - خودکار، بدون /buy)
# ---------------------------------------------------------------------------

def sync_badges(chat_id: int, uid: int) -> None:
    """بج‌های نقشی (OWNER/ADMIN/VIP) و دستاوردی رو با وضعیت فعلی کاربر Sync می‌کنه."""
    host = _host()
    owned = host._owned_badges[chat_id][uid]
    unlocked_achievements = host._achievements[chat_id].get(uid, set())
    owner_ids = getattr(config, "OWNER_IDS", []) or []

    for key, meta in getattr(config, "BADGE_DEFINITIONS", {}).items():
        auto = meta.get("auto_type")
        should_have = False
        if auto == "owner":
            should_have = uid in owner_ids
        elif auto == "admin":
            should_have = host.is_admin(uid)
        elif auto == "vip":
            should_have = uid in host._vip_users[chat_id]
        elif auto == "achievement":
            should_have = meta.get("requires") in unlocked_achievements
        if should_have:
            owned.add(key)


def badge_emoji(key: str) -> str:
    """emoji یک بج رو، چه از فروشگاه (config.SHOP_ITEMS) چه از بج‌های نقشی/دستاوردی
    (config.BADGE_DEFINITIONS) پیدا می‌کنه."""
    shop_items = getattr(config, "SHOP_ITEMS", {})
    if key in shop_items:
        return shop_items[key].get("emoji", "🎖")
    badge_defs = getattr(config, "BADGE_DEFINITIONS", {})
    if key in badge_defs:
        return badge_defs[key].get("emoji", "🎖")
    return "🎖"


def badge_label(key: str) -> str:
    shop_items = getattr(config, "SHOP_ITEMS", {})
    if key in shop_items:
        return shop_items[key].get("name", key)
    badge_defs = getattr(config, "BADGE_DEFINITIONS", {})
    if key in badge_defs:
        return f"{badge_defs[key].get('emoji', '🎖')} {badge_defs[key].get('label', key)}"
    return key


def all_achievement_labels() -> dict:
    """همه‌ی دستاوردهای قابل‌آزادکردن رو (از هر منبعی که ازش میاد) یک‌جا برمی‌گردونه:
    key -> label. برای دقیق بودن مخرج X/Y توی پروفایل و کامل بودن خروجی /achievements."""
    merged = {}
    for key, meta in getattr(config, "ACTIVITY_ACHIEVEMENTS", {}).items():
        merged[key] = meta.get("label", key)
    for key, meta in getattr(config, "RANK_ACHIEVEMENTS", {}).items():
        merged[key] = meta.get("label", key)
    for key, meta in getattr(config, "POLL_ACHIEVEMENTS", {}).items():
        merged[key] = meta.get("label", key)
    for key, meta in getattr(config, "ECONOMY_ACHIEVEMENTS", {}).items():
        merged[key] = meta.get("label", key)
    for key, label in getattr(config, "EXTRA_ACHIEVEMENT_LABELS", {}).items():
        merged[key] = label
    return merged


# ---------------------------------------------------------------------------
# 💎 Reputation
# ---------------------------------------------------------------------------

def reputation_of(chat_id: int, uid: int) -> int:
    return _reputation[chat_id].get(uid, 0)


def give_reputation(chat_id: int, giver_id: int, target_id: int) -> str:
    """طبق قوانین ضد سوءاستفاده، Rep می‌ده و پیام نتیجه رو برمی‌گردونه (هیچ‌وقت Exception نمی‌ده)."""
    if not getattr(config, "REPUTATION_ENABLED", True):
        return "سیستم Reputation فعلاً خاموشه."
    if giver_id == target_id:
        return "❌ نمی‌تونی به خودت Reputation بدی."

    daily_limit = getattr(config, "REPUTATION_DAILY_LIMIT", 5)
    target_cooldown_h = getattr(config, "REPUTATION_TARGET_COOLDOWN_HOURS", 24)
    amount = getattr(config, "REPUTATION_GIVE_AMOUNT", 1)

    today = _today_str()
    daily = _rep_daily[chat_id].get(giver_id)
    if not daily or daily.get("date") != today:
        daily = {"date": today, "count": 0}
    if daily["count"] >= daily_limit:
        return f"❌ به سقف روزانه‌ی Reputation ({daily_limit} بار) رسیدی؛ فردا دوباره تلاش کن."

    last_given = _rep_last_given[chat_id][giver_id].get(str(target_id))
    now = time.time()
    if last_given and now - last_given < target_cooldown_h * 3600:
        remaining_h = round((target_cooldown_h * 3600 - (now - last_given)) / 3600, 1)
        return f"❌ همین اواخر به این کاربر Reputation دادی؛ حدود {remaining_h} ساعت دیگه دوباره می‌تونی."

    _reputation[chat_id][target_id] += amount
    _rep_last_given[chat_id][giver_id][str(target_id)] = now
    daily["count"] += 1
    _rep_daily[chat_id][giver_id] = daily

    return f"💎 Reputation ثبت شد! (+{amount})"


async def rep_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    target_id, name, _reason = host._parse_target_and_reason(update)
    if target_id is None:
        await message.reply_text("استفاده: روی پیام یه نفر ریپلای بزن و بنویس /rep")
        return
    result = give_reputation(chat.id, update.effective_user.id, target_id)
    await message.reply_text(f"{result}\n👤 گیرنده: {name} — 💎 مجموع Reputation: {reputation_of(chat.id, target_id)}")
    await host.save_state()


# ---------------------------------------------------------------------------
# 👁 Profile Views
# ---------------------------------------------------------------------------

def record_view(chat_id: int, viewer_id: int, target_id: int) -> None:
    if not getattr(config, "PROFILE_VIEWS_ENABLED", True):
        return
    if viewer_id == target_id:
        return  # دیدن پروفایل خودت حساب نمی‌شه
    _profile_views[chat_id][target_id] += 1
    _profile_last_viewed[chat_id][target_id] = time.time()


def view_stats(chat_id: int, uid: int) -> tuple[int, float | None]:
    count = _profile_views[chat_id].get(uid, 0)
    last_ts = _profile_last_viewed[chat_id].get(uid)
    return count, last_ts


# ---------------------------------------------------------------------------
# 🏷 Nickname (لقب) — جلوگیری از جعل ادمین/اونر (علاوه بر فیلتر کلمات ممنوعه‌ی فعلی)
# ---------------------------------------------------------------------------

def validate_nickname(user_id: int, title: str) -> str | None:
    """اگه لقب مجاز نباشه، پیام خطا برمی‌گردونه؛ وگرنه None (یعنی مجازه)."""
    host = _host()
    low = title.lower()
    if not host.is_admin(user_id):
        for bad in getattr(config, "NICKNAME_IMPERSONATION_WORDS", []):
            if bad.lower() in low:
                return "❌ این لقب مجاز نیست (شبیه‌سازی نقش ادمین/مالک ممنوعه)."
    return None


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/mystats یا «وضعیت من» - نمایش RPG Statهای کاربر: Prestige/Fame/Wealth/Crime/
    Trust/Happiness/Health/Energy/Hunger/Stress. اگه NEEDS_ENABLED خاموش
    باشه، ستون‌های Needs (Health/Energy/Hunger/Stress/Happiness) اصلاً نشون
    داده نمی‌شن (طبق قانون فاز ۱۷: کاملاً قابل خاموش‌شدن)."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    target_id, name, _reason = host._parse_target_and_reason(update)
    if target_id is None:
        target_id = update.effective_user.id
        name = update.effective_user.first_name or update.effective_user.username or str(target_id)

    _apply_needs_decay(chat.id, target_id)  # فاز ۱۷: قبل از نمایش، Decay رو اعمال کن

    stats = all_stats_of(chat.id, target_id)
    lines = [f"📊 آمار {name}:", f"🎖 Prestige: {stats['prestige']}",
             f"⭐ Fame: {stats['fame']}", f"💎 Wealth: {wealth_of(chat.id, target_id):,}",
             f"🚨 Crime: {stats['crime']}", f"🤝 Trust: {stats['trust']}/100"]
    if _needs_enabled():
        lines += [
            f"😊 Happiness: {stats['happiness']}/100",
            f"❤️ Health: {stats['health']}/100",
            f"⚡ Energy: {stats['energy']}/100",
            f"🍔 Hunger: {stats['hunger']}/100",
            f"😰 Stress: {stats['stress']}/100",
        ]
    await message.reply_text("\n".join(lines))


async def prestige_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/prestige یا «پرستیژ» - اگه به Level لازم رسیده باشی، Prestige رو یکی
    زیاد می‌کنه و XP رو صفر می‌کنه (شروع دوباره‌ی مسیر Level با یک نشان
    Prestige جدید روی پروفایل)."""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    user = update.effective_user
    if not can_prestige(chat.id, user.id):
        await message.reply_text(
            f"❌ برای Prestige باید حداقل به Level {prestige_requirement()} برسی."
        )
        return
    new_prestige = prestige_up(chat.id, user.id)
    await message.reply_text(
        f"🎖 تبریک! حالا Prestige {new_prestige} هستی. Level و XP از نو شروع شد."
    )
    host = _host()
    await host.save_state()


# ---------------------------------------------------------------------------
# 🏆 Collections — فاز ۲۴
# ---------------------------------------------------------------------------

def collection_summary(chat_id: int, uid: int) -> dict:
    """درصد تکمیل هر Collection (Cars/Houses/Businesses/Badges/Pets). هر
    دسته‌ای که ماژولش لود نشه یا کاتالوگش خالی باشه، بی‌صدا از خروجی حذف
    می‌شه (به‌جای کرش یا نمایش ۰٪ گمراه‌کننده)."""
    result = {}

    try:
        import economy_vehicle
        owned_types = {v["type"] for v in economy_vehicle.owned(chat_id, uid)}
        total = len(economy_vehicle.TYPES)
        if total:
            result["🚗 خودروها"] = (len(owned_types), total)
    except Exception:
        pass

    try:
        import economy_property
        owned_types = {p["type"] for p in economy_property.owned(chat_id, uid)}
        total = len(economy_property.TYPES)
        if total:
            result["🏠 املاک"] = (len(owned_types), total)
    except Exception:
        pass

    try:
        import economy_business
        owned_types = {b["type"] for b in economy_business.owned(chat_id, uid)}
        total = len(economy_business.TYPES)
        if total:
            result["🏢 کسب‌وکارها"] = (len(owned_types), total)
    except Exception:
        pass

    try:
        import economy_pet
        owned_species = economy_pet.species_seen(chat_id, uid)
        total = len(economy_pet.SPECIES)
        if total:
            result["🐾 پت‌ها"] = (len(owned_species), total)
    except Exception:
        pass

    try:
        host = _host()
        owned_badges = host._owned_badges.get(chat_id, {}).get(uid, set())
        total = len([k for k, v in config.SHOP_ITEMS.items() if not v.get("duration_hours")])
        if total:
            result["🎖 بج‌های دائمی"] = (len(owned_badges & set(config.SHOP_ITEMS.keys())), total)
    except Exception:
        pass

    return result


async def collections_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """کالکشن من / /collections — درصد تکمیل هر دسته + درصد کلی."""
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    summary = collection_summary(chat.id, uid)
    if not summary:
        await message.reply_text("هنوز چیزی برای Collection نداری.")
        return

    lines = ["🏆 کالکشن‌های تو:", ""]
    total_owned = total_all = 0
    for label, (owned, total) in summary.items():
        pct = (owned / total * 100) if total else 0
        bar_len = 10
        filled = round(bar_len * owned / total) if total else 0
        bar = "▰" * filled + "▱" * (bar_len - filled)
        lines.append(f"{label}: {owned}/{total} [{bar}] {pct:.0f}٪")
        total_owned += owned
        total_all += total
    overall = (total_owned / total_all * 100) if total_all else 0
    lines.append("")
    lines.append(f"📊 تکمیل کلی: {overall:.1f}٪")
    await message.reply_text("\n".join(lines))


async def lqab_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/lqab - لقب فعلی، لقب خودکار سطح، و شرایط لقب‌های ویژه رو نشون می‌ده."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    user = update.effective_user
    custom = host._custom_titles[chat.id].get(user.id)
    xp = host._xp[chat.id].get(user.id, 0)
    level, _, _ = host._xp_progress(xp)
    auto_title = host._auto_title_for_level(level)

    lines = [f"🏷️ لقب فعلی: {custom or auto_title or '—'}"]
    lines.append(f"⭐ لقب خودکار (بر اساس Level {level}): {auto_title or '—'}")
    if host.is_admin(user.id):
        lines.append("🛡 لقب ویژه‌ی در دسترس: ADMIN")
    if user.id in getattr(config, "OWNER_IDS", []):
        lines.append("👑 لقب ویژه‌ی در دسترس: OWNER")
    if user.id in host._vip_users[chat.id]:
        lines.append("💎 لقب ویژه‌ی در دسترس: VIP")
    lines.append("\nبرای تنظیم لقب دلخواه: /setlqab <لقب>")
    await message.reply_text("\n".join(lines))


async def toplqab_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/toplqab - لیست کاربرانی که لقب اختصاصی دارن (مرتب‌شده بر اساس XP)."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    titled = list(host._custom_titles[chat.id].items())
    if not titled:
        await message.reply_text("هنوز هیچکس لقب اختصاصی تنظیم نکرده. با /setlqab شروع کن.")
        return
    titled.sort(key=lambda kv: host._xp[chat.id].get(kv[0], 0), reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏷️ لقب‌های گروه:"]
    for i, (uid, title) in enumerate(titled[:20]):
        name = host._user_display_names.get(uid, str(uid))
        prefix = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{prefix} {name} — «{title}»")
    await message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# 🎨 Profile Card
# ---------------------------------------------------------------------------

def _progress_bar(fraction: float, width: int = 10) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled)


def build_profile_card(chat_id: int, viewer_id: int, target_user) -> str:
    """کارت پروفایل حرفه‌ای فاز ۱ - با سبک فعلی DIGIANTI (همون قاب‌ها/فونت که بقیه‌ی
    کارت‌های bot.py مثل /activity استفاده می‌کنن)."""
    host = _host()
    uid = target_user.id
    name = target_user.first_name or target_user.username or str(uid)
    username_line = f"@{target_user.username}" if target_user.username else name

    record_view(chat_id, viewer_id, uid)
    check_rank_and_membership_achievements(chat_id, uid)
    sync_badges(chat_id, uid)

    xp = host._xp[chat_id].get(uid, 0)
    level, into_level, needed = host._xp_progress(xp)
    title = host._display_title(chat_id, uid)
    prestige = get_stat(chat_id, uid, "prestige")  # فاز ۳
    prestige_line = f"🎖 Prestige: {prestige}\n" if prestige else ""

    ranking = sorted(host._xp[chat_id].items(), key=lambda kv: kv[1], reverse=True)
    rank = next((i + 1 for i, (u, _x) in enumerate(ranking) if u == uid), None)
    rank_text = f"#{rank}" if rank else "—"

    msg_count = host._message_counts[chat_id].get(uid, 0)
    streak = host._streaks[chat_id].get(uid, {"current": 0, "best": 0})
    rep = reputation_of(chat_id, uid)
    views_count, _ = view_stats(chat_id, uid)

    unlocked = host._achievements[chat_id].get(uid, set())
    total_achievements = len(all_achievement_labels())

    badges = host._owned_badges[chat_id].get(uid, set())
    badge_line = ""
    if badges:
        badge_emojis = " ".join(badge_emoji(b) for b in badges)
        badge_line = f"🏅 بج‌ها: {badge_emojis} ({len(badges)})\n"

    economy_line = ""
    if host.get_setting(chat_id, "economy_enabled"):
        bal = host._wallet[chat_id].get(uid, 0)
        economy_line = f"{config.CURRENCY_EMOJI} Coins: {bal} {config.CURRENCY_NAME}\n"
        # فاز ۱۰: Profile Integration - چند خط خلاصه از Economy (Bank/Net Worth/Job/Title)
        try:
            import economy_core
            bank = economy_core.get_bank_balance(chat_id, uid)
            net_worth = economy_core.get_net_worth(chat_id, uid)
            economy_line += f"🏦 بانک: {bank:,} — 💎 ارزش خالص: {net_worth:,} {config.CURRENCY_NAME}\n"
        except Exception:
            pass
        try:
            import economy_jobs
            job_key = economy_jobs.current_job_key(chat_id, uid)
            if job_key:
                job = economy_jobs.JOBS[job_key]
                economy_line += f"💼 شغل: {job['emoji']} {job['name']}\n"
        except Exception:
            pass
        try:
            import economy_achievements
            econ_title = economy_achievements.economy_title_for(chat_id, uid)
            if econ_title:
                economy_line += f"👑 عنوان اقتصادی: {econ_title}\n"
        except Exception:
            pass

    progress_frac = (into_level / needed) if needed else 0
    views_line = f"👁 بازدید پروفایل: {views_count}\n" if getattr(config, "PROFILE_VIEWS_ENABLED", True) else ""

    return (
        "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "👤 𝐏𝐑𝐎𝐅𝐈𝐋𝐄\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"𓆩 {username_line} 𓆪\n"
        f"🏷️ لقب: {title or '—'}\n\n"
        f"{prestige_line}"
        f"⭐ Level: {level}\n"
        f"✨ XP: {into_level:,} / {needed:,}\n"
        f"{_progress_bar(progress_frac)} {round(progress_frac * 100)}%\n"
        f"🥇 رتبه: {rank_text}\n"
        f"{economy_line}\n"
        f"💬 پیام‌ها: {msg_count:,}\n"
        f"🔥 Streak: {streak['current']} روز (رکورد: {streak['best']})\n"
        f"💎 Reputation: {rep}\n"
        f"{views_line}"
        f"\n{badge_line}"
        f"🏆 دستاوردها: {len(unlocked)}/{total_achievements}\n\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n"
        "    𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪"
    )
