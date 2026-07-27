#!/usr/bin/env python3
"""
پست‌های N روز گذشته‌ی یه پروفایل اینستاگرام رو چک می‌کنه، هرکدوم که تو
seen_posts.json نبوده رو (به ترتیب تاریخ، قدیمی‌ترین اول) دانلود می‌کنه و
پیش‌نمایششو با دکمه‌های شیشه‌ای (از طریق بات، نه اکانت شخصی) به چت ریویو
می‌فرسته:
  - برای کروسل: هر اسلاید با دکمه‌ی toggle (☐/✅) زیرش، + یه پیام آخر با
    دکمه‌ی "تایید و ارسال"
  - برای پست تکی (عکس/ویدیو/ریلز): همون آیتم با دو دکمه‌ی "تایید" و "رد"

پیدا کردن/دیدن پست‌ها الان از طریق EnsembleData API انجام می‌شه (چون مسیر
instaloader's Profile.from_username فعلاً به‌طور گسترده روی اینستاگرام
بروکن‌ه). خود ارسال نهایی به کانال جای دیگه (توسط n8n، با صدا زدن workflow
download-send.yml که همچنان از instaloader/Post.from_shortcode استفاده
می‌کنه) انجام می‌شه؛ این اسکریپت فقط پیش‌نمایش می‌فرسته.

استفاده:
  python send_preview_buttons.py --profile steambird.id --review-chat-id -1001234567890 \
      --seen-file data/seen_posts.json --days 7

نیاز محیطی:
  ENSEMBLEDATA_TOKEN   توکن EnsembleData (از dashboard.ensembledata.com)
  TG_BOT_TOKEN         توکن بات تلگرام (برای ارسال پیش‌نمایش)

نصب:
  pip install ensembledata requests
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

import requests
from ensembledata.api import EDClient, EDError

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
ENSEMBLEDATA_TOKEN = os.environ.get("ENSEMBLEDATA_TOKEN")

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


def ed_get_user_id(client: EDClient, username: str) -> str:
    """یوزرنیم رو به user_id (pk) عددی اینستاگرام تبدیل می‌کنه."""
    result = client.instagram.user_info(username=username)
    data = result.data
    # ساختار دقیق پاسخ ممکنه بسته به نسخه‌ی API کمی فرق کنه؛ چند مسیر محتمل رو امتحان می‌کنیم
    user_id = (
        data.get('pk')
        or data.get('id')
        or data.get('user_id')
        or (data.get('user') or {}).get('pk')
        or (data.get('data') or {}).get('pk')
    )
    if not user_id:
        raise RuntimeError(f'user_id تو پاسخ EnsembleData پیدا نشد. پاسخ خام: {json.dumps(data)[:500]}')
    return str(user_id)


def ed_get_recent_posts(client: EDClient, user_id: str, oldest_timestamp: int) -> list:
    """لیست پست‌های اخیر پروفایل رو از EnsembleData می‌گیره."""
    result = client.instagram.user_posts(
        user_id=user_id,
        depth=1,
        oldest_timestamp=oldest_timestamp,
        chunk_size=20,
    )
    data = result.data
    print(f'🧾 نوع result.data: {type(data).__name__}')
    if isinstance(data, dict):
        print(f'🧾 کلیدهای سطح اول result.data: {list(data.keys())}')
    print(f'🧾 خام (۱۲۰۰ کاراکتر اول): {json.dumps(data, ensure_ascii=False, default=str)[:1200]}')

    # پاسخ معمولاً { "data": [ ...posts... ] } یا مستقیم لیست هست
    posts = data.get('data') if isinstance(data, dict) else data
    return posts or []


def extract_media_items(post: dict) -> list:
    """
    از یه آبجکت پست، لیست آیتم‌های قابل‌دانلود (عکس/ویدیو) رو استخراج می‌کنه.
    هر آیتم: {"url": ..., "is_video": bool}
    برای کروسل، همه‌ی media جدا برگردونده می‌شن.
    """
    items = []

    def media_url(node: dict):
        if node.get('video_versions'):
            return node['video_versions'][0]['url'], True
        candidates = (node.get('image_versions2') or {}).get('candidates') or []
        if candidates:
            return candidates[0]['url'], False
        return None, False

    carousel = post.get('carousel_media')
    if carousel:
        for node in carousel:
            url, is_video = media_url(node)
            if url:
                items.append({'url': url, 'is_video': is_video})
    else:
        url, is_video = media_url(post)
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


def describe_post_type(post: dict, n_items: int) -> str:
    if n_items > 1:
        return f'کروسل ({n_items} اسلاید)'
    if post.get('video_versions'):
        return 'ریلز/ویدیو'
    return 'عکس تکی'


def main():
    global API_BASE

    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', required=True)
    parser.add_argument('--review-chat-id', required=True)
    parser.add_argument('--seen-file', default='data/seen_posts.json')
    parser.add_argument('--days', type=int, default=7)
    args = parser.parse_args()

    if not TG_BOT_TOKEN:
        sys.exit('❌ TG_BOT_TOKEN تنظیم نشده (سکرت بات، نه اکانت شخصی)')
    if not ENSEMBLEDATA_TOKEN:
        sys.exit('❌ ENSEMBLEDATA_TOKEN تنظیم نشده')
    API_BASE = f'https://api.telegram.org/bot{TG_BOT_TOKEN}'

    # اگه دستی از URL پیست شده، فقط یوزرنیم خالص رو نگه می‌داریم
    profile_username = args.profile.strip().lstrip('@').split('?')[0].rstrip('/')

    client = EDClient(ENSEMBLEDATA_TOKEN)

    try:
        user_id = ed_get_user_id(client, profile_username)
    except EDError as e:
        sys.exit(f'❌ خطای EnsembleData موقع گرفتن user_id: {e.detail}')
    print(f'🆔 user_id پیدا شد: {user_id}')

    cutoff = int(time.time() - args.days * 86400)
    seen = load_seen(args.seen_file)

    try:
        posts = ed_get_recent_posts(client, user_id, cutoff)
    except EDError as e:
        sys.exit(f'❌ خطای EnsembleData موقع گرفتن پست‌ها: {e.detail}')

    print(f'📦 EnsembleData تعداد {len(posts)} پست خام برگردوند.')
    if posts:
        print(f'🔍 نمونه‌ی اولین پست (خام، ۸۰۰ کاراکتر اول):\n{json.dumps(posts[0], ensure_ascii=False)[:800]}')
    else:
        print('⚠️  لیست پست‌های خام هم صفره — یعنی مشکل از EnsembleData/پارامترهاست، نه از فیلتر seen/days.')

    candidates = []
    skipped_seen = 0
    skipped_old = 0
    skipped_no_code = 0
    for post in posts:
        shortcode = post.get('code')
        taken_at = post.get('taken_at') or post.get('taken_at_timestamp')
        if not shortcode:
            skipped_no_code += 1
            continue
        if taken_at and taken_at < cutoff:
            skipped_old += 1
            continue
        if shortcode in seen:
            skipped_seen += 1
            continue
        candidates.append(post)
    print(
        f'📊 فیلتر: {len(candidates)} کاندید جدید | '
        f'{skipped_seen} قبلاً seen | {skipped_old} قدیمی‌تر از {args.days} روز | '
        f'{skipped_no_code} بدون فیلد code'
    )

    if not candidates:
        print('ℹ️  پست جدیدی تو این بازه نیست.')
        return

    for post in reversed(candidates):  # قدیمی‌ترین اول
        shortcode = post.get('code')
        post_url = f'https://www.instagram.com/p/{shortcode}/'

        media_items = extract_media_items(post)
        if not media_items:
            print(f'⚠️  آیتم قابل‌دانلودی برای {shortcode} پیدا نشد، رد شد.')
            continue

        kind = describe_post_type(post, len(media_items))
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
