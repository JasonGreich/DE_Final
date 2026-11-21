# Data profiling

Generated 2026-11-18 13:53 by `python -m src.profile`. Numbers come straight from `data/store/olist.db` and the two APIs.

## Source: Olist store database (SQLite)

### Table sizes

| table | rows |
|---|---|
| customers | 99,441 |
| sellers | 3,095 |
| products | 32,951 |
| product_category_name_translation | 71 |
| geolocation | 1,000,163 |
| orders | 99,441 |
| order_items | 112,650 |
| order_reviews | 99,224 |
| order_payments | 103,886 |

## Per-table profile

### customers
Rows: **99,441**

| column | type | nulls | null % |
|---|---|---|---|
| customer_id | TEXT | 0 | 0.00% |
| customer_unique_id | TEXT | 0 | 0.00% |
| customer_zip_code_prefix | TEXT | 0 | 0.00% |
| customer_city | TEXT | 0 | 0.00% |
| customer_state | TEXT | 0 | 0.00% |

Duplicate key (customer_id): **0** surplus rows

### sellers
Rows: **3,095**

| column | type | nulls | null % |
|---|---|---|---|
| seller_id | TEXT | 0 | 0.00% |
| seller_zip_code_prefix | TEXT | 0 | 0.00% |
| seller_city | TEXT | 0 | 0.00% |
| seller_state | TEXT | 0 | 0.00% |

Duplicate key (seller_id): **0** surplus rows

### products
Rows: **32,951**

| column | type | nulls | null % |
|---|---|---|---|
| product_id | TEXT | 0 | 0.00% |
| product_category_name | TEXT | 610 | 1.85% |
| product_name_lenght | INTEGER | 610 | 1.85% |
| product_description_lenght | INTEGER | 610 | 1.85% |
| product_photos_qty | INTEGER | 610 | 1.85% |
| product_weight_g | INTEGER | 2 | 0.01% |
| product_length_cm | INTEGER | 2 | 0.01% |
| product_height_cm | INTEGER | 2 | 0.01% |
| product_width_cm | INTEGER | 2 | 0.01% |

Duplicate key (product_id): **0** surplus rows

### product_category_name_translation
Rows: **71**

| column | type | nulls | null % |
|---|---|---|---|
| product_category_name | TEXT | 0 | 0.00% |
| product_category_name_english | TEXT | 0 | 0.00% |

Duplicate key (product_category_name): **0** surplus rows

### geolocation
Rows: **1,000,163**

| column | type | nulls | null % |
|---|---|---|---|
| geolocation_zip_code_prefix | TEXT | 0 | 0.00% |
| geolocation_lat | REAL | 0 | 0.00% |
| geolocation_lng | REAL | 0 | 0.00% |
| geolocation_city | TEXT | 0 | 0.00% |
| geolocation_state | TEXT | 0 | 0.00% |

Duplicate key (geolocation_zip_code_prefix, geolocation_lat, geolocation_lng, geolocation_city, geolocation_state): **261,831** surplus rows

### orders
Rows: **99,441**

| column | type | nulls | null % |
|---|---|---|---|
| order_id | TEXT | 0 | 0.00% |
| customer_id | TEXT | 0 | 0.00% |
| order_status | TEXT | 0 | 0.00% |
| order_purchase_timestamp | TEXT | 0 | 0.00% |
| order_approved_at | TEXT | 160 | 0.16% |
| order_delivered_carrier_date | TEXT | 1,783 | 1.79% |
| order_delivered_customer_date | TEXT | 2,965 | 2.98% |
| order_estimated_delivery_date | TEXT | 0 | 0.00% |

Duplicate key (order_id): **0** surplus rows

- `order_purchase_timestamp`: min 2016-09-04 21:15:19, max 2018-10-17 17:30:18, not in 'YYYY-MM-DD HH:MM:SS' format: 0
- `order_approved_at`: min 2016-09-15 12:16:38, max 2018-09-03 17:40:06, not in 'YYYY-MM-DD HH:MM:SS' format: 0
- `order_delivered_carrier_date`: min 2016-10-08 10:34:01, max 2018-09-11 19:48:28, not in 'YYYY-MM-DD HH:MM:SS' format: 0
- `order_delivered_customer_date`: min 2016-10-11 13:46:32, max 2018-10-17 13:22:46, not in 'YYYY-MM-DD HH:MM:SS' format: 0
- `order_estimated_delivery_date`: min 2016-09-30 00:00:00, max 2018-11-12 00:00:00, not in 'YYYY-MM-DD HH:MM:SS' format: 0

### order_items
Rows: **112,650**

| column | type | nulls | null % |
|---|---|---|---|
| order_id | TEXT | 0 | 0.00% |
| order_item_id | INTEGER | 0 | 0.00% |
| product_id | TEXT | 0 | 0.00% |
| seller_id | TEXT | 0 | 0.00% |
| shipping_limit_date | TEXT | 0 | 0.00% |
| price | REAL | 0 | 0.00% |
| freight_value | REAL | 0 | 0.00% |

Duplicate key (order_id, order_item_id): **0** surplus rows

- `shipping_limit_date`: min 2016-09-19 00:15:34, max 2020-04-09 22:35:08, not in 'YYYY-MM-DD HH:MM:SS' format: 0

### order_reviews
Rows: **99,224**

| column | type | nulls | null % |
|---|---|---|---|
| review_id | TEXT | 0 | 0.00% |
| order_id | TEXT | 0 | 0.00% |
| review_score | INTEGER | 0 | 0.00% |
| review_comment_title | TEXT | 87,656 | 88.34% |
| review_comment_message | TEXT | 58,247 | 58.70% |
| review_creation_date | TEXT | 0 | 0.00% |
| review_answer_timestamp | TEXT | 0 | 0.00% |

Duplicate key (review_id, order_id): **0** surplus rows

- `review_creation_date`: min 2016-10-02 00:00:00, max 2018-08-31 00:00:00, not in 'YYYY-MM-DD HH:MM:SS' format: 0
- `review_answer_timestamp`: min 2016-10-07 18:32:28, max 2018-10-29 12:27:35, not in 'YYYY-MM-DD HH:MM:SS' format: 0

### order_payments
Rows: **103,886**

| column | type | nulls | null % |
|---|---|---|---|
| order_id | TEXT | 0 | 0.00% |
| payment_sequential | INTEGER | 0 | 0.00% |
| payment_type | TEXT | 0 | 0.00% |
| payment_installments | INTEGER | 0 | 0.00% |
| payment_value | REAL | 0 | 0.00% |

Duplicate key (order_id, payment_sequential): **0** surplus rows

### Status and category counts

`orders.order_status`

| order_status | rows |
|---|---|
| delivered | 96,478 |
| shipped | 1,107 |
| canceled | 625 |
| unavailable | 609 |
| invoiced | 314 |
| processing | 301 |
| created | 5 |
| approved | 2 |

`order_payments.payment_type`

| payment_type | rows |
|---|---|
| credit_card | 76,795 |
| boleto | 19,784 |
| voucher | 5,775 |
| debit_card | 1,529 |
| not_defined | 3 |

`order_reviews.review_score`

| review_score | rows |
|---|---|
| 5 | 57,328 |
| 4 | 19,142 |
| 1 | 11,424 |
| 3 | 8,179 |
| 2 | 3,151 |

## Quirks listed in the project brief

### Quirk: product categories

- Products with no category: **610**
- Categories with no English translation: **2** (13 products): pc_gamer (3), portateis_cozinha_e_preparadores_de_alimentos (10)
- Typo column names present in products: ['product_name_lenght', 'product_description_lenght']

### Quirk: order dates and statuses

- Delivered orders: **96,478**
- Delivered with no delivery date: **8** (go to quarantine, BR1)
- Delivery date earlier than purchase date: **0**
- Carrier handoff date earlier than purchase date: 166
- Non-delivered orders that do have a delivery date: 6
- Estimated delivery dates not at 00:00:00: **0** (0 means the estimate is always midnight, so compare dates not timestamps)
- Delivered orders with no carrier handoff date (BR3 cause = unknown): 2
- Carrier handoff later than customer delivery: 23
- Items with shipping_limit_date in 2019 or later (max is 2020): 4 items on 3 orders
- Late by date-only rule (BR1): **6,534** of 96,470 delivered orders with a date (6.77%)
- Late if we wrongly compared full timestamps: 7,826 (1,292 extra orders delivered on the estimated day itself)

### Quirk: order items (why the rate must be per order)

- Order items: 112,650 over 98,666 orders (per-item vs per-order inflation: 1.142x)
- Orders with more than one item: **9,803** (max items in one order: 21)
- Orders with more than one seller: 1,278
- Orders with no items: **775**
- Items with negative price or freight: 0; items with price 0: 0

### Quirk: reviews

- Review rows: 99,224 for 98,673 distinct orders
- Orders with more than one review: **547** (551 surplus rows to drop)
- review_id values used on more than one row: 789
- Orders with no review: 768
- Scores outside 1 to 5: 0

### Quirk: geolocation

- Rows: 1,000,163; distinct zip prefixes: **19,015**
- Rows per zip prefix: min 1, median 29, max 1,146
- Rows with coordinates outside Brazil's box: **42** (touching 21 prefixes; 5 prefixes have ONLY bad rows)
- Customer zip prefixes with no geolocation row: 157; seller zip prefixes with none: 7

### Relationships we rely on

- Orders whose customer_id is missing from customers: 0
- Customers: 99,441 customer_id values for 96,096 distinct customer_unique_id (repeat buyers)
- customer_id values with more than one order: 0 (0 means one customer_id per order)

## Source: Nager.Date public holidays (Brazil)

- Records: **42** over 2016 to 2018
- Per year: 2016: 14, 2017: 14, 2018: 14
- By type: Public: 36, Bank+Optional: 6
- Not global (state holidays, to drop): **3** 2016-07-09 Constitutionalist Revolution of 1932 counties=['BR-SP'], 2017-07-09 Constitutionalist Revolution of 1932 counties=['BR-SP'], 2018-07-09 Constitutionalist Revolution of 1932 counties=['BR-SP']
- Carnival rows: 6 (2016-02-08, 2016-02-09, 2017-02-27, 2017-02-28, 2018-02-12, 2018-02-13), types ['Bank+Optional']
- Duplicate (date, name) pairs: 0

| date | name | types | global |
|---|---|---|---|
| 2016-01-01 | New Year's Day | Public | True |
| 2016-02-08 | Carnival | Bank+Optional | True |
| 2016-02-09 | Carnival | Bank+Optional | True |
| 2016-03-25 | Good Friday | Public | True |
| 2016-03-27 | Easter Sunday | Public | True |
| 2016-04-21 | Tiradentes | Public | True |
| 2016-05-01 | Labour Day | Public | True |
| 2016-05-26 | Corpus Christi | Public | True |
| 2016-07-09 | Constitutionalist Revolution of 1932 | Public | False |
| 2016-09-07 | Independence Day | Public | True |
| 2016-10-12 | Our Lady of Aparecida | Public | True |
| 2016-11-02 | All Souls' Day | Public | True |
| 2016-11-15 | Republic Proclamation Day | Public | True |
| 2016-12-25 | Christmas Day | Public | True |
| 2017-01-01 | New Year's Day | Public | True |
| 2017-02-27 | Carnival | Bank+Optional | True |
| 2017-02-28 | Carnival | Bank+Optional | True |
| 2017-04-14 | Good Friday | Public | True |
| 2017-04-16 | Easter Sunday | Public | True |
| 2017-04-21 | Tiradentes | Public | True |
| 2017-05-01 | Labour Day | Public | True |
| 2017-06-15 | Corpus Christi | Public | True |
| 2017-07-09 | Constitutionalist Revolution of 1932 | Public | False |
| 2017-09-07 | Independence Day | Public | True |
| 2017-10-12 | Our Lady of Aparecida | Public | True |
| 2017-11-02 | All Souls' Day | Public | True |
| 2017-11-15 | Republic Proclamation Day | Public | True |
| 2017-12-25 | Christmas Day | Public | True |
| 2018-01-01 | New Year's Day | Public | True |
| 2018-02-12 | Carnival | Bank+Optional | True |
| 2018-02-13 | Carnival | Bank+Optional | True |
| 2018-03-30 | Good Friday | Public | True |
| 2018-04-01 | Easter Sunday | Public | True |
| 2018-04-21 | Tiradentes | Public | True |
| 2018-05-01 | Labour Day | Public | True |
| 2018-05-31 | Corpus Christi | Public | True |
| 2018-07-09 | Constitutionalist Revolution of 1932 | Public | False |
| 2018-09-07 | Independence Day | Public | True |
| 2018-10-12 | Our Lady of Aparecida | Public | True |
| 2018-11-02 | All Souls' Day | Public | True |
| 2018-11-15 | Republic Proclamation Day | Public | True |
| 2018-12-25 | Christmas Day | Public | True |

## Source: Frankfurter v2 exchange rates, BRL to USD (ECB)

- Request: `https://api.frankfurter.dev/v2/rates?base=BRL&quotes=USD&from=2016-01-01&to=2018-12-31&providers=ecb`
- Response is a flat JSON list of 768 rows; pagination headers seen: none
- Date range returned: 2015-12-31 to 2018-12-31 (the first row is the last business day BEFORE the requested start)
- Rate min 0.23847, max 0.32875 (test 11 expects 0.15 to 0.45)
- Weekend rows present: 0
- Calendar days in span with no row: **329** (314 weekend days, 15 weekdays such as ECB holidays)
- Longest run of consecutive days with no row: 4 (test 11 warns above 5)
- Weekdays with no row: 2016-01-01, 2016-03-25, 2016-03-28, 2016-12-26, 2017-04-14, 2017-04-17, 2017-05-01, 2017-12-25, 2017-12-26, 2018-01-01, 2018-03-30, 2018-04-02 ...
- Forward-fill needed for Olist purchase dates (2016-09 to 2018-10): weekends and the weekdays above take the last available business-day rate.

## Cross-check: does the rate series cover Olist purchase dates?

- Olist purchase dates: 2016-09-04 to 2018-10-17; rate series: 2015-12-31 to 2018-12-31; holidays: 2016-01-01 to 2018-12-25

