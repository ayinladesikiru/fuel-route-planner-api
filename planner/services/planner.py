"""Orchestrates one trip: geocode -> route (1 API call) -> stations -> optimise -> response."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from django.conf import settings

from planner.exceptions import InfeasibleTripError
from planner.services.geo import cumulative_miles, densify
from planner.services.geocoding import Geocoder
from planner.services.optimizer import Candidate, FuelPlan, plan_fuel_stops, plan_fuel_stops_dp
from planner.services.routing import OSRMClient, Route
from planner.services.station_index import CorridorStation, StationIndex, get_station_index


@dataclass
class TripOptions:
    max_range_miles: float
    mpg: float
    start_fuel_gallons: float | None  # None -> full tank
    corridor_miles: float
    stop_penalty: float
    include_geometry: bool = False

    @property
    def tank_gallons(self) -> float:
        return self.max_range_miles / self.mpg

    @property
    def start_gallons(self) -> float:
        if self.start_fuel_gallons is None:
            return self.tank_gallons
        return min(self.start_fuel_gallons, self.tank_gallons)

    @classmethod
    def from_settings(cls, **overrides) -> TripOptions:
        """Defaults from ``settings.FUEL_PLANNER``; ``None`` overrides are ignored."""
        cfg = settings.FUEL_PLANNER
        values = {
            "max_range_miles": cfg["MAX_RANGE_MILES"],
            "mpg": cfg["MILES_PER_GALLON"],
            "start_fuel_gallons": None,
            "corridor_miles": cfg["CORRIDOR_MILES"],
            "stop_penalty": cfg["STOP_PENALTY_USD"],
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)


@dataclass
class TripPlan:
    """The API response body, plus the corridor stations the HTML map draws."""

    response: dict
    corridor: list[dict] = field(default_factory=list)


class TripPlanner:
    def __init__(
        self,
        geocoder: Geocoder | None = None,
        router: OSRMClient | None = None,
        stations: StationIndex | None = None,
    ):
        self.geocoder = geocoder or Geocoder()
        self.router = router or OSRMClient()
        self.stations = stations or get_station_index()
        self.bucket_miles = settings.FUEL_PLANNER["BUCKET_MILES"]

    def plan(self, start: str, finish: str, options: TripOptions) -> TripPlan:
        t0 = time.perf_counter()
        origin = self.geocoder.geocode(start)
        destination = self.geocoder.geocode(finish)
        t_geocode = time.perf_counter()

        route = self.router.route(origin.lat, origin.lon, destination.lat, destination.lon)
        t_route = time.perf_counter()

        corridor = self._stations_along(route, options.corridor_miles)
        best, cheapest = self._optimise(corridor, route.distance_miles, options)
        t_optimise = time.perf_counter()

        response = {
            "start": origin.as_dict(),
            "finish": destination.as_dict(),
            "route": self._route_json(route, options.include_geometry),
            "vehicle": {
                "max_range_miles": options.max_range_miles,
                "miles_per_gallon": options.mpg,
                "tank_gallons": round(options.tank_gallons, 2),
                "start_fuel_gallons": round(options.start_gallons, 2),
            },
            "fuel_stops": [self._stop_json(n, p) for n, p in enumerate(best.purchases, start=1)],
            "summary": self._summary_json(best, route.distance_miles / options.mpg),
            "optimisation": {
                "objective": "minimise fuel cost + stop_penalty x stops",
                "stop_penalty_usd": options.stop_penalty,
                "corridor_miles": options.corridor_miles,
                "absolute_cheapest_plan": {
                    "total_fuel_cost": round(cheapest.total_cost, 2),
                    "number_of_stops": len(cheapest.purchases),
                },
            },
            "meta": {
                "routing_api_calls": self.router.api_calls,
                "geocoding_api_calls": self.geocoder.api_calls,
                "route_from_cache": route.from_cache,
                "stations_in_corridor": len(corridor),
                "timing_ms": {
                    "geocode": _ms(t0, t_geocode),
                    "route": _ms(t_geocode, t_route),
                    "optimise": _ms(t_route, t_optimise),
                    "total": _ms(t0, time.perf_counter()),
                },
            },
        }
        return TripPlan(response=response, corridor=[self._corridor_json(s) for s in corridor])

    # -- steps -----------------------------------------------------------------

    def _stations_along(self, route: Route, corridor_miles: float) -> list[CorridorStation]:
        """Stations near the route, each with the road-mile at which it is reached."""
        points = densify(route.points, max_step_miles=0.5)
        miles = cumulative_miles(points)
        if miles[-1] > 0:  # scale straight-segment length to OSRM's road distance
            miles *= route.distance_miles / miles[-1]
        return self.stations.stations_along(points, miles, corridor_miles)

    def _optimise(
        self, corridor: list[CorridorStation], total_miles: float, options: TripOptions
    ) -> tuple[FuelPlan, FuelPlan]:
        """Return (plan with stop penalty, absolute-cheapest plan)."""

        def run(pool: list[CorridorStation]) -> tuple[FuelPlan, FuelPlan]:
            candidates = [Candidate(s.mile_marker, s.price, s) for s in pool]
            args = (candidates, total_miles, options.tank_gallons, options.mpg, options.start_gallons)
            return plan_fuel_stops_dp(*args, stop_penalty=options.stop_penalty), plan_fuel_stops(*args)

        try:
            return run(_cheapest_per_bucket(corridor, self.bucket_miles))
        except InfeasibleTripError:
            # Thinning can, rarely, remove the only station that bridges a gap.
            return run(corridor)

    # -- serialisation -----------------------------------------------------------

    @staticmethod
    def _route_json(route: Route, include_geometry: bool) -> dict:
        data = {
            "distance_miles": round(route.distance_miles, 1),
            "duration_hours": round(route.duration_hours, 2),
            "polyline": route.polyline,
            "polyline_precision": 5,
        }
        if include_geometry:
            data["geometry"] = {
                "type": "LineString",
                "coordinates": [[round(lon, 5), round(lat, 5)] for lat, lon in route.points],
            }
        return data

    def _stop_json(self, number: int, purchase) -> dict:
        cs: CorridorStation = purchase.candidate.payload
        row = self.stations.rows[cs.index]
        return {
            "stop_number": number,
            "mile_marker": round(cs.mile_marker, 1),
            "distance_from_route_miles": round(cs.offset_miles, 1),
            "price_per_gallon": round(cs.price, 3),
            "gallons": round(purchase.gallons, 2),
            "cost": round(purchase.cost, 2),
            "fuel_on_arrival_gallons": round(purchase.fuel_on_arrival, 2),
            "station": {
                "opis_id": row["opis_id"],
                "name": row["name"],
                "address": row["address"],
                "city": row["city"],
                "state": row["state"],
                "lat": row["latitude"],
                "lon": row["longitude"],
                "location_precision": row["geocode_precision"],
            },
        }

    @staticmethod
    def _summary_json(plan: FuelPlan, gallons_used: float) -> dict:
        bought = plan.total_gallons
        return {
            "total_fuel_cost": round(plan.total_cost, 2),
            "total_gallons_purchased": round(bought, 2),
            "total_gallons_used": round(gallons_used, 2),
            "number_of_stops": len(plan.purchases),
            "average_price_paid": round(plan.total_cost / bought, 3) if bought else None,
            "fuel_left_at_finish_gallons": round(max(plan.end_gallons, 0.0), 2),
            "currency": "USD",
        }

    def _corridor_json(self, s: CorridorStation) -> dict:
        row = self.stations.rows[s.index]
        return {
            "lat": row["latitude"],
            "lon": row["longitude"],
            "price": round(s.price, 3),
            "name": row["name"],
            "city": row["city"],
            "state": row["state"],
            "mile": round(s.mile_marker, 1),
        }


def _cheapest_per_bucket(stations: list[CorridorStation], bucket_miles: float) -> list[CorridorStation]:
    """Keep the cheapest station in each ``bucket_miles`` stretch of the route."""
    best: dict[int, CorridorStation] = {}
    for s in stations:
        bucket = int(s.mile_marker // bucket_miles)
        current = best.get(bucket)
        if current is None or (s.price, s.offset_miles) < (current.price, current.offset_miles):
            best[bucket] = s
    return sorted(best.values(), key=lambda s: s.mile_marker)


def _ms(start: float, end: float) -> float:
    return round((end - start) * 1000, 1)
