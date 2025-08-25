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

# ===== تنظیمات =====
API_ID = 26180086
API_HASH = "d91e174c7faf0e5a6a3a2ecb0b3361f6"
SESSION_STRING = "BAGPefYAEAaXaj52wzDLPF0RSfWtF_Slk8nFWzYAHS9vu-HBxRUz9yLnq7m8z-ajYCQxQZO-5aNX0he9OttDjmjieYDMbDjBJtbsOT2ZwsQNe8UCAo5oFPveD5V1H0cIBMlXCG1P49G2oonf1YL1r16Nt34AJLkmzDIoFD0hhxwVBXvrUGwZmEoTtdkfORCYUMGACKO4-Al-NH35oVCkTIqmXQ5DUp9PVx6DND243VW5Xcqay7qwrwfoS4sWRA-7TMXykbHa37ZsdcCOf0VS8e6PyaYvG5BjMCd9BGRnR9IImrksYY2uBM2Bg42MLaa1WFxQtn97p5ViPF9c1MpY49bc5Gm5lwAAAAF--TK5AA"
ALLOWED_USER_ID = 417536686
MAX_FILE_SIZE = 2097152000  # 2GB
MAX_TOTAL_SIZE = 2097152000  # 2GB

# ===== لاگ =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ===== کلاینت Pyrogram =====
app = None

# ===== داده‌ها و صف =====
user_files = {}
user_states = {}
request_queue = deque()
is_processing = False
processing_lock = asyncio.Lock()

# ===== فانکشن‌های کمکی =====
def is_user_allowed(user_id: int) -> bool:
    return user_id == ALLOWED_USER_ID

async def safe_send_message(chat_id, text, reply_to_message_id=None):
    """ارسال پیام با مدیریت FloodWait"""
    try:
        await asyncio.sleep(random.uniform(1.0, 3.0))  # تاخیر تصادفی
        await app.send_message(chat_id, text, reply_to_message_id=reply_to_message_id)
        return True
    except FloodWait as e:
        logger.warning(f"FloodWait: {e.value} seconds")
        await asyncio.sleep(e.value + 5)
        await app.send_message(chat_id, text, reply_to_message_id=reply_to_message_id)
        return True
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return False

async def safe_download_media(message, file_path, progress=None, progress_args=None):
    """دانلود با مدیریت FloodWait"""
    try:
        await asyncio.sleep(random.uniform(2.0, 5.0))  # تاخیر قبل از دانلود
        await app.download_media(message, file_path, progress=progress, progress_args=progress_args)
        return True
    except FloodWait as e:
        logger.warning(f"Download FloodWait: {e.value} seconds")
        await asyncio.sleep(e.value + 10)
        await app.download_media(message, file_path, progress=progress, progress_args=progress_args)
        return True
    except Exception as e:
        logger.error(f"Error downloading: {e}")
        return False

async def progress_bar(current, total, message: Message, start_time, stage="دانلود"):
    """نوار پیشرفت با تاخیرهای کنترل شده"""
    try:
        now = time.time()
        diff = now - start_time
        if diff == 0: 
            diff = 1
        
        percent = int(current * 100 / total)
        
        # فقط هر 5% آپدیت شود تا پیام کمتری ارسال شود
        if percent % 5 != 0 and current != total:
            return
            
        speed = current / diff
        eta = int((total - current) / speed) if speed > 0 else 0
        bar_filled = int(percent / 5)
        bar = "▓" * bar_filled + "░" * (20 - bar_filled)
        
        text = f"""
🚀 {stage} فایل...

{bar} {percent}%

📦 {current//1024//1024}MB / {total//1024//1024}MB
⚡️ سرعت: {round(speed/1024,2)} KB/s
⏳ زمان باقی‌مانده: {eta}s
        """
        
        await message.edit_text(text)
        await asyncio.sleep(1)  # تاخیر بعد از آپدیت progress
        
    except Exception as e:
        logger.error(f"Progress error: {e}")

async def process_queue():
    """پردازش هوشمندانه صف درخواست‌ها"""
    global is_processing
    
    async with processing_lock:
        if is_processing:
            return
        is_processing = True
    
    try:
        while request_queue:
            task_func, args, kwargs = request_queue.popleft()
            
            try:
                await task_func(*args, **kwargs)
                # تاخیر بین پردازش درخواست‌ها
                await asyncio.sleep(random.uniform(3.0, 7.0))
                
            except FloodWait as e:
                # بازگرداندن تسک به صف
                request_queue.appendleft((task_func, args, kwargs))
                logger.warning(f"Queue FloodWait: sleeping {e.value + 10} seconds")
                await asyncio.sleep(e.value + 10)
                
            except Exception as e:
                logger.error(f"Queue task error: {e}")
                await asyncio.sleep(5)
    
    finally:
        is_processing = False

def add_to_queue(task_func, *args, **kwargs):
    """اضافه کردن تسک به صف"""
    request_queue.append((task_func, args, kwargs))
    asyncio.create_task(process_queue())

# ===== هندلرها =====
async def start(client, message):
    if not is_user_allowed(message.from_user.id):
        return
    
    add_to_queue(
        safe_send_message,
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
        add_to_queue(
            safe_send_message,
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
    
    add_to_queue(
        safe_send_message,
        message.chat.id,
        f"✅ فایل '{file_name}' ذخیره شد. برای شروع زیپ /zip را بزنید.",
        reply_to_message_id=message.id
    )

async def start_zip(client, message):
    if not is_user_allowed(message.from_user.id):
        return
    
    user_id = message.from_user.id
    if user_id not in user_files or not user_files[user_id]:
        add_to_queue(
            safe_send_message,
            message.chat.id,
            "❌ هیچ فایلی برای زیپ کردن وجود ندارد.",
            reply_to_message_id=message.id
        )
        return
    
    total_size = sum(f["file_size"] for f in user_files[user_id])
    if total_size > MAX_TOTAL_SIZE:
        add_to_queue(
            safe_send_message,
            message.chat.id,
            f"❌ حجم کل فایل‌ها بیش از حد مجاز است! ({MAX_TOTAL_SIZE//1024//1024}MB)",
            reply_to_message_id=message.id
        )
        user_files[user_id] = []
        return
    
    user_states[user_id] = "waiting_password"
    
    add_to_queue(
        safe_send_message,
        message.chat.id,
        "🔐 لطفاً رمز عبور برای فایل زیپ وارد کن:\n❌ برای لغو /cancel را بزنید",
        reply_to_message_id=message.id
    )

async def cancel_zip(client, message):
    user_id = message.from_user.id
    if user_id in user_files:
        user_files[user_id] = []
    
    user_states.pop(user_id, None)
    
    add_to_queue(
        safe_send_message,
        message.chat.id,
        "❌ عملیات لغو شد.",
        reply_to_message_id=message.id
    )

async def process_zip(client, message):
    user_id = message.from_user.id
    
    if user_id not in user_states:
        return
    
    await asyncio.sleep(1)  # تاخیر اولیه
    
    # مرحله پسورد
    if user_states.get(user_id) == "waiting_password":
        zip_password = message.text.strip()
        if not zip_password:
            add_to_queue(
                safe_send_message,
                message.chat.id,
                "❌ رمز عبور نمی‌تواند خالی باشد.",
                reply_to_message_id=message.id
            )
            return
        
        user_states[user_id] = "waiting_filename"
        user_states[f"{user_id}_password"] = zip_password
        
        add_to_queue(
            safe_send_message,
            message.chat.id,
            "📝 حالا اسم فایل زیپ نهایی را وارد کن (بدون .zip)",
            reply_to_message_id=message.id
        )
        return
    
    # مرحله اسم فایل
    if user_states.get(user_id) == "waiting_filename":
        zip_name = message.text.strip()
        if not zip_name:
            add_to_queue(
                safe_send_message,
                message.chat.id,
                "❌ اسم فایل نمی‌تواند خالی باشد.",
                reply_to_message_id=message.id
            )
            return
        
        # شروع پردازش زیپ
        await process_zip_files(user_id, zip_name, message.chat.id, message.id)

async def process_zip_files(user_id, zip_name, chat_id, message_id):
    """پردازش اصلی فایل‌های زیپ"""
    try:
        processing_msg = await app.send_message(chat_id, "⏳ در حال ایجاد فایل زیپ...")
        zip_password = user_states.get(f"{user_id}_password")
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_file_name = f"{zip_name}.zip"
            zip_path = os.path.join(tmp_dir, zip_file_name)
            
            with pyzipper.AESZipFile(zip_path, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zipf:
                if zip_password:
                    zipf.setpassword(zip_password.encode())
                
                total_files = len(user_files[user_id])
                for i, finfo in enumerate(user_files[user_id], 1):
                    file_msg = finfo["message"]
                    file_name = finfo["file_name"]
                    file_path = os.path.join(tmp_dir, file_name)
                    
                    start_time = time.time()
                    await safe_download_media(
                        file_msg,
                        file_path,
                        progress=progress_bar,
                        progress_args=(processing_msg, start_time, "دانلود")
                    )
                    
                    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                        zipf.write(file_path, file_name)
                    
                    os.remove(file_path)
                    await asyncio.sleep(2)  # تاخیر بین فایل‌ها
            
            # آپلود فایل زیپ
            start_time = time.time()
            await app.send_document(
                chat_id,
                zip_path,
                caption=f"✅ فایل زیپ آماده شد!\n🔑 رمز: `{zip_password}`\n📦 تعداد فایل‌ها: {total_files}",
                progress=progress_bar,
                progress_args=(processing_msg, start_time, "آپلود"),
                reply_to_message_id=message_id
            )
            
    except Exception as e:
        logger.error(f"Error in zip processing: {e}", exc_info=True)
        add_to_queue(
            safe_send_message,
            chat_id,
            "❌ خطایی در ایجاد فایل زیپ رخ داد.",
            reply_to_message_id=message_id
        )
    finally:
        # پاکسازی
        if user_id in user_files:
            user_files[user_id] = []
        user_states.pop(user_id, None)
        user_states.pop(f"{user_id}_password", None)

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
    logger.info("Starting user bot with flood protection...")
    
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
    logger.info("Bot started successfully with flood protection!")
    
    # منتظر ماندن تا ربات اجرا شود
    await asyncio.Event().wait()

# ===== اجرا =====
if __name__ == "__main__":
    # ایجاد وب سرور Flask
    web_app = Flask(__name__)
    
    @web_app.route('/')
    def home():
        return "Bot is running with flood protection", 200
    
    @web_app.route('/health')
    def health_check():
        return "Bot is running", 200
    
    # اجرای ربات در یک thread جداگانه
    def start_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_bot())
        except Exception as e:
            logger.error(f"Bot error: {e}")
    
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()
    
    # اجرای Flask در thread اصلی
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"Starting Flask web server on port {port}...")
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
