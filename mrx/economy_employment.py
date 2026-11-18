# -*- coding: utf-8 -*-
"""
DIGNANTI ECONOMY EMPLOYMENT — فاز ۱۵ (V18: تکمیلِ بخشِ ۹)
=====================================================================
بخشِ۹ صریحاً خواسته بود: «شرکت‌ها بتوانند کاربرانی را استخدام کنند. کاربر
بتواند درخواست استخدام بدهد. مدیر شرکت بتواند حقوق و شرایط کار را تعیین
کند.» تا این فاز، «کارمند»ِ هر کسب‌وکار فقط یه عدد بود (نه یه کاربرِ
واقعی). این ماژول دقیقاً همون سه‌تا رو اضافه می‌کنه:

  • کاربر با «درخواست استخدام [شناسه کسب‌وکار]» به یه کسب‌وکار درخواست می‌ده.
  • مالک با «پذیرش استخدام [شناسه درخواست] [حقوقِ ساعتی]» قبول می‌کنه و
    حقوق رو خودش تعیین می‌کنه (دقیقاً طبقِ خواسته‌ی متن).
  • از اون به بعد، economy_business._collect_one خودکار همون حقوق رو از
    هزینه‌ی کارگریِ از-قبل-محاسبه‌شده (نه هزینه‌ی اضافه) به کاربرِ واقعی
    پرداخت می‌کنه.

پذیرشِ دوطرفه (درخواست از طرفِ کارجو + پذیرشِ صریحِ مالک) دقیقاً طبقِ همون
اصلِ ضدِسوءاستفاده‌ای‌ـه که economy_partnership (فازِ۹) و economy_trade
(فازِ۸) هم رعایت می‌کنن.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

import config

logger = logging.getLogger("economy_employment")

STATE_FILE = getattr(config, "ECONOMY_EMPLOYMENT_STATE_FILE", "economy_employment_state.json")
STATE_VERSION = 1

_save_lock = asyncio.Lock()


class EmploymentError(Exception):
    pass


# chat_id -> app_id(int) -> {id, owner_uid, business_id, applicant_uid,
#   status: pending|accepted|rejected|cancelled, created_ts}
_applications: dict[int, dict] = defaultdict(dict)
_next_app_id: dict[int, int] = defaultdict(int)


def _owner_business(chat_id: int, owner_uid: int, business_id: int):
    import economy_business
    return next((b for b in economy_business.owned(chat_id, owner_uid) if b["id"] == business_id), None)


def _find_business_anywhere(chat_id: int, business_id: int, owner_uid_hint: int | None = None):
    """برایِ پیداکردنِ کسب‌وکار وقتی فقط آیدیِ خودش رو داریم (مثلاً موقعِ
    پذیرش) - چون applicant آیدیِ مالک رو مستقیم نداره، از خودِ Applicationش
    (owner_uid ذخیره‌شده) پیدا می‌کنیم؛ این تابع فقط برایِ موردی‌که owner_uid
    معلومه کمک می‌کنه."""
    if owner_uid_hint is not None:
        return _owner_business(chat_id, owner_uid_hint, business_id)
    return None


def _pending_count_for_applicant(chat_id: int, applicant_uid: int) -> int:
    return sum(
        1 for a in _applications[chat_id].values()
        if a["applicant_uid"] == applicant_uid and a["status"] == "pending"
    )


def apply(chat_id: int, applicant_uid: int, owner_uid: int, business_id: int) -> dict:
    if applicant_uid == owner_uid:
        raise EmploymentError("نمی‌تونی برایِ کسب‌وکارِ خودت درخواست بدی.")
    b = _owner_business(chat_id, owner_uid, business_id)
    if not b:
        raise EmploymentError("این کسب‌وکار پیدا نشد.")
    if b.get("bankrupt"):
        raise EmploymentError("این کسب‌وکار ورشکسته‌ست و الان استخدام نمی‌کنه.")

    import economy_business
    info = economy_business.TYPES[b["type"]]
    if b["employees"] >= info["max_employees"]:
        raise EmploymentError("این کسب‌وکار ظرفیتِ کارمندِ خالی نداره.")
    if str(applicant_uid) in b.get("employed_users", {}):
        raise EmploymentError("از قبل توی همین کسب‌وکار کار می‌کنی.")

    max_pending = getattr(config, "EMPLOYMENT_MAX_PENDING_PER_USER", 5)
    if _pending_count_for_applicant(chat_id, applicant_uid) >= max_pending:
        raise EmploymentError(f"حداکثر {max_pending} درخواستِ معلقِ هم‌زمان مجازه.")

    app_id = _next_app_id[chat_id] + 1
    _next_app_id[chat_id] = app_id
    application = {
        "id": app_id, "owner_uid": owner_uid, "business_id": business_id,
        "applicant_uid": applicant_uid, "status": "pending", "created_ts": time.time(),
    }
    _applications[chat_id][app_id] = application
    return application


def accept(chat_id: int, owner_uid: int, app_id: int, salary_per_hour: float) -> dict:
    a = _applications[chat_id].get(app_id)
    if not a or a["status"] != "pending":
        raise EmploymentError("این درخواست دیگه معتبر نیست.")
    if a["owner_uid"] != owner_uid:
        raise EmploymentError("این درخواست برایِ تو نیست.")
    if salary_per_hour < 0:
        raise EmploymentError("حقوق نمی‌تونه منفی باشه.")

    b = _owner_business(chat_id, owner_uid, a["business_id"])
    if not b:
        a["status"] = "cancelled"
        raise EmploymentError("کسب‌وکار دیگه وجود نداره.")

    import economy_business
    info = economy_business.TYPES[b["type"]]
    if b["employees"] >= info["max_employees"]:
        raise EmploymentError("ظرفیتِ کارمند پر شده.")

    # حقوق نمی‌تونه از هزینه‌یِ از-قبل-حساب‌شده‌یِ هر Slot بیشتر باشه - این
    # دقیقاً همون چیزیه که economy_business از hourly_expense کم می‌کنه؛
    # استخدامِ واقعی فقط مقصدِ همون هزینه رو مشخص می‌کنه، هزینه‌ی تازه نمی‌سازه.
    max_wage = info["employee_cost_per_hour"]
    salary_per_hour = min(salary_per_hour, max_wage)

    employed = b.setdefault("employed_users", {})
    employed[str(a["applicant_uid"])] = {"salary_per_hour": salary_per_hour, "hired_ts": time.time()}
    b["employees"] = b.get("employees", 0) + 1
    a["status"] = "accepted"
    return a


def reject(chat_id: int, owner_uid: int, app_id: int) -> dict:
    a = _applications[chat_id].get(app_id)
    if not a or a["status"] != "pending":
        raise EmploymentError("این درخواست دیگه معتبر نیست.")
    if a["owner_uid"] != owner_uid:
        raise EmploymentError("این درخواست برایِ تو نیست.")
    a["status"] = "rejected"
    return a


def fire_employee(chat_id: int, owner_uid: int, business_id: int, employee_uid: int) -> dict:
    b = _owner_business(chat_id, owner_uid, business_id)
    if not b:
        raise EmploymentError("این کسب‌وکار پیدا نشد.")
    employed = b.get("employed_users", {})
    key = str(employee_uid)
    if key not in employed:
        raise EmploymentError("این کاربر کارمندِ این کسب‌وکار نیست.")
    del employed[key]
    b["employees"] = max(0, b.get("employees", 1) - 1)
    return b


def pending_for_owner(chat_id: int, owner_uid: int) -> list[dict]:
    return [a for a in _applications[chat_id].values() if a["owner_uid"] == owner_uid and a["status"] == "pending"]


def my_applications(chat_id: int, uid: int) -> list[dict]:
    return [a for a in _applications[chat_id].values() if a["owner_uid"] == uid or a["applicant_uid"] == uid]


def employees_of(chat_id: int, owner_uid: int, business_id: int) -> dict:
    b = _owner_business(chat_id, owner_uid, business_id)
    if not b:
        return {}
    return b.get("employed_users", {})


# ---------------------------------------------------------------------------
# 🖊 نمایش متنی
# ---------------------------------------------------------------------------

def format_pending_text(chat_id: int, uid: int) -> str:
    rows = pending_for_owner(chat_id, uid)
    lines = ["👔 درخواست‌هایِ استخدامِ در انتظار", "─────────────────"]
    if not rows:
        lines.append("هیچ درخواستِ استخدامی برایِ کسب‌وکارهات نیست.")
        return "\n".join(lines)
    for a in rows:
        lines.append(f"#{a['id']} کاربر {a['applicant_uid']} برایِ کسب‌وکارِ #{a['business_id']}")
    lines.append("")
    lines.append("پذیرش: «پذیرش استخدام [شناسه] [حقوقِ ساعتی]» — رد: «رد استخدام [شناسه]»")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 📨 Command Handlers
# ---------------------------------------------------------------------------

def _host():
    import bot as host
    return host


def _group_only_or_none(update: Update):
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return None
    return chat


async def apply_job_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """درخواست استخدام [شناسه کسب‌وکار] (با Reply رویِ پیامِ مالک) / /applyjob <business_id>"""
    host = _host()
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not message.reply_to_message:
        await message.reply_text(
            "برایِ درخواستِ استخدام، رویِ پیامِ مالکِ کسب‌وکار Reply کن و بنویس: "
            "«درخواست استخدام [شناسه کسب‌وکار]»"
        )
        return
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: (با Reply رویِ مالک) «درخواست استخدام [شناسه کسب‌وکار]»")
        return
    try:
        business_id = int(args[0])
    except ValueError:
        await message.reply_text("شناسه‌ی کسب‌وکار نامعتبره.")
        return
    applicant_uid = update.effective_user.id
    owner_uid = message.reply_to_message.from_user.id
    try:
        a = apply(chat.id, applicant_uid, owner_uid, business_id)
    except EmploymentError as e:
        await message.reply_text(f"❌ {e}")
        return
    await message.reply_text(f"👔 درخواستِ استخدامِ #{a['id']} ثبت شد. منتظرِ تأییدِ مالک باش.")
    await host.save_state()


async def accept_job_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پذیرش استخدام [شناسه] [حقوق ساعتی] / /acceptjob <app_id> <wage>"""
    host = _host()
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if len(args) < 2:
        await message.reply_text("استفاده: «پذیرش استخدام [شناسه] [حقوقِ ساعتی]»")
        return
    try:
        app_id = int(args[0])
        wage = float(args[1])
    except ValueError:
        await message.reply_text("شناسه/حقوق نامعتبره.")
        return
    owner_uid = update.effective_user.id
    try:
        a = accept(chat.id, owner_uid, app_id, wage)
    except EmploymentError as e:
        await message.reply_text(f"❌ {e}")
        return
    await message.reply_text(
        f"✅ کاربر {a['applicant_uid']} استخدام شد! حقوقِ ساعتی: {wage:,} {config.CURRENCY_NAME}"
    )
    await host.save_state()


async def reject_job_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رد استخدام [شناسه] / /rejectjob <app_id>"""
    host = _host()
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «رد استخدام [شناسه]»")
        return
    try:
        app_id = int(args[0])
    except ValueError:
        await message.reply_text("شناسه نامعتبره.")
        return
    owner_uid = update.effective_user.id
    try:
        reject(chat.id, owner_uid, app_id)
    except EmploymentError as e:
        await message.reply_text(f"❌ {e}")
        return
    await message.reply_text("✅ درخواست رد شد.")
    await host.save_state()


async def fire_real_employee_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اخراج کارمند [شناسه کسب‌وکار] (با Reply رویِ کارمند) / /firerealemployee <business_id>"""
    host = _host()
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not message.reply_to_message:
        await message.reply_text("رویِ پیامِ کارمند Reply کن و بنویس: «اخراج کارمند [شناسه کسب‌وکار]»")
        return
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: (با Reply رویِ کارمند) «اخراج کارمند [شناسه کسب‌وکار]»")
        return
    try:
        business_id = int(args[0])
    except ValueError:
        await message.reply_text("شناسه نامعتبره.")
        return
    owner_uid = update.effective_user.id
    employee_uid = message.reply_to_message.from_user.id
    try:
        fire_employee(chat.id, owner_uid, business_id, employee_uid)
    except EmploymentError as e:
        await message.reply_text(f"❌ {e}")
        return
    await message.reply_text("✅ کارمند اخراج شد.")
    await host.save_state()


async def my_job_applications_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """درخواست‌های استخدام من / /myjobapplications"""
    message = update.effective_message
    chat = _group_only_or_none(update)
    if not chat:
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    await message.reply_text(format_pending_text(chat.id, uid))


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "version": STATE_VERSION,
        "applications": {str(c): v for c, v in _applications.items()},
        "next_app_id": {str(c): v for c, v in _next_app_id.items()},
    }


def _write_state_file(data: dict):
    tmp = STATE_FILE + ".tmp"
    if os.path.exists(STATE_FILE):
        try:
            shutil.copy2(STATE_FILE, STATE_FILE + ".bak")
        except Exception as e:
            logger.warning(f"بکاپ گرفتن از state ناموفق بود: {e}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_FILE)


async def save_state():
    async with _save_lock:
        try:
            await asyncio.to_thread(_write_state_file, _collect_state())
        except Exception as e:
            logger.warning(f"ذخیره‌ی state استخدام ناموفق بود: {e}")


def load_state():
    data = None
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"خوندن state استخدام ناموفق بود: {e}؛ تلاش برای بازیابی از بکاپ...")
            data = None
    if data is None:
        bak = STATE_FILE + ".bak"
        if os.path.exists(bak):
            try:
                with open(bak, encoding="utf-8") as f:
                    data = json.load(f)
                logger.warning("state اصلیِ استخدام خراب/نامعتبر بود؛ از بکاپ بازیابی شد.")
            except Exception as e:
                logger.warning(f"خوندنِ بکاپِ استخدام هم ناموفق بود: {e}")
                return
        else:
            return

    for cid_s, apps in data.get("applications", {}).items():
        cid = int(cid_s)
        for aid_s, a in apps.items():
            a = dict(a)
            a["id"] = int(a["id"])
            a["owner_uid"] = int(a["owner_uid"])
            a["applicant_uid"] = int(a["applicant_uid"])
            a["business_id"] = int(a["business_id"])
            _applications[cid][int(aid_s)] = a

    for cid_s, v in data.get("next_app_id", {}).items():
        _next_app_id[int(cid_s)] = int(v)

    logger.info("وضعیتِ استخدام از فایل بارگذاری شد.")
