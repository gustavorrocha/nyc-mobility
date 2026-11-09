-- Chaves compostas: uma corrida por registro de origem; uma linha por hora/modalidade/zona.
select 'trips' as model, count(*) as duplicates
from {{ ref('trips') }}
group by service_type, source_file_id, source_row_number
having count(*) > 1

union all

select 'trip_demand_hourly', count(*)
from {{ ref('trip_demand_hourly') }}
group by hour_utc, service_type, pickup_zone_id
having count(*) > 1
