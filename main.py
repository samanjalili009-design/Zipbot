import os
import tempfile
import pyzipper
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import logging
import asyncio
import aiohttp
import aiofiles

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
MAX_FILE_SIZE = 300 * 1024 * 1024  # 300MB
CHUNK_SIZE = 10 * 1024 * 1024  # 10MB chunks برای دانلود

HELP_TEXT = """
سلام 👋
📦 بات فشرده‌ساز رمزدار

📌 فایل رو برای من بفرست و در کپشنش بنویس:
/zip pass=رمزتو

مثال: 
/zip pass=1234

⚠️ حداکثر حجم: 300 مگابایت
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

async def download_large_file(file_id, file_path, bot):
    """دانلود فایل‌های بزرگ با chunking"""
    try:
        # دریافت لینک دانلود مستقیم
        file = await bot.get_file(file_id)
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
        
        logger.info(f"Downloading from: {file_url}")
        
        # دانلود با aiohttp به صورت chunked
        async with aiohttp.ClientSession() as session:
            async with session.get(file_url) as response:
                if response.status != 200:
                    return False
                
                # دانلود به صورت chunked
                async with aiofiles.open(file_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                        await f.write(chunk)
        
        return os.path.exists(file_path) and os.path.getsize(file_path) > 0
        
    except Exception as e:
        logger.error(f"Download error: {e}")
        return False

async def create_encrypted_zip(input_path, output_path, password):
    """ایجاد فایل زیپ رمزدار"""
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

async def split_and_send_large_file(message, file_path, password):
    """تقسیم و ارسال فایل‌های بزرگ"""
    try:
        file_size = os.path.getsize(file_path)
        chunk_size = 45 * 1024 * 1024  # 45MB برای تلگرام
        total_chunks = (file_size + chunk_size - 1) // chunk_size
        
        if total_chunks == 1:
            # فایل کوچک است
            with open(file_path, 'rb') as f:
                await message.reply_document(
                    document=InputFile(f, filename=os.path.basename(file_path)),
                    caption=f"📦 فایل زیپ رمزدار\n🔐 رمز: {password}"
                )
            return True
        
        # تقسیم فایل بزرگ
        await message.reply_text(f"📦 فایل به {total_chunks} قسمت تقسیم شد...")
        
        with open(file_path, 'rb') as f:
            for i in range(total_chunks):
                chunk_data = f.read(chunk_size)
                if not chunk_data:
                    break
                
                chunk_name = f"{os.path.basename(file_path)}.part{i+1:03d}"
                chunk_path = os.path.join(os.path.dirname(file_path), chunk_name)
                
                # ذخیره chunk موقت
                with open(chunk_path, 'wb') as chunk_file:
                    chunk_file.write(chunk_data)
                
                # ارسال chunk
                with open(chunk_path, 'rb') as chunk_file:
                    await message.reply_document(
                        document=InputFile(chunk_file, filename=chunk_name),
                        caption=f"📦 قسمت {i+1} از {total_chunks}\n🔐 رمز: {password}"
                    )
                
                # حذف فایل موقت
                try:
                    os.unlink(chunk_path)
                except:
                    pass
                
                await asyncio.sleep(1)  # تأخیر بین ارسال
        
        return True
        
    except Exception as e:
        logger.error(f"Split and send error: {e}")
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
        pwd = parse_password(msg.caption or "")
        if not pwd:
            return await msg.reply_text("❌ رمز پیدا نشد. در کپشن بنویسید: /zip pass=1234")
        
        doc = msg.document
        file_name = doc.file_name or "file"
        file_size = doc.file_size or 0
        
        # بررسی حجم فایل
        if file_size > MAX_FILE_SIZE:
            return await msg.reply_text(f"❌ حجم فایل بیشتر از {MAX_FILE_SIZE//1024//1024}MB است")
        
        await msg.reply_text("⬇️ در حال دانلود فایل... (این ممکن است چند دقیقه طول بکشد)")
        
        with tempfile.TemporaryDirectory() as td:
            orig_path = os.path.join(td, file_name)
            
            # دانلود فایل با روش chunked
            download_success = await download_large_file(doc.file_id, orig_path, context.bot)
            
            if not download_success:
                return await msg.reply_text("❌ خطا در دانلود فایل. لطفاً دوباره تلاش کنید.")
            
            downloaded_size = os.path.getsize(orig_path)
            if downloaded_size == 0:
                return await msg.reply_text("❌ فایل دانلود شده خالی است")
            
            await msg.reply_text("🔒 در حال رمزگذاری فایل...")
            
            # ایجاد فایل زیپ
            zip_name = f"{os.path.splitext(file_name)[0]}.zip"
            zip_path = os.path.join(td, zip_name)
            
            zip_success = await create_encrypted_zip(orig_path, zip_path, pwd)
            if not zip_success:
                return await msg.reply_text("❌ خطا در ایجاد فایل زیپ")
            
            zip_size = os.path.getsize(zip_path)
            size_mb = zip_size / (1024 * 1024)
            
            await msg.reply_text(f"✅ فایل رمزگذاری شد ({size_mb:.1f} MB). در حال ارسال...")
            
            # ارسال فایل
            send_success = await split_and_send_large_file(msg, zip_path, pwd)
            
            if send_success:
                await msg.reply_text("🎉 فایل با موفقیت ارسال شد!")
            else:
                await msg.reply_text("❌ خطا در ارسال فایل")
                
    except Exception as e:
        logger.error(f"General error: {str(e)}")
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
        
        logger.info("Bot is starting with large file support...")
        app.run_polling(
            read_timeout=60,
            write_timeout=60,
            connect_timeout=60,
            pool_timeout=60
        )
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")

if __name__ == "__main__":
    main()
