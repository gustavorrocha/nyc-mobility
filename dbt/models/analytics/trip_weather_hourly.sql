{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key='period') }}

-- Uma linha por hora [hour_utc, hour_utc + 1h) das competências com viagens e clima carregados.
-- Instantâneas vêm da observação em hour_utc; acumulados vêm da observação seguinte, que cobre
-- exatamente essa hora (NULL na última hora enquanto a competência seguinte não for carregada;
-- carregá-la muda a versão e reprocessa esta competência).
with periods as (
    {{ incremental_periods('trip_weather_version') }}
),

weather as (
    select * from {{ ref('weather_hourly') }}
),

demand as (
    select
        hour_utc,
        sum(trip_count) as trip_count,
        sum(avg_duration_min * trip_count) / sum(trip_count) as avg_duration_min,
        sum(avg_distance_miles * trip_count) / sum(trip_count) as avg_distance_miles
    from {{ ref('trip_demand_hourly') }}
    where period in (select period from periods)
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
    nxt.snowfall_cm,
    p.source_version
from weather as w
inner join periods as p on p.period = w.period
left join weather as nxt on nxt.observed_at_utc = w.observed_at_utc + interval '1 hour'
left join demand as d on d.hour_utc = w.observed_at_utc
