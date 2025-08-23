import os
import logging
import tempfile
import asyncio
import json
import time
import pyzipper
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)

# دریافت تنظیمات از Environment Variables
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', 'YOUR_BOT_TOKEN')
ALLOWED_USER_ID = int(os.environ.get('ALLOWED_USER_ID', '123456789'))
MAX_FILE_SIZE = int(os.environ.get('MAX_FILE_SIZE', '2097152000'))  # پیش‌فرض 2GB

# حالت‌های گفتگو
WAITING_FOR_PASSWORD = 1

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
3. رمز عبور را وارد کنید
4. فایل زیپ شده دریافت خواهد شد

⚡ حداکثر حجم: {MAX_FILE_SIZE // 1024 // 1024}MB
"""
    await update.message.reply_text(welcome_text)
    logger.info(f"User {update.effective_user.id} started the bot")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت دریافت فایل‌ها"""
    if not is_user_allowed(update.effective_user.id):
        return

    document = update.message.document
    if document.file_size and document.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(
            f"❌ حجم فایل بیش از حد مجاز است! (حداکثر: {MAX_FILE_SIZE // 1024 // 1024}MB)"
        )
        return

    if 'files' not in context.user_data:
        context.user_data['files'] = []

    file_name = document.file_name or f"file_{len(context.user_data['files']) + 1}"
    context.user_data['files'].append({
        'file_id': document.file_id,
        'file_name': file_name,
        'file_size': document.file_size or 0,
    })

    total_files = len(context.user_data['files'])
    total_size = sum(f['file_size'] for f in context.user_data['files'])

    await update.message.reply_text(
        f"✅ فایل '{file_name}' ذخیره شد.\n"
        f"📊 تعداد فایل‌ها: {total_files}\n"
        f"💾 حجم کل: {total_size // 1024 // 1024}MB\n\n"
        f"📝 پس از اتمام فایل‌ها، از /zip استفاده کنید."
    )
    logger.info(f"File received: {file_name}, size: {document.file_size}")


async def list_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش لیست فایل‌های ذخیره شده"""
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


async def ask_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """درخواست رمز از کاربر"""
    if not is_user_allowed(update.effective_user.id):
        await update.message.reply_text("❌ دسترسی denied.")
        return ConversationHandler.END

    if 'files' not in context.user_data or not context.user_data['files']:
        await update.message.reply_text("❌ هیچ فایلی برای زیپ کردن وجود ندارد.")
        return ConversationHandler.END

    await update.message.reply_text("🔐 لطفاً رمز عبور برای فایل زیپ را وارد کنید:")
    return WAITING_FOR_PASSWORD


async def zip_files_with_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زیپ کردن فایل‌ها با رمز عبور و ارسال"""
    password = update.message.text.strip()
    if not password:
        await update.message.reply_text("❌ رمز عبور نمی‌تواند خالی باشد.")
        return WAITING_FOR_PASSWORD

    processing_msg = await update.message.reply_text("⏳ در حال ایجاد فایل زیپ...")

    total_files = len(context.user_data['files'])
    with tempfile.TemporaryDirectory() as tmp_dir:
        zip_file_name = f"archive_{int(time.time())}.zip"
        zip_path = os.path.join(tmp_dir, zip_file_name)

        with pyzipper.AESZipFile(zip_path, 'w', compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES) as zipf:
            zipf.setpassword(password.encode())
            for i, file_info in enumerate(context.user_data['files'], 1):
                try:
                    file = await context.bot.get_file(file_info['file_id'])
                    file_download_path = os.path.join(tmp_dir, file_info['file_name'])
                    await file.download_to_drive(file_download_path)
                    zipf.write(file_download_path, file_info['file_name'])
                except Exception as e:
                    logger.error(f"Error processing file {file_info['file_name']}: {e}")
                    continue

        try:
            await processing_msg.edit_text("✅ فایل زیپ آماده شد. در حال ارسال...")
        except:
            pass

        try:
            with open(zip_path, 'rb') as zip_file:
                await update.message.reply_document(
                    document=zip_file,
                    caption=f"📦 {total_files} فایل با رمز عبور زیپ شدند!\n\n"
                            f"🔐 رمز عبور: {password}\n⚠️ این رمز را حفظ کنید!",
                    filename=zip_file_name
                )
        except Exception as e:
            logger.error(f"Error sending file: {e}")
            await update.message.reply_text(f"❌ خطا در ارسال فایل.\n🔐 رمز عبور: {password}")

    context.user_data['files'] = []

    try:
        await processing_msg.delete()
    except:
        pass

    logger.info(f"Successfully zipped {total_files} files with password")
    return ConversationHandler.END


async def cancel_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ عملیات زیپ لغو شد.")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user_allowed(update.effective_user.id):
        return

    if 'files' in context.user_data and context.user_data['files']:
        file_count = len(context.user_data['files'])
        context.user_data['files'] = []
        await update.message.reply_text(f"✅ {file_count} فایل پاک شدند.")
    else:
        await update.message.reply_text("📭 هیچ فایلی برای پاک کردن وجود ندارد.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user_allowed(update.effective_user.id):
        return

    help_text = """
📖 راهنما:
• /start - شروع
• /zip - زیپ کردن فایل‌ها
• /list - لیست فایل‌ها
• /cancel - پاک کردن فایل‌ها
• /help - راهنما
"""
    await update.message.reply_text(help_text)


def main():
    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('zip', ask_password)],
        states={WAITING_FOR_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, zip_files_with_password)]},
        fallbacks=[CommandHandler('cancel', cancel_zip)]
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("list", list_files))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    application.run_polling()


if __name__ == "__main__":
    main()
