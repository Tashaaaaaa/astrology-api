from fastapi import FastAPI, Query
from flatlib import const
from flatlib.chart import Chart
from flatlib.datetime import Datetime
from flatlib.geopos import GeoPos
import swisseph
import logging
import os
import openai

# Настраиваем API-ключ OpenAI
openai.api_key = os.getenv("OPENAI_API_KEY")


swisseph.set_ephe_path('./ephe')

app = FastAPI()

@app.get("/natal")
def natal_analysis(
    date: str = Query(..., description="Дата в формате YYYY-MM-DD"),
    time: str = Query(..., description="Время в формате HH:MM"),
    lat: float = Query(...),
    lon: float = Query(...),
    tz: str = Query(..., description="UTC-смещение, например '+03:00'")
):
    try:
        # Переставляем год-месяц-день в день/месяц/год
        year, month, day = date.split('-')
        converted_date = f"{day}/{month}/{year}"

        dt = Datetime(converted_date, time, tz)
        pos = GeoPos(lat, lon)

        # Используем правильную константу для Placidus
        chart = Chart(dt, pos, hsys=const.HOUSES_PLACIDUS)

        sun = chart.get(const.SUN)
        moon = chart.get(const.MOON)
        asc  = chart.get(const.ASC)

        return {
            "sun_sign":          sun.sign,
            "moon_sign":         moon.sign,
            "ascendant_sign":    asc.sign,
            "interpretation":    f"Солнце в {sun.sign}, Луна в {moon.sign}, Асцендент в {asc.sign}"
        }

    except Exception as e:
        logging.exception("Ошибка при расчёте:")
        return {"error": str(e)}

@app.post("/chat")
async def chat_gpt(payload: dict):
    prompt = payload.get("prompt", "").strip()
    if not prompt:
        return {"error": "Empty prompt"}
    resp = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}]
    )
    text = resp.choices[0].message.content.strip()
    return {"reply": text}
