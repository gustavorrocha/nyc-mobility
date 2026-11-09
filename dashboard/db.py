"""Acesso somente leitura ao PostgreSQL (usuário `dashboard_reader`, migração Alembic 0004)."""

import os

import psycopg
from psycopg.rows import dict_row


def connect() -> psycopg.Connection:
    """Conexão de leitura: privilégios só de SELECT e transações read-only por padrão."""
    password = os.environ.get("DASHBOARD_DB_PASSWORD")
    if not password:
        raise RuntimeError(
            "Variável DASHBOARD_DB_PASSWORD ausente. No host: uv run --env-file .env python -m "
            "dashboard (a senha é aplicada ao usuário por `nyc-mobility db upgrade`)."
        )
    return psycopg.connect(
        # 127.0.0.1 em vez de localhost: no Windows, ::1 pode aceitar a conexão sem responder.
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "nyc_mobility"),
        user=os.environ.get("DASHBOARD_DB_USER", "dashboard_reader"),
        password=password,
        connect_timeout=10,
        options="-c default_transaction_read_only=on",
        row_factory=dict_row,
    )


def query(sql: str, params: dict | None = None) -> list[dict]:
    """Executa uma consulta e devolve as linhas como dicionários."""
    with connect() as conn:
        return conn.execute(sql, params).fetchall()
