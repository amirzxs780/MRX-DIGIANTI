# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY MISSIONS — فاز ۲ (Module 3)
==============================================
موتور Mission کاملاً جنریکه: هیچ منطق خاص Job/Market/Pet/... توش نیست.
هر ماژول (فعلی یا آینده) فقط بعد از یه اکشن مربوطه این رو صدا می‌زنه:

    completed = await economy_missions.record_progress(chat_id, uid, "work", 1)

و اگه ماموریتی با همون action کامل بشه، خودش با Economy Core جایزه رو واریز
می‌کنه و متن تبریک برمی‌گردونه (caller می‌تونه به پیام خودش اضافه کنه).

ماموریت‌های هر بازه (daily/weekly/...) طبق config.MISSION_TEMPLATES تعریف
می‌شن؛ اضافه‌کردن Template جدید یا بازه‌ی جدید (monthly/special/event) فقط با
ویرایش config.py انجام می‌شه، نه این فایل.

همون الگوی بقیه‌ی Engineها: state مستقل، import bot لغزنده، Atomic Save.
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

logger = logging.getLogger("economy_missions")

STATE_FILE = getattr(config, "ECONOMY_MISSIONS_STATE_FILE", "economy_missions_state.json")

# chat_id -> user_id -> period -> {"period_key": str, "missions": [ {key, action, target, progress, reward, claimed} ]}
_user_missions = defaultdict(lambda: defaultdict(dict))

_save_lock = asyncio.Lock()


def _host():
    import bot as host
    return host


# ---------------------------------------------------------------------------
# 🗓 Period keys — یه Period مثل "daily" تا وقتی که period_key عوض نشه (روز/هفته
# فرق نکنه) همون Mission Setِ فعلی رو نگه می‌داره؛ با عوض شدن روز/هفته، Set جدید
# تصادفی انتخاب می‌شه.
# ---------------------------------------------------------------------------

def _period_key(period: str) -> str:
    now = time.localtime()
    if period == "daily":
        return time.strftime("%Y-%m-%d", now)
    if period == "weekly":
        return time.strftime("%Y-W%W", now)
    if period == "monthly":
        return time.strftime("%Y-%m", now)
    # special/event: کلید دستی نداره؛ فعلاً مثل daily رفتار می‌کنه (Template خالی
    # باشه یعنی چیزی assign نمی‌شه، پس بی‌ضرره)
    return time.strftime("%Y-%m-%d", now)


def _assign(chat_id: int, uid: int, period: str) -> dict:
    templates = list(config.MISSION_TEMPLATES.get(period, []))
    count = config.MISSION_ASSIGN_COUNT.get(period, min(3, len(templates)))
    chosen = random.sample(templates, k=min(count, len(templates))) if templates else []
    record = {
        "period_key": _period_key(period),
        "missions": [
            {
                "key": t["key"],
                "text": t["text"],
                "action": t["action"],
                "target": t["target"],
                "reward": t.get("reward", {}),
                "progress": 0,
                "claimed": False,
            }
            for t in chosen
        ],
    }
    _user_missions[chat_id][uid][period] = record
    return record


def _current(chat_id: int, uid: int, period: str) -> dict:
    """ماموریت‌های فعلی این بازه رو برمی‌گردونه؛ اگه بازه عوض شده (روز/هفته‌ی
    جدید) خودکار Set جدید assign می‌کنه."""
    record = _user_missions[chat_id][uid].get(period)
    key_now = _period_key(period)
    if not record or record.get("period_key") != key_now:
        record = _assign(chat_id, uid, period)
    return record


# ---------------------------------------------------------------------------
# 📈 Progress
# ---------------------------------------------------------------------------

async def record_progress(chat_id: int, uid: int, action: str, amount: int = 1) -> list[str]:
    """پیشرفت یه اکشن (مثلاً "work"، "earn"، "deposit"، ...) رو برای همه‌ی
    ماموریت‌های فعالِ کاربر (توی همه‌ی Periodها) اعمال می‌کنه. اگه ماموریتی کامل
    بشه، خودکار جایزه‌ش رو از Economy Core واریز می‌کنه و متن تبریک برمی‌گردونه.
    هیچ‌وقت Exception بالا نمی‌ره (Fail-Safe) — یه مشکل توی Mission نباید کار
    اصلی‌ای که این تابع رو صدا زده (مثلاً /work) رو خراب کنه."""
    completed_texts: list[str] = []
    try:
        import economy_core
        for period in config.MISSION_TEMPLATES:
            record = _current(chat_id, uid, period)
            changed = False
            for m in record["missions"]:
                if m["claimed"] or m["action"] != action:
                    continue
                m["progress"] = min(m["target"], m["progress"] + amount)
                changed = True
                if m["progress"] >= m["target"]:
                    m["claimed"] = True
                    reward = m.get("reward", {})
                    coins = int(reward.get("coins", 0))
                    if coins > 0:
                        # ⚡ فاز ۷: Boost عمومی سکه + Boost اختصاصی ماموریت (boost_mission)
                        try:
                            import economy_engine
                            coins = economy_engine.apply_coin_multiplier(chat_id, uid, coins)
                            if economy_engine.has_active_item_type(chat_id, uid, "boost_mission"):
                                coins = round(coins * getattr(config, "MISSION_BOOST_MULTIPLIER", 2.0))
                        except Exception as e:
                            logger.warning(f"اعمال Mission Boost ناموفق بود: {e}")
                        try:
                            await economy_core.add_coins(
                                chat_id, uid, coins, kind="mission_reward",
                                note=f"ماموریت: {m['text']}",
                            )
                        except Exception as e:
                            logger.warning(f"واریز جایزه‌ی ماموریت ناموفق بود: {e}")
                    completed_texts.append(
                        f"🎯 ماموریت کامل شد: {m['text']} (+{coins} {config.CURRENCY_NAME})"
                    )
            if changed:
                _user_missions[chat_id][uid][period] = record
    except Exception as e:
        logger.warning(f"record_progress برای action={action} خطا داد (نادیده گرفته شد): {e}")
    return completed_texts


# ---------------------------------------------------------------------------
# 📋 نمایش
# ---------------------------------------------------------------------------

def _progress_bar(progress: int, target: int, width: int = 10) -> str:
    frac = 0 if target <= 0 else min(1.0, progress / target)
    filled = round(frac * width)
    return "▰" * filled + "▱" * (width - filled)


PERIOD_LABELS = {"daily": "📅 روزانه", "weekly": "🗓️ هفتگی", "monthly": "🗓️ ماهانه"}


async def missions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not host.get_setting(chat.id, "economy_enabled"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    lines = ["🎯 ماموریت‌های تو", ""]
    for period in ("daily", "weekly", "monthly"):
        if period not in config.MISSION_TEMPLATES:
            continue
        record = _current(chat.id, uid, period)
        if not record["missions"]:
            continue
        lines.append(PERIOD_LABELS.get(period, period))
        for m in record["missions"]:
            status = "✅" if m["claimed"] else _progress_bar(m["progress"], m["target"])
            lines.append(f"  • {m['text']}")
            reward_txt = f"+{m['reward'].get('coins', 0)} {config.CURRENCY_EMOJI}"
            lines.append(f"    {status}  {m['progress']}/{m['target']}  ({reward_txt})")
        lines.append("")

    if len(lines) <= 2:
        lines.append("فعلاً ماموریتی تعریف نشده.")
    await message.reply_text("\n".join(lines).rstrip())


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        str(c): {
            str(u): dict(periods)
            for u, periods in v.items()
        }
        for c, v in _user_missions.items()
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
            logger.warning(f"ذخیره‌ی state موتور ماموریت ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور ماموریت ناموفق بود: {e}")
        return
    for cid, v in data.items():
        for uid, periods in v.items():
            _user_missions[int(cid)][int(uid)] = periods
    logger.info("وضعیت موتور ماموریت از فایل بارگذاری شد.")
