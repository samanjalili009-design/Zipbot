import os
import zipfile
import asyncio
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# اطلاعات UserBot شما
API_ID = 2487823
API_HASH = "3ba2af01cad4bdd6138d15e353096e3f"
SESSION_STRING = "BAAcgIcAbm3Hdroaq-gHzwTUhklM4QhrzHSHm1uy_ZeMKXDmDamwhqFNGhK9zG_ZwyxF50TxLgez_a6zJ738_-qHLofVT3dgQCSdBHLuKPm39X46lRk1omWxBtmUEIpNzVZZJqEnyP32szYrHFkNx5IexSIyKWPURIx92AUeqBD6VKDRZJxs61Gq0U0-FSykY0a5sjEXp-3Mmz07sL7RYbCraYsdTsYx9n1EL1Bmg7IT-xpWeWpaEa0u4cmTkfJxpY03WwYDZ1J4zuCsYCNsauQrS2w7r3M6bNdTBAUIHPF8kSttPhnwEEFJQK-kLeK0aslMI-LzMhqS7nfR5fIhNM4wxFAHOAAAAAAK4sD3AA"

# ایجاد کلاینت
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

async def create_zip(file_paths, zip_name):
    """ایجاد فایل زیپ از فایل‌های داده شده"""
    with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_path in file_paths:
            if os.path.exists(file_path):
                zipf.write(file_path, os.path.basename(file_path))
    return zip_name

@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    """Handler برای دستور /start"""
    await event.reply('🤖 **ربات UserBot زیپ ساز فعال شد!**\n\n'
                     '📦 فایل‌ها را برای من فوروارد کنید یا آپلود کنید، سپس از دستور /zip استفاده کنید.')

@client.on(events.NewMessage(pattern='/zip'))
async def zip_handler(event):
    """Handler برای دستور /zip"""
    try:
        # بررسی اینکه آیا پیام ریپلای شده است
        if not event.is_reply:
            await event.reply('❌ لطفاً روی پیامی که حاوی فایل است ریپلای کنید و سپس /zip را بفرستید.')
            return
        
        replied_msg = await event.get_reply_message()
        
        if not replied_msg.media:
            await event.reply('❌ پیام ریپلای شده حاوی فایل نیست.')
            return
        
        # دانلود فایل
        await event.reply('⏳ در حال دانلود فایل...')
        file_path = await client.download_media(replied_msg)
        
        if not file_path:
            await event.reply('❌ خطا در دانلود فایل.')
            return
        
        # ایجاد فایل زیپ
        await event.reply('📦 در حال ایجاد فایل زیپ...')
        zip_name = f"zipped_{os.path.basename(file_path)}.zip"
        zip_path = await create_zip([file_path], zip_name)
        
        # آپلود فایل زیپ
        await event.reply('⬆️ در حال آپلود فایل زیپ...')
        await client.send_file(
            event.chat_id,
            zip_path,
            caption=f'📦 فایل زیپ شده: {os.path.basename(file_path)}'
        )
        
        # پاک کردن فایل‌های موقت
        try:
            os.remove(file_path)
            os.remove(zip_path)
        except:
            pass
        
        await event.reply('✅ عملیات با موفقیت انجام شد!')
        
    except Exception as e:
        await event.reply(f'❌ خطا: {str(e)}')

@client.on(events.NewMessage(pattern='/help'))
async def help_handler(event):
    """Handler برای دستور /help"""
    help_text = """
🤖 **راهنمای ربات UserBot زیپ ساز**

📋 **دستورات موجود:**
/start - شروع کار با ربات
/zip - زیپ کردن فایل (روی پیام فایل ریپلای کنید)
/help - نمایش این راهنما

📦 **نحوه استفاده:**
1. فایل را برای ربات فوروارد کنید یا آپلود کنید
2. روی پیام فایل ریپلای کنید
3. دستور /zip را ارسال کنید
4. ربات فایل را زیپ کرده و ارسال می‌کند

⚡ **پشتیبانی از انواع فایل‌ها:**
- documents
- images
- videos
- audio files
- و سایر مدیاها
"""
    await event.reply(help_text)

async def main():
    """تابع اصلی"""
    print("🤖 ربات UserBot زیپ ساز در حال راه اندازی...")
    await client.start()
    print("✅ ربات با موفقیت راه اندازی شد!")
    print("👤 اطلاعات کاربر:", await client.get_me())
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
