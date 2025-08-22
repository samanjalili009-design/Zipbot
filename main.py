import os
import io
import aiohttp
import pyzipper
import asyncio
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN تعریف نشده! لطفاً در Render → Environment Variables اضافه کن.")

MAX_FILE_SIZE = 512 * 1024 * 1024  # 512 MB
CHUNK_SIZE = 1 * 1024 * 1024  # 1MB chunks

HELP_TEXT = """
سلام 👋
📌 لینک مستقیم فایل و رمز را بده.
مثال:
pass=1234 https://example.com/file.zip
"""

def parse_password(text: str | None) -> str | None:
    if not text:
        return None
    for part in text.split():
        if part.startswith("pass="):
            return part.split("=", 1)[1]
    return None

def parse_link(text: str | None) -> str | None:
    if not text:
        return None
    for part in text.split():
        if part.startswith("http://") or part.startswith("https://"):
            return part
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    text = msg.text
    pwd = parse_password(text)
    link = parse_link(text)

    if not pwd:
        return await msg.reply_text("❌ رمز پیدا نشد. در پیام بنویس: pass=1234")
    if not link:
        return await msg.reply_text("❌ لینک فایل پیدا نشد. لینک مستقیم بده.")

    await msg.reply_text("⬇️ دارم دانلود می‌کنم...")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(link) as resp:
                if resp.status != 200:
                    return await msg.reply_text(f"❌ دانلود موفق نبود! Status code: {resp.status}")

                total = int(resp.headers.get("Content-Length", 0))
                if total > MAX_FILE_SIZE:
                    return await msg.reply_text(f"❌ حجم فایل بیش از 512MB است ({total / (1024*1024):.1f} MB)")

                # دانلود به memory
                file_data = bytearray()
                downloaded = 0
                
                async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                    file_data.extend(chunk)
                    downloaded += len(chunk)
                    
                    # بررسی حجم برای جلوگیری از مصرف بیش از حد memory
                    if downloaded > MAX_FILE_SIZE:
                        return await msg.reply_text("❌ حجم فایل بیش از حد مجاز است")

                await msg.reply_text("🔐 دارم فایل رو رمزگذاری می‌کنم...")

                # ایجاد زیپ در memory
                zip_buffer = io.BytesIO()
                
                with pyzipper.AESZipFile(zip_buffer, 'w',
                                       compression=pyzipper.ZIP_DEFLATED,
                                       encryption=pyzipper.WZ_AES) as zf:
                    zf.setpassword(pwd.encode("utf-8"))
                    zf.writestr("file", bytes(file_data))

                zip_size = len(zip_buffer.getvalue())
                await msg.reply_text(f"✅ فشرده شد ({zip_size / (1024*1024):.1f} MB). دارم می‌فرستم...")

                # ارسال فایل
                zip_buffer.seek(0)
                await msg.reply_document(
                    document=InputFile(zip_buffer, filename="file.zip"),
                    caption="📦 زیپ رمزدار آماده شد."
                )

    except aiohttp.ClientError as e:
        await msg.reply_text(f"❌ خطا در دانلود: {str(e)}")
    except Exception as e:
        await msg.reply_text(f"❌ خطای غیرمنتظره: {str(e)}")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    
    print("🤖 ربات در حال اجرا است...")
    app.run_polling()

if __name__ == "__main__":
    main()
