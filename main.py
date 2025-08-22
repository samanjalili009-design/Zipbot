import os
import io
import aiohttp
import pyzipper
import logging
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# تنظیمات logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN not found! Set it in Render Environment Variables")

MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB برای اطمینان
CHUNK_SIZE = 512 * 1024  # 512KB chunks

HELP_TEXT = """
🔐 **File Zipper Bot**
📦 فایل‌ها را زیپ کرده و رمزگذاری می‌کند

📌 **نحوه استفاده:**
pass=رمز_خود https://example.com/file.ext

🎯 **مثال:**
`pass=1234 https://site.com/document.pdf`

⚠️ **توجه:**
- حداکثر حجم فایل: 100MB
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
    try:
        message = update.message
        text = message.text.strip()
        
        # استخراج رمز و لینک
        password = parse_password(text)
        file_url = parse_link(text)
        
        if not password:
            await message.reply_text("❌ رمز پیدا نشد!\nلطفاً با فرمت `pass=رمز` رمز را وارد کنید.", parse_mode='Markdown')
            return
            
        if not file_url:
            await message.reply_text("❌ لینک پیدا نشد!\nلطفاً یک لینک مستقیم ارسال کنید.", parse_mode='Markdown')
            return
        
        await message.reply_text("⬇️ در حال دانلود فایل...")
        
        # دانلود فایل
        file_data = await download_file(file_url, message)
        if not file_data:
            return
            
        # ایجاد زیپ رمزدار
        zip_buffer = await create_encrypted_zip(file_data, password, message)
        if not zip_buffer:
            return
            
        # ارسال فایل
        await send_zip_file(zip_buffer, message)
        
    except Exception as e:
        logger.error(f"Error in handle_message: {e}")
        await message.reply_text(f"❌ خطای سیستمی: {str(e)}")

async def download_file(url: str, message) -> bytes:
    """دانلود فایل از لینک"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    await message.reply_text(f"❌ خطا در دانلود! کد وضعیت: {response.status}")
                    return None
                
                content_length = int(response.headers.get('Content-Length', 0))
                if content_length > MAX_FILE_SIZE:
                    size_mb = content_length / (1024 * 1024)
                    await message.reply_text(f"❌ حجم فایل زیاد است! ({size_mb:.1f}MB > 100MB)")
                    return None
                
                # دانلود chunk به chunk
                file_data = bytearray()
                downloaded = 0
                
                async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                    file_data.extend(chunk)
                    downloaded += len(chunk)
                    
                    if downloaded > MAX_FILE_SIZE:
                        await message.reply_text("❌ حجم فایل بیش از حد مجاز است!")
                        return None
                
                await message.reply_text(f"✅ دانلود کامل شد ({downloaded/(1024*1024):.1f}MB)")
                return bytes(file_data)
                
    except Exception as e:
        await message.reply_text(f"❌ خطا در دانلود: {str(e)}")
        return None

async def create_encrypted_zip(file_data: bytes, password: str, message) -> io.BytesIO:
    """ایجاد زیپ رمزدار"""
    try:
        await message.reply_text("🔐 در حال رمزگذاری فایل...")
        
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
        await message.reply_text(f"✅ رمزگذاری کامل شد ({zip_size/(1024*1024):.1f}MB)")
        
        return zip_buffer
        
    except Exception as e:
        await message.reply_text(f"❌ خطا در رمزگذاری: {str(e)}")
        return None

async def send_zip_file(zip_buffer: io.BytesIO, message):
    """ارسال فایل زیپ شده"""
    try:
        zip_buffer.seek(0)
        
        await message.reply_document(
            document=zip_buffer,
            filename="encrypted_file.zip",
            caption="📦 فایل زیپ شده با رمز آماده است\n✅ عملیات با موفقیت انجام شد"
        )
        
    except Exception as e:
        await message.reply_text(f"❌ خطا در ارسال فایل: {str(e)}")

def main():
    """تابع اصلی اجرای ربات"""
    try:
        # ساخت اپلیکیشن با تنظیمات ساده
        application = Application.builder().token(BOT_TOKEN).build()
        
        # اضافه کردن handlerها
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # اجرای ربات
        print("🤖 ربات File Zipper در حال اجرا است...")
        print(f"🤖 استفاده از توکن: {BOT_TOKEN[:10]}...")
        
        # اجرای ربات با مدیریت خطا
        loop = asyncio.get_event_loop()
        loop.run_until_complete(application.initialize())
        loop.run_until_complete(application.start())
        
        print("✅ ربات با موفقیت راه اندازی شد")
        
        # نگه داشتن برنامه در حال اجرا
        try:
            loop.run_forever()
        except KeyboardInterrupt:
            pass
        finally:
            loop.run_until_complete(application.stop())
            loop.run_until_complete(application.shutdown())
            
    except Exception as e:
        logger.error(f"خطا در اجرای ربات: {e}")
        print(f"❌ خطا: {e}")

if __name__ == "__main__":
    main()
