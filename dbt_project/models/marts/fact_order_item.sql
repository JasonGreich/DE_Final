-- Grain: one row per order item (order_id, order_item_id).
-- Order-level columns (dates, holiday flags, review) are repeated on each item of the order,
-- so never count rows for a rate: count distinct order_id (see the vw_ views).
select
    i.order_id,
    i.order_item_id,
    cast(strftime('%Y%m%d', o.purchase_date) as integer)  as purchase_date_key,
    cast(strftime('%Y%m%d', o.estimated_date) as integer) as estimated_date_key,
    cast(strftime('%Y%m%d', o.delivered_date) as integer) as delivered_date_key,   -- null if not delivered
    i.product_id,
    i.seller_id,
    i.customer_id,
    i.price as price_brl,
    i.freight_value as freight_brl,
    i.price * d.brl_usd_rate as price_usd,           -- ECB rate of the purchase date, forward-filled
    i.freight_value * d.brl_usd_rate as freight_usd,
    i.freight_share,
    i.distance_km,
    i.is_late,
    o.days_late,
    i.handoff_late_days,
    i.transit_days,
    i.delay_cause,
    r.review_score,
    o.holiday_in_window,
    o.carnival_in_window,
    i.order_status,
    i.in_scope
from {{ source('staging', 'stg_order_items') }} i
join {{ source('staging', 'stg_orders') }} o on o.order_id = i.order_id
left join {{ source('staging', 'stg_order_reviews') }} r on r.order_id = i.order_id
left join {{ ref('dim_date') }} d on d.date_key = cast(strftime('%Y%m%d', o.purchase_date) as integer)
