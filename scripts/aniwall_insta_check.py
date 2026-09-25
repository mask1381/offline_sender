#!/usr/bin/env python3
"""
aniwall_insta_check.py — چک دوره‌ای پیج‌های اینستاگرام برای کانال اصلی aniwall_lab.

دقیقاً همون الگوی gacha_insta_check.py: پیج‌های aniwall_pages.json (فایل جدا،
مستقل از pages.json خود گچا) رو مستقیم از Apify می‌خونه، پست جدید رو دانلود و
با کپشن «فقط هشتگ» (download_and_send4.build_hashtag_caption) به کانال
می‌فرسته، و بعد از هر پیجِ موفق state (last_post_id/last_post_taken_at) رو
commit/push می‌کنه. اگه ارسال یه پست fail کنه، state دست‌نخورده می‌مونه و دفعه‌ی
بعد دوباره retry می‌شه — هیچ پستی گم نمی‌شه.

هیچ منطق دانلود/ارسال از صفر نوشته نشده: از download_and_send3.py (پایه) و
download_and_send4.py (کپشن هشتگی) import می‌شه.
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

import requests
from telethon.errors import FloodWaitError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import download_and_send3 as dsend3          # noqa: E402 — دانلود/ارسال/entity resolution
import download_and_send4 as dsend4          # noqa: E402 — کپشن هشتگی/دیکشنری

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "").strip()
MAX_PER_PAGE = 8
APIFY_RESULTS_LIMIT = 12


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
    """کامیت و پوش تغییرات aniwall_pages.json. اگه چیزی برای کامیت نبود، بی‌سروصدا True برمی‌گردونه."""
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


# ─────────────────────────────────────────
# دانلود + کپشن‌سازی + ارسال یه پست
# ─────────────────────────────────────────
def reset_workdir():
    shutil.rmtree(dsend3.WORKDIR, ignore_errors=True)
    os.makedirs(dsend3.WORKDIR, exist_ok=True)


def send_post(post: dict, chat_id: str) -> bool:
    """True = هندل شد (چه بفرسته چه عمداً skip کنه) → state آپدیت بشه.
    False/exception = شکست واقعی → state آپدیت نشه، دفعه‌ی بعد retry بشه."""
    media_items = post["media_items"]
    caption_raw = post["caption"]
    shortcode = post["shortcode"]

    if not media_items:
        print(f'⏭️ {shortcode}: بدون رسانه — رد شد')
        return True

    if any((i.get("type") or "").lower() == "video" for i in media_items):
        print(f'⏭️ {shortcode}: پست ویدیوئه — طبق قانون کانال ارسال نمی‌شه')
        return True

    caption, why = dsend4.build_hashtag_caption(caption_raw)
    if caption is None:
        print(f'⏭️ {shortcode}: {why}')
        return True
    print(f'🏷️ {shortcode}: {why}')

    reset_workdir()
    files = dsend3.download_media_direct(media_items)

    sent = asyncio.run(dsend3.send_items_to_telegram(files, caption, chat_id))
    ids = ", ".join(str(s.id) for s in sent)
    print(f'✅ {shortcode} ارسال شد. message_id(s)={ids}')
    return True


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

    for page in pages:
        username = page["username"]
        print(f"\n━━━ پیج: {username} ━━━")
        try:
            new_posts = fetch_new_posts(page)
        except Exception as e:
            print(f"⚠️ چک {username} شکست خورد ({e}) — رد شد، state دست‌نخورده می‌مونه")
            continue

        if not new_posts:
            print("ℹ️ پست جدیدی نیست")
            continue

        print(f"📬 {len(new_posts)} پست جدید")
        page_advanced = False

        for post in new_posts:
            try:
                ok = send_post(post, args.chat_id)
            except FloodWaitError as e:
                print(f"⏳ FloodWait: {e.seconds} ثانیه — توقف کل ران")
                if page_advanced:
                    save_pages(args.pages_file, pages)
                    git_commit_and_push(args.pages_file, f"aniwall-insta: partial update {username} (flood wait)")
                sys.exit(2)
            except Exception as e:
                print(f'❌ ارسال {post["shortcode"]} شکست خورد ({e}) — توقف این پیج، retry دفعه‌ی بعد')
                break
            else:
                if ok:
                    page["last_post_id"] = post["shortcode"]
                    page["last_post_taken_at"] = post["takenAt"]
                    page_advanced = True
                else:
                    break

        if page_advanced:
            save_pages(args.pages_file, pages)
            git_commit_and_push(args.pages_file, f"aniwall-insta: update {username} → {page['last_post_id']}")

    print("\n✅ تمام شد")


if __name__ == "__main__":
    main()
