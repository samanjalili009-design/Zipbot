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
import heapq
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

# ===== سیستم زمان‌بندی تسک‌ها =====
class TaskScheduler:
    def __init__(self):
        self.scheduled_tasks: List[Tuple[float, int, Callable, Tuple, Dict]] = []
        self.task_counter = 0
        self.running = True
        asyncio.create_task(self._scheduler_loop())
    
    async def _scheduler_loop(self):
        """حلقه اصلی زمان‌بندی"""
        while self.running:
            now = time.time()
            
            while self.scheduled_tasks and self.scheduled_tasks[0][0] <= now:
                execution_time, task_id, task_func, args, kwargs = heapq.heappop(self.scheduled_tasks)
                try:
                    if asyncio.iscoroutinefunction(task_func):
                        await task_func(*args, **kwargs)
                    else:
                        task_func(*args, **kwargs)
                except Exception as e:
                    logger.error(f"Scheduled task error: {e}")
            
            if self.scheduled_tasks:
                next_time = self.scheduled_tasks[0][0]
                sleep_time = max(0, next_time - now)
                await asyncio.sleep(sleep_time)
            else:
                await asyncio.sleep(1)
    
    def schedule_task(self, task_func: Callable, delay: float, *args, **kwargs) -> int:
        """زمان‌بندی یک تسک برای اجرای بعدی"""
        execution_time = time.time() + delay
        task_id = self.task_counter
        self.task_counter += 1
        
        heapq.heappush(self.scheduled_tasks, (execution_time, task_id, task_func, args, kwargs))
        return task_id
    
    def cancel_task(self, task_id: int):
        """لغو یک تسک زمان‌بندی شده"""
        self.scheduled_tasks = [task for task in self.scheduled_tasks if task[1] != task_id]
        heapq.heapify(self.scheduled_tasks)
    
    def stop(self):
        """توقف scheduler"""
        self.running = False

# ===== سیستم مدیریت تسک‌ها =====
class TaskManager:
    def __init__(self):
        self.scheduler = TaskScheduler()
        self.task_queue = deque()
        self.processing = False
    
    async def add_task(self, task_func: Callable, *args, **kwargs):
        """اضافه کردن تسک جدید"""
        self.task_queue.append((task_func, args, kwargs))
        await self._process_queue()
    
    async def _process_queue(self):
        """پردازش صف تسک‌ها"""
        if self.processing:
            return
        
        self.processing = True
        
        try:
            while self.task_queue:
                task_func, args, kwargs = self.task_queue.popleft()
                
                try:
                    if asyncio.iscoroutinefunction(task_func):
                        await task_func(*args, **kwargs)
                    else:
                        task_func(*args, **kwargs)
                    
                    await asyncio.sleep(random.uniform(2.0, 5.0))
                    
                except FloodWait as e:
                    wait_time = e.value + 10
                    logger.warning(f"🕒 FloodWait detected: {wait_time} seconds. Rescheduling task...")
                    
                    self.scheduler.schedule_task(
                        task_func, 
                        wait_time, 
                        *args, 
                        **kwargs
                    )
                    
                    user_id = kwargs.get('user_id')
                    if user_id:
                        await self._notify_user_floodwait(user_id, wait_time)
                    
                    await asyncio.sleep(5)
                    
                except Exception as e:
                    logger.error(f"Task error: {e}")
                    await asyncio.sleep(5)
        
        finally:
            self.processing = False
    
    async def _notify_user_floodwait(self, user_id: int, wait_time: int):
        """اطلاع به کاربر درباره FloodWait"""
        try:
            wait_minutes = wait_time // 60
            wait_seconds = wait_time % 60
            
            await self.safe_send_message(
                user_id,
                f"⏳ به دلیل محدودیت موقت تلگرام، عملیات متوقف شد.\n"
                f"🕒 زمان انتظار: {wait_minutes} دقیقه و {wait_seconds} ثانیه\n"
                f"✅ بعد از این زمان، عملیات به طور خودکار ادامه می‌یابد.",
                priority=True
            )
        except:
            pass
    
    async def safe_send_message(self, chat_id, text, reply_to_message_id=None, priority=False):
        """ارسال پیام با پشتیبانی از زمان‌بندی"""
        async def _send():
            await app.send_message(chat_id, text, reply_to_message_id=reply_to_message_id)
            await asyncio.sleep(1)
        
        if priority:
            try:
                await _send()
            except FloodWait as e:
                self.scheduler.schedule_task(_send, e.value + 5)
        else:
            await self.add_task(_send)

# ===== نمونه global =====
task_manager = TaskManager()

# ===== فانکشن‌های کمکی =====
def is_user_allowed(user_id: int) -> bool:
    return user_id == ALLOWED_USER_ID

async def safe_send_message(chat_id, text, reply_to_message_id=None, priority=False):
    """ارسال پیام با مدیریت FloodWait"""
    await task_manager.safe_send_message(chat_id, text, reply_to_message_id, priority)

async def safe_download_media(message, file_path, progress=None, progress_args=None):
    """دانلود با مدیریت FloodWait"""
    async def _download():
        await asyncio.sleep(random.uniform(2.0, 5.0))
        await app.download_media(message, file_path, progress=progress, progress_args=progress_args)
        return True
    
    try:
        return await _download()
    except FloodWait as e:
        task_manager.scheduler.schedule_task(_download, e.value + 10)
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
        
        await message.edit_text(text)
        await asyncio.sleep(1)
        
    except Exception as e:
        logger.error(f"Progress error: {e}")

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
            reply_to_message_id=message.id,
            priority=True
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
            reply_to_message_id=message.id,
            priority=True
        )
        return
    
    total_size = sum(f["file_size"] for f in user_files[user_id])
    if total_size > MAX_TOTAL_SIZE:
        await safe_send_message(
            message.chat.id,
            f"❌ حجم کل فایل‌ها بیش از حد مجاز است! ({MAX_TOTAL_SIZE//1024//1024}MB)",
            reply_to_message_id=message.id,
            priority=True
        )
        user_files[user_id] = []
        return
    
    user_states[user_id] = "waiting_password"
    
    await safe_send_message(
        message.chat.id,
        "🔐 لطفاً رمز عبور برای فایل زیپ وارد کن:\n❌ برای لغو /cancel را بزنید",
        reply_to_message_id=message.id,
        priority=True
    )

async def cancel_zip(client, message):
    user_id = message.from_user.id
    if user_id in user_files:
        user_files[user_id] = []
    
    user_states.pop(user_id, None)
    
    await safe_send_message(
        message.chat.id,
        "❌ عملیات لغو شد.",
        reply_to_message_id=message.id,
        priority=True
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
                reply_to_message_id=message.id,
                priority=True
            )
            return
        
        user_states[user_id] = "waiting_filename"
        user_states[f"{user_id}_password"] = zip_password
        
        await safe_send_message(
            message.chat.id,
            "📝 حالا اسم فایل زیپ نهایی را وارد کن (بدون .zip)",
            reply_to_message_id=message.id,
            priority=True
        )
        return
    
    if user_states.get(user_id) == "waiting_filename":
        zip_name = message.text.strip()
        if not zip_name:
            await safe_send_message(
                message.chat.id,
                "❌ اسم فایل نمی‌تواند خالی باشد.",
                reply_to_message_id=message.id,
                priority=True
            )
            return
        
        await process_zip_files(user_id, zip_name, message.chat.id, message.id)

async def process_zip_files(user_id, zip_name, chat_id, message_id):
    """پردازش هوشمندانه فایل‌ها با زیپ و آپلود تدریجی"""
    try:
        processing_msg = await app.send_message(chat_id, "⏳ در حال ایجاد فایل زیپ...")
        zip_password = user_states.get(f"{user_id}_password")
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            total_files = len(user_files[user_id])
            all_files = []
            
            # دانلود همه فایل‌ها
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
                    all_files.append((file_path, file_name))
                
                await asyncio.sleep(2)
            
            # ایجاد و آپلود پارت‌ها
            total_size = sum(os.path.getsize(f[0]) for f in all_files)
            num_parts = math.ceil(total_size / PART_SIZE)
            
            await processing_msg.edit_text(f"📦 در حال ایجاد {num_parts} پارت زیپ...")
            
            current_part = 1
            current_size = 0
            part_files = []
            current_zip_path = None
            
            for file_path, file_name in all_files:
                file_size = os.path.getsize(file_path)
                
                if current_size + file_size > PART_SIZE or current_zip_path is None:
                    if current_zip_path:
                        await upload_zip_part(
                            current_zip_path, 
                            current_part - 1, 
                            num_parts, 
                            chat_id, 
                            message_id, 
                            zip_password,
                            processing_msg
                        )
                    
                    current_zip_path = os.path.join(tmp_dir, f"{zip_name}_part{current_part}.zip")
                    part_files = []
                    current_size = 0
                    current_part += 1
                
                part_files.append((file_path, file_name))
                current_size += file_size
                
                with pyzipper.AESZipFile(current_zip_path, "w", 
                                       compression=pyzipper.ZIP_DEFLATED, 
                                       encryption=pyzipper.WZ_AES) as zipf:
                    if zip_password:
                        zipf.setpassword(zip_password.encode())
                    
                    for part_file_path, part_file_name in part_files:
                        zipf.write(part_file_path, part_file_name)
            
            if current_zip_path and os.path.exists(current_zip_path):
                await upload_zip_part(
                    current_zip_path, 
                    current_part - 1, 
                    num_parts, 
                    chat_id, 
                    message_id, 
                    zip_password,
                    processing_msg
                )
            
            await safe_send_message(
                chat_id,
                f"✅ تمامی {num_parts} پارت زیپ آماده شد!\n🔑 رمز: `{zip_password}`",
                reply_to_message_id=message_id,
                priority=True
            )
            
    except FloodWait as e:
        logger.warning(f"⏰ Rescheduling zip task after {e.value} seconds")
        
        task_manager.scheduler.schedule_task(
            lambda: process_zip_files(user_id, zip_name, chat_id, message_id),
            e.value + 15
        )
        
        await safe_send_message(
            chat_id,
            f"⏳ عملیات زیپ به دلیل محدودیت موقت متوقف شد.\n"
            f"🕒 ادامه کار بعد از {e.value} ثانیه...",
            reply_to_message_id=message_id,
            priority=True
        )
        
    except Exception as e:
        logger.error(f"Error in zip processing: {e}", exc_info=True)
        await safe_send_message(
            chat_id,
            "❌ خطایی در ایجاد فایل زیپ رخ داد.",
            reply_to_message_id=message_id,
            priority=True
        )
    finally:
        if user_id in user_files:
            user_files[user_id] = []
        user_states.pop(user_id, None)
        user_states.pop(f"{user_id}_password", None)

async def upload_zip_part(zip_path, part_number, total_parts, chat_id, message_id, password, processing_msg):
    """آپلود یک پارت زیپ"""
    try:
        part_size = os.path.getsize(zip_path)
        
        await processing_msg.edit_text(
            f"📤 در حال آپلود پارت {part_number + 1}/{total_parts}\n"
            f"📦 حجم: {part_size // 1024 // 1024}MB"
        )
        
        start_time = time.time()
        await app.send_document(
            chat_id,
            zip_path,
            caption=(
                f"📦 پارت {part_number + 1}/{total_parts}\n"
                f"🔑 رمز: `{password}`\n"
                f"💾 حجم: {part_size // 1024 // 1024}MB"
            ),
            progress=progress_bar,
            progress_args=(processing_msg, start_time, f"آپلود پارت {part_number + 1}"),
            reply_to_message_id=message_id
        )
        
        await asyncio.sleep(random.uniform(5.0, 10.0))
        
    except FloodWait as e:
        task_manager.scheduler.schedule_task(
            lambda: upload_zip_part(zip_path, part_number, total_parts, chat_id, message_id, password, processing_msg),
            e.value + 10
        )
        raise
    except Exception as e:
        logger.error(f"Error uploading part {part_number}: {e}")
        raise

# ===== فیلتر پیام‌های غیردستوری =====
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
    logger.info("Starting user bot with intelligent task scheduling...")
    
    app = Client(
        "user_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=SESSION_STRING,
        in_memory=True
    )
    
    # اضافه کردن هندلرها
    app.on_message(filters.command("start"))(start)
    app.on_message(filters.document)(handle_file)
    app.on_message(filters.command("zip"))(start_zip)
    app.on_message(filters.command("cancel"))(cancel_zip)
    app.on_message(filters.text & non_command)(process_zip)
    
    await app.start()
    logger.info("Bot started successfully with intelligent scheduling!")
    
    await asyncio.Event().wait()

# ===== اجرا =====
if __name__ == "__main__":
    web_app = Flask(__name__)
    
    @web_app.route('/')
    def home():
        return "Bot is running with intelligent scheduling", 200
    
    @web_app.route('/health')
    def health_check():
        return "Bot is running", 200
    
    def start_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_bot())
        except Exception as e:
            logger.error(f"Bot error: {e}")
    
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()
    
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"Starting Flask web server on port {port}...")
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
