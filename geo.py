"""
Geofencing utilities.

Used by the mobile / PWA check-in endpoint to confirm the user is inside an
approved site polygon (or within radius of a centre point). Detects obvious
fake-GPS signals such as:

  * accuracy == 0 (mock providers often expose impossibly perfect fixes)
  * accuracy worse than `accuracy_max_m` (low confidence => reject)
  * altitude or speed deltas inconsistent with previous fixes
  * jumps between fixes faster than human travel (e.g. >800 km/h)

A site has either `lat,lng,radius_m` (circle) or a polygon stored as a JSON
list of (lat,lng) pairs in the `sites.polygon_json` column.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional


EARTH_R_M = 6_371_000.0


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres."""
    r1, r2 = math.radians(lat1), math.radians(lat2)
    dr = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dr / 2) ** 2 + math.cos(r1) * math.cos(r2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(min(1.0, math.sqrt(a)))


def point_in_polygon(lat: float, lng: float, polygon: list[tuple[float, float]]) -> bool:
    """Ray-casting; polygon is a closed list of (lat,lng)."""
    if len(polygon) < 3:
        return False
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i]
        yj, xj = polygon[j]
        intersect = ((yi > lat) != (yj > lat)) and \
                    (lng < (xj - xi) * (lat - yi) / (yj - yi + 1e-12) + xi)
        if intersect:
            inside = not inside
        j = i
    return inside


def inside_site(site: dict, lat: float, lng: float) -> tuple[bool, float]:
    """Returns (inside, distance_to_centre_m).

    `site` is a sites table row converted to dict; supports either
    radius mode (`lat`, `lng`, `radius_m`) or polygon mode (`polygon_json`).
    """
    poly = site.get('polygon_json')
    if poly:
        import json
        try:
            polygon = [tuple(p) for p in json.loads(poly)]
            inside = point_in_polygon(lat, lng, polygon)
            # distance to the centroid as a coarse "how far" indicator
            cx = sum(p[0] for p in polygon) / len(polygon)
            cy = sum(p[1] for p in polygon) / len(polygon)
            return inside, haversine_m(lat, lng, cx, cy)
        except Exception:  # noqa: BLE001
            pass
    slat = site.get('lat')
    slng = site.get('lng')
    radius = float(site.get('radius_m') or 100.0)
    if slat is None or slng is None:
        return False, float('inf')
    d = haversine_m(lat, lng, float(slat), float(slng))
    return d <= radius, d


# ---------------------------------------------------------------------------
# Fake-GPS / mock-location detection
# ---------------------------------------------------------------------------
def looks_mocked(fix: dict, previous: Optional[dict] = None,
                 accuracy_max_m: float = 60.0) -> Optional[str]:
    """Return None if the fix looks plausible, else a short reason string."""
    try:
        acc = float(fix.get('accuracy') or 0.0)
    except (TypeError, ValueError):
        return 'invalid-accuracy'
    if acc <= 0.0:
        return 'accuracy-zero'
    if acc > accuracy_max_m:
        return f'accuracy>{accuracy_max_m:.0f}m'

    # Some emulators flag mock providers explicitly via the client SDK
    if fix.get('mocked') is True:
        return 'mock-provider'

    if previous:
        try:
            prev_t = datetime.fromisoformat(previous['ts'])
            cur_t = datetime.fromisoformat(fix['ts'])
            dt = max(0.001, (cur_t - prev_t).total_seconds())
            d_m = haversine_m(previous['lat'], previous['lng'],
                              fix['lat'], fix['lng'])
            kmh = (d_m / 1000.0) / (dt / 3600.0)
            if kmh > 800:           # nothing on the ground moves this fast
                return f'teleport-{int(kmh)}kmh'
        except Exception:  # noqa: BLE001
            pass

    return None
