"""Usuário somente leitura do dashboard (dashboard_reader).

Acesso: leitura em `metadata` (aqui) e nos modelos dbt liberados por `+grants` em
`dbt_project.yml` (`analytics`, `core.zones`, `core.service_types`). A senha vem de
DASHBOARD_DB_PASSWORD, aplicada a cada `nyc-mobility db upgrade` (sem senha, não há login).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-09
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UPGRADE = [
    # Papéis são do servidor, não do banco: pode já existir (ex.: outro banco de teste).
    """
    DO $$ BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dashboard_reader') THEN
            CREATE ROLE dashboard_reader LOGIN;
        END IF;
    END $$
    """,
    "ALTER ROLE dashboard_reader SET default_transaction_read_only = on",
    "GRANT USAGE ON SCHEMA core, analytics, metadata TO dashboard_reader",
    "GRANT SELECT ON ALL TABLES IN SCHEMA metadata TO dashboard_reader",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    # Remove só os privilégios neste banco (inclusive os concedidos pelo dbt). O papel não é
    # excluído: é global ao servidor e pode ter privilégios em outros bancos.
    op.execute("DROP OWNED BY dashboard_reader")
