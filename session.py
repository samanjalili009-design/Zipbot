from pyrogram import Client
from pyrogram.session import StringSession

API_ID = 2487823
API_HASH = "3ba2af01cad4bdd6138d15e353096e3f"

print("📱 شماره تلفن رو وارد کن (با +98 برای ایران):")

with Client(StringSession(), api_id=API_ID, api_hash=API_HASH) as app:
    session = app.export_session_string()
    print("\n✅ SESSION STRING:")
    print(session)
