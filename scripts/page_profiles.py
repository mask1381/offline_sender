# -*- coding: utf-8 -*-
"""
پروفایل پیج‌ها — قواعد اختصاصی هر پیج اینستاگرام برای حالت --caption-mode channel.

طرز کار:
  توی pages.json به هر پیج می‌شه یه کلید "profile" داد:
      {"username": "somepage", "profile": "leak", "last_post_id": "", "last_post_taken_at": 0}
  پیجی که "profile" نداره (یا "default" داره) دقیقاً با قواعد سراسری download_and_send3.py
  کار می‌کنه — یعنی ۴ پیج فعلی بدون هیچ تغییری مثل قبل می‌مونن.
  اسم پروفایلی که اینجا تعریف نشده باشه = خطا؛ اون پیج اجرا نمی‌شه و state دست‌نخورده می‌مونه.

کلیدهای مجاز هر پروفایل (همه اختیاری؛ کلیدِ نبود = مقدار سراسری v3):
  text_rules            : لیست قواعد متنی که کل TEXT_RULES سراسری رو *جایگزین* می‌کنه
  extra_rules           : لیست قواعدی که *قبل* از TEXT_RULES سراسری بررسی می‌شن (اولویت بالاتر)
  unmatched_non_video   : نوع پست عکسی/آلبومی که هیچ قاعده‌ای نخورد (کلید TAXONOMY، یا None = ارسال نشه)
  video_fallback        : نوع ویدیویی که هیچ قاعده‌ی متنی نخورد؛ اگه ندی، قاعده‌ی مدت (کلیپ/دمو/تریلر)
  clip_max_seconds      : مرز کلیپ/دمو-تریلر بر حسب ثانیه
  extra_generic_tags    : هشتگ‌های اضافه که «اشاره به شخصیت» حساب نشن (کوچک‌حرف، بدون فاصله/زیرخط)
  drop_first / drop_last: تعداد اسلاید ثابتِ اول/آخر کروسل که قبل از دانلود حذف بشه (تبلیغ)
  drop_min_items        : حداقل تعداد آیتم برای اعمال حذف (پیش‌فرض first+last+1؛ همیشه یه آیتم می‌مونه)
  crop_bottom_pct       : درصدی از پایین *عکس‌ها* که کراپ بشه (ویدیو دست‌نخورده)
  schedule_delay_min    : ارسال با schedule تلگرام، N دقیقه بعد (None/۰ = فوری) — برای review توی scheduled
  strip_blocks          : لیست regex؛ پاراگراف‌های (جداشده با خط خالی) کپشن که بخورن حذف می‌شن (re.I|re.M)
  split_title           : False = خط اول کپشن عنوان bold جدا نشه (همه‌چیز توی blockquote)؛ پیش‌فرض True
  redundant_tags        : {'genshin': 'genshinimpact'} → #genshin حذف می‌شه اگه #genshinimpact هم باشه
  protect_blocks        : لیست regex؛ پاراگراف‌های منطبق (مثل «Via X») هنگام کوتاه‌سازی به سقف ۱۰۲۴ حذف نمی‌شن
                          (بقیه‌ی متن کوتاه می‌شه و «…» می‌گیره؛ پاراگراف محافظت‌شده آخر کپشن می‌مونه)
  character_tags        : اسم فرنچایز (مثل 'Genshin Impact') → اسم شخصیت‌های داخل متن با دیکشنری v4 هشتگ می‌شن
                          (به ترتیب ظهور در متن؛ پاراگراف‌های protect_blocks در تشخیص حساب نمی‌شن؛ حداکثر ۶ تا)
  (کلیدهای پروفایل‌های ANINEXUS — keep_lines, own_hashtags, raw_caption, drop_text, blocked_hashtags,
   extra_hashtags, fixed_caption, continuation_caption — توضیحشون بالای همون پروفایل‌هاست)

قالب هر قاعده (همون TEXT_RULES در download_and_send3.py):
  {'kind': 'LEAK', 'video': True|False, 'patterns': [('title'|'text', r'regex'), ...]}
  ترتیب مهمه؛ اولین قاعده‌ای که بخوره برنده‌ست.

نکته: kind باید کلید TAXONOMY باشه. نوع جدید (مثلاً RUMOR) رو اول توی TAXONOMY هر ۳ جا
(download_and_send3.py، passer «Build Final Caption»، و در صورت لزوم news) اضافه کن.
"""

PROFILES = {
    # رفتار سراسری فعلی — پیج بدون "profile" هم همین رو می‌گیره
    'default': {},

    # ⚠️ اسکلت پیج‌های لیک — قواعد واقعی رو بعد از تصمیم‌گیری جایگزین کن.
    # فعلاً: هر پستی که قاعده‌ی متنی نخوره (چه عکس چه ویدیو) LEAK می‌شه.
    'leak': {
        'extra_rules': [],
        'unmatched_non_video': 'LEAK',
        'video_fallback': 'LEAK',
    },

    # steambird.id — همه‌چیز LEAK؛ اسلاید اول (کاور تبلیغاتی) خودکار حذف می‌شه (۳ تای آخر عمداً نه،
    # چون گاهی محتوای اصلی اونجاست → خودت از scheduled پاک می‌کنی)؛ ۱۰٪ پایین عکس‌ها (فوتر برند پیج)
    # کراپ می‌شه؛ همه‌ی پست‌ها (عکس/ویدیو/آلبوم) ۶۰ دقیقه بعد منتشر می‌شن تا review کنی.
    'steambird': {
        'text_rules': [],
        'unmatched_non_video': 'LEAK',
        'video_fallback': 'LEAK',
        'drop_first': 1,
        'drop_last': 0,
        'drop_min_items': 3,
        'crop_bottom_pct': 10,
        'schedule_delay_min': 60,
        'split_title': False,
        'redundant_tags': {'genshin': 'genshinimpact'},
        'strip_blocks': [
            r'^[^\w\n]*cover\s*art\b',           # ✦ Cover Art + لینک pixiv
            r'leaks\s+and\s+news\s+about',        # ↠ Leaks and News about Genshin Impact
            r'like\s*&\s*follow',                 # ↠ Like & Follow for more ...
            r'^[^\w\n]*hashtags?[^\w\n]*$',       # ✦ Hashtags (تیتر؛ خود هشتگ‌ها جدا استخراج می‌شن)
        ],
    },

    # gensh1nuniverse — پیج لیک؛ کپشن: TITLE / بولت‌ها / «Via X» / #GenshinImpact (بدون تبلیغ)
    # همه‌چیز LEAK؛ بدون کراپ و بدون حذف اسلاید؛ عنوان bold جدا نمی‌شه (همه داخل blockquote)؛
    # خط اعتبار «Via ...» هیچ‌وقت با کوتاه‌سازی حذف نمی‌شه؛ اسم شخصیت‌ها هشتگ می‌شن؛
    # ۹۰ دقیقه بعد منتشر می‌شه (steambird ۶۰ دقیقه) تا دو پیج با هم قاطی نشن.
    'gensh1nuniverse': {
        'text_rules': [],
        'unmatched_non_video': 'LEAK',
        'video_fallback': 'LEAK',
        'schedule_delay_min': 90,
        'split_title': False,
        'protect_blocks': [r'^\s*via\b'],
        'character_tags': 'Genshin Impact',
    },

    # ───────── پیج‌های ANINEXUS (aninexus_pages.json) ─────────
    # فعلاً خالی = رفتار سراسری؛ قواعد هر پیج بعد از تعیین اینجا پر می‌شه.
    # hanbil_project — فقط سه خط Song / Artist / Anime نگه داشته می‌شه (تبلیغ «Want faster requests»،
    # «Sinopsis:» و پاراگراف‌های سینوپسیس حذف می‌شن)؛ هشتگ‌های خود پیج عیناً استفاده می‌شن و
    # سیستم هشتگ‌سازی اجرا نمی‌شه.
    'hanbil_project': {
        'keep_lines': [
            r'^\s*song\s*:',
            r'^\s*artist\s*:',
            r'^\s*anime\s*:',
        ],
        'own_hashtags': True,
        'split_title': False,
    },
    # sylver_ae — کپشن و هشتگ‌ها عیناً و بدون هیچ دخالتی می‌رن؛ فقط قالب کانال (فوتر/blockquote) اعمال
    # می‌شه. سیستم هشتگ‌سازی و هرگونه پاک‌سازی/حذف متن اجرا نمی‌شه.
    'sylver_ae': {
        'raw_caption': True,
        'split_title': False,
    },
    # animecorner_ac — خط اول کپشن = عنوان bold؛ پاراگراف تبلیغ «Follow @animecornernews for more!» حذف؛
    # بقیه‌ی متن داخل blockquote؛ هشتگ‌های پایانی خود پیج عیناً (بدون سیستم هشتگ‌سازی).
    'animecorner_ac': {
        'split_title': True,
        'own_hashtags': True,
        'strip_blocks': [
            r'\bfollow\s+@[\w.]+',
        ],
    },
    # ringwitdahoodie — متن کپشن کامل حذف می‌شه؛ فقط هشتگ‌های خود پیج (از هر جای کپشن) عیناً می‌رن،
    # بدون سیستم هشتگ‌سازی؛ هشتگ‌های الکی فیلتر می‌شن؛ #trend همیشه اضافه می‌شه.
    # blocked_hashtags: کوچک‌حرف، بدون # — بعداً دستی اضافه کن.
    'ringwitdahoodie': {
        'drop_text': True,
        'own_hashtags': True,
        'blocked_hashtags': ['fyp', 'explore'],
        'extra_hashtags': ['trend'],
    },
    # animeronix — پیج معرفی انیمه، کپشن خودش نداره: کپشن ثابت ما گذاشته می‌شه (بدون سیستم هشتگ‌سازی)؛
    # اسلاید اول (کاور/تبلیغ) حذف می‌شه؛ اگه بعد از حذف بیشتر از ۱۰ عکس بمونه، آلبوم‌های بعدی
    # (۱۱ به بعد) به‌جای کپشن خالی، متن «ادامه‌ی آلبوم قبلی» می‌گیرن.
    # ⚠️ fixed_caption جایگزین موقته — متن نهایی رو خودت بگو.
    'animeronix': {
        'drop_first': 1,
        'drop_last': 0,
        'split_title': False,
        'fixed_caption': '#Anime #AnimeSpotlight',
        'continuation_caption': '↪️ Continued from the previous album',
    },
}
