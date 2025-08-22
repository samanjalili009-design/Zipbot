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

HELP_TEXT = """
سلام 👋
📦 بات فشرده‌ساز رمزدار

📌 فایل رو برای من بفرست و در کپشنش بنویس:
/zip pass=رمزتو

مثال: 
/zip pass=1234

⚠️ حداکثر حجم: 20 مگابایت (محدودیت تلگرام)
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        msg = update.message
        
        if not msg.document:
            await msg.reply_text("❌ لطفاً یک فایل ارسال کنید.")
            return
        
        # بررسی رمز
        pwd = parse_password(msg.caption or "")
        if not pwd:
            return await msg.reply_text("❌ رمز پیدا نشد. در کپشن بنویسید: /zip pass=1234")
        
        doc = msg.document
        file_name = doc.file_name or "file"
        file_size = doc.file_size or 0
        
        # بررسی حجم (محدودیت تلگرام)
        if file_size > 20 * 1024 * 1024:
            return await msg.reply_text(
                "❌ حجم فایل بیشتر از 20MB است (محدودیت تلگرام)\n\n"
                "📌 برای فایل‌های بزرگ:\n"
                "1. فایل را با نرم‌افزار 7-Zip به قسمت‌های کوچکتر تقسیم کنید\n"
                "2. هر قسمت را جداگانه ارسال کنید"
            )
        
        await msg.reply_text("⬇️ در حال دانلود فایل...")
        
        with tempfile.TemporaryDirectory() as td:
            orig_path = os.path.join(td, file_name)
            
            # دانلود فایل
            file = await context.bot.get_file(doc.file_id)
            await file.download_to_drive(orig_path)
            
            if not os.path.exists(orig_path) or os.path.getsize(orig_path) == 0:
                return await msg.reply_text("❌ خطا در دانلود فایل")
            
            await msg.reply_text("🔒 در حال رمزگذاری...")
            
            # ایجاد زیپ
            zip_name = f"{os.path.splitext(file_name)[0]}.zip"
            zip_path = os.path.join(td, zip_name)
            
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
                logger.error(f"Zip error: {e}")
                return await msg.reply_text("❌ خطا در ایجاد فایل زیپ")
            
            if not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
                return await msg.reply_text("❌ فایل زیپ ایجاد نشد")
            
            zip_size = os.path.getsize(zip_path)
            size_mb = zip_size / (1024 * 1024)
            
            await msg.reply_text(f"✅ فایل رمزگذاری شد ({size_mb:.1f} MB)")
            
            # ارسال فایل
            with open(zip_path, 'rb') as f:
                await msg.reply_document(
                    document=InputFile(f, filename=zip_name),
                    caption=f"📦 فایل زیپ رمزدار\n🔐 رمز: {pwd}"
                )
            
            await msg.reply_text("🎉 انجام شد!")
                
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        await msg.reply_text("❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is required")
    
    try:
        # ساخت application با نسخه سازگار
        application = Application.builder().token(BOT_TOKEN).build()
        
        # اضافه کردن handlerها
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.Document.ALL, on_document))
        application.add_error_handler(error_handler)
        
        logger.info("🚀 Starting bot...")
        
        # اجرای ساده بدون پارامترهای اضافی
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")

if __name__ == "__main__":
    main()
