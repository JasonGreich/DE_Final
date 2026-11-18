select
    product_id, category_pt, category_en, category_en_is_fallback,
    weight_g, length_cm, height_cm, width_cm
from {{ source('staging', 'stg_products') }}
