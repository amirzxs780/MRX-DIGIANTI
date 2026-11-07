# -*- coding: utf-8 -*-
"""
🛍 پنل کاربریِ اقتصاد (Economy User Panel) — نسخه‌ی کامل و دکمه‌ای
═══════════════════════════════════════════════════════════════════
یه پنل جداگونه، تمیز و کاملاً دکمه‌ای برای *کاربرای عادی* گروهه (نه ادمین).
با /eco، /economy یا نوشتن «پنل اقتصاد» باز می‌شه و هر قابلیتِ اقتصادِ
ربات — از کار و شغل تا بانک، بازار، املاک، خودرو، کسب‌وکار، شهر، منطقه،
لباس، پت، ازدواج، بازی‌ها، بازار سیاه، دنیای زیرزمینی، دزدی/زندان،
دستاوردها و رتبه‌بندی — رو زیر یه منوی دسته‌بندی‌شده و قابل‌فهم می‌ذاره.

ساختار ناوبری سه‌لایه‌ست تا هم *همه‌چیز* باشه هم شلوغ/پیچیده نشه:

    🏠 پنل اصلی  →  📂 دسته‌بندی (مثلاً «دارایی و سرمایه‌گذاری»)  →  🧩 بخش
    (مثلاً «خودروها»)  →  (در موارد لازم) 🔍 جزئیات یه آیتم خاص

هر دکمه، خودش همون کاری که اسمشه رو انجام می‌ده: خرید/فروش/استفاده/
جابه‌جایی و... همه بدون نوشتن هیچ دستوری. اگه پول کافی نباشه یا بخش
قفل باشه، فقط یه پیام/Alert خطا می‌ده و چیزی عوض نمی‌شه.

روشن/خاموش بودنش کاملاً دست ادمینه: این ماژول *هیچ* منطق جدیدی برای
روشن/خاموش کردن اقتصاد نداره و فقط از همون سوییچ‌های موجود استفاده می‌کنه:
  • سوییچ سراسری هر گروه: get_setting(chat_id, "economy_enabled")  (از /menu)
  • سوییچ هر بخش: economy_admin.module_enabled(chat_id, "economy_module_X")
اگه ادمین این‌ها رو خاموش کنه، همون توابع اصلی (که این پنل صداشون می‌زنه)
خودشون پیام «این بخش خاموشه» رو می‌دن؛ این پنل هم قبل از باز شدن یه
Alert کوتاه می‌ده که کلاً وقت کاربر رو با دکمه‌ای که کار نمی‌کنه نگیره.

این فایل هیچ منطق اقتصادی/محاسباتی جدیدی نمی‌سازه — فقط یه لایه‌ی UI
روی توابع _command موجودِ هر ماژول اقتصادیه (Single Source of Truth
همون ماژول‌هاست، این پنل فقط صداشون می‌زنه و بعد منو رو تازه می‌کنه).
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
import economy_city
import economy_district
import economy_vehicle
import economy_business
import economy_clothing
import economy_theft
import economy_achievements
import economy_missions
import economy_macro
import economy_stock
import economy_auction

logger = logging.getLogger(__name__)

PREFIX = "ecop:"


def _host():
    import bot as host
    return host


# ═══════════════════════════════════════════════════════════════════════════
# 🧰 ابزارهای مشترک نمایش
# ═══════════════════════════════════════════════════════════════════════════

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


def _tier_badge(amount) -> str:
    """بج رنگی‌ای که بر اساس مبلغ واقعی عوض می‌شه - حس زنده‌بودن بدون نیاز به انیمیشن واقعی."""
    try:
        amount = float(amount)
    except Exception:
        return "💰"
    if amount >= 1_000_000:
        return "💎"
    if amount >= 100_000:
        return "🔥"
    if amount >= 10_000:
        return "✨"
    if amount > 0:
        return "💰"
    return "🪫"


def _mark(ok: bool) -> str:
    return "✅" if ok else "❌"


def _net_worth_line(total: int) -> str:
    """یه خط پایانی که با رشد دارایی کاربر عوض می‌شه."""
    if total >= 1_000_000:
        return "👑 داری تبدیل به قدرتمندترین فرد این گروه می‌شی!"
    if total >= 100_000:
        return "🔥 خیلی خوب داری پیش می‌ری، همینطوری ادامه بده."
    if total >= 10_000:
        return "✨ وضعیتت داره رو به رشده."
    return "🌱 تازه شروع کردی — با «پاداش روزانه» و «کار کن» شروع کن."


def _rows_of(buttons: list, per_row: int = 2) -> list:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def _section(label: str) -> str:
    return f"┌─ {label} ─┐"


# ═══════════════════════════════════════════════════════════════════════════
# 📂 دسته‌بندی‌های منو — نقشه‌ی کل پنل
# ═══════════════════════════════════════════════════════════════════════════
# هر آیتم: (برچسب دکمه, نوع, مقدار)
#   نوع "sec" → باز کردن یه بخشِ کامل (ecop:sec:<مقدار>)
#   نوع "act" → یه اکشن فوری که نتیجه‌اش پیام جدیدشه (ecop:<مقدار>)

CATEGORIES: dict[str, dict] = {
    "earn": {
        "title": "💼 کار، ماموریت و درآمد",
        "desc": "شغل انتخاب کن، کار کن، پاداش روزانه بگیر و ماموریت‌هات رو ببین.",
        "items": [
            ("🎁 پاداش روزانه", "act", "daily"),
            ("🛒 پیشنهاد ویژه‌ی امروز", "act", "deal"),
            ("💼 شغل‌ها و کار کردن", "sec", "jobs"),
            ("🎯 ماموریت‌های من", "act", "missions"),
        ],
    },
    "money": {
        "title": "🏦 بانک و بازار سرمایه",
        "desc": "پولت رو امن نگه دار، سود بگیر یا توی بازار معامله کن.",
        "items": [
            ("🏦 بانک (سپرده/برداشت/سود)", "sec", "bank"),
            ("📈 بازار و ترید", "sec", "market"),
            ("🏢 بورس سهام شرکت‌ها", "sec", "stock"),
            ("🔨 مزایده‌ها", "sec", "auction"),
        ],
    },
    "shop": {
        "title": "🛍 فروشگاه‌ها و خرید",
        "desc": "آیتم، لباس و کالاهای ویژه بخر.",
        "items": [
            ("🛍 فروشگاه اصلی", "sec", "shop"),
            ("👔 فروشگاه لباس", "sec", "clothing"),
            ("🖤 بازار سیاه", "sec", "black"),
        ],
    },
    "assets": {
        "title": "🏠 دارایی و سرمایه‌گذاری",
        "desc": "املاک، خودرو، کسب‌وکار، شهر و منطقه‌ی زندگیت رو مدیریت کن.",
        "items": [
            ("🏠 املاک", "sec", "prop"),
            ("🚗 خودروها", "sec", "vehicle"),
            ("🏢 کسب‌وکارها", "sec", "business"),
            ("🏙 شهر من", "sec", "city"),
            ("🗺 منطقه‌ی زندگی", "sec", "district"),
        ],
    },
    "life": {
        "title": "🐾 سبک زندگی",
        "desc": "کوله‌پشتی، پت و زندگی مشترکت.",
        "items": [
            ("🎒 کوله‌پشتی", "sec", "inv"),
            ("🐾 پت", "sec", "pet"),
            ("💍 ازدواج", "sec", "marriage"),
        ],
    },
    "risk": {
        "title": "🎮 بازی، شانس و ریسک",
        "desc": "شرط ببند، وارد دنیای زیرزمینی شو یا وضعیت دزدی/زندانت رو ببین.",
        "items": [
            ("🎮 بازی‌ها و شرط‌بندی", "sec", "games"),
            ("🌑 دنیای زیرزمینی", "sec", "ug"),
            ("🦹 دزدی و زندان", "sec", "theft"),
        ],
    },
    "stats": {
        "title": "🏆 آمار، رتبه و دستاوردها",
        "desc": "ببین کجای این گروه وایستادی.",
        "items": [
            ("💎 ارزش خالص من", "act", "ach"),
            ("👑 جدول رتبه‌بندی‌ها", "sec", "lb"),
            ("📊 اقتصاد کلان گروه", "sec", "macro"),
        ],
    },
}

CATEGORY_ORDER = ["earn", "money", "shop", "assets", "life", "risk", "stats"]

SECTION_CATEGORY: dict[str, str] = {}
for _cat_key, _cat in CATEGORIES.items():
    for _label, _kind, _val in _cat["items"]:
        if _kind == "sec":
            SECTION_CATEGORY[_val] = _cat_key


def _back_row(sec: str | None = None) -> list:
    """ردیف پایین هر صفحه‌ی بخش: تازه‌سازی + بازگشت به دسته‌ی مربوطه."""
    cat = SECTION_CATEGORY.get(sec) if sec else None
    back_cb = f"ecop:cat:{cat}" if cat else "ecop:hub"
    row = [InlineKeyboardButton("🔙 بازگشت", callback_data=back_cb)]
    if sec:
        row.insert(0, InlineKeyboardButton("🔄 تازه‌سازی", callback_data=f"ecop:sec:{sec}"))
    return row


def _category_back_row() -> list:
    return [InlineKeyboardButton("🏠 پنل اصلی", callback_data="ecop:hub")]


# ═══════════════════════════════════════════════════════════════════════════
# 🏠 هاب اصلی
# ═══════════════════════════════════════════════════════════════════════════

def _hub_text(chat_id: int, uid: int) -> str:
    wallet = economy_core.get_wallet(chat_id, uid)
    bank = economy_core.get_bank_balance(chat_id, uid)
    total = wallet + bank
    return (
        "╭━━━━━━━━━━━━━━━━━━━━╮\n"
        "   🛍 پنل اقتصاد\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"{_tier_badge(wallet)} کیف پول: {_fmt(wallet)} {config.CURRENCY_NAME}\n"
        f"{_tier_badge(bank)} بانک: {_fmt(bank)} {config.CURRENCY_NAME}\n\n"
        f"{_net_worth_line(total)}\n\n"
        "یه دسته رو انتخاب کن، بعد از توی هر بخش با دکمه خرید/استفاده کن.\n"
        "اگه پول کافی نباشه، ربات بهت می‌گه و چیزی کم نمی‌شه."
    )


def _hub_markup(chat_id: int) -> InlineKeyboardMarkup:
    quick = [
        InlineKeyboardButton("🎁 پاداش روزانه", callback_data="ecop:daily"),
        InlineKeyboardButton("🛒 پیشنهاد ویژه", callback_data="ecop:deal"),
    ]
    cat_buttons = [
        InlineKeyboardButton(CATEGORIES[key]["title"], callback_data=f"ecop:cat:{key}")
        for key in CATEGORY_ORDER
    ]
    rows = _rows_of(quick, 2) + _rows_of(cat_buttons, 2)
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
            "🔒 اقتصاد این گروه خاموشه. یه ادمین باید از ‎/menu بخش «اقتصاد و نظرسنجی» روشنش کنه."
        )
        return
    await _render_hub(update, context, edit=False)


# ═══════════════════════════════════════════════════════════════════════════
# 📂 صفحه‌ی هر دسته
# ═══════════════════════════════════════════════════════════════════════════

def _category_text(chat_id: int, uid: int, key: str) -> str:
    cat = CATEGORIES[key]
    wallet = economy_core.get_wallet(chat_id, uid)
    return (
        f"{cat['title']}\n"
        "─────────────────\n"
        f"{cat['desc']}\n\n"
        f"{_tier_badge(wallet)} موجودی: {_fmt(wallet)} {config.CURRENCY_NAME}"
    )


def _category_markup(chat_id: int, key: str) -> InlineKeyboardMarkup:
    cat = CATEGORIES[key]
    buttons = []
    for label, kind, val in cat["items"]:
        if kind == "sec":
            mod_key = _SECTION_MODULE_KEY.get(val)
            locked = mod_key is not None and not _module_on(chat_id, mod_key)
            label = f"🔒 {label}" if locked else label
            cb = f"ecop:sec:{val}"
        else:
            cb = f"ecop:{val}"
        buttons.append(InlineKeyboardButton(label, callback_data=cb))
    rows = _rows_of(buttons, 1)
    rows.append(_category_back_row())
    return InlineKeyboardMarkup(rows)


async def _render_category(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    if key not in CATEGORIES:
        await update.callback_query.answer()
        return
    chat = update.effective_chat
    uid = update.effective_user.id
    text = _category_text(chat.id, uid, key)
    markup = _category_markup(chat.id, key)
    try:
        await update.callback_query.edit_message_text(text, reply_markup=markup)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# 🧰 اجرای امن دستورات موجود (با شبیه‌سازی context.args)
# ═══════════════════════════════════════════════════════════════════════════

async def _run(update: Update, context: ContextTypes.DEFAULT_TYPE, func, args: list):
    """یه تابع _command موجود رو با آرگومان‌های داده‌شده صدا می‌زنه.
    خروجی خودِ تابع (پیام جدید) دست نخورده باقی می‌مونه؛ ما فقط بعدش
    صفحه‌ی مربوطه رو رفرش می‌کنیم."""
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
    """بعد از یه اکشن، همون بخش رو با اعداد تازه دوباره می‌سازه."""
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
# 🛍 فروشگاه اصلی (SHOP_ITEMS + ITEM_CATALOG)
# ═══════════════════════════════════════════════════════════════════════════

async def _render_shop(chat_id: int, uid: int):
    host = _host()
    wallet = economy_core.get_wallet(chat_id, uid)
    overrides = host._shop_price_overrides[chat_id]
    hidden = host._shop_hidden_items[chat_id]
    owned = host._owned_badges[chat_id][uid]
    lines = [f"🛍 فروشگاه ({_tier_badge(wallet)} موجودی: {_fmt(wallet)} {config.CURRENCY_NAME})", ""]
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
    dep_buttons = [
        InlineKeyboardButton(f"{_mark(wallet >= a)} ➕ سپرده {_fmt(a)}", callback_data=f"ecop:bank:dep:{a}")
        for a in _BANK_PRESETS
    ]
    if wallet > 0:
        dep_buttons.append(InlineKeyboardButton(f"✅ ➕ سپرده همه ({_fmt(wallet)})", callback_data="ecop:bank:dep:all"))
    wd_buttons = [
        InlineKeyboardButton(f"{_mark(bank >= a)} ➖ برداشت {_fmt(a)}", callback_data=f"ecop:bank:wd:{a}")
        for a in _BANK_PRESETS
    ]
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
    lines = [f"📈 بازار ({_tier_badge(wallet)} موجودی: {_fmt(wallet)} {config.CURRENCY_NAME})", ""]
    rows = []
    for symbol, info in economy_market.ASSETS.items():
        price = economy_market.current_price(chat_id, symbol)
        held = portfolio.get(symbol, {}).get("qty", 0)
        held_txt = f" | داری: {held:.4f}" if held else ""
        lines.append(f"• {info['name']} ({symbol}): {_fmt(round(price))} {config.CURRENCY_NAME}{held_txt}")
        row = [
            InlineKeyboardButton(f"{_mark(wallet >= spend)} 🛒 {symbol} {_fmt(spend)}", callback_data=f"ecop:mk:buy:{symbol}:{spend}")
            for spend in _MARKET_SPEND_PRESETS
        ]
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
# 🏙 شهر
# ═══════════════════════════════════════════════════════════════════════════

async def _render_city(chat_id: int, uid: int):
    wallet = economy_core.get_wallet(chat_id, uid)
    if not economy_city.has_city(chat_id, uid):
        text = (
            f"🏙 شهر من ({_tier_badge(wallet)} موجودی: {_fmt(wallet)} {config.CURRENCY_NAME})\n\n"
            "هنوز شهری نساختی. شهرسازی رایگانه و شروعِ ساختن یه امپراتوری اقتصادیه؛ "
            "بعد از ساختش می‌تونی توش ساختمان بسازی و درآمد غیرفعال بگیری."
        )
        rows = [[InlineKeyboardButton("🏙 ساخت شهر (رایگان)", callback_data="ecop:city:found")]]
        rows.append(_back_row("city"))
        return text, InlineKeyboardMarkup(rows)

    city = economy_city._city(chat_id, uid)
    level = city.get("level", 1)
    buildings = city.get("buildings", {})
    built_count = sum(1 for lvl in buildings.values() if lvl > 0)
    lines = [
        f"🏙 شهر من — سطح {level}",
        f"🏗 ساختمان‌های ساخته‌شده: {built_count}/{len(config.CITY_BUILDINGS)}",
        "",
        "برای ساخت/ارتقای هر ساختمون، روی دکمه‌اش بزن:",
    ]
    buttons = []
    for key, info in config.CITY_BUILDINGS.items():
        lvl = buildings.get(key, 0)
        locked = level < info["unlock_city_level"]
        if locked:
            buttons.append(InlineKeyboardButton(
                f"🔒 {info['name']} — نیاز سطح {info['unlock_city_level']}", callback_data="ecop:noop"))
            continue
        if lvl == 0:
            cost = info["base_cost"]
            label = f"{_mark(wallet >= cost)} ⬜ {info['name']} — ساخت {_fmt(cost)}"
        else:
            cost = round(info["base_cost"] * (1 + lvl * config.CITY_BUILDING_UPGRADE_MULTIPLIER))
            label = f"{_mark(wallet >= cost)} ✅ {info['name']} Lv{lvl} — ارتقا {_fmt(cost)}"
        buttons.append(InlineKeyboardButton(label, callback_data=f"ecop:city:build:{key}"))
    rows = _rows_of(buttons, 1)
    next_cost = config.CITY_LEVEL_UPGRADE_COST.get(level + 1)
    if next_cost:
        rows.append([InlineKeyboardButton(
            f"{_mark(wallet >= next_cost)} ⬆️ ارتقای شهر به سطح {level + 1} — {_fmt(next_cost)}",
            callback_data="ecop:city:upgrade")])
    else:
        lines.append("🏆 شهرت همین الان هم توی بالاترین سطحه.")
    rows.append(_back_row("city"))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _cb_city(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, key: str | None):
    if action == "found":
        await _run(update, context, economy_city.build_city_command, [])
    elif action == "upgrade":
        await _run(update, context, economy_city.upgrade_city_command, [])
    elif action == "build":
        await _run(update, context, economy_city.buildings_command, [key])
    await update.callback_query.answer()
    await _refresh(update, context, "city")


# ═══════════════════════════════════════════════════════════════════════════
# 🗺 منطقه‌ی زندگی
# ═══════════════════════════════════════════════════════════════════════════

async def _render_district(chat_id: int, uid: int):
    wallet = economy_core.get_wallet(chat_id, uid)
    current = economy_district.district_of(chat_id, uid)
    lines = [f"🗺 منطقه‌ی زندگی ({_tier_badge(wallet)} موجودی: {_fmt(wallet)} {config.CURRENCY_NAME})", ""]
    buttons = []
    for key, info in config.DISTRICTS.items():
        if key == current:
            label = f"📍 {info['name']} (همینجایی)"
            cb = "ecop:noop"
        else:
            cost_txt = "رایگان" if info["move_cost"] == 0 else _fmt(info["move_cost"])
            mark = _mark(wallet >= info["move_cost"])
            label = f"{mark} {info['name']} — جابه‌جایی {cost_txt} (نیاز سطح {info['min_level']})"
            cb = f"ecop:dist:move:{key}"
        buttons.append(InlineKeyboardButton(label, callback_data=cb))
    rows = _rows_of(buttons, 1)
    rows.append(_back_row("district"))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _cb_district(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    await _run(update, context, economy_district.move_district_command, [key])
    await update.callback_query.answer()
    await _refresh(update, context, "district")


# ═══════════════════════════════════════════════════════════════════════════
# 🚗 خودرو
# ═══════════════════════════════════════════════════════════════════════════

async def _render_vehicle(chat_id: int, uid: int):
    wallet = economy_core.get_wallet(chat_id, uid)
    owned = economy_vehicle.owned(chat_id, uid)
    lines = [f"🚗 خودروهای من ({_tier_badge(wallet)} موجودی: {_fmt(wallet)} {config.CURRENCY_NAME})", ""]
    rows = []
    if owned:
        for v in owned:
            info = config.VEHICLE_TYPES.get(v["type"], {})
            label = f"🚗 {info.get('name', v['type'])} #{v['id']} — سلامت {v['condition']}٪ | سوخت {v['fuel']}٪"
            rows.append([InlineKeyboardButton(label, callback_data=f"ecop:veh:item:{v['id']}")])
    else:
        lines.append("هنوز خودرویی نداری. از لیست زیر یکی بخر:")
    lines.append("")
    lines.append("🆕 خرید خودروی نو:")
    buy_buttons = []
    for key, info in config.VEHICLE_TYPES.items():
        price = economy_vehicle.current_price(chat_id, uid, key)
        mark = _mark(wallet >= price)
        buy_buttons.append(InlineKeyboardButton(f"{mark} {info['name']} — {_fmt(price)}", callback_data=f"ecop:veh:buy:{key}"))
    rows += _rows_of(buy_buttons, 1)
    rows.append(_back_row("vehicle"))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _render_vehicle_item(chat_id: int, uid: int, vid: int):
    target = next((v for v in economy_vehicle.owned(chat_id, uid) if v["id"] == vid), None)
    if not target:
        return await _render_vehicle(chat_id, uid)
    info = config.VEHICLE_TYPES.get(target["type"], {})
    insured = economy_vehicle._is_insured(target) if hasattr(economy_vehicle, "_is_insured") else False
    lines = [
        f"🚗 {info.get('name', target['type'])} #{target['id']}",
        "─────────────────",
        f"🔧 سلامت (Condition): {target['condition']}٪",
        f"⛽ سوخت: {target['fuel']}٪",
        f"🛡 بیمه: {'فعال ✅' if insured else 'ندارد ❌'}",
    ]
    value = economy_vehicle.current_value(target) if hasattr(economy_vehicle, "current_value") else None
    repair_cost = economy_vehicle.repair_cost(target) if hasattr(economy_vehicle, "repair_cost") else 0
    refuel_cost = economy_vehicle.refuel_cost(chat_id, target) if hasattr(economy_vehicle, "refuel_cost") else 0
    insure_cost = economy_vehicle.insurance_cost(target) if hasattr(economy_vehicle, "insurance_cost") else 0
    resale_fraction = getattr(config, "VEHICLE_RESALE_BASE_FRACTION", 0.6)
    payout = round(value * resale_fraction) if value else 0

    rows = [[InlineKeyboardButton("🏁 رانندگی", callback_data=f"ecop:veh:act:{vid}:drive")]]
    row2 = []
    if target["condition"] < 100:
        row2.append(InlineKeyboardButton(f"🔧 تعمیر — {_fmt(repair_cost)}", callback_data=f"ecop:veh:act:{vid}:repair"))
    if info.get("fuel_consumption_per_100km", 0) > 0 and target["fuel"] < 100:
        row2.append(InlineKeyboardButton(f"⛽ سوخت‌گیری — {_fmt(refuel_cost)}", callback_data=f"ecop:veh:act:{vid}:refuel"))
    if row2:
        rows.append(row2)
    rows.append([InlineKeyboardButton(f"🛡 بیمه — {_fmt(insure_cost)}", callback_data=f"ecop:veh:act:{vid}:insure")])
    rows.append([InlineKeyboardButton(f"💵 فروش به بازار — {_fmt(payout)}", callback_data=f"ecop:veh:act:{vid}:sell")])
    rows.append([
        InlineKeyboardButton("🔄 تازه‌سازی", callback_data=f"ecop:veh:item:{vid}"),
        InlineKeyboardButton("🔙 لیست خودروها", callback_data="ecop:sec:vehicle"),
    ])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _cb_vehicle(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str, a: str, b: str | None):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    if kind == "buy":
        await _run(update, context, economy_vehicle.buy_vehicle_command, [a])
        await update.callback_query.answer()
        await _refresh(update, context, "vehicle")
        return
    if kind == "item":
        await update.callback_query.answer()
        text, markup = await _render_vehicle_item(chat_id, uid, int(a))
        try:
            await update.callback_query.edit_message_text(text, reply_markup=markup)
        except Exception:
            pass
        return
    if kind == "act":
        vid, action = a, b
        mapping = {
            "drive": economy_vehicle.drive_vehicle_command,
            "repair": economy_vehicle.repair_vehicle_command,
            "refuel": economy_vehicle.refuel_vehicle_command,
            "insure": economy_vehicle.insure_vehicle_command,
            "sell": economy_vehicle.sell_vehicle_command,
        }
        func = mapping.get(action)
        if func:
            await _run(update, context, func, [vid])
        await update.callback_query.answer()
        if action == "sell":
            await _refresh(update, context, "vehicle")
        else:
            text, markup = await _render_vehicle_item(chat_id, uid, int(vid))
            try:
                await update.callback_query.edit_message_text(text, reply_markup=markup)
            except Exception:
                pass
        return
    await update.callback_query.answer()


# ═══════════════════════════════════════════════════════════════════════════
# 🏢 کسب‌وکار
# ═══════════════════════════════════════════════════════════════════════════

_BIZ_INVENTORY_PRESETS = [10, 50, 100]


async def _render_business(chat_id: int, uid: int):
    wallet = economy_core.get_wallet(chat_id, uid)
    owned = economy_business.owned(chat_id, uid)
    lines = [f"🏢 کسب‌وکارهای من ({_tier_badge(wallet)} موجودی: {_fmt(wallet)} {config.CURRENCY_NAME})", ""]
    rows = []
    if owned:
        for b in owned:
            info = config.BUSINESS_TYPES.get(b["type"], {})
            label = f"🏢 {info.get('name', b['type'])} #{b['id']} — سطح {b['level']} | کارمند {b['employees']}/{info.get('max_employees', 0)}"
            rows.append([InlineKeyboardButton(label, callback_data=f"ecop:biz:item:{b['id']}")])
    else:
        lines.append("هنوز کسب‌وکاری نداری. از لیست زیر یکی بخر:")
    lines.append("")
    lines.append("🆕 خرید کسب‌وکار جدید:")
    buy_buttons = []
    for key, info in config.BUSINESS_TYPES.items():
        price = economy_business.current_price(chat_id, uid, key)
        mark = _mark(wallet >= price)
        buy_buttons.append(InlineKeyboardButton(f"{mark} {info['name']} — {_fmt(price)}", callback_data=f"ecop:biz:buy:{key}"))
    rows += _rows_of(buy_buttons, 1)
    rows.append(_back_row("business"))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _render_business_item(chat_id: int, uid: int, bid: int):
    target = next((b for b in economy_business.owned(chat_id, uid) if b["id"] == bid), None)
    if not target:
        return await _render_business(chat_id, uid)
    info = config.BUSINESS_TYPES.get(target["type"], {})
    lines = [
        f"🏢 {info.get('name', target['type'])} #{target['id']}",
        "─────────────────",
        f"⭐ سطح: {target['level']}",
        f"👥 کارمند: {target['employees']}/{info.get('max_employees', 0)}",
        f"📦 موجودی انبار: {target['inventory']}/{info.get('inventory_capacity', 0)}",
    ]
    hire_cost = economy_business.hire_cost(target) if hasattr(economy_business, "hire_cost") else 0
    upgrade_cost = economy_business.upgrade_cost(target) if hasattr(economy_business, "upgrade_cost") else 0
    tax_due = economy_business.tax_due(target) if hasattr(economy_business, "tax_due") else 0
    value = economy_business.current_value(target) if hasattr(economy_business, "current_value") else None
    resale_fraction = getattr(config, "BUSINESS_RESALE_FRACTION", 0.55)
    payout = round(value * resale_fraction) if value else 0
    unit_cost = info.get("inventory_cost_per_unit", 0)

    rows = [[InlineKeyboardButton(f"👷 استخدام — {_fmt(hire_cost)}", callback_data=f"ecop:biz:act:{bid}:hire")]]
    if target["employees"] > 0:
        rows[-1].append(InlineKeyboardButton("🚪 اخراج یه نفر", callback_data=f"ecop:biz:act:{bid}:fire"))

    inv_row = []
    for qty in _BIZ_INVENTORY_PRESETS:
        cost = round(qty * unit_cost)
        inv_row.append(InlineKeyboardButton(f"📦 خرید {qty} — {_fmt(cost)}", callback_data=f"ecop:biz:inv:{bid}:{qty}"))
    rows += _rows_of(inv_row, 1)

    rows.append([InlineKeyboardButton("📢 شروع کمپین تبلیغاتی", callback_data=f"ecop:biz:act:{bid}:ad")])
    rows.append([InlineKeyboardButton(f"⭐ ارتقا — {_fmt(upgrade_cost)}", callback_data=f"ecop:biz:act:{bid}:upgrade")])
    if tax_due > 0:
        rows.append([InlineKeyboardButton(f"💰 پرداخت مالیات — {_fmt(tax_due)}", callback_data=f"ecop:biz:act:{bid}:tax")])
    rows.append([InlineKeyboardButton(f"💵 فروش به بازار — {_fmt(payout)}", callback_data=f"ecop:biz:act:{bid}:sell")])
    rows.append([
        InlineKeyboardButton("🔄 تازه‌سازی", callback_data=f"ecop:biz:item:{bid}"),
        InlineKeyboardButton("🔙 لیست کسب‌وکارها", callback_data="ecop:sec:business"),
    ])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _cb_business(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str, a: str, b: str | None):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    if kind == "buy":
        await _run(update, context, economy_business.buy_business_command, [a])
        await update.callback_query.answer()
        await _refresh(update, context, "business")
        return
    if kind == "item":
        await update.callback_query.answer()
        text, markup = await _render_business_item(chat_id, uid, int(a))
        try:
            await update.callback_query.edit_message_text(text, reply_markup=markup)
        except Exception:
            pass
        return
    if kind == "inv":
        bid, qty = a, b
        await _run(update, context, economy_business.buy_inventory_command, [bid, qty])
        await update.callback_query.answer()
        text, markup = await _render_business_item(chat_id, uid, int(bid))
        try:
            await update.callback_query.edit_message_text(text, reply_markup=markup)
        except Exception:
            pass
        return
    if kind == "act":
        bid, action = a, b
        mapping = {
            "hire": economy_business.hire_command,
            "fire": economy_business.fire_command,
            "ad": economy_business.advertise_command,
            "upgrade": economy_business.upgrade_business_command,
            "tax": economy_business.pay_business_tax_command,
            "sell": economy_business.sell_business_command,
        }
        func = mapping.get(action)
        if func:
            await _run(update, context, func, [bid])
        await update.callback_query.answer()
        if action == "sell":
            await _refresh(update, context, "business")
        else:
            text, markup = await _render_business_item(chat_id, uid, int(bid))
            try:
                await update.callback_query.edit_message_text(text, reply_markup=markup)
            except Exception:
                pass
        return
    await update.callback_query.answer()


# ═══════════════════════════════════════════════════════════════════════════
# 👔 لباس
# ═══════════════════════════════════════════════════════════════════════════

async def _render_clothing(chat_id: int, uid: int):
    wallet = economy_core.get_wallet(chat_id, uid)
    owned = economy_clothing.owned_items(chat_id, uid)
    equipped = economy_clothing.equipped(chat_id, uid)
    equipped_keys = set(equipped.values())
    lines = [f"👔 فروشگاه لباس ({_tier_badge(wallet)} موجودی: {_fmt(wallet)} {config.CURRENCY_NAME})", ""]

    buttons = []
    if equipped:
        for slot, key in equipped.items():
            info = economy_clothing.CATALOG.get(key, {})
            buttons.append(InlineKeyboardButton(
                f"🔙 درآوردن: {info.get('name', key)}", callback_data=f"ecop:cloth:unwear:{slot}"))
    for key in owned:
        if key in equipped_keys:
            continue
        info = economy_clothing.CATALOG.get(key, {})
        buttons.append(InlineKeyboardButton(f"👕 پوشیدن: {info.get('name', key)}", callback_data=f"ecop:cloth:wear:{key}"))
    if buttons:
        lines.append("🎒 آیتم‌های تو:")
    rows = _rows_of(buttons, 1)

    buy_buttons = []
    for key, info in economy_clothing.CATALOG.items():
        if key in owned:
            continue
        mark = _mark(wallet >= info["price"])
        buy_buttons.append(InlineKeyboardButton(f"{mark} {info['name']} — {_fmt(info['price'])}", callback_data=f"ecop:cloth:buy:{key}"))
    if buy_buttons:
        lines.append("🛍 برای خرید:")
    rows += _rows_of(buy_buttons, 1)
    rows.append(_back_row("clothing"))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _cb_clothing(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, val: str):
    if action == "buy":
        await _run(update, context, economy_clothing.buy_clothing_command, [val])
    elif action == "wear":
        await _run(update, context, economy_clothing.wear_command, [val])
    elif action == "unwear":
        await _run(update, context, economy_clothing.unwear_command, [val])
    await update.callback_query.answer()
    await _refresh(update, context, "clothing")


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
        m = _mark(wallet >= bet)
        rows.append([
            InlineKeyboardButton(f"{m} 🎲 تاس {_fmt(bet)}", callback_data=f"ecop:game:dice:{bet}"),
            InlineKeyboardButton(f"{m} 🪙 شیر {_fmt(bet)}", callback_data=f"ecop:game:cfS:{bet}"),
            InlineKeyboardButton(f"{m} 🪙 خط {_fmt(bet)}", callback_data=f"ecop:game:cfX:{bet}"),
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
# 🦹 دزدی و زندان
# ═══════════════════════════════════════════════════════════════════════════

async def _render_theft(chat_id: int, uid: int):
    jailed = economy_theft.is_jailed(chat_id, uid)
    wanted = economy_theft.wanted_level(chat_id, uid)
    lines = ["🦹 دزدی و زندان", ""]
    rows = []
    if jailed:
        remaining = economy_theft.jail_remaining_seconds(chat_id, uid)
        fmt_dur = economy_theft._fmt_duration(remaining) if hasattr(economy_theft, "_fmt_duration") else f"{int(remaining)}s"
        lines.append(f"⛓️ الان زندانی هستی؛ {fmt_dur} مونده.")
        rows.append([InlineKeyboardButton("🔓 تلاش برای فرار", callback_data="ecop:theft:escape")])
    else:
        lines.append("🔓 الان زندانی نیستی.")
    if wanted > 0:
        stars = "⭐" * min(5, max(1, round(wanted)))
        lines.append(f"🚔 سطح تحت‌تعقیب بودن: {wanted:.1f} {stars}")
    else:
        lines.append("😇 تحت تعقیب نیستی.")
    lines.append("")
    lines.append("برای دزدی: روی پیامِ یه نفرِ دیگه توی گروه ریپلای کن و بنویس «دزدی».")
    rows.append(_back_row("theft"))
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _cb_theft(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str):
    if action == "escape":
        await _run(update, context, economy_theft.escape_command, [])
    await update.callback_query.answer()
    await _refresh(update, context, "theft")


# ═══════════════════════════════════════════════════════════════════════════
# 👑 رتبه‌بندی‌ها
# ═══════════════════════════════════════════════════════════════════════════

async def _render_leaderboard(chat_id: int, uid: int):
    text = "👑 جدول رتبه‌بندی‌ها\n\nهر دسته رو بزنی، همون جدول برات فرستاده می‌شه:"
    buttons = [
        InlineKeyboardButton(label, callback_data=f"ecop:lbcat:{key}")
        for key, label in config.LEADERBOARD_CATEGORIES.items()
    ]
    rows = _rows_of(buttons, 2)
    rows.append(_back_row("lb"))
    return text, InlineKeyboardMarkup(rows)


async def _cb_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    await _run(update, context, economy_leaderboard.leaderboard_command, [key])
    await update.callback_query.answer()


# ═══════════════════════════════════════════════════════════════════════════
# 🌍 اقتصاد کلان گروه (فاز ۲ V18 — Living Economy Engine، فقط نمایشی/اطلاعاتی)
# ═══════════════════════════════════════════════════════════════════════════

async def _render_macro(chat_id: int, uid: int):
    text = economy_macro.format_dashboard_text(chat_id)
    rows = [_back_row("macro")]
    return text, InlineKeyboardMarkup(rows)


# ═══════════════════════════════════════════════════════════════════════════
# 🏢 بورسِ سهامِ شرکت‌ها (فاز ۳ V18) — نمایشی؛ خرید/فروش/IPO با دستورِ متنی
# ═══════════════════════════════════════════════════════════════════════════

async def _render_stock(chat_id: int, uid: int):
    text = economy_stock.format_exchange_text(chat_id) + "\n\n" + economy_stock.format_portfolio_text(chat_id, uid)
    rows = [_back_row("stock")]
    return text, InlineKeyboardMarkup(rows)


# ═══════════════════════════════════════════════════════════════════════════
# 🔨 مزایده‌ها (فاز ۳ V18) — نمایشی؛ ایجاد/پیشنهاد با دستورِ متنی
# ═══════════════════════════════════════════════════════════════════════════

async def _render_auction(chat_id: int, uid: int):
    active_text = await economy_auction.format_active_text(chat_id)
    mine_text = economy_auction.format_my_auctions_text(chat_id, uid)
    rows = [_back_row("auction")]
    return active_text + "\n\n" + mine_text, InlineKeyboardMarkup(rows)


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
    "city": _render_city,
    "district": _render_district,
    "vehicle": _render_vehicle,
    "business": _render_business,
    "clothing": _render_clothing,
    "pet": _render_pet,
    "marriage": _render_marriage,
    "games": _render_games,
    "black": _render_black,
    "ug": _render_ug,
    "theft": _render_theft,
    "lb": _render_leaderboard,
    "macro": _render_macro,
    "stock": _render_stock,
    "auction": _render_auction,
}

_SECTION_MODULE_KEY = {
    "bank": "economy_module_bank",
    "jobs": "economy_module_jobs",
    "market": "economy_module_market",
    "stock": "economy_module_stock",
    "auction": "economy_module_auction",
    "prop": "economy_module_property",
    "city": "economy_module_city",
    "district": "economy_module_district",
    "vehicle": "economy_module_vehicle",
    "business": "economy_module_business",
    "clothing": "economy_module_clothing",
    "pet": "economy_module_pet",
    "marriage": "economy_module_marriage",
    "games": "economy_module_games",
    "black": "economy_module_blackmarket",
    "ug": "economy_module_underground",
    "theft": "economy_module_theft",
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
        if action == "noop":
            await query.answer("🔒 این بخش هنوز برات باز نشده.", show_alert=True)
            return

        if action == "hub":
            await query.answer()
            await _render_hub(update, context, edit=True)
            return

        if action == "cat":
            key = parts[1] if len(parts) > 1 else ""
            await query.answer()
            await _render_category(update, context, key)
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

        if action == "missions":
            await _run(update, context, economy_missions.missions_command, [])
            await query.answer()
            return

        if action == "ach":
            await _run(update, context, economy_achievements.networth_command, [])
            await query.answer()
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
        if action == "city":
            await _cb_city(update, context, parts[1], parts[2] if len(parts) > 2 else None)
            return
        if action == "dist":
            await _cb_district(update, context, parts[2])
            return
        if action == "veh":
            await _cb_vehicle(update, context, parts[1], parts[2], parts[3] if len(parts) > 3 else None)
            return
        if action == "biz":
            await _cb_business(update, context, parts[1], parts[2], parts[3] if len(parts) > 3 else None)
            return
        if action == "cloth":
            await _cb_clothing(update, context, parts[1], parts[2])
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
        if action == "theft":
            await _cb_theft(update, context, parts[1])
            return
        if action == "lbcat":
            await _cb_leaderboard(update, context, parts[1])
            return

        await query.answer()
    except Exception as e:
        logger.warning(f"خطا توی دیسپچر پنل اقتصاد ({data}): {e}")
        try:
            await query.answer("⚠️ یه خطا پیش اومد.", show_alert=True)
        except Exception:
            pass
