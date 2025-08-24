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
from pyrogram.errors import FloodWait
from flask import Flask
import threading
import aiohttp
import aiofiles

# ===== تنظیمات =====
API_ID = 2487823
API_HASH = "3ba2af01cad4bdd6138d15e353096e3f"
SESSION_STRING = "BAAcgIcAbm3Hdroaq-gHzwTUhklM4QhrzHSHm1uy_ZeMKXDmDamwhqFNGhK9zG_ZwyxF50TxLgez_a6zJ738_-qHLofVT3dgQCSdBHLuKPm39X46lRk1omWxBtmUEIpNzVZZJqEnyP32szYrHFkNx5IexSIyKWPURIx92AUeqBD6VKDRZJxs61Gq0U0-FSykY0a5sjEXp-3Mmz07sL7RYbCraYsdTsYx9n1EL1Bmg7IT-xpWeWpaEa0u4cmTkfJxpY03WwYDZ1J4zuCsYCNsauQrS2w7r3M6bNdTBAUIHPF8kSttPhnwEEFJQK-kLeK0aslMI-LzMhqS7nfR5fIhNM4wxFAHOAAAAAAK4sD3AA"
ALLOWED_USER_ID = 417536686
MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024  # 10GB
MAX_TOTAL_SIZE = 20 * 1024 * 1024 * 1024  # 20GB
MAX_SPLIT_SIZE = 1990000000  # 1.99GB
CHUNK_SIZE = 10 * 1024 * 1024  # 10MB chunks برای سرعت بالاتر

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
download_speeds = {}

# ===== فانکشن‌های پیشرفته دانلود =====
async def download_file_with_max_speed(client, message, file_path, processing_msg):
    """دانلود فایل با حداکثر سرعت"""
    start_time = time.time()
    file_size = message.document.file_size
    downloaded = 0
    
    try:
        # استفاده از stream media برای دانلود chunk-based
        async with aiofiles.open(file_path, 'wb') as f:
            async for chunk in client.stream_media(message, chunk_size=CHUNK_SIZE):
                await f.write(chunk)
                downloaded += len(chunk)
                
                # آپدیت progress هر 5 ثانیه یا هر 50MB
                current_time = time.time()
                if current_time - start_time >= 5 or downloaded % (50 * 1024 * 1024) == 0:
                    await update_progress(downloaded, file_size, processing_msg, start_time, "دانلود")
                    start_time = current_time  # reset timer
        
        # آپدیت نهایی
        await update_progress(file_size, file_size, processing_msg, start_time, "دانلود")
        return True
        
    except Exception as e:
        logger.error(f"Download error: {e}")
        return False

async def update_progress(current, total, message: Message, start_time, stage="دانلود"):
    """آپدیت پیشرفت با بهینه‌سازی سرعت"""
    try:
        now = time.time()
        diff = now - start_time
        if diff == 0:
            diff = 1
            
        percent = int(current * 100 / total)
        speed = current / diff
        eta = int((total - current) / speed) if speed > 0 else 0
        
        # ذخیره سرعت برای نمایش
        download_speeds[message.chat.id] = speed
        
        bar_filled = int(percent / 5)
        bar = "▓" * bar_filled + "░" * (20 - bar_filled)
        
        text = f"""
🚀 {stage} فایل...

{bar} {percent}%

📦 {current//1024//1024}MB / {total//1024//1024}MB
⚡️ سرعت: {round(speed/1024/1024, 2)} MB/s
⏳ زمان باقی‌مانده: {eta}s
        """
        
        await message.edit_text(text)
        
    except Exception as e:
        logger.error(f"Progress update error: {e}")

# ===== فانکشن‌های تقسیم فایل =====
async def split_large_file(file_path, max_size=MAX_SPLIT_SIZE):
    """تقسیم فایل به چند part"""
    part_files = []
    file_size = os.path.getsize(file_path)
    
    if file_size <= max_size:
        return [file_path]
    
    num_parts = math.ceil(file_size / max_size)
    base_name = os.path.basename(file_path)
    
    with open(file_path, 'rb') as f:
        part_num = 1
        while True:
            chunk = f.read(max_size)
            if not chunk:
                break
                
            part_filename = f"{base_name}.part{part_num:03d}"
            part_path = os.path.join(os.path.dirname(file_path), part_filename)
            
            with open(part_path, 'wb') as part_file:
                part_file.write(chunk)
            
            part_files.append(part_path)
            part_num += 1
    
    os.remove(file_path)
    return part_files

async def create_split_zip(files, zip_path, password, processing_msg):
    """ایجاد زیپ تقسیم شده"""
    try:
        with pyzipper.AESZipFile(zip_path, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zipf:
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
                        os.remove(part_path)
                else:
                    zipf.write(file_path, file_name)
                    os.remove(file_path)
                
                progress_text = f"⏳ در حال فشرده سازی... {i}/{total_files}"
                try: 
                    await processing_msg.edit_text(progress_text)
                except: 
                    pass
                
        return True
    except Exception as e:
        logger.error(f"Error creating split zip: {e}")
        return False

# ===== فانکشن‌های اصلی =====
def is_user_allowed(user_id: int) -> bool:
    return user_id == ALLOWED_USER_ID

# ===== هندلرها =====
async def start(client, message):
    if not is_user_allowed(message.from_user.id):
        return await message.reply_text("❌ دسترسی denied.")
    await message.reply_text(
        "سلام 👋\nفایل‌تو بفرست تا برات زیپ کنم.\n"
        "💡 کپشن فایل = pass=رمز برای تعیین پسورد (اختیاری)\n"
        f"📦 حداکثر حجم هر فایل: {MAX_FILE_SIZE//1024//1024//1024}GB\n"
        f"📦 حداکثر حجم کل: {MAX_TOTAL_SIZE//1024//1024//1024}GB\n"
        "🔧 فایل‌های بزرگتر از 2GB به صورت خودکار تقسیم می‌شوند\n"
        "⚡️ سرعت دانلود: تا 10MB/s\n"
        "بعد از ارسال فایل‌ها دستور /zip رو بزن"
    )

async def handle_file(client, message):
    if not is_user_allowed(message.from_user.id):
        return
    if not message.document:
        return
        
    doc = message.document
    file_name = doc.file_name or f"file_{message.id}"
    caption = message.caption or ""
    password = None
    
    if "pass=" in caption:
        try:
            password = caption.split("pass=",1)[1].split()[0].strip()
        except:
            pass
    
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
    size_gb = round(doc.file_size / (1024 * 1024 * 1024), 2)
    await message.reply_text(f"✅ فایل دریافت شد: {file_name}\n📦 حجم: {size_mb}MB ({size_gb}GB)")

async def start_zip(client, message):
    if not is_user_allowed(message.from_user.id): 
        return
        
    user_id = message.from_user.id
    if user_id not in user_files or not user_files[user_id]:
        return await message.reply_text("❌ هیچ فایلی برای زیپ کردن وجود ندارد.")
    
    total_size = sum(f["file_size"] for f in user_files[user_id])
    if total_size > MAX_TOTAL_SIZE:
        await message.reply_text(f"❌ حجم کل فایل‌ها بیش از حد مجاز است! ({MAX_TOTAL_SIZE//1024//1024//1024}GB)")
        user_files[user_id] = []
        return
        
    await message.reply_text("🔐 لطفاً رمز عبور برای فایل زیپ وارد کن:\n❌ برای لغو /cancel را بزنید")
    waiting_for_password[user_id] = True

async def cancel_zip(client, message):
    user_id = message.from_user.id
    if user_id in user_files: 
        user_files[user_id] = []
    waiting_for_password.pop(user_id, None)
    waiting_for_filename.pop(user_id, None)
    zip_password_storage.pop(user_id, None)
    await message.reply_text("❌ عملیات لغو شد.")

# فیلتر برای پیام‌های متنی غیرکامندی
@filters.create
async def non_command_filter(_, client, message):
    return message.text and not message.text.startswith('/')

async def process_zip(client, message):
    user_id = message.from_user.id
    
    if not message.text:
        return
    
    # مرحله پسورد
    if user_id in waiting_for_password and waiting_for_password[user_id]:
        zip_password = message.text.strip()
        if not zip_password:
            return await message.reply_text("❌ رمز عبور نمی‌تواند خالی باشد.")
        
        zip_password_storage[user_id] = zip_password
        waiting_for_password[user_id] = False
        waiting_for_filename[user_id] = True
        return await message.reply_text("📝 حالا اسم فایل زیپ نهایی را وارد کن (بدون .zip)")
    
    # مرحله اسم فایل
    if user_id in waiting_for_filename and waiting_for_filename[user_id]:
        zip_name = message.text.strip()
        if not zip_name:
            return await message.reply_text("❌ اسم فایل نمی‌تواند خالی باشد.")
        
        waiting_for_filename.pop(user_id, None)
        processing_msg = await message.reply_text("⏳ در حال ایجاد فایل زیپ...")
        zip_password = zip_password_storage.get(user_id, "123456")
        
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                files_to_zip = []
                total_files = len(user_files[user_id])
                
                for i, finfo in enumerate(user_files[user_id], 1):
                    file_msg = finfo["message"]
                    file_name = finfo["file_name"]
                    file_path = os.path.join(tmp_dir, file_name)
                    
                    # دانلود با سرعت بالا
                    download_start = time.time()
                    success = await download_file_with_max_speed(client, file_msg, file_path, processing_msg)
                    
                    if not success:
                        await message.reply_text(f"❌ خطا در دانلود فایل: {file_name}")
                        continue
                    
                    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                        files_to_zip.append({"path": file_path, "name": file_name})
                    
                    progress_text = f"📥 دانلود کامل شد {i}/{total_files}"
                    try: 
                        await processing_msg.edit_text(progress_text)
                    except: 
                        pass
                
                if not files_to_zip:
                    await message.reply_text("❌ هیچ فایلی برای زیپ کردن دانلود نشد.")
                    return
                
                # ایجاد زیپ
                zip_file_name = f"{zip_name}.zip"
                zip_path = os.path.join(tmp_dir, zip_file_name)
                
                success = await create_split_zip(files_to_zip, zip_path, zip_password, processing_msg)
                
                if success and os.path.exists(zip_path):
                    # آپلود زیپ
                    upload_msg = await processing_msg.edit_text("📤 در حال آپلود فایل زیپ...")
                    upload_start = time.time()
                    
                    await client.send_document(
                        message.chat.id,
                        zip_path,
                        caption=f"✅ فایل زیپ آماده شد!\n🔑 رمز: `{zip_password}`\n📦 تعداد فایل‌ها: {total_files}\n⚡️ سرعت متوسط: {round(sum(download_speeds.values())/len(download_speeds)/1024/1024, 2)} MB/s",
                        progress=lambda c, t: asyncio.ensure_future(
                            update_progress(c, t, upload_msg, upload_start, "آپلود")
                        )
                    )
                    
                    await upload_msg.edit_text("✅ آپلود کامل شد!")
                else:
                    await message.reply_text("❌ خطایی در ایجاد فایل زیپ رخ داد.")
                    
        except Exception as e:
            logger.error(f"Error in zip process: {e}", exc_info=True)
            await message.reply_text(f"❌ خطایی رخ داد: {str(e)}")
        finally:
            user_files[user_id] = []
            waiting_for_password.pop(user_id, None)
            waiting_for_filename.pop(user_id, None)
            zip_password_storage.pop(user_id, None)
            download_speeds.pop(message.chat.id, None)

# ===== تابع برای اجرای ربات =====
async def run_bot():
    global app
    logger.info("Starting user bot...")
    
    app = Client(
        "user_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=SESSION_STRING,
        sleep_threshold=120,  # افزایش threshold برای دانلودهای طولانی
        workers=8,  # افزایش workerها برای سرعت بیشتر
        max_concurrent_transmissions=4  # افزایش انتقال همزمان
    )
    
    # اضافه کردن هندلرها
    app.add_handler(filters.command("start")(start))
    app.add_handler(filters.document(handle_file))
    app.add_handler(filters.command("zip")(start_zip))
    app.add_handler(filters.command("cancel")(cancel_zip))
    app.add_handler(non_command_filter(process_zip))
    
    await app.start()
    logger.info("Bot started successfully!")
    
    # نمایش اطلاعات session
    me = await app.get_me()
    logger.info(f"Logged in as: {me.first_name} (@{me.username})")
    
    await asyncio.Event().wait()

# ===== اجرا =====
if __name__ == "__main__":
    web_app = Flask(__name__)
    
    @web_app.route('/')
    def home():
        return "Bot is running", 200
    
    @web_app.route('/health')
    def health_check():
        return "Bot is running", 200
    
    def start_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_bot())
        except Exception as e:
            logger.error(f"Bot error: {e}")
            import traceback
            traceback.print_exc()
    
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()
    
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"Starting Flask web server on port {port}...")
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
