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

# ===== تنظیمات پیشرفته =====
class Config:
    API_ID = 26180086
    API_HASH = "d91e174c7faf0e5a6a3a2ecb0b3361f6"
    SESSION_STRING = "BAGPefYAEAaXaj52wzDLPF0RSfWtF_Slk8nFWzYAHS9vu-HBxRUz9yLnq7m8z-ajYCQxQZO-5aNX0he9OttDjmjieYDMbDjBJtbsOT2ZwsQNe8UCAo5oFPveD5V1H0cIBMlXCG1P49G2oonf1YL1r16Nt34AJLkmzDIoFD0hhxwVBXvrUGwZmEoTtdkfORCYUMGACKO4-Al-NH35oVCkTIqmXQ5DUp9PVx6DND243VW5Xcqay7qwrwfoS4sWRA-7TMXykbHa37ZsdcCOf0VS8e6PyaYvG5BjMCd9BGRnR9IImrksYY2uBM2Bg42MLaa1WFxQtn97p5ViPF9c1MpY49bc5Gm5lwAAAAF--TK5AA"
    ALLOWED_USER_IDS = [417536686]  # لیست کاربران مجاز
    MAX_FILE_SIZE = 4194304000  # 4GB
    MAX_TOTAL_SIZE = 8388608000  # 8GB
    PART_SIZE = 1900 * 1024 * 1024  # 1900MB (زیر محدودیت 2GB تلگرام)
    CHUNK_SIZE = 4 * 1024 * 1024  # 4MB برای دانلود/آپلود
    MAX_CONCURRENT_DOWNLOADS = 2  # حداکثر دانلود همزمان
    MAX_CONCURRENT_UPLOADS = 1  # حداکثر آپلود همزمان
    RETRY_DELAY = 10  # تاخیر بین تلاش مجدد
    PROGRESS_UPDATE_INTERVAL = 5  # ثانیه بین آپدیت‌های پیشرفت
    DATA_FILE = "user_data.json"  # فایل ذخیره وضعیت

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

# ===== فانکشن‌های کمکی پیشرفته =====
def is_user_allowed(user_id: int) -> bool:
    return user_id in Config.ALLOWED_USER_IDS

def load_user_data():
    """بارگذاری داده‌های کاربر از فایل"""
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
    """ذخیره داده‌های کاربر در فایل"""
    try:
        data = {
            'user_files': user_files,
            'user_states': user_states
        }
        with open(Config.DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error saving user data: {e}")

def format_size(size_bytes: int) -> str:
    """فرمت‌بندی حجم فایل به صورت خوانا"""
    if size_bytes == 0:
        return "0B"
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_names[i]}"

def format_time(seconds: int) -> str:
    """فرمت‌بندی زمان به صورت خوانا"""
    if seconds < 60:
        return f"{seconds} ثانیه"
    elif seconds < 3600:
        return f"{seconds // 60} دقیقه و {seconds % 60} ثانیه"
    else:
        return f"{seconds // 3600} ساعت و {(seconds % 3600) // 60} دقیقه"

async def safe_send_message(chat_id, text, reply_to_message_id=None, reply_markup=None, parse_mode=None):
    """ارسال پیام با مدیریت FloodWait و تلاش مجدد"""
    max_retries = 3  # کاهش تعداد تلاش‌ها
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
    
    # اگر همه تلاش‌ها ناموفق بودند، بدون parse_mode امتحان کن
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

async def safe_download_media(message, file_path, progress_callback=None):
    """دانلود با مدیریت FloodWait، قطع ارتباط و تلاش مجدد"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with download_semaphore:
                await asyncio.sleep(random.uniform(1.0, 3.0))
                
                # ایجاد دایرکتوری مقصد اگر وجود ندارد
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                
                # دانلود با progress callback
                await app.download_media(
                    message,
                    file_name=file_path,
                    progress=progress_callback,
                    progress_args=(message, f"دانلود (تلاش {attempt + 1})")
                )
                
                # بررسی صحت دانلود
                if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                    return True
                else:
                    logger.warning(f"Downloaded file is empty or missing (attempt {attempt + 1})")
                    
        except FloodWait as e:
            wait_time = e.value + random.uniform(5, 10)
            logger.warning(f"Download FloodWait: {wait_time} seconds (attempt {attempt + 1})")
            await asyncio.sleep(wait_time)
        except (RPCError, aiohttp.ClientError, OSError) as e:
            logger.error(f"Download error (attempt {attempt + 1}): {e}")
            await asyncio.sleep(Config.RETRY_DELAY)
        except Exception as e:
            logger.error(f"Unexpected download error (attempt {attempt + 1}): {e}")
            await asyncio.sleep(Config.RETRY_DELAY)
    
    # حذف فایل ناقص در صورت وجود
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except:
        pass
    
    return False

async def progress_callback(current, total, message: Message, stage: str):
    """کالبک پیشرفت با بهینه‌سازی برای کاهش بار"""
    try:
        # فقط در بازه‌های زمانی مشخص آپدیت شود
        now = time.time()
        if hasattr(progress_callback, 'last_update'):
            if now - progress_callback.last_update < Config.PROGRESS_UPDATE_INTERVAL:
                return
        progress_callback.last_update = now
        
        percent = (current / total) * 100
        speed = current / (now - progress_callback.start_time) if hasattr(progress_callback, 'start_time') else 0
        eta = (total - current) / speed if speed > 0 else 0
        
        # فقط در درصدهای خاص یا تغییرات بزرگ آپدیت شود
        if percent % 10 != 0 and current != total and total - current > 1024 * 1024:
            return
            
        bar_length = 20
        filled_length = int(bar_length * current // total)
        bar = '█' * filled_length + '░' * (bar_length - filled_length)
        
        progress_text = (
            f"{stage}\n\n"
            f"{bar} {percent:.1f}%\n\n"
            f"📊 پیشرفت: {format_size(current)} / {format_size(total)}\n"
            f"🚀 سرعت: {format_size(speed)}/ثانیه\n"
            f"⏱️ زمان باقیمانده: {format_time(int(eta))}"
        )
        
        # فقط اگر متن تغییر کرده آپدیت شود
        if (not hasattr(progress_callback, 'last_text') or 
            progress_callback.last_text != progress_text):
            await message.edit_text(progress_text)
            progress_callback.last_text = progress_text
            
    except Exception as e:
        logger.error(f"Progress callback error: {e}")

# مقداردهی اولیه برای progress_callback
progress_callback.start_time = time.time()
progress_callback.last_update = 0
progress_callback.last_text = ""

def schedule_task(task_func: Callable, delay: float, *args, **kwargs):
    """زمان‌بندی یک تسک برای اجرای بعدی"""
    execution_time = time.time() + delay
    scheduled_tasks.append((execution_time, task_func, args, kwargs))
    # مرتب کردن بر اساس زمان اجرا
    scheduled_tasks.sort(key=lambda x: x[0])

async def process_scheduled_tasks():
    """پردازش تسک‌های زمان‌بندی شده"""
    while True:
        now = time.time()
        tasks_to_run = []
        
        # پیدا کردن تسک‌های رسیده
        for i, (execution_time, task_func, args, kwargs) in enumerate(scheduled_tasks):
            if execution_time <= now:
                tasks_to_run.append((task_func, args, kwargs))
                scheduled_tasks.pop(i)
            else:
                break  # لیست مرتب شده است
        
        # اجرای تسک‌های رسیده
        for task_func, args, kwargs in tasks_to_run:
            try:
                if asyncio.iscoroutinefunction(task_func):
                    await task_func(*args, **kwargs)
                else:
                    # اجرا در thread جداگانه برای توابع غیر همزمان
                    await asyncio.to_thread(task_func, *args, **kwargs)
            except Exception as e:
                logger.error(f"Scheduled task error: {e}")
        
        await asyncio.sleep(1)

async def process_task_queue():
    """پردازش صف تسک‌ها"""
    global processing
    
    while True:
        if not task_queue:
            await asyncio.sleep(1)
            continue
        
        processing = True
        task_func, args, kwargs = task_queue.popleft()
        
        try:
            if asyncio.iscoroutinefunction(task_func):
                await task_func(*args, **kwargs)
            else:
                await asyncio.to_thread(task_func, *args, **kwargs)
            
            await asyncio.sleep(random.uniform(2.0, 5.0))
            
        except FloodWait as e:
            wait_time = e.value + random.uniform(10, 15)
            logger.warning(f"🕒 FloodWait detected: {wait_time} seconds. Rescheduling task...")
            
            # زمان‌بندی مجدد
            schedule_task(task_func, wait_time, *args, **kwargs)
            
            # اطلاع به کاربر
            user_id = kwargs.get('user_id', args[0] if args else None)
            if user_id:
                await notify_user_floodwait(user_id, wait_time)
            
            await asyncio.sleep(5)
            
        except Exception as e:
            logger.error(f"Task error: {e}")
            await asyncio.sleep(5)
        
        finally:
            processing = False
            save_user_data()  # ذخیره وضعیت بعد از هر تسک

def add_to_queue(task_func: Callable, *args, **kwargs):
    """اضافه کردن تسک به صف"""
    task_queue.append((task_func, args, kwargs))
    logger.info(f"Task added to queue. Queue size: {len(task_queue)}")

async def notify_user_floodwait(user_id: int, wait_time: int):
    """اطلاع به کاربر درباره FloodWait"""
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

async def create_zip_part(zip_path: str, files: List[Dict], password: Optional[str] = None):
    """ایجاد یک پارت زیپ با قابلیت بازیابی"""
    try:
        # ایجاد دایرکتوری مقصد
        os.makedirs(os.path.dirname(zip_path), exist_ok=True)
        
        # استفاده از compression level بهینه
        with pyzipper.AESZipFile(
            zip_path, 
            "w", 
            compression=pyzipper.ZIP_DEFLATED,
            compresslevel=6,  # تعادل بین سرعت و حجم
            encryption=pyzipper.WZ_AES
        ) as zipf:
            if password:
                zipf.setpassword(password.encode('utf-8'))
            
            for file_info in files:
                # اضافه کردن فایل با حفظ ساختار دایرکتوری نسبی
                arcname = os.path.basename(file_info['path'])
                zipf.write(file_info['path'], arcname)
        
        return True
        
    except Exception as e:
        logger.error(f"Error creating zip part {zip_path}: {e}")
        # حذف فایل زیپ ناقص
        try:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        except:
            pass
        return False

async def upload_zip_part(zip_path: str, part_number: int, total_parts: int, 
                         chat_id: int, message_id: int, password: str, processing_msg: Message):
    """آپلود یک پارت زیپ با مدیریت خطا"""
    try:
        async with upload_semaphore:
            part_size = os.path.getsize(zip_path)
            
            await processing_msg.edit_text(
                f"📤 در حال آپلود پارت {part_number + 1}/{total_parts}\n"
                f"📦 حجم: {format_size(part_size)}"
            )
            
            # تنظیم زمان شروع برای progress callback
            progress_callback.start_time = time.time()
            
            # آپلود با تلاش مجدد
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    await app.send_document(
                        chat_id,
                        zip_path,
                        caption=(
                            f"📦 پارت {part_number + 1}/{total_parts}\n"
                            f"🔑 رمز: `{password}`\n"
                            f"💾 حجم: {format_size(part_size)}"
                        ),
                        progress=progress_callback,
                        progress_args=(processing_msg, f"آپلود پارت {part_number + 1}"),
                        reply_to_message_id=message_id
                    )
                    break  # خروج از حلقه در صورت موفقیت
                    
                except FloodWait as e:
                    if attempt == max_retries - 1:
                        raise  # اگر آخرین تلاش است، خطا را بالا ببر
                    wait_time = e.value + random.uniform(5, 10)
                    logger.warning(f"Upload FloodWait: {wait_time} seconds (attempt {attempt + 1})")
                    await asyncio.sleep(wait_time)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise  # اگر آخرین تلاش است، خطا را بالا ببر
                    logger.error(f"Upload error (attempt {attempt + 1}): {e}")
                    await asyncio.sleep(Config.RETRY_DELAY)
            
            await asyncio.sleep(random.uniform(3.0, 8.0))
            return True
            
    except FloodWait as e:
        # زمان‌بندی مجدد
        wait_time = e.value + random.uniform(10, 15)
        schedule_task(
            upload_zip_part, 
            wait_time, 
            zip_path, part_number, total_parts, 
            chat_id, message_id, password, processing_msg
        )
        logger.warning(f"Upload rescheduled after {wait_time} seconds")
        return False
    except Exception as e:
        logger.error(f"Error uploading part {part_number}: {e}")
        return False

# ===== هندلرها =====
async def start(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    
    welcome_text = (
        "👋 سلام! به ربات زیپ و آپلود پیشرفته خوش آمدید\n\n"
        "📦 قابلیت‌های ربات:\n"
        "• زیپ کردن فایل‌ها با رمزگذاری AES-256\n"
        "• تقسیم به پارت‌های خودکار زیر 2GB\n"
        "• دانلود و آپلود با قابلیت بازیابی\n"
        "• مدیریت محدودیت‌های تلگرام\n\n"
        "📝 روش استفاده:\n"
        "1. فایل‌ها را ارسال کنید (می‌توانید چندین فایل ارسال کنید)\n"
        "2. از کپشن `pass=رمز` برای رمز جداگانه هر فایل استفاده کنید\n"
        "3. دستور /zip را برای شروع فرآیند وارد کنید\n\n"
        f"⚙️ محدودیت‌ها:\n"
        f"• حداکثر حجم هر فایل: {format_size(Config.MAX_FILE_SIZE)}\n"
        f"• حداکثر حجم کل: {format_size(Config.MAX_TOTAL_SIZE)}\n\n"
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
        reply_markup=keyboard
    )

async def handle_file(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    
    if not message.document and not message.video and not message.audio:
        return
    
    # تشخیص نوع فایل و اطلاعات آن
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
    
    # استخراج رمز از کپشن
    if "pass=" in caption:
        password_match = caption.split("pass=", 1)[1].split()[0].strip()
        if password_match:
            password = password_match
    
    # بررسی محدودیت حجم
    if file_size > Config.MAX_FILE_SIZE:
        await safe_send_message(
            message.chat.id,
            f"❌ حجم فایل بیش از حد مجاز است!\n"
            f"📦 حجم فایل: {format_size(file_size)}\n"
            f"⚖️ حد مجاز: {format_size(Config.MAX_FILE_SIZE)}",
            reply_to_message_id=message.id
        )
        return
    
    user_id = message.from_user.id
    if user_id not in user_files:
        user_files[user_id] = []
    
    # بررسی تکراری نبودن فایل
    existing_files = [f['file_name'] for f in user_files[user_id]]
    if file_name in existing_files:
        # اضافه کردن پسوند برای جلوگیری از تداخل
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
        f"✅ فایل ذخیره شد\n\n"
        f"📝 نام: {file_name}\n"
        f"📦 حجم: {format_size(file_size)}\n"
        f"🔑 رمز: {password if password else '❌ ندارد'}\n\n"
        f"📊 وضعیت فعلی: {file_count} فایل ({format_size(total_size)})\n\n"
        f"📌 برای شروع زیپ از /zip استفاده کنید",
        reply_to_message_id=message.id
    )
    
    save_user_data()  # ذخیره وضعیت

async def start_zip(client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return
    
    user_id = message.from_user.id
    if user_id not in user_files or not user_files[user_id]:
        await safe_send_message(
            message.chat.id,
            "❌ هیچ فایلی برای زیپ کردن وجود ندارد\n\n"
            "📝 لطفاً ابتدا فایل‌ها را ارسال کنید",
            reply_to_message_id=message.id
        )
        return
    
    total_size = sum(f["file_size"] for f in user_files[user_id])
    if total_size > Config.MAX_TOTAL_SIZE:
        await safe_send_message(
            message.chat.id,
            f"❌ حجم کل فایل‌ها بیش از حد مجاز است!\n\n"
            f"📦 حجم کل: {format_size(total_size)}\n"
            f"⚖️ حد مجاز: {format_size(Config.MAX_TOTAL_SIZE)}\n\n"
            f"📌 لطفاً تعداد فایل‌ها را کاهش دهید",
            reply_to_message_id=message.id
        )
        user_files[user_id] = []
        save_user_data()
        return
    
    user_states[user_id] = "waiting_password"
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 بدون رمز", callback_data="no_password")],
        [InlineKeyboardButton("❌ لغو", callback_data="cancel_zip")]
    ])
    
    await safe_send_message(
        message.chat.id,
        "🔐 لطفاً رمز عبور برای فایل زیپ وارد کنید:\n\n"
        "📝 می‌توانید از دکمه زیر برای بدون رمز استفاده کنید\n"
        "⚠️ توجه: رمز عبور باید حداقل 4 کاراکتر باشد",
        reply_to_message_id=message.id,
        reply_markup=keyboard
    )

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
        "✅ همه فایل‌های ذخیره شده پاک شدند\n"
        "📌 می‌توانید دوباره فایل‌ها را ارسال کنید",
        reply_to_message_id=message.id
    )

async def process_zip(client, message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_states:
        return
    
    if user_states.get(user_id) == "waiting_password":
        zip_password = message.text.strip()
        
        if not zip_password:
            await safe_send_message(
                message.chat.id,
                "❌ رمز عبور نمی‌تواند خالی باشد\n\n"
                "📝 لطفاً یک رمز عبور معتبر وارد کنید",
                reply_to_message_id=message.id
            )
            return
        
        if len(zip_password) < 4:
            await safe_send_message(
                message.chat.id,
                "❌ رمز عبور باید حداقل 4 کاراکتر باشد\n\n"
                "📝 لطفاً یک رمز قوی‌تر انتخاب کنید",
                reply_to_message_id=message.id
            )
            return
        
        user_states[user_id] = "waiting_filename"
        user_states[f"{user_id}_password"] = zip_password
        
        # پیشنهاد نام فایل بر اساس تاریخ و زمان
        suggested_name = f"archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        await safe_send_message(
            message.chat.id,
            f"📝 حالا نام فایل زیپ نهایی را وارد کنید\n\n"
            f"💡 پیشنهاد: {suggested_name}\n"
            f"⚠️ توجه: پسوند .zip اضافه خواهد شد",
            reply_to_message_id=message.id
        )
        return
    
    if user_states.get(user_id) == "waiting_filename":
        zip_name = message.text.strip()
        if not zip_name:
            await safe_send_message(
                message.chat.id,
                "❌ نام فایل نمی‌تواند خالی باشد",
                reply_to_message_id=message.id
            )
            return
        
        # حذف کاراکترهای غیرمجاز از نام فایل
        import re
        zip_name = re.sub(r'[<>:"/\\|?*]', '_', zip_name)
        zip_name = zip_name[:50]  # محدودیت طول نام
        
        user_states[f"{user_id}_zipname"] = zip_name
        
        # نمایش خلاصه و تایید نهایی
        total_files = len(user_files[user_id])
        total_size = sum(f["file_size"] for f in user_files[user_id])
        password = user_states.get(f"{user_id}_password", "بدون رمز")
        
        # ایجاد دکمه‌ها
        buttons = [
            [InlineKeyboardButton("✅ شروع زیپ", callback_data="confirm_zip")],
            [InlineKeyboardButton("❌ لغو", callback_data="cancel_zip")]
        ]
        keyboard = InlineKeyboardMarkup(buttons)
        
        # ارسال پیام با دکمه‌ها
        summary_message = await safe_send_message(
            message.chat.id,
            f"📦 خلاصه درخواست زیپ\n\n"
            f"📝 نام فایل: {zip_name}.zip\n"
            f"🔑 رمز: {password}\n"
            f"📊 تعداد فایل‌ها: {total_files}\n"
            f"💾 حجم کل: {format_size(total_size)}\n\n"
            f"⚠️ توجه: این عملیات ممکن است زمان بر باشد\n"
            f"📌 برای شروع روی دکمه زیر کلیک کنید",
            reply_to_message_id=message.id,
            reply_markup=keyboard
        )
        
        # ذخیره ID پیام برای استفاده بعدی
        if summary_message:
            user_states[f"{user_id}_summary_msg_id"] = summary_message.id

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
            "📤 حالت ارسال فایل فعال شد\n\n"
            "📝 می‌توانید فایل‌ها را ارسال کنید\n"
            "🔑 برای رمزگذاری از کپشن `pass=رمز` استفاده کنید\n"
            "📌 پس از اتمام از /zip استفاده کنید"
        )
    
    elif data == "help":
        await callback_query.answer()
        await safe_send_message(
            user_id,
            "📖 راهنمای کامل ربات\n\n"
            "1. ارسال فایل‌ها: فایل‌های خود را به ربات ارسال کنید\n"
            "2. رمزگذاری: در کپشن از `pass=رمز` استفاده کنید\n"
            "3. شروع زیپ: پس از ارسال همه فایل‌ها، /zip را بزنید\n"
            "4. تنظیمات: رمز کلی و نام فایل را وارد کنید\n"
            "5. دریافت: ربات فایل‌ها را زیپ و آپلود می‌کند\n\n"
            "⚙️ ویژگی‌های پیشرفته:\n"
            "• تقسیم خودکار به پارت‌های زیر 2GB\n"
            "• رمزگذاری AES-256\n"
            "• بازیابی از خطا\n"
            "• مدیریت محدودیت تلگرام\n\n"
            "🛠 پشتیبانی: در صورت مشکل با /cancel شروع کنید"
        )
    
    elif data == "no_password":
        await callback_query.answer("حالت بدون رمز انتخاب شد")
        user_states[user_id] = "waiting_filename"
        user_states[f"{user_id}_password"] = None
        
        suggested_name = f"archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        await safe_send_message(
            user_id,
            f"📝 حالا نام فایل زیپ نهایی را وارد کنید\n\n"
            f"💡 پیشنهاد: {suggested_name}\n"
            f"⚠️ توجه: پسوند .zip اضافه خواهد شد"
        )
    
    elif data == "confirm_zip":
        await callback_query.answer("پردازش شروع شد...")
        zip_name = user_states.get(f"{user_id}_zipname", f"archive_{int(time.time())}")
        add_to_queue(process_zip_files, user_id, zip_name, callback_query.message.chat.id, callback_query.message.id)
    
    elif data == "cancel_zip":
        await callback_query.answer("عملیات لغو شد")
        await cancel_zip(client, callback_query.message)
    
    await callback_query.message.delete()

async def process_zip_files(user_id, zip_name, chat_id, message_id):
    """پردازش هوشمندانه فایل‌ها با زیپ و آپلود تدریجی"""
    processing_msg = None
    
    try:
        processing_msg = await app.send_message(chat_id, "⏳ در حال آماده‌سازی...")
        zip_password = user_states.get(f"{user_id}_password")
        
        # ایجاد دایرکتوری موقت
        with tempfile.TemporaryDirectory() as tmp_dir:
            total_files = len(user_files[user_id])
            file_info_list = []
            
            # مرحله 1: دانلود همه فایل‌ها
            await processing_msg.edit_text("📥 در حال دانلود فایل‌ها...")
            
            for i, finfo in enumerate(user_files[user_id], 1):
                file_msg_id = finfo["message_id"]
                
                try:
                    # دریافت پیام فایل از تاریخچه چت
                    file_msg = await app.get_messages(chat_id, file_msg_id)
                    if not file_msg:
                        logger.error(f"Message {file_msg_id} not found")
                        continue
                    
                    file_name = finfo["file_name"]
                    file_path = os.path.join(tmp_dir, file_name)
                    
                    # دانلود فایل با progress
                    await processing_msg.edit_text(
                        f"📥 در حال دانلود فایل {i}/{total_files}\n"
                        f"📝 نام: {file_name}"
                    )
                    
                    success = await safe_download_media(
                        file_msg,
                        file_path,
                        progress_callback
                    )
                    
                    if success and os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                        file_size = os.path.getsize(file_path)
                        file_info_list.append({
                            'path': file_path,
                            'name': file_name,
                            'size': file_size,
                            'password': finfo["password"] or zip_password
                        })
                        logger.info(f"Downloaded {file_name} ({format_size(file_size)})")
                    else:
                        logger.error(f"Failed to download {file_name}")
                    
                    await asyncio.sleep(1)
                    
                except Exception as e:
                    logger.error(f"Error processing file {finfo['file_name']}: {e}")
                    continue
            
            if not file_info_list:
                await processing_msg.edit_text("❌ هیچ فایلی با موفقیت دانلود نشد")
                return
            
            # مرحله 2: ایجاد پارت‌ها به صورت هوشمند
            await processing_msg.edit_text("📦 در حال ایجاد پارت‌های زیپ...")
            
            # مرتب کردن فایل‌ها بر اساس حجم (بزرگ به کوچک)
            file_info_list.sort(key=lambda x: x['size'], reverse=True)
            
            # ایجاد پارت‌ها
            parts = []
            current_part = []
            current_size = 0
            
            for file_info in file_info_list:
                file_size = file_info['size']
                
                # اگر فایل از 1.8GB بزرگتر است،独自 یک پارت شود
                if file_size > Config.PART_SIZE * 0.9:
                    if current_part:
                        parts.append(current_part)
                        current_part = []
                        current_size = 0
                    parts.append([file_info])
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
            await processing_msg.edit_text(
                f"📦 تقسیم به {num_parts} پارت\n\n"
                f"💾 حجم هر پارت: ~{format_size(Config.PART_SIZE)}"
            )
            
            # مرحله 3: ایجاد و آپلود هر پارت
            successful_parts = 0
            
            for part_index, part_files in enumerate(parts):
                part_number = part_index + 1
                part_zip_name = f"{zip_name}_part{part_number}.zip"
                zip_path = os.path.join(tmp_dir, part_zip_name)
                
                # استفاده از رمز اولین فایل در پارت یا رمز全局
                part_password = part_files[0].get('password', zip_password)
                
                # ایجاد زیپ برای این پارت
                await processing_msg.edit_text(
                    f"🗜️ در حال فشرده‌سازی پارت {part_number}/{num_parts}\n"
                    f"📝 شامل {len(part_files)} فایل"
                )
                
                success = await create_zip_part(zip_path, part_files, part_password)
                if not success:
                    logger.error(f"Failed to create zip part {part_number}")
                    continue
                
                # آپلود پارت
                upload_success = await upload_zip_part(
                    zip_path, 
                    part_index, 
                    num_parts, 
                    chat_id, 
                    message_id, 
                    part_password or "بدون رمز",
                    processing_msg
                )
                
                if upload_success:
                    successful_parts += 1
                
                # حذف فایل‌های موقت این پارت
                try:
                    os.remove(zip_path)
                    for file_info in part_files:
                        try:
                            os.remove(file_info['path'])
                        except:
                            pass
                except:
                    pass
                
                await asyncio.sleep(2)
            
            # نتیجه نهایی
            if successful_parts > 0:
                result_text = (
                    f"✅ عملیات با موفقیت завер شد!\n\n"
                    f"📦 پارت‌های ایجاد شده: {successful_parts}/{num_parts}\n"
                    f"🔑 رمز اصلی: {zip_password or 'بدون رمز'}\n\n"
                    f"📌 نکات مهم:\n"
                    f"• برای extract همه پارت‌ها را دانلود کنید\n"
                    f"• از رمز یکسان برای همه پارت‌ها استفاده کنید\n"
                    f"• فایل‌ها به طور خودکار حذف شدند"
                )
            else:
                result_text = "❌ خطا در ایجاد پارت‌ها\n\nلطفاً دوباره تلاش کنید"
            
            await safe_send_message(
                chat_id,
                result_text,
                reply_to_message_id=message_id
            )
            
    except FloodWait as e:
        logger.warning(f"⏰ FloodWait در پردازش زیپ: {e.value} ثانیه")
        
        if processing_msg:
            await processing_msg.edit_text(
                f"⏳ عملیات متوقف شد due to limit\n\n"
                f"🕒 ادامه بعد از: {e.value} ثانیه\n"
                f"✅ به طور خودکار ادامه خواهد یافت"
            )
        
        # زمان‌بندی مجدد
        schedule_task(process_zip_files, e.value + 15, user_id, zip_name, chat_id, message_id)
        
    except Exception as e:
        logger.error(f"خطا در پردازش زیپ: {e}", exc_info=True)
        if processing_msg:
            await processing_msg.edit_text(
                "❌ خطایی در پردازش رخ داد\n\n"
                "📌 لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید"
            )
    finally:
        # پاکسازی نهایی
        if user_id in user_files:
            user_files[user_id] = []
        user_states.pop(user_id, None)
        user_states.pop(f"{user_id}_password", None)
        user_states.pop(f"{user_id}_zipname", None)
        save_user_data()

# ===== فیلترها =====
def non_command_filter(_, __, message: Message):
    user_id = message.from_user.id
    return (message.text and 
            not message.text.startswith('/') and 
            user_id in user_states and
            user_states.get(user_id) in ["waiting_password", "waiting_filename"])

non_command = filters.create(non_command_filter)

# ===== تابع برای اجرای ربات =====
async def run_bot():
    global app
    logger.info("🚀 Starting advanced zip/upload bot...")
    
    # بارگذاری داده‌های کاربر
    load_user_data()
    
    app = Client(
        "user_bot",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        session_string=Config.SESSION_STRING,
        in_memory=True
    )
    
    # راه‌اندازی پردازش‌کننده‌های پس‌زمینه
    asyncio.create_task(process_scheduled_tasks())
    asyncio.create_task(process_task_queue())
    
    # اضافه کردن هندلرها
    app.on_message(filters.command("start"))(start)
    app.on_message(filters.document | filters.video | filters.audio)(handle_file)
    app.on_message(filters.command("zip"))(start_zip)
    app.on_message(filters.command("cancel"))(cancel_zip)
    app.on_message(filters.text & non_command)(process_zip)
    app.on_callback_query()(handle_callback_query)
    
    await app.start()
    logger.info("✅ Bot started successfully with advanced features!")
    
    # ذخیره دوره‌ای داده‌ها
    async def periodic_save():
        while True:
            await asyncio.sleep(300)  # هر 5 دقیقه
            save_user_data()
            logger.info("💾 User data saved periodically")
    
    asyncio.create_task(periodic_save())
    
    await asyncio.Event().wait()

# ===== اجرا =====
if __name__ == "__main__":
    # راه‌اندازی سرور وب برای سلامت‌سنجی
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
            "formatted_size": format_size(total_size)
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
    
    # راه‌اندازی ربات در thread جداگانه
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()
    
    # راه‌اندازی سرور وب
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"🌐 Starting Flask web server on port {port}...")
    
    # اجرای سرور وب در thread جداگانه
    def run_web_app():
        web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    
    web_thread = threading.Thread(target=run_web_app, daemon=True)
    web_thread.start()
    
    # انتظار برای پایان threads
    try:
        bot_thread.join()
        web_thread.join()
    except KeyboardInterrupt:
        logger.info("👋 Bot stopped by user")
        save_user_data()
        sys.exit(0)
