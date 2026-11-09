{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key='period') }}

-- Corridas por competência, modalidade e situação de qualidade, para monitoramento (Pipeline
-- Health) sem varrer core.trips: ok, metric_issue (válida, fora das médias) ou invalid.
-- `reason` traz o invalid_reason ou o metric_issue; NULL quando ok.
with periods as (
    {{ incremental_periods('trips_version') }}
)

select
    t.period,
    t.service_type,
    case
        when not t.is_valid then 'invalid'
        when t.metric_issue is not null then 'metric_issue'
        else 'ok'
    end as status,
    coalesce(t.invalid_reason, t.metric_issue) as reason,
    count(*) as trip_count,
    p.source_version
from {{ ref('trips') }} as t
inner join periods as p on p.period = t.period
group by t.period, t.service_type, 3, 4, p.source_version
