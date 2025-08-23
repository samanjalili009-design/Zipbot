import os
import asyncio
import tempfile
import time
import pyzipper
import logging
import sys
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded
from telegram import Update, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)

# ===== تنظیمات =====
BOT_TOKEN = "8145993181:AAFK7PeFs_9VsHqaP3iKagj9lWTNJXKpgjk"
API_ID = 1867911
API_HASH = "f9e86b274826212a2712b18754fabc47"
ALLOWED_USER_ID = 417536686
MAX_FILE_SIZE = 2097152000  # 2GB پیش‌فرض

# برای Pyrogram - استفاده از bot token به جای user account
USERBOT_SESSION = "bot"  # یا می‌توانید از session string استفاده کنید

WAITING_FOR_PASSWORD = 1

# تنظیمات لاگ برای Render
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

def is_user_allowed(user_id: int) -> bool:
    return user_id == ALLOWED_USER_ID

# ===== نوار پیشرفت =====
def get_progress_bar(percent: int, length: int = 20):
    filled_length = int(length * percent // 100)
    bar = '■' * filled_length + '□' * (length - filled_length)
    return f"[{bar}] {percent}%"

# ===== پیشرفت دانلود =====
async def progress_callback(current, total, msg, start_time, file_name):
    try:
        percent = int(current * 100 / total) if total > 0 else 0
        elapsed = time.time() - start_time
        speed = current / elapsed / 1024 if elapsed > 0 else 0
        
        bar = get_progress_bar(percent)
        await msg.edit_text(
            f"📂 فایل: {file_name}\n"
            f"📊 {bar} ({current//1024//1024}/{total//1024//1024} MB)\n"
            f"💾 سرعت: {int(speed)} KB/s"
        )
    except Exception as e:
        logger.warning(f"Could not update progress: {e}")

# ===== دستورات ربات =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام 👋\nفایل‌تو بفرست تا برات زیپ کنم (رمزدار هم میشه).\n"
        "💡 کپشن فایل = pass=رمز برای تعیین پسورد"
    )

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user_allowed(update.effective_user.id):
        return await update.message.reply_text("❌ دسترسی denied.")

    if not update.message.document:
        return await update.message.reply_text("فقط فایل بفرست 🌹")

    doc = update.message.document
    file_id = doc.file_id
    caption = update.message.caption or ""
    password = None
    if caption.startswith("pass="):
        password = caption.split("=", 1)[1].strip()

    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        return await update.message.reply_text(
            f"❌ حجم فایل بیش از حد مجاز است! (حداکثر {MAX_FILE_SIZE // 1024 // 1024}MB)"
        )

    if "files" not in context.user_data:
        context.user_data["files"] = []

    context.user_data["files"].append({
        "file_id": file_id, 
        "file_name": doc.file_name, 
        "password": password
    })
    
    await update.message.reply_text(
        f"✅ فایل '{doc.file_name}' ذخیره شد.\n📝 برای زیپ کردن همه فایل‌ها /zip را بزنید"
    )

async def ask_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user_allowed(update.effective_user.id):
        await update.message.reply_text("❌ دسترسی denied.")
        return ConversationHandler.END

    if "files" not in context.user_data or not context.user_data["files"]:
        await update.message.reply_text("❌ هیچ فایلی برای زیپ کردن وجود ندارد.")
        return ConversationHandler.END

    await update.message.reply_text(
        "🔐 لطفاً رمز عبور برای فایل زیپ وارد کن (اگر قبلاً روی فایل مشخص کردی، همون استفاده میشه):"
    )
    return WAITING_FOR_PASSWORD

async def zip_files_with_userbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_password = update.message.text.strip()
    if not user_password:
        await update.message.reply_text("❌ رمز عبور نمی‌تواند خالی باشد.")
        return WAITING_FOR_PASSWORD

    processing_msg = await update.message.reply_text("⏳ در حال ایجاد فایل زیپ...")
    
    userbot = None
    try:
        # استفاده از bot token برای Pyrogram - بدون نیاز به ورودی کاربر
        userbot = Client(
            "userbot_session", 
            api_id=API_ID, 
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,  # استفاده از bot token
            in_memory=True,
            no_updates=True
        )
        
        await userbot.start()
        logger.info("Userbot started successfully with bot token")
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_file_name = f"archive_{int(time.time())}.zip"
            zip_path = os.path.join(tmp_dir, zip_file_name)
            
            with pyzipper.AESZipFile(
                zip_path,
                "w",
                compression=pyzipper.ZIP_DEFLATED,
                encryption=pyzipper.WZ_AES,
            ) as zipf:
                zipf.setpassword(user_password.encode())

                total_files = len(context.user_data["files"])
                for i, f in enumerate(context.user_data["files"], 1):
                    try:
                        file_path = os.path.join(tmp_dir, f["file_name"])
                        start_time = time.time()
                        
                        # دانلود با استفاده از bot telegram (نه pyrogram)
                        file = await context.bot.get_file(f["file_id"])
                        await file.download_to_drive(file_path)
                        
                        # به روز رسانی وضعیت
                        percent_download = 100
                        bar_download = get_progress_bar(percent_download)
                        await processing_msg.edit_text(
                            f"📥 دانلود: {bar_download}\n"
                            f"📦 در حال اضافه کردن فایل: {f['file_name']}"
                        )

                        zip_password = f["password"] or user_password
                        if zip_password:
                            zipf.setpassword(zip_password.encode())

                        zipf.write(file_path, f["file_name"])
                        os.remove(file_path)  # پاک کردن فایل موقت

                        percent_total = int((i / total_files) * 100)
                        bar_total = get_progress_bar(percent_total)
                        
                        await processing_msg.edit_text(
                            f"📦 فایل '{f['file_name']}' اضافه شد.\n"
                            f"📊 پیشرفت کل: {bar_total} ({i}/{total_files} فایل)"
                        )
                        
                    except Exception as e:
                        logger.error(f"Error processing file {f['file_name']}: {e}")
                        continue

            # ارسال فایل زیپ شده
            await update.message.reply_document(
                InputFile(zip_path, filename=zip_file_name), 
                caption=f"✅ فایل زیپ آماده شد!\n🔐 رمز: {user_password}"
            )
            logger.info("Zip file sent successfully")

    except Exception as e:
        logger.error(f"Error in zip process: {e}", exc_info=True)
        await update.message.reply_text("❌ خطایی در پردازش فایل‌ها رخ داد.")
    
    finally:
        # مدیریت صحیح بستن userbot
        if userbot:
            try:
                await userbot.stop()
                logger.info("Userbot stopped successfully")
            except Exception as e:
                logger.warning(f"Error stopping userbot: {e}")

    # پاک کردن فایل‌های ذخیره شده
    if "files" in context.user_data:
        context.user_data["files"] = []
    
    try:
        await processing_msg.delete()
    except Exception as e:
        logger.warning(f"Could not delete processing message: {e}")

    return ConversationHandler.END

async def cancel_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ عملیات زیپ لغو شد.")
    return ConversationHandler.END

async def clear_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "files" in context.user_data and context.user_data["files"]:
        count = len(context.user_data["files"])
        context.user_data["files"] = []
        await update.message.reply_text(f"✅ {count} فایل ذخیره شده پاک شدند.")
    else:
        await update.message.reply_text("📭 هیچ فایلی برای پاک کردن وجود ندارد.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update:", exc_info=True)
    try:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ خطایی رخ داد. لطفاً دوباره تلاش کنید."
        )
    except:
        pass

# ===== ران اصلی =====
def main():
    try:
        app = Application.builder().token(BOT_TOKEN).build()
        
        # اضافه کردن error handler
        app.add_error_handler(error_handler)

        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("zip", ask_password)],
            states={
                WAITING_FOR_PASSWORD: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, zip_files_with_userbot)
                ]
            },
            fallbacks=[CommandHandler("cancel", cancel_zip)],
        )

        app.add_handler(conv_handler)
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
        app.add_handler(CommandHandler("clear", clear_files))

        logger.info("Bot is starting on Render with polling...")
        
        # استفاده از polling به جای webhook
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            close_loop=False
        )
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
