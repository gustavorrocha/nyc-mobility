-- Demanda por hora (UTC) de embarque, modalidade e zona de embarque; apenas corridas válidas.
select
    date_trunc('hour', pickup_at_utc) as hour_utc,
    date_trunc('hour', pickup_at_utc) at time zone 'America/New_York' as hour_local,
    service_type,
    pickup_zone_id,
    count(*) as trip_count,
    avg(duration_seconds) / 60.0 as avg_duration_min,
    avg(trip_distance_miles) as avg_distance_miles,
    sum(total_amount) as total_amount
from {{ ref('trips') }}
where is_valid
group by 1, 2, 3, 4
