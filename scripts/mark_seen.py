#!/usr/bin/env python3
"""
یه shortcode رو به seen_posts.json اضافه می‌کنه.

این اسکریپت جدا از check_new_posts_apify.py هست چون هدف اینه که یه پست
فقط زمانی seen بشه که واقعاً روش تصمیم گرفته شده (تایید یا رد از طریق
دکمه‌های تلگرام تو n8n) — نه فقط به‌خاطر این‌که پیش‌نمایششو فرستادیم.
اگه تصمیمی گرفته نشه، چک‌های بعدی (هر ۶/۱۲ ساعت) دوباره همین پست رو
می‌بینن و پیش‌نمایشش رو دوباره می‌فرستن.

استفاده:
  python mark_seen.py --shortcode DbQQCRRkrn- --seen-file data/seen_posts.json
"""

import argparse
import json
import os


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--shortcode', required=True)
    parser.add_argument('--seen-file', default='data/seen_posts.json')
    args = parser.parse_args()

    shortcode = args.shortcode.strip()
    seen = load_seen(args.seen_file)

    if shortcode in seen:
        print(f'ℹ️ {shortcode} از قبل تو seen بود.')
        return

    seen.add(shortcode)
    save_seen(args.seen_file, seen)
    print(f'✅ {shortcode} به seen اضافه شد (بعد از تصمیم‌گیری).')


if __name__ == '__main__':
    main()
