FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON=/usr/local/bin/python \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app
# Só o grupo `dashboard` do uv.lock (Dash, Plotly, psycopg); o pacote do pipeline não entra.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --only-group dashboard \
 && rm -rf /root/.cache/uv

COPY dashboard ./dashboard
ENV DASHBOARD_HOST=0.0.0.0
USER nobody
EXPOSE 8050
CMD ["python", "-m", "dashboard"]
