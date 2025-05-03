from fastapi import FastAPI, Query
from flatlib.chart import Chart
from flatlib.datetime import Datetime
from flatlib.geopos import GeoPos
from flatlib.ephem import swe
from flatlib import ephem
import logging

# Подключение эфемерид Swiss Ephemeris
swe.set_ephe_path('./ephe')
ephem.use(swe)

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
        year, month, day = date.split('-')
        converted_date = f"{day}/{month}/{year}"

        dt = Datetime(converted_date, time, tz)
        pos = GeoPos(lat, lon)
        chart = Chart(dt, pos, hsys='P')

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
