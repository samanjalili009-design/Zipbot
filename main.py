import os
import time
import tempfile
import pyzipper
import logging
import sys
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from pyrogram.errors import FloodWait, RPCError
from flask import Flask
import threading
from collections import deque
import math
from typing import Dict, List, Any
import json
import gc

# ===== تنظیمات =====
class Config:
    API_ID = 1867911
    API_HASH = "f9e86b274826212a2712b18754fabc47"
    SESSION_STRING = "BAAcgIcAHFzRCBK8bXUoRyPGXLQaXCUVVc8YRwzBkm7m9RHERF-DwcIcuO1XunQeFNnVTsCgpeR4yfVY-qygIVL-ayKd4FXec1Q0AJSwwMztT_JNgRjlIupm9OaujoI68FlcNibGtCYEkktHPWlN7I8F5ux34MWBQbK3v6DIXfKyAza3yCksCwYI7YoZz7-Ay2d3XK2S_GDqcNW3DF-PGGc-ZAnpdPe11aDiX1vwpDjXm0pV0_Cw5GeHgLUm6LcZ1PwPLvIkUDhhGsR3cFYHHrxjS4SuD-cgb4Zjv9r7zBJ5HGaGnBPZKRW3OSxnv2DpnaJOoX_tbFAp0ZWNYOFTsIX6Nt55xgAAAAAY4xquAA"
    ALLOWED_USER_IDS = [417536686]
    
    MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
    MAX_TOTAL_SIZE = 4 * 1024 * 1024 * 1024  # 4GB
    MAX_FILES_COUNT = 3
    
    CHUNK_SIZE = 128 * 1024  # 128KB
    PROGRESS_INTERVAL = 5  # 5 ثانیه
    ZIP_CHUNK_SIZE = 256 * 1024  # 256KB

# ===== لاگینگ =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log')
    ]
)
logger = logging.getLogger(__name__)

# ===== متغیرهای جهانی =====
app = None
user_files: Dict[int, List] = {}
user_states: Dict[int, Any] = {}
user_passwords: Dict[int, str] = {}

# ===== مدیریت پیشرفت =====
class Progress:
    def __init__(self):
        self.last_update = 0
        self.message = None
        self.zip_progress_queue = deque()
    
    async def update(self, current, total, stage="در حال پردازش"):
        now = time.time()
        if now - self.last_update < Config.PROGRESS_INTERVAL:
            return
            
        self.last_update = now
        percent = (current / total) * 100 if total > 0 else 0
        
        if self.message:
            try:
                text = f"⏳ **{stage}**\n\n" \
                       f"📊 {self.format_size(current)} / {self.format_size(total)}\n" \
                       f"📈 {percent:.1f}%"
                await self.message.edit_text(text)
            except Exception as e:
                logger.error(f"Error updating progress: {e}")
    
    @staticmethod
    def format_size(size_bytes):
        if size_bytes == 0:
            return "0B"
        size_names = ["B", "KB", "MB", "GB", "TB"]
        i = 0
        while size_bytes >= 1024 and i < len(size_names) - 1:
            size_bytes /= 1024.0
            i += 1
        return f"{size_bytes:.2f} {size_names[i]}"

progress = Progress()

# ===== توابع اصلی =====
def is_user_allowed(user_id):
    return user_id in Config.ALLOWED_USER_IDS

async def send_msg(chat_id, text, reply_id=None):
    try:
        return await app.send_message(chat_id, text, reply_to_message_id=reply_id)
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return None

async def download_with_progress(message, file_path):
    """دانلود با نمایش پیشرفت"""
    try:
        file_size = message.document.file_size if message.document else message.video.file_size
        downloaded = 0
        
        # ایجاد دایرکتوری اگر وجود ندارد
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        async for chunk in app.stream_media(message, chunk_size=Config.CHUNK_SIZE):
            with open(file_path, 'ab') as f:
                f.write(chunk)
            downloaded += len(chunk)
            
            await progress.update(downloaded, file_size, "📥 در حال دانلود")
                
        return True
    except Exception as e:
        logger.error(f"Download error: {e}")
        return False

def create_zip_with_files(zip_path, files, password=None):
    """ایجاد فایل زیپ از فایل‌ها"""
    try:
        total_size = sum(f['size'] for f in files)
        processed = 0
        
        with pyzipper.AESZipFile(
            zip_path, 
            'w', 
            compression=pyzipper.ZIP_DEFLATED,  # با فشرده‌سازی
            encryption=pyzipper.WZ_AES if password else None
        ) as zipf:
            
            if password:
                zipf.setpassword(password.encode('utf-8'))
            
            for file_info in files:
                if not os.path.exists(file_info['path']):
                    continue
                    
                arcname = os.path.basename(file_info['name'])
                zipf.write(file_info['path'], arcname)
                
                # آپدیت پیشرفت
                processed += file_info['size']
                progress.zip_progress_queue.append((processed, total_size))
                                
        return True
    except Exception as e:
        logger.error(f"Zip creation error: {e}")
        return False

async def upload_with_progress(file_path, chat_id, caption, reply_id):
    """آپلود با نمایش پیشرفت"""
    try:
        file_size = os.path.getsize(file_path)
        uploaded = 0
        
        await app.send_document(
            chat_id,
            document=file_path,
            caption=caption,
            reply_to_message_id=reply_id,
            progress=progress_callback,
            progress_args=(file_size, "📤 در حال آپلود")
        )
        return True
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return False

async def progress_callback(current, total, file_size, stage):
    """کالبک برای آپدیت پیشرفت آپلود"""
    await progress.update(current, file_size, stage)

async def process_zip_files(user_id, chat_id, message_id):
    """پردازش و زیپ کردن فایل‌ها"""
    temp_files = []
    processing_msg = None
    
    try:
        processing_msg = await send_msg(chat_id, "📥 در حال دانلود فایل‌ها...", message_id)
        progress.message = processing_msg
        
        # دانلود همه فایل‌ها
        file_infos = []
        for file_data in user_files[user_id]:
            try:
                file_msg = await app.get_messages(chat_data["chat_id"], file_data["message_id"])
                if not file_msg:
                    continue
                    
                # ایجاد مسیر فایل موقت
                temp_dir = tempfile.mkdtemp()
                file_path = os.path.join(temp_dir, file_data['file_name'])
                temp_files.append(file_path)
                temp_files.append(temp_dir)  # برای پاک کردن دایرکتوری بعداً
                
                if await download_with_progress(file_msg, file_path):
                    file_size = os.path.getsize(file_path)
                    file_infos.append({
                        'path': file_path,
                        'name': file_data['file_name'],
                        'size': file_size
                    })
                    
                await asyncio.sleep(1)
                gc.collect()
                
            except Exception as e:
                logger.error(f"File processing error: {e}")
                continue
        
        if not file_infos:
            await processing_msg.edit_text("❌ خطا در دانلود فایل‌ها")
            return False
            
        # ایجاد زیپ
        await processing_msg.edit_text("📦 در حال ایجاد فایل زیپ...")
        
        # ایجاد دایرکتوری موقت برای زیپ
        zip_temp_dir = tempfile.mkdtemp()
        zip_name = f"archive_{int(time.time())}"
        zip_path = os.path.join(zip_temp_dir, f"{zip_name}.zip")
        temp_files.append(zip_path)
        temp_files.append(zip_temp_dir)
        
        # دریافت رمز عبور اگر وجود دارد
        password = user_passwords.get(user_id)
        
        # ایجاد زیپ
        success = await asyncio.get_event_loop().run_in_executor(
            None,
            create_zip_with_files,
            zip_path, file_infos, password
        )
        
        if not success or not os.path.exists(zip_path):
            await processing_msg.edit_text("❌ خطا در ایجاد فایل زیپ")
            return False
            
        # آپلود زیپ
        zip_size = os.path.getsize(zip_path)
        await processing_msg.edit_text(f"📤 در حال آپلود فایل زیپ ({progress.format_size(zip_size)})...")
        
        caption = f"📦 فایل زیپ شده\n" \
                 f"💾 حجم: {progress.format_size(zip_size)}\n" \
                 f"📁 تعداد فایل: {len(file_infos)}"
        
        if password:
            caption += f"\n🔐 رمز عبور: `{password}`"
        
        upload_success = await upload_with_progress(
            zip_path,
            chat_id,
            caption,
            message_id
        )
        
        if upload_success:
            await processing_msg.edit_text("✅ فایل زیپ با موفقیت آپلود شد!")
            # پاک کردن اطلاعات کاربر
            if user_id in user_files:
                del user_files[user_id]
            if user_id in user_passwords:
                del user_passwords[user_id]
            if user_id in user_states:
                del user_states[user_id]
            return True
        else:
            await processing_msg.edit_text("❌ خطا در آپلود فایل زیپ")
            return False
            
    except Exception as e:
        logger.error(f"Process error: {e}")
        if processing_msg:
            await processing_msg.edit_text("❌ خطا در پردازش")
        return False
    finally:
        # پاک‌سازی فایل‌های موقت
        for file_path in temp_files:
            try:
                if os.path.exists(file_path):
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                    elif os.path.isdir(file_path):
                        import shutil
                        shutil.rmtree(file_path)
            except Exception as e:
                logger.error(f"Error cleaning up file {file_path}: {e}")
        gc.collect()

# ===== هندلرها =====
@app.on_message(filters.command("start"))
async def start_handler(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    await send_msg(message.chat.id, 
        "🤖 **ربات زیپ‌ساز آماده است**\n\n"
        "📦 فایل‌ها را ارسال کنید و سپس از /zip استفاده کنید\n"
        "📊 حداکثر حجم هر فایل: 2GB\n"
        "📁 حداکثر تعداد فایل: 3\n"
        "💾 حداکثر حجم کل: 4GB", 
        message.id
    )

@app.on_message(filters.command("zip"))
async def start_zip_handler(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
        
    user_id = message.from_user.id
    if user_id not in user_files or not user_files[user_id]:
        await send_msg(message.chat.id, "❌ هیچ فایلی برای زیپ کردن وجود ندارد", message.id)
        return
        
    await send_msg(message.chat.id, 
        "🔐 لطفاً رمز عبور برای فایل زیپ وارد کنید\n\n"
        "📝 حداقل 4 کاراکتر\n"
        "🔒 یا از /skip برای بدون رمز استفاده کنید", 
        message.id
    )
    user_states[user_id] = "waiting_password"

@app.on_message(filters.command("skip"))
async def skip_password_handler(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
        
    user_id = message.from_user.id
    if user_states.get(user_id) == "waiting_password":
        user_states[user_id] = "ready"
        user_passwords[user_id] = None
        await process_zip_files(user_id, message.chat.id, message.id)

@app.on_message(filters.command("clear"))
async def clear_files_handler(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
        
    user_id = message.from_user.id
    if user_id in user_files:
        del user_files[user_id]
    if user_id in user_states:
        del user_states[user_id]
    if user_id in user_passwords:
        del user_passwords[user_id]
    
    await send_msg(message.chat.id, "✅ فایل‌ها پاک شدند", message.id)

@app.on_message(filters.command("status"))
async def status_handler(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
        
    user_id = message.from_user.id
    if user_id in user_files:
        total_size = sum(f["file_size"] for f in user_files[user_id])
        status_text = f"📊 وضعیت فعلی:\n\n" \
                     f"📁 تعداد فایل: {len(user_files[user_id])}\n" \
                     f"💾 حجم کل: {progress.format_size(total_size)}"
    else:
        status_text = "📭 هیچ فایلی ذخیره نشده است"
    
    await send_msg(message.chat.id, status_text, message.id)

@app.on_message(filters.text & ~filters.command)
async def handle_text(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
        
    user_id = message.from_user.id
    if user_states.get(user_id) == "waiting_password":
        password = message.text.strip()
        if len(password) < 4:
            await send_msg(message.chat.id, "❌ رمز عبور باید حداقل 4 کاراکتر باشد", message.id)
            return
            
        user_states[user_id] = "ready"
        user_passwords[user_id] = password
        await process_zip_files(user_id, message.chat.id, message.id)

@app.on_message(filters.document | filters.video)
async def handle_file(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
        
    file_obj = message.document or message.video
    file_size = file_obj.file_size
    file_name = getattr(file_obj, 'file_name', None) or f"file_{message.id}.bin"
    
    if file_size > Config.MAX_FILE_SIZE:
        await send_msg(message.chat.id, 
            f"❌ فایل بسیار بزرگ!\n"
            f"📦 حجم فایل: {progress.format_size(file_size)}\n"
            f"📊 حداکثر مجاز: {progress.format_size(Config.MAX_FILE_SIZE)}", 
            message.id
        )
        return
        
    user_id = message.from_user.id
    if user_id not in user_files:
        user_files[user_id] = []
        
    if len(user_files[user_id]) >= Config.MAX_FILES_COUNT:
        await send_msg(message.chat.id, 
            f"❌ حداکثر {Config.MAX_FILES_COUNT} فایل مجاز است", 
            message.id
        )
        return
        
    total_size = sum(f["file_size"] for f in user_files[user_id]) + file_size
    if total_size > Config.MAX_TOTAL_SIZE:
        await send_msg(message.chat.id, 
            f"❌ حجم کل فایل‌ها بیش از حد مجاز است\n"
            f"📊 حجم کل: {progress.format_size(total_size)}\n"
            f"💾 حداکثر مجاز: {progress.format_size(Config.MAX_TOTAL_SIZE)}", 
            message.id
        )
        return
        
    user_files[user_id].append({
        "message_id": message.id,
        "chat_id": message.chat.id,
        "file_name": file_name,
        "file_size": file_size
    })
    
    await send_msg(message.chat.id, 
        f"✅ فایل ذخیره شد\n\n"
        f"📝 نام: {file_name}\n"
        f"📦 حجم: {progress.format_size(file_size)}\n"
        f"📁 تعداد کل: {len(user_files[user_id])}", 
        message.id
    )

# ===== وب سرور برای فعال نگه داشتن =====
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "🤖 Zip Bot is Running", 200

@web_app.route('/ping')
def ping():
    return "pong", 200

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

async def keep_alive():
    """نگه داشتن ربات فعال"""
    while True:
        await asyncio.sleep(25 * 60)  # هر 25 دقیقه
        try:
            await app.send_message("me", "🤖 Bot is alive and ready")
        except Exception as e:
            logger.error(f"Keep alive error: {e}")

async def main():
    global app
    app = Client(
        "zip_bot", 
        api_id=Config.API_ID, 
        api_hash=Config.API_HASH, 
        session_string=Config.SESSION_STRING,
        in_memory=True
    )
    
    await app.start()
    logger.info("✅ Zip Bot started successfully!")
    
    # اطلاعات ربات
    me = await app.get_me()
    logger.info(f"🤖 Bot: @{me.username} (ID: {me.id})")
    
    # شروع keep-alive
    asyncio.create_task(keep_alive())
    
    # اجرای وب سرور در ترد جداگانه
    threading.Thread(target=run_web_server, daemon=True).start()
    
    logger.info("🚀 Bot is ready to receive messages...")
    
    # نگه داشتن ربات فعال
    await idle()

async def idle():
    """نگه داشتن ربات فعال"""
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    try:
        # ایجاد دایرکتوری temp اگر وجود ندارد
        os.makedirs('/tmp', exist_ok=True)
        
        # اجرای ربات
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⏹️ Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Bot error: {e}")
    finally:
        logger.info("👋 Bot shutdown complete")
