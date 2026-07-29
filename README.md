# yt-genshin — Instagram → Telegram (one-shot uploader) 🚀

A small CLI tool to download an Instagram post (single video, image, or multi-slide carousel) and send selected slides to a Telegram chat or channel. Designed to run as a short-lived job (e.g. in GitHub Actions): temporary files are cleaned up after the job finishes.

## Key features ✨

- Download Instagram posts with `instaloader` (anonymous or with a saved session).
- `preview` mode: send interactive preview messages via a Telegram Bot so an operator can toggle which slides to keep.
- `send` mode: send the selected slides as a media album using a Telegram user session (Telethon).
- Safety guard: in `send` mode, if `--slides` is empty or parses to no valid indices, the script will abort to avoid unintentionally sending everything.
- Caption cleanup: the original Instagram caption is cleaned from boilerplate/CTA blocks (e.g. cover art, hashtags, follow/like prompts) before being embedded in a blockquote in the final caption. The final caption is trimmed to respect Telegram's 1024-character limit.

## Repo layout 📁

- `scripts/download_and_send.py` — main script and logic

## Requirements 🧰

- Python 3.8+
- Dependencies (examples): `instaloader`, `telethon`, `pillow`, `requests`
- Optional: `curl_cffi` to better impersonate browser fingerprints and use proxies

Install (example):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Environment variables (important) 🔒

- `TG_API_ID` — Telethon API ID (required for `--mode send`)
- `TG_API_HASH` — Telethon API hash (required for `--mode send`)
- `TG_SESSION_STRING` — Telethon session string (required for `--mode send`)
- `TG_BOT_TOKEN` — Telegram Bot token (required for `--mode preview`)
- `REGISTER_SLIDE_WEBHOOK_URL` — (optional) webhook used to register each previewed slide as `selected=true` in your n8n Data Table
- `IG_PROXY_URL` — (optional) residential proxy for Instagram requests
- `IG_USERNAME` — (optional) Instagram username when loading a session file
- `CROP_BOTTOM_PERCENT` — percentage cropped from the bottom of images (default `0.10`)

## Usage

Preview mode (bot-driven selection) 💬

```bash
export TG_BOT_TOKEN="123:ABC..."
python scripts/download_and_send.py \
  --mode preview \
  --url "https://www.instagram.com/p/SHORTCODE/" \
  --chat-id <TELEGRAM_CHAT_ID>
```

- Sends each slide as a preview message with inline buttons.
- The script will call `REGISTER_SLIDE_WEBHOOK_URL` (if configured) to mark each slide as selected by default.

Send mode (final upload using Telethon) 📤

```bash
export TG_API_ID="..." TG_API_HASH="..." TG_SESSION_STRING="..."
python scripts/download_and_send.py \
  --mode send \
  --url "https://www.instagram.com/p/SHORTCODE/" \
  --chat-id <TELEGRAM_CHAT_ID> \
  --slides "1,3"
```

Important: In `--mode send` you MUST provide `--slides`. If `--slides` is empty or parses to no valid integers, the script will abort with an error and exit code `1` to prevent accidental mass-sending.

Error example when `--slides` is empty:

```
❌ هیچ اسلایدی انتخاب نشده - از ارسال خودداری شد
```

(That message is printed to stderr and the process exits with status code 1.)

## Caption cleanup rules 🧹

The script applies a cleaning step to the Instagram post's original caption before placing it inside a `<blockquote>` in the final caption. Behavior:

1. The caption is split into paragraph-like blocks by double-newlines (`\n\n`).
2. Any block that starts (case-insensitive) with a known boilerplate prefix is removed. The current conservative prefixes include:
   - `✦ Cover Art`
   - `✦ Hashtags`
   - `↠ Leaks and News`
   - `↠ Like & Follow`
   - `↠ Leaks`
   - `↠ Like`
3. Remaining blocks are re-joined with `\n\n` and used as the quoted original caption.
4. If the cleaned caption is not considered "meaningful" (heuristic `_caption_has_meaningful_text`), the blockquote is omitted entirely.
5. After building the full caption (header + optional blockquote + footer), it is truncated according to Telegram's 1024-character limit (the script calculates a budget for the blockquote so the final caption fits).

Notes:
- The script does NOT add the source username itself. If the original caption contains a `Source: ...` line, that line is preserved by the cleaning logic.
- The cleaning is intentionally conservative — it removes only recognized CTA/boilerplate blocks. You can extend the prefix list or change to regex-based matching if your source pages use other formats.

## Exit codes & behavior

- `0` — success
- `1` — user/configuration error (e.g. missing Telegram session variables, no slides selected in `send` mode, nothing downloaded)
- `2` — Telegram FloodWait (the script prints wait time and exits)

## Development & testing 🧪

- Unit tests are recommended for `clean_original_caption()` to validate removal rules against real captions from source pages.
- You can run local tests by mocking Telegram APIs (both bot and Telethon) to verify preview/send flows without contacting real services.

## Where changes were made

This README and the recent fixes (safety around `--slides` in `send` mode and `clean_original_caption`) live on the branch `fix/send-slides-caption-cleanup`.

## License

Add a license of your choice (e.g. MIT) in `LICENSE` if you plan to share publicly.

---

If you want, I can commit this README into the branch `fix/send-slides-caption-cleanup` now and then open a PR to merge it into `main`. Should I proceed?