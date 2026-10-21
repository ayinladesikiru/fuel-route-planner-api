import pytest

from planner.exceptions import GeocodingError, OutsideUSAError
from planner.services.geocoding import Geocoder
from planner.tests.fixtures import FakeSession


@pytest.mark.parametrize(
    "text,expected_name",
    [
        ("Chicago, IL", "Chicago, IL"),
        ("chicago, illinois", "Chicago, IL"),
        ("Salt Lake City UT", "Salt Lake City, UT"),
        ("St. Louis, MO, USA", "Saint Louis, MO"),
        ("Fort Worth, TX", "Fort Worth, TX"),
    ],
)
def test_offline_gazetteer_needs_no_api_call(text, expected_name):
    g = Geocoder(session=FakeSession([]))
    place = g.geocode(text)
    assert place.name == expected_name
    assert place.source == "gazetteer"
    assert g.api_calls == 0


def test_coordinates_are_accepted_verbatim():
    place = Geocoder().geocode(" 41.8781, -87.6298 ")
    assert (place.lat, place.lon, place.source) == (41.8781, -87.6298, "coordinates")


def test_coordinates_outside_usa_rejected():
    with pytest.raises(OutsideUSAError):
        Geocoder().geocode("51.5072,-0.1276")


def test_unknown_text_falls_back_to_nominatim_once_and_is_cached():
    session = FakeSession([{"lat": "36.1069", "lon": "-112.1129", "display_name": "Grand Canyon Village, AZ"}])
    g = Geocoder(session=session)
    a = g.geocode("Grand Canyon Village visitor center")
    b = Geocoder(session=session).geocode("Grand Canyon Village visitor center")
    assert a.source == b.source == "nominatim"
    assert len(session.calls) == 1
    assert session.calls[0][1]["params"]["countrycodes"] == "us"


def test_nominatim_no_result():
    with pytest.raises(GeocodingError):
        Geocoder(session=FakeSession([])).geocode("zzzz not a place")
