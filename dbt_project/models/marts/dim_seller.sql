select seller_id, city, state, region, zip_prefix, lat, lng
from {{ source('staging', 'stg_sellers') }}
