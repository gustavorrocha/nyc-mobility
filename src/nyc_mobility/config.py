import os
from pathlib import Path

from sqlalchemy import URL


def landing_dir() -> Path:
    """Raiz da landing zone (bind mount `./data/landing` no Docker)."""
    return Path(os.environ.get("NYC_MOBILITY_LANDING_DIR", "data/landing")).resolve()


def database_url() -> URL:
    """URL do banco `nyc_mobility`, montada a partir das variáveis POSTGRES_*."""
    missing = [v for v in ("POSTGRES_USER", "POSTGRES_PASSWORD") if not os.environ.get(v)]
    if missing:
        raise RuntimeError(f"Variáveis de ambiente ausentes: {', '.join(missing)}")
    return URL.create(
        "postgresql+psycopg",
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        # 127.0.0.1 em vez de localhost: no Windows, ::1 pode aceitar a conexão sem responder.
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        database=os.environ.get("POSTGRES_DB", "nyc_mobility"),
        query={"connect_timeout": "10"},
    )
