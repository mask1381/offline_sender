# -*- coding: utf-8 -*-
"""pages_store.py — پایش پیج‌ها با جدا کردن «تنظیمات» از «state».

مسئله (بخش ۱.۴ گزارش): قبل از این ماژول، config و state پیج‌ها در یک فایل
(مثل aniwall_pages.json) ذخیره می‌شدن و ادمین برای اضافه/حذف/ریست هر کاری
باید دستی JSON را ویرایش می‌کرد — و state خراب (مثلاً پست تکراری) راه ساده‌ی
ریست نداشت.

حالا دو فایل جدا:
  *_pages_config.json   ← ادمین ویرایش می‌کند (username / profile / enabled)
  *_pages_state.json    ← اسکریپت فقط state را می‌نویسد (last_post_id / last_post_taken_at)

ریست state = پاک کردن کلیدِ آن پیج در state file (یا حذف فایل) — تنظیمات هیچ
وقت دست نمی‌خورد. برای سازگاری، اگر فایل config پیدا نشود ولی فایل قدیمیِ
یک‌تکه (مثلا aniwall_pages.json) وجود داشته باشد، به‌عنوان config استفاده
می‌شود و state اول‌بار جدا می‌شود.
"""
import json
import os
import subprocess
import sys


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        print(f"❌ نمی‌تونم {path} رو بخونم: {e}")
        return None


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_pages(config_path: str, state_path: str) -> list:
    """config + state را می‌خواند و state را در آبجکتِ هر پیج merge می‌کند.

    config:  [{username, profile?, enabled?}, ...]
    state:   { username: {last_post_id, last_post_taken_at}, ... }

    fallback: اگر config_path نبود ولی فایل قدیمیِ یک‌تکه (همون config_path با
    پسوند قدیمی) بود، از آن به‌عنوان config استفاده می‌کند؛ state آن موقع خالی است.
    """
    pages = _read_json(config_path)
    if pages is None:
        # fallback به فایل قدیمیِ یک‌تکه — اگه اون جایی داشته باشه state هم داخلشه
        legacy = _read_json(config_path.replace("_config", ""))
        pages = legacy if legacy is not None else []
    if not isinstance(pages, list):
        print(f"❌ {config_path}: باید لیست باشد")
        return []

    state = _read_json(state_path)
    if not isinstance(state, dict):
        state = {}
    for p in pages:
        if not isinstance(p, dict) or not p.get("username"):
            continue
        s = state.get(p["username"])
        if isinstance(s, dict):
            if s.get("last_post_id"):
                p["last_post_id"] = s["last_post_id"]
            if s.get("last_post_taken_at"):
                p["last_post_taken_at"] = s["last_post_taken_at"]
    return pages


def save_state(state_path: str, pages: list) -> None:
    """فقط state را می‌نویسد (config هرگز توسط اسکریپت بازنویسی نمی‌شود)."""
    state = {}
    for p in pages:
        if not isinstance(p, dict) or not p.get("username"):
            continue
        piece = {}
        if p.get("last_post_id"):
            piece["last_post_id"] = p["last_post_id"]
        if p.get("last_post_taken_at"):
            piece["last_post_taken_at"] = p["last_post_taken_at"]
        if piece:
            state[p["username"]] = piece
    _write_json(state_path, state)


def reset_state(state_path: str) -> None:
    """ریست کامل state (برای یک پیج، کلید آن را حذف کن)."""
    _write_json(state_path, {})


def git_commit_and_push(files, message: str, bot_name: str = "insta-bot") -> bool:
    """کامیت و پوش فایل(های) state. اگه چیزی برای کامیت نبود، بی‌سروصدا True برمی‌گردونه.

    `files`: مسیر یا لیست مسیرها (config را ادمین خودش ویرایش/کامیت می‌کند؛
    این‌جا فقط state و شاید configِ تازه‌ساخته‌شده کامیت می‌شه).
    """
    if isinstance(files, str):
        files = [files]
    try:
        subprocess.run(["git", "config", "user.name", bot_name], check=True)
        subprocess.run(["git", "config", "user.email", f"{bot_name}@users.noreply.github.com"], check=True)
        for f in files:
            subprocess.run(["git", "add", "--", f], check=True)

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


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--reset-state":
        reset_state(sys.argv[2])
        print(f"✅ state {sys.argv[2]} پاک شد (config دست نخورد).")
