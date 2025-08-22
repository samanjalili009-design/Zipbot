import os
import io
import aiohttp
import pyzipper
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.request import HTTPXRequest

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN تعریف نشده!")

MAX_FILE_SIZE = 512 * 1024 * 1024
CHUNK_SIZE = 1 * 1024 * 1024

HELP_TEXT = """
سلام 👋
📌 لینک مستقیم فایل و رمز را بده.
مثال:
pass=1234 https://example.com/file.zip
"""

def parse_password(text):
    if not text: return None
    for part in text.split():
        if part.startswith("pass="):
            return part.split("=", 1)[1]
    return None

def parse_link(text):
    if not text: return None
    for part in text.split():
        if part.startswith("http://") or part.startswith("https://"):
            return part
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # کد قبلی بدون تغییر
    pass

def main():
    # استفاده از HTTPXRequest برای نسخه‌های جدید
    request = HTTPXRequest(connection_pool_size=8)
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    
    print("🤖 ربات در حال اجرا است...")
    app.run_polling()

if __name__ == "__main__":
    main()
