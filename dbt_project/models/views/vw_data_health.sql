-- Header numbers for the dashboard: orders set aside (out of scope or quarantined).
select 'orders_out_of_scope' as metric, count(*) as value
from {{ source('staging', 'stg_orders') }} where in_scope = 0
union all
select 'orders_quarantined', count(*) from {{ source('staging', 'quarantine_orders') }}
union all
select 'rows_quarantined_all_sources',
       (select count(*) from {{ source('staging', 'quarantine_orders') }})
     + (select count(*) from {{ source('staging', 'quarantine_order_items') }})
     + (select count(*) from {{ source('staging', 'quarantine_order_reviews') }})
     + (select count(*) from {{ source('staging', 'quarantine_geolocation') }})
     + (select count(*) from {{ source('staging', 'quarantine_holidays') }})
