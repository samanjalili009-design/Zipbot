import os
import time
import tempfile
import pyzipper
import logging
import sys
import asyncio
import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from pyrogram.errors import FloodWait, RPCError
from flask import Flask
import threading
from collections import deque
import random
import math
from typing import Dict, List, Any
import json
from datetime import datetime
import shutil
from concurrent.futures import ThreadPoolExecutor
import queue
import re
import gc
import psutil

# ===== تنظیمات تضمینی =====
class Config:
    API_ID = 1867911
    API_HASH = "f9e86b274826212a2712b18754fabc47"
    SESSION_STRING = "BAAcgIcAHFzRCBK8bXUoRyPGXLQaXCUVVc8YRwzBkm7m9RHERF-DwcIcuO1XunQeFNnVTsCgpeR4yfVY-qygIVL-ayKd4FXec1Q0AJSwwMztT_JNgRjlIupm9OaujoI68FlcNibGtCYEkktHPWlN7I8F5ux34MWBQbK3v6DIXfKyAza3yCksCwYI7YoZz7-Ay2d3XK2S_GDqcNW3DF-PGGc-ZAnpdPe11aDiX1vwpDjXm0pV0_Cw5GeHgLUm6LcZ1PwPLvIkUDhhGsR3cFYHHrxjS4SuD-cgb4Zjv9r7zBJ5HGaGnBPZKRW3OSxnv2DpnaJOoX_tbFAp0ZWNYOFTsIX6Nt55xgAAAAAY4xquAA"
    ALLOWED_USER_IDS = [417536686]
    
    # تنظیمات بهینه برای فایل‌های بزرگ
    MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
    MAX_TOTAL_SIZE = 4 * 1024 * 1024 * 1024  # 4GB
    MAX_FILES_COUNT = 3  # حداکثر 3 فایل
    
    DEFAULT_PART_SIZE = 200 * 1024 * 1024  # 200MB
    CHUNK_SIZE = 64 * 1024  # 64KB - بسیار مهم!
    
    # تنظیمات عمومی
    MAX_CONCURRENT_DOWNLOADS = 1
    MAX_CONCURRENT_UPLOADS = 1
    RETRY_DELAY = 2
    PROGRESS_UPDATE_INTERVAL = 5.0  # آپدیت کمتر
    
    DATA_FILE = "user_data.json"
    UPLOAD_CHUNK_SIZE = 512 * 1024  # 512KB
    
    # تنظیمات زیپ
    ZIP_COMPRESSION_LEVEL = 0  # بدون فشرده‌سازی
    MAX_ZIP_RETRIES = 1
    
    # تایم‌اوت‌ها
    ZIP_BASE_TIMEOUT = 7200  # 2 ساعت
    ZIP_TIMEOUT_PER_GB = 1800  # 30 دقیقه به ازای هر GB
    
    # مدیریت منابع
    MEMORY_LIMIT = 300 * 1024 * 1024  # 300MB
    STREAMING_CHUNK_SIZE = 64 * 1024  # 64KB - حیاتی!
    CLEANUP_INTERVAL = 45  # پاک‌سازی هر 45 ثانیه

# ===== لاگینگ ساده =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ===== مدیریت منابع =====
class ResourceManager:
    @staticmethod
    def free_memory():
        """پاک‌سازی حافظه"""
        gc.collect()
    
    @staticmethod
    def get_memory_usage():
        try:
            process = psutil.Process()
            return process.memory_info().rss
        except:
            return 0
    
    @staticmethod
    def is_memory_ok():
        return ResourceManager.get_memory_usage() < Config.MEMORY_LIMIT

# ===== کلاینت Pyrogram =====
app = None

# ===== داده‌ها =====
user_files: Dict[int, List] = {}
user_states: Dict[int, Any] = {}
task_queue = deque()
processing = False
zip_executor = ThreadPoolExecutor(max_workers=1)

# ===== پیشرفت ساده =====
class SimpleProgress:
    def __init__(self):
        self.current = 0
        self.total = 0
        self.stage = ""
        self.message = None
    
    async def update(self, current: int, total: int):
        self.current = current
        self.total = total
        
        if total == 0:
            return
            
        percent = (current / total) * 100
        elapsed = time.time() - self.start_time if hasattr(self, 'start_time') else 0
        speed = current / elapsed if elapsed > 0 else 0
        
        if self.message and current % (50 * 1024 * 1024) < Config.STREAMING_CHUNK_SIZE:  # هر 50MB
            try:
                text = (
                    f"⏳ {self.stage}\n"
                    f"📊 {self.format_size(current)}/{self.format_size(total)}\n"
                    f"📈 {percent:.1f}%\n"
                    f"⚡ {self.format_size(speed)}/s"
                )
                await self.message.edit_text(text)
            except:
                pass
    
    @staticmethod
    def format_size(size_bytes: int) -> str:
        if size_bytes == 0:
            return "0B"
        size_names = ["B", "KB", "MB", "GB"]
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 1)
        return f"{s}{size_names[i]}"

progress = SimpleProgress()

# ===== فانکشن‌های اصلی =====
def is_user_allowed(user_id: int):
    return user_id in Config.ALLOWED_USER_IDS

def load_user_data():
    global user_files, user_states
    try:
        if os.path.exists(Config.DATA_FILE):
            with open(Config.DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                user_files = data.get('user_files', {})
                user_states = data.get('user_states', {})
    except:
        pass

def save_user_data():
    try:
        data = {'user_files': user_files, 'user_states': user_states}
        with open(Config.DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except:
        pass

async def send_message(chat_id, text, reply_id=None):
    try:
        return await app.send_message(chat_id, text, reply_to_message_id=reply_id)
    except:
        return None

async def download_file(message, file_path):
    """دانلود با مدیریت حافظه"""
    try:
        progress.start_time = time.time()
        progress.stage = "دانلود"
        
        await app.download_media(
            message,
            file_name=file_path,
            progress=progress.update,
            block=False
        )
        
        return os.path.exists(file_path) and os.path.getsize(file_path) > 0
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return False
    except:
        return False

def create_zip_safe(zip_path, files, password):
    """ایجاد زیپ با مدیریت منابع"""
    try:
        total_size = sum(f['size'] for f in files)
        processed = 0
        
        with pyzipper.AESZipFile(
            zip_path, 'w', 
            compression=pyzipper.ZIP_STORED,
            encryption=pyzipper.WZ_AES if password else None
        ) as zipf:
            
            if password:
                zipf.setpassword(password.encode())
            
            for file_info in files:
                if not os.path.exists(file_info['path']):
                    continue
                    
                with open(file_info['path'], 'rb') as f:
                    with zipf.open(file_info['name'], 'w') as zf:
                        while True:
                            chunk = f.read(Config.STREAMING_CHUNK_SIZE)
                            if not chunk:
                                break
                            zf.write(chunk)
                            processed += len(chunk)
                            
                            # پاک‌سازی هر 100MB
                            if processed % (100 * 1024 * 1024) < Config.STREAMING_CHUNK_SIZE:
                                gc.collect()
        
        return True
    except:
        return False

async def upload_file(file_path, chat_id, caption, reply_id):
    """آپلود با مدیریت خطا"""
    try:
        progress.stage = "آپلود"
        progress.start_time = time.time()
        
        file_size = os.path.getsize(file_path)
        part_size = Config.DEFAULT_PART_SIZE
        
        if file_size <= part_size:
            await app.send_document(
                chat_id,
                document=file_path,
                caption=caption,
                reply_to_message_id=reply_id,
                progress=progress.update,
                block=False
            )
        else:
            # آپلود تکه‌تکه
            parts = (file_size + part_size - 1) // part_size
            with open(file_path, 'rb') as f:
                for i in range(parts):
                    chunk_data = f.read(part_size)
                    if not chunk_data:
                        break
                        
                    part_path = f"{file_path}_part{i+1}"
                    with open(part_path, 'wb') as part_file:
                        part_file.write(chunk_data)
                    
                    await app.send_document(
                        chat_id,
                        document=part_path,
                        caption=f"{caption} - Part {i+1}/{parts}",
                        reply_to_message_id=reply_id,
                        progress=progress.update,
                        block=False
                    )
                    
                    try:
                        os.remove(part_path)
                    except:
                        pass
                    
                    await asyncio.sleep(1)
                    gc.collect()
        
        return True
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return False
    except:
        return False

# ===== هندلرها =====
async def start(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
        
    text = (
        "🤖 **ربات پردازش فایل‌های بزرگ**\n\n"
        "✅ پشتیبانی از فایل‌های تا 2GB\n"
        "📦 ارسال فایل‌ها و سپس /zip"
    )
    await send_message(message.chat.id, text, message.id)

async def handle_file(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
        
    file_obj = message.document or message.video
    if not file_obj:
        return
        
    file_size = file_obj.file_size
    file_name = getattr(file_obj, 'file_name', f'file_{message.id}')
    
    if file_size > Config.MAX_FILE_SIZE:
        await send_message(message.chat.id, f"❌ فایل بزرگتر از {progress.format_size(Config.MAX_FILE_SIZE)} است", message.id)
        return
        
    user_id = message.from_user.id
    if user_id not in user_files:
        user_files[user_id] = []
        
    if len(user_files[user_id]) >= Config.MAX_FILES_COUNT:
        await send_message(message.chat.id, f"❌ حداکثر {Config.MAX_FILES_COUNT} فایل مجاز است", message.id)
        return
        
    total_size = sum(f["file_size"] for f in user_files[user_id]) + file_size
    if total_size > Config.MAX_TOTAL_SIZE:
        await send_message(message.chat.id, f"❌ حجم کل بیش از حد مجاز", message.id)
        return
        
    user_files[user_id].append({
        "message_id": message.id,
        "file_name": file_name,
        "file_size": file_size
    })
    
    await send_message(message.chat.id, f"✅ فایل ذخیره شد ({progress.format_size(file_size)})", message.id)
    save_user_data()

async def start_zip_process(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
        
    user_id = message.from_user.id
    if not user_files.get(user_id):
        await send_message(message.chat.id, "❌ هیچ فایلی وجود ندارد", message.id)
        return
        
    user_states[user_id] = "processing"
    await send_message(message.chat.id, "⏳ شروع پردازش...", message.id)
    
    # اضافه به صفر
    task_queue.append((process_files, user_id, message.chat.id, message.id))
    
async def process_files(user_id, chat_id, message_id):
    """پردازش اصلی فایل‌ها"""
    temp_files = []
    processing_msg = None
    
    try:
        processing_msg = await send_message(chat_id, "📥 در حال دانلود فایل‌ها...")
        progress.message = processing_msg
        
        file_infos = []
        for file_data in user_files[user_id]:
            try:
                file_msg = await app.get_messages(chat_id, file_data["message_id"])
                if not file_msg:
                    continue
                    
                file_path = f"/tmp/{file_data['file_name']}"
                temp_files.append(file_path)
                
                if await download_file(file_msg, file_path):
                    file_infos.append({
                        'path': file_path,
                        'name': file_data['file_name'],
                        'size': os.path.getsize(file_path)
                    })
                    
                await asyncio.sleep(1)
                ResourceManager.free_memory()
                
            except Exception as e:
                logger.error(f"Download error: {e}")
                continue
        
        if not file_infos:
            await processing_msg.edit_text("❌ خطا در دانلود")
            return
            
        await processing_msg.edit_text("📦 در حال ایجاد زیپ...")
        
        zip_name = f"archive_{int(time.time())}"
        zip_path = f"/tmp/{zip_name}.zip"
        temp_files.append(zip_path)
        
        # ایجاد زیپ
        success = await asyncio.get_event_loop().run_in_executor(
            zip_executor,
            create_zip_safe,
            zip_path, file_infos, None
        )
        
        if not success or not os.path.exists(zip_path):
            await processing_msg.edit_text("❌ خطا در ایجاد زیپ")
            return
            
        zip_size = os.path.getsize(zip_path)
        await processing_msg.edit_text(f"📤 آپلود فایل ({progress.format_size(zip_size)})...")
        
        # آپلود
        upload_success = await upload_file(
            zip_path,
            chat_id,
            f"📦 فایل زیپ شده\n💾 حجم: {progress.format_size(zip_size)}",
            message_id
        )
        
        if upload_success:
            await send_message(chat_id, f"✅ پردازش کامل شد!", message_id)
        else:
            await send_message(chat_id, "⚠️ آپلود با خطا مواجه شد", message_id)
            
    except Exception as e:
        logger.error(f"Process error: {e}")
        if processing_msg:
            await processing_msg.edit_text("❌ خطا در پردازش")
    finally:
        # پاک‌سازی
        for file_path in temp_files:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except:
                pass
                
        if user_id in user_files:
            user_files[user_id] = []
        if user_id in user_states:
            user_states.pop(user_id)
            
        save_user_data()
        ResourceManager.free_memory()

# ===== مدیریت صف =====
async def process_queue():
    while True:
        if task_queue:
            task = task_queue.popleft()
            await task[0](*task[1:])
            await asyncio.sleep(5)
        else:
            await asyncio.sleep(2)

# ===== راه‌اندازی =====
async def run_bot():
    global app
    
    load_user_data()
    
    app = Client(
        "user_bot",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        session_string=Config.SESSION_STRING,
        in_memory=True
    )
    
    # ثبت هندلرها
    app.on_message(filters.command("start"))(start)
    app.on_message(filters.command("zip"))(start_zip_process)
    app.on_message(filters.document | filters.video)(handle_file)
    
    await app.start()
    logger.info("✅ Bot started successfully!")
    
    # شروع پردازش صف
    asyncio.create_task(process_queue())
    
    # پاک‌سازی دوره‌ای
    async def cleanup_task():
        while True:
            await asyncio.sleep(Config.CLEANUP_INTERVAL)
            ResourceManager.free_memory()
    
    asyncio.create_task(cleanup_task())
    
    await asyncio.Event().wait()

# ===== وب سرور =====
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "🤖 Large Files Bot - Running", 200

def run_web():
    web_app.run(host="0.0.0.0", port=10000, debug=False)

if __name__ == "__main__":
    # راه‌اندازی وب در ترد جداگانه
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    
    # راه‌اندازی ربات
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot stopped")
        save_user_data()
    except Exception as e:
        logger.error(f"Bot failed: {e}")
        save_user_data()
