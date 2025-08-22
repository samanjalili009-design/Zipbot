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

📌 مراحل کار:
1. فایل رو ارسال کن (تا 50 مگابایت)
2. بعد از دانلود، رمز مورد نظر رو بفرست

مثال رمز: 
1234

⚠️ حداکثر حجم: 50 مگابایت
"""

# دیکشنری برای ذخیره اطلاعات کاربران
user_data = {}

def parse_password(text: str) -> str:
    """استخراج رمز از متن"""
    return text.strip()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        msg = update.message
        user_id = msg.from_user.id
        
        if not msg.document:
            await msg.reply_text("❌ لطفاً یک فایل ارسال کنید.")
            return
        
        doc = msg.document
        file_name = doc.file_name or "file"
        file_size = doc.file_size or 0
        
        # بررسی حجم فایل
        if file_size > 50 * 1024 * 1024:
            return await msg.reply_text(
                "❌ حجم فایل بیشتر از 50MB است\n\n"
                "لطفاً فایل کوچکتری ارسال کنید"
            )
        
        # ذخیره اطلاعات فایل برای کاربر
        user_data[user_id] = {
            'file_id': doc.file_id,
            'file_name': file_name,
            'file_size': file_size,
            'step': 'waiting_for_password'
        }
        
        await msg.reply_text(
            "✅ فایل دریافت شد!\n\n"
            "🔐 لطفاً رمز مورد نظر را ارسال کنید:\n"
            "(فقط رمز را بنویسید، مثلاً: 1234)"
        )
                
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        await msg.reply_text("❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.")

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پردازش رمز ارسالی کاربر"""
    try:
        msg = update.message
        user_id = msg.from_user.id
        text = msg.text.strip()
        
        # بررسی آیا کاربر در مرحله وارد کردن رمز است
        if user_id not in user_data or user_data[user_id]['step'] != 'waiting_for_password':
            await msg.reply_text("❌ لطفاً ابتدا یک فایل ارسال کنید.")
            return
        
        if not text:
            await msg.reply_text("❌ رمز نمی‌تواند خالی باشد.")
            return
        
        # ذخیره رمز
        user_data[user_id]['password'] = text
        user_data[user_id]['step'] = 'processing'
        
        await msg.reply_text("⏳ در حال پردازش فایل...")
        
        # پردازش فایل
        await process_file(user_id, msg)
        
    except Exception as e:
        logger.error(f"Text processing error: {e}")
        await msg.reply_text("❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.")

async def process_file(user_id, message):
    """پردازش فایل و ایجاد زیپ رمزدار"""
    try:
        user_info = user_data.get(user_id)
        if not user_info:
            await message.reply_text("❌ اطلاعات فایل یافت نشد.")
            return
        
        file_id = user_info['file_id']
        file_name = user_info['file_name']
        password = user_info['password']
        
        await message.reply_text("⬇️ در حال دانلود فایل...")
        
        with tempfile.TemporaryDirectory() as td:
            orig_path = os.path.join(td, file_name)
            
            # دانلود فایل
            file = await message._bot.get_file(file_id)
            await file.download_to_drive(orig_path)
            
            if not os.path.exists(orig_path) or os.path.getsize(orig_path) == 0:
                await message.reply_text("❌ خطا در دانلود فایل")
                return
            
            await message.reply_text("🔒 در حال رمزگذاری...")
            
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
                    zf.setpassword(password.encode('utf-8'))
                    zf.write(orig_path, os.path.basename(orig_path))
            except Exception as e:
                logger.error(f"Zip error: {e}")
                await message.reply_text("❌ خطا در ایجاد فایل زیپ")
                return
            
            if not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
                await message.reply_text("❌ فایل زیپ ایجاد نشد")
                return
            
            zip_size = os.path.getsize(zip_path)
            size_mb = zip_size / (1024 * 1024)
            
            await message.reply_text(f"✅ فایل رمزگذاری شد ({size_mb:.1f} MB)")
            
            # بررسی اگر فایل زیپ بزرگتر از 50MB شد، تقسیم کنیم
            if zip_size > 50 * 1024 * 1024:
                await split_and_send_file(message, zip_path, password)
            else:
                # ارسال مستقیم فایل
                with open(zip_path, 'rb') as f:
                    await message.reply_document(
                        document=InputFile(f, filename=zip_name),
                        caption=f"📦 فایل زیپ رمزدار\n🔐 رمز: {password}"
                    )
            
            await message.reply_text("🎉 انجام شد!")
            
            # پاک کردن اطلاعات کاربر
            if user_id in user_data:
                del user_data[user_id]
                
    except Exception as e:
        logger.error(f"Process error: {e}")
        await message.reply_text("❌ خطایی در پردازش فایل رخ داد.")

async def split_and_send_file(message, file_path, password):
    """تقسیم و ارسال فایل‌های بزرگ"""
    try:
        file_size = os.path.getsize(file_path)
        chunk_size = 45 * 1024 * 1024  # 45MB برای اطمینان
        total_chunks = (file_size + chunk_size - 1) // chunk_size
        
        await message.reply_text(f"📦 فایل به {total_chunks} قسمت تقسیم شد...")
        
        file_name = os.path.basename(file_path)
        
        with open(file_path, 'rb') as f:
            for i in range(total_chunks):
                # خواندن chunk
                chunk_data = f.read(chunk_size)
                if not chunk_data:
                    break
                
                # ذخیره موقت
                chunk_filename = f"{file_name}.part{i+1:03d}"
                chunk_path = os.path.join(os.path.dirname(file_path), chunk_filename)
                
                with open(chunk_path, 'wb') as chunk_file:
                    chunk_file.write(chunk_data)
                
                # ارسال chunk
                with open(chunk_path, 'rb') as chunk_file:
                    await message.reply_document(
                        document=InputFile(chunk_file, filename=chunk_filename),
                        caption=f"📦 قسمت {i+1} از {total_chunks}\n🔐 رمز: {password}"
                    )
                
                # حذف فایل موقت
                try:
                    os.remove(chunk_path)
                except:
                    pass
                
                await asyncio.sleep(1)  # تأخیر بین ارسال
        
    except Exception as e:
        logger.error(f"Split error: {e}")
        await message.reply_text("❌ خطا در تقسیم فایل")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is required")
    
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
        # اضافه کردن handlerها
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.Document.ALL, on_document))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
        application.add_error_handler(error_handler)
        
        logger.info("🚀 Starting bot with 50MB support...")
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")

if __name__ == "__main__":
    main()
