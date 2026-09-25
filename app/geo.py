import math


def haversine_m(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dphi = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)
    x = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(x)))


def sort_packages(packages, lat: float, lon: float):
    scored = []
    for p in packages:
        m = haversine_m(lat, lon, p.lat, p.lon)
        p.sort_m = m
        scored.append((m, p))
    scored.sort(key=lambda x: x[0])
    return [p for _, p in scored]
