select
    locationid as zone_id,
    nullif(borough, '') as borough,
    nullif(zone, '') as zone,
    nullif(service_zone, '') as service_zone,
    source_file_id
from {{ source('raw', 'tlc_taxi_zones') }}
