import os
import time
import tempfile
import pyzipper
import logging
import sys
import math
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from flask import Flask
import threading

# ===== تنظیمات =====
API_ID = 1867911
API_HASH = "f9e86b274826212a2712b18754fabc47"
SESSION_STRING = "BAAcgIcAgh6c-Xa01ljkm3Uhy9aG_I2jG2BeLbe6RZoA9nwrVW5se2DgNMOWKllp9RZC19-DT4I-fBTDXAcK280SdLjqAXxd96-_xLgpwdI_sV50FuEpN37UZbR3lX6lXDeipiwGwiXBD5UyMlid7RXw5LpYC200yjtQT7KZVRVs56mYR2fSCio4O9U9euUUxHyW7ATt92nfmsyaRXfb1g121Kp-kVx1ux95LqG7T8I6yWaH3Jy11rEY8KxJpO8WKknv2dciDerkY58PTykTIGoVlitOAVaxGo20lAd0ase5gX9WRvjixqoXr_BlgKpCcYgv-sOUW8mSRyPJCyE2FpP0P2ZgFQAAAAAY4xquAA"
ALLOWED_USER_ID = 417536686
MAX_FILE_SIZE = 2097152000  # 2GB
MAX_TOTAL_SIZE = 2097152000  # 2GB
MAX_SPLIT_SIZE = 1990000000  # 1.99GB

# ===== لاگ =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ===== Flask برای health check =====
web_app = Flask(__name__)

@web_app.route('/')
def health_check():
    return "Bot is running", 200

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ===== کلاینت =====
app = Client(
    "user_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    in_memory=True
)

# ===== داده‌ها =====
user_files = {}
waiting_for_password = {}
waiting_for_filename = {}
zip_password_storage = {}

# ===== فانکشن‌های کمکی =====
def is_user_allowed(user_id: int) -> bool:
    return user_id == ALLOWED_USER_ID

async def simple_progress(current, total, message: Message, stage="دانلود"):
    """پیشرفت ساده بدون محاسبات سنگین"""
    try:
        percent = int(current * 100 / total)
        text = f"🚀 {stage} فایل... {percent}% ({current//1024//1024}MB/{total//1024//1024}MB)"
        await message.edit_text(text)
    except:
        pass

async def split_large_file(file_path, max_size=MAX_SPLIT_SIZE):
    """تقسیم فایل به چند part"""
    part_files = []
    file_size = os.path.getsize(file_path)
    
    if file_size <= max_size:
        return [file_path]
    
    num_parts = math.ceil(file_size / max_size)
    base_name = os.path.basename(file_path)
    
    with open(file_path, 'rb') as f:
        part_num = 1
        while True:
            chunk = f.read(max_size)
            if not chunk:
                break
                
            part_filename = f"{base_name}.part{part_num:03d}"
            part_path = os.path.join(os.path.dirname(file_path), part_filename)
            
            with open(part_path, 'wb') as part_file:
                part_file.write(chunk)
            
            part_files.append(part_path)
            part_num += 1
    
    os.remove(file_path)
    return part_files

async def create_split_zip(files, zip_path, password, processing_msg):
    """ایجاد زیپ تقسیم شده"""
    try:
        with pyzipper.AESZipFile(zip_path, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zipf:
            zipf.setpassword(password.encode())
            
            total_files = len(files)
            for i, file_info in enumerate(files, 1):
                file_path = file_info["path"]
                file_name = file_info["name"]
                
                # آپدیت وضعیت
                if i % 2 == 0 or i == total_files:  # فقط هر چند فایل یکبار آپدیت کنیم
                    progress_text = f"⏳ در حال فشرده سازی... {i}/{total_files}"
                    try: 
                        await processing_msg.edit_text(progress_text)
                    except: 
                        pass
                
                if os.path.getsize(file_path) > MAX_SPLIT_SIZE:
                    parts = await split_large_file(file_path)
                    for part_path in parts:
                        part_name = os.path.basename(part_path)
                        zipf.write(part_path, part_name)
                        os.remove(part_path)
                else:
                    zipf.write(file_path, file_name)
                    os.remove(file_path)
                
        return True
    except Exception as e:
        logger.error(f"Error creating split zip: {e}")
        return False

# ===== فیلتر برای تشخیص پیام‌های غیر دستوری =====
def non_command_filter(_, __, message):
    return message.text and not message.text.startswith('/')

non_command = filters.create(non_command_filter)

# ===== هندلرها =====
@app.on_message(filters.command("start"))
async def start(client, message):
    if not is_user_allowed(message.from_user.id):
        return await message.reply_text("❌ دسترسی denied.")
    await message.reply_text(
        "سلام 👋\nفایل‌تو بفرست تا برات زیپ کنم.\n"
        "💡 کپشن فایل = pass=رمز برای تعیین پسورد (اختیاری)\n"
        f"📦 حداکثر حجم هر فایل: {MAX_FILE_SIZE//1024//1024}MB\n"
        f"📦 حداکثر حجم کل: {MAX_TOTAL_SIZE//1024//1024}MB\n"
        "🔧 فایل‌های بزرگتر از 2GB به صورت خودکار تقسیم می‌شوند\n"
        "بعد از ارسال فایل‌ها دستور /zip رو بزن تا ابتدا پسورد و سپس اسم فایل نهایی را وارد کنی."
    )

@app.on_message(filters.document)
async def handle_file(client, message):
    if not is_user_allowed(message.from_user.id):
        return
    doc = message.document
    if not doc:
        return
    file_name = doc.file_name or f"file_{message.id}"
    caption = message.caption or ""
    password = None
    if "pass=" in caption:
        password = caption.split("pass=",1)[1].split()[0].strip()
    
    user_id = message.from_user.id
    if user_id not in user_files: 
        user_files[user_id] = []
    user_files[user_id].append({
        "message": message, 
        "file_name": file_name, 
        "password": password, 
        "file_size": doc.file_size
    })

@app.on_message(filters.command("zip"))
async def start_zip(client, message):
    if not is_user_allowed(message.from_user.id): 
        return
    user_id = message.from_user.id
    if user_id not in user_files or not user_files[user_id]:
        return await message.reply_text("❌ هیچ فایلی برای زیپ کردن وجود ندارد.")
    
    await message.reply_text("🔐 لطفاً رمز عبور برای فایل زیپ وارد کن:\n❌ برای لغو /cancel را بزنید")
    waiting_for_password[user_id] = True

@app.on_message(filters.command("cancel"))
async def cancel_zip(client, message):
    user_id = message.from_user.id
    if user_id in user_files: 
        user_files[user_id] = []
    waiting_for_password.pop(user_id, None)
    waiting_for_filename.pop(user_id, None)
    zip_password_storage.pop(user_id, None)
    await message.reply_text("❌ عملیات لغو شد.")

@app.on_message(filters.text & non_command)
async def process_zip(client, message):
    user_id = message.from_user.id
    
    if user_id in waiting_for_password and waiting_for_password[user_id]:
        zip_password = message.text.strip()
        if not zip_password:
            return await message.reply_text("❌ رمز عبور نمی‌تواند خالی باشد.")
        zip_password_storage[user_id] = zip_password
        waiting_for_password[user_id] = False
        waiting_for_filename[user_id] = True
        return await message.reply_text("📝 حالا اسم فایل زیپ نهایی را وارد کن (بدون .zip)")
    
    if user_id in waiting_for_filename and waiting_for_filename[user_id]:
        zip_name = message.text.strip()
        if not zip_name:
            return await message.reply_text("❌ اسم فایل نمی‌تواند خالی باشد.")
        waiting_for_filename.pop(user_id, None)
        
        try:
            processing_msg = await message.reply_text("⏳ در حال دانلود فایل‌ها...")
            zip_password = zip_password_storage.pop(user_id, None)
            
            with tempfile.TemporaryDirectory() as tmp_dir:
                files_to_zip = []
                total_files = len(user_files[user_id])
                
                # دانلود سریع بدون progress bar
                for i, finfo in enumerate(user_files[user_id], 1):
                    file_msg = finfo["message"]
                    file_name = finfo["file_name"]
                    file_path = os.path.join(tmp_dir, file_name)
                    
                    # دانلود بدون progress برای سرعت بیشتر
                    await client.download_media(file_msg, file_path)
                    
                    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                        files_to_zip.append({"path": file_path, "name": file_name})
                    
                    # آپدیت وضعیت هر چند فایل یکبار
                    if i % 2 == 0 or i == total_files:
                        await processing_msg.edit_text(f"📥 دانلود شده: {i}/{total_files}")
                
                # ایجاد زیپ
                await processing_msg.edit_text("⏳ در حال ایجاد فایل زیپ...")
                zip_file_name = f"{zip_name}.zip"
                zip_path = os.path.join(tmp_dir, zip_file_name)
                
                success = await create_split_zip(files_to_zip, zip_path, zip_password, processing_msg)
                
                if success and os.path.exists(zip_path):
                    # آپلود سریع بدون progress
                    await processing_msg.edit_text("⏳ در حال آپلود فایل زیپ...")
                    await client.send_document(
                        message.chat.id,
                        zip_path,
                        caption=f"✅ فایل زیپ آماده شد!\n🔑 رمز: `{zip_password}`\n📦 تعداد فایل‌ها: {total_files}"
                    )
                    await processing_msg.delete()
                    await message.reply_text("✅ عملیات با موفقیت完成 شد!")
                else:
                    await message.reply_text("❌ خطایی در ایجاد فایل زیپ رخ داد.")
                    
        except Exception as e:
            logger.error(f"Error in zip process: {e}", exc_info=True)
            await message.reply_text(f"❌ خطایی رخ داد: {str(e)}")
        finally:
            user_files[user_id] = []

# ===== اجرا =====
if __name__ == "__main__":
    logger.info("Starting user bot...")
    
    # راه اندازی Flask در thread جداگانه
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    app.run()
