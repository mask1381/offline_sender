#!/usr/bin/env python3
"""
اسکریپت یک‌باره (نه سرور دائمی) که توسط GitHub Actions اجرا می‌شه.
فقط اینستاگرام (پست تکی/ویدیو/کروسل چند اسلایدی) → با instaloader

دو حالت اجرا (--mode):
  - preview : همه‌ی اسلایدهای پست رو با شماره به یه چت پیش‌نمایش می‌فرسته (برای تایید دستی)
  - send    : فقط اسلایدهای انتخابی (--slides) رو با کپشن نهایی به کانال اصلی می‌فرسته

بعد از اجرا، این اسکریپت و کل VM موقتِ GitHub از بین می‌ره - هیچ سروری باقی نمی‌مونه.
"""

import argparse
import asyncio
import glob
import json
import os
import re
import shutil
import sys
import tempfile

import requests
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from telethon.tl.types import DocumentAttributeFilename

# ─────────────────────────────────────────
# تنظیمات از Environment Variables (GitHub Secrets)
# ─────────────────────────────────────────
TG_API_ID = os.environ.get("TG_API_ID")
TG_API_HASH = os.environ.get("TG_API_HASH")
TG_SESSION_STRING = os.environ.get("TG_SESSION_STRING")
IG_USERNAME = os.environ.get("IG_USERNAME")

# توکن بات (فقط برای پیش‌نمایش با دکمه — ارسال نهایی همچنان با اکانت شخصی/Telethon است)
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")

# پراکسی رزیدنشیال برای عبور از بلاک IP دیتاسنتری گیت‌هاب
# فرمت: http://user:pass@host:port   یا   socks5://user:pass@host:port
IG_PROXY_URL = os.environ.get("IG_PROXY_URL", "").strip()

# کدام مرورگر واقعی برای جعل TLS fingerprint شبیه‌سازی بشه (curl_cffi)
CURL_CFFI_IMPERSONATE = os.environ.get("CURL_CFFI_IMPERSONATE", "chrome124").strip()

WORKDIR = tempfile.mkdtemp(prefix="job_")

BOLD_ITALIC_MAP = {
    'a': '𝒂', 'b': '𝒃', 'c': '𝒄', 'd': '𝒅', 'e': '𝒆', 'f': '𝒇', 'g': '𝒈', 'h': '𝒉', 'i': '𝒊',
    'j': '𝒋', 'k': '𝒌', 'l': '𝒍', 'm': '𝒎', 'n': '𝒏', 'o': '𝒐', 'p': '𝒑', 'q': '𝒒', 'r': '𝒓',
    's': '𝒔', 't': '𝒕', 'u': '𝒖', 'v': '𝒗', 'w': '𝒘', 'x': '𝒙', 'y': '𝒚', 'z': '𝒛',
    'A': '𝑨', 'B': '𝑩', 'C': '𝑪', 'D': '𝑫', 'E': '𝑬', 'F': '𝑭', 'G': '𝑮', 'H': '𝑯', 'I': '𝑰',
    'J': '𝑱', 'K': '𝑲', 'L': '𝑳', 'M': '𝑴', 'N': '𝑵', 'O': '𝑶', 'P': '𝑷', 'Q': '𝑸', 'R': '𝑹',
    'S': '𝑺', 'T': '𝑻', 'U': '𝑼', 'V': '𝑽', 'W': '𝑾', 'X': '𝑿', 'Y': '𝒀', 'Z': '𝒁',
    ' ': ' ', ':': ':',
}


def bi(text: str) -> str:
    return ''.join(BOLD_ITALIC_MAP.get(ch, ch) for ch in text)


# ─────────────────────────────────────────
# منطق کپشن اینستاگرام (لیک/ریپورت با کردیت پیج منبع)
# ─────────────────────────────────────────
def build_caption_instagram(source_username: str) -> str:
    caption = f"⚡️ <b>{bi('TEYVAT LEAK')}</b>\n\n"
    caption += (
        f"🔗 {bi('Source')}: "
        f'<a href="https://www.instagram.com/{source_username}">@{source_username}</a>\n\n'
    )
    caption += f'🌸 <a href="https://t.me/genshinworldsensei">{bi("GWS | Teyvat Archive")}</a>'
    return caption


# ─────────────────────────────────────────
# جایگزینی requests.Session داخلی instaloader با یه session از curl_cffi
# که رفتار TLS/HTTP2 مرورگر واقعی رو جعل می‌کنه (fingerprint spoofing)
# + وصل کردن پراکسی رزیدنشیال (اگه تنظیم شده باشه)
# ─────────────────────────────────────────
def harden_session(L):
    if not IG_PROXY_URL and not CURL_CFFI_IMPERSONATE:
        return

    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        print('⚠️ curl_cffi نصب نیست — بدون جعل TLS fingerprint ادامه می‌دیم (pip install curl_cffi)')
        return

    old_session = L.context._session

    proxies = None
    if IG_PROXY_URL:
        proxies = {'http': IG_PROXY_URL, 'https': IG_PROXY_URL}

    new_session = cffi_requests.Session(
        impersonate=CURL_CFFI_IMPERSONATE or None,
        proxies=proxies,
        timeout=L.context.request_timeout or 300,
    )

    # کوکی‌ها و هدرهای قبلی (از جمله سشن لاگین‌شده، اگه لود شده باشه) رو منتقل می‌کنیم
    try:
        new_session.cookies.update(dict(old_session.cookies))
    except Exception:
        pass
    try:
        new_session.headers.update(dict(old_session.headers))
    except Exception:
        pass

    L.context._session = new_session

    proxy_note = 'با پراکسی رزیدنشیال' if IG_PROXY_URL else 'بدون پراکسی'
    print(f'🛡️ session با curl_cffi (impersonate={CURL_CFFI_IMPERSONATE!r}, {proxy_note}) جایگزین شد')


# ─────────────────────────────────────────
# راه‌اندازی instaloader (پیش‌فرض: anonymous / بدون لاگین)
# ─────────────────────────────────────────
def build_instaloader_instance(ig_session_path: str):
    import instaloader

    L = instaloader.Instaloader(
        dirname_pattern=WORKDIR,
        filename_pattern='{shortcode}_{mediaid}',
        download_video_thumbnails=False,
        save_metadata=False,
        post_metadata_txt_pattern='',
        quiet=True,
        max_connection_attempts=1,  # anonymous: سریع fail بشه به‌جای retry loop طولانی
    )

    # اگه session داده نشده باشه، عمداً anonymous کار می‌کنیم (بدون لاگین)
    if not ig_session_path:
        print('ℹ️ بدون سشن اجرا می‌شه (anonymous) — فقط پست‌های پابلیک قابل دانلودن')
        harden_session(L)
        return L

    if not os.path.exists(ig_session_path):
        print(f'⚠️ فایل سشن پیدا نشد ({ig_session_path!r}) — ادامه به‌صورت anonymous')
        harden_session(L)
        return L

    if not IG_USERNAME:
        print('⚠️ IG_USERNAME تنظیم نشده — نمی‌شه سشن رو لود کرد، ادامه به‌صورت anonymous')
        harden_session(L)
        return L

    try:
        L.load_session_from_file(IG_USERNAME, ig_session_path)
        is_logged_in = getattr(L.context, 'is_logged_in', bool(L.context.username))
        if is_logged_in:
            print(f'✅ لاگین اینستا با موفقیت انجام شد: {L.context.username}')
        else:
            print('⚠️ سشن معتبر نیست (احتمالاً منقضی شده) — ادامه به‌صورت anonymous')
    except Exception as e:
        print(f'⚠️ لود سشن شکست خورد ({e}) — ادامه به‌صورت anonymous')

    harden_session(L)
    return L


# ─────────────────────────────────────────
# دانلود اینستاگرام با instaloader (پشتیبانی از کروسل چند اسلایدی)
# فقط دانلود خام — تصمیم درباره‌ی فیلتر/کپشن به‌عهده‌ی main() است
# ─────────────────────────────────────────
def download_instagram(url: str, ig_session_path: str):
    import instaloader

    L = build_instaloader_instance(ig_session_path)

    shortcode_match = re.search(r'/(?:p|reel|tv)/([^/?#]+)', url)
    if not shortcode_match:
        print('❌ نتونستم shortcode رو از لینک استخراج کنم')
        sys.exit(1)
    shortcode = shortcode_match.group(1)

    post = instaloader.Post.from_shortcode(L.context, shortcode)
    source_username = post.owner_username

    L.download_post(post, target=WORKDIR)

    files = sorted(f for f in glob.glob(os.path.join(WORKDIR, '*'))
                    if os.path.isfile(f) and f.lower().endswith(('.jpg', '.jpeg', '.png', '.mp4')))
    if not files:
        print('❌ هیچ فایلی دانلود نشد')
        sys.exit(1)

    return files, shortcode, source_username


# ─────────────────────────────────────────
# ارسال پیش‌نمایش با دکمه‌ی شیشه‌ای از طریق Bot API
# (بات، نه اکانت شخصی — فرمت دکمه‌ها دقیقاً با n8n هماهنگه:
#  tg:<shortcode>:<idx> برای toggle هر اسلاید، cf:<shortcode> برای تایید،
#  rj:<shortcode> برای رد)
# ─────────────────────────────────────────
def tg_bot_call(method: str, data: dict, files=None):
    if not TG_BOT_TOKEN:
        sys.exit('❌ TG_BOT_TOKEN تنظیم نشده (لازم برای پیش‌نمایش با دکمه)')
    url = f'https://api.telegram.org/bot{TG_BOT_TOKEN}/{method}'
    resp = requests.post(url, data=data, files=files, timeout=60)
    result = resp.json()
    if not result.get('ok'):
        raise RuntimeError(f'خطای Telegram Bot API در {method}: {result}')
    return result['result']


def send_preview(files, shortcode, chat_id):
    total = len(files)

    def send_media(filepath, caption, keyboard):
        is_video = filepath.lower().endswith('.mp4')
        method = 'sendVideo' if is_video else 'sendPhoto'
        field = 'video' if is_video else 'photo'
        with open(filepath, 'rb') as f:
            tg_bot_call(method, {
                'chat_id': chat_id,
                'caption': caption,
                'reply_markup': json.dumps(keyboard),
            }, files={field: f})

    if total == 1:
        keyboard = {'inline_keyboard': [[
            {'text': '✅ تایید', 'callback_data': f'cf:{shortcode}'},
            {'text': '❌ رد', 'callback_data': f'rj:{shortcode}'},
        ]]}
        send_media(files[0], f'shortcode: {shortcode}', keyboard)
        return

    for idx, filepath in enumerate(files, start=1):
        keyboard = {'inline_keyboard': [[
            {'text': f'☐ اسلاید {idx}', 'callback_data': f'tg:{shortcode}:{idx}'}
        ]]}
        send_media(filepath, f'اسلاید {idx}/{total} — {shortcode}', keyboard)

    confirm_keyboard = {'inline_keyboard': [[
        {'text': '✅ تایید و ارسال', 'callback_data': f'cf:{shortcode}'},
        {'text': '❌ رد', 'callback_data': f'rj:{shortcode}'},
    ]]}
    tg_bot_call('sendMessage', {
        'chat_id': chat_id,
        'text': f'کروسل ({total} اسلاید) — {shortcode}\n\nاسلایدهای موردنظر رو تیک بزن، بعد «تایید و ارسال» رو بزن.',
        'reply_markup': json.dumps(confirm_keyboard),
    })


# ─────────────────────────────────────────
# ارسال به تلگرام (تک‌فایل، برای هر آیتم جدا صدا زده می‌شه)
# ─────────────────────────────────────────
async def send_items_to_telegram(items, chat_id):
    client = TelegramClient(StringSession(TG_SESSION_STRING), int(TG_API_ID), TG_API_HASH)
    await client.connect()

    chat_id_value = int(chat_id) if str(chat_id).lstrip('-').isdigit() else chat_id

    sent_messages = []
    try:
        for item in items:
            attributes = [DocumentAttributeFilename(os.path.basename(item['filepath']))]

            sent = await client.send_file(
                chat_id_value,
                item['filepath'],
                caption=item['caption'],
                parse_mode='html',
                supports_streaming=True,
                attributes=attributes,
            )
            sent_messages.append(sent)
    finally:
        await client.disconnect()

    return sent_messages


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--chat-id', required=True)
    parser.add_argument('--ig-session', default='', help='مسیر فایل سشن instaloader (اختیاری — نبود آن یعنی anonymous)')
    parser.add_argument('--slides', default='', help='شماره اسلایدهای انتخابی، مثلا 2,4 (فقط mode=send)')
    parser.add_argument('--mode', default='send', choices=['preview', 'send'])
    args = parser.parse_args()

    url = args.url.strip()
    chat_id = args.chat_id.strip()

    print(f'🌐 حالت: {args.mode}')

    try:
        files, shortcode, source_username = download_instagram(url, args.ig_session)
        print(f'📦 {len(files)} فایل دانلود شد (shortcode={shortcode}).')

        if args.mode == 'preview':
            print(f'📨 ارسال پیش‌نمایش با دکمه به chat_id={chat_id} (از طریق بات)...')
            send_preview(files, shortcode, chat_id)
            print('✅ پیش‌نمایش ارسال شد.')
            return

        # mode == send
        if not (TG_API_ID and TG_API_HASH and TG_SESSION_STRING):
            print('❌ TG_API_ID / TG_API_HASH / TG_SESSION_STRING تنظیم نشده (باید GitHub Secrets باشن)')
            sys.exit(1)

        slides_filter = args.slides.strip()
        if slides_filter:
            selected_indices = [int(x) for x in slides_filter.split(',') if x.strip().isdigit()]
        else:
            selected_indices = list(range(1, len(files) + 1))

        caption = build_caption_instagram(source_username)
        items = [
            {'filepath': f, 'caption': caption}
            for idx, f in enumerate(files, start=1)
            if idx in selected_indices
        ]
        if not items:
            print('❌ بعد از فیلتر اسلایدها چیزی برای ارسال نموند')
            sys.exit(1)

        for item in items:
            size_mb = os.path.getsize(item['filepath']) / 1024 / 1024
            print(f"✅ آماده ارسال: {item['filepath']} ({size_mb:.1f} MB)")

        print(f'📨 در حال ارسال نهایی به تلگرام (chat_id={chat_id}, تعداد آیتم={len(items)})...')
        try:
            sent = asyncio.run(send_items_to_telegram(items, chat_id))
            ids = ', '.join(str(s.id) for s in sent)
            print(f'✅ ارسال شد. message_id(s)={ids}')
        except FloodWaitError as e:
            print(f'⏳ FloodWait: باید {e.seconds} ثانیه صبر کرد')
            sys.exit(2)
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)


if __name__ == '__main__':
    main()
