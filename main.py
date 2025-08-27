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

# ===== تنظیمات پیشرفته =====
class Config:
    API_ID = 26180086
    API_HASH = "d91e174c7faf0e5a6a3a2ecb0b3361f6"
    SESSION_STRING = "BAGPefYAEAaXaj52wzDLPF0RSfWtF_Slk8nFWzYAHS9vu-HBxRUz9yLnq7m8z-ajYCQxQZO-5aNX0he9OttDjmjieYDMbDjBJtbsOT2ZwsQNe8UCAo5oFPveD5V1H0cIBMlXCG1P49G2oonf1YL1r16Nt34AJLkmzDIoFD0hhxwVBXvrUGwZmEoTtdkfORCYUMGACKO4-Al-NH35oVCkTIqmXQ5DUp9PVx6DND243VW5Xcqay7qwrwfoS4sWRA-7TMXykbHa37ZsdcCOf0VS8e6PyaYvG5BjMCd9BGRnR9IImrksYY2uBM2Bg42MLaa1WFxQtn97p5ViPF9c1MpY49bc5Gm5lwAAAAF--TK5AA"
    ALLOWED_USER_IDS = [417536686]
    MAX_FILE_SIZE = 2147483648  # 2GB افزایش یافته
    MAX_TOTAL_SIZE = 8589934592  # 8GB
    PART_SIZE = 1900 * 1024 * 1024  # 1900MB
    CHUNK_SIZE = 1 * 1024 * 1024  # کاهش به 1MB برای مدیریت بهتر حافظه
    MAX_CONCURRENT_DOWNLOADS = 2
    MAX_CONCURRENT_UPLOADS = 1
    RETRY_DELAY = 10
    PROGRESS_UPDATE_INTERVAL = 0.5  # افزایش فرکانس آپدیت
    DATA_FILE = "user_data.json"
    ZIP_MAX_RETRIES = 3  # حداکثر تعداد تلاش برای فشرده‌سازی
    ZIP_RETRY_DELAY = 5  # تاخیر بین تلاش‌های مجدد فشرده‌سازی

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

# ===== کلاس جدید برای مدیریت پیشرفت =====
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
                
                # به روزرسانی وضعیت دانلود
                if processing_msg:
                    progress_text = f"📥 **Downloading** `{file_name}`\n\n**Progress:** {file_index}/{total_files} files\n**Status:** Downloading..."
                    try:
                        await processing_msg.edit_text(progress_text)
                    except:
                        pass
                
                # دانلود فایل
                await message.download(file_path)
                logger.info(f"Downloaded: {file_name} to {file_path}")
                return True
                
        except FloodWait as e:
            wait_time = e.value + random.uniform(2, 5)
            logger.warning(f"FloodWait during download: {wait_time} seconds (attempt {attempt + 1}/{max_retries})")
            if processing_msg:
                try:
                    await processing_msg.edit_text(f"⏳ Flood wait: {wait_time} seconds. Retrying...")
                except:
                    pass
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(f"Error downloading file {file_name} (attempt {attempt + 1}): {e}")
            await asyncio.sleep(2)
    
    return False

def schedule_task(task_func: Callable, delay: float, *args, **kwargs):
    execution_time = time.time() + delay
    scheduled_tasks.append((execution_time, task_func, args, kwargs))
    scheduled_tasks.sort(key=lambda x: x[0])

async def process_scheduled_tasks():
    while True:
        now = time.time()
        tasks_to_run = []
        
        # جمع‌آوری وظایفی که زمان اجرای آنها فرا رسیده است
        for task in scheduled_tasks[:]:
            if task[0] <= now:
                tasks_to_run.append(task)
                scheduled_tasks.remove(task)
        
        # اجرای وظایف
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
            processing = True
            task_func, args, kwargs = task_queue.popleft()
            try:
                if asyncio.iscoroutinefunction(task_func):
                    await task_func(*args, **kwargs)
                else:
                    task_func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Error processing task: {e}")
            processing = False
        await asyncio.sleep(0.1)

def add_to_queue(task_func: Callable, *args, **kwargs):
    task_queue.append((task_func, args, kwargs))
    logger.info(f"Task added to queue. Queue size: {len(task_queue)}")

async def notify_user_floodwait(user_id: int, wait_time: int):
    try:
        wait_minutes = wait_time // 60
        wait_seconds = wait_time % 60
        message = f"⏳ Flood wait activated. Please wait {wait_minutes} minutes and {wait_seconds} seconds."
        await safe_send_message(user_id, message)
    except Exception as e:
        logger.error(f"Error notifying user about floodwait: {e}")

async def create_zip_part(zip_path: str, files: List[Dict], password: Optional[str] = None) -> bool:
    """
    ایجاد یک بخش ZIP با قابلیت retry و مدیریت خطاهای بهبود یافته
    """
    for attempt in range(Config.ZIP_MAX_RETRIES):
        try:
            logger.info(f"Creating zip part: {zip_path} (attempt {attempt + 1})")
            os.makedirs(os.path.dirname(zip_path), exist_ok=True)
            
            # استفاده از context manager برای مدیریت خودکار فایل
            with pyzipper.AESZipFile(
                zip_path,
                'w',
                compression=pyzipper.ZIP_DEFLATED,
                compresslevel=6  # سطح متعادل فشرده‌سازی
            ) as zipf:
                if password:
                    zipf.setpassword(password.encode())
                    zipf.setencryption(pyzipper.WZ_AES, nbits=256)
                
                # افزودن فایل‌ها به ZIP
                for file_info in files:
                    file_path = file_info.get('file_path')
                    file_name = file_info.get('file_name', os.path.basename(file_path))
                    
                    if os.path.exists(file_path):
                        try:
                            zipf.write(file_path, file_name)
                            logger.info(f"Added {file_name} to zip")
                        except Exception as e:
                            logger.error(f"Error adding {file_name} to zip: {e}")
                            continue
            
            # بررسی صحت فایل ZIP ایجاد شده
            if os.path.exists(zip_path) and os.path.getsize(zip_path) > 0:
                logger.info(f"Zip part created successfully: {zip_path} (size: {os.path.getsize(zip_path)} bytes)")
                return True
            else:
                logger.warning(f"Zip part creation failed: empty or missing file (attempt {attempt + 1})")
                
        except Exception as e:
            logger.error(f"Error creating zip part (attempt {attempt + 1}): {e}")
            # پاک کردن فایل خراب اگر وجود دارد
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except:
                    pass
        
        # تاخیر قبل از تلاش مجدد
        if attempt < Config.ZIP_MAX_RETRIES - 1:
            logger.info(f"Retrying zip creation in {Config.ZIP_RETRY_DELAY} seconds...")
            await asyncio.sleep(Config.ZIP_RETRY_DELAY)
    
    return False

async def upload_zip_part(zip_path: str, part_number: int, total_parts: int, 
                         chat_id: int, message_id: int, password: str, processing_msg: Message):
    try:
        async with upload_semaphore:
            part_size = os.path.getsize(zip_path)
            
            # به روزرسانی وضعیت آپلود
            progress_text = f"📤 **Uploading** Part {part_number}/{total_parts}\n\n**File:** {os.path.basename(zip_path)}\n**Size:** {part_size / (1024*1024):.2f} MB\n**Status:** Uploading..."
            try:
                await processing_msg.edit_text(progress_text)
            except:
                pass
            
            # آپلود فایل
            await app.send_document(
                chat_id,
                zip_path,
                caption=f"Part {part_number}/{total_parts}" + (f"\nPassword: `{password}`" if password else ""),
                reply_to_message_id=message_id,
                progress=progress_callback,
                progress_args=(processing_msg, f"Part {part_number}/{total_parts}")
            )
            
            logger.info(f"Uploaded part {part_number}/{total_parts}")
            
    except FloodWait as e:
        wait_time = e.value
        logger.warning(f"FloodWait during upload: {wait_time} seconds")
        await notify_user_floodwait(chat_id, wait_time)
        await asyncio.sleep(wait_time)
        # تلاش مجدد پس از FloodWait
        await upload_zip_part(zip_path, part_number, total_parts, chat_id, message_id, password, processing_msg)
    except Exception as e:
        logger.error(f"Error uploading zip part {part_number}: {e}")
        # تلاش مجدد پس از خطا
        await asyncio.sleep(Config.RETRY_DELAY)
        await upload_zip_part(zip_path, part_number, total_parts, chat_id, message_id, password, processing_msg)

async def progress_callback(current, total, processing_msg, file_name):
    try:
        percent = (current / total) * 100
        progress_bar = "█" * int(percent / 5) + "░" * (20 - int(percent / 5))
        speed = current / (time.time() - progress_tracker.start_time)
        
        text = f"📤 **Uploading** `{file_name}`\n\n{progress_bar} {percent:.1f}%\n\n**Size:** {current/(1024*1024):.1f}MB / {total/(1024*1024):.1f}MB\n**Speed:** {speed/(1024*1024):.1f} MB/s"
        
        # فقط هر 0.5 ثانیه آپدیت کنیم تا از FloodWait جلوگیری شود
        if time.time() - progress_tracker.last_update > Config.PROGRESS_UPDATE_INTERVAL:
            await processing_msg.edit_text(text)
            progress_tracker.last_update = time.time()
    except FloodWait as e:
        await asyncio.sleep(e.value)
    except Exception as e:
        logger.error(f"Error updating progress: {e}")

# ===== هندلرها =====
async def start_handler(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    
    welcome_text = """
    🤖 **Advanced Zip/Upload Bot**
    
    **دستورات موجود:**
    /start - نمایش این راهنما
    /zip - شروع فرآیند ZIP (پس از ارسال فایل‌ها)
    /zipnow - شروع فشرده‌سازی فوری
    /cancel - لغو عملیات جاری
    /done - اتمام ارسال فایل‌ها و شروع فشرده‌سازی
    
    **نحوه استفاده:**
    1. فایل‌های خود را ارسال کنید
    2. از دستور /zip یا /zipnow استفاده کنید
    3. در صورت نیاز رمز عبور وارد کنید
    4. منتظر بمانید تا فایل‌ها فشرده و آپلود شوند
    """
    
    await safe_send_message(
        message.chat.id,
        welcome_text,
        reply_to_message_id=message.id
    )

async def handle_file_handler(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    
    user_id = message.from_user.id
    
    # بررسی اندازه فایل
    file_size = message.document.file_size if message.document else (
        message.video.file_size if message.video else (
            message.audio.file_size if message.audio else (
                message.photo.file_size if message.photo else 0
            )
        )
    )
    
    if file_size > Config.MAX_FILE_SIZE:
        await safe_send_message(
            user_id,
            f"❌ فایل بسیار بزرگ است! حداکثر اندازه مجاز: {Config.MAX_FILE_SIZE / (1024*1024*1024):.1f}GB",
            reply_to_message_id=message.id
        )
        return
    
    # افزودن فایل به لیست کاربر
    if user_id not in user_files:
        user_files[user_id] = []
    
    file_name = message.document.file_name if message.document else (
        message.video.file_name if message.video else (
            message.audio.file_name if message.audio else f"photo_{message.id}.jpg"
        )
    )
    
    user_files[user_id].append({
        'message_id': message.id,
        'file_name': file_name,
        'file_size': file_size,
        'file_type': 'document' if message.document else (
            'video' if message.video else (
                'audio' if message.audio else 'photo'
            )
        )
    })
    
    total_size = sum(f['file_size'] for f in user_files[user_id])
    if total_size > Config.MAX_TOTAL_SIZE:
        await safe_send_message(
            user_id,
            f"❌ حجم کل فایل‌ها بیش از حد مجاز است! حداکثر: {Config.MAX_TOTAL_SIZE / (1024*1024*1024):.1f}GB",
            reply_to_message_id=message.id
        )
        user_files[user_id] = []
        return
    
    await safe_send_message(
        user_id,
        f"✅ فایل `{file_name}` اضافه شد.\n\n📊 تعداد فایل‌ها: {len(user_files[user_id])}\n💾 حجم کل: {total_size/(1024*1024):.1f}MB\n\nبرای شروع فشرده‌سازی از /zip استفاده کنید.",
        reply_to_message_id=message.id
    )
    
    save_user_data()

async def start_zip_handler(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    
    user_id = message.from_user.id
    
    if user_id not in user_files or not user_files[user_id]:
        await safe_send_message(
            user_id,
            "❌ هیچ فایلی برای فشرده‌سازی وجود ندارد! لطفا ابتدا فایل‌ها را ارسال کنید.",
            reply_to_message_id=message.id
        )
        return
    
    user_states[user_id] = "waiting_filename"
    await safe_send_message(
        user_id,
        "📝 لطفا یک نام برای فایل ZIP وارد کنید:",
        reply_to_message_id=message.id
    )

async def start_zip_now_handler(client, message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_files or not user_files[user_id]:
        await safe_send_message(
            user_id,
            "❌ هیچ فایلی برای فشرده‌سازی وجود ندارد!",
            reply_to_message_id=message.id
        )
        return
    
    # استفاده از نام پیش‌فرض برای فایل ZIP
    zip_name = f"archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    user_states[user_id] = "waiting_password"
    
    await safe_send_message(
        user_id,
        f"🔐 آیا می‌خواهید برای فایل ZIP رمز عبور設定 کنید؟\n\nنام فایل: `{zip_name}.zip`",
        reply_to_message_id=message.id,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ بله", callback_data=f"pwd_yes_{zip_name}"),
             InlineKeyboardButton("❌ خیر", callback_data=f"pwd_no_{zip_name}")]
        ])
    )

async def cancel_zip_handler(client, message: Message):
    user_id = message.from_user.id
    if user_id in user_files:
        user_files[user_id] = []
    if user_id in user_states:
        user_states[user_id] = None
    
    await safe_send_message(
        user_id,
        "✅ عملیات کنسل شد.",
        reply_to_message_id=message.id
    )
    save_user_data()

async def handle_done_command_handler(client, message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_files or not user_files[user_id]:
        await safe_send_message(
            user_id,
            "❌ هیچ فایلی برای فشرده‌سازی وجود ندارد!",
            reply_to_message_id=message.id
        )
        return
    
    await start_zip_now_handler(client, message)

async def handle_callback_query_handler(client, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    if data.startswith("pwd_yes_"):
        zip_name = data.split("_", 2)[2]
        user_states[user_id] = f"waiting_password_{zip_name}"
        await callback_query.message.edit_text(
            "🔐 لطفا رمز عبور مورد نظر را وارد کنید:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data=f"back_{zip_name}")]
            ])
        )
    
    elif data.startswith("pwd_no_"):
        zip_name = data.split("_", 2)[2]
        await callback_query.message.edit_text(
            "⏳ در حال آماده‌سازی فایل‌ها...",
            reply_markup=None
        )
        await process_zip_files(user_id, zip_name, callback_query.message.chat.id, callback_query.message.id, None)
    
    elif data.startswith("back_"):
        zip_name = data.split("_", 1)[1]
        user_states[user_id] = "waiting_password"
        await callback_query.message.edit_text(
            f"🔐 آیا می‌خواهید برای فایل ZIP رمز عبور設定 کنید؟\n\nنام فایل: `{zip_name}.zip`",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ بله", callback_data=f"pwd_yes_{zip_name}"),
                 InlineKeyboardButton("❌ خیر", callback_data=f"pwd_no_{zip_name}")]
            ])
        )
    
    await callback_query.answer()

async def handle_text_message_handler(client, message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_states:
        return
    
    state = user_states[user_id]
    
    if state == "waiting_filename":
        zip_name = message.text.strip()
        user_states[user_id] = "waiting_password"
        
        await safe_send_message(
            user_id,
            f"🔐 آیا می‌خواهید برای فایل ZIP رمز عبور設定 کنید؟\n\nنام فایل: `{zip_name}.zip`",
            reply_to_message_id=message.id,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ بله", callback_data=f"pwd_yes_{zip_name}"),
                 InlineKeyboardButton("❌ خیر", callback_data=f"pwd_no_{zip_name}")]
            ])
        )
    
    elif state.startswith("waiting_password_"):
        zip_name = state.split("_", 2)[2]
        password = message.text.strip()
        
        if len(password) < 4:
            await safe_send_message(
                user_id,
                "❌ رمز عبور باید حداقل 4 کاراکتر باشد! لطفا مجددا وارد کنید:",
                reply_to_message_id=message.id
            )
            return
        
        await safe_send_message(
            user_id,
            "⏳ در حال آماده‌سازی فایل‌ها...",
            reply_to_message_id=message.id
        )
        await process_zip_files(user_id, zip_name, message.chat.id, message.id, password)
        
        user_states[user_id] = None

async def process_zip_files(user_id, zip_name, chat_id, message_id, password):
    """
    پردازش و فشرده‌سازی فایل‌ها با مدیریت خطاهای بهبود یافته
    """
    processing_msg = None
    
    try:
        # ایجاد پیام وضعیت
        processing_msg = await safe_send_message(
            chat_id,
            "⏳ در حال دانلود فایل‌ها...",
            reply_to_message_id=message_id
        )
        
        # ایجاد پوشه موقت
        temp_dir = tempfile.mkdtemp()
        downloaded_files = []
        total_size = 0
        
        # دانلود تمام فایل‌ها
        for i, file_info in enumerate(user_files[user_id], 1):
            try:
                file_message = await app.get_messages(chat_id, file_info['message_id'])
                file_path = os.path.join(temp_dir, file_info['file_name'])
                
                # دانلود فایل
                success = await safe_download_media(
                    file_message,
                    file_path,
                    file_info['file_name'],
                    i,
                    len(user_files[user_id]),
                    processing_msg
                )
                
                if success and os.path.exists(file_path):
                    downloaded_files.append({
                        'file_path': file_path,
                        'file_name': file_info['file_name']
                    })
                    total_size += os.path.getsize(file_path)
                    
                    # به روزرسانی وضعیت
                    progress_text = f"📥 **Downloading** {i}/{len(user_files[user_id])}\n\n**Total Size:** {total_size/(1024*1024):.1f}MB\n**Status:** Downloading..."
                    try:
                        await processing_msg.edit_text(progress_text)
                    except:
                        pass
                else:
                    logger.error(f"Failed to download file: {file_info['file_name']}")
                    
            except Exception as e:
                logger.error(f"Error processing file {file_info['file_name']}: {e}")
                continue
        
        if not downloaded_files:
            await processing_msg.edit_text("❌ هیچ فایلی با موفقیت دانلود نشد!")
            return
        
        # محاسبه تعداد بخش‌های مورد نیاز
        total_parts = math.ceil(total_size / Config.PART_SIZE)
        
        await processing_msg.edit_text(
            f"✅ دانلود کامل شد!\n\n"
            f"📊 تعداد فایل‌ها: {len(downloaded_files)}\n"
            f"💾 حجم کل: {total_size/(1024*1024):.1f}MB\n"
            f"📦 تعداد بخش‌ها: {total_parts}\n\n"
            f"⏳ در حال فشرده‌سازی..."
        )
        
        # تقسیم فایل‌ها به بخش‌ها و فشرده‌سازی
        current_part_size = 0
        current_files = []
        part_number = 1
        
        for file_info in downloaded_files:
            file_size = os.path.getsize(file_info['file_path'])
            
            # اگر افزودن این فایل باعث превы اندازه بخش شود، بخش فعلی را فشرده کنید
            if current_part_size + file_size > Config.PART_SIZE and current_files:
                zip_path = os.path.join(temp_dir, f"{zip_name}_part{part_number}.zip")
                
                # فشرده‌سازی بخش فعلی
                success = await create_zip_part(zip_path, current_files, password)
                
                if success:
                    # آپلود بخش فشرده شده
                    await upload_zip_part(
                        zip_path, part_number, total_parts, 
                        chat_id, message_id, password, processing_msg
                    )
                    
                    # پاکسازی فایل‌های موقت
                    for f in current_files:
                        try:
                            os.remove(f['file_path'])
                        except:
                            pass
                    try:
                        os.remove(zip_path)
                    except:
                        pass
                    
                    part_number += 1
                    current_files = []
                    current_part_size = 0
                else:
                    logger.error(f"Failed to create zip part {part_number}")
            
            # افزودن فایل به بخش فعلی
            current_files.append(file_info)
            current_part_size += file_size
        
        # فشرده‌سازی آخرین بخش
        if current_files:
            zip_path = os.path.join(temp_dir, f"{zip_name}_part{part_number}.zip")
            
            success = await create_zip_part(zip_path, current_files, password)
            
            if success:
                await upload_zip_part(
                    zip_path, part_number, total_parts,
                    chat_id, message_id, password, processing_msg
                )
                
                # پاکسازی
                for f in current_files:
                    try:
                        os.remove(f['file_path'])
                    except:
                        pass
                try:
                    os.remove(zip_path)
                except:
                    pass
        
        # اتمام عملیات
        await processing_msg.edit_text(
            f"✅ فشرده‌سازی و آپلود کامل شد!\n\n"
            f"📦 تعداد بخش‌ها: {part_number}\n"
            f"🔐 رمز عبور: {'ست شده' if password else 'ندارد'}\n"
            f"💾 حجم کل: {total_size/(1024*1024):.1f}MB"
        )
        
        # پاکسازی داده‌های کاربر
        if user_id in user_files:
            user_files[user_id] = []
        if user_id in user_states:
            user_states[user_id] = None
        save_user_data()
        
    except Exception as e:
        logger.error(f"Error in process_zip_files: {e}")
        if processing_msg:
            try:
                await processing_msg.edit_text(f"❌ خطا در پردازش فایل‌ها: {str(e)}")
            except:
                pass
    finally:
        # پاکسازی پوشه موقت
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except:
            pass

def non_command_filter(_, __, message: Message):
    user_id = message.from_user.id
    return (message.text and 
            not message.text.startswith('/') and 
            user_id in user_states and 
            user_states.get(user_id) in ["waiting_password", "waiting_filename"])

non_command = filters.create(non_command_filter)

async def run_bot():
    global app
    logger.info("🚀 Starting advanced zip/upload bot...")
    
    try:
        app = Client(
            "my_bot",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            session_string=Config.SESSION_STRING
        )
        
        # بارگذاری داده‌های کاربر
        load_user_data()
        
        # ثبت هندلرها بعد از مقداردهی app
        app.add_handler(filters.command("start"), start_handler)
        app.add_handler(filters.command("zip"), start_zip_handler)
        app.add_handler(filters.command("zipnow"), start_zip_now_handler)
        app.add_handler(filters.command("cancel"), cancel_zip_handler)
        app.add_handler(filters.command("done"), handle_done_command_handler)
        app.add_handler(filters.document | filters.video | filters.audio | filters.photo, handle_file_handler)
        app.add_handler(filters.text & filters.private, handle_text_message_handler)
        app.add_handler(filters.callback_query, handle_callback_query_handler)
        
        # شروع پردازش وظایف
        asyncio.create_task(process_scheduled_tasks())
        asyncio.create_task(process_task_queue())
        
        # اجرای ربات
        await app.start()
        logger.info("🤖 Bot started successfully!")
        
        # نگه داشتن ربات فعال
        while True:
            await asyncio.sleep(3600)
            
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
    finally:
        if app:
            await app.stop()

if __name__ == "__main__":
    web_app = Flask(__name__)
    
    @web_app.route('/')
    def home():
        return "Bot is running!"
    
    # اجرای ربات در یک thread جداگانه
    bot_thread = threading.Thread(target=lambda: asyncio.run(run_bot()))
    bot_thread.daemon = True
    bot_thread.start()
    
    # اجرای وب سرور
    web_app.run(host='0.0.0.0', port=5000)
