import pytest

from route_eta import compute_eta, load_route, total_distance_km
from route_eta.eta import segment_minutes


def test_short_route():
    assert compute_eta(load_route("rotterdam_to_den_haag.json")) == 24


def test_route_eta():
    route = load_route("amsterdam_to_utrecht.json")
    assert compute_eta(route) == 38


def test_total_distance():
    assert total_distance_km(load_route("amsterdam_to_utrecht.json")) == 47.05


def test_unknown_road_type():
    with pytest.raises(ValueError):
        segment_minutes({"road": "ferry", "km": 3})


def test_motorway_is_fastest():
    assert segment_minutes({"road": "motorway", "km": 10}) < segment_minutes({"road": "urban", "km": 10})


def test_longer_route_takes_longer():
    a = load_route("rotterdam_to_den_haag.json")
    b = load_route("amsterdam_to_utrecht.json")
    assert compute_eta(b) > compute_eta(a)
