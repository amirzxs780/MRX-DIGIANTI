# -*- coding: utf-8 -*-
"""
🛍 پنل کاربریِ اقتصاد (Economy User Panel)
─────────────────────────────────────────
این ماژول یه پنل جداگونه و کاملاً دکمه‌ای برای *کاربرای عادی* گروهه (نه ادمین).
با /eco یا با نوشتن «پنل اقتصاد من» باز می‌شه و همه‌ی قابلیت‌های اقتصاد
(فروشگاه، بانک، بازار، شغل‌ها، املاک، پت، ازدواج، بازی‌ها، بازار سیاه، دنیای
زیرزمینی، اینونتوری، رتبه‌بندی) رو با دکمه نشون می‌ده؛ با زدن دکمه‌ی هر چیزی
که می‌خوان بخرن، خودش خریدو انجام می‌ده (اگه پول کافی نباشه، پیام خطا می‌ده
و چیزی خریده نمی‌شه).

روشن/خاموش بودنش کاملاً دست ادمینه: این ماژول *هیچ* منطق جدیدی برای
روشن/خاموش کردن اقتصاد نداره و فقط از همون سوییچ‌های موجود استفاده می‌کنه:
  • سوییچ سراسری هر گروه: get_setting(chat_id, "economy_enabled")  (از /menu)
  • سوییچ هر بخش: economy_admin.module_enabled(chat_id, "economy_module_X")
اگه ادمین این‌ها رو خاموش کنه، همون توابع اصلی (که این پنل صداشون می‌زنه)
خودشون پیام «اقتصاد گروه خاموشه» رو می‌دن؛ این پنل هم قبل از باز شدن یه چک
سراسری اضافه می‌کنه که کلاً دکمه‌ای رو نشون نده که کار نمی‌کنه.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import config
import economy_core
import economy_jobs
import economy_bank
import economy_market
import economy_property
import economy_pet
import economy_marriage
import economy_games
import economy_inventory
import economy_blackmarket
import economy_underground
import economy_leaderboard
import economy_shop2
import economy_engine

logger = logging.getLogger(__name__)

PREFIX = "ecop:"


def _host():
    import bot as host
    return host


def _closed_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 پنل اقتصاد", callback_data="ecop:hub")]])


def _module_on(chat_id: int, module_key: str | None) -> bool:
    import economy_admin
    if module_key is None:
        return _host().get_setting(chat_id, "economy_enabled")
    return economy_admin.module_enabled(chat_id, module_key)


def _fmt(n) -> str:
    try:
        return f"{n:,}"
    except Exception:
        return str(n)


def _back_row(sec: str | None = None) -> list:
    row = [InlineKeyboardButton("🔙 پنل اصلی", callback_data="ecop:hub")]
    if sec:
        row.insert(0, InlineKeyboardButton("🔄 تازه‌سازی", callback_data=f"ecop:sec:{sec}"))
    return row


def _rows_of(buttons: list, per_row: int = 2) -> list:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


# ═══════════════════════════════════════════════════════════════════════════
# 🏠 هاب اصلی
# ═══════════════════════════════════════════════════════════════════════════

def _hub_text(chat_id: int, uid: int) -> str:
    wallet = economy_core.get_wallet(chat_id, uid)
    bank = economy_core.get_bank_balance(chat_id, uid)
    return (
        "╭━━━━━━━━━━━━━━━━━━━━╮\n"
        "🛍 پنل اقتصاد (کاربری)\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"💰 کیف پول: {_fmt(wallet)} {config.CURRENCY_NAME}\n"
        f"🏦 بانک: {_fmt(bank)} {config.CURRENCY_NAME}\n\n"
        "با زدن هر دکمه، همون بخش باز می‌شه و خودت با دکمه خرید/استفاده می‌کنی.\n"
        "اگه پول کافی نباشه، ربات بهت می‌گه و چیزی کم نمی‌شه."
    )


def _hub_markup(chat_id: int) -> InlineKeyboardMarkup:
    items = [
        ("🎁 پاداش روزانه", "ecop:daily"),
        ("🛒 پیشنهاد ویژه", "ecop:deal"),
        ("🛍 فروشگاه", "ecop:sec:shop"),
        ("🎒 کوله‌پشتی", "ecop:sec:inv"),
        ("🏦 بانک", "ecop:sec:bank"),
        ("📈 بازار", "ecop:sec:market"),
        ("💼 شغل‌ها", "ecop:sec:jobs"),
        ("🏠 املاک", "ecop:sec:prop"),
        ("🐾 پت", "ecop:sec:pet"),
        ("💍 ازدواج", "ecop:sec:marriage"),
        ("🎮 بازی‌ها", "ecop:sec:games"),
        ("🖤 بازار سیاه", "ecop:sec:black"),
        ("🌑 دنیای زیرزمینی", "ecop:sec:ug"),
        ("🏆 رتبه‌ها", "ecop:rank"),
    ]
    buttons = [InlineKeyboardButton(t, callback_data=d) for t, d in items]
    rows = _rows_of(buttons, 2)
    rows.append([InlineKeyboardButton("❌ بستن", callback_data="menu_close")])
    return InlineKeyboardMarkup(rows)


async def _render_hub(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool):
    chat = update.effective_chat
    uid = update.effective_user.id
    text = _hub_text(chat.id, uid)
    markup = _hub_markup(chat.id)
    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=markup)
            return
        except Exception:
            pass
    await update.effective_message.reply_text(text, reply_markup=markup)


async def eco_panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("این پنل فقط توی گروه کار می‌کنه.")
        return
    if not _host().get_setting(chat.id, "economy_enabled"):
        await update.effective_message.reply_text(
            "🔒 اقتصاد این گروه خاموشه. یه ادمین باید از /menu بخش «اقتصاد و نظرسنجی» روشنش کنه."
        )
        return
    await _render_hub(update, context, edit=False)


# ═══════════════════════════════════════════════════════════════════════════
# 🧰 اجرای امن دستورات موجود (با شبیه‌سازی context.args)
# ═══════════════════════════════════════════════════════════════════════════

async def _run(update: Update, context: ContextTypes.DEFAULT_TYPE, func, args: list):
    """یه تابع _command موجود رو با آرگومان‌های داده‌شده صدا می‌زنه.
    خروجی خودِ تابع (پیام جدید) دست نخورده باقی می‌مونه؛ ما فقط بعدش
    منوی مربوطه رو رفرش می‌کنیم."""
    old_args = getattr(context, "args", None)
    context.args = args
    try:
        await func(update, context)
    except Exception as e:
        logger.warning(f"اجرای دکمه‌ی پنل اقتصاد ناموفق بود ({getattr(func, '__name__', func)}): {e}")
        try:
            await update.effective_message.reply_text("⚠️ یه خطای غیرمنتظره پیش اومد. دوباره امتحان کن.")
        except Exception:
            pass
    finally:
        context.args = old_args


async def _refresh(update: Update, context: ContextTypes.DEFAULT_TYPE, sec: str):
    """بعد از یه اکشن، همون زیرمنو رو با اعداد تازه دوباره می‌سازه."""
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    try:
        text, markup = await _SECTION_RENDERERS[sec](chat_id, uid)
    except Exception as e:
        logger.warning(f"رفرش بخش {sec} ناموفق بود: {e}")
        return
    try:
        await update.callback_query.edit_message_text(text, reply_markup=markup)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# 🛍 فروشگاه (SHOP_ITEMS + ITEM_CATALOG)
# ═══════════════════════════════════════════════════════════════════════════

async def _render_shop(chat_id: int, uid: int):
    host = _host()
    wallet = economy_core.get_wallet(chat_id, uid)
    overrides = host._shop_price_overrides[chat_id]
    hidden = host._shop_hidden_items[chat_id]
    owned = host._owned_badges[chat_id][uid]
    lines = [f"🛍 فروشگاه (موجودی: {_fmt(wallet)} {config.CURRENCY_NAME})", ""]
    buttons = []
    for key, item in config.SHOP_ITEMS.items():
        if key in hidden:
            continue
        price = overrides.get(key, item["price"])
        is_timed = "duration_hours" in item
        if not is_timed and key in owned:
            continue  # قبلاً خریده، دیگه لازم نیست دکمه نشون بدیم
        mark = "✅" if wallet >= price else "❌"
        buttons.append(InlineKeyboardButton(f"{mark} {item['name']} — {_fmt(price)}", callback_data=f"ecop:buy:{key}"))
    for key, item in economy_inventory.CATALOG.items():
        price = item["price"]
        mark = "✅" if wallet >= price else "❌"
        buttons.append(InlineKeyboardButton(f"{mark} {item['name']} — {_fmt(price)}", callback_data=f"ecop:buy:{key}"))
    if not buttons:
        lines.append("فعلاً هیچ آیتمی برای خرید نیست.")
    rows = _rows_of(buttons, 1)
    rows.append(_back_row("shop"))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _cb_buy(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    host = _host()
    await _run(update, context, host.buy_command, [key])
    await update.callback_query.answer()
    # اگه کد مال Shop/Inventory بود توی «فروشگاه» بمون، وگرنه (نماد بازار) توی «بازار»
    if key.upper() in economy_market.ASSETS:
        await _refresh(update, context, "market")
    else:
        await _refresh(update, context, "shop")


# ═══════════════════════════════════════════════════════════════════════════
# 🎒 کوله‌پشتی
# ═══════════════════════════════════════════════════════════════════════════

async def _render_inv(chat_id: int, uid: int):
    text = "🎒 کوله‌پشتی\n\n" + economy_engine.inventory_text(chat_id, uid) + economy_inventory.extra_inventory_text(chat_id, uid)
    buttons = []
    stacks = economy_inventory._items.get(chat_id, {}).get(uid, {}) if hasattr(economy_inventory, "_items") else {}
    for key, qty in (stacks or {}).items():
        info = economy_inventory.CATALOG.get(key)
        if not info or qty <= 0:
            continue
        buttons.append(InlineKeyboardButton(f"✨ استفاده: {info['name']} ({qty})", callback_data=f"ecop:use:{key}"))
    rows = _rows_of(buttons, 1)
    rows.append(_back_row("inv"))
    return text, InlineKeyboardMarkup(rows)


async def _cb_use(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    await _run(update, context, economy_inventory.use_item_command, [key])
    await update.callback_query.answer()
    await _refresh(update, context, "inv")


# ═══════════════════════════════════════════════════════════════════════════
# 🏦 بانک
# ═══════════════════════════════════════════════════════════════════════════

_BANK_PRESETS = [100, 500, 1000, 5000]


async def _render_bank(chat_id: int, uid: int):
    wallet = economy_core.get_wallet(chat_id, uid)
    bank = economy_core.get_bank_balance(chat_id, uid)
    text = (
        "🏦 بانک\n\n"
        f"💰 کیف پول: {_fmt(wallet)} {config.CURRENCY_NAME}\n"
        f"🏦 موجودی بانک: {_fmt(bank)} {config.CURRENCY_NAME}\n\n"
        "برای سپرده یا برداشتِ مبلغ دلخواه از دستور «سپرده [مبلغ]» / «برداشت [مبلغ]» هم می‌تونی استفاده کنی."
    )
    dep_buttons = [InlineKeyboardButton(f"➕ سپرده {_fmt(a)}", callback_data=f"ecop:bank:dep:{a}") for a in _BANK_PRESETS]
    if wallet > 0:
        dep_buttons.append(InlineKeyboardButton(f"➕ سپرده همه ({_fmt(wallet)})", callback_data="ecop:bank:dep:all"))
    wd_buttons = [InlineKeyboardButton(f"➖ برداشت {_fmt(a)}", callback_data=f"ecop:bank:wd:{a}") for a in _BANK_PRESETS]
    rows = _rows_of(dep_buttons, 2) + _rows_of(wd_buttons, 2)
    rows.append([
        InlineKeyboardButton("🎁 دریافت سود", callback_data="ecop:bank:int"),
        InlineKeyboardButton("⬆️ ارتقای بانک", callback_data="ecop:bank:up"),
    ])
    rows.append(_back_row("bank"))
    return text, InlineKeyboardMarkup(rows)


async def _cb_bank(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, arg: str | None):
    if action == "dep":
        wallet = economy_core.get_wallet(update.effective_chat.id, update.effective_user.id)
        amount = wallet if arg == "all" else arg
        await _run(update, context, economy_bank.deposit_command, [str(amount)])
    elif action == "wd":
        await _run(update, context, economy_bank.withdraw_command, [arg])
    elif action == "int":
        await _run(update, context, economy_bank.interest_command, [])
    elif action == "up":
        await _run(update, context, economy_bank.bank_upgrade_command, [])
    await update.callback_query.answer()
    await _refresh(update, context, "bank")


# ═══════════════════════════════════════════════════════════════════════════
# 💼 شغل‌ها
# ═══════════════════════════════════════════════════════════════════════════

async def _render_jobs(chat_id: int, uid: int):
    current = economy_jobs.current_job_key(chat_id, uid) if hasattr(economy_jobs, "current_job_key") else None
    text_lines = ["💼 شغل‌ها", ""]
    if current and current in config.JOBS:
        j = config.JOBS[current]
        text_lines.append(f"شغل فعلیت: {j['emoji']} {j['name']}")
    else:
        text_lines.append("هنوز شغلی انتخاب نکردی.")
    buttons = []
    for key, job in config.JOBS.items():
        if key == current:
            continue
        buttons.append(InlineKeyboardButton(f"{job['emoji']} {job['name']}", callback_data=f"ecop:job:choose:{key}"))
    rows = _rows_of(buttons, 2)
    action_row = [InlineKeyboardButton("💵 کار کن", callback_data="ecop:job:work")]
    if current:
        action_row.append(InlineKeyboardButton("🚪 ترک شغل", callback_data="ecop:job:quit"))
    rows.append(action_row)
    rows.append(_back_row("jobs"))
    return "\n".join(text_lines), InlineKeyboardMarkup(rows)


async def _cb_job(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, key: str | None):
    if action == "choose":
        await _run(update, context, economy_jobs.choosejob_command, [key])
    elif action == "work":
        await _run(update, context, economy_jobs.work_command, [])
    elif action == "quit":
        await _run(update, context, economy_jobs.quitjob_command, [])
    await update.callback_query.answer()
    await _refresh(update, context, "jobs")


# ═══════════════════════════════════════════════════════════════════════════
# 📈 بازار
# ═══════════════════════════════════════════════════════════════════════════

_MARKET_SPEND_PRESETS = [500, 1000, 5000]


async def _render_market(chat_id: int, uid: int):
    wallet = economy_core.get_wallet(chat_id, uid)
    portfolio = economy_market._portfolio[chat_id].get(uid, {}) if hasattr(economy_market, "_portfolio") else {}
    lines = [f"📈 بازار (موجودی: {_fmt(wallet)} {config.CURRENCY_NAME})", ""]
    rows = []
    for symbol, info in economy_market.ASSETS.items():
        price = economy_market.current_price(chat_id, symbol)
        held = portfolio.get(symbol, {}).get("qty", 0)
        held_txt = f" | داری: {held:.4f}" if held else ""
        lines.append(f"• {info['name']} ({symbol}): {_fmt(round(price))} {config.CURRENCY_NAME}{held_txt}")
        row = [InlineKeyboardButton(f"🛒 {symbol} {_fmt(spend)}", callback_data=f"ecop:mk:buy:{symbol}:{spend}") for spend in _MARKET_SPEND_PRESETS]
        if held > 0:
            row.append(InlineKeyboardButton(f"💵 فروش همه‌ی {symbol}", callback_data=f"ecop:mk:sell:{symbol}"))
        if row:
            rows.append(row)
    rows.append(_back_row("market"))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _cb_market(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, symbol: str, extra: str | None):
    if action == "buy":
        await _run(update, context, economy_market.buy_command, [symbol, str(int(extra))])
    elif action == "sell":
        await _run(update, context, economy_market.sell_command, [symbol, "all"])
    await update.callback_query.answer()
    await _refresh(update, context, "market")


# ═══════════════════════════════════════════════════════════════════════════
# 🏠 املاک
# ═══════════════════════════════════════════════════════════════════════════

async def _render_prop(chat_id: int, uid: int):
    wallet = economy_core.get_wallet(chat_id, uid)
    owned = economy_property.owned(chat_id, uid) if hasattr(economy_property, "owned") else []
    lines = [f"🏠 املاک (موجودی: {_fmt(wallet)} {config.CURRENCY_NAME})", ""]
    buttons = []
    for key, info in config.PROPERTY_TYPES.items():
        mark = "✅" if wallet >= info["base_price"] else "❌"
        buttons.append(InlineKeyboardButton(f"{mark} {info['name']} — {_fmt(info['base_price'])}", callback_data=f"ecop:prop:buy:{key}"))
    rows = _rows_of(buttons, 1)
    if owned:
        lines.append(f"🏘 ملک‌های تو: {len(owned)} مورد (برای فروش، «فروش ملک [نوع]» رو بنویس)")
    rows.append(_back_row("prop"))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _cb_prop(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    await _run(update, context, economy_property.buy_property_command, [key])
    await update.callback_query.answer()
    await _refresh(update, context, "prop")


# ═══════════════════════════════════════════════════════════════════════════
# 🐾 پت
# ═══════════════════════════════════════════════════════════════════════════

async def _render_pet(chat_id: int, uid: int):
    wallet = economy_core.get_wallet(chat_id, uid)
    pet = economy_pet.get_pet(chat_id, uid)
    rows = []
    if pet:
        text = f"🐾 پت فعلیت: {economy_pet._display_name(pet) if hasattr(economy_pet, '_display_name') else pet.get('species')}"
        rows.append([
            InlineKeyboardButton("🍖 غذا", callback_data="ecop:pet:feed"),
            InlineKeyboardButton("📚 آموزش", callback_data="ecop:pet:train"),
        ])
        rows.append([
            InlineKeyboardButton("✨ ارتقا", callback_data="ecop:pet:evolve"),
            InlineKeyboardButton("⚔️ فایت", callback_data="ecop:pet:fight"),
        ])
    else:
        text = f"🐾 پت (موجودی: {_fmt(wallet)} {config.CURRENCY_NAME})\n\nهنوز پتی نداری، یکی رو انتخاب کن:"
        buttons = []
        for key, info in config.PET_SPECIES.items():
            mark = "✅" if wallet >= info["cost"] else "❌"
            buttons.append(InlineKeyboardButton(f"{mark} {info['name']} — {_fmt(info['cost'])}", callback_data=f"ecop:pet:buy:{key}"))
        rows = _rows_of(buttons, 1)
    rows.append(_back_row("pet"))
    return text, InlineKeyboardMarkup(rows)


async def _cb_pet(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, key: str | None):
    mapping = {
        "buy": (economy_pet.buy_pet_command, [key]),
        "feed": (economy_pet.feed_command, []),
        "train": (economy_pet.train_command, []),
        "evolve": (economy_pet.evolve_command, []),
        "fight": (economy_pet.fight_command, []),
    }
    func, args = mapping[action]
    await _run(update, context, func, args)
    await update.callback_query.answer()
    await _refresh(update, context, "pet")


# ═══════════════════════════════════════════════════════════════════════════
# 💍 ازدواج
# ═══════════════════════════════════════════════════════════════════════════

_GIFT_PRESETS = [100, 500, 1000]


async def _render_marriage(chat_id: int, uid: int):
    partner = economy_marriage.spouse_of(chat_id, uid) if hasattr(economy_marriage, "spouse_of") else None
    wallet = economy_core.get_wallet(chat_id, uid)
    rows = []
    if partner:
        text = "💍 متأهلی! برای هدیه دادن به همسرت یه مبلغ رو انتخاب کن (یا «درخواست طلاق» بده):"
        gift_row = [InlineKeyboardButton(f"🎁 {_fmt(a)}", callback_data=f"ecop:mar:gift:{a}") for a in _GIFT_PRESETS if a <= wallet]
        if gift_row:
            rows.append(gift_row)
        rows.append([InlineKeyboardButton("💔 درخواست طلاق", callback_data="ecop:mar:divorce")])
    else:
        text = (
            "💍 ازدواج\n\n"
            "هنوز همسری نداری. برای درخواست ازدواج، روی پیام کسی ریپلای کن و بنویس «درخواست ازدواج»."
        )
    rows.append(_back_row("marriage"))
    return text, InlineKeyboardMarkup(rows)


async def _cb_marriage(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, arg: str | None):
    if action == "gift":
        await _run(update, context, economy_marriage.gift_command, [arg])
    elif action == "divorce":
        await _run(update, context, economy_marriage.divorce_command, [])
    await update.callback_query.answer()
    await _refresh(update, context, "marriage")


# ═══════════════════════════════════════════════════════════════════════════
# 🎮 بازی‌ها
# ═══════════════════════════════════════════════════════════════════════════

_GAME_BETS = [50, 100, 500]


async def _render_games(chat_id: int, uid: int):
    wallet = economy_core.get_wallet(chat_id, uid)
    text = f"🎮 بازی‌ها (موجودی: {_fmt(wallet)} {config.CURRENCY_NAME})\n\nیه شرط رو انتخاب کن:"
    rows = []
    for bet in _GAME_BETS:
        rows.append([
            InlineKeyboardButton(f"🎲 تاس {_fmt(bet)}", callback_data=f"ecop:game:dice:{bet}"),
            InlineKeyboardButton(f"🪙 شیر {_fmt(bet)}", callback_data=f"ecop:game:cfS:{bet}"),
            InlineKeyboardButton(f"🪙 خط {_fmt(bet)}", callback_data=f"ecop:game:cfX:{bet}"),
        ])
    rows.append(_back_row("games"))
    return text, InlineKeyboardMarkup(rows)


async def _cb_games(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str, bet: str):
    if kind == "dice":
        await _run(update, context, economy_games.dice_command, [bet])
    elif kind == "cfS":
        await _run(update, context, economy_games.coinflip_command, [bet, "شیر"])
    elif kind == "cfX":
        await _run(update, context, economy_games.coinflip_command, [bet, "خط"])
    await update.callback_query.answer()
    await _refresh(update, context, "games")


# ═══════════════════════════════════════════════════════════════════════════
# 🖤 بازار سیاه
# ═══════════════════════════════════════════════════════════════════════════

async def _render_black(chat_id: int, uid: int):
    wallet = economy_core.get_wallet(chat_id, uid)
    rec = economy_blackmarket._ensure_rotation(chat_id) if hasattr(economy_blackmarket, "_ensure_rotation") else {"stock": {}}
    lines = [f"🖤 بازار سیاه (موجودی: {_fmt(wallet)} {config.CURRENCY_NAME})", ""]
    buttons = []
    for key, qty in rec.get("stock", {}).items():
        info = economy_blackmarket.CATALOG.get(key)
        if not info or qty <= 0:
            continue
        mark = "✅" if wallet >= info["price"] else "❌"
        buttons.append(InlineKeyboardButton(f"{mark} {info['name']} — {_fmt(info['price'])} ({qty} مونده)", callback_data=f"ecop:bm:buy:{key}"))
    if not buttons:
        lines.append("الان چیزی توی بازار سیاه موجود نیست.")
    rows = _rows_of(buttons, 1)
    rows.append(_back_row("black"))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _cb_black(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    await _run(update, context, economy_blackmarket.buy_command, [key])
    await update.callback_query.answer()
    await _refresh(update, context, "black")


# ═══════════════════════════════════════════════════════════════════════════
# 🌑 دنیای زیرزمینی
# ═══════════════════════════════════════════════════════════════════════════

async def _render_ug(chat_id: int, uid: int):
    text = (
        "🌑 دنیای زیرزمینی\n\n"
        "یکی از قراردادها رو انتخاب کن (ریسکی‌ان، ممکنه ببازی):"
    )
    rows = [
        [InlineKeyboardButton("🕸 دارک وب", callback_data="ecop:ug:darkweb")],
        [InlineKeyboardButton("🕵️ جاسوس", callback_data="ecop:ug:spy")],
        [InlineKeyboardButton("💻 هکر", callback_data="ecop:ug:hacker")],
        [InlineKeyboardButton("🎲 ماموریت تصادفی", callback_data="ecop:ug:random")],
    ]
    rows.append(_back_row("ug"))
    return text, InlineKeyboardMarkup(rows)


async def _cb_ug(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str):
    mapping = {
        "darkweb": economy_underground.darkweb_command,
        "spy": economy_underground.spy_command,
        "hacker": economy_underground.hacker_command,
        "random": economy_underground.random_mission_command,
    }
    await _run(update, context, mapping[kind], [])
    await update.callback_query.answer()
    await _refresh(update, context, "ug")


# ═══════════════════════════════════════════════════════════════════════════
# 🔀 جدول بخش‌ها
# ═══════════════════════════════════════════════════════════════════════════

_SECTION_RENDERERS = {
    "shop": _render_shop,
    "inv": _render_inv,
    "bank": _render_bank,
    "jobs": _render_jobs,
    "market": _render_market,
    "prop": _render_prop,
    "pet": _render_pet,
    "marriage": _render_marriage,
    "games": _render_games,
    "black": _render_black,
    "ug": _render_ug,
}

_SECTION_MODULE_KEY = {
    "bank": "economy_module_bank",
    "jobs": "economy_module_jobs",
    "market": "economy_module_market",
    "prop": "economy_module_property",
    "pet": "economy_module_pet",
    "marriage": "economy_module_marriage",
    "games": "economy_module_games",
    "black": "economy_module_blackmarket",
    "ug": "economy_module_underground",
}


# ═══════════════════════════════════════════════════════════════════════════
# 🎛 دیسپچر مرکزیِ کال‌بک‌ها (فقط ecop:)
# ═══════════════════════════════════════════════════════════════════════════

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    if not data.startswith(PREFIX):
        return
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await query.answer("این پنل فقط توی گروه کار می‌کنه.", show_alert=True)
        return
    if not _host().get_setting(chat.id, "economy_enabled"):
        await query.answer("🔒 اقتصاد این گروه خاموشه (دست ادمینه).", show_alert=True)
        return

    parts = data[len(PREFIX):].split(":")
    action = parts[0] if parts else ""

    try:
        if action == "hub":
            await query.answer()
            await _render_hub(update, context, edit=True)
            return

        if action == "daily":
            await _run(update, context, _host().daily_command, [])
            await query.answer()
            await _render_hub(update, context, edit=True)
            return

        if action == "deal":
            await _run(update, context, economy_shop2.buy_daily_deal_command, [])
            await query.answer()
            await _render_hub(update, context, edit=True)
            return

        if action == "rank":
            await _run(update, context, economy_leaderboard.my_rank_command, [])
            await query.answer()
            return

        if action == "sec":
            sec = parts[1] if len(parts) > 1 else ""
            mod_key = _SECTION_MODULE_KEY.get(sec)
            if not _module_on(chat.id, mod_key):
                await query.answer("🔒 این بخش رو ادمین خاموش کرده.", show_alert=True)
                return
            renderer = _SECTION_RENDERERS.get(sec)
            if not renderer:
                await query.answer()
                return
            await query.answer()
            text, markup = await renderer(chat.id, update.effective_user.id)
            try:
                await query.edit_message_text(text, reply_markup=markup)
            except Exception:
                await update.effective_message.reply_text(text, reply_markup=markup)
            return

        if action == "buy":
            await _cb_buy(update, context, parts[1])
            return
        if action == "use":
            await _cb_use(update, context, parts[1])
            return
        if action == "bank":
            await _cb_bank(update, context, parts[1], parts[2] if len(parts) > 2 else None)
            return
        if action == "job":
            await _cb_job(update, context, parts[1], parts[2] if len(parts) > 2 else None)
            return
        if action == "mk":
            await _cb_market(update, context, parts[1], parts[2], parts[3] if len(parts) > 3 else None)
            return
        if action == "prop":
            await _cb_prop(update, context, parts[2])
            return
        if action == "pet":
            await _cb_pet(update, context, parts[1], parts[2] if len(parts) > 2 else None)
            return
        if action == "mar":
            await _cb_marriage(update, context, parts[1], parts[2] if len(parts) > 2 else None)
            return
        if action == "game":
            kind = parts[1]
            bet = parts[2] if len(parts) > 2 else "0"
            await _cb_games(update, context, kind, bet)
            return
        if action == "bm":
            await _cb_black(update, context, parts[2])
            return
        if action == "ug":
            await _cb_ug(update, context, parts[1])
            return

        await query.answer()
    except Exception as e:
        logger.warning(f"خطا توی دیسپچر پنل اقتصاد ({data}): {e}")
        try:
            await query.answer("⚠️ یه خطا پیش اومد.", show_alert=True)
        except Exception:
            pass
