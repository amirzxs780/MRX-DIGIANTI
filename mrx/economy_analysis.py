# -*- coding: utf-8 -*-
"""
DIGNANTI ECONOMY ANALYSIS — فاز ۱۱ (V18: هوشِ اقتصادی و تحلیل)
=====================================================================
بخشِ ۱۷. طبقِ خواسته‌ی صریحِ همون بخش: «تحلیل‌ها باید از محاسباتِ
قابل‌توضیح تولید بشن» و «از تولیدِ اعداد یا خبرهایِ اقتصادیِ ساختگی به‌عنوانِ
اطلاعاتِ واقعی خودداری کن». این ماژول هیچ Stateی برایِ خودش نداره، هیچ
پولی جابه‌جا نمی‌کنه، و هیچ عددی حدس نمی‌زنه - فقط از رویِ داده‌یِ واقعیِ
ماژول‌هایِ دیگه (economy_business، economy_supply، economy_bank،
economy_housing) چندتا الگویِ مشخص رو چک می‌کنه و در صورتِ وجود گزارش
می‌ده. دقیقاً مثالِ‌هایِ خودِ بخشِ ۱۷:
  • «هزینه‌های یک شرکت بیشتر از درآمد آن شده است» → business_insights
  • «قیمت یک کالا به دلیل کاهش عرضه افزایش یافته است» → commodity_insights
  • «ارزش یک ملک نسبت به دوره قبلی تغییر کرده است» → property_insights
  • «کاربر بدهی سررسیدشده دارد» → loan_insights
  • «یک کسب‌وکار به سرمایه در گردش نیاز دارد» → business_insights (debt)
  • «موجودی انبار یک شرکت رو به اتمام است» → business_insights (inventory)
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

import config


def business_insights(chat_id: int, uid: int) -> list[str]:
    import economy_business
    out = []
    for b in economy_business.owned(chat_id, uid):
        info = economy_business.TYPES.get(b["type"], {"name": b["type"]})
        name = f"{info.get('name', b['type'])} #{b['id']}"

        if b.get("bankrupt"):
            out.append(
                f"🚫 {name} ورشکسته و Frozen‌ـه؛ بدهی: {b.get('debt', 0):,} {config.CURRENCY_NAME}. "
                f"با «پرداخت بدهی {b['id']} [مبلغ]» دوباره فعالش کن."
            )
            continue

        if b.get("crisis_strikes", 0) > 0:
            limit = getattr(config, "BUSINESS_BANKRUPTCY_STRIKES", 3)
            out.append(f"⚠️ {name} توی بحرانِ مالیه ({b['crisis_strikes']}/{limit} هشدار).")

        lifetime_revenue = b.get("lifetime_revenue", 0)
        lifetime_expense = b.get("lifetime_expense", 0)
        if lifetime_revenue > 0 and lifetime_expense > lifetime_revenue:
            out.append(
                f"📉 هزینه‌هایِ تجمعیِ {name} ({lifetime_expense:,}) از درآمدِ تجمعی‌اش "
                f"({lifetime_revenue:,}) بیشتر شده."
            )

        consumption = info.get("inventory_consumption_per_hour", 0)
        if consumption and consumption > 0:
            hours_left = b.get("inventory", 0) / consumption
            threshold = getattr(config, "ANALYSIS_LOW_INVENTORY_HOURS", 6)
            if hours_left < threshold:
                out.append(
                    f"📦 موجودیِ انبارِ {name} حدودِ {hours_left:.1f} ساعتِ دیگه تموم می‌شه - "
                    f"با «خرید موجودی {b['id']} [تعداد]» شارژش کن."
                )

        if b.get("debt", 0) > 0 and not b.get("bankrupt"):
            out.append(
                f"💰 {name} {b['debt']:,} بدهی داره و به سرمایه‌ی در گردش نیاز داره؛ "
                f"برایِ جلوگیری از بحرانِ بعدی زودتر با «پرداخت بدهی {b['id']} [مبلغ]» پرداختش کن."
            )
    return out


def commodity_insights(chat_id: int) -> list[str]:
    import economy_supply
    out = []
    for status in economy_supply.all_status(chat_id).values():
        if status["shortage"]:
            direction = "افزایش" if status["change_pct"] >= 0 else "کاهش"
            out.append(
                f"📈 قیمتِ {status['name']} به‌خاطرِ عدمِ تعادلِ عرضه/تقاضا "
                f"{abs(status['change_pct']):.1f}٪ {direction} پیدا کرده (کمبود در بازار)."
            )
    return out


def property_insights(chat_id: int, uid: int) -> list[str]:
    import economy_property
    out = []
    props = economy_property.owned(chat_id, uid)
    if not props:
        return out
    try:
        import economy_housing
        import economy_district
    except Exception:
        return out
    district_key = economy_district.district_of(chat_id, uid)
    status = economy_housing.get_status(chat_id, district_key)
    if status["zone"] in ("🔥 داغ", "🧊 کم‌تقاضا"):
        out.append(
            f"🏘 ارزشِ ملک‌هایِ منطقه‌ی «{status['name']}» نسبت به قیمتِ پایه تغییر کرده "
            f"(الان {status['zone']}، ضریب ×{status['multiplier']})."
        )
    return out


def loan_insights(chat_id: int, uid: int) -> list[str]:
    try:
        import economy_bank
    except Exception:
        return []
    out = []
    for loan in economy_bank.due_loans(chat_id, uid):
        out.append(
            f"🏦 قسطِ وامِ #{loan['id']} سررسید شده ({loan.get('installment_amount', 0):,} "
            f"{config.CURRENCY_NAME}) - با «پرداخت وام {loan['id']}» پرداختش کن تا جریمه نخوری."
        )
    return out


def generate_insights(chat_id: int, uid: int) -> list[str]:
    insights: list[str] = []
    insights.extend(business_insights(chat_id, uid))
    insights.extend(loan_insights(chat_id, uid))
    insights.extend(property_insights(chat_id, uid))
    insights.extend(commodity_insights(chat_id))
    return insights


def format_insights_text(chat_id: int, uid: int) -> str:
    insights = generate_insights(chat_id, uid)
    lines = ["🧠 تحلیلِ اقتصادیِ من", "─────────────────"]
    if not insights:
        lines.append("الان هیچ هشدار یا پیشنهادِ خاصی نیست - وضعیتِ اقتصادیت خوبه!")
        return "\n".join(lines)
    lines.extend(f"• {i}" for i in insights)
    lines.append("")
    lines.append("این تحلیل‌ها مستقیم از دادهٔ واقعیِ خودت ساخته شدن، نه حدس.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 📨 Command Handler
# ---------------------------------------------------------------------------

async def analysis_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحلیل اقتصادی / /analysis"""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or chat.type not in ("group", "supergroup"):
        await message.reply_text("این دستور فقط توی گروه کار می‌کنه.")
        return
    uid = update.effective_user.id
    await message.reply_text(format_insights_text(chat.id, uid))
