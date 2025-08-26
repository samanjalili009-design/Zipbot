import os
import time
import tempfile
import pyzipper
import logging
import sys
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait
from flask import Flask
import threading
from collections import deque
import random
import math
from typing import Dict, List, Callable, Any, Tuple

# ===== تنظیمات =====
API_ID = 26180086
API_HASH = "d91e174c7faf0e5a6a3a2ecb0b3361f6"
SESSION_STRING = "BAGPefYAEAaXaj52wzDLPF0RSfWtF_Slk8nFWzYAHS9vu-HBxRUz9yLnq7m8z-ajYCQxQZO-5aNX0he9OttDjmjieYDMbDjBJtbsOT2ZwsQNe8UCAo5oFPveD5V1H0cIBMlXCG1P49G2oonf1YL1r16Nt34AJLkmzDIoFD0hhxwVBXvrUGwZmEoTtdkfORCYUMGACKO4-Al-NH35oVCkTIqmXQ5DUp9PVx6DND243VW5Xcqay7qwrwfoS4sWRA-7TMXykbHa37ZsdcCOf0VS8e6PyaYvG5BjMCd9BGRnR9IImrksYY2uBM2Bg42MLaa1WFxQtn97p5ViPF9c1MpY49bc5Gm5lwAAAAF--TK5AA"
ALLOWED_USER_ID = 417536686
MAX_FILE_SIZE = 2097152000  # 2GB
MAX_TOTAL_SIZE = 2097152000  # 2GB
PART_SIZE = 500 * 1024 * 1024  # 500MB per part

# ===== لاگ =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ===== کلاینت Pyrogram =====
app = None

# ===== داده‌ها =====
user_files: Dict[int, List] = {}
user_states: Dict[int, Any] = {}
scheduled_tasks: List[Tuple[float, Callable, Tuple, Dict]] = []
task_queue = deque()
processing = False

# ===== فانکشن‌های کمکی =====
def is_user_allowed(user_id: int) -> bool:
    return user_id == ALLOWED_USER_ID

async def safe_send_message(chat_id, text, reply_to_message_id=None, priority=False):
    """ارسال پیام با مدیریت FloodWait"""
    try:
        await asyncio.sleep(random.uniform(1.0, 3.0))
        await app.send_message(chat_id, text, reply_to_message_id=reply_to_message_id)
    except FloodWait as e:
        logger.warning(f"FloodWait: {e.value} seconds")
        schedule_task(lambda: safe_send_message(chat_id, text, reply_to_message_id, priority), e.value + 5)
    except Exception as e:
        logger.error(f"Error sending message: {e}")

async def safe_download_media(message, file_path, progress=None, progress_args=None):
    """دانلود با مدیریت FloodWait"""
    try:
        await asyncio.sleep(random.uniform(2.0, 5.0))
        await app.download_media(message, file_path, progress=progress, progress_args=progress_args)
        return True
    except FloodWait as e:
        logger.warning(f"Download FloodWait: {e.value} seconds")
        schedule_task(lambda: safe_download_media(message, file_path, progress, progress_args), e.value + 10)
        return False
    except Exception as e:
        logger.error(f"Download error: {e}")
        return False

async def progress_bar(current, total, message: Message, start_time, stage="دانلود"):
    """نوار پیشرفت با تاخیرهای کنترل شده"""
    try:
        now = time.time()
        diff = now - start_time
        if diff == 0: 
            diff = 1
        
        percent = int(current * 100 / total)
        
        if percent % 5 != 0 and current != total:
            return
            
        speed = current / diff
        eta = int((total - current) / speed) if speed > 0 else 0
        bar_filled = int(percent / 5)
        bar = "▓" * bar_filled + "░" * (20 - bar_filled)
        
        text = f"🚀 {stage} فایل...\n{bar} {percent}%\n📦 {current//1024//1024}MB / {total//1024//1024}MB"
        
        # جلوگیری از MESSAGE_NOT_MODIFIED
        if getattr(message, "_last_text", None) != text:
            await message.edit_text(text)
            message._last_text = text
        
        await asyncio.sleep(1)
        
    except Exception as e:
        logger.error(f"Progress error: {e}")

def schedule_task(task_func: Callable, delay: float, *args, **kwargs):
    execution_time = time.time() + delay
    scheduled_tasks.append((execution_time, task_func, args, kwargs))

async def process_scheduled_tasks():
    while True:
        now = time.time()
        for i, (execution_time, task_func, args, kwargs) in enumerate(scheduled_tasks[:]):
            if execution_time <= now:
                try:
                    if asyncio.iscoroutinefunction(task_func):
                        await task_func(*args, **kwargs)
                    else:
                        task_func(*args, **kwargs)
                    scheduled_tasks.pop(i)
                except Exception as e:
                    logger.error(f"Scheduled task error: {e}")
                    scheduled_tasks.pop(i)
        await asyncio.sleep(1)

async def process_task_queue():
    global processing
    while True:
        if processing or not task_queue:
            await asyncio.sleep(1)
            continue
        processing = True
        try:
            task_func, args, kwargs = task_queue.popleft()
            try:
                if asyncio.iscoroutinefunction(task_func):
                    await task_func(*args, **kwargs)
                else:
                    task_func(*args, **kwargs)
                await asyncio.sleep(random.uniform(2.0, 5.0))
            except FloodWait as e:
                wait_time = e.value + 10
                logger.warning(f"🕒 FloodWait detected: {wait_time} seconds. Rescheduling task...")
                schedule_task(task_func, wait_time, *args, **kwargs)
                user_id = kwargs.get('user_id')
                if user_id:
                    await notify_user_floodwait(user_id, wait_time)
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Task error: {e}")
                await asyncio.sleep(5)
        finally:
            processing = False
            await asyncio.sleep(0.1)

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
    except:
        pass

def add_to_queue(task_func: Callable, *args, **kwargs):
    task_queue.append((task_func, args, kwargs))

async def create_single_zip(zip_path, files, password):
    with pyzipper.AESZipFile(zip_path, "w", 
                           compression=pyzipper.ZIP_DEFLATED, 
                           encryption=pyzipper.WZ_AES) as zipf:
        if password:
            zipf.setpassword(password.encode())
        for file_info in files:
            zipf.write(file_info['path'], file_info['name'])

def split_file(input_file, chunk_size=PART_SIZE):
    part_number = 1
    parts = []
    try:
        file_size = os.path.getsize(input_file)
        total_parts = math.ceil(file_size / chunk_size)
        logger.info(f"📦 Splitting file {file_size//1024//1024}MB into {total_parts} parts")
        with open(input_file, 'rb') as f:
            for part_num in range(1, total_parts + 1):
                part_filename = f"{input_file}.part{part_num:03d}"
                bytes_written = 0
                with open(part_filename, 'wb') as part_file:
                    while bytes_written < chunk_size:
                        remaining = chunk_size - bytes_written
                        chunk = f.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            break
                        part_file.write(chunk)
                        bytes_written += len(chunk)
                if bytes_written > 0:
                    parts.append(part_filename)
                    logger.info(f"✅ Created part {part_num}: {bytes_written//1024//1024}MB")
                else:
                    break
        return parts
    except Exception as e:
        logger.error(f"Error splitting file: {e}")
        for part_file in parts:
            try:
                os.remove(part_file)
            except:
                pass
        raise

async def upload_zip_part(zip_path, part_number, total_parts, chat_id, message_id, password, processing_msg):
    try:
        part_size = os.path.getsize(zip_path)
        await processing_msg.edit_text(
            f"📤 در حال آپلود پارت {part_number}/{total_parts}\n"
            f"📦 حجم: {part_size // 1024 // 1024}MB"
        )
        start_time = time.time()
        await app.send_document(
            chat_id,
            zip_path,
            caption=(
                f"📦 پارت {part_number}/{total_parts}\n"
                f"🔑 رمز: `{password}`\n"
                f"💾 حجم: {part_size // 1024 // 1024}MB"
            ),
            progress=progress_bar,
            progress_args=(processing_msg, start_time, f"آپلود پارت {part_number}"),
            reply_to_message_id=message_id
        )
        await asyncio.sleep(random.uniform(5.0, 10.0))
    except FloodWait as e:
        schedule_task(upload_zip_part, e.value + 10, zip_path, part_number, total_parts, chat_id, message_id, password, processing_msg)
        raise
    except Exception as e:
        logger.error(f"Error uploading part {part_number}: {e}")
        raise

# ===== هندلرها =====
async def start(client, message):
    if not is_user_allowed(message.from_user.id):
        return
    await safe_send_message(
        message.chat.id,
        "سلام 👋\nفایل‌تو بفرست تا برات زیپ کنم.\n"
        "💡 کپشن فایل = pass=رمز برای تعیین پسورد (اختیاری)\n"
        f"📦 حداکثر حجم هر فایل: {MAX_FILE_SIZE//1024//1024}MB\n"
        f"📦 حداکثر حجم کل: {MAX_TOTAL_SIZE//1024//1024}MB\n"
        "بعد از ارسال فایل‌ها دستور /zip رو بزن",
        reply_to_message_id=message.id
    )

async def handle_file(client, message):
    if not is_user_allowed(message.from_user.id):
        return
    if not message.document:
        return
    doc = message.document
    file_name = doc.file_name or f"file_{message.id}"
    caption = message.caption or ""
    password = None
    if "pass=" in caption:
        password = caption.split("pass=",1)[1].split()[0].strip()
    if doc.file_size > MAX_FILE_SIZE:
        await safe_send_message(
            message.chat.id,
            f"❌ حجم فایل بیش از حد مجاز است! ({MAX_FILE_SIZE//1024//1024}MB)",
            reply_to_message_id=message.id
        )
        return
    user_id = message.from_user.id
    if user_id not in user_files:
        user_files[user_id] = []
    user_files[user_id].append({
        "message": message, 
        "file_name": file_name, 
        "password": password, 
        "file_size": doc.file_size
    })
    await safe_send_message(
        message.chat.id,
        f"✅ فایل '{file_name}' ذخیره شد. برای شروع زیپ /zip را بزنید.",
        reply_to_message_id=message.id
    )

async def start_zip(client, message):
    if not is_user_allowed(message.from_user.id):
        return
    user_id = message.from_user.id
    if user_id not in user_files or not user_files[user_id]:
        await safe_send_message(
            message.chat.id,
            "❌ هیچ فایلی برای زیپ کردن وجود ندارد.",
            reply_to_message_id=message.id
        )
        return
    total_size = sum(f["file_size"] for f in user_files[user_id])
    if total_size > MAX_TOTAL_SIZE:
        await safe_send_message(
            message.chat.id,
            f"❌ حجم کل فایل‌ها بیش از حد مجاز است! ({MAX_TOTAL_SIZE//1024//1024}MB)",
            reply_to_message_id=message.id
        )
        user_files[user_id] = []
        return
    user_states[user_id] = "waiting_password"
    await safe_send_message(
        message.chat.id,
        "🔐 لطفاً رمز عبور برای فایل زیپ وارد کن:\n❌ برای لغو /cancel را بزنید",
        reply_to_message_id=message.id
    )

async def cancel_zip(client, message):
    user_id = message.from_user.id
    if user_id in user_files:
        user_files[user_id] = []
    user_states.pop(user_id, None)
    await safe_send_message(
        message.chat.id,
        "❌ عملیات لغو شد.",
        reply_to_message_id=message.id
    )

async def process_zip(client, message):
    user_id = message.from_user.id
    if user_id not in user_states:
        return
    await asyncio.sleep(1)
    if user_states.get(user_id) == "waiting_password":
        zip_password = message.text.strip()
        if not zip_password:
            await safe_send_message(
                message.chat.id,
                "❌ رمز عبور نمی‌تواند خالی باشد.",
                reply_to_message_id=message.id
            )
            return
        user_states[user_id] = "waiting_filename"
        user_states[f"{user_id}_password"] = zip_password
        await safe_send_message(
            message.chat.id,
            "📝 حالا اسم فایل زیپ نهایی را وارد کن (بدون .zip)",
            reply_to_message_id=message.id
        )
        return
    if user_states.get(user_id) == "waiting_filename":
        zip_name = message.text.strip()
        if not zip_name:
            await safe_send_message(
                message.chat.id,
                "❌ اسم فایل نمی‌تواند خالی باشد.",
                reply_to_message_id=message.id
            )
            return
        add_to_queue(process_zip_files, user_id, zip_name, message.chat.id, message.id)

async def process_zip_files(user_id, zip_name, chat_id, message_id):
    try:
        processing_msg = await app.send_message(chat_id, "⏳ در حال ایجاد فایل زیپ...")
        zip_password = user_states.get(f"{user_id}_password")
        with tempfile.TemporaryDirectory() as tmp_dir:
            total_files = len(user_files[user_id])
            file_info_list = []
            for i, finfo in enumerate(user_files[user_id], 1):
                file_msg = finfo["message"]
                file_name = finfo["file_name"]
                file_path = os.path.join(tmp_dir, file_name)
                start_time = time.time()
                await safe_download_media(
                    file_msg,
                    file_path,
                    progress=progress_bar,
                    progress_args=(processing_msg, start_time, f"دانلود {i}/{total_files}")
                )
                if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                    file_size = os.path.getsize(file_path)
                    file_info_list.append({
                        'path': file_path,
                        'name': file_name,
                        'size': file_size
                    })
                await asyncio.sleep(2)
            zip_path = os.path.join(tmp_dir, f"{zip_name}.zip")
            await processing_msg.edit_text("🔐 در حال ایجاد فایل زیپ رمزگذاری شده...")
            await create_single_zip(zip_path, file_info_list, zip_password)
            zip_size = os.path.getsize(zip_path)
            await processing_msg.edit_text(f"✅ فایل زیپ ایجاد شد. حجم: {zip_size//1024//1024}MB")
            if zip_size <= PART_SIZE:
                await processing_msg.edit_text("📤 در حال آپلود فایل زیپ...")
                start_time = time.time()
                await app.send_document(
                    chat_id,
                    zip_path,
                    caption=f"🔑 رمز: `{zip_password}`\n💾 حجم: {zip_size//1024//1024}MB",
                    progress=progress_bar,
                    progress_args=(processing_msg, start_time, "آپلود فایل زیپ"),
                    reply_to_message_id=message_id
                )
                await safe_send_message(
                    chat_id,
                    f"✅ فایل زیپ آماده شد!\n🔑 رمز: `{zip_password}`",
                    reply_to_message_id=message_id
                )
            else:
                await processing_msg.edit_text("✂️ در حال تقسیم فایل زیپ به بخش‌های 500 مگابایتی...")
                loop = asyncio.get_event_loop()
                parts = await loop.run_in_executor(None, split_file, zip_path, PART_SIZE)
                total_parts = len(parts)
                await processing_msg.edit_text(f"✅ فایل زیپ به {total_parts} بخش تقسیم شد.")
                for part_index, part_path in enumerate(parts, 1):
                    await upload_zip_part(
                        part_path, 
                        part_index, 
                        total_parts, 
                        chat_id, 
                        message_id, 
                        zip_password,
                        processing_msg
                    )
                    try:
                        os.remove(part_path)
                    except:
                        pass
                await safe_send_message(
                    chat_id,
                    f"✅ تمامی {total_parts} پارت زیپ آماده شد!\n🔑 رمز: `{zip_password}`",
                    reply_to_message_id=message_id
                )
    except FloodWait
