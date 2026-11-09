-- Semântica da fonte preservada: variáveis instantâneas em observed_at_utc; acumulados
-- (precipitation_mm, rain_mm, snowfall_cm) referem-se a (observed_at_utc - 1h, observed_at_utc].
select
    to_timestamp(time_unix) as observed_at_utc,
    period,
    temperature_2m as temperature_c,
    relative_humidity_2m as relative_humidity_pct,
    precipitation as precipitation_mm,
    rain as rain_mm,
    snowfall as snowfall_cm,
    weather_code,
    wind_speed_10m as wind_speed_kmh,
    latitude,
    longitude,
    source_file_id
from {{ source('raw', 'openmeteo_weather_hourly') }}
