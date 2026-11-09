-- Uma linha por hora [hour_utc, hour_utc + 1h) das competências com viagens e clima carregados.
-- Instantâneas vêm da observação em hour_utc; acumulados vêm da observação seguinte, que cobre
-- exatamente essa hora (fica NULL na última hora se a competência seguinte não estiver carregada).
with weather as (
    select * from {{ ref('weather_hourly') }}
),

demand as (
    select
        date_trunc('hour', pickup_at_utc) as hour_utc,
        count(*) as trip_count,
        avg(duration_seconds) / 60.0 as avg_duration_min,
        avg(trip_distance_miles) as avg_distance_miles
    from {{ ref('trips') }}
    where is_valid
    group by 1
)

select
    w.observed_at_utc as hour_utc,
    w.observed_at_utc at time zone 'America/New_York' as hour_local,
    w.period,
    coalesce(d.trip_count, 0) as trip_count,
    d.avg_duration_min,
    d.avg_distance_miles,
    w.temperature_c,
    w.relative_humidity_pct,
    w.wind_speed_kmh,
    w.weather_code,
    nxt.precipitation_mm,
    nxt.rain_mm,
    nxt.snowfall_cm
from weather as w
left join weather as nxt on nxt.observed_at_utc = w.observed_at_utc + interval '1 hour'
left join demand as d on d.hour_utc = w.observed_at_utc
where w.period in (select distinct period from {{ ref('trips') }})
