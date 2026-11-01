# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY ADMIN + MODULE TOGGLES + MENU — فاز ۱۰ (Module 30 + 31 + 32)
=================================================================================
با Permission فعلی پروژه (manage_economy) هماهنگه — هیچ Permission جدیدی
نساخته شده. برای Give/Remove Coins، Give/Remove Item، Set Balance، Force
Event و Transaction Log از Commandهای ادمینِ از قبل موجود استفاده کنید
(setcoins/addcoins/دادن‌آیتم/حذف‌آیتم/فورس‌رویداد/تراکنش‌ها) — این فایل فقط
دو تا قابلیت واقعاً تازه اضافه می‌کنه که قبلاً نبودن: Reset کامل اقتصاد یه
کاربر، و Inspect (بازرسی خلاصه‌ی وضعیت اقتصادی یه کاربر) — به‌علاوه‌ی مدیریت
Toggle مستقل هر ماژول و نمایش منوی اقتصاد.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

import config
import economy_core

TOGGLES: dict = getattr(config, "ECONOMY_MODULE_TOGGLES", {})

# chat_id -> list[{"ts","admin_id","action","target_id","note"}] — فاز ۲۶:
# «تمام اقدامات Admin نیز Audit Log شوند»
_admin_audit_log = defaultdict(list)


def _audit(chat_id: int, admin_id: int, action: str, target_id: int | None = None, note: str = "") -> None:
    log = _admin_audit_log[chat_id]
    log.append({"ts": time.time(), "admin_id": admin_id, "action": action, "target_id": target_id, "note": note})
    cap = getattr(config, "ADMIN_AUDIT_LOG_MAX", 200)
    if len(log) > cap:
        del log[: len(log) - cap]

# ---------------------------------------------------------------------------
# 📖 راهنمای اقتصاد — به‌جای دامپ‌کردن متن، مستقیم زیرمنوی «💰 اقتصاد» همون
# سیستم راهنمای بخش‌بندی‌شده‌ی bot.py رو باز می‌کنه (هر دکمه = بخش خودش،
# فارسی + انگلیسی با هم)، دقیقاً همون‌جوری که از /help → «💰 اقتصاد» می‌رسی.
# ---------------------------------------------------------------------------

async def economy_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """راهنمای اقتصاد / /economyhelp"""
    host = _host()
    message = update.effective_message
    user_id = update.effective_user.id
    await message.reply_text(
        host.HELP_HEADER + "\n        💰 راهنمای اقتصاد — یه بخش رو انتخاب کن",
        reply_markup=host._build_help_eco_markup(user_id),
    )


def _host():
    import bot as host
    return host


def _normalize_fa(s: str) -> str:
    """نیم‌فاصله‌ها (ZWNJ) و فاصله‌های اضافی رو یکدست می‌کنه تا تایپ عادی (بدون نیم‌فاصله،
    که روی اکثر گوشی‌ها پیش‌فرضه) هم درست با لیبل‌های فارسی مطابقت پیدا کنه."""
    s = (s or "").replace("\u200c", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _label_text(label: str) -> str:
    """ایموجی ابتدای لیبل رو برمی‌داره، فقط متن فارسی می‌مونه."""
    return re.sub(r"^[^\w\u0600-\u06FF]+", "", _normalize_fa(label)).strip()


def resolve_module_key(name: str) -> tuple[str | None, list[str]]:
    """اسمی که ادمین تایپ کرده رو به یه کلید ماژول دقیق تبدیل می‌کنه.
    خروجی: (کلید یا None، لیست کاندیدها اگه مبهم/نامشخص بود).
    برای جلوگیری از خاموش‌شدن اشتباه ماژول (مثلاً «بازار» که هم با بازار و هم
    با بازار سیاه هم‌پوشانی داره)، اول فقط دنبال تطبیق دقیق (کلید یا متن کامل
    لیبل) می‌گرده؛ تطبیق جزئی (substring) فقط وقتی قبول می‌شه که دقیقاً
    یک کاندید داشته باشه، وگرنه همه‌ی کاندیدها برگردونده می‌شن تا کاربر دقیق‌تر بنویسه."""
    norm_name = _normalize_fa(name)
    if not norm_name:
        return None, []
    if name in TOGGLES:
        return name, []
    for k, v in TOGGLES.items():
        if norm_name == _label_text(v) or norm_name == _normalize_fa(v):
            return k, []
    candidates = [k for k, v in TOGGLES.items() if norm_name in _normalize_fa(v)]
    if len(candidates) == 1:
        return candidates[0], []
    return None, candidates


def module_off_message(chat_id: int, key: str) -> str:
    """پیام دقیق می‌ده بر اساس دلیل واقعی خاموش‌بودن: یا کل اقتصاد گروه خاموشه،
    یا فقط همین یه بخش رو ادمین خاموش کرده (نه کل اقتصاد) - قبلاً هر دو حالت
    یه پیام یکسانِ گمراه‌کننده («اقتصاد گروه خاموشه») نشون می‌دادن."""
    host = _host()
    if not host.get_setting(chat_id, "economy_enabled"):
        return "🔒 اقتصاد این گروه خاموشه. یه ادمین باید از ‎/menu بخش «اقتصاد و نظرسنجی» روشنش کنه."
    label = TOGGLES.get(key, key)
    return f"🔒 بخش «{label}» رو ادمین این گروه خاموش کرده (بقیه‌ی بخش‌های اقتصاد مشکلی ندارن)."


async def set_module_enabled(chat_id: int, key: str, enabled: bool, actor_uid: int | None = None) -> None:
    """روشن/خاموش‌کردن واقعی یه ماژول - لاگ می‌گیره و ذخیره می‌کنه. منبع واحد برای
    هم دستور متنی («ماژول‌های اقتصاد...») و هم دکمه‌های پنل ویژه‌ی ادمین، که جلوی
    دوشاخه‌شدن منطق رو می‌گیره."""
    host = _host()
    host._chat_settings[chat_id][key] = enabled
    if actor_uid is not None:
        _audit(chat_id, actor_uid, "MODULE_TOGGLE", note=f"{key} -> {'روشن' if enabled else 'خاموش'}")
    await host.save_state()


def module_enabled(chat_id: int, key: str) -> bool:
    """چک ترکیبی: هم economy_enabled سراسری، هم Toggle اختصاصی این ماژول.
    ماژول‌هایی که Key ندارن (چون Toggle مستقل ندارن) همیشه True حساب می‌شن."""
    host = _host()
    if not host.get_setting(chat_id, "economy_enabled"):
        return False
    if key not in TOGGLES:
        return True
    return host.get_setting(chat_id, key)


# ---------------------------------------------------------------------------
# 📋 منوی اقتصاد (Module 32)
# ---------------------------------------------------------------------------

async def economy_menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی اقتصاد / /economymenu"""
    message = update.effective_message
    await message.reply_text(getattr(config, "ECONOMY_MENU_TEXT", "منوی اقتصاد تعریف نشده."))


# ---------------------------------------------------------------------------
# 🔌 مدیریت Toggle ماژول‌ها (Module 31)
# ---------------------------------------------------------------------------

async def economy_modules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ماژول‌های اقتصاد [نام] [روشن|خاموش] — بدون آرگومان: لیست وضعیت.
    /economymodules <key> <on|off>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not host.has_permission(update.effective_user.id, "manage_economy"):
        await message.reply_text("⛔ این دستور فقط برای ادمین‌های اقتصاده.")
        return

    args = context.args or []
    if not args:
        lines = ["🔌 وضعیت ماژول‌های اقتصاد:", ""]
        for key, label in TOGGLES.items():
            status = "روشن ✅" if host.get_setting(chat.id, key) else "خاموش ⛔"
            lines.append(f"{label}: {status}")
        lines.append("")
        lines.append("برای تغییر: «ماژول‌های اقتصاد [نام] [روشن|خاموش]»")
        await message.reply_text("\n".join(lines))
        return

    if len(args) < 2:
        await message.reply_text("استفاده: «ماژول‌های اقتصاد [نام] [روشن|خاموش]»")
        return

    state_word = args[-1]
    name = " ".join(args[:-1])
    if state_word not in ("روشن", "خاموش"):
        await message.reply_text("وضعیت باید «روشن» یا «خاموش» باشه (آخرین کلمه).")
        return
    key, candidates = resolve_module_key(name)
    if not key:
        if candidates:
            opts = "، ".join(TOGGLES[c] for c in candidates)
            await message.reply_text(
                f"⚠️ اسمی که نوشتی بین چند ماژول مشترکه، دقیق‌تر بنویس: {opts}"
            )
        else:
            names = "، ".join(TOGGLES.values())
            await message.reply_text(f"همچین ماژولی نیست. گزینه‌ها: {names}")
        return

    await set_module_enabled(chat.id, key, state_word == "روشن", actor_uid=update.effective_user.id)
    await message.reply_text(f"✅ {TOGGLES[key]} {'روشن' if state_word == 'روشن' else 'خاموش'} شد.")


# ---------------------------------------------------------------------------
# 🔎 بازرسی + ریست اقتصاد کاربر (Module 30)
# ---------------------------------------------------------------------------

async def inspect_economy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بازرسی اقتصاد (ریپلای روی کاربر هدف) / /inspecteconomy"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not host.has_permission(update.effective_user.id, "manage_economy"):
        await message.reply_text("⛔ این دستور فقط برای ادمین‌های اقتصاده.")
        return

    target_id = host._resolve_target_id(message)
    if not target_id:
        await message.reply_text("باید روی پیام کاربر هدف ریپلای کنی: «بازرسی اقتصاد» (ریپلای).")
        return

    lines = [f"🔎 بازرسی اقتصاد کاربر {target_id}", ""]
    lines.append(f"💰 کیف پول: {economy_core.get_wallet(chat.id, target_id):,} {config.CURRENCY_NAME}")
    lines.append(f"🏦 بانک: {economy_core.get_bank_balance(chat.id, target_id):,} {config.CURRENCY_NAME}")
    lines.append(f"💎 ارزش خالص: {economy_core.get_net_worth(chat.id, target_id):,} {config.CURRENCY_NAME}")

    try:
        import economy_jobs
        key = economy_jobs.current_job_key(chat.id, target_id)
        lines.append(f"💼 شغل: {economy_jobs.JOBS[key]['name'] if key else 'ندارد'}")
    except Exception:
        pass
    try:
        import economy_theft
        s = economy_theft._thief_stats[chat.id].get(target_id)
        if s:
            lines.append(f"🦹 Thief Level: {s['level']} — موفقیت: {s['successes']}/{s['successes']+s['fails']}")
    except Exception:
        pass
    try:
        import economy_pet
        pet = economy_pet.get_pet(chat.id, target_id)
        if pet:
            lines.append(f"🐾 پت: {economy_pet._display_name(pet)} — Level {pet['level']}")
    except Exception:
        pass
    try:
        import economy_city
        if economy_city.has_city(chat.id, target_id):
            city = economy_city._city(chat.id, target_id)
            lines.append(f"🏙️ شهر: Level {city['level']}")
    except Exception:
        pass
    try:
        import economy_property
        n = len(economy_property.owned(chat.id, target_id))
        if n:
            lines.append(f"🏠 تعداد املاک: {n}")
    except Exception:
        pass
    try:
        import economy_vehicle
        vs = economy_vehicle.owned(chat.id, target_id)
        if vs:
            lines.append(f"🚗 تعداد خودرو: {len(vs)} — ارزش کل: {economy_vehicle.get_vehicle_value(chat.id, target_id):,}")
    except Exception:
        pass
    try:
        import economy_business
        bs = economy_business.owned(chat.id, target_id)
        if bs:
            lines.append(f"🏢 تعداد کسب‌وکار: {len(bs)} — ارزش کل: {economy_business.get_business_value(chat.id, target_id):,}")
    except Exception:
        pass
    try:
        import economy_bank
        score = economy_bank.get_credit_score(chat.id, target_id)
        active_loans = economy_bank._active_loans(chat.id, target_id)
        lines.append(f"💳 اعتبار: {score}/850" + (f" — {len(active_loans)} وام فعال" if active_loans else ""))
    except Exception:
        pass
    try:
        import economy_marriage
        if economy_marriage.is_married(chat.id, target_id):
            lines.append(f"💍 همسر: {economy_marriage.spouse_of(chat.id, target_id)}")
    except Exception:
        pass

    await message.reply_text("\n".join(lines))


async def reset_user_economy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ریست اقتصاد کاربر (ریپلای روی کاربر هدف) / /reseteconomy"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not host.has_permission(update.effective_user.id, "manage_economy"):
        await message.reply_text("⛔ این دستور فقط برای ادمین‌های اقتصاده.")
        return

    target_id = host._resolve_target_id(message)
    if not target_id:
        await message.reply_text("باید روی پیام کاربر هدف ریپلای کنی: «ریست اقتصاد کاربر» (ریپلای).")
        return

    args = context.args or []
    if not args or args[0] != "تایید":
        await message.reply_text(
            "⚠️ این کار کیف‌پول، بانک، شغل، پت، ملک، شهر و همه‌ی پیشرفت اقتصادیِ این کاربر رو پاک می‌کنه.\n"
            "برای تأیید: «ریست اقتصاد کاربر تایید» (ریپلای روی همون کاربر)."
        )
        return

    host._wallet[chat.id].pop(target_id, None)
    economy_core._bank[chat.id].pop(target_id, None)

    for modname, attr in [
        ("economy_jobs", "_current_job"), ("economy_jobs", "_job_progress"),
        ("economy_bank", "_bank_level"), ("economy_bank", "_long_deposits"),
        ("economy_bank", "_credit_score"), ("economy_bank", "_loans"),
        ("economy_market", "_portfolio"), ("economy_market", "_trading"),
        ("economy_theft", "_thief_stats"), ("economy_theft", "_jail_until"), ("economy_theft", "_wanted"),
        ("economy_city", "_cities"),
        ("economy_property", "_properties"),
        ("economy_vehicle", "_vehicles"),
        ("economy_business", "_businesses"),
        ("economy_district", "_residency"),
        ("economy_pet", "_pets"),
        ("economy_marriage", "_spouse"),
        ("economy_inventory", "_items"),
        ("economy_security", "_insurance"),
        ("economy_underground", "_stats"),
        ("economy_games", "_stats"),
    ]:
        try:
            mod = __import__(modname)
            container = getattr(mod, attr)
            if chat.id in container and target_id in container[chat.id]:
                del container[chat.id][target_id]
        except Exception:
            pass

    await message.reply_text(f"✅ اقتصاد کاربر {target_id} کاملاً ریست شد.")
    _audit(chat.id, update.effective_user.id, "RESET_USER_ECONOMY", target_id=target_id)
    await host.save_state()


# ---------------------------------------------------------------------------
# 🧊 Economy Freeze — فاز ۲۶
# ---------------------------------------------------------------------------

async def freeze_economy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فریز اقتصاد / /freezeeconomy — عملیات مالی معمولی (خرید/فروش/انتقال/...)
    رو مسدود می‌کنه؛ ابزارهای ادمینی (تنظیم موجودی، برگشت تراکنش) همچنان کار می‌کنن."""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not host.has_permission(update.effective_user.id, "manage_economy"):
        await message.reply_text("⛔ این دستور فقط برای ادمین‌های اقتصاده.")
        return
    economy_core.set_frozen(chat.id, True)
    _audit(chat.id, update.effective_user.id, "FREEZE_ECONOMY")
    await message.reply_text("🧊 اقتصاد این گروه فریز شد. برای رفع: «رفع فریز اقتصاد»")
    await host.save_state()


async def unfreeze_economy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رفع فریز اقتصاد / /unfreezeeconomy"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not host.has_permission(update.effective_user.id, "manage_economy"):
        await message.reply_text("⛔ این دستور فقط برای ادمین‌های اقتصاده.")
        return
    economy_core.set_frozen(chat.id, False)
    _audit(chat.id, update.effective_user.id, "UNFREEZE_ECONOMY")
    await message.reply_text("✅ فریز اقتصاد این گروه برداشته شد.")
    await host.save_state()


# ---------------------------------------------------------------------------
# 🧾 Transaction Lookup / Rollback — فاز ۲۶
# ---------------------------------------------------------------------------

async def lookup_transaction_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بررسی تراکنش [شناسه] / /txlookup <id>"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not host.has_permission(update.effective_user.id, "manage_economy"):
        await message.reply_text("⛔ این دستور فقط برای ادمین‌های اقتصاده.")
        return
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «بررسی تراکنش [شناسه]»")
        return
    txn = economy_core.get_transaction(args[0])
    if not txn or txn["chat_id"] != chat.id:
        await message.reply_text("همچین تراکنشی توی این گروه پیدا نشد.")
        return
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(txn["timestamp"]))
    lines = [
        f"🧾 تراکنش {txn['transaction_id']}",
        f"نوع: {txn['type']} — وضعیت: {txn['status']}",
        f"کاربر: {txn['user_id']}" + (f" ↔ {txn['target_user_id']}" if txn["target_user_id"] else ""),
        f"مبلغ: {txn['amount']:,} {txn['currency']}",
        f"منبع: {txn['source'] or '—'}",
        f"زمان: {ts}",
    ]
    await message.reply_text("\n".join(lines))


async def rollback_transaction_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """برگشت تراکنش [شناسه] / /txrollback <id>"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not host.has_permission(update.effective_user.id, "manage_economy"):
        await message.reply_text("⛔ این دستور فقط برای ادمین‌های اقتصاده.")
        return
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «برگشت تراکنش [شناسه]»")
        return
    txn = economy_core.get_transaction(args[0])
    if not txn or txn["chat_id"] != chat.id:
        await message.reply_text("همچین تراکنشی توی این گروه پیدا نشد.")
        return
    try:
        await economy_core.rollback_transaction(args[0], reason=f"admin:{update.effective_user.id}")
    except economy_core.TransactionNotFoundError:
        await message.reply_text("تراکنش پیدا نشد.")
        return
    except economy_core.RollbackError as e:
        await message.reply_text(f"❌ {e}")
        return
    _audit(chat.id, update.effective_user.id, "ROLLBACK_TRANSACTION", target_id=txn["user_id"], note=args[0])
    await message.reply_text(f"✅ تراکنش {args[0]} برگشت داده شد.")
    await host.save_state()


async def admin_log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گزارش ادمین / /adminlog — آخرین اقدامات ادمینی روی اقتصاد این گروه."""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not host.has_permission(update.effective_user.id, "manage_economy"):
        await message.reply_text("⛔ این دستور فقط برای ادمین‌های اقتصاده.")
        return
    log = _admin_audit_log.get(chat.id, [])
    if not log:
        await message.reply_text("هنوز هیچ اقدام ادمینی ثبت نشده.")
        return
    lines = ["📋 آخرین اقدامات ادمین:", ""]
    for entry in reversed(log[-15:]):
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry["ts"]))
        target_txt = f" (هدف: {entry['target_id']})" if entry["target_id"] else ""
        note_txt = f" — {entry['note']}" if entry["note"] else ""
        lines.append(f"• {ts} — {entry['action']} توسط {entry['admin_id']}{target_txt}{note_txt}")
    await message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {str(c): log for c, log in _admin_audit_log.items()}


async def save_state():
    import json
    import os
    import shutil
    path = getattr(config, "ECONOMY_ADMIN_STATE_FILE", "economy_admin_state.json")
    try:
        import asyncio
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
        await asyncio.to_thread(_write)
    except Exception:
        pass


def load_state():
    import json
    import os
    path = getattr(config, "ECONOMY_ADMIN_STATE_FILE", "economy_admin_state.json")
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
    for cid, log in data.items():
        _admin_audit_log[int(cid)] = log
