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
    
    MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024
    MAX_TOTAL_SIZE = 4 * 1024 * 1024 * 1024
    MAX_FILES_COUNT = 3
    
    CHUNK_SIZE = 128 * 1024
    PROGRESS_INTERVAL = 30
    ZIP_CHUNK_SIZE = 256 * 1024

# ===== لاگینگ =====
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# ===== متغیرهای جهانی =====
app = None
user_files: Dict[int, List] = {}
user_states: Dict[int, Any] = {}

# ===== مدیریت پیشرفت =====
class Progress:
    def __init__(self):
        self.last_update = 0
        self.message = None
    
    async def update(self, current, total, stage="در حال پردازش"):
        now = time.time()
        if now - self.last_update < Config.PROGRESS_INTERVAL:
            return
            
        self.last_update = now
        percent = (current / total) * 100 if total > 0 else 0
        
        if self.message:
            try:
                text = f"⏳ {stage}\n📊 {self.format_size(current)}/{self.format_size(total)}\n📈 {percent:.1f}%"
                await self.message.edit_text(text)
            except:
                pass
    
    @staticmethod
    def format_size(size_bytes):
        if size_bytes == 0:
            return "0B"
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f}{unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f}TB"

progress = Progress()

# ===== توابع اصلی =====
def is_user_allowed(user_id):
    return user_id in Config.ALLOWED_USER_IDS

async def send_msg(chat_id, text, reply_id=None):
    try:
        return await app.send_message(chat_id, text, reply_to_message_id=reply_id)
    except:
        return None

async def download_with_progress(message, file_path):
    """دانلود با نمایش پیشرفت"""
    try:
        file_size = message.document.file_size if message.document else message.video.file_size
        downloaded = 0
        
        async for chunk in app.stream_media(message, chunk_size=Config.CHUNK_SIZE):
            with open(file_path, 'ab') as f:
                f.write(chunk)
            downloaded += len(chunk)
            
            await progress.update(downloaded, file_size, "دانلود")
            gc.collect()
                
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
            compression=pyzipper.ZIP_STORED,  # بدون فشرده‌سازی
            encryption=pyzipper.WZ_AES if password else None
        ) as zipf:
            
            if password:
                zipf.setpassword(password.encode('utf-8'))
            
            for file_info in files:
                if not os.path.exists(file_info['path']):
                    continue
                    
                with open(file_info['path'], 'rb') as f:
                    with zipf.open(file_info['name'], 'w') as zf:
                        while True:
                            chunk = f.read(Config.ZIP_CHUNK_SIZE)
                            if not chunk:
                                break
                            zf.write(chunk)
                            processed += len(chunk)
                            
                            # آپدیت پیشرفت هر 50MB
                            if processed % (50 * 1024 * 1024) < Config.ZIP_CHUNK_SIZE:
                                progress.zip_progress_queue.put((processed, total_size))
                                
        return True
    except Exception as e:
        logger.error(f"Zip creation error: {e}")
        return False

async def upload_with_progress(file_path, chat_id, caption, reply_id):
    """آپلود با نمایش پیشرفت"""
    try:
        file_size = os.path.getsize(file_path)
        uploaded = 0
        
        async for chunk in app.stream_media(file_path, chunk_size=Config.CHUNK_SIZE):
            uploaded += len(chunk)
            await progress.update(uploaded, file_size, "آپلود")
            
        await app.send_document(
            chat_id,
            document=file_path,
            caption=caption,
            reply_to_message_id=reply_id
        )
        return True
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return False

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
                file_msg = await app.get_messages(chat_id, file_data["message_id"])
                if not file_msg:
                    continue
                    
                file_path = f"/tmp/{file_data['file_name']}"
                temp_files.append(file_path)
                
                if await download_with_progress(file_msg, file_path):
                    file_infos.append({
                        'path': file_path,
                        'name': file_data['file_name'],
                        'size': os.path.getsize(file_path)
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
        
        zip_name = f"archive_{int(time.time())}"
        zip_path = f"/tmp/{zip_name}.zip"
        temp_files.append(zip_path)
        
        # ایجاد زیپ در ترد جداگانه
        success = await asyncio.get_event_loop().run_in_executor(
            None,  # از ترد اصلی استفاده کن
            create_zip_with_files,
            zip_path, file_infos, None
        )
        
        if not success or not os.path.exists(zip_path):
            await processing_msg.edit_text("❌ خطا در ایجاد فایل زیپ")
            return False
            
        # آپلود زیپ
        zip_size = os.path.getsize(zip_path)
        await processing_msg.edit_text(f"📤 در حال آپلود فایل زیپ ({progress.format_size(zip_size)})...")
        
        upload_success = await upload_with_progress(
            zip_path,
            chat_id,
            f"📦 فایل زیپ شده\n💾 حجم: {progress.format_size(zip_size)}\n📁 تعداد فایل: {len(file_infos)}",
            message_id
        )
        
        if upload_success:
            await processing_msg.edit_text("✅ فایل زیپ با موفقیت آپلود شد!")
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
                    os.remove(file_path)
            except:
                pass
        gc.collect()

# ===== هندلرها =====
@app.on_message(filters.command("start"))
async def start(client, message):
    if not is_user_allowed(message.from_user.id):
        return
    await send_msg(message.chat.id, 
        "🤖 ربات زیپ‌ساز آماده است\n\n"
        "📦 فایل‌ها را ارسال کنید و سپس از /zip استفاده کنید", 
        message.id
    )

@app.on_message(filters.command("zip"))
async def start_zip(client, message):
    if not is_user_allowed(message.from_user.id):
        return
        
    user_id = message.from_user.id
    if user_id not in user_files or not user_files[user_id]:
        await send_msg(message.chat.id, "❌ هیچ فایلی برای زیپ کردن وجود ندارد", message.id)
        return
        
    await send_msg(message.chat.id, "🔐 لطفاً رمز عبور برای فایل زیپ وارد کنید (یا /skip برای بدون رمز)", message.id)
    user_states[user_id] = "waiting_password"

@app.on_message(filters.command("skip"))
async def skip_password(client, message):
    if not is_user_allowed(message.from_user.id):
        return
        
    user_id = message.from_user.id
    if user_states.get(user_id) == "waiting_password":
        user_states[user_id] = "ready"
        await process_zip_files(user_id, message.chat.id, message.id)

@app.on_message(filters.text & ~filters.command)
async def handle_password(client, message):
    if not is_user_allowed(message.from_user.id):
        return
        
    user_id = message.from_user.id
    if user_states.get(user_id) == "waiting_password":
        password = message.text.strip()
        if len(password) < 4:
            await send_msg(message.chat.id, "❌ رمز عبور باید حداقل 4 کاراکتر باشد", message.id)
            return
            
        user_states[user_id] = "ready"
        # ذخیره رمز (می‌توانید استفاده کنید)
        await process_zip_files(user_id, message.chat.id, message.id)

@app.on_message(filters.document | filters.video)
async def handle_file(client, message):
    if not is_user_allowed(message.from_user.id):
        return
        
    file_obj = message.document or message.video
    file_size = file_obj.file_size
    file_name = getattr(file_obj, 'file_name', None) or f"file_{message.id}"
    
    if file_size > Config.MAX_FILE_SIZE:
        await send_msg(message.chat.id, f"❌ فایل بسیار بزرگ! (حداکثر: {progress.format_size(Config.MAX_FILE_SIZE)})", message.id)
        return
        
    user_id = message.from_user.id
    if user_id not in user_files:
        user_files[user_id] = []
        
    if len(user_files[user_id]) >= Config.MAX_FILES_COUNT:
        await send_msg(message.chat.id, f"❌ حداکثر {Config.MAX_FILES_COUNT} فایل مجاز است", message.id)
        return
        
    total_size = sum(f["file_size"] for f in user_files[user_id]) + file_size
    if total_size > Config.MAX_TOTAL_SIZE:
        await send_msg(message.chat.id, "❌ حجم کل فایل‌ها بیش از حد مجاز است", message.id)
        return
        
    user_files[user_id].append({
        "message_id": message.id,
        "file_name": file_name,
        "file_size": file_size
    })
    
    await send_msg(message.chat.id, f"✅ فایل ذخیره شد ({progress.format_size(file_size)})\n📁 تعداد: {len(user_files[user_id])}", message.id)

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
        except:
            pass

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
    
    # شروع keep-alive
    asyncio.create_task(keep_alive())
    
    # اجرای وب سرور در ترد جداگانه
    threading.Thread(target=run_web_server, daemon=True).start()
    
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot error: {e}")
