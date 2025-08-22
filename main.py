import os
import tempfile
import pyzipper
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler
import logging
from typing import Dict

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

# حالت‌های گفتگو
WAITING_PASSWORD, WAITING_FILES = range(2)
user_data: Dict[int, Dict] = {}

HELP_TEXT = """سلام👋 
📦بات فشرده‌ساز رمزدار

📌 برای استفاده از بات:
1. دستور /zip را ارسال کنید
2. رمز دلخواه خود را وارد نمایید
3. فایل‌های خود را یکی پس از دیگری ارسال کنید
4. پس از اتمام ارسال فایل‌ها، دستور /done را ارسال کنید

⚠️ حداکثر حجم هر فایل: 20 مگابایت (محدودیت تلگرام)
⚠️ حداکثر تعداد فایل‌ها در یک آرشیو: 10 فایل"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def zip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id] = {'files': [], 'password': None}
    
    await update.message.reply_text("لطفاً رمز مورد نظر برای فایل زیپ را وارد کنید:")
    return WAITING_PASSWORD

async def receive_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    password = update.message.text.strip()
    
    if not password:
        await update.message.reply_text("رمز نمی‌تواند خالی باشد. لطفاً رمز معتبر وارد کنید:")
        return WAITING_PASSWORD
    
    user_data[user_id]['password'] = password
    
    await update.message.reply_text(
        f"رمز '{password}' ذخیره شد.\n"
        "لطفاً فایل‌های خود را ارسال کنید. پس از اتمام، دستور /done را ارسال کنید."
    )
    return WAITING_FILES

async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_data or user_data[user_id]['password'] is None:
        await update.message.reply_text("لطفاً ابتدا دستور /zip را اجرا کنید.")
        return ConversationHandler.END
    
    if len(user_data[user_id]['files']) >= 10:
        await update.message.reply_text("حداکثر تعداد فایل‌ها (10) رسیده است. لطفاً /done را ارسال کنید.")
        return WAITING_FILES
    
    document = update.message.document
    file = await document.get_file()
    
    # ایجاد پوشه موقت برای ذخیره فایل
    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        await file.download_to_drive(temp_file.name)
        user_data[user_id]['files'].append({
            'name': document.file_name,
            'path': temp_file.name
        })
    
    remaining = 10 - len(user_data[user_id]['files'])
    await update.message.reply_text(
        f"فایل '{document.file_name}' دریافت شد. ({remaining} فایل دیگر قابل دریافت است)\n"
        "فایل بعدی را ارسال کنید یا برای اتمام /done را ارسال کنید."
    )
    return WAITING_FILES

async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_data or not user_data[user_id]['files']:
        await update.message.reply_text("هیچ فایلی دریافت نشده است. لطفاً ابتدا فایل‌ها را ارسال کنید.")
        return ConversationHandler.END
    
    await update.message.reply_text("در حال ایجاد فایل زیپ... لطفاً منتظر بمانید.")
    
    # ایجاد فایل زیپ رمزدار
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as zip_file:
        with pyzipper.AESZipFile(
            zip_file.name, 
            'w', 
            compression=pyzipper.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES
        ) as zf:
            zf.setpassword(user_data[user_id]['password'].encode('utf-8'))
            
            for file_info in user_data[user_id]['files']:
                zf.write(file_info['path'], file_info['name'])
        
        # ارسال فایل زیپ
        zip_file.seek(0)
        await update.message.reply_document(
            document=InputFile(zip_file, filename='archive.zip'),
            caption=f"فایل زیپ با رمز '{user_data[user_id]['password']}' ایجاد شد."
        )
    
    # پاکسازی فایل‌های موقت
    for file_info in user_data[user_id]['files']:
        try:
            os.unlink(file_info['path'])
        except:
            pass
    try:
        os.unlink(zip_file.name)
    except:
        pass
    
    # پاکسازی داده کاربر
    if user_id in user_data:
        del user_data[user_id]
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # پاکسازی فایل‌های موقت
    if user_id in user_data:
        for file_info in user_data[user_id]['files']:
            try:
                os.unlink(file_info['path'])
            except:
                pass
        del user_data[user_id]
    
    await update.message.reply_text("عملیات کنسل شد.")
    return ConversationHandler.END

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is required")
    
    # استفاده از Application بدون Updater
    application = Application.builder().token(BOT_TOKEN).build()
    
    # handlers
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('zip', zip_command)],
        states={
            WAITING_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_password)],
            WAITING_FILES: [
                MessageHandler(filters.Document.ALL, receive_file),
                CommandHandler('done', done_command)
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(conv_handler)
    application.add_error_handler(error_handler)
    
    # راه‌اندازی بات
    print("Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()
