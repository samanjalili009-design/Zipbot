import os
import logging
import zipfile
import tempfile
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# تنظیمات
TOKEN = "8145993181:AAFK7PeFs_9VsHqaP3iKagj9lWTNJXKpgjk"
ACCOUNT_HASH = "f9e86b274826212a2712b18754fabc47"
ALLOWED_USER_ID = 1867911  # آیدی کاربر مجاز

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور شروع"""
    if update.effective_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("❌ دسترسی denied.")
        return
    
    await update.message.reply_text(
        "🤖 ربات آماده است!\n\n"
        "فایل‌ها را ارسال کنید و پس از اتمام، از دستور /zip استفاده کنید."
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت دریافت فایل‌ها"""
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    
    # ذخیره اطلاعات فایل در context
    if 'files' not in context.user_data:
        context.user_data['files'] = []
    
    document = update.message.document
    context.user_data['files'].append({
        'file_id': document.file_id,
        'file_name': document.file_name,
        'mime_type': document.mime_type
    })
    
    await update.message.reply_text(f"✅ فایل '{document.file_name}' ذخیره شد.")

async def zip_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زیپ کردن فایل‌ها و ارسال"""
    if update.effective_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("❌ دسترسی denied.")
        return
    
    if 'files' not in context.user_data or not context.user_data['files']:
        await update.message.reply_text("❌ هیچ فایلی برای زیپ کردن وجود ندارد.")
        return
    
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = os.path.join(tmp_dir, "archive.zip")
            
            # دانلود و زیپ کردن فایل‌ها
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_info in context.user_data['files']:
                    file = await context.bot.get_file(file_info['file_id'])
                    file_download_path = os.path.join(tmp_dir, file_info['file_name'])
                    await file.download_to_drive(file_download_path)
                    zipf.write(file_download_path, file_info['file_name'])
            
            # ارسال فایل زیپ شده
            with open(zip_path, 'rb') as zip_file:
                await update.message.reply_document(
                    document=zip_file,
                    caption="📦 فایل‌های شما زیپ شدند!",
                    filename="archive.zip"
                )
            
            # پاک کردن لیست فایل‌ها
            context.user_data['files'] = []
            
    except Exception as e:
        logger.error(f"Error in zip_files: {e}")
        await update.message.reply_text("❌ خطایی در پردازش فایل‌ها رخ داد.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پاک کردن فایل‌های ذخیره شده"""
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    
    if 'files' in context.user_data:
        context.user_data['files'] = []
    
    await update.message.reply_text("✅ فایل‌های ذخیره شده پاک شدند.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """راهنما"""
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    
    help_text = """
📖 راهنمای ربات:

1. فایل‌های خود را ارسال کنید
2. پس از اتمام از دستور /zip استفاده کنید
3. فایل زیپ شده دریافت خواهد شد
4. برای پاک کردن فایل‌های ذخیره شده از /cancel استفاده کنید

💡 توجه: فایل‌ها پس از هر بار زیپ کردن به طور خودکار پاک می‌شوند.
"""
    await update.message.reply_text(help_text)

def main():
    """اجرای اصلی ربات"""
    # ایجاد اپلیکیشن
    application = Application.builder().token(TOKEN).build()
    
    # اضافه کردن handlerها
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("zip", zip_files))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    # شروع ربات
    application.run_polling()

if __name__ == "__main__":
    main()
