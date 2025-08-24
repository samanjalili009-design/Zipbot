import os
import time
import tempfile
import pyzipper
import logging
import sys
from pyrogram import Client, filters
from pyrogram.types import Message

# ===== تنظیمات =====
API_ID = 1867911
API_HASH = "f9e86b274826212a2712b18754fabc47"
SESSION_STRING = "BAAcgIcAp7vwU3nnTi-xRZN3D_0rGdAPZN1qv1Pedm9p6zcuDZk_5zYJaTdpnsiobnWymDG28cvHU09pjJiSwTK1lCV98QUyPg9sjUyTQTmbIMRBCxuc-eJLYNKq4TBqrvvqbTbELSMkTyAwbPr36vB2b3WyYZPXqRzZfGjbYPiHJMnIz6TRZ6PKwGxEIj4PBK6hZ1DckYbmEm1Z-LFny8NQdpZ3mDsQzSVyxOrdZHZjFhcBfRnjA3GkAg5kLCCOhbUTY9xvLhS9XrEaEfm2CBxVFkZGwSu-tK0neYa2L0mNIT00PV3FD9-KzWo3uZSxnuaFKiM3w3cE1ymgKcGBa_0e6VJp1QAAAAAY4xquAA"
ALLOWED_USER_ID = 417536686
MAX_FILE_SIZE = 2097152000  # 2GB
MAX_TOTAL_SIZE = 2097152000  # 2GB

# ===== لاگ =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ===== کلاینت =====
app = Client(
    "user_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    in_memory=True
)

# ===== داده‌ها =====
user_files = {}
waiting_for_password = {}
ZIP_PASSWORD = b"1234"

# ===== فانکشن‌ها =====
def is_user_allowed(user_id: int) -> bool:
    return user_id == ALLOWED_USER_ID

async def progress_bar(current, total, message: Message, start_time, stage="دانلود"):
    now = time.time()
    diff = now - start_time
    if diff == 0: diff = 1
    percent = int(current * 100 / total)
    speed = current / diff
    eta = int((total - current) / speed) if speed > 0 else 0
    bar_filled = int(percent / 5)
    bar = "▓" * bar_filled + "░" * (20 - bar_filled)
    text = f"""
🚀 {stage} فایل...

{bar} {percent}%

📦 {current//1024//1024}MB / {total//1024//1024}MB
⚡️ سرعت: {round(speed/1024,2)} KB/s
⏳ زمان باقی‌مانده: {eta}s
    """
    try: await message.edit_text(text)
    except: pass

# ===== هندلرها =====
@app.on_message(filters.command("start"))
async def start(client, message):
    if not is_user_allowed(message.from_user.id):
        return await message.reply_text("❌ دسترسی denied.")
    await message.reply_text(
        "سلام 👋\nفایل‌تو بفرست تا برات زیپ کنم (رمزدار هم میشه).\n"
        f"💡 کپشن فایل = pass=رمز برای تعیین پسورد\n"
        f"📦 حداکثر حجم هر فایل: {MAX_FILE_SIZE//1024//1024}MB\n"
        f"📦 حداکثر حجم کل: {MAX_TOTAL_SIZE//1024//1024}MB\n"
        f"🔑 پسورد فعلی: `{ZIP_PASSWORD.decode()}`\n"
        "برای تغییر پسورد دستور زیر رو بزن:\n`/setpass پسوردجدید`"
    )

@app.on_message(filters.command("setpass"))
async def set_password(client, message):
    global ZIP_PASSWORD
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply_text("⚠️ پسورد جدید رو وارد کن.\nمثال: `/setpass mypass`")
    ZIP_PASSWORD = args[1].strip().encode()
    await message.reply_text(f"✅ پسورد تغییر کرد!\n🔑 پسورد جدید: `{args[1].strip()}`")

@app.on_message(filters.document)
async def handle_file(client, message):
    if not is_user_allowed(message.from_user.id):
        return await message.reply_text("❌ دسترسی denied.")
    doc = message.document
    if not doc:
        return await message.reply_text("فقط فایل بفرست 🌹")
    file_name = doc.file_name or f"file_{message.id}"
    caption = message.caption or ""
    password = None
    if "pass=" in caption:
        password = caption.split("pass=",1)[1].split()[0].strip()
    if doc.file_size > MAX_FILE_SIZE:
        return await message.reply_text(f"❌ حجم فایل بیش از حد مجاز است! ({MAX_FILE_SIZE//1024//1024}MB)")
    user_id = message.from_user.id
    if user_id not in user_files: user_files[user_id] = []
    user_files[user_id].append({"message": message, "file_name": file_name, "password": password, "file_size": doc.file_size})
    total_size = sum(f["file_size"] for f in user_files[user_id])
    await message.reply_text(f"✅ فایل '{file_name}' ذخیره شد.\n📦 حجم کل: {total_size//1024//1024}MB\n📝 برای زیپ کردن همه فایل‌ها /zip را بزنید")

@app.on_message(filters.command("list"))
async def list_files(client, message):
    if not is_user_allowed(message.from_user.id): return await message.reply_text("❌ دسترسی denied.")
    user_id = message.from_user.id
    if user_id not in user_files or not user_files[user_id]:
        return await message.reply_text("📭 هیچ فایلی ذخیره نشده است.")
    files_list = "\n".join([f"📄 {f['file_name']} ({f['file_size']//1024//1024}MB)" for f in user_files[user_id]])
    total_size = sum(f["file_size"] for f in user_files[user_id])
    await message.reply_text(f"📋 فایل‌های ذخیره شده:\n{files_list}\n\n📦 حجم کل: {total_size//1024//1024}MB\n🔢 تعداد: {len(user_files[user_id])} فایل")

@app.on_message(filters.command("clear"))
async def clear_files(client, message):
    if not is_user_allowed(message.from_user.id): return await message.reply_text("❌ دسترسی denied.")
    user_id = message.from_user.id
    if user_id in user_files and user_files[user_id]:
        count = len(user_files[user_id])
        user_files[user_id] = []
        waiting_for_password.pop(user_id,None)
        await message.reply_text(f"✅ {count} فایل ذخیره شده پاک شدند.")
    else: await message.reply_text("📭 هیچ فایلی برای پاک کردن وجود ندارد.")

@app.on_message(filters.command("zip"))
async def start_zip(client, message):
    if not is_user_allowed(message.from_user.id): return await message.reply_text("❌ دسترسی denied.")
    user_id = message.from_user.id
    if user_id not in user_files or not user_files[user_id]:
        return await message.reply_text("❌ هیچ فایلی برای زیپ کردن وجود ندارد.")
    total_size = sum(f["file_size"] for f in user_files[user_id])
    if total_size > MAX_TOTAL_SIZE:
        await message.reply_text(f"❌ حجم کل فایل‌ها بیش از حد مجاز است! ({MAX_TOTAL_SIZE//1024//1024}MB)")
        user_files[user_id] = []
        return
    await message.reply_text("🔐 لطفاً رمز عبور برای فایل زیپ وارد کن (اگر قبلاً روی فایل مشخص کردی، همون استفاده میشه):\n❌ برای لغو /cancel را بزنید")
    waiting_for_password[user_id] = True

@app.on_message(filters.command("cancel"))
async def cancel_zip(client, message):
    if not is_user_allowed(message.from_user.id): return await message.reply_text("❌ دسترسی denied.")
    user_id = message.from_user.id
    if user_id in user_files: user_files[user_id] = []
    waiting_for_password.pop(user_id,None)
    await message.reply_text("❌ عملیات لغو شد.")

# ===== هندلر پسورد و زیپ =====
def non_command_filter(_, __, message: Message):
    return message.text and not message.text.startswith('/')
non_command = filters.create(non_command_filter)

@app.on_message(filters.text & non_command)
async def process_zip_password(client, message):
    if not is_user_allowed(message.from_user.id): return
    user_id = message.from_user.id
    if user_id not in waiting_for_password or not waiting_for_password[user_id]: return
    zip_password = message.text.strip()
    if not zip_password: return await message.reply_text("❌ رمز عبور نمی‌تواند خالی باشد.")
    waiting_for_password.pop(user_id,None)
    processing_msg = await message.reply_text("⏳ در حال ایجاد فایل زیپ...")

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_file_name = f"archive_{int(time.time())}.zip"
            zip_path = os.path.join(tmp_dir, zip_file_name)
            with pyzipper.AESZipFile(zip_path,"w",compression=pyzipper.ZIP_DEFLATED,encryption=pyzipper.WZ_AES) as zipf:
                zipf.setpassword(zip_password.encode())
                total_files = len(user_files[user_id])
                successful_files = 0
                for i, finfo in enumerate(user_files[user_id],1):
                    file_msg = finfo["message"]
                    file_name = finfo["file_name"]
                    file_password = finfo["password"] or zip_password
                    file_path = os.path.join(tmp_dir,file_name)
                    await processing_msg.edit_text(f"📥 دانلود: {file_name}\n📊 فایل {i} از {total_files}")
                    start_time = time.time()
                    file_path = await client.download_media(file_msg,file_path,progress=progress_bar,progress_args=(processing_msg,start_time,"دانلود"))
                    if os.path.exists(file_path) and os.path.getsize(file_path)>0:
                        zipf.setpassword(file_password.encode())
                        zipf.write(file_path,file_name)
                        successful_files +=1
                    os.remove(file_path)
                if successful_files==0: return await message.reply_text("❌ هیچ فایلی موفق نشد.")
                await processing_msg.edit_text("📤 در حال آپلود فایل زیپ...")
                start_time = time.time()
                await client.send_document(message.chat.id,zip_path,caption=f"✅ فایل زیپ آماده شد!\n🔑 رمز: `{zip_password}`\n📦 {successful_files}/{total_files}",progress=progress_bar,progress_args=(processing_msg,start_time,"آپلود"))
    except Exception as e:
        logger.error(f"Error in zip: {e}",exc_info=True)
        await message.reply_text("❌ خطایی رخ داد.")
    finally:
        user_files[user_id] = []

# ===== اجرا =====
if __name__ == "__main__":
    logger.info("Starting user bot...")
    app.run()
