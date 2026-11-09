FROM apache/airflow:3.4.0-python3.12

USER root
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /usr/local/bin/uv
ENV UV_PYTHON=/usr/local/bin/python \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /opt/nyc_mobility
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# Dois virtualenvs isolados do ambiente do Airflow, ambos fixados pelo uv.lock:
#  - /opt/venvs/app: pacote nyc_mobility (CLI de ingestão e migrações)
#  - /opt/venvs/dbt: dbt-core + dbt-postgres
RUN UV_PROJECT_ENVIRONMENT=/opt/venvs/app uv sync --frozen --no-default-groups --no-editable \
 && UV_PROJECT_ENVIRONMENT=/opt/venvs/dbt uv sync --frozen --no-default-groups --group dbt --no-install-project \
 && ln -s /opt/venvs/app/bin/nyc-mobility /opt/venvs/dbt/bin/dbt /usr/local/bin/ \
 && rm -rf /root/.cache/uv

COPY --chown=airflow:0 dbt ./dbt
ENV DBT_PROFILES_DIR=/opt/nyc_mobility/dbt \
    DBT_PROJECT_DIR=/opt/nyc_mobility/dbt

USER airflow
WORKDIR /opt/airflow
