import os
import asyncio
import logging
import sys
import threading
from flask import Flask
from pyrogram import Client, filters, idle

# ===== تنظیمات =====
API_ID = 2487823
API_HASH = "3ba2af01cad4bdd6138d15e353096e3f"
SESSION_STRING = "BAAcgIcAbm3Hdroaq-gHzwTUhklM4QhrzHSHm1uy_ZeMKXDmDamwhqFNGhK9zG_ZwyxF50TxLgez_a6zJ738_-qHLofVT3dgQCSdBHLuKPm39X46lRk1omWxBtmUEIpNzVZZJqEnyP32szYrHFkNx5IexSIyKWPURIx92AUeqBD6VKDRZJxs61Gq0U0-FSykY0a5sjEXp-3Mmz07sL7RYbCraYsdTsYx9n1EL1Bmg7IT-xpWeWpaEa0u4cmTkfJxpY03WwYDZ1J4zuCsYCNsauQrS2w7r3M6bNdTBAUIHPF8kSttPhnwEEFJQK-kLeK0aslMI-LzMhqS7nfR5fIhNM4wxFAHOAAAAAAK4sD3AA"

# ===== لاگ =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ===== Flask =====
web_app = Flask(__name__)

@web_app.route("/")
def index():
    return "OK", 200

@web_app.route("/health")
def health():
    return "Bot is running", 200

# ===== Pyrogram =====
app = Client(
    "test_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    in_memory=True
)

@app.on_message(filters.command("start"))
async def start_handler(client, message):
    await message.reply("✅ ربات روی Render بالا اومده!")

# ===== اجرا =====
async def main():
    await app.start()
    logger.info("✅ Bot started")
    await idle()
    await app.stop()

if __name__ == "__main__":
    # اجرای Flask توی یک Thread
    threading.Thread(
        target=lambda: web_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000))),
        daemon=True
    ).start()

    # اجرای Pyrogram با asyncio
    asyncio.run(main())
