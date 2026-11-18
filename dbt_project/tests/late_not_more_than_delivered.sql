-- Late orders can never exceed delivered orders. Passes when no rows come back.
select count(*) as orders, sum(is_late) as late_orders
from {{ ref('vw_order_late') }}
having sum(is_late) > count(*)
