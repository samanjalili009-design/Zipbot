import os
import io
import aiohttp
import pyzipper
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN not found! Set it in Render Environment Variables")

MAX_FILE_SIZE = 150 * 1024 * 1024  # 150MB برای اطمینان
CHUNK_SIZE = 1 * 1024 * 1024  # 1MB chunks

HELP_TEXT = """
🔐 **File Zipper Bot**
📦 فایل‌ها را زیپ کرده و رمزگذاری می‌کند

📌 **نحوه استفاده:**
pass=رمز_خود https://example.com/file.ext

🎯 **مثال:**
`pass=1234 https://site.com/document.pdf`

⚠️ **توجه:**
- حداکثر حجم فایل: 150MB
- لینک باید مستقیم باشد
"""

def parse_password(text: str) -> str:
    """استخراج رمز از متن"""
    for part in text.split():
        if part.startswith("pass="):
            return part.split("=", 1)[1]
    return ""

def parse_link(text: str) -> str:
    """استخراج لینک از متن"""
    for part in text.split():
        if part.startswith("http://") or part.startswith("https://"):
            return part
    return ""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور start"""
    await update.message.reply_text(HELP_TEXT, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پردازش پیام کاربر"""
    message = update.message
    text = message.text.strip()
    
    # استخراج رمز و لینک
    password = parse_password(text)
    file_url = parse_link(text)
    
    if not password:
        await message.reply_text("❌ **رمز پیدا نشد!**\nلطفاً با فرمت `pass=رمز` رمز را وارد کنید.", parse_mode='Markdown')
        return
        
    if not file_url:
        await message.reply_text("❌ **لینک پیدا نشد!**\nلطفاً یک لینک مستقیم ارسال کنید.", parse_mode='Markdown')
        return
    
    await message.reply_text("⬇️ **در حال دانلود فایل...**", parse_mode='Markdown')
    
    try:
        # دانلود فایل
        file_data = await download_file(file_url, message)
        if file_data is None:
            return
            
        # ایجاد زیپ رمزدار
        zip_buffer = await create_encrypted_zip(file_data, password, message)
        if zip_buffer is None:
            return
            
        # ارسال فایل
        await send_zip_file(zip_buffer, message)
        
    except Exception as e:
        logger.error(f"Error processing file: {e}")
        await message.reply_text(f"❌ **خطا در پردازش:**\n{str(e)}")

async def download_file(url: str, message) -> bytes:
    """دانلود فایل از لینک"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    await message.reply_text(f"❌ **خطا در دانلود!**\nکد وضعیت: {response.status}")
                    return None
                
                content_length = int(response.headers.get('Content-Length', 0))
                if content_length > MAX_FILE_SIZE:
                    size_mb = content_length / (1024 * 1024)
                    await message.reply_text(f"❌ **حجم فایل زیاد است!**\nحجم: {size_mb:.1f}MB (حداکثر: 150MB)")
                    return None
                
                # دانلود chunk به chunk
                file_data = bytearray()
                downloaded = 0
                
                async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                    file_data.extend(chunk)
                    downloaded += len(chunk)
                    
                    if downloaded > MAX_FILE_SIZE:
                        await message.reply_text("❌ **حجم فایل بیش از حد مجاز است!**")
                        return None
                
                await message.reply_text(f"✅ **دانلود کامل شد**\nحجم: {downloaded/(1024*1024):.1f}MB")
                return bytes(file_data)
                
    except aiohttp.ClientError as e:
        await message.reply_text(f"❌ **خطا در اتصال:**\n{str(e)}")
        return None
    except Exception as e:
        await message.reply_text(f"❌ **خطا در دانلود:**\n{str(e)}")
        return None

async def create_encrypted_zip(file_data: bytes, password: str, message) -> io.BytesIO:
    """ایجاد زیپ رمزدار"""
    try:
        await message.reply_text("🔐 **در حال رمزگذاری فایل...**")
        
        zip_buffer = io.BytesIO()
        
        with pyzipper.AESZipFile(
            zip_buffer, 
            'w', 
            compression=pyzipper.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES
        ) as zf:
            zf.setpassword(password.encode('utf-8'))
            zf.writestr("file", file_data)
        
        zip_size = len(zip_buffer.getvalue())
        await message.reply_text(f"✅ **رمزگذاری کامل شد**\nحجم فایل زیپ: {zip_size/(1024*1024):.1f}MB")
        
        return zip_buffer
        
    except Exception as e:
        await message.reply_text(f"❌ **خطا در رمزگذاری:**\n{str(e)}")
        return None

async def send_zip_file(zip_buffer: io.BytesIO, message):
    """ارسال فایل زیپ شده"""
    try:
        zip_buffer.seek(0)
        
        await message.reply_document(
            document=zip_buffer,
            filename="encrypted_file.zip",
            caption="📦 **فایل زیپ شده با رمز آماده است**\n\n✅ عملیات با موفقیت انجام شد"
        )
        
    except Exception as e:
        await message.reply_text(f"❌ **خطا در ارسال فایل:**\n{str(e)}")

def main():
    """تابع اصلی اجرای ربات"""
    try:
        # ساخت اپلیکیشن
        application = Application.builder().token(BOT_TOKEN).build()
        
        # اضافه کردن handlerها
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # اجرای ربات
        print("🤖 ربات File Zipper در حال اجرا است...")
        application.run_polling()
        
    except Exception as e:
        logger.error(f"خطا در اجرای ربات: {e}")
        print(f"خطا: {e}")

if __name__ == "__main__":
    main()
