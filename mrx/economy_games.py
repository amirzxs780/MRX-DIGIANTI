# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY MINI GAMES — فاز ۶ (Module 14)
====================================================
سه بازی سریع (تاس/شیرخط/حدس) با کول‌داون و سقف روزانه‌ی مشترک (Anti-Abuse)،
و یه بازی واقعاً Multiplayer (اکس‌او): «اکس او [مبلغ]» روی پیام حریف ریپلای
می‌شه تا بازی با Escrow شروع بشه، بعد با «اکس او [۱-۹]» نوبتی حرکت می‌کنن.
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

logger = logging.getLogger("economy_games")

STATE_FILE = getattr(config, "ECONOMY_GAMES_STATE_FILE", "economy_games_state.json")

_save_lock = asyncio.Lock()

# chat_id -> uid -> {"wins","losses","streak","best_streak"}
_stats = defaultdict(dict)
_last_game_ts = defaultdict(dict)
_daily = defaultdict(dict)

# چالش‌های در انتظار قبول: chat_id -> target_id -> {"challenger_id","bet","ts"}
_xo_pending = defaultdict(dict)
# بازی‌های فعال: chat_id -> "min_max" -> {"board":[...], "turn":uid, "p1","p2","bet","started_ts"}
_xo_games = defaultdict(dict)


def _host():
    import bot as host
    return host


def _today_str() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _stats_of(chat_id: int, uid: int) -> dict:
    s = _stats[chat_id].get(uid)
    if not s:
        s = {"wins": 0, "losses": 0, "streak": 0, "best_streak": 0}
        _stats[chat_id][uid] = s
    return s


def _record_result(chat_id: int, uid: int, win: bool) -> None:
    s = _stats_of(chat_id, uid)
    if win:
        s["wins"] += 1
        s["streak"] += 1
        s["best_streak"] = max(s["best_streak"], s["streak"])
    else:
        s["losses"] += 1
        s["streak"] = 0


def _check_cooldown_and_limit(chat_id: int, uid: int) -> str | None:
    now = time.time()
    last = _last_game_ts[chat_id].get(uid, 0)
    cooldown = getattr(config, "GAMES_COOLDOWN_SECONDS", 30)
    remaining = cooldown - (now - last)
    if remaining > 0:
        return f"⏳ {int(remaining) + 1} ثانیه‌ی دیگه صبر کن."
    rec = _daily[chat_id].get(uid)
    today = _today_str()
    if not rec or rec.get("date") != today:
        rec = {"date": today, "count": 0}
        _daily[chat_id][uid] = rec
    limit = getattr(config, "GAMES_DAILY_LIMIT", 50)
    if rec["count"] >= limit:
        return f"📆 امروز به سقف {limit} بار بازی رسیدی."
    return None


def _mark_played(chat_id: int, uid: int) -> None:
    _last_game_ts[chat_id][uid] = time.time()
    _daily[chat_id][uid]["count"] += 1


async def _after_minigame(chat_id: int, uid: int) -> list[str]:
    try:
        return await economy_missions.record_progress(chat_id, uid, "minigame_play", 1)
    except Exception as e:
        logger.warning(f"ثبت پیشرفت ماموریت minigame_play ناموفق بود: {e}")
        return []


def _parse_bet(args: list[str], idx: int, min_a: int, max_a: int) -> int | None:
    if idx >= len(args):
        return None
    try:
        amount = int(args[idx].replace(",", ""))
    except ValueError:
        return None
    return amount if min_a <= amount <= max_a else None


# ---------------------------------------------------------------------------
# 🎲 تاس
# ---------------------------------------------------------------------------

async def dice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تاس [مبلغ] / /dice <amount>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_games"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return
    uid = update.effective_user.id
    # 🧠 فاز ۹: Anti-Abuse سراسری
    try:
        import economy_antiabuse
        wait = economy_antiabuse.check_and_mark(chat.id, uid)
        if wait > 0:
            await message.reply_text(f"⏳ یکم آروم‌تر؛ {wait:.1f} ثانیه‌ی دیگه صبر کن.")
            return
    except Exception as e:
        logger.warning(f"چک Anti-Abuse سراسری ناموفق بود: {e}")
    bet = _parse_bet(context.args or [], 0, config.DICE_MIN_BET, config.DICE_MAX_BET)
    if bet is None:
        await message.reply_text(f"استفاده: «تاس [مبلغ]» (بین {config.DICE_MIN_BET} و {config.DICE_MAX_BET})")
        return
    err = _check_cooldown_and_limit(chat.id, uid)
    if err:
        await message.reply_text(err)
        return
    try:
        await economy_core.remove_coins(chat.id, uid, bet, kind="dice_bet", note="شرط تاس")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست ({economy_core.get_wallet(chat.id, uid)} {config.CURRENCY_NAME}).")
        return
    _mark_played(chat.id, uid)

    my_roll = random.randint(1, 6)
    house_roll = random.randint(1, 6)
    lines = [f"🎲 تو: {my_roll} — خانه: {house_roll}"]
    if my_roll > house_roll:
        payout = round(bet * config.DICE_WIN_MULTIPLIER)
        new_wallet = await economy_core.add_coins(chat.id, uid, payout, kind="dice_win", note="برد تاس")
        _record_result(chat.id, uid, True)
        lines.append(f"🎉 بردی! +{payout} {config.CURRENCY_EMOJI} — موجودی: {new_wallet}")
    elif my_roll == house_roll:
        new_wallet = await economy_core.add_coins(chat.id, uid, bet, kind="dice_push", note="مساوی تاس")
        lines.append(f"🤝 مساوی شد؛ شرطت برگشت. موجودی: {new_wallet}")
    else:
        _record_result(chat.id, uid, False)
        lines.append(f"😓 باختی. -{bet} {config.CURRENCY_EMOJI}")
    lines += await _after_minigame(chat.id, uid)
    await message.reply_text("\n".join(lines))
    await host.save_state()


# ---------------------------------------------------------------------------
# 🪙 شیر یا خط
# ---------------------------------------------------------------------------

async def coinflip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شیر یا خط [مبلغ] [شیر|خط] / /coinflip <amount> <side>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_games"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return
    uid = update.effective_user.id
    args = context.args or []
    bet = _parse_bet(args, 0, config.COINFLIP_MIN_BET, config.COINFLIP_MAX_BET)
    side = args[1].strip() if len(args) > 1 else None
    if bet is None or side not in ("شیر", "خط"):
        await message.reply_text(
            f"استفاده: «شیر یا خط [مبلغ] [شیر|خط]» (بین {config.COINFLIP_MIN_BET} و {config.COINFLIP_MAX_BET})"
        )
        return
    err = _check_cooldown_and_limit(chat.id, uid)
    if err:
        await message.reply_text(err)
        return
    try:
        await economy_core.remove_coins(chat.id, uid, bet, kind="coinflip_bet", note="شرط شیر یا خط")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست ({economy_core.get_wallet(chat.id, uid)} {config.CURRENCY_NAME}).")
        return
    _mark_played(chat.id, uid)

    result = random.choice(["شیر", "خط"])
    lines = [f"🪙 نتیجه: {result}"]
    if result == side:
        payout = round(bet * config.COINFLIP_WIN_MULTIPLIER)
        new_wallet = await economy_core.add_coins(chat.id, uid, payout, kind="coinflip_win", note="برد شیر یا خط")
        _record_result(chat.id, uid, True)
        lines.append(f"🎉 بردی! +{payout} {config.CURRENCY_EMOJI} — موجودی: {new_wallet}")
    else:
        _record_result(chat.id, uid, False)
        lines.append(f"😓 باختی. -{bet} {config.CURRENCY_EMOJI}")
    lines += await _after_minigame(chat.id, uid)
    await message.reply_text("\n".join(lines))
    await host.save_state()


# ---------------------------------------------------------------------------
# 🔢 حدس
# ---------------------------------------------------------------------------

async def guess_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حدس [عدد] [مبلغ] / /guess <number> <amount>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_games"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return
    uid = update.effective_user.id
    args = context.args or []
    rng = getattr(config, "GUESS_RANGE", 10)
    try:
        guess_num = int(args[0]) if args else -1
    except ValueError:
        guess_num = -1
    bet = _parse_bet(args, 1, config.GUESS_MIN_BET, config.GUESS_MAX_BET)
    if not (1 <= guess_num <= rng) or bet is None:
        await message.reply_text(
            f"استفاده: «حدس [عدد بین ۱ تا {rng}] [مبلغ]» (بین {config.GUESS_MIN_BET} و {config.GUESS_MAX_BET})"
        )
        return
    err = _check_cooldown_and_limit(chat.id, uid)
    if err:
        await message.reply_text(err)
        return
    try:
        await economy_core.remove_coins(chat.id, uid, bet, kind="guess_bet", note="شرط حدس")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست ({economy_core.get_wallet(chat.id, uid)} {config.CURRENCY_NAME}).")
        return
    _mark_played(chat.id, uid)

    answer = random.randint(1, rng)
    lines = [f"🔢 عدد درست: {answer}"]
    if guess_num == answer:
        payout = round(bet * config.GUESS_WIN_MULTIPLIER)
        new_wallet = await economy_core.add_coins(chat.id, uid, payout, kind="guess_win", note="برد حدس")
        _record_result(chat.id, uid, True)
        lines.append(f"🎉 درست حدس زدی! +{payout} {config.CURRENCY_EMOJI} — موجودی: {new_wallet}")
    else:
        _record_result(chat.id, uid, False)
        lines.append(f"😓 اشتباه بود. -{bet} {config.CURRENCY_EMOJI}")
    lines += await _after_minigame(chat.id, uid)
    await message.reply_text("\n".join(lines))
    await host.save_state()


# ---------------------------------------------------------------------------
# ❌⭕ اکس او (Multiplayer)
# ---------------------------------------------------------------------------

WIN_LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
]


def _pair_key(a: int, b: int) -> str:
    return f"{min(a, b)}_{max(a, b)}"


def _render_board(board: list[str | None]) -> str:
    symbols = [c if c else str(i + 1) for i, c in enumerate(board)]
    rows = [" ".join(symbols[r * 3:r * 3 + 3]) for r in range(3)]
    return "\n".join(rows)


def _check_winner(board: list) -> str | None:
    for a, b, c in WIN_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return None


async def xo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اکس او [مبلغ] (ریپلای، برای شروع) یا اکس او [۱-۹] (برای حرکت) / /xo"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_games"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    uid = update.effective_user.id
    args = context.args or []

    # آیا کاربر توی یه بازی فعاله؟ اگه آره و یه عدد ۱-۹ داده، این یعنی حرکت.
    active_key = next((k for k, g in _xo_games[chat.id].items() if uid in (g["p1"], g["p2"])), None)
    if active_key and args and args[0].isdigit():
        await _xo_move(update, active_key, uid, int(args[0]))
        return

    # وگرنه: شروع بازی جدید (نیاز به ریپلای روی حریف)
    target_id = host._resolve_target_id(message)
    if not target_id:
        if active_key:
            game = _xo_games[chat.id][active_key]
            await message.reply_text(f"یه بازی فعال داری! با «اکس او [۱-۹]» حرکت کن.\n\n{_render_board(game['board'])}")
        else:
            await message.reply_text("برای شروع بازی، روی پیام حریفت ریپلای کن: «اکس او [مبلغ]» (مبلغ اختیاریه، ۰ هم قبوله).")
        return
    if target_id == uid:
        await message.reply_text("نمی‌تونی با خودت بازی کنی 😅")
        return

    bet = 0
    if args:
        try:
            bet = int(args[0].replace(",", ""))
        except ValueError:
            bet = -1
    if bet < 0 or bet > getattr(config, "XO_MAX_BET", 10000):
        await message.reply_text("مبلغ نامعتبره.")
        return

    key = _pair_key(uid, target_id)
    if key in _xo_games[chat.id]:
        await message.reply_text("شما دوتا همین الان هم یه بازی فعال دارید.")
        return

    if bet > 0:
        if not economy_core.can_afford(chat.id, uid, bet) or not economy_core.can_afford(chat.id, target_id, bet):
            await message.reply_text("برای شرط‌بندی، هر دو طرف باید اون‌قدر موجودی داشته باشن.")
            return
        await economy_core.remove_coins(chat.id, uid, bet, kind="xo_stake", note="شرط اکس او")
        await economy_core.remove_coins(chat.id, target_id, bet, kind="xo_stake", note="شرط اکس او")

    _xo_games[chat.id][key] = {
        "board": [None] * 9, "turn": uid, "p1": uid, "p2": target_id, "bet": bet, "started_ts": time.time(),
    }
    await message.reply_text(
        f"❌⭕ بازی شروع شد! (شرط: {bet})\nنوبت اول با توئه (❌).\n\n{_render_board([None]*9)}\n\n"
        "با «اکس او [۱-۹]» حرکت کن."
    )
    await host.save_state()


async def _xo_move(update: Update, key: str, uid: int, pos: int) -> None:
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    game = _xo_games[chat.id][key]

    if game["turn"] != uid:
        await message.reply_text("نوبت تو نیست.")
        return
    if not (1 <= pos <= 9) or game["board"][pos - 1] is not None:
        await message.reply_text("این خونه خالی نیست یا نامعتبره. یه عدد ۱ تا ۹ از خونه‌های خالی بده.")
        return

    symbol = "❌" if uid == game["p1"] else "⭕"
    game["board"][pos - 1] = symbol
    winner_symbol = _check_winner(game["board"])
    is_full = all(c is not None for c in game["board"])

    if winner_symbol:
        winner_id = game["p1"] if winner_symbol == "❌" else game["p2"]
        loser_id = game["p2"] if winner_id == game["p1"] else game["p1"]
        _record_result(chat.id, winner_id, True)
        _record_result(chat.id, loser_id, False)
        lines = [_render_board(game["board"]), "", f"🏆 برد {'❌' if winner_symbol=='❌' else '⭕'}!"]
        if game["bet"] > 0:
            payout = game["bet"] * 2
            await economy_core.add_coins(chat.id, winner_id, payout, kind="xo_win", counterparty_id=loser_id, note="برد اکس او")
            lines.append(f"💰 +{payout} {config.CURRENCY_EMOJI}")
        del _xo_games[chat.id][key]
        await message.reply_text("\n".join(lines))
    elif is_full:
        lines = [_render_board(game["board"]), "", "🤝 مساوی شد!"]
        if game["bet"] > 0:
            await economy_core.add_coins(chat.id, game["p1"], game["bet"], kind="xo_push", note="مساوی اکس او")
            await economy_core.add_coins(chat.id, game["p2"], game["bet"], kind="xo_push", note="مساوی اکس او")
            lines.append("شرط‌ها برگشت.")
        del _xo_games[chat.id][key]
        await message.reply_text("\n".join(lines))
    else:
        game["turn"] = game["p2"] if uid == game["p1"] else game["p1"]
        next_symbol = "⭕" if symbol == "❌" else "❌"
        await message.reply_text(f"{_render_board(game['board'])}\n\nنوبت {next_symbol}.")

    await host.save_state()


# ---------------------------------------------------------------------------
# 📋 منو
# ---------------------------------------------------------------------------

async def game_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بازی / /game — راهنما + آمار شخصی."""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    s = _stats_of(chat.id, uid)
    total = s["wins"] + s["losses"]
    rate = (s["wins"] / total * 100) if total else 0
    lines = [
        "🎮 بازی‌های DIGIANTI",
        "",
        "🎲 «تاس [مبلغ]» — تاس در برابر خانه",
        "🪙 «شیر یا خط [مبلغ] [شیر|خط]»",
        f"🔢 «حدس [عدد ۱-{config.GUESS_RANGE}] [مبلغ]»",
        "❌⭕ «اکس او [مبلغ]» (ریپلای روی حریف) — بازی واقعی دو نفره",
        "",
        f"📊 آمار تو: {s['wins']} برد / {s['losses']} باخت ({rate:.0f}٪) — Streak: {s['streak']} (بهترین: {s['best_streak']})",
    ]
    await message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "stats": {str(c): dict(v) for c, v in _stats.items()},
        "last_game_ts": {str(c): dict(v) for c, v in _last_game_ts.items()},
        "daily": {str(c): {str(u): d for u, d in v.items()} for c, v in _daily.items()},
        "xo_games": {str(c): dict(v) for c, v in _xo_games.items()},
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
            logger.warning(f"ذخیره‌ی state موتور Games ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور Games ناموفق بود: {e}")
        return
    for cid, v in data.get("stats", {}).items():
        _stats[int(cid)] = {int(u): s for u, s in v.items()}
    for cid, v in data.get("last_game_ts", {}).items():
        _last_game_ts[int(cid)] = {int(u): ts for u, ts in v.items()}
    for cid, v in data.get("daily", {}).items():
        _daily[int(cid)] = {int(u): d for u, d in v.items()}
    for cid, v in data.get("xo_games", {}).items():
        _xo_games[int(cid)] = v
    logger.info("وضعیت موتور Games از فایل بارگذاری شد.")
