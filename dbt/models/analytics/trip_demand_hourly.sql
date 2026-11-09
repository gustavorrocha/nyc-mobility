{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='period',
    indexes=[{'columns': ['period', 'source_version']}],
) }}

-- Demanda por hora (UTC) de embarque, modalidade e zona de embarque; apenas corridas válidas.
-- Métricas em somas + contagem (somáveis em qualquer recorte), só das corridas sem metric_issue:
--   duração média = duration_min_sum / measured_trip_count (idem distância e valor);
--   velocidade média = distance_miles_sum / (duration_min_sum / 60).
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
    count(*) filter (where t.metric_issue is null) as measured_trip_count,
    coalesce(sum(t.duration_seconds) filter (where t.metric_issue is null), 0)::float8 / 60
        as duration_min_sum,
    coalesce(sum(t.trip_distance_miles) filter (where t.metric_issue is null), 0)
        as distance_miles_sum,
    coalesce(sum(t.total_amount) filter (where t.metric_issue is null), 0) as amount_sum,
    p.source_version
from {{ ref('trips') }} as t
inner join periods as p on p.period = t.period
where t.is_valid
group by t.period, 2, 3, t.service_type, t.pickup_zone_id, p.source_version
