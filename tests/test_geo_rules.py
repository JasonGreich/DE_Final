"""Stage 3 unit tests: haversine and the business rules BR1 to BR4 (pure functions)."""
import pandas as pd
import pytest

from src.transform import geo, rules

# ---------------------------------------------------------------- geo


def test_haversine_one_degree_of_longitude_on_the_equator():
    # 1 degree of arc on a sphere of radius 6371 km = 6371 * pi / 180 = 111.195 km
    assert geo.haversine_km(0, 0, 0, 1) == pytest.approx(111.195, abs=0.01)


def test_haversine_sao_paulo_to_rio_is_about_358_km():
    # Known great-circle distance between the two city centres is roughly 358 km
    assert geo.haversine_km(-23.5505, -46.6333, -22.9068, -43.1729) == pytest.approx(358, abs=3)


def test_haversine_same_point_is_zero_and_distance_is_symmetric():
    assert geo.haversine_km(-23.5, -46.6, -23.5, -46.6) == 0
    a = geo.haversine_km(-23.5, -46.6, -3.1, -60.0)
    b = geo.haversine_km(-3.1, -60.0, -23.5, -46.6)
    assert a == pytest.approx(b)


def test_in_brazil_bounding_box():
    assert geo.in_brazil(-23.5, -46.6)          # Sao Paulo
    assert not geo.in_brazil(28.0, -15.5)       # a real bad row from the data (Atlantic, north)
    assert not geo.in_brazil(-23.5, -30.0)      # too far east
    assert not geo.in_brazil(None, -46.6)


def test_zip_centroids_gives_one_row_per_prefix_with_mean_coordinates():
    points = pd.DataFrame({"zip_prefix": ["02000", "01000", "01000"],
                           "lat": [-10.0, -20.0, -22.0], "lng": [-40.0, -50.0, -52.0]})
    out = geo.zip_centroids(points)
    assert list(out["zip_prefix"]) == ["01000", "02000"]
    row = out[out["zip_prefix"] == "01000"].iloc[0]
    assert (row["lat"], row["lng"], row["n_points"]) == (-21.0, -51.0, 2)


# ---------------------------------------------------------------- BR1 late

def test_late_uses_dates_not_timestamps():
    # The estimate is always 00:00:00. Delivered at 15:30 on the estimated day is NOT late.
    assert not rules.is_late("delivered", "2018-03-10 15:30:00", "2018-03-10 00:00:00")
    # A raw timestamp comparison would have said late:
    assert "2018-03-10 15:30:00" > "2018-03-10 00:00:00"


def test_late_one_day_after_the_estimate():
    assert rules.is_late("delivered", "2018-03-11 00:00:01", "2018-03-10 00:00:00")
    assert rules.days_late("delivered", "2018-03-13 09:00:00", "2018-03-10 00:00:00") == 3


def test_early_delivery_has_zero_days_late():
    assert rules.days_late("delivered", "2018-03-01 10:00:00", "2018-03-10 00:00:00") == 0


def test_only_delivered_orders_with_a_date_are_in_scope():
    assert rules.in_scope("delivered", "2018-03-01 10:00:00")
    assert not rules.in_scope("delivered", None)          # goes to quarantine
    assert not rules.in_scope("canceled", "2018-03-01 10:00:00")
    assert not rules.in_scope("shipped", None)
    assert not rules.is_late("canceled", "2018-04-01 10:00:00", "2018-03-10 00:00:00")


# ---------------------------------------------------------------- BR2 holidays

HOLIDAYS = {pd.Timestamp("2018-05-01").date()}  # a Tuesday


def test_holiday_window_includes_both_end_dates():
    assert rules.holiday_in_window("2018-05-01 08:00:00", "2018-05-10 00:00:00", HOLIDAYS)  # on purchase day
    assert rules.holiday_in_window("2018-04-20 08:00:00", "2018-05-01 00:00:00", HOLIDAYS)  # on estimated day
    assert rules.holiday_in_window("2018-04-27 23:59:59", "2018-05-03 00:00:00", HOLIDAYS)  # inside


def test_holiday_window_is_false_outside_the_window():
    assert not rules.holiday_in_window("2018-05-02 08:00:00", "2018-05-10 00:00:00", HOLIDAYS)
    assert not rules.holiday_in_window("2018-04-01 08:00:00", "2018-04-30 00:00:00", HOLIDAYS)
    assert not rules.holiday_in_window("2018-04-20 08:00:00", None, HOLIDAYS)


def test_carnival_is_a_separate_flag():
    carnival = {pd.Timestamp("2018-02-13").date()}
    purchase, estimate = "2018-02-10 10:00:00", "2018-02-20 00:00:00"
    assert not rules.holiday_in_window(purchase, estimate, HOLIDAYS)   # not a public holiday
    assert rules.holiday_in_window(purchase, estimate, carnival)       # but in the Carnival window


# ---------------------------------------------------------------- BR3 delay cause

def test_cause_is_seller_when_handoff_was_after_the_shipping_limit():
    assert rules.delay_cause(True, "2017-01-06 09:00:00", "2017-01-05 23:00:00") == "seller"
    assert rules.handoff_late_days("2017-01-06 09:00:00", "2017-01-05 23:00:00") == 1


def test_cause_is_carrier_when_handoff_was_on_time_even_on_the_same_day():
    assert rules.delay_cause(True, "2017-01-05 22:00:00", "2017-01-05 10:00:00") == "carrier"  # same date
    assert rules.delay_cause(True, "2017-01-03 09:00:00", "2017-01-05 10:00:00") == "carrier"  # early
    assert rules.handoff_late_days("2017-01-03 09:00:00", "2017-01-05 10:00:00") == 0          # never negative


def test_cause_is_unknown_without_a_carrier_date_and_none_for_on_time_orders():
    assert rules.delay_cause(True, None, "2017-01-05 10:00:00") == "unknown"
    assert rules.delay_cause(False, "2017-01-09 09:00:00", "2017-01-05 10:00:00") is None


def test_transit_days():
    assert rules.transit_days("2017-01-20 12:00:00", "2017-01-10 09:00:00") == 10
    assert rules.transit_days(None, "2017-01-10 09:00:00") is None


# ---------------------------------------------------------------- BR4 and the per-order rate

def test_freight_share():
    assert rules.freight_share(5.0, 20.0) == 0.25
    assert rules.freight_share(5.0, 0) is None      # no division by zero
    assert rules.freight_share(None, 20.0) is None


def test_late_rate_counts_a_three_item_order_once():
    # order A has 3 items and is late; order B has 1 item and is on time
    flags = [("A", True), ("A", True), ("A", True), ("B", False)]
    late, orders, rate = rules.order_late_rate(flags)
    assert (late, orders, rate) == (1, 2, 0.5)          # per order: 1 of 2
    per_item = sum(1 for _, f in flags if f) / len(flags)
    assert per_item == 0.75                              # the wrong, per-item answer
    assert rules.order_late_rate([])[2] is None


def test_every_state_maps_to_one_of_five_regions():
    assert len(rules.REGION_BY_STATE) == 27
    assert set(rules.REGION_BY_STATE.values()) == {"North", "Northeast", "Central-West", "Southeast", "South"}
    assert rules.region_of("SP") == "Southeast" and rules.region_of("XX") is None
