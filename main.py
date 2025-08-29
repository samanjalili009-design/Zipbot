import os
import time
import pyzipper
import logging
import asyncio
from pyrogram import Client, filters
from flask import Flask
import threading
from typing import Dict, List, Any
import gc

# ===== تنظیمات =====
class Config:
    API_ID = 1867911
    API_HASH = "f9e86b274826212a2712b18754fabc47"
    SESSION_STRING = "BAAcgIcAHFzRCBK8bXUoRyPGXLQaXCUVVc8YRwzBkm7m9RHERF-DwcIcuO1XunQeFNnVTsCgpeR4yfVY-qygIVL-ayKd4FXec1Q0AJSwwMztT_JNgRjlIupm9OaujoI68FlcNibGtCYEkktHPWlN7I8F5ux34MWBQbK3v6DIXfKyAza3yCksCwYI7YoZz7-Ay2d3XK2S_GDqcNW3DF-PGGc-ZAnpdPe11aDiX1vwpDjXm0pV0_Cw5GeHgLUm6LcZ1PwPLvIkUDhhGsR3cFYHHrxjS4SuD-cgb4Zjv9r7zBJ5HGaGnBPZKRW3OSxnv2DpnaJOoX_tbFAp0ZWNYOFTsIX6Nt55xgAAAAAY4xquAA"
    ALLOWED_USER_IDS = [417536686]

    MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
    MAX_TOTAL_SIZE = 4 * 1024 * 1024 * 1024 # 4GB
    MAX_FILES_COUNT = 3

    CHUNK_SIZE = 256 * 1024
    PROGRESS_INTERVAL = 15
    ZIP_CHUNK_SIZE = 512 * 1024
    SPLIT_SIZE = 500 * 1024 * 1024  # هر پارت 500MB

# ===== لاگ =====
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

# ===== اپلیکیشن تلگرام =====
app = Client(
    "zip_bot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    session_string=Config.SESSION_STRING,
    in_memory=True
)

# ===== متغیر =====
user_files: Dict[int, List] = {}
user_states: Dict[int, Any] = {}

# ===== پیشرفت =====
class Progress:
    def __init__(self):
        self.last_update = 0
        self.message = None

    async def update(self, current, total, stage="Processing"):
        now = time.time()
        if now - self.last_update < Config.PROGRESS_INTERVAL:
            return
        self.last_update = now
        percent = (current / total * 100) if total else 0
        if self.message:
            try:
                await self.message.edit_text(
                    f"⏳ {stage}\n📊 {self.format_size(current)} / {self.format_size(total)}\n📈 {percent:.1f}%"
                )
            except:
                pass

    @staticmethod
    def format_size(size):
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024.0:
                return f"{size:.1f}{unit}"
            size /= 1024.0
        return f"{size:.1f}TB"

progress = Progress()

# ===== توابع =====
def is_user_allowed(uid): return uid in Config.ALLOWED_USER_IDS

async def send_msg(chat, text, reply=None):
    try:
        return await app.send_message(chat, text, reply_to_message_id=reply)
    except: return None

async def download_file(message, file_path):
    """دانلود chunk به chunk"""
    try:
        size = message.document.file_size if message.document else message.video.file_size
        downloaded = 0
        async for chunk in app.stream_media(message, chunk_size=Config.CHUNK_SIZE):
            with open(file_path, "ab") as f:
                f.write(chunk)
            downloaded += len(chunk)
            await progress.update(downloaded, size, "دانلود")
            gc.collect()
        return True
    except Exception as e:
        logger.error(f"Download error: {e}")
        return False

def create_zip(zip_path, files, password=None):
    """ایجاد zip به صورت استریم"""
    try:
        with pyzipper.AESZipFile(zip_path, "w", compression=pyzipper.ZIP_STORED) as zipf:
            if password:
                zipf.setpassword(password.encode())
            for f in files:
                with open(f["path"], "rb") as src, zipf.open(f["name"], "w") as dst:
                    while True:
                        chunk = src.read(Config.ZIP_CHUNK_SIZE)
                        if not chunk: break
                        dst.write(chunk)
        return True
    except Exception as e:
        logger.error(f"Zip error: {e}")
        return False

def split_file(file_path, part_size):
    """تقسیم فایل به چند پارت"""
    parts = []
    with open(file_path, "rb") as f:
        idx = 1
        while True:
            chunk = f.read(part_size)
            if not chunk: break
            part_path = f"{file_path}.part{idx}"
            with open(part_path, "wb") as pf:
                pf.write(chunk)
            parts.append(part_path)
            idx += 1
    return parts

async def upload_parts(parts, chat, msgid):
    """آپلود پارت‌ها یکی یکی"""
    for idx, p in enumerate(parts, 1):
        size = os.path.getsize(p)
        caption = f"📦 Part {idx}/{len(parts)}\n💾 {progress.format_size(size)}"
        try:
            await app.send_document(chat, document=p, caption=caption, reply_to_message_id=msgid)
        except Exception as e:
            logger.error(f"Upload error: {e}")
            return False
    return True

async def process_zip(user_id, chat, msgid, password=None):
    """دانلود → زیپ → تقسیم → آپلود"""
    temp_files = []
    msg = await send_msg(chat, "📥 در حال دانلود...", msgid)
    progress.message = msg
    try:
        file_infos = []
        for f in user_files[user_id]:
            fmsg = await app.get_messages(chat, f["message_id"])
            if not fmsg: continue
            path = f"/tmp/{f['file_name']}"
            temp_files.append(path)
            if await download_file(fmsg, path):
                file_infos.append({"path": path, "name": f["file_name"], "size": os.path.getsize(path)})
        if not file_infos:
            await msg.edit_text("❌ دانلود ناموفق")
            return
        zip_path = f"/tmp/archive_{int(time.time())}.zip"
        temp_files.append(zip_path)
        await msg.edit_text("📦 در حال ساخت zip...")
        ok = await asyncio.get_event_loop().run_in_executor(None, create_zip, zip_path, file_infos, password)
        if not ok:
            await msg.edit_text("❌ خطا در ساخت zip")
            return
        await msg.edit_text("📤 تقسیم فایل...")
        parts = await asyncio.get_event_loop().run_in_executor(None, split_file, zip_path, Config.SPLIT_SIZE)
        temp_files.extend(parts)
        await msg.edit_text(f"📤 آپلود {len(parts)} پارت...")
        if await upload_parts(parts, chat, msgid):
            await msg.edit_text("✅ تمام شد (پارت‌ها ارسال شدند)")
    finally:
        for p in temp_files:
            try: os.remove(p)
            except: pass
        gc.collect()

# ===== هندلر =====
@app.on_message(filters.command("start"))
async def start(_, m): 
    if not is_user_allowed(m.from_user.id): return
    await send_msg(m.chat.id, "🤖 فایل بفرست و بعد /zip بزن", m.id)

@app.on_message(filters.command("zip"))
async def dozip(_, m):
    if not is_user_allowed(m.from_user.id): return
    uid = m.from_user.id
    if not user_files.get(uid): 
        return await send_msg(m.chat.id, "❌ فایلی نیست", m.id)
    await send_msg(m.chat.id, "🔑 رمز بفرست یا /skip", m.id)
    user_states[uid] = "wait_pass"

@app.on_message(filters.command("skip"))
async def skip(_, m):
    uid = m.from_user.id
    if user_states.get(uid) == "wait_pass":
        user_states[uid] = "ready"
        await process_zip(uid, m.chat.id, m.id)

@app.on_message(filters.text & ~filters.command)
async def password(_, m):
    uid = m.from_user.id
    if user_states.get(uid) == "wait_pass":
        pw = m.text.strip()
        if len(pw) < 4:
            return await send_msg(m.chat.id, "❌ حداقل ۴ کاراکتر", m.id)
        user_states[uid] = "ready"
        await process_zip(uid, m.chat.id, m.id, password=pw)

@app.on_message(filters.document | filters.video)
async def addfile(_, m):
    if not is_user_allowed(m.from_user.id): return
    f = m.document or m.video
    size = f.file_size
    name = getattr(f, "file_name", f"file_{m.id}")
    if size > Config.MAX_FILE_SIZE:
        return await send_msg(m.chat.id, "❌ فایل خیلی بزرگه", m.id)
    uid = m.from_user.id
    user_files.setdefault(uid, [])
    if len(user_files[uid]) >= Config.MAX_FILES_COUNT:
        return await send_msg(m.chat.id, "❌ سقف فایل", m.id)
    total = sum(ff["file_size"] for ff in user_files[uid]) + size
    if total > Config.MAX_TOTAL_SIZE:
        return await send_msg(m.chat.id, "❌ حجم کل زیاد شد", m.id)
    user_files[uid].append({"message_id": m.id, "file_name": name, "file_size": size})
    await send_msg(m.chat.id, f"✅ ذخیره شد ({progress.format_size(size)})", m.id)

# ===== وب سرور =====
web = Flask(__name__)
@web.route("/") 
def home(): return "Bot is alive", 200

def run_web(): 
    web.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)), debug=False)

async def keep_alive():
    while True:
        await asyncio.sleep(25*60)
        try: await app.send_message("me", "✅ Alive")
        except: pass

async def main():
    await app.start()
    logger.info("Bot started ✅")
    asyncio.create_task(keep_alive())
    threading.Thread(target=run_web, daemon=True).start()
    await asyncio.Event().wait()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: logger.info("Stopped")
