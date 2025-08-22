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

HELP_TEXT = """سلام👋
📦 بات فشرده‌ساز رمزدار

📌 چندتا فایل برام بفرست.
بعدش دستور رو بزن:
/zip pass=رمزتو

مثال:
/zip pass=1234

⚠️ حداکثر حجم هر فایل: 20MB (محدودیت تلگرام)
"""

# حافظه موقت برای ذخیره فایل‌های کاربر
user_files = {}

def parse_password(caption: str | None) -> str | None:
    if not caption:
        return None
    caption = caption.strip()
    if caption.startswith("/zip pass="):
        return caption.replace("/zip pass=", "").strip()
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    file = update.message.document

    # ایجاد پوشه موقت برای هر کاربر
    if user_id not in user_files:
        user_files[user_id] = []

    # دانلود فایل
    file_path = await file.get_file()
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        await file_path.download_to_drive(custom_path=tmp.name)
        user_files[user_id].append((file.file_name, tmp.name))

    await update.message.reply_text(f"✅ فایل {file.file_name} ذخیره شد. \nوقتی همه فایل‌ها رو فرستادی دستور /zip pass=رمز رو بزن.")

async def on_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    caption = update.message.text

    password = parse_password(caption)
    if not password:
        await update.message.reply_text("⚠️ لطفاً رمز رو به شکل درست بزن: /zip pass=1234")
        return

    if user_id not in user_files or not user_files[user_id]:
        await update.message.reply_text("❌ هیچ فایلی ذخیره نشده. اول فایل‌هاتو بفرست بعد دستور رو بزن.")
        return

    # ساخت فایل زیپ
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_zip:
        with pyzipper.AESZipFile(tmp_zip.name, 'w', compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zf:
            zf.setpassword(password.encode())
            for filename, filepath in user_files[user_id]:
                zf.write(filepath, arcname=filename)

        # ارسال فایل به کاربر
        await update.message.reply_document(InputFile(tmp_zip.name, filename="compressed.zip"))

    # پاک کردن فایل‌ها بعد از ارسال
    for _, filepath in user_files[user_id]:
        os.remove(filepath)
    user_files[user_id] = []

    await update.message.reply_text("✅ زیپ ساخته شد و برات فرستادم.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is required")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("zip", on_zip))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_error_handler(error_handler)

    print("🚀 Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
