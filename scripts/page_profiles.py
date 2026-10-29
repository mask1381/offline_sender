# -*- coding: utf-8 -*-
"""page_profiles.py — پروفایل‌های اختصاصی پیج‌ها (بخش ۱.۵ گزارش).

قبلاً این فایل خالی بود («پروفایل‌های واقعی اینجا نیست؛ بات اون‌ها رو داخل هر batch
می‌فرسته») و ادمین هیچ مرجع قابل‌ویرایشی برای دیدن/ساختن پروفایل نداشت.

الان:
  - PROFILES یک دیکشنری مستند است (گوهرِ زیرساخت اسکریپت‌ها — همین‌جا تعریف و
    ویرایش می‌شه، روی ریپو کامیت می‌شه، و batch اون رو با update رویش overwrite
    می‌کنه: `d3.PROFILES.update(batch.get('profiles') or {})` در process_batch.py).
  - هر کلیدِ پروفایل یک dict از قواعد اختیاری است (کلیدها در کامنتِ هر نمونه شرح
    داده شده). پیج بدون profile → 'default' → قواعد سراسری، بدون تغییر.
  - `validate_profiles()` در پایان این فایل اجرا می‌شه و غلط‌های ساختاری (نوع اشتباه)
    رو موقع import گزارش می‌ده — قبل از اینکه اسکریپت با KeyError وسط ران بشکنه.

منبع کلیدها: download_and_send3.py / download_and_send4.py / insta_checkها.
"""

# ─────────────────────────────────────────────────────────────
# ساختار هر پروفایل (همه اختیاری):
#
#   دانلود/ویدیو (download_and_send3):
#     drop_first / drop_last        : اسلایدهای تبلیغاتیِ ثابت اول/آخر (عدد)
#     drop_min_items                : حداقل آیتم برای اعمال drop (پیش‌فرض first+last+1)
#     crop_bottom_pct               : درصد برش پایین عکس‌ها (عدد، مثل 12)
#     silent_audio                  : درست = به ویدیوی بی‌صدا track صوتی اضافه کن (بعد از ensure_audio)
#     schedule_gap_min              : حداقل فاصله از پست قبلی در همین ران (عدد، دقیقه)
#     extra_rules / text_rules      : قواعد نوع‌دهی (لیست)
#     unmatched_non_video /
#     video_fallback                : رفتار وقتی قاعده‌ای نخورد (مقادیر از rules.py)
#     strip_blocks                  : الگوهای بلوک/هشتگ که از کپشن حذف می‌شن
#     split_title / protect_blocks /
#     character_tags / redundant_tags : ساخت کپشن رده (download_and_send3.build_*)
#
#   هشتگ/کپشن (download_and_send4):
#     hashtag_mode                  : 'all' | 'filtered' | 'explicit' | ...
#     blocked_hashtags / allowed_hashtags / blocked_patterns
#     max_hashtags / default_hashtag / fixed_hashtags
#     fixed_caption / raw_caption / keep_lines / strip_label / split_title
#     own_hashtags / tags_from_field / hashtag_fallback / fallback_hashtag
#     continuation_caption / nfkc / text_replace / drop_text / skip_if_*
#
#   زمان‌بندی (aniwall/gacha):
#     post_hour                     : ساعت انتشار (به وقت Tehran، 0-23)
#     schedule_gap_min              : (بالا)
# ─────────────────────────────────────────────────────────────

PROFILES = {
    # قواعد سراسری پیج‌های بدون profile — این‌جا میتوان فقط کلیدهای «پیش‌فرضِ همه» را گذاشت.
    'default': {},
}


def validate_profiles(profiles=None):
    """ساختار پروفایل‌ها را چک می‌کند و غلط را در stdout گزارش می‌دهد.

    چون لاگ GitHub Actions عمومیه، اسم پیج‌های واقعی چاپ نمی‌شه — فقط اسم پروفایل
    (که یک برچسب بی‌ضرر است، نه آیدی صفحه). هیچ مقداری بازگشت نمی‌کند که بتواند
    محتوای خصوصی لو بدهد.
    """
    profiles = profiles if profiles is not None else PROFILES
    errors = 0
    for name, body in (profiles or {}).items():
        if not isinstance(body, dict):
            print(f"⚠️ پروفایل {name!r}: باید dict باشد (الان: {type(body).__name__})")
            errors += 1
            continue
        for key in ('drop_first', 'drop_last', 'drop_min_items', 'post_hour',
                    'schedule_gap_min', 'crop_bottom_pct', 'max_hashtags'):
            if key in body and not isinstance(body[key], (int, float)):
                print(f"⚠️ پروفایل {name!r}: کلید {key} باید عدد باشد")
                errors += 1
        for key in ('blocked_hashtags', 'allowed_hashtags', 'extra_hashtags',
                    'blocked_patterns', 'text_rules', 'extra_rules', 'strip_blocks'):
            if key in body and not isinstance(body[key], (list, tuple)):
                print(f"⚠️ پروفایل {name!r}: کلید {key} باید لیست باشد")
                errors += 1
        for key in ('silent_audio', 'raw_caption', 'nfkc', 'own_hashtags'):
            if key in body and not isinstance(body[key], bool):
                print(f"⚠️ پروفایل {name!r}: کلید {key} باید boolean باشد")
                errors += 1
        if isinstance(body.get('redundant_tags'), dict) is False and 'redundant_tags' in body:
            print(f"⚠️ پروفایل {name!r}: redundant_tags باید dict باشد")
            errors += 1
        if isinstance(body.get('text_replace', []), (list, tuple)) and 'text_replace' in body:
            for pair in body['text_replace']:
                if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
                    print(f"⚠️ پروفایل {name!r}: text_replace باید لیستی از جفت (pattern, repl) باشد")
                    errors += 1
    if errors:
        print(f"⚠️ validate_profiles: {errors} مشکل ساختاری در پروفایل‌ها (رفع کنید تا ران‌ها وسط کار نشکنن).")
    else:
        print(f"✅ پروفایل‌ها معتبرند ({len(profiles)} پروفایل).")


# لیست مرجع پروفایل‌های تعریف‌شده (برای پیام‌های خطای resolve_profile)
def profile_names():
    return sorted(PROFILES)


if __name__ == "__main__":
    validate_profiles()
    for name in profile_names():
        print(f"• {name}: {sorted(PROFILES[name])}")
