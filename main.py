import os
import zipfile
import asyncio
import logging
import pyzipper
from datetime import datetime
from telethon import TelegramClient, events
from telethon.tl.types import InputFileBig
from telethon.sessions import StringSession
from flask import Flask

# تنظیمات لاگ
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# اطلاعات API - از متغیرهای محیطی بخوان
API_ID = int(os.environ.get('API_ID', 26180086))
API_HASH = os.environ.get('API_HASH', "d91e174c7faf0e5a6a3a2ecb0b3361f6")
SESSION_STRING = os.environ.get('SESSION_STRING', "YOUR_SESSION_STRING")
ALLOWED_USER_IDS = [int(x) for x in os.environ.get('ALLOWED_USER_IDS', '417536686').split(',')]
ZIP_PASSWORD = os.environ.get('ZIP_PASSWORD', "DefaultPassword123!")
PORT = int(os.environ.get('PORT', 5000))

# ایجاد دایرکتوری موقت
TEMP_DIR = "temp_files"
os.makedirs(TEMP_DIR, exist_ok=True)

# ایجاد کلاینت تلگرام
client = TelegramClient(
    session=StringSession(SESSION_STRING),
    api_id=API_ID,
    api_hash=API_HASH
)

# ایجاد اپلیکیشن Flask برای render.com
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Telegram Zip Bot is Running!"

# دیکشنری برای ذخیره اطلاعات پیشرفت
progress_data = {}

def human_readable_size(size_bytes):
    """تبدیل حجم فایل به فرمت قابل خواندن"""
    if size_bytes == 0:
        return "0B"
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    i = 0
    while size_bytes >= 1024 and i < len(units) - 1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.2f} {units[i]}"

async def download_file_with_progress(event, file_path):
    """دانلود فایل با نمایش پیشرفت"""
    message = await event.reply("📥 در حال دانلود فایل... (0%)")
    
    user_id = event.sender_id
    progress_data[user_id] = {
        "downloaded": 0,
        "total_size": 0,
        "last_update": datetime.now(),
        "message": message
    }
    
    def progress_callback(current, total):
        now = datetime.now()
        if user_id in progress_data:
            # به روز رسانی فقط هر 2 ثانیه برای جلوگیری از اسپم
            if (now - progress_data[user_id]["last_update"]).total_seconds() >= 2:
                progress_data[user_id]["downloaded"] = current
                progress_data[user_id]["total_size"] = total
                progress_data[user_id]["last_update"] = now
                
                # محاسبه درصد
                percent = (current / total) * 100
                asyncio.create_task(
                    progress_data[user_id]["message"].edit(
                        f"📥 در حال دانلود فایل... ({percent:.1f}%)\n"
                        f"📊 {human_readable_size(current)} از {human_readable_size(total)}"
                    )
                )
    
    try:
        file = await event.message.download_media(
            file=file_path,
            progress_callback=progress_callback
        )
        
        # به روز رسانی نهایی
        if user_id in progress_data:
            await progress_data[user_id]["message"].edit("✅ دانلود فایل کامل شد!")
            del progress_data[user_id]
            
        return file
    except Exception as e:
        if user_id in progress_data:
            await progress_data[user_id]["message"].edit(f"❌ خطا در دانلود: {str(e)}")
            del progress_data[user_id]
        raise e

async def zip_file_with_password(input_path, output_path, password):
    """فشرده سازی فایل با پسورد"""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: _sync_zip_with_password(input_path, output_path, password)
    )

def _sync_zip_with_password(input_path, output_path, password):
    """تابع همزمان برای فشرده‌سازی با pyzipper"""
    with pyzipper.AESZipFile(
        output_path, 
        'w', 
        compression=pyzipper.ZIP_DEFLATED, 
        encryption=pyzipper.WZ_AES
    ) as zipf:
        zipf.setpassword(password.encode())
        zipf.write(input_path, os.path.basename(input_path))

async def upload_file_with_progress(event, file_path, caption=""):
    """آپلود فایل با نمایش پیشرفت"""
    user_id = event.sender_id
    message = await event.reply("📤 در حال آپلود فایل... (0%)")
    
    file_size = os.path.getsize(file_path)
    progress_data[user_id] = {
        "uploaded": 0,
        "total_size": file_size,
        "last_update": datetime.now(),
        "message": message
    }
    
    def progress_callback(current, total):
        now = datetime.now()
        if user_id in progress_data:
            # به روز رسانی فقط هر 2 ثانیه برای جلوگیری از اسپم
            if (now - progress_data[user_id]["last_update"]).total_seconds() >= 2:
                progress_data[user_id]["uploaded"] = current
                progress_data[user_id]["last_update"] = now
                
                # محاسبه درصد
                percent = (current / total) * 100
                asyncio.create_task(
                    progress_data[user_id]["message"].edit(
                        f"📤 در حال آپلود فایل... ({percent:.1f}%)\n"
                        f"📊 {human_readable_size(current)} از {human_readable_size(total)}"
                    )
                )
    
    try:
        # استفاده از InputFileBig برای فایل‌های بزرگ
        file = InputFileBig(
            file_path,
            filename=os.path.basename(file_path)
        )
        
        # ارسال فایل
        await client.send_file(
            event.chat_id,
            file,
            caption=caption,
            progress_callback=progress_callback,
            force_document=True
        )
        
        # به روز رسانی نهایی
        if user_id in progress_data:
            await progress_data[user_id]["message"].edit("✅ آپلود فایل کامل شد!")
            del progress_data[user_id]
            
    except Exception as e:
        if user_id in progress_data:
            await progress_data[user_id]["message"].edit(f"❌ خطا در آپلود: {str(e)}")
            del progress_data[user_id]
        raise e

@client.on(events.NewMessage(from_users=ALLOWED_USER_IDS))
async def handle_message(event):
    """مدیریت پیام‌های دریافتی"""
    if not event.message.file:
        await event.reply("لطفاً یک فایل ارسال کنید.")
        return
    
    user_id = event.sender_id
    try:
        # ایجاد پوشه مخصوص کاربر
        user_dir = os.path.join(TEMP_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        
        # دانلود فایل
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        original_file = os.path.join(user_dir, f"original_{timestamp}")
        downloaded_file = await download_file_with_progress(event, original_file)
        
        # فشرده سازی فایل
        zip_file = os.path.join(user_dir, f"compressed_{timestamp}.zip")
        await event.reply("🔒 در حال فشرده سازی فایل با پسورد...")
        
        await zip_file_with_password(downloaded_file, zip_file, ZIP_PASSWORD)
        
        await event.reply("✅ فشرده سازی کامل شد!")
        
        # آپلود فایل فشرده
        await upload_file_with_progress(
            event, 
            zip_file, 
            f"فایل فشرده شده با پسورد\nپسورد: {ZIP_PASSWORD}"
        )
        
        # پاک کردن فایل‌های موقت
        try:
            os.remove(downloaded_file)
            os.remove(zip_file)
        except:
            pass
            
    except Exception as e:
        logger.error(f"Error processing file: {e}")
        await event.reply(f"❌ خطا در پردازش فایل: {str(e)}")

@client.on(events.NewMessage(pattern='/start', from_users=ALLOWED_USER_IDS))
async def start_command(event):
    """دستور start"""
    await event.reply(
        "🤖 ربات فشرده‌ساز فایل فعال شد!\n\n"
        "فایلی را برای من ارسال کنید تا آن را با پسورد فشرده کرده و مجدداً ارسال کنم.\n\n"
        f"پسورد فعلی: {ZIP_PASSWORD}"
    )

async def run_bot():
    """اجرای ربات تلگرام"""
    await client.start()
    logger.info("🤖 ربات تلگرام شروع به کار کرد...")
    await client.run_until_disconnected()

def run_flask():
    """اجرای سرور Flask برای render.com"""
    app.run(host='0.0.0.0', port=PORT)

if __name__ == '__main__':
    # اجرای همزمان Flask و Telegram Bot
    import threading
    
    # اجرای Flask در یک thread جداگانه
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # اجرای ربات تلگرام در thread اصلی
    asyncio.run(run_bot())
