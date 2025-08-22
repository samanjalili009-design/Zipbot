import os
import io
import aiohttp
import pyzipper
import logging
from telegram import Update, InputFile
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

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

def start(update: Update, context: CallbackContext):
    update.message.reply_text(HELP_TEXT)

async def on_text(update: Update, context: CallbackContext):
    msg = update.message
    text = msg.text
    pwd = parse_password(text)
    link = parse_link(text)

    if not pwd:
        return await msg.reply_text("❌ رمز پیدا نشد. در پیام بنویس: pass=1234")
    if not link:
        return await msg.reply_text("❌ لینک فایل پیدا نشد. لینک مستقیم بده.")

    await msg.reply_text("⬇️ دارم دانلود می‌کنم...")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(link) as resp:
                if resp.status != 200:
                    return await msg.reply_text(f"❌ دانلود موفق نبود! Status code: {resp.status}")

                total = int(resp.headers.get("Content-Length", 0))
                if total > MAX_FILE_SIZE:
                    return await msg.reply_text(f"❌ حجم فایل بیش از 512MB است ({total / (1024*1024):.1f} MB)")

                # دانلود به memory
                file_data = bytearray()
                downloaded = 0
                
                async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                    file_data.extend(chunk)
                    downloaded += len(chunk)
                    
                    if downloaded > MAX_FILE_SIZE:
                        return await msg.reply_text("❌ حجم فایل بیش از حد مجاز است")

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

                # ارسال فایل
                zip_buffer.seek(0)
                await msg.reply_document(
                    document=InputFile(zip_buffer, filename="file.zip"),
                    caption="📦 زیپ رمزدار آماده شد."
                )

    except Exception as e:
        await msg.reply_text(f"❌ خطا: {str(e)}")

def main():
    try:
        updater = Updater(BOT_TOKEN, use_context=True)
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
