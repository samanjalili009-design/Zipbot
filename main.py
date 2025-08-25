from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# اطلاعات ربات شما
BOT_ID = "2487823"
BOT_TOKEN = "3ba2af01cad4bdd6138d15e353096e3f"
SESSION_STRING = "BAAcgIcAbm3Hdroaq-gHzwTUhklM4QhrzHSHm1uy_ZeMKXDmDamwhqFNGhK9zG_ZwyxF50TxLgez_a6zJ738_-qHLofVT3dgQCSdBHLuKPm39X46lRk1omWxBtmUEIpNzVZZJqEnyP32szYrHFkNx5IexSIyKWPURIx92AUeqBD6VKDRZJxs61Gq0U0-FSykY0a5sjEXp-3Mmz07sL7RYbCraYsdTsYx9n1EL1Bmg7IT-xpWeWpaEa0u4cmTkfJxpY03WwYDZ1J4zuCsYCNsauQrS2w7r3M6bNdTBAUIHPF8kSttPhnwEEFJQK-kLeK0aslMI-LzMhqS7nfR5fIhNM4wxFAHOAAAAAAK4sD3AA"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('سلام! ربات فعال است.')

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f'شما گفتید: {update.message.text}')

def main():
    # ایجاد اپلیکیشن با توکن ربات
    application = Application.builder().token(BOT_TOKEN).build()
    
    # اضافه کردن handlerها
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    
    # اجرای ربات
    application.run_polling()

if __name__ == "__main__":
    main()
