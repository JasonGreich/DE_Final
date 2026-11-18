-- One row per IN-SCOPE order (delivered with a delivery date). The base of every rate.
select
    f.order_id,
    c.state as customer_state,
    c.region as customer_region,
    dd.year as purchase_year,
    max(f.is_late) as is_late,
    max(f.days_late) as days_late,
    max(f.holiday_in_window) as holiday_in_window,
    max(f.carnival_in_window) as carnival_in_window,
    max(f.review_score) as review_score,          -- same value on every item of the order
    sum(f.price_usd) as revenue_usd,
    max(f.delay_cause) as delay_cause,
    count(*) as items
from {{ ref('fact_order_item') }} f
join {{ ref('dim_customer') }} c on c.customer_id = f.customer_id
join {{ ref('dim_date') }} dd on dd.date_key = f.purchase_date_key
where f.in_scope = 1
group by f.order_id, c.state, c.region, dd.year
