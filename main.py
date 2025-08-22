import os
import tempfile
import pyzipper
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import logging
import traceback

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

HELP_TEXT = """
سلام 👋
📌 فایل رو برای من بفرست و در کپشنش بنویس:
/zip pass=رمزتو
مثال:
/zip pass=1234
"""

def parse_password(caption: str | None) -> str | None:
    if not caption:
        return None
    
    # بررسی چندین فرمت مختلف
    patterns = ["pass=", "password=", "رمز=", "پسورد="]
    caption_lower = caption.lower()
    
    for pattern in patterns:
        if pattern in caption_lower:
            parts = caption.split()
            for part in parts:
                if part.lower().startswith(pattern):
                    return part.split("=", 1)[1]
    
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        msg = update.message
        
        # بررسی اینکه آیا پیام حاوی document است
        if not msg.document:
            await msg.reply_text("❌ لطفاً یک فایل ارسال کنید.")
            return
            
        pwd = parse_password(msg.caption)
        
        if not pwd:
            return await msg.reply_text("❌ رمز پیدا نشد. در کپشن بنویس: /zip pass=1234")

        doc = msg.document
        file_name = doc.file_name or "file"
        
        await msg.reply_text("⬇️ در حال دانلود فایل...")

        # دانلود فایل
        file = await context.bot.get_file(doc.file_id)
        
        with tempfile.TemporaryDirectory() as td:
            # مسیرهای فایل
            orig_path = os.path.join(td, file_name)
            zip_name = f"{os.path.splitext(file_name)[0]}.zip"
            zip_path = os.path.join(td, zip_name)

            # دانلود فایل اصلی
            await file.download_to_drive(orig_path)
            
            # بررسی وجود فایل
            if not os.path.exists(orig_path):
                return await msg.reply_text("❌ خطا در دانلود فایل")

            # بررسی حجم فایل
            file_size = os.path.getsize(orig_path)
            if file_size == 0:
                return await msg.reply_text("❌ فایل دانلود شده خالی است")

            # ساخت زیپ AES-256 رمزدار
            await msg.reply_text("🔒 در حال رمزگذاری فایل...")
            
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
                return await msg.reply_text(f"❌ خطا در ایجاد فایل زیپ: {str(e)}")

            # بررسی وجود فایل زیپ
            if not os.path.exists(zip_path):
                return await msg.reply_text("❌ فایل زیپ ایجاد نشد")

            # بررسی حجم فایل زیپ
            zip_size = os.path.getsize(zip_path)
            if zip_size == 0:
                return await msg.reply_text("❌ فایل زیپ خالی است")

            # ارسال فایل زیپ
            size_mb = zip_size / (1024 * 1024)
            
            await msg.reply_text(f"✅ فایل رمزگذاری شد ({size_mb:.1f} MB). در حال ارسال...")
            
            try:
                with open(zip_path, 'rb') as f:
                    await msg.reply_document(
                        document=InputFile(f, filename=zip_name),
                        caption=f"📦 فایل زیپ رمزدار آماده شد\n🔐 رمز: {pwd}"
                    )
            except Exception as e:
                logger.error(f"File sending error: {e}")
                await msg.reply_text(f"❌ خطا در ارسال فایل: {str(e)}")

    except Exception as e:
        error_traceback = traceback.format_exc()
        logger.error(f"General error: {error_traceback}")
        await msg.reply_text(f"❌ خطای سیستمی: {str(e)}\nلطفاً دوباره تلاش کنید.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    error_traceback = traceback.format_exc()
    logger.error(f"Update {update} caused error: {error_traceback}")
    if update and update.message:
        await update.message.reply_text("❌ خطای داخلی رخ داد. لطفاً بعداً تلاش کنید.")

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is required")
    
    try:
        app = Application.builder().token(BOT_TOKEN).build()
        
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.Document.ALL, on_document))
        app.add_error_handler(error_handler)
        
        logger.info("Bot is starting...")
        app.run_polling()
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        raise

if __name__ == "__main__":
    main()
