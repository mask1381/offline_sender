"""
تست واقعی سشن ذخیره‌شده - نسخه‌ی دو، با User-Agent سفارشی که با یه مرورگر
واقعی مطابقت داره. بعضی وقتا خطای 400 روی graphql/query به این دلیله که
User-Agent پیش‌فرض instaloader با چیزی که اینستاگرام پشت اون سشن دیده فرق
داره و درخواست رو رد می‌کنه.

استفاده:
  python test_session_v2.py -f session-genshinworldsensei -u genshinworldsensei -p "https://www.instagram.com/p/SHORTCODE/"
"""

import re
import sys
from argparse import ArgumentParser

import instaloader

# همون UA که موقع export کوکی، کروم واقعی‌ت داشت (اگه فرق داره، از
# chrome://version یا "what's my user agent" تو همون کروم بگیر و جایگزین کن)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def main():
    p = ArgumentParser()
    p.add_argument("-f", "--sessionfile", required=True)
    p.add_argument("-u", "--username", required=True)
    p.add_argument("-p", "--post-url", required=True)
    args = p.parse_args()

    L = instaloader.Instaloader(
        max_connection_attempts=1,
        quiet=False,
        user_agent=USER_AGENT,
    )
    try:
        L.load_session_from_file(args.username, args.sessionfile)
    except Exception as e:
        sys.exit(f"❌ لود سشن شکست خورد: {e}")

    match = re.search(r'/(?:p|reel|tv)/([^/?#]+)', args.post_url)
    if not match:
        sys.exit("❌ نتونستم shortcode رو از لینک استخراج کنم")
    shortcode = match.group(1)

    try:
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        print(f"✅ موفق! owner={post.owner_username}, is_video={post.is_video}, "
              f"mediacount={post.mediacount if hasattr(post, 'mediacount') else 1}")
    except Exception as e:
        sys.exit(f"❌ گرفتن اطلاعات پست شکست خورد: {e}")


if __name__ == "__main__":
    main()
