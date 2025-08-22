import os
import io
import aiohttp
import pyzipper
import logging
import asyncio
from telegram import Update, InputFile
from telegram.ext import Updater, CommandHandler, MessageHandler, CallbackContext
from telegram.ext import Filters

# تنظیمات logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN تعریف نشده!")

MAX_FILE_SIZE = 200 * 1024 * 1024  # کاهش به 200MB برای اطمینان
CHUNK_SIZE = 512 * 1024  # 512KB chunks

HELP_TEXT = """
سلام 👋
📌 لینک مستقیم فایل و رمز را بده.
مثال:
pass=1234 https://example.com/file.zip
"""

def parse_password(text):
    if not text:
        return None
    for part in text.split():
        if part.startswith("pass="):
            return part.split("=", 1)[1]
    return None

def parse_link(text):
    if not text:
        return None
    for part in text.split():
        if part.startswith("http://") or part.startswith("https://"):
            return part
    return None

def start(update: Update, context: CallbackContext):
    update.message.reply_text(HELP_TEXT)

async def download_file(session, url):
    async with session.get(url) as response:
        if response.status != 200:
            raise Exception(f"HTTP error {response.status}")
        return await response.read()

async def process_file_async(link, pwd, msg):
    try:
        async with aiohttp.ClientSession() as session:
            await msg.reply_text("⬇️ در حال دانلود...")
            
            # دانلود فایل
            file_data = await download_file(session, link)
            
            if len(file_data) > MAX_FILE_SIZE:
                await msg.reply_text(f"❌ حجم فایل بیش از {MAX_FILE_SIZE/(1024*1024)}MB است")
                return
                
            await msg.reply_text("🔐 در حال رمزگذاری...")
            
            # ایجاد زیپ رمزدار
            zip_buffer = io.BytesIO()
            with pyzipper.AESZipFile(zip_buffer, 'w', 
                                   compression=pyzipper.ZIP_DEFLATED,
                                   encryption=pyzipper.WZ_AES) as zf:
                zf.setpassword(pwd.encode('utf-8'))
                zf.writestr("file", file_data)
            
            zip_data = zip_buffer.getvalue()
            await msg.reply_text(f"✅ فشرده شد ({len(zip_data)/(1024*1024):.1f}MB)")
            
            # ارسال فایل
            zip_buffer.seek(0)
            await msg.reply_document(
                document=InputFile(zip_buffer, filename="file.zip"),
                caption="📦 فایل رمزدار آماده شد"
            )
            
    except Exception as e:
        await msg.reply_text(f"❌ خطا: {str(e)}")

def on_text(update: Update, context: CallbackContext):
    msg = update.message
    text = msg.text
    pwd = parse_password(text)
    link = parse_link(text)

    if not pwd:
        msg.reply_text("❌ رمز پیدا نشد. فرمت: pass=1234")
        return
    if not link:
        msg.reply_text("❌ لینک پیدا نشد")
        return

    # اجرای async در thread جداگانه
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(process_file_async(link, pwd, msg))
    finally:
        loop.close()

def main():
    try:
        # استفاده از نسخه پایدار Updater بدون use_context
        updater = Updater(BOT_TOKEN)
        dp = updater.dispatcher
        
        dp.add_handler(CommandHandler("start", start))
        dp.add_handler(MessageHandler(Filters.text & ~Filters.command, on_text))
        
        print("🤖 ربات در حال اجرا است...")
        updater.start_polling()
        updater.idle()
        
    except Exception as e:
        print(f"خطا در اجرای ربات: {e}")
        logger.error(f"خطا در اجرای ربات: {e}")

if __name__ == "__main__":
    main()
