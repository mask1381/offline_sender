#!/usr/bin/env python3
"""
اسکریپت یک‌باره (نه سرور دائمی) که توسط GitHub Actions اجرا می‌شه.
فقط اینستاگرام (پست تکی/ویدیو/کروسل چند اسلایدی) → با instaloader

دو حالت اجرا (--mode):
  - preview : همه‌ی اسلایدهای پست رو با شماره به یه چت پیش‌نمایش می‌فرسته (برای تایید دستی)
              و برای هر اسلاید یه ردیف با selected=true تو n8n Data Table (از طریق
              وبهوک REGISTER_SLIDE_WEBHOOK_URL) ثبت می‌کنه -> یعنی پیش‌فرض «همه انتخابن»
              و کاربر با تیک زدن، اسلاید غیرلازم رو از حالت انتخاب خارج می‌کنه.
  - send    : فقط اسلایدهای انتخابی (--slides) رو با کپشن واقعیِ خودِ پست (فرمت‌شده)
              به‌صورت آلبوم (media group) به کانال اصلی می‌فرسته.

نکته‌ی مهم درباره‌ی --slides خالی: دیگه به‌معنی «همه» نیست! اگه خالی برسه یعنی
هیچ اسلایدی برای ارسال انتخاب نشده -> اسکریپت خطا می‌ده و از ارسال ناخواسته‌ی
همه‌ی اسلایدها جلوگیری می‌کنه.

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

# توکن بات (فقط برای پیش‌نمایش با دکمه — ارسال نهایی همچنان با اکانت شخصی/Telethon است)
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")

# آدرس وبهوک n8n که با هر اسلاید ارسالی، یه ردیف selected=true تو Data Table
# ثبت می‌کنه (تا پیش‌فرض «همه انتخاب‌شده» باشه، نه «هیچ‌کدوم»)
REGISTER_SLIDE_WEBHOOK_URL = os.environ.get("REGISTER_SLIDE_WEBHOOK_URL", "").strip()

# پراکسی رزیدنشیال برای عبور از بلاک IP دیتاسنتری گیت‌هاب
# فرمت: http://user:pass@host:port   یا   socks5://user:pass@host:port
IG_PROXY_URL = os.environ.get("IG_PROXY_URL", "").strip()

# کدام مرورگر واقعی برای جعل TLS fingerprint شبیه‌سازی بشه (curl_cffi)
CURL_CFFI_IMPERSONATE = os.environ.get("CURL_CFFI_IMPERSONATE", "chrome124").strip()

# درصدی از پایین هر عکس که کراپ بشه (نوار سورس/سواپ اینستاگرام معمولاً همونجاست)
CROP_BOTTOM_PERCENT = float(os.environ.get("CROP_BOTTOM_PERCENT", "0.10"))

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
# کپشن واقعیِ خودِ پست اینستا رو می‌گیره و داخل قالب برند خودمون می‌ذاره
# ─────────────────────────────────────────
MAX_TG_CAPTION_LEN = 1024  # محدودیت خود تلگرام برای caption عکس/ویدیو/آلبوم


def _caption_has_meaningful_text(caption: str) -> bool:
    """Heuristic: remove URLs/mentions/hashtags and count remaining words."""
    if not caption:
        return False
    text = re.sub(r'https?://\S+', '', caption)
    text = re.sub(r'[@#]\S+', '', text)
    # Keep words with at least one alphabet/digit (unicode aware simplified)
    words = re.findall(r"[\w\u0600-\u06FF]+", text)  # include Persian/Arabic letters
    return len(words) >= 5


def clean_original_caption(caption: str) -> str:
    """
    Remove boilerplate/CTA blocks from an Instagram post caption.

    Rules:
    - Split caption into blocks by double-newline (\n\n).
    - Remove any block that starts (case-insensitive) with one of known prefixes
      such as "✦ Cover Art", "✦ Hashtags", "↠ Leaks and News", "↠ Like & Follow".
    - Rejoin remaining blocks with \n\n and return.
    - If caption is falsy, return empty string.
    """
    if not caption:
        return ""

    blocks = [blk.strip() for blk in caption.split("\n\n")]

    remove_prefixes = [
        "✦ Cover Art",
        "✦ Hashtags",
        "↠ Leaks and News",
        "↠ Like & Follow",
        "↠ Leaks",
        "↠ Like",
    ]

    cleaned_blocks = []
    for blk in blocks:
        if not blk:
            continue
        lower_blk = blk.lower()
        should_remove = False
        for pref in remove_prefixes:
            if lower_blk.startswith(pref.lower()):
                should_remove = True
                break
        if not should_remove:
            cleaned_blocks.append(blk)

    return "\n\n".join(cleaned_blocks).strip()


def build_caption_instagram(source_username: str, original_caption: str = '') -> str:
    header = f"⚡️ <b>{bi('TEYVAT LEAK')}</b>\n\n"

    original_caption = (original_caption or '').strip()

    body = ''
    # Clean the original caption from boilerplate blocks before deciding
    cleaned_caption = clean_original_caption(original_caption)

    # اگر کپشن معنادار باشه، داخل blockquote قرار می‌دیم
    if cleaned_caption and _caption_has_meaningful_text(cleaned_caption):
        # محاسبه بودجه باقیمانده برای جلوگیری از overflow
        footer_stub = '\n\n#leak\n🌸 <a href="https://t.me/genshinworldsensei">GWS | Teyvat Archive</a>'
        budget = MAX_TG_CAPTION_LEN - len(header) - len(footer_stub) - len('<blockquote></blockquote>') - 10
        if budget < 20:
            budget = 20
        oc = cleaned_caption
        if len(oc) > budget:
            oc = oc[:budget].rstrip() + '…'
        body = f'<blockquote>{oc}</blockquote>\n\n'

    # ساختار نهایی: header + optional body + #leak + GWS link
    footer = '#leak\n🌸 <a href="https://t.me/genshinworldsensei">GWS | Teyvat Archive</a>'

    return header + body + footer


# ────────────────────────────���────────────
# جایگزینی requests.Session داخلی instaloader با یه session از curl_cffi
# که رفتار TLS/HTTP2 مرورگر واقعی رو جعل می‌کنه (fingerprint spoofing)
# + وصل کردن پراکسی رزیدنشیال (اگه تنظیم شده باشه)
# ─────────────────────────────────────────
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
# کراپ کردن پایین عکس (نوار سورس/swipe اینستاگرام معمولاً همونجاست)
# ─────────────────────────────────────────
def crop_bottom(filepath: str, percent: float = CROP_BOTTOM_PERCENT) -> str:
    if percent <= 0 or not filepath.lower().endswith(('.jpg', '.jpeg', '.png')):
        return filepath
    try:
        img = Image.open(filepath)
        w, h = img.size
        new_h = max(1, int(h * (1 - percent)))
        img.crop((0, 0, w, new_h)).save(filepath)
    except Exception as e:
        print(f'⚠️ کراپ عکس {filepath} شکست خورد: {e}')
    return filepath


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
    try:
        original_caption = post.caption or ''
    except Exception:
        original_caption = ''

    L.download_post(post, target=WORKDIR)

    files = sorted(f for f in glob.glob(os.path.join(WORKDIR, '*'))
                    if os.path.isfile(f) and f.lower().endswith(('.jpg', '.jpeg', '.png', '.mp4')))
    if not files:
        print('❌ هیچ فایلی دانلود نشد')
        sys.exit(1)

    # نکته: اینجا دیگه کراپ نمی‌شه! کراپ حالا یه تصمیمِ دستیه که کاربر موقع
    # preview با دکمه‌ی toggle مشخص می‌کنه (نه یه درصد ثابت روی همه‌چیز).
    # فقط تو main()، وقتی mode=send و --crop true باشه، همینجا (crop_bottom)
    # روی فایل‌های نهاییِ انتخاب‌شده اجرا می‌شه.

    return files, shortcode, source_username, original_caption


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


def register_slide(shortcode, slide_index, chat_id, message_id, selected, username=''):
    """
    به‌محض ارسال هر پیام اسلاید، بلافاصله یه ردیف تو n8n Data Table ثبت
    می‌کنه (از طریق وبهوک register-slide).

    مدل جدید opt-in است: برای پست‌های چندعکسی، هر اسلاید با selected=False
    ثبت می‌شه (یعنی پیش‌فرض هیچ‌کدوم انتخاب نشده) و کاربر با تپ کردن روی
    دکمه‌ی همون اسلاید در تلگرام آن را انتخاب می‌کند (تپ = انتخاب، نه حذفِ
    انتخاب). برای پست تک‌عکسی چون گزینه‌ای برای انتخاب وجود ندارد، همان
    یک اسلاید با selected=True ثبت می‌شود تا با زدن «تایید» مستقیم ارسال شود.

    اگر REGISTER_SLIDE_WEBHOOK_URL تنظیم نشده باشد یا وبهوک n8n در دسترس
    نباشد، فقط هشدار چاپ می‌شود و ادامه می‌دهد.
    """
    if not REGISTER_SLIDE_WEBHOOK_URL:
        print('⚠️ REGISTER_SLIDE_WEBHOOK_URL تنظیم نشده — ثبت اسلاید انجام نشد')
        return
    try:
        requests.post(REGISTER_SLIDE_WEBHOOK_URL, json={
            'shortcode': shortcode,
            'slide_index': str(slide_index),
            'chat_id': str(chat_id),
            'message_id': str(message_id),
            'selected': selected,
            'username': username or '',
        }, timeout=15)
    except Exception as e:
        print(f'⚠️ ثبت اسلاید {slide_index} تو دیتا تیبل شکست خورد: {e}')


def send_preview(files, shortcode, chat_id, username=''):
    """
    نکته درباره‌ی پاک‌سازی پیام‌ها: این تابع خودش هیچ پیامی رو پاک نمی‌کنه.
    تک‌تک اسلایدها (و پیام کنترلی تایید/رد) از طریق register_slide() با
    chat_id + message_id تو n8n Data Table ثبت می‌شن. پاک‌سازی واقعی
    (Telegram deleteMessage برای همه‌ی این پیام‌ها) بعد از Confirm/Reject
    تو خودِ ورک‌فلوی n8n انجام می‌شه (رو ردیف‌های ثبت‌شده‌ی همین shortcode)،
    نه این‌جا.
    """
    total = len(files)

    def send_media(filepath, caption, keyboard):
        is_video = filepath.lower().endswith('.mp4')
        method = 'sendVideo' if is_video else 'sendPhoto'
        field = 'video' if is_video else 'photo'
        with open(filepath, 'rb') as f:
            result = tg_bot_call(method, {
                'chat_id': chat_id,
                'caption': caption,
                'reply_markup': json.dumps(keyboard),
            }, files={field: f})
        return result['message_id']

    if total == 1:
        keyboard = {'inline_keyboard': [[
            {'text': '✅ تایید', 'callback_data': f'cf:{shortcode}'},
            {'text': '❌ رد', 'callback_data': f'rj:{shortcode}'},
        ]]}
        mid = send_media(files[0], f'shortcode: {shortcode}', keyboard)
        # تک‌عکسی به‌عنوان اسلاید ۱ و از قبل «انتخاب‌شده» ثبت می‌شه، چون
        # اینجا چیزی برای انتخاب/رد وجود نداره - فقط تایید یا رد کل پست
        register_slide(shortcode, 1, chat_id, mid, selected=True, username=username)
        return

    for idx, filepath in enumerate(files, start=1):
        keyboard = {'inline_keyboard': [[
            {'text': f'☐ اسلاید {idx}', 'callback_data': f'tg:{shortcode}:{idx}'}
        ]]}
        mid = send_media(filepath, f'اسلاید {idx}/{total} — {shortcode}', keyboard)
        # opt-in: پیش‌فرض انتخاب نشده؛ کاربر با تپ کردن آن را انتخاب می‌کند
        register_slide(shortcode, idx, chat_id, mid, selected=False)

    confirm_keyboard = {'inline_keyboard': [[
        {'text': '✅ تایید و ارسال', 'callback_data': f'cf:{shortcode}'},
        {'text': '❌ رد', 'callback_data': f'rj:{shortcode}'},
    ]]}
    ctrl = tg_bot_call('sendMessage', {
        'chat_id': chat_id,
        'text': (
            f'کروسل ({total} اسلاید) — {shortcode}\n\n'
            'به‌صورت پیش‌فرض هیچ اسلایدی انتخاب نشده. '
            'روی اسلایدهایی که می‌خوای ارسال بشه بزن تا ✅ بشن، بعد «تایید و ارسال» رو بزن.'
        ),
        'reply_markup': json.dumps(confirm_keyboard),
    })
    # پیام کنترلی هم به‌عنوان یه ردیف مخصوص ثبت می‌شه (slide_index="_control")
    # تا موقع پاک‌سازی نهایی، این پیام هم حذف بشه
    register_slide(shortcode, '_control', chat_id, ctrl['message_id'], selected=False)


# ─────────────────────────────────────────
# ارسال به تلگرام (تک‌فایل، برای هر آیتم جدا صدا زده می‌شه)
# ─────────────────────────────────────────
async def send_items_to_telegram(items, chat_id):
    client = TelegramClient(StringSession(TG_SESSION_STRING), int(TG_API_ID), TG_API_HASH)
    await client.connect()

    chat_id_value = int(chat_id) if str(chat_id).lstrip('-').isdigit() else chat_id

    sent_messages = []
    try:
        # اگر همه فایل‌ها عکس/ویدیو هستن و تعداد بین 2 تا 10 هست، ارسال در یک آلبوم
        filepaths = [item['filepath'] for item in items]
        def is_media(fp):
            return fp.lower().endswith(('.jpg', '.jpeg', '.png', '.mp4'))

        if len(filepaths) > 1 and all(is_media(p) for p in filepaths) and len(filepaths) <= 10:
            # caption فقط برای فایل اول اعمال می‌شه (محدودیت تلگرام)
            caption = items[0].get('caption') if items else None
            sent = await client.send_file(
                chat_id_value,
                filepaths,
                caption=caption,
                parse_mode='html',
                supports_streaming=True,
            )
            # Telethon ممکنه یه لیست پیام برگردونه یا یه پیام منفرد
            if isinstance(sent, list):
                sent_messages.extend(sent)
            else:
                sent_messages.append(sent)
        else:
            # fallback: ارسال تک‌تک (برای فایل‌های غیر مدیا یا وقتی بیش از 10 فایل باشه)
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
        files, shortcode, source_username, original_caption = download_instagram(url, args.ig_session)
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
        if args.mode == 'send':
            if slides_filter:
                selected_indices = [int(x) for x in slides_filter.split(",") if x.strip().isdigit()]
            else:
                selected_indices = []

            if not selected_indices:
                print("❌ هیچ اسلایدی انتخاب نشده - از ارسال خودداری شد", file=sys.stderr)
                sys.exit(1)
        else:
            selected_indices = list(range(1, len(files) + 1))

        caption = build_caption_instagram(source_username, original_caption)
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
