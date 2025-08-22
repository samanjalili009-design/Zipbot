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
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB - محدودیت تلگرام

HELP_TEXT = """
سلام 👋
📦 بات فشرده‌ساز رمزدار

📌 برای فایل‌های کوچک (تا ۲۰ مگابایت):
• فایل را ارسال کنید
• در کپشن بنویسید: /zip pass=رمزتون

📌 برای فایل‌های بزرگ (تا ۳۰۰ مگابایت):
1. فایل را با نرم‌افزار 7-Zip یا WinRAR به قسمت‌های ۲۰ مگابایتی تقسیم کنید
2. قسمت اول را ارسال کنید و در کپشن بنویسید: /bigzip pass=رمزتون
3. بات به شما دستورالعمل ارسال قسمت‌های بعدی را می‌دهد

مثال: /zip pass=1234
مثال: /bigzip pass=1234
"""

def parse_password(text: str) -> str | None:
    """استخراج رمز از متن"""
    if not text:
        return None
    
    patterns = ["pass=", "password=", "رمز=", "پسورد="]
    text_lower = text.lower()
    
    for pattern in patterns:
        if pattern in text_lower:
            parts = text.split()
            for part in parts:
                if part.lower().startswith(pattern):
                    return part.split("=", 1)[1]
    
    return None

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def handle_small_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پردازش فایل‌های کوچک (تا 20MB)"""
    try:
        msg = update.message
        
        if not msg.document:
            await msg.reply_text("❌ لطفاً یک فایل ارسال کنید.")
            return
        
        # بررسی حجم فایل
        doc = msg.document
        file_size = doc.file_size or 0
        
        if file_size > MAX_FILE_SIZE:
            await msg.reply_text(
                f"❌ فایل شما {file_size//1024//1024}MB است که بیشتر از حد مجاز (20MB) می‌باشد.\n\n"
                "📌 برای فایل‌های بزرگ:\n"
                "1. فایل را با 7-Zip به قسمت‌های 20MB تقسیم کنید\n"
                "2. دستور /bigzip را استفاده کنید"
            )
            return
        
        # استخراج رمز
        pwd = parse_password(msg.caption or "")
        if not pwd:
            return await msg.reply_text("❌ رمز پیدا نشد. در کپشن بنویسید: /zip pass=1234")
        
        await msg.reply_text("⬇️ در حال دانلود فایل...")
        
        with tempfile.TemporaryDirectory() as td:
            # دانلود فایل
            file = await context.bot.get_file(doc.file_id)
            orig_path = os.path.join(td, doc.file_name or "file")
            await file.download_to_drive(orig_path)
            
            if not os.path.exists(orig_path):
                return await msg.reply_text("❌ خطا در دانلود فایل")
            
            # ایجاد زیپ
            zip_name = f"{os.path.splitext(os.path.basename(orig_path))[0]}.zip"
            zip_path = os.path.join(td, zip_name)
            
            await msg.reply_text("🔒 در حال رمزگذاری فایل...")
            
            success = await create_encrypted_zip(orig_path, zip_path, pwd)
            if not success or not os.path.exists(zip_path):
                return await msg.reply_text("❌ خطا در ایجاد فایل زیپ")
            
            # ارسال فایل
            zip_size = os.path.getsize(zip_path)
            size_mb = zip_size / (1024 * 1024)
            
            await msg.reply_text(f"✅ فایل رمزگذاری شد ({size_mb:.1f} MB)")
            
            with open(zip_path, 'rb') as f:
                await msg.reply_document(
                    document=InputFile(f, filename=zip_name),
                    caption=f"📦 فایل زیپ رمزدار\n🔐 رمز: {pwd}"
                )
                
    except Exception as e:
        logger.error(f"Small zip error: {e}")
        await update.message.reply_text("❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.")

async def handle_big_zip_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارسال اطلاعات برای فایل‌های بزرگ"""
    info_text = """
📦 راهنمای فایل‌های بزرگ:

1. 🔧 فایل خود را با نرم‌افزار 7-Zip یا WinRAR به قسمت‌های ۲۰ مگابایتی تقسیم کنید

2. 📤 قسمت اول فایل را ارسال کنید و در کپشن بنویسید:
   /bigzip pass=رمزتون

3. 🔄 بات به شما می‌گوید که قسمت بعدی را ارسال کنید

4. 🎯 بعد از ارسال تمام قسمتها، بات فایل نهایی را برای شما می‌سازد

💡 نکته: اسم فایل‌های تقسیم شده باید به صورت زیر باشد:
   filename.7z.001
   filename.7z.002
   filename.7z.003
   ...

📥 برای دانلود 7-Zip:
   https://www.7-zip.org/
"""
    await update.message.reply_text(info_text)

async def handle_big_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هدایت کاربر به راهنمای فایل‌های بزرگ"""
    await update.message.reply_text(
        "📦 برای فایل‌های بزرگ، لطفاً ابتدا راهنمای زیر را مطالعه کنید:\n"
        "دستور: /bigzipinfo"
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error: {context.error}")

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is required")
    
    try:
        app = Application.builder().token(BOT_TOKEN).build()
        
        # handlers
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("bigzipinfo", handle_big_zip_info))
        app.add_handler(CommandHandler("bigzip", handle_big_zip))
        
        # handler برای فایل‌های معمولی با کپشن /zip
        app.add_handler(MessageHandler(
            filters.Document.ALL & filters.CaptionRegex(r'^/zip'), 
            handle_small_zip
        ))
        
        # handler برای سایر فایل‌ها (بدون کپشن مناسب)
        app.add_handler(MessageHandler(
            filters.Document.ALL, 
            lambda update, context: update.message.reply_text(
                "❌ لطفاً از دستور /zip در کپشن استفاده کنید\n"
                "مثال: /zip pass=1234\n\n"
                "برای فایل‌های بزرگ: /bigzipinfo"
            )
        ))
        
        app.add_error_handler(error_handler)
        
        logger.info("Bot is starting...")
        app.run_polling()
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")

if __name__ == "__main__":
    main()
