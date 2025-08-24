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
SESSION_STRING = "BAAcgIcAHABwNKUs8BNA4PbOH3k5sCClUxn30ELuBYsvUUINEey2ngYfCnQnP-Ckxi9iaWV7Xe5LJ7O1x71tQDpBckFKGNecQmlPJFxupKqhHPmC2mXdrDHLSDo20Jn5lcNc1Hh-vv_sgo72-EGEV1TIbThKgXE53ncvudBNMaO6bKgFsKbs6HE8mIBlyNTsteQkSJ1-VQF32eA8m9DTNiDdwrO5D7YpEs-dImUTFLhMl744OwY_twzlB3-caCOH1AWhHkjKALpOxQ50ToCg9zZ2gBbKKXyWl3KTjhL7GHzNU0iDrpJLWSmdP4oqn9OU8pKxXhqj8rYqg6-6OKw9fyl1Jb7XngAAAAF--TK5AA"
ALLOWED_USER_ID = 417536686
MAX_FILE_SIZE = 2097152000  # 2GB
MAX_TOTAL_SIZE = 2097152000  # 2GB

# ===== لاگ =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
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

# ===== فانکشن‌ها =====
def is_user_allowed(user_id: int) -> bool:
    return user_id == ALLOWED_USER_ID

async def progress_bar(current, total, message: Message, start_time, stage="دانلود"):
    now = time.time()
    diff = now - start_time
    if diff == 0: diff = 1
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
    try: await message.edit_text(text)
    except: pass

# ===== هندلرها =====
async def start(client, message):
    if not is_user_allowed(message.from_user.id):
        return await message.reply_text("❌ دسترسی denied.")
    await message.reply_text(
        "سلام 👋\nفایل‌تو بفرست تا برات زیپ کنم.\n"
        "💡 کپشن فایل = pass=رمز برای تعیین پسورد (اختیاری)\n"
        f"📦 حداکثر حجم هر فایل: {MAX_FILE_SIZE//1024//1024}MB\n"
        f"📦 حداکثر حجم کل: {MAX_TOTAL_SIZE//1024//1024}MB\n"
        "بعد از ارسال فایل‌ها دستور /zip رو بزن تا ابتدا پسورد و سپس اسم فایل نهایی را وارد کنی."
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
        password = caption.split("pass=",1)[1].split()[0].strip()
    if doc.file_size > MAX_FILE_SIZE:
        return await message.reply_text(f"❌ حجم فایل بیش از حد مجاز است! ({MAX_FILE_SIZE//1024//1024}MB)")
    user_id = message.from_user.id
    if user_id not in user_files: user_files[user_id] = []
    user_files[user_id].append({"message": message, "file_name": file_name, "password": password, "file_size": doc.file_size})

async def start_zip(client, message):
    if not is_user_allowed(message.from_user.id): return
    user_id = message.from_user.id
    if user_id not in user_files or not user_files[user_id]:
        return await message.reply_text("❌ هیچ فایلی برای زیپ کردن وجود ندارد.")
    total_size = sum(f["file_size"] for f in user_files[user_id])
    if total_size > MAX_TOTAL_SIZE:
        await message.reply_text(f"❌ حجم کل فایل‌ها بیش از حد مجاز است! ({MAX_TOTAL_SIZE//1024//1024}MB)")
        user_files[user_id] = []
        return
    await message.reply_text("🔐 لطفاً رمز عبور برای فایل زیپ وارد کن:\n❌ برای لغو /cancel را بزنید")
    waiting_for_password[user_id] = True

async def cancel_zip(client, message):
    user_id = message.from_user.id
    if user_id in user_files: user_files[user_id] = []
    waiting_for_password.pop(user_id,None)
    waiting_for_filename.pop(user_id,None)
    zip_password_storage.pop(user_id,None)
    await message.reply_text("❌ عملیات لغو شد.")

def non_command_filter(_, __, message: Message):
    return message.text and not message.text.startswith('/')
non_command = filters.create(non_command_filter)

async def process_zip(client, message):
    user_id = message.from_user.id
    # مرحله پسورد
    if user_id in waiting_for_password and waiting_for_password[user_id]:
        zip_password = message.text.strip()
        if not zip_password:
            return await message.reply_text("❌ رمز عبور نمی‌تواند خالی باشد.")
        zip_password_storage[user_id] = zip_password
        waiting_for_password.pop(user_id,None)
        waiting_for_filename[user_id] = True
        return await message.reply_text("📝 حالا اسم فایل زیپ نهایی را وارد کن (بدون .zip)")
    
    # مرحله اسم فایل
    if user_id in waiting_for_filename and waiting_for_filename[user_id]:
        zip_name = message.text.strip()
        if not zip_name:
            return await message.reply_text("❌ اسم فایل نمی‌تواند خالی باشد.")
        waiting_for_filename.pop(user_id,None)
        processing_msg = await message.reply_text("⏳ در حال ایجاد فایل زیپ...")
        zip_password = zip_password_storage.pop(user_id,None)
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                zip_file_name = f"{zip_name}.zip"
                zip_path = os.path.join(tmp_dir, zip_file_name)
                with pyzipper.AESZipFile(zip_path,"w",compression=pyzipper.ZIP_DEFLATED,encryption=pyzipper.WZ_AES) as zipf:
                    zipf.setpassword(zip_password.encode())
                    total_files = len(user_files[user_id])
                    for i, finfo in enumerate(user_files[user_id],1):
                        file_msg = finfo["message"]
                        file_name = finfo["file_name"]
                        file_path = os.path.join(tmp_dir,file_name)
                        start_time = time.time()
                        await client.download_media(file_msg,file_path,progress=progress_bar,progress_args=(processing_msg,start_time,"دانلود"))
                        if os.path.exists(file_path) and os.path.getsize(file_path)>0:
                            zipf.write(file_path,file_name)
                        os.remove(file_path)
                start_time = time.time()
                await client.send_document(
                    message.chat.id,
                    zip_path,
                    caption=f"✅ فایل زیپ آماده شد!\n🔑 رمز: `{zip_password}`\n📦 تعداد فایل‌ها: {total_files}",
                    progress=progress_bar,
                    progress_args=(processing_msg,start_time,"آپلود")
                )
        except Exception as e:
            logger.error(f"Error in zip: {e}",exc_info=True)
            await message.reply_text("❌ خطایی رخ داد.")
        finally:
            user_files[user_id] = []

# ===== تابع برای اجرای ربات =====
async def run_bot():
    """تابعی که ربات را اجرا می‌کند"""
    global app
    logger.info("Starting user bot...")
    
    app = Client(
        "user_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=SESSION_STRING,
        in_memory=True
    )
    
    # اضافه کردن هندلرها
    app.on_message(filters.command("start"))(start)
    app.on_message(filters.document)(handle_file)
    app.on_message(filters.command("zip"))(start_zip)
    app.on_message(filters.command("cancel"))(cancel_zip)
    app.on_message(filters.text & non_command)(process_zip)
    
    await app.start()
    logger.info("Bot started successfully!")
    
    # منتظر ماندن تا ربات اجرا شود
    await asyncio.Event().wait()

# ===== اجرا =====
if __name__ == "__main__":
    # ایجاد وب سرور Flask
    web_app = Flask(__name__)
    
    @web_app.route('/')
    def home():
        return "Bot is running", 200
    
    @web_app.route('/health')
    def health_check():
        return "Bot is running", 200
    
    # اجرای ربات در یک thread جداگانه
    def start_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_bot())
        except Exception as e:
            logger.error(f"Bot error: {e}")
    
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()
    
    # اجرای Flask در thread اصلی
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"Starting Flask web server on port {port}...")
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
