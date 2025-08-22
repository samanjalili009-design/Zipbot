import os
import logging
import zipfile
import tempfile
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

# دریافت تنظیمات از Environment Variables
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '8145993181:AAFK7PeFs_9VsHqaP3iKagj9lWTNJXKpgjk')
ACCOUNT_HASH = os.environ.get('ACCOUNT_HASH', 'f9e86b274826212a2712b18754fabc47')
ALLOWED_USER_ID = int(os.environ.get('ALLOWED_USER_ID', '1867911'))
MAX_FILE_SIZE = int(os.environ.get('MAX_FILE_SIZE', '2097152000'))  # 2GB پیش‌فرض

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def is_user_allowed(user_id: int) -> bool:
    """بررسی مجاز بودن کاربر"""
    return user_id == ALLOWED_USER_ID

def start(update: Update, context: CallbackContext):
    """دستور شروع"""
    if not is_user_allowed(update.effective_user.id):
        update.message.reply_text("❌ دسترسی denied.")
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
    update.message.reply_text(welcome_text)

def handle_document(update: Update, context: CallbackContext):
    """مدیریت دریافت فایل‌ها"""
    if not is_user_allowed(update.effective_user.id):
        return
    
    document = update.message.document
    
    # بررسی حجم فایل
    if document.file_size > MAX_FILE_SIZE:
        update.message.reply_text(
            f"❌ حجم فایل بیش از حد مجاز است! (حداکثر: {MAX_FILE_SIZE // 1024 // 1024}MB)"
        )
        return
    
    # ذخیره اطلاعات فایل در context
    if 'files' not in context.user_data:
        context.user_data['files'] = []
    
    context.user_data['files'].append({
        'file_id': document.file_id,
        'file_name': document.file_name or f"file_{len(context.user_data['files'])}",
        'file_size': document.file_size,
        'mime_type': document.mime_type
    })
    
    total_files = len(context.user_data['files'])
    total_size = sum(f['file_size'] for f in context.user_data['files'])
    
    update.message.reply_text(
        f"✅ فایل '{document.file_name}' ذخیره شد.\n"
        f"📊 تعداد فایل‌ها: {total_files}\n"
        f"💾 حجم کل: {total_size // 1024 // 1024}MB"
    )

def list_files(update: Update, context: CallbackContext):
    """نمایش لیست فایل‌های ذخیره شده"""
    if not is_user_allowed(update.effective_user.id):
        return
    
    if 'files' not in context.user_data or not context.user_data['files']:
        update.message.reply_text("📭 هیچ فایلی ذخیره نشده است.")
        return
    
    files_list = []
    total_size = 0
    
    for i, file_info in enumerate(context.user_data['files'], 1):
        files_list.append(f"{i}. {file_info['file_name']} ({file_info['file_size'] // 1024}KB)")
        total_size += file_info['file_size']
    
    message = (
        "📋 فایل‌های ذخیره شده:\n" +
        "\n".join(files_list) +
        f"\n\n📊 تعداد: {len(context.user_data['files'])} فایل" +
        f"\n💾 حجم کل: {total_size // 1024 // 1024}MB"
    )
    
    update.message.reply_text(message)

def zip_files(update: Update, context: CallbackContext):
    """زیپ کردن فایل‌ها و ارسال"""
    if not is_user_allowed(update.effective_user.id):
        update.message.reply_text("❌ دسترسی denied.")
        return
    
    if 'files' not in context.user_data or not context.user_data['files']:
        update.message.reply_text("❌ هیچ فایلی برای زیپ کردن وجود ندارد.")
        return
    
    # نمایش وضعیت پردازش
    processing_msg = update.message.reply_text("⏳ در حال پردازش فایل‌ها...")
    
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = os.path.join(tmp_dir, "archive.zip")
            total_files = len(context.user_data['files'])
            
            # دانلود و زیپ کردن فایل‌ها
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for i, file_info in enumerate(context.user_data['files'], 1):
                    try:
                        # آپدیت وضعیت پردازش
                        if i % 3 == 0:  # هر 3 فایل یکبار آپدیت
                            context.bot.edit_message_text(
                                chat_id=processing_msg.chat_id,
                                message_id=processing_msg.message_id,
                                text=f"⏳ پردازش فایل‌ها... ({i}/{total_files})"
                            )
                        
                        file = context.bot.get_file(file_info['file_id'])
                        file_download_path = os.path.join(tmp_dir, file_info['file_name'])
                        file.download(file_download_path)
                        zipf.write(file_download_path, file_info['file_name'])
                        
                    except Exception as e:
                        logger.error(f"Error processing file {file_info['file_name']}: {e}")
                        continue
            
            # ارسال فایل زیپ شده
            context.bot.edit_message_text(
                chat_id=processing_msg.chat_id,
                message_id=processing_msg.message_id,
                text="✅ فایل‌ها زیپ شدند. در حال ارسال..."
            )
            
            with open(zip_path, 'rb') as zip_file:
                update.message.reply_document(
                    document=zip_file,
                    caption=f"📦 {total_files} فایل زیپ شدند!",
                    filename="archive.zip"
                )
            
            # پاک کردن لیست فایل‌ها
            context.user_data['files'] = []
            
            context.bot.delete_message(
                chat_id=processing_msg.chat_id,
                message_id=processing_msg.message_id
            )
            
    except Exception as e:
        logger.error(f"Error in zip_files: {e}")
        update.message.reply_text("❌ خطایی در پردازش فایل‌ها رخ داد.")
        try:
            context.bot.delete_message(
                chat_id=processing_msg.chat_id,
                message_id=processing_msg.message_id
            )
        except:
            pass

def cancel(update: Update, context: CallbackContext):
    """پاک کردن فایل‌های ذخیره شده"""
    if not is_user_allowed(update.effective_user.id):
        return
    
    if 'files' in context.user_data and context.user_data['files']:
        file_count = len(context.user_data['files'])
        context.user_data['files'] = []
        update.message.reply_text(f"✅ {file_count} فایل ذخیره شده پاک شدند.")
    else:
        update.message.reply_text("📭 هیچ فایلی برای پاک کردن وجود ندارد.")

def help_command(update: Update, context: CallbackContext):
    """راهنما"""
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
    update.message.reply_text(help_text)

def error_handler(update: Update, context: CallbackContext):
    """مدیریت خطاهای全局"""
    logger.error(f"Error occurred: {context.error}")
    if update and update.effective_message:
        update.effective_message.reply_text("❌ خطای سیستمی رخ داد.")

def main():
    """اجرای اصلی ربات"""
    try:
        # ایجاد updater
        updater = Updater(TOKEN, use_context=True)
        
        # دریافت dispatcher
        dp = updater.dispatcher
        
        # اضافه کردن handlerها
        dp.add_handler(CommandHandler("start", start))
        dp.add_handler(CommandHandler("zip", zip_files))
        dp.add_handler(CommandHandler("list", list_files))
        dp.add_handler(CommandHandler("cancel", cancel))
        dp.add_handler(CommandHandler("help", help_command))
        dp.add_handler(MessageHandler(Filters.document, handle_document))
        
        # اضافه کردن handler خطا
        dp.add_error_handler(error_handler)
        
        logger.info("🤖 ربات ZipBot در حال اجراست...")
        logger.info(f"👤 کاربر مجاز: {ALLOWED_USER_ID}")
        logger.info(f"⚡ حداکثر حجم فایل: {MAX_FILE_SIZE // 1024 // 1024}MB")
        
        # شروع ربات
        updater.start_polling()
        updater.idle()
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        raise

if __name__ == "__main__":
    main()
