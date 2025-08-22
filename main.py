import os
import tempfile
import pyzipper
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import logging
import asyncio
import shutil

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
MAX_INPUT_SIZE = 300 * 1024 * 1024  # 300MB
TELEGRAM_CHUNK_SIZE = 45 * 1024 * 1024  # 45MB

HELP_TEXT = """
سلام 👋
📌 فایل رو برای من بفرست و در کپشنش بنویس:
/zip pass=رمزتو
مثال:
/zip pass=1234

⚠️ توجه: 
- حداکثر حجم فایل: 300 مگابایت
- فایل‌های بزرگ به صورت چند قسمتی ارسال می‌شوند
"""

def parse_password(caption: str | None) -> str | None:
    if not caption:
        return None
    
    patterns = ["pass=", "password=", "رمز=", "پسورد="]
    
    for pattern in patterns:
        if pattern in caption.lower():
            parts = caption.split()
            for part in parts:
                if part.lower().startswith(pattern):
                    return part.split("=", 1)[1]
    
    return None

async def download_large_file(file, destination_path):
    """دانلود فایل‌های بزرگ با مدیریت حافظه"""
    try:
        # استفاده از download_to_drive برای فایل‌های بزرگ
        await file.download_to_drive(destination_path)
        return True
    except Exception as e:
        logger.error(f"Download error: {e}")
        return False

async def create_encrypted_zip(input_path, output_path, password):
    """ایجاد فایل زیپ رمزدار با مدیریت حافظه"""
    try:
        with pyzipper.AESZipFile(
            output_path, 
            'w', 
            compression=pyzipper.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES
        ) as zf:
            zf.setpassword(password.encode('utf-8'))
            zf.write(input_path, os.path.basename(input_path))
        return True
    except Exception as e:
        logger.error(f"Zip creation error: {e}")
        return False

async def split_file_to_chunks(file_path, chunk_size=TELEGRAM_CHUNK_SIZE):
    """تقسیم فایل به chunkهای کوچکتر"""
    chunks = []
    file_name = os.path.basename(file_path)
    
    try:
        with open(file_path, 'rb') as f:
            chunk_number = 1
            while True:
                chunk_data = f.read(chunk_size)
                if not chunk_data:
                    break
                
                chunk_filename = f"{file_name}.part{chunk_number:03d}"
                chunk_path = os.path.join(os.path.dirname(file_path), chunk_filename)
                
                with open(chunk_path, 'wb') as chunk_file:
                    chunk_file.write(chunk_data)
                
                chunks.append(chunk_path)
                chunk_number += 1
                
        return chunks
    except Exception as e:
        logger.error(f"Split error: {e}")
        return []

async def send_file_with_retry(message, file_path, caption="", max_retries=3):
    """ارسال فایل با قابلیت retry"""
    for attempt in range(max_retries):
        try:
            with open(file_path, 'rb') as f:
                await message.reply_document(
                    document=InputFile(f, filename=os.path.basename(file_path)),
                    caption=caption,
                    read_timeout=60,
                    write_timeout=60,
                    connect_timeout=60
                )
            return True
        except Exception as e:
            logger.error(f"Send attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)  # تأخیر قبل از تلاش مجدد
    return False

async def process_large_file(message, file_path, password):
    """پردازش فایل‌های بزرگ"""
    try:
        # ایجاد فایل زیپ
        zip_name = f"{os.path.splitext(os.path.basename(file_path))[0]}.zip"
        zip_path = os.path.join(os.path.dirname(file_path), zip_name)
        
        success = await create_encrypted_zip(file_path, zip_path, password)
        if not success:
            return False
        
        # بررسی حجم فایل زیپ
        zip_size = os.path.getsize(zip_path)
        if zip_size == 0:
            return False
        
        # ارسال فایل
        if zip_size <= TELEGRAM_CHUNK_SIZE:
            # فایل کوچک
            caption = f"📦 فایل زیپ رمزدار\n🔐 رمز: {password}"
            return await send_file_with_retry(message, zip_path, caption)
        else:
            # فایل بزرگ - تقسیم به چند قسمت
            chunks = await split_file_to_chunks(zip_path)
            if not chunks:
                return False
            
            total_chunks = len(chunks)
            await message.reply_text(f"📦 فایل به {total_chunks} قسمت تقسیم شد")
            
            # ارسال قسمتها
            for i, chunk_path in enumerate(chunks, 1):
                caption = f"📦 قسمت {i} از {total_chunks}\n🔐 رمز: {password}"
                success = await send_file_with_retry(message, chunk_path, caption)
                if not success:
                    logger.error(f"Failed to send chunk {i}")
                
                # حذف فایل موقت
                try:
                    os.unlink(chunk_path)
                except:
                    pass
                
                await asyncio.sleep(1)  # تأخیر بین ارسالها
            
            return True
            
    except Exception as e:
        logger.error(f"Process large file error: {e}")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        msg = update.message
        
        if not msg.document:
            await msg.reply_text("❌ لطفاً یک فایل ارسال کنید.")
            return
        
        # بررسی caption برای رمز
        pwd = parse_password(msg.caption)
        if not pwd:
            return await msg.reply_text("❌ رمز پیدا نشد. در کپشن بنویس: /zip pass=1234")
        
        doc = msg.document
        file_name = doc.file_name or "file"
        file_size = doc.file_size or 0
        
        # بررسی حجم فایل
        if file_size > MAX_INPUT_SIZE:
            return await msg.reply_text(f"❌ حجم فایل بیشتر از {MAX_INPUT_SIZE//1024//1024}MB است")
        
        await msg.reply_text("⬇️ در حال دانلود فایل...")
        
        # ایجاد دایرکتوری موقت
        with tempfile.TemporaryDirectory() as td:
            orig_path = os.path.join(td, file_name)
            
            # دانلود فایل
            file = await context.bot.get_file(doc.file_id)
            download_success = await download_large_file(file, orig_path)
            
            if not download_success or not os.path.exists(orig_path):
                return await msg.reply_text("❌ خطا در دانلود فایل")
            
            downloaded_size = os.path.getsize(orig_path)
            if downloaded_size == 0:
                return await msg.reply_text("❌ فایل دانلود شده خالی است")
            
            await msg.reply_text("🔒 در حال رمزگذاری فایل...")
            
            # پردازش فایل
            success = await process_large_file(msg, orig_path, pwd)
            
            if success:
                await msg.reply_text("✅ پردازش فایل با موفقیت انجام شد")
            else:
                await msg.reply_text("❌ خطا در پردازش فایل")
                
    except Exception as e:
        logger.error(f"General error: {str(e)}")
        await msg.reply_text("❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error: {context.error}")

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is required")
    
    try:
        # افزایش timeoutها برای فایل‌های بزرگ
        app = Application.builder().token(BOT_TOKEN).build()
        
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.Document.ALL, on_document))
        app.add_error_handler(error_handler)
        
        logger.info("Starting bot with large file support...")
        app.run_polling(
            poll_interval=1,
            timeout=60,
            read_timeout=60,
            write_timeout=60,
            connect_timeout=60
        )
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")

if __name__ == "__main__":
    main()
