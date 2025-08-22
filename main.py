import asyncio
import os
import tempfile
from pathlib import Path
import pyzipper
from pyrogram import Client, filters
from pyrogram.types import Message
from asyncio import Queue

# ===== API و SESSION =====
API_ID = 1867911
API_HASH = "f9e86b274826212a2712b18754fabc47"
SESSION_NAME = "userbot_zip_session"

CHUNK_SIZE = 4 * 1024 * 1024  # 4MB
MAX_CONCURRENT_DOWNLOADS = 3  # تعداد همزمان دانلودها

# ===== داده کاربر =====
user_data = {
    "password": None,
    "files": [],
    "download_queue": Queue()
}

app = Client(SESSION_NAME, api_id=API_ID, api_hash=API_HASH)

# ===== Helper =====
def format_size(size_bytes: int) -> str:
    if size_bytes == 0:
        return "0 B"
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names)-1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.2f} {size_names[i]}"

async def download_file(message: Message, file_path: Path):
    file_size = message.document.file_size
    downloaded = 0

    async for chunk in message.download(file_name=None, in_memory=True):
        with open(file_path, "ab") as f:
            f.write(chunk)
        downloaded += len(chunk)
        progress = (downloaded / file_size) * 100
        await message.edit_text(f"📥 دانلود '{message.document.file_name}': {format_size(downloaded)} / {format_size(file_size)} ({int(progress)}%)")
    return file_path

async def worker_download():
    while True:
        message, temp_path = await user_data["download_queue"].get()
        try:
            await download_file(message, temp_path)
        except Exception as e:
            await message.reply(f"❌ خطا در دانلود {message.document.file_name}: {e}")
        finally:
            user_data["download_queue"].task_done()

# ===== Commands =====
@app.on_message(filters.command("zip") & filters.me)
async def start_zip(client: Client, message: Message):
    user_data["password"] = None
    user_data["files"] = []
    await message.reply("🔐 لطفاً رمز عبور برای فایل زیپ وارد کنید (حداقل 6 کاراکتر):")

@app.on_message(filters.text & filters.me)
async def receive_password(client: Client, message: Message):
    if user_data["password"] is not None:
        return
    password = message.text.strip()
    if len(password) < 6:
        await message.reply("❌ رمز حداقل 6 کاراکتر باشد. دوباره وارد کنید:")
        return
    user_data["password"] = password
    await message.reply(f"✅ رمز ذخیره شد: {password}\n📁 حالا فایل‌ها را ارسال کنید.")

@app.on_message(filters.document & filters.me)
async def receive_file(client: Client, message: Message):
    if user_data["password"] is None:
        await message.reply("❌ ابتدا /zip را اجرا کنید و رمز وارد کنید.")
        return

    temp_dir = Path(tempfile.mkdtemp())
    temp_file_path = temp_dir / message.document.file_name

    await message.reply(f"📥 فایل '{message.document.file_name}' به صف دانلود اضافه شد...")
    await user_data["download_queue"].put((message, temp_file_path))
    user_data["files"].append(str(temp_file_path))

@app.on_message(filters.command("done") & filters.me)
async def done_zip(client: Client, message: Message):
    if not user_data.get("files"):
        await message.reply("❌ هیچ فایلی ارسال نشده است.")
        return

    # ===== شروع دانلود همزمان =====
    workers = [asyncio.create_task(worker_download()) for _ in range(MAX_CONCURRENT_DOWNLOADS)]
    await user_data["download_queue"].join()
    for w in workers:
        w.cancel()

    # ===== ایجاد زیپ =====
    zip_temp_dir = Path(tempfile.mkdtemp())
    zip_file_path = zip_temp_dir / "archive.zip"

    msg = await message.reply("📦 شروع ایجاد فایل زیپ...")
    with pyzipper.AESZipFile(zip_file_path, 'w', compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(user_data["password"].encode("utf-8"))
        for file_path in user_data["files"]:
            zf.write(file_path, Path(file_path).name)

    zip_size = os.path.getsize(zip_file_path)
    await msg.edit_text(f"✅ فایل زیپ ایجاد شد!\n📦 حجم: {format_size(zip_size)}\n🔐 رمز: {user_data['password']}")

    # ===== آپلود زیپ =====
    await message.reply("⬆️ شروع آپلود فایل زیپ...")
    await client.send_document(message.chat.id, zip_file_path)

    # ===== پاکسازی =====
    for file_path in user_data["files"]:
        try:
            os.unlink(file_path)
            os.rmdir(Path(file_path).parent)
        except:
            pass
    try:
        os.unlink(zip_file_path)
        os.rmdir(zip_temp_dir)
    except:
        pass

    user_data.clear()
    await message.reply("♻️ فایل‌های موقت پاک شدند و عملیات تمام شد.")

# ===== اجرا =====
app.run()
