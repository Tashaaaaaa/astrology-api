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
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove

from flatlib import const
from flatlib.chart import Chart
from flatlib.datetime import Datetime
from flatlib.geopos import GeoPos
import swisseph
import openai
import dateparser  # Для гибкого парсинга дат на разных языках

# ─── Настройка ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = FastAPI()

# Читаем секреты из окружения
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # полный URL для Telegram webhook
API_URL = os.getenv("API_URL", "http://127.0.0.1:10000")

if not OPENAI_API_KEY or not TELEGRAM_TOKEN or not WEBHOOK_URL:
    raise RuntimeError("Нужно задать OPENAI_API_KEY, TELEGRAM_TOKEN и WEBHOOK_URL в окружении")

openai.api_key = OPENAI_API_KEY
# Инициализируем Swiss Ephemeris
swisseph.set_ephe_path('./ephe')

# ─── Conversation States ─────────────────────────────────────────────────────
DATE, TIME_PERIOD, PLACE, FORMAT = range(4)

# ─── Создаём Telegram Bot и Dispatcher ────────────────────────────────────────
bot = Bot(token=TELEGRAM_TOKEN)
# Синхронный режим, один рабочий поток — избегаем дублирования
dp = Dispatcher(bot, None, workers=0, use_context=True)

# ─── REST API для натальной карты и ChatGPT ────────────────────────────────────
@app.get("/natal")
def natal_analysis(date: str, time: str, lat: float, lon: float, tz: str):
    try:
        y, m, d = date.split('-')
        dt = Datetime(f"{d}/{m}/{y}", time, tz)
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
        messages=[
            {"role": "system", "content": "Ты — опытный астролог-автор."},
            {"role": "user", "content": prompt}
        ],
        timeout=15
    )
    return {"reply": resp.choices[0].message.content.strip()}

# ─── Handlers for Conversation ─────────────────────────────────────────────────

def start_handler(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Привет! Давай создадим твою натальную карту.\n"
        "Введите дату рождения любым форматом, например: '3 мая 1990' или '1990-05-03'."
    )
    return DATE


def date_handler(update: Update, context: CallbackContext):
    text = update.message.text.strip()
    dt_obj = dateparser.parse(text, languages=['ru', 'en'])
    if not dt_obj:
        update.message.reply_text(
            "Не удалось распознать дату. Попробуйте снова, например '3 мая 1990' или '1990-05-03'."
        )
        return DATE
    date_iso = dt_obj.strftime('%Y-%m-%d')
    context.user_data['date'] = date_iso
    update.message.reply_text(
        "Отлично! Теперь выберите, когда вы родились: 'ночью', 'утром', 'днем' или 'вечером'."
    )
    return TIME_PERIOD


def time_period_handler(update: Update, context: CallbackContext):
    # Предлагаем варианты времени через клавиатуру
    keyboard = [["ночью", "утром", "днем", "вечером"]]
    markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    update.message.reply_text(
        "Когда вы родились? Выберите вариант:",
        reply_markup=markup
    )
    return TIME_PERIOD

# Обработка выбранного времени
@dp.message_handler(Filters.text & ~Filters.command, state=TIME_PERIOD)
def time_period_choice(update: Update, context: CallbackContext):
    choice = update.message.text.strip().lower()
    mapping = {'ночью': '00:00', 'утром': '08:00', 'днем': '13:00', 'вечером': '18:00'}
    if choice not in mapping:
        update.message.reply_text("Пожалуйста, выберите один из вариантов кнопок.")
        return TIME_PERIOD
    context.user_data['time'] = mapping[choice]
    # Снимаем клавиатуру
    update.message.reply_text(
        "Введите город рождения (например: Москва). Я сам определю координаты.",
        reply_markup=ReplyKeyboardRemove()
    )
    return PLACE


def place_handler(update: Update, context: CallbackContext):
    city = update.message.text.strip()
    # Запрос к Nominatim с указанием русского языка и User-Agent
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": city, "format": "json", "limit": 1},
            headers={"Accept-Language": "ru", "User-Agent": "astrology-bot/1.0"},
            timeout=5
        )
        data = r.json() if r.text else []
    except Exception:
        data = []
    # Если город не найден, пробуем добавить страну Россия
    if not data:
        try:
            fallback = f"{city}, Russia"
            r = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": fallback, "format": "json", "limit": 1},
                headers={"Accept-Language": "en", "User-Agent": "astrology-bot/1.0"},
                timeout=5
            )
            data = r.json() if r.text else []
        except Exception:
            data = []
    if not data:
        update.message.reply_text("Не удалось найти город Воронеж. Попробуйте ввести город и страну, например: 'Воронеж, Россия'.")
        return PLACE
    context.user_data['lat'] = float(data[0]['lat'])
    context.user_data['lon'] = float(data[0]['lon'])
    context.user_data['place'] = data[0].get('display_name', city)
    update.message.reply_text("Выберите формат интерпретации: 'короткую' или 'красочную'.")
    return FORMAT


def format_handler(update: Update, context: CallbackContext):
    choice = update.message.text.strip().lower()
    if choice not in ['короткую', 'красочную']:
        update.message.reply_text("Пожалуйста, введите 'короткую' или 'красочную'.")
        return FORMAT
    data = context.user_data
    try:
        r = requests.get(
            f"{API_URL}/natal",
            params={
                'date': data['date'], 'time': data['time'],
                'lat': data['lat'], 'lon': data['lon'], 'tz': '+00:00'
            },
            timeout=5
        )
        resp = r.json() if r.text else {}
    except Exception as e:
        resp = {'error': f'Ошибка запроса к сервису: {e}'}
    if 'error' in resp:
        text = f"Ошибка расчёта: {resp['error']}"
    else:
        sun, moon, asc = resp['sun_sign'], resp['moon_sign'], resp['ascendant_sign']
        place = data['place']
        if choice == 'короткую':
            text = f"{place}: Солнце в {sun}, Луна в {moon}, Асцендент в {asc}."
        else:
            prompt = (
                f"Опиши натальную карту для человека из {place} "
                f"({data['date']} {data['time']}), Солнце в {sun}, "
                f"Луна в {moon}, Асцендент в {asc}." 
                "Пиши вдохновенно и детально."
            )
            try:
                cgpt_r = requests.post(
                    f"{API_URL}/chat", json={'prompt': prompt}, timeout=10
                )
                text = cgpt_r.json().get('reply', 'Не удалось получить описание.')
            except Exception as e:
                text = f"Ошибка при запросе ChatGPT: {e}"
    update.message.reply_text(text)
    return ConversationHandler.END


def cancel_handler(update: Update, context: CallbackContext):
    update.message.reply_text("Отменено пользователем.")
    return ConversationHandler.END

# Регистрация ConversationHandler
conv = ConversationHandler(
    entry_points=[CommandHandler('start', start_handler)],
    states={
        DATE:        [MessageHandler(Filters.text & ~Filters.command, date_handler)],
        TIME_PERIOD: [MessageHandler(Filters.text & ~Filters.command, time_period_handler)],
        PLACE:       [MessageHandler(Filters.text & ~Filters.command, place_handler)],
        FORMAT:      [MessageHandler(Filters.text & ~Filters.command, format_handler)],
    },
    fallbacks=[CommandHandler('cancel', cancel_handler)],
    allow_reentry=False
)
dp.add_handler(conv)(conv)

# Webhook endpoint для Telegram
@app.post('/webhook')
async def telegram_webhook(req: Request):
    try:
        payload = await req.json()
    except Exception:
        return {'ok': True}
    update = Update.de_json(payload, bot)
    dp.process_update(update)
    return {'ok': True}

# Health check endpoint
@app.get('/')
def health():
    return {'status': 'ok'}

# Устанавливаем webhook при старте
@app.on_event("startup")
async def set_webhook():
    logging.info(f"Setting Telegram webhook: {WEBHOOK_URL}")
    bot.delete_webhook()
    bot.set_webhook(WEBHOOK_URL)
    logging.info("Webhook setup complete.")
