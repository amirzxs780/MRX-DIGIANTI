# DIGNANTI V17 — گزارش کامل آدیت فنی/مالی/امنیتی

تاریخ: ۲۰۲۶ · دامنه: `mrx_fixed_v17` کامل (۳۷ فایل Python، ~۳۲٬۵۰۰ خط)
اصل راهنما در کل این آدیت: **بازنویسی نشد، معماری عوض نشد، فقط تا جایی که
واقعاً باگ/ریسکِ قابل‌اثبات بود فیکس شد.** هر فیکس داخل همون فایل، با کامنتِ
فارسیِ «فیکس ...» مشخص شده تا دقیقاً معلوم باشه چی و چرا عوض شده.

---

## ۱. خلاصه‌ی مدیریتی

- **۳ محل** که قانون معماریِ اصلیِ پروژه («هیچ ماژولی مستقیم `host._wallet`/بانک
  رو دست نزنه») رو نقض می‌کردن، پیدا و فیکس شدن: `profile_engine.grant_rewards_for_unlocked`،
  `poll_engine._grant_vote_reward`، و `economy_admin.reset_user_economy_command`.
- **۴ مسیرِ پاداش** که Idempotency Key نداشتن (و در نتیجه زیر شرایط خاص
  می‌تونستن دوبار پرداخت بشن) فیکس شدن: Achievement، Poll Vote، Mission،
  Daily Reward، و یه باگِ «Repeated Callback» واقعی توی دکمه‌ی Bulk-Bonus
  ادمینی (`econquick`).
- **۲ ضعف امنیتیِ کم‌خطر** توی `owner_panel.py` (سیستم ورود ادمینِ پویا) فیکس
  شد: نشتِ اطلاعاتِ Username Enumeration، و مقایسه‌ی غیر-Constant-Time هش رمز.
- **عدم تقارنِ fsync** بین `economy_core.py` (که از اول `fsync` داشت) و بقیه‌ی
  ۲۸ ماژول (که نداشتن) پیدا و برای همه یکسان‌سازی شد - تضمینِ بیشتر در برابر
  از دست رفتنِ داده حین کرش/قطعیِ برق.
- **۲۷ تست خودکار جدید** نوشته و اجرا شدن (هم‌زمانی/Idempotency/Persistence) —
  همه Pass.
- هیچ Command/Callback/ساختار دیتای موجودی حذف یا Rename نشد. سازگاری با
  داده‌ی قدیمی (فایل‌های state فعلی) کامل حفظ شده — هیچ Migration دستی لازم
  نیست.
- بزرگ‌ترین ریسکِ باقی‌مانده (مستندشده، نه فیکس‌شده): عدم اتمیسیتیِ Cross-File
  بین فایلِ `bot.py` (موجودیِ کیف‌پول) و فایلِ `economy_core` (Ledger) حین یه
  کرشِ دقیقاً هم‌زمان با Save؛ نیاز به یکی‌کردنِ کل Persistence پروژه داره که
  یه بازنویسیِ معماریِ بزرگه (خارج از اسکوپِ این آدیت). جزییات در بخش ۷.

---

## ۲. مشکلات شناسایی‌شده (کامل) — فایل، خط، چرا، فیکس

### 🔴 Phase 2 — نقضِ «تنها مرجع مالی» (Economy Core Enforcement)

| # | فایل:خط (قبل از فیکس) | وضعیتِ قبلی | چرا مشکل بود | فیکس |
|---|---|---|---|---|
| 1 | `profile_engine.py:519` (`grant_rewards_for_unlocked`) | `host._xp[...] += xp_gain` / `host._wallet[chat_id][uid] += coin_gain` مستقیم | بدون Ledger، بدون Idempotency، بدون چک فریز اقتصاد؛ جایزه‌ی Achievement هیچ‌جا توی `/transactions` یا گزارش‌های اقتصادی دیده نمی‌شد | از `economy_core.add_coins_sync(..., idempotency_key=f"achievement:{chat_id}:{uid}:{key}")` عبور داده شد؛ `DuplicateTransactionError`/`EconomyFrozenError` گرفته و Log می‌شن بدون کرش‌دادنِ کلِ فراخوانی |
| 2 | `poll_engine.py:333` (`_grant_vote_reward`) | `host._wallet[poll["chat_id"]][uid] += reward` مستقیم + یه `try/except` بی‌سروصدا به `economy_engine.log_transaction` | همون مشکلِ بالا؛ ضمناً اگه `log_transaction` Exception می‌داد، کلاً هیچ اثری (even legacy log) ثبت نمی‌شد | از `economy_core.add_coins_sync(..., idempotency_key=f"poll_reward:{poll_id}:{uid}")` عبور داده شد (دیگه لازم نیست جدا `log_transaction` صدا بزنیم - خودِ `record_transaction` داخلیِ Core این کارو می‌کنه). امضای `record_vote`/`_grant_vote_reward` یه پارامتر `poll_id` اضافه گرفت (برای کلید Idempotency)؛ تنها Caller (`bot.py:3727`) آپدیت شد. |
| 3 | `economy_admin.py:308-309` (`reset_user_economy_command`) | `host._wallet[chat.id].pop(target_id, None)` و `economy_core._bank[chat.id].pop(target_id, None)` مستقیم | موجودیِ کاربر بدون هیچ رکوردی توی Ledger صفر می‌شد - غیرقابل حسابرسی؛ اگه کاربر بعداً بپرسه «پولم کجا رفت»، هیچ اثری توی `/transactions` نبود | جایگزین شد با `await economy_core.admin_set_balance(chat.id, target_id, 0, ...)` + `await economy_core.remove_bank_coins(...)` (هر دو از APIهای رسمیِ ادمینیِ Core، با Ledger کامل) |
| — | `economy_balance.py:40` | `economy_core._bank.get(chat.id, {})` مستقیم خونده می‌شه (Read-only) | این **نقض نیست** (Phase 2 فقط نوشتن رو منع می‌کنه)؛ صرفاً یه نکته‌ی سبک‌نویسی - می‌تونست از یه Getter عمومی استفاده کنه | فیکس نشد (ریسک صفر، فقط یه یادداشتِ Style) |

تمام ۳۷ فایل پروژه برای الگوهای `host._wallet[...] = /+= /-=`، `economy_core._bank[...]`،
و alias های مختلفِ `import bot as host` جست‌وجو شدن (شامل تمام فایل‌هایی که
این الگو رو دارن). بعد از فیکس، **تنها فایلی که `host._wallet`/`_bank` رو
می‌نویسه خودِ `economy_core.py` است.**

### 🟠 Phase 3 — Idempotency گم‌شده روی مسیرهای پاداش

| # | فایل:تابع | مشکل | فیکس |
|---|---|---|---|
| 4 | `economy_missions.py:record_progress` | `m["claimed"]=True` ست می‌شد ولی `add_coins` بدون `idempotency_key` صدا زده می‌شد؛ اگه یه بازنشانیِ دستیِ `claimed` یا بازیابیِ state از بکاپِ قدیمی‌تر رخ بده، همون ماموریت می‌تونست دوباره پرداخت بشه | `idempotency_key=f"mission_reward:{chat_id}:{uid}:{period}:{period_key}:{mission_key}"` اضافه شد (کلید شاملِ `period_key` یعنی هر نمونه‌ی جدیدِ همون Template توی بازه‌ی بعدی خودکار کلید جدا می‌گیره) |
| 5 | `bot.py:daily_command` | `economy_core.add_coins(...)` بدون `idempotency_key`؛ `_last_daily[...] = now` *بعد* از پرداخت ست می‌شه - اگه سرور دقیقاً بین موفقیتِ `add_coins` و Persist شدنِ `_last_daily` کرش کنه، بعد از ری‌استارت `_last_daily` به مقدار قدیمی برمی‌گرده و کاربر می‌تونه فوراً دوباره `/daily` بزنه و همون روز رو دوبار جایزه بگیره | `idempotency_key=f"daily_reward:{chat_id}:{uid}:{YYYY-MM-DD UTC}"` اضافه شد؛ `DuplicateTransactionError` گرفته می‌شه و پیام مناسب نشون داده می‌شه |
| 6 | `bot.py` — دکمه‌ی تاییدِ `econquick:bonusXX` (پاداشِ دسته‌جمعیِ ادمینی) | **این یه باگِ واقعی و قابل سوءاستفاده بود**: callback_data این دکمه‌ی تایید یه مقدارِ ثابته (`econquick:bonus20` مثلاً)؛ اگه تلگرام همین Tap رو دوباره Deliver کنه (شناخته‌شده: Retry شبکه/دابل‌تپ)، کلِ لیستِ اعضای شناخته‌شده‌ی گروه **دوباره** جایزه می‌گرفتن - بدون هیچ محدودیتی | `idempotency_key=f"econquick_bonus:{chat_id}:{query.id}:{uid}"` برای هر عضو؛ `query.id` شناسه‌ی یکتای تلگرامیِ همون Tap واقعیه، پس یه Tap *جدید* و واقعی (که `query.id` جدید داره) هنوزم مجازه |
| — | «Referral rewards» (توی Phase 3 اسم برده شده) | **این قابلیت اصلاً توی پروژه وجود نداره** (فقط یه پیام خوش‌آمدگویی برای لینک دعوت، بدون جایزه‌ی سکه‌ای) | N/A — چیزی برای فیکس نیست |
| — | پاداشِ «activity» (`bot.py` - هر پیام) | Idempotency Key نداره | **عمداً فیکس نشد**: این یه پاداشِ «قطره‌ای» و پیوسته‌ست (نه یه Claim گسسته با یه هویتِ ثابت مثل Achievement/Mission)؛ هیچ «شناسه‌ی تکرارپذیر»ی براش معنی نداره - دو اجرای جدا یعنی دو پیامِ واقعیِ جدا، نه یه Duplicate |

### 🟡 Phase 7 — امنیت (`owner_panel.py` / `economy_panel.py` / `moderation_engine.py`)

| # | فایل:خط | مشکل | شدت | فیکس |
|---|---|---|---|---|
| 7 | `owner_panel.py:_try_claim` | پیامِ خطا برای «یوزرنیم ناموجود» با «یوزرنیمِ معتبر ولی رمزِ غلط» فرق داشت → Username/Invite Enumeration (مهاجم می‌تونست بفهمه کدوم یوزرنیم‌ها همین الان دعوتِ زنده دارن) | پایین (طرف مقابل باید از قبل حدس بزنه یه Invite زنده‌ست، و بعدش هنوزم محدود به ۵ تلاشه) | پیامِ «یوزرنیم پیدا نشد» و «دعوت منقضی‌شده» یکسان شدن با یه پیامِ عمومی. (محدودیتِ باقی‌مونده: پیامِ «رمز اشتباهه، N تلاش دیگه داری» هنوز با پیامِ عمومی فرق داره - برای حفظِ UX مفیدِ «چند تلاش مونده» برای کاربرِ واقعی؛ این یه Trade-off آگاهانه‌ست، نه یه باگِ جامونده) |
| 8 | `owner_panel.py:_try_claim` | مقایسه‌ی `_hash(password) != invite["password_hash"]` با `!=` معمولی (نه Constant-Time) | خیلی پایین (کانال تلگرامه، نه تماسِ لوکال؛ Jitter شبکه هرگونه تفاوتِ زمانیِ نانوثانیه‌ای رو کاملاً می‌پوشونه) | با `hmac.compare_digest` جایگزین شد (هزینه‌ی فیکس صفره) |
| — | `owner_panel.py:panel_callback_handler` | چک شد: `is_owner(user.id)` **روی خودِ هر callback** (نه فقط موقع باز کردنِ منو) چک می‌شه | ✅ از قبل درست بود | فیکس لازم نبود |
| — | `economy_panel.py:handle_callback` | چک شد: هیچ اکشنی `target_id` از callback_data نمی‌گیره؛ همه‌چیز روی `update.effective_user.id` خودِ فرستنده عمل می‌کنه | ✅ از قبل درست بود (و چون self-service است، اصلاً نیازی به چک دسترسیِ ادمین نداره) | فیکس لازم نبود |
| — | `bot.py` — دیسپچرِ پنلِ مدیریتیِ گروه (`friend_add`/`vip_add`/`mute_quick`/`ban_quick`/`promote`/...) | چک شد: هر Action جدا `has_permission(user.id, ...)` رو چک می‌کنه، حتی موقعِ پردازشِ ورودیِ متنیِ بعد از کلیکِ دکمه (نه فقط موقعِ باز شدنِ دکمه) | ✅ از قبل درست بود (دقیقاً الگوی «هیچ‌وقت به مخفی‌بودنِ دکمه تکیه نکن») | فیکس لازم نبود |
| — | `moderation_engine.py` | چک شد: `can_moderate`/`has_permission(..., "moderate")` قبل از هر Warn/Mute/Ban چک می‌شه (هم از دستورِ متنی، هم از دکمه‌های پنل) | ✅ از قبل درست بود | فیکس لازم نبود |
| — | Replay سطحِ Telegram Update (نه Callback) | بررسی شد: `run_polling` استفاده شده - خودِ کتابخانه‌ی `python-telegram-bot` آفسِتِ `getUpdates` رو مدیریت می‌کنه، پس Redelivery در سطحِ Update (نه Callback تکی) ریسکِ اضافه‌ای نداره | — | نیازی به فیکس نبود (مسئولیتِ کتابخانه) |

### 🟢 Phase 8 — یکپارچگیِ داده/Persistence

| # | یافته | شدت | فیکس |
|---|---|---|---|
| 9 | همه‌ی ۲۸ ماژولِ دیگه (غیر از `economy_core.py`) بعد از نوشتنِ JSON به فایلِ `.tmp`، قبل از `os.replace`، `f.flush()`/`os.fsync()` صدا نمی‌زدن - یعنی ممکن بود دیتا توی بافرِ سطحِ OS بمونه و با قطعیِ برق (نه فقط کرشِ پردازه) از دست بره | متوسط (فقط سناریوی قطعیِ برق/کرشِ هسته، نه کرشِ خودِ پایتون) | با یه اسکریپتِ خودکار (نه دستی، برای جلوگیری از خطای تایپی) همون ۲ خطِ `f.flush(); os.fsync(f.fileno())` که `economy_core.py` از قبل داشت، به تمام ۲۸ ماژولِ دیگه اضافه شد. بعدش هر ۳۷ فایل با `py_compile` چک شدن (Syntax سالم) |
| — | Backup/Recovery: چک شد همه‌ی ماژول‌ها (نه فقط `economy_core`) از همون الگوی «قبل از نوشتن، از فایلِ فعلی `.bak` بگیر» و «موقعِ Load، اگه اصلی خراب بود از `.bak` بازیابی کن» استفاده می‌کنن | ✅ از قبل درست و یکدست بود | فیکس لازم نبود |
| — | کرش‌تستِ واقعی (شبیه‌سازیِ JSON خراب، شبیه‌سازیِ شکستِ `os.replace`) | — | با تستِ خودکار پوشش داده شد (بخش ۴) |

---

## ۳. فایل‌های تغییریافته (۳۰ فایل)

```
economy_core.py        + add_coins_sync (API جدید، هیچ امضای موجودی عوض نشد)
profile_engine.py       فیکس #1 (grant_rewards_for_unlocked) + fsync
poll_engine.py          فیکس #2 (_grant_vote_reward/record_vote) + fsync
economy_admin.py        فیکس #3 (reset_user_economy_command) + fsync
economy_missions.py     فیکس #4 (idempotency_key ماموریت) + fsync
bot.py                  فیکس #5 (daily_command) + فیکس #6 (econquick bulk-bonus)
owner_panel.py          فیکس #7 (username enumeration) + فیکس #8 (hmac) + fsync
autoreply_engine.py                                         fsync
economy_bank.py / blackmarket / business / city / clothing /
district / engine / events / games / inventory / jobs /
market / marriage / pet / property / security / theft /
underground / vehicle                                       fsync (۱۸ فایل)
lock_engine.py / moderation_engine.py / purge_engine.py /
spam_engine.py                                               fsync (۴ فایل)
```

هیچ فایلِ دیگه‌ای لمس نشده. هیچ Command، Callback، نام تابع، یا ساختار JSON
عمومی تغییر نکرده (تنها «تغییرِ امضا»: `record_vote`/`_grant_vote_reward` یه
پارامترِ اختیاریِ جدید `poll_id=None` گرفتن - کاملاً Backward-Compatible، چون
مقدارِ پیش‌فرضش None است و فقط یه لایه‌ی اضافیِ Idempotency رو غیرفعال می‌کنه،
نه رفتارِ اصلی رو).

---

## ۴. تست‌های جدید/تغییریافته + نتیجه‌ی واقعی اجرا

فایل: `tests/test_economy_audit.py` (۲۷ تست، `unittest.IsolatedAsyncioTestCase`
— بدون نیازِ pytest). اجرا:

```bash
export BOT_TOKEN=dummy_for_tests
python3 -m unittest tests.test_economy_audit -v
```

**نتیجه‌ی واقعیِ اجرا (کپی مستقیم از خروجیِ ترمینال):**

```
test_concurrent_access_same_user_wallet_add_and_remove ... ok
test_concurrent_transfers_conserve_total_money ... ok
test_concurrent_transfers_multiple_users_many_pairs ... ok
test_many_concurrent_removes_never_go_negative ... ok
test_mission_reward_idempotent_across_period ... ok
test_poll_reward_paid_once_even_with_duplicate_record_vote ... ok
test_repeated_achievement_unlock_pays_once ... ok
test_two_simultaneous_purchases_no_negative_balance ... ok
test_two_simultaneous_withdrawals_no_negative_bank ... ok
test_add_remove_round_trip_and_ledger ... ok
test_admin_adjust_balance_clamps_at_zero ... ok
test_admin_set_balance_records_delta_not_amount ... ok
test_economy_freeze_blocks_normal_ops_not_admin_ops ... ok
test_remove_coins_allow_bank_spills_over ... ok
test_remove_coins_never_negative ... ok
test_rollback_transaction_reverses_reward ... ok
test_transfer_conserves_total ... ok
test_transfer_insufficient_funds_leaves_state_untouched ... ok
test_add_coins_idempotency_key_blocks_sequential_duplicate ... ok
test_add_coins_sync_idempotency_key_blocks_duplicate ... ok
test_concurrent_duplicate_idempotency_key_only_one_wins ... ok
test_different_keys_both_paid ... ok
test_backup_created_on_second_save ... ok
test_corrupted_main_file_recovers_from_backup ... ok
test_missing_state_file_starts_clean_without_crash ... ok
test_save_failure_does_not_corrupt_existing_file ... ok
test_save_then_load_round_trip ... ok

----------------------------------------------------------------------
Ran 27 tests in 0.143s

OK
```

**صفر Failure.** (توی این گزارش هیچ نتیجه‌ی تستی جعل/گرد نشده - این دقیقاً
همون خروجیِ واقعیِ آخرین اجراست.)

### چی رو این تست‌ها واقعاً اثبات می‌کنن (Phase 6)

- دو خرید/برداشتِ هم‌زمان روی یه کاربر → فقط یکی موفق، موجودی هیچ‌وقت منفی نشد
- ۲۰ تا حذفِ هم‌زمانِ ۱۵۰ تایی از ۱۰۰۰ → دقیقاً ۶ بار موفق (۹۰۰)، نه کمتر نه بیشتر
- انتقالِ هم‌زمان و دوطرفه بینِ کاربرها (تستِ Deadlock) → مجموع پول قبل/بعد دقیقاً برابر
- ۴۰ انتقالِ تصادفیِ هم‌زمان بینِ ۵ کاربر → مجموع پول حفظ شد، هیچ موجودیِ منفی
- ۲۰ تا تلاشِ هم‌زمان با **یه `idempotency_key`** → دقیقاً ۱ موفق، ۱۹ تا `DuplicateTransactionError`
- Achievement/Poll/Mission: اجرای دوبارهٔ همون جایزه → بارِ دوم هیچ سکه‌ای اضافه نشد
- JSON خراب → بازیابیِ خودکار از `.bak` (نه کرش، نه از دست رفتنِ داده)
- شکستِ `os.replace` (شبیه‌سازیِ دیسکِ پر) → فایلِ معتبرِ قبلی دست‌نخورده موند

### محدودیتِ دانسته‌شده و صادقانه‌ی این تست‌ها

محیطِ این آدیت **دسترسیِ اینترنت نداشت** (تأییدشده: DNS برای `pypi.org` هم از
مسیرِ مستقیم و هم از مسیرِ Proxy شکست خورد) و نتونست `python-telegram-bot`
(طبقِ `requirements.txt`) رو نصب کنه. بنابراین:

- تست‌ها `bot.py` واقعی رو مستقیم Import/اجرا **نمی‌کنن** (۸۸۰۰ خط، به کتابخانه‌ی
  واقعی + Token واقعی + راه‌اندازیِ کاملِ Application نیاز داره).
- به‌جاش، یه استابِ سبکِ `bot` (`tests/_stub_bot.py`، فقط `_wallet`/`_xp`/
  `_achievements` به شکلِ dict خالی) و یه استابِ مینیمالِ پکیجِ `telegram`
  (`tests/_telegram_stub/`) ساخته شد تا ماژول‌های **واقعیِ** پروژه
  (`economy_core`, `profile_engine`, `poll_engine`, `economy_missions`) بدونِ
  تغییر، عیناً import و تست بشن.
- دو فیکسی که مستقیماً توی خودِ `bot.py` هستن (`daily_command` و دکمه‌ی
  `econquick`) از این طریق **مستقیماً** اجرا نشدن؛ ولی هر دو دقیقاً از همون
  مکانیزمِ `economy_core.add_coins(idempotency_key=...)` استفاده می‌کنن که
  مستقیماً و با جزییاتِ کامل تست شده (`test_concurrent_duplicate_idempotency_key_only_one_wins`).
  این یعنی *مکانیزمِ* فیکس تأیید شده، ولی *مسیرِ خاصِ* `bot.py` (پارس کردنِ
  callback_data، چک‌های `get_setting`، ...) فقط با خواندنِ دستیِ کد تأیید شده،
  نه با اجرای خودکار.
- تابعِ کاملِ `economy_admin.reset_user_economy_command` (چون `Update`/
  `context` واقعیِ تلگرام می‌خواد) مستقیم تست نشده؛ ولی منطقِ مالیِ داخلش
  (`admin_set_balance`/`remove_bank_coins`) همون توابعیه که توی
  `EconomyCoreBasicsTest` مستقیم و کامل تست شدن.
- Regression سطحِ کل پروژه با `python3 -m py_compile` روی **هر ۳۷ فایل** (بدون
  استثنا) انجام شد - هیچ Syntax Error. این تضمین می‌کنه هیچ تغییری چیزی رو از
  نظرِ Syntax نشکسته؛ ولی جایگزینِ یه تستِ End-to-End با یه ربات واقعیِ متصل
  به تلگرام نیست (که از اسکوپِ این محیط بیرونه).

**توصیه:** قبل از Deploy روی Production، حداقل یه بار دستی `/daily`،
`/reseteconomy`، و دکمه‌ی Bulk-Bonus رو روی یه گروهِ تستی امتحان کن تا همون
دو مسیرِ تست‌نشده هم با چشم دیده بشن.

---

## ۵. ریسک‌های شناخته‌شده‌ی باقی‌مانده

1. **(متوسط) عدمِ اتمیسیتیِ Cross-File بینِ `bot.py` و `economy_core`** — توضیحِ
   کامل در `README.md` بخشِ «⚠️ ریسک شناخته‌شده». نیاز به یکی‌کردنِ کلِ
   Persistence پروژه داره؛ خارج از اسکوپِ «تغییراتِ تدریجی».
2. **(پایین) Username Enumeration جزئی در `owner_panel.py`** — پیامِ «رمزِ
   اشتباه، N تلاش مونده» هنوز نشون می‌ده یوزرنیم معتبره (بخشِ ۲، ردیفِ ۷).
   Trade-off آگاهانه برای حفظِ UX.
3. **(پایین) حداقلِ طولِ رمزِ دستیِ ادمینِ پویا فقط ۴ کاراکتره** (`owner_panel._MIN_PASSWORD_LEN`).
   با ۵ تلاشِ مجاز ریسکش محدوده، ولی اگه می‌خوای سخت‌گیرتر بشه، این مقدار
   (و `_MIN_USERNAME_LEN`) رو بالا ببر - یه تغییرِ یک‌خطی و امنه که عمداً
   بدونِ درخواستِ صریح تغییر داده نشد (یه تصمیمِ Policy است، نه یه باگ).
4. **(پایین، مستندشده نه فیکس‌شده) پاداشِ «activity»** هیچ Idempotency Key
   نداره - عمداً، چون یه پاداشِ پیوسته‌ست نه یه Claim گسسته (بخشِ ۲).
5. **محدودیتِ پوششِ تست** طبقِ بخشِ ۴ (نبودِ اینترنت برای نصبِ کتابخانه‌ی
   تلگرام) - دو مسیرِ `bot.py` فقط با خواندنِ کد تأیید شدن، نه اجرای خودکار.

---

## ۶. تأییدِ سازگاریِ عقب‌رو (Backward Compatibility)

- ✅ هیچ کلیدِ JSON موجود حذف/Rename نشد؛ `migrate_state`ِ `economy_core`
  دست‌نخورده‌ست (نسخه هنوز ۲).
- ✅ `/transactions` و تاریخچه‌ی قدیمی (`economy_engine.log_transaction`)
  دقیقاً مثلِ قبل کار می‌کنه - در واقع فیکسِ #1/#2 باعث شد Achievement/Poll
  Reward هم **برای اولین بار** توش دیده بشن (قبلاً اصلاً ثبت نمی‌شدن).
  Phase 5 تأیید شد: هیچ رکوردِ قدیمی گم یا تکراری نشد.
- ✅ همه‌ی Command/Callback های موجود دست‌نخورده‌ن؛ تنها تغییرِ امضا
  (`record_vote(poll, uid, idx, poll_id=None)`) کاملاً اختیاریه.
- ✅ فایل‌های state فعلیِ کاربر (هر فرمتی که الان دارن) بدونِ نیاز به
  Migration دستی لود می‌شن - فرمتِ ذخیره‌سازی عوض نشده، فقط *مسیرِ نوشتنِ*
  موجودی (از مستقیم به از-طریقِ-Core) عوض شده که تأثیری روی فرمتِ فایل نداره.

---

## ۷. جمع‌بندی

هدفِ این آدیت ارتقای DIGNANTI درونِ همون معماریِ فعلیش بود، نه بازسازیش -
و دقیقاً همین کار انجام شد. سه نقضِ واقعیِ معماریِ مالی، یه باگِ واقعیِ
Double-Reward (دکمه‌ی Bulk-Bonus)، چند شکافِ Idempotency، و یه نایکدستیِ
کوچیک در Durability (fsync) پیدا و فیکس شدن - همه با کمترین Footprintِ ممکن
و بدونِ لمسِ چیزی که سالم بود. بزرگ‌ترین مشکلِ باقی‌مونده (اتمیسیتیِ
Cross-File) یه مشکلِ واقعیه ولی حلش نیاز به یه تصمیمِ معماریِ بزرگ‌تر داره که
از اسکوپِ «حفظِ معماریِ فعلی» بیرونه - به‌جایِ یه فیکسِ ریسکی و نصفه‌نیمه،
اینجا صادقانه مستند شد.

---
---

# ضمیمه‌ی آدیتِ دوم (Final Critical Audit) — ۲۰۲۶/۰۸/۱۶ (۱۴۰۵/۰۸/۱۶)

این ضمیمه سه مسئله‌ی باقی‌مونده‌ای که توی آدیتِ اول «مستندشده نه فیکس‌شده»
علامت خورده بودن (به‌خصوص Cross-File Atomicity، بخشِ ۷ بالا) و دو نقطه‌ضعفِ
اضافیِ Concurrency/Idempotency رو پوشش می‌ده. همون قواعدِ آدیتِ اول رعایت
شده: **بازنویسی نشد، معماری عوض نشد، ماژولِ نامرتبط دست‌نخورد.** قبل از هر
تغییری، یه بکاپِ کاملِ پروژه گرفته شد
(`/home/claude/backups/mrx_pre_round2_20261106_012016`).

## مسئله‌ی ۱ — محافظت در برابرِ رول‌بکِ هم‌زمان

**علتِ ریشه‌ای:** `rollback_transaction()` زنجیره‌ی «خوندنِ `status`» →
«اعمالِ اثرِ مالیِ معکوس (`await remove_coins/add_coins`)» → «نوشتنِ
`status = ROLLED_BACK`» رو بدونِ هیچ قفلی انجام می‌داد. اگه همین تابع برای
یک `transaction_id` دوبار هم‌زمان صدا زده می‌شد، هر دو فراخوانی می‌تونستن
`status` رو `COMPLETED` ببینن (قبل از اینکه هرکدوم فرصت کنه اون رو عوض کنه)
و هر دو اثرِ مالی رو خنثی کنن - یعنی یه رول‌بکِ دوبرابر (پولِ اضافه برگشتِ
واقعی). توجه: زیرِ semantics فعلیِ asyncio (تک‌رشته‌ای، بدونِ I/O واقعی وسطِ
این تابع) این باگ به‌ندرت بروز می‌کرد، ولی وابسته‌بودن به این جزئیاتِ
پیاده‌سازیِ implicit به‌جایِ یه قفلِ صریح، شکننده و قابلِ‌اعتماد نبود -
دقیقاً همون چیزی که آدیت درست تشخیص داده بود.

**فایلِ تغییریافته:** `economy_core.py`

**پیاده‌سازی:** یه `WeakValueDictionary` جدید و مجزا به نامِ `_rollback_locks`
(دقیقاً همون الگوی بدونِ‌نشتیِ `_user_locks`) اضافه شد، کلیددهی‌شده بر اساسِ
خودِ `transaction_id` (نه `(chat_id, uid)` - چون قفلِ کاربر برای جلوگیری از
رول‌بکِ دوبرابرِ *همون* تراکنش کافی نیست). کلِ بدنه‌ی `rollback_transaction`
الان زیرِ `async with lock:` اجراش می‌شه. اگه اعمالِ اثرِ معکوس شکست بخوره
(مثلاً `InsufficientFundsError`)، `status` همونجوری `COMPLETED` می‌مونه - یعنی
رول‌بک «قابلِ تلاشِ دوباره»ست، نه یه حالتِ نصفه‌ونیمه.

**تستِ جدید:** `RollbackConcurrencyTest.test_twenty_concurrent_rollbacks_only_one_succeeds`
- دقیقاً ۲۰ تلاشِ هم‌زمان (`asyncio.gather`) روی یک `transaction_id`.
- نتیجه‌ی واقعی: **دقیقاً ۱ موفق، ۱۹ تا `RollbackError`**، موجودیِ نهایی
  فقط یک‌بار اصلاح شده (نه ۲۰ بار). + یه تستِ کمکی برای رول‌بکِ متوالیِ
  (غیرِهم‌زمان) روی چیزی که از قبل رول‌بک شده.

## مسئله‌ی ۲ — پایداریِ Idempotency فراتر از Pruning شدنِ Ledger

**علتِ ریشه‌ای (تأیید شد - یه باگِ واقعی بود، نه False Positive):**
`_idempotency_index` قبلاً فقط `idempotency_key -> transaction_id` (رشته‌ی
خام) نگه می‌داشت، و چکِ Duplicate مستقیماً وابسته بود به این‌که خودِ رکورد
هنوز توی `_ledger` باشه (`existing in _ledger and _ledger[existing]["status"]
== COMPLETED`). ولی `_ledger` با `MAX_LEDGER_SIZE=20000` هرس می‌شه (FIFO,
`_ledger_order.pop(0)`). یعنی بعدِ اینکه ۲۰٬۰۰۰ تراکنشِ دیگه (از هر کاربر/هر
نوعی) اتفاق بیفته، رکوردِ اصلی حذف می‌شد، `existing in _ledger` نادرست
`False` برمی‌گردوند، و همون `idempotency_key` دوباره قابل‌استفاده می‌شد -
یعنی یه Achievement/Mission/Poll Reward می‌تونست (بعدِ فشارِ کافی روی
ربات، یا بعدِ Restart بعدِ اون حد) دوباره پرداخت بشه.

**فایلِ تغییریافته:** `economy_core.py`

**پیاده‌سازی:** فرمتِ `_idempotency_index` به `idempotency_key -> {"transaction_id":
..., "status": ...}` تغییر کرد - حالا خودش `status` رو مستقل نگه می‌داره و
هیچ وابستگی‌ای به وجودِ رکورد توی `_ledger` نداره. این دیکشنری (برخلافِ
`_ledger`) عمداً **هرس نمی‌شه** - تریدآف صریح: رشدش کندِه (یه ورودی به‌ازای
هر Claim، نه هر تراکنشِ خام) ولی بدونِ سقف؛ اگه در آینده نیاز به محدودسازی
شد، باید یه مکانیزمِ Archive/Export جدا (خارج از اسکوپِ این آدیت) طراحی
بشه. `ECONOMY_STATE_VERSION` از ۲ به ۳ رفت بالا و یه بلوکِ Migration
(`if version < 3`) اضافه شد که فایل‌های قدیمی رو مهاجرت می‌کنه: برای هر
ورودیِ قدیمیِ رشته‌ای، سعی می‌کنه `status` واقعی رو از خودِ `ledger` (اگه
هنوز Prune نشده) بخونه؛ اگه پیدا نشد، محافظه‌کارانه `COMPLETED` فرض می‌کنه
(ترجیحِ امنیتی: جلوگیری از یه پرداختِ دوباره‌ی احتمالی، به قیمتِ اینکه شاید
یه کلیدِ خیلی‌قدیمیِ rollback-شده اشتباهاً یه بار دیگه مسدود بمونه - حالتی
فوق‌العاده نادر و کم‌خطر).

**تست‌های جدید:** `IdempotencyAfterPruningTest`
- `test_idempotency_survives_ledger_pruning_and_restart`: دقیقاً سناریوی
  خواسته‌شده - تراکنش با `idempotency_key` ساخته شد، رکوردش از Ledger حذف شد
  (شبیه‌سازیِ اثرِ Pruning)، `save_state()` + ری‌استارتِ کامل (پاک‌کردنِ
  حافظه + `load_state()` از فایل) انجام شد، و تلاشِ دوباره **`DuplicateTransactionError`
  گرفت** - پرداختِ دوباره اتفاق نیفتاد.
- `test_migration_from_old_bare_string_idempotency_format` و
  `test_migration_defaults_to_completed_when_ledger_record_already_gone`:
  مسیرِ Migration رو هم برای حالتی که رکوردِ قدیمی هنوز موجوده، هم برای
  حالتی که از قبل Prune شده، جداگانه تأیید می‌کنن.

## مسئله‌ی ۳ — هماهنگیِ Wallet و Ledger حینِ کرش

**علتِ ریشه‌ای (بعدِ بررسیِ کاملِ `save_state`/`load_state` در `bot.py` و
`economy_core.py`):**
- *ذخیره‌سازی:* `bot.py.save_state()` اول فایلِ خودش (شاملِ `_wallet`) رو
  Sync می‌نویسه، بعد با یه `asyncio.gather(..., return_exceptions=True)`
  همه‌ی ۲۹ موتور (از جمله `economy_core`) رو هم‌زمان صدا می‌زنه. یعنی
  فایلِ Wallet و فایلِ Ledger دو `os.replace()` کاملاً مستقل‌ان - هیچ
  ضمانتی نیست که اگه برنامه وسطِ این فرایند کرش کنه، هر دو فایل باهم آپدیت
  شده باشن یا هیچ‌کدوم.
- *بارگذاری:* `bot.py.load_state()` اول `economy_core.load_state()` رو صدا
  می‌زنه (خطِ ۴۸۸)، و فقط *بعدش* فایلِ خودشو می‌خونه و `_wallet` رو پر
  می‌کنه (خطِ ۶۵۶). یعنی لحظه‌ای که `economy_core.load_state()` اجرا می‌شه،
  `_wallet` هنوز خالی/قدیمیه - هر فیکسی باید *بعدِ* این دو مرحله اجرا بشه.
- نتیجه: دو جهتِ ناهماهنگیِ ممکن بعدِ کرش: (الف) فایلِ Ledger جدیدتر از
  Wallet (کاربر پاداش رو «گم» می‌کنه - آزاردهنده ولی نه خطرِ امنیتی)، و
  (ب) فایلِ Wallet جدیدتر از Ledger (خطرناک‌تر - Idempotency Index خبر
  نداره، پس یه `idempotency_key` می‌تونه اشتباهاً دوباره قبول و دوباره
  پرداخت بشه).

**فایل‌های تغییریافته:** `economy_core.py`، `bot.py` (فقط ۸ خط - یه فراخوانی
و try/except دورش، در نقطه‌ی دقیقِ بعدِ بازیابیِ `_wallet`).

**پیاده‌سازی (Write-Ahead Journal سبک، نه بازسازیِ معماری):**
یه فایلِ جدید و مجزا (`economy_core_state.json.wallet_journal.jsonl`، فرمتِ
append-only JSONL) اضافه شد. هر تابعی که `host._wallet` رو تغییر می‌ده
(`add_coins`، `add_coins_sync`، `remove_coins`، `transfer_coins`،
`admin_set_balance`، `admin_adjust_balance`، `deposit`، `withdraw`) حالا
بلافاصله بعدِ همون تغییرِ درون‌حافظه‌ای (بدونِ هیچ `await` ای وسط - دقیقاً
توی همون بخشِ Atomic که قبلاً هم `_lock_for` تضمینش می‌کرد)، یه خطِ
`{txn_id, chat_id, uid, wallet_after, record, ts}` رو با `fsync` مستقیم
روی دیسک می‌نویسه - مستقل از هر دو فایلِ بزرگِ Wallet/Ledger که فقط دوره‌ای
نوشته می‌شن.

تابعِ جدیدِ `economy_core.reconcile_wallet_journal()` دقیقاً بعدِ بازیابیِ
`_wallet` توی `bot.py.load_state()` صدا زده می‌شه (نقطه‌ی هوکِ تنها، طبقِ
بررسیِ بالا) و:
1. برای هر ورودیِ Journal که `transaction_id`ـش توی `_ledger`ِ بارگذاری‌شده
   نیست (یعنی فایلِ Ledger قدیمی‌تر بوده) - رکوردِ Ledger/Idempotency رو
   **مستقیماً از خودِ Journal بازسازی می‌کنه**. این «اجرای دوباره‌ی عملیاتِ
   مالی» نیست - هیچ‌وقت `add_coins`/`remove_coins` دوباره صدا زده نمی‌شن؛
   فقط رکوردِ از-قبل-اتفاق‌افتاده بازسازی می‌شه (جهتِ «ب» بالا رو می‌بنده).
2. برای هر `(chat_id, uid)`، آخرین `wallet_after` ثبت‌شده توی Journal رو با
   مقدارِ فعلیِ `_wallet` مقایسه می‌کنه؛ اگه فرق داشت، مقدارِ `_wallet` رو
   **مستقیماً به همون مقدار ست می‌کنه** (نه این‌که Delta رو دوباره جمع بزنه -
   این دقیقاً همون چیزیه که دستورِ کار صراحتاً منع کرده بود: «هیچ‌وقت یه
   عملیاتِ مالیِ از-قبل-اعمال‌شده رو دوباره Replay نکن»).

Journal بعدِ هر `economy_core.save_state()` موفق به آخرین ۵۰۰ خط کوتاه
می‌شه (`_journal_truncate`) تا بی‌نهایت رشد نکنه - چون در اون لحظه، فایلِ
Wallet (که Sync و قبل‌تر نوشته شده) و فایلِ Ledور (که همین الان نوشته شده)
هر دو باید از قبل این مقدار رو منعکس کرده باشن.

**تست‌های جدید:** `WalletJournalRecoveryTest`
- `test_crash_after_journal_write_before_ledger_file_save__no_double_pay`:
  جهتِ «ب» (خطرناک) - یه تراکنشِ دوم با `idempotency_key` بعدِ آخرین
  `save_state()` موفق انجام شد (شبیه‌سازیِ کرش قبل از Flush شدنِ فایلِ
  Ledger)، `economy_core.load_state()` دوباره (بدونِ دست‌زدن به Wallet،
  دقیقاً مثلِ معماریِ واقعی) اجرا شد. نتیجه‌ی واقعی: `reconcile_wallet_journal()`
  **۱ رکورد بازسازی کرد**، موجودی تغییر نکرد (۱۵۰ ماند)، و تلاشِ دوباره برای
  همون `idempotency_key` **`DuplicateTransactionError` گرفت** - یعنی
  دوباره‌پرداخت رخ نداد.
- `test_crash_during_state_saving__stale_wallet_file_gets_corrected_not_replayed`:
  جهتِ «الف» - فایلِ Wallet دستی روی مقدارِ قدیمی (۱۰۰) ست شد تا کرشِ وسطِ
  نوشتنِ همون فایل رو شبیه‌سازی کنه. نتیجه‌ی واقعی: `reconcile_wallet_journal()`
  موجودی رو دقیقاً به ۱۵۰ (مقدارِ واقعیِ طبقِ Journal) اصلاح کرد - نه ۲۰۰
  (که می‌شد اگه Delta دوباره جمع می‌شد).
- `test_journal_truncated_to_bounded_size_after_successful_save`: تأییدِ
  اینکه Journal واقعاً بعدِ یه `save_state()` موفق کوتاه می‌شه.
- `test_transfer_coins_journals_both_sides`: تأییدِ اینکه `transfer_coins`
  (که دو رکوردِ Ledger جدا می‌سازه) هر دو طرف رو توی Journal ثبت می‌کنه.

**⚠️ محدودیت‌های صادقانه (طبقِ دستورِ صریح - بدونِ ادعای Crash Consistency
کامل):**
- این مکانیزم فقط پنجره‌ی بینِ آخرین Flush موفقِ Journal و لحظه‌ی کرش رو
  می‌پوشونه. اگه خودِ نوشتنِ یه خطِ Journal (fsync) شکست بخوره (مثلاً دیسک
  پر باشه - دقیقاً هم‌زمان که Wallet یا Ledger هم ممکنه شکست بخورن)، دقیقاً
  همون یک تراکنش از این محافظت بیرونه؛ این موردِ خاص با Warning لاگ می‌شه،
  نه با ادعای ترمیمِ قطعی.
- اگه خودِ نوشتنِ فایلِ Wallet (در `bot.py.save_state`) شکست بخوره ولی
  `economy_core.save_state()` همون چرخه موفق بشه و Journal رو کوتاه کنه،
  در تئوری یه پنجره‌ی خیلی باریک وجود داره که داده‌ی لازم برای ترمیمِ *بعدیِ
  بعدی* از دست بره؛ این حالت (شکستِ هم‌زمانِ دو مکانیزمِ مستقل، درست سرِ یه
  لحظه‌ی خاص) تست نشده و عمداً به‌عنوانِ یه ریسکِ باقی‌مانده (نه یه ادعای حل‌شده)
  مستند می‌شه.
- این مکانیزم فقط **Wallet** رو پوشش می‌ده (چون فقط Wallet بینِ دو فایلِ
  جدا تقسیم شده). **Bank** از اول با Ledger توی یه فایل (`economy_core`ِ
  خودش) ذخیره می‌شه - یعنی Bank/Ledger از اول Atomic بودن نسبت به هم (یه
  `os.replace()` مشترک) و نیازی به Journal نداشتن؛ این توی تست‌ها فرضِ
  جداگانه‌ای نگرفته شد چون از قبل طبقِ ساختارِ کد تضمین‌شده‌ست.
- این Recovery فقط موقعِ `load_state()` (یعنی Restartِ برنامه) اجرا می‌شه،
  نه به‌صورتِ زنده/دوره‌ای حینِ اجرا - اگه ربات کرش نکنه و فقط یه بارِ خیلی
  وقفه‌ای بینِ دو Save داشته باشه، تا Restartِ بعدی ناهماهنگی (در صورتِ
  وجود) دیده نمی‌شه (هرچند، چون Journal همیشه در لحظه‌ی مطمئن نوشته می‌شه،
  این ناهماهنگی فقط حینِ یه کرشِ واقعی ممکنه اصلاً به‌وجود بیاد).

## نتیجه‌ی واقعیِ اجرای کاملِ تست‌ها (بعدِ هر سه فیکس)

```
$ BOT_TOKEN=dummy_test_token python3 -m unittest tests.test_economy_audit -v
...
----------------------------------------------------------------------
Ran 36 tests in 0.161s

OK
```

۳۶ تست (۲۷ تستِ آدیتِ اول + ۹ تستِ جدیدِ این آدیت) - همه Pass. علاوه بر این،
`python3 -m py_compile` روی هر ۳۷+۱ فایلِ پروژه (شاملِ `bot.py` و
`tests/test_economy_audit.py`) بدونِ خطا اجرا شد.

## تأییدِ سازگاریِ عقب‌رو (آدیتِ دوم)

- ✅ هیچ کلید/فایل/Command/Callbackِ موجود حذف یا Rename نشد.
- ✅ `ECONOMY_STATE_VERSION` از ۲ به ۳ رفت بالا، با یه بلوکِ Migrationِ
  Idempotent که فایل‌های قدیمی رو بدونِ نیاز به دخالتِ دستی بالا میاره.
- ✅ فایلِ Journal کاملاً جدید و اضافیه - نبودنش (مثلاً اولین اجرا بعدِ
  این آپدیت، قبل از هر تراکنشِ جدید) باعثِ کرش یا رفتارِ غیرمنتظره نمی‌شه؛
  `reconcile_wallet_journal` اگه فایل نباشه فقط `{0, 0}` برمی‌گردونه.
- ✅ هیچ ماژولِ نامرتبط (غیر از `economy_core.py` و همون ۸ خطِ هوک توی
  `bot.py`) دست‌نخورد.
