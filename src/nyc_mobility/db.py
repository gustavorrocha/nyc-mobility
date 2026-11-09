import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from nyc_mobility.config import database_url

SCHEMAS = ("raw", "core", "analytics", "metadata")


def _config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", "nyc_mobility:migrations")
    return cfg


def upgrade() -> None:
    """Aplica as migrações Alembic até a revisão mais recente (idempotente)."""
    command.upgrade(_config(), "head")


def downgrade(revision: str) -> None:
    """Reverte as migrações até `revision` (usado nos testes de reversibilidade)."""
    command.downgrade(_config(), revision)


def missing_schemas() -> list[str]:
    engine = sa.create_engine(database_url(), poolclass=sa.pool.NullPool)
    with engine.connect() as conn:
        found = set(conn.scalars(sa.text("SELECT schema_name FROM information_schema.schemata")))
    return [s for s in SCHEMAS if s not in found]
