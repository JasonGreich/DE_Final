-- Grain check: (order_id, order_item_id) must be unique. Passes when no rows come back.
select order_id, order_item_id, count(*) as n
from {{ ref('fact_order_item') }}
group by order_id, order_item_id
having count(*) > 1
