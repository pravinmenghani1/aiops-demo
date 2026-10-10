from route_eta import load_route


def test_fixture_ids():
    assert load_route("amsterdam_to_utrecht.json")["id"] == "AMS-UTR-01"


def test_every_segment_has_distance():
    for name in ("amsterdam_to_utrecht.json", "rotterdam_to_den_haag.json"):
        assert all(s["km"] > 0 for s in load_route(name)["segments"])
