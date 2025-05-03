import os
import logging
import requests
from fastapi import FastAPI, Request
from telegram import Update, Bot, ReplyKeyboardMarkup, ReplyKeyboardRemove
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
import dateparser  # гибкий парсинг дат и времени

# ─── Настройка приложения ─────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = FastAPI()

# Переменные окружения
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")
# Базовый URL для собственного API: либо задаётся, либо выводится из WEBHOOK_URL
API_URL = os.getenv("API_URL")
if not API_URL and WEBHOOK_URL:
    API_URL = WEBHOOK_URL.rstrip('/webhook')
if not OPENAI_API_KEY or not TELEGRAM_TOKEN or not WEBHOOK_URL or not API_URL:
    raise RuntimeError("Нужно задать OPENAI_API_KEY, TELEGRAM_TOKEN, WEBHOOK_URL и API_URL в окружении")

openai.api_key = OPENAI_API_KEY
swisseph.set_ephe_path('./ephe')  # Swiss Ephemeris path

# ─── Состояния ───────────────────────────────────────────────────────────────
DATE, TIME, PLACE, FORMAT = range(4)

# ─── Telegram Bot и Dispatcher ─────────────────────────────────────────────────
bot = Bot(TELEGRAM_TOKEN)
dp  = Dispatcher(bot, None, workers=1, use_context=True)

# ─── Вспомогательные функции ───────────────────────────────────────────────────
def parse_date(text: str):
    dt = dateparser.parse(text, languages=['ru', 'en'])
    return dt.date().strftime('%Y-%m-%d') if dt else None

def parse_time(text: str):
    dt = dateparser.parse(text, languages=['ru', 'en'])
    return dt.time().strftime('%H:%M') if dt else None

def geocode_city(city: str):
    for lang, suffix in [("ru", ""), ("en", ", Russia")]:
        try:
            r = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": city + suffix, "format": "json", "limit": 1},
                headers={"Accept-Language": lang, "User-Agent": "astrology-bot/1.0"},
                timeout=5
            )
            arr = r.json() if r.text else []
        except:
            arr = []
        if arr:
            d = arr[0]
            return float(d['lat']), float(d['lon']), d.get('display_name', city)
    return None

# Safe wrapper для запросов к API собственного сервиса
def safe_get_natal(date, time, lat, lon, tz='+00:00'):
    try:
        r = requests.get(
            f"{API_URL}/natal",
            params={'date': date, 'time': time, 'lat': lat, 'lon': lon, 'tz': tz},
            timeout=5
        )
        return r.json() if r.status_code == 200 and r.text else {'error': f'HTTP {r.status_code}'}
    except Exception as e:
        return {'error': 'Сервис недоступен'}

# ─── Handlers ─────────────────────────────────────────────────────────────────

def start_handler(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Привет! Создаем натальную карту.\n"
        "Шаг 1/4: Введите дату рождения, например '3 мая 1990'."
    )
    return DATE


def date_handler(update: Update, context: CallbackContext):
    iso = parse_date(update.message.text)
    if not iso:
        update.message.reply_text("Не понял дату. Попробуйте '3 мая 1990'.")
        return DATE
    context.user_data['date'] = iso
    update.message.reply_text("Шаг 2/4: Введите время рождения 'HH:MM', например '14:30'.")
    return TIME


def time_handler(update: Update, context: CallbackContext):
    tm = parse_time(update.message.text)
    if not tm:
        update.message.reply_text("Не понял время. Попробуйте '14:30'.")
        return TIME
    context.user_data['time'] = tm
    update.message.reply_text("Шаг 3/4: Введите город рождения, например 'Москва'.")
    return PLACE


def place_handler(update: Update, context: CallbackContext):
    res = geocode_city(update.message.text)
    if not res:
        update.message.reply_text("Город не найден. Попробуйте 'Воронеж, Россия'.")
        return PLACE
    context.user_data['lat'], context.user_data['lon'], context.user_data['place'] = res
    update.message.reply_text("Шаг 4/4: 'короткую' или 'красочную' интерпретацию? Напишите слово.")
    return FORMAT


def format_handler(update: Update, context: CallbackContext):
    choice = update.message.text.lower()
    if choice not in ['короткую', 'красочную']:
        update.message.reply_text("Введите 'короткую' или 'красочную'.")
        return FORMAT
    data = context.user_data
    resp = safe_get_natal(data['date'], data['time'], data['lat'], data['lon'])
    if 'error' in resp:
        update.message.reply_text(f"Ошибка расчёта: {resp['error']}")
        return ConversationHandler.END
    sun, moon, asc = resp['sun_sign'], resp['moon_sign'], resp['ascendant_sign']
    if choice == 'короткую':
        text = f"{data['place']}: ☀️{sun}, 🌙{moon}, ASC{asc}."
    else:
        prompt = (
            f"Натальная карта для {data['place']} ({data['date']} {data['time']}): "
            f"Солнце в {sun}, Луна в {moon}, Асцендент в {asc}. "
            "Подробное красочное описание."
        )
        try:
            cg = requests.post(f"{API_URL}/chat", json={'prompt': prompt}, timeout=10).json()
            text = cg.get('reply', 'Ошибка GPT')
        except:
            text = 'Ошибка GPT'
    update.message.reply_text(text)
    return ConversationHandler.END


def cancel_handler(update: Update, context: CallbackContext):
    update.message.reply_text("Отменено.")
    return ConversationHandler.END

# ─── Регистрация разговорного хендлера ─────────────────────────────────────────
conv = ConversationHandler(
    entry_points=[CommandHandler('start', start_handler)],
    states={
        DATE:   [MessageHandler(Filters.text & ~Filters.command, date_handler)],
        TIME:   [MessageHandler(Filters.text & ~Filters.command, time_handler)],
        PLACE:  [MessageHandler(Filters.text & ~Filters.command, place_handler)],
        FORMAT: [MessageHandler(Filters.text & ~Filters.command, format_handler)],
    },
    fallbacks=[CommandHandler('cancel', cancel_handler)],
    allow_reentry=False
)

dp.add_handler(conv)

# ─── Webhook и Health ─────────────────────────────────────────────────────────
@app.post('/webhook')
async def telegram_webhook(req: Request):
    try:
        data = await req.json()
    except:
        return {'ok': True}
    dp.process_update(Update.de_json(data, bot))
    return {'ok': True}

@app.get('/')
def health():
    return {'status': 'ok'}

@app.on_event('startup')
async def on_startup():
    logging.info(f"Setting webhook to {WEBHOOK_URL}")
    bot.delete_webhook()
    bot.set_webhook(WEBHOOK_URL)
    logging.info("Webhook set.")
