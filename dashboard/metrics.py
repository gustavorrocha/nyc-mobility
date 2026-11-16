"""Exporta a data da última publicação validada para o Prometheus."""

import logging
from http.server import BaseHTTPRequestHandler, HTTPServer

from dashboard.db import query

METRIC = "nyc_mobility_publication_timestamp_seconds"


def metrics() -> str:
    rows = query("SELECT published_at FROM metadata.analytics_publication")
    result = (
        f"# HELP {METRIC} Unix timestamp da última publicação validada.\n# TYPE {METRIC} gauge\n"
    )
    if rows:
        result += f"{METRIC} {rows[0]['published_at'].timestamp()}\n"
    return result


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_error(404)
            return
        try:
            body = metrics().encode("utf-8")
        except Exception:
            logging.exception("Falha ao consultar publicação")
            self.send_error(503, "Banco indisponível")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 9101), Handler).serve_forever()
