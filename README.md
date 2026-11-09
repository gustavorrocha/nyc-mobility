# nyc-mobility

Pipeline local de dados de mobilidade de NYC: corridas de táxi da TLC e clima horário da
Open-Meteo, ingeridos por uma CLI Python, transformados com dbt e orquestrados pelo Airflow,
tudo sobre PostgreSQL em Docker Compose. Arquitetura e decisões: [`ARCHITECTURE.md`](ARCHITECTURE.md).

```text
TLC / Open-Meteo ─> landing (arquivo original, por hash) ─> raw ─> core ─> analytics
                    └──────── CLI nyc-mobility ────────┘    └──── dbt ────┘
                                  orquestrado pela DAG monthly_pipeline (Airflow)
```

## Estrutura

| Caminho | Conteúdo |
|---|---|
| `src/nyc_mobility/` | Pacote Python e CLI `nyc-mobility` (um adaptador por fonte) |
| `src/nyc_mobility/migrations/` | Migrações Alembic (schemas `raw`, `core`, `analytics`, `metadata`) |
| `dbt/` | Projeto dbt (`core` e `analytics`) |
| `dags/` | DAG `monthly_pipeline` |
| `docker/` | Imagem do Airflow (virtualenvs isolados para o pacote e o dbt) e init do PostgreSQL |
| `tests/` | Pytest: unidade, integração com PostgreSQL e `dbt build` sobre fixtures |
| `data/landing/`, `data/logs/` | Arquivos originais e logs do Airflow (bind mounts, fora do Git) |

## Demonstração rápida

Pré-requisitos: Docker com Compose v2. Para desenvolver no host: [uv](https://docs.astral.sh/uv/)
e Python 3.12.

```bash
# 1. Configuração local (edite as senhas/segredos; o .env não é versionado)
cp .env.example .env

# 2. PostgreSQL 17 + migrações + Airflow 3.4 (LocalExecutor)
docker compose up -d --build --wait airflow-apiserver airflow-scheduler airflow-dag-processor

# 3. Pipeline de janeiro/2025: zonas, corridas (~3,5 mi, ~60 MB) e clima; depois dbt build
docker compose exec airflow-scheduler airflow dags trigger monthly_pipeline -c '{"period": "2025-01"}'

# 4. Acompanhe em http://127.0.0.1:8080 → monthly_pipeline (sem login; só uso local)

# 5. Consulte os resultados (ou conecte um cliente SQL em 127.0.0.1:5432)
#    Portas configuráveis no .env: AIRFLOW_HOST_PORT e POSTGRES_HOST_PORT
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d nyc_mobility'
```

Consultas de exemplo:

```sql
-- Zonas com mais embarques no mês
select z.borough, z.zone, sum(d.trip_count) as trips
from analytics.trip_demand_hourly d
join core.zones z on z.zone_id = d.pickup_zone_id
where d.period = '2025-01'
group by 1, 2 order by trips desc limit 5;

-- Média de corridas por hora, com e sem neve
select snowfall_cm > 0 as snowing, count(*) as hours, round(avg(trip_count)) as avg_trips_per_hour
from analytics.trip_weather_hourly
where snowfall_cm is not null
group by 1;

-- Qualidade: corridas preservadas em core, mas fora das métricas, por motivo
select period, invalid_reason, count(*)
from core.trips where not is_valid
group by 1, 2 order by 1, 3 desc;
```

Parar: `docker compose down` (preserva o volume `postgres-data`; **não** use `-v` a menos que
queira apagar os bancos).

## Orquestração (Airflow)

DAG `monthly_pipeline` (`dags/monthly_pipeline.py`), disparada manualmente com a competência
(`period`, `AAAA-MM`) e, opcionalmente, `force`:

```text
tlc_taxi_zones ─┐
tlc_yellow ─────┼─> dbt_build ─> summary
openmeteo_era5 ─┘
```

- Ingestões em paralelo via CLI `nyc-mobility ingest` (processos próprios, argumentos em lista,
  sem shell), com 3 retries e backoff exponencial para falhas transitórias; o resumo JSON de
  cada uma vai para o XCom e é consolidado em `summary`.
- Erro de contrato (arquivo fora do esperado; CLI sai com código 3) falha a tarefa **sem
  retry**, já que repetir baixaria o mesmo arquivo.
- `dbt_build` só roda se as três fontes concluírem (`succeeded` ou `skipped`); sem retry.
  Para reexecutar só as transformações: *Clear* em `dbt_build` com *Downstream* marcado.
- `force=true` recarrega Yellow e clima mesmo sem hash novo (zonas nunca são forçadas).
- Uma execução ativa por vez; cargas concorrentes da mesma fonte/competência também são
  barradas no PostgreSQL (advisory lock), inclusive fora do Airflow. A recusa fica registrada em
  `metadata.ingestion_runs` como `failed` (motivo `carga concorrente`, sem arquivo associado) e
  a tarefa tenta de novo pelos retries.
- Linhas inseridas por modelo: log de `dbt_build` (`INSERT 0 N`).

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
- **Download condicional:** quando a versão publicada tem `ETag`/`Last-Modified` (CloudFront da
  TLC), a requisição é condicional; se a origem responder 304, a execução é `skipped` sem baixar
  o arquivo. A Open-Meteo não envia esses cabeçalhos e é sempre baixada (JSON pequeno).
- Mesmo hash já publicado → execução registrada como `skipped`. Hash novo ou `--force` →
  validação e substituição da competência (ou do cadastro inteiro, nas zonas) em `raw` numa
  transação, com staging temporário no Yellow; falhas preservam a versão anterior.
- Códigos de saída da CLI: `0` sucesso (inclusive `skipped`), `3` erro de contrato (arquivo
  fora do esperado; não adianta repetir), `2` uso inválido, `1` demais erros (rede, carga
  concorrente etc.).
- Execuções, contagens, registros sinalizados por motivo e erros ficam em
  `metadata.ingestion_runs`; arquivos e hashes em `metadata.source_files`.

## Transformação (dbt)

```bash
docker compose exec airflow-scheduler dbt build        # ou, no host:
uv run --env-file .env dbt build --project-dir dbt --profiles-dir dbt
```

| Modelo | Grão | Observações |
|---|---|---|
| `core.zones` | zona | Cadastro TLC; strings vazias viram `NULL` |
| `core.service_types` | modalidade | Seed; no MVP, apenas `yellow` |
| `core.trips` | corrida | Uma linha por registro de `raw` (nada é excluído); `is_valid` e `invalid_reason` (`missing_required_value`, `nonexistent_local_time`, `dropoff_before_pickup`, `negative_distance`, `pickup_outside_period`, `unknown_zone`); horários locais originais e em UTC; `local_time_issue` nas mudanças de horário de verão |
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
- **Horário de verão:** a TLC publica horário local sem fuso, então `core.trips` trata
  explicitamente as transições (`local_time_issue`):
  - `nonexistent` (02:00–02:59 no início do horário de verão): o horário não existe; a corrida
    fica inválida (`nonexistent_local_time`) e não é convertida para UTC.
  - `ambiguous` (01:00–01:59 na volta, que ocorre duas vezes): escolhe-se a combinação de
    ocorrências de embarque e desembarque com menor duração não negativa; no empate (ambos na
    hora repetida), a primeira ocorrência (EDT). A ordem embarque/desembarque é avaliada em UTC,
    então uma corrida das 01:50 (EDT) às 01:10 (EST) é válida, com 20 min; na ingestão, que
    compara o horário local, ela aparece como `dropoff_before_pickup`.
- `dbt build --full-refresh` reconstrói tudo (necessário após mudar a estrutura de um modelo).
- Testes dbt: unicidade, não nulos, integridade referencial (corridas válidas), valores aceitos
  e conciliação `core.trips` = `raw` por competência.

## Desenvolvimento e testes

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run --env-file .env pytest -rs       # integração pulada sem PostgreSQL (exceto no CI)
uv run --env-file .env nyc-mobility db upgrade
uv run --env-file .env nyc-mobility db check
```

- Os testes não acessam as APIs públicas: usam Parquet/JSON/CSV gerados em fixtures, servidos
  por `file://` ou por um servidor HTTP local (para as respostas 200/304 do download condicional).
- Os testes de integração usam bancos descartáveis (`nyc_mobility_test`,
  `nyc_mobility_test_migrations`), recriados a cada execução; o banco de desenvolvimento não é
  tocado. Cobrem ingestão, idempotência, download condicional, concorrência, migrações (ida e
  volta), horário de verão e `dbt build` incremental.
- **CI** (`.github/workflows/ci.yml`, em PRs e pushes na `main`): Ruff; Pytest com PostgreSQL 17
  (no CI, a ausência do banco falha em vez de pular); migrações + `db check`; build da imagem do
  Airflow e verificação da estrutura da DAG dentro dela. Sem publicação de imagem nem deploy.

## Solução de problemas

- **`Variáveis de ambiente ausentes: POSTGRES_USER...`** no host: use `uv run --env-file .env ...`
  (no container as variáveis já vêm do Compose).
- **Conexão travando no Windows:** use `127.0.0.1` (padrão do projeto), não `localhost`; o
  Docker Desktop pode aceitar `::1` sem responder.
- **Mudou algo em `dbt/` ou `src/`:** reconstrua a imagem (`docker compose up -d --build ...`);
  se mudou a estrutura de um modelo, rode `dbt build --full-refresh` uma vez.
- **DAG nova não aparece:** aguarde o `dag-processor` ou force com
  `docker compose exec airflow-dag-processor airflow dags reserialize`.
- **Pasta sincronizada (ex.: OneDrive):** exclua `data/` e `.venv/` da sincronização; arquivos
  "somente online" podem causar `Access is denied` no `uv sync`.

## Limitações conhecidas

- **Horário de verão:** corridas ambíguas em que as duas ocorrências são igualmente plausíveis
  (ambos os horários na hora repetida) são resolvidas por regra fixa (primeira ocorrência), sem
  como confirmar pela origem; ficam identificadas em `local_time_issue = 'ambiguous'`.
- Clima de um único ponto (Central Park, grade ERA5) para toda a cidade.
- Testes dbt varrem as tabelas inteiras a cada build, mesmo no incremental.
- Sem política de retenção na landing: versões antigas se acumulam em `data/landing/`.
- UI do Airflow sem login (`SIMPLE_AUTH_MANAGER_ALL_ADMINS`), só para uso local;
  `/api/v2/monitor/health` reporta o `triggerer` como `down` (não usado no MVP).

## Desvios do `ARCHITECTURE.md`

- **Processamento incremental em dbt** (o item 4.5 previa tabelas reconstruídas no MVP):
  adotado por decisão do projeto, por competência e guiado por `source_version`.
