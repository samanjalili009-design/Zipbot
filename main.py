import os
import logging
import zipfile
import tempfile
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# دریافت تنظیمات از Environment Variables
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '8145993181:AAFK7PeFs_9VsHqaP3iKagj9lWTNJXKpgjk')
ACCOUNT_HASH = os.environ.get('ACCOUNT_HASH', 'f9e86b274826212a2712b18754fabc47')
ALLOWED_USER_ID = int(os.environ.get('ALLOWED_USER_ID', '1867911'))
MAX_FILE_SIZE = int(os.environ.get('MAX_FILE_SIZE', '2097152000'))

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

def is_user_allowed(user_id: int) -> bool:
    """بررسی مجاز بودن کاربر"""
    return user_id == ALLOWED_USER_ID

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور شروع"""
    try:
        if not is_user_allowed(update.effective_user.id):
            await update.message.reply_text("❌ دسترسی denied.")
            return
        
        welcome_text = f"""
🤖 ربات ZipBot آماده است!

👤 کاربر: {update.effective_user.first_name}
🆔 آیدی: {update.effective_user.id}

📦 نحوه استفاده:
1. فایل‌های خود را ارسال کنید
2. پس از اتمام از دستور /zip استفاده کنید
3. فایل زیپ شده دریافت خواهد شد

⚡ حداکثر حجم: {MAX_FILE_SIZE // 1024 // 1024}MB
"""
        await update.message.reply_text(welcome_text)
        logger.info(f"User {update.effective_user.id} started the bot")
        
    except Exception as e:
        logger.error(f"Error in start command: {e}")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت دریافت فایل‌ها"""
    try:
        if not is_user_allowed(update.effective_user.id):
            return
        
        document = update.message.document
        
        # بررسی حجم فایل
        if document.file_size and document.file_size > MAX_FILE_SIZE:
            await update.message.reply_text(
                f"❌ حجم فایل بیش از حد مجاز است! (حداکثر: {MAX_FILE_SIZE // 1024 // 1024}MB)"
            )
            return
        
        # ذخیره اطلاعات فایل در context
        if 'files' not in context.user_data:
            context.user_data['files'] = []
        
        file_name = document.file_name or f"file_{len(context.user_data['files']) + 1}"
        
        context.user_data['files'].append({
            'file_id': document.file_id,
            'file_name': file_name,
            'file_size': document.file_size or 0,
            'mime_type': document.mime_type
        })
        
        total_files = len(context.user_data['files'])
        total_size = sum(f['file_size'] for f in context.user_data['files'])
        
        await update.message.reply_text(
            f"✅ فایل '{file_name}' ذخیره شد.\n"
            f"📊 تعداد فایل‌ها: {total_files}\n"
            f"💾 حجم کل: {total_size // 1024 // 1024}MB"
        )
        
        logger.info(f"File received: {file_name}, size: {document.file_size}")
        
    except Exception as e:
        logger.error(f"Error handling document: {e}")
        await update.message.reply_text("❌ خطا در دریافت فایل")

async def list_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش لیست فایل‌های ذخیره شده"""
    try:
        if not is_user_allowed(update.effective_user.id):
            return
        
        if 'files' not in context.user_data or not context.user_data['files']:
            await update.message.reply_text("📭 هیچ فایلی ذخیره نشده است.")
            return
        
        files_list = []
        total_size = 0
        
        for i, file_info in enumerate(context.user_data['files'], 1):
            size_kb = file_info['file_size'] // 1024 if file_info['file_size'] else 0
            files_list.append(f"{i}. {file_info['file_name']} ({size_kb}KB)")
            total_size += file_info['file_size'] or 0
        
        message = (
            "📋 فایل‌های ذخیره شده:\n" +
            "\n".join(files_list) +
            f"\n\n📊 تعداد: {len(context.user_data['files'])} فایل" +
            f"\n💾 حجم کل: {total_size // 1024 // 1024}MB"
        )
        
        await update.message.reply_text(message)
        logger.info(f"Listed {len(context.user_data['files'])} files")
        
    except Exception as e:
        logger.error(f"Error listing files: {e}")

async def zip_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زیپ کردن فایل‌ها و ارسال"""
    try:
        if not is_user_allowed(update.effective_user.id):
            await update.message.reply_text("❌ دسترسی denied.")
            return
        
        if 'files' not in context.user_data or not context.user_data['files']:
            await update.message.reply_text("❌ هیچ فایلی برای زیپ کردن وجود ندارد.")
            return
        
        processing_msg = await update.message.reply_text("⏳ در حال پردازش فایل‌ها...")
        total_files = len(context.user_data['files'])
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = os.path.join(tmp_dir, "archive.zip")
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for i, file_info in enumerate(context.user_data['files'], 1):
                    try:
                        if i % 3 == 0:
                            try:
                                await processing_msg.edit_text(
                                    f"⏳ پردازش فایل‌ها... ({i}/{total_files})"
                                )
                            except:
                                pass
                        
                        file = await context.bot.get_file(file_info['file_id'])
                        file_download_path = os.path.join(tmp_dir, file_info['file_name'])
                        await file.download_to_drive(file_download_path)
                        zipf.write(file_download_path, file_info['file_name'])
                        
                    except Exception as e:
                        logger.error(f"Error processing file {file_info['file_name']}: {e}")
                        continue
            
            try:
                await processing_msg.edit_text("✅ فایل‌ها زیپ شدند. در حال ارسال...")
            except:
                pass
            
            with open(zip_path, 'rb') as zip_file:
                await update.message.reply_document(
                    document=zip_file,
                    caption=f"📦 {total_files} فایل زیپ شدند!",
                    filename="archive.zip"
                )
            
            context.user_data['files'] = []
            
            try:
                await processing_msg.delete()
            except:
                pass
            
            logger.info(f"Successfully zipped and sent {total_files} files")
            
    except Exception as e:
        logger.error(f"Error in zip_files: {e}")
        await update.message.reply_text("❌ خطایی در پردازش فایل‌ها رخ داد.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پاک کردن فایل‌های ذخیره شده"""
    try:
        if not is_user_allowed(update.effective_user.id):
            return
        
        if 'files' in context.user_data and context.user_data['files']:
            file_count = len(context.user_data['files'])
            context.user_data['files'] = []
            await update.message.reply_text(f"✅ {file_count} فایل ذخیره شده پاک شدند.")
            logger.info(f"Cancelled {file_count} files")
        else:
            await update.message.reply_text("📭 هیچ فایلی برای پاک کردن وجود ندارد.")
            
    except Exception as e:
        logger.error(f"Error in cancel command: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """راهنما"""
    try:
        if not is_user_allowed(update.effective_user.id):
            return
        
        help_text = f"""
📖 راهنمای ربات ZipBot:

• /start - شروع ربات و نمایش اطلاعات
• /zip - زیپ کردن و دریافت فایل‌ها
• /list - نمایش لیست فایل‌های ذخیره شده
• /cancel - پاک کردن فایل‌های ذخیره شده
• /help - نمایش این راهنما

📝 دستورالعمل:
1. فایل‌های خود را ارسال کنید
2. از /list برای مشاهده فایل‌ها استفاده کنید
3. از /zip برای زیپ کردن استفاده کنید
4. از /cancel برای پاک کردن استفاده کنید

⚡ محدودیت‌ها:
• حداکثر حجم فایل: {MAX_FILE_SIZE // 1024 // 1024}MB
• فقط کاربر با آیدی {ALLOWED_USER_ID} مجاز است
"""
        await update.message.reply_text(help_text)
        logger.info("Help command executed")
        
    except Exception as e:
        logger.error(f"Error in help command: {e}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت خطاهای全局"""
    try:
        logger.error(f"Error occurred: {context.error}")
        if update and update.effective_message:
            await update.effective_message.reply_text("❌ خطای سیستمی رخ داد.")
    except Exception as e:
        logger.error(f"Error in error handler: {e}")

def main():
    """اجرای اصلی ربات"""
    try:
        logger.info("🤖 Starting ZipBot...")
        logger.info(f"👤 Allowed user: {ALLOWED_USER_ID}")
        logger.info(f"⚡ Max file size: {MAX_FILE_SIZE // 1024 // 1024}MB")
        
        # ایجاد اپلیکیشن
        application = Application.builder().token(TOKEN).build()
        
        # اضافه کردن handlerها
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("zip", zip_files))
        application.add_handler(CommandHandler("list", list_files))
        application.add_handler(CommandHandler("cancel", cancel))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        
        # اضافه کردن handler خطا
        application.add_error_handler(error_handler)
        
        logger.info("🤖 Bot is running...")
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        raise

if __name__ == "__main__":
    main()
