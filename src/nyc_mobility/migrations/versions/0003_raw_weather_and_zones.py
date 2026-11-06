"""Tabelas raw.openmeteo_weather_hourly e raw.tlc_taxi_zones; period opcional em execuções.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-06
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UPGRADE = [
    # Referências sem competência (ex.: Taxi Zones) registram execuções com period NULL.
    "ALTER TABLE metadata.ingestion_runs ALTER COLUMN period DROP NOT NULL",
    """
    CREATE TABLE raw.openmeteo_weather_hourly (
        source_file_id        bigint NOT NULL REFERENCES metadata.source_files,
        period                text NOT NULL,
        time_unix             bigint NOT NULL,
        latitude              double precision,
        longitude             double precision,
        temperature_2m        double precision,
        relative_humidity_2m  double precision,
        precipitation         double precision,
        rain                  double precision,
        snowfall              double precision,
        weather_code          integer,
        wind_speed_10m        double precision,
        PRIMARY KEY (source_file_id, time_unix)
    )
    """,
    "CREATE INDEX ON raw.openmeteo_weather_hourly (period)",
    """COMMENT ON COLUMN raw.openmeteo_weather_hourly.time_unix IS
       'Instante UTC (Unix) da hora. Acumulados (precipitation, rain, snowfall) referem-se ao
        intervalo (time_unix - 1h, time_unix]; demais variáveis são instantâneas.'""",
    "COMMENT ON COLUMN raw.openmeteo_weather_hourly.temperature_2m IS '°C'",
    "COMMENT ON COLUMN raw.openmeteo_weather_hourly.relative_humidity_2m IS 'Percentual'",
    "COMMENT ON COLUMN raw.openmeteo_weather_hourly.precipitation IS 'mm, soma da hora anterior'",
    "COMMENT ON COLUMN raw.openmeteo_weather_hourly.rain IS 'mm, soma da hora anterior'",
    "COMMENT ON COLUMN raw.openmeteo_weather_hourly.snowfall IS 'cm, soma da hora anterior'",
    "COMMENT ON COLUMN raw.openmeteo_weather_hourly.weather_code IS 'Código WMO'",
    "COMMENT ON COLUMN raw.openmeteo_weather_hourly.wind_speed_10m IS 'km/h'",
    """
    CREATE TABLE raw.tlc_taxi_zones (
        source_file_id  bigint NOT NULL REFERENCES metadata.source_files,
        row_number      bigint NOT NULL,
        locationid      integer NOT NULL,
        borough         text,
        zone            text,
        service_zone    text,
        PRIMARY KEY (source_file_id, row_number),
        UNIQUE (source_file_id, locationid)
    )
    """,
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE raw.tlc_taxi_zones")
    op.execute("DROP TABLE raw.openmeteo_weather_hourly")
    # Falha se já houver execuções sem competência, evitando perda silenciosa de registros.
    op.execute("ALTER TABLE metadata.ingestion_runs ALTER COLUMN period SET NOT NULL")
