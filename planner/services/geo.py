"""Small, dependency-light geometry helpers (all distances in statute miles)."""

from __future__ import annotations

import numpy as np

EARTH_RADIUS_MILES = 3958.7613


def to_unit_xyz(lat_deg, lon_deg) -> np.ndarray:
    """Lat/lon (degrees) -> points on the unit sphere, shape (n, 3).

    Euclidean (chord) distance between unit vectors is monotonic with
    great-circle distance, which lets a plain KD-tree do geo nearest-neighbour.
    """
    lat = np.radians(np.asarray(lat_deg, dtype=float))
    lon = np.radians(np.asarray(lon_deg, dtype=float))
    cos_lat = np.cos(lat)
    return np.column_stack((cos_lat * np.cos(lon), cos_lat * np.sin(lon), np.sin(lat)))


def miles_to_chord(miles: float) -> float:
    return 2.0 * np.sin((miles / EARTH_RADIUS_MILES) / 2.0)


def chord_to_miles(chord):
    return 2.0 * EARTH_RADIUS_MILES * np.arcsin(np.clip(np.asarray(chord) / 2.0, 0.0, 1.0))


def haversine_miles(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))


def cumulative_miles(points: np.ndarray) -> np.ndarray:
    """Cumulative distance along a (n, 2) [lat, lon] polyline."""
    if len(points) < 2:
        return np.zeros(len(points))
    seg = haversine_miles(points[:-1, 0], points[:-1, 1], points[1:, 0], points[1:, 1])
    return np.concatenate(([0.0], np.cumsum(seg)))


def densify(points: np.ndarray, max_step_miles: float = 0.5) -> np.ndarray:
    """Insert points so no segment is longer than ``max_step_miles``.

    Long straight interstate segments can have vertices miles apart; densifying
    keeps the nearest-vertex projection of stations onto the route accurate.
    """
    if len(points) < 2:
        return points
    seg = haversine_miles(points[:-1, 0], points[:-1, 1], points[1:, 0], points[1:, 1])
    steps = np.maximum(1, np.ceil(seg / max_step_miles).astype(int))
    if steps.max() == 1:
        return points
    out = []
    for i, n in enumerate(steps):
        t = np.arange(n)[:, None] / n
        out.append(points[i] + (points[i + 1] - points[i]) * t)
    out.append(points[-1:])
    return np.vstack(out)


def decode_polyline(encoded: str, precision: int = 5) -> np.ndarray:
    """Decode a Google/OSRM encoded polyline into an (n, 2) [lat, lon] array."""
    coords, index, lat, lon = [], 0, 0, 0
    factor = 10**precision
    length = len(encoded)
    while index < length:
        for axis in (0, 1):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lon += delta
        coords.append((lat / factor, lon / factor))
    return np.array(coords, dtype=float)


def encode_polyline(points, precision: int = 5) -> str:
    """Inverse of :func:`decode_polyline` (used by tests and fixtures)."""
    factor = 10**precision
    out, prev_lat, prev_lon = [], 0, 0
    for lat, lon in points:
        ilat, ilon = round(lat * factor), round(lon * factor)
        for delta in (ilat - prev_lat, ilon - prev_lon):
            v = ~(delta << 1) if delta < 0 else delta << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev_lat, prev_lon = ilat, ilon
    return "".join(out)


# Rough bounding boxes: contiguous US, Alaska, Hawaii.
_USA_BOXES = (
    (24.3, 49.6, -125.0, -66.8),
    (51.0, 71.6, -180.0, -129.9),
    (18.8, 22.4, -160.5, -154.7),
)


def is_in_usa(lat: float, lon: float) -> bool:
    return any(s <= lat <= n and w <= lon <= e for s, n, w, e in _USA_BOXES)
