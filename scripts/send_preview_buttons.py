#!/usr/bin/env python3
"""
پست‌های N روز گذشته‌ی یه پروفایل اینستاگرام رو چک می‌کنه، هرکدوم که تو
seen_posts.json نبوده رو (به ترتیب تاریخ، قدیمی‌ترین اول) دانلود می‌کنه و
پیش‌نمایششو با دکمه‌های شیشه‌ای (از طریق بات، نه اکانت شخصی) به چت ریویو
می‌فرسته:
  - برای کروسل: هر اسلاید با دکمه‌ی toggle (☐/✅) زیرش، + یه پیام آخر با
    دکمه‌ی "تایید و ارسال"
  - برای پست تکی (عکس/ویدیو/ریلز): همون آیتم با دو دکمه‌ی "تایید" و "رد"

خود ارسال نهایی به کانال جای دیگه (توسط n8n، با صدا زدن workflow
download-send.yml) انجام می‌شه؛ این اسکریپت فقط پیش‌نمایش می‌فرسته.

استفاده:
  python send_preview_buttons.py --profile steambird.id --review-chat-id -1001234567890 \
      --ig-session ig_session.bin --seen-file data/seen_posts.json --days 7
"""

import argparse
import glob
import json
import os
import shutil
import sys
import tempfile
import time

import requests

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
IG_USERNAME = os.environ.get("IG_USERNAME")

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


def build_instaloader(ig_session_path: str):
    import instaloader

    L = instaloader.Instaloader(
        dirname_pattern=WORKDIR,
        filename_pattern='{shortcode}_{mediaid}',
        download_video_thumbnails=False,
        save_metadata=False,
        post_metadata_txt_pattern='',
        quiet=True,
        max_connection_attempts=1,
    )

    if not ig_session_path or not os.path.exists(ig_session_path) or not IG_USERNAME:
        print('ℹ️ بدون سشن اجرا می‌شه (anonymous) — فقط پروفایل‌های پابلیک قابل چک‌ان')
        return L

    try:
        L.load_session_from_file(IG_USERNAME, ig_session_path)
        print(f'✅ لاگین اینستا با موفقیت انجام شد: {L.context.username}')
    except Exception as e:
        print(f'⚠️ لود سشن شکست خورد ({e}) — ادامه به‌صورت anonymous')

    return L


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


def describe_post_type(post) -> str:
    if getattr(post, 'mediacount', 1) > 1:
        return f'کروسل ({post.mediacount} اسلاید)'
    if post.is_video:
        return 'ریلز/ویدیو'
    return 'عکس تکی'


def main():
    global API_BASE

    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', required=True)
    parser.add_argument('--review-chat-id', required=True)
    parser.add_argument('--ig-session', default='', help='مسیر فایل سشن (اختیاری — نبود آن یعنی anonymous)')
    parser.add_argument('--seen-file', default='data/seen_posts.json')
    parser.add_argument('--days', type=int, default=7)
    args = parser.parse_args()

    if not TG_BOT_TOKEN:
        sys.exit('❌ TG_BOT_TOKEN تنظیم نشده (سکرت بات، نه اکانت شخصی)')
    API_BASE = f'https://api.telegram.org/bot{TG_BOT_TOKEN}'

    import instaloader

    L = build_instaloader(args.ig_session)
    profile = instaloader.Profile.from_username(L.context, args.profile)

    cutoff = time.time() - args.days * 86400
    seen = load_seen(args.seen_file)

    candidates = []
    for post in profile.get_posts():
        if post.date_utc.timestamp() < cutoff:
            break
        if post.shortcode not in seen:
            candidates.append(post)

    if not candidates:
        print('ℹ️  پست جدیدی تو این بازه نیست.')
        return

    for post in reversed(candidates):  # قدیمی‌ترین اول
        shortcode = post.shortcode
        post_url = f'https://www.instagram.com/p/{shortcode}/'
        kind = describe_post_type(post)
        print(f'⬇️  دانلود: {post_url} ({kind})')

        post_dir = os.path.join(WORKDIR, shortcode)
        os.makedirs(post_dir, exist_ok=True)
        try:
            L.download_post(post, target=post_dir)
        except Exception as e:
            print(f'⚠️  دانلود {shortcode} شکست خورد: {e} — رد شد.')
            continue

        files = sorted(
            f for f in glob.glob(os.path.join(post_dir, '*'))
            if os.path.isfile(f) and f.lower().endswith(('.jpg', '.jpeg', '.png', '.mp4'))
        )
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
