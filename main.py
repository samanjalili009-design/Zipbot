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

# ===== تنظیمات بهینه‌شده برای رندر رایگان =====
class Config:
    API_ID = 1867911
    API_HASH = "f9e86b274826212a2712b18754fabc47"
    SESSION_STRING = "BAAcgIcAHFzRCBK8bXUoRyPGXLQaXCUVVc8YRwzBkm7m9RHERF-DwcIcuO1XunQeFNnVTsCgpeR4yfVY-qygIVL-ayKd4FXec1Q0AJSwwMztT_JNgRjlIupm9OaujoI68FlcNibGtCYEkktHPWlN7I8F5ux34MWBQbK3v6DIXfKyAza3yCksCwYI7YoZz7-Ay2d3XK2S_GDqcNW3DF-PGGc-ZAnpdPe11aDiX1vwpDjXm0pV0_Cw5GeHgLUm6LcZ1PwPLvIkUDhhGsR3cFYHHrxjS4SuD-cgb4Zjv9r7zBJ5HGaGnBPZKRW3OSxnv2DpnaJOoX_tbFAp0ZWNYOFTsIX6Nt55xgAAAAAY4xquAA"
    ALLOWED_USER_IDS = [417536686]
    
    # محدودیت‌های کاهش یافته برای رندر رایگان
    MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB (کاهش از 2GB)
    MAX_TOTAL_SIZE = 1 * 1024 * 1024 * 1024  # 1GB (کاهش از 4GB)
    MAX_FILES_COUNT = 10  # حداکثر 10 فایل
    
    DEFAULT_PART_SIZE = 50 * 1024 * 1024  # 50MB (کاهش از 100MB)
    CHUNK_SIZE = 256 * 1024  # 256KB (کاهش از 512KB)
    
    MAX_CONCURRENT_DOWNLOADS = 1
    MAX_CONCURRENT_UPLOADS = 1
    RETRY_DELAY = 5  # کاهش تاخیر
    PROGRESS_UPDATE_INTERVAL = 2.0  # افزایش اینتروال آپدیت
    
    DATA_FILE = "user_data.json"
    UPLOAD_CHUNK_SIZE = 1 * 1024 * 1024  # 1MB (کاهش از 2MB)
    
    MAX_UPLOAD_RETRIES = 2  # کاهش تعداد تلاش
    ZIP_COMPRESSION_LEVEL = 1  # سطح فشرده‌سازی پایین‌تر
    MAX_ZIP_RETRIES = 1  # فقط یکبار تلاش برای زیپ
    
    # تایم‌اوت‌های کوتاه‌تر برای رندر
    ZIP_BASE_TIMEOUT = 1800  # 30 دقیقه
    ZIP_TIMEOUT_PER_GB = 600  # 10 دقیقه به ازای هر GB
    
    MEMORY_LIMIT = 300 * 1024 * 1024  # 300MB محدودیت حافظه
    STREAMING_CHUNK_SIZE = 1 * 1024 * 1024  # 1MB chunks
    MAX_STREAMING_BUFFER = 2 * 1024 * 1024  # 2MB max buffer
    
    # تنظیمات جدید برای مدیریت منابع
    CLEANUP_INTERVAL = 60  # پاک‌سازی هر 60 ثانیه
    MEMORY_CHECK_INTERVAL = 10  # بررسی حافظه هر 10 ثانیه

# ===== لاگ بهینه‌شده =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8", mode='w')  # overwrite mode
    ]
)
logger = logging.getLogger(__name__)

# ===== مدیریت حافظه پیشرفته =====
class MemoryManager:
    @staticmethod
    def get_memory_usage():
        try:
            process = psutil.Process()
            return process.memory_info().rss
        except:
            return 0
    
    @staticmethod
    def is_memory_critical():
        return MemoryManager.get_memory_usage() > Config.MEMORY_LIMIT * 0.8
    
    @staticmethod
    def free_memory():
        gc.collect()
        logger.info("Memory cleanup performed")
    
    @staticmethod
    def get_memory_info():
        try:
            process = psutil.Process()
            memory = process.memory_info()
            return {
                'rss': memory.rss,
                'vms': memory.vms,
                'percent': process.memory_percent()
            }
        except:
            return {'rss': 0, 'vms': 0, 'percent': 0}

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

# ===== کلاس مدیریت پیشرفت بهینه‌شده =====
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
                
                # بررسی حافظه هر 10 ثانیه
                if now - self.last_memory_check > Config.MEMORY_CHECK_INTERVAL:
                    if MemoryManager.is_memory_critical():
                        MemoryManager.free_memory()
                    self.last_memory_check = now
                
                # کاهش فرکانس آپدیت برای صرفه‌جویی در منابع
                update_interval = Config.PROGRESS_UPDATE_INTERVAL
                if self.is_uploading:
                    update_interval = 1.0  # کاهش از 0.3 به 1.0
                
                if now - self.last_update < update_interval and current != total:
                    return
                
                # فقط هر 10 آپدیت یکبار حافظه چک شود
                if self.update_count % 10 != 0 and not self.is_uploading:
                    return
                
                self.current = current
                self.total = total
                self.last_update = now
                
                percent = (current / total) * 100 if total > 0 else 0
                elapsed = now - self.start_time
                speed = current / elapsed if elapsed > 0 else 0
                eta = (total - current) / speed if speed > 0 and current > 0 else 0
                
                if not self.is_uploading and abs(percent - self.last_percent) < 2.0 and current != total:
                    return
                
                self.last_percent = percent
                
                bar = self.get_progress_bar(percent)
                
                # متن ساده‌تر برای کاهش پردازش
                if self.total_files > 1:
                    progress_text = (
                        f"🚀 {self.stage} فایل {self.file_index}/{self.total_files}\n"
                        f"{bar}\n"
                        f"📁: {self.file_name[:20]}{'...' if len(self.file_name) > 20 else ''}\n"
                        f"📊: {self.format_size(current)}/{self.format_size(total)}\n"
                        f"⚡: {self.format_size(speed)}/s\n"
                        f"⏰: {self.format_time(int(eta))}"
                    )
                else:
                    progress_text = (
                        f"🚀 {self.stage}\n"
                        f"{bar}\n"
                        f"📁: {self.file_name[:20]}{'...' if len(self.file_name) > 20 else ''}\n"
                        f"📊: {self.format_size(current)}/{self.format_size(total)}\n"
                        f"⚡: {self.format_size(speed)}/s\n"
                        f"⏰: {self.format_time(int(eta))}"
                    )
                
                if self.last_text != progress_text and self.message:
                    try:
                        await self.message.edit_text(progress_text, parse_mode=enums.ParseMode.MARKDOWN)
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
                    await asyncio.sleep(1)  # کاهش فرکانس
                except queue.Empty:
                    await asyncio.sleep(2)  # افزایش sleep
        except Exception as e:
            logger.error(f"Zip progress update error: {e}")

    async def update_upload_progress(self, current: int, total: int):
        try:
            await self.update(current, total)
        except Exception as e:
            logger.error(f"Upload progress update error: {e}")

    @staticmethod
    def get_progress_bar(percentage: float, length: int = 10) -> str:  # کاهش طول progress bar
        filled = int(length * percentage / 100)
        bar = "⬢" * filled + "⬡" * (length - filled)
        return f"{bar} {percentage:.1f}%"

    @staticmethod
    def format_size(size_bytes: int) -> str:
        if size_bytes == 0:
            return "0B"
        size_names = ["B", "KB", "MB", "GB"]
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 1)  # کاهش دقت
        return f"{s}{size_names[i]}"

    @staticmethod
    def format_time(seconds: int) -> str:
        if seconds < 60:
            return f"{seconds}ث"
        elif seconds < 3600:
            return f"{seconds//60}د"
        else:
            return f"{seconds//3600}س"

# ایجاد نمونه پیشرفت
progress_tracker = ProgressTracker()

# ===== فانکشن‌های کمکی بهینه‌شده =====
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
                logger.info("User data loaded successfully")
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

async def safe_send_message(chat_id, text, reply_to_message_id=None, reply_markup=None, parse_mode=None):
    max_retries = 1  # کاهش تعداد تلاش
    for attempt in range(max_retries):
        try:
            await asyncio.sleep(random.uniform(1.0, 2.0))
            return await app.send_message(
                chat_id, 
                text, 
                reply_to_message_id=reply_to_message_id,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        except FloodWait as e:
            wait_time = e.value + random.uniform(1, 3)
            logger.warning(f"FloodWait: {wait_time} seconds")
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            await asyncio.sleep(1)
    
    return None

async def safe_download_media(message, file_path, file_name="", file_index=0, total_files=0, processing_msg=None):
    max_retries = 1  # کاهش تعداد تلاش
    for attempt in range(max_retries):
        try:
            async with download_semaphore:
                await asyncio.sleep(random.uniform(0.5, 1.0))
                
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                
                progress_tracker.reset(processing_msg, "دانلود", file_name, file_index, total_files)
                
                await app.download_media(
                    message,
                    file_name=file_path,
                    progress=progress_tracker.update
                )
                
                if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                    return True
                else:
                    logger.warning(f"Downloaded file is empty")
                    
        except FloodWait as e:
            wait_time = e.value + random.uniform(3, 5)
            logger.warning(f"Download FloodWait: {wait_time} seconds")
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(f"Download error: {e}")
            await asyncio.sleep(Config.RETRY_DELAY)
    
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except:
        pass
    
    return False

def schedule_task(task_func: Callable, delay: float, *args, **kwargs):
    execution_time = time.time() + delay
    scheduled_tasks.append((execution_time, task_func, args, kwargs))
    scheduled_tasks.sort(key=lambda x: x[0])

async def process_scheduled_tasks():
    while True:
        now = time.time()
        tasks_to_run = []
        
        for i, (execution_time, task_func, args, kwargs) in enumerate(scheduled_tasks):
            if execution_time <= now:
                tasks_to_run.append((task_func, args, kwargs))
                scheduled_tasks.pop(i)
            else:
                break
        
        for task_func, args, kwargs in tasks_to_run:
            try:
                if asyncio.iscoroutinefunction(task_func):
                    await task_func(*args, **kwargs)
                else:
                    await asyncio.to_thread(task_func, *args, **kwargs)
            except Exception as e:
                logger.error(f"Scheduled task error: {e}")
        
        await asyncio.sleep(3)  # افزایش sleep

async def process_task_queue():
    global processing
    
    while True:
        if not task_queue:
            await asyncio.sleep(3)  # افزایش sleep
            continue
        
        processing = True
        task_func, args, kwargs = task_queue.popleft()
        
        try:
            if asyncio.iscoroutinefunction(task_func):
                await task_func(*args, **kwargs)
            else:
                await asyncio.to_thread(task_func, *args, **kwargs)
            
            await asyncio.sleep(random.uniform(3.0, 5.0))  # افزایش تاخیر
            
        except FloodWait as e:
            wait_time = e.value + random.uniform(5, 8)
            logger.warning(f"FloodWait: {wait_time} seconds. Rescheduling...")
            
            schedule_task(task_func, wait_time, *args, **kwargs)
            
            user_id = kwargs.get('user_id', args[0] if args else None)
            if user_id:
                await notify_user_floodwait(user_id, wait_time)
            
            await asyncio.sleep(3)
            
        except Exception as e:
            logger.error(f"Task error: {e}")
            await asyncio.sleep(3)
        
        finally:
            processing = False
            MemoryManager.free_memory()
            save_user_data()

def add_to_queue(task_func: Callable, *args, **kwargs):
    task_queue.append((task_func, args, kwargs))
    logger.info(f"Task added to queue. Size: {len(task_queue)}")

async def notify_user_floodwait(user_id: int, wait_time: int):
    try:
        wait_minutes = wait_time // 60
        wait_seconds = wait_time % 60
        
        await safe_send_message(
            user_id,
            f"⏳ محدودیت موقت تلگرام\n"
            f"🕒 انتظار: {wait_minutes}د {wait_seconds}ث\n"
            f"✅ ادامه خودکار"
        )
    except Exception as e:
        logger.error(f"Error notifying user: {e}")

def calculate_zip_timeout(total_size_mb: float) -> int:
    base_timeout = Config.ZIP_BASE_TIMEOUT
    additional_time = max(0, (total_size_mb - 500) / 1024 * Config.ZIP_TIMEOUT_PER_GB)
    total_timeout = min(base_timeout + additional_time, 3600)  # حداکثر 1 ساعت
    return int(total_timeout)

def zip_creation_task_streaming(zip_path: str, files: List[Dict], password: Optional[str], progress_queue: queue.Queue) -> bool:
    try:
        total_size = sum(f['size'] for f in files)
        processed_size = 0
        
        logger.info(f"Starting zip for {len(files)} files, total: {total_size/1024/1024:.1f}MB")
        
        # بررسی وجود فایل‌ها
        for file_info in files:
            if not os.path.exists(file_info['path']):
                logger.error(f"File not found: {file_info['path']}")
                return False
        
        # عدم فشرده‌سازی برای صرفه‌جویی در RAM
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
                try:
                    zipf.setpassword(password.encode('utf-8'))
                except Exception as e:
                    logger.error(f"Error setting password: {e}")
                    return False
            
            # پردازش هر فایل به صورت streaming با بافر کوچک
            for file_info in files:
                file_path = file_info['path']
                arcname = os.path.basename(file_info['name'])
                
                if not os.path.exists(file_path):
                    continue
                
                try:
                    file_size = os.path.getsize(file_path)
                    
                    with open(file_path, 'rb') as f:
                        with zipf.open(arcname, 'w', force_zip64=True) as zf:
                            while True:
                                chunk = f.read(Config.STREAMING_CHUNK_SIZE)
                                if not chunk:
                                    break
                                zf.write(chunk)
                                processed_size += len(chunk)
                                
                                # ارسال پیشرفت هر 10MB
                                if processed_size % (10 * 1024 * 1024) < Config.STREAMING_CHUNK_SIZE:
                                    progress_queue.put((processed_size, total_size))
                    
                except Exception as e:
                    logger.error(f"Error adding file {arcname}: {e}")
                    continue
        
        # بررسی نهایی فایل زیپ
        if os.path.exists(zip_path) and os.path.getsize(zip_path) > 0:
            return True
        else:
            return False
            
    except Exception as e:
        logger.error(f"Error in zip creation: {e}")
        try:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        except:
            pass
        return False

async def create_zip_part_advanced(zip_path: str, files: List[Dict], default_password: Optional[str] = None) -> bool:
    max_retries = Config.MAX_ZIP_RETRIES
    
    for attempt in range(max_retries):
        try:
            os.makedirs(os.path.dirname(zip_path), exist_ok=True)
            
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except:
                    pass
            
            total_size_mb = sum(f['size'] for f in files) / (1024 * 1024)
            dynamic_timeout = calculate_zip_timeout(total_size_mb)
            
            logger.info(f"Zip attempt {attempt + 1}, size: {total_size_mb:.1f}MB, timeout: {dynamic_timeout/60:.1f}min")
            
            loop = asyncio.get_event_loop()
            
            success = await asyncio.wait_for(
                loop.run_in_executor(
                    zip_executor, 
                    zip_creation_task_streaming, 
                    zip_path, files, default_password, progress_tracker.zip_progress_queue
                ),
                timeout=dynamic_timeout
            )
            
            if success:
                return True
            
        except asyncio.TimeoutError:
            logger.error(f"Zip creation timeout")
            try:
                if os.path.exists(zip_path):
                    os.remove(zip_path)
            except:
                pass
            
            if attempt < max_retries - 1:
                await asyncio.sleep(5)
                
        except Exception as e:
            logger.error(f"Unexpected error in zip creation: {e}")
            try:
                if os.path.exists(zip_path):
                    os.remove(zip_path)
            except:
                pass
            
            if attempt < max_retries - 1:
                await asyncio.sleep(5)
    
    return False

async def upload_large_file_chunked(file_path: str, chat_id: int, caption: str, reply_to_message_id: int, 
                                  progress_callback, progress_args, part_size: int) -> bool:
    max_retries = Config.MAX_UPLOAD_RETRIES
    
    for attempt in range(max_retries):
        try:
            async with upload_semaphore:
                if attempt > 0:
                    await asyncio.sleep(random.uniform(3, 8))
                
                file_size = os.path.getsize(file_path)
                
                if file_size <= part_size:
                    await app.send_document(
                        chat_id=chat_id,
                        document=file_path,
                        caption=caption,
                        reply_to_message_id=reply_to_message_id,
                        progress=progress_callback,
                        progress_args=progress_args
                    )
                else:
                    await upload_file_in_parts(file_path, chat_id, caption, reply_to_message_id, progress_callback, progress_args, part_size)
                
                return True
                
        except FloodWait as e:
            wait_time = e.value + random.uniform(3, 8)
            logger.warning(f"Upload FloodWait: {wait_time} seconds")
            
            if attempt == max_retries - 1:
                return False
                
            await asyncio.sleep(wait_time)
            
        except Exception as e:
            logger.error(f"Error during upload: {e}")
            if attempt == max_retries - 1:
                return False
            await asyncio.sleep(Config.RETRY_DELAY)
    
    return False

async def upload_file_in_parts(file_path: str, chat_id: int, caption: str, reply_to_message_id: int,
                             progress_callback, progress_args, part_size: int):
    file_size = os.path.getsize(file_path)
    total_parts = (file_size + part_size - 1) // part_size
    
    logger.info(f"Uploading in {total_parts} parts, size: {part_size/1024/1024:.1f}MB")
    
    temp_parts = []
    
    try:
        with open(file_path, 'rb') as f:
            for part_num in range(total_parts):
                part_data = f.read(part_size)
                if not part_data:
                    break
                    
                temp_part_path = f"{file_path}_part{part_num + 1}"
                with open(temp_part_path, 'wb') as part_file:
                    part_file.write(part_data)
                
                temp_parts.append(temp_part_path)
                
                part_caption = f"{caption} - part {part_num + 1}/{total_parts}"
                
                await app.send_document(
                    chat_id=chat_id,
                    document=temp_part_path,
                    caption=part_caption,
                    reply_to_message_id=reply_to_message_id,
                    progress=progress_callback,
                    progress_args=progress_args
                )
                
                try:
                    os.remove(temp_part_path)
                    temp_parts.remove(temp_part_path)
                except:
                    pass
                
                await asyncio.sleep(2)  # افزایش تاخیر
        
    except Exception as e:
        logger.error(f"Error in chunked upload: {e}")
        raise
    finally:
        for temp_part in temp_parts:
            try:
                if os.path.exists(temp_part):
                    os.remove(temp_part)
            except:
                pass

async def cleanup_files(file_paths: List[str]):
    for file_path in file_paths:
        try:
            if os.path.exists(file_path):
                if os.path.isdir(file_path):
                    shutil.rmtree(file_path)
                else:
                    os.remove(file_path)
        except Exception as e:
            logger.error(f"Error cleaning up file {file_path}: {e}")

# ===== مدیریت پاک‌سازی دوره‌ای =====
async def periodic_cleanup():
    while True:
        await asyncio.sleep(Config.CLEANUP_INTERVAL)
        MemoryManager.free_memory()
        
        # پاک‌سازی فایل‌های موقت قدیمی
        try:
            temp_dir = tempfile.gettempdir()
            for file in os.listdir(temp_dir):
                if file.startswith('zip_bot_'):
                    file_path = os.path.join(temp_dir, file)
                    if os.path.isfile(file_path):
                        file_age = time.time() - os.path.getctime(file_path)
                        if file_age > 3600:  # فایل‌های قدیمی‌تر از 1 ساعت
                            os.remove(file_path)
                            logger.info(f"Cleaned up old temp file: {file}")
        except Exception as e:
            logger.error(f"Error in periodic cleanup: {e}")

# ===== هندلرهای اصلی =====
async def start(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    
    welcome_text = (
        "👋 **ربات زیپ و آپلود بهینه‌شده**\n\n"
        "✨ **قابلیت‌ها:**\n"
        "• 🔒 زیپ با رمزگذاری\n"
        "• 📦 تقسیم به پارت‌ها\n"
        "• ⚡ آپلود فایل‌های بزرگ\n\n"
        f"⚙️ **محدودیت‌ها:**\n"
        f"• حداکثر حجم فایل: {progress_tracker.format_size(Config.MAX_FILE_SIZE)}\n"
        f"• حداکثر حجم کل: {progress_tracker.format_size(Config.MAX_TOTAL_SIZE)}\n"
        f"• حداکثر تعداد فایل: {Config.MAX_FILES_COUNT}\n\n"
        "📝 **روش استفاده:**\n"
        "1. فایل‌ها را ارسال کنید\n"
        "2. از /zip برای شروع استفاده کنید"
    )
    
    await safe_send_message(
        message.chat.id,
        welcome_text,
        reply_to_message_id=message.id,
        parse_mode=enums.ParseMode.MARKDOWN
    )

async def handle_file(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    
    if not message.document and not message.video and not message.audio:
        return
    
    if message.document:
        file_obj = message.document
        file_type = "document"
    elif message.video:
        file_obj = message.video
        file_type = "video"
    elif message.audio:
        file_obj = message.audio
        file_type = "audio"
    else:
        return
    
    file_name = getattr(file_obj, 'file_name', None) or f"{file_type}_{message.id}"
    file_size = file_obj.file_size
    
    # بررسی محدودیت‌ها
    if file_size > Config.MAX_FILE_SIZE:
        await safe_send_message(
            message.chat.id,
            f"❌ حجم فایل زیاد است!\n"
            f"📦 حجم: {progress_tracker.format_size(file_size)}\n"
            f"⚖️ حد مجاز: {progress_tracker.format_size(Config.MAX_FILE_SIZE)}",
            reply_to_message_id=message.id
        )
        return
    
    user_id = message.from_user.id
    if user_id not in user_files:
        user_files[user_id] = []
    
    # بررسی تعداد فایل‌ها
    if len(user_files[user_id]) >= Config.MAX_FILES_COUNT:
        await safe_send_message(
            message.chat.id,
            f"❌ تعداد فایل‌ها زیاد است!\n"
            f"📊 حداکثر: {Config.MAX_FILES_COUNT} فایل",
            reply_to_message_id=message.id
        )
        return
    
    total_size = sum(f["file_size"] for f in user_files[user_id]) + file_size
    if total_size > Config.MAX_TOTAL_SIZE:
        await safe_send_message(
            message.chat.id,
            f"❌ حجم کل فایل‌ها زیاد است!\n"
            f"📦 حجم کل: {progress_tracker.format_size(total_size)}\n"
            f"⚖️ حد مجاز: {progress_tracker.format_size(Config.MAX_TOTAL_SIZE)}",
            reply_to_message_id=message.id
        )
        return
    
    user_files[user_id].append({
        "message_id": message.id,
        "file_name": file_name, 
        "file_size": file_size,
        "file_type": file_type,
        "added_time": time.time()
    })
    
    file_count = len(user_files[user_id])
    part_size = get_user_part_size(user_id) // (1024 * 1024)
    
    await safe_send_message(
        message.chat.id,
        f"✅ فایل ذخیره شد\n\n"
        f"📝 نام: `{file_name}`\n"
        f"📦 حجم: `{progress_tracker.format_size(file_size)}`\n"
        f"📏 پارت: `{part_size} MB`\n\n"
        f"📊 وضعیت: `{file_count}` فایل\n"
        f"📌 از /zip استفاده کنید",
        reply_to_message_id=message.id,
        parse_mode=enums.ParseMode.MARKDOWN
    )
    
    save_user_data()

async def start_zip(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    
    user_id = message.from_user.id
    if user_id not in user_files or not user_files[user_id]:
        await safe_send_message(
            message.chat.id,
            "❌ هیچ فایلی وجود ندارد",
            reply_to_message_id=message.id
        )
        return
    
    total_size = sum(f["file_size"] for f in user_files[user_id])
    if total_size > Config.MAX_TOTAL_SIZE:
        await safe_send_message(
            message.chat.id,
            f"❌ حجم کل فایل‌ها زیاد است!",
            reply_to_message_id=message.id
        )
        user_files[user_id] = []
        save_user_data()
        return
    
    user_states[user_id] = "waiting_password"
    
    part_size = get_user_part_size(user_id) // (1024 * 1024)
    
    await safe_send_message(
        message.chat.id,
        f"🔐 رمز عبور وارد کنید:\n\n"
        f"📏 پارت: `{part_size} MB`\n"
        f"📝 پس از رمز، /done بزنید",
        reply_to_message_id=message.id,
        parse_mode=enums.ParseMode.MARKDOWN
    )

async def start_zip_now(client, message: Message):
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    if user_states.get(user_id) != "ready_to_zip":
        await message.reply("❌ مراحل قبلی را کامل کنید")
        return
    
    zip_name = user_states.get(f"{user_id}_zipname", f"archive_{int(time.time())}")
    part_size = get_user_part_size(user_id) // (1024 * 1024)
    
    await message.reply(
        f"📦 شروع عملیات...\n\n"
        f"📝 نام: `{zip_name}.zip`\n"
        f"📏 پارت: `{part_size} MB`\n"
        f"⏳ لطفاً منتظر بمانید",
        parse_mode=enums.ParseMode.MARKDOWN
    )
    
    add_to_queue(process_zip_files, user_id, zip_name, message.chat.id, message.id)

async def cancel_zip(client, message: Message):
    user_id = message.from_user.id
    if user_id in user_files:
        user_files[user_id] = []
    
    user_states.pop(user_id, None)
    user_states.pop(f"{user_id}_password", None)
    user_states.pop(f"{user_id}_zipname", None)
    
    save_user_data()
    
    await safe_send_message(
        message.chat.id,
        "❌ عملیات لغو شد\n\n"
        "✅ فایل‌ها پاک شدند\n"
        "📌 می‌توانید دوباره شروع کنید",
        reply_to_message_id=message.id
    )

async def process_zip(client, message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_states:
        return
    
    if user_states.get(user_id) == "waiting_password":
        zip_password = message.text.strip()
        
        if not zip_password:
            await message.reply("❌ رمز عبور نمی‌تواند خالی باشد")
            return
        
        if len(zip_password) < 4:
            await message.reply("❌ رمز عبور باید حداقل 4 کاراکتر باشد")
            return
        
        user_states[user_id] = "waiting_filename"
        user_states[f"{user_id}_password"] = zip_password
        
        suggested_name = f"archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        part_size = get_user_part_size(user_id) // (1024 * 1024)
        
        await message.reply(
            f"📝 نام فایل زیپ را وارد کنید:\n\n"
            f"💡 پیشنهاد: `{suggested_name}`\n"
            f"📏 پارت: `{part_size} MB`\n\n"
            f"✅ پس از نام، /done بزنید",
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return
    
    if user_states.get(user_id) == "waiting_filename":
        zip_name = message.text.strip()
        if not zip_name:
            await message.reply("❌ نام فایل نمی‌تواند خالی باشد")
            return
        
        zip_name = re.sub(r'[<>:"/\\|?*]', '_', zip_name)
        zip_name = zip_name[:30]  # کاهش طول نام
        
        user_states[f"{user_id}_zipname"] = zip_name
        user_states[user_id] = "ready_to_zip"
        
        total_files = len(user_files[user_id])
        total_size = sum(f["file_size"] for f in user_files[user_id])
        password = user_states.get(f"{user_id}_password", "بدون رمز")
        part_size = get_user_part_size(user_id) // (1024 * 1024)
        
        await message.reply(
            f"📦 خلاصه درخواست:\n\n"
            f"📝 نام: `{zip_name}.zip`\n"
            f"🔑 رمز: `{password}`\n"
            f"📏 پارت: `{part_size} MB`\n"
            f"📊 تعداد: `{total_files}` فایل\n"
            f"💾 حجم: `{progress_tracker.format_size(total_size)}`\n\n"
            f"✅ برای شروع /zipnow بزنید\n"
            f"❌ برای لغو /cancel بزنید",
            parse_mode=enums.ParseMode.MARKDOWN
        )

async def process_zip_files(user_id, zip_name, chat_id, message_id):
    processing_msg = None
    temp_downloaded_files = []
    
    try:
        processing_msg = await app.send_message(chat_id, "⏳ در حال آماده‌سازی...", parse_mode=enums.ParseMode.MARKDOWN)
        zip_password = user_states.get(f"{user_id}_password")
        part_size = get_user_part_size(user_id)
        
        zip_progress_task = asyncio.create_task(progress_tracker.update_zip_progress())
        
        total_files = len(user_files[user_id])
        file_info_list = []
        
        for i, finfo in enumerate(user_files[user_id], 1):
            file_msg_id = finfo["message_id"]
            
            try:
                file_msg = await app.get_messages(chat_id, file_msg_id)
                if not file_msg:
                    continue
                
                file_name = finfo["file_name"]
                file_path = os.path.join(tempfile.gettempdir(), f"zip_bot_{user_id}_{file_name}")
                temp_downloaded_files.append(file_path)
                
                success = await safe_download_media(
                    file_msg,
                    file_path,
                    file_name,
                    i,
                    total_files,
                    processing_msg
                )
                
                if success and os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                    file_size = os.path.getsize(file_path)
                    file_info_list.append({
                        'path': file_path,
                        'name': file_name,
                        'size': file_size
                    })
                
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Error processing file: {e}")
                continue
        
        if not file_info_list:
            await processing_msg.edit_text("❌ هیچ فایلی دانلود نشد")
            return
        
        await processing_msg.edit_text("📦 در حال فشرده‌سازی...", parse_mode=enums.ParseMode.MARKDOWN)
        
        final_zip_name = f"{zip_name}.zip"
        zip_path = os.path.join(tempfile.gettempdir(), f"zip_bot_{user_id}_{final_zip_name}")
        
        total_size = sum(f['size'] for f in file_info_list)
        progress_tracker.reset(processing_msg, "فشرده‌سازی", final_zip_name, 1, 1)
        progress_tracker.total = total_size
        
        success = await create_zip_part_advanced(zip_path, file_info_list, zip_password)
        if not success:
            await processing_msg.edit_text("❌ خطا در ایجاد فایل زیپ")
            return
        
        if not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
            await processing_msg.edit_text("❌ فایل زیپ خالی است")
            return
        
        zip_size = os.path.getsize(zip_path)
        part_size_mb = part_size // (1024 * 1024)
        
        if zip_size <= part_size:
            await processing_msg.edit_text(
                f"📤 در حال آپلود...\n\n"
                f"📝 نام: `{final_zip_name}`\n"
                f"💾 حجم: `{progress_tracker.format_size(zip_size)}`",
                parse_mode=enums.ParseMode.MARKDOWN
            )
            
            progress_tracker.reset(processing_msg, "آپلود", final_zip_name, 1, 1)
            
            upload_success = await upload_large_file_chunked(
                file_path=zip_path,
                chat_id=chat_id,
                caption=(
                    f"📦 فایل زیپ شده\n"
                    f"🔑 رمز: `{zip_password or 'بدون رمز'}`\n"
                    f"💾 حجم: {progress_tracker.format_size(zip_size)}"
                ),
                reply_to_message_id=message_id,
                progress_callback=progress_tracker.update_upload_progress,
                progress_args=(),
                part_size=part_size
            )
            
            if upload_success:
                try:
                    if os.path.exists(zip_path):
                        os.remove(zip_path)
                except:
                    pass
            
        else:
            total_parts = (zip_size + part_size - 1) // part_size
            
            await processing_msg.edit_text(
                f"📦 تقسیم به {total_parts} پارت...\n\n"
                f"📝 نام: `{final_zip_name}`\n"
                f"💾 حجم: `{progress_tracker.format_size(zip_size)}`",
                parse_mode=enums.ParseMode.MARKDOWN
            )
            
            try:
                with open(zip_path, 'rb') as f:
                    for part_num in range(total_parts):
                        part_data = f.read(part_size)
                        if not part_data:
                            break
                            
                        part_path = f"{zip_path}_part{part_num + 1}"
                        with open(part_path, 'wb') as part_file:
                            part_file.write(part_data)
                        
                        progress_tracker.reset(processing_msg, "آپلود", f"پارت {part_num + 1}", part_num + 1, total_parts)
                        
                        part_caption = (
                            f"📦 پارت {part_num + 1}/{total_parts}\n"
                            f"🔑 رمز: `{zip_password or 'بدون رمز'}`"
                        )
                        
                        part_success = await upload_large_file_chunked(
                            file_path=part_path,
                            chat_id=chat_id,
                            caption=part_caption,
                            reply_to_message_id=message_id,
                            progress_callback=progress_tracker.update_upload_progress,
                            progress_args=(),
                            part_size=part_size
                        )
                        
                        try:
                            os.remove(part_path)
                        except:
                            pass
                
                upload_success = True
                
            except Exception as e:
                logger.error(f"Error splitting and uploading: {e}")
                upload_success = False
        
        if upload_success:
            await cleanup_files(temp_downloaded_files)
            result_text = (
                f"✅ عملیات تکمیل شد!\n\n"
                f"📦 فایل: `{final_zip_name}`\n"
                f"🔑 رمز: `{zip_password or 'بدون رمز'}`\n"
                f"📊 تعداد: `{len(file_info_list)}` فایل\n"
                f"💾 حجم: `{progress_tracker.format_size(zip_size)}`"
            )
        else:
            result_text = "❌ خطا در آپلود"
        
        await safe_send_message(
            chat_id,
            result_text,
            reply_to_message_id=message_id,
            parse_mode=enums.ParseMode.MARKDOWN
        )
        
    except FloodWait as e:
        logger.warning(f"FloodWait: {e.value} ثانیه")
        
        if processing_msg:
            await processing_msg.edit_text(
                f"⏳ توقف موقت\n\n"
                f"🕒 ادامه بعد از: {e.value} ثانیه",
                parse_mode=enums.ParseMode.MARKDOWN
            )
        
        schedule_task(process_zip_files, e.value + 10, user_id, zip_name, chat_id, message_id)
        
    except Exception as e:
        logger.error(f"Error in processing: {e}")
        if processing_msg:
            await processing_msg.edit_text("❌ خطا در پردازش")
    finally:
        if 'zip_progress_task' in locals():
            zip_progress_task.cancel()
        
        if user_id in user_files:
            user_files[user_id] = []
        user_states.pop(user_id, None)
        user_states.pop(f"{user_id}_password", None)
        user_states.pop(f"{user_id}_zipname", None)
        save_user_data()
        MemoryManager.free_memory()

async def run_bot():
    global app
    logger.info("🚀 Starting optimized bot for Render Free...")
    
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
    app.on_message(filters.command("cancel"))(cancel_zip)
    app.on_message(filters.document | filters.video | filters.audio)(handle_file)
    app.on_message(filters.text & filters.create(lambda _, __, m: m.from_user.id in user_states))(process_zip)
    
    # شروع تسک‌های پس‌زمینه
    asyncio.create_task(process_scheduled_tasks())
    asyncio.create_task(process_task_queue())
    asyncio.create_task(periodic_cleanup())
    
    await app.start()
    logger.info("✅ Bot started successfully on Render Free!")
    
    # ذخیره‌سازی دوره‌ای داده‌ها
    async def periodic_save():
        while True:
            await asyncio.sleep(300)
            save_user_data()
    
    asyncio.create_task(periodic_save())
    
    await asyncio.Event().wait()

# ===== وب سرور سبک‌تر برای رندر =====
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "🤖 Optimized Zip/Upload Bot is Running on Render Free", 200

@web_app.route('/health')
def health_check():
    memory_info = MemoryManager.get_memory_info()
    return {
        "status": "healthy",
        "memory_usage": f"{memory_info['rss'] / 1024 / 1024:.1f}MB",
        "memory_percent": f"{memory_info['percent']:.1f}%",
        "queue_size": len(task_queue),
        "timestamp": time.time()
    }, 200

@web_app.route('/stats')
def stats():
    total_files = sum(len(files) for files in user_files.values())
    total_size = sum(f["file_size"] for files in user_files.values() for f in files)
    return {
        "total_users": len(user_files),
        "total_files": total_files,
        "total_size": total_size,
        "formatted_size": progress_tracker.format_size(total_size)
    }, 200

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
        logger.info("👋 Bot stopped by user")
        save_user_data()
        zip_executor.shutdown(wait=False)
    except Exception as e:
        logger.error(f"Bot error: {e}")
        save_user_data()
