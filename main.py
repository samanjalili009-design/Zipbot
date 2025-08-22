import os
import tempfile
import pyzipper
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler
import logging
from typing import Dict

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = "@flvst1"  # یوزرنیم کانال/گروه بدون @
CHANNEL_ID = -1001093039800  # آیدی عددی کانال/گروه (منفی باشد)

# حالت‌های گفتگو
WAITING_PASSWORD, WAITING_FILES = range(2)
user_data: Dict[int, Dict] = {}

HELP_TEXT = f"""سلام👋 
📦بات فشرده‌ساز رمزدار

📌 برای استفاده از بات ابتدا باید در کانال ما عضو شوید:
{CHANNEL_USERNAME}

✅ پس از عضویت، از دستورات زیر استفاده کنید:

1. دستور /zip را ارسال کنید
2. رمز دلخواه خود را وارد نمایید
3. فایل‌های خود را یکی پس از دیگری ارسال کنید
4. پس از اتمام ارسال فایل‌ها، دستور /done را ارسال کنید

⚠️ حداکثر حجم هر فایل: 20 مگابایت (محدودیت تلگرام)
⚠️ حداکثر تعداد فایل‌ها در یک آرشیو: 10 فایل"""

async def check_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """چک کردن عضویت کاربر در کانال"""
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Error checking membership: {e}")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # چک عضویت
    if not await check_membership(user_id, context):
        await update.message.reply_text(
            f"❌ برای استفاده از بات باید ابتدا در کانال ما عضو شوید:\n"
            f"{CHANNEL_USERNAME}\n\n"
            f"✅ پس از عضویت، دوباره /start را ارسال کنید."
        )
        return
    
    await update.message.reply_text(HELP_TEXT)

async def zip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # چک عضویت
    if not await check_membership(user_id, context):
        await update.message.reply_text(
            f"❌ برای استفاده از بات باید ابتدا در کانال ما عضو شوید:\n"
            f"{CHANNEL_USERNAME}\n\n"
            f"✅ پس از عضویت، دوباره امتحان کنید."
        )
        return ConversationHandler.END
    
    user_data[user_id] = {'files': [], 'password': None}
    
    await update.message.reply_text("لطفاً رمز مورد نظر برای فایل زیپ را وارد کنید:")
    return WAITING_PASSWORD

async def receive_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # چک عضویت
    if not await check_membership(user_id, context):
        await update.message.reply_text(
            f"❌ برای استفاده از بات باید ابتدا در کانال ما عضو شوید:\n"
            f"{CHANNEL_USERNAME}\n\n"
            f"✅ پس از عضویت، دوباره امتحان کنید."
        )
        return ConversationHandler.END
    
    password = update.message.text.strip()
    
    if not password:
        await update.message.reply_text("رمز نمی‌تواند خالی باشد. لطفاً رمز معتبر وارد کنید:")
        return WAITING_PASSWORD
    
    user_data[user_id]['password'] = password
    
    await update.message.reply_text(
        f"رمز '{password}' ذخیره شد.\n"
        "لطفاً فایل‌های خود را ارسال کنید. پس از اتمام، دستور /done را ارسال کنید."
    )
    return WAITING_FILES

async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # چک عضویت
    if not await check_membership(user_id, context):
        await update.message.reply_text(
            f"❌ برای استفاده از بات باید ابتدا در کانال ما عضو شوید:\n"
            f"{CHANNEL_USERNAME}\n\n"
            f"✅ پس از عضویت، دوباره امتحان کنید."
        )
        return ConversationHandler.END
    
    if user_id not in user_data or user_data[user_id]['password'] is None:
        await update.message.reply_text("لطفاً ابتدا دستور /zip را اجرا کنید.")
        return ConversationHandler.END
    
    if len(user_data[user_id]['files']) >= 10:
        await update.message.reply_text("حداکثر تعداد فایل‌ها (10) رسیده است. لطفاً /done را ارسال کنید.")
        return WAITING_FILES
    
    try:
        document = update.message.document
        file = await document.get_file()
        
        # ایجاد پوشه موقت برای ذخیره فایل
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            await file.download_to_drive(temp_file.name)
            user_data[user_id]['files'].append({
                'name': document.file_name,
                'path': temp_file.name
            })
        
        remaining = 10 - len(user_data[user_id]['files'])
        await update.message.reply_text(
            f"فایل '{document.file_name}' دریافت شد. ({remaining} فایل دیگر قابل دریافت است)\n"
            "فایل بعدی را ارسال کنید یا برای اتمام /done را ارسال کنید."
        )
        return WAITING_FILES
    except Exception as e:
        logger.error(f"Error receiving file: {e}")
        await update.message.reply_text("خطا در دریافت فایل. لطفاً دوباره امتحان کنید.")
        return WAITING_FILES

async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # چک عضویت
    if not await check_membership(user_id, context):
        await update.message.reply_text(
            f"❌ برای استفاده از بات باید ابتدا در کانال ما عضو شوید:\n"
            f"{CHANNEL_USERNAME}\n\n"
            f"✅ پس از عضویت، دوباره امتحان کنید."
        )
        return ConversationHandler.END
    
    if user_id not in user_data or not user_data[user_id]['files']:
        await update.message.reply_text("هیچ فایلی دریافت نشده است. لطفاً ابتدا فایل‌ها را ارسال کنید.")
        return ConversationHandler.END
    
    try:
        await update.message.reply_text("در حال ایجاد فایل زیپ... لطفاً منتظر بمانید.")
        
        # ایجاد فایل زیپ رمزدار
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as zip_file:
            with pyzipper.AESZipFile(
                zip_file.name, 
                'w', 
                compression=pyzipper.ZIP_DEFLATED,
                encryption=pyzipper.WZ_AES
            ) as zf:
                zf.setpassword(user_data[user_id]['password'].encode('utf-8'))
                
                for file_info in user_data[user_id]['files']:
                    zf.write(file_info['path'], file_info['name'])
            
            # ارسال فایل زیپ
            zip_file.seek(0)
            await update.message.reply_document(
                document=InputFile(zip_file, filename='archive.zip'),
                caption=f"فایل زیپ با رمز '{user_data[user_id]['password']}' ایجاد شد.\n\n"
                        f"✅ از حمایت شما متشکریم! {CHANNEL_USERNAME}"
            )
        
        # پاکسازی فایل‌های موقت
        for file_info in user_data[user_id]['files']:
            try:
                os.unlink(file_info['path'])
            except:
                pass
        try:
            os.unlink(zip_file.name)
        except:
            pass
        
        await update.message.reply_text(
            "✅ فایل زیپ با موفقیت ایجاد و ارسال شد!\n\n"
            f"💚 از کانال ما حمایت کنید: {CHANNEL_USERNAME}"
        )
        
    except Exception as e:
        logger.error(f"Error creating zip: {e}")
        await update.message.reply_text("❌ خطا در ایجاد فایل زیپ. لطفاً دوباره امتحان کنید.")
    
    finally:
        # پاکسازی داده کاربر
        if user_id in user_data:
            del user_data[user_id]
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # پاکسازی فایل‌های موقت
    if user_id in user_data:
        for file_info in user_data[user_id]['files']:
            try:
                os.unlink(file_info['path'])
            except:
                pass
        del user_data[user_id]
    
    await update.message.reply_text("عملیات کنسل شد.")
    return ConversationHandler.END

async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور برای چک کردن عضویت"""
    user_id = update.effective_user.id
    
    if await check_membership(user_id, context):
        await update.message.reply_text(
            "✅ شما در کانال عضو هستید!\n\n"
            "اکنون می‌توانید از بات استفاده کنید:\n"
            "/zip - شروع فرآیند فشرده‌سازی"
        )
    else:
        await update.message.reply_text(
            f"❌ شما در کانال عضو نیستید!\n\n"
            f"لطفاً در کانال ما عضو شوید:\n"
            f"{CHANNEL_USERNAME}\n\n"
            f"سپس دوباره /check را ارسال کنید."
        )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")
    if update and update.message:
        await update.message.reply_text("❌ خطایی رخ داده است. لطفاً دوباره امتحان کنید.")

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable is required")
        raise ValueError("BOT_TOKEN environment variable is required")
    
    if not CHANNEL_USERNAME or not CHANNEL_ID:
        logger.error("CHANNEL_USERNAME and CHANNEL_ID must be set")
        raise ValueError("CHANNEL_USERNAME and CHANNEL_ID must be set")
    
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
        # handlers
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('zip', zip_command)],
            states={
                WAITING_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_password)],
                WAITING_FILES: [
                    MessageHandler(filters.Document.ALL, receive_file),
                    CommandHandler('done', done_command)
                ],
            },
            fallbacks=[CommandHandler('cancel', cancel)],
        )
        
        application.add_handler(CommandHandler('start', start))
        application.add_handler(CommandHandler('check', check_subscription))
        application.add_handler(conv_handler)
        application.add_error_handler(error_handler)
        
        logger.info("Bot is starting...")
        print("🤖 Bot is starting with mandatory subscription...")
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        print(f"❌ Failed to start bot: {e}")

if __name__ == "__main__":
    main()
