#!/usr/bin/env python3
"""
اسکریپت یک‌باره (نه سرور دائمی) که توسط GitHub Actions اجرا می‌شه.
پشتیبانی از دو پلتفرم:
  - یوتیوب (و هرچی yt-dlp پشتیبانی کنه) → همون منطق قبلی
  - اینستاگرام (پست تکی/ویدیو/کروسل چند اسلایدی) → با instaloader

دو حالت اجرا (--mode):
  - preview : همه‌ی اسلایدهای پست رو با شماره به یه چت پیش‌نمایش می‌فرسته (برای تایید دستی)
  - send    : فقط اسلایدهای انتخابی (--slides) رو با کپشن نهایی به کانال اصلی می‌فرسته

بعد از اجرا، این اسکریپت و کل VM موقتِ GitHub از بین می‌ره - هیچ سروری باقی نمی‌مونه.
"""

import argparse
import asyncio
import glob
import os
import re
import shutil
import sys
import tempfile
from urllib.parse import urlparse

import requests
import yt_dlp
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeFilename

# ─────────────────────────────────────────
# تنظیمات از Environment Variables (GitHub Secrets)
# ─────────────────────────────────────────
TG_API_ID = os.environ.get("TG_API_ID")
TG_API_HASH = os.environ.get("TG_API_HASH")
TG_SESSION_STRING = os.environ.get("TG_SESSION_STRING")
IG_USERNAME = os.environ.get("IG_USERNAME")

WORKDIR = tempfile.mkdtemp(prefix="job_")
COOKIES_ARG = ''  # مقداردهی می‌شه از --cookies تو main()

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


def detect_platform(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if 'instagram.com' in host:
        return 'instagram'
    return 'generic'  # youtube و هر چیز دیگه‌ای که yt-dlp پشتیبانی کنه


# ─────────────────────────────────────────
# منطق کپشن یوتیوب (بدون تغییر)
# ─────────────────────────────────────────
def is_audio_only_content(title: str) -> bool:
    if not title:
        return False
    pattern = r'OST|Vol\.?\s*\d+|Disc\s*\d+|Soundtrack|Original Soundtrack|Original Score'
    return bool(re.search(pattern, title, re.IGNORECASE))


def clean_title(text: str) -> str:
    text = re.sub(r'#[^\s#]+', '', text, flags=re.UNICODE)
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\|\s*$', '', text)
    return text.strip()


def clean_audio_title(text: str) -> str:
    text = re.sub(r'#[^\s#]+', '', text, flags=re.UNICODE)
    text = re.sub(r'\|\s*Genshin Impact\s*$', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\|\s*$', '', text)
    return text.strip()


def build_caption_generic(info: dict, normalized_url: str) -> dict:
    title = info.get('title', '') or ''
    audio_only = is_audio_only_content(title)

    is_short = bool(re.search(r'#shorts?\b', title, re.IGNORECASE)) or '/shorts/' in normalized_url
    is_trailer = bool(re.search(r'trailer|teaser|\bpv\b', title, re.IGNORECASE))

    if audio_only:
        content_type, emoji = 'TEYVAT SOUNDSCAPE', '🎵'
    elif is_trailer:
        content_type, emoji = 'TEYVAT TEASER', '🎥'
    elif is_short:
        content_type, emoji = 'TEYVAT MOMENT', '📱'
    else:
        content_type, emoji = 'TEYVAT CHRONICLE', '🎬'

    hashtags = re.findall(r'#[^\s#]+', title, flags=re.UNICODE)
    hashtags = [h for h in hashtags if not re.match(r'^#shorts?$', h, re.IGNORECASE)]
    hashtag_line = ' '.join(hashtags)

    cleaned_title = clean_title(title)
    audio_title = clean_audio_title(title)

    caption = f"{emoji} <b>{bi(content_type)}</b>\n\n"
    caption += f"<blockquote><b>{bi(cleaned_title)}</b></blockquote>\n\n"
    if audio_only:
        caption += f"{bi('🎧 Audio Release')}\n\n"
    if hashtag_line:
        caption += f"{hashtag_line}\n\n"
    caption += f'🌸 <a href="https://t.me/genshinworldsensei">{bi("GWS | Teyvat Archive")}</a>'

    return {
        'caption': caption,
        'is_audio_only': audio_only,
        'audio_title': audio_title,
        'performer': info.get('uploader') or 'Genshin Impact',
        'duration': info.get('duration') or 0,
        'thumbnail': info.get('thumbnail') or '',
    }


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
# دانلود یوتیوب/عمومی با yt-dlp (بدون تغییر ساختاری)
# ─────────────────────────────────────────
def find_deno():
    return shutil.which('deno')


def get_ydl_opts(audio_only=False):
    opts = {
        'quiet': True,
        'no_warnings': True,
        'outtmpl': os.path.join(WORKDIR, '%(autonumber)s.%(ext)s'),
        'windowsfilenames': True,
        'noplaylist': True,
        'retries': 15,
        'fragment_retries': 15,
        'socket_timeout': 30,
    }
    if audio_only:
        opts['format'] = 'bestaudio/best'
    else:
        opts['format'] = 'bv*+ba/b'
        opts['merge_output_format'] = 'mp4'

    deno_path = find_deno()
    extractor_args = {'youtube': {'player_client': ['web', 'tv', 'android']}}
    if deno_path:
        extractor_args['youtube']['js_runtimes'] = [f'deno:{deno_path}']
    opts['extractor_args'] = extractor_args

    cookie_file = os.environ.get('YTDLP_COOKIES_FILE', '') or COOKIES_ARG
    if cookie_file and os.path.exists(cookie_file):
        opts['cookiefile'] = cookie_file

    return opts


def download_generic(url: str):
    print(f'🔎 در حال گرفتن اطلاعات: {url}')
    probe_opts = get_ydl_opts()
    probe_opts['skip_download'] = True
    with yt_dlp.YoutubeDL(probe_opts) as ydl_probe:
        probe_info = ydl_probe.extract_info(url, download=False)

    audio_only = is_audio_only_content(probe_info.get('title', ''))
    caption_data = build_caption_generic(probe_info, url)

    print(f'⬇️  در حال دانلود ({"صوتی" if audio_only else "ویدیو"})...')
    opts = get_ydl_opts(audio_only=audio_only)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        if 'Requested format is not available' in str(e):
            print('⚠️  فرمت اصلی موجود نبود، تلاش دوباره با فرمت ساده‌تر ("best")...')
            opts['format'] = 'best'
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
        else:
            raise

    files = sorted(f for f in glob.glob(os.path.join(WORKDIR, '*')) if os.path.isfile(f))
    if not files:
        print('❌ هیچ فایلی دانلود نشد')
        sys.exit(1)

    thumb_path = None
    if caption_data['thumbnail']:
        try:
            resp = requests.get(caption_data['thumbnail'], timeout=15)
            if resp.status_code == 200:
                thumb_path = os.path.join(WORKDIR, 'thumb.jpg')
                with open(thumb_path, 'wb') as f:
                    f.write(resp.content)
        except Exception:
            thumb_path = None

    # هر آیتم: (filepath, caption برای همین آیتم، آیا صوتیه)
    items = [{
        'filepath': files[0],
        'caption': caption_data['caption'],
        'is_audio_only': caption_data['is_audio_only'],
        'audio_title': caption_data['audio_title'],
        'performer': caption_data['performer'],
        'duration': caption_data['duration'],
        'thumb_path': thumb_path,
    }]
    return items


# ─────────────────────────────────────────
# دانلود اینستاگرام با instaloader (پشتیبانی از کروسل چند اسلایدی)
# ─────────────────────────────────────────
def download_instagram(url: str, ig_session_path: str, slides_filter, mode: str):
    import instaloader

    L = instaloader.Instaloader(
        dirname_pattern=WORKDIR,
        filename_pattern='{shortcode}_{mediaid}',
        download_video_thumbnails=False,
        save_metadata=False,
        post_metadata_txt_pattern='',
        quiet=True,
    )

    if IG_USERNAME and ig_session_path and os.path.exists(ig_session_path):
        try:
            L.load_session_from_file(IG_USERNAME, ig_session_path)
        except Exception as e:
            print(f'⚠️  لود سشن اینستا شکست خورد ({e})، بدون لاگین ادامه می‌دیم (ریسک rate-limit بیشتر)')

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

    total = len(files)

    if mode == 'preview':
        # تو preview فیلتری اعمال نمی‌کنیم، همه‌ی اسلایدها با شماره می‌رن
        selected_indices = list(range(1, total + 1))
    else:
        if slides_filter:
            selected_indices = [int(x) for x in slides_filter.split(',') if x.strip().isdigit()]
        else:
            selected_indices = list(range(1, total + 1))

    items = []
    for idx, filepath in enumerate(files, start=1):
        if idx not in selected_indices:
            continue
        if mode == 'preview':
            caption = f'اسلاید {idx}/{total} — shortcode: {shortcode}'
        else:
            caption = build_caption_instagram(source_username)
        items.append({
            'filepath': filepath,
            'caption': caption,
            'is_audio_only': False,
            'audio_title': None,
            'performer': None,
            'duration': 0,
            'thumb_path': None,
        })

    if not items:
        print('❌ بعد از فیلتر اسلایدها چیزی برای ارسال نموند')
        sys.exit(1)

    return items


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
            attributes = []
            if item['is_audio_only']:
                attributes.append(DocumentAttributeAudio(
                    duration=int(item['duration'] or 0),
                    title=item['audio_title'] or None,
                    performer=item['performer'] or None,
                ))
                display_filename = f"{item['audio_title']}.m4a"
            else:
                display_filename = os.path.basename(item['filepath'])
            attributes.append(DocumentAttributeFilename(display_filename))

            sent = await client.send_file(
                chat_id_value,
                item['filepath'],
                caption=item['caption'],
                parse_mode='html',
                supports_streaming=True,
                attributes=attributes,
                thumb=item['thumb_path'],
            )
            sent_messages.append(sent)
    finally:
        await client.disconnect()

    return sent_messages


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--chat-id', required=True)
    parser.add_argument('--cookies', default='', help='مسیر فایل cookies.txt (اختیاری، برای یوتیوب)')
    parser.add_argument('--ig-session', default='', help='مسیر فایل سشن instaloader (اختیاری، برای اینستا)')
    parser.add_argument('--slides', default='', help='شماره اسلایدهای انتخابی، مثلا 2,4 (فقط اینستا + mode=send)')
    parser.add_argument('--mode', default='send', choices=['preview', 'send'])
    args = parser.parse_args()

    global COOKIES_ARG
    COOKIES_ARG = args.cookies

    if not (TG_API_ID and TG_API_HASH and TG_SESSION_STRING):
        print('❌ TG_API_ID / TG_API_HASH / TG_SESSION_STRING تنظیم نشده (باید GitHub Secrets باشن)')
        sys.exit(1)

    url = args.url.strip()
    chat_id = args.chat_id.strip()
    platform = detect_platform(url)

    print(f'🌐 پلتفرم تشخیص داده شد: {platform} | حالت: {args.mode}')

    if platform == 'instagram':
        items = download_instagram(url, args.ig_session, args.slides.strip(), args.mode)
    else:
        items = download_generic(url)

    for item in items:
        size_mb = os.path.getsize(item['filepath']) / 1024 / 1024
        print(f"✅ آماده ارسال: {item['filepath']} ({size_mb:.1f} MB)")

    print(f'📨 در حال ارسال به تلگرام (chat_id={chat_id}, تعداد آیتم={len(items)})...')
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
