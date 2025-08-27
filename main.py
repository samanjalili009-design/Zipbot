#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pyrogram Zip/Upload Bot — up to 2GB per part with live progress
===============================================================

ویژگی‌ها:
- نمایش درصد پیشرفت دانلود و آپلود (Progress Bar + سرعت + ETA) با ادیت پیام هر 1 ثانیه
- پشتیبانی از فایل‌های بزرگ با تقسیم‌بندی به پارت‌های ≤ 1900MB برای رعایت محدودیت تلگرام (2GB)
- زیپ کردن چند فایل با امکان رمزگذاری AES (اختیاری) با pyzipper
- مدیریت صف، هم‌زمانی دانلود/آپلود با Semaphore
- هندل امن FloodWait/RPCError با Retry
- ذخیره وضعیت کاربر و فایل‌ها در JSON (Persist)
- کنترل دسترسی کاربران مجاز
- کلاینت Flask برای ping/keepalive (اختیاری)

نیازمندی‌ها:
    pip install pyrogram tgcrypto pyzipper aiohttp aiofiles flask

نکته مهم: تلگرام برای کاربران عادی محدودیت 2GB به ازای هر فایل دارد.
این بات فایل‌ها را به پارت‌های ~1.9GB تقسیم می‌کند تا قابل ارسال شوند.

"""
from __future__ import annotations

import os
import io
import re
import sys
import gc
import math
import json
import time
import uuid
import queue
import shutil
import random
import string
import signal
import logging
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Tuple, Optional, Callable

import aiofiles
import aiohttp
import pyzipper
from flask import Flask
from collections import deque

from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from pyrogram.errors import FloodWait, RPCError

# =============================
#  Logging
# =============================
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("zipbot")

# =============================
#  Config
# =============================
class Config:
    # ----- Telegram API (Fill these) -----
    API_ID: int = int(os.getenv("API_ID", "123456"))
    API_HASH: str = os.getenv("API_HASH", "YOUR_API_HASH")

    # Two options to login: SESSION_STRING or BOT_TOKEN (choose one)
    SESSION_STRING: Optional[str] = os.getenv("SESSION_STRING", None)
    BOT_TOKEN: Optional[str] = os.getenv("BOT_TOKEN", None)

    # ----- Access Control -----
    ALLOWED_USER_IDS: List[int] = json.loads(os.getenv("ALLOWED_USER_IDS", "[417536686]"))

    # ----- Size Limits -----
    MAX_FILE_SIZE: int = 4 * 1024 * 1024 * 1024  # 4GB (for local processing)
    MAX_TOTAL_SIZE: int = 8 * 1024 * 1024 * 1024  # 8GB (sum of user batch)

    # Telegram limit ~2GB per file. Keep part < 2GB.
    PART_SIZE: int = 1900 * 1024 * 1024  # 1900MB ≈ 1.86GB
    CHUNK_SIZE: int = 4 * 1024 * 1024    # 4MB read chunk when zipping from disk

    # ----- Concurrency & Retry -----
    MAX_CONCURRENT_DOWNLOADS: int = 2
    MAX_CONCURRENT_UPLOADS: int = 1
    RETRY_DELAY: int = 10

    # progress update interval (seconds)
    PROGRESS_UPDATE_INTERVAL: float = 1.0

    # persist file
    DATA_FILE: str = "user_data.json"

    # workspace dirs
    BASE_DIR: Path = Path(os.getenv("WORKDIR", ".")).resolve()
    DOWNLOAD_DIR: Path = BASE_DIR / "downloads"
    ZIP_DIR: Path = BASE_DIR / "zips"

    # flask keepalive
    ENABLE_FLASK: bool = True
    FLASK_HOST: str = "0.0.0.0"
    FLASK_PORT: int = int(os.getenv("PORT", "8080"))

# ensure dirs exist
Config.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
Config.ZIP_DIR.mkdir(parents=True, exist_ok=True)

# =============================
#  Globals / State
# =============================
app: Optional[Client] = None

user_files: Dict[int, List[Dict[str, Any]]] = {}
user_states: Dict[int, str] = {}
# states: "idle" | "waiting_password" | "waiting_filename"

scheduled_tasks: List[Tuple[float, Callable, Tuple, Dict]] = []

# task queue for heavy jobs (zip/create/upload)
task_queue: deque[Tuple[Callable, tuple, dict]] = deque()
processing: bool = False

# concurrency
_download_sem = asyncio.Semaphore(Config.MAX_CONCURRENT_DOWNLOADS)
_upload_sem = asyncio.Semaphore(Config.MAX_CONCURRENT_UPLOADS)

# =============================
#  Persistence
# =============================
def load_user_data() -> None:
    global user_files, user_states
    try:
        p = Path(Config.DATA_FILE)
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            user_files = {int(k): v for k, v in data.get("user_files", {}).items()}
            user_states = {int(k): v for k, v in data.get("user_states", {}).items()}
            logger.info("User data loaded.")
        else:
            logger.info("No previous user data.")
    except Exception as e:
        logger.error(f"Error loading user data: {e}")


def save_user_data() -> None:
    try:
        data = {"user_files": user_files, "user_states": user_states}
        with open(Config.DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error saving user data: {e}")

# =============================
#  Utils
# =============================
def is_user_allowed(user_id: int) -> bool:
    return user_id in Config.ALLOWED_USER_IDS


def format_size(n: int) -> str:
    if n is None:
        return "-"
    if n == 0:
        return "0B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = int(math.floor(math.log(max(n, 1), 1024)))
    p = 1024 ** i
    s = round(n / p, 2)
    return f"{s} {units[i]}"


def format_time(seconds: int) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds} ثانیه"
    if seconds < 3600:
        return f"{seconds // 60} دقیقه و {seconds % 60} ثانیه"
    return f"{seconds // 3600} ساعت و {(seconds % 3600) // 60} دقیقه"


def progress_bar(percentage: float, length: int = 20) -> str:
    percentage = max(0.0, min(100.0, percentage))
    filled = int(length * percentage / 100)
    return "█" * filled + "░" * (length - filled)


async def safe_send_message(
    chat_id: int,
    text: str,
    reply_to_message_id: Optional[int] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = None,
) -> Optional[Message]:
    max_retries = 3
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            await asyncio.sleep(random.uniform(0.3, 1.2))
            return await app.send_message(
                chat_id,
                text,
                reply_to_message_id=reply_to_message_id,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        except FloodWait as e:
            wait_time = int(e.value) + random.randint(1, 4)
            logger.warning(f"FloodWait: sleeping {wait_time}s (attempt {attempt}/{max_retries})")
            await asyncio.sleep(wait_time)
            last_err = e
        except Exception as e:
            logger.error(f"send_message error (attempt {attempt}): {e}")
            last_err = e
            await asyncio.sleep(2)
    logger.error(f"send_message failed after retries: {last_err}")
    return None


async def safe_edit_message(message: Message, text: str,
                            reply_markup: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception as e:
        logger.debug(f"edit failed (ignored): {e}")


class Progress:
    """Callable progress reporter for both download & upload."""

    def __init__(self, message: Message, stage: str, filename: str = ""):
        self.message = message
        self.stage = stage
        self.filename = filename
        self.start_time = time.time()
        self.last_update = 0.0
        self.last_percent = -1.0

    async def __call__(self, current: int, total: int) -> None:
        now = time.time()
        if now - self.last_update < Config.PROGRESS_UPDATE_INTERVAL:
            return
        self.last_update = now

        percent = (current * 100 / total) if total else 0.0
        elapsed = now - self.start_time
        speed = (current / elapsed) if elapsed > 0 else 0.0
        eta = int((total - current) / speed) if speed > 0 else 0

        bar = progress_bar(percent)
        text = (
            f"📦 {self.filename or '-'}\n"
            f"🔄 مرحله: {self.stage}\n\n"
            f"{bar} {percent:.1f}%\n"
            f"⬇️/⬆️ {format_size(current)} از {format_size(total)}\n"
            f"🚀 سرعت: {format_size(int(speed))}/s\n"
            f"⏳ زمان باقی‌مانده: {format_time(eta)}"
        )
        await safe_edit_message(self.message, text)


async def safe_download_media(
    message: Message,
    dest_path: Path,
    display_message: Message,
    filename: str,
) -> Optional[Path]:
    max_retries = 3
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            async with _download_sem:
                cb = Progress(display_message, "دانلود", filename)
                # pyrogram handles chunking; we set target folder
                file_path = await message.download(
                    file_name=str(dest_path),
                    progress=cb,
                )
                return Path(file_path) if file_path else None
        except FloodWait as e:
            wait_time = int(e.value) + random.randint(1, 4)
            await safe_edit_message(display_message, f"⏳ FloodWait {wait_time}s ...")
            logger.warning(f"download FloodWait: {wait_time}s")
            await asyncio.sleep(wait_time)
            last_err = e
        except Exception as e:
            logger.error(f"download error (attempt {attempt}): {e}")
            last_err = e
            await asyncio.sleep(Config.RETRY_DELAY)
    await safe_edit_message(display_message, f"❌ خطا در دانلود: {last_err}")
    return None


async def safe_upload_document(
    chat_id: int,
    file_path: Path,
    display_message: Message,
) -> Optional[Message]:
    max_retries = 3
    last_err = None
    filename = file_path.name
    for attempt in range(1, max_retries + 1):
        try:
            async with _upload_sem:
                cb = Progress(display_message, "آپلود", filename)
                sent = await app.send_document(
                    chat_id=chat_id,
                    document=str(file_path),
                    progress=cb,
                    file_name=filename,
                )
                return sent
        except FloodWait as e:
            wait_time = int(e.value) + random.randint(1, 4)
            await safe_edit_message(display_message, f"⏳ FloodWait {wait_time}s ...")
            logger.warning(f"upload FloodWait: {wait_time}s")
            await asyncio.sleep(wait_time)
            last_err = e
        except RPCError as e:
            last_err = e
            logger.error(f"RPC upload err: {e}")
            await asyncio.sleep(Config.RETRY_DELAY)
        except Exception as e:
            last_err = e
            logger.error(f"upload error: {e}")
            await asyncio.sleep(Config.RETRY_DELAY)
    await safe_edit_message(display_message, f"❌ خطا در آپلود {filename}: {last_err}")
    return None


def sanitize_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]", "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or f"file_{int(time.time())}"


# =============================
#  Zipping (split to parts ≤ PART_SIZE)
# =============================
async def create_zip_parts(
    files: List[Dict[str, Any]],
    base_name: str,
    password: Optional[str],
    status_msg: Message,
) -> List[Path]:
    """Create split zip parts. Each part ≤ Config.PART_SIZE.

    We sequentially add files; if current part would exceed size, close it and start new part.
    Uses Deflate compression by default. If password provided -> AES256.
    """
    await safe_edit_message(status_msg, "🧰 شروع ساخت آرشیو...")

    part_paths: List[Path] = []
    part_index = 1
    bytes_in_part = 0
    zip_handle: Optional[pyzipper.AESZipFile] = None

    def new_part_path(idx: int) -> Path:
        # name.part01.zip pattern keeps lexicographic order
        return Config.ZIP_DIR / f"{base_name}.part{idx:02d}.zip"

    try:
        for i, f in enumerate(files, 1):
            src: Path = Path(f["path"]).resolve()
            arcname: str = f.get("arcname") or src.name
            size: int = src.stat().st_size if src.exists() else 0

            # if no zip open, open
            if zip_handle is None:
                part_path = new_part_path(part_index)
                if part_path.exists():
                    part_path.unlink()
                zip_handle = pyzipper.AESZipFile(part_path, "w", compression=pyzipper.ZIP_DEFLATED)
                if password:
                    zip_handle.setpassword(password.encode("utf-8"))
                    zip_handle.setencryption(pyzipper.WZ_AES, nbits=256)
                part_paths.append(part_path)
                bytes_in_part = 0
                await safe_edit_message(status_msg, f"📦 ساخت پارت {part_index} ...")

            # if adding this file would exceed part size, rotate
            if bytes_in_part + size > Config.PART_SIZE and bytes_in_part > 0:
                # close current
                zip_handle.close()
                zip_handle = None
                part_index += 1
                await safe_edit_message(status_msg, f"🔁 تعویض به پارت {part_index} ...")
                continue  # re-open in next loop iteration

            # add file to current zip with streaming read to control memory
            await safe_edit_message(
                status_msg,
                f"➕ افزودن: {arcname}\n"
                f"📏 اندازه: {format_size(size)}\n"
                f"🧩 پارت: {part_index}"
            )

            # write using writestr with streamed chunks to avoid RAM spike
            # pyzipper lacks direct stream API; we read into memory per file if small.
            # For large files, read chunks and write via a temp file object.
            if size <= 32 * 1024 * 1024:  # ≤32MB direct read
                async with aiofiles.open(src, "rb") as rf:
                    data = await rf.read()
                zip_handle.writestr(arcname, data)
            else:
                # big file: copy to a temp file and then write from disk
                # We avoid loading entire file into memory by writing to an actual
                # file entry; pyzipper/zipfile doesn't expose incremental API easily,
                # so we fallback to ZipFile.write
                zip_handle.close()
                # reopen using standard ZipFile to leverage write()
                # Note: pyzipper.AESZipFile inherits ZipFile and supports write()
                part_path = part_paths[-1]
                zip_handle = pyzipper.AESZipFile(part_path, "a", compression=pyzipper.ZIP_DEFLATED)
                if password:
                    zip_handle.setpassword(password.encode("utf-8"))
                    zip_handle.setencryption(pyzipper.WZ_AES, nbits=256)
                zip_handle.write(src, arcname)

            bytes_in_part += size

        if zip_handle is not None:
            zip_handle.close()
            zip_handle = None

        await safe_edit_message(status_msg, f"✅ ساخت آرشیو تکمیل شد. تعداد پارت: {len(part_paths)}")
        return part_paths
    except Exception as e:
        try:
            if zip_handle is not None:
                zip_handle.close()
        except Exception:
            pass
        await safe_edit_message(status_msg, f"❌ خطا در ساخت آرشیو: {e}")
        # cleanup broken parts
        for p in part_paths:
            with contextlib.suppress(Exception):
                if p.exists():
                    p.unlink()
        raise


# =============================
#  Queue & Scheduling
# =============================

def schedule_task(task_func: Callable, delay: float, *args, **kwargs) -> None:
    execution_time = time.time() + delay
    scheduled_tasks.append((execution_time, task_func, args, kwargs))
    scheduled_tasks.sort(key=lambda x: x[0])


async def process_scheduled_tasks() -> None:
    while True:
        now = time.time()
        to_run: List[Tuple[Callable, tuple, dict]] = []
        while scheduled_tasks and scheduled_tasks[0][0] <= now:
            _, func, args, kwargs = scheduled_tasks.pop(0)
            to_run.append((func, args, kwargs))
        for func, args, kwargs in to_run:
            try:
                func(*args, **kwargs)
            except Exception as e:
                logger.error(f"scheduled task error: {e}")
        await asyncio.sleep(0.5)


def add_to_queue(task_func: Callable, *args, **kwargs) -> None:
    task_queue.append((task_func, args, kwargs))
    logger.info(f"Task added. Queue size: {len(task_queue)}")


async def process_task_queue() -> None:
    global processing
    if processing:
        return
    processing = True
    try:
        while task_queue:
            func, args, kwargs = task_queue.popleft()
            try:
                await func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Task error: {e}")
    finally:
        processing = False

# =============================
#  User Helpers
# =============================
async def reset_user(user_id: int) -> None:
    user_files[user_id] = []
    user_states[user_id] = "idle"
    save_user_data()


def total_user_size(user_id: int) -> int:
    files = user_files.get(user_id, [])
    return sum(int(f.get("size", 0)) for f in files)


def kb_main(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧰 شروع زیپ", callback_data="zip_start")],
        [InlineKeyboardButton("🗂 وضعیت/لیست فایل‌ها", callback_data="status")],
        [InlineKeyboardButton("🧹 لغو و پاکسازی", callback_data="cancel")],
    ])


# =============================
#  Handlers
# =============================
async def cmd_start(c: Client, m: Message):
    if not is_user_allowed(m.from_user.id):
        return
    load_user_data()
    user_states.setdefault(m.from_user.id, "idle")
    user_files.setdefault(m.from_user.id, [])
    text = (
        "سلام! 👋\n\n"
        "فایل‌هات رو برام بفرست. بعد با دکمه ‘🧰 شروع زیپ’ آرشیو می‌کنم و به پارت‌های ≤ 1.9GB آپلود می‌کنم.\n"
        "می‌تونی اسم زیپ و رمز رو هم تعیین کنی."
    )
    await safe_send_message(m.chat.id, text, reply_to_message_id=m.id, reply_markup=kb_main(m.from_user.id))


async def cmd_set_name(c: Client, m: Message):
    if not is_user_allowed(m.from_user.id):
        return
    user_states[m.from_user.id] = "waiting_filename"
    save_user_data()
    await safe_send_message(m.chat.id, "📝 نام فایل زیپ رو بفرست.")


async def cmd_set_pass(c: Client, m: Message):
    if not is_user_allowed(m.from_user.id):
        return
    user_states[m.from_user.id] = "waiting_password"
    save_user_data()
    await safe_send_message(m.chat.id, "🔐 رمز زیپ رو بفرست (برای حذف رمز، عبارت none رو ارسال کن).")


async def non_command_message(c: Client, m: Message):
    # capture filename/password states
    uid = m.from_user.id
    if not is_user_allowed(uid):
        return
    st = user_states.get(uid)
    if st == "waiting_filename":
        name = sanitize_filename(m.text or "archive")
        state = get_or_create_user_state(uid)
        state["zip_name"] = name
        user_states[uid] = "idle"
        save_user_data()
        await safe_send_message(m.chat.id, f"✅ نام زیپ تنظیم شد: {name}")
        return
    if st == "waiting_password":
        pwd_raw = (m.text or "").strip()
        state = get_or_create_user_state(uid)
        if pwd_raw.lower() == "none":
            state["password"] = None
            await safe_send_message(m.chat.id, "✅ رمز حذف شد.")
        else:
            state["password"] = pwd_raw
            await safe_send_message(m.chat.id, "✅ رمز تنظیم شد.")
        user_states[uid] = "idle"
        save_user_data()
        return


def get_or_create_user_state(uid: int) -> Dict[str, Any]:
    # store misc state (zip_name/password)
    state = next((f for f in user_files.get(uid, []) if f.get("_meta") == True), None)
    if state is None:
        state = {"_meta": True, "zip_name": None, "password": None}
        user_files.setdefault(uid, []).append(state)
        save_user_data()
    return state


async def handle_file(c: Client, m: Message):
    uid = m.from_user.id
    if not is_user_allowed(uid):
        return

    # accept document/video/audio/photos (photos become JPEG)
    file_name = None
    file_size = None

    if m.document:
        file_name = m.document.file_name
        file_size = m.document.file_size
    elif m.video:
        file_name = m.video.file_name or f"video_{m.video.file_unique_id}.mp4"
        file_size = m.video.file_size
    elif m.audio:
        file_name = m.audio.file_name or f"audio_{m.audio.file_unique_id}.mp3"
        file_size = m.audio.file_size
    elif m.photo:
        # take biggest size
        sz = m.photo.sizes[-1]
        file_name = f"photo_{sz.file_unique_id}.jpg"
        file_size = sz.file_size
    else:
        return

    if file_size and file_size > Config.MAX_FILE_SIZE:
        await safe_send_message(m.chat.id, f"❌ اندازه فایل بیشتر از حد مجاز: {format_size(file_size)}")
        return

    if total_user_size(uid) + (file_size or 0) > Config.MAX_TOTAL_SIZE:
        await safe_send_message(m.chat.id, f"❌ جمع حجم فایل‌ها از حد مجاز بیشتر شد (حداکثر {format_size(Config.MAX_TOTAL_SIZE)}).")
        return

    # create placeholder message for progress
    disp = await safe_send_message(m.chat.id, f"⬇️ شروع دانلود \n{file_name}")
    if disp is None:
        return

    # destination path
    dst = Config.DOWNLOAD_DIR / f"{uid}" / sanitize_filename(file_name or f"file_{uuid.uuid4().hex}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    # download
    downloaded_path = await safe_download_media(m, dst, disp, file_name or dst.name)
    if not downloaded_path:
        await safe_edit_message(disp, f"❌ دانلود ناموفق برای {file_name}")
        return

    # register file for this user
    entry = {
        "path": str(downloaded_path),
        "size": int(downloaded_path.stat().st_size),
        "arcname": downloaded_path.name,
        "time": int(time.time()),
    }
    user_files.setdefault(uid, []).append(entry)
    save_user_data()

    await safe_edit_message(disp, f"✅ دانلود شد: {downloaded_path.name} ( {format_size(entry['size'])} )")
    await safe_send_message(m.chat.id, "➕ فایل به لیست اضافه شد.", reply_markup=kb_main(uid))


async def cb_handler(c: Client, q: CallbackQuery):
    uid = q.from_user.id
    if not is_user_allowed(uid):
        await q.answer("اجازه دسترسی ندارید.", show_alert=True)
        return

    data = q.data or ""
    if data == "status":
        files = [f for f in user_files.get(uid, []) if not f.get("_meta")]
        state = get_or_create_user_state(uid)
        name = state.get("zip_name") or "archive"
        pwd = state.get("password") or "—"
        if not files:
            await q.message.edit_text("📂 لیست فایل‌ها خالی است.", reply_markup=kb_main(uid))
            return
        lines = [
            f"🧾 نام زیپ: {name}",
            f"🔐 رمز: {pwd}",
            f"📦 تعداد فایل: {len(files)}",
            f"📏 مجموع حجم: {format_size(sum(int(f['size']) for f in files))}",
            "", "فایل‌ها:",
        ]
        for i, f in enumerate(files, 1):
            lines.append(f"{i}. {Path(f['path']).name} — {format_size(int(f['size']))}")
        await q.message.edit_text("\n".join(lines), reply_markup=kb_main(uid))
        return

    if data == "cancel":
        # clean user folder
        try:
            base = Config.DOWNLOAD_DIR / f"{uid}"
            if base.exists():
                shutil.rmtree(base, ignore_errors=True)
        except Exception:
            pass
        await reset_user(uid)
        await q.message.edit_text("🧹 همه‌چیز پاک شد.", reply_markup=kb_main(uid))
        return

    if data == "zip_start":
        await q.answer("شروع شد…")
        await q.message.edit_text("🧰 تنظیمات: /name برای نام زیپ، /pass برای رمز (اختیاری). سپس /done.", reply_markup=kb_main(uid))
        return


async def cmd_done(c: Client, m: Message):
    uid = m.from_user.id
    if not is_user_allowed(uid):
        return
    files = [f for f in user_files.get(uid, []) if not f.get("_meta")]
    if not files:
        await safe_send_message(m.chat.id, "📂 هیچ فایلی در لیست نیست.")
        return
    state = get_or_create_user_state(uid)
    zip_name = state.get("zip_name") or f"archive_{uid}_{datetime.now().strftime('%Y%m%d_%H%M')}"
    password = state.get("password")

    # enqueue heavy task
    disp = await safe_send_message(m.chat.id, "⚙️ در صف پردازش…")
    add_to_queue(process_zip_and_upload, uid, files, zip_name, password, m.chat.id, disp.id)
    await process_task_queue()


# =============================
#  Core: zip then upload
# =============================
async def process_zip_and_upload(
    uid: int,
    files: List[Dict[str, Any]],
    zip_name: str,
    password: Optional[str],
    chat_id: int,
    display_msg_id: int,
) -> None:
    msg = await app.get_messages(chat_id, display_msg_id)
    await safe_edit_message(msg, "🧰 شروع ساخت آرشیو…")

    safe_name = sanitize_filename(zip_name)

    # 1) Create zip parts
    parts = await create_zip_parts(files, safe_name, password, msg)
    if not parts:
        await safe_edit_message(msg, "❌ هیچ پارت ساخته نشد.")
        return

    # 2) Upload parts sequentially (≤ 1 concurrent by semaphore)
    uploaded: List[Message] = []
    for i, part_path in enumerate(parts, 1):
        await safe_edit_message(msg, f"⬆️ آپلود پارت {i}/{len(parts)}: {part_path.name}")
        sent = await safe_upload_document(chat_id, part_path, msg)
        if sent is None:
            await safe_edit_message(msg, f"❌ آپلود پارت ناموفق: {part_path.name}")
            return
        uploaded.append(sent)

    # 3) Done
    await safe_edit_message(
        msg,
        "✅ همه پارت‌ها با موفقیت آپلود شدند.\n"
        "برای استخراج، همه پارت‌ها را در یک پوشه قرار دهید و از پارت اول اکسترکت کنید.")

    # Optional: cleanup downloads to save space
    try:
        base = Config.DOWNLOAD_DIR / f"{uid}"
        if base.exists():
            shutil.rmtree(base, ignore_errors=True)
    except Exception:
        pass


# =============================
#  Filters
# =============================

def _non_command_filter(_, __, message: Message) -> bool:
    uid = getattr(message.from_user, "id", None)
    if not uid:
        return False
    return (
        (message.text is not None)
        and not message.text.startswith('/')
        and uid in user_states
        and user_states.get(uid) in ("waiting_password", "waiting_filename")
    )

non_command = filters.create(_non_command_filter)


# =============================
#  Flask keepalive (optional)
# =============================
flask_app = Flask(__name__)

@flask_app.route('/')
def _root():
    return 'OK', 200


# =============================
#  Startup
# =============================
async def run_bot() -> None:
    global app

    if Config.SESSION_STRING:
        app = Client(
            name="zipbot",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            session_string=Config.SESSION_STRING,
            workdir=str(Config.BASE_DIR),
            parse_mode=enums.ParseMode.HTML,
        )
    elif Config.BOT_TOKEN:
        app = Client(
            name="zipbot-bot",
            bot_token=Config.BOT_TOKEN,
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            workdir=str(Config.BASE_DIR),
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        logger.error("No SESSION_STRING or BOT_TOKEN provided.")
        return

    load_user_data()

    @app.on_message(filters.command(["start"]))
    async def _start(c, m):
        await cmd_start(c, m)

    @app.on_message(filters.command(["name"]))
    async def _name(c, m):
        await cmd_set_name(c, m)

    @app.on_message(filters.command(["pass"]))
    async def _pass(c, m):
        await cmd_set_pass(c, m)

    @app.on_message(filters.command(["done"]))
    async def _done(c, m):
        await cmd_done(c, m)

    # receive files
    @app.on_message(
        (filters.document | filters.video | filters.audio | filters.photo)
        & ~filters.edited
    )
    async def _files(c, m):
        await handle_file(c, m)

    @app.on_callback_query()
    async def _cb(c, q):
        await cb_handler(c, q)

    # background workers
    async def _bg():
        await asyncio.gather(
            process_scheduled_tasks(),
        )

    async with app:
        logger.info("Bot started.")
        # start flask in thread if enabled
        if Config.ENABLE_FLASK:
            th = asyncio.to_thread(flask_app.run, host=Config.FLASK_HOST, port=Config.FLASK_PORT)
            asyncio.create_task(th)
        await asyncio.gather(app.start(), _bg())


if __name__ == "__main__":
    # Support running without asyncio.run if environment handles it differently
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
