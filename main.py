import os
import time
import tempfile
import pyzipper
import logging
import sys
import asyncio
import aiohttp
import aiofiles
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, RPCError
from flask import Flask, jsonify
import threading
from collections import deque
import random
import math
from typing import Dict, List, Callable, Any, Tuple, Optional
from pathlib import Path
import json
from datetime import datetime
import shutil
from concurrent.futures import ThreadPoolExecutor
import queue
import re
import gc
import psutil
import signal

# ===== تنظیمات فوق العاده بهینه برای فایل‌های بزرگ =====
class Config:
    API_ID = 1867911
    API_HASH = "f9e86b274826212a2712b18754fabc47"
    SESSION_STRING = "BAAcgIcAHFzRCBK8bXUoRyPGXLQaXCUVVc8YRwzBkm7m9RHERF-DwcIcuO1XunQeFNnVTsCgpeR4yfVY-qygIVL-ayKd4FXec1Q0AJSwwMztT_JNgRjlIupm9OaujoI68FlcNibGtCYEkktHPWlN7I8F5ux34MWBQbK3v6DIXfKyAza3yCksCwYI7YoZz7-Ay2d3XK2S_GDqcNW3DF-PGGc-ZAnpdPe11aDiX1vwpDjXm0pV0_Cw5GeHgLUm6LcZ1PwPLvIkUDhhGsR3cFYHHrxjS4SuD-cgb4Zjv9r7zBJ5HGaGnBPZKRW3OSxnv2DpnaJOoX_tbFAp0ZWNYOFTsIX6Nt55xgAAAAAY4xquAA"
    ALLOWED_USER_IDS = [417536686]
    
    # محدودیت‌های واقع‌بینانه برای رندر رایگان
    MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB - برای فایل‌های بزرگ
    MAX_TOTAL_SIZE = 3 * 1024 * 1024 * 1024  # 3GB - حجم کل
    MAX_FILES_COUNT = 5  # حداکثر 5 فایل
    
    DEFAULT_PART_SIZE = 100 * 1024 * 1024  # 100MB
    CHUNK_SIZE = 128 * 1024  # 128KB - کاهش برای صرفه‌جویی در RAM
    
    MAX_CONCURRENT_DOWNLOADS = 1
    MAX_CONCURRENT_UPLOADS = 1
    RETRY_DELAY = 3  # تاخیر کمتر
    PROGRESS_UPDATE_INTERVAL = 3.0  # افزایش اینتروال آپدیت
    
    DATA_FILE = "user_data.json"
    UPLOAD_CHUNK_SIZE = 512 * 1024  # 512KB - کاهش بیشتر
    
    MAX_UPLOAD_RETRIES = 2
    ZIP_COMPRESSION_LEVEL = 0  # بدون فشرده‌سازی - حیاتی برای فایل‌های بزرگ
    MAX_ZIP_RETRIES = 1
    
    # تایم‌اوت‌های واقع‌بینانه
    ZIP_BASE_TIMEOUT = 3600  # 1 ساعت
    ZIP_TIMEOUT_PER_GB = 1200  # 20 دقیقه به ازای هر GB
    
    MEMORY_LIMIT = 350 * 1024 * 1024  # 350MB
    STREAMING_CHUNK_SIZE = 512 * 1024  # 512KB chunks - بسیار مهم
    MAX_STREAMING_BUFFER = 1 * 1024 * 1024  # 1MB max buffer
    
    # تنظیمات جدید برای فایل‌های بزرگ
    CLEANUP_INTERVAL = 30  # پاک‌سازی هر 30 ثانیه
    MEMORY_CHECK_INTERVAL = 5  # بررسی حافظه هر 5 ثانیه
    DISK_BUFFER_DIR = "/tmp/large_files"  # دایرکتوری برای فایل‌های بزرگ

# ===== مدیریت حافظه و دیسک پیشرفته =====
class ResourceManager:
    @staticmethod
    def setup_disk_buffer():
        """ایجاد دایرکتوری برای فایل‌های بزرگ"""
        try:
            os.makedirs(Config.DISK_BUFFER_DIR, exist_ok=True)
            logger.info(f"Disk buffer directory created: {Config.DISK_BUFFER_DIR}")
        except Exception as e:
            logger.error(f"Error creating disk buffer: {e}")
    
    @staticmethod
    def get_memory_usage():
        try:
            process = psutil.Process()
            return process.memory_info().rss
        except:
            return 0
    
    @staticmethod
    def get_disk_usage():
        try:
            usage = shutil.disk_usage(Config.DISK_BUFFER_DIR)
            return usage.free
        except:
            return 0
    
    @staticmethod
    def is_memory_critical():
        return ResourceManager.get_memory_usage() > Config.MEMORY_LIMIT * 0.7
    
    @staticmethod
    def is_disk_space_low():
        return ResourceManager.get_disk_usage() < 500 * 1024 * 1024  # کمتر از 500MB فضای آزاد
    
    @staticmethod
    def free_resources():
        """پاک‌سازی جامع منابع"""
        gc.collect()
        
        # پاک‌سازی فایل‌های موقت قدیمی
        try:
            for root, dirs, files in os.walk(Config.DISK_BUFFER_DIR):
                for file in files:
                    if file.startswith('temp_'):
                        file_path = os.path.join(root, file)
                        file_age = time.time() - os.path.getctime(file_path)
                        if file_age > 1800:  # فایل‌های قدیمی‌تر از 30 دقیقه
                            os.remove(file_path)
        except Exception as e:
            logger.error(f"Error in resource cleanup: {e}")
    
    @staticmethod
    def get_resource_info():
        try:
            process = psutil.Process()
            memory = process.memory_info()
            disk_free = ResourceManager.get_disk_usage()
            
            return {
                'memory_rss': memory.rss,
                'memory_percent': process.memory_percent(),
                'disk_free_mb': disk_free / 1024 / 1024,
                'critical': ResourceManager.is_memory_critical() or ResourceManager.is_disk_space_low()
            }
        except:
            return {'memory_rss': 0, 'memory_percent': 0, 'disk_free_mb': 0, 'critical': False}

# ===== لاگ بهینه‌شده =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8", mode='a')
    ]
)
logger = logging.getLogger(__name__)

# ===== کلاینت Pyrogram =====
app = None

# ===== داده‌ها و وضعیت =====
user_files: Dict[int, List] = {}
user_states: Dict[int, Any] = {}
scheduled_tasks: List[Tuple[float, Callable, Tuple, Dict]] = []
task_queue = deque()
processing = False
download_semaphore = asyncio.Semaphore(Config.MAX_CONCURRENT_DOWNLOADS)
upload_semaphore = asyncio.Semaphore(Config.MAX_CONCURRENT_UPLOADS)
zip_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ZipWorker")

# ===== کلاس مدیریت پیشرفت فوق بهینه =====
class ProgressTracker:
    def __init__(self):
        self.start_time = time.time()
        self.last_update = 0
        self.last_text = ""
        self.last_percent = 0
        self.current = 0
        self.total = 0
        self.stage = ""
        self.file_name = ""
        self.message = None
        self.file_index = 0
        self.total_files = 0
        self.lock = asyncio.Lock()
        self.zip_progress_queue = queue.Queue()
        self.upload_progress_queue = queue.Queue()
        self.is_uploading = False
        self.last_memory_check = time.time()
        self.update_count = 0

    def reset(self, message: Message = None, stage: str = "", file_name: str = "", file_index: int = 0, total_files: int = 0):
        self.start_time = time.time()
        self.last_update = 0
        self.last_text = ""
        self.last_percent = 0
        self.current = 0
        self.total = 0
        self.stage = stage
        self.file_name = file_name
        self.message = message
        self.file_index = file_index
        self.total_files = total_files
        self.is_uploading = (stage == "آپلود")
        self.last_memory_check = time.time()
        self.update_count = 0

    async def update(self, current: int, total: int):
        try:
            async with self.lock:
                now = time.time()
                self.update_count += 1
                
                # بررسی منابع هر 5 ثانیه
                if now - self.last_memory_check > Config.MEMORY_CHECK_INTERVAL:
                    if ResourceManager.is_memory_critical() or ResourceManager.is_disk_space_low():
                        ResourceManager.free_resources()
                    self.last_memory_check = now
                
                # کاهش شدید فرکانس آپدیت
                update_interval = Config.PROGRESS_UPDATE_INTERVAL
                if self.is_uploading:
                    update_interval = 2.0
                
                if now - self.last_update < update_interval and current != total:
                    return
                
                # فقط هر 15 آپدیت یکبار نمایش دهید
                if self.update_count % 15 != 0 and not self.is_uploading:
                    return
                
                self.current = current
                self.total = total
                self.last_update = now
                
                percent = (current / total) * 100 if total > 0 else 0
                
                # متن بسیار ساده برای کاهش پردازش
                progress_text = (
                    f"⏳ {self.stage}\n"
                    f"📁: {self.file_name[:15]}...\n"
                    f"📊: {self.format_size(current)}/{self.format_size(total)}\n"
                    f"📈: {percent:.1f}%"
                )
                
                if self.last_text != progress_text and self.message:
                    try:
                        await self.message.edit_text(progress_text)
                        self.last_text = progress_text
                    except Exception as e:
                        logger.error(f"Error updating progress: {e}")
                        
        except Exception as e:
            logger.error(f"Progress update error: {e}")

    async def update_zip_progress(self):
        try:
            while True:
                try:
                    current, total = self.zip_progress_queue.get_nowait()
                    await self.update(current, total)
                    await asyncio.sleep(2)  # افزایش sleep
                except queue.Empty:
                    await asyncio.sleep(3)
        except Exception as e:
            logger.error(f"Zip progress update error: {e}")

    async def update_upload_progress(self, current: int, total: int):
        try:
            await self.update(current, total)
        except Exception as e:
            logger.error(f"Upload progress update error: {e}")

    @staticmethod
    def format_size(size_bytes: int) -> str:
        if size_bytes == 0:
            return "0B"
        size_names = ["B", "KB", "MB", "GB"]
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 1)
        return f"{s}{size_names[i]}"

# ایجاد نمونه پیشرفت
progress_tracker = ProgressTracker()

# ===== فانکشن‌های کمکی =====
def is_user_allowed(user_id: int) -> bool:
    return user_id in Config.ALLOWED_USER_IDS

def load_user_data():
    global user_files, user_states
    try:
        if os.path.exists(Config.DATA_FILE):
            with open(Config.DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                user_files = {int(k): v for k, v in data.get('user_files', {}).items()}
                user_states = {int(k): v for k, v in data.get('user_states', {}).items()}
    except Exception as e:
        logger.error(f"Error loading user data: {e}")

def save_user_data():
    try:
        data = {
            'user_files': user_files,
            'user_states': user_states
        }
        with open(Config.DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error saving user data: {e}")

async def safe_send_message(chat_id, text, reply_to_message_id=None):
    try:
        await asyncio.sleep(1)
        return await app.send_message(chat_id, text, reply_to_message_id=reply_to_message_id)
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return None

async def safe_download_media(message, file_path, file_name="", file_index=0, total_files=0, processing_msg=None):
    try:
        async with download_semaphore:
            await asyncio.sleep(1)
            
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            progress_tracker.reset(processing_msg, "دانلود", file_name, file_index, total_files)
            
            # دانلود با chunk کوچک‌تر
            await app.download_media(
                message,
                file_name=file_path,
                progress=progress_tracker.update,
                block=False
            )
            
            if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                return True
            
    except FloodWait as e:
        logger.warning(f"Download FloodWait: {e.value} seconds")
        await asyncio.sleep(e.value + 2)
    except Exception as e:
        logger.error(f"Download error: {e}")
    
    return False

# ===== سیستم زیپ‌سازی برای فایل‌های بزرگ =====
def zip_large_files_streaming(zip_path: str, files: List[Dict], password: Optional[str], progress_queue: queue.Queue) -> bool:
    try:
        total_size = sum(f['size'] for f in files)
        processed_size = 0
        
        logger.info(f"Starting large zip creation, total: {total_size/1024/1024/1024:.2f}GB")
        
        # بدون فشرده‌سازی برای صرفه‌جویی در RAM
        compression = pyzipper.ZIP_STORED
        
        with pyzipper.AESZipFile(
            zip_path, 
            "w", 
            compression=compression,
            compresslevel=Config.ZIP_COMPRESSION_LEVEL,
            encryption=pyzipper.WZ_AES if password else None,
            allowZip64=True
        ) as zipf:
            
            if password:
                zipf.setpassword(password.encode('utf-8'))
            
            for file_info in files:
                file_path = file_info['path']
                arcname = os.path.basename(file_info['name'])
                
                if not os.path.exists(file_path):
                    continue
                
                try:
                    with open(file_path, 'rb') as f:
                        with zipf.open(arcname, 'w', force_zip64=True) as zf:
                            while True:
                                chunk = f.read(Config.STREAMING_CHUNK_SIZE)
                                if not chunk:
                                    break
                                zf.write(chunk)
                                processed_size += len(chunk)
                                
                                # آپدیت پیشرفت هر 25MB
                                if processed_size % (25 * 1024 * 1024) < Config.STREAMING_CHUNK_SIZE:
                                    progress_queue.put((processed_size, total_size))
                                    # پاک‌سازی منابع هر 100MB
                                    if processed_size % (100 * 1024 * 1024) < Config.STREAMING_CHUNK_SIZE:
                                        gc.collect()
                    
                except Exception as e:
                    logger.error(f"Error adding file {arcname}: {e}")
                    continue
        
        return os.path.exists(zip_path) and os.path.getsize(zip_path) > 0
            
    except Exception as e:
        logger.error(f"Error in large zip creation: {e}")
        try:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        except:
            pass
        return False

async def create_large_zip(zip_path: str, files: List[Dict], password: Optional[str] = None) -> bool:
    try:
        os.makedirs(os.path.dirname(zip_path), exist_ok=True)
        
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except:
                pass
        
        total_size_mb = sum(f['size'] for f in files) / (1024 * 1024)
        dynamic_timeout = min(Config.ZIP_BASE_TIMEOUT + (total_size_mb / 1024 * Config.ZIP_TIMEOUT_PER_GB), 7200)
        
        logger.info(f"Creating large zip, size: {total_size_mb/1024:.2f}GB, timeout: {dynamic_timeout/60:.1f}min")
        
        loop = asyncio.get_event_loop()
        
        success = await asyncio.wait_for(
            loop.run_in_executor(
                zip_executor, 
                zip_large_files_streaming, 
                zip_path, files, password, progress_tracker.zip_progress_queue
            ),
            timeout=dynamic_timeout
        )
        
        return success
            
    except asyncio.TimeoutError:
        logger.error("Zip creation timeout")
        try:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        except:
            pass
        return False
    except Exception as e:
        logger.error(f"Unexpected error in zip creation: {e}")
        return False

# ===== آپلود فایل‌های بزرگ =====
async def upload_large_file(file_path: str, chat_id: int, caption: str, reply_to_message_id: int, progress_callback, part_size: int) -> bool:
    try:
        async with upload_semaphore:
            file_size = os.path.getsize(file_path)
            
            if file_size <= part_size:
                await app.send_document(
                    chat_id=chat_id,
                    document=file_path,
                    caption=caption,
                    reply_to_message_id=reply_to_message_id,
                    progress=progress_callback,
                    block=False
                )
            else:
                # آپلود تکه‌تکه برای فایل‌های بسیار بزرگ
                await upload_in_chunks(file_path, chat_id, caption, reply_to_message_id, progress_callback, part_size)
            
            return True
            
    except FloodWait as e:
        logger.warning(f"Upload FloodWait: {e.value} seconds")
        await asyncio.sleep(e.value + 3)
        return False
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return False

async def upload_in_chunks(file_path: str, chat_id: int, caption: str, reply_to_message_id: int, progress_callback, part_size: int):
    file_size = os.path.getsize(file_path)
    total_parts = (file_size + part_size - 1) // part_size
    
    logger.info(f"Uploading in {total_parts} parts, part size: {part_size/1024/1024:.1f}MB")
    
    try:
        with open(file_path, 'rb') as f:
            for part_num in range(total_parts):
                part_data = f.read(part_size)
                if not part_data:
                    break
                
                temp_part_path = f"{file_path}_part{part_num + 1}"
                with open(temp_part_path, 'wb') as part_file:
                    part_file.write(part_data)
                
                part_caption = f"{caption} - Part {part_num + 1}/{total_parts}"
                
                await app.send_document(
                    chat_id=chat_id,
                    document=temp_part_path,
                    caption=part_caption,
                    reply_to_message_id=reply_to_message_id,
                    progress=progress_callback,
                    block=False
                )
                
                try:
                    os.remove(temp_part_path)
                except:
                    pass
                
                await asyncio.sleep(2)  # استراحت بین آپلودها
                ResourceManager.free_resources()  # پاک‌سازی منابع بعد از هر پارت
        
    except Exception as e:
        logger.error(f"Error in chunked upload: {e}")
        raise

# ===== هندلرهای اصلی =====
async def start(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    
    welcome_text = (
        "🤖 **ربات پردازش فایل‌های بزرگ**\n\n"
        "✨ **پشتیبانی از فایل‌های تا 2GB**\n\n"
        "📝 **روش استفاده:**\n"
        "1. فایل‌ها را ارسال کنید (حداکثر 5 فایل)\n"
        "2. از /zip برای شروع استفاده کنید\n\n"
        "⚙️ **توجه:**\n"
        "• پردازش فایل‌های بزرگ زمان‌بر است\n"
        "• از اتصال پایدار اینترنت مطمئن شوید"
    )
    
    await safe_send_message(message.chat.id, welcome_text, message.id)

async def handle_file(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    
    if not message.document and not message.video:
        return
    
    file_obj = message.document or message.video
    file_name = getattr(file_obj, 'file_name', None) or f"file_{message.id}"
    file_size = file_obj.file_size
    
    # بررسی محدودیت‌ها
    if file_size > Config.MAX_FILE_SIZE:
        await safe_send_message(
            message.chat.id,
            f"❌ فایل بسیار بزرگ!\nحجم: {progress_tracker.format_size(file_size)}\nحداکثر: {progress_tracker.format_size(Config.MAX_FILE_SIZE)}",
            message.id
        )
        return
    
    user_id = message.from_user.id
    if user_id not in user_files:
        user_files[user_id] = []
    
    if len(user_files[user_id]) >= Config.MAX_FILES_COUNT:
        await safe_send_message(
            message.chat.id,
            f"❌ تعداد فایل‌ها زیاد است!\nحداکثر: {Config.MAX_FILES_COUNT} فایل",
            message.id
        )
        return
    
    total_size = sum(f["file_size"] for f in user_files[user_id]) + file_size
    if total_size > Config.MAX_TOTAL_SIZE:
        await safe_send_message(
            message.chat.id,
            f"❌ حجم کل زیاد است!\nحجم کل: {progress_tracker.format_size(total_size)}\nحداکثر: {progress_tracker.format_size(Config.MAX_TOTAL_SIZE)}",
            message.id
        )
        return
    
    user_files[user_id].append({
        "message_id": message.id,
        "file_name": file_name, 
        "file_size": file_size
    })
    
    await safe_send_message(
        message.chat.id,
        f"✅ فایل ذخیره شد\n\n📝: {file_name}\n📦: {progress_tracker.format_size(file_size)}\n\n📌 از /zip استفاده کنید",
        message.id
    )
    
    save_user_data()

async def start_zip(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    
    user_id = message.from_user.id
    if user_id not in user_files or not user_files[user_id]:
        await safe_send_message(message.chat.id, "❌ هیچ فایلی وجود ندارد", message.id)
        return
    
    user_states[user_id] = "waiting_password"
    
    await safe_send_message(
        message.chat.id,
        "🔐 لطفاً رمز عبور وارد کنید:\n(یا /skip برای بدون رمز)",
        message.id
    )

async def process_zip(client, message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_states:
        return
    
    if user_states.get(user_id) == "waiting_password":
        if message.text == "/skip":
            zip_password = None
        else:
            zip_password = message.text.strip()
            
            if not zip_password or len(zip_password) < 4:
                await message.reply("❌ رمز باید حداقل 4 کاراکتر باشد")
                return
        
        user_states[user_id] = "ready_to_zip"
        user_states[f"{user_id}_password"] = zip_password
        
        zip_name = f"archive_{int(time.time())}"
        user_states[f"{user_id}_zipname"] = zip_name
        
        total_files = len(user_files[user_id])
        total_size = sum(f["file_size"] for f in user_files[user_id])
        
        await message.reply(
            f"📦 آماده برای پردازش\n\n"
            f"📝 نام: {zip_name}.zip\n"
            f"🔑 رمز: {'✓' if zip_password else '✗'}\n"
            f"📊 تعداد: {total_files} فایل\n"
            f"💾 حجم: {progress_tracker.format_size(total_size)}\n\n"
            f"✅ /zipnow برای شروع"
        )

async def start_zip_now(client, message: Message):
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id) or user_states.get(user_id) != "ready_to_zip":
        await message.reply("❌ ابتدا مراحل را کامل کنید")
        return
    
    zip_name = user_states.get(f"{user_id}_zipname", f"archive_{int(time.time())}")
    zip_password = user_states.get(f"{user_id}_password")
    
    await message.reply(
        f"🚀 شروع پردازش فایل‌های بزرگ...\n\n"
        f"⏳ این فرآیند ممکن است زمان‌بر باشد\n"
        f"📊 لطفاً صبور باشید...",
        parse_mode=enums.ParseMode.MARKDOWN
    )
    
    add_to_queue(process_large_files, user_id, zip_name, zip_password, message.chat.id, message.id)

async def process_large_files(user_id, zip_name, zip_password, chat_id, message_id):
    processing_msg = None
    temp_files = []
    
    try:
        processing_msg = await app.send_message(chat_id, "⏳ در حال آماده‌سازی فایل‌های بزرگ...")
        
        total_files = len(user_files[user_id])
        file_info_list = []
        
        for i, finfo in enumerate(user_files[user_id], 1):
            try:
                file_msg = await app.get_messages(chat_id, finfo["message_id"])
                if not file_msg:
                    continue
                
                file_name = finfo["file_name"]
                file_path = os.path.join(Config.DISK_BUFFER_DIR, f"temp_{user_id}_{file_name}")
                temp_files.append(file_path)
                
                success = await safe_download_media(
                    file_msg,
                    file_path,
                    file_name,
                    i,
                    total_files,
                    processing_msg
                )
                
                if success:
                    file_info_list.append({
                        'path': file_path,
                        'name': file_name,
                        'size': os.path.getsize(file_path)
                    })
                
                await asyncio.sleep(1)
                ResourceManager.free_resources()
                
            except Exception as e:
                logger.error(f"Error processing file: {e}")
                continue
        
        if not file_info_list:
            await processing_msg.edit_text("❌ خطا در پردازش فایل‌ها")
            return
        
        await processing_msg.edit_text("📦 در حال ایجاد فایل زیپ...")
        
        final_zip_name = f"{zip_name}.zip"
        zip_path = os.path.join(Config.DISK_BUFFER_DIR, f"zip_{user_id}_{final_zip_name}")
        
        success = await create_large_zip(zip_path, file_info_list, zip_password)
        if not success:
            await processing_msg.edit_text("❌ خطا در ایجاد فایل زیپ")
            return
        
        zip_size = os.path.getsize(zip_path)
        await processing_msg.edit_text(f"📤 آپلود فایل ({progress_tracker.format_size(zip_size)})...")
        
        progress_tracker.reset(processing_msg, "آپلود", final_zip_name, 1, 1)
        
        upload_success = await upload_large_file(
            zip_path,
            chat_id,
            f"📦 فایل زیپ شده\n🔑 رمز: {zip_password or 'بدون'}\n💾 حجم: {progress_tracker.format_size(zip_size)}",
            message_id,
            progress_tracker.update_upload_progress,
            Config.DEFAULT_PART_SIZE
        )
        
        if upload_success:
            result_text = (
                f"✅ پردازش کامل شد!\n\n"
                f"📦 فایل: {final_zip_name}\n"
                f"💾 حجم: {progress_tracker.format_size(zip_size)}\n"
                f"🔑 رمز: {zip_password or 'بدون'}"
            )
        else:
            result_text = "❌ خطا در آپلود"
        
        await safe_send_message(chat_id, result_text, message_id)
        
    except Exception as e:
        logger.error(f"Error in large files processing: {e}")
        if processing_msg:
            await processing_msg.edit_text("❌ خطا در پردازش")
    finally:
        # پاک‌سازی نهایی
        for file_path in temp_files:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except:
                pass
        
        try:
            if 'zip_path' in locals() and os.path.exists(zip_path):
                os.remove(zip_path)
        except:
            pass
        
        if user_id in user_files:
            user_files[user_id] = []
        user_states.pop(user_id, None)
        user_states.pop(f"{user_id}_password", None)
        user_states.pop(f"{user_id}_zipname", None)
        
        save_user_data()
        ResourceManager.free_resources()

# ===== راه‌اندازی ربات =====
async def run_bot():
    global app
    logger.info("🚀 Starting Large Files Bot for Render Free...")
    
    # راه‌اندازی اولیه
    ResourceManager.setup_disk_buffer()
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
    app.on_message(filters.command("zip"))(start_zip)
    app.on_message(filters.command("zipnow"))(start_zip_now)
    app.on_message(filters.command("skip"))(process_zip)
    app.on_message(filters.document | filters.video)(handle_file)
    app.on_message(filters.text & filters.create(lambda _, __, m: m.from_user.id in user_states))(process_zip)
    
    await app.start()
    logger.info("✅ Bot started successfully!")
    
    # تسک پاک‌سازی دوره‌ای
    async def periodic_cleanup():
        while True:
            await asyncio.sleep(Config.CLEANUP_INTERVAL)
            ResourceManager.free_resources()
    
    asyncio.create_task(periodic_cleanup())
    
    await asyncio.Event().wait()

# ===== وب سرور ساده =====
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "🤖 Large Files Bot - Render Free", 200

@web_app.route('/health')
def health_check():
    resources = ResourceManager.get_resource_info()
    return jsonify({
        "status": "healthy" if not resources['critical'] else "warning",
        "memory_usage": f"{resources['memory_rss'] / 1024 / 1024:.1f}MB",
        "disk_free": f"{resources['disk_free_mb']:.1f}MB",
        "critical": resources['critical']
    }), 200

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    # راه‌اندازی وب سرور در ترد جداگانه
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    # راه‌اندازی ربات
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("👋 Bot stopped")
        save_user_data()
    except Exception as e:
        logger.error(f"Bot error: {e}")
        save_user_data()
