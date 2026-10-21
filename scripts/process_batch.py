#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
process_batch.py — پردازش یک batch که بات توی گروه خصوصی تلگرام گذاشته.

جریان:
  ۱) فایل batch رو با سشن تلگرام از گروه خصوصی می‌خونه (BATCH_CHAT_ID از secret).
  ۲) پروفایل‌ها و فوتر کانال از خود batch میان (هیچ‌کدوم توی ریپو نیست).
  ۳) هر پست با قواعد همون نوع کانال (gacha | aniwall | aninexus | gachaverse) پردازش و
     با schedule تلگرام فرستاده می‌شه. هر پست در صورت شکست یک‌بار دوباره امتحان می‌شه؛
     اگه باز شکست خورد، «رد‌شده (gave_up)» حساب می‌شه و می‌ریم سراغ بعدی.
  ۴) بعد از هر پست یک پیام /ack توی همون گروه می‌فرسته:
        /ack <batch_id> <idx> <sent|skipped|failed> <code|-> <when|->
        /ack <batch_id> done
  ۵) لاگ ران‌ها عمومیه، پس هیچ اسم پیج/کپشن/آی‌دی‌ای چاپ نمی‌شه: خروجی ماژول‌های قدیمی
     گرفته می‌شه و فقط توی گروه خصوصی (پیام «🧾») می‌ره.

منطق کپشن/دانلود/ارسال همون ماژول‌های قبلیه (download_and_send3/4 و ...)؛ این فایل فقط
جایگزین بخش‌های «چک پیج / state / commit» شده.
"""

import argparse
import asyncio
import contextlib
import importlib
import io
import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from telethon import TelegramClient                      # noqa: E402
from telethon.sessions import StringSession              # noqa: E402
from telethon.errors import FloodWaitError               # noqa: E402

SEND_GAP_SECONDS = 5.0     # فاصله‌ی واقعی بین دو پست (محض اطمینان)
LOG_TAIL = 3000            # چند کاراکتر آخر لاگ ماژول‌ها به گروه خصوصی می‌ره

d3 = d4 = gch = anw = anx = gv = None   # ماژول‌های پروژه (بعد از load_modules)


def load_modules():
    """ماژول‌ها موقع import چیزی چاپ نکنن (لاگ عمومیه)."""
    global d3, d4, gch, anw, anx, gv
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        d3 = importlib.import_module("download_and_send3")
        d4 = importlib.import_module("download_and_send4")
        gch = importlib.import_module("gacha_insta_check")
        anw = importlib.import_module("aniwall_insta_check")
        anx = importlib.import_module("aninexus_insta_check")
        gv = importlib.import_module("gachaverse_insta_check")


# ─────────────────────────────────────────
# تلگرام (سشن یوزربات) — هر عملیات یک اتصال کوتاه، پشت‌سرهم (نه هم‌زمان)
# ─────────────────────────────────────────
async def _connect():
    client = TelegramClient(
        StringSession(os.environ["TG_SESSION_STRING"].strip()),
        int(os.environ["TG_API_ID"].strip()),
        os.environ["TG_API_HASH"].strip(),
    )
    await client.connect()
    return client


async def _resolve(client, chat_id):
    value = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
    try:
        return await client.get_input_entity(value)
    except ValueError:
        await client.get_dialogs()
        return await client.get_input_entity(value)


async def _fetch_batch(chat_id, message_id):
    client = await _connect()
    try:
        entity = await _resolve(client, chat_id)
        msg = await client.get_messages(entity, ids=int(message_id))
        if not msg or not msg.media:
            raise RuntimeError("batch message not found")
        data = await client.download_media(msg, file=bytes)
        return json.loads(data.decode("utf-8"))
    finally:
        await client.disconnect()


async def _say(chat_id, text):
    client = await _connect()
    try:
        entity = await _resolve(client, chat_id)
        await client.send_message(entity, text, parse_mode=None)
    finally:
        await client.disconnect()


def say(chat_id, text) -> bool:
    for _ in range(3):
        try:
            asyncio.run(_say(chat_id, str(text)[:4000]))
            return True
        except FloodWaitError as e:
            time.sleep(min(e.seconds + 1, 60))
        except Exception as e:                       # فقط اسم خطا (لاگ عمومیه)
            print(f"say failed: {type(e).__name__}")
            time.sleep(3)
    return False


# ─────────────────────────────────────────
# اجرای بی‌صدا + retry
# ─────────────────────────────────────────
def run_quiet(fn, *args, **kwargs):
    """-> (result, captured_log, error|None). stdout/stderr ماژول‌ها گرفته می‌شه."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            return fn(*args, **kwargs), buf.getvalue(), None
    except KeyboardInterrupt:
        raise
    except BaseException as e:                       # SystemExit از دانلود هم شکست حساب می‌شه
        return None, buf.getvalue(), e


def with_retry(fn):
    """دو تلاش؛ FloodWait تلاش دوباره نداره."""
    out, log, err = None, "", None
    for attempt in (1, 2):
        out, log, err = run_quiet(fn)
        if err is None:
            return out, log, None
        if isinstance(err, FloodWaitError):
            break
        if attempt == 1:
            time.sleep(5)
    return None, log, err


def fmt_when(dt) -> str:
    if not dt:
        return "now"
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%MZ")


class Ctx:
    def __init__(self, batch, chat):
        self.batch = batch
        self.batch_id = batch["batch_id"]
        self.chat = chat
        self.target = batch.get("target_chat_id")
        self.counts = {"sent": 0, "skipped": 0, "failed": 0}
        self.blocked = set()       # صفحه‌هایی که توی مرحله‌ی آماده‌سازی شکست خوردن
        self.aborted = False       # FloodWait

    def ack(self, idx, status, code="-", when="-"):
        self.counts[status] += 1
        say(self.chat, f"/ack {self.batch_id} {idx} {status} {code or '-'} {when or '-'}")

    def private_log(self, idx, err, log):
        tail = (log or "")[-LOG_TAIL:]
        say(self.chat, f"🧾 {self.batch_id} #{idx}\n{type(err).__name__}: {str(err)[:300]}\n\n{tail}")


# ─────────────────────────────────────────
# ساخت plan و ارسال (مشترک بین نوع‌ها)
# ─────────────────────────────────────────
def make_entry(p):
    post = {
        **p,
        "takenAt": p.get("taken_at"),
        "url": f"https://www.instagram.com/p/{p['shortcode']}/",
        "media_items": list(p.get("media_items") or []),
        "caption": p.get("caption") or "",
    }
    pid = p.get("page")
    return {
        "idx": p["idx"], "pid": pid, "post": post,
        "page": {"username": f"p{pid}", "profile": p.get("profile") or ""},   # نام خنثی (برای توابع aniwall)
        "profile": None, "skip": None, "caption": None, "cont": None, "when": None,
        "orig": len(post["media_items"]), "hold": False,
    }


def build_plan(ctx, posts, prepare):
    """prepare(e) روی هر پست: e["skip"] (کد رد) یا e["caption"] و ... رو پر می‌کنه."""
    plan = []
    for p in posts:
        e = make_entry(p)
        if e["pid"] in ctx.blocked:
            e["hold"] = True                     # بدون ack می‌مونه (دفعه‌ی بعد دوباره گرفته می‌شه)
            plan.append(e)
            continue
        try:
            e["profile"] = d3.resolve_profile(e["page"]["profile"])
        except KeyError:
            ctx.blocked.add(e["pid"])
            ctx.ack(e["idx"], "failed", "bad_profile")
            continue
        _, log, err = run_quiet(prepare, e)
        if err is not None:
            ctx.blocked.add(e["pid"])
            ctx.ack(e["idx"], "failed", "prepare")
            ctx.private_log(e["idx"], err, log)
            continue
        plan.append(e)
    return plan


def hold_from(plan, start):
    """از plan[start] به بعد، پست‌های همون صفحه‌ها رو نگه می‌داره (ack نمی‌شن)."""
    held = set()
    for i in range(start, len(plan)):
        e = plan[i]
        if i == start or e["pid"] in held:
            e["hold"] = True
            held.add(e["pid"])


def deliver(ctx, plan, sender):
    for e in plan:
        if ctx.aborted:
            break
        if e["hold"]:
            continue
        if e["skip"]:
            ctx.ack(e["idx"], "skipped", e["skip"])
            continue
        out, log, err = with_retry(lambda: sender(e))
        if err is None:
            if isinstance(out, tuple) and out and out[0] == "skipped":
                ctx.ack(e["idx"], "skipped", out[1])
            else:
                ctx.ack(e["idx"], "sent", "-", fmt_when(e.get("when") or (out[1] if isinstance(out, tuple) and len(out) > 1 else None)))
        elif isinstance(err, FloodWaitError):
            ctx.ack(e["idx"], "failed", "flood")
            ctx.private_log(e["idx"], err, log)
            ctx.aborted = True
        else:
            ctx.private_log(e["idx"], err, log)
            ctx.ack(e["idx"], "skipped", "gave_up")
        time.sleep(SEND_GAP_SECONDS)


# ─────────────────────────────────────────
# kind = gacha  (پورت send_post از gacha_insta_check، حالت channel)
# ─────────────────────────────────────────
def send_gacha(ctx, e):
    post, profile = e["post"], e["profile"]
    media_items = post["media_items"]
    if not media_items:
        return ("skipped", "no_media")

    orig_count = len(media_items)
    media_items = d3.apply_media_drops(media_items, profile)
    gch.reset_workdir()

    caption_raw = post["caption"]
    tags = d3.extract_hashtags(caption_raw)
    duration = d3.meta_duration(media_items)
    kind, _why = d3.decide_type(media_items, caption_raw, duration, profile)
    if kind is None:
        return ("skipped", "rule")

    files = d3.download_media_direct(media_items)
    files_after, replaced = gch.ensure_audio(files, post["url"], post["shortcode"])
    if files_after is None:
        files = d3.download_media_direct(media_items)
    else:
        files = files_after
        if replaced:
            start, end = d3.drop_range(orig_count, profile)
            if len(files) == orig_count and (start, end) != (0, orig_count):
                files = files[start:end]

    if profile.get("silent_audio"):
        files = gch.add_silent_audio(files)
    files = d3.crop_bottom_pct_files(files, profile.get("crop_bottom_pct"))

    thumb, video_meta = None, None
    if any((i.get("type") or "").lower() == "video" for i in media_items):
        thumb = d3.prepare_thumb(media_items, files)
        video_meta = None if replaced else [
            i for i in media_items if (i.get("type") or "").lower() == "video"
        ]

    if kind == "PENDING":
        ds = [d3.mp4_info(f)["duration"] for f in files if f.lower().endswith(".mp4")]
        ds = [d for d in ds if d]
        duration = max(ds) if ds else None
        kind, _why = d3.decide_type(media_items, caption_raw, duration, profile)
        if kind is None:
            return ("skipped", "rule")

    caption_final = d3.build_channel_caption(kind, caption_raw, tags, post["url"], profile)
    when = gch.spaced_schedule(d3.compute_schedule_dt(profile), profile.get("schedule_gap_min"))
    asyncio.run(d3.send_items_to_telegram(files, caption_final, ctx.target, thumb, video_meta, when))
    e["when"] = when
    return ("sent", when)


def run_gacha(ctx):
    plan = build_plan(ctx, ctx.batch["posts"], lambda e: None)
    deliver(ctx, plan, lambda e: send_gacha(ctx, e))


# ─────────────────────────────────────────
# kind = aniwall  (فقط عکس، کپشن فقط هشتگ، ساعت انتشار از پروفایل)
# ─────────────────────────────────────────
def prepare_aniwall(e):
    items = e["post"]["media_items"]
    if not items:
        e["skip"] = "no_media"
        return
    if any((i.get("type") or "").lower() == "video" for i in items):
        e["skip"] = "video"
        return
    caption, _why = d4.build_caption_for_profile(e["post"]["caption"], e["profile"])
    if caption is None:
        e["skip"] = "no_tags"
        return
    e["caption"] = caption


def run_aniwall(ctx):
    plan = build_plan(ctx, ctx.batch["posts"], prepare_aniwall)
    plan, log, err = run_quiet(anw.apply_photo_cap, plan)
    if err is not None:
        raise err
    send_entries = [e for e in plan if e["caption"] is not None and not e["hold"]]
    now = datetime.now(timezone.utc)
    times, log, err = run_quiet(anw.compute_times, send_entries, now)
    if err is not None:
        raise err
    for e, t in zip(send_entries, times):
        e["when"] = t

    def send(e):
        anw.reset_workdir()
        files = d3.download_media_direct(e["post"]["media_items"])
        asyncio.run(d4.send_photo_album_scheduled(files, e["caption"], ctx.target, schedule_dt=e["when"]))
        return ("sent", e["when"])

    deliver(ctx, plan, send)


# ─────────────────────────────────────────
# kind = aninexus  (پورت بدنه‌ی schedule_all از aninexus_insta_check)
# ─────────────────────────────────────────
def prepare_aninexus(e):
    post, profile = e["post"], e["profile"]
    reason = anx.skip_reason(post["caption"], profile)
    if reason:
        e["skip"] = "filtered"
        return
    post["media_items"] = d3.apply_media_drops(post["media_items"], profile)
    if not post["media_items"]:
        e["skip"] = "no_media"
        return
    caption, cont, _why = anx.safe_make_caption(post["caption"], profile, f"p{e['pid']}")
    e["caption"], e["cont"] = caption, cont


def send_aninexus(ctx, e):
    post = e["post"]
    sc = post["shortcode"]
    anx.reset_workdir()
    files = d3.download_media_direct(post["media_items"])

    files_after, replaced = gch.ensure_audio(files, post["url"], sc)
    if files_after is None:
        files = d3.download_media_direct(post["media_items"])
        replaced = False
    else:
        files = files_after
        if replaced:
            orig = e["orig"]
            start, end = d3.drop_range(orig, e["profile"])
            if (start, end) != (0, orig) and len(files) == orig:
                files = files[start:end]

    thumb, video_meta = None, None
    if any(f.lower().endswith(".mp4") for f in files):
        thumb = d3.prepare_thumb(post["media_items"], files)
        if not replaced:
            video_meta = [{"duration": i.get("duration")} for i in post["media_items"]
                          if (i.get("type") or "").lower() == "video"]
    asyncio.run(anx.send_scheduled(
        files, e["caption"], ctx.target, e["when"], thumb=thumb, video_meta=video_meta,
        continuation_caption=e.get("cont"),
    ))
    return ("sent", e["when"])


def run_aninexus(ctx):
    plan = build_plan(ctx, ctx.batch["posts"], prepare_aninexus)
    send_entries = [e for e in plan if e["caption"] is not None and not e["hold"]]
    times = d4.compute_schedule_times(datetime.now(timezone.utc), len(send_entries))
    for e, t in zip(send_entries, times):
        e["when"] = t
    deliver(ctx, plan, lambda e: send_aninexus(ctx, e))


# ─────────────────────────────────────────
# kind = gachaverse
# ─────────────────────────────────────────
def prepare_gachaverse(e):
    post = e["post"]
    if not post["media_items"]:
        e["skip"] = "no_media"
        return
    caption, _why = gv.build_game_caption(post["caption"], post["shortcode"])
    if caption is None:
        e["skip"] = "no_game"
        return
    e["caption"] = caption


def run_gachaverse(ctx):
    plan = build_plan(ctx, ctx.batch["posts"], prepare_gachaverse)

    # سقف پیام‌های زمان‌بندی‌شده‌ی تلگرام: بقیه بدون ack می‌مونن (دفعه‌ی بعد)
    sendable = 0
    for i, e in enumerate(plan):
        if e["caption"] is None or e["hold"]:
            continue
        sendable += 1
        if sendable > gv.MAX_SCHEDULED:
            hold_from(plan, i)
            break

    send_entries = [e for e in plan if e["caption"] is not None and not e["hold"]]
    times = d4.compute_schedule_times(datetime.now(timezone.utc), len(send_entries))
    for e, t in zip(send_entries, times):
        e["when"] = t

    def send(e):
        gv.send_entry({"post": e["post"], "caption": e["caption"]}, ctx.target, e["when"])
        return ("sent", e["when"])

    deliver(ctx, plan, send)


def run_selftest(ctx):
    """تست مسیر: هیچ‌چیز دانلود/ارسال نمی‌شه، فقط ack می‌ره (ماژول‌ها هم import شدن)."""
    for p in ctx.batch["posts"]:
        ctx.ack(p["idx"], "skipped", "test")


RUNNERS = {
    "selftest": run_selftest,
    "gacha": run_gacha,
    "aniwall": run_aniwall,
    "aninexus": run_aninexus,
    "gachaverse": run_gachaverse,
}


def apply_batch_config(batch):
    """پروفایل‌ها و فوتر از خود batch (هیچ‌کدوم توی ریپو نیست)."""
    d3.PROFILES.update(batch.get("profiles") or {})
    footer = (batch.get("footer_html") or "").strip()
    if footer:
        d3.CHANNEL_FOOTER = footer          # kind=gacha
        anx.FOOTER_HTML = footer            # kind=aninexus
        gv.FOOTER = "\n\n" + footer         # kind=gachaverse


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-id", required=True)
    ap.add_argument("--message-id", required=True)
    args = ap.parse_args()

    chat = os.environ.get("BATCH_CHAT_ID", "").strip()
    for k in ("TG_API_ID", "TG_API_HASH", "TG_SESSION_STRING", "BATCH_CHAT_ID"):
        if not os.environ.get(k, "").strip():
            print(f"missing env: {k}")
            return 1

    load_modules()
    ctx = None
    try:
        batch = asyncio.run(_fetch_batch(chat, args.message_id))
        if batch.get("batch_id") != args.batch_id:
            raise RuntimeError("batch id mismatch")
        apply_batch_config(batch)
        runner = RUNNERS.get(batch.get("kind"))
        if runner is None:
            raise RuntimeError("unknown kind")
        ctx = Ctx(batch, chat)
        print(f"batch loaded: {len(batch.get('posts') or [])} posts")
        try:
            runner(ctx)
        finally:
            say(chat, f"/ack {ctx.batch_id} done")
        c = ctx.counts
        print(f"finished: sent={c['sent']} skipped={c['skipped']} failed={c['failed']} aborted={ctx.aborted}")
        return 2 if ctx.aborted else 0
    except Exception as e:                                # فقط اسم خطا توی لاگ عمومی
        print(f"crashed: {type(e).__name__}")
        say(chat, f"❌ {args.batch_id} crashed: {type(e).__name__}: {str(e)[:300]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
