import os
import time
import tempfile
import pyzipper
import logging
import sys
import asyncio
import math
import aiofiles
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait
from flask import Flask
import threading
from concurrent.futures import ThreadPoolExecutor

# ===== تنظیمات =====
API_ID = 2487823
API_HASH = "3ba2af01cad4bdd6138d15e353096e3f"
SESSION_STRING = "BAAcgIcAbm3Hdroaq-gHzwTUhklM4QhrzHSHm1uy_ZeMKXDmDamwhqFNGhK9zG_ZwyxF50TxLgez_a6zJ738_-qHLofVT3dgQCSdBHLuKPm39X46lRk1omWxBtmUEIpNzVZZJqEnyP32szYrHFkNx5IexSIyKWPURIx92AUeqBD6VKDRZJxs61Gq0U0-FSykY0a5sjEXp-3Mmz07sL7RYbCraYsdTsYx9n1EL1Bmg7IT-xpWeWpaEa0u4cmTkfJxpY03WwYDZ1J4zuCsYCNsauQrS2w7r3M6bNdTBAUIHPF8kSttPhnwEEFJQK-kLeK0aslMI-LzMhqS7nfR5fIhNM4wxFAHOAAAAAAK4sD3AA"
ALLOWED_USER_ID = 417536686
MAX_FILE_SIZE = 4197152000  # 2GB
MAX_TOTAL_SIZE = 4197152000  # 2GB
MAX_SPLIT_SIZE = 1990000000  # 1.99GB - برای حاشیه امنیت

# تنظیمات بهینه‌سازی سرعت
MAX_CONCURRENT_DOWNLOADS = 3  # کاهش تعداد دانلودهای همزمان برای جلوگیری از Flood
DOWNLOAD_CHUNK_SIZE = 512 * 1024  # 512KB chunk size
UPLOAD_CHUNK_SIZE = 512 * 1024  # 512KB chunk size
BUFFER_SIZE = 8 * 1024 * 1024  # 8MB بافر

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

# ===== ThreadPool برای عملیات I/O =====
io_executor = ThreadPoolExecutor(max_workers=4)

# ===== فانکشن‌های کمکی برای مدیریت FloodWait =====
async def safe_send_message(client, chat_id, text, **kwargs):
    """ارسال پیام با مدیریت FloodWait"""
    try:
        return await client.send_message(chat_id, text, **kwargs)
    except FloodWait as e:
        logger.warning(f"FloodWait: Waiting {e.value} seconds")
        await asyncio.sleep(e.value)
        return await client.send_message(chat_id, text, **kwargs)
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        raise

async def safe_reply_message(message, text, **kwargs):
    """Reply به پیام با مدیریت FloodWait"""
    try:
        return await message.reply_text(text, **kwargs)
    except FloodWait as e:
        logger.warning(f"FloodWait: Waiting {e.value} seconds")
        await asyncio.sleep(e.value)
        return await message.reply_text(text, **kwargs)
    except Exception as e:
        logger.error(f"Error replying to message: {e}")
        raise

async def safe_edit_message(message, text, **kwargs):
    """Edit پیام با مدیریت FloodWait"""
    try:
        return await message.edit_text(text, **kwargs)
    except FloodWait as e:
        logger.warning(f"FloodWait: Waiting {e.value} seconds")
        await asyncio.sleep(e.value)
        return await message.edit_text(text, **kwargs)
    except Exception:
        # اگر پیام حذف شده یا قابل edit نیست، خطا را نادیده بگیر
        pass

# ===== فانکشن‌های جدید برای تقسیم فایل =====
async def split_large_file(file_path, max_size=MAX_SPLIT_SIZE):
    """تقسیم فایل به چند part با استفاده از بافر بزرگ"""
    part_files = []
    file_size = os.path.getsize(file_path)
    
    if file_size <= max_size:
        return [file_path]  # نیازی به تقسیم نیست
    
    base_name = os.path.basename(file_path)
    
    async with aiofiles.open(file_path, 'rb') as f:
        part_num = 1
        while True:
            chunk = await f.read(max_size)
            if not chunk:
                break
                
            part_filename = f"{base_name}.part{part_num:03d}"
            part_path = os.path.join(os.path.dirname(file_path), part_filename)
            
            async with aiofiles.open(part_path, 'wb') as part_file:
                await part_file.write(chunk)
            
            part_files.append(part_path)
            part_num += 1
    
    await asyncio.get_event_loop().run_in_executor(io_executor, os.remove, file_path)
    return part_files

async def create_split_zip(files, zip_path, password, processing_msg):
    """ایجاد زیپ تقسیم شده با بهینه‌سازی سرعت"""
    try:
        with pyzipper.AESZipFile(
            zip_path, "w", 
            compression=pyzipper.ZIP_DEFLATED, 
            encryption=pyzipper.WZ_AES,
            compresslevel=6
        ) as zipf:
            zipf.setpassword(password.encode())
            
            total_files = len(files)
            for i, file_info in enumerate(files, 1):
                file_path = file_info["path"]
                file_name = file_info["name"]
                
                if os.path.getsize(file_path) > MAX_SPLIT_SIZE:
                    parts = await split_large_file(file_path)
                    for part_path in parts:
                        part_name = os.path.basename(part_path)
                        zipf.write(part_path, part_name)
                        await asyncio.get_event_loop().run_in_executor(io_executor, os.remove, part_path)
                else:
                    zipf.write(file_path, file_name)
                    await asyncio.get_event_loop().run_in_executor(io_executor, os.remove, file_path)
                
                if i % 3 == 0 or i == total_files:
                    progress_text = f"⏳ در حال فشرده سازی... {i}/{total_files}"
                    await safe_edit_message(processing_msg, progress_text)
                
        return True
    except Exception as e:
        logger.error(f"Error creating split zip: {e}")
        return False

# ===== فانکشن‌های اصلی =====
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
    
    speed_mb = speed / (1024 * 1024)
    
    text = f"""
🚀 {stage} فایل...

{bar} {percent}%

📦 {current//1024//1024}MB / {total//1024//1024}MB
⚡️ سرعت: {speed_mb:.2f} MB/s
⏳ زمان باقی‌مانده: {eta}s
    """
    await safe_edit_message(message, text)

async def download_file_with_retry(client, file_msg, file_path, progress_callback):
    """دانلود فایل با قابلیت retry و بهینه‌سازی سرعت"""
    max_retries = 2
    for attempt in range(max_retries):
        try:
            await client.download_media(
                file_msg, 
                file_path, 
                progress=progress_callback,
                block=False,
            )
            return True
        except FloodWait as e:
            logger.warning(f"FloodWait during download: {e}")
            await asyncio.sleep(e.value)
        except Exception as e:
            if attempt == max_retries - 1:
                raise e
            await asyncio.sleep(1)
    return False

async def download_files_sequentially(client, files_to_process, processing_msg, tmp_dir):
    """دانلود فایل‌ها به صورت متوالی برای جلوگیری از Flood"""
    files_to_zip = []
    
    for i, finfo in enumerate(files_to_process, 1):
        file_msg = finfo["message"]
        file_name = finfo["file_name"]
        file_path = os.path.join(tmp_dir, file_name)
        
        async def progress_callback(current, total):
            await progress_bar(current, total, processing_msg, time.time(), f"دانلود فایل {i}")
        
        try:
            success = await download_file_with_retry(client, file_msg, file_path, progress_callback)
            if success and os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                files_to_zip.append({"path": file_path, "name": file_name})
                
                progress_text = f"✅ دانلود فایل {i} تکمیل شد"
                await safe_edit_message(processing_msg, progress_text)
                await asyncio.sleep(1)  # تاخیر بین دانلودها
                
        except Exception as e:
            logger.error(f"Error downloading file {file_name}: {e}")
    
    return files_to_zip

# ===== هندلرها =====
async def start(client, message):
    if not is_user_allowed(message.from_user.id):
        return
    try:
        await safe_reply_message(message,
            "سلام 👋\nفایل‌تو بفرست تا برات زیپ کنم.\n"
            "💡 کپشن فایل = pass=رمز برای تعیین پسورد (اختیاری)\n"
            f"📦 حداکثر حجم هر فایل: نامحدود (فایل‌های بزرگ به صورت خودکار تقسیم می‌شوند)\n"
            f"📦 حداکثر حجم کل: {MAX_TOTAL_SIZE//1024//1024}MB\n"
            "🔧 فایل‌های بزرگتر از 2GB به صورت خودکار تقسیم می‌شوند\n"
            "بعد از ارسال فایل‌ها دستور /zip رو بزن تا ابتدا پسورد و سپس اسم فایل نهایی را وارد کنی."
        )
    except Exception as e:
        logger.error(f"Error in start handler: {e}")

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
    try:
        await safe_reply_message(message, f"✅ فایل دریافت شد: {file_name}\n📦 حجم: {size_mb}MB")
    except Exception as e:
        logger.error(f"Error confirming file receipt: {e}")

async def start_zip(client, message):
    if not is_user_allowed(message.from_user.id): 
        return
    user_id = message.from_user.id
    if user_id not in user_files or not user_files[user_id]:
        try:
            await safe_reply_message(message, "❌ هیچ فایلی برای زیپ کردن وجود ندارد.")
        except Exception as e:
            logger.error(f"Error in start_zip: {e}")
        return
    
    total_size = sum(f["file_size"] for f in user_files[user_id])
    if total_size > MAX_TOTAL_SIZE:
        try:
            await safe_reply_message(message, f"❌ حجم کل فایل‌ها بیش از حد مجاز است! ({MAX_TOTAL_SIZE//1024//1024}MB)")
        except Exception as e:
            logger.error(f"Error in size check: {e}")
        user_files[user_id] = []
        return
        
    try:
        await safe_reply_message(message, "🔐 لطفاً رمز عبور برای فایل زیپ وارد کن:\n❌ برای لغو /cancel را بزنید")
        waiting_for_password[user_id] = True
    except Exception as e:
        logger.error(f"Error requesting password: {e}")

async def cancel_zip(client, message):
    user_id = message.from_user.id
    if user_id in user_files: 
        user_files[user_id] = []
    waiting_for_password.pop(user_id, None)
    waiting_for_filename.pop(user_id, None)
    zip_password_storage.pop(user_id, None)
    try:
        await safe_reply_message(message, "❌ عملیات لغو شد.")
    except Exception as e:
        logger.error(f"Error in cancel: {e}")

def non_command_filter(_, __, message: Message):
    return message.text and not message.text.startswith('/')
non_command = filters.create(non_command_filter)

async def process_zip(client, message):
    user_id = message.from_user.id
    
    # مرحله پسورد
    if user_id in waiting_for_password and waiting_for_password[user_id]:
        zip_password = message.text.strip()
        if not zip_password:
            try:
                await safe_reply_message(message, "❌ رمز عبور نمی‌تواند خالی باشد.")
            except Exception as e:
                logger.error(f"Error in password validation: {e}")
            return
        
        zip_password_storage[user_id] = zip_password
        waiting_for_password.pop(user_id, None)
        waiting_for_filename[user_id] = True
        
        try:
            await safe_reply_message(message, "📝 حالا اسم فایل زیپ نهایی را وارد کن (بدون .zip)")
        except Exception as e:
            logger.error(f"Error requesting filename: {e}")
        return
    
    # مرحله اسم فایل
    if user_id in waiting_for_filename and waiting_for_filename[user_id]:
        zip_name = message.text.strip()
        if not zip_name:
            try:
                await safe_reply_message(message, "❌ اسم فایل نمی‌تواند خالی باشد.")
            except Exception as e:
                logger.error(f"Error in filename validation: {e}")
            return
        
        waiting_for_filename.pop(user_id, None)
        zip_password = zip_password_storage.pop(user_id, None)
        
        try:
            processing_msg = await safe_reply_message(message, "⏳ در حال ایجاد فایل زیپ...")
        except Exception as e:
            logger.error(f"Error creating processing message: {e}")
            return
        
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                # دانلود فایل‌ها به صورت متوالی
                files_to_zip = await download_files_sequentially(
                    client, user_files[user_id], processing_msg, tmp_dir
                )
                
                if not files_to_zip:
                    await safe_reply_message(message, "❌ خطایی در دانلود فایل‌ها رخ داد.")
                    return
                
                # ایجاد زیپ
                zip_file_name = f"{zip_name}.zip"
                zip_path = os.path.join(tmp_dir, zip_file_name)
                
                success = await create_split_zip(files_to_zip, zip_path, zip_password, processing_msg)
                
                if success and os.path.exists(zip_path):
                    # آپلود زیپ
                    start_time = time.time()
                    
                    async def upload_progress(current, total):
                        await progress_bar(current, total, processing_msg, start_time, "آپلود")
                    
                    try:
                        await client.send_document(
                            message.chat.id,
                            zip_path,
                            caption=f"✅ فایل زیپ آماده شد!\n🔑 رمز: `{zip_password}`\n📦 تعداد فایل‌ها: {len(files_to_zip)}",
                            progress=upload_progress,
                            force_document=True
                        )
                    except FloodWait as e:
                        logger.warning(f"FloodWait during upload: {e}")
                        await asyncio.sleep(e.value)
                        await client.send_document(
                            message.chat.id,
                            zip_path,
                            caption=f"✅ فایل زیپ آماده شد!\n🔑 رمز: `{zip_password}`\n📦 تعداد فایل‌ها: {len(files_to_zip)}",
                            force_document=True
                        )
                else:
                    await safe_reply_message(message, "❌ خطایی در ایجاد فایل زیپ رخ داد.")
                    
        except Exception as e:
            logger.error(f"Error in zip process: {e}", exc_info=True)
            try:
                await safe_reply_message(message, f"❌ خطایی رخ داد: {str(e)}")
            except:
                pass
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
        in_memory=True,
        max_concurrent_transmissions=2  # کاهش انتقال همزمان
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
        finally:
            io_executor.shutdown()
    
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()
    
    # اجرای Flask در thread اصلی
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"Starting Flask web server on port {port}...")
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
