{{ config(materialized='ephemeral') }}

-- Versão dos insumos de cada competência, usada pelos modelos incrementais: uma competência só é
-- reprocessada quando a versão gravada no modelo difere da atual (ver macro incremental_periods).
with published as (
    select distinct on (s.source, s.dataset, r.period)
        s.source || '.' || s.dataset as dataset,
        r.period,
        r.source_file_id
    from {{ source('metadata', 'ingestion_runs') }} as r
    inner join {{ source('metadata', 'sources') }} as s using (source_id)
    where r.status = 'succeeded'
    order by s.source, s.dataset, r.period, r.run_id desc
),

-- unknown_zone depende do cadastro: nova versão de zonas reprocessa todas as viagens.
zones as (
    select 'tlc.taxi_zones=' || coalesce(max(source_file_id)::text, 'none') as version
    from published
    where dataset = 'tlc.taxi_zones'
),

trips as (
    select p.period, 'tlc.yellow=' || p.source_file_id || ';' || z.version as version
    from published as p
    cross join zones as z
    where p.dataset = 'tlc.yellow'
),

weather as (
    select period, 'openmeteo.era5=' || source_file_id as version
    from published
    where dataset = 'openmeteo.era5'
)

select
    coalesce(t.period, w.period) as period,
    t.version as trips_version,
    w.version as weather_version,
    -- Viagens x clima exige as duas fontes; a última hora usa os acumulados do mês seguinte.
    case when t.version is not null and w.version is not null then
        t.version || ';' || w.version || ';next:' || coalesce(nxt.version, 'none')
    end as trip_weather_version
from trips as t
full join weather as w on w.period = t.period
left join weather as nxt
    on nxt.period = to_char(to_date(coalesce(t.period, w.period), 'YYYY-MM')
        + interval '1 month', 'YYYY-MM')
