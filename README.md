# راه‌اندازی (بدون سرور، رایگان، روی GitHub Actions)

## ۱. ساخت ریپازیتوری
یه ریپوی جدید تو گیت‌هاب بساز (می‌تونه public یا private باشه) و این سه فایل رو توش قرار بده:
- `scripts/download_and_send.py`
- `requirements.txt`
- `.github/workflows/download-send.yml`

## ۲. ساخت Personal Access Token (PAT)
برای اینکه n8n بتونه workflow رو تریگر کنه:
1. برو به GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens
2. یه توکن جدید بساز با دسترسی `Actions: Read and write` روی همون ریپو
3. توکن رو یه جای امن ذخیره کن (فقط یه‌بار نشونش می‌ده)

## ۳. تنظیم Secrets روی ریپو
برو به ریپو → Settings → Secrets and variables → Actions → New repository secret، و این سه‌تا رو اضافه کن:
- `TG_API_ID`
- `TG_API_HASH`
- `TG_SESSION_STRING`

(همون مقادیری که تو `.env` سیستم لوکالت داشتی)

## ۴. تغییر ورک‌فلوی n8n
نودهای `Get Info` → `caption` → `HTTP Request2` → `HTTP Request3` رو حذف کن (دیگه لازم نیستن، چون همه‌ی این منطق رفته تو اسکریپت پایتون).

به‌جاشون فقط یه HTTP Request node بساز که مستقیم بعد از `Code in JavaScript1` بیاد:

**Method:** POST
**URL:** `https://api.github.com/repos/USERNAME/REPO_NAME/actions/workflows/download-send.yml/dispatches`

**Headers:**
```
Authorization: Bearer YOUR_PAT_TOKEN
Accept: application/vnd.github+json
```

**Body (JSON):**
```json
{
  "ref": "main",
  "inputs": {
    "url": "{{ $json.normalizedUrl }}",
    "chat_id": "182267768"
  }
}
```

نکته: `USERNAME`، `REPO_NAME`، `YOUR_PAT_TOKEN` و `ref` (اسم برنچ اصلی، معمولاً `main`) رو با مقادیر خودت جایگزین کن. `Insert row` رو هم بعد از همین HTTP Request بذار تا مثل قبل تو data table ثبت بشه.

## ۵. تست
اول از تب Actions تو گیت‌هاب، دستی روی "Run workflow" بزن و یه لینک/chat_id تست بده تا مطمئن بشی همه‌چی درست کار می‌کنه، بعد وصلش کن به n8n.

## نکات مهم
- هر اجرا از صفر یه ماشین مجازی می‌سازه (چیزی ذخیره نمی‌مونه بین اجراها) - این عمداً همینه، چون قرار نیست سرور دائمی داشته باشیم
- محدودیت زمانی هر اجرا `timeout-minutes: 60` گذاشته شده، کافیه برای اکثر ویدیوها
- اگه کوکی یوتیوب لازم داری، باید فایل `cookies.txt` رو هم به ریپو اضافه کنی (یا از Secret استفاده کنی و تو یه step قبل از اجرای اسکریپت بنویسیش رو دیسک)
