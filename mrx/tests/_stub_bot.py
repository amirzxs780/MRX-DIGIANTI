"""
استاب سبک bot.py - فقط برای تست‌های خودکار (tests/). ماژول‌های اقتصادی
(economy_core, profile_engine, poll_engine, ...) موقع اجرا `import bot as host`
می‌کنن تا به globalهای مشترک (`_wallet`, `_xp`, `_achievements`, ...) برسن.
import کردنِ bot.py واقعی برای تست غیرعملیه (۸۸۰۰ خط، نیاز به کل
python-telegram-bot + توکن واقعی + راه‌اندازی Application). این استاب فقط
همون چندتا container‌ی حافظه‌ای رو که منطق اقتصادیِ تست‌شده واقعاً بهشون دست
می‌زنه فراهم می‌کنه - هیچ ربطی به دیپلوی واقعی نداره.
"""
from collections import defaultdict

_wallet = defaultdict(lambda: defaultdict(int))
_xp = defaultdict(lambda: defaultdict(int))
_achievements = defaultdict(lambda: defaultdict(set))
