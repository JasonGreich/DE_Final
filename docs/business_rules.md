# Business rules

Implemented as small pure functions in `src/transform/rules.py` and `src/transform/geo.py`, applied in
`src/transform/staging.py`, covered by `tests/test_geo_rules.py`. Numbers below are from the real data (full run).

## BR1 Late
- Only orders with status `delivered` **and** a delivery date are in scope (`in_scope = 1`): 96,470 orders.
- Late means `date(delivered) > date(estimated)`. `days_late = max(0, date difference)`.
- Example: estimated `2018-03-10 00:00:00`, delivered `2018-03-10 15:30:00` is **not late** (same date). Comparing raw timestamps would have
  called it late; on the real data that mistake would add 1,292 false lates (7,826 instead of 6,534).
- Out of scope but kept in staging with `in_scope = 0`: 2,963 orders (canceled 625, unavailable 609, shipped 1,107, invoiced 314, processing 301, created 5, approved 2).
  For these rows `is_late` and `days_late` are 0, so **always filter `in_scope = 1` when computing a rate**.
- Delivered with no delivery date (8 orders) go to `quarantine_orders` (reason `delivered_without_delivery_date`), with their items and reviews.
- Result: 6,534 late of 96,470 = **6.77% per order**.

## BR2 Holiday window
- `holiday_in_window` is 1 when a **national public** holiday falls between the purchase date and the estimated delivery date, both inclusive.
  National = Nager `global = true`; the 9 July Sao Paulo holiday (3 rows, one per year) is quarantined with reason `not_national_global_false`.
- Carnival (types Bank, Optional) is **not** in `holiday_in_window`; it has its own `carnival_in_window`.
- Example: purchase 2018-04-27, estimated 2018-05-03 contains 1 May: flag = 1. A purchase on 2018-05-01 itself also counts.
- Result on all 99,433 staged orders: 41,916 have a public holiday in the window, 8,815 have Carnival in the window.

## BR3 Delay cause (late orders only)
- Per order item: `handoff_late_days = max(0, date(carrier handoff) - date(shipping_limit_date))`, `transit_days = date(delivered) - date(carrier handoff)`.
- Cause: `seller` if `handoff_late_days > 0`, else `carrier`; `unknown` if the carrier date is missing (kept, not quarantined).
  Not late: cause is empty.
- Example: shipping limit 2017-04-25, handed to the carrier 2017-04-28: seller, 3 days late at handoff.
- Result (late orders): carrier 5,073, seller 1,460, unknown 1. No order has items with different causes (5,073 + 1,460 + 1 = 6,534).
- **Simplification to state in the report:** we have no promised carrier transit time, so "carrier" means "the seller was on time, so the delay happened in transit".
- Data caveats: 44 items have negative `transit_days` (carrier date after delivery date); 4 items have shipping limit dates in 2019 or 2020.
  Both are kept as received and flagged here, not corrected.

## BR4 Distance and freight
- `distance_km` = haversine distance (earth radius 6,371 km) between the seller and customer zip-prefix centroids.
- Geolocation is first reduced to **one row per zip prefix** (mean lat/lng; `n_points` records how many rows were averaged), so joins cannot multiply rows.
  Rows outside Brazil's box (lat -34 to 5.3, lng -74 to -34) are quarantined first: 42 rows.
- `freight_share = freight_value / price` (empty if the price is 0 or missing).
- Items with no distance: 555 of 112,642, because 279 customer zip prefixes have no geolocation row (sellers: 7 prefixes).

## Reviews
One review per order: the latest by `review_answer_timestamp`, tie-break `review_creation_date`, then `review_id`.
551 older reviews are quarantined (reason `superseded_by_later_review_for_same_order`).

## Per-order rate (never per item)
`late rate = count(distinct order_id where is_late) / count(distinct order_id in scope)`. An order with three items counts once.
Counted per item the same data gives 7,264 / 110,189 = 6.59%, so the per-item number is wrong, not just different.
When slicing by category or seller, an order counts once in each category or seller it contains.

## Categories
English category comes from the translation table. Two categories (`pc_gamer`, `portateis_cozinha_e_preparadores_de_alimentos`, 13 products) have no
translation: they keep their Portuguese name and `category_en_is_fallback = 1`. 610 products have no category (left empty).

## Money
`price_usd = price * rate` and `freight_usd = freight_value * rate`, with the ECB BRL/USD rate of the **purchase date**.
ECB data has no weekend or holiday rows, so `dim_date.brl_usd_rate` is forward-filled from the last available business day.
Example: an order bought on Saturday 2017-09-02 uses the rate of Friday 2017-09-01. Rates in the warehouse range from 0.238 to 0.329 USD per BRL.
Revenue on late orders = sum of item prices (USD) of late orders: US$ 297,482 of US$ 3.94 million in total. It is revenue on late orders, not revenue lost.

## Results on the real data (per order)
- Late: 6,534 of 96,470 = 6.77%. Northeast 12.7%, North 8.6%, Central-West 6.5%, Southeast 6.1%, South 5.9%.
- Distance: under 100 km 4.4%, 100-500 km 6.1%, 500-1000 km 7.1%, 1000-2000 km 9.5%, 2000 km+ 12.0%. Freight share matters little (6.4% to 7.5%).
- Holiday window: 6.6% with a national holiday in the window vs 6.9% without. Holidays do **not** make delivery later here (carnival: 6.5% vs 6.8%).
- Cause: 22.3% seller, 77.6% carrier, 1 unknown.
- Review score: 2.27 on late orders vs 4.29 on time.
All numbers (with counts and minimum volumes) are in `docs/report_numbers.json`.
