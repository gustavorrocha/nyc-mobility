-- Nenhuma corrida some entre raw e core: contagens por competência devem bater.
with raw_counts as (
    select period, count(*) as n from {{ source('raw', 'tlc_yellow_trips') }} group by 1
),

core_counts as (
    select period, count(*) as n from {{ ref('trips') }} where service_type = 'yellow' group by 1
)

select *
from raw_counts as r
full join core_counts as c using (period)
where r.n is distinct from c.n
