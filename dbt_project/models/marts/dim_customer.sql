select customer_id, customer_unique_id, city, state, region, zip_prefix, lat, lng
from {{ source('staging', 'stg_customers') }}
