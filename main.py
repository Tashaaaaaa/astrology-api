import os
import re
import logging
import requests
from fastapi import FastAPI, Request, Body
from telegram import Update, Bot
from telegram.ext import (
    Dispatcher, CommandHandler, MessageHandler,
    Filters, CallbackContext, ConversationHandler
)
from flatlib import const
from flatlib.chart import Chart
from flatlib.datetime import Datetime
from flatlib.geopos import GeoPos
import swisseph
import openai

# ─── Настройка ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = FastAPI()

# Читаем секреты из окружения
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL     = os.getenv("WEBHOOK_URL")  # полный URL для Telegram webhook
API_URL         = os.getenv("API_URL", "http://127.0.0.1:10000")

if not OPENAI_API_KEY or not TELEGRAM_TOKEN or not WEBHOOK_URL:
    raise RuntimeError("Нужно задать OPENAI_API_KEY, TELEGRAM_TOKEN и WEBHOOK_URL в окружении")

openai.api_key = OPENAI_API_KEY
# Инициализируем Swiss Ephemeris
swisseph.set_ephe_path('./ephe')

# ─── Conversation States ─────────────────────────────────────────────────────
DATE, TIME_PERIOD, PLACE, FORMAT = range(4)

# ─── Создаём Telegram Bot и Dispatcher ────────────────────────────────────────
bot = Bot(token=TELEGRAM_TOKEN)
dp  = Dispatcher(bot, None, workers=0, use_context=True)

# ─── REST API для натальной карты и ChatGPT ────────────────────────────────────
@app.get("/natal")
def natal_analysis(date: str, time: str, lat: float, lon: float, tz: str):
    try:
        y, m, d = date.split('-')
        dt = Datetime(f"{d}/{m}/{y}", time, tz)
        pos = GeoPos(lat, lon)
        chart = Chart(dt, pos, hsys=const.HOUSES_PLACIDUS)
        sun, moon, asc = chart.get(const.SUN), chart.get(const.MOON), chart.get(const.ASC)
        return {"sun_sign": sun.sign, "moon_sign": moon.sign, "ascendant_sign": asc.sign}
    except Exception as e:
        logging.exception("Ошибка в /natal:")
        return {"error": str(e)}

@app.post("/chat")
async def chat_gpt(payload: dict = Body(...)):
    prompt = payload.get("prompt", "").strip()
    if not prompt:
        return {"error": "Empty prompt"}
    resp = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "system", "content": "Ты — опытный астролог-автор."},
                  {"role": "user",   "content": prompt}],
        timeout=15
    )
    return {"reply": resp.choices[0].message.content.strip()}

# ─── Handlers for Conversation ─────────────────────────────────────────────────

def start_handler(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Привет! Давай создадим твою натальную карту.\n"
        "Введите дату рождения (ГГГГ-ММ-ДД), например: 1990-05-03"
    )
    return DATE


def date_handler(update: Update, context: CallbackContext):
    text = update.message.text.strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        update.message.reply_text("Неверный формат даты. Попробуйте: ГГГГ-ММ-ДД")
        return DATE
    context.user_data['date'] = text
    update.message.reply_text(
        "Когда ты родился? Выбери: 'ночью', 'утром', 'днем' или 'вечером'"
    )
    return TIME_PERIOD


def time_period_handler(update: Update, context: CallbackContext):
    choice = update.message.text.strip().lower()
    mapping = {'ночью': '00:00', 'утром': '08:00', 'днем': '13:00', 'вечером': '18:00'}
    if choice not in mapping:
        update.message.reply_text("Выбери один из: ночью, утром, днем, вечером")
        return TIME_PERIOD
    context.user_data['time'] = mapping[choice]
    update.message.reply_text(
        "Введите город рождения (например: Москва). Я сам определю координаты."
    )
    return PLACE


def place_handler(update: Update, context: CallbackContext):
    city = update.message.text.strip()
    # Геокодирование через Nominatim
    resp = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": city, "format": "json", "limit": 1}
    )
    data = resp.json()
    if not data:
        update.message.reply_text("Не удалось найти город. Попробуйте ввести снова.")
        return PLACE
    context.user_data['lat'] = float(data[0]['lat'])
    context.user_data['lon'] = float(data[0]['lon'])
    context.user_data['place'] = city
    update.message.reply_text("Выберите формат интерпретации: 'короткую' или 'красочную'")
    return FORMAT


def format_handler(update: Update, context: CallbackContext):
    choice = update.message.text.strip().lower()
    if choice not in ['короткую','красочную']:
        update.message.reply_text("Пожалуйста, 'короткую' или 'красочную'")
        return FORMAT
    data = context.user_data
    resp = requests.get(f"{API_URL}/natal", params={
        'date': data['date'], 'time': data['time'],
        'lat': data['lat'],   'lon': data['lon'],
        'tz': '+00:00'
    }).json()
    if 'error' in resp:
        text = f"Ошибка расчёта: {resp['error']}"
    else:
        sun, moon, asc = resp['sun_sign'], resp['moon_sign'], resp['ascendant_sign']
        place = data['place']
        if choice == 'короткую':
            text = f"{place}: Солнце в {sun}, Луна в {moon}, Асцендент в {asc}."
        else:
            prompt = (f"Опиши натальную карту для человека из {place} "
                      f"({data['date']} {data['time']}), Солнце в {sun}, "
                      f"Луна в {moon}, Асцендент в {asc}."
                      " Пиши вдохновенно и детально.")
            text = requests.post(
                f"{API_URL}/chat", json={'prompt': prompt}
            ).json().get('reply', 'Не удалось получить описание.')
    update.message.reply_text(text)
    return ConversationHandler.END


def cancel_handler(update: Update, context: CallbackContext):
    update.message.reply_text("Отменено пользователем.")
    return ConversationHandler.END

# Регистрируем ConversationHandler на dispatcher
conv = ConversationHandler(
    entry_points=[CommandHandler('start', start_handler)],
    states={
        DATE:         [MessageHandler(Filters.text & ~Filters.command, date_handler)],
        TIME_PERIOD: [MessageHandler(Filters.text & ~Filters.command, time_period_handler)],
        PLACE:        [MessageHandler(Filters.text & ~Filters.command, place_handler)],
        FORMAT:       [MessageHandler(Filters.text & ~Filters.command, format_handler)],
    },
    fallbacks=[CommandHandler('cancel', cancel_handler)]
)
dp.add_handler(conv)

# Webhook endpoint для Telegram
@app.post('/webhook')
async def telegram_webhook(req: Request):
    update = Update.de_json(await req.json(), bot)
    dp.process_update(update)
    return {'ok': True}

# Устанавливаем webhook при старте
@app.on_event('startup')
def on_startup():
    logging.info('Setting Telegram webhook…')
    bot.delete_webhook()
    bot.set_webhook(WEBHOOK_URL)

# Запуск Uvicorn (бот и API в одном процессе) по CMD в Dockerfile
