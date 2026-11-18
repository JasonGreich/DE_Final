-- Chart 1: late rate by distance band and by freight-share band.
-- An order counts once in each band it has an item in (count distinct order_id).
-- The grouping columns (state, year, flags) are order-level, so summing rows in the app is safe.
with item_bands as (
    select
        f.order_id,
        c.state as customer_state,
        dd.year as purchase_year,
        f.holiday_in_window,
        f.carnival_in_window,
        f.is_late,
        case when f.distance_km is null then null
             when f.distance_km < 100 then '1: under 100 km'
             when f.distance_km < 500 then '2: 100-500 km'
             when f.distance_km < 1000 then '3: 500-1000 km'
             when f.distance_km < 2000 then '4: 1000-2000 km'
             else '5: 2000 km +' end as distance_band,
        case when f.freight_share is null then null
             when f.freight_share < 0.1 then '1: under 10%'
             when f.freight_share < 0.2 then '2: 10-20%'
             when f.freight_share < 0.4 then '3: 20-40%'
             when f.freight_share < 0.8 then '4: 40-80%'
             else '5: 80% +' end as freight_band
    from {{ ref('fact_order_item') }} f
    join {{ ref('dim_customer') }} c on c.customer_id = f.customer_id
    join {{ ref('dim_date') }} dd on dd.date_key = f.purchase_date_key
    where f.in_scope = 1
)
select 'distance' as band_type, distance_band as band, customer_state, purchase_year,
       holiday_in_window, carnival_in_window,
       count(distinct order_id) as orders,
       count(distinct case when is_late = 1 then order_id end) as late_orders
from item_bands where distance_band is not null
group by distance_band, customer_state, purchase_year, holiday_in_window, carnival_in_window
union all
select 'freight', freight_band, customer_state, purchase_year, holiday_in_window, carnival_in_window,
       count(distinct order_id),
       count(distinct case when is_late = 1 then order_id end)
from item_bands where freight_band is not null
group by freight_band, customer_state, purchase_year, holiday_in_window, carnival_in_window
