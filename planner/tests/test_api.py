import pytest
from rest_framework.test import APIClient

from planner.services.station_index import reset_station_index

URL = "/api/v1/route/"
pytestmark = pytest.mark.django_db


@pytest.fixture
def api():
    reset_station_index()
    return APIClient()


def test_cross_country_trip(api, fake_osrm):
    r = api.post(URL, {"start": "New York, NY", "finish": "Los Angeles, CA"}, format="json")
    assert r.status_code == 200, r.content
    body = r.json()

    assert body["meta"]["routing_api_calls"] == 1
    assert body["meta"]["geocoding_api_calls"] == 0
    assert fake_osrm.calls == 1
    assert body["route"]["distance_miles"] > 2500
    assert body["map_url"].startswith("http://testserver/map/?")

    stops = body["fuel_stops"]
    assert len(stops) >= 5  # (2776 - 500) / 500 -> at least 5 stops
    # Never more than one tank (500 mi) between fuel opportunities.
    markers = [0.0] + [s["mile_marker"] for s in stops] + [body["route"]["distance_miles"]]
    assert all(b - a <= 500.1 for a, b in zip(markers, markers[1:], strict=False))
    # Totals add up.
    assert body["summary"]["total_fuel_cost"] == pytest.approx(sum(s["cost"] for s in stops), abs=0.05)
    used = body["summary"]["total_gallons_used"]
    assert used == pytest.approx(body["route"]["distance_miles"] / 10, abs=0.05)
    assert body["summary"]["total_gallons_purchased"] == pytest.approx(used - 50, abs=0.1)
    assert body["optimisation"]["absolute_cheapest_plan"]["total_fuel_cost"] <= body["summary"]["total_fuel_cost"]
    assert all(s["distance_from_route_miles"] <= 10 for s in stops)
    assert "_corridor" not in body


def test_repeat_request_is_served_without_calling_routing_api(api, fake_osrm):
    api.get(URL, {"start": "New York, NY", "finish": "Los Angeles, CA"})
    r = api.get(URL, {"start": "New York, NY", "finish": "Los Angeles, CA"})
    assert r.json()["meta"]["route_from_cache"] is True
    assert r.json()["meta"]["routing_api_calls"] == 0
    assert fake_osrm.calls == 1


def test_short_trip_with_full_tank_has_no_stops(api, fake_osrm, chicago_detroit):
    fake_osrm.set(chicago_detroit)
    body = api.get(URL, {"start": "Chicago, IL", "finish": "Detroit, MI"}).json()
    assert body["fuel_stops"] == [] and body["summary"]["total_fuel_cost"] == 0


def test_start_empty_forces_an_early_stop(api, fake_osrm, chicago_detroit):
    fake_osrm.set(chicago_detroit)
    body = api.get(URL, {"start": "Chicago, IL", "finish": "Detroit, MI", "start_fuel_gallons": 1}).json()
    assert body["fuel_stops"][0]["mile_marker"] <= 10
    summary = body["summary"]
    assert summary["total_gallons_purchased"] == pytest.approx(summary["total_gallons_used"] - 1, abs=0.1)


def test_zero_stop_penalty_returns_absolute_cheapest(api, fake_osrm):
    payload = {"start": "New York, NY", "finish": "Los Angeles, CA", "stop_penalty": 0}
    body = api.post(URL, payload, format="json").json()
    assert body["summary"]["total_fuel_cost"] == body["optimisation"]["absolute_cheapest_plan"]["total_fuel_cost"]


def test_geometry_is_optional(api, fake_osrm):
    body = api.get(URL, {"start": "New York, NY", "finish": "Los Angeles, CA", "include_geometry": "true"}).json()
    assert body["route"]["geometry"]["type"] == "LineString"
    assert len(body["route"]["geometry"]["coordinates"]) > 100


@pytest.mark.parametrize("payload,code", [
    ({}, 400),
    ({"start": "Chicago, IL"}, 400),
    ({"start": "Chicago, IL", "finish": "chicago, il"}, 400),
    ({"start": "Chicago, IL", "finish": "51.5072,-0.1276"}, 400),
    ({"start": "Chicago, IL", "finish": "Detroit, MI", "mpg": 0}, 400),
])
def test_validation_errors(api, fake_osrm, payload, code):
    assert api.post(URL, payload, format="json").status_code == code


def test_no_route(api, fake_osrm):
    fake_osrm.set({"code": "NoRoute", "message": "Impossible route"})
    r = api.get(URL, {"start": "Honolulu, HI", "finish": "Chicago, IL"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_route"


def test_routing_provider_down(api, fake_osrm):
    fake_osrm.set({"code": "InvalidUrl", "message": "boom"}, status=500)
    r = api.get(URL, {"start": "Dallas, TX", "finish": "Chicago, IL"})
    assert r.status_code == 502 and r.json()["error"]["code"] == "routing_provider_error"


def test_map_page_renders_from_cached_route(api, fake_osrm):
    api.get(URL, {"start": "New York, NY", "finish": "Los Angeles, CA"})
    r = api.get("/map/", {"start": "New York, NY", "finish": "Los Angeles, CA"})
    assert r.status_code == 200
    html = r.content.decode()
    assert "leaflet" in html and "total spent on fuel" in html
    assert fake_osrm.calls == 1  # map reused the cached route


def test_health(api):
    r = api.get("/api/v1/health/")
    assert r.status_code == 200 and r.json()["stations_loaded"] > 6000


@pytest.mark.parametrize("path", ["/api/v1/route", "/api/v1/route/"])
def test_trailing_slash_is_optional_for_post(api, fake_osrm, path):
    r = api.post(path, {"start": "New York, NY", "finish": "Los Angeles, CA"}, format="json")
    assert r.status_code == 200
