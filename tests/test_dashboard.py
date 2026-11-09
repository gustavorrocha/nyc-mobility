"""Dashboard: casca do app (sem banco) e acesso somente leitura (integração)."""

from datetime import date

import psycopg
import pytest
import sqlalchemy as sa

from dashboard import app as dash_app
from dashboard import db as dash_db

ZONES = [
    {"zone_id": 4, "zone": "Alphabet City", "borough": "Manhattan"},
    {"zone_id": 7, "zone": "Astoria", "borough": "Queens"},
    {"zone_id": 265, "zone": "Outside of NYC", "borough": "N/A"},
]


def test_layout_loads_filters_from_database(monkeypatch):
    answers = iter(
        [
            [{"first_day": date(2025, 1, 1), "last_day": date(2025, 2, 28)}],
            ZONES,
            [{"service_type": "yellow", "description": "Yellow Taxi"}],
        ]
    )
    monkeypatch.setattr(dash_app, "query", lambda *_: next(answers))
    tree = dash_app.layout()
    assert tree["f-period"].start_date == date(2025, 1, 1)
    assert tree["f-period"].end_date == date(2025, 2, 28)
    assert tree["f-borough"].options == ["Manhattan", "Queens"]
    assert getattr(tree["f-borough"], "value", None) is None  # padrão: NYC inteira
    assert tree["f-service"].value == ["yellow"]
    assert tree["zones"].data == ZONES


@pytest.mark.parametrize("path", list(dash_app.PAGES))
def test_route_renders_each_page_and_marks_nav(path):
    content, *nav, filters_hidden = dash_app.route(path)
    assert content[0].children == dash_app.PAGES[path][0]
    assert filters_hidden == (path == "/pipeline")  # metadados não usam os filtros globais
    assert nav == ["nav-link active" if p == path else "nav-link" for p in dash_app.PAGES]


def test_route_unknown_path():
    content, *nav, _ = dash_app.route("/nao-existe")
    assert content[0].children == "Página não encontrada"
    assert "nav-link active" not in nav


def test_zone_options_follow_borough():
    options, value = dash_app.zone_options(None, ZONES, [4, 7])
    assert [o["label"] for o in options] == [
        "Alphabet City (Manhattan)",
        "Astoria (Queens)",
        "Outside of NYC (N/A)",
    ]
    assert value == [4, 7]
    options, value = dash_app.zone_options("Queens", ZONES, [4, 7])
    assert options == [{"label": "Astoria", "value": 7}]
    assert value == [7]  # Alphabet City saiu do filtro com a troca de distrito


def test_dashboard_reader_can_only_read_allowed_schemas(engine):
    def as_reader(statement):
        with engine.connect() as conn:
            conn.execute(sa.text("SET ROLE dashboard_reader"))
            return conn.execute(sa.text(statement)).all()

    as_reader("SELECT count(*) FROM metadata.ingestion_runs")
    for statement in (
        "SELECT 1 FROM raw.tlc_yellow_trips",
        "INSERT INTO metadata.sources (source, dataset) VALUES ('x', 'y')",
        "DELETE FROM metadata.ingestion_runs",
    ):
        with pytest.raises(sa.exc.ProgrammingError, match="permission denied"):
            as_reader(statement)
    with engine.connect() as conn:
        config = conn.scalar(
            sa.text("SELECT rolconfig FROM pg_roles WHERE rolname = 'dashboard_reader'")
        )
    assert "default_transaction_read_only=on" in config


def test_dashboard_connection_is_read_only(engine, monkeypatch):
    # Mesmas credenciais do teste (superusuário): a sessão continua somente leitura.
    monkeypatch.setenv("POSTGRES_DB", engine.url.database)
    monkeypatch.setenv("DASHBOARD_DB_USER", engine.url.username)
    monkeypatch.setenv("DASHBOARD_DB_PASSWORD", engine.url.password)
    assert dash_db.query("SELECT current_setting('transaction_read_only') AS ro") == [{"ro": "on"}]
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        dash_db.query("CREATE TEMP TABLE t (x int)")


def test_dashboard_requires_password(monkeypatch):
    monkeypatch.delenv("DASHBOARD_DB_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="DASHBOARD_DB_PASSWORD"):
        dash_db.connect()


def test_layout_reports_database_unavailable(monkeypatch):
    monkeypatch.delenv("DASHBOARD_DB_PASSWORD", raising=False)
    tree = dash_app.layout()
    assert tree.children[0].children == "Banco de dados indisponível"
