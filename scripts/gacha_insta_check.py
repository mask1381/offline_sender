#!/usr/bin/env python3
"""
چک دوره‌ای پیج‌های اینستاگرام (Apify) + ارسال پست‌های جدید به تلگرام.
جایگزین بخش «gacha insta» از IG → channel.json (n8n).

اصل طراحی — قابلیتی که قبلاً نبود و دردسرساز بود:
  state هر پیج (last_post_id / last_post_taken_at) فقط بعد از ارسال *موفق*
  همون پست به تلگرام آپدیت و commit می‌شه. اگه ارسال fail بشه، state دست‌نخورده
  می‌مونه و اجرای بعدی دوباره همون پست رو retry می‌کنه — هیچ پستی گم نمی‌شه.

commit به pages.json بعد از هر پیج انجام می‌شه (نه در آخر کل ران)، تا اگه پیج
سوم fail کنه، موفقیت پیج اول/دوم از دست نره.

Generic/آینده‌نگر: لیست پیج‌ها و chat_id مقصد از --pages-file و --chat-id
گرفته می‌شن، پس همین اسکریپت رو می‌شه برای پروژه/کانال دیگه هم با یه pages_*.json
و chat_id متفاوت صدا زد — بدون ساختن فایل جدید.
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

# ─────────────────────────────────────────
# download_and_send3.py همین پوشه‌ست — importش می‌کنیم تا منطق دانلود/ارسال
# صفر بار تکرار نشه (نه subprocess، نه کپی کد)
# ─────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import download_and_send3 as dsend  # noqa: E402

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "").strip()
MAX_PER_PAGE = 8
APIFY_RESULTS_LIMIT = 12  # کمی بیشتر از MAX_PER_PAGE برای اطمینان از پوشش کامل


# ─────────────────────────────────────────
# خوندن/نوشتن pages.json
# ─────────────────────────────────────────
def load_pages(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_pages(path: str, pages: list):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pages, f, ensure_ascii=False, indent=2)
        f.write("\n")


def git_commit_and_push(pages_file: str, message: str) -> bool:
    """کامیت و پوش تغییرات pages.json. اگه چیزی برای کامیت نبود، بی‌سروصدا True برمی‌گردونه."""
    try:
        subprocess.run(["git", "config", "user.name", "gacha-insta-bot"], check=True)
        subprocess.run(["git", "config", "user.email", "gacha-insta-bot@users.noreply.github.com"], check=True)
        subprocess.run(["git", "add", pages_file], check=True)

        diff = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if diff.returncode == 0:
            print("ℹ️ تغییری برای کامیت نبود.")
            return True

        subprocess.run(["git", "commit", "-m", message], check=True)
        # قبل از push یه rebase سبک، برای وقتی چند اجرا هم‌پوشانی داشتن
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
    """معادل دقیق new Date(ts*1000).toISOString() جاوااسکریپت —
    فرمت ...THH:MM:SS.mmmZ (نه +00:00). Apify actor فقط این فرمت رو قبول می‌کنه؛
    فرمت +00:00 پیش‌فرض isoformat() پایتون با 400 Bad Request رد می‌شه."""
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
    """گارد اضافه فراتر از skipPinnedPosts سمت Apify — بعضی وقتا اسکرپرها
    فیلد pin رو با اسم‌های مختلف برمی‌گردونن یا اصلاً حذفش نمی‌کنن."""
    for key in ("pinned", "isPinned", "isPin"):
        if post.get(key):
            return True
    return False


def extract_media(node: dict) -> list:
    if not node:
        return []
    if node.get("videoUrl"):
        item = {"url": node["videoUrl"], "type": "video"}
        try:
            d = float(node.get("videoDuration") or 0)
            if d > 0:
                item["duration"] = d
        except (TypeError, ValueError):
            pass
        if node.get("displayUrl"):
            item["thumb"] = node["displayUrl"]
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
    """پست‌های جدید یه پیج رو برمی‌گردونه (قدیمی→جدید، حداکثر MAX_PER_PAGE تا)."""
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


# ─────────────────────────────────────────
# دانلود + ارسال یه پست (با reuse کامل توابع download_and_send3.py)
# ─────────────────────────────────────────
def reset_workdir():
    shutil.rmtree(dsend.WORKDIR, ignore_errors=True)
    os.makedirs(dsend.WORKDIR, exist_ok=True)


def send_post(post: dict, chat_id: str, caption_mode: str) -> bool:
    """True = هندل شد (چه بفرسته چه عمداً skip کنه) → state آپدیت بشه.
    False/exception = شکست واقعی → state آپدیت نشه، دفعه‌ی بعد retry بشه."""
    media_items = post["media_items"]
    caption_raw = post["caption"]

    if not media_items:
        print(f'⏭️ {post["shortcode"]}: بدون رسانه — رد شد')
        return True

    reset_workdir()

    duration, tags, kind = None, [], None
    if caption_mode == "channel":
        tags = dsend.extract_hashtags(caption_raw)
        duration = dsend.meta_duration(media_items)
        kind, why = dsend.decide_type(media_items, caption_raw, duration)
        if kind is None:
            print(f'⏭️ {post["shortcode"]}: {why}')
            return True

    files = dsend.download_media_direct(media_items)

    thumb, video_meta = None, None
    if any((i.get("type") or "").lower() == "video" for i in media_items):
        thumb = dsend.prepare_thumb(media_items, files)
        video_meta = next(
            (i for i in media_items if (i.get("type") or "").lower() == "video"), None
        )

    if caption_mode == "channel":
        if kind == "PENDING":
            ds = [dsend.mp4_info(f)["duration"] for f in files if f.lower().endswith(".mp4")]
            ds = [d for d in ds if d]
            duration = max(ds) if ds else None
            kind, why = dsend.decide_type(media_items, caption_raw, duration)
            print(f"🏷️ نوع={kind} | {why}")
        caption_final = dsend.build_channel_caption(kind, caption_raw, tags, post["url"])
    else:
        caption_final = dsend.build_caption(caption_raw)

    sent = asyncio.run(
        dsend.send_items_to_telegram(files, caption_final, chat_id, thumb, video_meta)
    )
    ids = ", ".join(str(s.id) for s in sent)
    print(f'✅ {post["shortcode"]} ارسال شد. message_id(s)={ids}')
    return True


# ─────────────────────────────────────────
# main
# ─────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages-file", default="pages.json")
    parser.add_argument("--chat-id", required=True)
    parser.add_argument("--caption-mode", default="channel")
    args = parser.parse_args()

    if not APIFY_TOKEN:
        print("❌ APIFY_TOKEN تنظیم نشده")
        sys.exit(1)
    if not (dsend.TG_API_ID and dsend.TG_API_HASH and dsend.TG_SESSION_STRING):
        print("❌ TG_API_ID / TG_API_HASH / TG_SESSION_STRING تنظیم نشده")
        sys.exit(1)

    pages = load_pages(args.pages_file)

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
                ok = send_post(post, args.chat_id, args.caption_mode)
            except FloodWaitError as e:
                print(f"⏳ FloodWait: {e.seconds} ثانیه — توقف کل ران")
                if page_advanced:
                    save_pages(args.pages_file, pages)
                    git_commit_and_push(args.pages_file, f"gacha-insta: partial update {username} (flood wait)")
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
            git_commit_and_push(args.pages_file, f"gacha-insta: update {username} → {page['last_post_id']}")

    print("\n✅ تمام شد")


if __name__ == "__main__":
    main()
