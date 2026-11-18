"""Business rules BR1 to BR4 as small pure functions (no database, no pandas).

Every function takes plain values (strings, numbers, None) and returns plain values,
so each rule can be read and tested on its own. Timestamps are strings like
'2018-03-02 14:30:00'; only the first 10 characters (the date) are used, because
the estimated delivery date is always at 00:00:00 and comparing full timestamps
would wrongly mark same-day deliveries as late.

See docs/business_rules.md for each rule with an example.
"""
from datetime import date

# The 27 Brazilian states mapped to the 5 IBGE regions.
REGION_BY_STATE = {
    "AC": "North", "AP": "North", "AM": "North", "PA": "North", "RO": "North",
    "RR": "North", "TO": "North",
    "AL": "Northeast", "BA": "Northeast", "CE": "Northeast", "MA": "Northeast",
    "PB": "Northeast", "PE": "Northeast", "PI": "Northeast", "RN": "Northeast",
    "SE": "Northeast",
    "DF": "Central-West", "GO": "Central-West", "MT": "Central-West", "MS": "Central-West",
    "ES": "Southeast", "MG": "Southeast", "RJ": "Southeast", "SP": "Southeast",
    "PR": "South", "RS": "South", "SC": "South",
}


def region_of(state):
    """IBGE region for a state code, or None if the state is unknown."""
    return REGION_BY_STATE.get(state)


def is_missing(value):
    """True for None and for float NaN (what pandas uses for a missing number)."""
    return value is None or (isinstance(value, float) and value != value)


def to_date(timestamp):
    """Date part of a 'YYYY-MM-DD ...' string as a date object, or None if missing."""
    if is_missing(timestamp):
        return None
    return date.fromisoformat(str(timestamp)[:10])


# ---------------------------------------------------------------- BR1: late

def in_scope(status, delivered_ts):
    """BR1: only delivered orders that have a delivery date count towards the late rate."""
    return status == "delivered" and not is_missing(delivered_ts)


def days_late(status, delivered_ts, estimated_ts):
    """BR1: whole days between estimate and delivery, never negative. 0 when out of scope."""
    if not in_scope(status, delivered_ts) or is_missing(estimated_ts):
        return 0
    return max(0, (to_date(delivered_ts) - to_date(estimated_ts)).days)


def is_late(status, delivered_ts, estimated_ts):
    """BR1: late means the delivery DATE is after the estimated DATE (date-only comparison)."""
    return days_late(status, delivered_ts, estimated_ts) > 0


# ---------------------------------------------------------------- BR2: holidays

def holiday_in_window(purchase_ts, estimated_ts, holiday_dates):
    """BR2: True when a holiday falls from the purchase date to the estimated date, inclusive.

    holiday_dates is a collection of date objects. Call it once with the national
    public holidays and once with the Carnival dates (Carnival has its own flag).
    """
    start, end = to_date(purchase_ts), to_date(estimated_ts)
    if start is None or end is None:
        return False
    return any(start <= holiday <= end for holiday in holiday_dates)


# ---------------------------------------------------------------- BR3: delay cause

def handoff_late_days(carrier_ts, shipping_limit_ts):
    """BR3: days the seller handed the parcel to the carrier after the shipping limit date (>= 0)."""
    if is_missing(carrier_ts) or is_missing(shipping_limit_ts):
        return None
    return max(0, (to_date(carrier_ts) - to_date(shipping_limit_ts)).days)


def transit_days(delivered_ts, carrier_ts):
    """BR3: days from carrier handoff to delivery to the customer."""
    if is_missing(delivered_ts) or is_missing(carrier_ts):
        return None
    return (to_date(delivered_ts) - to_date(carrier_ts)).days


def delay_cause(order_is_late, carrier_ts, shipping_limit_ts):
    """BR3: who caused a late order. Only late orders get a cause (others get None).

    seller  = the handoff to the carrier was after the shipping limit date
    carrier = the handoff was on time, so the delay happened in transit
    unknown = no carrier date (we warn, we do not drop the row)
    Simplification: we have no promised carrier transit time to compare against.
    """
    if not order_is_late:
        return None
    late_days = handoff_late_days(carrier_ts, shipping_limit_ts)
    if late_days is None:
        return "unknown"
    return "seller" if late_days > 0 else "carrier"


# ---------------------------------------------------------------- BR4: freight and rates

def freight_share(freight_value, price):
    """BR4: freight as a share of the item price. None when the price is missing or zero."""
    if is_missing(freight_value) or is_missing(price) or price == 0:
        return None
    return freight_value / price


def order_late_rate(order_flags):
    """The late rate PER ORDER. An order with three items counts once.

    order_flags: pairs (order_id, is_late), one per ORDER ITEM (so an order can appear
    several times). Returns (late_orders, orders, rate); rate is None for no orders.
    """
    late_by_order = {}
    for order_id, late in order_flags:
        # an order is late if any of its rows says so (they all agree in practice)
        late_by_order[order_id] = late_by_order.get(order_id, False) or bool(late)
    orders = len(late_by_order)
    late_orders = sum(late_by_order.values())
    return late_orders, orders, (late_orders / orders if orders else None)
