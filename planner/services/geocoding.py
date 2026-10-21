"""Resolve user input ("Chicago, IL", "41.88,-87.63") to coordinates.

Order of resolution (cheapest first):
  1. Literal "lat,lon"                        -> 0 API calls
  2. Offline US city gazetteer (29k places)    -> 0 API calls
  3. Nominatim (OpenStreetMap), US-only, cached -> 1 API call, only as fallback

The same gazetteer gives every fuel station its coordinates when the price
file is loaded, so station data never costs an API call either.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import re
from dataclasses import dataclass
from functools import lru_cache

import requests
from django.conf import settings
from django.core.cache import cache

from planner.exceptions import GeocodingError, OutsideUSAError
from planner.services.geo import is_in_usa

logger = logging.getLogger(__name__)

_COORD_RE = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")
_COUNTRY_SUFFIX_RE = re.compile(r",?\s*(usa|us|u\.s\.a\.|u\.s\.|united states(?: of america)?)\s*$", re.I)

# The handful of station cities missing from the gazetteer (checked against the price file).
MANUAL_CITY_COORDS = {
    ("GA", "PORT WENTWORTH"): (32.1491, -81.1632),
    ("IL", "UNIVERSITY PARK"): (41.4431, -87.6842),
    ("AL", "EVERGREEN"): (31.4335, -86.9567),
    ("VA", "HENRICO"): (37.5059, -77.3328),
    ("NJ", "ELIZABETHPORT"): (40.6532, -74.1896),
    ("OH", "BROOKPARK"): (41.3984, -81.8046),
}


def normalize_city(name: str) -> str:
    s = name.upper().strip().replace("-", " ").replace(".", "")
    s = re.sub(r"[^A-Z0-9 ]", "", s)
    s = re.sub(r"\bSAINT\b", "ST", s)
    s = re.sub(r"\bFORT\b", "FT", s)
    s = re.sub(r"\bMOUNT\b", "MT", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass(frozen=True)
class Place:
    query: str
    name: str
    lat: float
    lon: float
    source: str  # "coordinates" | "gazetteer" | "nominatim"

    def as_dict(self) -> dict:
        return {"query": self.query, "name": self.name, "lat": self.lat, "lon": self.lon, "source": self.source}


class Gazetteer:
    """In-memory lookup of US places by (state, normalised city name)."""

    def __init__(self, path):
        self.by_key: dict[tuple[str, str], tuple[float, float, str]] = {}
        self.by_city: dict[str, list[str]] = {}
        self.state_names: dict[str, str] = {}
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                state, city = row["state"], row["city"]
                key = (state, normalize_city(city))
                if key in self.by_key:
                    continue
                self.by_key[key] = (float(row["lat"]), float(row["lon"]), city)
                self.by_city.setdefault(key[1], []).append(state)
                self.state_names[row["state_name"].upper()] = state
        self.state_codes = set(self.state_names.values())

    def state_code(self, text: str) -> str | None:
        t = text.strip().upper().replace(".", "")
        if t in self.state_codes:
            return t
        return self.state_names.get(t)

    def lookup(self, city: str, state: str) -> tuple[float, float, str, str] | None:
        """Return (lat, lon, display_city, precision) or None."""
        key = (state.upper(), normalize_city(city))
        if key in self.by_key:
            lat, lon, name = self.by_key[key]
            return lat, lon, name, "city"
        if key in MANUAL_CITY_COORDS:
            lat, lon = MANUAL_CITY_COORDS[key]
            return lat, lon, city.strip().title(), "manual"
        return None

    def resolve_free_text(self, text: str) -> tuple[float, float, str] | None:
        """Parse 'City, ST' / 'City, State' / 'City ST' / 'City' (if unambiguous)."""
        text = _COUNTRY_SUFFIX_RE.sub("", text.strip()).strip(" ,")
        candidates: list[tuple[str, str]] = []
        if "," in text:
            city, _, state = text.rpartition(",")
            candidates.append((city, state))
        else:
            parts = text.split()
            for n in (2, 1):  # "New York NY", "Salt Lake City Utah", "West Virginia"-style names
                if len(parts) > n:
                    candidates.append((" ".join(parts[:-n]), " ".join(parts[-n:])))
        for city, state_text in candidates:
            code = self.state_code(state_text)
            if code:
                hit = self.lookup(city, code)
                if hit:
                    lat, lon, name, _ = hit
                    return lat, lon, f"{name}, {code}"
        states = self.by_city.get(normalize_city(text), [])
        if len(states) == 1:
            lat, lon, name = self.by_key[(states[0], normalize_city(text))]
            return lat, lon, f"{name}, {states[0]}"
        return None


@lru_cache(maxsize=1)
def get_gazetteer() -> Gazetteer:
    return Gazetteer(settings.FUEL_PLANNER["GAZETTEER_PATH"])


class Geocoder:
    def __init__(self, session: requests.Session | None = None):
        self.cfg = settings.FUEL_PLANNER
        self.session = session or requests.Session()
        self.api_calls = 0

    def geocode(self, text: str) -> Place:
        text = (text or "").strip()
        if not text:
            raise GeocodingError("Location must not be empty.")

        if m := _COORD_RE.match(text):
            lat, lon = float(m.group(1)), float(m.group(2))
            self._ensure_usa(lat, lon, text)
            return Place(text, f"{lat:.5f},{lon:.5f}", lat, lon, "coordinates")

        if hit := get_gazetteer().resolve_free_text(text):
            lat, lon, name = hit
            return Place(text, name, lat, lon, "gazetteer")

        return self._nominatim(text)

    def _nominatim(self, text: str) -> Place:
        cache_key = "geocode:v1:" + hashlib.sha1(text.lower().encode()).hexdigest()
        if (cached := cache.get(cache_key)) is not None:
            return Place(**cached)
        try:
            self.api_calls += 1
            resp = self.session.get(
                self.cfg["NOMINATIM_URL"],
                params={"q": text, "format": "jsonv2", "countrycodes": "us", "limit": 1},
                headers={"User-Agent": self.cfg["HTTP_USER_AGENT"]},
                timeout=self.cfg["HTTP_TIMEOUT_SECONDS"],
            )
            resp.raise_for_status()
            results = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Nominatim lookup failed for %r: %s", text, exc)
            raise GeocodingError(
                f"Could not resolve '{text}'. Use 'City, ST' (e.g. 'Dallas, TX') or 'lat,lon'."
            ) from exc
        if not results:
            raise GeocodingError(f"No US location found for '{text}'.")
        top = results[0]
        place = Place(text, top.get("display_name", text), float(top["lat"]), float(top["lon"]), "nominatim")
        self._ensure_usa(place.lat, place.lon, text)
        cache.set(cache_key, place.__dict__, self.cfg["GEOCODE_CACHE_SECONDS"])
        return place

    @staticmethod
    def _ensure_usa(lat: float, lon: float, text: str) -> None:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise GeocodingError(f"'{text}' is not a valid lat,lon pair.")
        if not is_in_usa(lat, lon):
            raise OutsideUSAError(f"'{text}' is outside the USA; both locations must be within the USA.")
