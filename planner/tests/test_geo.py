import numpy as np
import pytest

from planner.services.geo import (
    cumulative_miles,
    decode_polyline,
    densify,
    encode_polyline,
    haversine_miles,
    is_in_usa,
)


def test_polyline_round_trip():
    pts = np.array([[38.5, -120.2], [40.7, -120.95], [43.252, -126.453]])
    enc = encode_polyline(pts)
    assert enc == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"  # Google's reference example
    assert np.allclose(decode_polyline(enc), pts)


def test_haversine_known_distance():
    # New York -> Los Angeles is ~2,445 miles great-circle.
    assert haversine_miles(40.7128, -74.0060, 34.0522, -118.2437) == pytest.approx(2445, abs=10)


def test_densify_preserves_length_and_limits_step():
    pts = np.array([[40.0, -100.0], [40.0, -95.0]])
    dense = densify(pts, 0.5)
    steps = np.diff(cumulative_miles(dense))
    assert steps.max() <= 0.5 + 1e-9
    assert cumulative_miles(dense)[-1] == pytest.approx(cumulative_miles(pts)[-1], rel=1e-3)


@pytest.mark.parametrize("lat,lon,expected", [
    (41.88, -87.63, True), (61.2, -149.9, True), (21.3, -157.8, True),
    (19.43, -99.13, False), (51.5, -0.12, False),
])
def test_is_in_usa(lat, lon, expected):
    assert is_in_usa(lat, lon) is expected
