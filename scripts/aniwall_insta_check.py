#!/usr/bin/env python3
"""
aniwall_insta_check.py — چک شبانه‌ی پیج‌های اینستاگرام برای کانال aniwall_lab.

برخلاف نسخه‌ی اول، اینجا دو مرحله‌ایه:
  ۱) همه‌ی پیج‌های aniwall_pages.json چک می‌شن و هرچی پست جدید هست جمع می‌شه
     (بدون ارسال فوری، بدون توجه به ترتیب بین پیج‌ها).
  ۲) بر اساس تعداد کل پست‌های جمع‌شده، هر کدوم با قابلیت schedule تلگرام توی
     یه بازه‌ی ۲۴ساعته پخش می‌شن: از ۱ ساعت بعد از همین اجرا تا ۲ ساعت قبل
     از اجرای فردا شب. این یعنی بین ۱۲ شب (وقتی اسکریپت اجرا می‌شه) تا ۱ شب،
     و همچنین از ۱۰ شب تا ۱۲ شب، هیچ پستی زمان‌بندی نمی‌شه — تا وقت داشته
     باشی قبل از هر انتشار، از بخش Scheduled Messages تلگرام پست‌ها رو ببینی
     و در صورت نیاز دخالت کنی.

تریگر این اسکریپت کاملاً دستیه (نه cron) — خود کاربر هر شب ساعت ۱۲ صداش می‌زنه.

هیچ منطق دانلود/ورد نویسی از صفر نیست: از download_and_send3.py (دانلود،
entity resolution) و download_and_send4.py (کپشن هشتگی + ارسال زمان‌دار)
import می‌شه.
"""

import argparse
import asyncio
import json
import os
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
SCHEDULE_CALL_GAP_SECONDS = 5.0   # فاصله‌ی واقعی بین هر schedule call (محض اطمینان، نه فاصله‌ی نمایش)


# ─────────────────────────────────────────
# خوندن/نوشتن aniwall_pages.json
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
        subprocess.run(["git", "config", "user.name", "aniwall-insta-bot"], check=True)
        subprocess.run(["git", "config", "user.email", "aniwall-insta-bot@users.noreply.github.com"], check=True)
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
        return [{"url": node["videoUrl"], "type": "video"}]
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
# مرحله‌ی ۱: جمع‌آوری همه‌ی پست‌های قابل‌ارسالِ همه‌ی پیج‌ها
# ─────────────────────────────────────────
def collect_sendable_posts(pages: list) -> list:
    """خروجی: لیستی از {"page": page_dict, "post": post_dict, "caption": html}
    فقط شامل پست‌هایی که رد نشدن (نه ویدیو، نه بی‌ربط)."""
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

            if any((i.get("type") or "").lower() == "video" for i in media_items):
                print(f'⏭️ {shortcode}: پست ویدیوئه — رد شد (ولی state جلو می‌ره)')
                page["last_post_id"] = shortcode
                page["last_post_taken_at"] = post["takenAt"]
                continue

            caption, why = dsend4.build_hashtag_caption(post["caption"])
            if caption is None:
                print(f'⏭️ {shortcode}: {why} — رد شد (ولی state جلو می‌ره)')
                page["last_post_id"] = shortcode
                page["last_post_taken_at"] = post["takenAt"]
                continue

            print(f'🏷️ {shortcode}: {why}')
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
            sent = asyncio.run(
                dsend4.send_photo_album_scheduled(files, item["caption"], chat_id, schedule_dt=when)
            )
            ids = ", ".join(str(s.id) for s in sent)
            print(f"✅ {shortcode} زمان‌بندی شد برای {when.strftime('%Y-%m-%d %H:%M UTC')} "
                  f"(message_id(s)={ids})")
            page["last_post_id"] = shortcode
            page["last_post_taken_at"] = post["takenAt"]
        except FloodWaitError as e:
            print(f"⏳ FloodWait: {e.seconds} ثانیه — توقف کل ران (state تا همینجا ثبت می‌شه)")
            break
        except Exception as e:
            print(f"❌ زمان‌بندی {shortcode} شکست خورد ({e}) — این پیج از همینجا برای دفعه‌ی بعد retry می‌شه")
            # state این پست جلو نمی‌ره، ولی بقیه‌ی پیج‌ها/پست‌ها ادامه پیدا می‌کنن
        time.sleep(SCHEDULE_CALL_GAP_SECONDS)


# ─────────────────────────────────────────
# main
# ─────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages-file", default="aniwall_pages.json")
    parser.add_argument("--chat-id", required=True)
    args = parser.parse_args()

    if not APIFY_TOKEN:
        print("❌ APIFY_TOKEN تنظیم نشده")
        sys.exit(1)
    if not (dsend3.TG_API_ID and dsend3.TG_API_HASH and dsend3.TG_SESSION_STRING):
        print("❌ TG_API_ID / TG_API_HASH / TG_SESSION_STRING تنظیم نشده")
        sys.exit(1)

    pages = load_pages(args.pages_file)
    if not pages:
        print("ℹ️ aniwall_pages.json خالیه — هیچ پیجی برای چک کردن نیست")
        return

    to_send = collect_sendable_posts(pages)
    schedule_all(to_send, args.chat_id)

    # هر پیجی که state ش (چه با ارسال، چه با رد آگاهانه) جلو رفته، اینجا ذخیره می‌شه
    save_pages(args.pages_file, pages)
    git_commit_and_push(args.pages_file, "aniwall-insta: nightly check + schedule")

    print("\n✅ تمام شد")


if __name__ == "__main__":
    main()
