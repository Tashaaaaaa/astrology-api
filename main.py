import os
import logging
import requests
from fastapi import FastAPI, Request, Body
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
import dateparser  # гибкий парсинг дат

# ─── Настройка ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = FastAPI()

# Переменные окружения
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")  # https://.../webhook
API_URL        = os.getenv("API_URL", "http://127.0.0.1:10000")

if not OPENAI_API_KEY or not TELEGRAM_TOKEN or not WEBHOOK_URL:
    raise RuntimeError("Не заданы OPENAI_API_KEY, TELEGRAM_TOKEN или WEBHOOK_URL")

openai.api_key = OPENAI_API_KEY
swisseph.set_ephe_path('./ephe')  # Swiss Ephemeris

# ─── Состояния диалога ────────────────────────────────────────────────────────
DATE, TIME_PERIOD, PLACE, FORMAT = range(4)

# ─── Телеграм бот и диспетчер ─────────────────────────────────────────────────
bot = Bot(TELEGRAM_TOKEN)
dp  = Dispatcher(bot, None, workers=1, use_context=True)

# ─── Вспомогательные функции ───────────────────────────────────────────────────
def parse_date_text(text: str):
    dt = dateparser.parse(text, languages=['ru', 'en'])
    return dt.strftime('%Y-%m-%d') if dt else None

# ─── Handlers ─────────────────────────────────────────────────────────────────

def start_handler(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Привет! Давай создадим твою натальную карту.\n"
        "Введите дату рождения любым форматом, например '3 мая 1990' или '1990-05-03'."
    )
    return DATE


def date_handler(update: Update, context: CallbackContext):
    iso = parse_date_text(update.message.text)
    if not iso:
        update.message.reply_text(
            "Не удалось распознать дату. Повтори, например '3 мая 1990' или '1990-05-03'."
        )
        return DATE
    context.user_data['date'] = iso
    keyboard = [["ночью", "утром", "днем", "вечером"]]
    markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    update.message.reply_text(
        "Когда ты родился? Выбери время суток:",
        reply_markup=markup
    )
    return TIME_PERIOD


def time_handler(update: Update, context: CallbackContext):
    choice = update.message.text.lower()
    mapping = {'ночью':'00:00','утром':'08:00','днем':'13:00','вечером':'18:00'}
    if choice not in mapping:
        update.message.reply_text("Используй кнопки под сообщением.")
        return TIME_PERIOD
    context.user_data['time'] = mapping[choice]
    update.message.reply_text(
        "Теперь введи город рождения (например: Москва). Я сам найду координаты.",
        reply_markup=ReplyKeyboardRemove()
    )
    return PLACE


def place_handler(update: Update, context: CallbackContext):
    city = update.message.text.strip()
    coords = geocode_city(city)
    if not coords:
        update.message.reply_text(
            "Не нашёл город. Попробуй 'Воронеж, Россия' или другой город."
        )
        return PLACE
    lat, lon, display = coords
    context.user_data.update({'lat':lat,'lon':lon,'place':display})
    update.message.reply_text("Выбери формат интерпретации: 'короткую' или 'красочную'.")
    return FORMAT


def format_handler(update: Update, context: CallbackContext):
    choice = update.message.text.lower()
    if choice not in ['короткую','красочную']:
        update.message.reply_text("Пиши 'короткую' или 'красочную'.")
        return FORMAT
    data = context.user_data
    resp = safe_get(
        f"{API_URL}/natal",
        params={
            'date': data['date'], 'time': data['time'],
            'lat': data['lat'], 'lon': data['lon'], 'tz':'+00:00'
        }
    ) or {}
    if 'error' in resp:
        return update.message.reply_text(f"Ошибка расчёта: {resp['error']}")
    sun, moon, asc = resp['sun_sign'], resp['moon_sign'], resp['ascendant_sign']
    if choice=='короткую':
        text = f"{data['place']}: ☀️ {sun}, 🌙 {moon}, ASC {asc}."
    else:
        prompt = (
            f"Опиши натальную карту для человека из {data['place']} "
            f"({data['date']} {data['time']}), Солнце в {sun}, Луна в {moon}, Асцендент в {asc}."
            " Вдохновенно и подробно."
        )
        cg = safe_post(f"{API_URL}/chat", json={'prompt':prompt}) or {}
        text = cg.get('reply','Ошибка GPT.')
    update.message.reply_text(text)
    return ConversationHandler.END


def cancel_handler(update: Update, context: CallbackContext):
    update.message.reply_text("Отмена")
    return ConversationHandler.END

# ─── Утилиты ──────────────────────────────────────────────────────────────────
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
        except: arr=[]
        if arr:
            d = arr[0]
            return float(d['lat']), float(d['lon']), d.get('display_name', city)
    return None


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

# ─── Регистрация ConversationHandler ─────────────────────────────────────────
conv = ConversationHandler(
    entry_points=[CommandHandler('start', start_handler)],
    states={
        DATE:        [MessageHandler(Filters.text & ~Filters.command, date_handler)],
        TIME_PERIOD:[MessageHandler(Filters.text & ~Filters.command, time_handler)],
        PLACE:      [MessageHandler(Filters.text & ~Filters.command, place_handler)],
        FORMAT:     [MessageHandler(Filters.text & ~Filters.command, format_handler)],
    },
    fallbacks=[CommandHandler('cancel', cancel_handler)],
    allow_reentry=False
)
dp.add_handler(conv)

# ─── Webhook & Health ────────────────────────────────────────────────────────
@app.post('/webhook')
async def telegram_webhook(req: Request):
    try:
        upd = await req.json()
    except:
        return {'ok':True}
    dp.process_update(Update.de_json(upd, bot))
    return {'ok':True}

@app.get('/')
def health():
    return {'status':'ok'}

# ─── Настройка Webhook ───────────────────────────────────────────────────────
@app.on_event('startup')
async def set_webhook():
    logging.info(f"Setting webhook: {WEBHOOK_URL}")
    bot.delete_webhook()
    bot.set_webhook(WEBHOOK_URL)
    logging.info("Webhook OK")
