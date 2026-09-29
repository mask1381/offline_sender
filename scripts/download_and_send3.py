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
import html
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

from PIL import Image
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo
from telethon.extensions import html as tl_html
try:
    from telethon.tl.types import MessageEntityBlockquote
except ImportError:  # Telethon خیلی قدیمی
    MessageEntityBlockquote = None

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


def _visible_len(html_text: str) -> int:
    """طول واقعی کپشن بعد از پارس HTML (بدون تگ‌ها، با UTF-16 چون تلگرام همین‌طور می‌شماره)."""
    plain = html.unescape(re.sub(r'<[^>]+>', '', html_text))
    return len(plain.encode('utf-16-le')) // 2


def build_caption(original_caption: str) -> str:
    """کپشن از n8n به‌صورت HTML آماده می‌رسه (Build Final Caption) و دست‌نخورده می‌ره.
    سقف ۱۰۲۴ روی طول *قابل‌نمایش* حساب می‌شه نه رشته‌ی خام (تگ‌ها شمرده نمی‌شن).
    فقط اگه واقعاً از سقف بیشتر بود، تگ‌ها حذف و متن ساده کوتاه می‌شه تا HTML نصفه نره."""
    caption = (original_caption or '').strip()
    if _visible_len(caption) <= MAX_TG_CAPTION_LEN:
        return caption
    plain = html.unescape(re.sub(r'<[^>]+>', '', caption))
    plain = plain[:MAX_TG_CAPTION_LEN - 1].rstrip() + '…'
    return html.escape(plain, quote=False)


# ─────────────────────────────────────────
# حالت کانال (--caption-mode channel)
#
# نوع پست (بدون فهرست کلمه‌ی کلیدی برای ویدیوها):
#   ۱) قواعد متنی TEXT_RULES (تولد، لیک، بنر، ایونت، خبر) — ترتیب مهمه
#   ۲) ویدیوی بدون قاعده‌ی متنی: زیر ۱ دقیقه CLIP | ۱ دقیقه یا بیشتر: DEMO (اگه به شخصیت
#      اشاره داشته باشه) وگرنه TRAILER
#   ۳) پست عکسی بدون قاعده: طبق UNMATCHED_NON_VIDEO (پیش‌فرض ارسال نمی‌شه)
# برای دیدن نوع پست‌های واقعی بدون ارسال:  --caption-mode channel --test-file items.json
#
# قالب کپشن (بر پایه‌ی نود «Build Final Caption» فلوی بررسی/ارسال تلگرام):
#   {ایموجی} <b>نوع bold-italic</b>
#   ← <b>عنوان bold-italic</b>   (فقط اگه خط اول کوتاه بود و بعدش متن دیگه‌ای هست)
#   ← <blockquote expandable>بقیه‌ی متن، جمع‌شونده</blockquote>
#   ← #نوع + هشتگ‌های خود پست ← فوتر HUB/ART
# هشتگ‌ها: فقط هشتگ نوع (#Clip / #Demo / #Trailer) ساخته می‌شه؛ بقیه دقیقاً همونایی‌ان
# که خود کپشن اینستاگرام داره (از متن برداشته می‌شن و می‌رن توی خط هشتگ).
# متن فقط وقتی کوتاه می‌شه که از سقف ۱۰۲۴ تلگرام رد بشه؛ بدون اون، همه‌ی متن توی blockquote می‌ره.
# ─────────────────────────────────────────
CLIP_MAX_SECONDS = 60
TITLE_MAX_LEN = 100  # خط اول تا این طول می‌تونه عنوان باشه
SHOW_SOURCE = False  # لینک 🔗 Source حذف شد (True کنی برمی‌گرده)

# ===== TAXONOMY — تنها جای تعریف دسته‌ها (همون نود Build Final Caption) =====
TAXONOMY = {
    'LEAK':          {'tag': 'Leak',          'label': 'LEAK',           'emoji': '⚡️'},
    'NEWS':          {'tag': 'News',          'label': 'NEWS',           'emoji': '📢'},
    'OFFICIAL':      {'tag': 'Official',      'label': 'OFFICIAL',       'emoji': '🏢'},
    'BANNER':        {'tag': 'Banner',        'label': 'BANNER',         'emoji': '🎴'},
    'EVENT':         {'tag': 'Event',         'label': 'EVENT',          'emoji': '🎉'},
    'EVENTSPREVIEW': {'tag': 'EventsPreview', 'label': 'EVENTS PREVIEW', 'emoji': '📅'},
    'CODE':          {'tag': 'Code',          'label': 'REDEEM CODE',    'emoji': '🔑'},
    'BIRTHDAY':      {'tag': 'Birthday',      'label': 'BIRTHDAY',       'emoji': '🎂'},
    'GUIDE':         {'tag': 'Guide',         'label': 'GUIDE',          'emoji': '📖'},
    'ART':           {'tag': 'Art',           'label': 'ART',            'emoji': '🎨'},
    'TRAILER':       {'tag': 'Trailer',       'label': 'TRAILER',        'emoji': '🎬'},
    'DEMO':          {'tag': 'Demo',          'label': 'CHARACTER DEMO', 'emoji': '🎥'},
    'CLIP':          {'tag': 'Clip',          'label': 'CLIP',           'emoji': '📱'},
    'STREAM':        {'tag': 'Stream',        'label': 'STREAM',         'emoji': '📡'},
    'LIVE':          {'tag': 'Live',          'label': 'LIVE',           'emoji': '🔴'},
}

# ===== قواعد متنی — تنها جای تعریف «چی چیه»؛ برای نوع/قاعده‌ی جدید فقط یه ردیف اضافه کن =====
# ترتیب مهمه: اولین قاعده‌ای که بخوره برنده‌ست.
#   kind     : کلید TAXONOMY
#   video    : True = روی پست‌های ویدیویی هم اعمال بشه؛ False = فقط عکس/آلبوم
#              (ویدیوی بدون قاعده‌ی متنی می‌ره سراغ قاعده‌ی مدت: کلیپ/دمو/تریلر)
#   patterns : لیست (scope, regex) — 'title' = خط اول کپشن (بدون هشتگ)، 'text' = کل کپشن
# الگوهای BANNER/EVENT از نود «کانال اخبار گنشین» گرفته شدن؛ LEAK/NEWS پیشنهادیه — ویرایش کن.
TEXT_RULES = [
    {'kind': 'BIRTHDAY', 'video': True,
     'patterns': [('text', r'birthday')]},
    {'kind': 'LEAK', 'video': False,
     'patterns': [('text', r'\bleak(ed|s)?\b|datamine[rd]?|unconfirmed|reportedly')]},
    {'kind': 'BANNER', 'video': False,
     'patterns': [('title', r'banner|\bwish\b|\bwarp\b|\bconvene\b|character preview|character skill showcase|resonator convene|weapon convene|epitomized path'),
                  ('text', r'\bbanners?\b|\bconvenes?\b')]},
    {'kind': 'EVENT', 'video': False,
     'patterns': [('text', r'event duration|\bduration\s*:|\bbundle\b|pop-?up|\bmerch\b|\btour\b|sales?\s*hours?|story event'),
                  ('title', r'\bevents?\b')]},
    {'kind': 'NEWS', 'video': False,
     'patterns': [('title', r'announcement|maintenance|patch notes|pre-install|pre install|compensation|'
                            r'update details|version details|known issues|phase\s*(ii|2)|kickoff|update available')]},
]

# پست عکسی/آلبومی که هیچ قاعده‌ای نخوره: None = ارسال نشه | 'NEWS' = به‌عنوان خبر بره
UNMATCHED_NON_VIDEO = 'NEWS'
# هشتگ‌هایی که اسم بازی/شرکت یا خود نوع محتوا هستن و «اشاره به شخصیت» حساب نمی‌شن.
# (کوچک‌حرف و بدون فاصله/زیرخط نوشته بشن)
GENERIC_TAGS = {
    'genshin', 'genshinimpact', 'gi', 'wutheringwaves', 'wuwa', 'ww',
    'zenlesszonezero', 'zzz', 'honkaistarrail', 'hsr', 'starrail',
    'honkaiimpact3rd', 'honkaiimpact', 'honkai', 'hi3', 'hi3rd',
    'hoyoverse', 'mihoyo', 'kurogames', 'game', 'games', 'gaming', 'anime',
    'trailer', 'demo', 'clip', 'characterdemo', 'charactertrailer',
    'characterteaser', 'teaser', 'newcharacter', 'fyp', 'viral', 'reels', 'reel',
}

# ─────────────────────────────────────────
# پروفایل پیج (scripts/page_profiles.py) — قواعد اختصاصی هر پیج
# profile=None یا {} یعنی همین قواعد سراسری بالا، بدون هیچ تغییری.
# ─────────────────────────────────────────
try:
    from page_profiles import PROFILES
except ImportError:  # فایل نبود → فقط پروفایل پیش‌فرض
    PROFILES = {'default': {}}


def resolve_profile(name):
    """اسم پروفایل → dict. خالی/None/'default' → {}. اسم ناشناخته → KeyError (تا پیج اشتباهی با قواعد غلط نره)."""
    name = (name or '').strip()
    if not name or name == 'default':
        return dict(PROFILES.get('default', {}))
    if name not in PROFILES:
        raise KeyError(f'پروفایل ناشناخته: {name!r} (تعریف‌شده‌ها: {sorted(PROFILES)})')
    return dict(PROFILES[name])


def drop_range(n: int, profile: dict = None):
    """(start, end) اسلایدهایی که می‌مونن. drop_first/drop_last از پروفایل؛ فقط وقتی اعمال می‌شه که
    تعداد آیتم‌ها >= drop_min_items باشه (پیش‌فرض: first+last+1 → همیشه حداقل یه آیتم می‌مونه)."""
    profile = profile or {}
    first = max(0, int(profile.get('drop_first', 0) or 0))
    last = max(0, int(profile.get('drop_last', 0) or 0))
    if not (first or last):
        return 0, n
    min_items = int(profile.get('drop_min_items', first + last + 1))
    if n < max(min_items, first + last + 1):
        return 0, n
    return first, n - last


def apply_media_drops(media_items: list, profile: dict = None) -> list:
    """حذف اسلایدهای تبلیغاتی ثابت (اول/آخر) قبل از دانلود."""
    start, end = drop_range(len(media_items), profile)
    if (start, end) == (0, len(media_items)):
        return media_items
    print(f'✂️ حذف خودکار اسلاید: {start} از اول، {len(media_items) - end} از آخر '
          f'({len(media_items)} → {end - start} آیتم)')
    return media_items[start:end]


def crop_bottom_pct_files(files: list, pct) -> list:
    """pct٪ پایین عکس‌ها (jpg/jpeg/png) رو می‌بُره (درجا). ویدیو دست‌نخورده می‌مونه."""
    try:
        pct = float(pct or 0)
    except (TypeError, ValueError):
        pct = 0
    if pct <= 0:
        return files
    pct = min(pct, 40.0)  # سقف اطمینان
    for f in files:
        if not f.lower().endswith(('.jpg', '.jpeg', '.png')):
            continue
        try:
            with Image.open(f) as im:
                w, h = im.size
                new_h = max(1, int(round(h * (1 - pct / 100.0))))
                cropped = im.crop((0, 0, w, new_h))
            if f.lower().endswith('.png'):
                cropped.save(f, 'PNG')
            else:
                if cropped.mode not in ('RGB', 'L'):
                    cropped = cropped.convert('RGB')
                cropped.save(f, 'JPEG', quality=95)
        except Exception as e:
            print(f'⚠️ کراپ {os.path.basename(f)} نشد: {e}')
    print(f'✂️ {pct:g}٪ پایین عکس‌ها کراپ شد')
    return files


def compute_schedule_dt(profile: dict = None):
    """زمان انتشار برای schedule تلگرام (UTC، aware) یا None = فوری. کلید: schedule_delay_min."""
    mins = (profile or {}).get('schedule_delay_min')
    if not mins:
        return None
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) + timedelta(minutes=float(mins))


def strip_caption_blocks(caption: str, patterns) -> str:
    """پاراگراف‌هایی (جداشده با خط خالی) از کپشن که با هر کدوم از regexهای patterns بخورن حذف می‌شن."""
    if not patterns or not caption:
        return caption
    regs = [re.compile(p, re.I | re.M) for p in patterns]
    kept = [para for para in re.split(r'\n[ \t]*\n', caption)
            if not any(r.search(para) for r in regs)]
    return '\n\n'.join(kept)


BOLD_ITALIC_MAP = {
    'a': '𝒂', 'b': '𝒃', 'c': '𝒄', 'd': '𝒅', 'e': '𝒆', 'f': '𝒇', 'g': '𝒈', 'h': '𝒉', 'i': '𝒊',
    'j': '𝒋', 'k': '𝒌', 'l': '𝒍', 'm': '𝒎', 'n': '𝒏', 'o': '𝒐', 'p': '𝒑', 'q': '𝒒', 'r': '𝒓',
    's': '𝒔', 't': '𝒕', 'u': '𝒖', 'v': '𝒗', 'w': '𝒘', 'x': '𝒙', 'y': '𝒚', 'z': '𝒛',
    'A': '𝑨', 'B': '𝑩', 'C': '𝑪', 'D': '𝑫', 'E': '𝑬', 'F': '𝑭', 'G': '𝑮', 'H': '𝑯', 'I': '𝑰',
    'J': '𝑱', 'K': '𝑲', 'L': '𝑳', 'M': '𝑴', 'N': '𝑵', 'O': '𝑶', 'P': '𝑷', 'Q': '𝑸', 'R': '𝑹',
    'S': '𝑺', 'T': '𝑻', 'U': '𝑼', 'V': '𝑽', 'W': '𝑾', 'X': '𝑿', 'Y': '𝒀', 'Z': '𝒁',
    # یونیکد رقم italic نداره؛ نزدیک‌ترین گزینه رقم bold ریاضیه (هم‌فونت با حروف)
    '0': '𝟎', '1': '𝟏', '2': '𝟐', '3': '𝟑', '4': '𝟒', '5': '𝟓', '6': '𝟔', '7': '𝟕', '8': '𝟖', '9': '𝟗',
    ' ': ' ', ':': ':', '-': '-',
}


def bi(text: str) -> str:
    return ''.join(BOLD_ITALIC_MAP.get(ch, ch) for ch in text)


HASHTAG_RE = re.compile(r'#\w+', re.UNICODE)

CHANNEL_FOOTER = """𝐓𝐇𝐄 𝐆𝐀𝐂𝐇𝐀𝐕𝐄𝐑𝐒𝐄  ›
│
├─ 📰 <a href="https://t.me/+Wf91O38uSB9kMzg8">𝐆𝐚𝐜𝐡𝐚 𝐍𝐞𝐰𝐬</a>
└─ 🎨 <a href="https://t.me/+TeQbrf_cIyljY2Zk">𝐀𝐫𝐭 𝐀𝐫𝐜𝐡𝐢𝐯𝐞</a>"""


def extract_hashtags(text: str) -> list:
    """هشتگ‌های خود کپشن، به ترتیب و بدون تکرار."""
    seen, out = set(), []
    for h in HASHTAG_RE.findall(text or ''):
        key = h.lower()
        if key not in seen:
            seen.add(key)
            out.append(h)
    return out


def strip_hashtags(text: str) -> str:
    text = HASHTAG_RE.sub('', text or '')
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def mentions_character(tags: list, profile: dict = None) -> bool:
    """اشاره به شخصیت = حداقل یه هشتگ که اسم بازی/شرکت/نوع محتوا نیست."""
    generic = GENERIC_TAGS | set((profile or {}).get('extra_generic_tags', ()))
    for t in tags:
        norm = re.sub(r'[\W_]+', '', t.lower())
        if norm and norm not in generic:
            return True
    return False


def classify(duration, tags: list, profile: dict = None):
    """None یعنی مدت ویدیو معلوم نیست."""
    if duration is None:
        return None
    if duration < (profile or {}).get('clip_max_seconds', CLIP_MAX_SECONDS):
        return 'CLIP'
    return 'DEMO' if mentions_character(tags, profile) else 'TRAILER'


def _first_line(caption: str) -> str:
    body = strip_hashtags(caption or '')
    return body.split('\n')[0].strip() if body else ''


def decide_type(media_items: list, caption: str, duration=None, profile: dict = None):
    """تصمیم نوع پست → (kind, why).
    kind: کلید TAXONOMY | 'PENDING' (ویدیو و مدتش هنوز معلوم نیست) | None (ارسال نشه)
    ترتیب: قواعد متنی (TEXT_RULES) ← اگه ویدیو بود قاعده‌ی مدت (کلیپ/دمو/تریلر) ← UNMATCHED_NON_VIDEO
    profile: dict پروفایل پیج (resolve_profile) — None/{} = قواعد سراسری بدون تغییر."""
    profile = profile or {}
    rules = list(profile.get('extra_rules', ())) + list(profile.get('text_rules', TEXT_RULES))
    unmatched = profile['unmatched_non_video'] if 'unmatched_non_video' in profile else UNMATCHED_NON_VIDEO
    has_video = any((i.get('type') or '').lower() == 'video' for i in media_items)
    title = _first_line(caption)
    for rule in rules:
        if has_video and not rule['video']:
            continue
        for scope, pattern in rule['patterns']:
            m = re.search(pattern, title if scope == 'title' else (caption or ''), re.I)
            if m:
                return rule['kind'], f"قاعده‌ی {rule['kind']} ({scope}) روی «{m.group(0)}»"
    if has_video:
        if profile.get('video_fallback'):
            return profile['video_fallback'], 'ویدیو — قاعده‌ای نخورد → video_fallback پروفایل'
        if duration is None:
            return 'PENDING', 'ویدیو — مدت هنوز معلوم نیست'
        tags = extract_hashtags(caption)
        return classify(duration, tags, profile), f'ویدیو {duration:.0f}s | شخصیت={mentions_character(tags, profile)}'
    if unmatched:
        return unmatched, 'هیچ قاعده‌ای نخورد → نوع پیش‌فرض'
    return None, 'پست بدون ویدیو و بدون قاعده‌ی متنی'


def run_test_file(path: str, profile: dict = None):
    """--test-file: لیست آیتم‌های n8n ({caption, mediaJson, shortcode}) → نوع و دلیل هر پست."""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = [data]
    for it in data:
        j = it.get('json', it)
        media = j.get('media') or json.loads(j.get('mediaJson') or '[]')
        cap = j.get('caption', '')
        kind, why = decide_type(media, cap, meta_duration(media), profile)
        print(f"{str(j.get('shortcode', '?')):<13} {str(kind):<9} {why} | «{_first_line(cap)[:50]}»")


def meta_duration(items: list):
    """بلندترین مدت ویدیو از فیلد duration که n8n (Apify videoDuration) داده."""
    vals = []
    for i in items:
        if (i.get('type') or '').lower() != 'video':
            continue
        try:
            v = float(i.get('duration'))
        except (TypeError, ValueError):
            continue
        if v > 0:
            vals.append(v)
    return max(vals) if vals else None


def mp4_info(path: str) -> dict:
    """مدت و ابعاد ویدیو از اتم‌های moov (mvhd + tkhd) — بدون ffprobe/hachoir.
    خروجی: {'duration': ثانیه یا None, 'w': int, 'h': int}"""
    info = {'duration': None, 'w': 0, 'h': 0, 'codecs': [], 'fragmented': False}

    def walk(f, end):
        while f.tell() < end:
            pos = f.tell()
            hdr = f.read(8)
            if len(hdr) < 8:
                return
            size, typ = struct.unpack('>I4s', hdr)
            hlen = 8
            if size == 1:
                size = struct.unpack('>Q', f.read(8))[0]
                hlen = 16
            elif size == 0:
                size = end - pos
            if size < hlen:
                return
            box_end = pos + size
            if typ in (b'moov', b'trak', b'mdia', b'minf', b'stbl'):
                walk(f, box_end)
            elif typ == b'moof':
                info['fragmented'] = True
            elif typ == b'stsd':
                f.read(8)  # version/flags + entry_count
                entry = f.read(8)
                if len(entry) == 8:
                    info['codecs'].append(entry[4:8].decode('latin-1'))
            elif typ == b'mvhd':
                ver = f.read(1)[0]
                f.read(3)
                if ver == 1:
                    f.read(16)
                    ts, dur = struct.unpack('>IQ', f.read(12))
                else:
                    f.read(8)
                    ts, dur = struct.unpack('>II', f.read(8))
                if ts and info['duration'] is None:
                    info['duration'] = dur / ts
            elif typ == b'tkhd':
                ver = f.read(1)[0]
                f.read(3)
                f.read(36 if ver == 0 else 48)
                matrix = struct.unpack('>9i', f.read(36))
                w, h = struct.unpack('>II', f.read(8))
                w, h = w >> 16, h >> 16
                if matrix[0] == 0 and matrix[4] == 0:  # چرخش ۹۰/۲۷۰ درجه
                    w, h = h, w
                if w and h and not info['w']:
                    info['w'], info['h'] = w, h
            f.seek(box_end)

    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            end = f.tell()
            f.seek(0)
            walk(f, end)
    except Exception:
        pass
    return info


def _pos_num(v):
    try:
        v = float(v)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def video_attributes(path: str, meta: dict = None, thumb_path: str = None):
    """DocumentAttributeVideo با مدت/ابعاد واقعی — بدون این، تلگرام ۰۰:۰۰ و جعبه‌ی سیاه نشون می‌ده.
    اولویت: مقدار n8n/Apify (duration, w, h) ← خود فایل mp4 ← نسبت ابعاد کاور.
    (فایل‌های اینستاگرام گاهی fragmented‌ن و مدت داخلشون صفره، برای همین متادیتای Apify مقدم‌تره.)"""
    meta = meta or {}
    info = mp4_info(path)
    duration = _pos_num(meta.get('duration')) or info['duration']
    w = int(_pos_num(meta.get('w')) or info['w'] or 0)
    h = int(_pos_num(meta.get('h')) or info['h'] or 0)
    if not (w and h) and thumb_path:
        try:
            w, h = Image.open(thumb_path).size
        except Exception:
            pass
    if not duration and not (w and h):
        return None
    return DocumentAttributeVideo(
        duration=int(round(duration or 0)),
        w=w or 1, h=h or 1, supports_streaming=True,
    )


def _save_thumb_jpeg(src: str, dest: str):
    im = Image.open(src).convert('RGB')
    im.thumbnail((320, 320))
    im.save(dest, 'JPEG', quality=85)


def prepare_thumb(media_items: list, files: list):
    """کاور ویدیو برای پیش‌نمایش: اول displayUrl اپیفای (فیلد thumb توی media_json)،
    اگه نبود اولین فریم با ffmpeg (فقط اگه نصب باشه). اگه هیچ‌کدوم نشد None."""
    out = os.path.join(WORKDIR, 'thumb.jpg')
    try:
        for it in media_items or []:
            if (it.get('type') or '').lower() == 'video' and (it.get('thumb') or '').strip():
                src = os.path.join(WORKDIR, 'thumb_src')
                _download_binary(build_hardened_cffi_session(), it['thumb'].strip(), src)
                _save_thumb_jpeg(src, out)
                return out
    except Exception as e:
        print(f'⚠️ دانلود کاور ویدیو نشد: {e}')
    vids = [f for f in files if f.lower().endswith('.mp4')]
    if vids and shutil.which('ffmpeg'):
        try:
            subprocess.run(
                ['ffmpeg', '-y', '-loglevel', 'error', '-ss', '0.5', '-i', vids[0],
                 '-frames:v', '1', '-vf', 'scale=320:-2', out],
                check=True, timeout=60, capture_output=True)
            if os.path.exists(out):
                return out
        except Exception as e:
            print(f'⚠️ ساخت کاور با ffmpeg نشد: {e}')
    return None


MAX_ALBUM_SIZE = 10  # سقف تلگرام برای هر آلبوم


def balanced_chunks(items: list, size: int = MAX_ALBUM_SIZE) -> list:
    """تقسیم ترتیب‌دار به کمترین تعداد تکه‌ی تقریباً هم‌اندازه (هیچ‌وقت تکه‌ی تک‌آیتمی نمی‌سازه).
    مثلاً ۱۱ → ۶+۵ ، ۲۰ → ۱۰+۱۰ ، ۲۳ → ۸+۸+۷ ، ۷ → ۷."""
    n = len(items)
    k = max(1, -(-n // size))
    base, rem = divmod(n, k)
    out, i = [], 0
    for j in range(k):
        step = base + (1 if j < rem else 0)
        out.append(items[i:i + step])
        i += step
    return out


def _u16len(s: str) -> int:
    return len(s.encode('utf-16-le')) // 2


def _truncate_paragraphs(text: str, max_len: int) -> str:
    if _u16len(text) <= max_len:
        return text
    result = ''
    for p in text.split('\n\n'):
        cand = f'{result}\n\n{p}' if result else p
        if _u16len(cand) > max_len:
            break
        result = cand
    if not result:
        cut = text
        while _u16len(cut) > max_len:
            cut = cut[:-1]
        sp = cut.rfind(' ')
        result = (cut[:sp] if sp > 0 else cut).rstrip() + '…'
    return result


def split_title(body: str):
    """خط اول = عنوان، به شرط اینکه کوتاه باشه و بعدش متن دیگه‌ای بیاد. وگرنه همه‌اش بدنه‌ست."""
    lines = body.split('\n')
    first = re.sub(r'\s{2,}', ' ', lines[0]).strip()
    rest = '\n'.join(lines[1:]).strip()
    if first and rest and len(first) <= TITLE_MAX_LEN:
        return first, rest
    return '', body


def build_channel_caption(kind: str, original_caption: str, tags: list, post_url: str = '', profile: dict = None) -> str:
    profile = profile or {}
    t = TAXONOMY[kind]
    link_part = f'🔗 <a href="{post_url}">{bi("Source")}</a>\n\n' if (SHOW_SOURCE and post_url) else ''

    original_caption = strip_caption_blocks(original_caption, profile.get('strip_blocks'))
    body = strip_hashtags(original_caption)
    if body and profile.get('split_title', True):
        title, body = split_title(body)
    else:
        title = ''
    # bi اول، escape بعد (وگرنه &amp; هم bold-italic می‌شد و خراب می‌شد)
    title_part = f'<b>{html.escape(bi(title), quote=False)}</b>\n\n' if title else ''

    type_tag = '#' + t['tag']
    extra = [h for h in tags if h.lower() != type_tag.lower()]
    # redundant_tags: {'genshin': 'genshinimpact'} → #genshin حذف می‌شه اگه #genshinimpact هم باشه
    present = {h.lstrip('#').lower() for h in extra}
    for redundant, covering in (profile.get('redundant_tags') or {}).items():
        if covering.lower() in present:
            extra = [h for h in extra if h.lstrip('#').lower() != redundant.lower()]

    def tag_line(ex):
        return ' '.join([type_tag] + ex) + '\n\n'

    def budget_for(ex):
        shell = title_part + link_part + tag_line(ex) + CHANNEL_FOOTER
        # +2 برای \n\n بعد از blockquote، +5 حاشیه‌ی اطمینان
        return MAX_TG_CAPTION_LEN - _visible_len(shell) - 2 - 5

    # اگه هشتگ‌ها اون‌قدر زیاد باشن که جایی برای متن نمونه، از آخر کم می‌کنیم
    while budget_for(extra) < 100 and extra:
        extra.pop()

    body = _truncate_paragraphs(body, max(50, budget_for(extra))) if body else ''

    caption = title_part
    if body:
        caption += f'<blockquote expandable>{html.escape(body, quote=False)}</blockquote>\n\n'
    caption += link_part + tag_line(extra) + CHANNEL_FOOTER
    return caption


class _ExpandableHTML:
    """parse_mode برای Telethon: مثل 'html'، ولی blockquote رو جمع‌شونده (expandable) می‌کنه.
    نیاز به Telethon جدید داره؛ روی نسخه‌ی قدیمی blockquote معمولی می‌مونه و خطا نمی‌ده."""

    _reported = False

    @staticmethod
    def _report():
        if _ExpandableHTML._reported:
            return
        _ExpandableHTML._reported = True
        try:
            import telethon
            ver = getattr(telethon, '__version__', '?')
        except Exception:
            ver = '?'
        ok = False
        if MessageEntityBlockquote is not None:
            try:
                ok = 'collapsed' in MessageEntityBlockquote.__init__.__code__.co_varnames
            except Exception:
                ok = False
        print(f"ℹ️ Telethon {ver} | blockquote جمع‌شونده: {'پشتیبانی می‌شه' if ok else 'پشتیبانی نمی‌شه (معمولی می‌مونه)'}")

    @staticmethod
    def parse(text):
        _ExpandableHTML._report()
        plain_html = text.replace('<blockquote expandable>', '<blockquote>')
        parsed_text, entities = tl_html.parse(plain_html)
        if MessageEntityBlockquote is not None:
            for e in entities:
                if isinstance(e, MessageEntityBlockquote):
                    try:
                        e.collapsed = True
                    except Exception:
                        pass
        return parsed_text, entities

    @staticmethod
    def unparse(text, entities):
        return tl_html.unparse(text, entities)


def pick_parse_mode(caption: str):
    return _ExpandableHTML if '<blockquote expandable>' in (caption or '') else 'html'


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
async def send_items_to_telegram(files, caption, chat_id, thumb=None, video_meta=None, schedule=None):
    client = TelegramClient(StringSession(TG_SESSION_STRING), int(TG_API_ID), TG_API_HASH)
    await client.connect()

    chat_id_value = int(chat_id) if str(chat_id).lstrip('-').isdigit() else chat_id
    # سشن تازه (StringSession) کش entity نداره؛ برای کانال با آی‌دی عددی (-100...)
    # اگه resolve نشد، یک‌بار دیالوگ‌ها رو می‌گیریم تا access_hash کانال ثبت بشه.
    try:
        chat_id_value = await client.get_input_entity(chat_id_value)
    except ValueError:
        await client.get_dialogs()
        chat_id_value = await client.get_input_entity(chat_id_value)

    sent_messages = []
    try:
        def is_media(fp):
            return fp.lower().endswith(('.jpg', '.jpeg', '.png', '.mp4'))

        if len(files) > 1 and all(is_media(p) for p in files):
            # آلبوم گروهی — کپشن فقط رو فایل اول هر آلبوم می‌شینه (محدودیت تلگرام).
            # بیش از ۱۰ فایل → چند آلبوم هم‌اندازه و ترتیب‌دار، هر کدوم با کپشن کامل.
            chunks = balanced_chunks(files, MAX_ALBUM_SIZE)
            if len(chunks) > 1:
                print(f'📚 {len(files)} فایل → {len(chunks)} آلبوم: {[len(c) for c in chunks]}')
            for idx, chunk in enumerate(chunks):
                when = schedule
                if schedule is not None and idx:
                    # چند ثانیه فاصله تا ترتیب آلبوم‌ها توی کانال حفظ بشه
                    from datetime import timedelta
                    when = schedule + timedelta(seconds=10 * idx)
                sent = await client.send_file(
                    chat_id_value,
                    chunk,
                    caption=caption,
                    parse_mode=pick_parse_mode(caption),
                    supports_streaming=True,
                    schedule=when,
                )
                if isinstance(sent, list):
                    sent_messages.extend(sent)
                else:
                    sent_messages.append(sent)
        else:
            # fallback: تک‌تک (برای فایل تکی یا وقتی فایل غیرمدیا داریم)
            for f in files:
                attributes = [DocumentAttributeFilename(os.path.basename(f))]
                extra = {}
                if f.lower().endswith('.mp4'):
                    meta = video_meta if len(files) == 1 else None
                    vattr = video_attributes(f, meta, thumb)
                    finfo = mp4_info(f)
                    print(f"🎞️ فایل: codec={finfo['codecs']} fragmented={finfo['fragmented']} "
                          f"duration(file)={finfo['duration']} | ابعاد(file)={finfo['w']}x{finfo['h']}")
                    if vattr:
                        attributes.append(vattr)
                        print(f'🎞️ attribute ارسالی: duration={vattr.duration}s ابعاد={vattr.w}x{vattr.h} '
                              f"| کاور={'دارد' if thumb else 'ندارد'}")
                    else:
                        print('⚠️ attribute ویدیو ساخته نشد (نه متادیتا، نه فایل، نه کاور)')
                    if thumb:
                        extra['thumb'] = thumb
                sent = await client.send_file(
                    chat_id_value,
                    f,
                    caption=caption,
                    parse_mode=pick_parse_mode(caption),
                    supports_streaming=True,
                    schedule=schedule,
                    attributes=attributes,
                    **extra,
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
    parser.add_argument('--chat-id', required=False, default='')
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
    parser.add_argument('--caption-mode', default='raw',
                         help="raw = کپشن دست‌نخورده (پیش‌فرض) | channel = نوع (کلیپ/دمو/تریلر) + قالب کانال")
    parser.add_argument('--test-file', default='',
                         help='JSON لیست آیتم‌های n8n ({caption, mediaJson}) — فقط نوع/دلیل هر پست چاپ می‌شه')
    parser.add_argument('--profile', default='',
                         help='اسم پروفایل پیج از scripts/page_profiles.py (خالی = قواعد سراسری) — برای تست/ارسال دستی')
    parser.add_argument('--dry-run', action='store_true',
                         help='فقط نوع و کپشن رو چاپ کن؛ نه دانلود، نه ارسال (برای تست قواعد)')
    args = parser.parse_args()

    try:
        profile = resolve_profile(args.profile)
    except KeyError as e:
        print(f'❌ {e}')
        sys.exit(1)

    if args.test_file:
        run_test_file(args.test_file, profile)
        return

    chat_id = args.chat_id.strip()
    media_json = args.media_json.strip()
    channel_mode = (args.caption_mode or 'raw').strip().lower() == 'channel'
    caption_override = ''
    thumb = None
    video_meta = None

    if not args.dry_run:
        if not chat_id:
            print('❌ --chat-id لازمه')
            sys.exit(1)
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
            duration = None
            tags = []
            kind = None
            if channel_mode:
                tags = extract_hashtags(args.caption)
                duration = meta_duration(media_items)
                kind, why = decide_type(media_items, args.caption, duration, profile)
                if kind is None:
                    print(f'⏭️ ارسال نمی‌شه — {why}')
                    return
                if kind != 'PENDING':
                    print(f'🏷️ نوع={kind} | {why}')
                if args.dry_run:
                    if kind == 'PENDING':
                        print('⚠️ dry-run: فیلد duration نیومده (بعد از دانلود از خود فایل خونده می‌شه)')
                        return
                    cap = build_channel_caption(kind, args.caption, tags, args.url.strip(), profile)
                    print(f'🧪 طول قابل‌نمایش: {_visible_len(cap)}/{MAX_TG_CAPTION_LEN}\n')
                    print(cap)
                    return
            files = download_media_direct(media_items)
            if any((i.get('type') or '').lower() == 'video' for i in media_items):
                thumb = prepare_thumb(media_items, files)
                print(f"🖼️ کاور ویدیو: {'آماده شد' if thumb else 'ساخته نشد'}")
                video_meta = next((i for i in media_items if (i.get('type') or '').lower() == 'video'), None)
            original_caption = args.caption
            source = 'instagram (direct CDN)'
            if channel_mode:
                if kind == 'PENDING':
                    ds = [mp4_info(f)['duration'] for f in files if f.lower().endswith('.mp4')]
                    ds = [d for d in ds if d]
                    duration = max(ds) if ds else None
                    if duration is None:
                        print('❌ مدت ویدیو مشخص نشد (نه از Apify، نه از خود فایل)')
                        sys.exit(1)
                    kind, why = decide_type(media_items, args.caption, duration, profile)
                    print(f'🏷️ نوع={kind} | {why}')
                caption_override = build_channel_caption(kind, args.caption, tags, args.url.strip(), profile)

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

        caption = build_caption(caption_override or original_caption)

        for f in files:
            size_mb = os.path.getsize(f) / 1024 / 1024
            print(f"✅ آماده ارسال: {f} ({size_mb:.1f} MB)")

        print(f'📨 در حال ارسال به تلگرام (chat_id={chat_id}, تعداد آیتم={len(files)})...')
        try:
            sent = asyncio.run(send_items_to_telegram(files, caption, chat_id, thumb, video_meta))
            ids = ', '.join(str(s.id) for s in sent)
            print(f'✅ ارسال شد. message_id(s)={ids}')
        except FloodWaitError as e:
            print(f'⏳ FloodWait: باید {e.seconds} ثانیه صبر کرد')
            sys.exit(2)
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)


if __name__ == '__main__':
    main()
