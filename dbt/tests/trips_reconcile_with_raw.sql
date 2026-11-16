-- Nenhuma corrida some entre raw e core: contagens por competência e modalidade devem bater.
with raw_counts as (
    select period, 'yellow' as service_type, count(*) as n
    from {{ source('raw', 'tlc_yellow_trips') }} group by 1
    union all
    select period, 'green', count(*) from {{ source('raw', 'tlc_green_trips') }} group by 1
    union all
    select period, 'fhv', count(*) from {{ source('raw', 'tlc_fhv_trips') }} group by 1
    union all
    select period, 'fhvhv', count(*) from {{ source('raw', 'tlc_fhvhv_trips') }} group by 1
),

core_counts as (
    select period, service_type, count(*) as n from {{ ref('trips') }} group by 1, 2
)

select *
from raw_counts as r
full join core_counts as c using (period, service_type)
where r.n is distinct from c.n
