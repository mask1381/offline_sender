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


async def send_scheduled(files, caption, chat_id, schedule_dt, thumb=None, video_meta=None):
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
                    caption=caption if i == 0 else "",
                    parse_mode="html", supports_streaming=True, schedule=schedule_dt,
                )
                sent_all.extend(sent if isinstance(sent, list) else [sent])
        return sent_all
    finally:
        await client.disconnect()


# ─────────────────────────────────────────
# مرحله‌ی ۱: جمع‌آوری همه‌ی پست‌های قابل‌ارسالِ همه‌ی پیج‌ها
# ─────────────────────────────────────────
def collect_sendable_posts(pages: list) -> list:
    """خروجی: لیستی از {"page": page_dict, "post": post_dict, "caption": html}
    همه‌ی پست‌های دارای رسانه (عکس یا ویدیو)."""
    to_send = []
    for page in pages:
        username = page["username"]
        print(f"\n━━━ چک پیج: {username} ━━━")
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
            media_items = post["media_items"]

            if not media_items:
                print(f'⏭️ {shortcode}: بدون رسانه — رد شد (ولی state جلو می‌ره)')
                page["last_post_id"] = shortcode
                page["last_post_taken_at"] = post["takenAt"]
                continue

            hashtag_html, why = dsend4.build_hashtag_caption(post["caption"])
            if hashtag_html is None:
                hashtag_html = f"<blockquote>💠 #{dsend4.FALLBACK_TAG}</blockquote>"
                why = f"هشتگ مرتبطی پیدا نشد — فالبک #{dsend4.FALLBACK_TAG}"

            caption = build_full_caption(post["caption"], hashtag_html)
            kinds = ",".join(sorted({(i.get("type") or "").lower() for i in media_items}))
            print(f'🏷️ {shortcode} [{kinds}]: {why}')
            to_send.append({"page": page, "post": post, "caption": caption})

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
            thumb = None
            video_meta = None
            if any(f.lower().endswith(".mp4") for f in files):
                thumb = dsend3.prepare_thumb(post["media_items"], files)
                first_video = next((i for i in post["media_items"] if (i.get("type") or "").lower() == "video"), {})
                video_meta = {"duration": first_video.get("duration")}
            sent = asyncio.run(
                send_scheduled(files, item["caption"], chat_id, when, thumb=thumb, video_meta=video_meta)
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
