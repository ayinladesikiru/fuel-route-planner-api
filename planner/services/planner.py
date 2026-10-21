"""Orchestrates one trip: geocode -> route (1 API call) -> stations -> optimise."""
from __future__ import annotations

import time
from dataclasses import dataclass

from django.conf import settings

from planner.exceptions import InfeasibleTripError
from planner.services.geo import cumulative_miles, densify
from planner.services.geocoding import Geocoder
from planner.services.optimizer import Candidate, plan_fuel_stops, plan_fuel_stops_dp
from planner.services.routing import OSRMClient
from planner.services.station_index import CorridorStation, get_station_index


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

    @classmethod
    def defaults(cls, **overrides) -> TripOptions:
        cfg = settings.FUEL_PLANNER
        base = dict(
            max_range_miles=cfg["MAX_RANGE_MILES"], mpg=cfg["MILES_PER_GALLON"],
            start_fuel_gallons=None, corridor_miles=cfg["CORRIDOR_MILES"],
            stop_penalty=cfg["STOP_PENALTY_USD"],
        )
        base.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**base)


def _cheapest_per_bucket(stations: list[CorridorStation], bucket_miles: float) -> list[CorridorStation]:
    best: dict[int, CorridorStation] = {}
    for s in stations:
        b = int(s.mile_marker // bucket_miles)
        cur = best.get(b)
        if cur is None or (s.price, s.offset_miles) < (cur.price, cur.offset_miles):
            best[b] = s
    return sorted(best.values(), key=lambda s: s.mile_marker)


class TripPlanner:
    def __init__(self, geocoder: Geocoder | None = None, router: OSRMClient | None = None):
        self.geocoder = geocoder or Geocoder()
        self.router = router or OSRMClient()
        self.cfg = settings.FUEL_PLANNER

    def plan(self, start: str, finish: str, options: TripOptions) -> dict:
        t0 = time.perf_counter()
        origin = self.geocoder.geocode(start)
        destination = self.geocoder.geocode(finish)
        t_geo = time.perf_counter()

        route = self.router.route(origin.lat, origin.lon, destination.lat, destination.lon)
        t_route = time.perf_counter()

        # Mile markers along the (densified) geometry, scaled to OSRM's road distance.
        points = densify(route.points, 0.5)
        miles = cumulative_miles(points)
        if miles[-1] > 0:
            miles *= route.distance_miles / miles[-1]

        index = get_station_index()
        corridor = index.stations_along(points, miles, options.corridor_miles)
        start_gallons = options.tank_gallons if options.start_fuel_gallons is None else min(
            options.start_fuel_gallons, options.tank_gallons
        )

        def optimise(pool: list[CorridorStation]):
            cands = [Candidate(s.mile_marker, s.price, s) for s in pool]
            args = (cands, route.distance_miles, options.tank_gallons, options.mpg, start_gallons)
            return plan_fuel_stops_dp(*args, stop_penalty=options.stop_penalty), plan_fuel_stops(*args)

        try:
            fuel_plan, cheapest = optimise(_cheapest_per_bucket(corridor, self.cfg["BUCKET_MILES"]))
        except InfeasibleTripError:
            fuel_plan, cheapest = optimise(corridor)  # bucketing only thins candidates; retry with all
        t_opt = time.perf_counter()

        stops = []
        for n, p in enumerate(fuel_plan.purchases, start=1):
            cs: CorridorStation = p.candidate.payload
            row = index.rows[cs.index]
            stops.append({
                "stop_number": n,
                "mile_marker": round(cs.mile_marker, 1),
                "distance_from_route_miles": round(cs.offset_miles, 1),
                "price_per_gallon": round(cs.price, 3),
                "gallons": round(p.gallons, 2),
                "cost": round(p.cost, 2),
                "fuel_on_arrival_gallons": round(p.fuel_on_arrival, 2),
                "station": {
                    "opis_id": row["opis_id"], "name": row["name"].strip(), "address": row["address"].strip(),
                    "city": row["city"].strip(), "state": row["state"],
                    "lat": row["latitude"], "lon": row["longitude"],
                    "location_precision": row["geocode_precision"],
                },
            })

        gallons_used = route.distance_miles / options.mpg
        bought = fuel_plan.total_gallons
        result = {
            "start": origin.as_dict(),
            "finish": destination.as_dict(),
            "route": {
                "distance_miles": round(route.distance_miles, 1),
                "duration_hours": round(route.duration_hours, 2),
                "polyline": route.polyline,
                "polyline_precision": 5,
            },
            "vehicle": {
                "max_range_miles": options.max_range_miles,
                "miles_per_gallon": options.mpg,
                "tank_gallons": round(options.tank_gallons, 2),
                "start_fuel_gallons": round(start_gallons, 2),
            },
            "fuel_stops": stops,
            "summary": {
                "total_fuel_cost": round(fuel_plan.total_cost, 2),
                "total_gallons_purchased": round(bought, 2),
                "total_gallons_used": round(gallons_used, 2),
                "number_of_stops": len(stops),
                "average_price_paid": round(fuel_plan.total_cost / bought, 3) if bought else None,
                "fuel_left_at_finish_gallons": round(max(fuel_plan.end_gallons, 0.0), 2),
                "currency": "USD",
            },
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
                    "geocode": round((t_geo - t0) * 1000, 1),
                    "route": round((t_route - t_geo) * 1000, 1),
                    "optimise": round((t_opt - t_route) * 1000, 1),
                    "total": round((time.perf_counter() - t0) * 1000, 1),
                },
            },
        }
        if options.include_geometry:
            result["route"]["geometry"] = {
                "type": "LineString",
                "coordinates": [[round(lon, 5), round(lat, 5)] for lat, lon in route.points],
            }
        # Kept for the HTML map; stripped from the JSON response by the view.
        result["_corridor"] = [
            {"lat": index.rows[s.index]["latitude"], "lon": index.rows[s.index]["longitude"],
             "price": round(s.price, 3), "name": index.rows[s.index]["name"].strip(),
             "city": index.rows[s.index]["city"].strip(), "state": index.rows[s.index]["state"],
             "mile": round(s.mile_marker, 1)}
            for s in corridor
        ]
        return result
