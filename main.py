import os
import tempfile
import pyzipper
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

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
    for part in caption.split():
        if part.startswith("pass="):
            return part.split("=", 1)[1]
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    pwd = parse_password(msg.caption)
    if not pwd:
        return await msg.reply_text("❌ رمز پیدا نشد. در کپشن بنویس: /zip pass=1234")

    doc = msg.document
    await msg.reply_text("⬇️ دارم دانلود می‌کنم...")

    with tempfile.TemporaryDirectory() as td:
        orig_path = os.path.join(td, doc.file_name or "input.bin")
        zip_path  = os.path.join(td, (doc.file_name or "file") + ".zip")

        # دانلود به حافظه
        file = await context.bot.get_file(doc.file_id)
        data = await file.download_as_bytearray()
        with open(orig_path, "wb") as f:
            f.write(data)

        # ساخت زیپ AES-256 رمزدار
        with pyzipper.AESZipFile(zip_path, 'w', compression=pyzipper.ZIP_DEFLATED) as zf:
            zf.setpassword(pwd.encode("utf-8"))
            zf.setencryption(pyzipper.WZ_AES, nbits=256)  # خیلی مهم
            zf.write(orig_path, os.path.basename(orig_path))

        size_mb = os.path.getsize(zip_path) / (1024 * 1024)
        await msg.reply_text(f"✅ فشرده شد ({size_mb:.1f} MB). دارم می‌فرستم...")

        await msg.reply_document(
            document=InputFile(zip_path, filename=os.path.basename(zip_path)),
            caption="📦 زیپ رمزدار آماده شد."
        )

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.run_polling()

if __name__ == "__main__":
    main()
