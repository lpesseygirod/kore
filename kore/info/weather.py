"""The weather for the home (BUILD-PLAN 1: "questions: … weather"; PEPE.md "Do I need a coat?").

Facts come from code, never from Claude: a forecast is fetched from a provider, kept in the
database, and turned into plain facts (sky in Spanish words, temperatures, rain, wind). Claude
only words them. With no connection, the last forecast is used for up to 12 hours, with the
day and time it was made, so Pepe can say "según la previsión de ayer a las 20:00". Older than
that, or none: Pepe says it can't check, and never guesses. A reading of "now" is only given
while it is under an hour old.

Times: the provider gives instants (unix time); each hour is turned into local time with the
home's time zone, so the clock change in spring and autumn can't shift an hour.

Fetching happens in a background thread (kore.info.fetch): a turn waits at most 3 seconds.

Provider: Open-Meteo (no key). Its free API is for non-commercial use only (CC BY 4.0,
attribution "Weather data by Open-Meteo.com"); fine for the family trial. Selling needs its
paid plan or another provider: one interface, swappable.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol

from kore.info import http
from kore.info import store as info
from kore.info.fetch import IN_TURN_WAIT_S, Fetcher
from kore.scheduler.clock import Clock

PLACE = "weather_place"
CACHE = "weather_forecast"
REFRESH = timedelta(minutes=30)
RETRY_AFTER_FAILURE = timedelta(minutes=2)
STALE = timedelta(hours=12)        # older than this, the forecast isn't used at all
NOW_FRESH = timedelta(hours=1)     # a "now" reading older than this isn't given as now
HOT_DAY_MAX_C = 30                 # a hot day: more water reminders (PEPE.md 6)
DAYS = 4                           # one more than Pepe offers, so "pasado mañana" works just after midnight
OFFERED = 3

SKY = {0: "despejado", 1: "casi despejado", 2: "parcialmente nublado", 3: "nublado", 45: "niebla", 48: "niebla",
       51: "llovizna débil", 53: "llovizna", 55: "llovizna intensa", 56: "llovizna helada", 57: "llovizna helada",
       61: "lluvia débil", 63: "lluvia", 65: "lluvia fuerte", 66: "lluvia helada", 67: "lluvia helada",
       71: "nieve débil", 73: "nieve", 75: "nieve fuerte", 77: "granizo fino", 80: "chubascos débiles",
       81: "chubascos", 82: "chubascos fuertes", 85: "chubascos de nieve", 86: "chubascos de nieve fuertes",
       95: "tormenta", 96: "tormenta con granizo", 99: "tormenta con granizo"}
_CODE = {}
for _code, _word in sorted(SKY.items()):
    _CODE[_word] = _code  # the word's worst code: to pick the worst sky of a few hours
DAY_NAMES = {0: "today", 1: "tomorrow", 2: "day_after"}
HOURLY = ("temperature_2m", "precipitation_probability", "weather_code", "wind_speed_10m")
DAILY = ("weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_probability_max",
         "wind_speed_10m_max")


@dataclass
class Place:
    name: str
    lat: float
    lon: float
    region: str = ""


class Provider(Protocol):
    name: str

    def forecast(self, place: Place) -> dict | None: ...


class OpenMeteo:
    name = "open-meteo"
    base = "https://api.open-meteo.com/v1/forecast"
    geo = "https://geocoding-api.open-meteo.com/v1/search"

    def forecast(self, place: Place) -> dict | None:
        q = urllib.parse.urlencode({
            # About 1 km: enough for the weather, and the house itself is never sent.
            "latitude": f"{place.lat:.2f}", "longitude": f"{place.lon:.2f}", "timezone": "Europe/Madrid",
            "timeformat": "unixtime", "forecast_days": DAYS, "wind_speed_unit": "kmh",
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
            "hourly": ",".join(HOURLY), "daily": ",".join(DAILY) + ",uv_index_max"})
        status, body = http.get(f"{self.base}?{q}")
        return _json(body) if status == 200 else None

    def geocode(self, name: str) -> list[Place]:
        q = urllib.parse.urlencode({"name": name, "count": 5, "language": "es", "format": "json"})
        status, body = http.get(f"{self.geo}?{q}")
        data = _json(body) if status == 200 else None
        out = []
        for r in (data or {}).get("results", []) or []:
            try:
                out.append(Place(str(r["name"]), float(r["latitude"]), float(r["longitude"]),
                                 ", ".join(str(r[k]) for k in ("admin2", "admin1", "country") if r.get(k))))
            except (KeyError, TypeError, ValueError):
                continue
        return out


def _json(body: bytes | None) -> dict | None:
    try:
        data = json.loads(body.decode("utf-8")) if body else None
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


@dataclass
class FakeWeather:
    """Tests and scenarios: a forecast built by `fake_forecast`, or None (the internet unplugged)."""
    data: dict | None = None
    online: bool = True
    calls: int = 0
    name: str = "fake"

    def forecast(self, place: Place) -> dict | None:
        self.calls += 1
        return self.data if self.online else None


def fake_forecast(at: datetime, days: dict[str, dict], now: dict | None = None) -> dict:
    """An Open-Meteo-shaped forecast (unix times) made at `at` (aware, local), from a short description:
    {"today": {"code": 61, "max": 14, "min": 7, "rain": 80, "wind": 20, "now": 11,
               "hours": {"10": {"code": 0, "temp": 12, "rain": 0, "wind": 10}}}, "tomorrow": {...}, ...}."""
    tz = at.tzinfo
    start = at.date()
    offset = int(at.utcoffset().total_seconds())
    daily = {k: [] for k in ("time", *DAILY, "uv_index_max")}
    hourly = {k: [] for k in ("time", *HOURLY)}
    names = {0: "today", 1: "tomorrow", 2: "day_after", 3: "day_3"}
    for i in range(DAYS):
        d = days.get(names[i], {})
        day = start + timedelta(days=i)
        lo, hi = d.get("min", 8), d.get("max", 18)
        midnight = datetime.combine(day, time(0), tz)
        daily["time"].append(int(midnight.timestamp()))
        daily["weather_code"].append(d.get("code", 1))
        daily["temperature_2m_max"].append(hi)
        daily["temperature_2m_min"].append(lo)
        daily["precipitation_probability_max"].append(d.get("rain", 0))
        daily["wind_speed_10m_max"].append(d.get("wind", 10))
        daily["uv_index_max"].append(d.get("uv", 3))
        t = midnight.astimezone(timezone.utc)
        while t.astimezone(tz).date() == day:  # 23, 24 or 25 hours: the clock change is real here too
            h = t.astimezone(tz).hour
            o = d.get("hours", {}).get(str(h), {})
            hourly["time"].append(int(t.timestamp()))
            hourly["temperature_2m"].append(o.get("temp", round(lo + (hi - lo) * max(0, 1 - abs(h - 15) / 9), 1)))
            hourly["precipitation_probability"].append(o.get("rain", d.get("rain", 0)))
            hourly["weather_code"].append(o.get("code", d.get("code", 1)))
            hourly["wind_speed_10m"].append(o.get("wind", d.get("wind", 10)))
            t += timedelta(hours=1)
    today = days.get("today", {})
    current = now or {"temperature_2m": today.get("now", today.get("max", 18)), "weather_code": today.get("code", 1),
                      "wind_speed_10m": today.get("wind", 10), "precipitation": 0}
    current.setdefault("apparent_temperature", current.get("temperature_2m"))
    current.setdefault("time", int(at.timestamp()))
    return {"timezone": "Europe/Madrid", "utc_offset_seconds": offset, "current": current, "daily": daily,
            "hourly": hourly}


@dataclass
class Weather:
    conn: sqlite3.Connection
    clock: Clock
    provider: Provider
    _tried: datetime | None = field(default=None, repr=False)
    _fetcher: Fetcher = field(default_factory=Fetcher, repr=False)

    # --- the place -----------------------------------------------------------------

    def place(self) -> Place | None:
        got = info.get(self.conn, PLACE, self.clock.tz)
        if got is None:
            return None
        v = got[0]
        return Place(v["name"], v["lat"], v["lon"], v.get("region", ""))

    def set_place(self, place: Place) -> None:
        info.put(self.conn, PLACE, place.__dict__, self.clock.now())
        self.conn.execute("DELETE FROM info WHERE key = ?", (CACHE,))  # the old place's forecast
        self._tried = None
        self._fetcher = Fetcher()  # a fetch for the old place, if any, is dropped

    # --- fetching ------------------------------------------------------------------

    def _due(self, now: datetime) -> bool:
        got = info.get(self.conn, CACHE, self.clock.tz)
        if got is None:
            return True
        data, at = got
        if not timedelta(0) <= now - at < REFRESH:
            return True
        days = _days(data)
        return not days or days[0] != now.date()  # past midnight: yesterday's forecast starts yesterday

    def refresh(self, wait: float = IN_TURN_WAIT_S) -> bool:
        """Fetch a new forecast if due (over 30 minutes old, or from yesterday), waiting at most `wait` seconds;
        otherwise the fetch carries on in the background and the next call collects it. True if saved now.
        After a failure, no new try for 2 minutes, so a turn never waits twice for a dead connection."""
        place = self.place()
        if place is None:
            return False
        saved = self._collect(0)
        now = self.clock.now()
        if not self._fetcher.busy and self._due(now) and not (
                self._tried and timedelta(0) <= now - self._tried < RETRY_AFTER_FAILURE):
            self._tried = now
            provider = self.provider
            self._fetcher.start(lambda: provider.forecast(place))
        return self._collect(wait) or saved

    def _collect(self, wait: float) -> bool:
        finished, data = self._fetcher.collect(wait)
        if not finished or not _valid(data):
            return False
        info.put(self.conn, CACHE, data, self.clock.now())
        self._tried = None  # the 2-minute pause is only after a failure
        return True

    def _cached(self) -> tuple[dict, datetime] | None:
        got = info.get(self.conn, CACHE, self.clock.tz)
        if got is None:
            return None
        data, at = got
        age = self.clock.now() - at
        if age > STALE or age < -timedelta(minutes=5):  # too old, or the clock jumped back past it
            return None
        return data, at

    # --- facts ---------------------------------------------------------------------

    def facts(self, day: int = 0, from_hour: int | None = None, to_hour: int | None = None,
              fetch: bool = True) -> dict:
        """Facts for Claude to word. day: 0 today, 1 tomorrow, 2 the day after."""
        place = self.place()
        if place is None:
            return {"ok": False, "error": "no_place",
                    "say": "You don't know where their home is yet, so you can't check the weather. Say so simply; "
                           "the family can set it up. Don't guess."}
        if fetch:
            self.refresh()
        got = self._cached()
        if got is None:
            return {"ok": False, "error": "no_forecast",
                    "say": "You can't check the weather right now (no connection). Say so simply; don't guess."}
        data, at = got
        now = self.clock.now()
        target = (now + timedelta(days=day)).date()
        days = _days(data)
        if day >= OFFERED or target not in days:
            return {"ok": False, "error": "beyond_forecast",
                    "say": "You only have the forecast for today, tomorrow and the day after."}
        i = days.index(target)
        d = data["daily"]
        out = {"ok": True, "place": place.name, "day": DAY_NAMES[day], "date": target.isoformat(),
               "sky": SKY.get(d["weather_code"][i], "variable"),
               "max_c": _round(d["temperature_2m_max"][i]), "min_c": _round(d["temperature_2m_min"][i]),
               "rain_chance_pct": d["precipitation_probability_max"][i],
               "wind_max_kmh": _round(d["wind_speed_10m_max"][i])}
        uvs = d.get("uv_index_max") or []
        uv = uvs[i] if len(uvs) == len(days) else None
        if uv is not None and uv >= 6:
            out["uv"] = "high"
        c = data.get("current") or {}
        if day == 0 and isinstance(c.get("time"), (int, float)) and \
                timedelta(0) <= now - datetime.fromtimestamp(c["time"], self.clock.tz) <= NOW_FRESH:
            out["now"] = {"temp_c": _round(c.get("temperature_2m")),
                          "feels_like_c": _round(c.get("apparent_temperature")),
                          "sky": SKY.get(c.get("weather_code"), "variable"), "wind_kmh": _round(c.get("wind_speed_10m"))}
        if day == 0 and from_hour is None:
            from_hour = now.hour  # today: what's left of it (rain at 3 am doesn't matter at 5 pm)
            out["whole_day_note"] = "sky and rain_chance_pct are for the whole day; rest_of_today is from now on"
        out.update(self._window(data, target, from_hour, to_hour))
        if day == 0 and out["hours"]:
            rest = [h for h in out["hours"] if h["sky"] is not None]
            out["rest_of_today"] = {
                "sky": max(rest, key=lambda h: _CODE.get(h["sky"], 0))["sky"] if rest else None,
                "rain_chance_pct": max((h["rain_chance_pct"] for h in out["hours"] if h["rain_chance_pct"] is not None),
                                       default=None)}
        if now - at > timedelta(hours=1):
            when = _spoken_when(at, now)
            out["forecast_from"] = when
            out["say"] = (f"No connection now: this is the forecast made {when}. Say so in passing "
                          "('según la previsión de …'); nothing here is a reading of right now.")
        return out

    def _window(self, data: dict, target: date, from_hour: int | None, to_hour: int | None) -> dict:
        """Each hour for a short window; for a long one, 3-hour blocks with the worst sky and the highest
        rain chance in each block, so rain between two sample hours can't disappear."""
        lo = 0 if from_hour is None else max(0, min(23, from_hour))
        hi = 23 if to_hour is None else max(0, min(23, to_hour))
        start = datetime.combine(target, time(lo), self.clock.tz)
        end = datetime.combine(target + timedelta(days=1 if hi < lo else 0), time(hi), self.clock.tz)
        h = data["hourly"]
        rows = []
        for i, t in enumerate(h["time"]):
            local = datetime.fromtimestamp(t, self.clock.tz)
            if start <= local <= end:
                rows.append((local, i))
        hours = []
        size = 1 if len(rows) <= 6 else 3
        for k in range(0, len(rows), size):
            block = rows[k:k + size]
            idx = [i for _, i in block]
            codes = [h["weather_code"][i] for i in idx if h["weather_code"][i] is not None]
            rain = [h["precipitation_probability"][i] for i in idx if h["precipitation_probability"][i] is not None]
            temps = [h["temperature_2m"][i] for i in idx if h["temperature_2m"][i] is not None]
            wind = [h["wind_speed_10m"][i] for i in idx if h["wind_speed_10m"][i] is not None]
            row = {"hour": block[0][0].hour}
            if size > 1:
                row["until"] = (block[-1][0] + timedelta(hours=1)).hour
            row.update({"temp_c": _round(max(temps)) if temps else None,
                        "sky": SKY.get(max(codes), "variable") if codes else None,
                        "rain_chance_pct": max(rain) if rain else None,
                        "wind_kmh": _round(max(wind)) if wind else None})
            hours.append(row)
        out: dict = {"hours": hours}
        if rows and rows[-1][0] < end:
            out["hours_note"] = "The forecast doesn't reach the end of that window."
        return out

    def brief_today(self) -> dict | None:
        """For the morning greeting and going out: today's forecast from the cache only (no waiting)."""
        f = self.facts(0, fetch=False)
        if not f.get("ok"):
            return None
        return {k: f[k] for k in ("sky", "max_c", "min_c", "rain_chance_pct", "wind_max_kmh", "uv", "now",
                                  "rest_of_today", "forecast_from", "say") if k in f}

    def hot_today(self) -> bool:
        f = self.facts(0, fetch=False)
        return bool(f.get("ok") and f["max_c"] is not None and f["max_c"] >= HOT_DAY_MAX_C)


def _days(data: dict) -> list[date]:
    """The local date of each daily entry (Open-Meteo gives local midnight as unix time)."""
    off = data.get("utc_offset_seconds") or 0
    try:
        return [datetime.fromtimestamp(t + off, timezone.utc).date() for t in data["daily"]["time"]]
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return []


def _spoken_when(at: datetime, now: datetime) -> str:
    if at.date() == now.date():
        return "today at " + at.strftime("%H:%M")
    if at.date() == (now - timedelta(days=1)).date():
        return "yesterday at " + at.strftime("%H:%M")
    return at.strftime("%Y-%m-%d %H:%M")


def _round(v):
    return None if v is None else round(v)


def _valid(data) -> bool:
    try:
        d, h = data["daily"], data["hourly"]
        n, m = len(d["time"]), len(h["time"])
        return (n > 0 and m > 0 and all(len(d[k]) == n for k in DAILY) and all(len(h[k]) == m for k in HOURLY)
                and all(isinstance(t, (int, float)) for t in d["time"] + h["time"]) and len(_days(data)) == n)
    except (KeyError, TypeError):
        return False
