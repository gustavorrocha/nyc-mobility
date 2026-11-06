# nyc-mobility

Pipeline local de dados de mobilidade de NYC (TLC + Open-Meteo). Arquitetura e plano de
implementação: [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Estrutura

| Caminho | Conteúdo |
|---|---|
| `src/nyc_mobility/` | Pacote Python e CLI `nyc-mobility` |
| `src/nyc_mobility/migrations/` | Migrações Alembic (schemas `raw`, `core`, `analytics`, `metadata`) |
| `dbt/` | Projeto dbt (`core` e `analytics`) |
| `dags/` | DAGs do Airflow |
| `docker/` | Imagem do Airflow (com virtualenvs isolados para o pacote e o dbt) e init do PostgreSQL |
| `data/landing/`, `data/logs/` | Arquivos originais e logs do Airflow (bind mounts, fora do Git) |

## Pré-requisitos

Docker com Compose v2, [uv](https://docs.astral.sh/uv/) e Python 3.12.

## Subir o ambiente local

```bash
cp .env.example .env        # edite as senhas/segredos
docker compose up -d --build --wait airflow-apiserver airflow-scheduler airflow-dag-processor
```

O `up` sobe o PostgreSQL 17 (bancos `nyc_mobility` e `airflow_metadata`), aplica as migrações
do Airflow (`airflow-init`) e do projeto (`migrate`) e inicia o Airflow 3.4 com `LocalExecutor`.

- UI do Airflow: <http://127.0.0.1:8080> (sem login; somente para desenvolvimento local).
- PostgreSQL: `127.0.0.1:5432` (porta configurável por `POSTGRES_HOST_PORT`).

## Comandos de validação

```bash
# Serviços e saúde
docker compose ps -a
curl http://127.0.0.1:8080/api/v2/version

# Migrações (idempotentes) e verificação de schemas
docker compose run --rm migrate
docker compose exec airflow-scheduler nyc-mobility db check

# dbt (virtualenv isolado do Airflow)
docker compose exec airflow-scheduler dbt debug

# Desenvolvimento no host
uv sync
uv run ruff check . && uv run ruff format --check .
uv run --env-file .env pytest            # testes de integração são pulados sem PostgreSQL
uv run --env-file .env nyc-mobility db upgrade
uv run --env-file .env dbt debug --project-dir dbt --profiles-dir dbt
```

## Ingestão

```bash
# No container (mesmo código usado pelo Airflow) ou no host com `uv run --env-file .env`
docker compose exec airflow-scheduler nyc-mobility ingest --dataset tlc.yellow --period 2025-01
docker compose exec airflow-scheduler nyc-mobility ingest --dataset tlc.yellow --period 2025-01 --force
docker compose exec airflow-scheduler nyc-mobility ingest --dataset openmeteo.era5 --period 2025-01
docker compose exec airflow-scheduler nyc-mobility ingest --dataset tlc.taxi_zones   # sem competência
```

| Dataset | Tabela raw | Observações |
|---|---|---|
| `tlc.yellow` | `raw.tlc_yellow_trips` | Timestamps locais de NY, sem fuso, como publicados |
| `openmeteo.era5` | `raw.openmeteo_weather_hourly` | `time_unix` em UTC; mês local de NY (DST incluído); acumulados (`precipitation`, `rain`, `snowfall`) referem-se à hora anterior |
| `tlc.taxi_zones` | `raw.tlc_taxi_zones` | Referência versionada: só recarrega quando o hash muda |

- O arquivo original fica em `data/landing/<fonte>/<dataset>/[AAAA-MM/]<sha256[:16]>/`; versões
  republicadas coexistem, nada é sobrescrito. Na Open-Meteo, a versão é o hash do conteúdo
  canônico (sem `generationtime_ms`, que muda a cada chamada); o hash do arquivo bruto fica em
  `acquisition_params.file_sha256`.
- Mesmo hash já publicado → execução registrada como `skipped`. Hash novo ou `--force` →
  staging temporário e substituição da competência em `raw.tlc_yellow_trips` numa transação.
- Cargas simultâneas da mesma fonte/competência são recusadas (advisory lock no PostgreSQL).
- Execuções, contagens, registros sinalizados por motivo e erros ficam em
  `metadata.ingestion_runs`; arquivos e hashes em `metadata.source_files`.
- Os testes de integração criam e recriam o banco descartável `nyc_mobility_test`.

Parar o ambiente: `docker compose down` (preserva o volume `postgres-data`; **não** use `-v`
a menos que queira apagar os bancos).

## Observações

- O repositório pode estar em uma pasta sincronizada (ex.: OneDrive). Recomenda-se excluir
  `data/` da sincronização, pois a landing e os logs podem ficar grandes.
- `/api/v2/monitor/health` reporta o `triggerer` como `down`: o serviço não é usado no MVP
  (não há operadores deferrable).
