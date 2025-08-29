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
from pyrogram.errors import FloodWait, RPCError, SessionPasswordNeeded
from flask import Flask
import threading
from collections import deque
import random
import math
from typing import Dict, List, Callable, Any, Tuple, Optional
from pathlib import Path
import json
from datetime import datetime
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
import queue

# ===== تنظیمات پیشرفته =====
class Config:
    API_ID = 26180086
    API_HASH = "d91e174c7faf0e5a6a3a2ecb0b3361f6"
    SESSION_STRING = "BAGPefYAEAaXaj52wzDLPF0RSfWtF_Slk8nFWzYAHS9vu-HBxRUz9yLnq7m8z-ajYCQxQZO-5aNX0he9OttDjmjieYDMbDjBJtbsOT2ZwsQNe8UCAo5oFPveD5V1H0cIBMlXCG1P49G2oonf1YL1r16Nt34AJLkmzDIoFD0hhxwVBXvrUGwZmEoTtdkfORCYUMGACKO4-Al-NH35oVCkTIqmXQ5DUp9PVx6DND243VW5Xcqay7qwrwfoS4sWRA-7TMXykbHa37ZsdcCOf0VS8e6PyaYvG5BjMCd9BGRnR9IImrksYY2uBM2Bg42MLaa1WFxQtn97p5ViPF9c1MpY49bc5Gm5lwAAAAF--TK5AA"
    ALLOWED_USER_IDS = [417536686]
    MAX_FILE_SIZE = 2147483648  # 2GB
    MAX_TOTAL_SIZE = 4294967296  # 4GB
    PART_SIZE = 400 * 1024 * 1024  # 400MB
    CHUNK_SIZE = 512 * 1024  # 512KB
    MAX_CONCURRENT_DOWNLOADS = 1
    MAX_CONCURRENT_UPLOADS = 1
    RETRY_DELAY = 10
    PROGRESS_UPDATE_INTERVAL = 1.0
    DATA_FILE = "user_data.json"
    UPLOAD_CHUNK_SIZE = 2 * 1024 * 1024  # 2MB برای آپلود
    MAX_UPLOAD_RETRIES = 3
    ZIP_COMPRESSION_LEVEL = 3
    MAX_ZIP_RETRIES = 2
    ZIP_BASE_TIMEOUT = 1800
    ZIP_TIMEOUT_PER_GB = 900
    MEMORY_LIMIT = 450 * 1024 * 1024
    STREAMING_CHUNK_SIZE = 4 * 1024 * 1024
    UPLOAD_PART_SIZE = 100 * 1024 * 1024  # 100MB - اندازه هر قسمت برای آپلود

# ===== لاگ پیشرفته =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8")
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

# ===== کلاس مدیریت پیشرفت =====
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

    async def update(self, current: int, total: int):
        try:
            async with self.lock:
                now = time.time()
                
                # برای آپلود، همیشه آپدیت کنیم حتی اگر زمان نرسیده
                update_interval = Config.PROGRESS_UPDATE_INTERVAL
                if self.is_uploading:
                    update_interval = 0.3  # آپدیت سریع‌تر برای آپلود
                
                if now - self.last_update < update_interval and current != total:
                    return
                
                self.current = current
                self.total = total
                self.last_update = now
                
                percent = (current / total) * 100 if total > 0 else 0
                elapsed = now - self.start_time
                speed = current / elapsed if elapsed > 0 else 0
                eta = (total - current) / speed if speed > 0 and current > 0 else 0
                
                # برای آپلود، همیشه آپدیت کنیم
                if not self.is_uploading and abs(percent - self.last_percent) < 1.0 and current != total:
                    return
                
                self.last_percent = percent
                
                bar = self.get_progress_bar(percent)
                
                if self.total_files > 1:
                    progress_text = (
                        f"🚀 **{self.stage} فایل {self.file_index}/{self.total_files}**\n\n"
                        f"{bar}\n\n"
                        f"📁 فایل: `{self.file_name[:25]}{'...' if len(self.file_name) > 25 else ''}`\n"
                        f"📊 پیشرفت: `{self.format_size(current)} / {self.format_size(total)}`\n"
                        f"⚡ سرعت: `{self.format_size(speed)}/s`\n"
                        f"⏰ زمان باقیمانده: `{self.format_time(int(eta))}`"
                    )
                else:
                    progress_text = (
                        f"🚀 **{self.stage}**\n\n"
                        f"{bar}\n\n"
                        f"📁 فایل: `{self.file_name[:25]}{'...' if len(self.file_name) > 25 else ''}`\n"
                        f"📊 پیشرفت: `{self.format_size(current)} / {self.format_size(total)}`\n"
                        f"⚡ سرعت: `{self.format_size(speed)}/s`\n"
                        f"⏰ زمان باقیمانده: `{self.format_time(int(eta))}`"
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
        """بروزرسانی پیشرفت فشرده‌سازی"""
        try:
            while True:
                try:
                    current, total = self.zip_progress_queue.get_nowait()
                    await self.update(current, total)
                except queue.Empty:
                    await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Zip progress update error: {e}")

    async def update_upload_progress(self, current: int, total: int):
        """بروزرسانی پیشرفت آپلود"""
        try:
            await self.update(current, total)
        except Exception as e:
            logger.error(f"Upload progress update error: {e}")

    @staticmethod
    def get_progress_bar(percentage: float, length: int = 15) -> str:
        filled = int(length * percentage / 100)
        bar = "⬢" * filled + "⬡" * (length - filled)
        return f"{bar} {percentage:.1f}%"

    @staticmethod
    def format_size(size_bytes: int) -> str:
        if size_bytes == 0:
            return "0B"
        size_names = ["B", "KB", "MB", "GB", "TB"]
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_names[i]}"

    @staticmethod
    def format_time(seconds: int) -> str:
        if seconds < 60:
            return f"{seconds} ثانیه"
        elif seconds < 3600:
            return f"{seconds // 60} دقیقه و {seconds % 60} ثانیه"
        else:
            return f"{seconds // 3600} ساعت و {(seconds % 3600) // 60} دقیقه"

# ایجاد نمونه پیشرفت
progress_tracker = ProgressTracker()

# ===== فانکشن‌های کمکی پیشرفته =====
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
    max_retries = 2
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
            logger.warning(f"FloodWait: {wait_time} seconds (attempt {attempt + 1}/{max_retries})")
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(f"Error sending message (attempt {attempt + 1}): {e}")
            await asyncio.sleep(1)
    
    try:
        return await app.send_message(
            chat_id, 
            text, 
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Failed to send message even without parse_mode: {e}")
        return None

async def safe_download_media(message, file_path, file_name="", file_index=0, total_files=0, processing_msg=None):
    max_retries = 2
    for attempt in range(max_retries):
        try:
            async with download_semaphore:
                await asyncio.sleep(random.uniform(0.5, 1.5))
                
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
                    logger.warning(f"Downloaded file is empty or missing (attempt {attempt + 1})")
                    
        except FloodWait as e:
            wait_time = e.value + random.uniform(3, 7)
            logger.warning(f"Download FloodWait: {wait_time} seconds (attempt {attempt + 1})")
            await asyncio.sleep(wait_time)
        except (RPCError, aiohttp.ClientError, OSError) as e:
            logger.error(f"Download error (attempt {attempt + 1}): {e}")
            await asyncio.sleep(Config.RETRY_DELAY)
        except Exception as e:
            logger.error(f"Unexpected download error (attempt {attempt + 1}): {e}")
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
        
        await asyncio.sleep(2)

async def process_task_queue():
    global processing
    
    while True:
        if not task_queue:
            await asyncio.sleep(2)
            continue
        
        processing = True
        task_func, args, kwargs = task_queue.popleft()
        
        try:
            if asyncio.iscoroutinefunction(task_func):
                await task_func(*args, **kwargs)
            else:
                await asyncio.to_thread(task_func, *args, **kwargs)
            
            await asyncio.sleep(random.uniform(2.0, 4.0))
            
        except FloodWait as e:
            wait_time = e.value + random.uniform(5, 10)
            logger.warning(f"🕒 FloodWait detected: {wait_time} seconds. Rescheduling task...")
            
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
            save_user_data()

def add_to_queue(task_func: Callable, *args, **kwargs):
    task_queue.append((task_func, args, kwargs))
    logger.info(f"Task added to queue. Queue size: {len(task_queue)}")

async def notify_user_floodwait(user_id: int, wait_time: int):
    try:
        wait_minutes = wait_time // 60
        wait_seconds = wait_time % 60
        
        await safe_send_message(
            user_id,
            f"⏳ به دلیل محدودیت موقت تلگرام، عملیات متوقف شد.\n"
            f"🕒 زمان انتظار: {wait_minutes} دقیقه و {wait_seconds} ثانیه\n"
            f"✅ بعد از این زمان، عملیات به طور خودکار ادامه می‌یابد."
        )
    except Exception as e:
        logger.error(f"Error notifying user about floodwait: {e}")

def calculate_zip_timeout(total_size_mb: float) -> int:
    """محاسبه timeout بر اساس حجم فایل"""
    base_timeout = Config.ZIP_BASE_TIMEOUT
    additional_time = max(0, (total_size_mb - 1024) / 1024 * Config.ZIP_TIMEOUT_PER_GB)
    total_timeout = min(base_timeout + additional_time, 3 * 3600)
    logger.info(f"Calculated zip timeout: {total_timeout/60:.1f} minutes for {total_size_mb:.1f}MB")
    return int(total_timeout)

def zip_creation_task_streaming(zip_path: str, files: List[Dict], password: Optional[str], progress_queue: queue.Queue) -> bool:
    """تابع فشرده‌سازی با استفاده از روش جریانی برای مصرف حافظه کمتر"""
    try:
        total_size = sum(f['size'] for f in files)
        processed_size = 0
        
        logger.info(f"Starting streaming zip creation for {len(files)} files, total size: {total_size/1024/1024:.1f}MB")
        
        # بررسی وجود همه فایل‌ها
        for file_info in files:
            if not os.path.exists(file_info['path']):
                logger.error(f"File not found: {file_info['path']}")
                return False
            if os.path.getsize(file_info['path']) == 0:
                logger.error(f"File is empty: {file_info['path']}")
                return False
        
        # استفاده از حالت بدون فشرده‌سازی برای فایل‌های از قبل فشرده
        compression = pyzipper.ZIP_STORED if any(f['name'].lower().endswith(('.zip', '.rar', '.7z', '.tar', '.gz')) for f in files) else pyzipper.ZIP_DEFLATED
        
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
                    logger.info("Password set successfully")
                except Exception as e:
                    logger.error(f"Error setting password: {e}")
                    return False
            
            for file_info in files:
                file_path = file_info['path']
                arcname = os.path.basename(file_info['name'])
                
                if not os.path.exists(file_path):
                    logger.error(f"File disappeared during processing: {file_path}")
                    continue
                
                try:
                    # اضافه کردن فایل به زیپ به صورت جریانی
                    with open(file_path, 'rb') as f:
                        with zipf.open(arcname, 'w') as zf:
                            while True:
                                chunk = f.read(Config.STREAMING_CHUNK_SIZE)
                                if not chunk:
                                    break
                                zf.write(chunk)
                                processed_size += len(chunk)
                                progress_queue.put((processed_size, total_size))
                                logger.debug(f"Added chunk of {len(chunk)} bytes from {arcname}, progress: {processed_size}/{total_size}")
                    
                    logger.info(f"Successfully added {arcname} to zip")
                    
                except Exception as e:
                    logger.error(f"Error adding file {arcname} to zip: {e}")
                    continue
        
        # بررسی نهایی فایل زیپ
        if os.path.exists(zip_path) and os.path.getsize(zip_path) > 0:
            zip_size = os.path.getsize(zip_path)
            compression_ratio = (1 - (zip_size / total_size)) * 100 if total_size > 0 else 0
            logger.info(f"Zip created successfully: {zip_path}, "
                       f"size: {zip_size/1024/1024:.1f}MB, "
                       f"compression: {compression_ratio:.1f}%")
            
            # اعتبارسنجی ساده
            try:
                with pyzipper.AESZipFile(zip_path, 'r') as test_zip:
                    if password:
                        test_zip.setpassword(password.encode('utf-8'))
                    test_zip.testzip()
                    logger.info("Zip validation passed")
                    return True
            except Exception as test_error:
                logger.error(f"Zip validation failed: {test_error}")
                return False
        else:
            logger.error("Created zip file is empty or missing")
            return False
            
    except Exception as e:
        logger.error(f"Error in zip creation: {e}", exc_info=True)
        try:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        except:
            pass
        return False

async def create_zip_part_advanced(zip_path: str, files: List[Dict], default_password: Optional[str] = None) -> bool:
    """تابع پیشرفته فشرده سازی با مدیریت خطا و timeout"""
    max_retries = Config.MAX_ZIP_RETRIES
    
    for attempt in range(max_retries):
        try:
            os.makedirs(os.path.dirname(zip_path), exist_ok=True)
            
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                    logger.info(f"Removed existing zip file: {zip_path}")
                except Exception as e:
                    logger.error(f"Error removing existing zip: {e}")
            
            # بررسی وجود فایل‌ها قبل از شروع
            missing_files = []
            empty_files = []
            for file_info in files:
                if not os.path.exists(file_info['path']):
                    missing_files.append(file_info['name'])
                    logger.error(f"File not found: {file_info['path']}")
                elif os.path.getsize(file_info['path']) == 0:
                    empty_files.append(file_info['name'])
                    logger.error(f"File is empty: {file_info['path']}")
            
            if missing_files or empty_files:
                logger.error(f"Problematic files - Missing: {missing_files}, Empty: {empty_files}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(3)
                    continue
                else:
                    return False
            
            # محاسبه timeout بر اساس حجم
            total_size_mb = sum(f['size'] for f in files) / (1024 * 1024)
            dynamic_timeout = calculate_zip_timeout(total_size_mb)
            
            logger.info(f"Zip attempt {attempt + 1}/{max_retries} for {len(files)} files, "
                       f"total: {total_size_mb:.1f}MB, timeout: {dynamic_timeout/60:.1f}min")
            
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
                if os.path.exists(zip_path) and os.path.getsize(zip_path) > 0:
                    logger.info(f"Zip part created successfully: {zip_path}, "
                               f"size: {os.path.getsize(zip_path)/1024/1024:.1f}MB")
                    return True
                else:
                    logger.error("Zip file created but is empty or missing")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2)
                        continue
            
        except asyncio.TimeoutError:
            logger.error(f"Zip creation timeout (attempt {attempt + 1}/{max_retries})")
            try:
                if os.path.exists(zip_path):
                    os.remove(zip_path)
                    logger.info("Removed timeout zip file")
            except Exception as e:
                logger.error(f"Error removing timeout zip file: {e}")
            
            if attempt < max_retries - 1:
                retry_delay = random.uniform(5, 15)
                logger.info(f"Retrying after timeout in {retry_delay:.1f} seconds...")
                await asyncio.sleep(retry_delay)
                
        except Exception as e:
            logger.error(f"Unexpected error in zip creation (attempt {attempt + 1}/{max_retries}): {e}", exc_info=True)
            try:
                if os.path.exists(zip_path):
                    os.remove(zip_path)
            except:
                pass
            
            if attempt < max_retries - 1:
                retry_delay = random.uniform(2, 8)
                logger.info(f"Retrying after error in {retry_delay:.1f} seconds...")
                await asyncio.sleep(retry_delay)
    
    logger.error(f"All {max_retries} zip attempts failed")
    return False

async def upload_large_file_chunked(file_path: str, chat_id: int, caption: str, reply_to_message_id: int, 
                                  progress_callback, progress_args) -> bool:
    """آپلود فایل‌های بزرگ به صورت تکه تکه"""
    max_retries = Config.MAX_UPLOAD_RETRIES
    
    for attempt in range(max_retries):
        try:
            async with upload_semaphore:
                if attempt > 0:
                    wait_time = random.uniform(5, 15)
                    logger.info(f"Upload retry {attempt + 1}/{max_retries} after {wait_time:.1f} seconds")
                    await asyncio.sleep(wait_time)
                
                file_size = os.path.getsize(file_path)
                
                # اگر فایل کوچک است، معمولی آپلود کن
                if file_size <= Config.UPLOAD_PART_SIZE:
                    await app.send_document(
                        chat_id=chat_id,
                        document=file_path,
                        caption=caption,
                        reply_to_message_id=reply_to_message_id,
                        progress=progress_callback,
                        progress_args=progress_args
                    )
                else:
                    # فایل بزرگ است، باید تکه تکه آپلود شود
                    await upload_file_in_parts(file_path, chat_id, caption, reply_to_message_id, progress_callback, progress_args)
                
                logger.info(f"File uploaded successfully: {file_path}")
                return True
                
        except FloodWait as e:
            wait_time = e.value + random.uniform(3, 10)
            logger.warning(f"Upload FloodWait: {wait_time} seconds (attempt {attempt + 1}/{max_retries})")
            
            if attempt == max_retries - 1:
                logger.error(f"Max retries reached for upload: {file_path}")
                return False
                
            await asyncio.sleep(wait_time)
            
        except RPCError as e:
            logger.error(f"RPCError during upload (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt == max_retries - 1:
                return False
            await asyncio.sleep(Config.RETRY_DELAY)
            
        except OSError as e:
            logger.error(f"OSError during upload (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt == max_retries - 1:
                return False
            await asyncio.sleep(Config.RETRY_DELAY)
            
        except Exception as e:
            logger.error(f"Unexpected error during upload (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt == max_retries - 1:
                return False
            await asyncio.sleep(Config.RETRY_DELAY)
    
    return False

async def upload_file_in_parts(file_path: str, chat_id: int, caption: str, reply_to_message_id: int,
                             progress_callback, progress_args):
    """آپلود فایل به صورت تکه تکه"""
    file_size = os.path.getsize(file_path)
    part_size = Config.UPLOAD_PART_SIZE
    total_parts = (file_size + part_size - 1) // part_size
    
    logger.info(f"Uploading file in {total_parts} parts, part size: {part_size/1024/1024:.1f}MB")
    
    # ایجاد یک فایل موقت برای هر قسمت
    temp_parts = []
    
    try:
        with open(file_path, 'rb') as f:
            for part_num in range(total_parts):
                part_data = f.read(part_size)
                if not part_data:
                    break
                    
                # ایجاد فایل موقت برای این قسمت
                temp_part_path = f"{file_path}_part{part_num + 1}"
                with open(temp_part_path, 'wb') as part_file:
                    part_file.write(part_data)
                
                temp_parts.append(temp_part_path)
                
                # آپلود این قسمت
                part_caption = f"{caption} - قسمت {part_num + 1}/{total_parts}"
                
                await app.send_document(
                    chat_id=chat_id,
                    document=temp_part_path,
                    caption=part_caption,
                    reply_to_message_id=reply_to_message_id,
                    progress=progress_callback,
                    progress_args=progress_args
                )
                
                logger.info(f"Uploaded part {part_num + 1}/{total_parts}")
                
                # حذف فایل موقت این قسمت
                try:
                    os.remove(temp_part_path)
                    temp_parts.remove(temp_part_path)
                except:
                    pass
                
                await asyncio.sleep(1)  # تاخیر بین آپلود قسمتها
        
        logger.info(f"All {total_parts} parts uploaded successfully")
        
    except Exception as e:
        logger.error(f"Error in chunked upload: {e}")
        raise
    finally:
        # پاکسازی فایل‌های موقت در صورت وجود
        for temp_part in temp_parts:
            try:
                if os.path.exists(temp_part):
                    os.remove(temp_part)
            except:
                pass

async def upload_zip_part(zip_path: str, part_number: int, total_parts: int, 
                         chat_id: int, message_id: int, password: str, processing_msg: Message):
    try:
        if not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
            logger.error(f"Zip file not found or empty: {zip_path}")
            return False
            
        part_size = os.path.getsize(zip_path)
        
        progress_tracker.reset(processing_msg, "آپلود", f"پارت {part_number + 1}", part_number + 1, total_parts)
        
        caption = (
            f"📦 پارت {part_number + 1}/{total_parts}\n"
            f"🔑 رمز: `{password}`\n"
            f"💾 حجم: {progress_tracker.format_size(part_size)}"
        )
        
        success = await upload_large_file_chunked(
            file_path=zip_path,
            chat_id=chat_id,
            caption=caption,
            reply_to_message_id=message_id,
            progress_callback=progress_tracker.update_upload_progress,
            progress_args=()
        )
        
        if success:
            logger.info(f"Part {part_number + 1}/{total_parts} uploaded successfully")
            await asyncio.sleep(random.uniform(2.0, 5.0))
            return True
        else:
            logger.error(f"Failed to upload part {part_number + 1}/{total_parts}")
            return False
            
    except FloodWait as e:
        wait_time = e.value + random.uniform(5, 15)
        logger.warning(f"Upload FloodWait in main function: {wait_time} seconds")
        
        schedule_task(
            upload_zip_part, 
            wait_time, 
            zip_path, part_number, total_parts, 
            chat_id, message_id, password, processing_msg
        )
        
        try:
            await processing_msg.edit_text(
                f"⏳ **آپلود متوقف شد**\n\n"
                f"📦 پارت: {part_number + 1}/{total_parts}\n"
                f"🕒 ادامه بعد از: {wait_time:.0f} ثانیه\n"
                f"✅ به طور خودکار ادامه خواهد یافت",
                parse_mode=enums.ParseMode.MARKDOWN
            )
        except:
            pass
            
        return False
        
    except Exception as e:
        logger.error(f"Error uploading part {part_number}: {e}")
        return False

async def cleanup_files(file_paths: List[str]):
    """پاکسازی فایل‌های موقت"""
    for file_path in file_paths:
        try:
            if os.path.exists(file_path):
                if os.path.isdir(file_path):
                    shutil.rmtree(file_path)
                else:
                    os.remove(file_path)
                logger.info(f"Cleaned up file: {file_path}")
        except Exception as e:
            logger.error(f"Error cleaning up file {file_path}: {e}")

# ===== هندلرها =====
async def start(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    
    welcome_text = (
        "👋 **سلام! به ربات زیپ و آپلود پیشرفته خوش آمدید**\n\n"
        "✨ **قابلیت‌های ربات:**\n"
        "• 🔒 زیپ کردن فایل‌ها با رمزگذاری AES-256\n"
        "• 📦 تقسیم به پارت‌های 400 مگابایتی\n"
        "• ⚡ آپلود تکه تکه فایل‌های بزرگ\n"
        "• 🛡️ مدیریت محدودیت‌های تلگرام\n"
        "• 📊 نمایش پیشرفت حرفه‌ای\n\n"
        "📝 **روش استفاده:**\n"
        "1. فایل‌ها را ارسال کنید\n"
        "2. از کپشن `pass=رمز` برای رمز جداگانه هر فایل استفاده کنید\n"
        "3. دستور /zip را برای شروع فرآیند وارد کنید\n\n"
        f"⚙️ **محدودیت‌ها:**\n"
        f"• حداکثر حجم هر فایل: {progress_tracker.format_size(Config.MAX_FILE_SIZE)}\n"
        f"• حداکثر حجم کل: {progress_tracker.format_size(Config.MAX_TOTAL_SIZE)}\n\n"
        "🛠 برای لغو عملیات از /cancel استفاده کنید"
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 شروع ارسال فایل", callback_data="start_upload")],
        [InlineKeyboardButton("ℹ️ راهنمای کامل", callback_data="help")]
    ])
    
    await safe_send_message(
        message.chat.id,
        welcome_text,
        reply_to_message_id=message.id,
        reply_markup=keyboard,
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
    caption = message.caption or ""
    password = None
    
    if "pass=" in caption:
        password_match = caption.split("pass=", 1)[1].split()[0].strip()
        if password_match:
            password = password_match
    
    if file_size > Config.MAX_FILE_SIZE:
        await safe_send_message(
            message.chat.id,
            f"❌ **حجم فایل بیش از حد مجاز است!**\n\n"
            f"📦 حجم فایل: {progress_tracker.format_size(file_size)}\n"
            f"⚖️ حد مجاز: {progress_tracker.format_size(Config.MAX_FILE_SIZE)}",
            reply_to_message_id=message.id
        )
        return
    
    user_id = message.from_user.id
    if user_id not in user_files:
        user_files[user_id] = []
    
    existing_files = [f['file_name'] for f in user_files[user_id]]
    if file_name in existing_files:
        base, ext = os.path.splitext(file_name)
        file_name = f"{base}_{message.id}{ext}"
    
    user_files[user_id].append({
        "message_id": message.id,
        "file_name": file_name, 
        "password": password, 
        "file_size": file_size,
        "file_type": file_type,
        "added_time": time.time()
    })
    
    total_size = sum(f["file_size"] for f in user_files[user_id])
    file_count = len(user_files[user_id])
    
    await safe_send_message(
        message.chat.id,
        f"✅ **فایل ذخیره شد**\n\n"
        f"📝 نام: `{file_name}`\n"
        f"📦 حجم: `{progress_tracker.format_size(file_size)}`\n"
        f"🔑 رمز: `{password if password else '❌ ندارد'}`\n\n"
        f"📊 وضعیت فعلی: `{file_count}` فایل (`{progress_tracker.format_size(total_size)}`)\n\n"
        f"📌 برای شروع زیپ از `/zip` استفاده کنید",
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
            "❌ **هیچ فایلی برای زیپ کردن وجود ندارد**\n\n"
            "📝 لطفاً ابتدا فایل‌ها را ارسال کنید",
            reply_to_message_id=message.id
        )
        return
    
    total_size = sum(f["file_size"] for f in user_files[user_id])
    if total_size > Config.MAX_TOTAL_SIZE:
        await safe_send_message(
            message.chat.id,
            f"❌ **حجم کل فایل‌ها بیش از حد مجاز است!**\n\n"
            f"📦 حجم کل: {progress_tracker.format_size(total_size)}\n"
            f"⚖️ حد مجاز: {progress_tracker.format_size(Config.MAX_TOTAL_SIZE)}\n\n"
            f"📌 لطفاً تعداد فایل‌ها را کاهش دهید",
            reply_to_message_id=message.id
        )
        user_files[user_id] = []
        save_user_data()
        return
    
    user_states[user_id] = "waiting_password"
    
    await safe_send_message(
        message.chat.id,
        "🔐 **لطفاً رمز عبور برای فایل زیپ وارد کنید:**\n\n"
        "📝 پس از وارد کردن رمز، از /done استفاده کنید\n"
        "⚠️ توجه: رمز عبور باید حداقل 4 کاراکتر باشد",
        reply_to_message_id=message.id
    )

async def start_zip_now(client, message: Message):
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    if user_states.get(user_id) != "ready_to_zip":
        await message.reply("❌ ابتدا باید مراحل قبلی را کامل کنید")
        return
    
    zip_name = user_states.get(f"{user_id}_zipname", f"archive_{int(time.time())}")
    
    add_to_queue(process_zip_files, user_id, zip_name, message.chat.id, message.id)
    
    await message.reply("✅ **درخواست زیپ به صف اضافه شد.**\n\n⏳ عملیات به زودی شروع می‌شود...")

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
        "❌ **عملیات لغو شد**\n\n"
        "✅ همه فایل‌های ذخیره شده پاک شدند\n"
        "📌 می‌توانید دوباره فایل‌ها را ارسال کنید",
        reply_to_message_id=message.id
    )
# ===== تغییرات در تابع process_zip_files =====
async def process_zip_files(user_id, zip_name, chat_id, message_id):
    processing_msg = None
    temp_downloaded_files = []
    
    try:
        processing_msg = await app.send_message(chat_id, "⏳ **در حال آماده‌سازی...**\n\n🌀 لطفاً منتظر بمانید", parse_mode=enums.ParseMode.MARKDOWN)
        zip_password = user_states.get(f"{user_id}_password")
        
        zip_progress_task = asyncio.create_task(progress_tracker.update_zip_progress())
        
        total_files = len(user_files[user_id])
        file_info_list = []
        
        for i, finfo in enumerate(user_files[user_id], 1):
            file_msg_id = finfo["message_id"]
            
            try:
                file_msg = await app.get_messages(chat_id, file_msg_id)
                if not file_msg:
                    logger.error(f"Message {file_msg_id} not found")
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
                        'size': file_size,
                        'password': finfo["password"] or zip_password
                    })
                    logger.info(f"Downloaded {file_name} ({progress_tracker.format_size(file_size)})")
                else:
                    logger.error(f"Failed to download {file_name}")
                    try:
                        if os.path.exists(file_path):
                            os.remove(file_path)
                    except:
                        pass
                
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Error processing file {finfo['file_name']}: {e}")
                continue
        
        if not file_info_list:
            await processing_msg.edit_text("❌ **هیچ فایلی با موفقیت دانلود نشد**\n\nلطفاً دوباره تلاش کنید")
            return
        
        await processing_msg.edit_text("📦 **در حال فشرده‌سازی همه فایل‌ها...**\n\n⏳ لطفاً منتظر بمانید", parse_mode=enums.ParseMode.MARKDOWN)
        
        # ایجاد یک فایل ZIP واحد برای همه فایل‌ها
        final_zip_name = f"{zip_name}.zip"
        zip_path = os.path.join(tempfile.gettempdir(), f"zip_bot_{user_id}_{final_zip_name}")
        
        total_size = sum(f['size'] for f in file_info_list)
        progress_tracker.reset(processing_msg, "فشرده‌سازی", final_zip_name, 1, 1)
        progress_tracker.total = total_size
        
        success = await create_zip_part_advanced(zip_path, file_info_list, zip_password)
        if not success:
            logger.error(f"Failed to create zip file")
            try:
                if os.path.exists(zip_path):
                    os.remove(zip_path)
            except:
                pass
            
            await processing_msg.edit_text("❌ **خطا در ایجاد فایل زیپ**\n\nلطفاً دوباره تلاش کنید")
            return
        
        # قبل از آپلود، مطمئن شویم فایل زیپ وجود دارد و خالی نیست
        if not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
            logger.error(f"Zip file is missing or empty: {zip_path}")
            await processing_msg.edit_text("❌ **فایل زیپ ایجاد شده خالی است**\n\nلطفاً دوباره تلاش کنید")
            return
        
        # آپلود فایل زیپ
        zip_size = os.path.getsize(zip_path)
        await processing_msg.edit_text(
            f"📤 **در حال آپلود فایل زیپ...**\n\n"
            f"📝 نام: `{final_zip_name}`\n"
            f"💾 حجم: `{progress_tracker.format_size(zip_size)}`\n"
            f"🔑 رمز: `{zip_password or 'بدون رمز'}`\n"
            f"⏳ لطفاً منتظر بمانید...",
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
            progress_args=()
        )
        
        # حذف فایل زیپ فقط بعد از آپلود موفق
        if upload_success:
            try:
                if os.path.exists(zip_path):
                    os.remove(zip_path)
                    logger.info(f"Cleaned up zip file: {zip_path}")
            except Exception as e:
                logger.error(f"Error cleaning up zip file {zip_path}: {e}")
        
        # حذف فایل‌های دانلود شده فقط بعد از آپلود موفق
        if upload_success:
            await cleanup_files(temp_downloaded_files)
        else:
            logger.warning("Upload failed, keeping downloaded files for retry")
        
        if upload_success:
            result_text = (
                f"✅ **عملیات با موفقیت تکمیل شد!**\n\n"
                f"📦 فایل زیپ ایجاد شده: `{final_zip_name}`\n"
                f"🔑 رمز: `{zip_password or 'بدون رمز'}`\n"
                f"📊 تعداد فایل‌ها: `{len(file_info_list)}`\n"
                f"💾 حجم نهایی: `{progress_tracker.format_size(zip_size)}`\n\n"
                f"📌 فایل‌ها به طور خودکار حذف شدند"
            )
        else:
            result_text = (
                "❌ **خطا در آپلود فایل زیپ**\n\n"
                "📌 ممکن است حجم فایل بسیار زیاد باشد\n"
                "🔄 لطفاً دوباره تلاش کنید"
            )
        
        await safe_send_message(
            chat_id,
            result_text,
            reply_to_message_id=message_id,
            parse_mode=enums.ParseMode.MARKDOWN
        )
        
    except FloodWait as e:
        logger.warning(f"⏰ FloodWait در پردازش زیپ: {e.value} ثانیه")
        
        if processing_msg:
            await processing_msg.edit_text(
                f"⏳ **عملیات متوقف شد**\n\n"
                f"🕒 ادامه بعد از: {e.value} ثانیه\n"
                f"✅ به طور خودکار ادامه خواهد یافت",
                parse_mode=enums.ParseMode.MARKDOWN
            )
        
        schedule_task(process_zip_files, e.value + 10, user_id, zip_name, chat_id, message_id)
        
    except Exception as e:
        logger.error(f"خطا در پردازش زیپ: {e}", exc_info=True)
        if processing_msg:
            await processing_msg.edit_text(
                "❌ **خطایی در پردازش رخ داد**\n\n"
                "📌 لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید",
                parse_mode=enums.ParseMode.MARKDOWN
            )
    finally:
        if 'zip_progress_task' in locals():
            zip_progress_task.cancel()
        
        # فقط در صورت موفقیت کامل فایل‌ها را حذف کن
        if user_id in user_files:
            user_files[user_id] = []
        user_states.pop(user_id, None)
        user_states.pop(f"{user_id}_password", None)
        user_states.pop(f"{user_id}_zipname", None)
        user_states.pop(f"{user_id}_upload_size", None)
        save_user_data()

# ===== حذف بخش‌های مربوط به انتخاب سایز آپلود =====
# در تابع start_zip_now و handle_callback_query بخش‌های مربوط به انتخاب سایز آپلود را حذف یا تغییر دهید

async def start_zip_now(client, message: Message):
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    if user_states.get(user_id) != "ready_to_zip":
        await message.reply("❌ ابتدا باید مراحل قبلی را کامل کنید")
        return
    
    zip_name = user_states.get(f"{user_id}_zipname", f"archive_{int(time.time())}")
    
    # مستقیماً شروع به پردازش زیپ می‌کنیم (بدون درخواست سایز آپلود)
    await message.reply(
        f"📦 **عملیات زیپ و آپلود شروع می‌شود...**\n\n"
        f"📝 نام فایل: `{zip_name}.zip`\n"
        f"⏳ لطفاً منتظر بمانید",
        parse_mode=enums.ParseMode.MARKDOWN
    )
    
    # اضافه کردن به صف پردازش
    add_to_queue(process_zip_files, user_id, zip_name, message.chat.id, message.id)

# در تابع handle_callback_query، بخش‌های مربوط به "size_" و "confirm_zip" را حذف یا تغییر دهید
async def handle_callback_query(client, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    if not is_user_allowed(user_id):
        await callback_query.answer("دسترسی denied!", show_alert=True)
        return
    
    if data == "start_upload":
        await callback_query.answer()
        await safe_send_message(
            user_id,
            "📤 **حالت ارسال فایل فعال شد**\n\n"
            "📝 می‌توانید فایل‌ها را ارسال کنید\n"
            "🔑 برای رمزگذاری از کپشن `pass=رمز` استفاده کنید\n"
            "📌 پس از اتمام از /zip استفاده کنید",
            parse_mode=enums.ParseMode.MARKDOWN
        )
    
    elif data == "help":
        await callback_query.answer()
        await safe_send_message(
            user_id,
            "📖 **راهنمای کامل ربات**\n\n"
            "1. ارسال فایل‌ها: فایل‌های خود را به ربات ارسال کنید\n"
            "2. رمزگذاری: در کپشن از `pass=رمز` استفاده کنید\n"
            "3. شروع زیپ: پس از ارسال همه فایل‌ها، /zip را بزنید\n"
            "4. تنظیمات: رمز کلی و نام فایل را وارد کنید\n"
            "5. دریافت: ربات فایل‌ها را زیپ و آپلود می‌کند\n\n"
            "⚙️ **ویژگی‌های پیشرفته:**\n"
            "• فشرده‌سازی یکجا همه فایل‌ها\n"
            "• آپلود تکه تکه فایل‌های بزرگ\n"
            "• رمزگذاری AES-256\n"
            "• بازیابی از خطا\n\n"
            "🛠 پشتیبانی: در صورت مشکل با /cancel شروع کنید",
            parse_mode=enums.ParseMode.MARKDOWN
        )
    
    elif data == "no_password":
        await callback_query.answer("حالت بدون رمز انتخاب شد")
        user_states[user_id] = "waiting_filename"
        user_states[f"{user_id}_password"] = None
        
        suggested_name = f"archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        await safe_send_message(
            user_id,
            f"📝 **حالا نام فایل زیپ نهایی را وارد کنید**\n\n"
            f"💡 پیشنهاد: {suggested_name}\n"
            f"⚠️ توجه: پسوند .zip اضافه خواهد شد"
        )
    
    elif data == "cancel_zip":
        await callback_query.answer("عملیات لغو شد")
        await cancel_zip(client, callback_query.message)
    
    await callback_query.message.delete()

def non_command_filter(_, __, message: Message):
    user_id = message.from_user.id
    return (message.text and 
            not message.text.startswith('/') and 
            user_id in user_states and
            user_states.get(user_id) in ["waiting_password", "waiting_filename"])

non_command = filters.create(non_command_filter)

async def process_zip_files(user_id, zip_name, chat_id, message_id):
    processing_msg = None
    temp_downloaded_files = []
    
    try:
        processing_msg = await app.send_message(chat_id, "⏳ **در حال آماده‌سازی...**\n\n🌀 لطفاً منتظر بمانید", parse_mode=enums.ParseMode.MARKDOWN)
        zip_password = user_states.get(f"{user_id}_password")
        
        zip_progress_task = asyncio.create_task(progress_tracker.update_zip_progress())
        
        total_files = len(user_files[user_id])
        file_info_list = []
        
        for i, finfo in enumerate(user_files[user_id], 1):
            file_msg_id = finfo["message_id"]
            
            try:
                file_msg = await app.get_messages(chat_id, file_msg_id)
                if not file_msg:
                    logger.error(f"Message {file_msg_id} not found")
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
                        'size': file_size,
                        'password': finfo["password"] or zip_password
                    })
                    logger.info(f"Downloaded {file_name} ({progress_tracker.format_size(file_size)})")
                else:
                    logger.error(f"Failed to download {file_name}")
                    try:
                        if os.path.exists(file_path):
                            os.remove(file_path)
                    except:
                        pass
                
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Error processing file {finfo['file_name']}: {e}")
                continue
        
        if not file_info_list:
            await processing_msg.edit_text("❌ **هیچ فایلی با موفقیت دانلود نشد**\n\nلطفاً دوباره تلاش کنید")
            return
        
        await processing_msg.edit_text("📦 **در حال ایجاد پارت‌های 400 مگابایتی...**\n\n⏳ لطفاً منتظر بمانید", parse_mode=enums.ParseMode.MARKDOWN)
        
        file_info_list.sort(key=lambda x: x['size'], reverse=True)
        
        parts = []
        current_part = []
        current_size = 0
        
        for file_info in file_info_list:
            file_size = file_info['size']
            
            if file_size > 350 * 1024 * 1024:
                if current_part:
                    parts.append(current_part)
                    current_part = []
                    current_size = 0
                parts.append([file_info])
                logger.info(f"Large file in separate part: {file_info['name']} ({file_size/1024/1024:.1f}MB)")
            else:
                if current_size + file_size > Config.PART_SIZE:
                    if current_part:
                        parts.append(current_part)
                        current_part = []
                        current_size = 0
                
                current_part.append(file_info)
                current_size += file_size
        
        if current_part:
            parts.append(current_part)
        
        num_parts = len(parts)
        logger.info(f"Created {num_parts} parts from {len(file_info_list)} files")
        
        if num_parts == 0:
            await processing_msg.edit_text("❌ **هیچ پارتی ایجاد نشد**\n\nلطفاً دوباره تلاش کنید")
            return
        
        successful_parts = 0
        
        for part_index, part_files in enumerate(parts):
            part_number = part_index + 1
            part_zip_name = f"{zip_name}_part{part_number}.zip"
            zip_path = os.path.join(tempfile.gettempdir(), f"zip_bot_{user_id}_{part_zip_name}")
            
            part_password = zip_password
            part_size_mb = sum(f['size'] for f in part_files) / (1024 * 1024)
            
            logger.info(f"Processing part {part_number}/{num_parts}, "
                       f"files: {len(part_files)}, size: {part_size_mb:.1f}MB")
            
            await processing_msg.edit_text(
                f"🗜️ **در حال فشرده‌سازی پارت {part_number}/{num_parts}**\n\n"
                f"📝 شامل {len(part_files)} فایل\n"
                f"💾 حجم: {part_size_mb:.1f}MB\n"
                f"⏳ لطفاً منتظر بمانید...",
                parse_mode=enums.ParseMode.MARKDOWN
            )
            
            total_part_size = sum(f['size'] for f in part_files)
            progress_tracker.reset(processing_msg, "فشرده‌سازی", f"پارت {part_number}", part_number, num_parts)
            progress_tracker.total = total_part_size
            
            success = await create_zip_part_advanced(zip_path, part_files, part_password)
            if not success:
                logger.error(f"Failed to create zip part {part_number}")
                try:
                    if os.path.exists(zip_path):
                        os.remove(zip_path)
                except:
                    pass
                continue
            
            # قبل از آپلود، مطمئن شویم فایل زیپ وجود دارد و خالی نیست
            if not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
                logger.error(f"Zip file is missing or empty: {zip_path}")
                continue
            
            upload_success = await upload_zip_part(
                zip_path, 
                part_index, 
                num_parts, 
                chat_id, 
                message_id, 
                part_password or "بدون رمز",
                processing_msg
            )
            
            try:
                if os.path.exists(zip_path):
                    os.remove(zip_path)
                    logger.info(f"Cleaned up zip part: {zip_path}")
            except Exception as e:
                logger.error(f"Error cleaning up zip part {zip_path}: {e}")
            
            if upload_success:
                successful_parts += 1
                logger.info(f"Part {part_number} processed successfully")
            else:
                logger.error(f"Failed to upload part {part_number}")
            
            await asyncio.sleep(1)
        
        await cleanup_files(temp_downloaded_files)
        
        if successful_parts > 0:
            result_text = (
                f"✅ **عملیات با موفقیت تکمیل شد!**\n\n"
                f"📦 پارت‌های ایجاد شده: `{successful_parts}/{num_parts}`\n"
                f"🔑 رمز اصلی: `{zip_password or 'بدون رمز'}`\n\n"
                f"📌 **نکات مهم:**\n"
                f"• برای extract همه پارت‌ها را دانلود کنید\n"
                f"• از رمز یکسان برای همه پارت‌ها استفاده کنید\n"
                f"• فایل‌ها به طور خودکار حذف شدند"
            )
        else:
            result_text = (
                "❌ **خطا در ایجاد پارت‌ها**\n\n"
                "📌 ممکن است فایل‌ها خراب شده باشند یا حجم بسیار زیاد باشد\n"
                "🔄 لطفاً دوباره فایل‌ها را ارسال کنید و تلاش کنید"
            )
        
        await safe_send_message(
            chat_id,
            result_text,
            reply_to_message_id=message_id,
            parse_mode=enums.ParseMode.MARKDOWN
        )
        
    except FloodWait as e:
        logger.warning(f"⏰ FloodWait در پردازش زیپ: {e.value} ثانیه")
        
        if processing_msg:
            await processing_msg.edit_text(
                f"⏳ **عملیات متوقف شد**\n\n"
                f"🕒 ادامه بعد از: {e.value} ثانیه\n"
                f"✅ به طور خودکار ادامه خواهد یافت",
                parse_mode=enums.ParseMode.MARKDOWN
            )
        
        schedule_task(process_zip_files, e.value + 10, user_id, zip_name, chat_id, message_id)
        
    except Exception as e:
        logger.error(f"خطا در پردازش زیپ: {e}", exc_info=True)
        if processing_msg:
            await processing_msg.edit_text(
                "❌ **خطایی در پردازش رخ داد**\n\n"
                "📌 لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید",
                parse_mode=enums.ParseMode.MARKDOWN
            )
    finally:
        if 'zip_progress_task' in locals():
            zip_progress_task.cancel()
        
        await cleanup_files(temp_downloaded_files)
        
        if user_id in user_files:
            user_files[user_id] = []
        user_states.pop(user_id, None)
        user_states.pop(f"{user_id}_password", None)
        user_states.pop(f"{user_id}_zipname", None)
        save_user_data()

async def run_bot():
    global app
    logger.info("🚀 Starting advanced zip/upload bot...")
    
    load_user_data()
    
    app = Client(
        "user_bot",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        session_string=Config.SESSION_STRING,
        in_memory=True
    )
    
    app.on_message(filters.command("start"))(start)
    app.on_message(filters.document | filters.video | filters.audio)(handle_file)
    app.on_message(filters.command("zip"))(start_zip)
    app.on_message(filters.command("zipnow"))(start_zip_now)
    app.on_message(filters.command("done"))(handle_done_command)
    app.on_message(filters.command("cancel"))(cancel_zip)
    app.on_message(filters.text & non_command)(process_zip)
    app.on_callback_query()(handle_callback_query)
    
    asyncio.create_task(process_scheduled_tasks())
    asyncio.create_task(process_task_queue())
    
    await app.start()
    logger.info("✅ Bot started successfully with advanced features!")
    
    async def periodic_save():
        while True:
            await asyncio.sleep(300)
            save_user_data()
            logger.info("💾 User data saved periodically")
    
    asyncio.create_task(periodic_save())
    
    await asyncio.Event().wait()

if __name__ == "__main__":
    web_app = Flask(__name__)
    
    @web_app.route('/')
    def home():
        return "🤖 Advanced Zip/Upload Bot is Running", 200
    
    @web_app.route('/health')
    def health_check():
        return {
            "status": "healthy",
            "queue_size": len(task_queue),
            "scheduled_tasks": len(scheduled_tasks),
            "users_with_files": len(user_files),
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
    
    def start_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_bot())
        except Exception as e:
            logger.error(f"Bot error: {e}")
        finally:
            loop.close()
            zip_executor.shutdown(wait=True)
    
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()
    
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"🌐 Starting Flask web server on port {port}...")
    
    def run_web_app():
        web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    
    web_thread = threading.Thread(target=run_web_app, daemon=True)
    web_thread.start()
    
    try:
        bot_thread.join()
        web_thread.join()
    except KeyboardInterrupt:
        logger.info("👋 Bot stopped by user")
        save_user_data()
        zip_executor.shutdown(wait=False)
        sys.exit(0)
