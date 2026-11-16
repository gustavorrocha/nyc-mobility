{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='period',
    indexes=[{'columns': ['period', 'source_version']}],
) }}

-- Uma linha por corrida de raw, de todas as modalidades: nada é excluído. Corridas inválidas
-- ficam com is_valid = false e o primeiro motivo em invalid_reason; o analytics usa só as válidas.
-- Incremental: só competências cuja versão (arquivos de viagens + cadastro de zonas) mudou.
with periods as (
    {{ incremental_periods('trips_version') }}
),

-- Esquema comum: o que a fonte não informa fica NULL (nunca zero). `zones_required`: a TLC
-- exige zonas e distância na modalidade (no FHV tradicional não; zona ausente é desconhecida).
unioned as (
    select
        'yellow'::text as service_type, period, source_file_id, row_number,
        tpep_pickup_datetime as pickup_local, tpep_dropoff_datetime as dropoff_local,
        pulocationid, dolocationid, passenger_count, trip_distance,
        fare_amount, tip_amount, total_amount, true as zones_required
    from {{ source('raw', 'tlc_yellow_trips') }}

    union all

    select
        'green', period, source_file_id, row_number,
        lpep_pickup_datetime, lpep_dropoff_datetime,
        pulocationid, dolocationid, passenger_count, trip_distance,
        fare_amount, tip_amount, total_amount, true
    from {{ source('raw', 'tlc_green_trips') }}

    union all

    select
        'fhv', period, source_file_id, row_number,
        pickup_datetime, dropoff_datetime,
        pulocationid, dolocationid, null::bigint, null::double precision,
        null::double precision, null::double precision, null::double precision, false
    from {{ source('raw', 'tlc_fhv_trips') }}

    union all

    -- HVFHV: valor pago pelo passageiro = tarifa base + pedágios + taxas + gorjeta.
    select
        'fhvhv', period, source_file_id, row_number,
        pickup_datetime, dropoff_datetime,
        pulocationid, dolocationid, null::bigint, trip_miles,
        base_passenger_fare, tips,
        base_passenger_fare + coalesce(tolls, 0) + coalesce(bcf, 0) + coalesce(sales_tax, 0)
            + coalesce(congestion_surcharge, 0) + coalesce(airport_fee, 0) + coalesce(tips, 0)
            + coalesce(cbd_congestion_fee, 0),
        true
    from {{ source('raw', 'tlc_fhvhv_trips') }}
),

staged as (
    select
        t.*,
        p.source_version,
        to_date(t.period, 'YYYY-MM')::timestamp as period_start,
        to_date(t.period, 'YYYY-MM')::timestamp + interval '1 month' as period_end,
        t.pickup_local at time zone 'America/New_York' as pickup_utc_default,
        t.dropoff_local at time zone 'America/New_York' as dropoff_utc_default
    from unioned as t
    inner join periods as p on p.period = t.period
),

-- Horário de verão: a origem não informa o fuso, então horários inexistentes e ambíguos são
-- identificados explicitamente em vez de convertidos pela regra implícita do PostgreSQL.
dst as (
    select
        *,
        {{ ny_is_nonexistent('pickup_local', 'pickup_utc_default') }}
            or {{ ny_is_nonexistent('dropoff_local', 'dropoff_utc_default') }}
            as is_nonexistent,
        {{ ny_ambiguous_alternative('pickup_local', 'pickup_utc_default') }}
            as pickup_utc_alternative,
        {{ ny_ambiguous_alternative('dropoff_local', 'dropoff_utc_default') }}
            as dropoff_utc_alternative
    from staged
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
        y.service_type,
        y.period,
        y.source_file_id,
        y.row_number as source_row_number,
        -- Horário local de NY como publicado pela TLC, e o mesmo instante em UTC.
        y.pickup_local as pickup_at_local,
        y.dropoff_local as dropoff_at_local,
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
        -- de horário de verão), mais horário inexistente e zona fora do cadastro. Zona ausente só
        -- invalida onde a TLC a exige; informada e fora do cadastro invalida sempre.
        case
            when y.pickup_local is null or y.dropoff_local is null
                or (y.zones_required and (y.trip_distance is null
                    or y.pulocationid is null or y.dolocationid is null))
                then 'missing_required_value'
            when y.local_time_issue = 'nonexistent' then 'nonexistent_local_time'
            when y.dropoff_utc < y.pickup_utc then 'dropoff_before_pickup'
            when y.trip_distance < 0 then 'negative_distance'
            when not (y.pickup_local >= y.period_start
                and y.pickup_local < y.period_end) then 'pickup_outside_period'
            when (y.pulocationid is not null and pu.zone_id is null)
                or (y.dolocationid is not null and dz.zone_id is null) then 'unknown_zone'
        end as invalid_reason,
        y.source_version
    from converted as y
    left join zones as pu on pu.zone_id = y.pulocationid
    left join zones as dz on dz.zone_id = y.dolocationid
),

measured as (
    select
        *,
        extract(epoch from dropoff_at_utc - pickup_at_utc)::bigint as duration_seconds,
        invalid_reason is null as is_valid
    from trips
)

-- Métricas não informadas pela fonte (FHV) ou implausíveis (primeiro motivo): a corrida segue
-- válida e conta na demanda, mas fica fora das médias de duração, distância, velocidade e valor.
-- Limites: vars metric_* em dbt_project.yml. Nas inválidas fica NULL (já estão fora de tudo).
select
    *,
    case
        when not is_valid then null
        when trip_distance_miles is null or total_amount is null then 'metric_unavailable'
        when duration_seconds < {{ var('metric_min_duration_minutes') }} * 60
            or duration_seconds > {{ var('metric_max_duration_minutes') }} * 60
            then 'implausible_duration'
        when trip_distance_miles <= 0
            or trip_distance_miles > {{ var('metric_max_distance_miles') }}
            then 'implausible_distance'
        when trip_distance_miles / (duration_seconds / 3600.0) > {{ var('metric_max_speed_mph') }}
            then 'implausible_speed'
        when total_amount is null or total_amount <= 0
            or total_amount > {{ var('metric_max_total_amount') }}
            then 'implausible_amount'
    end as metric_issue
from measured
