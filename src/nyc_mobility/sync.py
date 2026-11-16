"""Descoberta de publicações e backfill limitado, reutilizando ingestão e build rastreados."""

import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from nyc_mobility.build import INPUT_VERSIONS, build
from nyc_mobility.common import period_bounds
from nyc_mobility.config import database_url
from nyc_mobility.ingest import ADAPTERS, ingest

CATALOGUE_URL = "https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page"


class _Catalogue(HTMLParser):
    def __init__(self):
        super().__init__()
        self.files = set()

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        match = re.search(
            r"/(yellow|green|fhv|fhvhv)_tripdata_(\d{4}-\d{2})\.parquet$",
            dict(attrs).get("href") or "",
        )
        if match:
            self.files.add(("tlc." + match[1], match[2]))


def catalogue() -> set[tuple[str, str]]:
    with urlopen(
        Request(CATALOGUE_URL, headers={"User-Agent": "nyc-mobility"}), timeout=30
    ) as response:
        parser = _Catalogue()
        parser.feed(response.read().decode("utf-8"))
    if not parser.files:
        raise ValueError("Catálogo TLC sem links reconhecidos; conferir alteração do site")
    return parser.files


def periods(start: str, end: str, today: date | None = None) -> list[str]:
    current = (today or datetime.now(ZoneInfo("America/New_York")).date()).replace(day=1)
    first, _ = period_bounds(start)
    last, _ = period_bounds(end)
    if first > last or last.date() >= current:
        raise ValueError("Intervalo deve ser crescente e conter apenas meses completos")
    result = []
    while first <= last:
        result.append(first.strftime("%Y-%m"))
        _, first = period_bounds(result[-1])
    return result


def changed(url: str, previous: dict | None) -> bool | None:
    """True: conferir via ingestão; False: inalterado; None: ainda não publicado (404)."""
    previous = previous or {}
    if previous.get("url", url) != url:
        previous = {}
    headers = {"User-Agent": "nyc-mobility"}
    for old, conditional in (("ETag", "If-None-Match"), ("Last-Modified", "If-Modified-Since")):
        if previous.get(old):
            headers[conditional] = previous[old]
    try:
        with urlopen(Request(url, headers=headers, method="HEAD"), timeout=30) as response:
            for name in ("ETag", "Last-Modified"):
                if previous.get(name) and response.headers.get(name):
                    return previous[name] != response.headers[name]
            return True
    except HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code == 304 and previous:
            return False
        raise  # 403, 429, 5xx e falhas de rede são falhas operacionais, nunca ausência normal


def loaded() -> dict:
    engine = sa.create_engine(database_url(), poolclass=sa.pool.NullPool)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text("""
                SELECT DISTINCT ON (s.source, s.dataset, r.period)
                    s.source || '.' || s.dataset AS dataset, r.period, f.acquisition_params
                FROM metadata.ingestion_runs r JOIN metadata.sources s USING (source_id)
                JOIN metadata.source_files f USING (source_file_id)
                WHERE r.status = 'succeeded'
                ORDER BY s.source, s.dataset, r.period, r.run_id DESC
            """)
            )
            return {(r.dataset, r.period): r.acquisition_params for r in rows}
    finally:
        engine.dispose()


def plan(start: str, end: str | None, *, include_hvfhv: bool = False) -> dict:
    if end is None:
        today = datetime.now(ZoneInfo("America/New_York")).date()
        end = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    months = periods(start, end)
    previous = loaded()
    available = catalogue()
    datasets = ["tlc.yellow", "tlc.green", "tlc.fhv"]
    if include_hvfhv:
        datasets.append("tlc.fhvhv")
    result = {"start_period": start, "end_period": end, "ready": [], "waiting": [], "unchanged": []}
    for period in months:
        missing, updates = [], []
        for dataset in datasets:
            if (dataset, period) not in available:
                missing.append(dataset)
                continue
            status = changed(ADAPTERS[dataset].url(period), previous.get((dataset, period)))
            if status is None:
                missing.append(dataset)
            elif status:
                updates.append(dataset)
        if missing:
            result["waiting"].append({"period": period, "datasets": missing})
        elif updates or ("openmeteo.era5", period) not in previous:
            result["ready"].append({"period": period, "datasets": updates})
        else:
            result["unchanged"].append(period)
    return result


def needs_build() -> bool:
    engine = sa.create_engine(database_url(), poolclass=sa.pool.NullPool)
    try:
        with engine.connect() as conn:
            has_period = conn.scalar(
                sa.text(
                    "SELECT EXISTS (SELECT FROM metadata.ingestion_runs "
                    "WHERE status = 'succeeded' AND period IS NOT NULL)"
                )
            )
            published = conn.scalar(
                sa.text(
                    "SELECT r.input_versions FROM metadata.analytics_publication p "
                    "JOIN metadata.analytics_runs r USING (run_id)"
                )
            )
            latest = conn.scalar(
                sa.text("SELECT status FROM metadata.analytics_runs ORDER BY run_id DESC LIMIT 1")
            )
            return bool(
                has_period
                and (published != conn.scalar(sa.text(INPUT_VERSIONS)) or latest == "failed")
            )
    finally:
        engine.dispose()


def synchronize(
    start: str,
    end: str | None,
    *,
    include_hvfhv: bool = False,
    max_periods: int = 1,
    dry_run: bool = False,
) -> dict:
    if max_periods < 1:
        raise ValueError("max-periods deve ser positivo")
    discovery = plan(start, end, include_hvfhv=include_hvfhv)
    result = {"plan": discovery, "processed": [], "build": None}
    if dry_run:
        return result
    ingest("tlc.taxi_zones")
    for item in discovery["ready"][:max_periods]:
        period = item["period"]
        runs = [ingest(dataset, period) for dataset in item["datasets"]]
        runs.append(ingest("openmeteo.era5", period))
        result["processed"].append({"period": period, "runs": runs})
    if needs_build():
        result["build"] = build()
    return result
