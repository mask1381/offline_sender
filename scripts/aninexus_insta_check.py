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
  blocked_hashtags، extra_hashtags، fixed_caption، continuation_caption.

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
    """ارسال زمان‌بندی‌شده‌ی عکس/ویدیو (تکی یا آلبوم، هر آلبوم حداکثر ۱۰ تا)."""
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
                vattr = dsend3.video_attributes(f, video_meta, thumb)
                if vattr:
                    attributes.append(vattr)
                kwargs["attributes"] = attributes
                if thumb:
                    kwargs["thumb"] = thumb
            sent = await client.send_file(
                chat_value, f, caption=caption, parse_mode="html",
                supports_streaming=True, schedule=schedule_dt, **kwargs,
            )
            sent_all.extend(sent if isinstance(sent, list) else [sent])
        else:
            for i in range(0, len(files), 10):
                chunk = files[i:i + 10]
                sent = await client.send_file(
                    chat_value, chunk,
                    caption=caption if i == 0 else (continuation_caption or ""),
                    parse_mode="html", supports_streaming=True, schedule=schedule_dt,
                )
                sent_all.extend(sent if isinstance(sent, list) else [sent])
        return sent_all
    finally:
        await client.disconnect()


# ─────────────────────────────────────────
# مرحله‌ی ۱: جمع‌آوری همه‌ی پست‌های قابل‌ارسالِ همه‌ی پیج‌ها
# ─────────────────────────────────────────
# ─────────────────────────────────────────
# کپشن بر اساس پروفایل پیج
# ─────────────────────────────────────────
PROFILE_CAPTION_KEYS = ("keep_lines", "own_hashtags", "raw_caption", "drop_text", "fixed_caption")


def uses_profile_caption(profile: dict) -> bool:
    """True = این پیج کپشن اختصاصی داره؛ False = همون کپشن هشتگیِ قبلی."""
    return any(profile.get(k) for k in PROFILE_CAPTION_KEYS)


def _hashtag_block(tags: list) -> str:
    return f"<blockquote>💠 {' '.join(tags)}</blockquote>" if tags else ""


def _own_hashtags(caption: str, profile: dict) -> list:
    """هشتگ‌های خود پیج (از هر جای کپشن، بدون تکرار) − blocked + extra."""
    blocked = {str(b).lstrip("#").lower() for b in (profile.get("blocked_hashtags") or ())}
    out, seen = [], set()
    for h in dsend3.extract_hashtags(caption):
        key = h.lstrip("#").lower()
        if key and key not in blocked and key not in seen:
            seen.add(key)
            out.append("#" + h.lstrip("#"))
    for h in (profile.get("extra_hashtags") or ()):
        key = str(h).lstrip("#").lower()
        if key and key not in seen:
            seen.add(key)
            out.append("#" + str(h).lstrip("#"))
    return out


def _clean_raw(caption: str) -> str:
    """raw_caption: فقط توکن‌های هشتگ برداشته می‌شن (چون جدا می‌رن)، بقیه‌ی متن دست‌نخورده."""
    t = dsend3.HASHTAG_RE.sub("", caption or "")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


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
        body = dsend3.strip_hashtags(text)
        if body and profile.get("split_title", True):
            title, body = dsend3.split_title(body)

    if profile.get("own_hashtags") or raw_mode:
        tags = _own_hashtags(caption_raw, profile)

    cont = (profile.get("continuation_caption") or "").strip()
    continuation = html.escape(cont, quote=False) if cont else None
    why = (f"پروفایل: عنوان={'✓' if title else '✗'} متن={'✓' if body else '✗'} "
           f"هشتگ={len(tags)}{' | کپشن ادامه ✓' if continuation else ''}")
    return _compose_caption(title, body, tags), continuation, why


def collect_sendable_posts(pages: list) -> list:
    """خروجی: لیستی از {"page": page_dict, "post": post_dict, "caption": html}
    همه‌ی پست‌های دارای رسانه (عکس یا ویدیو)."""
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
            post["orig_count"] = len(post["media_items"])   # برای اعمال دوباره‌ی drop روی فایل‌های فال‌بک صدا
            media_items = dsend3.apply_media_drops(post["media_items"], profile)   # drop_first/drop_last
            post["media_items"] = media_items

            if not media_items:
                print(f'⏭️ {shortcode}: بدون رسانه — رد شد (ولی state جلو می‌ره)')
                page["last_post_id"] = shortcode
                page["last_post_taken_at"] = post["takenAt"]
                continue

            continuation = None
            if uses_profile_caption(profile):
                caption, continuation, why = build_profile_caption(post["caption"], profile)
            else:
                hashtag_html, why = dsend4.build_hashtag_caption(post["caption"])
                if hashtag_html is None:
                    hashtag_html = f"<blockquote>💠 #{dsend4.FALLBACK_TAG}</blockquote>"
                    why = f"هشتگ مرتبطی پیدا نشد — فالبک #{dsend4.FALLBACK_TAG}"
                caption = build_full_caption(post["caption"], hashtag_html)
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
                    first_video = next((i for i in post["media_items"] if (i.get("type") or "").lower() == "video"), {})
                    video_meta = {"duration": first_video.get("duration")}
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
            break
        except (Exception, SystemExit) as e:
            print(f"❌ زمان‌بندی {shortcode} شکست خورد ({e}) — این پیج از همینجا برای دفعه‌ی بعد retry می‌شه")
            # state این پست جلو نمی‌ره، ولی بقیه‌ی پیج‌ها/پست‌ها ادامه پیدا می‌کنن
        time.sleep(SCHEDULE_CALL_GAP_SECONDS)


# ─────────────────────────────────────────
# main
# ─────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages-file", default="aninexus_pages.json")
    parser.add_argument("--chat-id", default="-1003135845875")
    args = parser.parse_args()

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

    print("\n✅ تمام شد")


if __name__ == "__main__":
    main()
