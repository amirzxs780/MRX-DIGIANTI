"""
استاب مینیمال کتابخانه‌ی python-telegram-bot - فقط برای اجرای تست‌های خودکار
(tests/) توی محیطی که دسترسی اینترنت/pip نداره و نمی‌تونه python-telegram-bot
واقعی (طبق requirements.txt) رو نصب کنه. این پکیج هیچ‌وقت توی دیپلوی واقعی ربات
استفاده نمی‌شه - اونجا کتابخانه‌ی واقعی (22.7) طبق requirements.txt نصب می‌شه.
فقط همون چندتا نام (Update/InlineKeyboardButton/...) که ماژول‌های تست‌شده
(profile_engine, poll_engine, economy_missions, economy_admin, ...) در سطح
import به‌شون نیاز دارن رو فراهم می‌کنه؛ هیچ‌کدوم از منطق واقعی تلگرام رو
پیاده‌سازی نمی‌کنه چون منطق اقتصادی که تست می‌کنیم هیچ‌وقت واقعاً این آبجکت‌ها
رو صدا نمی‌زنه.
"""


class Update:
    pass


class InlineKeyboardButton:
    def __init__(self, *args, **kwargs):
        pass


class InlineKeyboardMarkup:
    def __init__(self, *args, **kwargs):
        pass
