import os
import re
import logging
import requests
from fastapi import FastAPI, Body
from telegram import Update
from telegram.ext import (
    Updater, CommandHandler, MessageHandler,
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

# Читаем секреты
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
API_URL = os.getenv("API_URL", "http://127.0.0.1:10000")
if not OPENAI_API_KEY or not TELEGRAM_TOKEN:
    raise RuntimeError("Не заданы обязательные переменные окружения OPENAI_API_KEY и TELEGRAM_TOKEN")
openai.api_key = OPENAI_API_KEY

# ─── Flatlib и Swiss Ephemeris ─────────────────────────────────────────────────
swisseph.set_ephe_path('./ephe')

# ─── Состояния для ConversationHandler ───────────────────────────────────────
DATE, TIME_PERIOD, COORDINATES, PLACE, FORMAT = range(5)

# ─── REST API ─────────────────────────────────────────────────────────────────
@app.get("/natal")
def natal_analysis(
    date: str, time: str, lat: float, lon: float, tz: str
):
    try:
        year, month, day = date.split('-')
        conv_date = f"{day}/{month}/{year}"
        dt = Datetime(conv_date, time, tz)
        pos = GeoPos(lat, lon)
        chart = Chart(dt, pos, hsys=const.HOUSES_PLACIDUS)
        sun = chart.get(const.SUN)
        moon = chart.get(const.MOON)
        asc = chart.get(const.ASC)
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
                  {"role": "user", "content": prompt}],
        timeout=15
    )
    text = resp.choices[0].message.content.strip()
    return {"reply": text}

# ─── Telegram-бот: пошаговый сбор данных ───────────────────────────────────────

def start_handler(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Привет! Давай создадим твою натальную карту.\n"
        "Введите дату рождения в формате ГГГГ-ММ-ДД (например: 1990-05-03):"
    )
    return DATE


def date_handler(update: Update, context: CallbackContext):
    text = update.message.text.strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        update.message.reply_text("Неверный формат даты. Повторите: ГГГГ-ММ-ДД")
        return DATE
    context.user_data['date'] = text
    update.message.reply_text(
        "Отлично! Теперь укажи, когда ты родился: 'ночью', 'утром', 'днем' или 'вечером'."
    )
    return TIME_PERIOD


def time_period_handler(update: Update, context: CallbackContext):
    choice = update.message.text.strip().lower()
    mapping = {'ночью': '00:00', 'утром': '08:00', 'днем': '13:00', 'вечером': '18:00'}
    if choice not in mapping:
        update.message.reply_text("Выбери один из вариантов: 'ночью', 'утром', 'днем' или 'вечером'.")
        return TIME_PERIOD
    # фиксированное время для расчётов
    context.user_data['time'] = mapping[choice]
    update.message.reply_text(
        "Хорошо! Теперь укажи координаты места рождения: широта и долгота через запятую"
        " (например: 55.75,37.62):"
    )
    return COORDINATES


def coordinates_handler(update: Update, context: CallbackContext):
    text = update.message.text.strip()
    parts = [p.strip() for p in text.split(',')]
    try:
        context.user_data['lat'] = float(parts[0])
        context.user_data['lon'] = float(parts[1])
    except:
        update.message.reply_text("Не смог разобрать координаты. Введите как: 55.75,37.62")
        return COORDINATES
    update.message.reply_text("Отлично! Теперь введите название места рождения (например: Москва):")
    return PLACE


def place_handler(update: Update, context: CallbackContext):
    context.user_data['place'] = update.message.text.strip()
    update.message.reply_text(
        "Какую интерпретацию ты хочешь? Напиши 'короткую' или 'красочную':"
    )
    return FORMAT


def format_handler(update: Update, context: CallbackContext):
    choice = update.message.text.strip().lower()
    if choice not in ['короткую', 'красочную']:
        update.message.reply_text("Выбери 'короткую' или 'красочную'.")
        return FORMAT
    data = context.user_data
    resp = requests.get(
        f"{API_URL}/natal",
        params={
            'date': data['date'],
            'time': data['time'],
            'lat': data['lat'],
            'lon': data['lon'],
            'tz': '+00:00'
        }
    ).json()
    if 'error' in resp:
        update.message.reply_text(f"Ошибка расчёта: {resp['error']}")
    else:
        sun, moon, asc = resp['sun_sign'], resp['moon_sign'], resp['ascendant_sign']
        place = data.get('place')
        if choice == 'короткую':
            text = f"{place}: Солнце в {sun}, Луна в {moon}, Асцендент в {asc}."
        else:
            prompt = (
                f"Опиши натальную карту для человека, родившегося в {place} ({data['date']} {data['time']}), "
                f"Солнце в {sun}, Луна в {moon}, Асцендент в {asc}. "
                "Пиши развернуто и вдохновенно."
            )
            cgpt = requests.post(
                f"{API_URL}/chat",
                json={"prompt": prompt}
            ).json()
            text = cgpt.get('reply', 'Не удалось получить описание.')
        update.message.reply_text(text)
    return ConversationHandler.END


def cancel_handler(update: Update, context: CallbackContext):
    update.message.reply_text("Отменено.")
    return ConversationHandler.END

@app.on_event("startup")
def on_startup():
    logging.info("Запускаю Telegram-бота…")
    updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start_handler)],
        states={
            DATE:         [MessageHandler(Filters.text & ~Filters.command, date_handler)],
            TIME_PERIOD:  [MessageHandler(Filters.text & ~Filters.command, time_period_handler)],
            COORDINATES:  [MessageHandler(Filters.text & ~Filters.command, coordinates_handler)],
            PLACE:        [MessageHandler(Filters.text & ~Filters.command, place_handler)],
            FORMAT:       [MessageHandler(Filters.text & ~Filters.command, format_handler)],
        },
        fallbacks=[CommandHandler('cancel', cancel_handler)]
    )
    dp.add_handler(conv)
    updater.start_polling()
