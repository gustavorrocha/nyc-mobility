{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key='period') }}

-- Semântica da fonte preservada: variáveis instantâneas em observed_at_utc; acumulados
-- (precipitation_mm, rain_mm, snowfall_cm) referem-se a (observed_at_utc - 1h, observed_at_utc].
-- Incremental: só competências cuja versão do arquivo mudou.
with periods as (
    {{ incremental_periods('weather_version') }}
)

select
    to_timestamp(w.time_unix) as observed_at_utc,
    w.period,
    w.temperature_2m as temperature_c,
    w.relative_humidity_2m as relative_humidity_pct,
    w.precipitation as precipitation_mm,
    w.rain as rain_mm,
    w.snowfall as snowfall_cm,
    w.weather_code,
    w.wind_speed_10m as wind_speed_kmh,
    w.latitude,
    w.longitude,
    w.source_file_id,
    p.source_version
from {{ source('raw', 'openmeteo_weather_hourly') }} as w
inner join periods as p on p.period = w.period
