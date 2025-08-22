import os
import tempfile
import pyzipper
from telegram import Update, InputFile
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
import logging
import shutil

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

HELP_TEXT = """
سلام 👋
📦 بات فشرده‌ساز چند فایلی

📌 مراحل کار:
1. فایل‌های مورد نظر را یکی یکی ارسال کنید
2. بعد از اتمام آپلود فایل‌ها، دستور /done را بفرستید
3. سپس رمز مورد نظر را ارسال کنید

💡 دستورات:
/start - نمایش راهنما
/done - اتمام آپلود فایل‌ها
/cancel - لغو عملیات

⚠️ محدودیت‌ها:
- حداکثر 10 فایل
- حداکثر حجم کل: 50 مگابایت
- هر فایل حداکثر 20 مگابایت
"""

# دیکشنری برای ذخیره اطلاعات کاربران
user_data = {}

class UserSession:
    def __init__(self, user_id):
        self.user_id = user_id
        self.files = []  # لیست فایل‌ها
        self.step = 'waiting_for_files'  # مراحل: waiting_for_files, waiting_for_password
        self.total_size = 0
        self.temp_dir = tempfile.mkdtemp()
    
    def add_file(self, file_id, file_name, file_size):
        if len(self.files) >= 10:
            return False, "❌ حداکثر 10 فایل مجاز است"
        
        if self.total_size + file_size > 50 * 1024 * 1024:
            return False, "❌ حجم کل فایل‌ها بیشتر از 50MB است"
        
        if file_size > 20 * 1024 * 1024:
            return False, "❌ حجم هر فایل حداکثر 20MB می‌باشد"
        
        self.files.append({
            'file_id': file_id,
            'file_name': file_name,
            'file_size': file_size
        })
        self.total_size += file_size
        return True, f"✅ فایل '{file_name}' اضافه شد ({file_size//1024}KB)"

    def cleanup(self):
        """پاک کردن فایل‌های موقت"""
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except:
            pass

def get_user_session(user_id):
    """دریافت یا ایجاد session کاربر"""
    if user_id not in user_data:
        user_data[user_id] = UserSession(user_id)
    return user_data[user_id]

def start(update: Update, context: CallbackContext):
    update.message.reply_text(HELP_TEXT)

def on_document(update: Update, context: CallbackContext):
    try:
        msg = update.message
        user_id = msg.from_user.id
        session = get_user_session(user_id)
        
        if session.step != 'waiting_for_files':
            msg.reply_text("❌ لطفاً ابتدا عملیات قبلی را کامل کنید یا /cancel بزنید")
            return
        
        doc = msg.document
        file_name = doc.file_name or f"file_{len(session.files) + 1}"
        file_size = doc.file_size or 0
        
        # افزودن فایل به session
        success, message = session.add_file(doc.file_id, file_name, file_size)
        msg.reply_text(message)
        
        if success:
            status_text = (
                f"📊 وضعیت فعلی:\n"
                f"📁 تعداد فایل‌ها: {len(session.files)}\n"
                f"💾 حجم کل: {session.total_size//1024//1024}MB\n\n"
                f"📌 فایل بعدی را ارسال کنید یا /done بزنید"
            )
            msg.reply_text(status_text)
                
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        msg.reply_text("❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.")

def done_command(update: Update, context: CallbackContext):
    """اتمام آپلود فایل‌ها"""
    try:
        msg = update.message
        user_id = msg.from_user.id
        
        if user_id not in user_data:
            msg.reply_text("❌ هیچ فایلی ارسال نکرده‌اید")
            return
        
        session = user_data[user_id]
        
        if len(session.files) == 0:
            msg.reply_text("❌ هیچ فایلی ارسال نکرده‌اید")
            return
        
        session.step = 'waiting_for_password'
        
        status_text = (
            f"✅ آپلود فایل‌ها کامل شد\n"
            f"📁 تعداد: {len(session.files)} فایل\n"
            f"💾 حجم کل: {session.total_size//1024//1024}MB\n\n"
            f"🔐 لطفاً رمز مورد نظر را ارسال کنید:"
        )
        msg.reply_text(status_text)
        
    except Exception as e:
        logger.error(f"Done error: {e}")
        msg.reply_text("❌ خطایی رخ داد")

def cancel_command(update: Update, context: CallbackContext):
    """لغو عملیات"""
    try:
        msg = update.message
        user_id = msg.from_user.id
        
        if user_id in user_data:
            user_data[user_id].cleanup()
            del user_data[user_id]
        
        msg.reply_text("✅ عملیات لغو شد")
        
    except Exception as e:
        logger.error(f"Cancel error: {e}")
        msg.reply_text("❌ خطایی رخ داد")

def on_text(update: Update, context: CallbackContext):
    """پردازش رمز ارسالی کاربر"""
    try:
        msg = update.message
        user_id = msg.from_user.id
        text = msg.text.strip()
        
        if user_id not in user_data:
            msg.reply_text("❌ لطفاً ابتدا فایل‌ها را ارسال کنید")
            return
        
        session = user_data[user_id]
        
        if session.step != 'waiting_for_password':
            msg.reply_text("❌ لطفاً ابتدا فایل‌ها را ارسال و /done کنید")
            return
        
        if not text:
            msg.reply_text("❌ رمز نمی‌تواند خالی باشد")
            return
        
        # شروع پردازش
        msg.reply_text("⏳ در حال پردازش فایل‌ها...")
        context.dispatcher.run_async(process_files, user_id, msg, text)
        
    except Exception as e:
        logger.error(f"Text processing error: {e}")
        msg.reply_text("❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.")

def process_files(user_id, message, password):
    """پردازش همه فایل‌ها و ایجاد زیپ"""
    try:
        if user_id not in user_data:
            message.reply_text("❌ اطلاعات فایل‌ها یافت نشد")
            return
        
        session = user_data[user_id]
        
        message.reply_text("⬇️ در حال دانلود فایل‌ها...")
        
        # دانلود همه فایل‌ها
        downloaded_files = []
        for i, file_info in enumerate(session.files):
            try:
                file_path = os.path.join(session.temp_dir, file_info['file_name'])
                
                file = message.bot.get_file(file_info['file_id'])
                file.download(file_path)
                
                if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                    downloaded_files.append(file_path)
                    progress = f"✅ دانلود فایل {i+1} از {len(session.files)}"
                    message.reply_text(progress)
                else:
                    message.reply_text(f"❌ خطا در دانلود فایل {file_info['file_name']}")
                    
            except Exception as e:
                logger.error(f"Download error for {file_info['file_name']}: {e}")
                message.reply_text(f"❌ خطا در دانلود فایل {file_info['file_name']}")
        
        if not downloaded_files:
            message.reply_text("❌ هیچ فایلی دانلود نشد")
            return
        
        message.reply_text("🔒 در حال ایجاد فایل زیپ رمزدار...")
        
        # ایجاد فایل زیپ
        from datetime import datetime
        zip_name = f"archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        zip_path = os.path.join(session.temp_dir, zip_name)
        
        try:
            with pyzipper.AESZipFile(
                zip_path, 
                'w', 
                compression=pyzipper.ZIP_DEFLATED,
                encryption=pyzipper.WZ_AES
            ) as zf:
                zf.setpassword(password.encode('utf-8'))
                
                for file_path in downloaded_files:
                    zf.write(file_path, os.path.basename(file_path))
                    
        except Exception as e:
            logger.error(f"Zip error: {e}")
            message.reply_text("❌ خطا در ایجاد فایل زیپ")
            return
        
        if not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
            message.reply_text("❌ فایل زیپ ایجاد نشد")
            return
        
        zip_size = os.path.getsize(zip_path)
        size_mb = zip_size / (1024 * 1024)
        
        # ارسال فایل زیپ
        message.reply_text(f"✅ فایل زیپ ایجاد شد ({size_mb:.1f} MB)")
        
        with open(zip_path, 'rb') as f:
            message.reply_document(
                document=InputFile(f, filename=zip_name),
                caption=(
                    f"📦 فایل زیپ رمزدار\n"
                    f"🔐 رمز: {password}\n"
                    f"📁 تعداد فایل‌ها: {len(downloaded_files)}\n"
                    f"💾 حجم: {size_mb:.1f}MB"
                )
            )
        
        message.reply_text("🎉 عملیات با موفقیت完成 شد!")
        
        # پاکسازی
        session.cleanup()
        if user_id in user_data:
            del user_data[user_id]
            
    except Exception as e:
        logger.error(f"Process error: {e}")
        message.reply_text("❌ خطایی در پردازش فایل‌ها رخ داد")
        
        # پاکسازی در صورت خطا
        if user_id in user_data:
            user_data[user_id].cleanup()
            del user_data[user_id]

def error_handler(update: Update, context: CallbackContext):
    logger.error(f"Error: {context.error}")

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is required")
    
    try:
        # استفاده از Updater به جای Application
        updater = Updater(token=BOT_TOKEN, use_context=True)
        dispatcher = updater.dispatcher
        
        # اضافه کردن handlerها
        dispatcher.add_handler(CommandHandler("start", start))
        dispatcher.add_handler(CommandHandler("done", done_command))
        dispatcher.add_handler(CommandHandler("cancel", cancel_command))
        dispatcher.add_handler(MessageHandler(Filters.document, on_document))
        dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, on_text))
        dispatcher.add_error_handler(error_handler)
        
        logger.info("🚀 Starting multi-file zip bot...")
        updater.start_polling()
        updater.idle()
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")

if __name__ == "__main__":
    main()
