import os
import tempfile
import pyzipper
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import logging
import asyncio

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
MAX_INPUT_SIZE = 300 * 1024 * 1024  # 300MB محدودیت حجم فایل ورودی
TELEGRAM_CHUNK_SIZE = 45 * 1024 * 1024  # 45MB برای اطمینان از محدودیت 50MB تلگرام

HELP_TEXT = """
سلام 👋
📌 فایل رو برای من بفرست و در کپشنش بنویس:
/zip pass=رمزتو
مثال:
/zip pass=1234

⚠️ توجه: 
- حداکثر حجم فایل: 300 مگابایت
- فایل‌های بزرگ به صورت چند قسمتی ارسال می‌شوند
- برای باز کردن فایل‌های چند قسمتی از نرم‌افزار 7-Zip استفاده کنید
"""

def parse_password(caption: str | None) -> str | None:
    if not caption:
        return None
    
    patterns = ["pass=", "password=", "رمز=", "پسورد="]
    caption_lower = caption.lower()
    
    for pattern in patterns:
        if pattern in caption_lower:
            parts = caption.split()
            for part in parts:
                if part.lower().startswith(pattern):
                    return part.split("=", 1)[1]
    
    return None

async def split_large_file(file_path, chunk_size=TELEGRAM_CHUNK_SIZE):
    """تقسیم فایل بزرگ به چند قسمت"""
    chunks = []
    file_name = os.path.basename(file_path)
    total_size = os.path.getsize(file_path)
    
    await asyncio.sleep(0.1)  # برای جلوگیری از block شدن
    
    with open(file_path, 'rb') as f:
        chunk_number = 1
        bytes_processed = 0
        
        while bytes_processed < total_size:
            chunk_data = f.read(chunk_size)
            if not chunk_data:
                break
            
            chunk_filename = f"{file_name}.part{chunk_number:03d}"
            chunk_path = os.path.join(os.path.dirname(file_path), chunk_filename)
            
            with open(chunk_path, 'wb') as chunk_file:
                chunk_file.write(chunk_data)
            
            chunks.append(chunk_path)
            chunk_number += 1
            bytes_processed += len(chunk_data)
    
    return chunks

async def send_file_chunks(message, file_path, caption=""):
    """ارسال فایل به صورت چند قسمتی"""
    file_size = os.path.getsize(file_path)
    
    if file_size <= TELEGRAM_CHUNK_SIZE:
        # فایل کوچک است، ارسال عادی
        try:
            with open(file_path, 'rb') as f:
                await message.reply_document(
                    document=InputFile(f, filename=os.path.basename(file_path)),
                    caption=caption
                )
        except Exception as e:
            logger.error(f"Error sending single file: {e}")
            await message.reply_text("❌ خطا در ارسال فایل")
    else:
        # فایل بزرگ است، تقسیم به چند قسمت
        try:
            chunks = await split_large_file(file_path)
            total_chunks = len(chunks)
            
            if total_chunks == 0:
                await message.reply_text("❌ خطا در تقسیم فایل")
                return
            
            await message.reply_text(
                f"📦 فایل به {total_chunks} قسمت تقسیم شد.\n"
                f"📊 حجم کل: {file_size/(1024*1024):.1f}MB\n"
                "⏳ در حال ارسال قسمتها..."
            )
            
            for i, chunk_path in enumerate(chunks, 1):
                try:
                    with open(chunk_path, 'rb') as f:
                        await message.reply_document(
                            document=InputFile(f, filename=os.path.basename(chunk_path)),
                            caption=f"{caption}\n📁 قسمت {i} از {total_chunks}"
                        )
                    # تأخیر بین ارسال فایل‌ها برای جلوگیری از rate limit
                    await asyncio.sleep(1)
                    
                except Exception as e:
                    logger.error(f"Error sending chunk {i}: {e}")
                    await message.reply_text(f"❌ خطا در ارسال قسمت {i}")
                
                finally:
                    # حذف فایل موقت بعد از ارسال
                    try:
                        os.unlink(chunk_path)
                    except:
                        pass
                        
        except Exception as e:
            logger.error(f"Error in chunk processing: {e}")
            await message.reply_text("❌ خطا در پردازش فایل چند قسمتی")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        msg = update.message
        
        if not msg.document:
            await msg.reply_text("❌ لطفاً یک فایل ارسال کنید.")
            return
            
        pwd = parse_password(msg.caption)
        
        if not pwd:
            return await msg.reply_text("❌ رمز پیدا نشد. در کپشن بنویس: /zip pass=1234")

        doc = msg.document
        file_name = doc.file_name or "file"
        file_size = doc.file_size or 0
        
        # بررسی حجم فایل ورودی
        if file_size > MAX_INPUT_SIZE:
            return await msg.reply_text(
                f"❌ حجم فایل بیشتر از {MAX_INPUT_SIZE//1024//1024}MB است\n"
                f"📊 حجم فایل شما: {file_size/(1024*1024):.1f}MB"
            )
        
        await msg.reply_text("⬇️ در حال دانلود فایل...")

        file = await context.bot.get_file(doc.file_id)
        
        with tempfile.TemporaryDirectory() as td:
            orig_path = os.path.join(td, file_name)
            zip_name = f"{os.path.splitext(file_name)[0]}.zip"
            zip_path = os.path.join(td, zip_name)

            # دانلود فایل اصلی
            await file.download_to_drive(orig_path)
            
            if not os.path.exists(orig_path):
                return await msg.reply_text("❌ خطا در دانلود فایل")

            downloaded_size = os.path.getsize(orig_path)
            if downloaded_size == 0:
                return await msg.reply_text("❌ فایل دانلود شده خالی است")

            await msg.reply_text("🔒 در حال رمزگذاری فایل...")
            
            # ساخت زیپ با AES-256
            try:
                with pyzipper.AESZipFile(
                    zip_path, 
                    'w', 
                    compression=pyzipper.ZIP_DEFLATED,
                    encryption=pyzipper.WZ_AES
                ) as zf:
                    zf.setpassword(pwd.encode('utf-8'))
                    zf.write(orig_path, os.path.basename(orig_path))
                    
            except Exception as e:
                logger.error(f"Zip creation error: {e}")
                return await msg.reply_text("❌ خطا در ایجاد فایل زیپ")

            if not os.path.exists(zip_path):
                return await msg.reply_text("❌ فایل زیپ ایجاد نشد")

            zip_size = os.path.getsize(zip_path)
            if zip_size == 0:
                return await msg.reply_text("❌ فایل زیپ خالی است")

            size_mb = zip_size / (1024 * 1024)
            
            await msg.reply_text(
                f"✅ فایل رمزگذاری شد\n"
                f"📊 حجم: {size_mb:.1f}MB\n"
                f"🔐 رمز: {pwd}\n"
                "⏳ در حال ارسال..."
            )
            
            # ارسال فایل (به صورت چند قسمتی اگر بزرگ باشد)
            caption = f"📦 فایل زیپ رمزدار\n🔐 رمز: {pwd}\n📊 حجم: {size_mb:.1f}MB"
            await send_file_chunks(msg, zip_path, caption)

    except Exception as e:
        logger.error(f"General error: {e}")
        await msg.reply_text("❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error: {context.error}")

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is required")
    
    try:
        app = Application.builder().token(BOT_TOKEN).build()
        
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.Document.ALL, on_document))
        app.add_error_handler(error_handler)
        
        logger.info("Bot is starting with 300MB support...")
        app.run_polling()
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")

if __name__ == "__main__":
    main()
