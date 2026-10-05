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
import unicodedata
from itertools import permutations

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# نکته: import واقعی download_and_send3 (که telethon/curl_cffi لازم داره) عمداً اینجا
# نیست، بلکه داخل توابعی که واقعاً بهش نیاز دارن (پایین همین فایل). این یعنی منطق
# تشخیص هشتگ/دیکشنری (build_hashtag_caption) بدون نصب telethon هم قابل تست و استفاده‌ست.

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DICTIONARY_PATH = os.environ.get("CHARACTER_DICTIONARY_PATH", os.path.join(REPO_ROOT, "character_dictionary.csv"))
HASHTAG_RULES_PATH = os.environ.get("HASHTAG_RULES_PATH", os.path.join(REPO_ROOT, "hashtag_rules.json"))

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
    # کلمه‌های عمومیِ رنگ/لقب که به‌عنوان جزئی از اسم شخصیت‌ها زیادن ولی به‌تنهایی
    # هیچ اطلاعاتی ندارن (نمونه‌ی واقعی باگ: «Black» از «Black Maria» با هشتگ
    # #blackbeard قاطی شد، چون blackbeard با «black» شروع می‌شه)
    'black', 'white', 'red', 'blue', 'gold', 'golden', 'silver', 'green', 'dark', 'bright',
    'king', 'queen', 'lord', 'lady', 'big', 'little', 'young', 'old', 'mr', 'saint', 'holy',
    'great', 'grand', 'prince', 'princess',
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

# هشتگ‌های خالص «ترند/تعامل‌گیری اینستاگرام» — چیزهایی که مردم پای هر پستی
# می‌زنن تا الگوریتم دیده‌شون کنه، هیچ ربطی به محتوای واقعی پست (فرنچایز/
# کاراکتر/سبک) ندارن. عمداً از GENERIC_HASHTAGS جدا نگه داشته شده که مدیریتش
# راحت‌تر باشه (این‌ها صرفاً اسپم تعامل‌گیری‌ان، نه اصطلاح توصیفیِ عمومی هنر).
INSTAGRAM_TREND_HASHTAGS = {
    'fyp', 'fypage', 'foryou', 'foryoupage', 'foryourpage', 'viral', 'viralpost',
    'viralreels', 'viralvideo', 'trend', 'trending', 'trendingnow', 'trendingreels',
    'explore', 'explorepage', 'reelsinstagram', 'reelitfeelit', 'instareels',
    'instagood', 'instadaily', 'instalike', 'instamood', 'instapic', 'instaphoto',
    'instagramreels', 'igers', 'igdaily', 'picoftheday', 'photooftheday',
    'bestoftheday', 'follow', 'followme', 'followforfollowback', 'follow4follow',
    'f4f', 'like4like', 'likeforlike', 'likeforlikes', 'l4l', 'commentforcomment',
    'tagsforlikes', 'share', 'shoutout', 'repost', 'growmyaccount', 'boostmyaccount',
    'algorithm', 'newpost', 'dailypost', 'contentcreator', 'creatorsoninstagram',
    'artistsoninstagram', 'supportsmallartists', 'smallartist', 'like', 'likes',
    'comment', 'comments', 'subscribe', 'follownow','openart','jjk','cozyart','creativeai','aidigitalart','aiartcommunity','aiartgallery','fyppage',
    'animecommunity','mangaart','viralart','trendingwallpaper','deminiart','digitalanime','animeedit','ai','pageone','aiedit',
}

HASHTAG_RE = re.compile(r'#(\w+)', re.UNICODE)
MENTION_RE = re.compile(r'@[\w.](?:[\w.]{0,29})', re.UNICODE)
URL_RE = re.compile(r'https?://\S+|www\.\S+', re.UNICODE)
WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)  # حروف خالص (بدون رقم/زیرخط)، برای ساخت n-gram متن


def fold_accents(s: str) -> str:
    """حرف‌های لاتینِ اکسان‌دار → بدون اکسان ('Pokémon' → 'Pokemon'). حروف غیرلاتین (ژاپنی/چینی...) دست‌نخورده می‌مونن."""
    s = str(s or '')
    if s.isascii():
        return s
    out = []
    for ch in s:
        if ord(ch) < 0x250:
            base = ''.join(c for c in unicodedata.normalize('NFKD', ch) if unicodedata.category(c) != 'Mn')
            out.append(base or ch)
        else:
            out.append(ch)
    return ''.join(out)


def compact(s: str) -> str:
    """کلید فشرده برای تطبیق: فقط حروف/رقم لاتین و یونیکد، حروف کوچک، بدون فاصله/علامت، بدون اکسان."""
    return re.sub(r'[^0-9a-zA-Z\u00c0-\uffff]', '', fold_accents(s)).lower()


def to_hashtag(name: str) -> str:
    """'Hu Tao' -> 'HuTao' ، 'Genshin Impact' -> 'GenshinImpact'
    اگه اسم «:» داشت (مثل 'Mushoku Tensei: Isekai Ittara Honki Dasu')، فقط
    قسمت قبل از «:» گرفته می‌شه تا هشتگ خیلی طولانی نشه ('MushokuTensei')."""
    name = str(name or '').split(':', 1)[0]
    parts = re.split(r'[^0-9a-zA-Z\u00c0-\uffff]+', name)
    return ''.join(p[:1].upper() + p[1:] for p in parts if p)


# ─────────────────────────────────────────
# بارگذاری دیکشنری — فقط یک‌بار در import
# ─────────────────────────────────────────
class Dictionary:
    def __init__(self, franchise_map, char_by_franchise, char_global,
                 franchise_display=None, weak_franchise_keys=None, name_group=None, name_words=None):
        self.franchise_map = franchise_map            # compact_key -> canonical name (ستون franchise)
        self.franchise_sorted = sorted(franchise_map.items(), key=lambda kv: -len(kv[0]))
        self.char_by_franchise = char_by_franchise     # franchise_compact -> [(compact, canonical, seen)]
        self.char_global = char_global                 # compact -> [(canonical, franchise, seen)]
        self.franchise_display = franchise_display or {}   # canonical (franchise) -> اسمی که توی هشتگ می‌ره (franchise_en اگه بود)
        self.weak_franchise_keys = weak_franchise_keys or set()  # کلیدهای کوتاه/تک‌کلمه‌ای که فقط از franchise_en اومدن
        self.name_group = name_group or {}             # کلید هر ترتیبِ ممکنِ اسم → کلید گروه (ترتیب‌بی‌اثر)
        self.name_words = name_words or set()          # تک‌کلمه‌های اسم شخصیت‌ها (برای تشخیص «اسم ناقص»)


# کلمه‌هایی که جزء «اسم شخصیت» حساب نمی‌شن، حتی اگه توی دیکشنری یه شخصیت با این اسم باشه
# (برای قاعده‌ی «اسم ناقص کنار اسم کامل» — #gojo کنار #gojosatoru، ولی #onepiece کنار #onepieceart نه)
NAME_WORD_STOPLIST = {
    'art', 'arts', 'anime', 'fanart', 'manga', 'official', 'edit', 'edits', 'girl', 'girls', 'boy',
    'world', 'fan', 'fans', 'wallpaper', 'cosplay', 'movie', 'series', 'ai', 'artist', 'artwork',
}


def load_dictionary(path: str) -> Dictionary:
    franchise_map = {}
    franchise_display = {}
    weak_keys = set()
    char_rows_by_franchise = {}
    char_rows_global = {}
    name_group = {}
    name_words = set()

    if not os.path.exists(path):
        print(f'⚠️ دیکشنری پیدا نشد ({path!r}) — بدون تشخیص فرنچایز/شخصیت ادامه می‌ده')
        return Dictionary({}, {}, {})

    with open(path, encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            franchise_name = (row.get('franchise') or '').strip()
            franchise_en = (row.get('franchise_en') or '').strip()
            character_name = (row.get('character') or '').strip()
            try:
                seen = int(float(row.get('seen_count') or 0))
            except (TypeError, ValueError):
                seen = 0

            f_key = compact(franchise_name)
            if f_key and f_key not in DENY:
                # اگه یه فرنچایز چندبار دیده بشه، همون اسم یکسانه؛ فقط اولین بار ثبت کافیه
                franchise_map.setdefault(f_key, franchise_name)
                if franchise_en:
                    franchise_display.setdefault(franchise_name, franchise_en)
                # اسم انگلیسی فرنچایز («My Dress-Up Darling») هم یه کلید تطبیق می‌شه که به همون
                # اسم اصلی (franchise) برگرده — تا کپشن‌هایی که اسم انگلیسی نوشتن هم پیدا بشن.
                en_key = compact(franchise_en)
                if en_key and en_key != f_key and en_key not in DENY and en_key not in franchise_map:
                    franchise_map[en_key] = franchise_name
                    if ' ' not in franchise_en.strip() and len(en_key) < 7:
                        weak_keys.add(en_key)   # تک‌کلمه‌ی کوتاه فقط از روی هشتگ قبول می‌شه، نه از متن آزاد

            c_key = compact(character_name)
            if not c_key or c_key in DENY:
                continue
            is_single_word = ' ' not in character_name.strip()
            if is_single_word and seen < MIN_SEEN_SINGLE:
                continue

            words = character_name.strip().split()
            if len(words) > 1:
                # همه‌ی ترتیب‌های ممکنِ اسم («Natsuki Subaru» ↔ «Subaru Natsuki») یه گروه‌ان؛
                # هم برای تطبیق متن، هم برای حذف تکرار.
                group = ''.join(sorted(compact(w) for w in words))
                for perm in (permutations(words) if len(words) <= 3 else [tuple(words), tuple(reversed(words))]):
                    pk = compact(''.join(perm))
                    if pk:
                        name_group.setdefault(pk, group)
                for w in words:
                    wk = compact(w)
                    if len(wk) >= 4 and wk not in DENY and wk not in NAME_WORD_STOPLIST and wk not in GENERIC_HASHTAGS:
                        name_words.add(wk)
            else:
                name_group.setdefault(c_key, c_key)

            char_rows_global.setdefault(c_key, []).append((character_name, franchise_name, seen))
            if f_key:
                char_rows_by_franchise.setdefault(f_key, []).append((c_key, character_name, seen))
                # علاوه بر کلید کامل («Donquixote Doflamingo» → donquixotedoflamingo)،
                # کلید تک‌تکِ کلمات اسم هم اضافه می‌شه («Doflamingo» → doflamingo) تا
                # هشتگی مثل #doflamingo (فقط نام خانوادگی، بدون کل اسم) هم پیدا بشه.
                # این با substring دلبخواهی فرق داره (نمونه‌ی باگ Nami/dynamic):
                # فقط کلمه‌های واقعیِ خودِ اسم به‌عنوان کلید اضافه می‌شن، نه هر زیررشته‌ای.
                if len(words) > 1:
                    for w in words:
                        w_key = compact(w)
                        if w_key and w_key not in DENY and len(w_key) >= 3:
                            char_rows_by_franchise[f_key].append((w_key, character_name, seen))
                    # ترتیب برعکس/جابه‌جا هم به همون اسم برمی‌گرده («Marin Kitagawa» → Kitagawa Marin)
                    for perm in (permutations(words) if len(words) <= 3 else [tuple(reversed(words))]):
                        pk = compact(''.join(perm))
                        if pk and pk != c_key:
                            char_rows_by_franchise[f_key].append((pk, character_name, seen))
                            char_rows_global.setdefault(pk, []).append((character_name, franchise_name, seen))

    return Dictionary(franchise_map, char_rows_by_franchise, char_rows_global,
                      franchise_display, weak_keys, name_group, name_words)


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


def extract_raw_hashtag_names(text: str) -> list:
    """هشتگ‌های خام پست (به ترتیب ظاهر)، بعد از حذف چرت‌های عمومی/ترند اینستا/DENY
    و تکراری‌ها. خروجی: لیست رشته‌های خام هشتگ (بدون #) — این‌ها مستقیم به
    خروجی نهایی اضافه می‌شن، مکملِ چیزی که از دیکشنری تشخیص داده می‌شه."""
    out = []
    seen = set()
    for h in HASHTAG_RE.findall(text or ''):
        key = compact(h)
        if not key or key in seen:
            continue
        if key in GENERIC_HASHTAGS or key in DENY or key in INSTAGRAM_TREND_HASHTAGS:
            continue
        seen.add(key)
        out.append(h)
    return out


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
def detect_franchise(hashtags: list, ngrams: set, title_ngrams: set = None):
    """title_ngrams: n-gramهای خط(های) اول کپشن؛ اسمی که توی عنوان اومده یه امتیاز اضافه می‌گیره
    (برای شکستن تساوی وقتی کپشن چندفرنچایزی/چندزبانه‌ست)."""
    scores = {}  # canonical name -> امتیاز

    for h in hashtags:
        if h in GENERIC_HASHTAGS or h in INSTAGRAM_TREND_HASHTAGS:
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
        if name and ng not in _DICT.weak_franchise_keys:
            scores[name] = scores.get(name, 0) + 1
            if title_ngrams and ng in title_ngrams:
                scores[name] += 1

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
                if not key:
                    continue
                # فقط «پیشوند» قبول می‌شه (اسم شخصیت اول هشتگ)، نه هر جای وسط
                # کلمه — وگرنه «nami» تصادفاً وسط «TsuDynamicShot» پیدا می‌شه
                # (نمونه‌ی واقعی باگ). اسم‌های خیلی کوتاه (زیر ۴ حرف) فقط با
                # تطبیق دقیق قبول می‌شن.
                is_exact = key == h
                is_prefix = len(key) >= 4 and h.startswith(key)
                if is_exact or is_prefix:
                    add(name, seen)
                    break
        for ng in ngrams:
            for key, name, seen in candidates_sorted:
                if key == ng:
                    add(name, seen)
                    break
    else:
        # بدون فرنچایز تأییدشده: تطبیق دقیق، هم روی هشتگ هم روی n-gram متن
        # (طبق تصمیم کاربر: هرچی از متن دراومد هم مثل هشتگ حساب می‌شه).
        for tok in set(hashtags) | ngrams:
            if tok in GENERIC_HASHTAGS or tok in INSTAGRAM_TREND_HASHTAGS:
                continue
            opts = _DICT.char_global.get(tok)
            if not opts:
                continue
            name, _fr, seen = max(opts, key=lambda o: o[2])
            add(name, seen)

    ranked = sorted(order, key=lambda n: (-found_seen[n], order.index(n)))
    return ranked  # بدون سقف — هرچی با قوانین بالا پیدا بشه، همه برمی‌گرده


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
            if re.search(r'\b' + re.escape(pattern) + r'\b', low):
                found.append(rule.get('tag') or _key)
                break
    return found  # بدون سقف — هرچی دسته‌ی توصیفی تشخیص داده بشه، همه میاد


# ─────────────────────────────────────────
# حذف تکرار هشتگ‌ها (مشترک بین همه‌ی مسیرها)
# ─────────────────────────────────────────
def _tag_key(tag: str) -> str:
    """کلید مقایسه: بدون اکسان/علامت/حروف بزرگ، و برای اسم‌های دوکلمه‌ای ترتیب‌بی‌اثر
    (NatsukiSubaru و SubaruNatsuki یه کلید می‌گیرن)."""
    k = compact(tag)
    return _DICT.name_group.get(k, k)


def dedupe_tags(tags: list, protect: int = 0) -> list:
    """اولین نمونه می‌مونه. سه نوع تکرار حذف می‌شه:
      ۱) یکی‌بودن بعد از حذف اکسان/حروف بزرگ (#Pokémon و #Pokemon)
      ۲) جابه‌جایی اسم و فامیل (#NatsukiSubaru و #SubaruNatsuki) — با کمک دیکشنری
      ۳) اسم ناقص کنار اسم کامل (#gojo کنار #gojosatoru): هشتگ کوتاه‌تر فقط وقتی حذف می‌شه که
         باقی‌مانده‌ی هشتگ بلندتر یکی از کلمه‌های اسم شخصیت‌ها باشه (نه هر پسوندی؛ #gojo کنار
         #gojosupremacy می‌مونه).
      ۴) هشتگ ترکیبی: فقط چسباندنِ دو هشتگ دیگرِ همین پست (#ryukdeathnote = #ryuk + #deathnote)
    protect: تعداد تگ‌های اولِ لیست (فرنچایز/شخصیتِ تشخیص‌داده‌شده) که قواعد ۳ و ۴ هیچ‌وقت حذفشون نمی‌کنن."""
    out, seen, orig_idx = [], set(), []
    for i, t in enumerate(tags):
        k = _tag_key(t)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(t)
        orig_idx.append(i)
    protected = {n for n, i in enumerate(orig_idx) if i < protect}

    keys = [compact(t) for t in out]
    words = _DICT.name_words
    drop = set()
    for a, ka in enumerate(keys):
        if len(ka) < 3 or a in protected:
            continue
        for b, kb in enumerate(keys):
            if a == b or len(kb) <= len(ka):
                continue
            if kb.startswith(ka) and kb[len(ka):] in words:
                drop.add(a)
                break
            if kb.endswith(ka) and kb[:-len(ka)] in words:
                drop.add(a)
                break

    # ۴) هشتگِ ترکیبی: فقط چسباندنِ دو هشتگ دیگرِ همین پست (#ryukdeathnote = #ryuk + #deathnote).
    #    همیشه خودِ ترکیبی حذف می‌شه، نه اجزاش.
    for c, kc in enumerate(keys):
        if c in protected:
            continue
        for a, ka in enumerate(keys):
            if len(ka) < 3 or a == c or not kc.startswith(ka):
                continue
            rest = kc[len(ka):]
            if len(rest) < 3:
                continue
            if any(b not in (a, c) and keys[b] == rest for b in range(len(keys))):
                drop.add(c)
                break
    return [t for idx, t in enumerate(out) if idx not in drop]


def _title_text(cleaned: str) -> str:
    """دو خط غیرخالیِ اول کپشن (عنوان) — برای وزن‌دادن به اسمی که توی عنوان اومده."""
    lines = [ln.strip() for ln in (cleaned or '').splitlines() if ln.strip()]
    return ' '.join(lines[:2])


def _format_caption(tags: list) -> str:
    return f"<blockquote>💠 {' '.join('#' + t for t in tags)}</blockquote>"


# ─────────────────────────────────────────
# ساخت کپشن نهایی — یا رشته‌ی HTML، یا None (یعنی این پست رد می‌شه)
# ─────────────────────────────────────────
def build_hashtag_caption(original_caption: str, default_hashtag: str = None):
    """خروجی: (caption_html یا None, دلیل به‌صورت متن برای لاگ).
    default_hashtag: اگه چیزی تشخیص داده نشد، به‌جای رد کردن پست این هشتگ می‌ره
    (پیش‌فرض None = همون رفتار قبلی: پست رد می‌شه)."""
    cleaned = clean_text(original_caption)
    translated = translate_to_english(cleaned) if cleaned.strip() else ''

    hashtags = extract_hashtags_compact(original_caption)
    ngrams = text_ngrams(cleaned)
    if translated and translated.strip().lower() != cleaned.strip().lower():
        ngrams |= text_ngrams(translated)
    title_ngrams = text_ngrams(_title_text(cleaned))

    franchise = detect_franchise(hashtags, ngrams, title_ngrams)
    characters = detect_characters(hashtags, ngrams, franchise)

    combined_text_for_rules = ' '.join([
        cleaned, translated,
        ' '.join(HASHTAG_RE.findall(original_caption or ''))
    ])
    descriptive = detect_descriptive_tags(combined_text_for_rules)
    raw_hashtags = extract_raw_hashtag_names(original_caption)

    if not franchise and not characters and not descriptive and not raw_hashtags:
        if default_hashtag:
            return _format_caption([default_hashtag]), f'چیزی تشخیص داده نشد — هشتگ پیش‌فرض {default_hashtag}'
        return None, 'هیچ نشونه‌ی مرتبط با انیمه/کاراکتر پیدا نشد — رد شد'

    tags = []

    def push(name):
        tag = to_hashtag(name)
        if tag:
            tags.append(tag)

    if franchise:
        # اسم انگلیسی فرنچایز (franchise_en) اگه بود؛ وگرنه همون اسم اصلی دیکشنری
        push(_DICT.franchise_display.get(franchise) or franchise)
    for c in characters:
        push(c)
    for d in descriptive:
        push(d)
    protect = len(tags)   # فرنچایز/شخصیت/توصیفیِ تشخیص‌داده‌شده با قواعد «ناقص/ترکیبی» حذف نمی‌شن
    for r in raw_hashtags:
        push(r)

    tags = dedupe_tags(tags, protect=protect)
    if not tags:
        tags = [default_hashtag or FALLBACK_TAG]

    why = f"franchise={franchise!r} characters={characters!r} descriptive={descriptive!r} raw_hashtags={raw_hashtags!r}"
    return _format_caption(tags), why


# ─────────────────────────────────────────
# کپشن بر اساس پروفایل پیج (page_profiles.py) — مسیر aniwall_insta_check
#   hashtag_mode:
#     (نبود)       → همون build_hashtag_caption سراسری (تشخیص با دیکشنری)
#     'dictionary' → همون سراسری، ولی با default_hashtag (به‌جای رد شدن پستِ بی‌نشونه)
#     'own'        → فقط هشتگ‌های خود پیج، منهای لیست‌های عمومی/ترند سراسری + قواعد پروفایل
#     'raw'        → هشتگ‌های خود پیج کاملاً دست‌نخورده
#   کلیدهای فقط-برای-'own': blocked_hashtags، blocked_patterns (regex روی کلید فشرده)،
#   allowed_hashtags (از بلاک سراسری/الگویی معاف)، max_hashtags، default_hashtag
# ─────────────────────────────────────────
def _own_hashtags(original_caption: str, profile: dict):
    mode = profile.get('hashtag_mode')
    raw = HASHTAG_RE.findall(original_caption or '')

    if mode == 'raw':
        out, seen = [], set()
        for h in raw:
            if h.lower() in seen:
                continue
            seen.add(h.lower())
            out.append(h)
        return out, f'raw hashtags={out!r}'

    blocked = {compact(b) for b in (profile.get('blocked_hashtags') or ())}
    allowed = {compact(a) for a in (profile.get('allowed_hashtags') or ())}
    patterns = [re.compile(p, re.I) for p in (profile.get('blocked_patterns') or ())]

    kept = []
    for h in raw:
        k = compact(h)
        if not k or k in blocked:
            continue
        if k not in allowed:
            if k in GENERIC_HASHTAGS or k in INSTAGRAM_TREND_HASHTAGS or k in DENY:
                continue
            if any(p.search(k) for p in patterns):
                continue
        tag = to_hashtag(h)
        if tag:
            kept.append(tag)

    kept = dedupe_tags(kept)
    cap = profile.get('max_hashtags')
    if cap:
        kept = kept[:int(cap)]
    return kept, f'own hashtags={kept!r}'


def _build_caption_base(original_caption: str, profile: dict = None):
    def _apply_fixed_tags(caption, fixed):
    """هشتگ‌های ثابت پیج رو آخر بلوک هشتگ اضافه می‌کنه (تکراری‌ها حذف)."""
    fixed = [re.sub(r'[^0-9a-zA-Z\u00c0-\uffff]', '', str(t)) for t in (fixed or ())]
    fixed = [t for t in fixed if t]
    if not fixed:
        return caption
    existing = []
    if caption:
        m = re.match(r'<blockquote>💠 (.*)</blockquote>\s*$', caption, re.S)
        if not m:
            return caption
        existing = [t.lstrip('#') for t in m.group(1).split()]
    seen = {compact(t) for t in existing}
    for t in fixed:
        if compact(t) not in seen:
            seen.add(compact(t))
            existing.append(t)
    return _format_caption(existing)


def build_caption_for_profile(original_caption: str, profile: dict = None):
    """خروجی: (caption_html یا None, دلیل). fixed_hashtags پروفایل آخر کار اضافه می‌شه."""
    profile = profile or {}
    caption, why = _build_caption_base(original_caption, profile)
    fixed = profile.get('fixed_hashtags')
    if fixed:
        caption = _apply_fixed_tags(caption, fixed)
        why += f' + fixed={list(fixed)!r}'
    return caption, why
    """خروجی مثل build_hashtag_caption: (caption_html یا None, دلیل)."""
    profile = profile or {}
    mode = profile.get('hashtag_mode')
    default_tag = profile.get('default_hashtag')

    if mode in ('own', 'raw'):
        tags, why = _own_hashtags(original_caption, profile)
        if not tags:
            if default_tag:
                return _format_caption([default_tag]), f'{why} — بعد از پاک‌سازی چیزی نموند، هشتگ پیش‌فرض {default_tag}'
            return None, f'{why} — بعد از پاک‌سازی چیزی نموند — رد شد'
        if mode == 'raw':
            return _format_caption(tags), why
        return _format_caption(tags), why

    return build_hashtag_caption(original_caption, default_hashtag=default_tag)


# ─────────────────────────────────────────
# main — مسیر CLI مستقل (برای فراخوانی مستقیم از n8n/GitHub Actions)
# ─────────────────────────────────────────
def _load_dsend3():
    import download_and_send3 as dsend3
    return dsend3


ALBUM_PAUSE_SECONDS = 3   # مکث واقعی بین ارسال آلبوم‌های یک پست (فقط وقتی بیش از ۱۰ عکس باشه)
ALBUM_SCHEDULE_GAP_SECONDS = 10   # فاصله‌ی زمان نمایش آلبوم‌ها توی کانال


async def send_photo_album_scheduled(files, caption, chat_id, schedule_dt=None):
    """ارسال آلبوم عکس با قابلیت زمان‌بندی تلگرام (schedule=<datetime>).
    بیش از ۱۰ عکس → آلبوم‌های هم‌اندازه مثل send3 (۱۵ → ۸+۷)، کپشن روی هر آلبوم،
    زمان نمایش هر آلبوم ۱۰ ثانیه بعد از قبلی تا ترتیب توی کانال حفظ بشه.
    عمداً از send3.send_items_to_telegram جدا نوشته شده (فقط عکسه، ویدیو قبلاً رد شده).
    بخش «resolve کردن chat_id» عیناً از send3 کپی شده (باگ سشن تازه با آی‌دی خام کانال)."""
    from datetime import timedelta
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    dsend3 = _load_dsend3()
    client = TelegramClient(StringSession(dsend3.TG_SESSION_STRING), int(dsend3.TG_API_ID), dsend3.TG_API_HASH)
    await client.connect()
    try:
        chat_id_value = int(chat_id) if str(chat_id).lstrip('-').isdigit() else chat_id
        try:
            chat_id_value = await client.get_input_entity(chat_id_value)
        except ValueError:
            await client.get_dialogs()
            chat_id_value = await client.get_input_entity(chat_id_value)

        chunks = dsend3.balanced_chunks(files, dsend3.MAX_ALBUM_SIZE)
        if len(chunks) > 1:
            print(f'📚 {len(files)} فایل → {len(chunks)} آلبوم: {[len(c) for c in chunks]}')

        sent_messages = []
        for idx, chunk in enumerate(chunks):
            when = schedule_dt
            if idx:
                await asyncio.sleep(ALBUM_PAUSE_SECONDS)
                if schedule_dt is not None:
                    when = schedule_dt + timedelta(seconds=ALBUM_SCHEDULE_GAP_SECONDS * idx)
            sent = await client.send_file(
                chat_id_value,
                chunk if len(chunk) > 1 else chunk[0],
                caption=caption,
                parse_mode='html',
                supports_streaming=True,
                schedule=when,
            )
            if isinstance(sent, list):
                sent_messages.extend(sent)
            else:
                sent_messages.append(sent)
        return sent_messages
    finally:
        await client.disconnect()


def compute_schedule_times(now, count, start_offset_hours=2.0, end_offset_hours=1.0, cycle_hours=24.0):
    """زمان‌های زمان‌بندی رو برای `count` پست، پخش‌شده توی بازه‌ی خنثی‌شده برمی‌گردونه.
    اولین پست حداقل start_offset_hours بعد از لحظه‌ی اجرا (پیش‌فرض ۲ ساعت)،
    آخرین پست دقیقاً end_offset_hours قبل از تریگر بعدی (پیش‌فرض ۱ ساعت، ۲۴
    ساعت بعد). اگه فقط ۱ پست باشه، وسط همین بازه قرار می‌گیره."""
    from datetime import timedelta
    window_start = now + timedelta(hours=start_offset_hours)
    window_end = now + timedelta(hours=cycle_hours - end_offset_hours)
    if count <= 0:
        return []
    if count == 1:
        return [window_start + (window_end - window_start) / 2]
    step = (window_end - window_start) / (count - 1)
    return [window_start + i * step for i in range(count)]


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
