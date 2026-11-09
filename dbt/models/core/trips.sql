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
        to_date(t.period, 'YYYY-MM')::timestamp + interval '1 month' as period_end,
        t.tpep_pickup_datetime at time zone 'America/New_York' as pickup_utc_default,
        t.tpep_dropoff_datetime at time zone 'America/New_York' as dropoff_utc_default
    from {{ source('raw', 'tlc_yellow_trips') }} as t
    inner join periods as p on p.period = t.period
),

-- Horário de verão: a origem não informa o fuso, então horários inexistentes e ambíguos são
-- identificados explicitamente em vez de convertidos pela regra implícita do PostgreSQL.
dst as (
    select
        *,
        {{ ny_is_nonexistent('tpep_pickup_datetime', 'pickup_utc_default') }}
            or {{ ny_is_nonexistent('tpep_dropoff_datetime', 'dropoff_utc_default') }}
            as is_nonexistent,
        {{ ny_ambiguous_alternative('tpep_pickup_datetime', 'pickup_utc_default') }}
            as pickup_utc_alternative,
        {{ ny_ambiguous_alternative('tpep_dropoff_datetime', 'dropoff_utc_default') }}
            as dropoff_utc_alternative
    from yellow
),

-- Ocorrências possíveis de cada instante: primeira (early) e última (late); iguais quando o
-- horário não é ambíguo.
occurrences as (
    select
        *,
        least(pickup_utc_default, pickup_utc_alternative) as pickup_early,
        greatest(pickup_utc_default, pickup_utc_alternative) as pickup_late,
        least(dropoff_utc_default, dropoff_utc_alternative) as dropoff_early,
        greatest(dropoff_utc_default, dropoff_utc_alternative) as dropoff_late
    from dst
    -- Barreira de otimização: sem ela o PostgreSQL reexpande as conversões de fuso a cada
    -- referência a estas colunas nas etapas seguintes (ordem de grandeza mais lento).
    offset 0
),

-- Ambíguos: escolhe a combinação de ocorrências com menor duração não negativa; no empate,
-- a primeira ocorrência (EDT). Para cada embarque possível, o melhor desembarque é o primeiro
-- que não o antecede.
candidates as (
    select
        *,
        case
            when dropoff_early >= pickup_early then dropoff_early
            when dropoff_late >= pickup_early then dropoff_late
        end as dropoff_for_early,
        case
            when dropoff_early >= pickup_late then dropoff_early
            when dropoff_late >= pickup_late then dropoff_late
        end as dropoff_for_late
    from occurrences
),

converted as (
    select
        *,
        case
            when is_nonexistent then 'nonexistent'
            when pickup_utc_alternative is not null or dropoff_utc_alternative is not null
                then 'ambiguous'
        end as local_time_issue,
        -- Horário inexistente não é convertido por suposição. Sem combinação válida, fica a
        -- primeira ocorrência (e a corrida é sinalizada como dropoff_before_pickup).
        case
            when is_nonexistent then null
            when dropoff_for_late is null
                or dropoff_for_early - pickup_early <= dropoff_for_late - pickup_late
                then pickup_early
            else pickup_late
        end as pickup_utc,
        case
            when is_nonexistent then null
            when dropoff_for_early is null and dropoff_for_late is null then dropoff_early
            when dropoff_for_late is null
                or dropoff_for_early - pickup_early <= dropoff_for_late - pickup_late
                then dropoff_for_early
            else dropoff_for_late
        end as dropoff_utc
    from candidates
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
        -- Horário local de NY como publicado pela TLC, e o mesmo instante em UTC.
        y.tpep_pickup_datetime as pickup_at_local,
        y.tpep_dropoff_datetime as dropoff_at_local,
        y.pickup_utc as pickup_at_utc,
        y.dropoff_utc as dropoff_at_utc,
        y.local_time_issue,
        y.pulocationid as pickup_zone_id,
        y.dolocationid as dropoff_zone_id,
        y.passenger_count,
        y.trip_distance as trip_distance_miles,
        y.fare_amount,
        y.tip_amount,
        y.total_amount,
        -- Regras da ingestão (ordem dos instantes avaliada em UTC, correta também nas mudanças
        -- de horário de verão), mais horário inexistente e zona fora do cadastro.
        case
            when y.tpep_pickup_datetime is null or y.tpep_dropoff_datetime is null
                or y.trip_distance is null or y.pulocationid is null or y.dolocationid is null
                then 'missing_required_value'
            when y.local_time_issue = 'nonexistent' then 'nonexistent_local_time'
            when y.dropoff_utc < y.pickup_utc then 'dropoff_before_pickup'
            when y.trip_distance < 0 then 'negative_distance'
            when not (y.tpep_pickup_datetime >= y.period_start
                and y.tpep_pickup_datetime < y.period_end) then 'pickup_outside_period'
            when pu.zone_id is null or dz.zone_id is null then 'unknown_zone'
        end as invalid_reason,
        y.source_version
    from converted as y
    left join zones as pu on pu.zone_id = y.pulocationid
    left join zones as dz on dz.zone_id = y.dolocationid
)

select
    *,
    extract(epoch from dropoff_at_utc - pickup_at_utc)::bigint as duration_seconds,
    invalid_reason is null as is_valid
from trips
