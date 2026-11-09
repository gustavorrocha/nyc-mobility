{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='period',
    indexes=[{'columns': ['period', 'source_version']}],
) }}

-- Demanda por hora (UTC) de embarque, modalidade e zona de embarque; apenas corridas válidas.
-- Corridas válidas embarcam dentro da competência (mês local, que começa em hora cheia), logo
-- cada hora pertence a uma única competência e o incremental por period é exato.
with periods as (
    {{ incremental_periods('trips_version') }}
)

select
    t.period,
    date_trunc('hour', t.pickup_at_utc) as hour_utc,
    date_trunc('hour', t.pickup_at_utc) at time zone 'America/New_York' as hour_local,
    t.service_type,
    t.pickup_zone_id,
    count(*) as trip_count,
    avg(t.duration_seconds) / 60.0 as avg_duration_min,
    avg(t.trip_distance_miles) as avg_distance_miles,
    sum(t.total_amount) as total_amount,
    p.source_version
from {{ ref('trips') }} as t
inner join periods as p on p.period = t.period
where t.is_valid
group by t.period, 2, 3, t.service_type, t.pickup_zone_id, p.source_version
