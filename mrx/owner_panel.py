# -*- coding: utf-8 -*-
"""
🛡 پنل مالک — مدیریت پویای ادمین‌های ربات (بدون نیاز به ویرایش config.py)
=================================================================================
این ماژول یه لایه‌ی *اضافه* روی سیستم فعلی ADMIN_IDS/ADMIN_PERMISSIONS (توی
config.py) می‌سازه؛ هیچ‌کدوم از اون‌ها رو حذف یا دستکاری نمی‌کنه. آدم‌هایی که
همین الان توی config.py به‌صورت دستی ادمین شدن، دقیقاً همونجوری که بودن کار
می‌کنن. این ماژول فقط یه راه دوم اضافه می‌کنه: «مالک ربات» (owner) می‌تونه از
توی چت خصوصی ربات، بدون لمس کد و بدون ری‌استارت، آدم جدید ادمین کنه/عزل کنه/
دسترسی‌هاش رو عوض کنه.

مالک کیه؟
  - اگه config.OWNER_IDS پر باشه → دقیقاً همون آیدی(های) توش.
  - اگه خالی باشه (پیش‌فرض) → اولین آیدیِ config.ADMIN_IDS، به‌عنوان یه fallback
    امن (تا این پنل بدون تنظیم اضافه هم کار کنه)، ولی توصیه می‌شه OWNER_IDS رو
    صریح پر کنی.
  مالک، صرف‌نظر از اینکه توی ADMIN_IDS/ADMIN_PERMISSIONS چی نوشته، همیشه و به
  همه‌ی بخش‌های ربات دسترسی کامل داره.

جریان کار «ادمین کردن» — کاملاً دستیِ مالک، تایید با یوزرنیم + رمز، خودکار سمت طرف:
  ۱) مالک از پنل «➕ ادمین جدید» رو می‌زنه و خودش یه «یوزرنیم ادمین» دلخواه و یه
     «رمز ادمین» دلخواه تعیین می‌کنه (یا با دکمه‌ی 🎲 یه رمز تصادفیِ امن بسازه)،
     بعد دسترسی‌هاش رو با دکمه‌های تیک‌دار مشخص می‌کنه. این یوزرنیم/رمز ربطی به
     اکانت تلگرام طرف نداره - صرفاً یه «کد ورود ادمین» ساخته‌ی خود مالکه.
  ۲) ربات فقط هَشِ رمز رو ذخیره می‌کنه (نه خود رمز) تا حتی با لو رفتن فایل state
     هم رمزها قابل استفاده نباشن. مالک یوزرنیم+رمز رو خودش (پیام خصوصی، تماس،
     هرجا) به طرف می‌ده - پیام آماده برای فوروارد/کپی هم توی پنل ساخته می‌شه.
  ۳) طرف توی پیوی خود ربات می‌زنه ‎/adminlogin و ربات قدم‌به‌قدم ازش اول یوزرنیم
     بعد رمز رو می‌پرسه (کاملاً خودکار و بدون نیاز به سینتکس خاص) - یا برای
     میانبر، یکجا می‌زنه ‎/claimadmin <یوزرنیم> <رمز>.
     - حداکثر ۵ تلاش اشتباه برای رمز؛ بعدش دعوت باطل می‌شه و مالک خبردار می‌شه
       (جلوی حدس‌زدن brute-force رو می‌گیره).
  ۴) با تاییدِ موفق، همون چت‌آیدیِ تلگرامیِ طرف (هرکی بوده که رمز درست رو زده)
     به‌عنوان ادمین پویا ذخیره می‌شه - دقیقاً با همون دسترسی‌هایی که مالک انتخاب
     کرده. از همون لحظه، has_permission/is_admin توی bot.py این آدم رو می‌شناسن،
     دقیقاً مثل یه ادمینِ تعریف‌شده‌ی دستی توی config.py.

هر اقدام مالک (ساخت دعوت، تایید موفق/ناموفق، عزل، تغییر دسترسی) توی یه Audit
Log جدا ثبت می‌شه و از پنل («📋 گزارش») قابل مشاهده‌ست.

امنیت: پنل مدیریتی فقط توی چت خصوصی (پیوی) خود ربات کار می‌کنه و فقط برای
مالک باز می‌شه؛ ‎/adminlogin و ‎/claimadmin هم فقط توی پیوی کار می‌کنن ولی هرکسی
می‌تونه امتحان کنه - فقط کسی که یوزرنیم+رمزِ درست رو بدونه قبول می‌شه.
"""

from __future__ import annotations

import hashlib
import secrets
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

import config

# ---------------------------------------------------------------------------
# 🔑 تعریف دسترسی‌ها — دقیقاً همون کلیدهایی که config.ADMIN_PERMISSIONS ازشون
# استفاده می‌کنه (هیچ دسترسی جدیدی اختراع نشده، فقط همون‌ها از پنل قابل تنظیمن)
# ---------------------------------------------------------------------------
PERMISSION_KEYS: list[tuple[str, str]] = [
    ("menu", "📋 منو (روشن/خاموش قابلیت‌ها + دستورات متنی)"),
    ("notify", "📢 اطلاع‌رسانی همگانی"),
    ("manage_relationships", "🤝 دوست/دشمن‌کردن + ترمیناتور"),
    ("manage_blacklist", "🛑 بلک‌لیست"),
    ("moderate", "🛡 مدیریت (اخطار/میوت/بن/پاک‌سازی)"),
    ("manage_admins", "👑 ادمین‌کردن/عزل ادمین تلگرامیِ گروه"),
    ("tag_members", "📣 تگ همه"),
    ("manage_economy", "💰 پنل اقتصاد"),
    ("manage_version", "🏷 نسخه‌ی ربات"),
    ("manage_groups", "🏘 مدیریت گروه‌ها (پیوی)"),
]
PERMISSION_LABELS: dict[str, str] = dict(PERMISSION_KEYS)
SHORT_LABELS: dict[str, str] = {
    "menu": "منو", "notify": "اطلاع‌رسانی", "manage_relationships": "روابط",
    "manage_blacklist": "بلک‌لیست", "moderate": "مدیریت", "manage_admins": "ادمین‌گروه",
    "tag_members": "تگ", "manage_economy": "اقتصاد", "manage_version": "نسخه",
    "manage_groups": "گروه‌ها",
}
ALL_PERMISSIONS: set[str] = {k for k, _ in PERMISSION_KEYS}

_INVITE_TTL_SECONDS = getattr(config, "OWNER_PANEL_INVITE_TTL_HOURS", 24) * 3600
_MAX_CLAIM_ATTEMPTS = getattr(config, "OWNER_PANEL_MAX_CLAIM_ATTEMPTS", 5)
_AUDIT_LOG_MAX = getattr(config, "OWNER_PANEL_AUDIT_LOG_MAX", 300)
_MIN_USERNAME_LEN = 3
_MIN_PASSWORD_LEN = 4

# ---------------------------------------------------------------------------
# 💾 State (در حافظه + ذخیره روی دیسک)
# ---------------------------------------------------------------------------
# user_id(تلگرام) -> {"username": str, "permissions": list[str], "added_by": int,
#                      "added_at": float}
_dynamic_admins: dict[int, dict] = {}

# username(نرمال‌شده) -> {"password_hash": str, "permissions": list[str],
#                          "created_by": int, "created_at": float, "expires": float,
#                          "attempts": int}
# توجه: این «یوزرنیم ادمین» همون @یوزرنیم تلگرامی نیست - یه کد ورودِ دلخواهِ
# ساخته‌ی مالکه، مستقل از اکانت تلگرام طرف.
_pending_invites: dict[str, dict] = {}

# لاگ اقدامات مالک روی خود این پنل (جدا از economy_admin._admin_audit_log که
# مخصوص اقدامات داخل یه گروهه؛ این یکی سراسریه، چون این پنل فقط توی پیویه)
_audit_log: list[dict] = []

# ویزارد چندمرحله‌ای «ادمین جدید» + حالت ویرایش دسترسیِ ادمین موجود — کلید owner_id
_wizard: dict[int, dict] = {}
# منتظرِ ورودیِ متنیِ بعدیِ مالک (یوزرنیم/رمز دلخواه) — کلید owner_id -> نوع انتظار
_owner_text_wait: dict[int, dict] = {}
# ورود قدم‌به‌قدمِ طرف از طریق /adminlogin — کلید telegram_user_id -> {"stage": ...}
_login_wait: dict[int, dict] = {}


def _audit(actor_id: int, action: str, note: str = "") -> None:
    _audit_log.append({"ts": time.time(), "actor": actor_id, "action": action, "note": note})
    if len(_audit_log) > _AUDIT_LOG_MAX:
        del _audit_log[: len(_audit_log) - _AUDIT_LOG_MAX]


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _norm(u: str) -> str:
    return (u or "").strip().lstrip("@").lower()


def _valid_login_token(s: str, min_len: int) -> bool:
    return bool(s) and len(s) >= min_len and " " not in s


# ---------------------------------------------------------------------------
# 👑 Owner / Dynamic-admin lookups — نقطه‌ی اتصال به bot.py
# ---------------------------------------------------------------------------

def is_owner(user_id: int) -> bool:
    owners = list(getattr(config, "OWNER_IDS", []) or [])
    if owners:
        return user_id in owners
    admin_ids = getattr(config, "ADMIN_IDS", [])
    return bool(admin_ids) and user_id == admin_ids[0]


def is_dynamic_admin(user_id: int) -> bool:
    return user_id in _dynamic_admins


def is_admin_or_owner(user_id: int) -> bool:
    """برای bot.is_admin(): مالک + هر ادمینِ ساخته‌شده از این پنل."""
    return is_owner(user_id) or is_dynamic_admin(user_id)


def has_permission(user_id: int, permission: str) -> bool:
    """برای bot.has_permission(): فقط وقتی کاربر توی config.ADMIN_IDS نیست چک می‌شه.
    مالک همیشه True؛ ادمین پویا فقط دسترسی‌های انتخاب‌شده‌اش."""
    if is_owner(user_id):
        return True
    rec = _dynamic_admins.get(user_id)
    if rec is None:
        return False
    return permission in set(rec["permissions"])


def list_admins() -> list[tuple[int, dict]]:
    return sorted(_dynamic_admins.items(), key=lambda kv: kv[1].get("added_at", 0))


def _perm_txt(perms) -> str:
    return "، ".join(SHORT_LABELS.get(p, p) for p in perms) or "—"


# ---------------------------------------------------------------------------
# 🖱 دسترسی به پنل فقط توی پیوی، فقط برای مالک
# ---------------------------------------------------------------------------

async def _deny_if_not_owner(update: Update) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message
    if not chat or chat.type != "private":
        if message:
            await message.reply_text("🛡 این پنل فقط توی چت خصوصی (پیوی) خود ربات کار می‌کنه.")
        return True
    if not is_owner(user.id):
        if message:
            await message.reply_text("⛔ این پنل فقط برای مالک رباته.")
        return True
    return False


async def ownerpanel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/ownerpanel یا /adminpanel — باز کردن پنل مدیریت ادمین‌ها (فقط پیوی مالک)."""
    if await _deny_if_not_owner(update):
        return
    user = update.effective_user
    _wizard.pop(user.id, None)
    _owner_text_wait.pop(user.id, None)
    await update.effective_message.reply_text(_home_text(), reply_markup=_home_markup())


def _home_text() -> str:
    return (
        "🛡 پنل مدیریت ادمین‌های ربات\n\n"
        f"👑 ادمین‌های پویا (ساخته‌شده از همین پنل): {len(_dynamic_admins)}\n"
        f"🧾 دعوت‌های در انتظار تایید: {len(_pending_invites)}\n\n"
        "از دکمه‌های زیر استفاده کن:"
    )


def _home_markup() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("➕ ادمین جدید", callback_data="op:add")],
        [InlineKeyboardButton("👑 لیست ادمین‌ها", callback_data="op:list")],
        [InlineKeyboardButton("🧾 دعوت‌های در انتظار", callback_data="op:pending")],
        [InlineKeyboardButton("📋 گزارش اقدامات", callback_data="op:log")],
        [InlineKeyboardButton("❌ بستن", callback_data="op:close")],
    ]
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# 🔐 ورود طرف: هم به‌صورت قدم‌به‌قدم (/adminlogin) هم یک‌جا (/claimadmin)
# ---------------------------------------------------------------------------

async def adminlogin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/adminlogin — شروع ورود قدم‌به‌قدم: اول یوزرنیم ادمین، بعد رمزش."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type != "private":
        await message.reply_text("این دستور فقط توی پیوی خود ربات کار می‌کنه.")
        return
    _login_wait[user.id] = {"stage": "username"}
    await message.reply_text("🔐 ورود ادمین\n\nیوزرنیمی که مالک بهت داده رو بفرست:")


async def claimadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/claimadmin <یوزرنیم> <رمز> — میانبرِ یک‌مرحله‌ای همون چیزی که /adminlogin قدم‌به‌قدم می‌پرسه."""
    message = update.effective_message
    chat = update.effective_chat
    if not chat or chat.type != "private":
        await message.reply_text("این دستور فقط توی پیوی خود ربات کار می‌کنه.")
        return
    args = context.args or []
    if len(args) < 2:
        await message.reply_text(
            "استفاده: ‎/claimadmin <یوزرنیم> <رمز>\n"
            "یا ساده‌تر: فقط بزن ‎/adminlogin و قدم‌به‌قدم جلو برو."
        )
        return
    await _try_claim(update, context, args[0], args[1])


async def _handle_login_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """پیام بعدیِ کسی که /adminlogin زده رو مصرف می‌کنه. True یعنی مصرف شد."""
    message = update.effective_message
    user = update.effective_user
    state = _login_wait.get(user.id)
    if not state:
        return False

    text = (message.text or "").strip()
    if not text:
        return False

    if state["stage"] == "username":
        uname = _norm(text.split()[0])
        if uname not in _pending_invites:
            await message.reply_text("❌ همچین یوزرنیمی پیدا نشد یا قبلاً استفاده/لغو شده. دوباره بفرست، یا ‎/adminlogin رو از اول بزن.")
            return True
        state["username"] = uname
        state["stage"] = "password"
        await message.reply_text("🔑 حالا رمز رو بفرست:")
        return True

    if state["stage"] == "password":
        password = text.split()[0]
        uname = state["username"]
        _login_wait.pop(user.id, None)
        await _try_claim(update, context, uname, password)
        return True

    return False


async def _try_claim(update: Update, context: ContextTypes.DEFAULT_TYPE, username_raw: str, password: str) -> None:
    message = update.effective_message
    user = update.effective_user
    uname = _norm(username_raw)

    invite = _pending_invites.get(uname)
    if invite is None:
        await message.reply_text("❌ یوزرنیم/رمز درست نیست یا این دعوت قبلاً استفاده/لغو شده.")
        return

    if invite["expires"] < time.time():
        _pending_invites.pop(uname, None)
        _audit(invite["created_by"], "CLAIM_EXPIRED", note=f"@{uname}")
        await message.reply_text("⌛ این دعوت منقضی شده. از مالک بخواه دوباره دعوتت کنه.")
        await save_state()
        return

    if _hash(password) != invite["password_hash"]:
        invite["attempts"] += 1
        if invite["attempts"] >= _MAX_CLAIM_ATTEMPTS:
            _pending_invites.pop(uname, None)
            _audit(invite["created_by"], "CLAIM_LOCKED", note=f"@{uname} بعد از {_MAX_CLAIM_ATTEMPTS} تلاش ناموفق")
            await message.reply_text("❌ رمز اشتباهه. تعداد تلاش‌های مجاز تموم شد؛ این دعوت باطل شد.")
            await _notify_owner(
                context, invite["created_by"],
                f"⚠️ دعوت @{uname} بعد از {_MAX_CLAIM_ATTEMPTS} تلاش ناموفق خودکار باطل شد."
            )
        else:
            left = _MAX_CLAIM_ATTEMPTS - invite["attempts"]
            await message.reply_text(f"❌ رمز اشتباهه. {left} تلاش دیگه داری. دوباره بزن ‎/adminlogin.")
        await save_state()
        return

    perms = list(invite["permissions"])
    _dynamic_admins[user.id] = {
        "username": uname,
        "permissions": perms,
        "added_by": invite["created_by"],
        "added_at": time.time(),
    }
    _pending_invites.pop(uname, None)
    _audit(invite["created_by"], "CLAIM_SUCCESS", note=f"@{uname} ({user.id})")
    await save_state()

    await message.reply_text(
        "🎉 تبریک! از همین الان ادمین ربات حساب می‌شی.\n"
        f"🔑 دسترسی‌هات: {_perm_txt(perms)}"
    )
    tg_tag = f" (@{user.username})" if user.username else ""
    await _notify_owner(
        context, invite["created_by"],
        f"✅ @{uname} با موفقیت ادمین شد — آیدی {user.id}{tg_tag}.\n🔑 دسترسی‌ها: {_perm_txt(perms)}"
    )


async def _notify_owner(context: ContextTypes.DEFAULT_TYPE, owner_id: int, text: str) -> None:
    try:
        await context.bot.send_message(owner_id, text)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# ⌨️ دکمه‌های دسترسی (تیک‌دار) — مشترک بین «ساخت دعوت» و «ویرایش ادمین موجود»
# ---------------------------------------------------------------------------

def _permission_markup(selected: set[str], token: str, confirm_label: str, confirm_cb: str, back_cb: str) -> InlineKeyboardMarkup:
    rows = []
    for key, _label in PERMISSION_KEYS:
        mark = "☑️" if key in selected else "⬜"
        rows.append([InlineKeyboardButton(f"{mark} {SHORT_LABELS[key]}", callback_data=f"op:ptoggle:{token}:{key}")])
    rows.append([
        InlineKeyboardButton("✅ همه", callback_data=f"op:pall:{token}"),
        InlineKeyboardButton("🚫 هیچکدوم", callback_data=f"op:pnone:{token}"),
    ])
    rows.append([InlineKeyboardButton(confirm_label, callback_data=confirm_cb)])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data=back_cb), InlineKeyboardButton("❌ لغو", callback_data="op:cancel")])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# 📜 متن‌های کمکی برای لیست/دعوت/لاگ
# ---------------------------------------------------------------------------

def _admins_text() -> str:
    if not _dynamic_admins:
        return "👑 هنوز هیچ ادمینی از این پنل ساخته نشده."
    lines = ["👑 ادمین‌های پویا:", ""]
    for uid, rec in list_admins():
        when = time.strftime("%Y-%m-%d", time.localtime(rec["added_at"]))
        lines.append(f"• @{rec['username']} ({uid}) — از {when}\n  دسترسی: {_perm_txt(rec['permissions'])}")
    return "\n".join(lines)


def _admins_markup() -> InlineKeyboardMarkup:
    rows = []
    for uid, rec in list_admins():
        rows.append([
            InlineKeyboardButton(f"✏️ @{rec['username']}", callback_data=f"op:perm:{uid}"),
            InlineKeyboardButton("🗑 عزل", callback_data=f"op:revoke:{uid}"),
        ])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="op:home")])
    return InlineKeyboardMarkup(rows)


def _pending_text() -> str:
    if not _pending_invites:
        return "🧾 دعوت معلقی وجود نداره."
    lines = ["🧾 دعوت‌های در انتظار تایید:", ""]
    for uname, inv in _pending_invites.items():
        exp = time.strftime("%Y-%m-%d %H:%M", time.localtime(inv["expires"]))
        lines.append(f"• @{uname} — تا {exp}\n  دسترسی: {_perm_txt(inv['permissions'])}")
    return "\n".join(lines)


def _pending_markup() -> InlineKeyboardMarkup:
    rows = []
    for uname in _pending_invites:
        rows.append([InlineKeyboardButton(f"🗑 لغو دعوت @{uname}", callback_data=f"op:pcancel:{uname}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="op:home")])
    return InlineKeyboardMarkup(rows)


def _log_text() -> str:
    if not _audit_log:
        return "📋 هنوز هیچ اقدامی ثبت نشده."
    lines = ["📋 آخرین اقدامات:", ""]
    for entry in reversed(_audit_log[-20:]):
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry["ts"]))
        note = f" — {entry['note']}" if entry["note"] else ""
        lines.append(f"• {ts} — {entry['action']} (توسط {entry['actor']}){note}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 🖱 Callback handler
# ---------------------------------------------------------------------------

async def panel_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    if query.message.chat.type != "private" or not is_owner(user.id):
        await query.answer("⛔ فقط مالک رباته.", show_alert=True)
        return

    data = query.data
    await query.answer()

    if data == "op:close":
        await query.edit_message_text("پنل بسته شد. برای باز کردن دوباره: ‎/ownerpanel")
        _wizard.pop(user.id, None)
        _owner_text_wait.pop(user.id, None)
        return

    if data == "op:home" or data == "op:cancel":
        _wizard.pop(user.id, None)
        _owner_text_wait.pop(user.id, None)
        await query.edit_message_text(_home_text(), reply_markup=_home_markup())
        return

    if data == "op:add":
        _wizard[user.id] = {"stage": "username", "permissions": set()}
        _owner_text_wait[user.id] = {"type": "new_admin_username"}
        await query.edit_message_text(
            "➕ ادمین جدید\n\n"
            "یه «یوزرنیم ادمین» دلخواه برای این فرد بساز (فقط حروف انگلیسی/عدد/زیرخط، "
            f"حداقل {_MIN_USERNAME_LEN} کاراکتر، بدون فاصله). این ربطی به یوزرنیم واقعیِ "
            "تلگرامش نداره - فقط یه کد شناسایی برای ورودشه.\nمثال: shop_admin",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="op:cancel")]]),
        )
        return

    if data == "op:list":
        await query.edit_message_text(_admins_text(), reply_markup=_admins_markup())
        return

    if data == "op:pending":
        await query.edit_message_text(_pending_text(), reply_markup=_pending_markup())
        return

    if data == "op:log":
        await query.edit_message_text(_log_text(), reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 بازگشت", callback_data="op:home")]]
        ))
        return

    if data.startswith("op:pcancel:"):
        uname = data.split(":", 2)[2]
        inv = _pending_invites.pop(uname, None)
        if inv:
            _audit(user.id, "INVITE_CANCELLED", note=f"@{uname}")
            await save_state()
        await query.edit_message_text(_pending_text(), reply_markup=_pending_markup())
        return

    # ---- مرحله‌ی رمز (بعد از تایید یوزرنیم) ----
    if data == "op:genpass":
        wiz = _wizard.get(user.id)
        if not wiz or wiz.get("stage") != "password":
            await query.edit_message_text(_home_text(), reply_markup=_home_markup())
            return
        password = secrets.token_hex(4).upper()
        wiz["password"] = password
        wiz["stage"] = "permissions"
        wiz["permissions"] = set()
        _owner_text_wait.pop(user.id, None)
        await query.edit_message_text(
            f"🎲 رمز تصادفی ساخته شد: `{password}`\n(این رمز رو جایی یادداشت کن؛ آخرش دوباره نشونت می‌دیم.)\n\n"
            f"حالا دسترسی‌های ادمین جدید (@{wiz['username']}) رو انتخاب کن:",
            parse_mode="Markdown",
            reply_markup=_permission_markup(set(), "new", "✅ تایید و ساخت دعوت", "op:geninvite", "op:add"),
        )
        return

    # ---- ساخت دعوت (ویزارد «ادمین جدید») ----
    if data.startswith("op:ptoggle:") or data.startswith("op:pall:") or data.startswith("op:pnone:"):
        parts = data.split(":")
        action, token = parts[1], parts[2]
        if token == "new":
            wiz = _wizard.get(user.id)
            if not wiz or wiz.get("stage") != "permissions":
                await query.edit_message_text(_home_text(), reply_markup=_home_markup())
                return
            selected = wiz["permissions"]
        else:
            target_id = int(token)
            rec = _dynamic_admins.get(target_id)
            if not rec:
                await query.edit_message_text(_admins_text(), reply_markup=_admins_markup())
                return
            selected = set(rec["permissions"])

        if action == "ptoggle":
            key = parts[3]
            if key in selected:
                selected.discard(key)
            else:
                selected.add(key)
        elif action == "pall":
            selected.clear()
            selected.update(ALL_PERMISSIONS)
        elif action == "pnone":
            selected.clear()

        if token == "new":
            wiz["permissions"] = selected
            await query.edit_message_text(
                f"دسترسی‌های ادمین جدید (@{wiz.get('username', '?')}) رو انتخاب کن:",
                reply_markup=_permission_markup(selected, "new", "✅ تایید و ساخت دعوت", "op:geninvite", "op:add"),
            )
        else:
            _dynamic_admins[target_id]["permissions"] = list(selected)
            await save_state()
            rec = _dynamic_admins[target_id]
            label = f"@{rec['username']}"
            await query.edit_message_text(
                f"دسترسی‌های {label} رو تنظیم کن (هر تغییر بلافاصله ذخیره می‌شه):",
                reply_markup=_permission_markup(selected, str(target_id), "✅ پایان ویرایش", "op:list", "op:list"),
            )
        return

    if data == "op:geninvite":
        wiz = _wizard.get(user.id)
        if not wiz or wiz.get("stage") != "permissions":
            await query.edit_message_text(_home_text(), reply_markup=_home_markup())
            return
        if not wiz["permissions"]:
            await query.answer("حداقل یه دسترسی رو انتخاب کن.", show_alert=True)
            return

        uname, password = wiz["username"], wiz["password"]
        _pending_invites[uname] = {
            "password_hash": _hash(password),
            "permissions": list(wiz["permissions"]),
            "created_by": user.id,
            "created_at": time.time(),
            "expires": time.time() + _INVITE_TTL_SECONDS,
            "attempts": 0,
        }
        _audit(user.id, "INVITE_CREATED", note=f"@{uname}")
        await save_state()
        _wizard.pop(user.id, None)
        _owner_text_wait.pop(user.id, None)
        hours = _INVITE_TTL_SECONDS // 3600
        perm_txt = _perm_txt(_pending_invites[uname]["permissions"])
        await query.edit_message_text(
            "✅ دعوت ساخته شد. این پیام رو برای طرف بفرست:\n\n"
            "🔐 ادمین ربات شدی!\n"
            f"۱) برو پیوی ربات: @{context.bot.username}\n"
            "۲) بزن: /adminlogin\n"
            f"۳) یوزرنیم: `{uname}`\n"
            f"۴) رمز: `{password}`\n\n"
            "—（اطلاعات پنل، فقط برای خودت）—\n"
            f"⏳ اعتبار دعوت: {hours} ساعت (حداکثر {_MAX_CLAIM_ATTEMPTS} تلاش اشتباه)\n"
            f"🔓 دسترسی‌ها: {perm_txt}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 منوی اصلی", callback_data="op:home")]]),
        )
        return

    # ---- ادمین موجود: عزل / ویرایش دسترسی ----
    if data.startswith("op:perm:"):
        target_id = int(data.split(":", 2)[2])
        rec = _dynamic_admins.get(target_id)
        if not rec:
            await query.edit_message_text(_admins_text(), reply_markup=_admins_markup())
            return
        await query.edit_message_text(
            f"دسترسی‌های @{rec['username']} رو تنظیم کن (هر تغییر بلافاصله ذخیره می‌شه):",
            reply_markup=_permission_markup(set(rec["permissions"]), str(target_id), "✅ پایان ویرایش", "op:list", "op:list"),
        )
        return

    if data.startswith("op:revoke:"):
        target_id = int(data.split(":", 2)[2])
        rec = _dynamic_admins.get(target_id)
        if not rec:
            await query.edit_message_text(_admins_text(), reply_markup=_admins_markup())
            return
        await query.edit_message_text(
            f"⚠️ مطمئنی @{rec['username']} عزل بشه؟ دسترسی‌هاش فوراً قطع می‌شه.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ بله، عزل کن", callback_data=f"op:revokeyes:{target_id}"),
                InlineKeyboardButton("❌ نه", callback_data="op:list"),
            ]]),
        )
        return

    if data.startswith("op:revokeyes:"):
        target_id = int(data.split(":", 2)[2])
        rec = _dynamic_admins.pop(target_id, None)
        if rec:
            _audit(user.id, "REVOKED", note=f"@{rec['username']}")
            await save_state()
            if target_id != user.id:
                await _notify_owner(context, target_id, "⛔ دسترسی ادمینی‌ات توسط مالک ربات لغو شد.")
        await query.edit_message_text(_admins_text(), reply_markup=_admins_markup())
        return

    await query.answer()


# ---------------------------------------------------------------------------
# ⌨️ ورودی‌های متنیِ پیوی — هم ویزارد مالک، هم ورودِ قدم‌به‌قدمِ طرف (/adminlogin)
# ---------------------------------------------------------------------------

async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = update.effective_user
    if not message or not user or message.chat.type != "private":
        return

    # اول: آیا مالک وسط ساختن ادمین جدیده؟
    wait = _owner_text_wait.get(user.id)
    if wait and is_owner(user.id):
        if wait["type"] == "new_admin_username":
            text = (message.text or "").strip()
            if not text:
                return
            uname = _norm(text.split()[0])
            if not _valid_login_token(uname, _MIN_USERNAME_LEN) or not all(c.isalnum() or c == "_" for c in uname):
                await message.reply_text(
                    f"❌ یوزرنیم نامعتبره. دوباره بفرست (فقط حروف/عدد/زیرخط، حداقل {_MIN_USERNAME_LEN} کاراکتر، بدون فاصله)."
                )
                return
            if any(rec.get("username") == uname for rec in _dynamic_admins.values()):
                await message.reply_text("⚠️ این یوزرنیم همین الان توی لیست ادمین‌هاست. برای تغییر دسترسی، از «👑 لیست ادمین‌ها» استفاده کن.")
                return
            if uname in _pending_invites:
                await message.reply_text("⚠️ برای این یوزرنیم یه دعوتِ در انتظارِ تایید از قبل وجود داره («🧾 دعوت‌های در انتظار»).")
                return

            _wizard[user.id] = {"stage": "password", "username": uname}
            _owner_text_wait[user.id] = {"type": "new_admin_password"}
            await message.reply_text(
                f"✅ یوزرنیم: {uname}\n\n"
                f"حالا یه رمز دلخواه (حداقل {_MIN_PASSWORD_LEN} کاراکتر، بدون فاصله) بفرست، "
                "یا بزن 🎲 برای ساختنِ خودکار یه رمز امن:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎲 رمز تصادفی بساز", callback_data="op:genpass")],
                    [InlineKeyboardButton("❌ لغو", callback_data="op:cancel")],
                ]),
            )
            raise ApplicationHandlerStop

        if wait["type"] == "new_admin_password":
            text = (message.text or "").strip()
            if not text:
                return
            password = text.split()[0]
            if not _valid_login_token(password, _MIN_PASSWORD_LEN):
                await message.reply_text(f"❌ رمز خیلی کوتاهه. حداقل {_MIN_PASSWORD_LEN} کاراکتر، بدون فاصله بفرست.")
                return
            wiz = _wizard.get(user.id)
            if not wiz:
                return
            wiz["password"] = password
            wiz["stage"] = "permissions"
            wiz["permissions"] = set()
            _owner_text_wait.pop(user.id, None)
            await message.reply_text(
                f"دسترسی‌های ادمین جدید (@{wiz['username']}) رو انتخاب کن:",
                reply_markup=_permission_markup(set(), "new", "✅ تایید و ساخت دعوت", "op:geninvite", "op:add"),
            )
            raise ApplicationHandlerStop
        return

    # دوم: آیا این کاربر وسط /adminlogin هست؟ (هرکسی می‌تونه باشه، نه فقط مالک)
    if user.id in _login_wait:
        consumed = await _handle_login_text(update, context)
        if consumed:
            raise ApplicationHandlerStop


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "admins": {str(uid): rec for uid, rec in _dynamic_admins.items()},
        "pending": _pending_invites,
        "log": _audit_log,
    }


async def save_state():
    import asyncio
    import json
    import os
    import shutil
    path = getattr(config, "OWNER_PANEL_STATE_FILE", "owner_panel_state.json")

    def _write():
        tmp = path + ".tmp"
        if os.path.exists(path):
            try:
                shutil.copy2(path, path + ".bak")
            except Exception:
                pass
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_collect_state(), f, ensure_ascii=False)
        os.replace(tmp, path)

    try:
        await asyncio.to_thread(_write)
    except Exception:
        pass


def load_state():
    import json
    import os
    path = getattr(config, "OWNER_PANEL_STATE_FILE", "owner_panel_state.json")
    data = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = None
    if data is None:
        bak = path + ".bak"
        if os.path.exists(bak):
            try:
                with open(bak, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                return
        else:
            return
    for uid, rec in data.get("admins", {}).items():
        try:
            _dynamic_admins[int(uid)] = rec
        except Exception:
            continue
    _pending_invites.update(data.get("pending", {}))
    _audit_log.extend(data.get("log", []))
