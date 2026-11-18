-- The per-order late rate in the mart must equal a recomputation straight from raw_orders
-- (no staging, no fact table: plain date() comparison). Passes when no rows come back.
with mart as (
    select sum(is_late) as late_orders, count(*) as orders from {{ ref('vw_order_late') }}
),
raw as (
    select
        sum(case when date(order_delivered_customer_date) > date(order_estimated_delivery_date) then 1 else 0 end) as late_orders,
        count(*) as orders
    from {{ source('staging', 'raw_orders') }}
    where order_status = 'delivered' and order_delivered_customer_date is not null
      and order_id in (select order_id from {{ ref('fact_order_item') }})   -- orders that have items and were not quarantined
)
select mart.late_orders as mart_late, raw.late_orders as raw_late, mart.orders as mart_orders, raw.orders as raw_orders
from mart, raw
where mart.late_orders != raw.late_orders or mart.orders != raw.orders
