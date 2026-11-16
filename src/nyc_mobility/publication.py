"""Publica apenas os modelos de consumo, na transação que conclui o build validado."""

import sqlalchemy as sa

TABLES = {
    "zones": "core",
    "service_types": "core",
    "trip_demand_hourly": "analytics",
    "trip_weather_hourly": "analytics",
    "trip_quality": "analytics",
    "data_coverage": "analytics",
}


def publish(conn, run_id: int) -> None:
    for table, schema in TABLES.items():
        conn.execute(
            sa.text(
                f"CREATE TABLE IF NOT EXISTS serving.{table} AS TABLE {schema}.{table} WITH NO DATA"
            )
        )
        if table in ("trip_demand_hourly", "trip_weather_hourly", "trip_quality"):
            conn.execute(
                sa.text(
                    f"CREATE INDEX IF NOT EXISTS {table}_period_idx ON serving.{table} (period)"
                )
            )
        # DELETE mantém versões MVCC para leitores em andamento; TRUNCATE não oferece isso.
        conn.execute(sa.text(f"DELETE FROM serving.{table}"))
        columns = conn.execute(
            sa.text(
                "SELECT column_name, udt_name, character_maximum_length, numeric_precision, "
                "numeric_scale, datetime_precision FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = :table ORDER BY ordinal_position"
            ),
            {"schema": schema, "table": table},
        ).all()
        published_columns = conn.execute(
            sa.text(
                "SELECT column_name, udt_name, character_maximum_length, numeric_precision, "
                "numeric_scale, datetime_precision FROM information_schema.columns "
                "WHERE table_schema = 'serving' AND table_name = :table ORDER BY ordinal_position"
            ),
            {"table": table},
        ).all()
        if columns != published_columns:
            raise ValueError(f"Schema de {table} mudou; migre a tabela serving antes de publicar")
        names = ", ".join('"' + column[0].replace('"', '""') + '"' for column in columns)
        conn.execute(
            sa.text(f"INSERT INTO serving.{table} ({names}) SELECT {names} FROM {schema}.{table}")
        )
        conn.execute(sa.text(f"GRANT SELECT ON serving.{table} TO dashboard_reader"))
    conn.execute(
        sa.text("""
        INSERT INTO metadata.analytics_publication (singleton, run_id, published_at)
        VALUES (true, :run_id, clock_timestamp())
        ON CONFLICT (singleton) DO UPDATE
        SET run_id = excluded.run_id, published_at = excluded.published_at
    """),
        {"run_id": run_id},
    )
