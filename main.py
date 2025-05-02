from fastapi import FastAPI, Query
from flatlib.chart import Chart
from flatlib.datetime import Datetime
from flatlib.geopos import GeoPos
import logging

app = FastAPI()

@app.get("/natal")
def natal_analysis(
    date: str = Query(...),
    time: str = Query(...),
    lat: float = Query(...),
    lon: float = Query(...),
    tz: str = Query(...)
):
    try:
        # Преобразуем дату из YYYY-MM-DD в DD/MM/YYYY
        year, month, day = date.split('-')
        converted_date = f"{day}/{month}/{year}"

        # Форматируем координаты
        pos = GeoPos(f"{lat:.4f}", f"{lon:.4f}")
        dt = Datetime(converted_date, time, tz)
        chart = Chart(dt, pos)

        sun = chart.get('SUN')
        moon = chart.get('MOON')
        asc = chart.Ascendant

        return {
            "sun_sign": sun.sign,
            "moon_sign": moon.sign,
            "ascendant": asc.sign,
            "interpretation": f"Солнце в {sun.sign}, Луна в {moon.sign}, Асцендент в {asc.sign}"
        }

    except Exception as e:
        logging.exception("Ошибка при расчёте:")
        return {"error": str(e)}
