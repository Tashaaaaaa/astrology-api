import os
import logging
import requests
from fastapi import FastAPI, Request, Body
from telegram import Update, Bot, ReplyKeyboardRemove
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
API_URL        = os.getenv("API_URL", "http://127.0.0.1:10000")
if not OPENAI_API_KEY or not TELEGRAM_TOKEN or not WEBHOOK_URL:
    raise RuntimeError("Не заданы OPENAI_API_KEY, TELEGRAM_TOKEN или WEBHOOK_URL")
openai.api_key = OPENAI_API_KEY
swisseph.set_ephe_path('./ephe')

# ─── Состояния диалога ─────────────────────────────────────────────────────────
DATE, TIME, PLACE, FORMAT = range(4)

# ─── Инициализация Telegram Bot и Dispatcher ───────────────────────────────────
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
    for lang, suffix in [("ru",""),("en",", Russia")]:
        try:
            r = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": city+suffix, "format":"json", "limit":1},
                headers={"Accept-Language":lang, "User-Agent":"astro-bot/1.0"},
                timeout=5
            )
            arr = r.json() if r.text else []
        except:
            arr = []
        if arr:
            d = arr[0]
            return float(d['lat']), float(d['lon']), d.get('display_name', city)
    return None

# ─── Safe API calls ────────────────────────────────────────────────────────────
def safe_get(url, **kwargs):
    try:
        r = requests.get(url, **kwargs, timeout=5)
        return r.json() if r.text else None
    except:
        return {'error':'Сервис недоступен'}

def safe_post(url, **kwargs):
    try:
        r = requests.post(url, **kwargs, timeout=10)
        return r.json() if r.text else None
    except:
        return None

# ─── Handlers диалога ─────────────────────────────────────────────────────────

def start_handler(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Привет! Давай создадим твою натальную карту.\n"
        "Шаг 1: Введите дату рождения, например '3 мая 1990' или '1990-05-03'."
    )
    return DATE


def date_handler(update: Update, context: CallbackContext):
    iso = parse_date(update.message.text)
    if not iso:
        update.message.reply_text("Не распознал дату. Попробуйте снова, например '3 мая 1990'.")
        return DATE
    context.user_data['date'] = iso
    update.message.reply_text("Шаг 2: Введите время рождения в формате 'HH:MM', например '14:30'.")
    return TIME


def time_handler(update: Update, context: CallbackContext):
    tm = parse_time(update.message.text)
    if not tm:
        update.message.reply_text("Не распознал время. Попробуйте, например '14:30'.")
        return TIME
    context.user_data['time'] = tm
    update.message.reply_text("Шаг 3: Введите город рождения, например 'Москва'.")
    return PLACE


def place_handler(update: Update, context: CallbackContext):
    city = update.message.text.strip()
    coords = geocode_city(city)
    if not coords:
        update.message.reply_text("Не нашёл город. Попробуйте 'Воронеж, Россия'.")
        return PLACE
    lat, lon, display = coords
    context.user_data.update({'lat':lat,'lon':lon,'place':display})
    update.message.reply_text("Шаг 4: Выберите формат интерпретации: 'короткую' или 'красочную'.")
    return FORMAT


def format_handler(update: Update, context: CallbackContext):
    choice = update.message.text.lower()
    if choice not in ['короткую','красочную']:
        update.message.reply_text("Пожалуйста, 'короткую' или 'красочную'.")
        return FORMAT
    data = context.user_data
    # Получаем натальную карту
    resp = safe_get(
        f"{API_URL}/natal",
        params={
            'date': data['date'], 'time': data['time'],
            'lat': data['lat'], 'lon': data['lon'], 'tz': '+00:00'
        }
    ) or {}
    if 'error' in resp:
        update.message.reply_text(f"Ошибка расчёта: {resp['error']}")
        return ConversationHandler.END
    sun, moon, asc = resp['sun_sign'], resp['moon_sign'], resp['ascendant_sign']
    if choice == 'короткую':
        text = f"{data['place']}: ☀️ {sun}, 🌙 {moon}, ASC {asc}."
    else:
        prompt = (
            f"Опиши натальную карту для человека из {data['place']} "
            f"({data['date']} {data['time']}), Солнце в {sun}, Луна в {moon}, ASC {asc}."
            " Дай красочное подробное описание."
        )
        cg = safe_post(f"{API_URL}/chat", json={'prompt':prompt}) or {}
        text = cg.get('reply', 'Ошибка GPT.')
    update.message.reply_text(text)
    return ConversationHandler.END


def cancel_handler(update: Update, context: CallbackContext):
    update.message.reply_text("Диалог прерван пользователем.")
    return ConversationHandler.END

# ─── Регистрация ConversationHandler ─────────────────────────────────────────
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

# ─── Webhook и healthcheck ──────────────────────────────────────────────────
@app.post('/webhook')
async def telegram_webhook(req: Request):
    try:
        upd = await req.json()
    except:
        return {'ok': True}
    dp.process_update(Update.de_json(upd, bot))
    return {'ok': True}

@app.get('/')
def health():
    return {'status': 'ok'}

@app.on_event('startup')
async def set_webhook():
    logging.info(f"Устанавливаем webhook: {WEBHOOK_URL}")
    bot.delete_webhook()
    bot.set_webhook(WEBHOOK_URL)
    logging.info("Webhook установлен.")
