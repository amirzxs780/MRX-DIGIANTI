# -*- coding: utf-8 -*-
"""
DIGIANTI ECONOMY INVENTORY 2.0 — فاز ۷ (Module 17)
=======================================================
سیستم Badge/Timed-Item فعلی (economy_engine.py) دست‌نخورده می‌مونه؛ این فایل
فقط یه لایه‌ی جدید برای آیتم‌های «قابل‌جمع‌شدن» (Consumable/Pet Item/
Collectible) اضافه می‌کنه که واقعاً روی بقیه‌ی ماژول‌ها اثر می‌ذارن:
    • energy_drink → انرژی Job رو پر می‌کنه
    • lucky_charm → کار بعدی تضمینی شکست نمی‌خوره
    • escape_kit → فرار از زندان تضمینی موفقه
    • pet_snack → HP پت رو برمی‌گردونه
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
import economy_core

logger = logging.getLogger("economy_inventory")

STATE_FILE = getattr(config, "ECONOMY_INVENTORY_STATE_FILE", "economy_inventory_state.json")
CATALOG: dict = getattr(config, "ITEM_CATALOG", {})

_save_lock = asyncio.Lock()

# chat_id -> uid -> item_key -> quantity
_items = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))

# فلگ‌های یک‌بارمصرفِ اثرگذار روی ماژول‌های دیگه (این ماژول‌ها خودشون هنگام
# عملیات مربوطه این‌ها رو چک/پاک می‌کنن؛ اینجا فقط ذخیره می‌شن)
_pending_lucky_work = defaultdict(set)     # chat_id -> {uid, ...}
_pending_guaranteed_escape = defaultdict(set)


def _host():
    import bot as host
    return host


def get_quantity(chat_id: int, uid: int, key: str) -> int:
    return _items[chat_id][uid].get(key, 0)


def current_weight(chat_id: int, uid: int) -> float:
    """مجموع وزن (kg) همه‌ی آیتم‌های قابل‌جمع‌شدنِ این کاربر — فاز ۱۶."""
    total = 0.0
    for key, qty in _items[chat_id][uid].items():
        info = CATALOG.get(key)
        if info and qty > 0:
            total += info.get("weight", 0) * qty
    return round(total, 2)


def max_capacity_kg(chat_id: int, uid: int) -> float:
    return float(getattr(config, "INVENTORY_BASE_CAPACITY_KG", 50.0))


def remaining_capacity_kg(chat_id: int, uid: int) -> float:
    return round(max_capacity_kg(chat_id, uid) - current_weight(chat_id, uid), 2)


def add_item(chat_id: int, uid: int, key: str, qty: int = 1, *, enforce_capacity: bool = False) -> bool:
    """اضافه‌کردن آیتم به کوله. اگه enforce_capacity=True باشه و وزن جدید از
    ظرفیت رد بشه، هیچی اضافه نمی‌شه و False برمی‌گرده (طبق فاز ۱۶: Weight/
    Capacity). enforce_capacity پیش‌فرض False‌ه تا مصرف‌های داخلی فعلی (مثل
    برگردوندنِ آیتم مصرف‌نشده) هیچ‌وقت به‌خاطر ظرفیت شکست نخورن."""
    if enforce_capacity:
        info = CATALOG.get(key)
        added_weight = info.get("weight", 0) * qty if info else 0
        if added_weight > 0 and current_weight(chat_id, uid) + added_weight > max_capacity_kg(chat_id, uid):
            return False
    _items[chat_id][uid][key] = _items[chat_id][uid].get(key, 0) + qty
    return True


def remove_item(chat_id: int, uid: int, key: str, qty: int = 1) -> bool:
    have = get_quantity(chat_id, uid, key)
    if have < qty:
        return False
    remaining = have - qty
    if remaining <= 0:
        _items[chat_id][uid].pop(key, None)
    else:
        _items[chat_id][uid][key] = remaining
    return True


def has_lucky_work(chat_id: int, uid: int) -> bool:
    return uid in _pending_lucky_work[chat_id]


def consume_lucky_work(chat_id: int, uid: int) -> None:
    _pending_lucky_work[chat_id].discard(uid)


def has_guaranteed_escape(chat_id: int, uid: int) -> bool:
    return uid in _pending_guaranteed_escape[chat_id]


def consume_guaranteed_escape(chat_id: int, uid: int) -> None:
    _pending_guaranteed_escape[chat_id].discard(uid)


# ---------------------------------------------------------------------------
# 📋 نمایش (از bot.myitems_command صدا زده می‌شه، اضافه بر خروجی فعلی)
# ---------------------------------------------------------------------------

def extra_inventory_text(chat_id: int, uid: int) -> str:
    owned = _items[chat_id][uid]
    if not owned:
        return ""
    lines = ["", "🎒 آیتم‌های قابل‌جمع‌شدن:"]
    for key, qty in owned.items():
        if qty <= 0:
            continue
        info = CATALOG.get(key)
        if not info:
            continue
        w = info.get("weight", 0) * qty
        lines.append(f"  {info['name']} × {qty} ({info['category']}, {info['rarity']}) — {w:.1f}kg")
    lines.append(f"⚖️ وزن کل: {current_weight(chat_id, uid)}kg / {max_capacity_kg(chat_id, uid)}kg")
    return "\n".join(lines)


async def capacity_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ظرفیت انبار / /inventorycapacity — فاز ۱۶: Weight/Capacity."""
    chat, message = update.effective_chat, update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    weight = current_weight(chat.id, uid)
    capacity = max_capacity_kg(chat.id, uid)
    bar_len = 20
    filled = round(bar_len * min(1.0, weight / capacity)) if capacity else 0
    bar = "█" * filled + "░" * (bar_len - filled)
    await message.reply_text(f"🎒 ظرفیت کوله: {weight}kg / {capacity}kg\n[{bar}]")


# ---------------------------------------------------------------------------
# 🛒 خرید (از bot.buy_command به‌عنوان Fallback سوم صدا زده می‌شه)
# ---------------------------------------------------------------------------

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    args = context.args or []
    if len(args) < 1:
        await message.reply_text("استفاده: «خرید [کد آیتم]»")
        return
    key = args[0]
    info = CATALOG.get(key)
    if not info:
        await message.reply_text("همچین آیتمی نیست.")
        return
    uid = update.effective_user.id
    if not add_item(chat.id, uid, key, 1, enforce_capacity=True):
        await message.reply_text(
            f"🎒 کوله‌ت جا نداره! ظرفیت: {max_capacity_kg(chat.id, uid)}kg — الان: {current_weight(chat.id, uid)}kg — "
            f"وزن این آیتم: {info.get('weight', 0)}kg"
        )
        return
    try:
        await economy_core.remove_coins(chat.id, uid, info["price"], kind="item_buy", note=f"خرید {info['name']}")
    except economy_core.InsufficientFundsError:
        remove_item(chat.id, uid, key, 1)  # پول کافی نبود، آیتمی که موقتاً اضافه شده بود رو برگردون
        await message.reply_text(f"❌ موجودی کافی نیست. قیمت: {info['price']:,} {config.CURRENCY_NAME}")
        return
    await message.reply_text(f"✅ {info['name']} خریداری شد! با «استفاده از {key}» ازش استفاده کن.")
    await host.save_state()


# ---------------------------------------------------------------------------
# ✨ استفاده از آیتم
# ---------------------------------------------------------------------------

async def use_item_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استفاده از [آیتم] / /useitem <key>"""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    args = context.args or []
    if not args:
        await message.reply_text("استفاده: «استفاده از [کد آیتم]»")
        return
    key = args[0]
    info = CATALOG.get(key)
    if not info:
        await message.reply_text("همچین آیتمی نیست.")
        return
    uid = update.effective_user.id
    if not remove_item(chat.id, uid, key, 1):
        await message.reply_text("از این آیتم چیزی نداری.")
        return

    effect = info.get("effect")
    if effect == "refill_energy":
        import economy_jobs
        economy_jobs._energy[chat.id][uid] = {"value": float(config.ENERGY_MAX), "ts": time.time()}
        await message.reply_text(f"⚡ {info['name']} استفاده شد! انرژیت کامل پر شد.")
    elif effect == "guarantee_next_work":
        _pending_lucky_work[chat.id].add(uid)
        await message.reply_text(f"🍀 {info['name']} استفاده شد! کار بعدیت تضمینی موفقه.")
    elif effect == "guarantee_escape":
        _pending_guaranteed_escape[chat.id].add(uid)
        await message.reply_text(f"🗝️ {info['name']} استفاده شد! فرار بعدیت از زندان تضمینی موفقه.")
    elif effect == "pet_snack_heal":
        import economy_pet
        pet = economy_pet.get_pet(chat.id, uid)
        if not pet:
            add_item(chat.id, uid, key, 1)  # پتی نداره، آیتم رو برگردون
            await message.reply_text("پتی نداری که بهش بدی. آیتم برگشت داده شد.")
            return
        economy_pet._current_hp(chat.id, uid)
        max_hp = economy_pet._max_hp(pet)
        pet["hp"] = min(max_hp, pet["hp"] + getattr(config, "ITEM_PET_SNACK_HP_RESTORE", 20))
        await message.reply_text(f"🦴 {info['name']} به پتت دادی! ❤️ HP: {round(pet['hp'])}/{max_hp}")
    else:
        add_item(chat.id, uid, key, 1)  # آیتم تزئینیه، اثر مصرفی نداره؛ برگردون
        await message.reply_text(f"{info['name']} یه یادگاریه، اثر مصرفی نداره — فقط برای نمایش توی کیفته.")
        return

    await host.save_state()


# ---------------------------------------------------------------------------
# 💾 Persistence
# ---------------------------------------------------------------------------

def _collect_state() -> dict:
    return {
        "items": {
            str(c): {str(u): dict(items) for u, items in v.items()}
            for c, v in _items.items()
        },
        "pending_lucky_work": {str(c): list(s) for c, s in _pending_lucky_work.items()},
        "pending_guaranteed_escape": {str(c): list(s) for c, s in _pending_guaranteed_escape.items()},
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
            logger.warning(f"ذخیره‌ی state موتور Inventory ناموفق بود: {e}")


def load_state():
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"خوندن state موتور Inventory ناموفق بود: {e}")
        return
    for cid, v in data.get("items", {}).items():
        for uid, items in v.items():
            _items[int(cid)][int(uid)] = defaultdict(int, items)
    for cid, lst in data.get("pending_lucky_work", {}).items():
        _pending_lucky_work[int(cid)] = set(lst)
    for cid, lst in data.get("pending_guaranteed_escape", {}).items():
        _pending_guaranteed_escape[int(cid)] = set(lst)
    logger.info("وضعیت موتور Inventory از فایل بارگذاری شد.")
