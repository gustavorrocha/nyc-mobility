# NYC Mobility Pipeline — Arquitetura

**Status:** implementação concluída para o escopo local de estudo · **Atualizado em:** 16/11/2026

**Fase:** encerramento. Evoluções opcionais e limites estão na seção 8; operação no README.

## 1. Objetivo e escopo

Construir um pipeline de dados local, reproduzível e modular para integrar corridas da **NYC Taxi & Limousine Commission (TLC)** e dados meteorológicos históricos da **Open-Meteo**. A implementação integra **Yellow Taxi, Green Taxi, FHV e HVFHV** e clima, com dashboard analítico e observabilidade local.

- **Período:** validação inicial em janeiro e fevereiro/2025; descoberta diária desde janeiro/2025 até o último mês completo, respeitando a disponibilidade das fontes.
- **Análises iniciais:** demanda, distância, duração e associação com o clima por hora e zona; foco analítico inicial em Manhattan.
- **Escopo de ingestão:** preservar corridas de toda Nova York; filtrar Manhattan nas consultas/modelos analíticos, não descartar outras zonas na origem.
- **Fora do escopo entregue:** predição, cluster Spark, infraestrutura cloud e deploy automático em produção.

## 2. Tecnologias e decisões de engenharia aprovadas (05/11/2026)

| Área | Decisão | Diretriz |
|---|---|---|
| Organização | **Monorepo modular** | Código de ingestão, DAGs, dbt, migrações, testes e infraestrutura no mesmo repositório, com módulos independentes por fonte. |
| CI/CD | **GitHub Actions** | PRs executam lint e testes; build/publicação versionada de imagens é evolução, sem deploy automático no MVP. |
| Python | **uv + Ruff + Pytest** | Dependências reproduzíveis, lint/formatação e testes automatizados. |
| Banco e transformação | **Alembic + dbt** | Alembic administra `raw`, `metadata` e a estrutura de publicação; dbt administra `core` e `analytics`; o build publica os modelos de consumo em `serving`. |
| Execução | **Local primeiro, via Docker Compose** | Airflow, PostgreSQL e dbt reproduzíveis em desenvolvimento; cloud posteriormente. |
| Integrações | **Contratos por fonte** | Adaptadores de extração/validação por dataset, com normalização em esquema comum. |
| Operação | **Observabilidade desde o MVP** | Metadados e logs do pipeline, mais perfil opcional local com Prometheus, Grafana, Alertmanager e Mailpit. |

**Princípio:** adoção de práticas profissionais com o menor número de componentes necessário para demonstrar um pipeline confiável.

## 3. Modelo de dados

**Banco PostgreSQL:** `nyc_mobility`

| Schema | Papel | Tabelas principais |
|---|---|---|
| `raw` | Dados da origem, separados por fornecedor/dataset | `tlc_yellow_trips`, `tlc_green_trips`, `tlc_fhv_trips`, `tlc_fhvhv_trips`, `tlc_taxi_zones`, `openmeteo_weather_hourly` |
| `core` | Dados padronizados e entidades comuns | `trips`, `zones`, `service_types`, `weather_hourly` |
| `analytics` | Modelos de trabalho agregados | `trip_demand_hourly`, `trip_weather_hourly`, `trip_quality`, `data_coverage` |
| `metadata` | Rastreabilidade e controle do pipeline | `sources`, `source_files`, `ingestion_runs`, `analytics_runs`, `analytics_publication` |
| `serving` | Última publicação validada para consumo | Cópias dos agregados e cadastros, publicadas numa transação |

**Convenções:**

- A **origem é explícita em `raw`**; em `core`, os nomes são independentes da fonte.
- `core.trips` terá uma coluna `service_type` (ex.: `yellow`, `green`, `fhv`, `fhvhv`) e um conjunto de campos comuns, sem presumir que todas as fontes ofereçam tarifas e distâncias comparáveis.
- Informação indisponível será representada por `NULL`, nunca substituída artificialmente por zero.
- IDs de corrida serão **técnicos e rastreáveis à versão/posição do registro na origem**, sem promessa de identidade estável entre republicações da TLC.

## 4. Fluxo operacional — decisões aprovadas (06/11/2026)

```text
NYC TLC (Parquet/CSV) ─┐
                      ├─> landing persistente ─> raw ─> core ─> analytics ─> serving
Open-Meteo (API) ─────┘                         │
                                              └─> metadata (controle)

Airflow: coordena tarefas e dependências
Python: extrai, valida e carrega as fontes
dbt: transforma, agrega e testa modelos SQL
```

### 4.1. Landing zone persistente

- Preservar **os arquivos/respostas originais** antes de carregar o PostgreSQL.
- No MVP, a landing será um **diretório local `./data/landing/` montado como bind mount** no ambiente Docker, separado do estado efêmero dos containers.
- Organização lógica por fonte, dataset e competência, por exemplo `landing/tlc/yellow/2025-01/` e `landing/openmeteo/era5/2025-01/`.
- Registrar o **hash SHA-256** e os parâmetros relevantes de aquisição; manter versões distintas de arquivos republicados, sem sobrescrever silenciosamente a versão antiga.
- Evolução possível: substituir o armazenamento local por um bucket GCS sem mudar os contratos de ingestão.

### 4.2. Ingestão mensal modular

- A **unidade de processamento será a competência (AAAA-MM)**, junto da fonte e do dataset; a Open-Meteo fornecerá horas correspondentes ao período solicitado.
- Cada dataset terá um **adaptador independente**, com contrato de campos, tipos obrigatórios e regras de extração/validação.
- Yellow, Green, FHV e HVFHV têm adaptadores próprios e normalização comum; HVFHV é opcional pelo volume.
- Para TLC, ler Parquet **em lotes** e carregar o banco via operação em massa (preferencialmente `COPY`), sem transferir o dataset inteiro por memória nem pelo Airflow.
- O processamento pode ser disparado manualmente por competência ou pela descoberta diária de publicações em `scheduled_pipeline`.

### 4.3. Reprocessamento idempotente e transacional

- Identificar uma versão de fonte por **fonte + dataset + competência + hash**.
- **Mesmo hash já concluído:** não repetir a inserção em uma execução normal; registrar a decisão de pular.
- **Hash novo / arquivo republicado:** carregar em staging temporário, validar e **substituir apenas a competência afetada em `raw` em uma transação**; falhas preservam a versão anteriormente válida.
- Uma reexecução intencional da mesma versão poderá ser solicitada explicitamente, sem gerar duplicações.
- Registrar versão carregada, status, início/fim, contagens e erros em `metadata`.
- O sucesso transacional da carga `raw` **não implica publicação atômica de todo o pipeline até `analytics`**. Falhas posteriores serão observáveis e recuperáveis por reexecução das transformações.

### 4.4. Airflow desacoplado da lógica de negócio

- As DAGs coordenarão **dependências, parâmetros, retries e estados**, chamando módulos Python testáveis e comandos/modelos dbt.
- Não embutir parsing de Parquet, contratos, SQL de negócio ou regras de reprocessamento diretamente nas DAGs.
- Trocar **referências pequenas** entre tarefas (dataset, competência, caminho, hash, IDs de execução), sem trafegar arquivos por XCom.
- Manter controle de concorrência para impedir duas cargas simultâneas conflitantes da mesma fonte/competência.
- Há uma DAG manual `monthly_pipeline` e uma DAG diária `scheduled_pipeline`; a execução de Python e dbt usará CLIs em processos independentes, conforme a seção 5.

### 4.5. Transformações simples inicialmente

- `raw` preserva a origem e inclui somente campos técnicos necessários à rastreabilidade.
- `core` padroniza e valida as entidades, unificando modalidades em `core.trips`.
- `analytics` agrega por hora e zona e integra indicadores de viagens às condições meteorológicas.
- Modelos transacionais e agregados usam processamento incremental por competência e versão dos insumos. Cadastros são reconstruídos e `data_coverage` é uma view.
- `--full-refresh` reconstrói os modelos quando houver mudança de estrutura ou critérios de métricas. Os testes dbt continuam verificando o conjunto inteiro.

### 4.6. Qualidade de dados rastreável

- **Falhas estruturais** (arquivo ilegível, coluna indispensável ausente, contrato incompatível) interrompem a carga antes da substituição da competência publicada.
- **Problemas por registro** (ex.: duração negativa ou distância inconsistente) devem ser identificados/contabilizados; preservar a evidência em `raw` e impedir que métricas incompatíveis sejam contaminadas.
- Validar contagens, cobertura de datas, tipos, unicidade técnica e regras de integridade; testes Python para ingestão e testes dbt para modelos.
- Registrar quantidades lidas, inseridas, rejeitadas/sinalizadas e motivos. **Não descartar dados silenciosamente**.
- Os testes do CI usam fixtures e simulações, sem depender da disponibilidade das APIs públicas.

### 4.7. Datas e fusos horários padronizados

- Preservar os **timestamps originais da TLC** e normalizar os horários confiáveis para **UTC** em `core`, utilizando `America/New_York`.
- Obter o histórico meteorológico com timestamps inequívocos (preferencialmente UTC/Unix) e manter documentada sua referência temporal.
- Para agregação horária, associar viagens ao intervalo de embarque correto e observar que valores horários de **precipitação representam um intervalo acumulado**, diferente de medidas instantâneas como temperatura.
- Horários inexistentes são sinalizados como inválidos; horários ambíguos são identificados e convertidos pela escolha determinística documentada no README. A TLC não informa qual ocorrência do horário repetido foi usada. O clima é consultado em UTC e recortado pelos limites locais de Nova York.

## 5. Arquitetura de execução — decisões aprovadas (06/11/2026)

### 5.1. CLI Python independente do Airflow

- A ingestão será implementada como **pacote Python instalável com uma CLI**, invocável manualmente, pelo Airflow ou pelo CI, sempre com o mesmo código e parâmetros explícitos.
- Interface ilustrativa (contrato sujeito apenas a refinamentos de sintaxe): `nyc-mobility ingest --dataset tlc.yellow --period 2025-01`.
- O Airflow ficará responsável por orquestração, parâmetros, dependências, retries e estados; **não conterá parsing de Parquet, acesso específico às fontes nem regras de negócio**.
- Cada fonte terá seu adaptador e contrato; a CLI direcionará a execução ao adaptador correspondente.

### 5.2. Runtime local com Airflow LocalExecutor e dependências isoladas

- Usar **Airflow 3 com `LocalExecutor`**, sem Celery, Redis, Kubernetes ou Docker-in-Docker no MVP.
- Utilizar **um servidor PostgreSQL com dois bancos lógicos separados**: `airflow_metadata` (metadados internos do Airflow) e `nyc_mobility` (dados do projeto).
- O ambiente Compose terá os serviços necessários ao Airflow 3 (API server, scheduler e processador de DAGs quando exigido pela distribuição adotada) e PostgreSQL; não manter um servidor dbt continuamente ativo.
- As tarefas de ingestão serão **processos locais acionados pelo Airflow** a partir da CLI do pacote. O **dbt será chamado via CLI** com dependências isoladas das do Airflow (por exemplo, um virtualenv preparado na imagem de tarefas); as imagens preparam ambientes separados para o pacote e o dbt; as DAGs usam tarefas Python e BashOperator.
- O pacote Python e o projeto dbt estarão disponíveis no ambiente que executa as tarefas. O Airflow trocará somente parâmetros/referências pequenas, nunca Parquets via XCom.

### 5.3. Orquestração manual e agendada

- Uma DAG principal chamada **`monthly_pipeline`**, inicialmente disparada **manualmente**, receberá a competência no formato `AAAA-MM`.
- Para a competência solicitada, executará independentemente as tarefas de ingestão de **Taxi Zones**, **Yellow**, **Green**, **FHV** e **Open-Meteo**, com **HVFHV opcional**. O cadastro de zonas é tratado como referência versionada: se sua versão não mudou, a tarefa apenas verifica/omite a recarga.
- Após o sucesso (ou reconhecimento idempotente de uma carga previamente concluída) de **todas** as fontes necessárias, executar **`dbt build`** para produzir `core` e `analytics` e testar os modelos.
- Ao final, registrar status e métricas operacionais. Se uma fonte falhar, **não executar dbt com o conjunto mensal incompleto**.
- A DAG diária verifica publicações às 09h UTC, sem catchup e com até uma competência pendente por execução por padrão.
- No MVP haverá **uma execução da DAG ativa por vez**. As ingestões de uma mesma execução poderão ocorrer em paralelo. Reexecuções do dbt não dependem necessariamente de recarregar fontes já válidas.

```text
Execução: monthly_pipeline(period=AAAA-MM)
           |                |                   |
     Taxi Zones      Viagens TLC           Open-Meteo
           |                |                   |
           +----------------+-------------------+
                            |
                  dbt build (core/analytics + testes)
                            |
                  Registrar resultado da execução
```

### 5.4. Persistência local separada

| Dado | Persistência definida |
|---|---|
| Bancos PostgreSQL | Volume Docker nomeado, persistente entre reinicializações |
| Arquivos originais e suas versões | Bind mount do diretório local `./data/landing/` |
| Logs do Airflow | Bind mount do diretório local `./data/logs/` |
| Código, DAGs, dbt e migrações | Monorepo Git (não tratados como dados persistentes de execução) |
| Credenciais e parâmetros sensíveis | `.env` local ignorado pelo Git; segredos nunca versionados |

- Não excluir automaticamente versões anteriores dos arquivos brutos. **A política de retenção ainda precisa ser definida**.
- O mecanismo local de armazenamento não impede trocar a landing por GCS futuramente, desde que os adaptadores trabalhem com referências de armazenamento.

### 5.5. Retries, isolamento de falhas e concorrência

- A DAG manual usa três retries de ingestão, atraso inicial de dois minutos e backoff; o build não tem retry. A diária usa dois retries com atraso inicial de cinco minutos, backoff e timeout de seis horas. Erros de contrato e falhas de build não têm retry imediato.
- Download incompleto não será promovido à landing válida; erro de contrato interromperá a substituição em `raw`.
- A detecção de versão já processada (`source + dataset + competência + SHA-256`) resultará em **no-op idempotente**, mas **não bloqueará a etapa analítica**.
- Arquivos republicados serão carregados/validados em estrutura temporária e substituirão a competência de `raw` **em uma transação**; falhas preservarão a versão anterior.
- A DAG limitará a concorrência global a uma execução ativa; adicionalmente, o **PostgreSQL terá proteção por fonte/dataset/competência** para evitar cargas conflitantes mesmo se disparadas fora do Airflow (advisory lock por fonte/competência).
- Uma falha no dbt **não desfaz a ingestão `raw` bem-sucedida**. A execução analítica será marcada como falha e poderá ser repetida sem novo download.
- Os modelos dbt não são publicados atomicamente entre si. Após validação, o build
  publica os modelos de consumo em `serving` numa única transação, conforme a seção 8.

### 5.6. Mesmo código no ambiente local, no Airflow e no CI

- A CLI, os módulos Python e as regras de negócio serão únicos. Desenvolvimento, DAGs e GitHub Actions usarão **as mesmas interfaces**, com diferentes configurações e dados de teste.
- O CI executará Ruff, Pytest, testes de integração com PostgreSQL e verificações dbt/Alembic nos componentes implementados.
- Testes do CI serão **determinísticos**: usarão Parquets/JSON pequenos e APIs simuladas, sem exigir conexão às fontes públicas.
- Dependências terão versões controladas por `uv.lock`/configuração de build; comandos externos receberão argumentos explícitos, sem interpolar parâmetros livres em comandos de shell.
- **Sem deploy automático no MVP**. A possibilidade de publicar imagens versionadas e releases permanece como evolução de CD.

## 6. Desenvolvimento e CI

- Trabalhar com branches curtas e Pull Requests para `main`.
- CI: Ruff, Pytest e, testes de integração com PostgreSQL, validação dbt e migrações Alembic.
- Manter os testes determinísticos, com pequenos Parquets/JSON simulados; testes com as fontes reais podem ser executados separadamente.
- **CD no MVP não significa deploy automático:** publicação de imagens e releases pode ser habilitada após estabilização do fluxo local.

## 7. Plano de implementação incremental e critérios de aceite

As etapas abaixo registram o plano original do MVP, já implementado. Para novas evoluções,
executar uma entrega coesa por vez e validar com o responsável antes de ampliar o escopo.

| Etapa | Escopo | Critério de aceite mínimo |
|---|---|---|
| **0 — Inspeção e planejamento** | Ler este documento e inspecionar o repositório, identificando estado atual, divergências e dependências. **Não alterar arquivos.** | Apresentar plano de execução, arquivos previstos e riscos; aguardar aprovação antes de editar. |
| **1 — Fundação local** | Estrutura do monorepo, `uv`, Docker Compose, Airflow, PostgreSQL, ambiente dbt, configurações e migrações Alembic iniciais. | Ambiente inicializável e reproduzível; serviços e conexões verificados; schemas criados; comandos de validação documentados. |
| **2 — NYC TLC Yellow Taxi** | Adaptador Yellow, download Parquet de janeiro/2025, landing, hash, contrato, leitura em lotes, carga transacional em `raw` e controle em `metadata`. | Janeiro/2025 carregado; segunda execução não duplica linhas; falha de carga não compromete versão publicada; testes executados. |
| **3 — Open-Meteo e zonas** | Ingestão meteorológica histórica de janeiro/2025, padronização de horários e ingestão/versionamento do cadastro Taxi Zones. | Cobertura esperada da competência, valores armazenados em `raw`, origem rastreável e testes de tratamento temporal/estrutura. |
| **4 — dbt `core`/`analytics`** | Padronização e unificação inicial de viagens (`yellow`), zonas, clima e agregados horários; testes de qualidade. | `dbt build` concluído; agregados consultáveis; registros inválidos tratados conforme regras e sem exclusão silenciosa. |
| **5 — Airflow ponta a ponta** | DAG `monthly_pipeline`, parâmetros, dependências, chamadas à CLI e ao dbt, retries e tratamento de reprocessamento. | Janeiro e fevereiro/2025 processáveis pela DAG; reexecução idempotente; estados e falhas rastreados. |
| **6 — CI e acabamento** | GitHub Actions, lint, testes automatizados, verificações de integração e documentação final. | CI reproduzível com fixtures, sem depender das APIs reais; README permite executar e demonstrar o projeto localmente. |

### 7.1. Protocolo obrigatório para o agente de código

1. **Antes de cada etapa:** ler a arquitetura pertinente, verificar o estado atual do repositório e apresentar plano curto (objetivo, arquivos previstos, critérios de validação); não iniciar a etapa seguinte sem aprovação explícita.
2. **Durante a etapa aprovada:** alterar somente o necessário para seus critérios de aceite, preservar os contratos e decisões deste documento e evitar abstrações ou dependências adicionais sem justificativa. Lacunas de implementação podem ser resolvidas com escolhas simples e documentadas; decisões que contrariem a arquitetura devem ser apresentadas para aprovação **antes** da alteração.
3. **Ao concluir a etapa:** informar arquivos modificados, escolhas relevantes, comandos/testes efetivamente executados e seus resultados, limitações e passos objetivos para validação manual. Não afirmar que algo foi validado sem evidência de execução.
4. **Gate de aprovação:** interromper a execução depois do relatório; esperar confirmação expressa para avançar. Correções dentro da etapa aprovada são permitidas, mas não antecipar outra etapa.
5. **Restrições:** não alterar este `ARCHITECTURE.md`, não executar operações destrutivas sobre dados persistidos, não fazer commits/pushes, deploys ou alterar serviços remotos sem autorização. Nunca versionar credenciais ou segredos.
6. **Testabilidade:** reutilizar o mesmo código Python na CLI, no Airflow e no CI. Para testes automatizados, preferir pequenos Parquets/JSON de exemplo e APIs simuladas; se Docker, rede ou outro requisito não estiver disponível, registrar a limitação e fornecer os comandos exatos para reprodução.

### 7.2. Formato resumido das respostas do agente

- **Antes:** `Plano da etapa` — objetivo, principais arquivos e verificações previstas.
- **Depois:** `Resultado da etapa` — alterações, testes/saídas, pendências e instruções para validação.
- **Fechamento:** `Aguardando aprovação para a próxima etapa.`

## 8. Estado implementado e evoluções

O plano acima registra a sequência original do MVP. As etapas 0–6 foram implementadas;
os comandos atuais e as limitações operacionais estão no README. O projeto também inclui
Green/FHV/HVFHV, comparações no dashboard e ingestão diária com backfill limitado.

- Airflow usa `LocalExecutor`, CLIs em processos separados e ambientes isolados para o
  pacote e o dbt. Retentativas e timeouts estão definidos nas DAGs.
- A ingestão usa advisory locks por fonte/competência; o build rastreado tem seu próprio
  advisory lock e verifica as versões dos insumos antes de publicar.
- Os modelos dbt são áreas de trabalho. A migração `0007` e o build rastreado publicam
  agregados e cadastros em `serving` numa transação única; falhas preservam a publicação
  anterior. Cada callback analítico lê uma transação `REPEATABLE READ`.
- O perfil opcional `observability` contém exportador de leitura, Prometheus, Grafana,
  Alertmanager e Mailpit. Monitora publicação, último build e coleta, com alertas e
  notificações locais. As regras são testadas com `promtool`; o CI verifica configurações
  e provisionamento do Grafana. O teste SMTP de alerta/recuperação é manual e reproduzível.

Evoluções ainda abertas:

- Ajustar os tempos didáticos dos alertas a uma política operacional e monitorar falhas
  de ingestão e atraso de dados considerando o calendário de publicação das fontes.
- Política de retenção/limpeza de arquivos antigos e dimensionamento de disco local.
- Desambiguar a hora repetida da TLC exigiria informação que a fonte não fornece; a limitação e a escolha determinística permanecem documentadas.
- Destino real de notificações e operação fora do ambiente local.
