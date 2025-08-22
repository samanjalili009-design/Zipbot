import os
import tempfile
import pyzipper
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import logging
import math

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB
TELEGRAM_LIMIT = 50 * 1024 * 1024  # 50MB (محدودیت تلگرام)

HELP_TEXT = """
سلام 👋
📌 فایل رو برای من بفرست و در کپشنش بنویس:
/zip pass=رمزتو
مثال:
/zip pass=1234

⚠️ حداکثر حجم فایل: 500 مگابایت
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

async def split_large_file(file_path, chunk_size=TELEGRAM_LIMIT):
    """تقسیم فایل بزرگ به چند قسمت"""
    chunks = []
    file_name = os.path.basename(file_path)
    
    with open(file_path, 'rb') as f:
        chunk_number = 1
        while True:
            chunk_data = f.read(chunk_size)
            if not chunk_data:
                break
            
            chunk_filename = f"{file_name}.part{chunk_number:03d}"
            chunk_path = os.path.join(os.path.dirname(file_path), chunk_filename)
            
            with open(chunk_path, 'wb') as chunk_file:
                chunk_file.write(chunk_data)
            
            chunks.append(chunk_path)
            chunk_number += 1
    
    return chunks

async def send_file_chunks(message, file_path, caption=""):
    """ارسال فایل به صورت چندتایی اگر بزرگ باشد"""
    file_size = os.path.getsize(file_path)
    
    if file_size <= TELEGRAM_LIMIT:
        # فایل کوچک است، ارسال عادی
        with open(file_path, 'rb') as f:
            await message.reply_document(
                document=InputFile(f, filename=os.path.basename(file_path)),
                caption=caption
            )
    else:
        # فایل بزرگ است، تقسیم به چند قسمت
        chunks = await split_large_file(file_path)
        total_chunks = len(chunks)
        
        await message.reply_text(f"📦 فایل به {total_chunks} قسمت تقسیم شد. در حال ارسال...")
        
        for i, chunk_path in enumerate(chunks, 1):
            with open(chunk_path, 'rb') as f:
                await message.reply_document(
                    document=InputFile(f, filename=os.path.basename(chunk_path)),
                    caption=f"{caption}\n📁 قسمت {i} از {total_chunks}"
                )
            # حذف فایل موقت بعد از ارسال
            os.unlink(chunk_path)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        msg = update.message
        pwd = parse_password(msg.caption)
        
        if not pwd:
            return await msg.reply_text("❌ رمز پیدا نشد. در کپشن بنویس: /zip pass=1234")

        doc = msg.document
        file_name = doc.file_name or "file"
        file_size = doc.file_size or 0
        
        # بررسی حجم فایل
        if file_size > MAX_FILE_SIZE:
            return await msg.reply_text(f"❌ حجم فایل بیشتر از {MAX_FILE_SIZE//1024//1024}MB است")
        
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

            # ساخت زیپ AES-256 رمزدار
            await msg.reply_text("🔒 در حال رمزگذاری فایل...")
            
            with pyzipper.AESZipFile(
                zip_path, 
                'w', 
                compression=pyzipper.ZIP_DEFLATED,
                encryption=pyzipper.WZ_AES
            ) as zf:
                zf.setpassword(pwd.encode('utf-8'))
                zf.write(orig_path, os.path.basename(orig_path))

            # بررسی وجود فایل زیپ
            if not os.path.exists(zip_path):
                return await msg.reply_text("❌ خطا در ایجاد فایل زیپ")

            # بررسی حجم فایل زیپ
            zip_size = os.path.getsize(zip_path)
            size_mb = zip_size / (1024 * 1024)
            
            if zip_size > MAX_FILE_SIZE:
                return await msg.reply_text("❌ حجم فایل زیپ بیش از حد مجاز است")

            await msg.reply_text(f"✅ فایل رمزگذاری شد ({size_mb:.1f} MB). در حال ارسال...")
            
            # ارسال فایل (به صورت چندتایی اگر بزرگ باشد)
            caption = f"📦 فایل زیپ رمزدار آماده شد\n🔐 رمز: {pwd}\n📊 حجم: {size_mb:.1f}MB"
            await send_file_chunks(msg, zip_path, caption)

    except Exception as e:
        logger.error(f"Error: {e}")
        await msg.reply_text("❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is required")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_error_handler(error_handler)
    
    logger.info("Bot is starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
