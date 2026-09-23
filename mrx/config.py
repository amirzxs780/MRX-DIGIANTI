# -*- coding: utf-8 -*-
"""
تنظیمات ربات
توکن رو از @BotFather بگیر و به‌عنوان متغیر محیطی BOT_TOKEN ست کن
یا مستقیم همینجا جایگزین "YOUR_TOKEN_HERE" کن (برای تست سریع).
"""
import os

# توکن و کلیدها فقط از Environment Variables خونده می‌شن (هیچ وابستگی‌ای به فایل
# .env نیست و هیچ مقدار پیش‌فرض/هاردکدی هم توی کد نیست). توی پنل هاستی که
# استفاده می‌کنی (مثلاً Deployka/Railway/Liara و ...) بخش Environment Variables
# رو باز کن و این متغیرها رو دستی ست کن:
#   BOT_TOKEN=<توکن ربات از @BotFather>
#   GROQ_API_KEY=<کلید از https://console.groq.com/keys>
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "متغیر محیطی BOT_TOKEN ست نشده. توکن رو توی پنل هاست، بخش Environment Variables اضافه کن."
    )

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# ---------- هوش مصنوعی (Groq) ----------

# مدل گروک مورد استفاده.
# نکته‌ی مهم درباره‌ی سرعت: "openai/gpt-oss-120b" یه مدل بزرگه و کندتر جواب می‌ده.
# برای کمترین تاخیر، مدل رو روی یکی از مدل‌های سریع‌تر گروک گذاشتیم:
#   "openai/gpt-oss-20b"        -> نسخه‌ی کوچیک‌تر همون خانواده، خیلی سریع‌تر از 120b (پیش‌فرض فعلی)
#   "llama-3.1-8b-instant"      -> سریع‌ترین مدل گروک، برای جواب‌های خیلی کوتاه/سریع
#   "llama-3.3-70b-versatile"   -> تعادل بین سرعت و کیفیت
# اگه بازم کند بود، اول این رو امتحان کن: "llama-3.1-8b-instant"
AI_MODEL = "openai/gpt-oss-20b"

# سقف تعداد توکن خروجی - هرچی کمتر، جواب سریع‌تر آماده می‌شه (پاسخ‌های کوتاه‌تر = تاخیر کمتر)
AI_MAX_TOKENS = 400

# دمای مدل (خلاقیت پاسخ). عدد کمتر = پاسخ سریع‌تر و قطعی‌تر
AI_TEMPERATURE = 0.6

# حداکثر زمان انتظار برای جواب API (ثانیه) - اگه گروک دیر جواب بده، به جای معطلی طولانی، خطا می‌ده
AI_TIMEOUT_SECONDS = 12

# طول متن کاربر که برای AI فرستاده می‌شه رو محدود می‌کنیم (پیام‌های خیلی بلند = پردازش کندتر)
AI_MAX_INPUT_CHARS = 1500

# دستورالعملی که شخصیت/رفتار ربات رو مشخص می‌کنه
AI_SYSTEM_INSTRUCTION = (
    "تو یک دستیار مفید و دوستانه در تلگرام هستی. کوتاه، مودبانه و به فارسی پاسخ بده."
)

# آیدی عددی ادمین‌هایی که مجاز به دستورات مدیریتی هستن (می‌تونی چندتا اضافه کنی)
# آیدی عددی هرکسی رو می‌تونی از ربات @userinfobot بگیری
ADMIN_IDS = [
    7286496010,
    5812434499,
]

# این ربات شخصیه؛ اگه یه کاربر عادی (غیرادمین) توی پیوی ربات /help بزنه، به‌جای لیست
# دستورات مدیریتی، همین متن براش نشون داده می‌شه. یوزرنیم یا آیدی خودتو اینجا بذار.
OWNER_CONTACT = "@MRXPRIME"

# دسترسی هر ادمین رو دقیق مشخص کن. کلیدهای ممکن:
#   "menu"                 -> استفاده از /menu (روشن/خاموش کردن قابلیت‌ها) + تمام دستورات متنی فارسی
#                              روشن/خاموش (مثل «ربات روشن/خاموش» و بقیه‌ی کلیدواژه‌های SETTING_KEYWORDS)
#   "notify"                -> استفاده از /notify (اطلاع‌رسانی همگانی)
#   "manage_relationships"  -> دوست/دشمن کردن، لغوش، و روشن/خاموش کردن ترمیناتور
#   "manage_blacklist"      -> اضافه/حذف از بلاک‌لیست
#   "moderate"              -> اخطار/میوت/آن‌میوت/بن/آن‌بن/پاک‌سازی پیام دستی، و ریست اخطارها
#   "manage_admins"         -> ارتقا/عزل ادمین‌های تلگرامی گروه (خطرناک‌ترین دسترسیه، با احتیاط بده)
#   "tag_members"           -> استفاده از /tag و «تگ همه» برای تگ کردن دسته‌جمعی اعضا
#   "manage_economy"        -> پنل ویژه‌ی ادمین: تنظیم دستی سکه/XP/لول افراد (تکی یا همگی) و
#                              تنظیمات اقتصاد/قرعه‌کشی هر گروه (/seteco, /setcoins, /setxp, ...)
#
# اگه آیدی یه ادمین توی این دیکشنری نباشه، به‌طور پیش‌فرض به همه‌چیز دسترسی کامل داره.
# برای محدود کردن یه ادمین، آیدیش رو با لیست دقیق دسترسی‌هاش اضافه کن، مثلاً:
#   111111111: {"manage_relationships"},   # این ادمین فقط دوست/دشمن‌کردن دسترسی داره
ADMIN_PERMISSIONS = {
    7286496010: {
        "menu",
        "notify",
        "manage_relationships",
        "manage_blacklist",
        "moderate",
        "manage_admins",
        "tag_members",
        "manage_economy",
    },
    5812434499: {
        "menu",
        "notify",
        "manage_relationships",
        "manage_blacklist",
        "moderate",
        "manage_admins",
        "tag_members",
        "manage_economy",
    },
}

# تایم‌زونی که تاریخ/ساعت پیام‌های ورود، خروج و... باهاش نمایش داده می‌شن.
# اگه سرور دیتابیس tzdata نداشته باشه (مثلاً بعضی نصب‌های ویندوز)، به‌صورت خودکار و
# بی‌سروصدا به ساعت سیستمی سرور برمی‌گرده - ربات کرش نمی‌کنه.
TIMEZONE = "Asia/Tehran"

# اگه اسم/آیدی گروه یا تعداد اعضا از API قابل خوندن نبود (نادر، ولی ممکنه)
MEMBER_COUNT_UNKNOWN = "نامشخص"

# ---------- پیام‌های خوش‌آمدگویی (چندتا نسخه؛ هر بار یکی به‌صورت تصادفی انتخاب می‌شه) ----------
# جای‌گزین‌های قابل استفاده: {full_name} {username_line} {user_id} {date} {time} {member_count}
# توجه: {username_line} خودش یا خالیه یا یه خط کامل با یوزرنیم - نیازی نیست دستی پرش کنی.
WELCOME_MESSAGES = [
    (
        "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "𓆩 𝐖𝐄𝐋𝐂𝐎𝐌𝐄 𓆪\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "👤 𝐂𝐨𝐦𝐞𝐫:\n{full_name}\n\n"
        "🌸 خوش اومدی به جمع ما!\n"
        "امیدواریم اینجا لحظات خوبی داشته باشی ❤️\n\n"
        "┏━━━━━━━━━━━━━━━━━━━━━━┓\n"
        "┃ 🆔 آیدی عددی: {user_id}\n"
        "{username_line}"
        "┃ 📅 تاریخ ورود: {date}\n"
        "┃ ⏰ ساعت ورود: {time}\n"
        "┃ 👥 اعضای گروه: {member_count}\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        "✦ قوانین گروه رو حتماً مطالعه کن.\n"
        "✦ برای شروع، یه سلام به بچه ها بده! 👋\n\n"
        "    𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪"
    ),
    (
        "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "𓆩 𝐍𝐄𝐖 𝐌𝐄𝐌𝐁𝐄𝐑 𓆪\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "سلام {full_name} 👋\n"
        "خوشحالیم که به خانواده‌مون اضافه شدی!\n\n"
        "┏━━━━━━━━━━━━━━━━━━━━━━┓\n"
        "┃ 🆔 آیدی عددی: {user_id}\n"
        "{username_line}"
        "┃ 📅 {date}  ⏰ {time}\n"
        "┃ 👥 نفر {member_count}ام گروه\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        "راحت باش، هر سوالی داشتی بپرس 🌟\n\n"
        "    𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪"
    ),
    (
        "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "𓆩 𝐇𝐄𝐋𝐋𝐎 𓆪\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "🎉 یه عضو جدید به جمعمون اضافه شد:\n{full_name}\n\n"
        "┏━━━━━━━━━━━━━━━━━━━━━━┓\n"
        "┃ 🆔 آیدی عددی: {user_id}\n"
        "{username_line}"
        "┃ 🕐 {date} - {time}\n"
        "┃ 👥 مجموع اعضا: {member_count}\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        "امیدواریم بهمون خوش بگذره 🖤\n\n"
        "    𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪"
    ),
]

# پیام ورود یه ربات جدید به گروه (وقتی قفل ربات‌ها خاموشه، وگرنه اصلاً اجازه‌ی موندن نداره)
BOT_JOIN_MESSAGE = (
    "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
    "𓆩 𝐁𝐎𝐓 𝐉𝐎𝐈𝐍𝐄𝐃 𓆪\n"
    "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
    "🤖 یه ربات جدید اضافه شد:\n{full_name}\n\n"
    "┏━━━━━━━━━━━━━━━━━━━━━━┓\n"
    "┃ 🆔 آیدی عددی: {user_id}\n"
    "{username_line}"
    "┃ 📅 {date}  ⏰ {time}\n"
    "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
    "    𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪"
)

# ---------- پیام‌های خروج (خودِ کاربر رفته - نه اخراج) ----------
# جای‌گزین‌های اضافه: {duration_line} (اگه زمان ورودش معلوم باشه، یه خط "مدت حضور" اضافه می‌کنه - وگرنه خالیه)
LEAVE_MESSAGES_SELF = [
    (
        "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "𓆩 𝐆𝐎𝐎𝐃𝐁𝐘𝐄 𓆪\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "👤 𝐔𝐬𝐞𝐫:\n{full_name}\n\n"
        "🚪 از گروه خارج شد.\n\n"
        "┏━━━━━━━━━━━━━━━━━━━━━━┓\n"
        "┃ 🆔 آیدی عددی: {user_id}\n"
        "{username_line}"
        "┃ 📅 تاریخ خروج: {date}\n"
        "┃ ⏰ ساعت خروج: {time}\n"
        "{duration_line}"
        "┃ 👥 اعضای باقی‌مانده: {member_count}\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        "💭 جای خالیت حس میشه…\n"
        "امیدواریم دوباره ببینیمت! 🖤\n\n"
        "    𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪"
    ),
    (
        "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "𓆩 𝐒𝐄𝐄 𝐘𝐎𝐔 𓆪\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "یکی از جمعمون رفت:\n{full_name}\n\n"
        "┏━━━━━━━━━━━━━━━━━━━━━━┓\n"
        "┃ 🆔 آیدی عددی: {user_id}\n"
        "{username_line}"
        "┃ 📅 {date}  ⏰ {time}\n"
        "{duration_line}"
        "┃ 👥 اعضای باقی‌مانده: {member_count}\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        "بدرود رفیق، در رو باز می‌ذاریم برای برگشتنت 🚪✨\n\n"
        "    𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪"
    ),
]

# ---------- پیام‌های خروج (اخراج/بن توسط ادمین - جدا از خروج خودخواسته) ----------
LEAVE_MESSAGES_KICKED = [
    (
        "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "𓆩 𝐑𝐄𝐌𝐎𝐕𝐄𝐃 𓆪\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "👤 𝐔𝐬𝐞𝐫:\n{full_name}\n\n"
        "👢 توسط ادمین از گروه حذف شد.\n\n"
        "┏━━━━━━━━━━━━━━━━━━━━━━┓\n"
        "┃ 🆔 آیدی عددی: {user_id}\n"
        "{username_line}"
        "┃ 📅 تاریخ: {date}\n"
        "┃ ⏰ ساعت: {time}\n"
        "{duration_line}"
        "┃ 👥 اعضای باقی‌مانده: {member_count}\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        "    𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪"
    ),
    (
        "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        "𓆩 𝐁𝐀𝐍𝐍𝐄𝐃 𓆪\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "🚫 {full_name} از گروه بن شد.\n\n"
        "┏━━━━━━━━━━━━━━━━━━━━━━┓\n"
        "┃ 🆔 آیدی عددی: {user_id}\n"
        "{username_line}"
        "┃ 📅 {date}  ⏰ {time}\n"
        "{duration_line}"
        "┃ 👥 اعضای باقی‌مانده: {member_count}\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        "    𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪"
    ),
]

# پیام مخصوص وقتی کاربر از طریق یه لینک دعوت اختصاصی وارد شده (متفاوت از ورود عادی)
# جای‌گزین اضافه: {invite_link_name}
WELCOME_MESSAGE_INVITE_LINK = (
    "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
    "𓆩 𝐉𝐎𝐈𝐍𝐄𝐃 𝐕𝐈𝐀 𝐋𝐈𝐍𝐊 𓆪\n"
    "╰━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
    "👤 𝐂𝐨𝐦𝐞𝐫:\n{full_name}\n\n"
    "🔗 از طریق لینک دعوت «{invite_link_name}» وارد شد!\n"
    "🌸 خوش اومدی به جمع ما ❤️\n\n"
    "┏━━━━━━━━━━━━━━━━━━━━━━┓\n"
    "┃ 🆔 آیدی عددی: {user_id}\n"
    "{username_line}"
    "┃ 📅 تاریخ ورود: {date}\n"
    "┃ ⏰ ساعت ورود: {time}\n"
    "┃ 👥 اعضای گروه: {member_count}\n"
    "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
    "✦ قوانین گروه رو حتماً مطالعه کن.\n\n"
    "    𓆩 𝐃𝐈𝐆𝐈𝐀𝐍𝐓𝐈 𓆪"
)

# ---------- تم ساده و مینیمال (بدون کادر، برای گروه‌هایی که ظاهر شلوغ نمی‌خوان) ----------
WELCOME_MESSAGES_MINIMAL = [
    (
        "🌸 {full_name} به گروه اضافه شد.\n"
        "🆔 {user_id}\n"
        "{username_line}"
        "📅 {date} - ⏰ {time}\n"
        "👥 اعضا: {member_count}\n\n"
        "خوش اومدی! 👋"
    ),
    (
        "👋 سلام {full_name}!\n"
        "به جمعمون خوش اومدی.\n"
        "🆔 {user_id}\n"
        "{username_line}"
        "👥 نفر {member_count}ام گروه"
    ),
]
WELCOME_MESSAGE_INVITE_LINK_MINIMAL = (
    "🔗 {full_name} با لینک دعوت «{invite_link_name}» وارد شد.\n"
    "🆔 {user_id}\n"
    "{username_line}"
    "👥 اعضا: {member_count}\n\n"
    "خوش اومدی! 👋"
)
LEAVE_MESSAGES_SELF_MINIMAL = [
    (
        "🚪 {full_name} از گروه رفت.\n"
        "🆔 {user_id}\n"
        "📅 {date} - ⏰ {time}\n"
        "{duration_line}"
        "👥 اعضای باقی‌مانده: {member_count}"
    ),
]
LEAVE_MESSAGES_KICKED_MINIMAL = [
    (
        "👢 {full_name} از گروه حذف شد.\n"
        "🆔 {user_id}\n"
        "📅 {date} - ⏰ {time}\n"
        "{duration_line}"
        "👥 اعضای باقی‌مانده: {member_count}"
    ),
]
BOT_JOIN_MESSAGE_MINIMAL = "🤖 ربات {full_name} اضافه شد. (🆔 {user_id})"

# ---------- تم پرانرژی و شاد (پر از ایموجی، برای گروه‌های صمیمی) ----------
WELCOME_MESSAGES_PARTY = [
    (
        "🎉🎊🎉🎊🎉🎊🎉\n"
        "یوهووو یه عضو جدید اومد! 🥳\n"
        "🎉🎊🎉🎊🎉🎊🎉\n\n"
        "✨ {full_name} ✨\n\n"
        "🆔 {user_id}\n"
        "{username_line}"
        "📅 {date} ⏰ {time}\n"
        "👥 حالا شدیم {member_count} نفر! 🚀\n\n"
        "بزن بریم بترکونیم 🔥🎈"
    ),
    (
        "🎈 خب خب خب، ببین کی اومده! 👀\n\n"
        "🌟 {full_name} 🌟\n\n"
        "🆔 {user_id}\n"
        "{username_line}"
        "👥 عضو شماره {member_count} 🏆\n\n"
        "جشن بگیریم؟ 🥂🎉"
    ),
]
WELCOME_MESSAGE_INVITE_LINK_PARTY = (
    "🎉 یکی با لینک «{invite_link_name}» اومد تو! 🎊\n\n"
    "✨ {full_name} ✨\n"
    "🆔 {user_id}\n"
    "{username_line}"
    "👥 {member_count} نفری شدیم! 🚀\n\n"
    "خوش اومدی رفیق 🔥"
)
LEAVE_MESSAGES_SELF_PARTY = [
    (
        "😢 وایی یکی رفت...\n\n"
        "💔 {full_name} 💔\n"
        "🆔 {user_id}\n"
        "📅 {date} ⏰ {time}\n"
        "{duration_line}"
        "👥 {member_count} نفر موندیم\n\n"
        "دلمون برات تنگ می‌شه 🥺"
    ),
]
LEAVE_MESSAGES_KICKED_PARTY = [
    (
        "🚨🚨 هشدار! یه نفر پرتاب شد! 🚨🚨\n\n"
        "👢 {full_name} 👢\n"
        "🆔 {user_id}\n"
        "📅 {date} ⏰ {time}\n"
        "{duration_line}"
        "👥 {member_count} نفر موندیم"
    ),
]
BOT_JOIN_MESSAGE_PARTY = "🤖🎉 یه ربات جدید پرید وسط جمع: {full_name} (🆔 {user_id})"

# ---------- رجیستری تم‌ها: هر گروه می‌تونه یکی از این تم‌ها رو برای پیام‌های ورود/خروجش
# انتخاب کنه (از /menu → «ورود و خوش‌آمدگویی» یا دستور /welcome_theme) ----------
MESSAGE_THEMES = {
    "digianti": {
        "label": "🖤 دیجی‌انتی (پیش‌فرض)",
        "welcome": WELCOME_MESSAGES,
        "welcome_invite_link": WELCOME_MESSAGE_INVITE_LINK,
        "leave_self": LEAVE_MESSAGES_SELF,
        "leave_kicked": LEAVE_MESSAGES_KICKED,
        "bot_join": BOT_JOIN_MESSAGE,
    },
    "minimal": {
        "label": "⚪ ساده و مینیمال",
        "welcome": WELCOME_MESSAGES_MINIMAL,
        "welcome_invite_link": WELCOME_MESSAGE_INVITE_LINK_MINIMAL,
        "leave_self": LEAVE_MESSAGES_SELF_MINIMAL,
        "leave_kicked": LEAVE_MESSAGES_KICKED_MINIMAL,
        "bot_join": BOT_JOIN_MESSAGE_MINIMAL,
    },
    "party": {
        "label": "🎉 پرانرژی و شاد",
        "welcome": WELCOME_MESSAGES_PARTY,
        "welcome_invite_link": WELCOME_MESSAGE_INVITE_LINK_PARTY,
        "leave_self": LEAVE_MESSAGES_SELF_PARTY,
        "leave_kicked": LEAVE_MESSAGES_KICKED_PARTY,
        "bot_join": BOT_JOIN_MESSAGE_PARTY,
    },
}
DEFAULT_MESSAGE_THEME = "digianti"


AUTO_REPLIES = {
    "سلام": "سلام! چطور می‌تونم کمکت کنم؟",
    "قیمت": "برای اطلاع از قیمت‌ها لطفاً با پشتیبانی تماس بگیرید.",
    "ساعت کاری": "ساعت کاری: شنبه تا چهارشنبه، ۹ تا ۱۷",
}

# کلمات/الگوهایی که پیام حاوی اون‌ها به‌عنوان اسپم حذف می‌شه
BANNED_WORDS = [
    "تبلیغ",
    "فالو بگیر",
]

# آیا لینک‌ها و یوزرنیم‌های تلگرامی (@something) به‌طور خودکار اسپم شناخته بشن؟
BLOCK_LINKS = True

# حداکثر تعداد پیام مجاز در بازه‌ی زمانی (برای جلوگیری از فلود)
FLOOD_LIMIT_MESSAGES = 5
FLOOD_LIMIT_SECONDS = 10

# ---------- مدیریت کاربرا (هشدار + میوت، بدون بن خودکار) ----------

# بعد از چند بار تخلف (اسپم/فلود)، کاربر میوت می‌شه — این مقدار پیش‌فرضه، هر گروه با دکمه‌ی /menu می‌تونه عوضش کنه
WARNING_LIMIT_BEFORE_MUTE = 3

# مدت میوت موقت (به دقیقه) — این مقدار پیش‌فرضه، هر گروه با دکمه‌ی /menu می‌تونه عوضش کنه
MUTE_DURATION_MINUTES = 30

# نکته: قبلاً اینجا دو تا لیست گزینه‌ی از پیش‌تعیین‌شده برای «حد اخطار» و «مدت میوت» بود که
# با دکمه توی /menu بینشون می‌چرخیدی. حالا به‌جاش با دستورات /setwarnlimit و /setmutetime
# می‌تونی مستقیم هر عدد دلخواهی رو ست کنی (ببین پایین‌تر توضیحات همون دستورات توی help).

# هر بار یه نفر دوباره میوت بشه، مدت میوتش دوبرابر قبلیه (میوت پیشرفته/تصاعدی) — تا این سقف (به دقیقه)
MAX_MUTE_MINUTES = 1440  # ۲۴ ساعت

# ---------- ذخیره‌سازی دائمی ----------

# مسیر فایلی که همه‌ی تنظیمات/لیست‌ها/آمار توش ذخیره می‌شه تا با ری‌استارت شدن ربات از بین نره
STATE_FILE = "bot_state.json"

# هر چند ثانیه یه‌بار، به‌صورت خودکار وضعیت ذخیره بشه (علاوه بر ذخیره‌ی فوری بعد از هر تغییر تنظیمات)
STATE_SAVE_INTERVAL_SECONDS = 30

# ---------- آمار فعالیت گروه ----------

TOP_ACTIVE_COUNT = 10

# ---------- سیستم دوست/دشمن (ترمیناتور) ----------

# با ریپلای زدن این کلمه روی پیام یه کاربر، اون کاربر «دشمن» علامت می‌خوره
ENEMY_TRIGGER_WORD = "دشمن"
# با ریپلای زدن این کلمه، کاربر از لیست دشمنان حذف می‌شه
ENEMY_REMOVE_WORD = "لغو دشمن"

# با ریپلای زدن این کلمه روی پیام یه کاربر، اون کاربر «دوست» علامت می‌خوره
FRIEND_TRIGGER_WORD = "دوست"
FRIEND_REMOVE_WORD = "لغو دوست"

# دستورات متنی برای روشن/خاموش کردن کل سیستم دوست/دشمن در یک گروه (فقط ادمین)
TERMINATOR_ON_WORD = "ترمیناتور روشن"
TERMINATOR_OFF_WORD = "ترمیناتور خاموش"

# نکته: پیش‌فرض روشن/خاموش بودن ترمیناتور دیگه اینجا نیست؛ توی بخش DEFAULT_SETTINGS پایین‌تر (کلید terminator_enabled) تعریف شده

# پیام‌هایی که به‌طور خودکار به هر پیام یه «دشمن» داده می‌شه (رندوم انتخاب می‌شه)
# نکته: چندتا از عبارت‌هایی که فرستادی (فحش رکیک، تهدید به دعوا، تحقیر) عمداً حذف شدن
ENEMY_REPLIES = [
    "ای کاش نمیومدی 😑",
    "عنکبوته مریض 🕷️",
    "احمق 😐",
    "یه دلقک پیام داد 🤡",
]

# پیام‌هایی که به‌طور خودکار به هر پیام یه «دوست» داده می‌شه (رندوم انتخاب می‌شه)
# نکته: عبارت «سکسی» عمداً حذف شد (نمی‌دونیم طرف چند سالشه، مناسب نیست)
FRIEND_REPLIES = [
    "خوش اومدی 😄",
    "گلم 🌸",
    "اومدی بویه گل اومد 🌷",
    "عزیزم❤️",
    "جون 😍",
    "چخبر 😊",
]

# ---------- ☠️ TERMINATOR 2.0: تنظیمات هوشمند ----------
# (این بخش روی همون سیستم دوست/دشمن بالا ساخته شده، نه جداگانه)

# حداقل فاصله بین دو پاسخ ترمیناتور به یه Target (ثانیه) - مگه اینکه خودِ Target کول‌داون
# اختصاصی داشته باشه (با /enemyinfo قابل دیدنه)
TERMINATOR_COOLDOWN_SECONDS = 8
# اگه توی این بازه‌ی زمانی (ثانیه) چندتا پیام پشت‌سرهم از Target بیاد، SPAM MODE فعال می‌شه
TERMINATOR_SPAM_WINDOW_SECONDS = 10
TERMINATOR_SPAM_MESSAGE_THRESHOLD = 4
# توی SPAM MODE، به‌جای کول‌داون عادی از این مقدار (ثانیه) استفاده می‌شه
TERMINATOR_SPAM_COOLDOWN_SECONDS = 30
# چندتا از آخرین پاسخ‌های هر Target نگه داشته بشه تا دوباره تکرار نشن
TERMINATOR_ANTI_REPEAT_COUNT = 5

# امتیاز Threat که هر نوع پیام بهش اضافه می‌کنه
TERMINATOR_SCORE_NORMAL_MESSAGE = 1
TERMINATOR_SCORE_RAPID_MESSAGE = 2   # پیام خیلی زود بعد از پیام قبلی همون Target
TERMINATOR_SCORE_FLOOD_MESSAGE = 5   # پیامی که توی SPAM MODE فرستاده بشه

# آستانه‌ی امتیاز Threat برای رسیدن به هر سطح (تجمعی؛ یعنی «این عدد یا بیشتر»)
TERMINATOR_THREAT_THRESHOLDS = {
    1: 0,
    2: 5,
    3: 12,
    4: 22,
    5: 35,  # ☠️ رسیدن به این سطح یعنی BOSS TARGET
}
TERMINATOR_LEVEL_LABELS = {
    1: "🟢 LOW",
    2: "🟡 MEDIUM",
    3: "🟠 HIGH",
    4: "🔴 EXTREME",
    5: "☠️ TERMINATOR",
}

TERMINATOR_RESPONSE_MODES = ["sarcastic", "funny", "cold", "smart", "roast", "random"]
TERMINATOR_MODE_LABELS = {
    "sarcastic": "😈 طعنه‌آمیز",
    "funny": "🤡 فان",
    "cold": "🗿 خشک و سنگین",
    "smart": "🧠 هوشمند",
    "roast": "🔥 روست سبک",
    "random": "🎲 تصادفی (ترکیبی)",
}
DEFAULT_TERMINATOR_MODE = "random"

# پاسخ بر اساس سطح Threat (شدت پیام‌ها هرچی بالاتر بره، از سطح بالاتر انتخاب می‌شه)
TERMINATOR_LEVEL_RESPONSES = {
    1: ["🙂 سلام، چیزی می‌خواستی؟", "😐 باز اومدی؟", "🙃 چه خبرا؟"],
    2: ["😏 باز تو؟", "😒 بازم پیدات شد", "😑 دوباره اینجایی؟"],
    3: ["🤡 دوباره پیدات شد؟", "🤨 بازم داری امتحان می‌کنی؟", "😤 هنوز دست از سرم برنداشتی"],
    4: ["☠️ هنوز دست‌بردار نیستی؟", "🔥 داری زیاده‌روی می‌کنی‌ها", "😤 اوضاع داره جدی می‌شه"],
    5: ["💀 ترمیناتور هنوز روشنه، چرا ادامه میدی؟", "☠️ رسیدی به آخر خط رفیق", "💀 دیگه واقعاً حرفی نمونده"],
}

# پاسخ بر اساس Response Mode انتخابی (روی پاسخ‌های بالا اضافه می‌شن، نه جایگزینشون)
TERMINATOR_MODE_RESPONSES = {
    "sarcastic": ["عجب... باز تو؟ 😏", "چه افتخاری که دوباره اومدی 🙄", "چقدر دلم برات تنگ نشده بود 😒"],
    "funny": ["هاها بازم تویی؟ 🤣", "دلقک گروه اومد 🤡", "شوی امروز رو تو اجرا می‌کنی؟ 🎪"],
    "cold": ["...", "باشه.", "متوجه شدم."],
    "smart": ["آماری که ازت دارم زیاد جالب نیست 🧠", "الگوی رفتاریت قابل پیش‌بینیه 🤓", "بازم همون کار همیشگی؟"],
    "roast": ["حداقل یه پیام باحال بفرست یه بار 😅", "کپی‌پیست جدید نداری؟ 😴", "همینا رو داری؟ 🥱"],
}

# پاسخ بر اساس نوع پیام (برای پیام‌های غیرمتنی - عکس/ویدیو/استیکر و...)
TERMINATOR_TYPE_RESPONSES = {
    "sticker": ["😐 استیکر فرستادن مشکلتو حل نمی‌کنه", "🎭 استیکر جالبی بود، ولی نه"],
    "photo": ["📸 عکس جالبیه، ولی هنوز دشمنی", "🖼 قشنگه، ولی موضوع عوض نمی‌شه"],
    "video": ["🎥 ویدیو نگاه نمی‌کنم", "🎬 حوصله‌ی ویدیوتو ندارم"],
    "animation": ["🎞 گیف باحالی بود، ولی بی‌فایده‌ست", "😑 گیف نمی‌تونه نجاتت بده"],
    "voice": ["🎤 ویس نمی‌شنوم، تایپ کن", "🔇 صداتو نمی‌خوام بشنوم"],
    "audio": ["🎵 آهنگ خوبی بود شاید، ولی بازم دشمنی", "🎧 موزیک نجاتت نمی‌ده"],
    "document": ["📄 فایلتو باز نمی‌کنم", "📎 نیازی به فایلت ندارم"],
    "location": ["📍 لوکیشن فرستادی؟ به من چه 😑", "🗺 کجایی به من ربطی نداره"],
    "live_location": ["📍 لوکیشن زنده؟ نگران نباش، دنبالت نمی‌کنم 😑", "🛰 لوکیشن زنده جالبه، ولی بی‌فایده‌ست"],
    "venue": ["📌 آدرس جالبیه، ولی به من چه 😐", "🏠 اونجا برو، فقط اینجا دست بردار"],
    "video_note": ["⭕ ویدیوی دایره‌ای نمی‌بینم", "🎥 اون فرمت خاص هم کمکت نمی‌کنه"],
    "poll": ["📊 نظرسنجی زدی؟ جوابشو خودت بده 😑", "🗳 رأی نمی‌دم، فقط برو"],
    "contact": ["📇 مخاطب فرستادی؟ به کارم نمیاد", "☎️ شماره‌تو نمی‌خوام"],
    "dice": ["🎲 تاس انداختی؟ شانست دیگه تموم شده 😏", "🎲 هر عددی بیاد بازم دشمنی"],
    "link": ["🔗 لینکتو باز نمی‌کنم", "⛔ لینک مشکوک، باز نمی‌کنم"],
}

# اگه متن پیام شامل یکی از این کلمه‌ها باشه، به‌جای پاسخ عمومی، یکی از این پاسخ‌های
# اختصاصی انتخاب می‌شه (بالاترین اولویت، حتی روی AI هم مقدمه)
TERMINATOR_TRIGGERS = {
    "سلام": ["سلام؟ 😂 باز اومدی اینجا؟", "سلام که چی بشه؟ 🙄"],
    "ربات": ["بله، خودمم؛ ولی سؤال اینه تو چرا هنوز اینجایی؟ 🤡", "آره منم، تو کار بهتری نداری؟ 😏"],
    "خوبی": ["من عالی‌ام، تو هنوز داری با ترمیناتور حرف می‌زنی 😂", "خوبم، تو چرا هنوز نرفتی؟ 😐"],
}

# پاسخ‌های ویژه‌ی Boss Target (سطح ۵ - وقتی is_boss فعال باشه، اینا هم به استخر انتخاب اضافه می‌شن)
TERMINATOR_BOSS_RESPONSES = [
    "☠️ به بالاترین سطح رسیدی. تبریک؟ 💀",
    "☠️ رسماً BOSS TARGET شدی، افتخار بزرگیه (نه) 🏆",
    "💀 دیگه سطحی برای رسیدن نمونده، همینجا بمون",
]

# سیستم پیام هوشمند (AI) مخصوص ترمیناتور - همون کلاینت Groq پروژه، ولی با یه شخصیت جدا
# (اگه AI در دسترس نباشه یا خطا بده، همیشه به پاسخ‌های محلی بالا برمی‌گرده - هیچ‌وقت Crash نمی‌کنه)
TERMINATOR_AI_SYSTEM_INSTRUCTION = (
    "تو بخش طنز و طعنه‌ی یه ربات مدیریت گروه تلگرام به اسم دیجی‌انتی هستی. یه پیام کوتاه، "
    "بامزه و طعنه‌آمیز (حداکثر ۲۰ کلمه) به فارسی محاوره‌ای برای یه کاربر «دشمن» تعریف‌شده "
    "توی گروه بنویس، بر اساس پیامی که فرستاده. لحن باید فان و کنایه‌آمیز باشه، نه توهین رکیک، "
    "تهدید واقعی، خشونت یا محتوای نامناسب. فقط خودِ پاسخ رو بنویس، بدون توضیح اضافه."
)
TERMINATOR_AI_MAX_TOKENS = 60
TERMINATOR_AI_TEMPERATURE = 0.9
# سقف قطعیِ زمان پاسخ AI ترمیناتور (ثانیه) - جدا و همیشه کوچیک‌تر/مساوی AI_TIMEOUT_SECONDS.
# این سقف با asyncio.wait_for روی خودِ Task اعمال می‌شه، پس حتی اگه کتابخانه‌ی Groq به هر
# دلیلی (DNS/شبکه/باگ) به تایم‌اوت داخلیش پایبند نمونه، بازم بعد از این مدت قطع می‌شه و
# به پاسخ محلی برمی‌گرده - دقیقاً همون چیزی که جلوی «تاخیر چند دقیقه‌ای» رو می‌گیره.
TERMINATOR_AI_HARD_TIMEOUT_SECONDS = 6

# ---------- ☠️ TERMINATOR: اقدام خودکار در سطح‌های بالا ----------
# از این سطح به بعد، علاوه بر پاسخ کنایه‌آمیز، یه اقدام مدیریتی واقعی هم اجرا می‌شه.
# مقادیر مجاز: "warn" یا "ban" (یا نبودن کلید = بدون اقدام خودکار در اون سطح)
# توجه: چون سطح‌ها تجمعی‌ان (سطح ۵ شامل ۴ هم می‌شه)، وقتی Target به سطح ۵ برسه هم اخطارِ
# سطح ۴ (اگه هنوز نگرفته) و هم بن سطح ۵ به ترتیب اعمال می‌شن.
TERMINATOR_LEVEL_ACTIONS = {
    4: "warn",
    5: "ban",
}
# دلیلی که برای اخطار/بن خودکار توی لاگ مدیریت و پیام گروه ثبت می‌شه
TERMINATOR_ACTION_REASON = "سیستم ترمیناتور: رسیدن به سطح تهدید {level} ({label})"

# ---------- ضداسپم پیشرفته ----------

# اگه عضوی کمتر از این مدت (به دقیقه) عضو گروه شده باشه و لینک بفرسته، اسپم شناخته می‌شه
NEW_MEMBER_GRACE_MINUTES = 10

# آستانه‌ی تشخیص کاپس‌لاک (داد زدن با حروف بزرگ لاتین)
CAPS_MIN_LENGTH = 10          # حداقل طول پیام برای بررسی
CAPS_RATIO_THRESHOLD = 0.7    # حداقل نسبت حروف بزرگ به کل حروف بابدار

# ضد فلود استیکر/گیف پشت سر هم
MEDIA_FLOOD_LIMIT_MESSAGES = 4
MEDIA_FLOOD_LIMIT_SECONDS = 10

# ---------- تگ دسته‌جمعی اعضا ----------

# دستور /tag یا نوشتن این عبارت، همه‌ی اعضایی که ربات تا الان دیده رو تگ می‌کنه
TAG_ALL_TRIGGER_WORD = "تگ همه"

# هر بار چند نفر توی یه پیام تگ بشن (برای جلوگیری از پیام‌های خیلی بلند و محدودیت فلود تلگرام)
TAG_BATCH_SIZE = 5

# فاصله‌ی زمانی بین ارسال دسته‌های تگ (ثانیه) - برای رعایت محدودیت نرخ ارسال تلگرام
TAG_BATCH_DELAY_SECONDS = 0.4

# ---------- تنظیمات قابل روشن/خاموش کردن هر گروه (با دستور /menu) ----------

# مقدار پیش‌فرض هر قابلیت روشن/خاموش (بولی) برای گروه‌های جدید — همه چیز پیش‌فرض خاموشه
DEFAULT_SETTINGS = {
    "bot_enabled": False,          # سوییچ کلی ربات - وقتی خاموشه، ربات کاملاً بی‌صداست
    "welcome_enabled": False,
    "keyword_reply": False,
    "antispam_words": False,
    "antiflood_text": False,
    "anticaps": False,
    "antisticker_flood": False,
    "lock_all_stickers": False,    # برخلاف antisticker_flood، این حتی یه استیکر تنها رو هم حذف می‌کنه
    "lock_all_gifs": False,        # برخلاف antisticker_flood، این حتی یه گیف تنها رو هم حذف می‌کنه
    "lock_tags": False,            # حذف پیام‌هایی که یوزرنیم (@کسی) توشونه
    "lock_hashtags": False,        # حذف پیام‌هایی که هشتگ (#چیزی) توشونه
    "lock_contacts": False,        # حذف اشتراک‌گذاری مخاطب (کارت مخاطب)
    "lock_location": False,        # حذف اشتراک‌گذاری موقعیت مکانی
    "lock_media": False,           # حذف همه‌ی عکس/ویدیو/فایل/صدا (سخت‌گیرانه)
    "lock_bots": False,            # اخراج خودکار هر ربات دیگه‌ای که به گروه اضافه بشه
    "lock_edited": False,          # حذف پیام‌هایی که ویرایش شدن
    "lock_group": False,           # فقط ادمین‌ها بتونن پیام بدن (گروه بسته)
    "antiforward_channel": False,
    "antinew_account_links": False,
    "captcha_enabled": False,      # ضد ربات‌های اسپمی: عضو جدید باید دکمه بزنه وگرنه اخراج می‌شه
    "stats_enabled": False,
    "terminator_enabled": False,
    "terminator_ai_enabled": False,  # پاسخ‌های هوشمند AI ترمیناتور (اضافه روی پاسخ‌های محلی، نه جایگزینشون)
    "tag_members_enabled": False,  # اگه خاموش باشه، /tag و «تگ همه» کار نمی‌کنن حتی برای ادمین مجاز
    "xp_enabled": False,           # سیستم XP و Level و پروفایل کاربر
    "economy_enabled": False,      # اقتصاد داخلی گروه (سکه، روزانه، فروشگاه)
    "poll_enabled": False,         # نظرسنجی با دکمه‌ی شیشه‌ای (/poll)
    "leave_enabled": False,        # پیام ترک گروه (اطلاع وقتی عضوی می‌ره/اخراج می‌شه)

    # ---------- فاز ۱۰: Toggle مستقل هر ماژول Economy (زیرمجموعه‌ی economy_enabled) ----------
    # این‌ها فقط وقتی economy_enabled روشن باشه معنا دارن؛ پیش‌فرض همه True (روشن) تا
    # روشن‌کردن economy_enabled رفتار قبلی رو حفظ کنه و چیزی خاموش نشه.
    "economy_module_jobs": True,
    "economy_module_bank": True,
    "economy_module_market": True,
    "economy_module_theft": True,
    "economy_module_city": True,
    "economy_module_property": True,
    "economy_module_district": True,
    "economy_module_vehicle": True,
    "economy_module_business": True,
    "economy_module_marriage": True,
    "economy_module_pet": True,
    "economy_module_games": True,
    "economy_module_blackmarket": True,
    "economy_module_underground": True,

    # ---------- DIGIANTI SMART LOCK ENGINE 2.0 (کلیدهای جدید) ----------
    # این‌ها علاوه بر قفل‌های قبلی (lock_all_stickers/lock_tags/...) هستن که بالاتر تعریف
    # شدن و دست‌نخورده باقی موندن؛ موتور قفل جدید (lock_engine.py) از هردو دسته استفاده می‌کنه.
    "lock_links": False,           # لینک هوشمند (با وایت‌لیست/بلک‌لیست دامنه) - جایگزین BLOCK_LINKS قدیمی
    "lock_photo": False,
    "lock_video": False,
    "lock_document": False,
    "lock_audio": False,
    "lock_voice": False,
    "lock_forward": False,         # فوروارد عادی (غیر از فوروارد کانال که antiforward_channel داره)
    "lock_poll": False,
    "lock_game": False,
    "lock_text": False,            # قفل کامل متن (فقط رسانه/دستور مجازه)
    "lock_long_message": False,
    "lock_inline": False,          # پیام‌هایی که با via_bot (اینلاین) ارسال شدن

    # ---------- DIGIANTI SMART ANTI-SPAM & ANTI-BOT ENGINE 3.0 (کلیدهای جدید) ----------
    "antispam_duplicate": False,     # تشخیص پیام/رسانه‌ی تکراری (Copy/Paste Spam)
    "antispam_mentions": False,      # منشن زیاد در یه پیام / پشت‌سرهم (فرق داره با lock_tags که قفل کامله)
    "antispam_hashtags": False,      # هشتگ بیش‌ازحد/تکراری
    "antispam_emoji": False,         # ایموجی بیش‌ازحد
    "antispam_ads": False,           # تشخیص تبلیغات (کلمه+الگو+لینک+رفتار)
    "antispam_forward_flood": False, # فوروارد پشت‌سرهم
    "antispam_mixed_flood": False,   # فلود ترکیبی (چند نوع محتوا)
    "antispam_link_flood": False,    # لینک پشت‌سرهم (فرق داره با lock_links که قفل کامله)
    "antispam_edit": False,          # ادیت مکرر برای دورزدن سیستم
    "antispam_photo_flood": False,
    "antispam_video_flood": False,
    "antispam_voice_flood": False,
    "antispam_audio_flood": False,
    "antispam_document_flood": False,
    "antispam_contact_flood": False,
    "antispam_location_flood": False,
    "antispam_poll_flood": False,
    "member_protection_enabled": False,   # New Member Protection (بند ۱۰)
    "member_protection_level": "NORMAL",  # LOW | NORMAL | HIGH | STRICT
}

# برچسب فارسی هر قابلیت بولی که توی منو نشون داده می‌شه
SETTING_LABELS = {
    "bot_enabled": "🤖 ربات (سوییچ کلی)",
    "welcome_enabled": "خوش‌آمدگویی اعضای جدید",
    "keyword_reply": "پاسخ خودکار کلیدواژه‌ای",
    "antispam_words": "فیلتر کلمات ممنوعه/لینک",
    "antiflood_text": "ضد فلود متن",
    "anticaps": "ضد کاپس‌لاک (داد زدن)",
    "antisticker_flood": "ضد فلود استیکر/گیف",
    "lock_all_stickers": "قفل کامل استیکر",
    "lock_all_gifs": "قفل کامل گیف",
    "lock_tags": "قفل تگ (@یوزرنیم)",
    "lock_hashtags": "قفل هشتگ (#)",
    "lock_contacts": "قفل اشتراک مخاطب",
    "lock_location": "قفل موقعیت مکانی",
    "lock_media": "قفل رسانه (عکس/ویدیو/فایل)",
    "lock_bots": "قفل ربات (اخراج ربات‌های دیگه)",
    "lock_edited": "قفل ویرایش پیام",
    "lock_group": "بستن گروه (فقط ادمین حرف بزنه)",
    "antiforward_channel": "بلاک فوروارد از کانال",
    "antinew_account_links": "بلاک لینک از اعضای تازه‌وارد",
    "captcha_enabled": "تایید عضو جدید (ضد ربات اسپمی)",
    "stats_enabled": "ثبت آمار فعالیت",
    "terminator_enabled": "سیستم دوست/دشمن",
    "terminator_ai_enabled": "🧠 پاسخ هوشمند AI ترمیناتور",
    "tag_members_enabled": "🏷️ فعال بودن تگ دسته‌جمعی",
    "xp_enabled": "🎮 سیستم XP، Level و پروفایل",
    "economy_enabled": "💰 اقتصاد داخلی گروه (سکه/فروشگاه)",
    "poll_enabled": "🗳️ نظرسنجی با دکمه‌ی شیشه‌ای",
    "leave_enabled": "👋 پیام ترک گروه",
    "lock_links": "🔗 قفل لینک (هوشمند)",
    "lock_photo": "🖼 قفل عکس",
    "lock_video": "🎥 قفل ویدیو",
    "lock_document": "📄 قفل فایل/سند",
    "lock_audio": "🎵 قفل صدا (آهنگ)",
    "lock_voice": "🎤 قفل ویس",
    "lock_forward": "🔁 قفل فوروارد",
    "lock_poll": "📊 قفل نظرسنجی",
    "lock_game": "🎮 قفل بازی/تاس",
    "lock_text": "💬 قفل کامل متن",
    "lock_long_message": "🔢 قفل پیام طولانی",
    "lock_inline": "📨 قفل محتوای اینلاین",
    "antispam_duplicate": "📨 ضد پیام/رسانه‌ی تکراری",
    "antispam_mentions": "👥 ضد منشن‌اسپم",
    "antispam_hashtags": "#️⃣ ضد هشتگ‌اسپم",
    "antispam_emoji": "😂 ضد ایموجی‌اسپم",
    "antispam_ads": "📢 تشخیص تبلیغات",
    "antispam_forward_flood": "🔁 ضد فوروارد پشت‌سرهم",
    "antispam_mixed_flood": "📦 ضد فلود ترکیبی",
    "antispam_link_flood": "🔗 ضد لینک پشت‌سرهم",
    "antispam_edit": "🔄 ضد ادیت‌اسپم",
    "antispam_photo_flood": "🖼 ضد فلود عکس",
    "antispam_video_flood": "🎥 ضد فلود ویدیو",
    "antispam_voice_flood": "🎤 ضد فلود ویس",
    "antispam_audio_flood": "🎵 ضد فلود صدا",
    "antispam_document_flood": "📄 ضد فلود فایل",
    "antispam_contact_flood": "👤 ضد فلود مخاطب",
    "antispam_location_flood": "📍 ضد فلود موقعیت",
    "antispam_poll_flood": "📊 ضد فلود نظرسنجی",
    "member_protection_enabled": "👶 محافظت عضو تازه‌وارد",
}

# دسته‌بندی قابلیت‌ها برای پنل چندلایه (/menu). هر دسته یه زیرمنوی جدا می‌شه.
SETTING_CATEGORIES = {
    "core": {"title": "🤖 هسته‌ی ربات", "keys": ["bot_enabled", "stats_enabled", "captcha_enabled"]},
    "welcome": {"title": "👋 ورود و خوش‌آمدگویی", "keys": ["welcome_enabled", "leave_enabled"]},
    "reply": {"title": "💬 پاسخ‌دهی", "keys": ["keyword_reply"]},
    "locks": {
        "title": "🔒 قفل‌ها",
        "keys": [
            "antispam_words",
            "lock_links",
            "lock_tags",
            "lock_hashtags",
            "lock_contacts",
            "lock_location",
            "lock_media",
            "lock_photo",
            "lock_video",
            "lock_document",
            "lock_audio",
            "lock_voice",
            "lock_all_stickers",
            "lock_all_gifs",
            "lock_forward",
            "lock_poll",
            "lock_game",
            "lock_text",
            "lock_long_message",
            "lock_inline",
            "lock_bots",
            "lock_edited",
            "lock_group",
        ],
    },
    "antispam": {
        "title": "🛡️ ضداسپم و ضدربات",
        "keys": [
            "antiflood_text",
            "anticaps",
            "antisticker_flood",
            "antiforward_channel",
            "antinew_account_links",
            "antispam_duplicate",
            "antispam_mentions",
            "antispam_hashtags",
            "antispam_emoji",
            "antispam_ads",
            "antispam_forward_flood",
            "antispam_mixed_flood",
            "antispam_link_flood",
            "antispam_edit",
            "antispam_photo_flood",
            "antispam_video_flood",
            "antispam_voice_flood",
            "antispam_audio_flood",
            "antispam_document_flood",
            "antispam_contact_flood",
            "antispam_location_flood",
            "antispam_poll_flood",
            "member_protection_enabled",
        ],
    },
    "moderation": {"title": "⚠️ اخطار / میوت / بن", "keys": []},
    "relationships": {"title": "⚔️ دوست/دشمن", "keys": ["terminator_enabled", "terminator_ai_enabled"]},
    "tagging": {"title": "🏷️ تگ دسته‌جمعی اعضا", "keys": ["tag_members_enabled"]},
    "profile": {"title": "👤 پروفایل، XP و لقب", "keys": ["xp_enabled"]},
    "economy": {"title": "💰 اقتصاد و نظرسنجی", "keys": ["economy_enabled", "poll_enabled"]},
    "econ_admin": {"title": "🛠 پنل ویژه ادمین (سکه/XP/قرعه‌کشی)", "keys": []},
}

# مدت زمانی که عضو جدید برای تایید (زدن دکمه) وقت داره؛ اگه نزنه، اخراج می‌شه (به دقیقه)
CAPTCHA_TIMEOUT_MINUTES = 5

# متن پیام تایید عضو جدید. {name} با نامش جایگزین می‌شه
CAPTCHA_MESSAGE = (
    "سلام {name}! برای اینکه مطمئن بشیم ربات یا اسپمر نیستی، لطفاً دکمه‌ی زیر رو بزن.\n"
    "اگه تا {minutes} دقیقه دیگه تایید نکنی، از گروه اخراج می‌شی."
)

# کلماتی که با نوشتنشون (بدون نیاز به ریپلای) ربات کلاً روشن/خاموش می‌شه
BOT_ON_WORD = "ربات روشن"
BOT_OFF_WORD = "ربات خاموش"

# ---------- ساند افکت روشن‌شدن (فقط «ربات روشن» و «ترمیناتور روشن») ----------
# مسیر فایل MP3 که بعد از روشن شدن موفق ربات فرستاده می‌شه. فقط کافیه فایل رو
# توی همین مسیر جایگزین کنی؛ نیازی به تغییر کد نیست. اگه فایل وجود نداشت یا
# ارسالش خطا داد، فقط توی لاگ ثبت می‌شه و روشن شدن ربات Fail نمی‌شه.
BOT_ON_SOUND = "sounds/bot_on.mp3"

# مسیر فایل MP3 که بعد از فعال شدن موفق سیستم ترمیناتور فرستاده می‌شه (مستقل از BOT_ON_SOUND).
TERMINATOR_ON_SOUND = "sounds/terminator_on.mp3"

# ---------- روشن/خاموش کردن هر قابلیت با نوشتن متن فارسی (بدون نیاز به /menu) ----------
# هر کلید از DEFAULT_SETTINGS یه کلیدواژه‌ی فارسی داره. با نوشتن «<کلیدواژه> روشن» یا
# «<کلیدواژه> خاموش» (دقیقاً همین متن، بدون نیاز به دستور)، اون قابلیت روشن/خاموش می‌شه.
# نکته: bot_enabled و terminator_enabled کلیدواژه‌ی اختصاصی خودشون رو دارن (بالاتر:
# BOT_ON_WORD/BOT_OFF_WORD و TERMINATOR_ON_WORD/TERMINATOR_OFF_WORD) و اینجا تکرار نشدن.
SETTING_KEYWORDS = {
    "welcome_enabled": "خوشامد",
    "keyword_reply": "پاسخ خودکار",
    "antispam_words": "فیلتر کلمات",
    "antiflood_text": "ضدفلود",
    "anticaps": "ضدکاپس",
    "antisticker_flood": "ضدفلود استیکر",
    "lock_all_stickers": "قفل استیکر",
    "lock_all_gifs": "قفل گیف",
    "lock_tags": "قفل تگ",
    "lock_hashtags": "قفل هشتگ",
    "lock_contacts": "قفل مخاطب",
    "lock_location": "قفل موقعیت",
    "lock_media": "قفل رسانه",
    "lock_bots": "قفل ربات",
    "lock_edited": "قفل ویرایش",
    "lock_group": "قفل گروه",
    "antiforward_channel": "قفل فوروارد",
    "antinew_account_links": "قفل لینک تازه‌وارد",
    "captcha_enabled": "تاییدیه عضو",
    "stats_enabled": "ثبت آمار",
    "tag_members_enabled": "تگ دسته‌جمعی",
    "xp_enabled": "ایکسپی",
    "economy_enabled": "اقتصاد",
    "poll_enabled": "نظرسنجی",
    "leave_enabled": "پیام لفت",
}

# ---------- حالت پاسخ هوش مصنوعی (چندحالته، جدا از تنظیمات بولی بالا) ----------
# با دکمه توی /menu این حالت‌ها به ترتیب عوض می‌شن:
#   off          -> هوش مصنوعی خاموشه (پیش‌فرض)
#   all          -> به همه (ادمین و غیرادمین) جواب می‌ده
#   non_admins   -> فقط به کاربرای غیرادمین جواب می‌ده
#   admins_only  -> فقط به ادمین‌ها جواب می‌ده
AI_REPLY_MODE_DEFAULT = "off"

AI_REPLY_MODE_ORDER = ["off", "all", "non_admins", "admins_only"]

AI_REPLY_MODE_LABELS = {
    "off": "❌ پاسخ هوش مصنوعی: خاموش",
    "all": "✅ پاسخ هوش مصنوعی: همه",
    "non_admins": "👤 پاسخ هوش مصنوعی: فقط غیرادمین‌ها",
    "admins_only": "👑 پاسخ هوش مصنوعی: فقط ادمین‌ها",
}


# ---------- امکانات جانبی/سرگرمی (با هوش مصنوعی تولید می‌شن، نیاز به کلید یا API جدا ندارن) ----------

AI_JOKE_PROMPT = "یه جوک کوتاه و بامزه‌ی فارسی بگو. فقط خود جوک، بدون مقدمه."
AI_FORTUNE_PROMPT = (
    "به سبک فال حافظ، یه بیت شعر فارسی (واقعی یا در همون حال‌وهوا) به همراه یه "
    "تعبیر کوتاه و مثبت بنویس. فضای شاعرانه و رازآلود داشته باش."
)


# ================= افزودنی‌های حرفه‌ای (پروفایل / XP / لقب / لاگ / ضد رید) =================

# آیدی عددی کانال لاگ مدیریتی (اختیاری). اگه پر بشه، اکشن‌های مدیریتی مهم
# (بن/آن‌بن/میوت/آن‌میوت/اخطار/ارتقا/عزل/پاک‌سازی/رید) به این کانال هم فرستاده می‌شن.
# برای گرفتن آیدی: ربات رو ادمین کانال کن، یه پیام بفرست و با @RawDataBot یا
# @userinfobot آیدیش رو بگیر (یه عدد منفی طولانی، مثلاً -1001234567890). اگه None
# بمونه، لاگ مدیریتی خاموشه.
ADMIN_LOG_CHANNEL_ID = -1004438812702

# ---------- سیستم XP و Level (روشن/خاموش‌ش با /menu یا نوشتن «ایکسپی روشن/خاموش») ----------
XP_PER_MESSAGE_MIN = 1
XP_PER_MESSAGE_MAX = 3
XP_MESSAGE_COOLDOWN_SECONDS = 15   # حداقل فاصله بین دو پیامی که XP می‌گیرن (ضدفارم‌کردن XP با اسپم)
XP_PER_LEVEL = 100                  # هر Level چقدر XP لازم داره (خطی و ساده)

# لقب خودکار بر اساس Level (بزرگ‌ترین آستانه‌ای که Level کاربر ≥ اونه انتخاب می‌شه)
LEVEL_AUTO_TITLES = {
    0: "🌱 تازه‌وارد",
    5: "🌿 فعال",
    10: "⭐ باتجربه",
    20: "🔥 حرفه‌ای",
    35: "💎 نخبه",
    50: "👑 افسانه‌ای",
}

TOP_XP_COUNT = 10

# ---------- 📊 سیستم فعالیت هوشمند (Activity System) - روی XP/آمار فعلی ساخته شده ----------
# وزن هر نوع پیام برای «Activity Score» (جدا از XP). کلیدها همون خروجی تابع تشخیص نوع پیامن
# (که سیستم ترمیناتور هم ازش استفاده می‌کنه - یه‌جا تعریف شده، تکراری نیست)
ACTIVITY_SCORE_WEIGHTS = {
    "text": 1,
    "photo": 2,
    "video": 2,
    "video_note": 2,
    "voice": 2,
    "audio": 2,
    "sticker": 1,
    "animation": 2,
    "document": 2,
    "location": 1,
    "live_location": 1,
    "venue": 1,
    "contact": 1,
    "poll": 1,
    "dice": 1,
    "link": 1,
    "other": 1,
}
# ضدسوءاستفاده: حداقل فاصله بین دو باری که Activity Score می‌گیره (جدا از کول‌داون XP بالا)
ACTIVITY_SCORE_COOLDOWN_SECONDS = 20
ACTIVITY_DAILY_BONUS = 5              # جایزه‌ی اولین پیام هر روز
ACTIVITY_STREAK_BONUS_PER_DAY = 1     # به‌ازای هر روز Streak، این مقدار هم به جایزه‌ی روزانه اضافه می‌شه
ACTIVITY_STREAK_BONUS_CAP = 20        # سقف جایزه‌ی Streak (که زیادی زیاد نشه)

# آستانه‌ها برای وضعیت اعضا (چند روز از آخرین فعالیتشون گذشته)
ACTIVITY_LOW_THRESHOLD_DAYS = 7        # بین این تا INACTIVE = 🟡 کم‌فعالیت
ACTIVITY_INACTIVE_THRESHOLD_DAYS = 30  # بیشتر از این = 🔴 غیرفعال

ACTIVITY_HISTORY_RETENTION_DAYS = 90   # آمار روزانه‌ی خام قدیمی‌تر از این پاک می‌شه (آمار کلی/XP دست‌نخورده می‌مونه)
ACTIVITY_HISTORY_LOG_SIZE = 15         # چندتا از آخرین فعالیت‌ها برای کارت /activity نگه داشته بشه

ACTIVITY_LEADERBOARD_SIZE = 10

# هدف روزانه/هفتگی برای نوار پیشرفت توی کارت /activity (قابل تنظیم)
ACTIVITY_DAILY_GOAL_MESSAGES = 20
ACTIVITY_WEEKLY_GOAL_XP = 1000

# دستاوردهای مبتنی بر آستانه (یک‌بار Unlock می‌شن و ذخیره می‌مونن). دستاوردهای Top1/Top10 و
# Night Owl/Early Bird چون به رتبه/الگوی رفتاری نیاز دارن، این‌جا نیستن و در لحظه محاسبه می‌شن.
ACTIVITY_ACHIEVEMENTS = {
    "msg_1": {"label": "💬 اولین پیام", "metric": "messages", "threshold": 1},
    "msg_100": {"label": "💬 صد پیام", "metric": "messages", "threshold": 100},
    "msg_1000": {"label": "💬 هزار پیام", "metric": "messages", "threshold": 1000},
    "msg_10000": {"label": "💬 ده‌هزار پیام", "metric": "messages", "threshold": 10000},
    "streak_3": {"label": "🔥 سه روز پیاپی", "metric": "streak", "threshold": 3},
    "streak_7": {"label": "🔥 هفت روز پیاپی", "metric": "streak", "threshold": 7},
    "streak_30": {"label": "🔥 سی روز پیاپی", "metric": "streak", "threshold": 30},
    "streak_100": {"label": "🔥 صد روز پیاپی", "metric": "streak", "threshold": 100},
    "voice_master": {"label": "🎤 استاد ویس", "metric": "voice", "threshold": 50},
    "media_master": {"label": "🖼 استاد رسانه", "metric": "media", "threshold": 200},
    "hyper_active": {"label": "⚡ فوق‌فعال", "metric": "score", "threshold": 5000},
    # --- فاز ۱ (Profile Engine) - افزوده به همین سیستم فعلی، نه یه سیستم جدا ---
    "level_10": {"label": "⭐ رسیدن به Level 10", "metric": "level", "threshold": 10},
    "level_25": {"label": "⭐ رسیدن به Level 25", "metric": "level", "threshold": 25},
    "level_50": {"label": "👑 رسیدن به Level 50", "metric": "level", "threshold": 50},
    "rich_1000": {"label": "💰 کاربر پولدار (۱۰۰۰ سکه)", "metric": "coins", "threshold": 1000},
}

# ---------- لقب اختصاصی کاربران (با /setlqab یا نوشتن «تنظیم لقب ...») ----------
CUSTOM_TITLE_MAX_LENGTH = 24
CUSTOM_TITLE_BANNED_WORDS = list(BANNED_WORDS)

# ---------- اخطار حرفه‌ای ----------
# هر اخطار بعد از این مدت (ساعت) خودش منقضی می‌شه و دیگه توی شمارش حساب نمی‌شه
PRO_WARNING_VALIDITY_HOURS = 24

# ---------- ضد لینک هوشمندتر (علاوه بر معافیت خودکار ادمین/VIP که از قبل هست) ----------
# لینک/دامنه‌هایی که همیشه مجازن، حتی وقتی antispam_words روشنه (مثلاً لینک کانال خودت)
LINK_WHITELIST_DOMAINS = []   # مثلاً ["t.me/mychannel", "mysite.com"]

# اگه >۰ باشه: عضوهایی که بیشتر از این مدت (دقیقه) توی گروه بودن، فرستادن لینک براشون آزاد می‌شه
LINK_ALLOWED_FOR_OLD_MEMBERS_MINUTES = 0

# ---------- ضد رید (اگه توی یه بازه‌ی کوتاه، عضو جدید زیادی وارد بشه) ----------
RAID_WINDOW_SECONDS = 30            # بازه‌ی زمانی بررسی
RAID_JOIN_THRESHOLD = 15            # چندتا عضو جدید توی همون بازه یعنی رید
RAID_MODE_DURATION_MINUTES = 15     # حالت رید (قفل خودکار تاییدیه‌ی عضو + قفل لینک) چقدر فعال می‌مونه
RAID_RESTRICT_NEW_JOINS = True      # توی حالت رید، عضو جدید بلافاصله محدود (فقط خواندن) بشه تا ادمین تصمیم بگیره

# ---------- DIGIANTI SMART ANTI-SPAM & ANTI-BOT ENGINE 3.0 ----------
SPAM_ENGINE_STATE_FILE = "spam_engine_state.json"
LOCK_ENGINE_STATE_FILE = "lock_engine_state.json"

MAX_MENTIONS_PER_MESSAGE = 3
MAX_MENTIONS_PER_MINUTE = 8
MAX_HASHTAGS = 5
MAX_EMOJIS = 8
MAX_EMOJI_RATIO = 0.5                    # نسبت ایموجی به کل طول پیام
DUPLICATE_SIMILARITY_THRESHOLD = 0.88    # ۰ تا ۱ - هرچی نزدیک‌تر به ۱، سخت‌گیرتر روی «تقریباً یکسان»

# کلمات/الگوهای رایج تبلیغاتی (کاملاً قابل توسعه؛ Advertisement Detection علاوه بر این‌ها
# از الگو/لینک/فراوانی/شباهت هم استفاده می‌کنه، نه فقط این لیست)
AD_KEYWORDS = [
    "تبلیغ", "تبلیغات", "خرید فالوور", "فروش", "عضو شوید", "عضو شو", "لینک زیر",
    "کانال ما", "ربات ما", "سابسکرایب", "دنبال کن", "ممبر ارزان", "افزایش ممبر",
    "subscribe", "join now", "buy now", "discount", "for sale", "click here",
    "join our channel", "join our group", "check my channel", "check out my",
    "promo code", "referral", "free followers", "earn money", "make money fast",
]

# ---------- اقتصاد داخلی گروه (روشن/خاموش‌ش با /menu، بخش «اقتصاد و نظرسنجی») ----------
# نکته: مقادیر MIN/MAX/DEFAULT پایین فقط «مقدار پیش‌فرض اولیه» هستن. ادمین می‌تونه با
# دستور /seteco یا از پنل ویژه‌ی ادمین (/menu → «پنل ویژه ادمین»)، برای هر گروه جداگانه
# این مقادیر رو بدون دست‌زدن به این فایل عوض کنه (توی bot_state.json ذخیره می‌مونه).
CURRENCY_NAME = "سکه"
CURRENCY_EMOJI = "🪙"

DAILY_REWARD_MIN = 20
DAILY_REWARD_MAX = 50
DAILY_COOLDOWN_HOURS = 24

# جایزه‌ی فعالیت: هر پیام (با همون کول‌داون XP) این‌قدر هم سکه می‌ده
ACTIVITY_COIN_MIN = 1
ACTIVITY_COIN_MAX = 2

# فروشگاه گروه: کد آیتم -> مشخصات. با /shop نمایش داده می‌شه و با /buy <کد> خریداری می‌شه.
# هر آیتم فعلاً یه «بج» (badge) هست که توی پروفایل کاربر نشون داده می‌شه.
SHOP_ITEMS = {
    "badge_star": {"name": "🌟 بج ستاره", "price": 100, "emoji": "🌟"},
    "badge_fire": {"name": "🔥 بج آتیش", "price": 200, "emoji": "🔥"},
    "badge_crown": {"name": "👑 بج تاج", "price": 300, "emoji": "👑"},
    "badge_diamond": {"name": "💎 بج الماس", "price": 500, "emoji": "💎"},
}

# ---------- نظرسنجی (روشن/خاموش با /menu یا نوشتن «نظرسنجی روشن/خاموش») ----------
POLL_MAX_OPTIONS = 8

# ---------- قرعه‌کشی (فقط ادمین‌های مجاز، نیازی به روشن/خاموش‌کردن نداره) ----------
LOTTERY_DEFAULT_MINUTES = 5

# ═══════════════════════════════════════════════════════════════════════════
# 🛡️ MODERATION ENGINE — تنظیمات موتور مدیریت حرفه‌ای (Warn/Mute/Ban/Risk)
# ═══════════════════════════════════════════════════════════════════════════
# همه‌ی مقادیر این بخش کاملاً اختیاری‌ان (اگه حذفشون کنی، moderation_engine.py
# از یه مقدار پیش‌فرض امن استفاده می‌کنه و ربات کرش نمی‌کنه). این بخش کاملاً
# اضافه‌ست و به تنظیمات قبلی (WARNING_LIMIT_BEFORE_MUTE و ...) دست نمی‌زنه؛
# فقط موتور جدید این‌ها رو هم به‌عنوان مرحله‌ی اول Ladder در نظر می‌گیره.

# فایل ذخیره‌سازی مستقل موتور مدیریت (مثل lock_engine_state.json / spam_engine_state.json)
MODERATION_ENGINE_STATE_FILE = "moderation_engine_state.json"

# نردبان تصاعدی «تخلف» که هم برای Warn دستی و هم برای تخلفات خودکار (اسپم/قفل) استفاده می‌شه.
# هر آیتم یعنی: «تا این شماره‌تخلف، این اکشن». آخرین آیتم برای همه‌ی موارد بعدش هم اجرا می‌شه.
# action: "warn" (فقط ثبت) / "mute" / "ban"
MODERATION_ESCALATION_LADDER = [
    {"count": 1, "action": "warn"},
    {"count": 2, "action": "warn"},
    {"count": 3, "action": "mute", "minutes": 10},
    {"count": 4, "action": "mute", "minutes": 60},
    {"count": 5, "action": "mute", "minutes": 360},
    {"count": 6, "action": "ban", "days": 0},   # 0 = دائمی
]

# مدت‌های Mute تصاعدی وقتی کاربر با /mute (بدون مدت مشخص) چند بار پشت‌سرهم میوت بشه (به دقیقه)
MODERATION_MUTE_LADDER_MINUTES = [5, 30, 120, 720, 1440]

# مدت‌های Ban تصاعدی وقتی کاربر با /ban (بدون مدت مشخص) چند بار پشت‌سرهم بن بشه (به روز، 0 = دائمی)
MODERATION_BAN_LADDER_DAYS = [7, 30, 0]

# آستانه‌های Risk Score برای برچسب‌گذاری (0 تا 100)
MODERATION_RISK_THRESHOLDS = {"LOW": 20, "MEDIUM": 50, "HIGH": 75, "CRITICAL": 100}

# وزن هر نوع رویداد در محاسبه‌ی Risk Score
MODERATION_RISK_WEIGHTS = {
    "warn": 8, "mute": 18, "ban": 35, "kick": 15,
    "spam_violation": 5, "lock_violation": 4, "captcha_fail": 6, "raid": 25,
}

# کاهش خودکار سابقه‌ی تخلف (Offense Decay): روزهای بدون تخلف -> چند «پله» سطح تخلف/ریسک کم بشه
MODERATION_DECAY_SCHEDULE = {30: 1, 60: 2, 90: 4}

# حذف خودکار پیام‌ها بعد از X ثانیه (0 یا None = حذف نشه)
MODERATION_AUTODELETE_SECONDS = {"warn": 20, "mute": 20, "ban": 20, "unmute": 15, "unban": 15}

# دلایل استاندارد قابل انتخاب برای Warn/Mute/Ban (ادمین می‌تونه دلیل دلخواه هم بنویسه)
MODERATION_STANDARD_REASONS = [
    "SPAM", "FLOOD", "ADVERTISING", "LINK", "INSULT", "HARASSMENT",
    "RAID", "BOT", "CAPS", "MENTION_SPAM", "MEDIA_SPAM", "OTHER",
]

# پنجره‌ی زمانی (ثانیه) برای جلوگیری از Duplicate Action روی یه رویداد مشابه
MODERATION_DEDUP_WINDOW_SECONDS = 8

# آیا موتور مدیریت به‌صورت خودکار به رویدادهای Spam Engine / Lock Engine واکنش نشون بده و
# در تاریخچه/ریسک ثبتشون کنه؟ (اکشن واقعی رو همچنان خود همون موتورها می‌گیرن؛ این فقط
# یکپارچه‌سازی و ثبت مرکزیه تا از Action تکراری جلوگیری بشه و همه‌چیز توی یه تاریخچه باشه)
MODERATION_AUTO_INTEGRATION_ENABLED = True

# چند مورد آخر از تاریخچه‌ی هر کاربر توی /modhistory نشون داده بشه
MODERATION_HISTORY_DISPLAY_LIMIT = 12

# آیا کاربرهای VIP (لیست _vip_users فعلی پروژه) از Auto Moderation معاف باشن؟
MODERATION_VIP_PROTECTED = True

# ═══════════════════════════════════════════════════════════════════════════
# 👤 PROFILE ENGINE — فاز ۱ ارتقای Profile / XP / Nickname / Economy / Poll
# این بخش کاملاً افزوده‌ست (Additive) و به هیچ‌کدوم از تنظیمات بالا دست نمی‌زنه.
# اگه profile_engine.py نتونه این مقادیر رو پیدا کنه، از یه پیش‌فرض امن استفاده
# می‌کنه (Fail-Safe)، پس حذفشون هم ربات رو Crash نمی‌کنه.
# ═══════════════════════════════════════════════════════════════════════════

# فایل ذخیره‌سازی مستقل موتور پروفایل (مثل lock_engine_state.json و بقیه)
PROFILE_ENGINE_STATE_FILE = "profile_engine_state.json"

# ---------- 💎 Reputation (/rep) ----------
REPUTATION_ENABLED = True
REPUTATION_DAILY_LIMIT = 5              # هر کاربر در روز حداکثر چندبار می‌تونه Rep بده
REPUTATION_TARGET_COOLDOWN_HOURS = 24   # به یک نفر خاص، حداقل هر چند ساعت یک‌بار می‌شه Rep داد
REPUTATION_GIVE_AMOUNT = 1

# ---------- 👁 Profile Views ----------
PROFILE_VIEWS_ENABLED = True

# ---------- 🏷 Nickname (لقب) - جلوگیری از جعل ادمین/اونر ----------
# اگه کاربر عادی (نه ادمین) سعی کنه لقبش رو شامل یکی از این کلمات بذاره، رد می‌شه
NICKNAME_IMPERSONATION_WORDS = [
    "ادمین", "ادمین کل", "مدیر", "اونر", "مالک", "owner", "admin", "administrator",
]

# ---------- 🎭 PHASE 3 — RPG Stats / Prestige / Needs (پیش‌فرض‌ها امن‌ان و
# هیچ رفتار قبلی رو عوض نمی‌کنن؛ چون Feature-Flag محسوب می‌شن) ----------
# سیستم Needs (Health/Energy/Hunger/Stress/Happiness) طبق فاز ۱۷ باید کاملاً
# اختیاری باشه؛ پیش‌فرض خاموشه تا تجربه‌ی فعلی بازی به‌هم نریزه.
NEEDS_ENABLED = False

# ---------- فاز ۱۷ — Hunger/Stress/Happiness (Energy عمداً اینجا نیست؛ از
# سیستم انرژیِ economy_jobs.py که از قبل وجود داره استفاده می‌شه، طبق قانون
# «قابلیت موجود رو دوباره نساز») ----------
NEEDS_HUNGER_DECAY_PER_HOUR = 2.0      # گرسنگی هر ساعت این‌قدر زیاد می‌شه
NEEDS_STRESS_DECAY_PER_HOUR = 1.5      # استرس هر ساعت این‌قدر زیاد می‌شه
NEEDS_HAPPINESS_DECAY_PER_HOUR = 1.0   # شادی هر ساعت این‌قدر کم می‌شه
NEEDS_EAT_COST = 150                   # هزینه‌ی «غذا خوردن»
NEEDS_EAT_HUNGER_RELIEF = 40           # هر بار غذا خوردن، این‌قدر گرسنگی کم می‌شه
NEEDS_REST_COOLDOWN_MINUTES = 30
NEEDS_REST_STRESS_RELIEF = 25          # هر بار استراحت، این‌قدر استرس کم می‌شه
NEEDS_REST_HAPPINESS_BONUS = 10        # هر بار استراحت، این‌قدر شادی اضافه می‌شه

# حداقل Level لازم برای Prestige گرفتن (فاز ۳)
PRESTIGE_LEVEL_REQUIREMENT = 50

# جایزه‌ی رسیدن به Levelهای خاص (فاز ۳؛ مشابه ACHIEVEMENT_REWARDS). Levelی که
# اینجا نباشه یعنی جایزه‌ی مالی نداره، فقط پیام تبریک می‌گیره (رفتار فعلی).
LEVEL_REWARDS = {
    5: {"xp": 0, "coins": 100},
    10: {"xp": 0, "coins": 250},
    25: {"xp": 0, "coins": 750},
    50: {"xp": 0, "coins": 2000},
}

# ---------- 🏅 Achievement Rewards ----------
# هر دستاورد (کلیدها دقیقاً همون کلیدهای config.ACTIVITY_ACHIEVEMENTS) موقع Unlock
# این‌قدر XP/Coin هم جایزه می‌ده. کلیدی که این‌جا نباشه یعنی جایزه‌ی مالی نداره
# (فقط توی /achievements نشون داده می‌شه) - این هیچ رفتار قبلی رو عوض نمی‌کنه.
ACHIEVEMENT_REWARDS = {
    "msg_1": {"xp": 10, "coins": 20},
    "msg_100": {"xp": 50, "coins": 100},
    "msg_1000": {"xp": 200, "coins": 500},
    "msg_10000": {"xp": 1000, "coins": 2000},
    "streak_3": {"xp": 30, "coins": 40},
    "streak_7": {"xp": 100, "coins": 150},
    "streak_30": {"xp": 400, "coins": 600},
    "streak_100": {"xp": 1500, "coins": 2500},
    "voice_master": {"xp": 80, "coins": 120},
    "media_master": {"xp": 80, "coins": 120},
    "hyper_active": {"xp": 300, "coins": 400},
    "level_10": {"xp": 0, "coins": 200},
    "level_25": {"xp": 0, "coins": 500},
    "level_50": {"xp": 0, "coins": 1000},
    "rich_1000": {"xp": 0, "coins": 0},
    "rank_top10": {"xp": 0, "coins": 150},
    "rank_top3": {"xp": 0, "coins": 400},
    "early_member": {"xp": 50, "coins": 100},
    "poll_first_vote": {"xp": 10, "coins": 10},
    "poll_10_votes": {"xp": 50, "coins": 50},
    "poll_50_votes": {"xp": 150, "coins": 150},
    "poll_creator": {"xp": 20, "coins": 30},
    "poll_master_10": {"xp": 200, "coins": 300},
    "big_spender": {"xp": 30, "coins": 0},
}

# دستاوردهای مبتنی بر رتبه/زمان عضویت/نظرسنجی - چون به رتبه‌بندی کل گروه یا رویدادهای
# خاص (نه فقط شمارش ساده) نیاز دارن، جدا از ACTIVITY_ACHIEVEMENTS محاسبه می‌شن، ولی
# در همون _achievements[chat_id][uid] موجود ذخیره و توسط /achievements نشون داده می‌شن.
RANK_ACHIEVEMENTS = {
    "rank_top10": {"label": "🥇 تاپ ۱۰ گروه", "rank_threshold": 10},
    "rank_top3": {"label": "🏆 تاپ ۳ گروه", "rank_threshold": 3},
}
EARLY_MEMBER_THRESHOLD = 50   # اولین ۵۰ نفری که join_time ثبت‌شده دارن
POLL_ACHIEVEMENTS = {
    "poll_first_vote": {"label": "🗳 اولین رأی", "votes_threshold": 1},
    "poll_10_votes": {"label": "🗳 ده رأی", "votes_threshold": 10},
    "poll_50_votes": {"label": "🗳 پنجاه رأی", "votes_threshold": 50},
    "poll_creator": {"label": "📊 اولین نظرسنجی ساخته‌شده", "created_threshold": 1},
    "poll_master_10": {"label": "🎯 استاد نظرسنجی (۱۰ نظرسنجی)", "created_threshold": 10},
}
BIG_SPENDER_THRESHOLD = 1000   # مجموع خرج در فروشگاه برای دستاورد big_spender

# ---------- 🎖 Badge Definitions (نقشی/دستاوردی - علاوه بر بج‌های خریدنی SHOP_ITEMS) ----------
# این‌ها به‌صورت خودکار Sync می‌شن (نه با /buy)؛ auto_type مشخص می‌کنه شرطش چیه.
BADGE_DEFINITIONS = {
    "role_owner": {"emoji": "👑", "label": "OWNER", "auto_type": "owner"},
    "role_admin": {"emoji": "🛡", "label": "ADMIN", "auto_type": "admin"},
    "role_vip": {"emoji": "💎", "label": "VIP", "auto_type": "vip"},
    "achv_active": {"emoji": "🔥", "label": "ACTIVE", "auto_type": "achievement", "requires": "streak_7"},
    "achv_top3": {"emoji": "🏆", "label": "TOP 3", "auto_type": "achievement", "requires": "rank_top3"},
    "achv_level50": {"emoji": "⭐", "label": "LEVEL 50", "auto_type": "achievement", "requires": "level_50"},
    "achv_rich": {"emoji": "💰", "label": "RICH", "auto_type": "achievement", "requires": "rich_1000"},
    "achv_pollmaster": {"emoji": "🎯", "label": "POLL MASTER", "auto_type": "achievement", "requires": "poll_master_10"},
}

# آیدی عددی مالک(های) ربات/گروه - اختیاریه (پیش‌فرض خالی = هیچ‌کس بج OWNER نمی‌گیره).
# پر کردنش هیچ رفتار فعلی رو عوض نمی‌کنه، فقط بج/لقب OWNER رو فعال می‌کنه.
OWNER_IDS: list[int] = []

# برچسب دستاوردهایی که در دیکشنری جدای خودشون تعریف شدن (RANK_ACHIEVEMENTS/POLL_ACHIEVEMENTS
# لیبل دارن، ولی early_member و big_spender جای دیگه‌ای غیر از ACHIEVEMENT_REWARDS تعریف نشدن)
EXTRA_ACHIEVEMENT_LABELS = {
    "early_member": "🌱 عضو اولیه‌ی گروه",
    "big_spender": "💸 خرج‌کن بزرگ (۱۰۰۰+ سکه)",
}

# ═══════════════════════════════════════════════════════════════════════════
# 🪙 ECONOMY ENGINE — فاز ۲ ارتقای Economy (Wallet/Transactions/Transfer/Shop/Stats)
# کاملاً افزوده‌ست؛ به CURRENCY_NAME، SHOP_ITEMS فعلی و بقیه‌ی تنظیمات اقتصاد دست
# نمی‌زنه، فقط موارد جدید رو اضافه می‌کنه.
# ═══════════════════════════════════════════════════════════════════════════

ECONOMY_ENGINE_STATE_FILE = "economy_engine_state.json"

# ---------- 🧾 Transaction History ----------
TRANSACTION_HISTORY_LIMIT = 200      # حداکثر تراکنش ذخیره‌شده برای هر کاربر (قدیمی‌ترها حذف می‌شن)
TRANSACTIONS_DISPLAY_COUNT = 15      # چندتا توی /transactions نشون داده بشه

TRANSACTION_KIND_LABELS = {
    "daily": "🎁 جایزه‌ی روزانه",
    "activity": "⚡ سکه‌ی فعالیت",
    "pay_sent": "📤 انتقال (ارسالی)",
    "pay_received": "📥 انتقال (دریافتی)",
    "purchase": "🛒 خرید از فروشگاه",
    "achievement": "🏅 جایزه‌ی دستاورد",
    "poll_reward": "🗳 جایزه‌ی نظرسنجی",
    "admin_add": "🛠 تنظیم دستی ادمین",
}

# ---------- 💸 Transfer Limits (جلوگیری از Abuse / Inflation) ----------
TRANSFER_DAILY_LIMIT_COUNT = 10      # حداکثر تعداد انتقال در روز
TRANSFER_DAILY_LIMIT_AMOUNT = 5000   # حداکثر مجموع مبلغ منتقل‌شده در روز
TRANSFER_MAX_SINGLE = 2000           # حداکثر مبلغ هر انتقال تکی

# ---------- 🎁 Daily Reward Streak Bonus ----------
# کلید = حداقل Streak پیاپی لازم، مقدار = سکه‌ی جایزه‌ی اضافه (بزرگ‌ترین آستانه‌ی رسیده انتخاب می‌شه)
DAILY_STREAK_BONUS = {
    3: 30,
    7: 100,
    30: 400,
}

# ---------- 🎒 Inventory / Timed Items (Equip خودکار، Expire خودکار) ----------
XP_BOOST_MULTIPLIER = 2      # ضریب XP وقتی boost فعاله
COIN_BOOST_MULTIPLIER = 2    # ضریب سکه‌ی فعالیت وقتی boost فعاله

# آیتم‌های تازه‌ی فروشگاه (Additive - به همون config.SHOP_ITEMS اضافه می‌شن، چیزی حذف نشده)
SHOP_ITEMS.update({
    "xp_boost_1h": {
        "name": "⚡ تقویت XP (۱ ساعت, x2)", "price": 150, "emoji": "⚡",
        "type": "boost_xp", "duration_hours": 1,
    },
    "coin_boost_1h": {
        "name": "🪙 تقویت سکه (۱ ساعت, x2)", "price": 150, "emoji": "🪙",
        "type": "boost_coin", "duration_hours": 1,
    },
    "vip_pass_24h": {
        "name": "💎 پاس VIP (۲۴ ساعت)", "price": 800, "emoji": "💎",
        "type": "vip", "duration_hours": 24,
    },
})

# ---------- 🎯 Admin economy tools (giveitem/removeitem) ----------
# از همون permission فعلی "manage_economy" استفاده می‌کنن (چیز جدیدی لازم نیست)

# ═══════════════════════════════════════════════════════════════════════════
# 🗳 POLL ENGINE — فاز ۳ ارتقای Poll (Timer/Anonymous/Multiple Choice/Reward/History)
# کاملاً افزوده‌ست؛ دستور فعلی /poll از کار نمی‌افته، فقط قابلیت‌های جدید (اختیاری)
# با پارامترهای اضافه بعد از | قابل‌فعال‌سازی‌ان. بدون این پارامترها، رفتار قبلی
# دقیقاً همون قبلیه (Public, Single-Choice, بدون تایمر خودکار، بدون جایزه).
# ═══════════════════════════════════════════════════════════════════════════

POLL_ENGINE_STATE_FILE = "poll_engine_state.json"
POLL_HISTORY_LIMIT = 200                # چندتا نظرسنجی بسته‌شده توی تاریخچه نگه داشته بشه
POLL_HISTORY_DISPLAY_COUNT = 10         # چندتا توی /pollhistory نشون داده بشه
POLL_REWARD_MAX = 500                   # سقف جایزه‌ی هر رأی (جلوگیری از Abuse با جایزه:99999)

# کلیدواژه‌های تشخیص تنظیمات اضافه در /poll (بعد از گزینه‌ها، با | جدا می‌شن)
POLL_ANONYMOUS_KEYWORDS = ["ناشناس", "anonymous", "anon"]
POLL_MULTI_KEYWORDS = ["چندتایی", "چند‌گزینه‌ای", "multi", "multiple"]
POLL_DURATION_PREFIXES = ["زمان:", "duration:", "مدت:"]
POLL_REWARD_PREFIXES = ["جایزه:", "reward:"]

# فقط برای راهنمای /poll (خود Parser با Regex هر Xm/Xh/Xd رو قبول می‌کنه)
POLL_DURATION_EXAMPLES = "1m، 5m، 30m، 1h، 1d (یا هر عدد دلخواه مثل 10m، 3h)"

# ═══════════════════════════════════════════════════════════════════════════
# 🧹 PURGE ENGINE — سیستم پاکسازی پیشرفته (فیلتر/زمان/تعداد/کاربر/هوشمند)
# دستور فعلی /purge (ریپلای‌محور، بدون آرگومان) دقیقاً همون رفتار قبلی رو
# داره؛ همه‌ی این قابلیت‌ها روی همون دستور و معادل فارسی «پاکسازی» با
# آرگومان اضافه شدن، بدون شکستن رفتار قبلی. جزئیات کامل: /purge help یا
# «پاکسازی راهنما».
# ═══════════════════════════════════════════════════════════════════════════

PURGE_ENGINE_STATE_FILE = "purge_engine_state.json"

PURGE_ENABLED = True                 # کلید کلی روشن/خاموش کل سیستم پاکسازی
PURGE_USER_ENABLED = True            # روشن/خاموش /purgeuser «پاکسازی کاربر»
PURGE_PREVIEW_ENABLED = True         # روشن/خاموش حالت پیش‌نمایش
PURGE_SILENT_ENABLED = True          # روشن/خاموش حالت بی‌صدا
PURGE_SMART_ENABLED = True           # روشن/خاموش «پاکسازی هوشمند»
PURGE_LOG_ENABLED = True             # ثبت هر عملیات پاکسازی توی کانال لاگ ادمین (ADMIN_LOG_CHANNEL_ID)

PURGE_MAX_MESSAGES = 1000            # سقف تعداد پیام در هر عملیات پاکسازی (بالاتر از این Clamp می‌شه، نه رد)
PURGE_CONFIRM_THRESHOLD = 300        # بالاتر از این تعداد پیام شناسایی‌شده، قبل از حذف تاییدیه گرفته می‌شه
PURGE_TIME_LIMIT = 86400             # (ثانیه) بازه‌ی پیش‌فرض جستجو برای پاکسازی‌های فیلتردار، وقتی نه زمان نه تعداد مشخص شده (پیش‌فرض ۲۴ ساعت)
PURGE_PENDING_TTL_SECONDS = 120      # تاییدیه/پیش‌نمایش‌های پاکسازی بعد از این‌قدر ثانیه منقضی می‌شن

# حداکثر تعداد پیام اخیر هر گروه که برای پاکسازی فیلتردار (رسانه/لینک/متن/بات/تکراری/اسپم/تبلیغات/هوشمند/کاربر)
# توی حافظه نگه داشته می‌شه. توجه: این کش فقط توی حافظه‌ست و با ری‌استارت ربات خالی می‌شه
# (Telegram Bot API راهی برای واکشی محتوای پیام‌های قدیمی در اختیار ربات‌ها نمی‌ذاره).
PURGE_CACHE_MAX_MESSAGES = 6000

PURGE_AD_SCORE_THRESHOLD = 40        # حداقل امتیاز تبلیغاتی (از spam_engine._ad_score) برای «پاکسازی تبلیغات»
PURGE_SPAM_SCORE_THRESHOLD = 20      # حداقل امتیاز اسپم کاربر (از spam_engine.current_score) برای «پاکسازی اسپم»

# ═══════════════════════════════════════════════════════════════════════════
# 💼 ECONOMY JOBS & INCOME — فاز ۲ ارتقای Economy (Module 1: درآمد + Module 2: Job System)
# کاملاً افزوده‌ست؛ روی economy_core.py (فاز ۱) سوار می‌شه، چیزی از اقتصاد فعلی
# (wallet/pay/shop/daily) رو تغییر نمی‌ده. اگه economy_jobs.py این مقادیر رو پیدا
# نکنه، از پیش‌فرض امن استفاده می‌کنه (Fail-Safe).
# ═══════════════════════════════════════════════════════════════════════════

ECONOMY_JOBS_STATE_FILE = "economy_jobs_state.json"

# ---------- ⚡ Energy / Fatigue ----------
ENERGY_MAX = 100
ENERGY_REGEN_PER_MINUTE = 1.0     # هر دقیقه این‌قدر انرژی برمی‌گرده (از 0 تا Max حدود 100 دقیقه)
ENERGY_COST_NO_JOB = 10           # هزینه‌ی انرژیِ کار کردن وقتی هنوز هیچ شغلی انتخاب نکرده

# ---------- 💵 Work ("هاپ هاپ" / "کار" / /work) ----------
WORK_COOLDOWN_MINUTES = 30
WORK_DAILY_LIMIT = 40             # حداکثر تعداد کار در روز (Anti-Abuse / جلوگیری از Farming)
WORK_INCOME_NO_JOB_MIN = 15       # درآمد پایه وقتی کاربر هنوز شغلی نداره (کار موقت/روزمزد)
WORK_INCOME_NO_JOB_MAX = 35
WORK_RANDOM_BONUS_CHANCE = 0.15   # احتمال جایزه‌ی شانسی اضافه روی هر کار
WORK_RANDOM_BONUS_MIN = 20
WORK_RANDOM_BONUS_MAX = 120
WORK_XP_BASE = 12                 # Job XP پایه‌ی هر بار کار (صرف‌نظر از شغل)
WORK_FAIL_COIN_FRACTION = 0.35    # اگه کار «شکست» بخوره، فقط این‌قدر از پاداش رو می‌گیره

# ---------- 🏦 Salary ("حقوق" / /salary) — دریافت حقوق دوره‌ای شغل فعلی ----------
SALARY_COOLDOWN_HOURS = 6

# ---------- 📈 Job XP / Level ----------
JOB_XP_PER_LEVEL_BASE = 80        # XP لازم برای رسیدن به Level بعدی = این عدد × Level فعلی
JOB_LEVEL_SALARY_GROWTH = 0.12    # هر Level بالاتر از ۱، حقوق/درآمد این‌قدر درصد بیشتر می‌شه
JOB_LEVEL_TITLES = {
    1: "کارآموز", 10: "حرفه‌ای", 25: "ارشد", 50: "استاد",
}

# ---------- 💼 تعریف شغل‌ها ----------
# tier فقط جنبه‌ی نمایشی/رقابتی داره. required_total_level = مجموع Levelِ همه‌ی
# شغل‌هایی که کاربر تا الان داشته (حتی شغل‌های قبلی، چون توی _job_progress
# نگه داشته می‌شن) — یعنی برای شغل بهتر باید قبلش رشد کرده باشی.
# required_reputation از همون profile_engine.reputation_of (Global Reputation
# فعلی پروژه) خونده می‌شه؛ Reputation جدا برای هر شغل توی فازهای بعدی اضافه می‌شه.
JOBS = {
    "worker":     {"name": "کارگر",        "emoji": "👷",  "tier": "COMMON",     "base_salary": 40,  "energy_cost": 12, "fail_chance": 0.02, "req_total_level": 0,  "req_reputation": 0,   "ability": "بدون نیاز به چیزی، همیشه در دسترس"},
    "chef":       {"name": "آشپز",         "emoji": "👨‍🍳", "tier": "COMMON",     "base_salary": 45,  "energy_cost": 13, "fail_chance": 0.02, "req_total_level": 0,  "req_reputation": 0,   "ability": "شانس کمی بالاتر برای جایزه‌ی شانسی"},
    "driver":     {"name": "راننده",        "emoji": "🚕",  "tier": "COMMON",     "base_salary": 50,  "energy_cost": 14, "fail_chance": 0.03, "req_total_level": 0,  "req_reputation": 0,   "ability": "کول‌داون کمی کمتر"},
    "employee":   {"name": "کارمند",        "emoji": "🧑‍💼", "tier": "UNCOMMON",   "base_salary": 75,  "energy_cost": 16, "fail_chance": 0.03, "req_total_level": 5,  "req_reputation": 0,   "ability": "حقوق ثابت‌تر و قابل‌اتکاتر"},
    "designer":   {"name": "طراح",         "emoji": "🎨",  "tier": "UNCOMMON",   "base_salary": 85,  "energy_cost": 17, "fail_chance": 0.04, "req_total_level": 8,  "req_reputation": 0,   "ability": "جایزه‌ی شانسی بزرگ‌تر"},
    "programmer": {"name": "برنامه‌نویس",   "emoji": "💻",  "tier": "RARE",       "base_salary": 130, "energy_cost": 20, "fail_chance": 0.05, "req_total_level": 15, "req_reputation": 20,  "ability": "Job XP بیشتر به ازای هر کار"},
    "engineer":   {"name": "مهندس",         "emoji": "🏗️", "tier": "RARE",       "base_salary": 140, "energy_cost": 20, "fail_chance": 0.05, "req_total_level": 18, "req_reputation": 20,  "ability": "احتمال شکست کمتر روی پروژه‌های بزرگ"},
    "trader":     {"name": "تریدر",        "emoji": "📈",  "tier": "RARE",       "base_salary": 150, "energy_cost": 22, "fail_chance": 0.06, "req_total_level": 20, "req_reputation": 25,  "ability": "پایه‌ی خوبی برای ماژول Trading (فاز بعد)"},
    "banker":     {"name": "بانکدار",       "emoji": "🏦",  "tier": "EPIC",       "base_salary": 220, "energy_cost": 24, "fail_chance": 0.07, "req_total_level": 32, "req_reputation": 55,  "ability": "پایه‌ی خوبی برای ماژول Bank (فاز بعد)"},
    "scientist":  {"name": "دانشمند",       "emoji": "🧑‍🔬", "tier": "EPIC",       "base_salary": 235, "energy_cost": 25, "fail_chance": 0.07, "req_total_level": 35, "req_reputation": 55,  "ability": "Job XP بالا، مناسب رشد سریع"},
    "jeweler":    {"name": "جواهرساز",      "emoji": "💎",  "tier": "EPIC",       "base_salary": 250, "energy_cost": 26, "fail_chance": 0.08, "req_total_level": 38, "req_reputation": 60,  "ability": "پایه‌ی خوبی برای ماژول Black Market (فاز بعد)"},
    "manager":    {"name": "مدیر",         "emoji": "🏢",  "tier": "LEGENDARY",  "base_salary": 380, "energy_cost": 30, "fail_chance": 0.10, "req_total_level": 65, "req_reputation": 110, "ability": "بالاترین حقوق ثابت بین شغل‌های غیر-تاپ"},
    "capitalist": {"name": "سرمایه‌دار",    "emoji": "👑",  "tier": "LEGENDARY",  "base_salary": 450, "energy_cost": 32, "fail_chance": 0.12, "req_total_level": 80, "req_reputation": 140, "ability": "بالاترین حقوق و پرستیژ کل Economy"},
}

# ═══════════════════════════════════════════════════════════════════════════
# 🎯 ECONOMY MISSIONS — فاز ۲ (Module 3)
# سیستم Mission روی record_progress(chat_id, uid, action, amount) کار می‌کنه؛
# هر ماژول جدید (Market/Trading/Property/Pet/...) با صدا زدن همین تابع با
# action مناسب، به‌صورت خودکار وارد ماموریت‌ها می‌شه — نیازی به تغییر این فایل
# یا economy_missions.py نیست، فقط باید توی MISSION_TEMPLATES زیر یه ماموریت
# با همون action اضافه بشه.
# ═══════════════════════════════════════════════════════════════════════════

ECONOMY_MISSIONS_STATE_FILE = "economy_missions_state.json"

MISSION_ASSIGN_COUNT = {"daily": 3, "weekly": 2}

# هر Template: key یکتا، period (daily/weekly/monthly/special/event)، متن نمایشی،
# action (باید با چیزی که record_progress باهاش صدا زده می‌شه یکی باشه)، target
# (چند بار/چقدر باید جمع بشه)، و reward (فعلاً فقط coins؛ فازهای بعد xp/item هم اضافه می‌کنن).
MISSION_TEMPLATES = {
    "daily": [
        {"key": "d_work5", "text": "۵ بار کار کن", "action": "work", "target": 5, "reward": {"coins": 150}},
        {"key": "d_earn1000", "text": "از راه کار، ۱۰۰۰ سکه به دست بیار", "action": "earn", "target": 1000, "reward": {"coins": 200}},
        {"key": "d_salary1", "text": "یک بار حقوقت رو بگیر", "action": "salary_claim", "target": 1, "reward": {"coins": 100}},
        {"key": "d_joblevelup1", "text": "شغلت رو یک Level ارتقا بده", "action": "job_levelup", "target": 1, "reward": {"coins": 250}},
    ],
    "weekly": [
        {"key": "w_work25", "text": "۲۵ بار کار کن", "action": "work", "target": 25, "reward": {"coins": 600}},
        {"key": "w_choosejob1", "text": "یک شغل انتخاب کن (یا شغل عوض کن)", "action": "choosejob", "target": 1, "reward": {"coins": 300}},
        {"key": "w_earn5000", "text": "از راه کار، ۵۰۰۰ سکه به دست بیار", "action": "earn", "target": 5000, "reward": {"coins": 900}},
    ],
    # monthly / special / event: هر وقت لازم شد، همین‌جا یه لیست جدید با همین
    # ساختار اضافه کن؛ economy_missions.py خودش از روی کلیدهای MISSION_TEMPLATES
    # می‌خونه، نیازی به تغییر کد نیست.
}

# ═══════════════════════════════════════════════════════════════════════════
# 🏦 ECONOMY BANK — فاز ۳ (Module 4)
# روی economy_core.deposit/withdraw/add_bank_coins/remove_bank_coins (فاز ۱)
# سوار می‌شه؛ چیزی از /pay یا /shop فعلی رو تغییر نمی‌ده.
# ═══════════════════════════════════════════════════════════════════════════

ECONOMY_BANK_STATE_FILE = "economy_bank_state.json"

# سود روزانه‌ی سپرده‌ی کوتاه‌مدت (همون موجودی ساده‌ی بانک) - با «سود» Claim می‌شه
BANK_INTEREST_RATE_DAILY = 0.01          # 1% در روز
BANK_INTEREST_CLAIM_MAX_DAYS = 14        # سقف روزهایی که سود براشون جمع می‌شه (جلوگیری از انباشت بی‌نهایت آفلاین)

# کارمزد برداشت از بانک (Money Sink - طبق Module 26، این پول از اقتصاد خارج می‌شه، به کسی داده نمی‌شه)
WITHDRAWAL_FEE_PERCENT = 0.02

# سپرده‌ی بلندمدت: روز قفل -> نرخ سود کل دوره (نه روزانه، یک‌جا موقع بلوغ محاسبه می‌شه)
LONG_TERM_DEPOSIT_DURATIONS = {7: 0.05, 30: 0.15, 90: 0.40}
LONG_TERM_MAX_ACTIVE_PER_LEVEL_BASE = 2   # حداقل تعداد سپرده‌ی بلندمدت هم‌زمان (Bank Level 1)
LONG_TERM_EARLY_WITHDRAW_PENALTY_PERCENT = 0.5   # برداشت زودهنگام: فقط نصف سود تعلق‌گرفته رو می‌گیره، اصل پول امنه

# Bank Level / Upgrade: هر Level بالاتر، سود روزانه رو بیشتر و سقف سپرده‌ی بلندمدت رو باز می‌کنه
BANK_LEVELS = {
    1: {"upgrade_cost": 0,     "max_long_deposits": 2, "interest_bonus": 0.000},
    2: {"upgrade_cost": 2000,  "max_long_deposits": 3, "interest_bonus": 0.002},
    3: {"upgrade_cost": 8000,  "max_long_deposits": 4, "interest_bonus": 0.005},
    4: {"upgrade_cost": 25000, "max_long_deposits": 5, "interest_bonus": 0.010},
    5: {"upgrade_cost": 80000, "max_long_deposits": 6, "interest_bonus": 0.015},
}

# ---------- 💳 Loans / Credit Score — ارتقای فاز ۱۳ (روی همون economy_bank.py) ----------
CREDIT_SCORE_DEFAULT = 600
CREDIT_SCORE_MIN = 300
CREDIT_SCORE_MAX = 850
CREDIT_SCORE_MIN_FOR_LOAN = 400
CREDIT_SCORE_ON_TIME_BONUS = 15      # هر قسط به‌موقع
CREDIT_SCORE_LATE_PENALTY = 25       # هر قسط عقب‌افتاده (بعد از Grace)
CREDIT_SCORE_DEFAULT_PENALTY = 120   # نکول کامل وام

LOAN_BASE_MAX_AMOUNT = 20000          # سقف وام در Credit Score پیش‌فرض (600)
LOAN_CREDIT_SCORE_MAX_MULTIPLIER = 3.0  # در Credit Score 850، سقف وام = ۳× این مقدار
LOAN_MAX_ACTIVE = 2                   # حداکثر وام هم‌زمان (جلوگیری از سو‌استفاده)
# مدت وام (روز) -> نرخ سود کل دوره (نه روزانه؛ هرچی مدت بیشتر، سود کل بیشتر)
LOAN_TERM_OPTIONS = {7: 0.08, 14: 0.14, 30: 0.25}
LOAN_INSTALLMENT_PERIOD_DAYS = 7      # هر قسط هر ۷ روز
LOAN_GRACE_PERIOD_HOURS = 48          # این‌قدر بعد از موعد، بدون جریمه مهلت داری
LOAN_LATE_FEE_FRACTION = 0.05         # جریمه‌ی هر قسط عقب‌افتاده (از مبلغ قسط)
LOAN_DEFAULT_AFTER_MISSED_INSTALLMENTS = 3   # این‌قدر قسط پشت‌سرهم نده = نکول

# ═══════════════════════════════════════════════════════════════════════════
# 📈 ECONOMY MARKET & TRADING — فاز ۳ (Module 5 + Module 6)
# طبق فایل معماری خودتون، Trading جدا از Market فایل نداره (economy_market.py
# «بازار و ارز» رو پوشش می‌ده) - دستور «ترید» هم همون‌جا پیاده‌سازی شده.
# قیمت‌ها/معاملات کاملاً مجازی‌ان، فقط داخل بازی.
# ═══════════════════════════════════════════════════════════════════════════

ECONOMY_MARKET_STATE_FILE = "economy_market_state.json"

# نماد -> نام فارسی، قیمت پایه، نوسان (درصد حرکت تصادفی در هر Tick)
MARKET_ASSETS = {
    "BTC":     {"name": "بیت‌کوین",   "base_price": 50000, "volatility": 0.040, "liquidity_mult": 200},
    "ETH":     {"name": "اتریوم",     "base_price": 3000,  "volatility": 0.045, "liquidity_mult": 180},
    "GOLD":    {"name": "طلا",        "base_price": 2000,  "volatility": 0.015, "liquidity_mult": 120},
    "OIL":     {"name": "نفت",        "base_price": 80,    "volatility": 0.030, "liquidity_mult": 100},
    "TECH":    {"name": "سهام تک",    "base_price": 500,   "volatility": 0.035, "liquidity_mult": 90},
    "ENERGY":  {"name": "انرژی",      "base_price": 150,   "volatility": 0.025, "liquidity_mult": 100},
    "DIAMOND": {"name": "الماس",      "base_price": 10000, "volatility": 0.020, "liquidity_mult": 40},
}

# ---------- ارتقای فاز ۱۴ — اثر واقعی Supply/Demand روی قیمت ----------
# هر خرید/فروش، خودِ قیمت رو هم جا‌به‌جا می‌کنه (نه فقط Random Walk مستقل از
# رفتار بازیکن‌ها). liquidity هر نماد = base_price × liquidity_mult؛ اثر هر
# معامله = ارزش معامله / liquidity، با سقف MARKET_MAX_IMPACT_PER_TRADE تا یه
# معامله‌ی غول‌آسا قیمت رو یهو Moon/Crash نکنه.
MARKET_MAX_IMPACT_PER_TRADE = 0.08   # حداکثر ۸٪ جابه‌جایی قیمت در یک معامله

MARKET_TICK_INTERVAL_SECONDS = 300   # هر Tick قیمت معادل ۵ دقیقه‌ی واقعیه
MARKET_MAX_TICKS_PER_ACCESS = 50     # سقف تعداد Tickی که یه‌جا (lazy) محاسبه می‌شه، حتی اگه خیلی وقت گذشته باشه
MARKET_PRICE_HISTORY_LEN = 30
MARKET_PRICE_MIN_FRACTION = 0.05     # قیمت هیچ‌وقت از ۵٪ قیمت پایه کمتر نمی‌شه (جلوگیری از صفر/منفی شدن)

MARKET_EVENT_CHANCE_PER_TICK = 0.04
MARKET_EVENTS = {
    "bull":  {"label": "📈 Bull Market",   "multiplier": 1.15, "duration_ticks": 6},
    "bear":  {"label": "📉 Bear Market",   "multiplier": 0.88, "duration_ticks": 6},
    "crash": {"label": "💥 Market Crash",  "multiplier": 0.60, "duration_ticks": 3},
    "boom":  {"label": "🚀 Market Boom",   "multiplier": 1.40, "duration_ticks": 3},
    "shock": {"label": "⚡ Supply Shock",  "multiplier": 1.25, "duration_ticks": 2},
}

MARKET_TRADING_FEE_PERCENT = 0.01    # کارمزد خرید/فروش دارایی (Money Sink)

# ---------- 💹 ترید سریع ("ترید [مبلغ]") — جدا از خرید/فروش دارایی ----------
TRADE_COOLDOWN_SECONDS = 600
TRADE_DAILY_LIMIT = 20
TRADE_MIN_AMOUNT = 50
TRADE_MAX_AMOUNT = 5000
TRADE_WIN_CHANCE = 0.48              # کمی زیر ۵۰٪ - طبق Module 26 (Economy Balancing)، جلوگیری از پول بی‌نهایت
TRADE_PROFIT_MIN_FRACTION = 0.10
TRADE_PROFIT_MAX_FRACTION = 0.60
TRADE_LOSS_MIN_FRACTION = 0.20
TRADE_LOSS_MAX_FRACTION = 1.00
TRADE_CRITICAL_CHANCE = 0.05         # شانس Critical Win/Loss
TRADE_CRITICAL_PROFIT_MULTIPLIER = 2.0
TRADE_XP_PER_TRADE = 15
TRADE_XP_PER_LEVEL_BASE = 100

# با اضافه‌شدن Bank/Market/Trading، این ماموریت‌های جدید هم به Templateهای فاز ۲
# اضافه می‌شن (چیزی از قبلی‌ها حذف/تغییر نشده):
MISSION_TEMPLATES["daily"].append(
    {"key": "d_trade3", "text": "۳ بار ترید کن", "action": "trade", "target": 3, "reward": {"coins": 300}}
)
MISSION_TEMPLATES["weekly"].append(
    {"key": "w_deposit1", "text": "به بانک سپرده بذار (حداقل یک بار)", "action": "deposit", "target": 1, "reward": {"coins": 350}}
)
MISSION_TEMPLATES["weekly"].append(
    {"key": "w_market_buy1", "text": "یک بار از بازار خرید کن", "action": "market_buy", "target": 1, "reward": {"coins": 350}}
)

# ═══════════════════════════════════════════════════════════════════════════
# 🛡️ ECONOMY SECURITY / INSURANCE — فاز ۴ (Module 7)
# ═══════════════════════════════════════════════════════════════════════════

ECONOMY_SECURITY_STATE_FILE = "economy_security_state.json"

INSURANCE_TIER_ORDER = ["basic", "advanced", "premium", "elite"]
INSURANCE_TIERS = {
    "basic":    {"name": "🥉 Basic",    "cost": 500,   "duration_hours": 24,  "theft_protection": 0.25, "max_loss": 400},
    "advanced": {"name": "🥈 Advanced", "cost": 1500,  "duration_hours": 48,  "theft_protection": 0.45, "max_loss": 1200},
    "premium":  {"name": "🥇 Premium",  "cost": 4000,  "duration_hours": 72,  "theft_protection": 0.65, "max_loss": 3500},
    "elite":    {"name": "💎 Elite",    "cost": 10000, "duration_hours": 168, "theft_protection": 0.85, "max_loss": 10000},
}

# ═══════════════════════════════════════════════════════════════════════════
# 🦹 ECONOMY THEFT + 🚔 WANTED/JAIL — فاز ۴ (Module 8 + Module 9)
# طبق فایل معماری، Wanted/Jail فایل جدا نداره؛ داخل economy_theft.py پیاده
# می‌شه. این‌ها کاملاً یه مکانیک فانتزی/بازی‌ان با پول مجازیه؛ هیچ عملیات
# واقعی‌ای انجام نمی‌شه.
# ═══════════════════════════════════════════════════════════════════════════

ECONOMY_THEFT_STATE_FILE = "economy_theft_state.json"

THEFT_COOLDOWN_MINUTES = 45
THEFT_DAILY_LIMIT = 8
THEFT_MIN_TARGET_WALLET = 200               # هدف باید حداقل این‌قدر توی کیف‌پولش داشته باشه

THEFT_BASE_SUCCESS_CHANCE = 0.42
THEFT_LEVEL_SUCCESS_BONUS_PER_LEVEL = 0.01  # هر Level دزد، ۱٪ (تا سقف زیر) به شانس اضافه می‌کنه
THEFT_SUCCESS_CHANCE_CAP = 0.75
THEFT_SUCCESS_CHANCE_MIN = 0.05             # حتی با بیمه‌ی قوی، شانس صفر نمی‌شه

THEFT_STEAL_MIN_FRACTION = 0.05
THEFT_STEAL_MAX_FRACTION = 0.25

THEFT_XP_PER_SUCCESS = 25
THEFT_XP_PER_FAIL = 5
THEFT_XP_PER_LEVEL_BASE = 120

THEFT_WANTED_INCREASE_ON_SUCCESS = 1
THEFT_WANTED_INCREASE_ON_FAIL = 2
THEFT_WANTED_DECAY_PER_HOUR = 0.5           # هر ساعت که کاری نکنه، Wanted Level کمی پایین میاد

# 🚔 اگه دزدی شکست بخوره: هم Jail هم Fine (هر دو با هم)
THEFT_FAIL_JAIL_MIN_MINUTES = 15
THEFT_FAIL_JAIL_MAX_MINUTES = 60
THEFT_FAIL_FINE_FRACTION = 0.15             # کسری از موجودی کیف‌پول دزد (نه عدد ثابت)
THEFT_FAIL_FINE_MAX = 2000

JAIL_ESCAPE_CHANCE = 0.35
JAIL_ESCAPE_COOLDOWN_SECONDS = 120
JAIL_ESCAPE_FAIL_EXTRA_MINUTES = 10

# با اضافه‌شدن Theft، این ماموریت‌ها هم به Templateهای فاز ۲ اضافه می‌شن:
MISSION_TEMPLATES["daily"].append(
    {"key": "d_steal1", "text": "۱ بار دزدی موفق انجام بده", "action": "steal_success", "target": 1, "reward": {"coins": 200}}
)

# ═══════════════════════════════════════════════════════════════════════════
# 🏙️ ECONOMY DISTRICT — فاز ۴ (منطقه‌های شهر؛ مستقل از City شخصی هرکس)
# ═══════════════════════════════════════════════════════════════════════════
# توجه معماری: economy_city.py از قبل «شهر» رو برای City-Builder شخصی هرکس
# (ساختمان/درآمد غیرفعال) استفاده می‌کنه (فاز ۵ فایل خودش). این «District»
# یه مفهوم متفاوته: منطقه‌ی زندگی کاربر توی یک شهر مشترک که روی قیمت
# ملک/کسب‌وکار/جرم/هزینه‌ی خدمات اثر می‌ذاره. طبق قانون کاربر (دوباره نساز)
# اسمش رو «District» گذاشتم که با «City» فعلی تداخل نداشته باشه.

ECONOMY_DISTRICT_STATE_FILE = "economy_district_state.json"

# ترتیب از فقیرترین به VIP؛ هرکسی تازه‌وارد از "poor" شروع می‌کنه.
DISTRICTS = {
    "poor":     {"name": "🏚 محله‌ی فقیرنشین", "order": 0, "move_cost": 0,
                 "property_price_mult": 0.7, "rent_mult": 0.6, "business_price_mult": 0.7, "vehicle_price_mult": 0.7,
                 "crime_mult": 1.5, "service_cost_mult": 0.8, "reputation_bonus": -5,
                 "min_level": 0},
    "regular":  {"name": "🏘 محله‌ی معمولی", "order": 1, "move_cost": 3000,
                 "property_price_mult": 1.0, "rent_mult": 1.0, "business_price_mult": 1.0, "vehicle_price_mult": 1.0,
                 "crime_mult": 1.0, "service_cost_mult": 1.0, "reputation_bonus": 0,
                 "min_level": 0},
    "downtown": {"name": "🏙 مرکز شهر", "order": 2, "move_cost": 15000,
                 "property_price_mult": 1.4, "rent_mult": 1.3, "business_price_mult": 1.4, "vehicle_price_mult": 1.4,
                 "crime_mult": 0.9, "service_cost_mult": 1.2, "reputation_bonus": 5,
                 "min_level": 10},
    "luxury":   {"name": "🌆 منطقه‌ی لوکس", "order": 3, "move_cost": 60000,
                 "property_price_mult": 2.0, "rent_mult": 1.8, "business_price_mult": 1.8, "vehicle_price_mult": 1.8,
                 "crime_mult": 0.6, "service_cost_mult": 1.5, "reputation_bonus": 15,
                 "min_level": 25},
    "vip":      {"name": "💎 منطقه‌ی VIP", "order": 4, "move_cost": 200000,
                 "property_price_mult": 3.0, "rent_mult": 2.5, "business_price_mult": 2.5, "vehicle_price_mult": 2.5,
                 "crime_mult": 0.3, "service_cost_mult": 2.0, "reputation_bonus": 30,
                 "min_level": 50},
}
DISTRICT_DEFAULT = "poor"

# ═══════════════════════════════════════════════════════════════════════════
# 🏙️ ECONOMY CITY — فاز ۵ (Module 10)
# ═══════════════════════════════════════════════════════════════════════════

ECONOMY_CITY_STATE_FILE = "economy_city_state.json"

# سطح شهر -> هزینه‌ی ارتقا به این سطح
CITY_LEVEL_UPGRADE_COST = {
    2: 5000, 3: 12000, 4: 25000, 5: 50000, 6: 100000, 7: 200000, 8: 400000,
}
CITY_INCOME_MAX_ACCRUE_HOURS = 24    # سقف ساعت‌هایی که درآمد شهر آفلاین جمع می‌شه

# building_key -> مشخصات. unlock_city_level = حداقل سطح شهر برای ساختنش
CITY_BUILDINGS = {
    "house":         {"name": "🏠 خانه",          "base_cost": 1000, "income_per_level": 15,  "maintenance_per_level": 3,  "security_per_level": 0,  "unlock_city_level": 1},
    "shop":          {"name": "🏪 فروشگاه",        "base_cost": 1800, "income_per_level": 25,  "maintenance_per_level": 5,  "security_per_level": 0,  "unlock_city_level": 1},
    "apartment":     {"name": "🏢 آپارتمان",       "base_cost": 2500, "income_per_level": 35,  "maintenance_per_level": 8,  "security_per_level": 0,  "unlock_city_level": 1},
    "entertainment": {"name": "🎰 مرکز سرگرمی",     "base_cost": 3500, "income_per_level": 55,  "maintenance_per_level": 12, "security_per_level": 0,  "unlock_city_level": 2},
    "police":        {"name": "👮 مرکز پلیس",       "base_cost": 4000, "income_per_level": 0,   "maintenance_per_level": 10, "security_per_level": 20, "unlock_city_level": 2},
    "hospital":      {"name": "🏥 بیمارستان",      "base_cost": 4500, "income_per_level": 40,  "maintenance_per_level": 20, "security_per_level": 0,  "unlock_city_level": 3},
    "factory":       {"name": "🏭 کارخانه",         "base_cost": 5000, "income_per_level": 70,  "maintenance_per_level": 18, "security_per_level": 0,  "unlock_city_level": 3},
    "bank":          {"name": "🏦 بانک",           "base_cost": 6000, "income_per_level": 80,  "maintenance_per_level": 15, "security_per_level": 0,  "unlock_city_level": 4},
    "hotel":         {"name": "🏨 هتل",            "base_cost": 7000, "income_per_level": 90,  "maintenance_per_level": 20, "security_per_level": 0,  "unlock_city_level": 5},
    "mall":          {"name": "💎 مرکز تجاری",      "base_cost": 9000, "income_per_level": 120, "maintenance_per_level": 25, "security_per_level": 0,  "unlock_city_level": 6},
}
CITY_BUILDING_UPGRADE_MULTIPLIER = 0.8   # هزینه‌ی هر Level بعدی = base_cost * (1 + level*این‌عدد)

# ═══════════════════════════════════════════════════════════════════════════
# 🏠 ECONOMY PROPERTY — فاز ۵ (Module 11)
# ═══════════════════════════════════════════════════════════════════════════

ECONOMY_PROPERTY_STATE_FILE = "economy_property_state.json"
PROPERTY_INCOME_MAX_ACCRUE_HOURS = 24
PROPERTY_RESALE_FRACTION = 0.6           # فروش ملک = فقط ۶۰٪ ارزش فعلی (Money Sink طبیعی)
PROPERTY_STACK_PRICE_GROWTH = 0.15       # هر ملک اضافه از همون نوع، ۱۵٪ گرون‌تر می‌شه
PROPERTY_UPGRADE_COST_MULTIPLIER = 0.9   # هزینه‌ی ارتقای ملک = base_price * (1 + level*این‌عدد)
RENT_PERIOD_HOURS = 24                   # مدت هر دوره‌ی اجاره (فاز ۵ ارتقا: Rent)

PROPERTY_TYPES = {
    "house":    {"name": "🏠 House",    "rarity": "COMMON",    "base_price": 3000,   "income_per_hour": 8,   "maintenance_per_hour": 2,   "tax_rate": 0.020},
    "apartment":{"name": "🏢 Apartment","rarity": "COMMON",    "base_price": 6000,   "income_per_hour": 18,  "maintenance_per_hour": 5,   "tax_rate": 0.020},
    "shop":     {"name": "🏪 Shop",     "rarity": "UNCOMMON",  "base_price": 10000,  "income_per_hour": 35,  "maintenance_per_hour": 10,  "tax_rate": 0.025},
    "office":   {"name": "🏢 Office",   "rarity": "RARE",      "base_price": 30000,  "income_per_hour": 100, "maintenance_per_hour": 35,  "tax_rate": 0.030},
    "factory":  {"name": "🏭 Factory",  "rarity": "RARE",      "base_price": 25000,  "income_per_hour": 90,  "maintenance_per_hour": 30,  "tax_rate": 0.030},
    "hotel":    {"name": "🏨 Hotel",    "rarity": "EPIC",      "base_price": 60000,  "income_per_hour": 220, "maintenance_per_hour": 70,  "tax_rate": 0.035},
    "mansion":  {"name": "🏰 Mansion",  "rarity": "LEGENDARY", "base_price": 150000, "income_per_hour": 500, "maintenance_per_hour": 150, "tax_rate": 0.040},
}

# با اضافه‌شدن City/Property، این ماموریت‌ها هم اضافه می‌شن:
MISSION_TEMPLATES["weekly"].append(
    {"key": "w_build1", "text": "یک ساختمان توی شهرت بساز یا ارتقا بده", "action": "city_build", "target": 1, "reward": {"coins": 500}}
)
MISSION_TEMPLATES["weekly"].append(
    {"key": "w_property1", "text": "یک ملک بخر", "action": "property_buy", "target": 1, "reward": {"coins": 500}}
)

# ═══════════════════════════════════════════════════════════════════════════
# 🚗 ECONOMY VEHICLE — فاز ۸ (ماژول جدید؛ از صفر ساخته شده)
# ═══════════════════════════════════════════════════════════════════════════

ECONOMY_VEHICLE_STATE_FILE = "economy_vehicle_state.json"

# هر کلید یه مدل واقعی نمونه از هر کلاس (طبق لیست فاز ۸). base_price = قیمت
# نو (قبل از ضریب منطقه). fuel_consumption_per_100km سوخت مصرفی هر ۱۰۰
# کیلومتر (٪ از باک). top_speed/accel/handling فقط نمایشی-مقایسه‌ای‌ان.
VEHICLE_TYPES = {
    "bicycle":    {"name": "🚲 دوچرخه",            "brand": "Generic",  "class": "Bicycle",     "base_price": 500,     "top_speed": 25,  "accel": "کند",     "handling": 9, "fuel_consumption_per_100km": 0,  "maintenance_per_100km": 2,   "tax_rate": 0.0},
    "moto":       {"name": "🏍 موتورسیکلت",        "brand": "Honda",    "class": "Motorcycle",  "base_price": 8000,    "top_speed": 140, "accel": "خوب",     "handling": 8, "fuel_consumption_per_100km": 15, "maintenance_per_100km": 15,  "tax_rate": 0.01},
    "economy":    {"name": "🚗 اکونومی",           "brand": "Kia",      "class": "Economy Car", "base_price": 15000,   "top_speed": 160, "accel": "متوسط",   "handling": 6, "fuel_consumption_per_100km": 22, "maintenance_per_100km": 20,  "tax_rate": 0.015},
    "sedan":      {"name": "🚘 سدان",              "brand": "Toyota",   "class": "Sedan",       "base_price": 28000,   "top_speed": 190, "accel": "متوسط",   "handling": 6, "fuel_consumption_per_100km": 26, "maintenance_per_100km": 28,  "tax_rate": 0.02},
    "suv":        {"name": "🚙 شاسی‌بلند",         "brand": "Hyundai",  "class": "SUV",         "base_price": 45000,   "top_speed": 200, "accel": "متوسط",   "handling": 5, "fuel_consumption_per_100km": 34, "maintenance_per_100km": 40,  "tax_rate": 0.025},
    "sport":      {"name": "🏎 اسپرت",             "brand": "BMW",      "class": "Sport",       "base_price": 90000,   "top_speed": 260, "accel": "خیلی خوب", "handling": 7, "fuel_consumption_per_100km": 40, "maintenance_per_100km": 65,  "tax_rate": 0.03},
    "classic":    {"name": "🚗 کلاسیک",            "brand": "Ford",     "class": "Classic",     "base_price": 55000,   "top_speed": 170, "accel": "ضعیف",    "handling": 4, "fuel_consumption_per_100km": 30, "maintenance_per_100km": 55,  "tax_rate": 0.02},
    "commercial": {"name": "🚚 تجاری",             "brand": "Isuzu",    "class": "Commercial",  "base_price": 35000,   "top_speed": 130, "accel": "ضعیف",    "handling": 4, "fuel_consumption_per_100km": 38, "maintenance_per_100km": 45,  "tax_rate": 0.02},
    "supercar":   {"name": "🏎 سوپراسپرت",         "brand": "Ferrari",  "class": "Supercar",    "base_price": 300000,  "top_speed": 330, "accel": "فوق‌العاده","handling": 9, "fuel_consumption_per_100km": 55, "maintenance_per_100km": 150, "tax_rate": 0.04},
    "luxury":     {"name": "🚘 لوکس",              "brand": "Mercedes", "class": "Luxury",      "base_price": 120000,  "top_speed": 240, "accel": "خوب",     "handling": 7, "fuel_consumption_per_100km": 32, "maintenance_per_100km": 90,  "tax_rate": 0.035},
    "hypercar":   {"name": "🏁 هایپرکار",          "brand": "Bugatti",  "class": "Hypercar",    "base_price": 900000,  "top_speed": 420, "accel": "غیرممکن", "handling": 10,"fuel_consumption_per_100km": 65, "maintenance_per_100km": 300, "tax_rate": 0.05},
}

VEHICLE_RESALE_BASE_FRACTION = 0.6        # مثل Property: فروش به بازار = ۶۰٪ ارزش، ضرب در Condition
VEHICLE_DRIVE_DISTANCE_KM = 80            # هر بار «رانندگی» این‌قدر کیلومتر طی می‌شه
VEHICLE_DRIVE_COOLDOWN_MINUTES = 15
VEHICLE_WEAR_PER_100KM = 3                # هر ۱۰۰ کیلومتر، این‌قدر از Condition کم می‌شه (Durability)
VEHICLE_BASE_ACCIDENT_CHANCE = 0.08       # شانس پایه‌ی تصادف در هر «رانندگی» (بدون بیمه/ضریب منطقه)
VEHICLE_ACCIDENT_DAMAGE_MIN = 10
VEHICLE_ACCIDENT_DAMAGE_MAX = 30
VEHICLE_ACCIDENT_FINE_MIN = 300           # جریمه‌ی نقدی تصادف وقتی بیمه نداری
VEHICLE_ACCIDENT_FINE_MAX = 1500
VEHICLE_INSURANCE_COST_FRACTION = 0.03    # هزینه‌ی بیمه = این‌قدر از قیمت پایه‌ی مدل، هر دوره
VEHICLE_INSURANCE_PERIOD_HOURS = 168      # ۷ روز
VEHICLE_TAX_PERIOD_HOURS = 168            # هر ۷ روز یه‌بار مالیات محاسبه می‌شه
VEHICLE_TAX_GRACE_HOURS = 72              # این‌قدر بعد از موعد، مهلت داری بدون جریمه بدیش
VEHICLE_REPAIR_COST_PER_POINT_FRACTION = 0.01  # تعمیر هر ۱ واحد Condition = این‌قدر از قیمت پایه
VEHICLE_FUEL_COST_PER_PERCENT_FRACTION = 0.003  # سوخت‌گیری هر ۱٪ باک = این‌قدر از قیمت پایه

# ---------- 🔧 Vehicle Tuning — فاز ۹ ----------
# هزینه‌ی هر Level ارتقا = base_price × cost_fraction × (level جدید). هر
# دسته حداکثر max_level داره. تمام اثرها واقعاً توی economy_vehicle.py
# استفاده می‌شن (نه فقط تزئینی)، جز paint که عمداً فقط ظاهریه (طبق فاز ۹:
# Paint واقعاً یه گزینه‌ی بصرفه، نه فانکشنال).
VEHICLE_TUNING = {
    "engine":   {"name": "🔧 موتور",   "max_level": 3, "cost_fraction": 0.08, "speed_bonus_per_level": 15},
    "turbo":    {"name": "💨 توربو",   "max_level": 3, "cost_fraction": 0.10, "speed_bonus_per_level": 25, "fuel_increase_per_level": 0.08},
    "brakes":   {"name": "🛑 ترمز",    "max_level": 3, "cost_fraction": 0.05, "accident_reduction_per_level": 0.08},
    "tires":    {"name": "🛞 لاستیک", "max_level": 3, "cost_fraction": 0.04, "accident_reduction_per_level": 0.06},
    "gearbox":  {"name": "⚙️ گیربکس", "max_level": 3, "cost_fraction": 0.06, "fuel_reduction_per_level": 0.06},
    "exhaust":  {"name": "🎐 اگزوز",   "max_level": 3, "cost_fraction": 0.03, "speed_bonus_per_level": 5},
    "paint":    {"name": "🎨 رنگ",     "max_level": 1, "cost_fraction": 0.02},
    "wheels":   {"name": "🛞 رینگ",    "max_level": 3, "cost_fraction": 0.03, "wear_reduction_per_level": 0.15},
    "audio":    {"name": "🔊 صوتی",    "max_level": 2, "cost_fraction": 0.02, "happiness_bonus_per_level": 3},
    "armor":    {"name": "🛡 زره",     "max_level": 3, "cost_fraction": 0.12, "damage_reduction_per_level": 0.15},
    "gps":      {"name": "🧭 GPS",     "max_level": 1, "cost_fraction": 0.015, "accident_reduction_per_level": 0.05},
    "security": {"name": "🔒 امنیتی", "max_level": 2, "cost_fraction": 0.03, "fine_reduction_per_level": 0.2},
}

MISSION_TEMPLATES["weekly"].append(
    {"key": "w_vehicle1", "text": "یک خودرو بخر", "action": "vehicle_buy", "target": 1, "reward": {"coins": 500}}
)

# ═══════════════════════════════════════════════════════════════════════════
# 🏢 ECONOMY BUSINESS — فاز ۱۲ (ماژول جدید؛ از صفر ساخته شده)
# ═══════════════════════════════════════════════════════════════════════════

ECONOMY_BUSINESS_STATE_FILE = "economy_business_state.json"

# هر نوع کسب‌وکار: base_price (قیمت خرید نو، قبل از ضریب منطقه)،
# base_revenue_per_hour (درآمد پایه در Level 1 با موجودی کافی و بدون کارمند)،
# base_expense_per_hour (اجاره/قبض ثابت، مستقل از موجودی/کارمند)،
# max_employees، employee_cost_per_hour (حقوق هر کارمند)،
# employee_revenue_bonus_frac (هر کارمند این‌قدر درصد به درآمد پایه اضافه می‌کنه)،
# inventory_capacity، inventory_cost_per_unit، inventory_consumption_per_hour
# (بدون موجودی، درآمد صفره — باید مرتب «خرید موجودی» کنی)، tax_rate.
BUSINESS_TYPES = {
    "shop":       {"name": "🛍 فروشگاه",       "base_price": 20000,  "base_revenue_per_hour": 90,  "base_expense_per_hour": 25, "max_employees": 5,  "employee_cost_per_hour": 12, "employee_revenue_bonus_frac": 0.12, "inventory_capacity": 200, "inventory_cost_per_unit": 15, "inventory_consumption_per_hour": 6, "tax_rate": 0.02},
    "restaurant": {"name": "🍽 رستوران",       "base_price": 45000,  "base_revenue_per_hour": 180, "base_expense_per_hour": 55, "max_employees": 8,  "employee_cost_per_hour": 15, "employee_revenue_bonus_frac": 0.10, "inventory_capacity": 150, "inventory_cost_per_unit": 25, "inventory_consumption_per_hour": 8, "tax_rate": 0.025},
    "cafe":       {"name": "☕ کافه",          "base_price": 25000,  "base_revenue_per_hour": 100, "base_expense_per_hour": 30, "max_employees": 4,  "employee_cost_per_hour": 11, "employee_revenue_bonus_frac": 0.13, "inventory_capacity": 180, "inventory_cost_per_unit": 12, "inventory_consumption_per_hour": 7, "tax_rate": 0.02},
    "garage":     {"name": "🔧 تعمیرگاه",      "base_price": 35000,  "base_revenue_per_hour": 140, "base_expense_per_hour": 40, "max_employees": 6,  "employee_cost_per_hour": 18, "employee_revenue_bonus_frac": 0.15, "inventory_capacity": 120, "inventory_cost_per_unit": 30, "inventory_consumption_per_hour": 4, "tax_rate": 0.02},
    "realestate": {"name": "🏢 آژانس املاک",   "base_price": 60000,  "base_revenue_per_hour": 220, "base_expense_per_hour": 60, "max_employees": 6,  "employee_cost_per_hour": 22, "employee_revenue_bonus_frac": 0.14, "inventory_capacity": 50,  "inventory_cost_per_unit": 80, "inventory_consumption_per_hour": 1, "tax_rate": 0.03},
    "itcompany":  {"name": "💻 شرکت IT",       "base_price": 80000,  "base_revenue_per_hour": 260, "base_expense_per_hour": 70, "max_employees": 10, "employee_cost_per_hour": 25, "employee_revenue_bonus_frac": 0.16, "inventory_capacity": 60,  "inventory_cost_per_unit": 60, "inventory_consumption_per_hour": 1, "tax_rate": 0.03},
    "hotel":      {"name": "🏨 هتل",           "base_price": 150000, "base_revenue_per_hour": 420, "base_expense_per_hour": 120,"max_employees": 15, "employee_cost_per_hour": 20, "employee_revenue_bonus_frac": 0.10, "inventory_capacity": 300, "inventory_cost_per_unit": 10, "inventory_consumption_per_hour": 10,"tax_rate": 0.035},
    "factory":    {"name": "🏭 کارخانه",       "base_price": 200000, "base_revenue_per_hour": 520, "base_expense_per_hour": 150,"max_employees": 20, "employee_cost_per_hour": 16, "employee_revenue_bonus_frac": 0.09, "inventory_capacity": 500, "inventory_cost_per_unit": 8,  "inventory_consumption_per_hour": 15,"tax_rate": 0.03},
    "clinic":     {"name": "🏥 کلینیک",        "base_price": 100000, "base_revenue_per_hour": 300, "base_expense_per_hour": 80, "max_employees": 8,  "employee_cost_per_hour": 28, "employee_revenue_bonus_frac": 0.15, "inventory_capacity": 100, "inventory_cost_per_unit": 40, "inventory_consumption_per_hour": 3, "tax_rate": 0.025},
    "cardealer":  {"name": "🚗 فروشگاه خودرو", "base_price": 250000, "base_revenue_per_hour": 480, "base_expense_per_hour": 140,"max_employees": 10, "employee_cost_per_hour": 24, "employee_revenue_bonus_frac": 0.12, "inventory_capacity": 30,  "inventory_cost_per_unit": 2000,"inventory_consumption_per_hour": 0.5,"tax_rate": 0.035},
}

BUSINESS_UPGRADE_COST_MULTIPLIER = 0.8    # هزینه‌ی ارتقا = base_price × این‌عدد × Level جدید
BUSINESS_LEVEL_REVENUE_BONUS = 0.25       # هر Level، ۲۵٪ به درآمد پایه اضافه می‌کنه
BUSINESS_RESALE_FRACTION = 0.55           # فروش به بازار = این‌قدر از ارزش فعلی
BUSINESS_AD_COST_FRACTION = 0.05          # هزینه‌ی تبلیغات = این‌قدر از base_price
BUSINESS_AD_DURATION_HOURS = 12
BUSINESS_AD_REVENUE_MULTIPLIER = 1.3
BUSINESS_MIN_PRICE_MULT = 0.5
BUSINESS_MAX_PRICE_MULT = 2.0
BUSINESS_TAX_PERIOD_HOURS = 168
BUSINESS_MAX_LAZY_HOURS = 72              # بیشتر از این مدت، شبیه‌سازی محاسبه نمی‌شه (سقف تجمع، نه ضرر بی‌نهایت)

MISSION_TEMPLATES["weekly"].append(
    {"key": "w_business1", "text": "یک کسب‌وکار بخر", "action": "business_buy", "target": 1, "reward": {"coins": 800}}
)

# ═══════════════════════════════════════════════════════════════════════════
# 💍 ECONOMY MARRIAGE — فاز ۶ (Module 12)
# ═══════════════════════════════════════════════════════════════════════════

ECONOMY_MARRIAGE_STATE_FILE = "economy_marriage_state.json"
MARRIAGE_RING_COST = 5000                  # هزینه‌ی درخواست ازدواج (شامل حلقه + مراسم)
MARRIAGE_PROPOSAL_EXPIRY_HOURS = 24
MARRIAGE_DIVORCE_COOLDOWN_HOURS = 12       # بعد از طلاق، قبل از ازدواج بعدی باید این‌قدر صبر کنه
MARRIAGE_GIFT_XP_PER_1000_COINS = 10       # هر هدیه، به ازای هر ۱۰۰۰ سکه این‌قدر Couple XP می‌ده
MARRIAGE_XP_PER_LEVEL_BASE = 200

# ═══════════════════════════════════════════════════════════════════════════
# 🐾 ECONOMY PET — فاز ۶ (Module 13)
# ═══════════════════════════════════════════════════════════════════════════

ECONOMY_PET_STATE_FILE = "economy_pet_state.json"

PET_HP_REGEN_PER_MINUTE = 0.5
PET_FEED_COST = 150
PET_FEED_HP_RESTORE = 40
PET_FEED_COOLDOWN_MINUTES = 20

PET_TRAIN_COST = 300
PET_TRAIN_COOLDOWN_MINUTES = 40
PET_TRAIN_XP = 30

PET_FIGHT_COOLDOWN_MINUTES = 20
PET_FIGHT_DAILY_LIMIT = 15
PET_FIGHT_MIN_HP_FRACTION = 0.2            # زیر این سطح HP، پت نمی‌تونه بجنگه (باید غذا بخوره)
PET_FIGHT_XP_WIN = 40
PET_FIGHT_XP_LOSS = 10
PET_FIGHT_DAMAGE_MIN_FRACTION = 0.10
PET_FIGHT_DAMAGE_MAX_FRACTION = 0.35
PET_XP_PER_LEVEL_BASE = 100

# نوع پت -> مشخصات پایه. evolves_at_level = سطحی که می‌تونه تکامل پیدا کنه.
PET_SPECIES = {
    "cat":     {"name": "🐱 گربه",    "rarity": "COMMON",    "cost": 800,   "hp": 50,  "atk": 8,  "def": 6,  "spd": 10, "intel": 7,  "evolves_at_level": 10, "evolved_name": "🐈‍⬛ گربه‌ی وحشی"},
    "dog":     {"name": "🐶 سگ",      "rarity": "COMMON",    "cost": 900,   "hp": 60,  "atk": 10, "def": 8,  "spd": 8,  "intel": 6,  "evolves_at_level": 10, "evolved_name": "🐕‍🦺 سگ جنگی"},
    "rabbit":  {"name": "🐰 خرگوش",   "rarity": "UNCOMMON",  "cost": 1500,  "hp": 40,  "atk": 6,  "def": 5,  "spd": 16, "intel": 9,  "evolves_at_level": 15, "evolved_name": "🐇 خرگوش برق‌آسا"},
    "fox":     {"name": "🦊 روباه",   "rarity": "UNCOMMON",  "cost": 1800,  "hp": 55,  "atk": 12, "def": 7,  "spd": 13, "intel": 11, "evolves_at_level": 15, "evolved_name": "🦊 روباه نه‌دم"},
    "wolf":    {"name": "🐺 گرگ",     "rarity": "RARE",      "cost": 4000,  "hp": 80,  "atk": 16, "def": 12, "spd": 14, "intel": 10, "evolves_at_level": 20, "evolved_name": "🐺 گرگ سایه"},
    "eagle":   {"name": "🦅 عقاب",    "rarity": "RARE",      "cost": 4500,  "hp": 65,  "atk": 18, "def": 9,  "spd": 20, "intel": 12, "evolves_at_level": 20, "evolved_name": "🦅 عقاب طلایی"},
    "tiger":   {"name": "🐯 ببر",     "rarity": "EPIC",      "cost": 10000, "hp": 110, "atk": 25, "def": 18, "spd": 16, "intel": 13, "evolves_at_level": 30, "evolved_name": "🐅 ببر سفید"},
    "dragon":  {"name": "🐉 اژدها",   "rarity": "LEGENDARY", "cost": 30000, "hp": 180, "atk": 40, "def": 30, "spd": 22, "intel": 20, "evolves_at_level": 40, "evolved_name": "🐲 اژدهای باستانی"},
    "phoenix": {"name": "🔥 ققنوس",   "rarity": "MYTHIC",    "cost": 80000, "hp": 220, "atk": 50, "def": 35, "spd": 30, "intel": 28, "evolves_at_level": 50, "evolved_name": "🔥 ققنوس جاودان"},
}

# ═══════════════════════════════════════════════════════════════════════════
# 🎮 ECONOMY MINI GAMES — فاز ۶ (Module 14)
# ═══════════════════════════════════════════════════════════════════════════

ECONOMY_GAMES_STATE_FILE = "economy_games_state.json"

GAMES_COOLDOWN_SECONDS = 30           # کول‌داون مشترک برای تاس/شیرخط/حدس (ضدِ اسپم)
GAMES_DAILY_LIMIT = 50                # سقف روزانه‌ی مشترک همین سه بازی

DICE_MIN_BET = 20
DICE_MAX_BET = 5000
DICE_WIN_MULTIPLIER = 1.9

COINFLIP_MIN_BET = 20
COINFLIP_MAX_BET = 5000
COINFLIP_WIN_MULTIPLIER = 1.9

GUESS_MIN_BET = 20
GUESS_MAX_BET = 2000
GUESS_RANGE = 10                      # حدس بین ۱ تا این عدد
GUESS_WIN_MULTIPLIER = 8.0

XO_MIN_BET = 0
XO_MAX_BET = 10000
XO_GAME_EXPIRY_MINUTES = 30           # اگه بازی نیمه‌کاره ول بشه، بعد این مدت منقضی می‌شه (پول برمی‌گرده)

# با اضافه‌شدن Marriage/Pet/Games، این ماموریت‌ها هم اضافه می‌شن:
MISSION_TEMPLATES["daily"].append(
    {"key": "d_game3", "text": "۳ بار یکی از بازی‌ها (تاس/شیرخط/حدس) رو انجام بده", "action": "minigame_play", "target": 3, "reward": {"coins": 150}}
)
MISSION_TEMPLATES["weekly"].append(
    {"key": "w_petfight3", "text": "۳ بار با پتت مبارزه کن", "action": "pet_fight", "target": 3, "reward": {"coins": 400}}
)

# ═══════════════════════════════════════════════════════════════════════════
# 🎒 ECONOMY INVENTORY 2.0 + ⚡ BOOSTS — فاز ۷ (Module 17 + Module 18)
# سیستم Badge/Timed-Item فعلی (economy_engine.py: grant_timed_item /
# active_timed_items / apply_xp_multiplier / apply_coin_multiplier) دست‌نخورده
# می‌مونه و حذف نمی‌شه؛ این بخش فقط «آیتم‌های قابل‌جمع‌شدن» (Consumable/Pet/
# Collectible) رو به‌عنوان یه لایه‌ی اضافه بهش اضافه می‌کنه.
# ═══════════════════════════════════════════════════════════════════════════

ECONOMY_INVENTORY_STATE_FILE = "economy_inventory_state.json"

# effect: refill_energy / guarantee_next_work / guarantee_escape / pet_snack_heal / None (فقط تزئینی)
ITEM_CATALOG = {
    "energy_drink":     {"name": "⚡ نوشیدنی انرژی‌زا",     "category": "Consumable",  "price": 400,  "rarity": "COMMON",   "description": "انرژی کارت رو فوری پر می‌کنه.", "effect": "refill_energy", "stackable": True, "sellable": True, "tradable": False, "weight": 0.5},
    "lucky_charm":      {"name": "🍀 خرگوش خوش‌شانسی",       "category": "Consumable",  "price": 600,  "rarity": "UNCOMMON", "description": "کار بعدیت تضمینی شکست نمی‌خوره.", "effect": "guarantee_next_work", "stackable": True, "sellable": True, "tradable": False, "weight": 0.2},
    "escape_kit":       {"name": "🗝️ کیت فرار",             "category": "Consumable",  "price": 1000, "rarity": "RARE",     "description": "یه فرار از زندان رو تضمینی موفق می‌کنه.", "effect": "guarantee_escape", "stackable": True, "sellable": True, "tradable": False, "weight": 1.0},
    "pet_snack":        {"name": "🦴 تنقلات پت",             "category": "Pet Item",    "price": 80,   "rarity": "COMMON",   "description": "کمی HP پت رو برمی‌گردونه (ارزون‌تر از «غذا»).", "effect": "pet_snack_heal", "stackable": True, "sellable": True, "tradable": False, "weight": 0.3},
    "collectible_coin": {"name": "🪙 سکه‌ی یادگاری قدیمی",    "category": "Collectible", "price": 2500, "rarity": "RARE",     "description": "یه یادگاری کمیاب، فقط برای نمایش.", "effect": None, "stackable": True, "sellable": True, "tradable": True, "weight": 0.05},
    "collectible_medal":{"name": "🎖️ مدال افتخار",           "category": "Collectible", "price": 5000, "rarity": "EPIC",     "description": "یه یادگاری نایاب، فقط برای نمایش.", "effect": None, "stackable": True, "sellable": True, "tradable": True, "weight": 0.1},
}

# ---------- ⚖️ Weight/Capacity — ارتقای فاز ۱۶ ----------
# ظرفیت پایه‌ی کوله؛ اگه در آینده Level/Upgrade به کوله اضافه شد، این تابع
# می‌تونه از اون هم استفاده کنه (الان فقط مقدار ثابت پایه است).
INVENTORY_BASE_CAPACITY_KG = 50.0

ITEM_ENERGY_DRINK_RESTORE_FRACTION = 1.0   # درصد پر شدن انرژی (۱.۰ = کامل)
ITEM_PET_SNACK_HP_RESTORE = 20

# ---------- ⚡ Boostهای تازه (Additive به همون config.SHOP_ITEMS فعلی؛ از همون
# سیستم Timed Item موجود economy_engine.grant_timed_item استفاده می‌کنن،
# نیازی به کد جدید نداشتن) ----------
SHOP_ITEMS.update({
    "job_boost_6h":      {"name": "💼 تقویت شغل (۶ ساعت, x1.5 Job XP)",   "price": 500, "emoji": "💼", "type": "boost_job",      "duration_hours": 6},
    "pet_xp_boost_6h":   {"name": "🐾 تقویت XP پت (۶ ساعت, x1.5)",        "price": 500, "emoji": "🐾", "type": "boost_pet_xp",   "duration_hours": 6},
    "trading_boost_6h":  {"name": "📈 تقویت ترید (۶ ساعت, x1.5 سود)",     "price": 600, "emoji": "📈", "type": "boost_trading",  "duration_hours": 6},
    "mission_boost_12h": {"name": "🎯 تقویت ماموریت (۱۲ ساعت, x2 جایزه)", "price": 700, "emoji": "🎯", "type": "boost_mission",  "duration_hours": 12},
})
JOB_BOOST_MULTIPLIER = 1.5
PET_XP_BOOST_MULTIPLIER = 1.5
TRADING_BOOST_MULTIPLIER = 1.5
MISSION_BOOST_MULTIPLIER = 2.0

# ═══════════════════════════════════════════════════════════════════════════
# 🖤 ECONOMY BLACK MARKET — فاز ۷ (Module 15)
# ═══════════════════════════════════════════════════════════════════════════

ECONOMY_BLACKMARKET_STATE_FILE = "economy_blackmarket_state.json"
BLACKMARKET_ROTATION_HOURS = 6            # هر چند ساعت موجودی بازار سیاه عوض می‌شه
BLACKMARKET_ITEMS_PER_ROTATION = 3
BLACKMARKET_STOCK_MIN = 1
BLACKMARKET_STOCK_MAX = 5
BLACKMARKET_PRICE_MULTIPLIER_MIN = 0.8    # قیمت بازار سیاه نسبت به قیمت پایه‌ی Item (می‌تونه ارزون‌تر یا گرون‌تر باشه)
BLACKMARKET_PRICE_MULTIPLIER_MAX = 1.6
BLACKMARKET_SELL_FRACTION = 0.5           # فروش کالا به بازار سیاه = نصف قیمت پایه (Money Sink)

BLACKMARKET_AUCTION_DURATION_MINUTES = 60
BLACKMARKET_AUCTION_MIN_INCREMENT = 100

# ═══════════════════════════════════════════════════════════════════════════
# 🌑 ECONOMY UNDERGROUND — فاز ۷ (Module 16)
# صرفاً NPC / Game Mechanics فانتزی؛ هیچ عملیات واقعی هک/نفوذ انجام نمی‌شه.
# ═══════════════════════════════════════════════════════════════════════════

ECONOMY_UNDERGROUND_STATE_FILE = "economy_underground_state.json"
UNDERGROUND_COOLDOWN_MINUTES = 35
UNDERGROUND_DAILY_LIMIT = 10
UNDERGROUND_XP_PER_LEVEL_BASE = 90
UNDERGROUND_RARE_ITEM_CHANCE = 0.08        # شانس گرفتن یه Collectible کمیاب در صورت موفقیت

# نوع قرارداد -> ریسک/پاداش. هر سه از همین ساختار پیروی می‌کنن.
UNDERGROUND_CONTRACTS = {
    "darkweb": {"label": "🌑 دارک وب", "success_chance": 0.55, "reward_min": 300, "reward_max": 900, "xp": 20, "fail_fine_fraction": 0.05},
    "spy":     {"label": "🕵️ جاسوسی", "success_chance": 0.50, "reward_min": 500, "reward_max": 1300, "xp": 28, "fail_fine_fraction": 0.08},
    "hacker":  {"label": "💻 هک (فانتزی)", "success_chance": 0.40, "reward_min": 900, "reward_max": 2500, "xp": 40, "fail_fine_fraction": 0.12},
}

# ═══════════════════════════════════════════════════════════════════════════
# 🏆 ECONOMY ACHIEVEMENTS + TITLES + NET WORTH — فاز ۸ (Module 19 + 21 + 22)
# با سیستم Achievement فعلی (profile_engine._unlock / grant_rewards_for_unlocked
# / all_achievement_labels) یکپارچه می‌شه؛ چیزی از اون سیستم عوض/حذف نمی‌شه،
# فقط یه دسته‌ی جدید از دستاورد بهش اضافه می‌شه (دقیقاً مثل الگوی
# RANK_ACHIEVEMENTS/POLL_ACHIEVEMENTS که از قبل وجود داره).
# ═══════════════════════════════════════════════════════════════════════════

# ساختار دقیقاً مثل RANK_ACHIEVEMENTS/POLL_ACHIEVEMENTS: key -> {"label": ...}
# tier فقط جنبه‌ی نمایشی داره (🥉/🥈/🥇/💎/👑)
ECONOMY_ACHIEVEMENTS = {
    "econ_first_job":        {"label": "💼 اولین شغل", "tier": "🥉"},
    "econ_first_property":   {"label": "🏠 اولین ملک", "tier": "🥉"},
    "econ_first_city":       {"label": "🏙️ اولین شهر", "tier": "🥉"},
    "econ_first_pet":        {"label": "🐾 اولین پت", "tier": "🥉"},
    "econ_first_marriage":   {"label": "💍 اولین ازدواج", "tier": "🥉"},
    "econ_first_investment": {"label": "📈 اولین سرمایه‌گذاری", "tier": "🥉"},
    "econ_first_theft":      {"label": "🦹 اولین سرقت موفق", "tier": "🥈"},
    "econ_first_trade_win":  {"label": "💹 اولین معامله‌ی سودده", "tier": "🥈"},
    "econ_job_level_10":     {"label": "⭐ شغل: Level ۱۰", "tier": "🥈"},
    "econ_thief_level_10":   {"label": "🦹 دزد: Level ۱۰", "tier": "🥈"},
    "econ_pet_evolved":      {"label": "✨ پت تکامل‌یافته", "tier": "🥈"},
    "econ_underground_10":   {"label": "🌑 زیرزمین: Level ۱۰", "tier": "🥇"},
    "econ_bank_level_5":     {"label": "🏦 بانک: بالاترین سطح", "tier": "🥇"},
    "econ_net_worth_bronze": {"label": "🥉 ثروت: ۱۰,۰۰۰", "tier": "🥉"},
    "econ_net_worth_silver": {"label": "🥈 ثروت: ۱۰۰,۰۰۰", "tier": "🥈"},
    "econ_net_worth_gold":   {"label": "🥇 ثروت: ۱,۰۰۰,۰۰۰ (اولین میلیون)", "tier": "🥇"},
    "econ_net_worth_diamond":{"label": "💎 ثروت: ۱۰,۰۰۰,۰۰۰", "tier": "💎"},
    "econ_net_worth_legend": {"label": "👑 ثروت: ۱۰۰,۰۰۰,۰۰۰", "tier": "👑"},
}
# آستانه‌های ثروت جدا نگه داشته شدن که با economy_achievements.py راحت خونده بشن
ECONOMY_NET_WORTH_THRESHOLDS = {
    "econ_net_worth_bronze": 10_000,
    "econ_net_worth_silver": 100_000,
    "econ_net_worth_gold": 1_000_000,
    "econ_net_worth_diamond": 10_000_000,
    "econ_net_worth_legend": 100_000_000,
}
# جایزه‌ی هر دستاورد اقتصادی (به همون ACHIEVEMENT_REWARDS بالا اضافه می‌شه، Additive)
ACHIEVEMENT_REWARDS.update({
    "econ_first_job": {"xp": 20, "coins": 100},
    "econ_first_property": {"xp": 30, "coins": 200},
    "econ_first_city": {"xp": 30, "coins": 200},
    "econ_first_pet": {"xp": 30, "coins": 200},
    "econ_first_marriage": {"xp": 40, "coins": 300},
    "econ_first_investment": {"xp": 30, "coins": 200},
    "econ_first_theft": {"xp": 40, "coins": 250},
    "econ_first_trade_win": {"xp": 40, "coins": 250},
    "econ_job_level_10": {"xp": 100, "coins": 800},
    "econ_thief_level_10": {"xp": 100, "coins": 800},
    "econ_pet_evolved": {"xp": 100, "coins": 800},
    "econ_underground_10": {"xp": 150, "coins": 1200},
    "econ_bank_level_5": {"xp": 150, "coins": 1200},
    "econ_net_worth_bronze": {"xp": 50, "coins": 0},
    "econ_net_worth_silver": {"xp": 150, "coins": 0},
    "econ_net_worth_gold": {"xp": 500, "coins": 0},
    "econ_net_worth_diamond": {"xp": 1500, "coins": 0},
    "econ_net_worth_legend": {"xp": 5000, "coins": 0},
})

# ---------- 👑 Economy Titles (فقط نمایشی، بر اساس Net Worth/Level لحظه‌ای محاسبه می‌شن) ----------
ECONOMY_TITLES = [
    # (حداقل Net Worth، عنوان) — از بزرگ به کوچیک چک می‌شه، اولین Match برنده‌ست
    (100_000_000, "👑 سرمایه‌دار"),
    (10_000_000, "🌑 سلطان زیرزمین"),
    (1_000_000, "💎 میلیونر"),
    (100_000, "💰 ثروتمند"),
    (10_000, "📈 سرمایه‌گذار"),
]

# ═══════════════════════════════════════════════════════════════════════════
# 👑 ECONOMY LEADERBOARDS — فاز ۸ (Module 23)
# ═══════════════════════════════════════════════════════════════════════════

LEADERBOARD_DISPLAY_COUNT = 10
LEADERBOARD_CATEGORIES = {
    "wealth":  "💰 ثروتمندترین",
    "bank":    "🏦 بیشترین موجودی بانک",
    "trading": "📈 بهترین تریدر",
    "job":     "💼 بهترین شغل",
    "city":    "🏙️ بهترین شهر",
    "property":"🏠 بیشترین املاک",
    "pet":     "🐾 بهترین پت",
    "theft":   "🦹 موفق‌ترین دزد",
    "game":    "🎮 بهترین بازیکن",
    "reputation": "⭐ بیشترین Reputation",
    "achievement": "🏆 بیشترین Achievement",
}

# ═══════════════════════════════════════════════════════════════════════════
# 🎁 ECONOMY EVENTS — فاز ۹ (Module 24)
# رویدادهای تصادفی سطح گروه که چند ماژول رو هم‌زمان تحت تأثیر قرار می‌دن.
# ═══════════════════════════════════════════════════════════════════════════

ECONOMY_EVENTS_STATE_FILE = "economy_events_state.json"
EVENT_TRIGGER_CHANCE_PER_CHECK = 0.03    # هر بار که چک می‌شه (مثلاً هنگام کار کردن)، این‌قدر شانس شروع رویداد جدید هست
EVENT_MIN_GAP_MINUTES = 45               # حداقل فاصله بین دو رویداد (جلوگیری از پشت‌سرهم اومدن)

ECONOMY_EVENTS = {
    "double_income":  {"label": "🎁 Double Income",      "effect": "income_multiplier",       "value": 2.0, "duration_minutes": 30},
    "bonus_hour":      {"label": "💰 Bonus Hour",          "effect": "income_multiplier",       "value": 1.5, "duration_minutes": 60},
    "bank_bonus":      {"label": "🏦 Bank Bonus",          "effect": "bank_interest_multiplier","value": 2.0, "duration_minutes": 120},
    "shop_discount":   {"label": "🛒 Shop Discount",       "effect": "shop_discount",           "value": 0.8, "duration_minutes": 90},
    "pet_weekend":     {"label": "🐾 Pet Weekend",         "effect": "pet_xp_multiplier",       "value": 2.0, "duration_minutes": 180},
    "job_festival":    {"label": "💼 Job Festival",        "effect": "job_xp_multiplier",       "value": 2.0, "duration_minutes": 90},
    "underground_night": {"label": "🌑 Underground Night", "effect": "underground_reward_multiplier", "value": 1.5, "duration_minutes": 90},
    "rare_item_event": {"label": "💎 Rare Item Event",     "effect": "rare_item_chance_multiplier", "value": 3.0, "duration_minutes": 60},
    # ---------- ارتقای فاز ۱۵: رویدادهای منفی/بحرانی (طبق درخواست خودِ کاربر:
    # رکود، بحران سوخت، تورم، کاهش/افزایش تقاضا) — قبلاً فقط رویداد مثبت بود ----------
    "recession":       {"label": "📉 رکود اقتصادی",        "effect": "income_multiplier",       "value": 0.7, "duration_minutes": 45},
    "fuel_crisis":     {"label": "⛽ بحران سوخت",          "effect": "fuel_cost_multiplier",    "value": 1.8, "duration_minutes": 60},
    "inflation":       {"label": "📈 تورم",                "effect": "price_inflation_multiplier", "value": 1.25, "duration_minutes": 90},
    "demand_drop":     {"label": "🧺 کاهش تقاضا",          "effect": "demand_multiplier",       "value": 0.7, "duration_minutes": 60},
    "demand_surge":    {"label": "🔥 افزایش تقاضا",        "effect": "demand_multiplier",       "value": 1.4, "duration_minutes": 45},
}

# ═══════════════════════════════════════════════════════════════════════════
# 🛒 SHOP 2.0 — فاز ۹ (Module 25)
# فروشگاه فعلی (SHOP_ITEMS/buy_command) دست‌نخورده می‌مونه؛ این فقط یه
# «پیشنهاد ویژه‌ی روزانه» اضافه می‌کنه که بدون هیچ Stateای، از تاریخ روز
# به‌صورت Deterministic محاسبه می‌شه (همه‌ی اعضای گروه دقیقاً یه پیشنهاد رو می‌بینن).
# ═══════════════════════════════════════════════════════════════════════════

DAILY_DEAL_DISCOUNT_FRACTION = 0.35   # ۳۵٪ تخفیف

# ═══════════════════════════════════════════════════════════════════════════
# 🧠 ANTI-ABUSE — فاز ۹ (Module 27)
# هر ماژول از فاز ۱ به بعد خودش Cooldown/Daily-Limit مخصوص خودشو داره؛ این
# بخش فقط یه لایه‌ی سراسری اضافه می‌کنه: حداقل فاصله‌ی زمانی بین *هر* دو
# Actionِ اقتصادی از یه کاربر، صرف‌نظر از این‌که کدوم Command باشه (جلوگیری
# از سوءاستفاده‌ی «تعویض سریع بین چند Command برای دور زدن Cooldown تکی»).
# ═══════════════════════════════════════════════════════════════════════════

GLOBAL_ACTION_MIN_GAP_SECONDS = 1.5

# ═══════════════════════════════════════════════════════════════════════════
# 🛠️ ECONOMY ADMIN + MODULE TOGGLES + MENU — فاز ۱۰ (Module 30 + 31 + 32)
# ═══════════════════════════════════════════════════════════════════════════

# کلید Toggle -> (نام نمایشی، نام Command برای فعال/غیرفعال کردن)
ECONOMY_MODULE_TOGGLES = {
    "economy_module_jobs":        "💼 شغل و درآمد",
    "economy_module_bank":        "🏦 بانک",
    "economy_module_market":      "📈 بازار و ترید",
    "economy_module_theft":       "🦹 دزدی",
    "economy_module_city":        "🏙️ شهر",
    "economy_module_property":    "🏠 املاک",
    "economy_module_district":    "🗺️ منطقه‌های شهر",
    "economy_module_vehicle":     "🚗 خودرو",
    "economy_module_business":    "🏢 کسب‌وکار",
    "economy_module_marriage":    "💍 ازدواج",
    "economy_module_pet":         "🐾 پت",
    "economy_module_games":       "🎮 بازی‌ها",
    "economy_module_blackmarket": "🖤 بازار سیاه",
    "economy_module_underground": "🌑 دنیای زیرزمینی",
}

# ---------- 🎛 Economy Admin Center — فاز ۲۶ ----------
SUSPICIOUS_TRANSACTION_THRESHOLD = 50000   # تراکنش‌های بالای این مقدار، توی «سلامت اقتصاد» فلگ می‌شن
SUSPICIOUS_WINDOW_HOURS = 24                # فقط تراکنش‌های این‌قدر ساعت اخیر رو بررسی کن
ADMIN_AUDIT_LOG_MAX = 200                   # حداکثر تعداد رکورد Audit Log ادمین که نگه داشته می‌شه

# متن منوی اقتصاد (Module 32) — طبق فرمت درخواستی خودتون
ECONOMY_MENU_TEXT = """💰 اقتصاد DIGIANTI

├ 💵 کسب درآمد — «کار» / «هاپ هاپ»
├ 💼 شغل‌ها — «مشاغل» / «شغل»
├ 🎯 ماموریت‌ها — «ماموریت‌ها»
├ 🏦 بانک — «بانک» / «سپرده» / «برداشت»
├ 📈 بازار — «بازار» / «قیمت» / «خرید» / «فروش»
├ 💹 ترید — «ترید [مبلغ]»
├ 🛡️ بیمه — «بیمه» / «خرید بیمه»
├ 🦹 دزدی — «دزدی» (ریپلای)
├ 🚔 زندان — «زندان» / «تحت تعقیب» / «فرار»
├ 🏙️ شهر — «شهر» / «ساخت شهر» / «ساختمان‌ها»
├ 🏠 املاک — «املاک» / «خرید ملک» / «ملک من»
├ 💍 ازدواج — «درخواست ازدواج» / «همسر»
├ 🐾 پت — «پت‌ها» / «خرید پت» / «فایت»
├ 🎮 بازی‌ها — «تاس» / «شیر یا خط» / «حدس» / «اکس او»
├ 🖤 بازار سیاه — «بازار سیاه» / «حراجی»
├ 🌑 دنیای زیرزمینی — «دارک وب» / «جاسوس» / «هکر»
├ 🎒 اینونتوری — «کیف» / «آیتم‌های من»
├ 🏆 دستاوردها — «دستاوردها»
├ 👑 رتبه‌ها — «ثروتمندان» / «برترین‌ها»
└ 📊 آمار اقتصاد — «ارزش خالص» / «آمار اقتصاد» / «سلامت اقتصاد»"""

