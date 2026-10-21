#!/usr/bin/env python3
"""
نسخه سبک — اینستاگرام + ردیت (پست تکی/ویدیو/کروسل چند اسلایدی/گالری)

بدون preview، بدون دکمه تایید/رد، بدون کراپ، بدون register-slide.
لینک میاد (تشخیص خودکار اینستاگرام یا ردیت از روی دامنه) → فایل‌ها دانلود
می‌شن → با کپشن *اصلی و دست‌نخورده‌ی* پست به‌صورت آلبوم (اگه چندتا بود)
یا تکی به chat_id فرستاده می‌شن.

بعد از اجرا، این اسکریپت و کل VM موقتِ GitHub از بین می‌ره - هیچ سروری باقی نمی‌مونه.
این یعنی هر اجرا از یه IP تازه‌ست (برخلاف n8n که IP ثابت داره و توسط
ردیت/اینستاگرام راحت بلاک می‌شه) و همینجا هم fingerprint مرورگر واقعی جعل
می‌شه (curl_cffi) که شانس رد شدن از بلاک‌ها رو بیشتر می‌کنه.

اگه --ig-session (و secret به اسم IG_USERNAME) داده بشه، سمت اینستاگرام با
اکانت واقعی لاگین می‌کنه (محدودیت نرخ درخواست خیلی بهتری نسبت به anonymous
داره)؛ نبود سشن یا نامعتبر/منقضی بودنش، بی‌سروصدا به anonymous فال‌بک می‌کنه.
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

from PIL import Image
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

# پراکسی رزیدنشیال برای عبور از بلاک IP دیتاسنتری گیت‌هاب (اختیاری - هم برای اینستاگرام هم ردیت)
IG_PROXY_URL = os.environ.get("IG_PROXY_URL", "").strip()

# کدام مرورگر واقعی برای جعل TLS fingerprint شبیه‌سازی بشه (curl_cffi)
CURL_CFFI_IMPERSONATE = os.environ.get("CURL_CFFI_IMPERSONATE", "chrome124").strip()

WORKDIR = tempfile.mkdtemp(prefix="job_")

MAX_TG_CAPTION_LEN = 1024  # محدودیت خود تلگرام برای caption عکس/ویدیو/آلبوم


def build_caption(original_caption: str) -> str:
    """کپشن دقیقاً همون چیزیه که تو پست هست — هیچ تغییری روش اعمال نمی‌شه،
    فقط اگه از سقف تلگرام بلندتر باشه کوتاه می‌شه تا ارسال fail نشه."""
    caption = (original_caption or '').strip()
    if len(caption) > MAX_TG_CAPTION_LEN:
        caption = caption[:MAX_TG_CAPTION_LEN - 1].rstrip() + '…'
    return caption


# ─────────────────────────────────────────
# سشن سخت‌شده‌ی مشترک (curl_cffi) - هم برای instaloader هم برای ردیت
# TLS/HTTP2 fingerprint مرورگر واقعی رو جعل می‌کنه + پراکسی رزیدنشیال
# ─────────────────────────────────────────
def build_hardened_cffi_session():
    from curl_cffi import requests as cffi_requests

    proxies = None
    if IG_PROXY_URL:
        proxies = {'http': IG_PROXY_URL, 'https': IG_PROXY_URL}

    session = cffi_requests.Session(
        impersonate=CURL_CFFI_IMPERSONATE or None,
        proxies=proxies,
        timeout=300,
    )
    return session


class _CookieIterAdapter:
    """
    curl_cffi's cookies object پیمایش رو مثل دیکشنری انجام می‌ده (فقط اسم
    کوکی‌ها رو برمی‌گردونه)، اما instaloader انتظار داره هر آیتم پیمایش‌شده
    یه شیء با .name/.value باشه (مثل http.cookiejar.Cookie / requests).
    این adapter فقط رفتار پیمایش رو اصلاح می‌کنه؛ همه‌چیز دیگه (get/set/
    update و مدیریت واقعی کوکی توسط curl_cffi) دست‌نخورده به شیء اصلی
    delegate می‌شه.
    """

    def __init__(self, cookies):
        self._cookies = cookies

    def __iter__(self):
        try:
            items = dict(self._cookies).items()
        except Exception:
            items = []
        for name, value in items:
            yield type('_Cookie', (), {'name': name, 'value': value})()

    def __getattr__(self, name):
        return getattr(self._cookies, name)

    def __contains__(self, key):
        return key in self._cookies

    def __len__(self):
        return len(self._cookies)


def harden_instaloader_session(L):
    """جعل TLS/HTTP2 fingerprint مرورگر واقعی (curl_cffi) روی سشن instaloader —
    چه لاگین‌شده باشه چه anonymous. کوکی‌های سشن (از جمله کوکی‌های لاگین اگه
    load_session_from_file قبلش صدا زده شده باشه) به سشن جدید منتقل می‌شن."""
    if not IG_PROXY_URL and not CURL_CFFI_IMPERSONATE:
        return

    try:
        new_session = build_hardened_cffi_session()
    except ImportError:
        print('⚠️ curl_cffi نصب نیست — بدون جعل TLS fingerprint ادامه می‌دیم (pip install curl_cffi)')
        return

    old_session = L.context._session

    try:
        new_session.cookies.update(dict(old_session.cookies))
    except Exception:
        pass
    try:
        new_session.headers.update(dict(old_session.headers))
    except Exception:
        pass

    # فقط رفتار پیمایش کوکی‌ها رو سازگار می‌کنیم (باگ instaloader/curl_cffi)
    # نکته‌ی مهم: نباید new_session.cookies = ... کرد، چون curl_cffi روش
    # یه property-setter داره که هر مقداری بدی رو دوباره به نوع native خودش
    # (Cookies) تبدیل می‌کنه و adapter ما رو silently دور می‌ندازه. باید
    # مستقیم attribute داخلی (_cookies) رو ست کنیم تا setter دور زده بشه.
    new_session._cookies = _CookieIterAdapter(new_session.cookies)

    L.context._session = new_session

    proxy_note = 'با پراکسی رزیدنشیال' if IG_PROXY_URL else 'بدون پراکسی'
    print(f'🛡️ session با curl_cffi (impersonate={CURL_CFFI_IMPERSONATE!r}, {proxy_note}) جایگزین شد')


# ─────────────────────────────────────────
# دانلود مستقیم از لینک‌های CDN که Apify از قبل استخراج کرده
# (image_versions2 / video_versions / carousel_media)
#
# نیازی به instaloader، لاگین، یا هیچ درخواست اضافه‌ای به اینستاگرام نیست:
# این لینک‌ها از قبل public و امضاشده‌ن (همون‌هایی که مرورگر خودت هم موقع
# باز کردن پست می‌گیره) — فقط یه GET ساده کافیه.
# ─────────────────────────────────────────
def download_media_direct(media_items: list):
    """
    media_items: لیستی از دیکشنری‌های {"url": "...", "type": "photo"|"video"}
    (دقیقاً چیزی که نود n8n از خروجی Apify استخراج و به‌عنوان media_json می‌فرسته)
    """
    session = build_hardened_cffi_session()
    files = []

    for idx, item in enumerate(media_items):
        url = item.get('url', '').strip()
        media_type = (item.get('type') or '').strip().lower()
        if not url:
            continue

        ext = '.mp4' if media_type == 'video' else _guess_ext_from_url(url, default='.jpg')
        dest = os.path.join(WORKDIR, f'ig_{idx}{ext}')
        try:
            _download_binary(session, url, dest)
            files.append(dest)
        except Exception as e:
            print(f'⚠️ دانلود آیتم {idx} شکست خورد ({url[:80]}...): {e}')

    if not files:
        print('❌ هیچ فایلی دانلود نشد (media_json خالی بود یا همه‌ی لینک‌ها fail شدن)')
        sys.exit(1)

    files = [
        convert_webp_to_jpg(f) if f.lower().endswith('.webp') else f
        for f in files
    ]
    return files


# ─────────────────────────────────────────
# راه‌اندازی instaloader (اگه --ig-session و IG_USERNAME داده بشه، با اکانت
# واقعی لاگین می‌کنه؛ در غیر این صورت — یا اگه سشن نامعتبر/منقضی بود —
# عمداً و بی‌سروصدا به anonymous فال‌بک می‌کنه)
# ─────────────────────────────────────────
def build_instaloader_instance(ig_session_path: str = ''):
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

    if not ig_session_path:
        print('ℹ️ بدون سشن اجرا می‌شه (anonymous) — فقط پست‌های پابلیک قابل دانلودن')
        harden_instaloader_session(L)
        return L

    if not os.path.exists(ig_session_path):
        print(f'⚠️ فایل سشن پیدا نشد ({ig_session_path!r}) — ادامه به‌صورت anonymous')
        harden_instaloader_session(L)
        return L

    if not IG_USERNAME:
        print('⚠️ IG_USERNAME تنظیم نشده — نمی‌شه سشن رو لود کرد، ادامه به‌صورت anonymous')
        harden_instaloader_session(L)
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

    harden_instaloader_session(L)
    return L


# ─────────────────────────────────────────
# تبدیل webp → jpg
# اینستاگرام این روزها عکس‌ها رو با فرمت webp سرو می‌کنه، ولی sendPhoto/
# send_file تلگرام فایل‌های webp رو به‌عنوان استیکر/فایل می‌گیره نه عکس
# واقعی — پس قبل از هر استفاده‌ای باید تبدیل بشن.
# ─────────────────────────────────────────
def convert_webp_to_jpg(webp_path: str) -> str:
    jpg_path = os.path.splitext(webp_path)[0] + '.jpg'
    try:
        with Image.open(webp_path) as img:
            if img.mode in ('RGBA', 'P', 'LA'):
                img = img.convert('RGB')
            img.save(jpg_path, 'JPEG', quality=95)
        os.remove(webp_path)
        return jpg_path
    except Exception as e:
        print(f'⚠️ تبدیل webp→jpg شکست خورد ({webp_path}): {e}')
        return webp_path


# ─────────────────────────────────────────
# دانلود اینستاگرام با instaloader (پشتیبانی از کروسل چند اسلایدی)
# ─────────────────────────────────────────
def download_instagram(url: str, ig_session_path: str = ''):
    import instaloader

    L = build_instaloader_instance(ig_session_path)

    shortcode_match = re.search(r'/(?:p|reel|tv)/([^/?#]+)', url)
    if not shortcode_match:
        print('❌ نتونستم shortcode رو از لینک استخراج کنم')
        sys.exit(1)
    shortcode = shortcode_match.group(1)

    post = instaloader.Post.from_shortcode(L.context, shortcode)
    try:
        original_caption = post.caption or ''
    except Exception:
        original_caption = ''

    try:
        L.download_post(post, target=WORKDIR)
    except Exception as e:
        print(f'❌ download_post شکست خورد با این خطا: {type(e).__name__}: {e}')
        sys.exit(1)

    raw_files = sorted(f for f in glob.glob(os.path.join(WORKDIR, '**', '*'), recursive=True)
                        if os.path.isfile(f) and f.lower().endswith(
                            ('.jpg', '.jpeg', '.png', '.mp4', '.webp')))
    if not raw_files:
        print(f'❌ هیچ فایلی دانلود نشد (محتویات {WORKDIR}: {os.listdir(WORKDIR)})')
        sys.exit(1)

    files = [
        convert_webp_to_jpg(f) if f.lower().endswith('.webp') else f
        for f in raw_files
    ]

    return files, original_caption


# ─────────────────────────────────────────
# دانلود ردیت
# با همون سشن سخت‌شده (curl_cffi، fingerprint مرورگر واقعی) که برای
# اینستاگرام هم استفاده می‌شه. ریدایرکت لینک‌های Share (/r/xxx/s/yyy)
# رو هم همینجا دنبال می‌کنیم چون از IP این ران‌ر (نه n8n) درخواست می‌زنیم.
# ─────────────────────────────────────────
REDDIT_BROWSER_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/html;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}


def _download_binary(session, url: str, dest_path: str):
    resp = session.get(url, headers=REDDIT_BROWSER_HEADERS)
    resp.raise_for_status()
    with open(dest_path, 'wb') as f:
        f.write(resp.content)
    return dest_path


def _guess_ext_from_url(url: str, default: str = '.jpg') -> str:
    path = url.split('?')[0]
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.jpg', '.jpeg', '.png', '.gif', '.mp4', '.webp'):
        return ext
    return default


def download_reddit(url: str):
    session = build_hardened_cffi_session()

    # مرحله ۱: ریدایرکت لینک‌های Share (/r/xxx/s/yyy) رو دنبال کن تا به
    # URL نهاییِ /comments/... برسیم. اگه از اول comments/ داشت، این
    # درخواست فقط همون URL رو با / تمیز برمی‌گردونه.
    resp = session.get(url, headers=REDDIT_BROWSER_HEADERS, allow_redirects=True)
    resp.raise_for_status()
    final_url = str(resp.url).split('?')[0].rstrip('/') + '/'

    # مرحله ۲: خود دیتای JSON پست رو بگیر
    json_resp = session.get(final_url + '.json', headers=REDDIT_BROWSER_HEADERS)
    json_resp.raise_for_status()
    data = json_resp.json()

    try:
        post = data[0]['data']['children'][0]['data']
    except Exception:
        print('❌ ساختار JSON پست ردیت غیرمنتظره بود')
        sys.exit(1)

    title = post.get('title') or ''
    selftext = post.get('selftext') or ''
    caption = f'{title}\n\n{selftext}' if selftext else title

    media_urls = []
    if post.get('is_video') and post.get('media', {}).get('reddit_video', {}).get('fallback_url'):
        media_urls = [post['media']['reddit_video']['fallback_url']]
        # نکته: fallback_url ویدیوی ردیت معمولاً صدا نداره (صدا جدا/DASH پخش
        # می‌شه). اگه صدا لازم شد باید ffmpeg + دانلود جدای audio track اضافه بشه.
    elif post.get('is_gallery') and post.get('media_metadata'):
        for meta in post['media_metadata'].values():
            src = (meta or {}).get('s', {}).get('u') or (meta or {}).get('s', {}).get('gif')
            if src:
                media_urls.append(src.replace('&amp;', '&'))
    elif post.get('url_overridden_by_dest') and re.search(
            r'\.(jpe?g|png|gif)$', post['url_overridden_by_dest'], re.I):
        media_urls = [post['url_overridden_by_dest']]
    elif post.get('preview', {}).get('images'):
        src = post['preview']['images'][0].get('source', {}).get('url')
        if src:
            media_urls = [src.replace('&amp;', '&')]

    if not media_urls:
        print('❌ هیچ رسانه‌ای توی این پست ردیت پیدا نشد (شاید فقط متن/لینکه)')
        sys.exit(1)

    files = []
    for i, media_url in enumerate(media_urls):
        ext = _guess_ext_from_url(media_url, default='.mp4' if post.get('is_video') else '.jpg')
        dest = os.path.join(WORKDIR, f'reddit_{i}{ext}')
        try:
            _download_binary(session, media_url, dest)
            files.append(dest)
        except Exception as e:
            print(f'⚠️ دانلود یه آیتم شکست خورد ({media_url}): {e}')

    if not files:
        print('❌ هیچ فایلی دانلود نشد')
        sys.exit(1)

    files = [
        convert_webp_to_jpg(f) if f.lower().endswith('.webp') else f
        for f in files
    ]

    return files, caption


# ─────────────────────────────────────────
# ارسال به تلگرام با Telethon (اکانت شخصی)
# ─────────────────────────────────────────
async def send_items_to_telegram(files, caption, chat_id):
    client = TelegramClient(StringSession(TG_SESSION_STRING), int(TG_API_ID), TG_API_HASH)
    await client.connect()

    chat_id_value = int(chat_id) if str(chat_id).lstrip('-').isdigit() else chat_id

    ALBUM_MAX = 10          # سقف آلبوم تلگرام
    PAUSE_BETWEEN = 4       # ثانیه مکث بین ارسال‌ها

    def is_media(fp):
        return fp.lower().endswith(('.jpg', '.jpeg', '.png', '.mp4'))

    def split_balanced(items, max_size):
        """۱۵ → ۸+۷ ، ۲۳ → ۸+8+7 ؛ دسته‌ی آخر تک‌افتاده نمی‌شه."""
        n = len(items)
        parts = -(-n // max_size)          # ceil
        base, extra = divmod(n, parts)
        out, start = [], 0
        for i in range(parts):
            size = base + (1 if i < extra else 0)
            out.append(items[start:start + size])
            start += size
        return out

    async def send_with_retry(**kwargs):
        # اگه FloodWait اومد، همون مدت صبر می‌کنیم و یک بار دوباره تلاش می‌کنیم
        try:
            return await client.send_file(chat_id_value, **kwargs)
        except FloodWaitError as e:
            print(f'⏳ FloodWait: {e.seconds} ثانیه صبر و تلاش دوباره...')
            await asyncio.sleep(e.seconds + 1)
            return await client.send_file(chat_id_value, **kwargs)

    sent_messages = []
    try:
        if all(is_media(p) for p in files):
            # تک‌فایل هم همین مسیر رو میره (یک دسته‌ی یک‌تایی)
            batches = split_balanced(files, ALBUM_MAX)
            for i, batch in enumerate(batches):
                if i > 0:
                    await asyncio.sleep(PAUSE_BETWEEN)
                sent = await send_with_retry(
                    file=batch if len(batch) > 1 else batch[0],
                    caption=caption,           # کپشن روی هر آلبوم
                    parse_mode='html',
                    supports_streaming=True,
                )
                if isinstance(sent, list):
                    sent_messages.extend(sent)
                else:
                    sent_messages.append(sent)
                print(f'📨 آلبوم {i + 1}/{len(batches)} ارسال شد ({len(batch)} فایل)')
        else:
            # fallback: فایل غیرمدیا (مثل gif) → تک‌تک
            for i, f in enumerate(files):
                if i > 0:
                    await asyncio.sleep(PAUSE_BETWEEN)
                sent = await send_with_retry(
                    file=f,
                    caption=caption,
                    parse_mode='html',
                    supports_streaming=True,
                    attributes=[DocumentAttributeFilename(os.path.basename(f))],
                )
                sent_messages.append(sent)
    finally:
        await client.disconnect()

    return sent_messages


def detect_source(url: str) -> str:
    if re.search(r'instagram\.com', url, re.I):
        return 'instagram'
    if re.search(r'reddit\.com|redd\.it', url, re.I):
        return 'reddit'
    return 'unknown'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=False, default='',
                         help='لینک پست (فقط برای ردیت لازمه؛ برای اینستاگرام از --media-json استفاده کن)')
    parser.add_argument('--chat-id', required=True)
    parser.add_argument('--media-json', required=False, default='',
                         help='(روش اصلی اینستاگرام) JSON آرایه‌ای از '
                              '{"url":"...","type":"photo|video"} که از خروجی Apify میاد — '
                              'لینک‌های CDN از قبل public و امضاشده‌ن، نیازی به لاگین/instaloader نیست.')
    parser.add_argument('--caption', required=False, default='',
                         help='کپشن اصلی پست (وقتی از --media-json استفاده می‌شه، کپشن رو Apify داده و اینجا پاس داده می‌شه)')
    parser.add_argument('--source', required=False, default='',
                         help='instagram | reddit (اختیاری - اگه ندی از روی خود URL تشخیص داده می‌شه)')
    parser.add_argument('--ig-session', default='',
                         help='[فقط fallback قدیمی instaloader] مسیر فایل سشن — برای مسیر اصلی (--media-json) لازم نیست')
    args = parser.parse_args()

    chat_id = args.chat_id.strip()
    media_json = args.media_json.strip()

    if not (TG_API_ID and TG_API_HASH and TG_SESSION_STRING):
        print('❌ TG_API_ID / TG_API_HASH / TG_SESSION_STRING تنظیم نشده (باید GitHub Secrets باشن)')
        sys.exit(1)

    try:
        # ── مسیر اصلی: لینک‌های CDN مستقیم از Apify (بدون لاگین/instaloader) ──
        if media_json:
            try:
                media_items = json.loads(media_json)
            except json.JSONDecodeError as e:
                print(f'❌ media-json نامعتبره: {e}')
                sys.exit(1)
            files = download_media_direct(media_items)
            original_caption = args.caption
            source = 'instagram (direct CDN)'

        # ── مسیر قدیمی: فقط برای ردیت (یا اینستاگرام از طریق instaloader اگه صراحتاً بخوای) ──
        else:
            url = args.url.strip()
            if not url:
                print('❌ یا --media-json بده یا --url (هیچ‌کدوم داده نشد)')
                sys.exit(1)
            source = args.source.strip().lower() or detect_source(url)
            if source not in ('instagram', 'reddit'):
                print(f'❌ نوع لینک ناشناخته‌ست (source={source!r}, url={url!r})')
                sys.exit(1)
            if source == 'instagram':
                files, original_caption = download_instagram(url, args.ig_session)
            else:
                files, original_caption = download_reddit(url)

        print(f'📦 {len(files)} فایل دانلود شد (source={source}).')

        caption = build_caption(original_caption)

        for f in files:
            size_mb = os.path.getsize(f) / 1024 / 1024
            print(f"✅ آماده ارسال: {f} ({size_mb:.1f} MB)")

        print(f'📨 در حال ارسال به تلگرام (chat_id={chat_id}, تعداد آیتم={len(files)})...')
        try:
            sent = asyncio.run(send_items_to_telegram(files, caption, chat_id))
            ids = ', '.join(str(s.id) for s in sent)
            print(f'✅ ارسال شد. message_id(s)={ids}')
        except FloodWaitError as e:
            print(f'⏳ FloodWait: باید {e.seconds} ثانیه صبر کرد')
            sys.exit(2)
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)


if __name__ == '__main__':
    main()
