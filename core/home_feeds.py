"""Home feeds: weather (MET Norway) + news (Google News RSS).

Keyless, free feeds only; no API keys anywhere.

* Weather — MET Norway's locationforecast (CC BY 4.0 data, free
  API, needs an identifying User-Agent). It needs coordinates:
  the caller passes lat/lon when known; otherwise the city is
  geocoded once through Nominatim (keyless, ODbL data — the
  User-Agent carries the attribution). LICENCE FLAG (owed for
  the parent's ruling): Nominatim is ODbL — if ruled out,
  weather falls back to the honest "unavailable" line and the
  geocode path is never called.
* News — Google News RSS (keyless). Headlines only: title +
  source name, never a fetched article body.

Every fetch is honest about failure: a failed fetch returns
``ok: False`` (weather) or an empty list (news) and the Home
surface renders the "unavailable" line — never a stale or
invented reading.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as _ET

_USER_AGENT = "VYUK/6.3 (home weather + news; contact: vyuk-assistant)"
_MET_URL = ("https://api.met.no/weatherapi/locationforecast/2.0/"
            "compact?lat={lat}&lon={lon}")
_NOMINATIM_URL = ("https://nominatim.openstreetmap.org/search"
                  "?q={query}&format=json&limit=1")
_NEWS_URL = ("https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en")


# ------------------------------------------------------------------
# Condition words (MET symbol_code -> plain word)
# ------------------------------------------------------------------

def condition_word(symbol_code: str) -> str:
    """The MET symbol code in plain words ("Partly cloudy", ...)."""
    code = str(symbol_code or "").strip().lower()
    if not code:
        return "Unknown"
    if "partlycloudy" in code:
        return "Partly cloudy"
    if "clearsky" in code:
        return "Clear"
    if "thunder" in code:
        return "Thunderstorm"
    if "snow" in code or "sleet" in code:
        return "Snow"
    if "fog" in code:
        return "Fog"
    if "rain" in code:
        return "Rain"
    if "cloudy" in code:
        return "Cloudy"
    return "Unknown"


def parse_forecast(payload: dict) -> dict:
    """One MET locationforecast payload -> the weather read.

    Returns {"ok", "temp_c", "condition", "wind_ms"}; ok is False
    (with "error") when the payload has no usable first entry.
    """
    try:
        series = (payload or {}).get("properties", {}).get("timeseries") or []
        first = series[0]
        data = first.get("data", {})
        details = data.get("instant", {}).get("details", {})
        temp = details.get("air_temperature")
        wind = details.get("wind_speed")
        symbol = ""
        nxt = data.get("next_1_hours") or data.get("next_6_hours") or {}
        symbol = ((nxt.get("summary") or {}).get("symbol_code") or "")
        if temp is None:
            return {"ok": False, "error": "no temperature in forecast"}
        return {
            "ok": True,
            "temp_c": float(temp),
            "condition": condition_word(symbol),
            "wind_ms": float(wind) if wind is not None else None,
        }
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        return {"ok": False, "error": f"unparseable forecast: {exc}"}


def parse_news_rss(rss_text: str, limit: int = 5) -> list[dict]:
    """One RSS document -> [{title, source}], newest first."""
    try:
        root = _ET.fromstring(rss_text or "")
    except _ET.ParseError:
        return []
    heads = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        source = (item.findtext("source") or "").strip()
        if title:
            heads.append({"title": title, "source": source or "News"})
        if len(heads) >= limit:
            break
    return heads


# ------------------------------------------------------------------
# Live fetches (network; the parsers above are the testable seam)
# ------------------------------------------------------------------

def _http_get(url: str, timeout: int = 10) -> str:
    request = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def geocode_city(city: str, timeout: int = 10) -> dict:
    """One-shot Nominatim geocode: {"ok", "lat", "lon"} or no-ok.

    LICENCE FLAG (owed): Nominatim data is ODbL. The call is a
    keyless API lookup, not a redistribution — but the parent
    rules it before the feature is relied on; until then the
    caller treats a ruled-out geocode as a failed fetch.
    """
    try:
        url = _NOMINATIM_URL.format(
            query=urllib.parse.quote(str(city or "").strip()))
        rows = json.loads(_http_get(url, timeout=timeout))
        if not rows:
            return {"ok": False, "error": "city not found"}
        return {"ok": True, "lat": float(rows[0]["lat"]),
                "lon": float(rows[0]["lon"])}
    except Exception as exc:  # noqa: BLE001 — any fetch failure
        return {"ok": False, "error": str(exc)}


def weather_for_city(city: str, lat=None, lon=None,
                     timeout: int = 10) -> dict:
    """Current weather for a city: {"ok", "temp_c", "condition",
    "wind_ms"} — or {"ok": False, "error": ...}.

    lat/lon, when given, skip the geocode entirely (the repro's
    live leg passes Mumbai's coordinates directly).
    """
    try:
        if lat is None or lon is None:
            geo = geocode_city(city, timeout=timeout)
            if not geo.get("ok"):
                return {"ok": False,
                        "error": geo.get("error", "geocode failed")}
            lat, lon = geo["lat"], geo["lon"]
        url = _MET_URL.format(lat=float(lat), lon=float(lon))
        payload = json.loads(_http_get(url, timeout=timeout))
        parsed = parse_forecast(payload)
        if not parsed.get("ok"):
            return parsed
        parsed["city"] = str(city or "")
        return parsed
    except Exception as exc:  # noqa: BLE001 — any fetch failure
        return {"ok": False, "error": str(exc)}


def fetch_news_headlines(limit: int = 5, timeout: int = 10) -> list[dict]:
    """Google News RSS headlines: [{title, source}] — [] on failure."""
    try:
        return parse_news_rss(_http_get(_NEWS_URL, timeout=timeout),
                              limit=limit)
    except Exception:  # noqa: BLE001 — any fetch failure
        return []
