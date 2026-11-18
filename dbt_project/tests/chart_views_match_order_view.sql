-- Chart 2 summed over all routes must give the same late orders as vw_order_late,
-- except that an order can sit in two routes (two seller states), so compare late orders
-- only when we can: seller+carrier+unknown must equal late orders per route row.
-- Passes when no rows come back.
select seller_state, customer_state, purchase_year, late_orders,
       seller_late_orders + carrier_late_orders + unknown_late_orders as caused
from {{ ref('vw_chart_route_cause') }}
where late_orders != seller_late_orders + carrier_late_orders + unknown_late_orders
