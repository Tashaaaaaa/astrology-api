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
        dt = Datetime(date, time, tz)
        pos = GeoPos(str(lat), str(lon))
        chart = Chart(dt, pos)

        sun = chart.get('SUN')
        moon = chart.get('MOON')
        asc = chart.Ascendant

        return {
            "sun_sign": sun.sign,
            "moon_sign": moon.sign,
            "ascendant": asc.sign,
            "interpretation": f"Солнце в {sun.sign}, Луна в {moon.sign}, Асцендент в {
