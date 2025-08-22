import os
import tempfile
import pyzipper
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler
import logging
from typing import Dict
import math
import asyncio
import aiohttp
import aiofiles
from pathlib import Path

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = "@flvst1"
CHANNEL_ID = -1001093039800

# محدودیت‌های پیشرفته
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 گیگابایت برای هر فایل
MAX_TOTAL_SIZE = 4 * 1024 * 1024 * 1024  # 4 گیگابایت برای کل آرشیو
MAX_FILES_COUNT = 20  # حداکثر 20 فایل
CHUNK_SIZE = 10 * 1024 * 1024  # 10 مگابایت برای هر chunk

# حالت‌های گفتگو
WAITING_PASSWORD, WAITING_FILES, PROCESSING = range(3)
user_data: Dict[int, Dict] = {}

HELP_TEXT = f"""🚀 سلام👋 
📦بات فشرده‌ساز حرفه‌ای با پشتیبانی از فایل‌های بزرگ

📌 برای استفاده از بات ابتدا باید در کانال ما عضو شوید:
{CHANNEL_USERNAME}

✅ پس از عضویت، از دستورات زیر استفاده کنید:

🔹 /zip - شروع فرآیند فشرده‌سازی
🔹 /check - بررسی وضعیت عضویت
🔹 /limits - مشاهده محدودیت‌ها

⚡ قابلیت‌های ویژه:
• پشتیبانی از فایل‌های تا ۲ گیگابایت
• فشرده‌سازی با رمزگذاری AES-256
• دانلود تکه‌ای برای فایل‌های بزرگ

📊 پس از ارسال /zip:
1. رمز دلخواه خود را وارد کنید
2. فایل‌های خود را ارسال کنید
3. پس از اتمام، /done را ارسال کنید"""

async def check_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """چک کردن عضویت کاربر در کانال"""
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Error checking membership: {e}")
        return False

def format_size(size_bytes):
    """تبدیل حجم به فرمت خوانا"""
    if size_bytes == 0:
        return "0 B"
    size_names = ["B", "KB", "MB", "GB"]
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_names[i]}"

async def download_large_file(file_url: str, file_path: str, file_size: int, update: Update):
    """دانلود فایل‌های بزرگ با نمایش پیشرفت"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(file_url) as response:
                if response.status != 200:
                    raise Exception(f"Download failed with status {response.status}")
                
                downloaded = 0
                async with aiofiles.open(file_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                        await f.write(chunk)
                        downloaded += len(chunk)
                        
                        # ارسال وضعیت هر 25%
                        progress = (downloaded / file_size) * 100
                        if progress % 25 < 1 and progress > 0:
                            await update.message.reply_text(
                                f"📥 دانلود: {format_size(downloaded)} / {format_size(file_size)} "
                                f"({int(progress)}%)"
                            )
        
        return True
    except Exception as e:
        logger.error(f"Download error: {e}")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not await check_membership(user_id, context):
        await update.message.reply_text(
            f"❌ برای استفاده از بات باید ابتدا در کانال ما عضو شوید:\n"
            f"{CHANNEL_USERNAME}\n\n"
            f"✅ پس از عضویت، دوباره /start را ارسال کنید."
        )
        return
    
    await update.message.reply_text(HELP_TEXT)

async def limits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش محدودیت‌های سیستم"""
    user_id = update.effective_user.id
    
    if not await check_membership(user_id, context):
        await update.message.reply_text(
            f"❌ برای استفاده از بات باید ابتدا در کانال ما عضو شوید:\n"
            f"{CHANNEL_USERNAME}"
        )
        return
    
    limits_text = f"""🚀 محدودیت‌های پیشرفته سیستم:

• 📁 حداکثر حجم هر فایل: {format_size(MAX_FILE_SIZE)}
• 📦 حداکثر حجم کل آرشیو: {format_size(MAX_TOTAL_SIZE)} 
• 🔢 حداکثر تعداد فایل‌ها: {MAX_FILES_COUNT} فایل

💡 نکات فنی:
- از رمزگذاری AES-256 استفاده می‌شود
- فایل‌های بزرگ به صورت تکه‌ای دانلود می‌شوند
- امکان فشرده‌سازی فایل‌های بسیار بزرگ وجود دارد

⚡ برای شروع: /zip"""

    await update.message.reply_text(limits_text)

async def zip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not await check_membership(user_id, context):
        await update.message.reply_text(
            f"❌ برای استفاده از بات باید ابتدا در کانال ما عضو شوید:\n"
            f"{CHANNEL_USERNAME}"
        )
        return ConversationHandler.END
    
    user_data[user_id] = {'files': [], 'password': None, 'total_size': 0}
    
    await update.message.reply_text(
        "🔐 لطفاً رمز قوی برای فایل زیپ وارد کنید:\n"
        "⚠️ این رمز را فراموش نکنید! (حداقل 6 کاراکتر)"
    )
    return WAITING_PASSWORD

async def receive_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not await check_membership(user_id, context):
        await update.message.reply_text(
            f"❌ برای استفاده از بات باید ابتدا در کانال ما عضو شوید:\n"
            f"{CHANNEL_USERNAME}"
        )
        return ConversationHandler.END
    
    password = update.message.text.strip()
    
    if not password:
        await update.message.reply_text("❌ رمز نمی‌تواند خالی باشد. لطفاً رمز معتبر وارد کنید:")
        return WAITING_PASSWORD
    
    if len(password) < 6:
        await update.message.reply_text("❌ رمز باید حداقل 6 کاراکتر باشد. لطفاً رمز قوی‌تری انتخاب کنید:")
        return WAITING_PASSWORD
    
    user_data[user_id]['password'] = password
    
    await update.message.reply_text(
        f"✅ رمز '{password}' ذخیره شد.\n\n"
        f"📁 لطفاً فایل‌های خود را ارسال کنید.\n"
        f"🚀 پشتیبانی از فایل‌های تا {format_size(MAX_FILE_SIZE)}\n"
        f"⏹️ پس از اتمام، /done را ارسال کنید."
    )
    return WAITING_FILES

async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not await check_membership(user_id, context):
        await update.message.reply_text(
            f"❌ برای استفاده از بات باید ابتدا در کانال ما عضو شوید:\n"
            f"{CHANNEL_USERNAME}"
        )
        return ConversationHandler.END
    
    if user_id not in user_data or user_data[user_id]['password'] is None:
        await update.message.reply_text("❌ لطفاً ابتدا دستور /zip را اجرا کنید.")
        return ConversationHandler.END
    
    # بررسی تعداد فایل‌ها
    if len(user_data[user_id]['files']) >= MAX_FILES_COUNT:
        await update.message.reply_text(
            f"❌ حداکثر تعداد فایل‌ها ({MAX_FILES_COUNT}) رسیده است.\n"
            f"⏹️ لطفاً /done را ارسال کنید."
        )
        return WAITING_FILES
    
    try:
        document = update.message.document
        
        # بررسی حجم فایل
        if document.file_size > MAX_FILE_SIZE:
            await update.message.reply_text(
                f"❌ حجم فایل ({format_size(document.file_size)}) بیش از حد مجاز ({format_size(MAX_FILE_SIZE)}) است!"
            )
            return WAITING_FILES
        
        # بررسی حجم کل
        new_total_size = user_data[user_id]['total_size'] + document.file_size
        if new_total_size > MAX_TOTAL_SIZE:
            await update.message.reply_text(
                f"❌ حجم کل فایل‌ها ({format_size(new_total_size)}) از حد مجاز ({format_size(MAX_TOTAL_SIZE)}) بیشتر شده!\n"
                f"⏹️ لطفاً /done را ارسال کنید."
            )
            return WAITING_FILES
        
        # ایجاد پوشه موقت برای ذخیره فایل
        temp_dir = Path(tempfile.mkdtemp())
        temp_file_path = temp_dir / document.file_name
        
        # دانلود فایل (برای فایل‌های بزرگ از دانلود تکه‌ای استفاده می‌شود)
        if document.file_size > 50 * 1024 * 1024:  # برای فایل‌های بزرگتر از 50 مگابایت
            await update.message.reply_text(
                f"📥 شروع دانلود فایل بزرگ...\n"
                f"📊 حجم: {format_size(document.file_size)}\n"
                f"⏳ لطفاً منتظر بمانید..."
            )
            
            file_url = await document.get_file()
            success = await download_large_file(file_url.file_path, str(temp_file_path), document.file_size, update)
            
            if not success:
                await update.message.reply_text("❌ خطا در دانلود فایل. لطفاً دوباره امتحان کنید.")
                return WAITING_FILES
        else:
            # دانلود معمولی برای فایل‌های کوچک
            file = await document.get_file()
            await file.download_to_drive(str(temp_file_path))
        
        user_data[user_id]['files'].append({
            'name': document.file_name,
            'path': str(temp_file_path),
            'size': document.file_size,
            'temp_dir': str(temp_dir)
        })
        user_data[user_id]['total_size'] = new_total_size
        
        remaining_files = MAX_FILES_COUNT - len(user_data[user_id]['files'])
        remaining_size = MAX_TOTAL_SIZE - new_total_size
        
        await update.message.reply_text(
            f"✅ فایل '{document.file_name}' دریافت شد.\n"
            f"📊 حجم: {format_size(document.file_size)}\n"
            f"📦 حجم کل: {format_size(new_total_size)} / {format_size(MAX_TOTAL_SIZE)}\n"
            f"📁 تعداد: {len(user_data[user_id]['files'])} / {MAX_FILES_COUNT}\n\n"
            f"📎 فایل بعدی را ارسال کنید یا برای اتمام /done را ارسال کنید."
        )
        return WAITING_FILES
        
    except Exception as e:
        logger.error(f"Error receiving file: {e}")
        await update.message.reply_text("❌ خطا در دریافت فایل. لطفاً دوباره امتحان کنید.")
        return WAITING_FILES

async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not await check_membership(user_id, context):
        await update.message.reply_text(
            f"❌ برای استفاده از بات باید ابتدا در کانال ما عضو شوید:\n"
            f"{CHANNEL_USERNAME}"
        )
        return ConversationHandler.END
    
    if user_id not in user_data or not user_data[user_id]['files']:
        await update.message.reply_text("❌ هیچ فایلی دریافت نشده است. لطفاً ابتدا فایل‌ها را ارسال کنید.")
        return ConversationHandler.END
    
    try:
        file_count = len(user_data[user_id]['files'])
        total_size = user_data[user_id]['total_size']
        
        processing_msg = await update.message.reply_text(
            f"⚡ در حال ایجاد فایل زیپ...\n"
            f"📊 {file_count} فایل با حجم کل {format_size(total_size)}\n"
            f"🔐 با رمز: {user_data[user_id]['password']}\n"
            f"⏳ این عملیات ممکن است چند دقیقه طول بکشد..."
        )
        
        # ایجاد فایل زیپ رمزدار
        zip_temp_dir = Path(tempfile.mkdtemp())
        zip_file_path = zip_temp_dir / "archive.zip"
        
        with pyzipper.AESZipFile(
            zip_file_path, 
            'w', 
            compression=pyzipper.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES
        ) as zf:
            zf.setpassword(user_data[user_id]['password'].encode('utf-8'))
            
            for i, file_info in enumerate(user_data[user_id]['files']):
                zf.write(file_info['path'], file_info['name'])
                
                # ارسال وضعیت پیشرفت هر 25%
                progress = ((i + 1) / file_count) * 100
                if progress % 25 < 1 and progress > 0:
                    await processing_msg.edit_text(
                        f"⚡ در حال فشرده‌سازی...\n"
                        f"📊 پیشرفت: {int(progress)}%\n"
                        f"📁 فایل {i + 1} از {file_count}"
                    )
        
        zip_size = os.path.getsize(zip_file_path)
        
        # ارسال فایل زیپ
        await update.message.reply_document(
            document=InputFile(zip_file_path, filename='archive.zip'),
            caption=f"✅ فایل زیپ با موفقیت ایجاد شد!\n\n"
                    f"📦 حجم فایل زیپ: {format_size(zip_size)}\n"
                    f"📁 تعداد فایل‌ها: {file_count}\n"
                    f"🔐 رمز: {user_data[user_id]['password']}\n"
                    f"💚 از حمایت شما متشکریم! {CHANNEL_USERNAME}"
        )
        
        # پاکسازی فایل‌های موقت
        for file_info in user_data[user_id]['files']:
            try:
                os.unlink(file_info['path'])
                if 'temp_dir' in file_info:
                    os.rmdir(file_info['temp_dir'])
            except:
                pass
        
        try:
            os.unlink(zip_file_path)
            os.rmdir(zip_temp_dir)
        except:
            pass
        
        await update.message.reply_text(
            "🎉 عملیات با موفقیت завер شد!\n\n"
            f"📦 فایل زیپ با حجم {format_size(zip_size)} ارسال شد.\n"
            f"🔐 رمز فایل: {user_data[user_id]['password']}\n\n"
            f"💚 برای فشرده‌سازی بیشتر، دوباره /zip را ارسال کنید.\n"
            f"📢 {CHANNEL_USERNAME}"
        )
        
    except Exception as e:
        logger.error(f"Error creating zip: {e}")
        await update.message.reply_text("❌ خطا در ایجاد فایل زیپ. لطفاً دوباره امتحان کنید.")
    
    finally:
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
                if 'temp_dir' in file_info:
                    os.rmdir(file_info['temp_dir'])
            except:
                pass
        del user_data[user_id]
    
    await update.message.reply_text("❌ عملیات کنسل شد.")
    return ConversationHandler.END

async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if await check_membership(user_id, context):
        await update.message.reply_text(
            "✅ شما در کانال عضو هستید!\n\n"
            "🚀 اکنون می‌توانید از بات استفاده کنید:\n"
            "/zip - شروع فرآیند فشرده‌سازی\n"
            "/limits - مشاهده محدودیت‌ها"
        )
    else:
        await update.message.reply_text(
            f"❌ شما در کانال عضو نیستید!\n\n"
            f"لطفاً در کانال ما عضو شوید:\n"
            f"{CHANNEL_USERNAME}\n\n"
            f"سپس دوباره /check را ارسال کنید."
        )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")
    if update and update.message:
        await update.message.reply_text("❌ خطایی رخ داده است. لطفاً دوباره امتحان کنید.")

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable is required")
        raise ValueError("BOT_TOKEN environment variable is required")
    
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
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
        application.add_handler(CommandHandler('check', check_subscription))
        application.add_handler(CommandHandler('limits', limits_command))
        application.add_handler(conv_handler)
        application.add_error_handler(error_handler)
        
        logger.info("Bot is starting with large file support...")
        print("🤖 Bot is starting with 2GB file support...")
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        print(f"❌ Failed to start bot: {e}")

if __name__ == "__main__":
    main()
