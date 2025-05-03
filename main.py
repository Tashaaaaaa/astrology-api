import os
import logging
import requests

from fastapi import FastAPI, Body
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

from flatlib import const
from flatlib.chart import Chart
from flatlib.datetime import Datetime
from flatlib.geopos import GeoPos
import swisseph

# ─── Настройка ────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI()

# Читаем секреты из переменных окружения
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
API_URL        = os.getenv("API_URL")  # сам же на проде не понадобится, но пусть будет

if not OPENAI_API_KEY:
    raise RuntimeError("Переменная OPENAI_API_KEY не задана")
if not TELEGRAM_TOKEN:
    raise RuntimeError("Переменная TELEGRAM_TOKEN не задана")

# ─── Ваши существующие endpoint’ы ─────────────────────────────────────────────
swisseph.set_ephe_path('./ephe')

@app.get("/natal")
def natal_analysis(
    date: str,
    time: str,
    lat: float,
    lon: float,
    tz: str,
):
    try:
        year, month, day = date.split('-')
        conv = f"{day}/{month}/{year}"
        dt  = Datetime(conv, time, tz)
        pos = GeoPos(lat, lon)
        chart = Chart(dt, pos, hsys=const.HOUSES_PLACIDUS)

        sun  = chart.get(const.SUN)
        moon = chart.get(const.MOON)
        asc  = chart.get(const.ASC)

        return {
            "sun_sign":       sun.sign,
            "moon_sign":      moon.sign,
            "ascendant_sign": asc.sign,
            "interpretation": f"Солнце в {sun.sign}, Луна в {moon.sign}, Асцендент в {asc.sign}"
        }
    except Exception as e:
        logging.exception("Ошибка в /natal:")
        return {"error": str(e)}

@app.post("/chat")
async def chat_gpt(payload: dict = Body(...)):
    prompt = payload.get("prompt", "").strip()
    if not prompt:
        return {"error": "Empty prompt"}
    # инициализируем здесь openai
    import openai
    openai.api_key = OPENAI_API_KEY
    logging.info(f"ChatGPT prompt: {prompt!r}")
    resp = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        timeout=15
    )
    text = resp.choices[0].message.content.strip()
    logging.info(f"ChatGPT reply: {text[:50]!r}…")
    return {"reply": text}

# ─── Настройка и запуск Telegram-бота при старте FastAPI ──────────────────────
def start_handler(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Привет! Я астролог-бот.\n"
        "• Натальная карта: /natal YYYY-MM-DD HH:MM LAT LON TZ\n"
        "• Общение: просто напишите любой текст"
    )

def natal_handler(update: Update, context: CallbackContext):
    text = update.message.text
    try:
        _, date, time, lat, lon, tz = text.split()
        params = {"date": date, "time": time, "lat": float(lat), "lon": float(lon), "tz": tz}
        r = requests.get(f"http://127.0.0.1:10000/natal", params=params, timeout=10)
        data = r.json()
        reply = data.get("interpretation") or data.get("error")
    except:
        reply = "Неверный формат. Пример:\n/natal 2025-05-03 09:00 55.75 37.62 +03:00"
    update.message.reply_text(reply)

def echo_handler(update: Update, context: CallbackContext):
    prompt = update.message.text
    r = requests.post(
        f"http://127.0.0.1:10000/chat",
        json={"prompt": prompt},
        timeout=10
    )
    data = r.json()
    update.message.reply_text(data.get("reply", "Ошибка сервера."))

@app.on_event("startup")
def on_startup():
    logging.info("Стартуем Telegram-бота…")
    updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start_handler))
    dp.add_handler(CommandHandler("natal", natal_handler))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, echo_handler))

    updater.start_polling()  # запускаем в фоне

# ────────────────────────────────────────────────────────────────────────────────
