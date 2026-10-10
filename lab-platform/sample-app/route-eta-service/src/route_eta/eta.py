"""Estimated time of arrival for a route made of road segments."""
import math

AVG_SPEED_KMH = {"motorway": 100, "rural": 70, "urban": 30}


def segment_minutes(segment: dict) -> float:
    road = segment["road"]
    if road not in AVG_SPEED_KMH:
        raise ValueError(f"unknown road type: {road}")
    return segment["km"] / AVG_SPEED_KMH[road] * 60


def total_distance_km(route: dict) -> float:
    return round(sum(s["km"] for s in route["segments"]), 2)


def compute_eta(route: dict) -> int:
    """Whole minutes to arrival. Always round up: arriving early is fine, arriving late is not."""
    minutes = sum(segment_minutes(s) for s in route["segments"])
    return math.ceil(minutes)
