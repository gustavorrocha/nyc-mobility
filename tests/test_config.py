import pytest

from nyc_mobility.cli import main
from nyc_mobility.config import database_url


def test_database_url_from_env(monkeypatch):
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss/word")
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.delenv("POSTGRES_PORT", raising=False)
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    url = database_url()
    assert (url.host, url.port, url.database) == ("db", 5432, "nyc_mobility")
    assert (url.password, url.drivername) == ("p@ss/word", "postgresql+psycopg")
    assert url.query["connect_timeout"] == "10"


def test_database_url_requires_credentials(monkeypatch):
    monkeypatch.delenv("POSTGRES_USER", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="POSTGRES_USER, POSTGRES_PASSWORD"):
        database_url()


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "0.1.0"
