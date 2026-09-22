# -*- coding: utf-8 -*-
"""
DIGIANTI ANTI-ABUSE — فاز ۹ (Module 27)
=============================================
توجه: بیشترِ Anti-Abuse این پروژه از فاز ۱ به بعد همین الان هم وجود داره —
هر Module (Jobs/Trading/Theft/Games/Pet/Underground/...) خودش Cooldown و
Daily Limit مخصوص خودشو پیاده کرده. این فایل فقط یه لایه‌ی *سراسری* اضافه
می‌کنه: صرف‌نظر از این‌که کدوم Command صدا زده می‌شه، بین هر دو Action
اقتصادیِ پشت‌سرهم از یه کاربر، یه حداقل فاصله‌ی زمانی (چند ثانیه) اجباره —
جلوی حمله‌ی «تعویض سریع بین چند Command مختلف برای دور زدن Cooldown تکی» رو
می‌گیره. State بدون نیاز به Persist بین Restart، چون بازه‌ش خیلی کوتاهه
(چند ثانیه) و از دست رفتنش بعد از Restart هیچ سوءاستفاده‌ای ایجاد نمی‌کنه.
"""

from __future__ import annotations

import time
from collections import defaultdict

import config

_last_action_ts = defaultdict(dict)  # chat_id -> uid -> ts


def check_and_mark(chat_id: int, uid: int) -> float:
    """اگه فاصله‌ی کافی از آخرین Action این کاربر گذشته، ۰.۰ برمی‌گردونه و
    زمان رو آپدیت می‌کنه. وگرنه، تعداد ثانیه‌ی باقی‌مونده رو برمی‌گردونه
    (>۰) و چیزی رو آپدیت نمی‌کنه — Caller باید خودش تصمیم بگیره که Action
    رو رد کنه یا نه."""
    now = time.time()
    last = _last_action_ts[chat_id].get(uid, 0)
    min_gap = getattr(config, "GLOBAL_ACTION_MIN_GAP_SECONDS", 1.5)
    remaining = min_gap - (now - last)
    if remaining > 0:
        return remaining
    _last_action_ts[chat_id][uid] = now
    return 0.0
