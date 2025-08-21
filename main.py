import os
import tempfile
import asyncio
import pyzipper
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("8133176558:AAE4uj57yovjfDJMg-aDdhRaovjFdMGJCWw")  # از محیط Render ست کن

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

        file = await context.bot.get_file(doc.file_id)
        await file.download_to_drive(custom_path=orig_path)

        # ساخت زیپ رمزدار
        with pyzipper.AESZipFile(zip_path, 'w',
                                 compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES) as zf:
            zf.setpassword(pwd.encode("utf-8"))
            arcname = os.path.basename(orig_path)
            zf.write(orig_path, arcname)

        size_mb = os.path.getsize(zip_path) / (1024 * 1024)
        await msg.reply_text(f"✅ فشرده شد ({size_mb:.1f} MB). دارم می‌فرستم...")

        await msg.reply_document(
            document=InputFile(zip_path, filename=os.path.basename(zip_path)),
            caption="📦 زیپ رمزدار آماده شد."
        )

async def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
