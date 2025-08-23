import os
import asyncio
import tempfile
import time
import pyzipper
import logging
import sys
from pyrogram import Client, filters, idle
from pyrogram.types import Message
from pyrogram.errors import RPCError

# ===== تنظیمات =====
API_ID = 1867911
API_HASH = "f9e86b274826212a2712b18754fabc47"
SESSION_STRING = "BAAcgIcAp7vwU3nnTi-xRZN3D_0rGdAPZN1qv1Pedm9p6zcuDZk_5zYJaTdpnsiobnWymDG28cvHU09pjJiSwTK1lCV98QUyPg9sjUyTQTmbIMRBCxuc-eJLYNKq4TBqrvvqbTbELSMkTyAwbPr36vB2b3WyYZPXqRzZfGjbYPiHJMnIz6TRZ6PKwGxEIj4PBK6hZ1DckYbmEm1Z-LFny8NQdpZ3mDsQzSVyxOrdZHZjFhcBfRnjA3GkAg5kLCCOhbUTY9xvLhS9XrEaEfm2CBxVFkZGwSu-tK0neYa2L0mNIT00PV3FD9-KzWo3uZSxnuaFKiM3w3cE1ymgKcGBa_0e6VJp1QAAAAAY4xquAA"  # این رو با session جدید جایگزین کن
ALLOWED_USER_ID = 417536686
MAX_FILE_SIZE = 2097152000  # 2GB
MAX_TOTAL_SIZE = 2097152000  # 2GB برای کل فایل‌ها

# تنظیمات لاگ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# دیکشنری برای ذخیره فایل‌های کاربران
user_files = {}
waiting_for_password = {}

def is_user_allowed(user_id: int) -> bool:
    return user_id == ALLOWED_USER_ID

def get_progress_bar(percent: int, length: int = 20):
    filled_length = int(length * percent // 100)
    bar = '■' * filled_length + '□' * (length - filled_length)
    return f"[{bar}] {percent}%"

async def send_progress(message: Message, current: int, total: int, file_name: str, operation: str):
    try:
        percent = int(current * 100 / total) if total > 0 else 0
        bar = get_progress_bar(percent)
        
        text = (
            f"📂 فایل: {file_name}\n"
            f"📊 {bar} ({current//1024//1024}/{total//1024//1024} MB)\n"
            f"🔄 عملیات: {operation}"
        )
        
        if hasattr(send_progress, 'last_message'):
            try:
                await send_progress.last_message.edit_text(text)
            except:
                send_progress.last_message = await message.reply_text(text)
        else:
            send_progress.last_message = await message.reply_text(text)
            
    except Exception as e:
        logger.warning(f"Could not update progress: {e}")

# فیلتر برای تشخیص متن غیر از دستورات
def non_command_filter(_, __, message: Message):
    if not message.text:
        return False
    text = message.text.strip()
    return not text.startswith('/')

non_command = filters.create(non_command_filter)

# دستور استارت
@Client.on_message(filters.command("start"))
async def start(client: Client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return await message.reply_text("❌ دسترسی denied.")
    
    await message.reply_text(
        "سلام 👋\nفایل‌تو بفرست تا برات زیپ کنم (رمزدار هم میشه).\n"
        "💡 کپشن فایل = pass=رمز برای تعیین پسورد\n"
        f"📦 حداکثر حجم هر فایل: {MAX_FILE_SIZE // 1024 // 1024}MB\n"
        f"📦 حداکثر حجم کل: {MAX_TOTAL_SIZE // 1024 // 1024}MB"
    )

# مدیریت فایل‌ها
@Client.on_message(filters.document)
async def handle_file(client: Client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return await message.reply_text("❌ دسترسی denied.")

    doc = message.document
    if not doc:
        return await message.reply_text("فقط فایل بفرست 🌹")

    file_name = doc.file_name or f"file_{message.id}"
    caption = message.caption or ""
    password = None
    
    if caption and "pass=" in caption:
        password = caption.split("pass=", 1)[1].split()[0].strip()

    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        return await message.reply_text(
            f"❌ حجم فایل بیش از حد مجاز است! (حداکثر {MAX_FILE_SIZE // 1024 // 1024}MB)"
        )

    user_id = message.from_user.id
    if user_id not in user_files:
        user_files[user_id] = []

    user_files[user_id].append({
        "message": message,
        "file_name": file_name,
        "password": password,
        "file_size": doc.file_size
    })
    
    total_size = sum(f["file_size"] for f in user_files[user_id])
    
    await message.reply_text(
        f"✅ فایل '{file_name}' ذخیره شد.\n"
        f"📦 حجم کل: {total_size//1024//1024}MB\n"
        f"📝 برای زیپ کردن همه فایل‌ها /zip را بزنید"
    )

# لیست فایل‌ها
@Client.on_message(filters.command("list"))
async def list_files(client: Client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return await message.reply_text("❌ دسترسی denied.")

    user_id = message.from_user.id
    if user_id not in user_files or not user_files[user_id]:
        return await message.reply_text("📭 هیچ فایلی ذخیره نشده است.")

    files_list = "\n".join([
        f"📄 {f['file_name']} ({f['file_size']//1024//1024}MB)" 
        for f in user_files[user_id]
    ])
    
    total_size = sum(f["file_size"] for f in user_files[user_id])
    
    await message.reply_text(
        f"📋 فایل‌های ذخیره شده:\n{files_list}\n\n"
        f"📦 حجم کل: {total_size//1024//1024}MB\n"
        f"🔢 تعداد: {len(user_files[user_id])} فایل"
    )

# پاک کردن فایل‌ها
@Client.on_message(filters.command("clear"))
async def clear_files(client: Client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return await message.reply_text("❌ دسترسی denied.")

    user_id = message.from_user.id
    if user_id in user_files and user_files[user_id]:
        count = len(user_files[user_id])
        user_files[user_id] = []
        waiting_for_password.pop(user_id, None)
        await message.reply_text(f"✅ {count} فایل ذخیره شده پاک شدند.")
    else:
        await message.reply_text("📭 هیچ فایلی برای پاک کردن وجود ندارد.")

# شروع فرآیند زیپ
@Client.on_message(filters.command("zip"))
async def start_zip(client: Client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return await message.reply_text("❌ دسترسی denied.")

    user_id = message.from_user.id
    if user_id not in user_files or not user_files[user_id]:
        return await message.reply_text("❌ هیچ فایلی برای زیپ کردن وجود ندارد.")

    # بررسی حجم کل
    total_size = sum(f["file_size"] for f in user_files[user_id])
    if total_size > MAX_TOTAL_SIZE:
        await message.reply_text(
            f"❌ حجم کل فایل‌ها ({total_size//1024//1024}MB) بیش از حد مجاز است! "
            f"(حداکثر {MAX_TOTAL_SIZE//1024//1024}MB)"
        )
        user_files[user_id] = []
        return

    # درخواست رمز عبور
    await message.reply_text(
        "🔐 لطفاً رمز عبور برای فایل زیپ وارد کن (اگر قبلاً روی فایل مشخص کردی، همون استفاده میشه):\n"
        "❌ برای لغو /cancel را بزنید"
    )
    
    # علامت‌گذاری برای انتظار رمز
    waiting_for_password[user_id] = True

# لغو عملیات
@Client.on_message(filters.command("cancel"))
async def cancel_zip(client: Client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return await message.reply_text("❌ دسترسی denied.")

    user_id = message.from_user.id
    if user_id in user_files:
        user_files[user_id] = []
    if user_id in waiting_for_password:
        waiting_for_password.pop(user_id)
    
    await message.reply_text("❌ عملیات لغو شد.")

# پردازش زیپ
@Client.on_message(filters.text & non_command)
async def process_zip_password(client: Client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return

    user_id = message.from_user.id
    
    # بررسی آیا منتظر رمز هستیم
    if user_id not in waiting_for_password or not waiting_for_password[user_id]:
        return

    zip_password = message.text.strip()
    if not zip_password:
        return await message.reply_text("❌ رمز عبور نمی‌تواند خالی باشد.")

    # حذف فلگ انتظار برای رمز
    waiting_for_password.pop(user_id, None)

    processing_msg = await message.reply_text("⏳ در حال ایجاد فایل زیپ...")
    
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_file_name = f"archive_{int(time.time())}.zip"
            zip_path = os.path.join(tmp_dir, zip_file_name)
            
            with pyzipper.AESZipFile(
                zip_path,
                "w",
                compression=pyzipper.ZIP_DEFLATED,
                encryption=pyzipper.WZ_AES,
            ) as zipf:
                zipf.setpassword(zip_password.encode())

                total_files = len(user_files[user_id])
                successful_files = 0
                
                for i, file_info in enumerate(user_files[user_id], 1):
                    try:
                        if "message" not in file_info:
                            continue
                            
                        file_msg = file_info["message"]
                        file_name = file_info["file_name"]
                        file_password = file_info["password"] or zip_password
                        
                        file_path = os.path.join(tmp_dir, file_name)
                        
                        # دانلود فایل
                        await processing_msg.edit_text(f"📥 در حال دانلود: {file_name}\n📊 فایل {i} از {total_files}")
                        
                        await client.download_media(
                            file_msg,
                            file_path
                        )
                        
                        # افزودن به زیپ
                        if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                            if file_password:
                                zipf.setpassword(file_password.encode())
                            
                            zipf.write(file_path, file_name)
                            successful_files += 1
                            
                            await processing_msg.edit_text(
                                f"✅ فایل '{file_name}' اضافه شد.\n"
                                f"📊 پیشرفت کل: {i}/{total_files} فایل"
                            )
                        else:
                            logger.error(f"Download failed for file: {file_name}")
                            continue
                            
                    except Exception as e:
                        logger.error(f"Error processing file {file_name}: {e}")
                        await processing_msg.edit_text(
                            f"❌ خطا در پردازش فایل: {file_name}\n"
                            f"📊 ادامه پردازش فایل‌های دیگر..."
                        )
                        continue
                    finally:
                        # پاک کردن فایل موقت
                        try:
                            if os.path.exists(file_path):
                                os.remove(file_path)
                        except:
                            pass

                if successful_files == 0:
                    await message.reply_text("❌ هیچ فایلی موفقیت آمیز پردازش نشد.")
                    return

                # آپلود فایل زیپ شده
                await processing_msg.edit_text("📤 در حال ارسال فایل زیپ...")
                
                await client.send_document(
                    message.chat.id,
                    zip_path,
                    caption=(
                        f"✅ فایل زیپ آماده شد!\n"
                        f"🔐 رمز: {zip_password}\n"
                        f"📦 تعداد فایل‌های موفق: {successful_files}/{total_files}"
                    )
                )
                
                logger.info("Zip file sent successfully")

    except Exception as e:
        logger.error(f"Error in zip process: {e}", exc_info=True)
        await message.reply_text("❌ خطایی در پردازش فایل‌ها رخ داد.")
    
    finally:
        # پاک کردن فایل‌های ذخیره شده
        if user_id in user_files:
            user_files[user_id] = []

async def main():
    """تابع اصلی برای راه‌اندازی کلاینت"""
    try:
        app = Client(
            "user_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=SESSION_STRING,
            in_memory=True
        )
        
        logger.info("Starting user bot...")
        await app.start()
        
        me = await app.get_me()
        logger.info(f"Logged in as: {me.first_name} (@{me.username})")
        
        # نگه داشتن بات فعال
        await idle()
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}", exc_info=True)
    finally:
        if 'app' in locals():
            await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
