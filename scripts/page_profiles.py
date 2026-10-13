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
   extra_hashtags, fixed_caption — توضیحشون بالای همون پروفایل‌هاست)

  (کلیدهای پروفایل‌های ANIWALL — فقط توی aniwall_insta_check.py، برای ساخت کپشنِ «فقط هشتگ»:)
  hashtag_mode          : 'own' = فقط هشتگ‌های خود پیج، منهای لیست‌های عمومی/ترند سراسری و قواعد همین پروفایل
                          'raw' = هشتگ‌های خود پیج کاملاً دست‌نخورده
                          'dictionary' = تشخیص با دیکشنری از روی متن (برای پیج‌های بدون هشتگ)
                          (نبود) = همون تشخیص سراسری download_and_send4.py
  blocked_hashtags      : (همون کلید ANINEXUS) نام‌های حذفی، حروف کوچک/بدون # ؛ زیرخط و نقطه مهم نیست
  blocked_patterns      : لیست regex روی کلید فشرده‌ی هشتگ (حروف کوچک، بدون علامت) — هر هشتگی که بخوره حذف می‌شه
  allowed_hashtags      : هشتگ‌هایی که از لیست‌های سراسری و blocked_patterns معاف‌ان (blocked_hashtags اولویت داره)
  max_hashtags          : سقف تعداد هشتگ؛ به ترتیب ظاهر شدن توی کپشن (نبود = بدون سقف)
  default_hashtag       : اگه بعد از پاک‌سازی چیزی نموند این هشتگ می‌ره (نبود = پست رد می‌شه)
  در حالت 'own' حذف تکرارِ اسم (ترتیب اسم/فامیل، اکسان، اسم ناقص کنار اسم کامل) همیشه اعمال می‌شه.

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
    # wuwauniverse — پیج لیک ودرینگ ویوز؛ هشتگ خود پیج فقط #WutheringWaves
    # ترتیب مهمه (اولین قاعده‌ی منطبق برنده‌ست)؛ قواعد سراسری v3 به‌عنوان fallback می‌مونن.
    'wuwauniverse': {
        'extra_rules': [
            # فیگور/مسکات → ارسال نشه (state جلو می‌ره)
            {'kind': None, 'video': True,
             'patterns': [('title', r'\bfigures?\b|\bmascots?\b')]},
            # Adهای 2D live (ویدیو) و ایلاستریشن → ART
            {'kind': 'ART', 'video': True,
             'patterns': [('title', r'\bad\s*$'), ('title', r'\billustrations?\b')]},
            # رسمی (دستی بهشون رسیدگی می‌شه)
            {'kind': 'OFFICIAL', 'video': True,
             'patterns': [('title', r'maintenance|servers?\s+(?:are|is)\s+up|welcome to version|launcher')]},
            {'kind': 'GUIDE', 'video': True,
             'patterns': [('title', r'\blocations?\b|\bguide\b|\broutes?\b')]},
            # LEAK = منبع (Via/Cre) AND کلمه‌ی آینده‌نگر/حدسی (بدون عدد نسخه)
            {'kind': 'LEAK', 'video': True,
             'patterns': [('text', r'(?s)(?=.*\b(?:via|cre)\b)'
                                   r'(?=.*\b(?:may|might|will|rumou?rs?|leaks?|leaked|upcoming|'
                                   r'brand-new|revolve|unconfirmed|reportedly)\b)')]},
        ],
        'unmatched_non_video': 'LEAK',    # عکس/آلبوم بدون قاعده
        'video_fallback': 'CLIP',          # ویدیوی بدون قاعده (کلیپ دست‌ساز/ترند)
        'schedule_delay_min': 90,
        'schedule_gap_min': 20,            # حداقل فاصله‌ی زمان‌بندی این پست از هر پست دیگه
        'silent_audio': True,              # ویدیوی بی‌صدا → track ساکت (جلوگیری از GIF شدن)
        'protect_blocks': [r'^\s*(?:via|cre)\b'],
        'redundant_tags': {'wuwa': 'wutheringwaves'},
        'character_tags': 'Wuthering Waves',
    },
    # steambird.id — همه‌چیز LEAK؛ اسلاید اول (کاور تبلیغاتی) خودکار حذف می‌شه (۳ تای آخر عمداً نه،
    # چون گاهی محتوای اصلی اونجاست → خودت از scheduled پاک می‌کنی)؛ ۱۰٪ پایین عکس‌ها (فوتر برند پیج)
    # کراپ می‌شه؛ همه‌ی پست‌ها (عکس/ویدیو/آلبوم) ۶۰ دقیقه بعد منتشر می‌شن تا review کنی.
    'steambird': {
        'text_rules': [],
        'unmatched_non_video': 'LEAK',
        'video_fallback': 'LEAK',
        'drop_first': 1,
        'drop_last': 3,
        'drop_min_items': 5,
        'crop_bottom_pct': 10,
        'schedule_delay_min': 60,
        'redundant_tags': {'genshin': 'genshinimpact'},
        'strip_blocks': [
            r'^[^\w\n]*cover\s*art\b',           # ✦ Cover Art + لینک pixiv
            r'leaks\s+and\s+news\s+about',        # ↠ Leaks and News about Genshin Impact
            r'like\s*&\s*follow',                 # ↠ Like & Follow for more ...
            r'^[^\w\n]*hashtags?[^\w\n]*$',       # ✦ Hashtags (تیتر؛ خود هشتگ‌ها جدا استخراج می‌شن)
        ],
    },
    'montelli.news': {
        'text_rules': [],
        'unmatched_non_video': 'LEAK',
        'video_fallback': 'LEAK',
        'drop_first': 1,
        'drop_last': 3,
        'drop_min_items': 5,
        'crop_bottom_pct': 10,
        'schedule_delay_min': 60,
        'redundant_tags': {'wuwa': 'wutheringwaves'},
        'strip_blocks': [
              r'^[^\w\n]*status\s*$',                             # ✦ Status
              r'^[^\w\n]*(reliable|↠)\s*',                        # ↠ Reliable
              r'^[^\w\n]*wuthering\s+waves\s+leak\s+content',     # ✦ Wuthering Waves Leak Content
              r'follow\s+for\s+more\s+wuthering\s+waves\s+content', # ✦ Follow for more Wuthering Waves Content!
          ],
    },

    # gensh1nuniverse — پیج لیک؛ کپشن: TITLE / بولت‌ها / «Via X» / #GenshinImpact (بدون تبلیغ)
    # همه‌چیز LEAK؛ بدون کراپ و بدون حذف اسلاید؛ عنوان bold جدا نمی‌شه (همه داخل blockquote)؛
    # خط اعتبار «Via ...» هیچ‌وقت با کوتاه‌سازی حذف نمی‌شه؛ اسم شخصیت‌ها هشتگ می‌شن؛
    # ۹۰ دقیقه بعد منتشر می‌شه (steambird ۶۰ دقیقه) تا دو پیج با هم قاطی نشن.
    'gensh1nuniverse': {
    'text_rules': [
        {'kind': 'LEAK', 'video': True,
         'patterns': [('text', r'(?m)^[^\w\n]*(?:via|source)\b')]},
    ],
    'unmatched_non_video': 'LEAK',
    # video_fallback حذف شد → ویدیوی بدون Via/Source می‌ره سراغ قاعده‌ی مدت (Clip/Demo/Trailer)
    'schedule_delay_min': 90,
    'protect_blocks': [r'^\s*via\b'],
    'character_tags': 'Genshin Impact',
     },

    # ───────── پیج‌های ANINEXUS (aninexus_pages.json) ─────────
    # فعلاً خالی = رفتار سراسری؛ قواعد هر پیج بعد از تعیین اینجا پر می‌شه.
    # hanbil_project — فقط سه خط Song / Artist / Anime نگه داشته می‌شه (تبلیغ «Want faster requests»،
    # «Sinopsis:» و پاراگراف‌های سینوپسیس حذف می‌شن)؛ هشتگ‌های خود پیج عیناً استفاده می‌شن و
    # سیستم هشتگ‌سازی اجرا نمی‌شه.
     # ───────── پیج‌های جدید ANINEXUS ─────────
    'animeotakuamv': {
        'own_hashtags': True,
        'split_title': False,
        'text_replace': [
            [r'(?s)anime\s*de[sp]\w*.*?(?=#\w|\Z)', ''],   # از «Anime Depscription» تا اولین هشتگ
            [r'#\w*edit\w*', ''],                          # #animeedit #edit #amvedit ...
            [r'(?m)^\s*\.\s*$', ''],                       # خطوط فقط-نقطه
        ],
        'extra_hashtags': ['amv'],
    },

    'animepixelwave': {
        'own_hashtags': True,
        'hashtag_fallback': 'dictionary',      # بدون هشتگ ← فرنچایز/شخصیت از متن
        'fallback_hashtag': 'AnimeNews',       # اگر دیکشنری هم چیزی نیافت
        'skip_if_caption_matches': [r'[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]'],
        'skip_if_ad': True,
        'split_title': True,
        'strip_blocks': [r'\bfollow\s+@[\w.]+'],
    },

    'suraimmu': {
        'nfkc': True,                          # «𝗣𝗹𝗼𝘁/𝗔𝗻𝗶𝗺𝗲» یونیکد-بولد ← حروف عادی، تا regexها بخورند
        'split_title': True,                   # خط اول (قبل از Plot) = عنوان
        'strip_blocks': [
            r'[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]',   # پاراگراف CJK
            r'^\s*(anime|genres|rating|status|available|dub|song)\s*:',  # بلوک مشخصات
        ],
        'strip_label': r'^\s*plot\s*:\s*',
        'tags_from_field': r'^[ \t]*anime[ \t]*:[ \t]*(.+)$',
        'skip_if_all_cjk': True,
        'skip_if_ad': True,                    # جایگزین رد کردن هر منشن
    },  
  
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
    # (۱۱ به بعد) همون کپشن ثابت رو می‌گیرن (کپشن یکسان برای همه‌ی آلبوم‌ها، برای همه‌ی پیج‌ها).
    # ⚠️ fixed_caption جایگزین موقته — متن نهایی رو خودت بگو.
    'animeronix': {
        'drop_first': 1,
        'drop_last': 0,
        'split_title': False,
        'fixed_caption': '#Anime #AnimeSpotlight',
    },

    # ───────── پیج‌های ANIWALL (aniwall_pages.json) ─────────
    # کپشن همه‌شون فقط هشتگه (بلوک blockquote)؛ متن اصلی پست هیچ‌وقت وارد کپشن نمی‌شه.
    # پیج pixeldestiny_ عمداً پروفایل نداره = رفتار سراسری.

    # فقط هشتگ‌های خود پیج، منهای لیست‌های عمومی/ترند سراسری.
    'art_lab_anime': {
        'hashtag_mode': 'own',
        'default_hashtag': 'AnimeArt',
        'post_hour': 22,                  # ساعت تهران، یکی از ساعت‌های خالی
        'fixed_hashtags': ['Unco'],   # اختیاری
    },

    # تبلیغ ابزارها (kusart/kusapics/plotparty/openart) و هم‌معنی‌ها حذف؛ سقف ۶.
    'anime_aiverse8': {
        'hashtag_mode': 'own',
        'blocked_hashtags': [
            'demonslayerfanart', 'kimetsunoyaiba', 'hashira', 'shinobi', 'infinitycastle',
            'kusart', 'kusapics', 'kusapics_neko', 'plotparty', 'openart',
        ],
        'max_hashtags': 6,
        'default_hashtag': 'AnimeArt',
        'post_hour': 18,                  # ساعت تهران، یکی از ساعت‌های خالی
        'fixed_hashtags': ['Aesthetic'],   # اختیاری
    },

    # هشتگ‌های fyp و خراب (حرف غیرلاتین) و برند پیج حذف؛ اگه چیزی نموند #AnimeArt.
    'citseven7': {
        'hashtag_mode': 'own',
        'blocked_hashtags': ['fypp', 'illustration', 'digitalillustration', 'cit7creations','crossover'],
        'blocked_patterns': [r'^fyp', r'[^a-z0-9]'],
        'default_hashtag': 'AnimeArt',
        'post_hour': 5,                  # ساعت تهران، یکی از ساعت‌های خالی
        'fixed_hashtags': ['crossover'],   # اختیاری
    },
    # هر هشتگی که به art/fanart ختم بشه حذف؛ سقف ۶.
    'tsukoon.art': {
        'hashtag_mode': 'own',
        'blocked_hashtags': ['animeartist', 'tsudynamicshot', 'limelightai', 'will'],
        'blocked_patterns': [r'fanart$', r'art$'],
        'max_hashtags': 6,
        'default_hashtag': 'AnimeArt',
        'post_hour': 10,                  # ساعت تهران، یکی از ساعت‌های خالی
        'fixed_hashtags': ['illustration'],   # اختیاری
    },

    # هشتگ‌های خود پیج کاملاً دست‌نخورده (بدون حذف عمومی‌ها، بدون سقف).
    'redknight.wallpapers': {
        'hashtag_mode': 'raw',
        'default_hashtag': 'AnimeArt',
        'post_hour': 14,                  # ساعت تهران، یکی از ساعت‌های خالی
    },

    # قواعد الگویی مشترکِ پیج‌های پرهشتگ (deminiart / shae_aiz / ani.wonderz)
    'deminiart': {
        'hashtag_mode': 'own',
        'blocked_hashtags': ['kimetsunoyaiba', 'journey', 'ashandpikachu', 'jjk', 'posterdesign'],
        'blocked_patterns': [
            r'anime', r'wallpaper', r'aesthetic', r'community', r'edit', r'fans', r'fandom',
            r'vibes', r'daily', r'world', r'(art|arts|artist|artwork)$', r'viral', r'trending',
            r'^ai(?=art|anime|wallpaper|digital|generated|image|gallery|creat)',
        ],
        'max_hashtags': 6,
        'default_hashtag': 'AnimeArt',
        'post_hour': 19, 
    },

    # بدون هشتگ؛ فرنچایز/شخصیت از روی متن (چندزبانه) با دیکشنری؛ اگه چیزی پیدا نشد #AnimeArt.
    'sheep_dream_studio': {
        'hashtag_mode': 'dictionary',
        'default_hashtag': 'AnimeArt',
        'post_hour': 13,                  # ساعت تهران، یکی از ساعت‌های خالی
        'fixed_hashtags': ['Trendy'],
    },

    # هشتگ‌های تبلیغ ابزار حذف؛ #AnimeGirls مجازه (قاعده‌ی «anime» ندارد)؛ #mikasha غلط املایی Mikasa.
    'ai_anime_3.o': {
        'hashtag_mode': 'own',
        'blocked_hashtags': ['flowwallai', 'syntxai', 'syntx_creators', 'imagineartofficial', 'mujoai', 'aot', 'mikasha'],
        'max_hashtags': 6,
        'default_hashtag': 'AnimeArt',
        'post_hour': 21,                  # ساعت تهران، یکی از ساعت‌های خالی
        'fixed_hashtags': ['Solo'], 
    },

    # بلوک ثابتِ انتهای هر پست (anime/animecommunity/midjourneyai/...) حذف؛ سقف ۶.
    'ai_art_infinity': {
        'hashtag_mode': 'own',
        'blocked_hashtags': ['animecommunity', 'animeai', 'animeedits', 'aiartcommunity', 'midjourneyai'],
        'max_hashtags': 6,
        'default_hashtag': 'AnimeArt',
        'post_hour': 3,                  # ساعت تهران، یکی از ساعت‌های خالی
        'fixed_hashtags': ['Hype'], 
    },

    # ابزارهای تولید (midjourney/photoshop) و هشتگ‌های ختم‌شونده به art/arts/artist حذف؛ سقف ۶.
    'framezeroo': {
        'hashtag_mode': 'own',
        'blocked_hashtags': ['midjourney', 'photoshop', 'nature'],
        'blocked_patterns': [r'(art|arts|artist|artwork)$'],
        'max_hashtags': 6,
        'default_hashtag': 'AnimeArt',
        'post_hour': 9,                  # ساعت تهران، یکی از ساعت‌های خالی
        'fixed_hashtags': ['Solo'], 
    },
    'pixeldestiny_': {
        'post_hour': 6,
    },

    # هشتگ‌های برند پیج حذف + همان قواعد الگویی deminiart؛ سقف ۶.
    'ani.wonderz': {
        'hashtag_mode': 'own',
        'blocked_hashtags': ['aniwonderz', 'aniaiart', 'aeoncollabs'],
        'blocked_patterns': [
            r'anime', r'wallpaper', r'aesthetic', r'community', r'edit', r'fans', r'fandom',
            r'vibes', r'daily', r'world', r'(art|arts|artist|artwork)$', r'viral', r'trending',
            r'^ai(?=art|anime|wallpaper|digital|generated|image|gallery|creat)',
        ],
        'max_hashtags': 6,
        'default_hashtag': 'AnimeArt',
        'post_hour': 17,                  # ساعت تهران، یکی از ساعت‌های خالی
        'fixed_hashtags': ['illustration'], 
    },
}
