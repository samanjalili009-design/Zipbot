import os
import time
import tempfile
import pyzipper
import logging
import sys
import asyncio
import math
from pyrogram import Client, filters
from pyrogram.types import Message
from flask import Flask
import threading

# ===== تنظیمات =====
API_ID = 26180086
API_HASH = "d91e174c7faf0e5a6a3a2ecb0b3361f6"
SESSION_STRING = "BAGPefYAEAaXaj52wzDLPF0RSfWtF_Slk8nFWzYAHS9vu-HBxRUz9yLnq7m8z-ajYCQxQZO-5aNX0he9OttDjmjieYDMbDjBJtbsOT2ZwsQNe8UCAo5oFPveD5V1H0cIBMlXCG1P49G2oonf1YL1r16Nt34AJLkmzDIoFD0hhxwVBXvrUGwZmEoTtdkfORCYUMGACKO4-Al-NH35oVCkTIqmXQ5DUp9PVx6DND243VW5Xcqay7qwrwfoS4sWRA-7TMXykbHa37ZsdcCOf0VS8e6PyaYvG5BjMCd9BGRnR9IImrksYY2uBM2Bg42MLaa1WFxQtn97p5ViPF9c1MpY49bc5Gm5lwAAAAF--TK5AA"
ALLOWED_USER_ID = 417536686
MAX_TOTAL_SIZE = 4197152000   # حدود 4GB
MAX_SPLIT_SIZE = 1500 * 1024 * 1024  # 1.5GB (برای امنیت و جلوگیری از fail آپلود)

# ===== لاگ =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ===== کلاینت Pyrogram =====
app = None

# ===== داده‌ها =====
user_files = {}
waiting_for_password = {}
waiting_for_filename = {}
zip_password_storage = {}

# ===== فانکشن تقسیم فایل =====
async def split_large_file(file_path, max_size=MAX_SPLIT_SIZE):
    """تقسیم فایل به چند part"""
    part_files = []
    file_size = os.path.getsize(file_path)

    if file_size <= max_size:
        return [file_path]

    num_parts = math.ceil(file_size / max_size)
    base_name = os.path.basename(file_path)

    with open(file_path, "rb") as f:
        for part_num in range(1, num_parts + 1):
            chunk = f.read(max_size)
            if not chunk:
                break
            part_filename = f"{base_name}.part{part_num:03d}"
            part_path = os.path.join(os.path.dirname(file_path), part_filename)
            with open(part_path, "wb") as part_file:
                part_file.write(chunk)
            part_files.append(part_path)

    os.remove(file_path)  # پاک کردن فایل اصلی
    return part_files

async def create_zip(files, zip_path, password, processing_msg):
    """ایجاد زیپ (بدون تقسیم داخلی)"""
    try:
        with pyzipper.AESZipFile(
            zip_path, "w",
            compression=pyzipper.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES
        ) as zipf:
            zipf.setpassword(password.encode())

            total_files = len(files)
            for i, file_info in enumerate(files, 1):
                zipf.write(file_info["path"], file_info["name"])
                os.remove(file_info["path"])

                progress_text = f"⏳ فشرده‌سازی... {i}/{total_files}"
                try:
                    await processing_msg.edit_text(progress_text)
                except:
                    pass
        return True
    except Exception as e:
        logger.error(f"Error creating zip: {e}", exc_info=True)
        return False

# ===== فانکشن اصلی =====
def is_user_allowed(user_id: int) -> bool:
    return user_id == ALLOWED_USER_ID

async def progress_bar(current, total, message: Message, start_time, stage="دانلود"):
    now = time.time()
    diff = max(now - start_time, 1)
    percent = int(current * 100 / total)
    speed = current / diff
    eta = int((total - current) / speed) if speed > 0 else 0
    bar_filled = int(percent / 5)
    bar = "▓" * bar_filled + "░" * (20 - bar_filled)
    text = f"""
🚀 {stage} فایل...

{bar} {percent}%

📦 {current//1024//1024}MB / {total//1024//1024}MB
⚡️ سرعت: {round(speed/1024,2)} KB/s
⏳ زمان باقی‌مانده: {eta}s
    """
    try:
        await message.edit_text(text)
    except:
        pass

# ===== هندلرها =====
async def start(client, message):
    if not is_user_allowed(message.from_user.id):
        return await message.reply_text("❌ دسترسی ندارید.")
    await message.reply_text(
        "سلام 👋\n"
        "فایل‌تو بفرست تا برات زیپ کنم.\n"
        "💡 کپشن فایل = pass=رمز برای تعیین پسورد (اختیاری)\n"
        f"📦 حداکثر حجم کل: {MAX_TOTAL_SIZE//1024//1024}MB\n"
        "🔧 فایل‌های بزرگتر از 1.5GB خودکار تقسیم می‌شوند.\n"
        "بعد از ارسال فایل‌ها دستور /zip رو بزن."
    )

async def handle_file(client, message):
    if not is_user_allowed(message.from_user.id):
        return
    doc = message.document
    if not doc:
        return
    file_name = doc.file_name or f"file_{message.id}"
    caption = message.caption or ""
    password = None
    if "pass=" in caption:
        password = caption.split("pass=", 1)[1].split()[0].strip()

    user_id = message.from_user.id
    if user_id not in user_files:
        user_files[user_id] = []
    user_files[user_id].append({
        "message": message,
        "file_name": file_name,
        "password": password,
        "file_size": doc.file_size
    })

    size_mb = doc.file_size // 1024 // 1024
    await message.reply_text(f"✅ دریافت شد: {file_name}\n📦 حجم: {size_mb}MB")

async def start_zip(client, message):
    if not is_user_allowed(message.from_user.id):
        return
    user_id = message.from_user.id
    if user_id not in user_files or not user_files[user_id]:
        return await message.reply_text("❌ هیچ فایلی برای زیپ کردن موجود نیست.")

    total_size = sum(f["file_size"] for f in user_files[user_id])
    if total_size > MAX_TOTAL_SIZE:
        await message.reply_text(f"❌ حجم کل فایل‌ها بیش از حد مجاز است! ({MAX_TOTAL_SIZE//1024//1024}MB)")
        user_files[user_id] = []
        return

    await message.reply_text("🔐 لطفاً رمز عبور برای فایل زیپ وارد کن:")
    waiting_for_password[user_id] = True

async def cancel_zip(client, message):
    user_id = message.from_user.id
    user_files[user_id] = []
    waiting_for_password.pop(user_id, None)
    waiting_for_filename.pop(user_id, None)
    zip_password_storage.pop(user_id, None)
    await message.reply_text("❌ عملیات لغو شد.")

def non_command_filter(_, __, message: Message):
    return message.text and not message.text.startswith("/")
non_command = filters.create(non_command_filter)

async def process_zip(client, message):
    user_id = message.from_user.id

    # مرحله پسورد
    if user_id in waiting_for_password and waiting_for_password[user_id]:
        zip_password = message.text.strip()
        if not zip_password:
            return await message.reply_text("❌ رمز عبور نمی‌تواند خالی باشد.")
        zip_password_storage[user_id] = zip_password
        waiting_for_password.pop(user_id, None)
        waiting_for_filename[user_id] = True
        return await message.reply_text("📝 اسم فایل زیپ را وارد کن (بدون .zip)")

    # مرحله اسم فایل
    if user_id in waiting_for_filename and waiting_for_filename[user_id]:
        zip_name = message.text.strip()
        if not zip_name:
            return await message.reply_text("❌ اسم فایل نمی‌تواند خالی باشد.")
        waiting_for_filename.pop(user_id, None)
        processing_msg = await message.reply_text("⏳ در حال ایجاد فایل زیپ...")
        zip_password = zip_password_storage.pop(user_id, None)

        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                files_to_zip = []
                total_files = len(user_files[user_id])

                # دانلود همه فایل‌ها
                for i, finfo in enumerate(user_files[user_id], 1):
                    file_msg = finfo["message"]
                    file_name = finfo["file_name"]
                    file_path = os.path.join(tmp_dir, file_name)
                    start_time = time.time()
                    await client.download_media(
                        file_msg,
                        file_path,
                        progress=progress_bar,
                        progress_args=(processing_msg, start_time, "دانلود")
                    )
                    if os.path.exists(file_path):
                        files_to_zip.append({"path": file_path, "name": file_name})
                    try:
                        await processing_msg.edit_text(f"📥 دانلود {i}/{total_files}")
                    except:
                        pass

                # ساخت زیپ
                zip_file_name = f"{zip_name}.zip"
                zip_path = os.path.join(tmp_dir, zip_file_name)

                success = await create_zip(files_to_zip, zip_path, zip_password, processing_msg)

                if success and os.path.exists(zip_path):
                    # تقسیم و آپلود
                    parts = await split_large_file(zip_path, MAX_SPLIT_SIZE)
                    for idx, part in enumerate(parts, 1):
                        start_time = time.time()
                        await client.send_document(
                            message.chat.id,
                            part,
                            caption=f"📦 بخش {idx}/{len(parts)}\n🔑 رمز: `{zip_password}`",
                            progress=progress_bar,
                            progress_args=(processing_msg, start_time, "آپلود")
                        )
                        os.remove(part)
                else:
                    await message.reply_text("❌ خطا در ایجاد فایل زیپ.")
        except Exception as e:
            logger.error(f"Error in zip process: {e}", exc_info=True)
            await message.reply_text("❌ خطایی رخ داد.")
        finally:
            user_files[user_id] = []

# ===== اجرای ربات =====
async def run_bot():
    global app
    logger.info("Starting user bot...")

    app = Client(
        "user_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=SESSION_STRING,
        in_memory=True
    )

    app.on_message(filters.command("start"))(start)
    app.on_message(filters.document)(handle_file)
    app.on_message(filters.command("zip"))(start_zip)
    app.on_message(filters.command("cancel"))(cancel_zip)
    app.on_message(filters.text & non_command)(process_zip)

    await app.start()
    logger.info("Bot started successfully!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    web_app = Flask(__name__)

    @web_app.route("/")
    def home():
        return "Bot is running", 200

    @web_app.route("/health")
    def health_check():
        return "Bot is healthy", 200

    def start_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_bot())
        except Exception as e:
            logger.error(f"Bot error: {e}")

    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()

    port = int(os.environ.get("PORT", 10000))
    logger.info(f"Starting Flask server on port {port}...")
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
