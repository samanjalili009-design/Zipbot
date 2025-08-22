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
    raise RuntimeError("❌ BOT_TOKEN تعریف نشده!")

MAX_FILE_SIZE = 512 * 1024 * 1024  # 512 MB
CHUNK_SIZE = 1 * 1024 * 1024  # 1MB chunks

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def download_and_process_file(link, pwd, msg):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(link) as resp:
                if resp.status != 200:
                    await msg.reply_text(f"❌ دانلود موفق نبود! Status code: {resp.status}")
                    return None

                total = int(resp.headers.get("Content-Length", 0))
                if total > MAX_FILE_SIZE:
                    await msg.reply_text(f"❌ حجم فایل بیش از 512MB است ({total / (1024*1024):.1f} MB)")
                    return None

                # دانلود به memory
                file_data = bytearray()
                downloaded = 0
                
                async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                    file_data.extend(chunk)
                    downloaded += len(chunk)
                    
                    if downloaded > MAX_FILE_SIZE:
                        await msg.reply_text("❌ حجم فایل بیش از حد مجاز است")
                        return None

                await msg.reply_text("🔐 دارم فایل رو رمزگذاری می‌کنم...")

                # ایجاد زیپ در memory
                zip_buffer = io.BytesIO()
                
                with pyzipper.AESZipFile(zip_buffer, 'w',
                                       compression=pyzipper.ZIP_DEFLATED,
                                       encryption=pyzipper.WZ_AES) as zf:
                    zf.setpassword(pwd.encode("utf-8"))
                    zf.writestr("file", bytes(file_data))

                zip_size = len(zip_buffer.getvalue())
                await msg.reply_text(f"✅ فشرده شد ({zip_size / (1024*1024):.1f} MB). دارم می‌فرستم...")

                return zip_buffer

    except Exception as e:
        await msg.reply_text(f"❌ خطا: {str(e)}")
        return None

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    text = msg.text
    pwd = parse_password(text)
    link = parse_link(text)

    if not pwd:
        await msg.reply_text("❌ رمز پیدا نشد. در پیام بنویس: pass=1234")
        return
    if not link:
        await msg.reply_text("❌ لینک فایل پیدا نشد. لینک مستقیم بده.")
        return

    await msg.reply_text("⬇️ دارم دانلود می‌کنم...")
    
    zip_buffer = await download_and_process_file(link, pwd, msg)
    
    if zip_buffer:
        try:
            # ارسال فایل
            zip_buffer.seek(0)
            await msg.reply_document(
                document=zip_buffer,
                filename="file.zip",
                caption="📦 زیپ رمزدار آماده شد."
            )
        except Exception as e:
            await msg.reply_text(f"❌ خطا در ارسال فایل: {str(e)}")

def main():
    try:
        # ساخت Application با روش جدید
        application = Application.builder().token(BOT_TOKEN).build()
        
        # اضافه کردن handlerها
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
        
        print("🤖 ربات در حال اجرا است...")
        application.run_polling()
        
    except Exception as e:
        print(f"خطا در اجرای ربات: {e}")
        logger.error(f"خطا در اجرای ربات: {e}")

if __name__ == "__main__":
    main()
