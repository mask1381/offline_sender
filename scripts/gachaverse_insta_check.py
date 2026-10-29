#!/usr/bin/env python3
"""
gachaverse_insta_check.py — چک دستی پیج‌های اینستاگرام برای کانال «GACHAVERSE ART».

ساختار مثل aniwall_insta_check.py:
  ۱) همه‌ی پیج‌های gachaverse_pages.json چک می‌شن و پست‌های جدید جمع می‌شن.
  ۲) پست‌ها با Scheduled Messages تلگرام توی بازه‌ی ۲۴ساعته پخش می‌شن.

فرق‌ها با aniwall:
  • فقط آرت بازی‌ها: فرنچایزی که توی character_dictionary.csv ستون
    classification=Game داره (هر بازی‌ای، محدود به لیست ثابت نیست). پست بدون
    فرنچایز بازی رد می‌شه (state جلو می‌ره).
  • کراس‌اور: اگه چند بازی توی پست باشه، هشتگ همه‌شون میاد (نه رد می‌شه).
  • کاراکتر فقط از دیکشنریِ همون بازی(ها) تشخیص داده می‌شه. سقف تعداد نداره.
  • ویدیو/ریلز هم ارسال می‌شه (کاور، متادیتا و فال‌بک صدا از gacha_insta_check).
  • هشتگ‌های خام اینستاگرام و #AnimeArt نمیاد.
  • قالب کپشن دقیقاً مثل کپشن کانال: <blockquote>#بازی #کاراکترها #محتوا</blockquote> + فوتر برند.
  • state هر پیج فقط تا آخرین پستِ «ارسال‌شده یا عمداً ردشده‌ی» پشت‌سرهم جلو می‌ره؛
    اگه پستی fail بشه، دفعه‌ی بعد دوباره امتحان می‌شه و پست‌های بعدیش گم نمی‌شن.

دانلود/Apify/فال‌بک صدا از gacha_insta_check.py و download_and_send3/4.py ایمپورت می‌شه.
"""

import argparse
import asyncio
import csv
import re
import subprocess
import sys
import os
import time
import unicodedata
import zlib
from datetime import datetime, timezone

from telethon.errors import FloodWaitError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import download_and_send3 as dsend3            # noqa: E402 — دانلود
import download_and_send4 as dsend4            # noqa: E402 — دیکشنری/تشخیص/ارسال زمان‌دار
import gacha_insta_check as base               # noqa: E402 — Apify fetch (با duration/thumb ویدیو)، ensure_audio، load/save

# ═══════════════════════════════════════════════════════════
# تنظیمات — فقط اینجا رو دستی ویرایش کن
# ═══════════════════════════════════════════════════════════
CONTENT_TAGS = 1                 # تعداد هشتگ محتوایی (Swimsuit / Duo / Wallpaper ...)
GAME_MIN_RATIO = 0.25            # حداقل نسبت ردیف‌های Game در دیکشنری برای «بازی» حساب‌شدن فرنچایز
MAX_SCHEDULED = 90               # سقف تلگرام ۱۰۰ پیام زمان‌بندی‌شده برای هر چته
SCHEDULE_CALL_GAP_SECONDS = 5.0

FOOTER = ""  # از batch تنظیم می‌شه

# هشتگ‌هایی که مخفف‌شون رو داخل دیکشنری ندارن → اسم رسمی فرنچایز توی CSV
# (فقط روی هشتگ‌های پست اعمال می‌شه، نه متن آزاد). اگه اسم توی CSV نباشه، نادیده گرفته می‌شه.
FRANCHISE_ALIASES = {
    'honkaiimpact3rd': 'Houkai 3rd',
    'honkaiimpact': 'Houkai 3rd',
    'hi3': 'Houkai 3rd',
    'hsr': 'Honkai Star Rail',
    'wuwa': 'Wuthering Waves',
    'zzz': 'Zenless Zone Zero',
    'fgo': 'Fate/Grand Order',
}

# اسم رسمی توی CSV (compact) → هشتگ دلخواه؛ بقیه خودکار ساخته می‌شن
FRANCHISE_TAG_OVERRIDES = {
    'houkai3rd': 'HonkaiImpact3rd',
}

# ─── قوانین محتوا (همون قوانین کانال؛ کلیدها compact: حروف کوچک بدون فاصله/علامت) ───
OFFICIAL = ['officialart', 'officialillustration']
WALLPAPER = ['desktopwallpaper', 'mobilewallpaper', 'wallpaper']

THEMES = [   # ترتیب = اولویت
    ('Swimsuit',      ['swimsuit', 'bikini', 'onepieceswimsuit', 'microbikini', 'whiteswimwear', 'schoolswimsuit']),
    ('Ballet',        ['ballet', 'balleroutfit', 'ballerinaoutfit']),
    ('Cheongsam',     ['cheongsam', 'qipao']),
    ('Hanfu',         ['hanfu', 'chineseclothes', 'changshan']),
    ('Miko',          ['miko']),
    ('Kimono',        ['kimono', 'yukata']),
    ('Maid',          ['maid', 'maidheaddress', 'maidapron']),
    ('Nun',           ['nunoutfit', 'nuncoif']),
    ('Cheerleader',   ['cheerleader']),
    ('SchoolUniform', ['schooluniform', 'sailorcollar', 'serafuku']),
    ('Wedding',       ['weddingdress', 'bride']),
    ('Christmas',     ['christmas', 'santahat', 'santacostume']),
    ('Halloween',     ['halloween', 'jackolantern']),
    ('Winter',        ['winter', 'snowing']),
    ('Autumn',        ['autumn', 'autumnleaves']),
    ('Sakura',        ['cherryblossom', 'cherryblossoms']),
    ('Chibi',         ['chibi']),
    ('Crossover',     ['crossover']),
    ('Skin',          ['officialalternatecostume']),
]

COMPOSITION = [   # فقط اولین مورد منطبق
    ('Siblings', ['siblings', 'brothers', 'brotherandsister', 'sisters']),
    ('Couple',   ['couple', 'hetero']),
    ('Trio',     ['trio', 'threegirls', '3girls', '3boys', 'threeboys']),
    ('Quartet',  ['quartet', 'fourgirls', '4girls', '4boys']),
    ('Duo',      ['duo', 'twogirls', '2girls', '2boys', 'twomales']),
    ('Group',    ['multiplegirls', 'multipleboys']),
    ('Portrait', ['portrait', 'closeup', 'faceportrait']),
]

FALLBACK_WORDS = ['Fanart', 'Fanart', 'Art', 'Illustration', 'Artwork']
# ═══════════════════════════════════════════════════════════
# پایان تنظیمات
# ═══════════════════════════════════════════════════════════


# ─────────────────────────────────────────
# دیکشنری: فقط فرنچایزهای بازی
# ─────────────────────────────────────────
def _load_game_keys() -> set:
    """فرنچایزی «بازی» حساب می‌شه که حداقل GAME_MIN_RATIO ردیف‌هاش classification=Game باشن
    (چون بعضی انیمه‌ها مثل One Piece چند ردیف Game هم دارن)."""
    total, game = {}, {}
    path = dsend4.DICTIONARY_PATH
    if not os.path.exists(path):
        return set()
    with open(path, encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            k = dsend4.compact(row.get('franchise'))
            if not k or k in dsend4.DENY:
                continue
            total[k] = total.get(k, 0) + 1
            if (row.get('classification') or '').strip().lower() == 'game':
                game[k] = game.get(k, 0) + 1
    return {k for k, n in game.items() if n / total[k] >= GAME_MIN_RATIO}


GAME_KEYS = _load_game_keys()
GAME_FRANCHISES_SORTED = [(k, n) for k, n in dsend4._DICT.franchise_sorted if k in GAME_KEYS]
ALIASES_RESOLVED = {
    alias: dsend4._DICT.franchise_map[dsend4.compact(target)]
    for alias, target in FRANCHISE_ALIASES.items()
    if dsend4.compact(target) in GAME_KEYS and dsend4.compact(target) in dsend4._DICT.franchise_map
}


def detect_game_franchises(hashtags: list, ngrams: set) -> list:
    """همون منطق امتیازدهی dsend4.detect_franchise ولی فقط روی فرنچایزهای Game، و لیست برمی‌گردونه.
    قاعده‌ی کراس‌اور: همه‌ی بازی‌هایی که حداقل یک هشتگ (امتیاز ≥ ۳) دارن میان.
    اگه هیچ‌کدوم هشتگ نداشت (فقط از متن)، بازی(های) با بیشترین امتیاز میان (مساوی = همه‌شون).
    ترتیب: امتیاز بیشتر اول؛ برابرها به ترتیب ظاهرشدن."""
    scores = {}
    for h in hashtags:
        if h in dsend4.GENERIC_HASHTAGS or h in dsend4.INSTAGRAM_TREND_HASHTAGS:
            continue
        if h in ALIASES_RESOLVED:
            name = ALIASES_RESOLVED[h]
            scores[name] = scores.get(name, 0) + 3
            continue
        for key, name in GAME_FRANCHISES_SORTED:
            if len(key) < dsend4.MIN_FRANCHISE_KEY_LEN:
                if key == h:
                    scores[name] = scores.get(name, 0) + 3
                    break
                continue
            is_contains = key in h
            is_prefix = len(h) >= dsend4.MIN_FRANCHISE_PREFIX_LEN and key.startswith(h)
            if is_contains or is_prefix:
                scores[name] = scores.get(name, 0) + 3
                break

    for ng in ngrams:
        if ng in GAME_KEYS:
            name = dsend4._DICT.franchise_map.get(ng)
            if name:
                scores[name] = scores.get(name, 0) + 1

    if not scores:
        return []
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])   # sorted پایداره → برابرها به ترتیب ظاهرشدن
    strong = [n for n, sc in ranked if sc >= 3]
    if strong:
        return strong
    top = ranked[0][1]
    return [n for n, sc in ranked if sc == top]


# ─────────────────────────────────────────
# ساخت هشتگ (قواعد کانال)
# ─────────────────────────────────────────
def _fold(s: str) -> str:
    s = unicodedata.normalize('NFD', str(s or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return s.replace('ł', 'l').replace('Ł', 'L')


def _pascal(s: str) -> str:
    parts = re.split(r'[^0-9A-Za-z]+', _fold(s))
    return ''.join(p[:1].upper() + p[1:] for p in parts if p)


def franchise_tag(name: str) -> str:
    override = FRANCHISE_TAG_OVERRIDES.get(dsend4.compact(name))
    if override:
        return override
    head, _, tail = str(name).partition(':')
    # «Arknights: Endfield» → ArknightsEndfield ؛ «NIKKE: The Goddess Of Victory» → NIKKE
    if tail.strip() and len(tail.split()) <= 1:
        return _pascal(head + ' ' + tail)
    if tail.strip() and len(_fold(head).strip()) < 4:      # «Re:Zero Kara ...» → ReZero (نه #Re)
        return _pascal(head + ' ' + tail.split()[0])
    return _pascal(head)


def character_tag(name: str) -> str:
    n = re.sub(r'\([^)]*\)', ' ', _fold(name))
    n = re.split(r'\s+the\s+', n, flags=re.I)[0]     # «Angelina the Mellow Wish» → Angelina
    return _pascal(n)


def content_candidates(keys: set) -> list:
    has = lambda lst: any(k in keys for k in lst)
    out = []
    if has(OFFICIAL):
        out.append('OfficialArt')
    if has(WALLPAPER):
        out.append('Wallpaper')
    for tag, lst in THEMES:
        if has(lst):
            out.append(tag)
    for tag, lst in COMPOSITION:
        if has(lst):
            out.append(tag)
            break
    return out


def build_tags(names: list) -> list:
    seen, tags = set(), []
    for t in names:
        h = re.sub(r'[^0-9A-Za-z\u00c0-\uffff]', '', str(t or ''))
        if not h or h.lower() in seen:
            continue
        seen.add(h.lower())
        tags.append('#' + h)
    if len(tags) < 2:
        tags.append('#GachaVerse')
    return tags


def build_game_caption(original_caption: str, shortcode: str):
    """خروجی: (caption_html یا None, دلیل برای لاگ)."""
    cleaned = dsend4.clean_text(original_caption)
    translated = dsend4.translate_to_english(cleaned) if cleaned.strip() else ''

    hashtags = dsend4.extract_hashtags_compact(original_caption)
    ngrams = dsend4.text_ngrams(cleaned)
    if translated and translated.strip().lower() != cleaned.strip().lower():
        ngrams |= dsend4.text_ngrams(translated)

    franchises = detect_game_franchises(hashtags, ngrams)
    
    # FALLBACK: اگر فرنچایز نشناخت → کپشن اصلی + فوتر
    if not franchises:
        caption = f"{original_caption}\n{FOOTER}"
        return caption, f'⚠️ فرنچایز شناخت‌نشده → send original caption'

    characters = []
    for fr in franchises:
        for c in dsend4.detect_characters(hashtags, ngrams, fr):
            if c not in characters:
                characters.append(c)

    keys = set(hashtags) | ngrams
    content = content_candidates(keys)[:CONTENT_TAGS]
    if not content:
        content = [FALLBACK_WORDS[zlib.crc32(shortcode.encode()) % len(FALLBACK_WORDS)]]

    tags = build_tags([franchise_tag(fr) for fr in franchises] + [character_tag(c) for c in characters] + content)
    caption = f"<blockquote>{' '.join(tags)}</blockquote>\n{FOOTER}"
    return caption, f'franchises={franchises!r} characters={characters!r} content={content!r}'


# ─────────────────────────────────────────
# git
# ─────────────────────────────────────────
def git_commit_and_push(pages_file: str, message: str) -> bool:
    """state/config را جدا کامیت و پوش می‌کند (بخش ۱.۴)."""
    import pages_store
    state = pages_file.replace(".json", "_state.json")
    return pages_store.git_commit_and_push([state, pages_file], message, "gachaverse-insta-bot")


# ─────────────────────────────────────────
# مرحله‌ی ۱: جمع‌آوری
# ─────────────────────────────────────────
def collect(pages: list) -> list:
    """هر پیج → {'page', 'entries': [{'post', 'caption'|None, 'status'}]} به ترتیب قدیمی→جدید.
    caption=None یعنی عمداً رد شده (state می‌تونه از روش رد بشه)."""
    plan = []
    for page in pages:
        username = page["username"]
        print(f"\n━━━ چک پیج: {username} ━━━")
        try:
            new_posts = base.fetch_new_posts(page)
        except Exception as e:
            print(f"⚠️ چک {username} شکست خورد ({e}) — رد شد، state دست‌نخورده می‌مونه")
            continue
        if not new_posts:
            print("ℹ️ پست جدیدی نیست")
            continue

        print(f"📬 {len(new_posts)} پست جدید")
        entries = []
        for post in new_posts:
            sc, items = post["shortcode"], post["media_items"]
            if not items:
                print(f"⏭️ {sc}: بدون رسانه — رد شد")
                entries.append({"post": post, "caption": None, "status": "skipped"})
                continue
            caption, why = build_game_caption(post["caption"], sc)
            if caption is None:
                print(f"⏭️ {sc}: {why}")
                entries.append({"post": post, "caption": None, "status": "skipped"})
                continue
            print(f"🏷️ {sc}: {why}")
            entries.append({"post": post, "caption": caption, "status": "pending"})
        plan.append({"page": page, "entries": entries})
    return plan


# ─────────────────────────────────────────
# مرحله‌ی ۲: زمان‌بندی
# ─────────────────────────────────────────
def send_entry(entry: dict, chat_id: str, when) -> list:
    """عکس/آلبوم عکس → همون مسیر aniwall. اگه ویدیو داشت → مسیر ویدیو (چک صدا با فال‌بک
    instaloader، کاور، attribute ویدیو) مثل gacha_insta_check."""
    post = entry["post"]
    media_items = post["media_items"]
    base.reset_workdir()
    files = dsend3.download_media_direct(media_items)

    has_video = any((i.get("type") or "").lower() == "video" for i in media_items)
    if not has_video:
        return asyncio.run(
            dsend4.send_photo_album_scheduled(files, entry["caption"], chat_id, schedule_dt=when)
        )

    files_after, replaced = base.ensure_audio(files, post["url"], post["shortcode"])
    if files_after is None:
        # فال‌بک fail شد و workdir پاک شده بود → دوباره از CDN
        files = dsend3.download_media_direct(media_items)
    else:
        files = files_after
    thumb = dsend3.prepare_thumb(media_items, files)
    # اگه فایل از instaloader اومده متادیتای Apify معتبر نیست؛ video_attributes از خود فایل می‌خونه
    video_meta = None if replaced else next(
        (i for i in media_items if (i.get("type") or "").lower() == "video"), None
    )
    return asyncio.run(
        dsend3.send_items_to_telegram(files, entry["caption"], chat_id, thumb, video_meta, when)
    )


def schedule_all(plan: list, chat_id: str) -> bool:
    """خروجی: True اگه FloodWait خورد."""
    sendable = [e for p in plan for e in p["entries"] if e["status"] == "pending"]
    if not sendable:
        print("\nℹ️ هیچ پستی برای زمان‌بندی نبود")
        return False
    if len(sendable) > MAX_SCHEDULED:
        print(f"⚠️ {len(sendable)} پست آماده‌ست؛ فقط {MAX_SCHEDULED} تای اول (سقف Scheduled تلگرام) — بقیه دفعه‌ی بعد")
        sendable = sendable[:MAX_SCHEDULED]

    times = dsend4.compute_schedule_times(datetime.now(timezone.utc), len(sendable))
    print(f"\n🗓️ {len(sendable)} پست، بین {times[0]:%H:%M} تا {times[-1]:%H:%M} UTC")

    flood = False
    for entry, when in zip(sendable, times):
        sc = entry["post"]["shortcode"]
        try:
            sent = send_entry(entry, chat_id, when)
            ids = ", ".join(str(s.id) for s in sent)
            print(f"✅ {sc} زمان‌بندی شد برای {when:%Y-%m-%d %H:%M} UTC (message_id(s)={ids})")
            entry["status"] = "sent"
        except FloodWaitError as e:
            print(f"⏳ FloodWait: {e.seconds} ثانیه — توقف کل ران")
            flood = True
            break
        except BaseException as e:   # SystemExit از دانلود هم شکست حساب می‌شه
            if isinstance(e, KeyboardInterrupt):
                raise
            print(f"❌ {sc} شکست خورد ({e}) — دفعه‌ی بعد retry می‌شه")
            entry["status"] = "failed"
        time.sleep(SCHEDULE_CALL_GAP_SECONDS)
    return flood


def advance_state(plan: list):
    """state هر پیج فقط تا آخرین پستِ پشت‌سرهمِ «sent/skipped» جلو می‌ره."""
    for p in plan:
        page = p["page"]
        for e in p["entries"]:
            if e["status"] not in ("sent", "skipped"):
                break
            page["last_post_id"] = e["post"]["shortcode"]
            page["last_post_taken_at"] = e["post"]["takenAt"]


# ─────────────────────────────────────────
# main
# ─────────────────────────────────────────
def main():
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages-file", default="gachaverse_pages.json")
    parser.add_argument("--chat-id", required=True)
    args = parser.parse_args()

    if not base.APIFY_TOKEN:
        print("❌ APIFY_TOKEN تنظیم نشده")
        sys.exit(1)
    if not (dsend3.TG_API_ID and dsend3.TG_API_HASH and dsend3.TG_SESSION_STRING):
        print("❌ TG_API_ID / TG_API_HASH / TG_SESSION_STRING تنظیم نشده")
        sys.exit(1)
    if not GAME_KEYS:
        print("❌ دیکشنری بازی‌ها خالیه (character_dictionary.csv / ستون classification)")
        sys.exit(1)

    pages = base.load_pages(args.pages_file)
    if not pages:
        print(f"ℹ️ {args.pages_file} خالیه — هیچ پیجی برای چک کردن نیست")
        return

    plan = collect(pages)
    flood = schedule_all(plan, args.chat_id)
    advance_state(plan)

    base.save_pages(args.pages_file, pages)
    git_commit_and_push(args.pages_file, "gachaverse-insta: check + schedule")

    print("\n✅ تمام شد")
    if flood:
        sys.exit(2)


if __name__ == "__main__":
    main()
