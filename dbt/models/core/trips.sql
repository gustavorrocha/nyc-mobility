{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='period',
    indexes=[{'columns': ['period', 'source_version']}],
) }}

-- Uma linha por corrida de raw: nada é excluído. Corridas inválidas ficam com is_valid = false
-- e o primeiro motivo encontrado em invalid_reason; o analytics usa apenas as válidas.
-- Incremental: só competências cuja versão (arquivo yellow + cadastro de zonas) mudou.
with periods as (
    {{ incremental_periods('trips_version') }}
),

yellow as (
    select
        t.*,
        p.source_version,
        to_date(t.period, 'YYYY-MM')::timestamp as period_start,
        to_date(t.period, 'YYYY-MM')::timestamp + interval '1 month' as period_end
    from {{ source('raw', 'tlc_yellow_trips') }} as t
    inner join periods as p on p.period = t.period
),

zones as (
    select zone_id from {{ ref('zones') }}
),

trips as (
    select
        'yellow'::text as service_type,
        y.period,
        y.source_file_id,
        y.row_number as source_row_number,
        -- Horário local de NY como publicado pela TLC, e o mesmo instante em UTC. Na volta do
        -- horário de verão, 01:00–01:59 local é ambíguo; o PostgreSQL resolve sempre da mesma
        -- forma (limitação aceita: a origem não informa o fuso).
        y.tpep_pickup_datetime as pickup_at_local,
        y.tpep_dropoff_datetime as dropoff_at_local,
        y.tpep_pickup_datetime at time zone 'America/New_York' as pickup_at_utc,
        y.tpep_dropoff_datetime at time zone 'America/New_York' as dropoff_at_utc,
        y.pulocationid as pickup_zone_id,
        y.dolocationid as dropoff_zone_id,
        y.passenger_count,
        y.trip_distance as trip_distance_miles,
        y.fare_amount,
        y.tip_amount,
        y.total_amount,
        -- Mesmas regras contabilizadas na ingestão, mais a zona fora do cadastro.
        case
            when y.tpep_pickup_datetime is null or y.tpep_dropoff_datetime is null
                or y.trip_distance is null or y.pulocationid is null or y.dolocationid is null
                then 'missing_required_value'
            when y.tpep_dropoff_datetime < y.tpep_pickup_datetime then 'dropoff_before_pickup'
            when y.trip_distance < 0 then 'negative_distance'
            when not (y.tpep_pickup_datetime >= y.period_start
                and y.tpep_pickup_datetime < y.period_end) then 'pickup_outside_period'
            when pu.zone_id is null or dz.zone_id is null then 'unknown_zone'
        end as invalid_reason,
        y.source_version
    from yellow as y
    left join zones as pu on pu.zone_id = y.pulocationid
    left join zones as dz on dz.zone_id = y.dolocationid
)

select
    *,
    extract(epoch from dropoff_at_utc - pickup_at_utc)::bigint as duration_seconds,
    invalid_reason is null as is_valid
from trips
