from pyrogram import Client
from pyrogram.session import StringSession

API_ID = 1867911
API_HASH = "f9e86b274826212a2712b18754fabc47"

print("📱 شماره تلفن رو وارد کن (با +98 برای ایران):")

with Client(StringSession(), api_id=API_ID, api_hash=API_HASH) as app:
    session = app.export_session_string()
    print("\n✅ SESSION STRING:")
    print(session)
