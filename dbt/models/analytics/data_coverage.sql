{{ config(materialized='view') }}

-- Cobertura significa que a versão atual de raw chegou aos agregados, mesmo com zero válidas.
-- A view consulta metadados ao vivo: uma republicação fica pendente antes do próximo dbt build.
with published as (
    select distinct on (s.dataset, r.period)
        s.dataset as service_type, r.period, r.rows_inserted
    from {{ source('metadata', 'ingestion_runs') }} as r
    join {{ source('metadata', 'sources') }} as s using (source_id)
    where s.source = 'tlc' and r.period is not null and r.status = 'succeeded'
    order by s.dataset, r.period, r.run_id desc
),

quality as (
    select period, service_type, source_version, sum(trip_count) as total,
        sum(case when status != 'invalid' then trip_count else 0 end) as valid
    from {{ ref('trip_quality') }} group by 1, 2, 3
),

demand as (
    select period, service_type, source_version, sum(trip_count) as valid
    from {{ ref('trip_demand_hourly') }} group by 1, 2, 3
),

weather as (
    select period, source_version, count(*) as hours
    from {{ ref('trip_weather_hourly') }} group by 1, 2
)

select
    v.period,
    s.service_type,
    case
        when p.period is null then 'missing'
        when coalesce(q.total, 0) = p.rows_inserted
            and coalesce(q.valid, 0) = coalesce(d.valid, 0)
            and (p.rows_inserted = 0 or q.source_version = v.trips_version)
            and (coalesce(q.valid, 0) = 0 or d.source_version = v.trips_version)
            then 'available'
        else 'pending'
    end as status,
    case
        when v.weather_version is null then 'missing'
        when w.source_version = v.trip_weather_version
            and w.hours = extract(epoch from
                (to_date(v.period, 'YYYY-MM') + interval '1 month')::timestamp
                    at time zone 'America/New_York'
                - to_date(v.period, 'YYYY-MM')::timestamp at time zone 'America/New_York') / 3600
            then 'available'
        else 'pending'
    end as weather_status
from {{ ref('period_versions') }} as v
cross join {{ ref('service_types') }} as s
left join published as p on p.period = v.period and p.service_type = s.service_type
left join quality as q on q.period = v.period and q.service_type = s.service_type
left join demand as d on d.period = v.period and d.service_type = s.service_type
left join weather as w on w.period = v.period
