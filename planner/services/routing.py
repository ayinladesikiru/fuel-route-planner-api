"""Routing provider client — exactly one HTTP call per (uncached) trip.

Uses the free OSRM demo server (no API key). A single ``/route`` request with
``overview=full`` returns distance, duration and the full geometry, which is
everything the planner needs. Responses are cached, so re-running a trip or
opening its map costs zero further calls.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import requests
from django.conf import settings
from django.core.cache import cache

from planner.exceptions import NoRouteError, RoutingError
from planner.services.geo import decode_polyline

logger = logging.getLogger(__name__)

METERS_PER_MILE = 1609.344


@dataclass
class Route:
    distance_miles: float
    duration_hours: float
    polyline: str          # encoded polyline (precision 5), compact for clients
    points: np.ndarray     # (n, 2) [lat, lon]
    from_cache: bool = False


class OSRMClient:
    def __init__(self, session: requests.Session | None = None):
        self.cfg = settings.FUEL_PLANNER
        self.session = session or requests.Session()
        self.api_calls = 0

    def route(self, start_lat: float, start_lon: float, end_lat: float, end_lon: float) -> Route:
        key = f"route:v1:{start_lat:.5f},{start_lon:.5f}:{end_lat:.5f},{end_lon:.5f}"
        data = cache.get(key)
        from_cache = data is not None
        if not from_cache:
            data = self._fetch(start_lat, start_lon, end_lat, end_lon)
            cache.set(key, data, self.cfg["ROUTE_CACHE_SECONDS"])

        return Route(
            distance_miles=data["distance"] / METERS_PER_MILE,
            duration_hours=data["duration"] / 3600.0,
            polyline=data["geometry"],
            points=decode_polyline(data["geometry"]),
            from_cache=from_cache,
        )

    def _fetch(self, start_lat, start_lon, end_lat, end_lon) -> dict:
        url = (
            f"{self.cfg['OSRM_BASE_URL'].rstrip('/')}/route/v1/driving/"
            f"{start_lon:.6f},{start_lat:.6f};{end_lon:.6f},{end_lat:.6f}"
        )
        params = {"overview": "full", "geometries": "polyline", "steps": "false", "alternatives": "false"}
        self.api_calls += 1
        try:
            resp = self.session.get(
                url, params=params, headers={"User-Agent": self.cfg["HTTP_USER_AGENT"]},
                timeout=self.cfg["HTTP_TIMEOUT_SECONDS"],
            )
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.error("OSRM request failed: %s", exc)
            raise RoutingError("The routing provider could not be reached. Please retry.") from exc

        code = payload.get("code")
        if code in {"NoRoute", "NoSegment"}:
            raise NoRouteError("No drivable route was found between these locations.")
        if resp.status_code != 200 or code != "Ok" or not payload.get("routes"):
            raise RoutingError(f"Routing provider error: {payload.get('message') or code or resp.status_code}")

        r = payload["routes"][0]
        return {"distance": r["distance"], "duration": r["duration"], "geometry": r["geometry"]}
