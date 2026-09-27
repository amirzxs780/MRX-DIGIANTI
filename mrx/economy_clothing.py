# -*- coding: utf-8 -*-
"""
DIGNANTI ECONOMY CLOTHING — فاز ۱۰ (ماژول جدید؛ از صفر ساخته شده)
=====================================================================
سیستم ظاهر بازیکن: خرید لباس/اکسسوری، پوشیدن/درآوردن (هر Slot فقط یه آیتم)،
و نمایش روی پروفایل. Rarity بالاتر یه Bonus کوچیک به Fame (زنده، نه ذخیره‌شده
— مثل district) اضافه می‌کنه، تا لباس گرون فقط تزئینی نباشه.

Persistence جدا از economy_inventory.py نگه داشته شده چون این یه سیستم Slot-
Based (هر Slot یدونه) است، نه Stackable (چندتایی) — منطق متفاوتی داره.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

import config
import economy_core

logger = logging.getLogger("economy_clothing")

STATE_FILE = getattr(config, "ECONOMY_CLOTHING_STATE_FILE", "economy_clothing_state.json")
CATALOG: dict = getattr(config, "CLOTHING_CATALOG", {})
SLOTS: list = getattr(config, "CLOTHING_SLOTS", [])
RARITY_FAME_BONUS: dict = getattr(config, "CLOTHING_RARITY_FAME_BONUS", {})

_save_lock = asyncio.Lock()

# chat_id -> uid -> set(item_key) — همه‌ی چیزهایی که خریده (Owned، نه لزوماً پوشیده)
_owned = defaultdict(lambda: defaultdict(set))
# chat_id -> uid -> {slot: item_key} — چیزی که الان پوشیده
_equipped = defaultdict(lambda: defaultdict(dict))


def _host():
    import bot as host
    return host


def owned_items(chat_id: int, uid: int) -> set:
    return _owned[chat_id][uid]


def equipped(chat_id: int, uid: int) -> dict:
    return _equipped[chat_id][uid]


def fame_bonus(chat_id: int, uid: int) -> int:
    """مجموع Bonus زنده‌ی Fame از همه‌ی چیزهایی که الان پوشیده (مثل District،
    یه مقدار محاسبه‌شده، نه یه استت ذخیره‌شده — تا با پوشیدن/درآوردن مکرر
    خراب نشه)."""
    total = 0
    for item_key in equipped(chat_id, uid).values():
        info = CATALOG.get(item_key)
        if info:
            total += RARITY_FAME_BONUS.get(info["rarity"], 0)
    return total


async def clothing_shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فروشگاه لباس / /clothingshop — لیست کامل بر اساس Slot."""
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    import economy_admin
    if not economy_admin.module_enabled(chat.id, "economy_module_clothing"):
        await message.reply_text("اقتصاد گروه خاموشه.")
        return

    lines = ["👔 فروشگاه لباس:", ""]
    for slot in SLOTS:
        items = [(k, v) for k, v in CATALOG.items() if v["slot"] == slot]
        if not items:
            continue
        lines.append(f"— {slot} —")
        for key, info in items:
            lines.append(f"  {info['name']} ({info['rarity']}) — {info['price']:,} {config.CURRENCY_NAME} — کلید: {key}")
    lines.append("")
    lines.append("خرید: «خرید لباس [کلید]» — پوشیدن: «پوشیدن [کلید]» — درآوردن: «درآوردن [Slot]»")
    await message.reply_text("\n".join(lines))


def _find_item(name: str) -> str | None:
    name = name.strip().lower()
    if name in CATALOG:
        return name
    for key, info in CATALOG.items():
        if name in info["name"].lower():
            return key
    return None


async def buy_clothing_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """خرید لباس [کلید یا نام] / /buyclothing <key>"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «خرید لباس [کلید]». با «فروشگاه لباس» لیست رو ببین.")
        return
    uid = update.effective_user.id
    key = _find_item(" ".join(args))
    if not key:
        await message.reply_text("همچین آیتمی نیست. با «فروشگاه لباس» لیست رو ببین.")
        return
    if key in owned_items(chat.id, uid):
        await message.reply_text("این رو قبلاً خریدی.")
        return

    info = CATALOG[key]
    try:
        await economy_core.remove_coins(chat.id, uid, info["price"], kind="OTHER", note=f"خرید {info['name']}")
    except economy_core.InsufficientFundsError:
        await message.reply_text(f"❌ موجودی کافی نیست. قیمت: {info['price']:,} {config.CURRENCY_NAME}")
        return

    owned_items(chat.id, uid).add(key)
    await message.reply_text(f"✅ {info['name']} خریداری شد! با «پوشیدن {key}» بپوشش.")
    await host.save_state()


async def wear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پوشیدن [کلید] / /wear <key>"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «پوشیدن [کلید]»")
        return
    uid = update.effective_user.id
    key = _find_item(" ".join(args))
    if not key or key not in owned_items(chat.id, uid):
        await message.reply_text("این آیتم رو نداری. اول باید بخریش.")
        return

    info = CATALOG[key]
    equipped(chat.id, uid)[info["slot"]] = key
    await message.reply_text(f"✅ {info['name']} رو پوشیدی.")
    await host.save_state()


async def unwear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """درآوردن [Slot] / /unwear <slot>"""
    host = _host()
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if not args:
        slots_txt = "، ".join(SLOTS)
        await message.reply_text(f"استفاده: «درآوردن [Slot]». Slotها: {slots_txt}")
        return
    uid = update.effective_user.id
    slot = args[0].strip().lower()
    if slot not in SLOTS:
        await message.reply_text("Slot نامعتبره.")
        return
    if slot not in equipped(chat.id, uid):
        await message.reply_text("چیزی توی این Slot پوشیده نشده.")
        return
    del equipped(chat.id, uid)[slot]
    await message.reply_text(f"✅ {slot} رو درآوردی.")
    await host.save_state()


async def outfit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ست من / /outfit — چیزی که الان پوشیدی."""
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    eq = equipped(chat.id, uid)
    if not eq:
        await message.reply_text("هنوز هیچی نپوشیدی. با «فروشگاه لباس» یه چیزی بخر.")
        return
    lines = ["👔 ست فعلی تو:", ""]
    for slot, key in eq.items():
        info = CATALOG.get(key)
        if info:
            lines.append(f"  {slot}: {info['name']} ({info['rarity']})")
    bonus = fame_bonus(chat.id, uid)
    if bonus:
        lines.append("")
        lines.append(f"✨ Bonus Fame از این ست: +{bonus}")
    await message.reply_text("\n".join(lines))


def outfit_summary_line(chat_id: int, uid: int) -> str:
    """یه خط کوتاه برای نمایش توی کارت پروفایل (فاز ۲۵)."""
    eq = equipped(chat_id, uid)
    if not eq:
        return ""
    count = len(eq)
    bonus = fame_bonus(chat_id, uid)
    bonus_txt = f" (+{bonus} Fame)" if bonus else ""
    return f"👔 ست: {count} آیتم پوشیده{bonus_txt}\n"


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "owned": {str(c): {str(u): list(s) for u, s in v.items()} for c, v in _owned.items()},
        "equipped": {str(c): {str(u): dict(e) for u, e in v.items()} for c, v in _equipped.items()},
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
            logger.warning(f"ذخیره‌ی state موتور Clothing ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور Clothing ناموفق بود: {e}")
        return
    for cid, v in data.get("owned", {}).items():
        _owned[int(cid)] = defaultdict(set, {int(u): set(s) for u, s in v.items()})
    for cid, v in data.get("equipped", {}).items():
        _equipped[int(cid)] = defaultdict(dict, {int(u): dict(e) for u, e in v.items()})
    logger.info("وضعیت موتور Clothing از فایل بارگذاری شد.")
