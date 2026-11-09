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

## CI

`.github/workflows/ci.yml` roda em PRs e em pushes na `main`: Ruff, Pytest com PostgreSQL 17
(service container; no CI os testes de integração falham em vez de serem pulados), `dbt build`
sobre fixtures (dentro do Pytest), migrações + `db check` e build da imagem do Airflow (sem push
nem deploy).

## Transformação (dbt)

```bash
docker compose exec airflow-scheduler dbt build        # ou, no host:
uv run --env-file .env dbt build --project-dir dbt --profiles-dir dbt
```

| Modelo | Grão | Observações |
|---|---|---|
| `core.zones` | zona | Cadastro TLC; strings vazias viram `NULL` |
| `core.service_types` | modalidade | Seed; no MVP, apenas `yellow` |
| `core.trips` | corrida | Uma linha por registro de `raw` (nada é excluído); `is_valid` e `invalid_reason` (`missing_required_value`, `dropoff_before_pickup`, `negative_distance`, `pickup_outside_period`, `unknown_zone`); horários locais originais e em UTC |
| `core.weather_hourly` | hora UTC | Semântica da fonte: acumulados referem-se à hora anterior a `observed_at_utc` |
| `analytics.trip_demand_hourly` | hora × modalidade × zona de embarque | Apenas corridas válidas |
| `analytics.trip_weather_hourly` | hora `[hour_utc, +1h)` | Viagens válidas e clima da hora, inclusive horas sem corrida; instantâneas no início da hora, acumulados da observação seguinte (`NULL` na última hora sem a competência seguinte) |

- **Incremental por competência** nos modelos transacionais (`core.trips`,
  `core.weather_hourly`, `analytics.*`); cadastros (`zones`, `service_types`) são reconstruídos
  inteiros. Cada linha guarda em `source_version` a versão dos insumos da sua competência
  (arquivo publicado em `metadata.ingestion_runs`, cadastro de zonas e, no viagens x clima, o
  clima do mês seguinte). Um `dbt build` reprocessa (delete+insert por `period`) só as
  competências cuja versão mudou: mês republicado → só ele; nova versão de zonas → todas as
  viagens; clima do mês seguinte → o viagens x clima do mês anterior.
- `dbt build --full-refresh` reconstrói tudo (necessário após mudar a estrutura de um modelo).
- Desvio registrado do `ARCHITECTURE.md` (item 5.3 previa tabelas reconstruídas no MVP):
  incremental adotado por decisão do projeto. Os testes dbt ainda varrem as tabelas inteiras.
- Testes garantem que `core.trips` concilia com `raw` por competência.
- Limitação aceita: na volta do horário de verão, 01:00–01:59 local ocorre duas vezes e a TLC
  não informa o fuso; a conversão para UTC escolhe sempre a mesma ocorrência.

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
