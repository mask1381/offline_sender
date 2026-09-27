# offline_sender

یک مجموعه ابزار Python برای دانلود محتوای اینستاگرام و ردیت و ارسال خودکار آن‌ها به تلگرام. این پروژه برای اجرای کوتاه‌مدت و بدون سرور دائمی (مثل GitHub Actions یا VM‌های موقت) طراحی شده است.

## خلاصه پروژه

این مخزن شامل چند اسکریپت مستقل است که هر کدام برای یکی از سناریوهای مختلف ارسال محتوا به تلگرام ساخته شده‌اند:

- دانلود پست‌های اینستاگرام/ردیت
- ارسال عکس، ویدیو و آلبوم چندتایی به کانال یا چت تلگرام
- بررسی دوره‌ای پیج‌های اینستاگرام
- تشخیص پست‌های جدید
- زمان‌بندی ارسال در بازه‌های مشخص
- تولید هشتگ‌های استاندارد برای کپشن کانال

## ساختار پوشه‌ها و فایل‌ها

```text
offline_sender/
├── README.md
├── requirements.txt
├── character_dictionary.csv
├── hashtag_rules.json
├── aniwall_pages.json
├── scripts/
│   ├── download_and_send2.py
│   ├── download_and_send3.py
│   ├── download_and_send4.py
│   ├── gacha_insta_check.py
│   └── aniwall_insta_check.py
└── .
```

## ابزارهای اصلی

### 1) scripts/download_and_send3.py

نسخه اصلی و عملیاتی برای دانلود و ارسال محتوا به تلگرام.

ویژگی‌ها:
- پشتیبانی از اینستاگرام و ردیت
- تشخیص خودکار نوع لینک
- دانلود تصاویر، ویدیوها و پست‌های چنداسلایدی
- ارسال تکی یا آلبوم به تلگرام
- پشتیبانی از Anonymous و Login-based Instagram flows
- استفاده از `curl_cffi` برای تقلید از fingerprint مرورگر

### 2) scripts/download_and_send2.py

نسخه سبک‌تر و ساده‌تر نسبت به `download_and_send3.py`.

### 3) scripts/download_and_send4.py

نسخه‌ای که روی تولید کپشن هشتگ‌دار و ارسال با قالب کانال تمرکز دارد.

ویژگی‌ها:
- استخراج هشتگ‌های مرتبط از متن پست
- استفاده از `character_dictionary.csv`
- ساخت کپشن نهایی با هشتگ‌های استاندارد کانال
- حذف متن‌های زائد، لینک‌ها و تبلیغات از کپشن نهایی

### 4) scripts/gacha_insta_check.py

اسکریپت بررسی دوره‌ای پیج‌های اینستاگرام.

ویژگی‌ها:
- چک کردن پیج‌ها از فایل JSON
- شناسایی پست‌های جدید
- ثبت وضعیت هر پیج به‌صورت state-based
- ارسال موفق فقط بعد از آپدیت state و commit
- retry خودکار اگر ارسال شکست بخورد

### 5) scripts/aniwall_insta_check.py

نسخه‌ی شبانه برای کانال `aniwall_lab`.

ویژگی‌ها:
- جمع‌آوری پست‌های جدید از چند پیج
- زمان‌بندی ارسال در بازه‌های خاص روز
- جلوگیری از ارسال در ساعات خاص
- استفاده از منطق `download_and_send3.py` و `download_and_send4.py`

## وابستگی‌ها

فایل `requirements.txt` شامل موارد زیر است:

```txt
telethon
requests
cryptg
curl_cffi>=0.7.1
Pillow
git+https://github.com/instaloader/instaloader.git@refs/pull/2706/head
```

### توضیح کوتاه

- `telethon`: رابط کاربری برای ارسال محتوا از طریق تلگرام
- `requests`: درخواست‌های HTTP
- `cryptg`: بهینه‌سازی cryptography برای Telethon
- `curl_cffi`: ساخت fingerprint مرورگر برای بهتر شدن نرخ موفقیت درخواست‌ها
- `Pillow`: پردازش و اصلاح تصاویر
- `instaloader`: دانلود محتوای اینستاگرام

## نسخه Python

- Python 3.8+

## متغیرهای محیطی

### متغیرهای الزامی برای تلگرام

- `TG_API_ID` — API ID تلگرام
- `TG_API_HASH` — API HASH تلگرام
- `TG_SESSION_STRING` — StringSession Telethon

### متغیرهای اختیاری

- `IG_USERNAME` — نام کاربری اینستاگرام برای لاگین با سشن
- `APIFY_TOKEN` — توکن Apify برای اسکریپت‌های بررسی پیج
- `CHARACTER_DICTIONARY_PATH` — مسیر فایل `character_dictionary.csv`
- `HASHTAG_RULES_PATH` — مسیر فایل `hashtag_rules.json`

## نحوه نصب

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## مثال استفاده

### ارسال یک پست به تلگرام

```bash
export TG_API_ID="..."
export TG_API_HASH="..."
export TG_SESSION_STRING="..."

python scripts/download_and_send3.py \
  --url "https://www.instagram.com/p/SHORTCODE/" \
  --chat-id 123456789
```

### بررسی پیج‌های اینستاگرام

```bash
export APIFY_TOKEN="..."

python scripts/gacha_insta_check.py \
  --pages-file aniwall_pages.json \
  --chat-id 123456789
```

### زمان‌بندی انتشار برای کانال

```bash
python scripts/aniwall_insta_check.py \
  --pages-file aniwall_pages.json \
  --chat-id 123456789
```

## رفتارهای مهم

- اگر `download_and_send` در حالت send هیچ اسلایدی انتخاب نشود، عملیات متوقف می‌شود.
- اگر ارسال به تلگرام با FloodWait مواجه شود، اسکریپت متوقف می‌شود و وضعیت را گزارش می‌کند.
- در حالت‌های حساس، سشن اینستاگرام به‌صورت fallback به anonymous می‌افتد.
- اجرای پروژه به‌صورت VM موقت و بدون سرور دائمی طراحی شده است.

## کدهای خروجی

- `0` — اجرای موفق
- `1` — خطای ورودی، تنظیمات یا داده‌ها
- `2` — خطای FloodWait در تلگرام

## نکات فنی

- پروژه به‌صورت استاتیک و ephemeral طراحی شده است؛ یعنی معمولاً هر اجرا در محیطی کوتاه‌مدت و بدون حفظ دائمی وضعیت انجام می‌شود.
- برای کاهش بلاک شدن IP، از `curl_cffi` و تغییر IP/محیط اجرا استفاده می‌شود.
- منطق تشخیص پست جدید و commit وضعیت برای جلوگیری از گم‌شدن پست‌ها طراحی شده است.
- ساختار کدها به گونه‌ای است که اسکریپت‌های مختلف از یکدیگر استفاده می‌کنند، بدون اینکه کل منطق در یک فایل قرار بگیرد.

## لایسنس

در حال حاضر لایسنس رسمی برای این مخزن تعیین نشده است. اگر قصد انتشار عمومی پروژه را داشته باشید، توصیه می‌شود یک لایسنس مثل MIT یا Apache 2.0 اضافه شود.

---

این README بر اساس ساختار فعلی مخزن، اسکریپت‌های موجود و وابستگی‌های واقعی پروژه به‌روزرسانی شده است.
