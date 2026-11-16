# nyc-mobility

Pipeline local de dados de mobilidade de NYC (TLC + Open-Meteo). Arquitetura e plano de
implementação: [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Estrutura

| Caminho | Conteúdo |
|---|---|
| `src/nyc_mobility/` | Pacote Python e CLI `nyc-mobility` |
| `src/nyc_mobility/migrations/` | Migrações Alembic (`raw`, `core`, `analytics`, `metadata`, `serving`) |
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

## Observabilidade: primeiro contato com Prometheus

```bash
docker compose --profile observability up -d prometheus
```

Acesse <http://127.0.0.1:9090> e consulte `up{job="prometheus"}`. O valor `1`
indica que a última coleta do próprio Prometheus teve sucesso. Em **Status → Target
health**, confira o alvo `prometheus` e seu estado. O arquivo
[`observability/prometheus.yml`](observability/prometheus.yml) configura uma coleta a
cada 15 segundos; o volume `prometheus-data` preserva o histórico entre reinícios.

Suba também o exportador para consultar a última publicação validada:

```bash
docker compose --profile observability up -d --build metrics-exporter
```

O exportador lê `metadata.analytics_publication` com o usuário de leitura do dashboard e
expõe `nyc_mobility_publication_timestamp_seconds`, um instante Unix em segundos. O Prometheus
consulta `/metrics` a cada 15 segundos. Execute `up{job="nyc_mobility"}` para verificar a
coleta; `1` indica que o exportador e sua consulta ao banco responderam. Para saber quantas
horas se passaram desde a publicação, consulte:

```promql
(time() - nyc_mobility_publication_timestamp_seconds) / 3600
```

Sem publicação, não há amostra da métrica; erro no banco faz a coleta falhar, em vez de
inventar um valor. Essa métrica não indica se a TLC publicou novos arquivos ou se a ingestão
falhou. Para parar os dois serviços: `docker compose stop prometheus metrics-exporter`.

### Painel no Grafana

```bash
docker compose --profile observability up -d grafana
```

Abra <http://127.0.0.1:3000/d/nyc-publication> para ver as horas desde a última
publicação. O Grafana consulta o Prometheus pela rede do Compose; a fonte de dados e o
painel são criados a partir dos arquivos versionados em `observability/grafana/`.
O painel usa a mesma expressão PromQL mostrada acima e mostra “Sem dados” quando não
há amostra. Um segundo indicador consulta `up{job="nyc_mobility"}`: `1` aparece como
“Coleta OK” e `0` como “Falha”; sem série, mostra “Sem dados”. Esse resultado reflete a
última coleta, enquanto o alerta abaixo espera dois minutos de falha contínua.
A visualização anônima é apenas para desenvolvimento local, na porta 3000
restrita ao computador. Para parar o Grafana: `docker compose stop grafana`.

### Primeiro alerta

O Prometheus avalia `ExportadorIndisponivel` a cada 15 segundos. Se a coleta do alvo
`nyc_mobility` falhar continuamente por dois minutos, o alerta passa de `pending` para
`firing`; quando a coleta volta a funcionar, ele se resolve. Os dois minutos são um valor
didático para este ambiente local. Consulte <http://127.0.0.1:9090/alerts> para ver o estado.
O alerta aparece no Prometheus e é encaminhado ao Alertmanager descrito abaixo.

Para investigar, confira `docker compose ps metrics-exporter postgres`, os logs com
`docker compose logs --tail=50 metrics-exporter` e o alvo em
<http://127.0.0.1:9090/targets>. O alerta só cobre o alvo configurado e não detecta se o
próprio Prometheus parar nem se a ingestão falhar. A regra e seus cenários estão em
`observability/alerts.yml` e `observability/alerts_test.yml`. Rode os testes com:

```bash
docker compose --profile observability run --rm --no-deps --entrypoint promtool prometheus test rules /etc/prometheus/alerts_test.yml
```

### Alertmanager local

```bash
docker compose --profile observability up -d alertmanager
docker compose --profile observability restart prometheus
```

O Prometheus avalia as regras e envia alertas ativos ao Alertmanager. Ele os agrupa por
`alertname` e permite consultá-los ou silenciá-los em <http://127.0.0.1:9093>. O volume
`alertmanager-data` preserva os silêncios entre reinícios. O receptor `local` envia e-mails
para o Mailpit, uma caixa SMTP de teste dentro do Compose. As durações de agrupamento estão
em `observability/alertmanager.yml`.

Para investigar o encaminhamento, confira os serviços com
`docker compose ps prometheus alertmanager` e o campo “Alertmanagers” em
<http://127.0.0.1:9090/status>. Para parar o serviço: `docker compose stop alertmanager`.

### Caixa de notificações local

```bash
docker compose --profile observability up -d mailpit alertmanager
docker compose --profile observability restart alertmanager
```

Abra <http://127.0.0.1:8025> para ver as mensagens de alerta e de recuperação. O
Alertmanager envia por SMTP para `mailpit:1025` na rede interna; a porta SMTP não é
publicada no host. Os endereços `example.test` são fictícios e o Mailpit captura os
e-mails localmente, sem encaminhá-los. A conexão SMTP sem TLS é apenas para este ambiente
de teste. As mensagens ficam em memória e são perdidas ao recriar ou reiniciar o Mailpit.
O agrupamento aguarda 30 segundos antes da primeira notificação. Para parar a caixa:
`docker compose stop mailpit`.

Para testar os dois estados sem interromper o pipeline, execute no host:

```bash
python scripts/testar_notificacao_local.py
```

O script cria um alerta sintético com identificador único, espera o e-mail `[FIRING]`,
encerra o alerta e espera o e-mail `[RESOLVED]`. A recuperação pode demorar até cinco
minutos por causa de `group_interval`; ambas as mensagens ficam no Mailpit. O teste não
verifica a regra de coleta do Prometheus, que tem seus próprios testes com `promtool`.

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
- Os resultados registrados são históricos. `core` e `analytics` são áreas de trabalho; o
  dashboard consulta apenas a última versão validada em `serving` (publicação descrita abaixo).
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

### Publicação validada

- A migração `0007` cria `serving` e `metadata.analytics_publication`. Após o dbt e seus testes,
  o build copia demanda, clima, qualidade, cobertura, zonas e modalidades em **uma transação**,
  junto do marcador de publicação e do sucesso da execução. Falhas mantêm a publicação anterior.
- O dashboard usa apenas essas tabelas de consumo; Pipeline Health continua observando os
  metadados e os modelos de trabalho, e identifica a publicação atual. O cabeçalho informa a
  data da última validação. Sem uma primeira publicação, a interface orienta executar o build.
- Cada callback lê em uma transação `REPEATABLE READ`. A publicação usa `DELETE` + `INSERT`,
  preservando as versões visíveis a leitores em andamento; não usa `TRUNCATE` nem substitui
  tabelas de consumo durante a atualização. Referência: [isolamento no PostgreSQL 17](https://www.postgresql.org/docs/17/transaction-iso.html).
- A cobertura do dashboard passa a descrever a **publicação validada**, não uma carga raw
  recém-chegada. Novas ingestões e builds com falha ficam no Pipeline Health até serem publicados.
- Uma alteração em colunas/tipos exige migrar explicitamente a tabela `serving` correspondente.
  A publicação recusa schemas incompatíveis e preserva a versão anterior. O snapshot de consumo
  duplica apenas agregados e cadastros, sem copiar os milhões de corridas de `core.trips`.
- Aplique `nyc-mobility db upgrade` e execute `nyc-mobility build` **antes** de atualizar o
  container do dashboard. Use o build rastreado; não execute dbt direto concorrentemente a ele.

### Ingestão agendada e backfill

```bash
# Descoberta sem downloads de dados nem alterações no banco:
uv run --env-file .env nyc-mobility sync --start-period 2025-01 --dry-run

# Processa o primeiro mês pendente; ampliar --max-periods para lotes maiores:
uv run --env-file .env nyc-mobility sync --start-period 2025-01 --max-periods 1

# Backfill limitado a um intervalo explícito:
uv run --env-file .env nyc-mobility sync --start-period 2025-03 --end-period 2025-04 --max-periods 2
```

- A DAG `scheduled_pipeline` verifica diariamente às **09h UTC (06h de Brasília)**, sem catchup
  do calendário e com uma execução ativa. Por padrão, examina janeiro/2025 até o último mês
  completo e processa no máximo um mês por execução; mantém HVFHV opcional.
- A disponibilidade vem dos links do [catálogo oficial da TLC](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page).
  Um mês só é processado quando todas as modalidades solicitadas estão disponíveis. Os arquivos
  já carregados são conferidos com HEAD e ETag/Last-Modified; a ingestão existente confirma hashes.
  Links ausentes/HTTP 404 ficam aguardando publicação; 403, 429, 5xx e erros de rede são falhas.
- Reutiliza ingestão, clima, cadastro de zonas e build rastreado; sem novas versões e sem
  publicação pendente, não refaz o dbt. Uma falha de ingestão interrompe a execução antes do build.
  Falhas transitórias têm retries; erros de contrato/parâmetros e falhas de build/validação
  falham sem retry imediato, como na DAG manual.
- Limites podem ser definidos nos parâmetros da DAG ou em `NYC_MOBILITY_START_PERIOD` e
  `NYC_MOBILITY_END_PERIOD` (fim vazio = último mês completo). `--include-hvfhv` habilita aplicativos.
  A DAG manual `monthly_pipeline` continua disponível para diagnóstico e reprocessamento dirigido.

### Comparações

- Na página Mobilidade urbana, a comparação entre modalidades mostra uma série de demanda
  por modalidade, participação nas corridas selecionadas e médias de duração, distância,
  velocidade e valor. A tabela informa a fração de corridas usada nas médias; métricas ausentes
  ficam vazias. As tarifas/componentes diferem entre modalidades e não equivalem a lucro ou
  remuneração do motorista.
- As séries e participações usam somente meses cobertos em **todas** as modalidades
  selecionadas, respeitando distrito e zonas. Participação é relativa à seleção, não ao mercado
  inteiro. A seleção inicial inclui apenas modalidades que já têm dados disponíveis.
- A comparação temporal usa o intervalo imediatamente anterior com o **mesmo número de dias**,
  mantendo os filtros (ex.: 01–31/01/2025 compara com 01–31/12/2024). Os dois intervalos e
  seus dias cobertos são exibidos. Com cobertura incompleta, os valores são parciais e as
  variações ficam ocultas; base anterior zero ou métrica ausente também não produz variação.
- Para usar esta versão, execute `nyc-mobility build` e reconstrua o dashboard. A incorporação
  das modalidades não baixa competências automaticamente; use a CLI ou a DAG para carregá-las.

## Observações

- O repositório pode estar em uma pasta sincronizada (ex.: OneDrive). Recomenda-se excluir
  `data/` da sincronização, pois a landing e os logs podem ficar grandes.
- `/api/v2/monitor/health` reporta o `triggerer` como `down`: o serviço não é usado no MVP
  (não há operadores deferrable).
