import asyncio
import os
import tempfile
from pathlib import Path
import pyzipper
from pyrogram import Client, filters
from pyrogram.types import Message

# ===== API و SESSION =====
API_ID = 1867911
API_HASH = "f9e86b274826212a2712b18754fabc47"
SESSION_NAME = "userbot_zip_session"

# ===== داده کاربر =====
user_data = {
    "password": None,
    "files": []
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

    await message.reply(f"📥 دانلود فایل '{message.document.file_name}'...")
    await message.download(file_name=temp_file_path)
    await message.reply(f"✅ فایل '{message.document.file_name}' دانلود شد.")

    user_data["files"].append(str(temp_file_path))

@app.on_message(filters.command("done") & filters.me)
async def done_zip(client: Client, message: Message):
    if not user_data.get("files"):
        await message.reply("❌ هیچ فایلی ارسال نشده است.")
        return

    zip_temp_dir = Path(tempfile.mkdtemp())
    zip_file_path = zip_temp_dir / "archive.zip"

    msg = await message.reply("📦 شروع ایجاد فایل زیپ...")
    with pyzipper.AESZipFile(zip_file_path, 'w', compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(user_data["password"].encode("utf-8"))
        for file_path in user_data["files"]:
            zf.write(file_path, Path(file_path).name)

    zip_size = os.path.getsize(zip_file_path)
    await msg.edit_text(f"✅ فایل زیپ ایجاد شد!\n📦 حجم: {format_size(zip_size)}\n🔐 رمز: {user_data['password']}")

    await message.reply("⬆️ شروع آپلود فایل زیپ...")
    await client.send_document(message.chat.id, zip_file_path)

    # ===== پاکسازی =====
    for file_path in user_data["files"]:
        try:
            file_path_obj = Path(file_path)
            if file_path_obj.exists():
                file_path_obj.unlink()
            if file_path_obj.parent.exists():
                file_path_obj.parent.rmdir()
        except:
            pass

    try:
        if zip_file_path.exists():
            zip_file_path.unlink()
        if zip_temp_dir.exists():
            zip_temp_dir.rmdir()
    except:
        pass

    user_data.clear()
    await message.reply("♻️ فایل‌های موقت پاک شدند و عملیات تمام شد.")

# ===== اجرا =====
app.run()
