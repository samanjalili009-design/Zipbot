import asyncio
from pyrogram import Client

async def main():
    API_ID = 1867911
    API_HASH = "f9e86b274826212a2712b18754fabc47"
    
    print("📱 ایجاد Session String جدید...")
    print("💡 بعد از اجرا، شماره تلفن و کد تأیید رو وارد کن")
    
    async with Client(":memory:", api_id=API_ID, api_hash=API_HASH) as app:
        session_string = await app.export_session_string()
        print("\n" + "="*50)
        print("✅ SESSION STRING جدید:")
        print(session_string)
        print("="*50)
        print("\n💡 این رشته رو در متغیر SESSION_STRING قرار بده")

if __name__ == "__main__":
    asyncio.run(main())
