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
import contextlib

# ===== تنظیمات پیشرفته =====
class Config:
    API_ID = 26180086
    API_HASH = "d91e174c7faf0e5a6a3a2ecb0b3361f6"
    SESSION_STRING = "BAGPefYAEAaXaj52wzDLPF0RSfWtF_Slk8nFWzYAHS9vu-HBxRUz9yLnq7m8z-ajYCQxQZO-5aNX0he9OttDjmjieYDMbDjBJtbsOT2ZwsQNe8UCAo5oFPveD5V1H0cIBMlXCG1P49G2oonf1YL1r16Nt34AJLkmzDIoFD0hhxwVBXvrUGwZmEoTtdkfORCYUMGACKO4-Al-NH35oVCkTIqmXQ5DUp9PVx6DND243VW5Xcqay7qwrwfoS4sWRA-7TMXykbHa37ZsdcCOf0VS8e6PyaYvG5BjMCd9BGRnR9IImrksYY2uBM2Bg42MLaa1WFxQtn97p5ViPF9c1MpY49bc5Gm5lwAAAAF--TK5AA"
    ALLOWED_USER_IDS = [417536686]
    MAX_FILE_SIZE = 2147483648  # 2GB
    MAX_TOTAL_SIZE = 8589934592  # 8GB
    PART_SIZE = 1900 * 1024 * 1024  # 1900MB
    CHUNK_SIZE = 1 * 1024 * 1024  # 1MB برای مدیریت بهتر حافظه
    MAX_CONCURRENT_DOWNLOADS = 2
    MAX_CONCURRENT_UPLOADS = 1
    RETRY_DELAY = 10
    PROGRESS_UPDATE_INTERVAL = 0.5
    DATA_FILE = "user_data.json"
    UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024  # 4MB برای آپلود فایل‌های بزرگ
    MAX_UPLOAD_RETRIES = 5
    ZIP_COMPRESSION_LEVEL = 6  # سطح فشرده سازی (1-9)
    MAX_ZIP_RETRIES = 3  # حداکثر تلاش برای فشرده سازی
    ZIP_BASE_TIMEOUT = 3600  # 1 hour base timeout
    ZIP_TIMEOUT_PER_GB = 1800  # 30 minutes per additional GB
    ZIP_MEMORY_LIMIT = 200 * 1024 * 1024  # 200MB memory limit for zip operations

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
zip_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ZipWorker")

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
    max_retries = 3
    for attempt in range(max_retries):
        try:
            await asyncio.sleep(random.uniform(1.0, 3.0))
            return await app.send_message(
                chat_id,
                text,
                reply_to_message_id=reply_to_message_id,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        except FloodWait as e:
            wait_time = e.value + random.uniform(2, 5)
            logger.warning(f"FloodWait: {wait_time} seconds (attempt {attempt + 1}/{max_retries})")
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(f"Error sending message (attempt {attempt + 1}): {e}")
            await asyncio.sleep(2)

async def safe_download_media(message, file_path, file_name="", file_index=0, total_files=0, processing_msg=None):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with download_semaphore:
                await asyncio.sleep(random.uniform(1.0, 3.0))
                
                # ایجاد دایرکتوری اگر وجود ندارد
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                
                # دانلود فایل
                await message.download(
                    file_name=file_path,
                    progress=download_progress_callback,
                    progress_args=(file_name, file_index, total_files, processing_msg)
                )
                return True
                
        except FloodWait as e:
            wait_time = e.value + random.uniform(2, 5)
            logger.warning(f"FloodWait during download: {wait_time} seconds")
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(f"Error downloading file (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
            else:
                return False
    return False

async def download_progress_callback(current, total, file_name, file_index, total_files, processing_msg):
    try:
        percent = (current / total) * 100
        progress_text = (
            f"📥 **در حال دانلود**\n"
            f"📁 فایل: {file_name}\n"
            f"📊 پیشرفت: {percent:.1f}%\n"
            f"🔢 فایل {file_index + 1} از {total_files}"
        )
        
        # فقط هر 0.5 ثانیه آپدیت کنیم
        if time.time() - progress_tracker.last_update > Config.PROGRESS_UPDATE_INTERVAL:
            if processing_msg:
                await processing_msg.edit_text(progress_text)
            progress_tracker.last_update = time.time()
            
    except Exception as e:
        logger.error(f"Error in download progress callback: {e}")

def schedule_task(task_func: Callable, delay: float, *args, **kwargs):
    execution_time = time.time() + delay
    scheduled_tasks.append((execution_time, task_func, args, kwargs))
    scheduled_tasks.sort(key=lambda x: x[0])

async def process_scheduled_tasks():
    while True:
        now = time.time()
        tasks_to_run = []
        
        # پیدا کردن تسک‌هایی که باید اجرا شوند
        for task in scheduled_tasks[:]:
            if task[0] <= now:
                tasks_to_run.append(task)
                scheduled_tasks.remove(task)
        
        # اجرای تسک‌ها
        for execution_time, task_func, args, kwargs in tasks_to_run:
            try:
                if asyncio.iscoroutinefunction(task_func):
                    await task_func(*args, **kwargs)
                else:
                    task_func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Error executing scheduled task: {e}")
        
        await asyncio.sleep(1)

async def process_task_queue():
    global processing
    while True:
        if task_queue:
            task_func, args, kwargs = task_queue.popleft()
            try:
                if asyncio.iscoroutinefunction(task_func):
                    await task_func(*args, **kwargs)
                else:
                    task_func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Error processing task: {e}")
        else:
            await asyncio.sleep(0.1)

def add_to_queue(task_func: Callable, *args, **kwargs):
    task_queue.append((task_func, args, kwargs))
    logger.info(f"Task added to queue. Queue size: {len(task_queue)}")

async def notify_user_floodwait(user_id: int, wait_time: int):
    try:
        wait_minutes = wait_time // 60
        wait_seconds = wait_time % 60
        message = (
            f"⏳ **لطفا منتظر بمانید**\n"
            f"⏰ زمان انتظار: {wait_minutes} دقیقه و {wait_seconds} ثانیه\n"
            f"🔁 پس از این مدت، عملیات ادامه خواهد یافت"
        )
        await safe_send_message(user_id, message)
    except Exception as e:
        logger.error(f"Error notifying user about floodwait: {e}")

def calculate_zip_timeout(total_size_mb: float) -> int:
    """محاسبه timeout بر اساس حجم فایل"""
    base_timeout = Config.ZIP_BASE_TIMEOUT
    additional_time = max(0, (total_size_mb - 1024) / 1024 * Config.ZIP_TIMEOUT_PER_GB)
    total_timeout = min(base_timeout + additional_time, 6 * 3600)  # حداکثر 6 ساعت
    logger.info(f"Calculated zip timeout: {total_timeout/60:.1f} minutes for {total_size_mb:.1f}MB")
    return int(total_timeout)

def zip_creation_task(zip_path: str, files: List[Dict], password: Optional[str], progress_queue: queue.Queue) -> bool:
    """تابع فشرده‌سازی با ردیابی پیشرفت"""
    try:
        total_size = sum(f['size'] for f in files)
        processed_size = 0
        
        with pyzipper.AESZipFile(
            zip_path,
            'w',
            compression=pyzipper.ZIP_DEFLATED,
            compresslevel=Config.ZIP_COMPRESSION_LEVEL
        ) as zipf:
            if password:
                zipf.setpassword(password.encode())
                zipf.setencryption(pyzipper.WZ_AES, nbits=128)
            
            for file_info in files:
                file_path = file_info['file_path']
                file_name = file_info['file_name']
                
                try:
                    # اضافه کردن فایل به زیپ
                    zipf.write(file_path, file_name)
                    
                    # آپدیت پیشرفت
                    processed_size += file_info['size']
                    progress_percent = (processed_size / total_size) * 100
                    progress_queue.put({
                        'stage': 'compressing',
                        'percent': progress_percent,
                        'current_file': file_name,
                        'processed_size': processed_size,
                        'total_size': total_size
                    })
                    
                except Exception as e:
                    logger.error(f"Error adding file {file_path} to zip: {e}")
                    continue
        
        return True
        
    except Exception as e:
        logger.error(f"Error in zip creation task: {e}")
        return False

async def create_zip_part_advanced(zip_path: str, files: List[Dict], default_password: Optional[str] = None) -> bool:
    """تابع پیشرفته فشرده سازی با مدیریت خطا و timeout"""
    max_retries = Config.MAX_ZIP_RETRIES
    
    for attempt in range(max_retries):
        try:
            # محاسبه timeout
            total_size_mb = sum(f['size'] for f in files) / (1024 * 1024)
            timeout = calculate_zip_timeout(total_size_mb)
            
            # اجرای فشرده سازی در thread جداگانه
            loop = asyncio.get_event_loop()
            password = default_password or (files[0].get('password') if files else None)
            
            success = await asyncio.wait_for(
                loop.run_in_executor(
                    zip_executor,
                    zip_creation_task,
                    zip_path,
                    files,
                    password,
                    progress_tracker.zip_progress_queue
                ),
                timeout=timeout
            )
            
            if success:
                return True
            else:
                logger.warning(f"Zip creation failed on attempt {attempt + 1}")
                
        except asyncio.TimeoutError:
            logger.error(f"Zip creation timeout on attempt {attempt + 1}")
        except Exception as e:
            logger.error(f"Error in zip creation attempt {attempt + 1}: {e}")
        
        if attempt < max_retries - 1:
            await asyncio.sleep(5)
    
    return False

async def upload_large_file(file_path: str, chat_id: int, caption: str, reply_to_message_id: int, progress_callback, progress_args) -> bool:
    """آپلود فایل‌های بزرگ با مدیریت حافظه و خطا"""
    max_retries = Config.MAX_UPLOAD_RETRIES
    
    for attempt in range(max_retries):
        try:
            async with upload_semaphore:
                await app.send_document(
                    chat_id=chat_id,
                    document=file_path,
                    caption=caption,
                    reply_to_message_id=reply_to_message_id,
                    progress=progress_callback,
                    progress_args=progress_args
                )
                return True
                
        except FloodWait as e:
            wait_time = e.value
            logger.warning(f"FloodWait during upload: {wait_time} seconds")
            await notify_user_floodwait(chat_id, wait_time)
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(f"Error uploading file (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
    
    return False

async def upload_zip_part(zip_path: str, part_number: int, total_parts: int, chat_id: int, message_id: int, password: str, processing_msg: Message):
    try:
        # بررسی وجود فایل زیپ قبل از آپلود
        if not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
            logger.error(f"Zip file not found or empty: {zip_path}")
            return False

        file_size = os.path.getsize(zip_path)
        file_size_mb = file_size / (1024 * 1024)
        
        caption = (
            f"📦 **Part {part_number} of {total_parts}**\n"
            f"📊 Size: {file_size_mb:.1f} MB\n"
            f"🔐 Password: `{password}`" if password else "🔓 No Password"
        )
        
        # آپلود فایل
        success = await upload_large_file(
            file_path=zip_path,
            chat_id=chat_id,
            caption=caption,
            reply_to_message_id=message_id,
            progress_callback=upload_progress_callback,
            progress_args=(part_number, total_parts, processing_msg)
        )
        
        if success:
            logger.info(f"Successfully uploaded part {part_number}")
            return True
        else:
            logger.error(f"Failed to upload part {part_number}")
            return False
            
    except Exception as e:
        logger.error(f"Error in upload_zip_part: {e}")
        return False

async def upload_progress_callback(current, total, part_number, total_parts, processing_msg):
    try:
        percent = (current / total) * 100
        progress_text = (
            f"📤 **در حال آپلود**\n"
            f"📦 Part {part_number} of {total_parts}\n"
            f"📊 پیشرفت: {percent:.1f}%\n"
            f"⚡ سرعت: {(current / (1024 * 1024)) / (time.time() - progress_tracker.start_time):.1f} MB/s"
        )
        
        if time.time() - progress_tracker.last_update > Config.PROGRESS_UPDATE_INTERVAL:
            await processing_msg.edit_text(progress_text)
            progress_tracker.last_update = time.time()
            
    except Exception as e:
        logger.error(f"Error in upload progress callback: {e}")

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
@app.on_message(filters.command("start"))
async def start(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    
    welcome_text = (
        "👋 **به ربات فشرده‌ساز پیشرفته خوش آمدید!**\n\n"
        "📦 **امکانات:**\n"
        "• فشرده‌سازی فایل‌ها با پسورد\n"
        "• تقسیم به پارت‌های 2GB\n"
        "• پشتیبانی از فایل‌های بزرگ\n\n"
        "📝 **دستورات:**\n"
        "/start - نمایش این راهنما\n"
        "/zip - شروع فشرده‌سازی\n"
        "/zipnow - فشرده‌سازی فوری\n"
        "/cancel - لغو عملیات\n"
        "/done - اتمام افزودن فایل"
    )
    
    await safe_send_message(
        message.chat.id,
        welcome_text,
        reply_to_message_id=message.id
    )

@app.on_message(filters.document | filters.video | filters.audio)
async def handle_file(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    
    user_id = message.from_user.id
    
    try:
        # دریافت اطلاعات فایل
        if message.document:
            file_name = message.document.file_name
            file_size = message.document.file_size
        elif message.video:
            file_name = message.video.file_name or f"video_{message.id}.mp4"
            file_size = message.video.file_size
        elif message.audio:
            file_name = message.audio.file_name or f"audio_{message.id}.mp3"
            file_size = message.audio.file_size
        else:
            return
        
        # بررسی حجم فایل
        if file_size > Config.MAX_FILE_SIZE:
            await safe_send_message(
                message.chat.id,
                f"❌ **حجم فایل بسیار زیاد است!**\n"
                f"📁 فایل: {file_name}\n"
                f"📊 حجم: {file_size / (1024*1024*1024):.1f} GB\n"
                f"⚠️ حداکثر حجم مجاز: {Config.MAX_FILE_SIZE / (1024*1024*1024):.1f} GB",
                reply_to_message_id=message.id
            )
            return
        
        # افزودن فایل به لیست کاربر
        if user_id not in user_files:
            user_files[user_id] = []
        
        user_files[user_id].append({
            'message_id': message.id,
            'file_name': file_name,
            'file_size': file_size,
            'chat_id': message.chat.id
        })
        
        total_size = sum(f['file_size'] for f in user_files[user_id])
        
        await safe_send_message(
            message.chat.id,
            f"✅ **فایل افزوده شد**\n"
            f"📁 نام: {file_name}\n"
            f"📊 حجم: {file_size / (1024*1024):.1f} MB\n"
            f"📦 کل فایل‌ها: {len(user_files[user_id])}\n"
            f"💾 حجم کل: {total_size / (1024*1024*1024):.1f} GB\n\n"
            f"📝 برای افزودن فایل‌های بیشتر ارسال کنید یا /done را بفرستید",
            reply_to_message_id=message.id
        )
        
        save_user_data()
        
    except Exception as e:
        logger.error(f"Error handling file: {e}")
        await safe_send_message(
            message.chat.id,
            "❌ **خطا در پردازش فایل**\nلطفاً دوباره تلاش کنید.",
            reply_to_message_id=message.id
        )

@app.on_message(filters.command("zip"))
async def start_zip(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    
    user_id = message.from_user.id
    
    if user_id not in user_files or not user_files[user_id]:
        await safe_send_message(
            message.chat.id,
            "❌ **هیچ فایلی برای فشرده‌سازی وجود ندارد!**\nلطفاً ابتدا فایل‌ها را ارسال کنید.",
            reply_to_message_id=message.id
        )
        return
    
    user_states[user_id] = "waiting_password"
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("بدون پسورد", callback_data="nopassword")],
        [InlineKeyboardButton("لغو", callback_data="cancel")]
    ])
    
    await safe_send_message(
        message.chat.id,
        "🔐 **لطفاً پسورد دلخواه را وارد کنید:**\n"
        "• یا از دکمه 'بدون پسورد' استفاده کنید\n"
        "• یا /cancel برای لغو",
        reply_to_message_id=message.id,
        reply_markup=keyboard
    )

@app.on_message(filters.command("zipnow"))
async def start_zip_now(client, message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_files or not user_files[user_id]:
        await safe_send_message(
            message.chat.id,
            "❌ **هیچ فایلی برای فشرده‌سازی وجود ندارد!**",
            reply_to_message_id=message.id
        )
        return
    
    # شروع فشرده‌سازی بدون پسورد
    await process_zip_files(user_id, "archive", message.chat.id, message.id)

@app.on_message(filters.command("cancel"))
async def cancel_zip(client, message: Message):
    user_id = message.from_user.id
    if user_id in user_files:
        user_files[user_id] = []
        user_states[user_id] = None
        save_user_data()
    
    await safe_send_message(
        message.chat.id,
        "✅ **عملیات لغو شد**\nهمه فایل‌ها پاکسازی شدند.",
        reply_to_message_id=message.id
    )

@app.on_message(filters.command("done"))
async def handle_done_command(client, message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_files or not user_files[user_id]:
        await safe_send_message(
            message.chat.id,
            "❌ **هیچ فایلی برای پردازش وجود ندارد!**",
            reply_to_message_id=message.id
        )
        return
    
    await start_zip(client, message)

@app.on_callback_query()
async def handle_callback_query(client, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    try:
        if data == "nopassword":
            user_states[user_id] = None
            await callback_query.message.edit_text("✅ فشرده‌سازی بدون پسورد شروع شد...")
            await process_zip_files(user_id, "archive", callback_query.message.chat.id, callback_query.message.id)
            
        elif data == "cancel":
            if user_id in user_files:
                user_files[user_id] = []
                user_states[user_id] = None
                save_user_data()
            await callback_query.message.edit_text("✅ عملیات لغو شد")
            
    except Exception as e:
        logger.error(f"Error in callback query: {e}")
        await callback_query.answer("خطا در پردازش", show_alert=True)

def non_command_filter(_, __, message: Message):
    user_id = message.from_user.id
    return (message.text and 
            not message.text.startswith('/') and 
            user_id in user_states and 
            user_states.get(user_id) in ["waiting_password", "waiting_filename"])

non_command = filters.create(non_command_filter)

@app.on_message(non_command)
async def process_zip(client, message: Message):
    user_id = message.from_user.id
    current_state = user_states.get(user_id)
    
    try:
        if current_state == "waiting_password":
            password = message.text.strip()
            if len(password) > 100:
                await safe_send_message(
                    message.chat.id,
                    "❌ **پسورد بسیار طولانی است!**\nحداکثر 100 کاراکتر مجاز است.",
                    reply_to_message_id=message.id
                )
                return
            
            user_states[user_id] = None
            await safe_send_message(
                message.chat.id,
                f"✅ **پسورد تنظیم شد:** `{password}`\nشروع فشرده‌سازی...",
                reply_to_message_id=message.id
            )
            
            await process_zip_files(user_id, "archive", message.chat.id, message.id, password)
            
    except Exception as e:
        logger.error(f"Error in process_zip: {e}")
        await safe_send_message(
            message.chat.id,
            "❌ **خطا در پردازش**\nلطفاً دوباره تلاش کنید.",
            reply_to_message_id=message.id
        )

async def process_zip_files(user_id, zip_name, chat_id, message_id, password=None):
    processing_msg = None
    temp_downloaded_files = []  # لیست فایل‌های دانلود شده
    
    try:
        if user_id not in user_files or not user_files[user_id]:
            await safe_send_message(chat_id, "❌ هیچ فایلی برای پردازش وجود ندارد!")
            return
        
        # ایجاد پیام پردازش
        processing_msg = await safe_send_message(
            chat_id,
            "🔄 **در حال آماده‌سازی...**\nلطفاً منتظر بمانید",
            reply_to_message_id=message_id
        )
        
        files = user_files[user_id]
        total_size = sum(f['file_size'] for f in files)
        
        if total_size > Config.MAX_TOTAL_SIZE:
            await processing_msg.edit_text(
                f"❌ **حجم کل فایل‌ها بسیار زیاد است!**\n"
                f"📊 حجم کل: {total_size / (1024*1024*1024):.1f} GB\n"
                f"⚠️ حداکثر حجم مجاز: {Config.MAX_TOTAL_SIZE / (1024*1024*1024):.1f} GB"
            )
            return
        
        # ایجاد دایرکتوری موقت
        temp_dir = tempfile.mkdtemp(prefix="zip_bot_")
        download_dir = os.path.join(temp_dir, "downloads")
        zip_dir = os.path.join(temp_dir, "zips")
        os.makedirs(download_dir, exist_ok=True)
        os.makedirs(zip_dir, exist_ok=True)
        
        # دانلود فایل‌ها
        await processing_msg.edit_text("📥 **در حال دانلود فایل‌ها...**")
        
        downloaded_files = []
        for i, file_info in enumerate(files):
            file_path = os.path.join(download_dir, file_info['file_name'])
            
            # دریافت پیام اصلی
            try:
                original_message = await app.get_messages(
                    file_info['chat_id'],
                    file_info['message_id']
                )
            except Exception as e:
                logger.error(f"Error getting message {file_info['message_id']}: {e}")
                continue
            
            # دانلود فایل
            success = await safe_download_media(
                original_message,
                file_path,
                file_info['file_name'],
                i,
                len(files),
                processing_msg
            )
            
            if success and os.path.exists(file_path):
                downloaded_files.append({
                    'file_path': file_path,
                    'file_name': file_info['file_name'],
                    'size': file_info['file_size'],
                    'password': password
                })
                temp_downloaded_files.append(file_path)
            else:
                logger.error(f"Failed to download file: {file_info['file_name']}")
        
        if not downloaded_files:
            await processing_msg.edit_text("❌ **خطا در دانلود فایل‌ها!**")
            await cleanup_files([temp_dir])
            return
        
        # تقسیم فایل‌ها به پارت‌ها
        await processing_msg.edit_text("📦 **در حال ایجاد پارت‌های زیپ...**")
        
        current_part_size = 0
        current_part_files = []
        part_number = 1
        total_parts = math.ceil(total_size / Config.PART_SIZE)
        
        all_parts = []
        
        for file_info in downloaded_files:
            if current_part_size + file_info['size'] > Config.PART_SIZE and current_part_files:
                # ایجاد پارت جدید
                zip_path = os.path.join(zip_dir, f"{zip_name}_part{part_number}.zip")
                success = await create_zip_part_advanced(zip_path, current_part_files, password)
                
                if success:
                    all_parts.append(zip_path)
                    part_number += 1
                    current_part_files = []
                    current_part_size = 0
                else:
                    logger.error(f"Failed to create zip part {part_number}")
            
            current_part_files.append(file_info)
            current_part_size += file_info['size']
        
        # ایجاد آخرین پارت
        if current_part_files:
            zip_path = os.path.join(zip_dir, f"{zip_name}_part{part_number}.zip")
            success = await create_zip_part_advanced(zip_path, current_part_files, password)
            
            if success:
                all_parts.append(zip_path)
            else:
                logger.error(f"Failed to create final zip part")
        
        if not all_parts:
            await processing_msg.edit_text("❌ **خطا در ایجاد فایل‌های زیپ!**")
            await cleanup_files([temp_dir])
            return
        
        # آپلود پارت‌ها
        await processing_msg.edit_text("📤 **در حال آپلود پارت‌ها...**")
        
        uploaded_count = 0
        for i, zip_path in enumerate(all_parts):
            success = await upload_zip_part(
                zip_path,
                i + 1,
                len(all_parts),
                chat_id,
                message_id,
                password or "",
                processing_msg
            )
            
            if success:
                uploaded_count += 1
            else:
                logger.error(f"Failed to upload part {i + 1}")
        
        # پیام نهایی
        if uploaded_count == len(all_parts):
            await processing_msg.edit_text(
                f"✅ **عملیات با موفقیت完成 شد!**\n"
                f"📦 تعداد پارت‌ها: {len(all_parts)}\n"
                f"🔐 پسورد: `{password}`" if password else "🔓 بدون پسورد"
            )
        else:
            await processing_msg.edit_text(
                f"⚠️ **عملیات با خطای جزئی完成 شد**\n"
                f"✅ آپلود شده: {uploaded_count}/{len(all_parts)}\n"
                f"❌ ناموفق: {len(all_parts) - uploaded_count}"
            )
        
        # پاکسازی
        await cleanup_files([temp_dir])
        
        # پاکسازی لیست فایل‌های کاربر
        if user_id in user_files:
            user_files[user_id] = []
            save_user_data()
        
    except Exception as e:
        logger.error(f"Error in process_zip_files: {e}", exc_info=True)
        
        if processing_msg:
            await processing_msg.edit_text(
                "❌ **خطا در پردازش فایل‌ها!**\n"
                "📌 ممکن است فایل‌ها خراب شده باشند یا حجم بسیار زیاد باشد\n"
                "🔄 لطفاً دوباره فایل‌ها را ارسال کنید و تلاش کنید"
            )
        
        # پاکسازی فایل‌های موقت در صورت خطا
        await cleanup_files(temp_downloaded_files)
        
        # پاکسازی دایرکتوری موقت اگر وجود دارد
        temp_dir_path = None
        for file_path in temp_downloaded_files:
            temp_dir_path = os.path.dirname(os.path.dirname(file_path))
            break
        
        if temp_dir_path and os.path.exists(temp_dir_path):
            await cleanup_files([temp_dir_path])

async def run_bot():
    global app
    logger.info("🚀 Starting advanced zip/upload bot...")
    
    try:
        app = Client(
            "zip_bot",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            session_string=Config.SESSION_STRING
        )
        
        # لود داده‌های کاربر
        load_user_data()
        
        # شروع تسک‌های پس‌زمینه
        asyncio.create_task(process_scheduled_tasks())
        asyncio.create_task(process_task_queue())
        
        await app.start()
        logger.info("✅ Bot started successfully!")
        
        # نگه داشتن بات فعال
        await asyncio.sleep(86400)  # 24 hours
        
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
    finally:
        if app:
            await app.stop()
        logger.info("Bot stopped")

if __name__ == "__main__":
    web_app = Flask(__name__)
    
    @web_app.route('/')
    def home():
        return "Zip Bot is running!"
    
    # اجرای وب سرور در thread جداگانه
    threading.Thread(
        target=lambda: web_app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False),
        daemon=True
    ).start()
    
    # اجرای بات
    asyncio.run(run_bot())
