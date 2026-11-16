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
docker compose exec airflow-scheduler nyc-mobility build    # ou, no host:
uv run --env-file .env nyc-mobility build
```

| Modelo | Grão | Observações |
|---|---|---|
| `core.zones` | zona | Cadastro TLC; strings vazias viram `NULL` |
| `core.service_types` | modalidade | Seed: `yellow`, `green`, `fhv` e `fhvhv` |
| `core.trips` | corrida | Uma linha por registro de `raw` (nada é excluído); `is_valid` e `invalid_reason` (`missing_required_value`, `dropoff_before_pickup`, `negative_distance`, `pickup_outside_period`, `unknown_zone`); horários locais originais e em UTC; `metric_issue` marca corridas válidas com duração, distância, velocidade ou valor fora das faixas plausíveis (vars `metric_*` em `dbt_project.yml`): contam na demanda, mas ficam fora das médias |
| `core.weather_hourly` | hora UTC | Semântica da fonte: acumulados referem-se à hora anterior a `observed_at_utc` |
| `analytics.trip_demand_hourly` | hora × modalidade × zona de embarque | Apenas corridas válidas; métricas em somas (`duration_min_sum`, `distance_miles_sum`, `amount_sum`) e `measured_trip_count` (corridas sem `metric_issue`), somáveis em qualquer recorte: média = soma / `measured_trip_count` |
| `analytics.trip_weather_hourly` | hora `[hour_utc, +1h)` | Viagens válidas e clima da hora, inclusive horas sem corrida; médias de duração, distância, velocidade (`avg_speed_mph`) e valor (`avg_amount`) só das corridas sem `metric_issue`; instantâneas no início da hora, acumulados da observação seguinte (`NULL` na última hora sem a competência seguinte) |
| `analytics.data_coverage` | competência × modalidade | View que concilia a versão atual dos insumos com `trip_quality` e demanda; distingue disponível, sem carga e aguardando transformação; também verifica a versão e a cobertura horária do clima |

- `nyc-mobility build` executa o projeto dbt inteiro e persiste início, fim, versões dos insumos,
  status, resultados de modelos/testes e competências disponíveis após sucesso em
  `metadata.analytics_runs`. `--period AAAA-MM` identifica a solicitação (como na DAG), sem
  restringir o processamento incremental. `--full-refresh` reconstrói todos os modelos.
- A CLI e a tarefa `dbt_build` do Airflow usam o mesmo comando rastreado. Um advisory lock
  impede dois builds rastreados simultâneos; cada execução usa um target temporário próprio,
  para nunca reaproveitar o artefato de uma execução anterior. Falta de artefato também é falha.
  Mudança nas versões dos insumos durante o build impede marcar a execução como validada.
- O resultado por modelo/teste vem do artefato oficial
  [`run_results.json`](https://docs.getdbt.com/reference/artifacts/run-results-json). Invocações
  diretas de `dbt build` continuam possíveis, mas não registram histórico nem participam do lock.
- Os resultados registrados são históricos: não tornam atômica a publicação entre modelos.
  Se o processo for encerrado à força, o registro pode permanecer `running`; conferir os logs
  do Airflow antes de reexecutar. Não há sucesso presumido para execuções anteriores à migração.

- **Incremental por competência** nos modelos transacionais (`core.trips`,
  `core.weather_hourly`, agregados `analytics`); cadastros (`zones`, `service_types`) são reconstruídos
  inteiros. Cada linha guarda em `source_version` a versão dos insumos da sua competência
  (arquivo publicado em `metadata.ingestion_runs`, cadastro de zonas e, no viagens x clima, o
  clima do mês seguinte). Um `dbt build` reprocessa (delete+insert por `period`) só as
  competências cuja versão mudou: mês republicado → só ele; nova versão de zonas → todas as
  viagens; clima do mês seguinte → o viagens x clima do mês anterior.
- `dbt build --full-refresh` reconstrói tudo (necessário após mudar a estrutura de um modelo ou
  um limite `metric_*`).
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
| `tlc.green` | `raw.tlc_green_trips` | Táxis verdes, com os horários `lpep_*` |
| `tlc.fhv` | `raw.tlc_fhv_trips` | Bases tradicionais e limusines; zonas opcionais, sem distância nem valores |
| `tlc.fhvhv` | `raw.tlc_fhvhv_trips` | Aplicativos de alto volume; distância e componentes do valor pago pelo passageiro |
| `openmeteo.era5` | `raw.openmeteo_weather_hourly` | `time_unix` em UTC; mês local de NY (DST incluído); acumulados (`precipitation`, `rain`, `snowfall`) referem-se à hora anterior |
| `tlc.taxi_zones` | `raw.tlc_taxi_zones` | Referência versionada: só recarrega quando o hash muda |

- O arquivo original fica em `data/landing/<fonte>/<dataset>/[AAAA-MM/]<sha256[:16]>/`; versões
  republicadas coexistem, nada é sobrescrito. Na Open-Meteo, a versão é o hash do conteúdo
  canônico (sem `generationtime_ms`, que muda a cada chamada); o hash do arquivo bruto fica em
  `acquisition_params.file_sha256`.
- Mesmo hash já publicado → execução registrada como `skipped`. Hash novo ou `--force` →
  staging temporário e substituição da competência na tabela raw da modalidade numa transação.
- As quatro modalidades usam contratos próprios em `src/nyc_mobility/tlc_trips.py`, com o
  mesmo fluxo de ingestão. A DAG inclui Yellow, Green e FHV; HVFHV é opcional
  (`include_hvfhv=true`, pelo volume). A migração `0005` já cria suas tabelas raw.
- Em `core.trips`, campos não informados pela fonte permanecem `NULL`. FHV sem zona continua
  na demanda total, fora do mapa/ranking; sem distância/valor, recebe `metric_unavailable` e
  fica fora das médias. No HVFHV, valor = tarifa base + pedágios + taxas + gorjetas.
- Cargas simultâneas da mesma fonte/competência são recusadas (advisory lock no PostgreSQL).
- Execuções, contagens, registros sinalizados por motivo e erros ficam em
  `metadata.ingestion_runs`; arquivos e hashes em `metadata.source_files`.
- Os testes de integração criam e recriam o banco descartável `nyc_mobility_test`.

Parar o ambiente: `docker compose down` (preserva o volume `postgres-data`; **não** use `-v`
a menos que queira apagar os bancos).

## Dashboard e cobertura

```bash
docker compose up -d --build dashboard
# Ou no host:
uv run --env-file .env python -m dashboard
```

Acesse <http://127.0.0.1:8050>. Aplique as migrações (`nyc-mobility db upgrade`) e execute
`nyc-mobility build` antes de usar esta versão do dashboard. Para o Airflow, reconstrua também
a imagem dos serviços após atualizar o código do pacote.

A migração de rastreamento é a `0006`. A `0005` original (tabelas raw das demais modalidades)
é preservada para compatibilidade com bancos que já a receberam; ela não deve ser substituída
por outra migração com o mesmo identificador.

- Mobilidade urbana e Mobilidade e clima mostram a cobertura de cada mês/modalidade
  selecionados, independentemente do distrito ou zona. Os indicadores usam apenas meses
  disponíveis em **todas** as modalidades selecionadas; o clima exige também sua versão atual
  e o número esperado de horas (incluindo DST).
- Mês sem carga não vira zero. Mês carregado e transformado sem corridas válidas é zero,
  inclusive quando o filtro de zona não encontra corridas. A demanda temporal mantém lacunas
  onde falta cobertura; médias diárias e heatmap usam somente dias cobertos.
- Cobertura verifica completude técnica da carga, versões e conciliação; não garante que o
  arquivo publicado pela TLC contenha todas as corridas reais. Horas sem precipitação
  conhecida continuam fora das comparações climáticas.
- Pipeline Health separa o histórico de ingestões do histórico de transformações dbt e mostra
  competências validadas na execução, resultados de modelos/testes e falhas.

### Comparações

- Na página Mobilidade urbana, a comparação entre modalidades mostra uma série de demanda
  por modalidade, participação nas corridas selecionadas e médias de duração, distância,
  velocidade e valor. A tabela informa a fração de corridas usada nas médias; métricas ausentes
  ficam vazias. As tarifas/componentes diferem entre modalidades e não equivalem a lucro ou
  remuneração do motorista.
- As séries e participações usam somente meses cobertos em **todas** as modalidades
  selecionadas, respeitando distrito e zonas. Participação é relativa à seleção, não ao mercado
  inteiro. A seleção inicial inclui apenas modalidades que já têm dados disponíveis.
- Para usar esta versão, execute `nyc-mobility build` e reconstrua o dashboard. A incorporação
  das modalidades não baixa competências automaticamente; use a CLI ou a DAG para carregá-las.

## Observações

- O repositório pode estar em uma pasta sincronizada (ex.: OneDrive). Recomenda-se excluir
  `data/` da sincronização, pois a landing e os logs podem ficar grandes.
- `/api/v2/monitor/health` reporta o `triggerer` como `down`: o serviço não é usado no MVP
  (não há operadores deferrable).
