-- A late order must be a delivered, in-scope order. Passes when no rows come back.
select order_id, order_item_id, order_status, in_scope, is_late
from {{ ref('fact_order_item') }}
where is_late = 1
  and (order_status != 'delivered' or in_scope != 1 or delivered_date_key is null)
