-- Chart 2: for each seller-state to customer-state route, how many late orders are seller-caused vs carrier-caused.
-- Orders are counted once per route. Only late orders have a cause.
select
    s.state as seller_state,
    c.state as customer_state,
    dd.year as purchase_year,
    f.holiday_in_window,
    f.carnival_in_window,
    count(distinct f.order_id) as orders,
    count(distinct case when f.is_late = 1 then f.order_id end) as late_orders,
    count(distinct case when f.delay_cause = 'seller' then f.order_id end) as seller_late_orders,
    count(distinct case when f.delay_cause = 'carrier' then f.order_id end) as carrier_late_orders,
    count(distinct case when f.delay_cause = 'unknown' then f.order_id end) as unknown_late_orders
from {{ ref('fact_order_item') }} f
join {{ ref('dim_seller') }} s on s.seller_id = f.seller_id
join {{ ref('dim_customer') }} c on c.customer_id = f.customer_id
join {{ ref('dim_date') }} dd on dd.date_key = f.purchase_date_key
where f.in_scope = 1
group by s.state, c.state, dd.year, f.holiday_in_window, f.carnival_in_window
