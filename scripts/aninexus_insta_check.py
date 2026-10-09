#!/usr/bin/env python3
"""
aninexus_insta_check.py — چک شبانه‌ی پیج‌های اینستاگرام برای کانال ANINEXUS (AniNexus_Lab).

برخلاف نسخه‌ی اول، اینجا دو مرحله‌ایه:
  ۱) همه‌ی پیج‌های aninexus_pages.json چک می‌شن و هرچی پست جدید هست جمع می‌شه
     (بدون ارسال فوری، بدون توجه به ترتیب بین پیج‌ها).
  ۲) بر اساس تعداد کل پست‌های جمع‌شده، هر کدوم با قابلیت schedule تلگرام توی
     یه بازه‌ی ۲۴ساعته پخش می‌شن: از ۱ ساعت بعد از همین اجرا تا ۲ ساعت قبل
     از اجرای فردا شب. این یعنی بین ۱۲ شب (وقتی اسکریپت اجرا می‌شه) تا ۱ شب،
     و همچنین از ۱۰ شب تا ۱۲ شب، هیچ پستی زمان‌بندی نمی‌شه — تا وقت داشته
     باشی قبل از هر انتشار، از بخش Scheduled Messages تلگرام پست‌ها رو ببینی
     و در صورت نیاز دخالت کنی.

تریگر این اسکریپت کاملاً دستیه (نه cron) — خود کاربر هر شب ساعت ۱۲ صداش می‌زنه.

تغییر نسبت به نسخه‌ی aniwall:
  - هیچ پستی به‌خاطر نوع (عکس/ویدیو) یا نبودن هشتگ مرتبط رد نمی‌شه — فقط پست
    بدون رسانه (که چیزی برای ارسال نداره) رد می‌شه.
  - کپشن نهایی = کپشن اینستاگرام (داخل blockquote بازشونده) + بلوک هشتگِ همون
    منطق قبلی (dsend4.build_hashtag_caption) + فوتر THE LABS. اگه هشتگی تشخیص
    داده نشد، #AnimeArt.

پروفایل پیج‌ها (scripts/page_profiles.py):
  هر پیج توی aninexus_pages.json می‌تونه کلید "profile" داشته باشه. پیج بدون پروفایل
  (یا "default") دقیقاً مثل قبل کپشن هشتگی می‌گیره. پروفایل ناشناخته = پیج رد می‌شه و
  state دست‌نخورده می‌مونه. کلیدهای پیاده‌شده اینجا: drop_first/drop_last/drop_min_items،
  keep_lines، strip_blocks، split_title، own_hashtags، raw_caption، drop_text،
  blocked_hashtags، extra_hashtags، fixed_caption، continuation_caption، text_replace،
  و کلیدهای جدید:
    nfkc                    : کپشن قبل از هر پردازشی NFKC می‌شه (حروف بولد یونیکد «𝗣𝗹𝗼𝘁» ← «Plot»)
    skip_if_caption_matches : لیست regex؛ اگه کپشن خام با هرکدوم بخوره، کل پست رد می‌شه
    skip_if_all_cjk         : کل کپشن غیرلاتین (ژاپنی/چینی/کره‌ای) ← پست رد می‌شه
    skip_if_ad              : عبارت‌های تبلیغیِ صریح (AD_PATTERNS + ad_patterns_extra) ← پست رد می‌شه
    strip_label             : regex که از ابتدای متن (بعد از عنوان) حذف می‌شه (مثل «Plot:»)
    tags_from_field         : regex با یک گروه؛ مقدارش (مثل اسم انیمه از «Anime: X») ← یک هشتگ
    hashtag_fallback        : 'dictionary' ← اگه هشتگی نبود، فرنچایز/شخصیت از متن با دیکشنری
    fallback_hashtag        : هشتگ ثابتِ آخرین راه (وقتی هیچ‌کدوم از روش‌های بالا هشتگی نساخت)

هیچ منطق دانلود/ورد نویسی از صفر نیست: از download_and_send3.py (دانلود،
entity resolution) و download_and_send4.py (کپشن هشتگی + ارسال زمان‌دار)
import می‌شه.
"""

import argparse
import asyncio
import html
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone

import requests
from telethon.errors import FloodWaitError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import download_and_send3 as dsend3          # noqa: E402 — دانلود/entity resolution
import download_and_send4 as dsend4          # noqa: E402 — کپشن هشتگی/ارسال زمان‌دار
import gacha_insta_check as base             # noqa: E402 — ensure_audio (فال‌بک صدا با instaloader)

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "").strip()
MAX_PER_PAGE = 8
APIFY_RESULTS_LIMIT = 12
CAPTION_LIMIT = 1024              # سقف کپشن تلگرام (کاراکتر قابل‌نمایش)
SCHEDULE_CALL_GAP_SECONDS = 5.0   # فاصله‌ی واقعی بین هر schedule call (محض اطمینان، نه فاصله‌ی نمایش)

CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")

# فقط عبارت‌های تبلیغیِ صریح؛ منشن یا کلمه‌ی تکی (affiliate، pre-order، collab) عمداً نیست
AD_PATTERNS = [
    r"\blink\s+in\s+(?:(?:@[\w.]+(?:'s|’s)?|the|my|our|their)\s+)*bio\b",
    r"\bbio\s+link\b",
    r"\blink\s+in\s+(?:the\s+)?(?:comments?|description|profile)\b",
    r"\bsign\s*up\s+for\s+(?:drop|release|early|updates|the\s+waitlist|our)\b",
    r"\b(?:use|with)\s+(?:the\s+)?(?:code|coupon|promo)\b",
    r"\b(?:promo|discount|coupon)\s+code\b",
    r"\b\d{1,2}\s?%\s+off\b",
    r"\b(?:shop|buy|order)\s+now\b",
    r"\bgiveaway\b",
    r"\bpaid\s+partnership\b|\bsponsored\b|#ad\b|#sponsored\b",
]


def skip_reason(raw: str, profile: dict):
    """None = پست برود. در غیر این صورت دلیل رد (برای لاگ)."""
    raw = raw or ""
    if profile.get("nfkc"):
        raw = unicodedata.normalize("NFKC", raw)

    for pat in profile.get("skip_if_caption_matches", ()):
        m = re.search(pat, raw, re.I | re.M)
        if m:
            return f"skip_if_caption_matches «{m.group(0)[:40]}»"

    if profile.get("skip_if_all_cjk"):
        plain = re.sub(r"#\w+|@[\w.]+|https?://\S+", " ", raw)
        if CJK_RE.search(plain) and not re.search(r"[A-Za-z]{2,}", plain):
            return "کل کپشن غیرلاتین"

    if profile.get("skip_if_ad"):
        for pat in AD_PATTERNS + list(profile.get("ad_patterns_extra", ())):
            m = re.search(pat, raw, re.I | re.M)
            if m:
                return f"تبلیغ «{m.group(0)[:50]}»"
    return None


# ─────────────────────────────────────────
# خوندن/نوشتن aninexus_pages.json
# ─────────────────────────────────────────
def load_pages(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_pages(path: str, pages: list):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pages, f, ensure_ascii=False, indent=2)
        f.write("\n")


def git_commit_and_push(pages_file: str, message: str) -> bool:
    try:
        subprocess.run(["git", "config", "user.name", "aninexus-insta-bot"], check=True)
        subprocess.run(["git", "config", "user.email", "aninexus-insta-bot@users.noreply.github.com"], check=True)
        subprocess.run(["git", "add", pages_file], check=True)

        diff = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if diff.returncode == 0:
            print("ℹ️ تغییری برای کامیت نبود.")
            return True

        subprocess.run(["git", "commit", "-m", message], check=True)
        subprocess.run(["git", "pull", "--rebase", "--autostash"], check=False)
        subprocess.run(["git", "push"], check=True)
        print(f"✅ کامیت و پوش شد: {message}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ git commit/push شکست خورد: {e}")
        return False


# ─────────────────────────────────────────
# Apify — گرفتن پست‌های اخیر یه پیج
# ─────────────────────────────────────────
def to_js_iso(ts: int) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def call_apify(username: str, newer_than_ts: int):
    body = {
        "username": [username],
        "resultsLimit": APIFY_RESULTS_LIMIT,
        "skipPinnedPosts": True,
    }
    if newer_than_ts:
        body["onlyPostsNewerThan"] = to_js_iso(newer_than_ts)

    resp = requests.post(
        "https://api.apify.com/v2/acts/apify~instagram-post-scraper/run-sync-get-dataset-items",
        params={"token": APIFY_TOKEN},
        json=body,
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()


def is_pinned(post: dict) -> bool:
    for key in ("pinned", "isPinned", "isPin"):
        if post.get(key):
            return True
    return False


def extract_media(node: dict) -> list:
    if not node:
        return []
    if node.get("videoUrl"):
        item = {"url": node["videoUrl"], "type": "video"}
        if node.get("displayUrl"):
            item["thumb"] = node["displayUrl"]
        if node.get("videoDuration"):
            item["duration"] = node["videoDuration"]
        return [item]
    if node.get("displayUrl"):
        return [{"url": node["displayUrl"], "type": "photo"}]
    return []


def build_media_items(post: dict) -> list:
    child = post.get("childPosts")
    if isinstance(child, list) and child:
        out = []
        for c in child:
            out.extend(extract_media(c))
        return out
    return extract_media(post)


def fetch_new_posts(page: dict) -> list:
    """پست‌های جدید یه پیج (قدیمی→جدید، حداکثر MAX_PER_PAGE تا)."""
    raw = call_apify(page["username"], page.get("last_post_taken_at") or 0)
    posts = [
        p for p in raw
        if p.get("shortCode") and p.get("timestamp") and not is_pinned(p)
        and p["shortCode"] != page.get("last_post_id")
    ]
    if not posts:
        return []

    def ts(p):
        return int(datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00")).timestamp())

    posts.sort(key=ts)

    last_taken_at = int(page.get("last_post_taken_at") or 0)
    if not last_taken_at:
        new_posts = posts[-1:]  # اولین اجرا برای این پیج: فقط آخرین پست
    else:
        new_posts = [p for p in posts if ts(p) > last_taken_at]

    if len(new_posts) > MAX_PER_PAGE:
        new_posts = new_posts[-MAX_PER_PAGE:]

    return [
        {
            "shortcode": p["shortCode"],
            "takenAt": ts(p),
            "caption": p.get("caption") or "",
            "url": f"https://www.instagram.com/p/{p['shortCode']}/",
            "media_items": build_media_items(p),
        }
        for p in new_posts
    ]


def reset_workdir():
    import shutil
    shutil.rmtree(dsend3.WORKDIR, ignore_errors=True)
    os.makedirs(dsend3.WORKDIR, exist_ok=True)


# ─────────────────────────────────────────
# کپشن نهایی: کپشن اینستا + هشتگ‌ها
# ─────────────────────────────────────────
def _u16len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _truncate_text(text: str, max_len: int) -> str:
    if _u16len(text) <= max_len:
        return text
    cut = text
    while _u16len(cut) > max_len - 1:
        cut = cut[:-1]
    space = cut.rfind(" ")
    nl = cut.rfind("\n")
    edge = max(space, nl)
    if edge > len(cut) * 0.6:
        cut = cut[:edge]
    return cut.rstrip() + "…"


FOOTER_HTML = (
    "𝗧𝗛𝗘 𝗟𝗔𝗕𝗦 ▸\n"
    '├─ 🎴 <a href="https://t.me/+DaLBmQfNUvU0Y2M0">𝑨𝒏𝒊𝑾𝒂𝒍𝒍 𝑳𝒂𝒃</a>\n'
    '├─ 🪽 <a href="https://t.me/+jGEAdMPw6uoyZTg8">𝑾𝒂𝒊𝒇𝒖 𝑳𝒂𝒃</a>\n'
    '└─ 🌌 <a href="https://t.me/+5NUPsURo5XBkOTg0">𝑨𝒏𝒊𝑵𝒆𝒙𝒖𝒔 𝑳𝒂𝒃</a>'
)


def _visible_len(html_text: str) -> int:
    return _u16len(html.unescape(re.sub(r"<[^>]+>", "", html_text)))


def build_full_caption(original_caption: str, hashtag_html: str) -> str:
    """کپشن اینستا (بازشونده) + بلوک هشتگ + فوتر، با رعایت سقف ۱۰۲۴ کاراکتر تلگرام."""
    tail = f"{hashtag_html}\n\n{FOOTER_HTML}"
    body = re.sub(r"\n{3,}", "\n\n", (original_caption or "").strip())
    if not body:
        return tail

    budget = CAPTION_LIMIT - _visible_len(tail) - 2 - 10   # 2 = دو خط جدا، 10 = حاشیه‌ی امن
    body = _truncate_text(body, max(budget, 50))
    return f"<blockquote expandable>{html.escape(body, quote=False)}</blockquote>\n\n{tail}"


async def send_scheduled(files, caption, chat_id, schedule_dt, thumb=None, video_meta=None,
                         continuation_caption=None):
    """ارسال زمان‌بندی‌شده‌ی عکس/ویدیو (تکی یا آلبوم) — همون منطق نسخه‌ی ۳:
      • بیش از ۱۰ فایل → آلبوم‌های هم‌اندازه (هیچ‌وقت آلبوم تک‌فایلی نمی‌سازه: ۱۱ → ۶+۵)
      • هر آلبوم ۱۰ ثانیه بعد از قبلی زمان‌بندی می‌شه تا ترتیبشون توی کانال حفظ بشه
        (قبلاً همه با یک زمان ثابت می‌رفتن و «Continued» قبل از آلبوم اصلی منتشر می‌شد)
      • آلبوم دارای ویدیو با مدت/ابعاد/کاور واقعی ارسال می‌شه (بدون این، جعبه‌ی سیاه/۰۰:۰۰)
    """
    from datetime import timedelta
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.types import DocumentAttributeFilename

    client = TelegramClient(StringSession(dsend3.TG_SESSION_STRING), int(dsend3.TG_API_ID), dsend3.TG_API_HASH)
    await client.connect()
    try:
        chat_value = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
        try:
            chat_value = await client.get_input_entity(chat_value)
        except ValueError:
            await client.get_dialogs()
            chat_value = await client.get_input_entity(chat_value)

        sent_all = []
        if len(files) == 1:
            f = files[0]
            kwargs = {}
            if f.lower().endswith(".mp4"):
                attributes = [DocumentAttributeFilename(os.path.basename(f))]
                meta = video_meta[0] if isinstance(video_meta, (list, tuple)) and video_meta else video_meta
                vattr = dsend3.video_attributes(f, meta if isinstance(meta, dict) else None, thumb)
                if vattr:
                    attributes.append(vattr)
                kwargs["attributes"] = attributes
                if thumb:
                    kwargs["thumb"] = thumb
            sent = await client.send_file(
                chat_value, f, caption=caption, parse_mode=dsend3.pick_parse_mode(caption),
                supports_streaming=True, schedule=schedule_dt, **kwargs,
            )
            sent_all.extend(sent if isinstance(sent, list) else [sent])
            return sent_all

        chunks = dsend3.balanced_chunks(files, dsend3.MAX_ALBUM_SIZE)
        vids_all = [x for x in files if x.lower().endswith(".mp4")]
        if isinstance(video_meta, (list, tuple)) and len(video_meta) == len(vids_all):
            meta_by_path = dict(zip(vids_all, video_meta))
        elif isinstance(video_meta, dict) and vids_all:
            meta_by_path = {vids_all[0]: video_meta}
        else:
            meta_by_path = {}
        thumb_by_path = {vids_all[0]: thumb} if (thumb and vids_all) else {}
        print(f"   📚 {len(files)} فایل → {len(chunks)} آلبوم: {[len(c) for c in chunks]}")

        for idx, chunk in enumerate(chunks):
            cap = caption if idx == 0 else (continuation_caption or "")
            when = schedule_dt + timedelta(seconds=10 * idx)
            sent = None
            if any(x.lower().endswith(".mp4") for x in chunk):
                try:
                    sent = await dsend3._send_album_with_video_attrs(
                        client, chat_value, chunk, cap, when, thumb_by_path, meta_by_path)
                except FloodWaitError:
                    raise
                except Exception as e:
                    warn(f"آلبوم {idx + 1} با مشخصات ویدیو نشد ({type(e).__name__}: {e}) — آلبوم ساده فرستاده می‌شه")
                    sent = None
            if not sent:
                sent = await client.send_file(
                    chat_value, chunk, caption=cap, parse_mode=dsend3.pick_parse_mode(cap),
                    supports_streaming=True, schedule=when,
                )
            sent = sent if isinstance(sent, list) else [sent]
            if len(sent) != len(chunk):
                warn(f"آلبوم {idx + 1}: {len(chunk)} فایل رفت ولی {len(sent)} پیام برگشت — آلبوم ممکنه ناقص باشه")
            print(f"   📦 آلبوم {idx + 1}/{len(chunks)}: {len(chunk)} فایل، زمان {when.strftime('%H:%M:%S UTC')}")
            sent_all.extend(sent)
        return sent_all
    finally:
        await client.disconnect()


# ─────────────────────────────────────────
# کپشن بر اساس پروفایل پیج
# ─────────────────────────────────────────
PROFILE_CAPTION_KEYS = ("keep_lines", "own_hashtags", "raw_caption", "drop_text", "fixed_caption",
                        "tags_from_field", "fallback_hashtag")


def uses_profile_caption(profile: dict) -> bool:
    """True = این پیج کپشن اختصاصی داره؛ False = همون کپشن هشتگیِ قبلی."""
    return any(profile.get(k) for k in PROFILE_CAPTION_KEYS)


def _hashtag_block(tags: list) -> str:
    return f"<blockquote>💠 {' '.join(tags)}</blockquote>" if tags else ""


_LATIN_TAG_RE = re.compile(r"[A-Za-z0-9_\u00C0-\u024F]+")


def _latin_tag(h: str) -> str:
    """هشتگ → فقط بخش لاتین ابتدای آن. «#millenniumparade好きな人と繋がりたい» → «millenniumparade»؛
    هشتگ کاملاً غیرلاتین (مثل #好きな人と繋がりたい) → '' (حذف می‌شه). کمتر از ۲ کاراکتر هم حذف."""
    m = _LATIN_TAG_RE.match(h.lstrip("#"))
    tag = m.group(0) if m else ""
    return tag if len(tag) >= 2 else ""


def _own_hashtags(caption: str, profile: dict) -> list:
    """هشتگ‌های خود پیج (از هر جای کپشن، بدون تکرار، فقط لاتین) − blocked + extra."""
    blocked = {str(b).lstrip("#").lower() for b in (profile.get("blocked_hashtags") or ())}
    out, seen = [], set()
    for h in dsend3.extract_hashtags(caption):
        tag = _latin_tag(h)
        key = tag.lower()
        if key and key not in blocked and key not in seen:
            seen.add(key)
            out.append("#" + tag)
    for h in (profile.get("extra_hashtags") or ()):
        key = str(h).lstrip("#").lower()
        if key and key not in seen:
            seen.add(key)
            out.append("#" + str(h).lstrip("#"))
    return out


def _field_tags(raw: str, profile: dict) -> list:
    """tags_from_field: مقدار یک خط (مثل «Anime: X») ← یک هشتگ."""
    m = re.search(profile["tags_from_field"], raw, re.I | re.M)
    if not m:
        return []
    name = m.group(1).strip()
    if len(re.findall(r"[A-Za-z0-9]", name)) < 2:       # «-» و «N/A» و مشابه
        return []
    tag = dsend4.to_hashtag(name)
    return ["#" + tag] if tag else []


def _dictionary_tags(raw: str) -> list:
    """فرنچایز + حداکثر ۴ شخصیت از روی متن (فقط وقتی فرنچایز تشخیص داده شد)."""
    cleaned = dsend4.clean_text(raw)
    ngrams = dsend4.text_ngrams(cleaned)
    fr = dsend4.detect_franchise([], ngrams, dsend4.text_ngrams(dsend4._title_text(cleaned)))
    if not fr:
        return []
    chars = dsend4.detect_characters([], ngrams, fr)[:4]
    names = [dsend4._DICT.franchise_display.get(fr) or fr] + chars
    tags = dsend4.dedupe_tags([dsend4.to_hashtag(n) for n in names if n], protect=len(names))
    return ["#" + t for t in tags if t]


_TRAILING_TAGS_RE = re.compile(r"(?:[ \t]*#\w+)+[ \t]*$")


def _strip_hashtags(text: str) -> str:
    """هشتگ‌های *انتهای خط* (بلوک هشتگ) حذف می‌شن؛ هشتگی که وسط جمله اومده فقط # ش برداشته می‌شه
    و کلمه می‌مونه («#BREAKING: X» → «BREAKING: X»، نه «: X»). علامت‌های یتیم ابتدای متن هم پاک می‌شن."""
    lines = []
    for line in (text or "").split("\n"):
        head = _TRAILING_TAGS_RE.sub("", line)
        head = dsend3.HASHTAG_RE.sub(lambda m: m.group(0)[1:], head)
        lines.append(head)
    t = "\n".join(lines)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"^[\s:;,\-–—|]+", "", t)
    return t.strip()


def _clean_raw(caption: str) -> str:
    """raw_caption: بلوک هشتگ برداشته می‌شه (چون جدا می‌ره)، بقیه‌ی متن دست‌نخورده."""
    return _strip_hashtags(caption)


def _keep_lines(text: str, patterns) -> str:
    regs = [re.compile(p, re.I) for p in patterns]
    return "\n".join(ln.strip() for ln in (text or "").splitlines() if any(r.search(ln) for r in regs))


def _compose_caption(title: str, body: str, tags: list) -> str:
    """[عنوان bold] + [blockquote متن] + [blockquote هشتگ] + فوتر، با سقف ۱۰۲۴."""
    title_part = f"<b>{html.escape(title, quote=False)}</b>\n\n" if title else ""

    def tail(tg):
        blk = _hashtag_block(tg)
        return (blk + "\n\n" if blk else "") + FOOTER_HTML

    # هشتگ‌ها اون‌قدر زیاد بودن که جا برای متن نمونده → از آخر کم می‌شن
    need = 100 if body else 0
    while tags and (CAPTION_LIMIT - _visible_len(title_part + tail(tags)) - 12) < need:
        tags = tags[:-1]

    body_part = ""
    if body:
        budget = CAPTION_LIMIT - _visible_len(title_part + tail(tags)) - 2 - 10
        body = _truncate_text(body, max(budget, 50))
        body_part = f"<blockquote expandable>{html.escape(body, quote=False)}</blockquote>\n\n"
    return title_part + body_part + tail(tags)


def build_profile_caption(caption_raw: str, profile: dict):
    """خروجی: (کپشن اول HTML, کپشن آلبوم‌های بعدی یا None, دلیل برای لاگ)."""
    caption_raw = caption_raw or ""
    raw_mode = bool(profile.get("raw_caption"))
    title = body = ""
    tags = []
    fixed = (profile.get("fixed_caption") or "").strip()

    if profile.get("drop_text"):
        pass
    elif fixed:
        toks = fixed.split()
        if toks and all(t.startswith("#") for t in toks):
            tags = toks
        else:
            body = fixed
    elif raw_mode:
        body = _clean_raw(caption_raw)
    else:
        text = dsend3.strip_caption_blocks(caption_raw, profile.get("strip_blocks"))
        if profile.get("keep_lines"):
            text = _keep_lines(text, profile["keep_lines"])
        body = _strip_hashtags(text)
        if body and profile.get("split_title", True):
            title, body = dsend3.split_title(body)
        if body and profile.get("strip_label"):
            body = re.sub(profile["strip_label"], "", body, count=1, flags=re.I).strip()

    if profile.get("own_hashtags") or raw_mode:
        tags = _own_hashtags(caption_raw, profile)
    if not tags and profile.get("tags_from_field"):
        tags = _field_tags(caption_raw, profile)
    if not tags and profile.get("hashtag_fallback") == "dictionary" and not raw_mode:
        tags = _dictionary_tags(caption_raw)
    if not tags and profile.get("fallback_hashtag"):
        tags = ["#" + str(profile["fallback_hashtag"]).lstrip("#")]

    cont = (profile.get("continuation_caption") or "").strip()
    continuation = html.escape(cont, quote=False) if cont else None
    why = (f"پروفایل: عنوان={'✓' if title else '✗'} متن={'✓' if body else '✗'} "
           f"هشتگ={len(tags)}{' | کپشن ادامه ✓' if continuation else ''}")
    return _compose_caption(title, body, tags), continuation, why


WARNINGS: list = []   # همه‌ی هشدارهای ران؛ آخر ران با ::warning:: توی خلاصه‌ی GitHub Actions چاپ می‌شن


def warn(msg: str):
    WARNINGS.append(msg)
    print(f"   ⚠️ {msg}")


def lint_caption(raw: str, caption_html: str, profile: dict) -> list:
    """کپشن نهایی رو چک می‌کنه و لیست مشکل‌های مشکوک رو برمی‌گردونه (چیزی رو تغییر نمی‌ده)."""
    out = []
    body_m = re.search(r"<blockquote expandable>(.*?)</blockquote>", caption_html, re.S)
    body = html.unescape(re.sub(r"<[^>]+>", "", body_m.group(1))).strip() if body_m else ""
    if body and re.match(r"^[:;,.\-–—|)\]}]", body):
        out.append(f"متن با علامت شروع می‌شه: {body[:25]!r}")
    if (raw or "").strip() and not body and not (profile.get("drop_text") or profile.get("fixed_caption")):
        out.append("کپشن اصلی متن داشت ولی متن نهایی خالیه (keep_lines/strip_blocks زیادی حذف کرد؟)")
    tags_m = re.findall(r"#(\w+)", re.sub(r"<[^>]+>", " ", caption_html.replace(FOOTER_HTML, "")))
    bad = [t for t in tags_m if not _LATIN_TAG_RE.fullmatch(t)]
    if bad:
        out.append(f"هشتگ غیرلاتین توی کپشن مونده: {bad[:3]}")
    if _u16len(html.unescape(re.sub(r"<[^>]+>", "", caption_html))) > CAPTION_LIMIT:
        out.append("کپشن از سقف ۱۰۲۴ بیشتره")
    return out


def make_caption(raw: str, profile: dict):
    """ساخت کپشن (مشترک بین ران واقعی و --preview). خروجی: (caption, continuation, why)."""
    raw = raw or ""
    if profile.get("nfkc"):
        raw = unicodedata.normalize("NFKC", raw)
    # قانون‌های دستی هر پروفایل: "text_replace": [["regex", "جایگزین"], ...] — برای مورد غیرمنتظره بدون دست‌زدن به کد
    for pat, repl in (profile.get("text_replace") or ()):
        raw = re.sub(pat, repl, raw, flags=re.I | re.M)
    continuation = None
    if uses_profile_caption(profile):
        caption, continuation, why = build_profile_caption(raw, profile)
    else:
        hashtag_html, why = dsend4.build_hashtag_caption(raw)
        if hashtag_html is None:
            hashtag_html = f"<blockquote>💠 #{dsend4.FALLBACK_TAG}</blockquote>"
            why = f"هشتگ مرتبطی پیدا نشد — فالبک #{dsend4.FALLBACK_TAG}"
        caption = build_full_caption(raw, hashtag_html)
    return caption, continuation, why


def safe_make_caption(raw: str, profile: dict, label: str):
    """اگه ساخت کپشن خطا داد، کل ران نمی‌خوابه: کپشن امن (فقط هشتگ فالبک) ساخته می‌شه + هشدار."""
    try:
        caption, continuation, why = make_caption(raw, profile)
    except Exception as e:
        warn(f"{label}: ساخت کپشن خطا داد ({type(e).__name__}: {e}) — کپشن خام + #{dsend4.FALLBACK_TAG} فرستاده می‌شه")
        tag_html = f"<blockquote>💠 #{dsend4.FALLBACK_TAG}</blockquote>"
        try:
            # کپشن خام اینستا (بازشونده، با سقف ۱۰۲۴) + هشتگ فالبک + فوتر
            caption = build_full_caption(raw, f"{tag_html}\n\n{FOOTER_HTML}")
        except Exception as e2:
            warn(f"{label}: کپشن خام هم نشد ({type(e2).__name__}: {e2}) — فقط هشتگ + فوتر")
            caption = f"{tag_html}\n\n{FOOTER_HTML}"
        return caption, None, "فالبک بعد از خطا (کپشن خام)"
    for w in lint_caption(raw, caption, profile):
        warn(f"{label}: {w}")
    visible = html.unescape(re.sub(r"<[^>]+>", "", caption.replace(FOOTER_HTML, ""))).strip()
    print(f"   📝 خام  : {(raw or '')[:160]!r}")
    print(f"   📤 نهایی: {visible[:160]!r}")
    return caption, continuation, why


# ─────────────────────────────────────────
# مرحله‌ی ۱: جمع‌آوری همه‌ی پست‌های قابل‌ارسالِ همه‌ی پیج‌ها
# ─────────────────────────────────────────
def collect_sendable_posts(pages: list) -> list:
    """خروجی: لیستی از {"page": page_dict, "post": post_dict, "caption": html}
    همه‌ی پست‌های دارای رسانه (عکس یا ویدیو) که طبق قواعد پروفایل رد نشده‌ن."""
    to_send = []
    for page in pages:
        username = page["username"]
        profile_name = (page.get("profile") or "").strip()
        print(f"\n━━━ چک پیج: {username} (profile={profile_name or 'default'}) ━━━")
        try:
            profile = dsend3.resolve_profile(profile_name)
        except KeyError as e:
            print(f"❌ {e} — پیج رد شد، state دست‌نخورده می‌مونه")
            continue
        try:
            new_posts = fetch_new_posts(page)
        except Exception as e:
            print(f"⚠️ چک {username} شکست خورد ({e}) — رد شد، state دست‌نخورده می‌مونه")
            continue

        if not new_posts:
            print("ℹ️ پست جدیدی نیست")
            continue

        print(f"📬 {len(new_posts)} پست جدید")
        for post in new_posts:
            shortcode = post["shortcode"]

            reason = skip_reason(post["caption"], profile)
            if reason:
                print(f"🚫 {shortcode}: رد شد — {reason}")
                page["last_post_id"] = shortcode
                page["last_post_taken_at"] = post["takenAt"]
                continue

            post["orig_count"] = len(post["media_items"])   # برای اعمال دوباره‌ی drop روی فایل‌های فال‌بک صدا
            media_items = dsend3.apply_media_drops(post["media_items"], profile)   # drop_first/drop_last
            post["media_items"] = media_items

            if not media_items:
                print(f'⏭️ {shortcode}: بدون رسانه — رد شد (ولی state جلو می‌ره)')
                page["last_post_id"] = shortcode
                page["last_post_taken_at"] = post["takenAt"]
                continue

            caption, continuation, why = safe_make_caption(post["caption"], profile, f"{username}/{shortcode}")
            kinds = ",".join(sorted({(i.get("type") or "").lower() for i in media_items}))
            print(f'🏷️ {shortcode} [{kinds}]: {why}')
            to_send.append({"page": page, "post": post, "caption": caption, "continuation": continuation,
                            "profile": profile})

    return to_send


# ─────────────────────────────────────────
# مرحله‌ی ۲: زمان‌بندی و ارسال
# ─────────────────────────────────────────
def schedule_all(to_send: list, chat_id: str):
    if not to_send:
        print("\nℹ️ هیچ پستی برای زمان‌بندی نبود")
        return

    now = datetime.now(timezone.utc)
    times = dsend4.compute_schedule_times(now, len(to_send))

    print(f"\n🗓️ {len(to_send)} پست، زمان‌بندی بین "
          f"{times[0].strftime('%H:%M UTC')} تا {times[-1].strftime('%H:%M UTC')}")

    for item, when in zip(to_send, times):
        page = item["page"]
        post = item["post"]
        shortcode = post["shortcode"]
        try:
            reset_workdir()
            files = dsend3.download_media_direct(post["media_items"])

            # ─── چک صدا + فال‌بک instaloader (مثل gacha_insta_check) ───
            # لینک videoUrl اپیفای گاهی preview بی‌صدا می‌ده؛ اگه هر mp4 بدون audio track بود،
            # کل پست با instaloader از خود صفحه‌ی پست گرفته می‌شه.
            replaced = False
            files_after, replaced = base.ensure_audio(files, post["url"], shortcode)
            if files_after is None:
                # فال‌بک fail شد و workdir پاک شده بود → دوباره از CDN
                files = dsend3.download_media_direct(post["media_items"])
                replaced = False
            else:
                files = files_after
                if replaced:
                    # instaloader کل پست (با اسلایدهای حذف‌شده) رو آورده؛ همون برش پروفایل رو اعمال کن
                    orig_count = post.get("orig_count", len(files))
                    start, end = dsend3.drop_range(orig_count, item.get("profile"))
                    if (start, end) != (0, orig_count):
                        if len(files) == orig_count:
                            files = files[start:end]
                        else:
                            print(f"⚠️ {shortcode}: تعداد فایل‌های فال‌بک ({len(files)}) با پست ({orig_count}) "
                                  f"نمی‌خونه — حذف اسلاید اعمال نشد")

            thumb = None
            video_meta = None
            if any(f.lower().endswith(".mp4") for f in files):
                thumb = dsend3.prepare_thumb(post["media_items"], files)
                # اگه فایل از instaloader اومده، متادیتای اپیفای معتبر نیست؛ video_attributes از خود فایل می‌خونه
                if not replaced:
                    video_meta = [{"duration": i.get("duration")} for i in post["media_items"]
                                  if (i.get("type") or "").lower() == "video"]
            sent = asyncio.run(
                send_scheduled(files, item["caption"], chat_id, when, thumb=thumb, video_meta=video_meta,
                               continuation_caption=item.get("continuation"))
            )
            ids = ", ".join(str(s.id) for s in sent)
            print(f"✅ {shortcode} زمان‌بندی شد برای {when.strftime('%Y-%m-%d %H:%M UTC')} "
                  f"(message_id(s)={ids})")
            page["last_post_id"] = shortcode
            page["last_post_taken_at"] = post["takenAt"]
        except FloodWaitError as e:
            print(f"⏳ FloodWait: {e.seconds} ثانیه — توقف کل ران (state تا همینجا ثبت می‌شه)")
            WARNINGS.append(f"FloodWait {e.seconds}s — ران زودتر متوقف شد")
            break
        except (Exception, SystemExit) as e:
            print(f"❌ زمان‌بندی {shortcode} شکست خورد ({e}) — این پیج از همینجا برای دفعه‌ی بعد retry می‌شه")
            WARNINGS.append(f"{shortcode}: ارسال شکست خورد ({type(e).__name__}: {e})")
            # state این پست جلو نمی‌ره، ولی بقیه‌ی پیج‌ها/پست‌ها ادامه پیدا می‌کنن
        time.sleep(SCHEDULE_CALL_GAP_SECONDS)


# ─────────────────────────────────────────
# main
# ─────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages-file", default="aninexus_pages.json")
    parser.add_argument("--chat-id", default="-1003135845875")
    parser.add_argument("--preview", default=None,
                        help="کپشن خام اینستا؛ بدون ارسال، فقط خروجی نهایی رو چاپ می‌کنه (نیازی به توکن نیست)")
    parser.add_argument("--profile", default="", help="اسم پروفایل برای --preview")
    args = parser.parse_args()

    if args.preview is not None:
        prof = dsend3.resolve_profile(args.profile)
        _raw = args.preview.replace("\\n", "\n")
        r = skip_reason(_raw, prof)
        if r:
            print(f"🚫 این پست رد می‌شد: {r}")
            return
        cap, cont, why = safe_make_caption(_raw, prof, "preview")
        print(f"\n🏷️ {why}\n{'─' * 40}\n{cap}")
        if cont:
            print(f"{'─' * 40}\n[ادامه‌ی کپشن]\n{cont}")
        return

    if not APIFY_TOKEN:
        print("❌ APIFY_TOKEN تنظیم نشده")
        sys.exit(1)
    if not (dsend3.TG_API_ID and dsend3.TG_API_HASH and dsend3.TG_SESSION_STRING):
        print("❌ TG_API_ID / TG_API_HASH / TG_SESSION_STRING تنظیم نشده")
        sys.exit(1)

    pages = load_pages(args.pages_file)
    if not pages:
        print("ℹ️ aninexus_pages.json خالیه — هیچ پیجی برای چک کردن نیست")
        return

    to_send = collect_sendable_posts(pages)
    schedule_all(to_send, args.chat_id)

    # هر پیجی که state ش (چه با ارسال، چه با رد آگاهانه) جلو رفته، اینجا ذخیره می‌شه
    save_pages(args.pages_file, pages)
    git_commit_and_push(args.pages_file, "aninexus-insta: nightly check + schedule")

    if WARNINGS:
        print(f"\n⚠️ {len(WARNINGS)} هشدار توی این ران:")
        for w in WARNINGS:
            print(f"::warning::{w}")   # توی صفحه‌ی خلاصه‌ی Actions نمایش داده می‌شه
    print("\n✅ تمام شد")


if __name__ == "__main__":
    main()
