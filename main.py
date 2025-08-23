import os
import asyncio
import tempfile
import time
import pyzipper
import logging
from pyrogram import Client
from telegram import Update, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)

# ===== تنظیمات =====
BOT_TOKEN = "8145993181:AAFK7PeFs_9VsHqaP3iKagj9lWTNJXKpgjk"
ACCOUNT_HASH = "f9e86b274826212a2712b18754fabc47"
ALLOWED_USER_ID = 1867911
MAX_FILE_SIZE = 2097152000  # 2GB پیش‌فرض

# Userbot (برای آپلود به حساب خودتان)
API_ID = 1867911  # می‌تونه همون آیدی باشه یا از my.telegram.org گرفته شود
API_HASH = "f9e86b274826212a2712b18754fabc47"
userbot = Client("userbot_session", api_id=API_ID, api_hash=API_HASH)

# حالت گفتگو
WAITING_FOR_PASSWORD = 1

# لاگ
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def is_user_allowed(user_id: int) -> bool:
    return user_id == ALLOWED_USER_ID

# ===== دستورات ربات =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام 👋\nفایل‌تو بفرست تا برات زیپ کنم (رمزدار هم میشه).\n"
        "💡 کپشن فایل = pass=رمز برای تعیین پسورد"
    )

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user_allowed(update.effective_user.id):
        return await update.message.reply_text("❌ دسترسی denied.")

    if not update.message.document:
        return await update.message.reply_text("فقط فایل بفرست 🌹")

    doc = update.message.document
    file_id = doc.file_id
    caption = update.message.caption or ""
    password = None
    if caption.startswith("pass="):
        password = caption.split("=", 1)[1].strip()

    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        return await update.message.reply_text(
            f"❌ حجم فایل بیش از حد مجاز است! (حداکثر {MAX_FILE_SIZE // 1024 // 1024}MB)"
        )

    if "files" not in context.user_data:
        context.user_data["files"] = []

    context.user_data["files"].append({"file_id": file_id, "file_name": doc.file_name, "password": password})
    await update.message.reply_text(
        f"✅ فایل '{doc.file_name}' ذخیره شد.\n"
        f"📝 برای زیپ کردن همه فایل‌ها /zip را بزنید"
    )

async def ask_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user_allowed(update.effective_user.id):
        await update.message.reply_text("❌ دسترسی denied.")
        return ConversationHandler.END

    if "files" not in context.user_data or not context.user_data["files"]:
        await update.message.reply_text("❌ هیچ فایلی برای زیپ کردن وجود ندارد.")
        return ConversationHandler.END

    await update.message.reply_text(
        "🔐 لطفاً رمز عبور برای فایل زیپ وارد کن (اگر قبلاً روی فایل مشخص کردی، همون استفاده میشه):"
    )
    return WAITING_FOR_PASSWORD

async def zip_files_with_userbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_password = update.message.text.strip()
    if not user_password:
        await update.message.reply_text("❌ رمز عبور نمی‌تواند خالی باشد.")
        return WAITING_FOR_PASSWORD

    processing_msg = await update.message.reply_text("⏳ در حال ایجاد فایل زیپ...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        zip_file_name = f"archive_{int(time.time())}.zip"
        zip_path = os.path.join(tmp_dir, zip_file_name)

        with pyzipper.AESZipFile(
            zip_path,
            "w",
            compression=pyzipper.ZIP_DEFLATED,
            encryption=pyzipper.WZ_AES,
        ) as zipf:
            zipf.setpassword(user_password.encode())

            async with userbot:
                for i, f in enumerate(context.user_data["files"], 1):
                    try:
                        file_path = os.path.join(tmp_dir, f["file_name"])
                        await userbot.download_media(f["file_id"], file_path)
                        zip_password = f["password"] or user_password
                        if zip_password:
                            zipf.setpassword(zip_password.encode())
                        zipf.write(file_path, f["file_name"])
                        if i % 2 == 0:
                            try:
                                await processing_msg.edit_text(f"⏳ در حال پردازش... ({i}/{len(context.user_data['files'])})")
                            except:
                                pass
                    except Exception as e:
                        logger.error(f"Error processing file {f['file_name']}: {e}")
                        continue

        # ارسال فایل زیپ فقط از طریق Userbot
        await update.message.reply_document(InputFile(zip_path), caption=f"✅ فایل زیپ آماده شد!\n🔐 رمز: {user_password}")

    context.user_data["files"] = []
    try:
        await processing_msg.delete()
    except:
        pass

    return ConversationHandler.END

async def cancel_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ عملیات زیپ لغو شد.")
    return ConversationHandler.END

async def clear_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "files" in context.user_data and context.user_data["files"]:
        count = len(context.user_data["files"])
        context.user_data["files"] = []
        await update.message.reply_text(f"✅ {count} فایل ذخیره شده پاک شدند.")
    else:
        await update.message.reply_text("📭 هیچ فایلی برای پاک کردن وجود ندارد.")

# ===== ران اصلی =====
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("zip", ask_password)],
        states={WAITING_FOR_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, zip_files_with_userbot)]},
        fallbacks=[CommandHandler("cancel", cancel_zip)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(CommandHandler("cancel", cancel_zip))
    app.add_handler(CommandHandler("clear", clear_files))

    app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
