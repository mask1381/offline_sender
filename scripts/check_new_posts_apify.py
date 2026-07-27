#!/usr/bin/env python3
"""
پایش پروفایل اینستاگرام برای پست جدید — با Apify (Instagram Post Scraper).

هدف: مصرف Apify رو به حداقل برسونیم (فقط چند تا پست آخر رو لیست می‌کنیم،
هیچ عکس/ویدیویی از طریق Apify دانلود نمی‌شه). به‌محض پیدا شدن پست(های) جدید
(که تو seen_posts.json نبودن)، workflow خودمون (download-send.yml که با
instaloader سخت‌سازی‌شده کار می‌کنه) رو برای هرکدوم دیسپچ می‌کنیم تا دانلود
و ارسال واقعی توسط کد خودمون انجام بشه — نه Apify.

نیاز محیطی:
  APIFY_TOKEN   توکن API اپیفای
  GH_TOKEN      یه PAT با دسترسی actions:write برای دیسپچ workflow دیگه

استفاده:
  python check_new_posts_apify.py --profile steambird.id \
      --seen-file data/seen_posts.json --days 2 --max-posts 5 \
      --chat-id -1001234567890 --mode preview \
      --repo mask1381/yt-genshin
"""

import argparse
import json
import os
import re
import sys
import time

import requests

APIFY_TOKEN = os.environ.get("APIFY_TOKEN")
GH_TOKEN = os.environ.get("GH_TOKEN")

APIFY_ACTOR = "apify~instagram-post-scraper"
APIFY_RUN_SYNC_URL = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items"


def load_seen(seen_file: str) -> set:
    if os.path.exists(seen_file):
        with open(seen_file, 'r', encoding='utf-8') as f:
            try:
                return set(json.load(f))
            except json.JSONDecodeError:
                return set()
    return set()


def save_seen(seen_file: str, seen: set):
    os.makedirs(os.path.dirname(seen_file) or '.', exist_ok=True)
    with open(seen_file, 'w', encoding='utf-8') as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)


def fetch_recent_posts(profile: str, days: int, max_posts: int) -> list:
    """
    Apify actor رو یه‌بار، با محدودیت پایین صدا می‌زنه.
    resultsLimit پایین = هزینه‌ی کم. onlyPostsNewerThan جلوی برگشتن
    پست‌های قدیمی رو می‌گیره (به‌جز پست‌های پین‌شده که خودمون فیلترشون
    می‌کنیم چون Apify این مورد رو برای pinned اعمال نمی‌کنه).
    """
    if not APIFY_TOKEN:
        sys.exit('❌ APIFY_TOKEN تنظیم نشده')

    payload = {
        "username": [profile],
        "resultsLimit": max_posts,
        "onlyPostsNewerThan": f"{days} days",
        "skipPinnedPosts": True,
    }

    resp = requests.post(
        APIFY_RUN_SYNC_URL,
        params={"token": APIFY_TOKEN},
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    items = resp.json()

    posts = []
    cutoff = time.time() - days * 86400
    for item in items:
        url = item.get('url') or ''
        shortcode = item.get('shortCode') or item.get('shortcode')
        if not shortcode:
            m = re.search(r'/(?:p|reel|tv)/([^/?#]+)', url)
            shortcode = m.group(1) if m else None
        if not shortcode:
            continue

        # فیلتر دستی pinned/قدیمی، چون onlyPostsNewerThan رو پست‌های
        # پین‌شده لزوماً اعمال نمی‌شه (باگ شناخته‌شده‌ی این actor)
        ts = item.get('timestamp')
        if ts:
            try:
                from datetime import datetime
                post_ts = datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
                if post_ts < cutoff:
                    continue
            except Exception:
                pass

        posts.append({
            'shortcode': shortcode,
            'url': url or f'https://www.instagram.com/p/{shortcode}/',
            'timestamp': ts,
        })

    # قدیمی‌ترین اول، تا ترتیب پست شدن طبیعی بمونه
    posts.sort(key=lambda p: p.get('timestamp') or '')
    return posts


def dispatch_download_workflow(repo: str, url: str, chat_id: str, mode: str):
    if not GH_TOKEN:
        sys.exit('❌ GH_TOKEN تنظیم نشده (لازم برای دیسپچ workflow دانلود)')

    api_url = f'https://api.github.com/repos/{repo}/actions/workflows/download-send.yml/dispatches'
    resp = requests.post(
        api_url,
        headers={
            'Authorization': f'Bearer {GH_TOKEN}',
            'Accept': 'application/vnd.github+json',
        },
        json={
            'ref': 'main',
            'inputs': {
                'url': url,
                'chat_id': str(chat_id),
                'mode': mode,
                'slides': '',
                'attempt': '1',
                'max_attempts': '5',
            },
        },
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f'⚠️ دیسپچ workflow برای {url} شکست خورد ({resp.status_code}): {resp.text}')
        return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', required=True)
    parser.add_argument('--seen-file', default='data/seen_posts.json')
    parser.add_argument('--days', type=int, default=2)
    parser.add_argument('--max-posts', type=int, default=5, help='کم نگه‌دار تا مصرف Apify پایین بمونه')
    parser.add_argument('--chat-id', required=True)
    parser.add_argument('--mode', default='preview', choices=['preview', 'send'])
    parser.add_argument('--repo', required=True, help="مثلا mask1381/yt-genshin")
    args = parser.parse_args()

    profile_username = args.profile.strip().lstrip('@').split('?')[0].rstrip('/')

    print(f'🔎 چک پروفایل {profile_username} (حداکثر {args.max_posts} پست، {args.days} روز اخیر)...')
    posts = fetch_recent_posts(profile_username, args.days, args.max_posts)
    print(f'📦 {len(posts)} پست برگشت از Apify.')

    seen = load_seen(args.seen_file)
    new_posts = [p for p in posts if p['shortcode'] not in seen]
    print(f'✨ {len(new_posts)} پست جدید (بقیه قبلاً seen بودن).')

    if not new_posts:
        return

    for post in new_posts:
        print(f"🚀 دیسپچ دانلود برای {post['url']} (shortcode={post['shortcode']})")
        ok = dispatch_download_workflow(args.repo, post['url'], args.chat_id, args.mode)
        if ok:
            seen.add(post['shortcode'])
            save_seen(args.seen_file, seen)
            print(f"✅ {post['shortcode']} به seen اضافه شد.")
        # فاصله‌ی کوچیک بین دیسپچ‌ها، مؤدبانه با API گیت‌هاب
        time.sleep(3)


if __name__ == '__main__':
    main()
