import os
import time
import tempfile
import pyzipper
import logging
import sys
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from flask import Flask
import threading

# ===== تنظیمات =====
API_ID = 1867911
API_HASH = "f9e86b274826212a2712b18754fabc47"
SESSION_STRING = "BAAcgIcAE08jkqQKFlTNOVn9T0SWvIjmGYv05OSBdjpq72ZAn4V7DEIYjiEQktbWKighncDdhRpNfHpLWECNro7vLFTnznTnHM_2xmsDGlrQ0jm8RZQRWHw7ATYg5ZIe9o7LG2ecqykOsxgrmeXZhEP4Szve_h7Djs2WZqBTx3raZgzLQpwMsl_7zD2jTmJTxZC6fZ6c3JnftfVSbqpuiHyUzxJqMcHXwNdlcp7arz5BvXpbfi8lfpqFafhK3Z1UAWwN0ip0ktMP7mAehNRFQi6bGpsd28v7UhMcjXCFKjl1O68KHmT8BIaM1hAo9t-VhkNCAb3irC55yfhHULqMHExDGp2d8gAAAAAY4xquAA"

ALLOWED_USER_ID = 417536686  # فقط آیدی خودت
MAX_FILE_SIZE = 2097152000   # 2GB
MAX_TOTAL_SIZE = 2097152000  # 2GB

# ===== لاگ =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ===== کلاینت Pyrogram =====
app = Client(
    session_name="userbot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

# ===== داده‌ها =====
user_files = {}
waiting_for_password = {}
waiting_for_filename = {}
zip_password_storage = {}

# ===== فانکشن‌ها =====
def is_user_allowed(user_id: int) -> bool:
    return user_id == ALLOWED_USER_ID

async def progress_bar(current, total, message: Message, start_time, stage="دانلود"):
    now = time.time()
    diff = now - start_time
    if diff == 0:
        diff = 1
    percent = int(current * 100 / total)
    speed = current / diff
    eta = int((total - current) / speed) if speed > 0 else 0
    bar_filled = int(percent / 5)
    bar = "▓" * bar_filled + "░" * (20 - bar_filled)
    text = f"""🚀 {stage} فایل...

{bar} {percent}%

📦 {current//1024//1024}MB / {total//1024//1024}MB
⚡️ سرعت: {round(speed/1024,2)} KB/s
⏳ زمان باقی‌مانده: {eta}s"""
    try:
        await message.edit_text(text)
    except:
        pass

# ===== هندلرها =====
@app.on_message(filters.command("start"))
async def start(client, message):
    if not is_user_allowed(message.from_user.id):
        return  # هیچ کاری نکن
    await message.reply_text(
        "سلام 👋\n"
        "فایل‌تو بفرست تا برات زیپ کنم.\n"
        "💡 کپشن فایل = pass=رمز برای تعیین پسورد (اختیاری)\n"
        f"📦 حداکثر حجم هر فایل: {MAX_FILE_SIZE//1024//1024}MB\n"
        f"📦 حداکثر حجم کل: {MAX_TOTAL_SIZE//1024//1024}MB\n"
        "بعد از ارسال فایل‌ها دستور /zip رو بزن تا ابتدا پسورد و سپس اسم فایل نهایی را وارد کنی."
    )

@app.on_message(filters.document)
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
    if doc.file_size > MAX_FILE_SIZE:
        return await message.reply_text(
            f"❌ حجم فایل بیش از حد مجاز است! ({MAX_FILE_SIZE//1024//1024}MB)"
        )
    user_id = message.from_user.id
    if user_id not in user_files:
        user_files[user_id] = []
    user_files[user_id].append({
        "message": message,
        "file_name": file_name,
        "password": password,
        "file_size": doc.file_size
    })

@app.on_message(filters.command("zip"))
async def start_zip(client, message):
    if not is_user_allowed(message.from_user.id):
        return
    user_id = message.from_user.id
    if user_id not in user_files or not user_files[user_id]:
        return await message.reply_text("❌ هیچ فایلی برای زیپ کردن وجود ندارد.")
    total_size = sum(f["file_size"] for f in user_files[user_id])
    if total_size > MAX_TOTAL_SIZE:
        user_files[user_id] = []
        return await message.reply_text(
            f"❌ حجم کل فایل‌ها بیش از حد مجاز است! ({MAX_TOTAL_SIZE//1024//1024}MB)"
        )
    await message.reply_text(
        "🔐 لطفاً رمز عبور برای فایل زیپ وارد کن:\n"
        "❌ برای لغو /cancel را بزنید"
    )
    waiting_for_password[user_id] = True

@app.on_message(filters.command("cancel"))
async def cancel_zip(client, message):
    user_id = message.from_user.id
    if user_id in user_files:
        user_files[user_id] = []
    waiting_for_password.pop(user_id, None)
    waiting_for_filename.pop(user_id, None)
    zip_password_storage.pop(user_id, None)
    await message.reply_text("❌ عملیات لغو شد.")

def non_command_filter(_, __, message: Message):
    return message.text and not message.text.startswith('/')

non_command = filters.create(non_command_filter)

@app.on_message(non_command)
async def process_zip(client, message):
    user_id = message.from_user.id
    if user_id != ALLOWED_USER_ID:
        return
    # مرحله پسورد
    if user_id in waiting_for_password and waiting_for_password[user_id]:
        zip_password = message.text.strip()
        if not zip_password:
            return await message.reply_text("❌ رمز عبور نمی‌تواند خالی باشد.")
        zip_password_storage[user_id] = zip_password
        waiting_for_password.pop(user_id, None)
        waiting_for_filename[user_id] = True
        return await message.reply_text("📝 حالا اسم فایل زیپ نهایی را وارد کن (بدون .zip)")

# ===== اجرای Flask برای alive =====
web_app = Flask(__name__)

@web_app.route("/")
def home():
    return "Userbot is running!"

def run_flask():
    web_app.run(host="0.0.0.0", port=8080)

# ===== اجرا =====
if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    logger.info("Starting user bot...")
    app.run()
