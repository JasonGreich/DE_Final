"""Geography helpers: haversine distance, the Brazil bounding box, zip-prefix centroids.

Business rule BR4: distance_km is the haversine (great-circle) distance between the
seller and the customer zip-prefix centroids. Geolocation has many rows per zip
prefix, so we first reduce it to ONE row per prefix (mean lat/lng). That way a join
on zip prefix can never multiply rows.
"""
from math import asin, cos, radians, sin, sqrt

import pandas as pd

EARTH_RADIUS_KM = 6371.0  # mean radius of the Earth

# Brazil bounding box from CLAUDE.md (BR4). A point outside it is bad data.
LAT_MIN, LAT_MAX = -34.0, 5.3
LNG_MIN, LNG_MAX = -74.0, -34.0


def haversine_km(lat1, lng1, lat2, lng2):
    """Great-circle distance in km between two points given in degrees.

    haversine formula: a = sin^2(dlat/2) + cos(lat1) * cos(lat2) * sin^2(dlng/2)
                       distance = 2 * R * asin(sqrt(a))
    """
    lat1, lng1, lat2, lng2 = radians(lat1), radians(lng1), radians(lat2), radians(lng2)
    d_lat = lat2 - lat1
    d_lng = lng2 - lng1
    a = sin(d_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(d_lng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


def in_brazil(lat, lng):
    """True when the coordinates are inside Brazil's bounding box."""
    if lat is None or lng is None:
        return False
    return LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX


def zip_centroids(points):
    """One row per zip prefix: mean lat, mean lng and how many points were averaged.

    points: DataFrame with columns zip_prefix, lat, lng (already inside Brazil).
    Sorted by zip_prefix so the output is the same every run.
    """
    grouped = points.groupby("zip_prefix", as_index=False).agg(
        lat=("lat", "mean"), lng=("lng", "mean"), n_points=("lat", "size")
    )
    return grouped.sort_values("zip_prefix").reset_index(drop=True)
