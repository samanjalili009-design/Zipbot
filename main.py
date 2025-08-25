import os
import time
import tempfile
import pyzipper
import logging
import sys
import asyncio
import math
from typing import List, Dict
from pyrogram import Client, filters
from pyrogram.types import Message
from flask import Flask
import threading
from concurrent.futures import ThreadPoolExecutor

# ===== تنظیمات کاربر =====
API_ID = 26180086
API_HASH = "d91e174c7faf0e5a6a3a2ecb0b3361f6"
SESSION_STRING = "BAGPefYAEAaXaj52wzDLPF0RSfWtF_Slk8nFWzYAHS9vu-HBxRUz9yLnq7m8z-ajYCQxQZO-5aNX0he9OttDjmjieYDMbDjBJtbsOT2ZwsQNe8UCAo5oFPveD5V1H0cIBMlXCG1P49G2oonf1YL1r16Nt34AJLkmzDIoFD0hhxwVBXvrUGwZmEoTtdkfORCYUMGACKO4-Al-NH35oVCkTIqmXQ5DUp9PVx6DND243VW5Xcqay7qwrwfoS4sWRA-7TMXykbHa37ZsdcCOf0VS8e6PyaYvG5BjMCd9BGRnR9IImrksYY2uBM2Bg42MLaa1WFxQtn97p5ViPF9c1MpY49bc5Gm5lwAAAAF--TK5AA"
ALLOWED_USER_ID = 417536686

# ===== محدودیت‌ها و اندازه‌ها =====
MB = 1024 * 1024
GB = 1024 * MB
MAX_TOTAL_SIZE = 4 * GB
ZIP_VOLUME_LIMIT = int(800 * MB)  # کاهش حجم هر جلد برای پایداری بیشتر
LARGE_FILE_PART_SIZE = 700 * MB
ZIP_SAFETY_MARGIN = 20 * MB
MAX_SINGLE_FILE_SIZE = 4 * GB

# ===== لاگ =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("telezip")

# ===== کلاینت Pyrogram =====
app: Client = None
executor = ThreadPoolExecutor(max_workers=2)

# ===== داده‌ها =====
user_files: Dict[int, List[Dict]] = {}
waiting_for_password: Dict[int, bool] = {}
waiting_for_filename: Dict[int, bool] = {}
zip_password_storage: Dict[int, str] = {}

# ===== ابزارها =====

def is_user_allowed(user_id: int) -> bool:
    return user_id == ALLOWED_USER_ID

async def safe_edit(msg: Message, text: str):
    try:
        await msg.edit_text(text)
    except Exception:
        pass

async def progress_bar(current, total, message: Message, start_time, stage="دانلود"):
    now = time.time()
    diff = max(now - start_time, 1e-6)
    percent = int(current * 100 / max(total, 1))
    speed = current / diff
    eta = int((total - current) / max(speed, 1e-6)) if speed > 0 else 0
    bar_filled = int(percent / 5)
    bar = "▓" * bar_filled + "░" * (20 - bar_filled)
    text = f"""
🚀 {stage} فایل...

{bar} {percent}%

📦 {current//MB}MB / {total//MB}MB
⚡️ سرعت: {round(speed/1024,2)} KB/s
⏳ زمان باقی‌مانده: {eta}s
    """
    await safe_edit(message, text)

async def run_in_thread(func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, lambda: func(*args, **kwargs))

async def split_large_file(file_path: str, part_size: int = LARGE_FILE_PART_SIZE) -> List[str]:
    part_files = []
    file_size = os.path.getsize(file_path)
    if file_size <= part_size:
        return [file_path]
    base_dir = os.path.dirname(file_path)
    base_name = os.path.basename(file_path)
    with open(file_path, 'rb') as src:
        part_num = 1
        while True:
            chunk = src.read(part_size)
            if not chunk: break
            part_filename = f"{base_name}.part{part_num:03d}"
            part_path = os.path.join(base_dir, part_filename)
            with open(part_path, 'wb') as dst: dst.write(chunk)
            part_files.append(part_path)
            part_num += 1
    try: os.remove(file_path)
    except: pass
    return part_files

async def plan_zip_volumes(files: List[Dict], tmp_dir: str, processing_msg: Message) -> List[List[Dict]]:
    prepared: List[Dict] = []
    for finfo in files:
        path = finfo["path"]
        arcname = finfo["name"]
        if not os.path.exists(path): continue
        size = os.path.getsize(path)
        if size > MAX_SINGLE_FILE_SIZE: raise ValueError("فایل خیلی بزرگ است")
        if size > LARGE_FILE_PART_SIZE:
            await safe_edit(processing_msg, f"✂️ تقسیم فایل بزرگ: {arcname}")
            parts = await split_large_file(path, LARGE_FILE_PART_SIZE)
            for i, part in enumerate(parts, start=1):
                prepared.append({"path": part, "arcname": f"{arcname}.part{i:03d}", "size": os.path.getsize(part)})
        else:
            prepared.append({"path": path, "arcname": arcname, "size": size})
    volumes: List[List[Dict]] = []
    current: List[Dict] = []
    current_size = 0
    for item in prepared:
        item_size = item["size"]
        if current and (current_size + item_size + ZIP_SAFETY_MARGIN > ZIP_VOLUME_LIMIT):
            volumes.append(current)
            current = [item]
            current_size = item_size
        else:
            current.append(item)
            current_size += item_size
    if current: volumes.append(current)
    return volumes

def create_encrypted_zip(zip_path: str, password: str, entries: List[Dict], processing_msg: Message, vol_idx: int, vol_count: int):
    with pyzipper.AESZipFile(zip_path, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zipf:
        zipf.setpassword(password.encode())
        total = len(entries)
        for idx, e in enumerate(entries, start=1):
            zipf.write(e["path"], e["arcname"])
            try: os.remove(e["path"])
            except: pass

# ===== هندلرها =====
async def start(client: Client, message: Message):
    if not is_user_allowed(message.from_user.id): return await message.reply_text("❌ دسترسی غیرمجاز")
    await message.reply_text("سلام 👋 فایل‌هایت را بفرست و بعد /zip بزن")

async def handle_file(client: Client, message: Message):
    if not is_user_allowed(message.from_user.id): return
    doc = message.document
    if not doc: return
    size = doc.file_size or 0
    if size > MAX_SINGLE_FILE_SIZE: return await message.reply_text("❌ فایل خیلی بزرگ است")
    file_name = (doc.file_name or f"file_{message.id}").replace(os.sep, "_")
    user_files.setdefault(message.from_user.id, []).append({"message": message, "file_name": file_name, "file_size": size})
    await message.reply_text(f"✅ فایل دریافت شد: {file_name} ({size//MB}MB)")

async def start_zip(client: Client, message: Message):
    user_id = message.from_user.id
    if not is_user_allowed(user_id): return
    if user_id not in user_files or not user_files[user_id]: return await message.reply_text("❌ فایل برای زیپ وجود ندارد")
    total_size = sum(f.get("file_size",0) for f in user_files[user_id])
    if total_size > MAX_TOTAL_SIZE: user_files[user_id] = [] ; return await message.reply_text("❌ حجم کل فایل‌ها زیاد است")
    waiting_for_password[user_id] = True
    await message.reply_text("🔐 رمز زیپ را وارد کن /cancel برای لغو")

async def cancel_zip(client: Client, message: Message):
    user_id = message.from_user.id
    user_files.pop(user_id,None)
    waiting_for_password.pop(user_id,None)
    waiting_for_filename.pop(user_id,None)
    zip_password_storage.pop(user_id,None)
    await message.reply_text("❌ عملیات لغو شد")

non_command = filters.create(lambda _,__,m: bool(m.text and not m.text.startswith('/')))

async def process_zip(client: Client, message: Message):
    user_id = message.from_user.id
    if not is_user_allowed(user_id): return
    if waiting_for_password.get(user_id):
        zip_password = message.text.strip()
        if not zip_password: return await message.reply_text("❌ رمز خالی است")
        zip_password_storage[user_id] = zip_password
        waiting_for_password.pop(user_id,None)
        waiting_for_filename[user_id] = True
        return await message.reply_text("📝 نام فایل زیپ را وارد کن")
    if waiting_for_filename.get(user_id):
        base_name = message.text.strip()
        if not base_name: return await message.reply_text("❌ نام فایل خالی است")
        waiting_for_filename.pop(user_id,None)
        processing_msg = await message.reply_text("⏳ آماده‌سازی...")
        zip_password = zip_password_storage.pop(user_id,"")
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                files_to_zip = []
                items = list(user_files.get(user_id,[]))
                for i,finfo in enumerate(items,start=1):
                    file_msg = finfo["message"]
                    file_name = finfo["file_name"]
                    dest_path = os.path.join(tmp_dir,file_name)
                    start_time = time.time()
                    await client.download_media(file_msg,dest_path,progress=progress_bar,progress_args=(processing_msg,start_time,"دانلود"))
                    if os.path.exists(dest_path) and os.path.getsize(dest_path)>0: files_to_zip.append({"path":dest_path,"name":file_name})
                    await safe_edit(processing_msg,f"📥 دانلود {i}/{len(items)}")
                if not files_to_zip: user_files[user_id]=[]; return await message.reply_text("❌ فایل برای پردازش نیست")
                volumes = await plan_zip_volumes(files_to_zip,tmp_dir,processing_msg)
                vol_count = len(volumes)
                for v_idx,entries in enumerate(volumes,start=1):
                    zip_file_name = f"{base_name}.zip" if vol_count==1 else f"{base_name}_part{v_idx:02d}.zip"
                    zip_path = os.path.join(tmp_dir,zip_file_name)
                    await run_in_thread(create_encrypted_zip,zip_path,zip_password,entries,processing_msg,v_idx,vol_count)
                    start_time=time.time()
                    caption = f"✅ جلد {v_idx}/{vol_count} آماده!\n🔑 رمز: `{zip_password}`\n📦 نام: {os.path.basename(zip_path)}"
                    await client.send_document(message.chat.id,zip_path,caption=caption,progress=progress_bar,progress_args=(processing_msg,start_time,"آپلود"))
                    try: os.remove(zip_path)
                    except: pass
                await safe_edit(processing_msg,"🎉 همهٔ جلدها ارسال شد")
        except Exception as e: logger.error(f"Error: {e}",exc_info=True); await message.reply_text("❌ خطا هنگام ساخت یا ارسال زیپ")
        finally: user_files[user_id]=[]

async def run_bot():
    global app
    logger.info("Starting user bot…")
    app=Client("user_bot",api_id=API_ID,api_hash=API_HASH,session_string=SESSION_STRING,in_memory=True,workdir=tempfile.gettempdir())
    app.on_message(filters.command("start"))(start)
    app.on_message(filters.document)(handle_file)
    app.on_message(filters.command("zip"))(start_zip)
    app.on_message(filters.command("cancel"))(cancel_zip)
    app.on_message(filters.text & non_command)(process_zip)
    await app.start()
    logger.info("Bot started")
    await asyncio.Event().wait()

if __name__ == "__main__":
    web_app=Flask(__name__)

    @web_app.route('/')
    def home():
        return "Bot is running", 200

    @web_app.route('/health')
    def health_check():
        return "OK", 200

    def start_bot_thread():
        loop=asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try: loop.run_until_complete(run_bot())
        except Exception as e: logger.error(f"Bot error: {e}")
        finally: loop.close()

    t=threading.Thread(target=start_bot_thread,daemon=True)
    t.start()

    port=int(os.environ.get("PORT",10000))
    logger.info(f"Starting Flask server on port {port}")
    web_app.run(host="0.0.0.0",port=port,debug=False,use_reloader=False)
