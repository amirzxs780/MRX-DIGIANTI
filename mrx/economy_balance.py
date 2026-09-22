# -*- coding: utf-8 -*-
"""
DIGIANTI BALANCE ENGINE — فاز ۹ (Module 26)
================================================
هیچ State جدیدی نمی‌سازه — economy_core.py از فاز ۹ به بعد خودش هر
تراکنش رو به تفکیک kind توی _source_totals/_sink_totals جمع می‌زنه (چون
همه‌ی پول از record_transaction عبور می‌کنه). این فایل فقط یه گزارش خوانا
از همون داده‌ها می‌سازه.
"""

from __future__ import annotations

import time

from telegram import Update
from telegram.ext import ContextTypes

import config
import economy_core


def _host():
    import bot as host
    return host


async def economy_health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """سلامت اقتصاد / /economyhealth — فقط ادمین (manage_economy)."""
    host = _host()
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    if not host.has_permission(update.effective_user.id, "manage_economy"):
        await message.reply_text("⛔ این دستور فقط برای ادمین‌های اقتصاده.")
        return

    wallet_total = sum(host._wallet.get(chat.id, {}).values())
    bank_total = sum(economy_core._bank.get(chat.id, {}).values())
    circulating = wallet_total + bank_total

    # ارتقای فاز ۲۶ (Economy Admin Center): ارزش کل دارایی‌های غیرنقدی +
    # حجم تراکنش + فعالیت مشکوک + ثروتمندترین‌ها + برترین کسب‌وکارها + وضعیت فریز
    members = list(host._known_members.get(chat.id, set()))

    def _safe_sum(fn):
        total = 0
        for u in members:
            try:
                total += fn(chat.id, u)
            except Exception:
                pass
        return total

    total_property = total_vehicle = total_business = 0
    try:
        import economy_property
        total_property = sum(
            economy_property.current_value(p) for u in members for p in economy_property.owned(chat.id, u)
        )
    except Exception:
        pass
    try:
        import economy_vehicle
        total_vehicle = _safe_sum(economy_vehicle.get_vehicle_value)
    except Exception:
        pass
    try:
        import economy_business
        total_business = _safe_sum(economy_business.get_business_value)
    except Exception:
        pass

    volume = economy_core.get_ledger_volume(chat.id)
    threshold = getattr(config, "SUSPICIOUS_TRANSACTION_THRESHOLD", 50000)
    window_h = getattr(config, "SUSPICIOUS_WINDOW_HOURS", 24)
    suspicious = economy_core.find_suspicious_transactions(chat.id, threshold, time.time() - window_h * 3600)
    richest = sorted(members, key=lambda u: economy_core.get_net_worth(chat.id, u), reverse=True)[:5]
    frozen = economy_core.is_frozen(chat.id)

    sources = economy_core._source_totals.get(chat.id, {})
    sinks = economy_core._sink_totals.get(chat.id, {})
    total_sources = sum(sources.values())
    total_sinks = sum(sinks.values())
    ratio = (total_sources / total_sinks) if total_sinks else float("inf")

    def top(d: dict, n: int = 5):
        return sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:n]

    lines = [
        f"📊 سلامت اقتصاد گروه {'🧊 (فریز شده)' if frozen else ''}",
        "",
        f"{config.CURRENCY_EMOJI} مجموع پول در گردش: {circulating:,} (کیف: {wallet_total:,} — بانک: {bank_total:,})",
        f"🏠 ارزش کل املاک: {total_property:,} — 🚗 خودروها: {total_vehicle:,} — 🏢 کسب‌وکارها: {total_business:,}",
        f"💎 کل دارایی اقتصاد (نقد + غیرنقد): {circulating + total_property + total_vehicle + total_business:,}",
        f"🧾 حجم کل تراکنش‌ها: {volume:,}",
        "",
        f"📈 مجموع Source (پول واردشده): {total_sources:,}",
        f"📉 مجموع Sink (پول خارج‌شده): {total_sinks:,}",
        f"⚖️ نسبت Source/Sink: {ratio:.2f}" + ("" if ratio != float("inf") else " (هنوز Sink ای ثبت نشده)"),
        "",
        "🔝 بزرگ‌ترین Sourceها:",
    ]
    for kind, amount in top(sources):
        lines.append(f"  • {kind}: +{amount:,}")
    lines.append("")
    lines.append("🔻 بزرگ‌ترین Sinkها:")
    for kind, amount in top(sinks):
        lines.append(f"  • {kind}: -{amount:,}")

    if richest:
        lines.append("")
        lines.append("👑 ثروتمندترین‌ها:")
        for u in richest:
            lines.append(f"  • {host._user_display_names.get(u, str(u))}: {economy_core.get_net_worth(chat.id, u):,}")

    try:
        import economy_business
        all_biz = [(u, b) for u in members for b in economy_business.owned(chat.id, u)]
        all_biz.sort(key=lambda ub: economy_business.current_value(ub[1]), reverse=True)
        top_businesses = all_biz[:5]
        if top_businesses:
            lines.append("")
            lines.append("🏆 برترین کسب‌وکارها:")
            for u, b in top_businesses:
                lines.append(
                    f"  • {economy_business.TYPES[b['type']]['name']} #{b['id']} "
                    f"({host._user_display_names.get(u, str(u))}): {economy_business.current_value(b):,}"
                )
    except Exception:
        pass

    if suspicious:
        lines.append("")
        lines.append(f"⚠️ تراکنش‌های مشکوک ({window_h} ساعت اخیر، بالای {threshold:,}):")
        for tx in suspicious:
            lines.append(f"  • {tx['type']} — {tx['amount']:,} — کاربر {tx['user_id']} — {tx['transaction_id'][:8]}")

    lines.append("")
    if ratio > 1.5:
        lines.append("⚠️ Source بیشتر از Sink‌ه — احتمال تورم پول در بلندمدت؛ شاید لازم باشه هزینه‌ها (مالیات/کارمزد) رو زیاد کنید.")
    elif ratio < 0.7 and total_sinks > 0:
        lines.append("⚠️ Sink بیشتر از Source‌ه — احتمال کمبود پول در گردش؛ شاید لازم باشه پاداش‌ها رو زیاد کنید.")
    else:
        lines.append("✅ نسبت Source/Sink در بازه‌ی سالمیه.")

    await message.reply_text("\n".join(lines))
