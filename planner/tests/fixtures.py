"""Offline stand-ins for the OSRM API, built from real US city coordinates.

Routes are straight segments between interstate waypoint cities, which is
close enough to real geometry for exercising corridor search and the optimiser
without network access.
"""
import numpy as np

from planner.services.geo import cumulative_miles, densify, encode_polyline
from planner.services.geocoding import get_gazetteer
from planner.services.routing import METERS_PER_MILE

NYC_TO_LA = [
    ("New York", "NY"), ("Allentown", "PA"), ("Harrisburg", "PA"), ("Breezewood", "PA"),
    ("Wheeling", "WV"), ("Columbus", "OH"), ("Indianapolis", "IN"), ("Effingham", "IL"),
    ("St. Louis", "MO"), ("Rolla", "MO"), ("Springfield", "MO"), ("Joplin", "MO"), ("Tulsa", "OK"),
    ("Oklahoma City", "OK"), ("Amarillo", "TX"), ("Tucumcari", "NM"), ("Albuquerque", "NM"),
    ("Gallup", "NM"), ("Flagstaff", "AZ"), ("Kingman", "AZ"), ("Needles", "CA"), ("Barstow", "CA"),
    ("Los Angeles", "CA"),
]
CHICAGO_TO_DETROIT = [("Chicago", "IL"), ("Gary", "IN"), ("Kalamazoo", "MI"), ("Ann Arbor", "MI"), ("Detroit", "MI")]


def waypoint_coords(waypoints):
    gaz = get_gazetteer()
    return np.array([gaz.lookup(c, s)[:2] for c, s in waypoints])


def osrm_payload(waypoints, road_factor=1.08, mph=60.0) -> dict:
    pts = densify(waypoint_coords(waypoints), 2.0)
    miles = cumulative_miles(pts)[-1] * road_factor
    return {
        "code": "Ok",
        "routes": [{
            "distance": miles * METERS_PER_MILE,
            "duration": miles / mph * 3600,
            "geometry": encode_polyline(pts),
        }],
    }


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class FakeSession:
    """Records calls; returns the given payload for every request."""

    def __init__(self, payload, status=200):
        self.payload, self.status, self.calls = payload, status, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload, self.status)
