import os
import time
import pyzipper
import logging
import asyncio
from pyrogram import Client, filters
from flask import Flask
import threading
from typing import Dict, List, Any, Optional
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

    # اندازهٔ پیش‌فرض هر پارت (قابل تغییر با /split)
    SPLIT_SIZE = int(os.environ.get("SPLIT_SIZE", 500)) * 1024 * 1024  # MB -> bytes

# ===== لاگ =====
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

# ===== اپ تلگرام را همینجا بساز تا هندلرها ثبت شوند =====
app = Client(
    "zip_bot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    session_string=Config.SESSION_STRING,
    in_memory=True
)

# ===== وضعیت کاربران =====
user_files: Dict[int, List[Dict[str, Any]]] = {}
user_states: Dict[int, Any] = {}        # wait_pass / ready
user_split_mb: Dict[int, int] = {}      # اندازهٔ پارت به MB برای هر کاربر (اختیاری)

# ===== پیشرفت =====
class Progress:
    def __init__(self):
        self.last_update = 0.0
        self.message = None

    async def update(self, current, total, stage="Processing"):
        now = time.time()
        if now - self.last_update < Config.PROGRESS_INTERVAL:
            return
        self.last_update = now
        percent = (current / total * 100) if total else 0.0
        if self.message:
            try:
                await self.message.edit_text(
                    f"⏳ {stage}\n📊 {self.format_size(current)} / {self.format_size(total)}\n📈 {percent:.1f}%"
                )
            except Exception:
                pass

    @staticmethod
    def format_size(size):
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024.0:
                return f"{size:.1f}{unit}"
            size /= 1024.0
        return f"{size:.1f}TB"

progress = Progress()

# ===== کمک‌تابع‌ها =====
def is_user_allowed(uid: int) -> bool:
    return uid in Config.ALLOWED_USER_IDS

async def send_msg(chat_id: int, text: str, reply_id: Optional[int] = None):
    try:
        return await app.send_message(chat_id, text, reply_to_message_id=reply_id)
    except Exception:
        return None

async def download_file(message, file_path: str) -> bool:
    """دانلود chunk به chunk به دیسک"""
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

def create_zip(zip_path: str, files: List[Dict[str, Any]], password: Optional[str] = None) -> bool:
    """ایجاد ZIP به صورت استریم (فشرده‌سازی غیرفعال برای سرعت/مصرف رم کمتر)"""
    try:
        with pyzipper.AESZipFile(zip_path, "w", compression=pyzipper.ZIP_STORED) as zipf:
            if password:
                zipf.setpassword(password.encode("utf-8"))
            for f in files:
                with open(f["path"], "rb") as src, zipf.open(f["name"], "w") as dst:
                    while True:
                        chunk = src.read(Config.ZIP_CHUNK_SIZE)
                        if not chunk:
                            break
                        dst.write(chunk)
        return True
    except Exception as e:
        logger.error(f"Zip error: {e}")
        return False

def split_file(file_path: str, part_size_bytes: int) -> List[str]:
    """تقسیم فایل به چند پارت روی دیسک"""
    parts = []
    with open(file_path, "rb") as f:
        idx = 1
        while True:
            chunk = f.read(part_size_bytes)
            if not chunk:
                break
            part_path = f"{file_path}.part{idx:03d}"
            with open(part_path, "wb") as pf:
                pf.write(chunk)
            parts.append(part_path)
            idx += 1
    return parts

async def upload_parts(parts: List[str], chat_id: int, reply_id: int) -> bool:
    """آپلود پارت‌ها به صورت جداگانه"""
    for idx, p in enumerate(parts, 1):
        try:
            size = os.path.getsize(p)
            caption = f"📦 Part {idx}/{len(parts)}\n💾 {progress.format_size(size)}"
            await app.send_document(chat_id, document=p, caption=caption, reply_to_message_id=reply_id)
        except Exception as e:
            logger.error(f"Upload error: {e}")
            return False
    return True

async def process_zip(user_id: int, chat_id: int, message_id: int, password: Optional[str] = None):
    """دانلود → زیپ → تقسیم → آپلود (با پاکسازی فایل‌های موقت)"""
    temp_files: List[str] = []
    msg = await send_msg(chat_id, "📥 در حال دانلود...", message_id)
    progress.message = msg

    try:
        # 1) دانلود همه فایل‌ها
        file_infos: List[Dict[str, Any]] = []
        for f in user_files.get(user_id, []):
            fmsg = await app.get_messages(chat_id, f["message_id"])
            if not fmsg:
                continue
            path = os.path.join("/tmp", f["file_name"])
            temp_files.append(path)
            ok = await download_file(fmsg, path)
            if ok:
                file_infos.append({"path": path, "name": f["file_name"], "size": os.path.getsize(path)})
        if not file_infos:
            await msg.edit_text("❌ دانلود ناموفق")
            return

        # 2) ساخت ZIP
        await msg.edit_text("📦 در حال ساخت فایل ZIP...")
        zip_path = os.path.join("/tmp", f"archive_{int(time.time())}.zip")
        temp_files.append(zip_path)
        ok = await asyncio.get_event_loop().run_in_executor(None, create_zip, zip_path, file_infos, password)
        if not ok:
            await msg.edit_text("❌ خطا در ساخت ZIP")
            return

        # 3) تقسیم به پارت‌ها
        split_mb = user_split_mb.get(user_id, int(Config.SPLIT_SIZE / (1024 * 1024)))
        split_bytes = max(50, split_mb) * 1024 * 1024  # حداقل 50MB برای جلوگیری از پارت‌های ریز
        await msg.edit_text(f"✂️ تقسیم فایل به پارت‌های {split_mb}MB ...")

        parts = await asyncio.get_event_loop().run_in_executor(None, split_file, zip_path, split_bytes)
        temp_files.extend(parts)

        # 4) آپلود پارت‌ها
        await msg.edit_text(f"📤 آپلود {len(parts)} پارت...")
        ok = await upload_parts(parts, chat_id, message_id)
        if ok:
            await msg.edit_text("✅ تمام شد! همهٔ پارت‌ها ارسال شد.")
        else:
            await msg.edit_text("❌ خطا در آپلود پارت‌ها")

    finally:
        # پاکسازی فایل‌های موقت
        for p in temp_files:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        gc.collect()

# ===== هندلرها =====
@app.on_message(filters.command(["start"]))
async def start_handler(_, m):
    if not is_user_allowed(m.from_user.id):
        return
    await send_msg(
        m.chat.id,
        "🤖 ربات زیپ‌ساز آماده است.\n"
        "1) فایل‌هات رو بفرست\n"
        "2) اگه خواستی اندازهٔ پارت رو تعیین کن: `/split 700` (MB)\n"
        "3) بعد بزن: `/zip`\n"
        "برای زیپ بدون رمز: `/skip`",
        m.id
    )

@app.on_message(filters.command(["split"]))
async def split_handler(_, m):
    if not is_user_allowed(m.from_user.id):
        return
    try:
        parts = m.text.strip().split()
        if len(parts) < 2:
            return await send_msg(m.chat.id, "فرمت درست: `/split 700` (بر حسب MB)", m.id)
        size_mb = int(parts[1])
        if size_mb < 50:
            return await send_msg(m.chat.id, "حداقل اندازهٔ پارت 50MB است.", m.id)
        user_split_mb[m.from_user.id] = size_mb
        await send_msg(m.chat.id, f"✅ اندازهٔ پارت تنظیم شد: {size_mb}MB", m.id)
    except Exception:
        await send_msg(m.chat.id, "❌ ورودی نامعتبر. مثال: `/split 700`", m.id)

@app.on_message(filters.command(["zip"]))
async def zip_handler(_, m):
    if not is_user_allowed(m.from_user.id):
        return
    uid = m.from_user.id
    if not user_files.get(uid):
        return await send_msg(m.chat.id, "❌ هنوز فایلی ارسال نشده", m.id)
    await send_msg(m.chat.id, "🔑 رمز فایل زیپ رو بفرست یا `/skip` برای بدون رمز.", m.id)
    user_states[uid] = "wait_pass"

@app.on_message(filters.command(["skip"]))
async def skip_handler(_, m):
    if not is_user_allowed(m.from_user.id):
        return
    uid = m.from_user.id
    if user_states.get(uid) == "wait_pass":
        user_states[uid] = "ready"
        await process_zip(uid, m.chat.id, m.id, password=None)

# ⚠️ اینجاست که قبلاً خطا می‌دادی؛ باید command() رو صدا بزنی:
# از ~filters.command([...]) استفاده می‌کنیم تا متن‌هایی که دستور نیستند (پسورد) را بگیریم.
@app.on_message(filters.text & ~filters.command(["start", "zip", "skip", "split"]))
async def password_handler(_, m):
    if not is_user_allowed(m.from_user.id):
        return
    uid = m.from_user.id
    if user_states.get(uid) == "wait_pass":
        pw = m.text.strip()
        if len(pw) < 4:
            return await send_msg(m.chat.id, "❌ رمز حداقل باید ۴ کاراکتر باشد.", m.id)
        user_states[uid] = "ready"
        await process_zip(uid, m.chat.id, m.id, password=pw)

@app.on_message(filters.document | filters.video)
async def addfile_handler(_, m):
    if not is_user_allowed(m.from_user.id):
        return
    file_obj = m.document or m.video
    size = file_obj.file_size
    name = getattr(file_obj, "file_name", f"file_{m.id}")

    if size > Config.MAX_FILE_SIZE:
        return await send_msg(m.chat.id, f"❌ فایل خیلی بزرگه (حداکثر {progress.format_size(Config.MAX_FILE_SIZE)})", m.id)

    uid = m.from_user.id
    user_files.setdefault(uid, [])

    if len(user_files[uid]) >= Config.MAX_FILES_COUNT:
        return await send_msg(m.chat.id, f"❌ حداکثر {Config.MAX_FILES_COUNT} فایل مجاز است.", m.id)

    total = sum(ff["file_size"] for ff in user_files[uid]) + size
    if total > Config.MAX_TOTAL_SIZE:
        return await send_msg(m.chat.id, "❌ مجموع حجم فایل‌ها از حد مجاز بیشتر شد.", m.id)

    user_files[uid].append({"message_id": m.id, "file_name": name, "file_size": size})
    await send_msg(m.chat.id, f"✅ فایل ذخیره شد ({progress.format_size(size)})\n📁 تعداد: {len(user_files[uid])}", m.id)

# ===== وب سرور برای Render =====
web = Flask(__name__)

@web.route("/")
def home():
    return "🤖 Zip Bot is Running", 200

def run_web():
    web.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=False, use_reloader=False)

async def keep_alive():
    while True:
        await asyncio.sleep(25 * 60)  # هر 25 دقیقه
        try:
            await app.send_message("me", "✅ Bot is alive")
        except Exception:
            pass

async def main():
    await app.start()
    logger.info("Bot started ✅")
    asyncio.create_task(keep_alive())
    threading.Thread(target=run_web, daemon=True).start()
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
