import os
import asyncio
import tempfile
import time
import pyzipper
import logging
import sys
from pyrogram import Client, filters, idle
from pyrogram.types import Message
import socket
from threading import Thread

# ===== تنظیمات =====
API_ID = 1867911
API_HASH = "f9e86b274826212a2712b18754fabc47"
SESSION_STRING = "BAAcgIcAp7vwU3nnTi-xRZN3D_0rGdAPZN1qv1Pedm9p6zcuDZk_5zYJaTdpnsiobnWymDG28cvHU09pjJiSwTK1lCV98QUyPg9sjUyTQTmbIMRBCxuc-eJLYNKq4TBqrvvqbTbELSMkTyAwbPr36vB2b3WyYZPXqRzZfGjbYPiHJMnIz6TRZ6PKwGxEIj4PBK6hZ1DckYbmEm1Z-LFny8NQdpZ3mDsQzSVyxOrdZHZjFhcBfRnjA3GkAg5kLCCOhbUTY9xvLhS9XrEaEfm2CBxVFkZGwSu-tK0neYa2L0mNIT00PV3FD9-KzWo3uZSxnuaFKiM3w3cE1ymgKcGBa_0e6VJp1QAAAAAY4xquAA"
ALLOWED_USER_ID = 417536686
MAX_FILE_SIZE = 2097152000
MAX_TOTAL_SIZE = 2097152000

# تنظیمات لاگ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

user_files = {}
waiting_for_password = {}

def is_user_allowed(user_id: int) -> bool:
    return user_id == ALLOWED_USER_ID

# ===== ایجاد پورت برای Render =====
def create_port():
    """ایجاد یک پورت ساده برای Render"""
    try:
        # ایجاد یک سوکت برای باز نگه داشتن پورت
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('0.0.0.0', int(os.environ.get('PORT', 10000))))
        sock.listen(1)
        print(f"✅ Port {os.environ.get('PORT', 10000)} is open")
        return sock
    except Exception as e:
        print(f"Port error: {e}")
        return None

# ===== دستورات ربات =====
@Client.on_message(filters.command("start"))
async def start(client: Client, message: Message):
    if not is_user_allowed(message.from_user.id):
        return await message.reply_text("❌ دسترسی denied.")
    
    await message.reply_text("✅ ربات فعال است! فایل بفرستید.")

# بقیه دستورات همونطور که بود...

async def main():
    """تابع اصلی"""
    try:
        # ایجاد پورت برای Render
        port_socket = create_port()
        
        app = Client(
            "user_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=SESSION_STRING,
            in_memory=True
        )
        
        logger.info("Starting user bot...")
        await app.start()
        
        me = await app.get_me()
        logger.info(f"Logged in as: {me.first_name} (@{me.username})")
        print(f"✅ ربات آماده است!")
        
        # اطلاع رسانی
        try:
            await app.send_message(ALLOWED_USER_ID, "🤖 ربات آماده به کار است!")
        except:
            pass
        
        await idle()
        
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        if 'app' in locals():
            await app.stop()
        if port_socket:
            port_socket.close()

if __name__ == "__main__":
    asyncio.run(main())
