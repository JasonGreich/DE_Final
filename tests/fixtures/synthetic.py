"""A small invented Olist-like dataset for tests and CI (no real Olist data is committed).

make_fixture(data_dir) writes the nine CSV files into data_dir/seed and builds data_dir/store/olist.db.
write_api_files(raw_dir) writes holiday and exchange-rate files like the ones the APIs return.
Everything is generated with a fixed seed, so every call gives the same data.
"""
import csv
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from src.extract import seed_store

STATES = [("SP", "01000"), ("RJ", "20000"), ("MG", "30000"), ("BA", "40000"), ("RS", "90000"), ("AM", "69000")]
CATEGORIES = [("bebidas", "drinks"), ("telefonia", "telephony"), ("moveis", "furniture")]
N_ORDERS = 60


def pick_status(i):
    """Mostly delivered, with a few other statuses so the in-scope rule is exercised."""
    if i % 20 == 0:
        return "canceled"
    if i % 25 == 0:
        return "shipped"
    return "delivered"


def fmt(moment):
    return None if moment is None else moment.strftime("%Y-%m-%d %H:%M:%S")


def build_rows():
    """All nine tables as {file name: (header, rows)}."""
    rnd = random.Random(7)
    customers = [[f"c{i:03d}", f"u{i:03d}", STATES[i % 6][1], "city", STATES[i % 6][0]] for i in range(N_ORDERS)]
    sellers = [[f"s{i}", STATES[i][1], "city", STATES[i][0]] for i in range(6)]
    products = [[f"p{i}", CATEGORIES[i % 3][0], 20, 100, 1, 500, 20, 10, 15] for i in range(9)]
    products.append(["p9", None, None, None, None, 300, 10, 10, 10])      # a product with no category
    translation = [[pt, en] for pt, en in CATEGORIES]
    geolocation = []
    for state, zip_prefix in STATES:
        for k in range(3):
            geolocation.append([zip_prefix, -10.0 - rnd.random() * 10, -45.0 - rnd.random() * 10, "city", state])
    geolocation.append(["99999", 40.0, 10.0, "nowhere", "XX"])             # outside Brazil: quarantined

    orders, items, reviews = [], [], []
    for i in range(N_ORDERS):
        status = pick_status(i)
        purchase = datetime(2017, 1, 1, 10) + timedelta(days=i * 5, hours=i)
        estimated = datetime.combine((purchase + timedelta(days=15)).date(), datetime.min.time())
        carrier = purchase + timedelta(days=rnd.randint(1, 6))
        # every 4th delivered order arrives after the estimate; one is delivered on the estimate day itself
        delivered = estimated + timedelta(days=rnd.randint(1, 8)) if i % 4 == 0 else estimated - timedelta(days=2)
        if i == 3:
            delivered = estimated + timedelta(hours=15, minutes=30)         # same date, later time: NOT late
        if status != "delivered":
            delivered = None
        if i == 11:
            delivered = None                                                 # delivered but no date: quarantined
        orders.append([f"o{i:03d}", f"c{i:03d}", status, fmt(purchase), fmt(purchase + timedelta(hours=1)),
                       fmt(carrier), fmt(delivered), fmt(estimated)])
        for n in range(1 + i % 3):                                           # 1 to 3 items per order
            limit = purchase + timedelta(days=rnd.randint(2, 5))
            items.append([f"o{i:03d}", n + 1, f"p{(i + n) % 10}", f"s{(i + n) % 6}", fmt(limit),
                          round(20 + rnd.random() * 100, 2), round(5 + rnd.random() * 20, 2)])
        score = 1 + (i % 5)
        reviews.append([f"r{i:03d}", f"o{i:03d}", score, None, None, fmt(purchase + timedelta(days=20))[:10],
                        fmt(purchase + timedelta(days=21))])
        if i % 10 == 0:                                                      # a second, later review: older one quarantined
            reviews.append([f"r{i:03d}b", f"o{i:03d}", 5, None, None, fmt(purchase + timedelta(days=22))[:10],
                            fmt(purchase + timedelta(days=23))])
    payments = [[f"o{i:03d}", 1, "credit_card", 1, 50.0] for i in range(N_ORDERS)]
    return {
        "customers": customers, "sellers": sellers, "products": products,
        "product_category_name_translation": translation, "geolocation": geolocation,
        "orders": orders, "order_items": items, "order_reviews": reviews, "order_payments": payments,
    }


def make_fixture(data_dir):
    """Write seed CSVs and build the store database. Returns the store path."""
    data_dir = Path(data_dir)
    seed_dir = data_dir / "seed"
    seed_dir.mkdir(parents=True, exist_ok=True)
    for table, rows in build_rows().items():
        file_name, columns, _ = seed_store.TABLES[table]
        with open(seed_dir / file_name, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([name for name, _ in columns])
            writer.writerows(["" if v is None else v for v in row] for row in rows)
    store = data_dir / "store" / "olist.db"
    seed_store.build_database(seed_dir, store)
    return store


def write_api_files(raw_dir):
    """Holiday files (2016-2018) and one exchange-rate file, in the shape the real APIs return."""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    for year in (2016, 2017, 2018):
        holidays = [
            {"date": f"{year}-01-01", "localName": "Ano Novo", "name": "New Year's Day", "countryCode": "BR",
             "fixed": True, "global": True, "counties": None, "launchYear": None, "types": ["Public"]},
            {"date": f"{year}-05-01", "localName": "Dia do Trabalho", "name": "Labour Day", "countryCode": "BR",
             "fixed": True, "global": True, "counties": None, "launchYear": None, "types": ["Public"]},
            {"date": f"{year}-07-09", "localName": "Revolucao", "name": "Constitutionalist Revolution",
             "countryCode": "BR", "fixed": True, "global": False, "counties": ["BR-SP"], "launchYear": None,
             "types": ["Public"]},
            {"date": f"{year}-09-07", "localName": "Independencia", "name": "Independence Day", "countryCode": "BR",
             "fixed": True, "global": True, "counties": None, "launchYear": None, "types": ["Public"]},
        ]
        (raw_dir / f"nager_holidays_{year}_20260101T000000000000Z.json").write_text(json.dumps(holidays))
    rates, day = [], date(2015, 12, 31)
    while day <= date(2018, 12, 31):
        if day.weekday() < 5:                                                # business days only, like the ECB
            rates.append({"date": day.isoformat(), "base": "BRL", "quote": "USD", "rate": 0.30})
        day += timedelta(days=1)
    (raw_dir / "frankfurter_brl_usd_20260101T000000000000Z.json").write_text(json.dumps(rates))
