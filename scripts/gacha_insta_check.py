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

فال‌بک صدا: بعضی پیج‌ها (مثل wuthering_waves) از Apify یه videoUrl بی‌صدا
برمی‌گردونن (نسخه‌ی preview autoplay فید، نه فایل اصلی). بعد از دانلود، هر
فایل mp4 از نظر داشتن track صوتی چک می‌شه؛ اگه نداشت، همون پست با
instaloader (تابع مشترک از download_and_send3.py) دوباره گرفته می‌شه که
مستقیم از صفحه‌ی پست می‌گیره و نسخه‌ی کامل رو برمی‌گردونه.
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

# فورکدهای شناخته‌شده‌ی صوتی توی باکس stsd (mp4_info آماده می‌ده)
AUDIO_CODEC_FOURCCS = {"mp4a", "ac-3", "ec-3", "opus", "alac", "samr", "sawb"}


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
# تشخیص نبود صدا + fallback با instaloader
# ─────────────────────────────────────────
def has_audio_track(path: str) -> bool:
    info = dsend.mp4_info(path)
    codecs = [c.lower() for c in info.get("codecs", [])]
    return any(c in AUDIO_CODEC_FOURCCS for c in codecs)


def ensure_audio(files: list, post_url: str, shortcode: str) -> tuple:
    """اگه هر کدوم از فایل‌های mp4 بی‌صدا بودن، کل پست رو با instaloader
    دوباره می‌گیره (چون instaloader مستقیم از صفحه‌ی پست می‌گیره، نه از
    لینک CDN که Apify داده). خروجی: (files_جدید_یا_قدیمی, replaced: bool)."""
    video_files = [f for f in files if f.lower().endswith(".mp4")]
    if not video_files:
        return files, False

    if all(has_audio_track(f) for f in video_files):
        return files, False

    print(f"🔇 {shortcode}: ویدیو بدون صدا از لینک مستقیم CDN — تلاش برای گرفتن نسخه‌ی کامل با instaloader")
    reset_workdir()
    try:
        new_files, _ = dsend.download_instagram(post_url, "")
    except SystemExit:
        print(f"⚠️ {shortcode}: fallback instaloader شکست خورد — همون نسخه‌ی بی‌صدا فرستاده می‌شه")
        # فایل‌های اصلی پاک شدن (reset_workdir)، پس دوباره از CDN می‌گیریم
        return None, False
    except Exception as e:
        print(f"⚠️ {shortcode}: fallback instaloader خطا داد ({e}) — همون نسخه‌ی بی‌صدا فرستاده می‌شه")
        return None, False

    print(f"✅ {shortcode}: نسخه‌ی با صدا از instaloader گرفته شد")
    return new_files, True


# ─────────────────────────────────────────
# دانلود + ارسال یه پست (با reuse کامل توابع download_and_send3.py)
# ─────────────────────────────────────────
def reset_workdir():
    shutil.rmtree(dsend.WORKDIR, ignore_errors=True)
    os.makedirs(dsend.WORKDIR, exist_ok=True)


def send_post(post: dict, chat_id: str, caption_mode: str, profile: dict = None) -> bool:
    """True = هندل شد (چه بفرسته چه عمداً skip کنه) → state آپدیت بشه.
    False/exception = شکست واقعی → state آپدیت نشه، دفعه‌ی بعد retry بشه.
    profile: قواعد اختصاصی پیج (dsend.resolve_profile) — None = قواعد سراسری."""
    media_items = post["media_items"]
    caption_raw = post["caption"]

    if not media_items:
        print(f'⏭️ {post["shortcode"]}: بدون رسانه — رد شد')
        return True

    profile = profile or {}
    orig_count = len(media_items)
    media_items = dsend.apply_media_drops(media_items, profile)   # drop_first / drop_last پروفایل

    reset_workdir()

    duration, tags, kind = None, [], None
    if caption_mode == "channel":
        tags = dsend.extract_hashtags(caption_raw)
        duration = dsend.meta_duration(media_items)
        kind, why = dsend.decide_type(media_items, caption_raw, duration, profile)
        if kind is None:
            print(f'⏭️ {post["shortcode"]}: {why}')
            return True

    files = dsend.download_media_direct(media_items)

    # ─── چک صدا و fallback (اگه لازم بود) ───
    files_after, replaced = ensure_audio(files, post["url"], post["shortcode"])
    if files_after is None:
        # fallback fail شد و workdir پاک شده بود — دوباره از CDN بگیر
        files = dsend.download_media_direct(media_items)
    else:
        files = files_after
        if replaced:
            # instaloader کل پست رو (با اسلایدهای حذف‌شده) گرفته؛ همون برش رو روی فایل‌ها اعمال کن
            start, end = dsend.drop_range(orig_count, profile)
            if len(files) == orig_count and (start, end) != (0, orig_count):
                files = files[start:end]
            elif len(files) != orig_count and (start, end) != (0, orig_count):
                print(f"⚠️ {post['shortcode']}: تعداد فایل‌های fallback ({len(files)}) با پست ({orig_count}) نمی‌خونه — حذف اسلاید اعمال نشد")

    # کراپ پایین عکس‌ها (پروفایل: crop_bottom_pct) — فقط عکس، ویدیو دست‌نخورده
    files = dsend.crop_bottom_pct_files(files, profile.get("crop_bottom_pct"))

    thumb, video_meta = None, None
    if any((i.get("type") or "").lower() == "video" for i in media_items):
        thumb = dsend.prepare_thumb(media_items, files)
        # اگه fallback جایگزین شد، متادیتای duration/w/h اپیفای دیگه معتبر نیست؛
        # video_attributes خودش از خود فایل mp4 مقدار واقعی رو می‌خونه
        video_meta = None if replaced else next(
            (i for i in media_items if (i.get("type") or "").lower() == "video"), None
        )

    if caption_mode == "channel":
        if kind == "PENDING":
            ds = [dsend.mp4_info(f)["duration"] for f in files if f.lower().endswith(".mp4")]
            ds = [d for d in ds if d]
            duration = max(ds) if ds else None
            kind, why = dsend.decide_type(media_items, caption_raw, duration, profile)
            print(f"🏷️ نوع={kind} | {why}")
        caption_final = dsend.build_channel_caption(kind, caption_raw, tags, post["url"], profile)
    else:
        caption_final = dsend.build_caption(caption_raw)

    schedule_dt = dsend.compute_schedule_dt(profile)
    sent = asyncio.run(
        dsend.send_items_to_telegram(files, caption_final, chat_id, thumb, video_meta, schedule_dt)
    )
    ids = ", ".join(str(s.id) for s in sent)
    if schedule_dt:
        print(f'⏰ {post["shortcode"]} زمان‌بندی شد برای {schedule_dt:%Y-%m-%d %H:%M} UTC. message_id(s)={ids}')
    else:
        print(f'✅ {post["shortcode"]} ارسال شد. message_id(s)={ids}')
    return True


# ─────────────────────────────────────────
# main
# ─────────────────────────────────────────
def main():
    sys.stdout.reconfigure(line_buffering=True)

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
        profile_name = (page.get("profile") or "").strip()
        print(f"\n━━━ پیج: {username} (profile={profile_name or 'default'}) ━━━")
        try:
            profile = dsend.resolve_profile(profile_name)
        except KeyError as e:
            print(f"❌ {e} — این پیج رد شد، state دست‌نخورده می‌مونه")
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
        page_advanced = False

        for post in new_posts:
            try:
                ok = send_post(post, args.chat_id, args.caption_mode, profile)
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
