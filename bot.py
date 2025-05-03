import os
import logging
import requests
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# Загружаем токен и адрес API из окружения
TELEGRAM_TOKEN = os.getenv("7130754445:AAHU_RXoc2OhF5bbGvteWGIw-pzJLsYZwqs")
API_URL        = os.getenv("http://127.0.0.1:8000")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN не задана")
if not API_URL:
    raise RuntimeError("API_URL не задан")

def start(update: Update, context: CallbackContext):
    logging.info("Получена команда /start")
    update.message.reply_text(
        "👋 Привет! Я астролог-бот.\n"
        "• Натальная карта: /natal YYYY-MM-DD HH:MM LAT LON TZ\n"
        "• Просто пообщаться: напиши любой текст"
    )

def natal(update: Update, context: CallbackContext):
    logging.info(f"Получена команда /natal: {update.message.text!r}")
    try:
        _, date, time, lat, lon, tz = update.message.text.split()
        params = {"date": date, "time": time, "lat": float(lat), "lon": float(lon), "tz": tz}
        r = requests.get(f"{API_URL}/natal", params=params, timeout=10)
        data = r.json()
        text = data.get("interpretation") or data.get("error")
    except Exception as e:
        logging.exception("Ошибка в /natal handler:")
        text = "Неверный формат. Пример:\n/natal 2025-05-03 09:00 55.75 37.62 +03:00"
    update.message.reply_text(text)

def echo(update: Update, context: CallbackContext):
    logging.info(f"Пересылаем в ChatGPT: {update.message.text!r}")
    try:
        r = requests.post(f"{API_URL}/chat", json={"prompt": update.message.text}, timeout=10)
        text = r.json().get("reply", "Извини, что-то пошло не так…")
    except Exception as e:
        logging.exception("Ошибка в echo handler:")
        text = "Произошла ошибка при общении с сервером."
    update.message.reply_text(text)

def main():
    updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("natal", natal))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, echo))

    logging.info("Бот запускается, начинаем polling…")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
