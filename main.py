import os
import time
import tempfile
import pyzipper
import logging
import sys
import math
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# ===== تنظیمات =====
API_ID = 2487823
API_HASH = "3ba2af01cad4bdd6138d15e353096e3f"
SESSION_STRING = "BAAcgIcAbm3Hdroaq-gHzwTUhklM4QhrzHSHm1uy_ZeMKXDmDamwhqFGNhK9zG_ZwyxF50TxLgez_a6zJ738_-qHLofVT3dgQCSdBHLuKPm39X46lRk1omWxBtmUEIpNzVZZJqEnyP32szYrHFkNx5IexSIyKWPURIx92AUeqBD6VKDRZJxs61Gq0U0-FSykY0a5sjEXp-3Mmz07sL7RYbCraYsdTsYx9n1EL1Bmg7IT-xpWeWpaEa0u4cmTkfJxpY03WwYDZ1J4zuCsYCNsauQrS2w7r3M6bNdTBAUIHPF8kSttPhnwEEFJQK-kLeK0aslMI-LzMhqS7nfR5fIhNM4wxFAHOAAAAAAK4sD3AA"
ALLOWED_USER_ID = 417536686
MAX_FILE_SIZE = 2097152000  # 2GB
MAX_TOTAL_SIZE = 2097152000  # 2GB
MAX_SPLIT_SIZE = 1990000000  # 1.99GB
DOWNLOAD_CHUNK_SIZE = 131072  # 128KB
UPLOAD_CHUNK_SIZE = 131072   # 128KB

# ===== لاگ =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ===== کلاینت =====
app = Client(
    "user_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    in_memory=True
)

# ===== داده‌ها =====
user_files = {}
waiting_for_password = {}
waiting_for_filename = {}
zip_password_storage = {}

# ===== فانکشن‌های کمکی =====
def is_user_allowed(user_id: int) -> bool:
    return user_id == ALLOWED_USER_ID

async def split_large_file(file_path, max_size=MAX_SPLIT_SIZE):
    """تقسیم فایل به چند part"""
    part_files = []
    file_size = os.path.getsize(file_path)
    
    if file_size <= max_size:
        return [file_path]
    
    num_parts = math.ceil(file_size / max_size)
    base_name = os.path.basename(file_path)
    
    try:
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
                
                await asyncio.sleep(0.1)
        
        os.remove(file_path)
        return part_files
    except Exception as e:
        logger.error(f"Error splitting file: {e}")
        if os.path.exists(file_path):
            return [file_path]
        return []

async def update_progress(current, total, message, start_time, stage, file_size=None):
    """آپدیت پیشرفت با مدیریت FloodWait"""
    try:
        now = time.time()
        diff = now - start_time
        if diff == 0: 
            diff = 1
        
        percent = int(current * 100 / total) if total > 0 else 0
        speed = current / diff
        eta = int((total - current) / speed) if speed > 0 else 0
        
        bar_filled = int(percent / 5)
        bar = "▓" * bar_filled + "░" * (20 - bar_filled)
        
        if file_size:
            total_mb = file_size // 1024 // 1024
        else:
            total_mb = total // 1024 // 1024
            
        text = f"""
🚀 {stage} فایل...

{bar} {percent}%

📦 {current//1024//1024}MB / {total_mb}MB
⚡️ سرعت: {round(speed/1024/1024, 2)} MB/s
⏳ زمان باقی‌مانده: {eta}s
        """
        
        await message.edit_text(text)
        
    except FloodWait as e:
        await asyncio.sleep(e.value)
    except Exception:
        pass

async def download_file_with_progress(client, message, file_path, processing_msg, file_size):
    """دانلود فایل با پیشرفت"""
    start_time = time.time()
    last_update_time = start_time
    
    def progress(current, total):
        nonlocal last_update_time
        now = time.time()
        
        if now - last_update_time >= 2 or current == total:
            asyncio.create_task(update_progress(
                current, total, processing_msg, start_time, "دانلود", file_size
            ))
            last_update_time = now
    
    try:
        await client.download_media(
            message, 
            file_path, 
            progress=progress,
            chunk_size=DOWNLOAD_CHUNK_SIZE
        )
        return True
    except Exception as e:
        logger.error(f"Download error: {e}")
        return False

async def create_split_zip(files, zip_path, password, processing_msg):
    """ایجاد زیپ تقسیم شده"""
    try:
        total_files = len(files)
        
        with pyzipper.AESZipFile(zip_path, "w", compression=pyzipper.ZIP_DEFLATED, 
                               encryption=pyzipper.WZ_AES) as zipf:
            zipf.setpassword(password.encode())
            
            for i, file_info in enumerate(files, 1):
                file_path = file_info["path"]
                file_name = file_info["name"]
                
                if not os.path.exists(file_path):
                    continue
                
                file_size = os.path.getsize(file_path)
                
                if file_size > MAX_SPLIT_SIZE:
                    parts = await split_large_file(file_path)
                    for part_path in parts:
                        if os.path.exists(part_path):
                            part_name = os.path.basename(part_path)
                            zipf.write(part_path, part_name)
                            os.remove(part_path)
                else:
                    zipf.write(file_path, file_name)
                    os.remove(file_path)
                
                if i % 3 == 0 or i == total_files:
                    progress_text = f"⏳ در حال فشرده سازی... {i}/{total_files}"
                    try:
                        await processing_msg.edit_text(progress_text)
                        await asyncio.sleep(1)
                    except FloodWait as e:
                        await asyncio.sleep(e.value)
                    except Exception:
                        pass
                
                await asyncio.sleep(0.1)
        
        return True
    except Exception as e:
        logger.error(f"Error creating zip: {e}")
        return False

async def process_zip_files(client, message, user_id, zip_name, zip_password, processing_msg):
    """پردازش فایل‌ها در background"""
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            files_to_zip = []
            total_files = len(user_files[user_id])
            
            # دانلود فایل‌ها
            for i, finfo in enumerate(user_files[user_id], 1):
                file_msg = finfo["message"]
                file_name = finfo["file_name"]
                file_path = os.path.join(tmp_dir, file_name)
                
                status_text = f"📥 دانلود فایل {i}/{total_files}: {file_name}"
                try:
                    await processing_msg.edit_text(status_text)
                    await asyncio.sleep(1)
                except:
                    pass
                
                success = await download_file_with_progress(
                    client, file_msg, file_path, processing_msg, finfo["file_size"]
                )
                
                if success and os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                    files_to_zip.append({"path": file_path, "name": file_name})
                else:
                    logger.error(f"Failed to download file: {file_name}")
            
            if not files_to_zip:
                await message.reply_text("❌ هیچ فایلی برای زیپ کردن دانلود نشد!")
                return
            
            # ایجاد زیپ
            zip_file_name = f"{zip_name}.zip"
            zip_path = os.path.join(tmp_dir, zip_file_name)
            
            await processing_msg.edit_text("⏳ در حال فشرده‌سازی فایل‌ها...")
            
            success = await create_split_zip(files_to_zip, zip_path, zip_password, processing_msg)
            
            if success and os.path.exists(zip_path):
                zip_size = os.path.getsize(zip_path)
                await processing_msg.edit_text(f"📤 در حال آپلود فایل زیپ ({zip_size//1024//1024}MB)...")
                
                start_time = time.time()
                
                try:
                    await client.send_document(
                        message.chat.id,
                        zip_path,
                        caption=f"✅ فایل زیپ آماده شد!\n🔑 رمز: `{zip_password}`\n📦 تعداد فایل‌ها: {total_files}",
                        progress=lambda current, total: asyncio.create_task(
                            update_progress(current, total, processing_msg, start_time, "آپلود", zip_size)
                        ),
                        chunk_size=UPLOAD_CHUNK_SIZE
                    )
                    
                    await processing_msg.delete()
                    await message.reply_text("✅ عملیات با موفقیت完成 شد!")
                    
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    await client.send_document(
                        message.chat.id,
                        zip_path,
                        caption=f"✅ فایل زیپ آماده شد!\n🔑 رمز: `{zip_password}`\n📦 تعداد فایل‌ها: {total_files}"
                    )
                    
            else:
                await message.reply_text("❌ خطایی در ایجاد فایل زیپ رخ داد.")
                
    except Exception as e:
        logger.error(f"Error in zip process: {e}", exc_info=True)
        await message.reply_text("❌ خطایی در پردازش فایل‌ها رخ داد.")
    finally:
        if user_id in user_files:
            user_files[user_id] = []

# ===== هندلرها =====
@app.on_message(filters.command("start"))
async def start_handler(client, message):
    if not is_user_allowed(message.from_user.id):
        return await message.reply_text("❌ دسترسی denied.")
    await message.reply_text(
        "سلام 👋\nفایل‌تو بفرست تا برات زیپ کنم.\n"
        "💡 کپشن فایل = pass=رمز برای تعیین پسورد (اختیاری)\n"
        f"📦 حداکثر حجم هر فایل: {MAX_FILE_SIZE//1024//1024}MB\n"
        f"📦 حداکثر حجم کل: {MAX_TOTAL_SIZE//1024//1024}MB\n"
        "🔧 فایل‌های بزرگتر از 2GB به صورت خودکار تقسیم می‌شوند"
    )

@app.on_message(filters.document)
async def document_handler(client, message):
    if not is_user_allowed(message.from_user.id):
        return
    
    doc = message.document
    if not doc or doc.file_size > MAX_FILE_SIZE:
        await message.reply_text("❌ فایل بسیار بزرگ است!")
        return
    
    file_name = doc.file_name or f"file_{message.id}"
    caption = message.caption or ""
    password = None
    
    if "pass=" in caption:
        password = caption.split("pass=",1)[1].split()[0].strip()
    
    user_id = message.from_user.id
    if user_id not in user_files:
        user_files[user_id] = []
    
    total_size = sum(f["file_size"] for f in user_files[user_id]) + doc.file_size
    if total_size > MAX_TOTAL_SIZE:
        await message.reply_text("❌ حجم کل فایل‌ها بیش از حد مجاز است!")
        return
    
    user_files[user_id].append({
        "message": message, 
        "file_name": file_name, 
        "password": password, 
        "file_size": doc.file_size
    })
    
    count = len(user_files[user_id])
    await message.reply_text(f"✅ فایل ذخیره شد! ({count} فایل)")

@app.on_message(filters.command("zip"))
async def zip_handler(client, message):
    if not is_user_allowed(message.from_user.id): 
        return
    
    user_id = message.from_user.id
    if user_id not in user_files or not user_files[user_id]:
        return await message.reply_text("❌ هیچ فایلی برای زیپ کردن وجود ندارد.")
    
    await message.reply_text("🔐 لطفاً رمز عبور برای فایل زیپ وارد کن:\n❌ برای لغو /cancel را بزنید")
    waiting_for_password[user_id] = True

@app.on_message(filters.command("cancel"))
async def cancel_handler(client, message):
    user_id = message.from_user.id
    if user_id in user_files: 
        user_files[user_id] = []
    waiting_for_password.pop(user_id, None)
    waiting_for_filename.pop(user_id, None)
    zip_password_storage.pop(user_id, None)
    await message.reply_text("❌ عملیات لغو شد.")

@app.on_message(filters.text & ~filters.command)
async def text_handler(client, message):
    user_id = message.from_user.id
    
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
        
        waiting_for_filename[user_id] = False
        processing_msg = await message.reply_text("⏳ در حال شروع فرآیند...")
        zip_password = zip_password_storage.get(user_id, "1234")
        
        try:
            asyncio.create_task(
                process_zip_files(client, message, user_id, zip_name, zip_password, processing_msg)
            )
            
        except Exception as e:
            logger.error(f"Error starting zip process: {e}")
            await message.reply_text("❌ خطایی در شروع فرآیند رخ داد.")
            if user_id in user_files:
                user_files[user_id] = []

# ===== وب سرور برای سلامت =====
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ['/health', '/']:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Bot is running')
        else:
            self.send_response(404)
            self.end_headers()

def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logger.info(f"HTTP server running on port {port}")
    server.serve_forever()

# ===== اجرا =====
if __name__ == "__main__":
    logger.info("Starting user bot...")
    
    # اجرای وب سرور در thread جداگانه
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    
    # اجرای ربات
    logger.info("Starting Telegram bot...")
    app.run()
