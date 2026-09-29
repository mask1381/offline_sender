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
}
