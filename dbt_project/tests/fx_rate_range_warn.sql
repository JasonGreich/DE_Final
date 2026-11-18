{{ config(severity='warn') }}
-- BRL/USD must stay between 0.15 and 0.45 on every day of dim_date (warn only, as in CLAUDE.md test 11).
select date_key, brl_usd_rate
from {{ ref('dim_date') }}
where brl_usd_rate is null or brl_usd_rate < 0.15 or brl_usd_rate > 0.45
