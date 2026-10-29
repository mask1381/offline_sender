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

پروفایل پیج‌ها (scripts/page_profiles.py):
  هر پیج توی aniwall_pages.json می‌تونه کلید "profile" داشته باشه (مثلاً "deminiart") تا
  هشتگ‌های کپشنش با قواعد اختصاصی ساخته بشه. پیج بدون "profile" دقیقاً مثل قبل (تشخیص سراسری
  با دیکشنری) کار می‌کنه. اسم پروفایلی که تعریف نشده باشه = خطا؛ اون پیج اجرا نمی‌شه و state
  دست‌نخورده می‌مونه.

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
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from telethon.errors import FloodWaitError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import download_and_send3 as dsend3          # noqa: E402 — دانلود/entity resolution
import download_and_send4 as dsend4          # noqa: E402 — کپشن هشتگی/ارسال زمان‌دار
import pages_store                          # noqa: E402 — جدا کردن config از state (بخش ۱.۴)

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "").strip()
MAX_PER_PAGE = 8
APIFY_RESULTS_LIMIT = 12
MAX_PHOTOS_PER_RUN = 95           # سقف پیام‌های زمان‌بندی‌شده‌ی تلگرام حدود ۱۰۰ه؛ ۵ تا جای خالی
SCHEDULE_CALL_GAP_SECONDS = 5.0   # فاصله‌ی واقعی بین هر schedule call (محض اطمینان، نه فاصله‌ی نمایش)


def state_path_of(pages_file: str) -> str:
    """فایل state جدا از فایل config (بخش ۱.۴)."""
    return pages_file.replace(".json", "_state.json")


def load_pages(config_path):
    """config + state را می‌خواند و در آبجکتِ هر پیج merge می‌کند."""
    return pages_store.load_pages(config_path, state_path_of(config_path))


def save_pages(pages_file: str, pages: list):
    """فقط state را جدا می‌نویسد — config دست نمی‌خورد."""
    pages_store.save_state(state_path_of(pages_file), pages)


def git_commit_and_push(pages_file: str, message: str) -> bool:
    """state (+configِ تازه‌ساخته) را کامیت و پوش می‌کند."""
    return pages_store.git_commit_and_push([state_path_of(pages_file), pages_file], message, "aniwall-insta-bot")


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
# مرحله‌ی ۱: جمع‌آوری پست‌های همه‌ی پیج‌ها (بدون دست زدن به state)
# ─────────────────────────────────────────
def collect_posts(pages: list) -> list:
    """خروجی: لیست ترتیب‌دار (پیج به پیج، قدیمی→جدید) از
    {"page", "post", "caption", "why"}.
    caption=None یعنی پست عمداً رد می‌شه (بدون رسانه / ویدیو / بی‌ربط)؛
    state اون فقط توی مرحله‌ی ارسال و به ترتیب جلو می‌ره، نه اینجا — تا اگه
    پست قبلیِ همون پیج ارسال نشد، state از روش رد نشه و پست گم نشه."""
    entries = []
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
            media_items = post["media_items"]

            if not media_items:
                entries.append({"page": page, "post": post, "caption": None, "why": "بدون رسانه"})
                continue

            if any((i.get("type") or "").lower() == "video" for i in media_items):
                entries.append({"page": page, "post": post, "caption": None, "why": "پست ویدیوئه"})
                continue

            caption, why = dsend4.build_caption_for_profile(post["caption"], profile)
            if caption is None:
                entries.append({"page": page, "post": post, "caption": None, "why": why})
                continue

            print(f"🏷️ {shortcode}: {why}")
            entries.append({"page": page, "post": post, "caption": caption, "why": why})

    return entries


# ─────────────────────────────────────────
# سقف تعداد عکس: پستی که جمع رو از سقف رد کنه کامل حذف می‌شه، همراه بقیه‌ی
# پست‌های همون پیج (تا state رد نشه)؛ شب بعد دوباره از همون‌جا میاد.
# ─────────────────────────────────────────
def apply_photo_cap(entries: list) -> list:
    total = 0
    blocked = set()
    plan = []
    for e in entries:
        name = e["page"]["username"]
        shortcode = e["post"]["shortcode"]
        if name in blocked:
            print(f"⏸️ {shortcode} ({name}): به شب بعد موکول شد (پست قبلیِ این پیج جا نشد)")
            continue
        if e["caption"] is not None:
            n = len(e["post"]["media_items"])
            if total + n > MAX_PHOTOS_PER_RUN:
                blocked.add(name)
                print(f"⏸️ {shortcode} ({name}): {n} عکس از سقف {MAX_PHOTOS_PER_RUN} رد می‌شه "
                      f"(جمع فعلی {total}) — به شب بعد موکول شد")
                continue
            total += n
        plan.append(e)
    print(f"\n📊 جمع عکس‌های این اجرا: {total} از سقف {MAX_PHOTOS_PER_RUN}")
    return plan


# ─────────────────────────────────────────
# مرحله‌ی ۲: زمان‌بندی و ارسال (state بعد از هر پستِ موفق ذخیره و commit می‌شه)
# ─────────────────────────────────────────
def advance_state(page: dict, post: dict):
    page["last_post_id"] = post["shortcode"]
    page["last_post_taken_at"] = post["takenAt"]

# ─────────────────────────────────────────
# زمان‌بندی بر اساس ساعت هر پیج (post_hour توی پروفایل، به وقت تهران)
# ─────────────────────────────────────────
TEHRAN = ZoneInfo("Asia/Tehran")
POST_GAP_MIN = 10          # فاصله‌ی پست‌های پشت‌سرهم (هر پیج و بین پیج‌ها)
MIN_LEAD_MIN = 90          # حداقل فاصله‌ی اولین پست تا لحظه‌ی اجرا
RUN_HOURS = {0, 4, 8, 12, 16, 20}   # ساعت‌های ران کانال (دقیقه ۰)


def _hour_of(entry):
    prof = dsend3.resolve_profile((entry["page"].get("profile") or "").strip())
    h = prof.get("post_hour")
    return int(h) if h is not None else None


def _reserve(t, used):
    """اگه کمتر از POST_GAP_MIN با پست دیگه‌ای فاصله داشت، جلو می‌بره."""
    gap = timedelta(minutes=POST_GAP_MIN)
    while any(abs(t - u) < gap for u in used):
        t += gap
    used.append(t)
    return t


def compute_times(send_entries, now):
    """خروجی: لیست زمان UTC هم‌ترتیب send_entries."""
    now_teh = now.astimezone(TEHRAN)
    n_fallback = sum(1 for e in send_entries if _hour_of(e) is None)
    fallback = iter(dsend4.compute_schedule_times(now, n_fallback))
    used, times = [], []
    for e in send_entries:
        name, sc = e["page"]["username"], e["post"]["shortcode"]
        hour = _hour_of(e)
        if hour is None:
            t = next(fallback)
            print(f"ℹ️ {name}: post_hour ندارد — پخش خودکار")
        else:
            if not 0 <= hour <= 23:
                raise ValueError(f"post_hour نامعتبر برای {name}: {hour}")
            base = now_teh.replace(hour=hour, minute=0, second=0, microsecond=0)
            while base < now_teh + timedelta(minutes=MIN_LEAD_MIN):
                base += timedelta(days=1)
            t = _reserve(base, used)
            local = t.astimezone(TEHRAN)
            if local.hour in RUN_HOURS or (((local.hour + 1) % 24) in RUN_HOURS and local.minute >= 45):
                print(f"⚠️ {name}/{sc}: زمان {local:%H:%M} با ران کانال تداخل دارد")
        times.append(t.astimezone(timezone.utc))
    return times
def schedule_all(plan: list, chat_id: str, pages: list, pages_file: str):
    send_entries = [e for e in plan if e["caption"] is not None]
    if not send_entries:
        print("\nℹ️ هیچ پستی برای زمان‌بندی نبود")
    now = datetime.now(timezone.utc)
    times = compute_times(send_entries, now)
    if times:
        print(f"\n🗓️ {len(send_entries)} پست، زمان‌بندی بین "
              f"{min(times).strftime('%H:%M UTC')} تا {max(times).strftime('%H:%M UTC')}")
    when_by_entry = {id(e): t for e, t in zip(send_entries, times)}

    failed_pages = set()
    for e in plan:
        page = e["page"]
        post = e["post"]
        name = page["username"]
        shortcode = post["shortcode"]

        if name in failed_pages:
            print(f"⏸️ {shortcode} ({name}): رد شد چون پست قبلیِ این پیج شکست خورد — شب بعد دوباره")
            continue

        if e["caption"] is None:
            print(f"⏭️ {shortcode}: {e['why']} — رد شد (state به ترتیب جلو می‌ره)")
            advance_state(page, post)
            continue

        when = when_by_entry[id(e)]
        try:
            reset_workdir()
            files = dsend3.download_media_direct(post["media_items"])
            sent = asyncio.run(
                dsend4.send_photo_album_scheduled(files, e["caption"], chat_id, schedule_dt=when)
            )
            ids = ", ".join(str(s.id) for s in sent)
            print(f"✅ {shortcode} زمان‌بندی شد برای {when.strftime('%Y-%m-%d %H:%M UTC')} "
                  f"(message_id(s)={ids})")
            advance_state(page, post)
            # ذخیره‌ی فوری: اگه ران وسط کار کرش کنه، پست‌های انجام‌شده شب بعد تکراری نمی‌شن
            save_pages(pages_file, pages)
            git_commit_and_push(pages_file, f"aniwall-insta: {name} {shortcode} scheduled")
        except FloodWaitError as exc:
            print(f"⏳ FloodWait: {exc.seconds} ثانیه — توقف کل ران (state تا همینجا ثبت شده)")
            break
        except (Exception, SystemExit) as exc:
            # SystemExit هم گرفته می‌شه چون download_media_direct موقع fail کامل sys.exit می‌زنه
            failed_pages.add(name)
            print(f"❌ زمان‌بندی {shortcode} شکست خورد ({type(exc).__name__}: {exc}) — "
                  f"بقیه‌ی پست‌های {name} این شب رد می‌شن و از همینجا شب بعد retry می‌شن")
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

    entries = collect_posts(pages)
    plan = apply_photo_cap(entries)
    try:
        schedule_all(plan, args.chat_id, pages, args.pages_file)
    finally:
        # ذخیره‌ی نهایی (شامل پست‌های عمداً ردشده که آخر لیست بودن)
        save_pages(args.pages_file, pages)
        git_commit_and_push(args.pages_file, "aniwall-insta: nightly check + schedule")

    print("\n✅ تمام شد")


if __name__ == "__main__":
    main()
