"""Tabelas raw.tlc_green_trips, raw.tlc_fhv_trips e raw.tlc_fhvhv_trips.

Mesmo padrão de raw.tlc_yellow_trips: campos técnicos (o ID da corrida é source_file_id +
row_number) e os campos da origem com o nome da TLC em minúsculas; timestamps em horário local
de NY, sem fuso, exatamente como publicados.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-11
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TECHNICAL = """
        source_file_id         bigint NOT NULL REFERENCES metadata.source_files,
        period                 text NOT NULL,
        row_number             bigint NOT NULL,"""

TABLES = {
    "tlc_green_trips": """
        vendorid               integer,
        lpep_pickup_datetime   timestamp,
        lpep_dropoff_datetime  timestamp,
        store_and_fwd_flag     text,
        ratecodeid             bigint,
        pulocationid           integer,
        dolocationid           integer,
        passenger_count        bigint,
        trip_distance          double precision,
        fare_amount            double precision,
        extra                  double precision,
        mta_tax                double precision,
        tip_amount             double precision,
        tolls_amount           double precision,
        ehail_fee              double precision,
        improvement_surcharge  double precision,
        total_amount           double precision,
        payment_type           bigint,
        trip_type              bigint,
        congestion_surcharge   double precision,
        cbd_congestion_fee     double precision,""",
    # FHV tradicional: sem distância nem valores; zonas opcionais na origem.
    "tlc_fhv_trips": """
        dispatching_base_num    text,
        pickup_datetime         timestamp,
        dropoff_datetime        timestamp,
        pulocationid            integer,
        dolocationid            integer,
        sr_flag                 bigint,
        affiliated_base_number  text,""",
    # HVFHV (aplicativos): distância em trip_miles; valor pago em componentes.
    "tlc_fhvhv_trips": """
        hvfhs_license_num      text,
        dispatching_base_num   text,
        originating_base_num   text,
        request_datetime       timestamp,
        on_scene_datetime      timestamp,
        pickup_datetime        timestamp,
        dropoff_datetime       timestamp,
        pulocationid           integer,
        dolocationid           integer,
        trip_miles             double precision,
        trip_time              bigint,
        base_passenger_fare    double precision,
        tolls                  double precision,
        bcf                    double precision,
        sales_tax              double precision,
        congestion_surcharge   double precision,
        airport_fee            double precision,
        tips                   double precision,
        driver_pay             double precision,
        shared_request_flag    text,
        shared_match_flag      text,
        access_a_ride_flag     text,
        wav_request_flag       text,
        wav_match_flag         text,
        cbd_congestion_fee     double precision,""",
}


def upgrade() -> None:
    for table, columns in TABLES.items():
        op.execute(
            f"CREATE TABLE raw.{table} ({TECHNICAL}{columns}\n"
            "        PRIMARY KEY (source_file_id, row_number)\n    )"
        )
        op.execute(f"CREATE INDEX ON raw.{table} (period)")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE raw.{table}")
