import os
import logging
import tempfile
import asyncio
import time
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeFilename
import pyzipper

# ===== تنظیمات اکانت کاربر =====
API_ID = 1867911
API_HASH = "f9e86b274826212a2712b18754fabc47"
SESSION_NAME = "user_session"
ALLOWED_USER_ID = 417536686
MAX_FILE_SIZE = 2097152000  # 2GB
MAX_TOTAL_SIZE = 4194304000  # 4GB برای کل فایل‌ها

# ===== تنظیمات لاگ =====
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# ===== متغیرهای全局 =====
user_files = {}
waiting_for_password = {}
processing_messages = {}

# ===== ایجاد کلاینت =====
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

def is_user_allowed(user_id: int) -> bool:
    """بررسی مجاز بودن کاربر"""
    return user_id == ALLOWED_USER_ID

def get_progress_bar(percent: int, length: int = 20):
    """نوار پیشرفت"""
    filled_length = int(length * percent // 100)
    bar = '■' * filled_length + '□' * (length - filled_length)
    return f"[{bar}] {percent}%"

# ===== دستور /start =====
@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    try:
        if not is_user_allowed(event.sender_id):
            await event.reply("❌ دسترسی denied.")
            return

        welcome_text = f"""
🤖 ربات ZipBot آماده است!

👤 کاربر: {event.sender.first_name}
🆔 آیدی: {event.sender_id}

📦 نحوه استفاده:
1. فایل‌های خود را ارسال کنید
2. پس از اتمام از دستور /zip استفاده کنید
3. رمز عبور را وارد کنید
4. فایل زیپ شده دریافت خواهد شد

💡 نکات:
• در caption فایل می‌توانید pass=رمز قرار دهید
• حداکثر حجم هر فایل: {MAX_FILE_SIZE // 1024 // 1024}MB
• حداکثر حجم کل: {MAX_TOTAL_SIZE // 1024 // 1024}MB

📋 دستورات:
/start - شروع ربات
/list - نمایش فایل‌های ذخیره شده
/zip - شروع فرآیند زیپ
/clear - پاک کردن فایل‌ها
/cancel - لغو عملیات
"""
        await event.reply(welcome_text)
        logger.info(f"User {event.sender_id} started the bot")
        
    except Exception as e:
        logger.error(f"Error in start: {e}")

# ===== مدیریت فایل‌ها =====
@client.on(events.NewMessage(func=lambda e: e.file and e.file.size > 0))
async def document_handler(event):
    try:
        if not is_user_allowed(event.sender_id):
            return

        # دریافت نام فایل
        file_name = "unknown_file"
        for attr in event.file.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                file_name = attr.file_name
                break

        # بررسی حجم فایل
        if event.file.size > MAX_FILE_SIZE:
            await event.reply(
                f"❌ حجم فایل بیش از حد مجاز است! (حداکثر {MAX_FILE_SIZE // 1024 // 1024}MB)"
            )
            return

        # دریافت رمز از caption (اگر وجود دارد)
        password = None
        if event.message.message and "pass=" in event.message.message:
            try:
                password = event.message.message.split("pass=")[1].split()[0].strip()
            except:
                pass

        # ذخیره اطلاعات فایل
        user_id = event.sender_id
        if user_id not in user_files:
            user_files[user_id] = []

        user_files[user_id].append({
            "message": event.message,
            "file_name": file_name,
            "password": password,
            "file_size": event.file.size
        })

        # محاسبه حجم کل
        total_size = sum(f["file_size"] for f in user_files[user_id])

        await event.reply(
            f"✅ فایل '{file_name}' ذخیره شد.\n"
            f"📦 حجم کل: {total_size//1024//1024}MB\n"
            f"📝 برای زیپ کردن /zip را بزنید"
        )
        logger.info(f"File {file_name} saved for user {user_id}")

    except Exception as e:
        logger.error(f"Error in document handler: {e}")
        await event.reply("❌ خطا در پردازش فایل.")

# ===== دستور /list =====
@client.on(events.NewMessage(pattern='/list'))
async def list_handler(event):
    try:
        if not is_user_allowed(event.sender_id):
            return

        user_id = event.sender_id
        if user_id not in user_files or not user_files[user_id]:
            await event.reply("📭 هیچ فایلی ذخیره نشده است.")
            return

        files_list = []
        total_size = 0
        
        for i, file_info in enumerate(user_files[user_id], 1):
            files_list.append(f"{i}. {file_info['file_name']} ({file_info['file_size']//1024//1024}MB)")
            total_size += file_info["file_size"]

        response = (
            f"📋 فایل‌های ذخیره شده:\n" +
            "\n".join(files_list) +
            f"\n\n📦 حجم کل: {total_size//1024//1024}MB\n"
            f"🔢 تعداد: {len(user_files[user_id])} فایل"
        )
        
        await event.reply(response)
        
    except Exception as e:
        logger.error(f"Error in list handler: {e}")

# ===== دستور /clear =====
@client.on(events.NewMessage(pattern='/clear'))
async def clear_handler(event):
    try:
        if not is_user_allowed(event.sender_id):
            return

        user_id = event.sender_id
        if user_id in user_files and user_files[user_id]:
            count = len(user_files[user_id])
            user_files[user_id] = []
            waiting_for_password.pop(user_id, None)
            await event.reply(f"✅ {count} فایل ذخیره شده پاک شدند.")
        else:
            await event.reply("📭 هیچ فایلی برای پاک کردن وجود ندارد.")
            
    except Exception as e:
        logger.error(f"Error in clear handler: {e}")

# ===== دستور /zip =====
@client.on(events.NewMessage(pattern='/zip'))
async def zip_handler(event):
    try:
        if not is_user_allowed(event.sender_id):
            return

        user_id = event.sender_id
        if user_id not in user_files or not user_files[user_id]:
            await event.reply("❌ هیچ فایلی برای زیپ کردن وجود ندارد.")
            return

        # بررسی حجم کل
        total_size = sum(f["file_size"] for f in user_files[user_id])
        if total_size > MAX_TOTAL_SIZE:
            await event.reply(
                f"❌ حجم کل فایل‌ها ({total_size//1024//1024}MB) بیش از حد مجاز است! "
                f"(حداکثر {MAX_TOTAL_SIZE//1024//1024}MB)"
            )
            user_files[user_id] = []
            return

        # درخواست رمز عبور
        await event.reply(
            "🔐 لطفاً رمز عبور برای فایل زیپ وارد کنید:\n"
            "❌ برای لغو /cancel را بزنید"
        )
        
        waiting_for_password[user_id] = True
        
    except Exception as e:
        logger.error(f"Error in zip handler: {e}")

# ===== دستور /cancel =====
@client.on(events.NewMessage(pattern='/cancel'))
async def cancel_handler(event):
    try:
        if not is_user_allowed(event.sender_id):
            return

        user_id = event.sender_id
        if user_id in user_files:
            user_files[user_id] = []
        if user_id in waiting_for_password:
            waiting_for_password.pop(user_id)
        
        await event.reply("❌ عملیات لغو شد.")
        
    except Exception as e:
        logger.error(f"Error in cancel handler: {e}")

# ===== پردازش رمز عبور =====
@client.on(events.NewMessage)
async def password_handler(event):
    try:
        if not is_user_allowed(event.sender_id):
            return

        user_id = event.sender_id
        
        # بررسی آیا منتظر رمز هستیم
        if user_id not in waiting_for_password or not waiting_for_password[user_id]:
            return

        if event.message.message.startswith('/'):
            return

        zip_password = event.message.message.strip()
        if not zip_password:
            await event.reply("❌ رمز عبور نمی‌تواند خالی باشد.")
            return

        # حذف فلگ انتظار برای رمز
        waiting_for_password.pop(user_id, None)

        processing_msg = await event.reply("⏳ در حال ایجاد فایل زیپ...")
        processing_messages[user_id] = processing_msg
        
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
                            file_msg = file_info["message"]
                            file_name = file_info["file_name"]
                            file_password = file_info["password"] or zip_password
                            
                            file_path = os.path.join(tmp_dir, file_name)
                            
                            # دانلود فایل
                            await processing_msg.edit(
                                f"📥 در حال دانلود: {file_name}\n"
                                f"📊 فایل {i} از {total_files}"
                            )
                            
                            # دانلود با Telethon
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
                                
                                await processing_msg.edit(
                                    f"✅ فایل '{file_name}' اضافه شد.\n"
                                    f"📊 پیشرفت کل: {i}/{total_files} فایل"
                                )
                            else:
                                logger.error(f"Download failed for file: {file_name}")
                                continue
                                
                        except Exception as e:
                            logger.error(f"Error processing file {file_name}: {e}")
                            await processing_msg.edit(
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
                        await event.reply("❌ هیچ فایلی موفقیت آمیز پردازش نشد.")
                        return

                    # آپلود فایل زیپ شده
                    await processing_msg.edit("📤 در حال ارسال فایل زیپ...")
                    
                    # آپلود با Telethon (استفاده از اکانت کاربر)
                    await client.send_file(
                        event.chat_id,
                        zip_path,
                        caption=(
                            f"✅ فایل زیپ آماده شد!\n"
                            f"🔐 رمز: {zip_password}\n"
                            f"📦 تعداد فایل‌های موفق: {successful_files}/{total_files}"
                        )
                    )
                    
                    logger.info(f"Zip file sent successfully to user {user_id}")

        except Exception as e:
            logger.error(f"Error in zip process: {e}", exc_info=True)
            await event.reply("❌ خطایی در پردازش فایل‌ها رخ داد.")
        
        finally:
            # پاک کردن فایل‌های ذخیره شده
            if user_id in user_files:
                user_files[user_id] = []
            
            # پاکسازی پیام پردازش
            if user_id in processing_messages:
                try:
                    await processing_messages[user_id].delete()
                except:
                    pass
                processing_messages.pop(user_id, None)
                
    except Exception as e:
        logger.error(f"Error in password handler: {e}")

# ===== اجرای ربات =====
async def main():
    """تابع اصلی اجرای ربات"""
    try:
        logger.info("🤖 Starting ZipBot with user account...")
        logger.info(f"👤 Allowed user: {ALLOWED_USER_ID}")
        logger.info(f"⚡ Max file size: {MAX_FILE_SIZE // 1024 // 1024}MB")
        
        await client.start()
        logger.info("✅ Client started successfully")
        
        me = await client.get_me()
        logger.info(f"🔗 Connected as: {me.first_name} (@{me.username})")
        
        # اطلاع رسانی به کاربر
        try:
            await client.send_message(
                ALLOWED_USER_ID, 
                "🤖 ربات ZipBot با اکانت کاربر راه‌اندازی شد!\n"
                "✅ آماده دریافت فایل‌ها هستم."
            )
        except Exception as e:
            logger.warning(f"Could not send startup message: {e}")
        
        # نگه داشتن ربات فعال
        await client.run_until_disconnected()
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}", exc_info=True)
    finally:
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
