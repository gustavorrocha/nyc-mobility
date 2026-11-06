"""Tabela raw.tlc_yellow_trips.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-06
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE = [
    """
    CREATE TABLE raw.tlc_yellow_trips (
        -- Campos técnicos: o ID da corrida é (source_file_id, row_number), rastreável à
        -- versão do arquivo e à posição do registro na origem.
        source_file_id         bigint NOT NULL REFERENCES metadata.source_files,
        period                 text NOT NULL,
        row_number             bigint NOT NULL,
        -- Campos da origem (nomes da TLC em minúsculas); timestamps em horário local de NY,
        -- sem fuso, exatamente como publicados.
        vendorid               integer,
        tpep_pickup_datetime   timestamp,
        tpep_dropoff_datetime  timestamp,
        passenger_count        bigint,
        trip_distance          double precision,
        ratecodeid             bigint,
        store_and_fwd_flag     text,
        pulocationid           integer,
        dolocationid           integer,
        payment_type           bigint,
        fare_amount            double precision,
        extra                  double precision,
        mta_tax                double precision,
        tip_amount             double precision,
        tolls_amount           double precision,
        improvement_surcharge  double precision,
        total_amount           double precision,
        congestion_surcharge   double precision,
        airport_fee            double precision,
        cbd_congestion_fee     double precision,
        PRIMARY KEY (source_file_id, row_number)
    )
    """,
    "CREATE INDEX ON raw.tlc_yellow_trips (period)",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE raw.tlc_yellow_trips")
