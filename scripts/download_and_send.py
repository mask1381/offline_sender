#!/usr/bin/env python3
"""
اسکریپت یک‌باره (نه سرور دائمی) که توسط GitHub Actions اجرا می‌شه.
ورودی: لینک ویدیو + chat_id تلگرام (از طریق آرگومان یا Environment Variable)
خروجی: دانلود با yt-dlp، ساخت کپشن، ارسال با Telethon (userbot، بدون محدودیت 50MB بات).

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

WORKDIR = tempfile.mkdtemp(prefix="job_")
COOKIES_ARG = ''  # مقداردهی می‌شه از --cookies تو main()

# ─────────────────────────────────────────
# منطق کپشن (همون چیزی که قبلاً تو n8n Code node بود)
# ─────────────────────────────────────────
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


def build_caption(info: dict, normalized_url: str) -> dict:
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
# yt-dlp
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
    if deno_path:
        opts['extractor_args'] = {'youtube': {'js_runtimes': [f'deno:{deno_path}']}}

    cookie_file = os.environ.get('YTDLP_COOKIES_FILE', '') or COOKIES_ARG
    if cookie_file and os.path.exists(cookie_file):
        opts['cookiefile'] = cookie_file

    return opts


# ─────────────────────────────────────────
# ارسال به تلگرام
# ─────────────────────────────────────────
async def send_to_telegram(filepath, chat_id, caption_data, thumb_path=None):
    client = TelegramClient(StringSession(TG_SESSION_STRING), int(TG_API_ID), TG_API_HASH)
    await client.connect()

    chat_id_value = int(chat_id) if str(chat_id).lstrip('-').isdigit() else chat_id

    attributes = []
    if caption_data['is_audio_only']:
        attributes.append(DocumentAttributeAudio(
            duration=int(caption_data['duration'] or 0),
            title=caption_data['audio_title'] or None,
            performer=caption_data['performer'] or None,
        ))
        display_filename = f"{caption_data['audio_title']}.m4a"
    else:
        display_filename = os.path.basename(filepath)
    attributes.append(DocumentAttributeFilename(display_filename))

    try:
        sent = await client.send_file(
            chat_id_value,
            filepath,
            caption=caption_data['caption'],
            parse_mode='html',
            supports_streaming=True,
            attributes=attributes,
            thumb=thumb_path,
        )
        return sent
    finally:
        await client.disconnect()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--chat-id', required=True)
    parser.add_argument('--cookies', default='', help='مسیر فایل cookies.txt (اختیاری)')
    args = parser.parse_args()

    global COOKIES_ARG
    COOKIES_ARG = args.cookies

    if not (TG_API_ID and TG_API_HASH and TG_SESSION_STRING):
        print('❌ TG_API_ID / TG_API_HASH / TG_SESSION_STRING تنظیم نشده (باید GitHub Secrets باشن)')
        sys.exit(1)

    url = args.url.strip()
    chat_id = args.chat_id.strip()

    print(f'🔎 در حال گرفتن اطلاعات: {url}')
    probe_opts = get_ydl_opts()
    probe_opts['skip_download'] = True
    with yt_dlp.YoutubeDL(probe_opts) as ydl_probe:
        probe_info = ydl_probe.extract_info(url, download=False)

    audio_only = is_audio_only_content(probe_info.get('title', ''))
    caption_data = build_caption(probe_info, url)

    print(f'⬇️  در حال دانلود ({"صوتی" if audio_only else "ویدیو"})...')
    opts = get_ydl_opts(audio_only=audio_only)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)

    files = [f for f in glob.glob(os.path.join(WORKDIR, '*')) if os.path.isfile(f)]
    if not files:
        print('❌ هیچ فایلی دانلود نشد')
        sys.exit(1)
    filepath = files[0]
    print(f'✅ دانلود شد: {filepath} ({os.path.getsize(filepath) / 1024 / 1024:.1f} MB)')

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

    print(f'📨 در حال ارسال به تلگرام (chat_id={chat_id})...')
    try:
        sent = asyncio.run(send_to_telegram(filepath, chat_id, caption_data, thumb_path))
        print(f'✅ ارسال شد. message_id={sent.id}')
    except FloodWaitError as e:
        print(f'⏳ FloodWait: باید {e.seconds} ثانیه صبر کرد')
        sys.exit(2)
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)


if __name__ == '__main__':
    main()
