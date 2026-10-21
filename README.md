# Fuel Route Planner API

A Django 6.1 + DRF API that takes a start and finish location in the USA and returns:

* the driving route (distance, duration, encoded polyline, optional GeoJSON) **and an interactive map**,
* the **cost-optimal fuel stops** along it for a vehicle with a **500-mile range** at **10 mpg**,
* the **total money spent on fuel**.

It makes **one call** to the free routing API per new trip, and **zero** for any trip it has already seen.
A cross-country request finishes in tens of milliseconds once the route is back.

```
POST /api/v1/route/   {"start": "New York, NY", "finish": "Los Angeles, CA"}
GET  /api/v1/route/?start=New York, NY&finish=Los Angeles, CA
GET  /map/?start=New York, NY&finish=Los Angeles, CA        ← interactive Leaflet map
GET  /api/v1/health/
```

---

## Quick start

Requires **Python 3.12+** (Django 6.1 requirement).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py load_fuel_stations          # ~1 s, no network needed
python manage.py runserver
```

Then import `postman_collection.json` into Postman, or:

```bash
curl -s -X POST localhost:8000/api/v1/route/ \
  -H 'Content-Type: application/json' \
  -d '{"start": "New York, NY", "finish": "Los Angeles, CA"}' | jq .summary
```

Open the `map_url` from the response in a browser to see the route and stops.

Tests: `pytest` (107 tests, ~2 s, fully offline; the routing API is mocked).
Docker: `docker build -t fuel-route . && docker run -p 8000:8000 fuel-route`.

---

## How it works

```
 request ──► 1. Geocode start/finish  (offline gazetteer; 0 API calls)
             2. Route                 (OSRM, 1 API call, cached 24 h)
             3. Stations near route   (in-memory KD-tree, ~ms)
             4. Optimise fuel stops   (exact DP, ~5 ms)
             5. JSON + map_url
```

### 1. Geocoding without API calls
The price file has no coordinates, only city/state. Geocoding ~6,600 stations through a free API would take hours
and break the "few API calls" rule, so the project ships a **US city gazetteer** (29k places, from the open-source
[kelvins/US-Cities-Database](https://github.com/kelvins/US-Cities-Database)).

* `load_fuel_stations` gives every station its city's coordinates. 6,626 of 6,626 US stations resolve: 6 small
  towns missing from the gazetteer are handled by explicit overrides. The 620 Canadian rows in the file are dropped,
  because the trip must be in the USA. Duplicate OPIS IDs keep the lowest price.
* User input accepts `"City, ST"`, `"City, State"`, `"City ST"` or `"lat,lon"` and is resolved from the same
  gazetteer. Only text the gazetteer can't parse (e.g. a street address) falls back to **Nominatim** (US-only,
  cached). So the worst case is 2 geocoding calls plus 1 routing call, and the normal case is 1 routing call.

### 2. Routing: one call
[OSRM](https://project-osrm.org/) public server (free, no API key). A single `/route` request with
`overview=full` returns distance, duration and full geometry. The response is cached by coordinates
(Django cache), so repeat requests and the `/map/` page cost **zero** extra calls. `meta.routing_api_calls` in
every response proves it.

### 3. Finding stations along the route
All stations are loaded once per process into numpy arrays as unit-sphere vectors. For a trip, the route geometry
is densified to ≤0.5-mile steps and put in a `scipy` **KD-tree**. One vectorised query then returns, for every
station, its distance from the route and its **mile marker** (where along the trip you reach it). Stations within
`corridor_miles` (default 10) are candidates. Cost: a few ms, no DB queries.

To keep plans practical, candidates are thinned to the **cheapest station per 25 route-miles**. If that ever makes
a trip infeasible, the planner retries with all candidates.

### 4. Choosing the fuel stops
This is the classic *gas station problem*. Two optimisers are implemented in `planner/services/optimizer.py`:

* **Greedy (pure cheapest):** at each station, if a cheaper one is within one tank, buy just enough to reach it;
  otherwise fill up and go to the cheapest in range. This is provably optimal for dollars, but it happily stops
  to top up 0.1 gal to save a cent.
* **Exact DP with a stop penalty (used for the answer):** it minimises `fuel cost + stop_penalty × stops`
  (default **$5 per stop**). It uses the structural lemma from Khuller, Malekian & Mestre, *"To fill or not to
  fill"* (2007): at every stop an optimal plan either fills the tank or buys just enough to reach the next stop.
  So only O(n·w) fuel states need exploring.

For New York → Los Angeles the pure-cheapest plan is $676.22 with 10 stops. The default plan is **$680.11 with
6 stops**: four fewer stops for $3.89. The pure-cheapest figure is always returned as
`optimisation.absolute_cheapest_plan`, and `stop_penalty: 0` makes it the answer.

The test suite checks both optimisers against an **exhaustive search** on 40 random instances. It also checks that
every plan never runs dry or overfills, and that the penalty never adds stops.

### Assumptions (all overridable per request)

| Parameter | Default | Notes |
|---|---|---|
| `max_range_miles` | 500 | tank = range / mpg = 50 gal |
| `mpg` | 10 | |
| `start_fuel_gallons` | full tank | The vehicle leaves with a full tank and arrives empty, so **`total_fuel_cost` is what is spent on the road**. Pass `0`–`5` to start (nearly) empty and pay for the whole trip. |
| `corridor_miles` | 10 | max straight-line distance from the route |
| `stop_penalty` | 5 | USD-equivalent cost of one extra stop; `0` = absolute cheapest |
| `include_geometry` | false | add the GeoJSON LineString (the encoded polyline is always returned) |

`summary.total_gallons_used` (distance / mpg) is also returned, so the whole-trip cost under any accounting is
easy to derive.

---

## Response (abridged)

Numbers below come from the offline test route (straight segments between I-70/I-44/I-40 cities). Real OSRM
geometry gives slightly different miles and stops.

```json
{
  "start":  {"query": "New York, NY", "name": "New York, NY", "lat": 40.748, "lon": -73.997, "source": "gazetteer"},
  "finish": {"query": "Los Angeles, CA", "name": "Los Angeles, CA", "lat": 33.973, "lon": -118.248, "source": "gazetteer"},
  "route":  {"distance_miles": 2776.4, "duration_hours": 46.27, "polyline": "ktuwFlns...", "polyline_precision": 5},
  "vehicle": {"max_range_miles": 500, "miles_per_gallon": 10, "tank_gallons": 50, "start_fuel_gallons": 50},
  "fuel_stops": [
    {"stop_number": 1, "mile_marker": 448.3, "distance_from_route_miles": 6.7,
     "price_per_gallon": 3.059, "gallons": 35.25, "cost": 107.82, "fuel_on_arrival_gallons": 5.17,
     "station": {"opis_id": 72781, "name": "SHEETZ #774", "address": "I-70 & SR-83",
                 "city": "New Concord", "state": "OH", "lat": 40.0088, "lon": -81.7387, "location_precision": "city"}}
  ],
  "summary": {"total_fuel_cost": 680.11, "total_gallons_purchased": 227.64, "total_gallons_used": 277.64,
              "number_of_stops": 6, "average_price_paid": 2.988, "fuel_left_at_finish_gallons": 0, "currency": "USD"},
  "optimisation": {"objective": "minimise fuel cost + stop_penalty x stops", "stop_penalty_usd": 5,
                   "corridor_miles": 10, "absolute_cheapest_plan": {"total_fuel_cost": 676.22, "number_of_stops": 10}},
  "meta": {"routing_api_calls": 1, "geocoding_api_calls": 0, "route_from_cache": false, "stations_in_corridor": 339,
           "timing_ms": {"geocode": 0.0, "route": 1.7, "optimise": 40.5, "total": 42.4}},
  "map_url": "http://127.0.0.1:8000/map/?start=New+York%2C+NY&finish=Los+Angeles%2C+CA"
}
```

Errors use one shape, `{"error": {"code": "...", "message": "..."}}`:
`400` invalid input or location outside the USA, `422` no route or a gap with no fuel in range,
`502` routing provider unavailable, `503` stations not loaded.

---

## Project layout

```
config/                     settings (env-driven), urls
planner/
  models.py                 FuelStation
  management/commands/
    load_fuel_stations.py   CSV import + offline geocoding
  services/
    geocoding.py            lat,lon → gazetteer → Nominatim fallback
    routing.py              OSRM client (1 call, cached)
    station_index.py        in-memory KD-tree corridor search
    optimizer.py            greedy optimum + exact stop-penalty DP
    planner.py              orchestration, response assembly
    geo.py                  haversine, polyline codec, densify
  views.py / serializers.py DRF endpoints, map view
  templates/planner/map.html  Leaflet map
  tests/                    107 offline tests
data/                       fuel price CSV, US city gazetteer
postman_collection.json
```

## Production notes / trade-offs

* **Station precision is city-level.** The file only has addresses like *"I-44, EXIT 283"*. City centroids are
  usually within a few miles of the exit, and the 10-mile corridor absorbs that. Upgrade path: a one-off batch
  geocode of the addresses stored in the DB (the `geocode_precision` field already exists for this).
* **The OSRM demo server** is rate-limited and best-effort. Set `OSRM_BASE_URL` to a self-hosted OSRM (Docker,
  US extract) for production. No code changes are needed.
* **Cache** is in-process LocMem. Point `CACHES` at Redis to share routes across workers.
* The station index loads lazily on the first request (~250 ms) and is reset when prices are re-imported.
  `gunicorn --preload` plus a warm-up request keeps the first user from paying that cost.
