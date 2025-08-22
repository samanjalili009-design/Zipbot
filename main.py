import os
import tempfile
import pyzipper
from telegram import Update, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters
)
import logging
from typing import Dict
import math
import aiofiles
from pathlib import Path
import asyncio
import aiohttp

# ===== تنظیمات لاگ =====
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== متغیرها =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = "@flvst1"
CHANNEL_ID = -1001093039800

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
MAX_TOTAL_SIZE = 4 * 1024 * 1024 * 1024  # 4GB
MAX_FILES_COUNT = 20
CHUNK_SIZE = 2 * 1024 * 1024  # 2MB

WAITING_PASSWORD, WAITING_FILES = range(2)
user_data: Dict[int, Dict] = {}

HELP_TEXT = f"""🚀 سلام👋
📦 بات فشرده‌ساز حرفه‌ای با پشتیبانی از فایل‌های بزرگ

📌 برای استفاده از بات ابتدا باید در کانال ما عضو شوید: {CHANNEL_USERNAME}

✅ دستورات:
🔹 /zip - شروع فشرده‌سازی
🔹 /check - بررسی وضعیت عضویت
🔹 /limits - مشاهده محدودیت‌ها

⚡ قابلیت‌ها:
• پشتیبانی فایل‌های تا ۲ گیگابایت
• فشرده‌سازی با AES-256
• دانلود تکه‌ای برای فایل‌های بزرگ
"""

# ===== Helper functions =====
async def check_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"Error checking membership: {e}")
        return False


def format_size(size_bytes: int) -> str:
    if size_bytes == 0:
        return "0 B"
    size_names = ["B", "KB", "MB", "GB"]
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_names[i]}"


async def download_large_file_telegram(file_instance, file_path: str, file_size: int, update: Update) -> bool:
    try:
        downloaded = 0
        last_progress = 0
        file = await file_instance.get_file()
        file_url = file.file_path

        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_url}") as response:
                if response.status != 200:
                    raise Exception(f"Download failed with status {response.status}")

                async with aiofiles.open(file_path, "wb") as f:
                    async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                        await f.write(chunk)
                        downloaded += len(chunk)
                        progress = (downloaded / file_size) * 100
                        if progress - last_progress >= 10:
                            await update.message.reply_text(
                                f"📥 دانلود: {format_size(downloaded)} / {format_size(file_size)} ({int(progress)}%)"
                            )
                            last_progress = progress
        return True
    except Exception as e:
        logger.error(f"Download error: {e}")
        return False


async def download_with_retry(file_instance, file_path: str, file_size: int, update: Update, max_retries=3) -> bool:
    for attempt in range(max_retries):
        try:
            await update.message.reply_text(f"📥 شروع دانلود (تلاش {attempt + 1}/{max_retries})...")
            success = await download_large_file_telegram(file_instance, file_path, file_size, update)
            if success:
                return True
        except Exception as e:
            logger.error(f"Download attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
    return False


async def download_simple(file_instance, file_path: str) -> bool:
    try:
        file = await file_instance.get_file()
        await file.download_to_drive(file_path)
        return True
    except Exception as e:
        logger.error(f"Simple download error: {e}")
        return False

# ===== Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await check_membership(user_id, context):
        await update.message.reply_text(
            f"❌ برای استفاده از بات باید ابتدا در کانال عضو شوید:\n{CHANNEL_USERNAME}\n✅ بعد /start را دوباره بفرستید."
        )
        return
    await update.message.reply_text(HELP_TEXT)


async def limits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await check_membership(user_id, context):
        await update.message.reply_text(f"❌ باید در کانال عضو شوید: {CHANNEL_USERNAME}")
        return
    limits_text = f"""🚀 محدودیت‌ها:
• 📁 حداکثر حجم هر فایل: {format_size(MAX_FILE_SIZE)}
• 📦 حداکثر حجم کل آرشیو: {format_size(MAX_TOTAL_SIZE)}
• 🔢 حداکثر تعداد فایل‌ها: {MAX_FILES_COUNT}
💡 نکات:
- رمزگذاری AES-256
- دانلود تکه‌ای فایل‌های بزرگ"""
    await update.message.reply_text(limits_text)


async def zip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await check_membership(user_id, context):
        await update.message.reply_text(f"❌ باید در کانال عضو شوید: {CHANNEL_USERNAME}")
        return ConversationHandler.END
    user_data[user_id] = {'files': [], 'password': None, 'total_size': 0}
    await update.message.reply_text("🔐 لطفاً رمز عبور برای فایل زیپ وارد کنید (حداقل 6 کاراکتر):")
    return WAITING_PASSWORD


async def receive_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    password = update.message.text.strip()
    if len(password) < 6:
        await update.message.reply_text("❌ رمز حداقل 6 کاراکتر باشد. دوباره وارد کنید:")
        return WAITING_PASSWORD
    user_data[user_id]['password'] = password
    await update.message.reply_text(
        f"✅ رمز ذخیره شد: {password}\n📁 فایل‌های خود را ارسال کنید یا /done را بفرستید."
    )
    return WAITING_FILES


async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data or user_data[user_id]['password'] is None:
        await update.message.reply_text("❌ ابتدا /zip را اجرا کنید.")
        return ConversationHandler.END

    document = update.message.document
    if document.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(f"❌ حجم فایل {format_size(document.file_size)} بیش از حد مجاز است!")
        return WAITING_FILES

    new_total_size = user_data[user_id]['total_size'] + document.file_size
    if new_total_size > MAX_TOTAL_SIZE:
        await update.message.reply_text(f"❌ حجم کل فایل‌ها بیش از حد مجاز است!")
        return WAITING_FILES

    temp_dir = Path(tempfile.mkdtemp())
    temp_file_path = temp_dir / document.file_name

    if document.file_size > 50 * 1024 * 1024:
        await update.message.reply_text(f"📥 دانلود فایل بزرگ...")
        success = await download_with_retry(document, str(temp_file_path), document.file_size, update)
    else:
        success = await download_simple(document, str(temp_file_path))

    if not success:
        await update.message.reply_text("❌ خطا در دانلود فایل.")
        return WAITING_FILES

    user_data[user_id]['files'].append({
        'name': document.file_name,
        'path': str(temp_file_path),
        'size': document.file_size,
        'temp_dir': str(temp_dir)
    })
    user_data[user_id]['total_size'] = new_total_size
    await update.message.reply_text(f"✅ فایل '{document.file_name}' دریافت شد.")
    return WAITING_FILES


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data or not user_data[user_id]['files']:
        await update.message.reply_text("❌ هیچ فایلی ارسال نشده است.")
        return ConversationHandler.END

    zip_temp_dir = Path(tempfile.mkdtemp())
    zip_file_path = zip_temp_dir / "archive.zip"

    try:
        with pyzipper.AESZipFile(
            zip_file_path,
            'w',
            compression=pyzipper.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES
        ) as zf:
            zf.setpassword(user_data[user_id]['password'].encode('utf-8'))
            for file_info in user_data[user_id]['files']:
                zf.write(file_info['path'], file_info['name'])

        zip_size = os.path.getsize(zip_file_path)
        await update.message.reply_document(
            document=InputFile(zip_file_path, filename="archive.zip"),
            caption=f"✅ فایل زیپ ایجاد شد!\n📦 حجم: {format_size(zip_size)}\n🔐 رمز: {user_data[user_id]['password']}"
        )
    finally:
        for file_info in user_data[user_id]['files']:
            try:
                if os.path.exists(file_info['path']):
                    os.unlink(file_info['path'])
                if os.path.exists(file_info['temp_dir']):
                    os.rmdir(file_info['temp_dir'])
            except:
                pass
        if os.path.exists(zip_file_path):
            os.unlink(zip_file_path)
        if os.path.exists(zip_temp_dir):
            os.rmdir(zip_temp_dir)
        if user_id in user_data:
            del user_data[user_id]

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_data:
        for file_info in user_data[user_id]['files']:
            try:
                if os.path.exists(file_info['path']):
                    os.unlink(file_info['path'])
                if os.path.exists(file_info['temp_dir']):
                    os.rmdir(file_info['temp_dir'])
            except:
                pass
        del user_data[user_id]
    await update.message.reply_text("❌ عملیات کنسل شد.")
    return ConversationHandler.END


async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if await check_membership(user_id, context):
        await update.message.reply_text("✅ شما عضو کانال هستید. /zip برای شروع استفاده کنید.")
    else:
        await update.message.reply_text(f"❌ عضو کانال نیستید: {CHANNEL_USERNAME}")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")
    if update and update.message:
        await update.message.reply_text("❌ خطایی رخ داد. دوباره امتحان کنید.")


# ===== Main =====
def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is required")

    application = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("zip", zip_command)],
        states={
            WAITING_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_password)],
            WAITING_FILES: [
                MessageHandler(filters.Document.ALL, receive_file),
                CommandHandler("done", done_command),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("check", check_subscription))
    application.add_handler(CommandHandler("limits", limits_command))
    application.add_handler(conv_handler)
    application.add_error_handler(error_handler)

    logger.info("Bot is starting...")
    application.run_polling()


if __name__ == "__main__":
    main()
