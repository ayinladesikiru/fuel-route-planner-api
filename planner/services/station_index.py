"""In-memory spatial index of fuel stations.

All ~7.5k US stations are loaded from the database once per process into
numpy arrays. Finding the stations along a route is then a single vectorised
KD-tree query (milliseconds) instead of thousands of DB or API lookups.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from planner.exceptions import StationsNotLoadedError
from planner.services.geo import chord_to_miles, miles_to_chord, to_unit_xyz


@dataclass
class CorridorStation:
    index: int  # row in the StationIndex arrays
    mile_marker: float  # distance along the route where it is reached
    offset_miles: float  # straight-line distance from the route
    price: float


class StationIndex:
    def __init__(self, rows: list[dict]):
        if not rows:
            raise StationsNotLoadedError("No fuel stations loaded. Run: python manage.py load_fuel_stations")
        self.rows = rows
        self.lat = np.array([r["latitude"] for r in rows])
        self.lon = np.array([r["longitude"] for r in rows])
        self.price = np.array([float(r["retail_price"]) for r in rows])
        self.xyz = to_unit_xyz(self.lat, self.lon)

    @classmethod
    def from_db(cls) -> StationIndex:
        from planner.models import FuelStation

        rows = list(
            FuelStation.objects.values(
                "opis_id",
                "name",
                "address",
                "city",
                "state",
                "retail_price",
                "latitude",
                "longitude",
                "geocode_precision",
            )
        )
        return cls(rows)

    def stations_along(
        self, route_points: np.ndarray, route_miles: np.ndarray, corridor_miles: float
    ) -> list[CorridorStation]:
        """Stations within ``corridor_miles`` of the route, with their mile marker."""
        tree = cKDTree(to_unit_xyz(route_points[:, 0], route_points[:, 1]))
        dist, idx = tree.query(self.xyz, k=1, distance_upper_bound=miles_to_chord(corridor_miles))
        hit = np.isfinite(dist)
        offsets = chord_to_miles(dist[hit])
        markers = route_miles[idx[hit]]
        return [
            CorridorStation(int(i), float(m), float(o), float(self.price[i]))
            for i, m, o in zip(np.nonzero(hit)[0], markers, offsets, strict=False)
        ]


_index: StationIndex | None = None
_lock = threading.Lock()


def get_station_index() -> StationIndex:
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                _index = StationIndex.from_db()
    return _index


def reset_station_index() -> None:
    """Drop the cached index (called after the price file is re-imported)."""
    global _index
    with _lock:
        _index = None
