      import os
import logging
import asyncio
from telegram import Update
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    ContextTypes, 
    ConversationHandler,
    filters
)
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneNumberInvalidError

# تنظیمات
BOT_TOKEN = "8145993181:AAFK7PeFs_9VsHqaP3iKagj9lWTNJXKpgjk"
API_ID = "1867911"  # از my.telegram.org با VPN بگیرید
API_HASH = "f9e86b274826212a2712b18754fabc47"  # از my.telegram.org با VPN بگیرید

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# مراحل مکالمه
PHONE, CODE, PASSWORD = range(3)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع فرآیند ساخت سشن"""
    await update.message.reply_text(
        "🔐 **ربات ساخت StringSession**\n\n"
        "برای ساخت سشن دستور /newsession رو ارسال کنید\n\n"
        "⚠️ حتماً از VPN استفاده کنید"
    )

async def new_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع ساخت سشن جدید"""
    await update.message.reply_text(
        "📱 لطفاً شماره تلفن خود را با کد کشور ارسال کنید:\n"
        "مثال: +989123456789\n\n"
        "❌ برای لغو: /cancel"
    )
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت شماره تلفن"""
    phone = update.message.text.strip()
    context.user_data['phone'] = phone
    
    try:
        # ایجاد کلاینت Telethon
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        
        # ارسال کد تأیید
        sent_code = await client.send_code_request(phone)
        context.user_data['client'] = client
        context.user_data['phone_code_hash'] = sent_code.phone_code_hash
        
        await update.message.reply_text(
            "📲 کد تأیید برای شما ارسال شد.\n"
            "لطفاً کد 5 رقمی را ارسال کنید:\n\n"
            "❌ برای لغو: /cancel"
        )
        return CODE
        
    except PhoneNumberInvalidError:
        await update.message.reply_text("❌ شماره تلفن نامعتبر است. لطفاً دوباره尝试 کنید:")
        return PHONE
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {str(e)}")
        return ConversationHandler.END

async def get_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت کد تأیید"""
    code = update.message.text.strip()
    client = context.user_data['client']
    phone = context.user_data['phone']
    phone_code_hash = context.user_data['phone_code_hash']
    
    try:
        # تلاش برای ورود با کد
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        
        # اگر موفق بود، سشن را ذخیره کن
        session_str = client.session.save()
        
        await update.message.reply_text(
            f"✅ **StringSession ساخته شد!**\n\n"
            f"🔐 Session String:\n`{session_str}`\n\n"
            f"💡 این رشته را در جای امن ذخیره کنید.\n"
            f"📱 شماره: `{phone}`",
            parse_mode="Markdown"
        )
        
        await client.disconnect()
        return ConversationHandler.END
        
    except SessionPasswordNeededError:
        await update.message.reply_text(
            "🔒 حساب شما رمز دو مرحله‌ای دارد.\n"
            "لطفاً رمز دو مرحله‌ای را ارسال کنید:"
        )
        return PASSWORD
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در ورود: {str(e)}")
        await client.disconnect()
        return ConversationHandler.END

async def get_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت رمز دو مرحله‌ای"""
    password = update.message.text.strip()
    client = context.user_data['client']
    
    try:
        # ورود با رمز دو مرحله‌ای
        await client.sign_in(password=password)
        
        session_str = client.session.save()
        
        await update.message.reply_text(
            f"✅ **StringSession ساخته شد!**\n\n"
            f"🔐 Session String:\n`{session_str}`\n\n"
            f"💡 این رشته را در جای امن ذخیره کنید.",
            parse_mode="Markdown"
        )
        
        await client.disconnect()
        return ConversationHandler.END
        
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در ورود: {str(e)}")
        await client.disconnect()
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لغو عملیات"""
    if 'client' in context.user_data:
        await context.user_data['client'].disconnect()
    
    await update.message.reply_text("❌ عملیات لغو شد.")
    return ConversationHandler.END

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت خطاها"""
    logger.error(f"Error: {context.error}")
    try:
        await update.message.reply_text("❌ خطایی رخ داد. لطفاً دوباره尝试 کنید.")
    except:
        pass

def main():
    """تابع اصلی"""
    try:
        # ایجاد اپلیکیشن
        application = Application.builder().token(BOT_TOKEN).build()
        
        # handler مکالمه
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('newsession', new_session)],
            states={
                PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
                CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_code)],
                PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_password)],
            },
            fallbacks=[CommandHandler('cancel', cancel)],
            allow_reentry=True
        )
        
        # اضافه کردن handlerها
        application.add_handler(CommandHandler("start", start))
        application.add_handler(conv_handler)
        application.add_error_handler(error_handler)
        
        logger.info("ربات در حال اجرا است...")
        application.run_polling()
        
    except Exception as e:
        logger.error(f"خطا در اجرای ربات: {e}")

if __name__ == "__main__":
    main()
    
