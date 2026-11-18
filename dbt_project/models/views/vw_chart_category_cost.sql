-- Chart 3: review score and revenue on late vs on-time orders, by category.
-- First one row per (order, category) so the review counts once per order and category;
-- then sums. The app divides review_sum by review_orders to get the average.
-- revenue_usd is revenue ON late orders (item prices in USD), not revenue lost.
with order_category as (
    select
        f.order_id,
        coalesce(p.category_en, 'unknown') as category,
        c.state as customer_state,
        dd.year as purchase_year,
        f.holiday_in_window,
        f.carnival_in_window,
        max(f.is_late) as is_late,
        max(f.review_score) as review_score,
        sum(f.price_usd) as revenue_usd
    from {{ ref('fact_order_item') }} f
    join {{ ref('dim_product') }} p on p.product_id = f.product_id
    join {{ ref('dim_customer') }} c on c.customer_id = f.customer_id
    join {{ ref('dim_date') }} dd on dd.date_key = f.purchase_date_key
    where f.in_scope = 1
    group by f.order_id, p.category_en, c.state, dd.year, f.holiday_in_window, f.carnival_in_window
)
select category, customer_state, purchase_year, holiday_in_window, carnival_in_window, is_late,
       count(*) as orders,
       sum(case when review_score is not null then 1 else 0 end) as review_orders,
       sum(review_score) as review_sum,
       sum(revenue_usd) as revenue_usd
from order_category
group by category, customer_state, purchase_year, holiday_in_window, carnival_in_window, is_late
