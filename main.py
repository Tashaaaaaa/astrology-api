from fastapi import FastAPI, Query
from flatlib.chart import Chart
from flatlib.datetime import Datetime
from flatlib.geopos import GeoPos

app = FastAPI()

@app.get("/natal")
def natal_analysis(
    date: str = Query(..., example="1990-08-20"),
    time: str = Query(..., example="12:00"),
    lat: float = Query(..., example=55.7558),
    lon: float = Query(..., example=37.6176),
    tz: str = Query(..., example="+03:00")
):
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
        "interpretation": f"Солнце в {sun.sign}, Луна в {moon.sign}, Асцендент в {asc.sign} — основа личности."
    }


