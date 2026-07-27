#!/usr/bin/env python3
"""
پست‌های N روز گذشته‌ی یه پروفایل اینستاگرام رو چک می‌کنه، هرکدوم که تو
seen_posts.json نبوده رو (به ترتیب تاریخ، قدیمی‌ترین اول) دانلود می‌کنه و
پیش‌نمایششو با دکمه‌های شیشه‌ای (از طریق بات، نه اکانت شخصی) به چت ریویو
می‌فرسته:
  - برای کروسل: هر اسلاید با دکمه‌ی toggle (☐/✅) زیرش، + یه پیام آخر با
    دکمه‌ی "تایید و ارسال"
  - برای پست تکی (عکس/ویدیو/ریلز): همون آیتم با دو دکمه‌ی "تایید" و "رد"

پیدا کردن/دیدن پست‌ها الان از طریق instagrapi (Instagram Private API، همون
APIای که اپ موبایل استفاده می‌کنه) انجام می‌شه — چون مسیر instaloader's
Profile.from_username و همچنین EnsembleData هردو تجربه‌شون شکست خورد.
instagrapi نیاز به لاگین واقعی با یه اکانت (ترجیحاً یه اکانت مصرفی/throwaway،
نه اکانت اصلی) داره.

خود ارسال نهایی به کانال جای دیگه (توسط n8n، با صدا زدن workflow
download-send.yml که از instaloader/Post.from_shortcode استفاده می‌کنه)
انجام می‌شه؛ این اسکریپت فقط پیش‌نمایش می‌فرسته.

استفاده:
  python send_preview_buttons.py --profile steambird.id --review-chat-id -1001234567890 \
      --ig-session ig_session.json --seen-file data/seen_posts.json --days 7

نیاز محیطی:
  TG_BOT_TOKEN     توکن بات تلگرام (برای ارسال پیش‌نمایش)
  IG_USERNAME      یوزرنیم اکانت اینستای مصرفی (برای لاگین/رفرش سشن)
  IG_PASSWORD      پسورد همون اکانت (برای لاگین/رفرش سشن)

نصب:
  pip install instagrapi requests
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

import requests
from instagrapi import Client
from instagrapi.exceptions import ClientError, LoginRequired

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
IG_USERNAME = os.environ.get("IG_USERNAME")
IG_PASSWORD = os.environ.get("IG_PASSWORD")

WORKDIR = tempfile.mkdtemp(prefix="preview_")
API_BASE = None  # بعد از خوندن TG_BOT_TOKEN ست می‌شه


def load_seen(seen_file: str) -> set:
    if os.path.exists(seen_file):
        with open(seen_file, 'r', encoding='utf-8') as f:
            try:
                return set(json.load(f))
            except json.JSONDecodeError:
                return set()
    return set()


def save_seen(seen_file: str, seen: set):
    os.makedirs(os.path.dirname(seen_file) or '.', exist_ok=True)
    with open(seen_file, 'w', encoding='utf-8') as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)


def build_client(session_path: str) -> Client:
    """
    یه Client لاگین‌شده‌ی instagrapi می‌سازه. اگه فایل سشن موجود باشه ازش
    استفاده می‌کنه (بدون لاگین مجدد کامل)؛ اگه نبود یا نامعتبر بود، با
    یوزر/پسورد لاگین می‌کنه و سشن جدید رو ذخیره می‌کنه.
    """
    if not IG_USERNAME or not IG_PASSWORD:
        sys.exit('❌ IG_USERNAME و IG_PASSWORD تنظیم نشدن (سکرت‌های اکانت مصرفی اینستا)')

    cl = Client()

    if session_path and os.path.exists(session_path):
        try:
            cl.load_settings(session_path)
            cl.login(IG_USERNAME, IG_PASSWORD)
            cl.get_timeline_feed()  # چک می‌کنیم سشن واقعاً معتبره
            print('✅ سشن قبلی معتبر بود، بدون لاگین کامل ادامه داده شد.')
            return cl
        except (LoginRequired, ClientError, Exception) as e:
            print(f'⚠️ سشن قبلی معتبر نبود ({e}) — لاگین کامل انجام می‌شه.')
            cl = Client()

    cl.login(IG_USERNAME, IG_PASSWORD)
    if session_path:
        os.makedirs(os.path.dirname(session_path) or '.', exist_ok=True)
        cl.dump_settings(session_path)
        print(f'✅ لاگین انجام شد و سشن جدید تو {session_path} ذخیره شد.')
    return cl


def extract_media_items(media) -> list:
    """
    از یه شیء Media (instagrapi)، لیست آیتم‌های قابل‌دانلود رو استخراج می‌کنه.
    هر آیتم: {"url": ..., "is_video": bool}
    """
    items = []

    def resource_url(res):
        if getattr(res, 'video_url', None):
            return str(res.video_url), True
        if getattr(res, 'thumbnail_url', None):
            return str(res.thumbnail_url), False
        return None, False

    if media.resources:  # کروسل
        for res in media.resources:
            url, is_video = resource_url(res)
            if url:
                items.append({'url': url, 'is_video': is_video})
    else:
        url, is_video = resource_url(media)
        if url:
            items.append({'url': url, 'is_video': is_video})

    return items


def download_media(url: str, is_video: bool, dest_dir: str, idx: int) -> str:
    ext = '.mp4' if is_video else '.jpg'
    filepath = os.path.join(dest_dir, f'{idx}{ext}')
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    with open(filepath, 'wb') as f:
        f.write(resp.content)
    return filepath


def tg_call(method: str, data: dict, files=None):
    url = f'{API_BASE}/{method}'
    resp = requests.post(url, data=data, files=files, timeout=60)
    result = resp.json()
    if not result.get('ok'):
        raise RuntimeError(f'خطای Telegram Bot API در {method}: {result}')
    return result['result']


def send_slide(chat_id, filepath, caption, callback_data, button_label):
    keyboard = {'inline_keyboard': [[{'text': button_label, 'callback_data': callback_data}]]}
    with open(filepath, 'rb') as f:
        is_video = filepath.lower().endswith('.mp4')
        method = 'sendVideo' if is_video else 'sendPhoto'
        field = 'video' if is_video else 'photo'
        tg_call(method, {
            'chat_id': chat_id,
            'caption': caption,
            'reply_markup': json.dumps(keyboard),
        }, files={field: f})


def send_confirm_message(chat_id, text, shortcode):
    keyboard = {
        'inline_keyboard': [[
            {'text': '✅ تایید و ارسال', 'callback_data': f'cf:{shortcode}'},
        ]]
    }
    tg_call('sendMessage', {
        'chat_id': chat_id,
        'text': text,
        'reply_markup': json.dumps(keyboard),
    })


def send_single_item(chat_id, filepath, caption, shortcode):
    keyboard = {
        'inline_keyboard': [[
            {'text': '✅ تایید', 'callback_data': f'cf:{shortcode}'},
            {'text': '❌ رد', 'callback_data': f'rj:{shortcode}'},
        ]]
    }
    with open(filepath, 'rb') as f:
        is_video = filepath.lower().endswith('.mp4')
        method = 'sendVideo' if is_video else 'sendPhoto'
        field = 'video' if is_video else 'photo'
        tg_call(method, {
            'chat_id': chat_id,
            'caption': caption,
            'reply_markup': json.dumps(keyboard),
        }, files={field: f})


def describe_post_type(media, n_items: int) -> str:
    if n_items > 1:
        return f'کروسل ({n_items} اسلاید)'
    if media.media_type == 2:
        return 'ریلز/ویدیو'
    return 'عکس تکی'


def main():
    global API_BASE

    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', required=True)
    parser.add_argument('--review-chat-id', required=True)
    parser.add_argument('--ig-session', default='ig_session.json', help='مسیر فایل سشن instagrapi')
    parser.add_argument('--seen-file', default='data/seen_posts.json')
    parser.add_argument('--days', type=int, default=7)
    args = parser.parse_args()

    if not TG_BOT_TOKEN:
        sys.exit('❌ TG_BOT_TOKEN تنظیم نشده (سکرت بات، نه اکانت شخصی)')
    API_BASE = f'https://api.telegram.org/bot{TG_BOT_TOKEN}'

    # اگه دستی از URL پیست شده، فقط یوزرنیم خالص رو نگه می‌داریم
    profile_username = args.profile.strip().lstrip('@').split('?')[0].rstrip('/')

    cl = build_client(args.ig_session)

    try:
        user_id = cl.user_id_from_username(profile_username)
    except Exception as e:
        sys.exit(f'❌ پیدا کردن user_id برای {profile_username} شکست خورد: {e}')
    print(f'🆔 user_id پیدا شد: {user_id}')

    cutoff = int(time.time() - args.days * 86400)
    seen = load_seen(args.seen_file)

    try:
        medias = cl.user_medias(user_id, amount=30)
    except Exception as e:
        sys.exit(f'❌ گرفتن پست‌ها شکست خورد: {e}')

    print(f'📦 تعداد {len(medias)} پست خام برگشت.')

    candidates = []
    skipped_seen = 0
    skipped_old = 0
    for media in medias:
        shortcode = media.code
        taken_at = media.taken_at.timestamp() if media.taken_at else None
        if taken_at and taken_at < cutoff:
            skipped_old += 1
            continue
        if shortcode in seen:
            skipped_seen += 1
            continue
        candidates.append(media)
    print(
        f'📊 فیلتر: {len(candidates)} کاندید جدید | '
        f'{skipped_seen} قبلاً seen | {skipped_old} قدیمی‌تر از {args.days} روز'
    )

    if not candidates:
        print('ℹ️  پست جدیدی تو این بازه نیست.')
        return

    for media in reversed(candidates):  # قدیمی‌ترین اول
        shortcode = media.code
        post_url = f'https://www.instagram.com/p/{shortcode}/'

        media_items = extract_media_items(media)
        if not media_items:
            print(f'⚠️  آیتم قابل‌دانلودی برای {shortcode} پیدا نشد، رد شد.')
            continue

        kind = describe_post_type(media, len(media_items))
        print(f'⬇️  دانلود: {post_url} ({kind})')

        post_dir = os.path.join(WORKDIR, shortcode)
        os.makedirs(post_dir, exist_ok=True)

        files = []
        try:
            for idx, item in enumerate(media_items, start=1):
                files.append(download_media(item['url'], item['is_video'], post_dir, idx))
        except Exception as e:
            print(f'⚠️  دانلود {shortcode} شکست خورد: {e} — رد شد.')
            continue

        if not files:
            print(f'⚠️  فایلی برای {shortcode} پیدا نشد، رد شد.')
            continue

        try:
            if len(files) == 1:
                caption = f'{kind}\n{post_url}\nshortcode: {shortcode}'
                send_single_item(args.review_chat_id, files[0], caption, shortcode)
            else:
                total = len(files)
                for idx, filepath in enumerate(files, start=1):
                    caption = f'اسلاید {idx}/{total} — {shortcode}'
                    send_slide(
                        args.review_chat_id, filepath, caption,
                        callback_data=f'tg:{shortcode}:{idx}',
                        button_label=f'☐ اسلاید {idx}',
                    )
                send_confirm_message(
                    args.review_chat_id,
                    f'{kind}\n{post_url}\nshortcode: {shortcode}\n\n'
                    f'اسلایدهای موردنظر رو تیک بزن، بعد «تایید و ارسال» رو بزن.',
                    shortcode,
                )
        except Exception as e:
            print(f'❌ ارسال پیش‌نمایش {shortcode} شکست خورد: {e} — این پست seen نمی‌شه، دوباره تلاش می‌شه.')
            continue

        seen.add(shortcode)
        save_seen(args.seen_file, seen)
        print(f'✅ پیش‌نمایش {shortcode} فرستاده شد.')

    shutil.rmtree(WORKDIR, ignore_errors=True)


if __name__ == '__main__':
    main()
