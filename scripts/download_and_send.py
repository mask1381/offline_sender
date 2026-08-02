*** Begin Patch
*** Update File: scripts/download_and_send.py
@@
 def register_slide(shortcode, slide_index, chat_id, message_id):
@@
-    if not REGISTER_SLIDE_WEBHOOK_URL:
-        print('⚠️ REGISTER_SLIDE_WEBHOOK_URL تنظیم نشده — پیش‌فرض انتخاب اسلایدها ثبت نمی‌شه')
-        return
-    try:
-        requests.post(REGISTER_SLIDE_WEBHOOK_URL, json={
-            'shortcode': shortcode,
-            'slide_index': str(slide_index),
-            'chat_id': str(chat_id),
-            'message_id': str(message_id),
-            'selected': True,
-        }, timeout=15)
-    except Exception as e:
-        print(f'⚠️ ثبت اسلاید {slide_index} تو دیتا تیبل شکست خورد: {e}')
+    if not REGISTER_SLIDE_WEBHOOK_URL:
+        print('⚠️ REGISTER_SLIDE_WEBHOOK_URL تنظیم نشده — پیش‌فرض انتخاب اسلایدها ثبت نمی‌شه')
+        return False
+
+    payload = {
+        'shortcode': shortcode,
+        'slide_index': str(slide_index),
+        'chat_id': str(chat_id),
+        'message_id': str(message_id),
+        'selected': True,
+    }
+
+    try:
+        resp = requests.post(REGISTER_SLIDE_WEBHOOK_URL, json=payload, timeout=15)
+        if resp.status_code >= 300:
+            print(f'⚠️ ثبت اسلاید {slide_index} تو دیتا تیبل شکست خورد (status={resp.status_code}): {resp.text}')
+            return False
+        return True
+    except Exception as e:
+        print(f'⚠️ ثبت اسلاید {slide_index} تو دیتا تیبل شکست خورد: {e}')
+        return False
@@
 def send_preview(files, shortcode, chat_id):
     total = len(files)
-    control_message_ids = []  # پیام‌های کنترلی (تایید/رد) که باید بعد از اتمام پاک بشن
+    # NOTE: message IDs for slides and the control message are persisted to n8n's Data Table
+    # via REGISTER_SLIDE_WEBHOOK_URL by register_slide(). That Data Table is later used
+    # by the n8n workflow to delete preview messages when Confirm/Reject completes.
*** End Patch
