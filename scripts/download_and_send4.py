#!/usr/bin/env python3
"""
download_and_send4.py — نسخه‌ی «فقط هشتگ» برای کانال اصلی aniwall_lab

کاری که این اسکریپت جدید اضافه می‌کنه (نسبت به send2/send3): از روی متن و
هشتگ‌های خام پست اینستا، خودش هشتگ‌های استاندارد کانال (فرنچایز + شخصیت +
دسته‌ی توصیفی) رو با کمک character_dictionary.csv می‌سازه و کپشن نهایی رو
به‌صورت «فقط هشتگ داخل blockquote» می‌فرسته. متن اصلی پست (تبلیغ، منشن،
لینک) هیچ‌وقت وارد کپشن نهایی نمی‌شه.

هیچ منطق دانلود/ارسال/ویدیو/entity-resolution اینجا از صفر نوشته نشده —
همه از download_and_send3.py (که عملیاتی و تست‌شده‌ست) import می‌شن، دقیقاً
به همون روشی که gacha_insta_check.py از send3 استفاده می‌کنه.
"""

import argparse
import asyncio
import csv
import json
import os
import re
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# نکته: import واقعی download_and_send3 (که telethon/curl_cffi لازم داره) عمداً اینجا
# نیست، بلکه داخل توابعی که واقعاً بهش نیاز دارن (پایین همین فایل). این یعنی منطق
# تشخیص هشتگ/دیکشنری (build_hashtag_caption) بدون نصب telethon هم قابل تست و استفاده‌ست.

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DICTIONARY_PATH = os.environ.get("CHARACTER_DICTIONARY_PATH", os.path.join(REPO_ROOT, "character_dictionary.csv"))
HASHTAG_RULES_PATH = os.environ.get("HASHTAG_RULES_PATH", os.path.join(REPO_ROOT, "hashtag_rules.json"))

MAX_CHARACTER_TAGS = 3
MAX_DESCRIPTIVE_TAGS = 2
MIN_SEEN_SINGLE = 3          # اسم‌های تک‌کلمه‌ای (بدون فاصله) حداقل این‌قدر seen_count لازم دارن
MIN_FRANCHISE_KEY_LEN = 4    # کلید فرنچایز کوتاه‌تر از این، فقط با تطبیق دقیق قبول می‌شه نه شامل‌بودن
MIN_FRANCHISE_PREFIX_LEN = 5 # حداقل طول کلید برای مجاز بودن تطبیق «پیشوندی» (#genshin ← genshinimpact)

# لیست کلمه‌های عمومی که هیچ‌وقت نباید به‌عنوان اسم فرنچایز/شخصیت قبول بشن
# (عیناً از نود «Enrich With Dictionary» خود کاربر — n8n)
DENY = {
    'shade', 'shadow', 'pudding', 'chair', 'flower', 'flowers', 'smile', 'star', 'stars',
    'ribbon', 'dress', 'skirt', 'bell', 'heart', 'cake', 'feet', 'wings', 'halo', 'gem',
    'ring', 'boots', 'hat', 'jacket', 'necklace', 'earrings', 'gloves', 'tights', 'fruit',
    'navel', 'bird',
}

FALLBACK_TAG = 'AnimeArt'

# هشتگ‌های عمومیِ خیلی رایج که هیچ‌وقت نباید شاهدِ فرنچایز حساب بشن — حتی اگه
# تصادفاً پیشوند/زیررشته‌ی اسم یه فرنچایز مبهم و کم‌شناخته باشن (نمونه‌ی واقعی:
# «manga» پیشوند «Manga de Wakaru! FGO» و «anime» پیشوند «Animedia 2024-10» است
# که باعث تساوی مصنوعی و رد شدن فرنچایز واقعی پست می‌شد).
GENERIC_HASHTAGS = {
    'anime', 'animeart', 'manga', 'art', 'artwork', 'fanart', 'digitalart',
    'aiart', 'aiartwork', 'aianime', 'wallpaper', 'hdwallpaper', 'hdwallpapers',
    'waifu', 'waifus', 'husbando', 'weeb', 'otaku', 'cosplay', 'fyp', 'viral',
    'reels', 'reel', 'explore', 'explorepage', 'trending', 'trendingnow',
    'love', 'cute', 'aesthetic', 'instagood', 'instareach', 'follow', 'like',
}

HASHTAG_RE = re.compile(r'#(\w+)', re.UNICODE)
MENTION_RE = re.compile(r'@[\w.](?:[\w.]{0,29})', re.UNICODE)
URL_RE = re.compile(r'https?://\S+|www\.\S+', re.UNICODE)
WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)  # حروف خالص (بدون رقم/زیرخط)، برای ساخت n-gram متن


def compact(s: str) -> str:
    """کلید فشرده برای تطبیق: فقط حروف/رقم لاتین و یونیکد، حروف کوچک، بدون فاصله/علامت."""
    return re.sub(r'[^0-9a-zA-Z\u00c0-\uffff]', '', str(s or '')).lower()


def to_hashtag(name: str) -> str:
    """'Hu Tao' -> 'HuTao' ، 'Genshin Impact' -> 'GenshinImpact'"""
    parts = re.split(r'[^0-9a-zA-Z\u00c0-\uffff]+', str(name or ''))
    return ''.join(p[:1].upper() + p[1:] for p in parts if p)


# ─────────────────────────────────────────
# بارگذاری دیکشنری — فقط یک‌بار در import
# ─────────────────────────────────────────
class Dictionary:
    def __init__(self, franchise_map, char_by_franchise, char_global):
        self.franchise_map = franchise_map            # compact_key -> canonical name
        self.franchise_sorted = sorted(franchise_map.items(), key=lambda kv: -len(kv[0]))
        self.char_by_franchise = char_by_franchise     # franchise_compact -> [(compact, canonical, seen)]
        self.char_global = char_global                 # compact -> [(canonical, franchise, seen)]


def load_dictionary(path: str) -> Dictionary:
    franchise_map = {}
    char_rows_by_franchise = {}
    char_rows_global = {}

    if not os.path.exists(path):
        print(f'⚠️ دیکشنری پیدا نشد ({path!r}) — بدون تشخیص فرنچایز/شخصیت ادامه می‌ده')
        return Dictionary({}, {}, {})

    with open(path, encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            franchise_name = (row.get('franchise') or '').strip()
            character_name = (row.get('character') or '').strip()
            try:
                seen = int(float(row.get('seen_count') or 0))
            except (TypeError, ValueError):
                seen = 0

            f_key = compact(franchise_name)
            if f_key and f_key not in DENY:
                # اگه یه فرنچایز چندبار دیده بشه، همون اسم یکسانه؛ فقط اولین بار ثبت کافیه
                franchise_map.setdefault(f_key, franchise_name)

            c_key = compact(character_name)
            if not c_key or c_key in DENY:
                continue
            is_single_word = ' ' not in character_name.strip()
            if is_single_word and seen < MIN_SEEN_SINGLE:
                continue

            char_rows_global.setdefault(c_key, []).append((character_name, franchise_name, seen))
            if f_key:
                char_rows_by_franchise.setdefault(f_key, []).append((c_key, character_name, seen))

    return Dictionary(franchise_map, char_rows_by_franchise, char_rows_global)


_DICT = load_dictionary(DICTIONARY_PATH)


def load_hashtag_rules(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f'⚠️ hashtag_rules.json خونده نشد ({e}) — دسته‌ی توصیفی غیرفعاله')
        return {}


_RULES = load_hashtag_rules(HASHTAG_RULES_PATH)


# ─────────────────────────────────────────
# ترجمه‌ی رایگان (همون endpoint غیررسمی گوگل که خود کاربر جای دیگه استفاده کرده)
# fail شدنش هیچ‌وقت جلوی ادامه‌ی کار رو نمی‌گیره
# ─────────────────────────────────────────
def translate_to_english(text: str) -> str:
    text = (text or '').strip()
    if not text:
        return ''
    try:
        resp = requests.get(
            'https://translate.googleapis.com/translate_a/single',
            params={'client': 'gtx', 'sl': 'auto', 'tl': 'en', 'dt': 't', 'q': text},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        translated = ''.join(seg[0] for seg in data[0] if seg and seg[0])
        return translated.strip()
    except Exception as e:
        print(f'⚠️ ترجمه شکست خورد ({e}) — با متن اصلی ادامه می‌دیم')
        return ''


# ─────────────────────────────────────────
# پاک‌سازی متن و استخراج شواهد (هشتگ‌ها با وزن بیشتر، متن آزاد با وزن کمتر)
# ─────────────────────────────────────────
def clean_text(text: str) -> str:
    text = MENTION_RE.sub(' ', text or '')
    text = URL_RE.sub(' ', text)
    text = HASHTAG_RE.sub(' ', text)  # هشتگ‌ها جدا پردازش می‌شن، اینجا حذف می‌شن که در n-gram متن دوبار حساب نشن
    return text


def extract_hashtags_compact(text: str) -> list:
    return [compact(h) for h in HASHTAG_RE.findall(text or '') if compact(h)]


def text_ngrams(text: str, max_n: int = 5) -> set:
    """عبارت‌های ۱ تا max_n کلمه‌ی متوالی، فشرده‌شده — برای تطبیق دقیق اسم‌های چندکلمه‌ای."""
    tokens = [t.lower() for t in WORD_RE.findall(text or '')]
    grams = set()
    for n in range(1, max_n + 1):
        for i in range(len(tokens) - n + 1):
            g = ''.join(tokens[i:i + n])
            if g:
                grams.add(g)
    return grams


# ─────────────────────────────────────────
# تشخیص فرنچایز
# ─────────────────────────────────────────
def detect_franchise(hashtags: list, ngrams: set):
    scores = {}  # canonical name -> امتیاز

    for h in hashtags:
        if h in GENERIC_HASHTAGS:
            continue
        for key, name in _DICT.franchise_sorted:
            if len(key) < MIN_FRANCHISE_KEY_LEN:
                if key == h:
                    scores[name] = scores.get(name, 0) + 3
                    break
                continue
            is_contains = key in h
            # پیشوندی یعنی خود هشتگ کوتاه‌تر، پیشوندِ کلید کامل فرنچایزه — مثل #genshin ← genshinimpact
            is_prefix = len(h) >= MIN_FRANCHISE_PREFIX_LEN and key.startswith(h)
            if is_contains or is_prefix:
                scores[name] = scores.get(name, 0) + 3
                break  # طولانی‌ترین تطبیق برای این هشتگ کافیه

    for ng in ngrams:
        name = _DICT.franchise_map.get(ng)
        if name:
            scores[name] = scores.get(name, 0) + 1

    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None  # دو فرنچایز امتیاز مساوی → به‌جای حدس، هیچ‌کدوم
    return ranked[0][0]


# ─────────────────────────────────────────
# تشخیص شخصیت
# ─────────────────────────────────────────
def detect_characters(hashtags: list, ngrams: set, franchise_name: str) -> list:
    found_seen = {}   # canonical -> seen_count
    order = []

    def add(name, seen):
        if name not in found_seen:
            found_seen[name] = seen
            order.append(name)

    if franchise_name:
        candidates = _DICT.char_by_franchise.get(compact(franchise_name), [])
        candidates_sorted = sorted(candidates, key=lambda c: -len(c[0]))
        for h in hashtags:
            for key, name, seen in candidates_sorted:
                if key and key in h:
                    add(name, seen)
                    break
        for ng in ngrams:
            for key, name, seen in candidates_sorted:
                if key == ng:
                    add(name, seen)
                    break
    else:
        # بدون فرنچایز تأییدشده: فقط تطبیق دقیق، و فقط روی هشتگ (نه متن آزاد).
        # دلیل محدودکردن به هشتگ: کلمه‌های معمولی مثل «Link» (مثلاً در «Link in
        # bio») تصادفاً اسم شخصیت (زلدا) هم هستن؛ هشتگ زدن یه تصمیم آگاهانه‌ی
        # کاربره، ولی کلمه‌ی معمولی وسط جمله نیست. نمونه‌ی KITA همین منطق رو
        # برای شامل‌بودن نشون داد؛ اینجا همون احتیاط برای متن آزاد هم اعمال می‌شه.
        for tok in set(hashtags):
            if tok in GENERIC_HASHTAGS:
                continue
            opts = _DICT.char_global.get(tok)
            if not opts:
                continue
            name, _fr, seen = max(opts, key=lambda o: o[2])
            add(name, seen)

    ranked = sorted(order, key=lambda n: (-found_seen[n], order.index(n)))
    return ranked[:MAX_CHARACTER_TAGS]


# ─────────────────────────────────────────
# دسته‌ی توصیفی (نوع سوم) — فقط کلیدواژه‌ی متنی
# ─────────────────────────────────────────
def detect_descriptive_tags(full_text: str) -> list:
    if not _RULES:
        return []
    low = (full_text or '').lower()
    found = []
    for _key, rule in _RULES.items():
        for pattern in rule.get('patterns', []):
            if pattern in low:
                found.append(rule.get('tag') or _key)
                break
        if len(found) >= MAX_DESCRIPTIVE_TAGS:
            break
    return found[:MAX_DESCRIPTIVE_TAGS]


# ─────────────────────────────────────────
# ساخت کپشن نهایی — یا رشته‌ی HTML، یا None (یعنی این پست رد می‌شه)
# ─────────────────────────────────────────
def build_hashtag_caption(original_caption: str):
    """خروجی: (caption_html یا None, دلیل به‌صورت متن برای لاگ)"""
    cleaned = clean_text(original_caption)
    translated = translate_to_english(cleaned) if cleaned.strip() else ''

    hashtags = extract_hashtags_compact(original_caption)
    ngrams = text_ngrams(cleaned)
    if translated and translated.strip().lower() != cleaned.strip().lower():
        ngrams |= text_ngrams(translated)

    franchise = detect_franchise(hashtags, ngrams)
    characters = detect_characters(hashtags, ngrams, franchise)

    combined_text_for_rules = ' '.join([
        cleaned, translated,
        ' '.join(HASHTAG_RE.findall(original_caption or ''))
    ])
    descriptive = detect_descriptive_tags(combined_text_for_rules)

    if not franchise and not characters and not descriptive:
        return None, 'هیچ نشونه‌ی مرتبط با انیمه/کاراکتر پیدا نشد — رد شد'

    tags = []
    seen_lower = set()

    def push(name):
        tag = to_hashtag(name)
        if tag and tag.lower() not in seen_lower:
            seen_lower.add(tag.lower())
            tags.append(tag)

    if franchise:
        push(franchise)
    for c in characters:
        push(c)
    for d in descriptive:
        push(d)

    if not tags:
        tags = [FALLBACK_TAG]

    caption = f"<blockquote>💠 {' '.join('#' + t for t in tags)}</blockquote>"
    why = f"franchise={franchise!r} characters={characters!r} descriptive={descriptive!r}"
    return caption, why


# ─────────────────────────────────────────
# main — مسیر CLI مستقل (برای فراخوانی مستقیم از n8n/GitHub Actions)
# ─────────────────────────────────────────
def _load_dsend3():
    import download_and_send3 as dsend3
    return dsend3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--chat-id', required=False, default='')
    parser.add_argument('--media-json', required=True,
                         help='JSON آرایه‌ای از {"url":"...","type":"photo|video"}')
    parser.add_argument('--caption', required=False, default='', help='کپشن اصلی پست اینستا')
    parser.add_argument('--dry-run', action='store_true',
                         help='فقط کپشن ساخته‌شده رو چاپ کن؛ نه دانلود نه ارسال')
    args = parser.parse_args()

    try:
        media_items = json.loads(args.media_json)
    except json.JSONDecodeError as e:
        print(f'❌ media-json نامعتبره: {e}')
        sys.exit(1)

    if any((i.get('type') or '').lower() == 'video' for i in media_items):
        print('⏭️ پست ویدیوئه — طبق قانون کانال ارسال نمی‌شه')
        return

    caption, why = build_hashtag_caption(args.caption)
    if caption is None:
        print(f'⏭️ ارسال نمی‌شه — {why}')
        return
    print(f'🏷️ {why}')
    print(caption)

    if args.dry_run:
        return

    if not args.chat_id:
        print('❌ --chat-id لازمه')
        sys.exit(1)

    dsend3 = _load_dsend3()
    if not (dsend3.TG_API_ID and dsend3.TG_API_HASH and dsend3.TG_SESSION_STRING):
        print('❌ TG_API_ID / TG_API_HASH / TG_SESSION_STRING تنظیم نشده')
        sys.exit(1)

    files = dsend3.download_media_direct(media_items)
    for f in files:
        size_mb = os.path.getsize(f) / 1024 / 1024
        print(f'✅ آماده ارسال: {f} ({size_mb:.1f} MB)')

    try:
        sent = asyncio.run(dsend3.send_items_to_telegram(files, caption, args.chat_id))
        ids = ', '.join(str(s.id) for s in sent)
        print(f'✅ ارسال شد. message_id(s)={ids}')
    finally:
        import shutil
        shutil.rmtree(dsend3.WORKDIR, ignore_errors=True)


if __name__ == '__main__':
    main()
