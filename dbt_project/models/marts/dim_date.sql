-- One row per calendar day, 2016-09-01 to 2018-12-31.
-- Holidays and exchange rates meet the fact table here.
with recursive days(d) as (
    select date('2016-09-01')
    union all
    select date(d, '+1 day') from days where d < date('2018-12-31')
),
holiday_by_day as (
    -- one row per date, in case two holidays ever share a date
    select holiday_date,
           max(is_public_national) as is_public_holiday,
           max(is_carnival) as is_carnival,
           group_concat(name, ' / ') as holiday_name
    from {{ source('staging', 'stg_holidays') }}
    group by holiday_date
)
select
    cast(strftime('%Y%m%d', days.d) as integer) as date_key,
    days.d as date,
    cast(strftime('%Y', days.d) as integer) as year,
    cast(strftime('%m', days.d) as integer) as month,
    (cast(strftime('%m', days.d) as integer) + 2) / 3 as quarter,
    -- strftime %w: 0 = Sunday ... 6 = Saturday
    cast(strftime('%w', days.d) as integer) as weekday,
    case when strftime('%w', days.d) in ('0', '6') then 1 else 0 end as is_weekend,
    coalesce(h.is_public_holiday, 0) as is_public_holiday,
    h.holiday_name as holiday_name,
    coalesce(h.is_carnival, 0) as is_carnival,
    -- forward fill: the latest ECB rate on or before this day (a weekend uses the Friday rate)
    (select r.brl_usd_rate
     from {{ source('staging', 'stg_fx_rates') }} r
     where r.rate_date <= days.d
     order by r.rate_date desc
     limit 1) as brl_usd_rate
from days
left join holiday_by_day h on h.holiday_date = days.d
