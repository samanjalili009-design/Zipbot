import os
import time
import tempfile
import pyzipper
import logging
import sys
import asyncio
import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, RPCError
from flask import Flask
import threading
from collections import deque
import random
import math
from typing import Dict, List, Any, Optional
from pathlib import Path
import json
from datetime import datetime
import shutil
from concurrent.futures import ThreadPoolExecutor
import queue
import re
import gc

# ===== تنظیمات =====
class Config:
    API_ID = 1867911
    API_HASH = "f9e86b274826212a2712b18754fabc47"
    SESSION_STRING = "BAAcgIcAHFzRCBK8bXUoRyPGXLQaXCUVVc8YRwzBkm7m9RHERF-DwcIcuO1XunQeFNnVTsCgpeR4yfVY-qygIVL-ayKd4FXec1Q0AJSwwMztT_JNgRjlIupm9OaujoI68FlcNibGtCYEkktHPWlN7I8F5ux34MWBQbK3v6DIXfKyAza3yCksCwYI7YoZz7-Ay2d3XK2S_GDqcNW3DF-PGGc-ZAnpdPe11aDiX1vwpDjXm0pV0_Cw5GeHgLUm6LcZ1PwPLvIkUDhhGsR3cFYHHrxjS4SuD-cgb4Zjv9r7zBJ5HGaGnBPZKRW3OSxnv2DpnaJOoX_tbFAp0ZWNYOFTsIX6Nt55xgAAAAAY4xquAA"
    ALLOWED_USER_IDS = [417536686]
    
    MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
    MAX_TOTAL_SIZE = 4 * 1024 * 1024 * 1024  # 4GB
    MAX_FILES_COUNT = 5
    
    CHUNK_SIZE = 512 * 1024  # 512KB
    PROGRESS_INTERVAL = 10  # ثانیه بین آپدیت‌ها
    
    DATA_FILE = "user_data.json"
    ZIP_COMPRESSION_LEVEL = 0  # بدون فشرده‌سازی
    ZIP_CHUNK_SIZE = 1 * 1024 * 1024  # 1MB

# ===== لاگینگ =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ===== متغیرهای جهانی =====
app = None
user_files: Dict[int, List] = {}
user_states: Dict[int, Any] = {}
task_queue = deque()
zip_executor = ThreadPoolExecutor(max_workers=1)
progress_zip_queue = queue.Queue()

# ===== مدیریت پیشرفت =====
class Progress:
    def __init__(self):
        self.current = 0
        self.total = 0
        self.stage = ""
        self.message = None
        self.last_update = 0
    
    async def update(self, current: int, total: int):
        self.current = current
        self.total = total
        
        now = time.time()
        if now - self.last_update < Config.PROGRESS_INTERVAL:
            return
            
        self.last_update = now
        percent = (current / total) * 100 if total > 0 else 0
        
        if self.message:
            try:
                text = (
                    f"⏳ **{self.stage}**\n\n"
                    f"📊 `{self.format_size(current)} / {self.format_size(total)}`\n"
                    f"📈 `{percent:.1f}%`\n"
                    f"⚡ در حال پردازش..."
                )
                await self.message.edit_text(text, parse_mode=enums.ParseMode.MARKDOWN)
            except Exception as e:
                logger.error(f"Progress update error: {e}")
    
    @staticmethod
    def format_size(size_bytes: int) -> str:
        if size_bytes == 0:
            return "0B"
        size_names = ["B", "KB", "MB", "GB"]
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 1)
        return f"{s} {size_names[i]}"

progress = Progress()

# ===== توابع اصلی =====
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
        user_files = {}
        user_states = {}

def save_user_data():
    try:
        data = {
            'user_files': {str(k): v for k, v in user_files.items()},
            'user_states': {str(k): v for k, v in user_states.items()}
        }
        with open(Config.DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error saving user data: {e}")

async def send_message(chat_id, text, reply_id=None):
    try:
        return await app.send_message(
            chat_id, 
            text, 
            reply_to_message_id=reply_id,
            parse_mode=enums.ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return None

async def download_file(message, file_path):
    """دانلود فایل با پیشرفت"""
    try:
        progress.stage = "دانلود"
        progress.start_time = time.time()
        
        await app.download_media(
            message,
            file_name=file_path,
            progress=progress.update,
            block=False
        )
        
        return os.path.exists(file_path) and os.path.getsize(file_path) > 0
    except FloodWait as e:
        logger.warning(f"FloodWait: {e.value} seconds")
        await asyncio.sleep(e.value)
        return False
    except Exception as e:
        logger.error(f"Download error: {e}")
        return False

def create_zip_streaming(zip_path: str, files: List[Dict], password: Optional[str] = None) -> bool:
    """ایجاد زیپ با مدیریت حافظه"""
    try:
        total_size = sum(f['size'] for f in files)
        processed = 0
        
        with pyzipper.AESZipFile(
            zip_path,
            'w',
            compression=pyzipper.ZIP_STORED,
            encryption=pyzipper.WZ_AES if password else None
        ) as zipf:
            
            if password:
                zipf.setpassword(password.encode('utf-8'))
            
            for file_info in files:
                if not os.path.exists(file_info['path']):
                    continue
                    
                with open(file_info['path'], 'rb') as f:
                    with zipf.open(file_info['name'], 'w') as zf:
                        while True:
                            chunk = f.read(Config.ZIP_CHUNK_SIZE)
                            if not chunk:
                                break
                            zf.write(chunk)
                            processed += len(chunk)
                            
                            # آپدیت پیشرفت
                            if processed % (10 * 1024 * 1024) < Config.ZIP_CHUNK_SIZE:
                                try:
                                    progress_zip_queue.put_nowait((processed, total_size))
                                except:
                                    pass
                            
                            # پاک‌سازی حافظه
                            if processed % (50 * 1024 * 1024) < Config.ZIP_CHUNK_SIZE:
                                gc.collect()
        
        return True
    except Exception as e:
        logger.error(f"Zip creation error: {e}")
        return False

async def upload_file(file_path: str, chat_id: int, caption: str, reply_id: int):
    """آپلود فایل با پیشرفت"""
    try:
        progress.stage = "آپلود"
        progress.start_time = time.time()
        file_size = os.path.getsize(file_path)
        
        await app.send_document(
            chat_id,
            document=file_path,
            caption=caption,
            reply_to_message_id=reply_id,
            progress=progress.update,
            block=False
        )
        
        return True
    except FloodWait as e:
        logger.warning(f"Upload FloodWait: {e.value} seconds")
        await asyncio.sleep(e.value)
        return False
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return False

async def process_zip_task(user_id: int, chat_id: int, message_id: int):
    """پردازش اصلی زیپ"""
    temp_files = []
    processing_msg = None
    
    try:
        processing_msg = await send_message(chat_id, "🔄 **شروع پردازش...**")
        progress.message = processing_msg
        
        # دانلود فایل‌ها
        file_infos = []
        for file_data in user_files[user_id]:
            try:
                file_msg = await app.get_messages(chat_id, file_data["message_id"])
                if not file_msg:
                    continue
                    
                file_path = os.path.join(tempfile.gettempdir(), f"dl_{user_id}_{file_data['file_name']}")
                temp_files.append(file_path)
                
                if await download_file(file_msg, file_path):
                    file_infos.append({
                        'path': file_path,
                        'name': file_data['file_name'],
                        'size': os.path.getsize(file_path)
                    })
                
                await asyncio.sleep(1)
                gc.collect()
                
            except Exception as e:
                logger.error(f"Error processing file: {e}")
                continue
        
        if not file_infos:
            await processing_msg.edit_text("❌ **خطا در دانلود فایل‌ها**")
            return
        
        # ایجاد زیپ
        await processing_msg.edit_text("📦 **در حال ایجاد فایل زیپ...**")
        
        zip_name = f"archive_{int(time.time())}"
        zip_path = os.path.join(tempfile.gettempdir(), f"zip_{user_id}_{zip_name}.zip")
        temp_files.append(zip_path)
        
        # ایجاد زیپ در ترد جداگانه
        password = user_states.get(f"{user_id}_password")
        success = await asyncio.get_event_loop().run_in_executor(
            zip_executor,
            create_zip_streaming,
            zip_path, file_infos, password
        )
        
        if not success or not os.path.exists(zip_path):
            await processing_msg.edit_text("❌ **خطا در ایجاد فایل زیپ**")
            return
        
        # آپلود زیپ
        zip_size = os.path.getsize(zip_path)
        await processing_msg.edit_text(f"📤 **در حال آپلود فایل زیپ...**\n\n💾 حجم: `{progress.format_size(zip_size)}`")
        
        caption = (
            f"📦 **فایل زیپ شده**\n\n"
            f"🔑 رمز: `{password or 'بدون رمز'}`\n"
            f"💾 حجم: `{progress.format_size(zip_size)}`\n"
            f"📁 تعداد فایل: `{len(file_infos)}`"
        )
        
        upload_success = await upload_file(zip_path, chat_id, caption, message_id)
        
        if upload_success:
            await processing_msg.edit_text("✅ **پردازش با موفقیت完成!**")
        else:
            await processing_msg.edit_text("⚠️ **خطا در آپلود فایل زیپ**")
        
    except Exception as e:
        logger.error(f"Process error: {e}")
        if processing_msg:
            await processing_msg.edit_text("❌ **خطا در پردازش**")
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
        if f"{user_id}_password" in user_states:
            user_states.pop(f"{user_id}_password")
        
        save_user_data()
        gc.collect()

# ===== وب سرور =====
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return "🤖 Zip Bot is Running", 200

@web_app.route('/health')
def health():
    return {"status": "healthy", "timestamp": time.time()}, 200

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

async def keep_alive():
    """نگه داشتن ربات فعال"""
    while True:
        await asyncio.sleep(20 * 60)  # هر 20 دقیقه
        try:
            await app.send_message("me", "🤖 Bot is alive and ready")
        except:
            pass

async def zip_progress_updater():
    """آپدیت پیشرفت زیپ"""
    while True:
        try:
            current, total = progress_zip_queue.get_nowait()
            await progress.update(current, total)
        except queue.Empty:
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Progress updater error: {e}")
            await asyncio.sleep(1)

# ایجاد فیلتر سفارشی برای متن غیرکامند
def text_filter(_, __, message: Message):
    return message.text and not message.text.startswith('/')

custom_text_filter = filters.create(text_filter)

async def main():
    global app
    load_user_data()
    
    app = Client(
        "zip_bot",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        session_string=Config.SESSION_STRING,
        in_memory=True
    )
    
    # ثبت هندلرها بعد از مقداردهی app
    @app.on_message(filters.command("start"))
    async def start_handler(client, message: Message):
        if not is_user_allowed(message.from_user.id):
            return
        
        text = (
            "🤖 **ربات زیپ‌ساز حرفه‌ای**\n\n"
            "✨ **ویژگی‌ها:**\n"
            "• 📦 پشتیبانی از فایل‌های تا 2GB\n"
            "• 🔒 رمزگذاری AES-256\n"
            "• ⚡ پردازش سریع و پایدار\n\n"
            "📝 **روش استفاده:**\n"
            "1. فایل‌ها را ارسال کنید\n"
            "2. از /zip برای شروع استفاده کنید\n"
            "3. رمز عبور را وارد کنید\n"
            "4. منتظر بمانید تا پردازش完成 شود"
        )
        
        await send_message(message.chat.id, text, message.id)

    @app.on_message(filters.command("zip"))
    async def zip_handler(client, message: Message):
        if not is_user_allowed(message.from_user.id):
            return
        
        user_id = message.from_user.id
        if user_id not in user_files or not user_files[user_id]:
            await send_message(message.chat.id, "❌ **هیچ فایلی برای زیپ کردن وجود ندارد**", message.id)
            return
        
        user_states[user_id] = "waiting_password"
        await send_message(
            message.chat.id,
            "🔐 **لطفاً رمز عبور برای فایل زیپ وارد کنید:**\n\n"
            "📝 پس از وارد کردن رمز، پردازش شروع می‌شود\n"
            "⚡ برای فایل‌های بزرگ ممکن است زمان‌بر باشد",
            message.id
        )

    @app.on_message(custom_text_filter)
    async def password_handler(client, message: Message):
        if not is_user_allowed(message.from_user.id):
            return
        
        user_id = message.from_user.id
        if user_states.get(user_id) == "waiting_password":
            password = message.text.strip()
            if len(password) < 4:
                await send_message(message.chat.id, "❌ **رمز عبور باید حداقل 4 کاراکتر باشد**", message.id)
                return
            
            user_states[f"{user_id}_password"] = password
            user_states[user_id] = "processing"
            
            await send_message(message.chat.id, "✅ **رمز ذخیره شد! شروع پردازش...**", message.id)
            await process_zip_task(user_id, message.chat.id, message.id)

    @app.on_message(filters.document | filters.video)
    async def file_handler(client, message: Message):
        if not is_user_allowed(message.from_user.id):
            return
        
        file_obj = message.document or message.video
        file_size = file_obj.file_size
        file_name = getattr(file_obj, 'file_name', None) or f"file_{message.id}"
        
        if file_size > Config.MAX_FILE_SIZE:
            await send_message(
                message.chat.id,
                f"❌ **حجم فایل بیش از حد مجاز است!**\n\n"
                f"📦 حجم فایل: `{progress.format_size(file_size)}`\n"
                f"⚖️ حد مجاز: `{progress.format_size(Config.MAX_FILE_SIZE)}`",
                message.id
            )
            return
        
        user_id = message.from_user.id
        if user_id not in user_files:
            user_files[user_id] = []
        
        if len(user_files[user_id]) >= Config.MAX_FILES_COUNT:
            await send_message(
                message.chat.id,
                f"❌ **تعداد فایل‌ها بیش از حد مجاز است!**\n\n"
                f"📁 تعداد فعلی: `{len(user_files[user_id])}`\n"
                f"📊 حداکثر مجاز: `{Config.MAX_FILES_COUNT}`",
                message.id
            )
            return
        
        total_size = sum(f["file_size"] for f in user_files[user_id]) + file_size
        if total_size > Config.MAX_TOTAL_SIZE:
            await send_message(
                message.chat.id,
                f"❌ **حجم کل فایل‌ها بیش از حد مجاز است!**\n\n"
                f"📦 حجم کل: `{progress.format_size(total_size)}`\n"
                f"⚖️ حد مجاز: `{progress.format_size(Config.MAX_TOTAL_SIZE)}`",
                message.id
            )
            return
        
        user_files[user_id].append({
            "message_id": message.id,
            "file_name": file_name,
            "file_size": file_size
        })
        
        await send_message(
            message.chat.id,
            f"✅ **فایل ذخیره شد**\n\n"
            f"📝 نام: `{file_name}`\n"
            f"📦 حجم: `{progress.format_size(file_size)}`\n"
            f"📊 تعداد کل: `{len(user_files[user_id])}` فایل\n\n"
            f"📌 برای شروع از /zip استفاده کنید",
            message.id
        )
        
        save_user_data()

    await app.start()
    logger.info("✅ Zip Bot started successfully!")
    
    # راه‌اندازی وب سرور
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    # نگه داشتن ربات فعال
    asyncio.create_task(keep_alive())
    asyncio.create_task(zip_progress_updater())
    
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        save_user_data()
    except Exception as e:
        logger.error(f"Bot error: {e}")
        save_user_data()
