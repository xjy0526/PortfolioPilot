# PortfolioPilot

[![Pull request CI](https://github.com/xjy0526/PortfolioPilot/actions/workflows/ci.yml/badge.svg?event=pull_request)](https://github.com/xjy0526/PortfolioPilot/actions/workflows/ci.yml?query=event%3Apull_request)

PortfolioPilot 是一个面向基金投研场景的可追溯投资组合研究平台。系统将交易流水、市场数据、风险分析和研究知识库统一到 PostgreSQL，通过受治理的 RAG、Prompt Registry、LLM Trace 和人工审核工作流，生成带证据引用、可验证、可复核的组合研究结果。

项目聚焦研究流程与工程可追溯性，不提供自动交易，不构成投资建议、交易指令或收益承诺。

## 核心能力

| 能力 | 当前实现 |
|---|---|
| Portfolio Ledger & Valuation | PostgreSQL 交易账本是持仓事实源；支持 CSV 幂等导入、任意 `as_of` 重建、多币种现金、历史价格/FX lineage 与估值快照 |
| Risk Analytics & Backtest | 确定性风险指标、行情覆盖/缺失语义、无前视成交口径、权重漂移、成本、Benchmark 与回测 provenance |
| Governed Hybrid RAG | 文档版本、权限、有效期、对象存储、PostgreSQL 全文检索、pgvector、RRF 与引用对象 |
| Prompt Registry & LLM Trace | 不可变 Prompt 版本、发布/回滚、结构化输出、数字与引用校验、Provider usage、成本和 fallback Trace |
| Human-in-the-loop Research Workflow | 固定节点、工具 allowlist、幂等运行、超时/预算、规则校验、人工审核与受控报告发布 |

## 系统界面与真实演示资源

- 完整本地启动后访问 Dashboard：<http://localhost:8000>，Swagger：<http://localhost:8000/docs>。
- 面试演示流程：[docs/demo-script.md](docs/demo-script.md)。
- 真实录屏准备与脱敏检查：[docs/demo-recording-guide.md](docs/demo-recording-guide.md)。
- 旧版真实截图归档：[docs/assets/legacy-ui/](docs/assets/legacy-ui/)。这些图片带旧品牌和旧功能布局，只用于迁移对照，不代表当前 PostgreSQL/RAG 主线界面。

仓库目前没有提交与最新治理链路完全一致的截图或 GIF，因此 README 不引用伪造图片或不存在的资源。完成一次按录屏指南执行的真实演示后，再将产物加入 `docs/assets/current-demo/`。

## 架构

```mermaid
flowchart LR
    INPUT[CSV / API] --> LEDGER[Transaction Ledger]
    LEDGER --> REBUILD[Position Rebuilder]
    REBUILD --> SNAPSHOT[Market Data & Valuation Snapshot]
    SNAPSHOT --> RISK[Risk Analytics]
    RISK --> RAG[Governed Hybrid RAG]
    RAG --> LLM[Structured LLM Output]
    LLM --> RULES[Rule Validation]
    RULES --> REVIEW[Human Review]
    REVIEW --> REPORT[Published Research Report]

    DB[(PostgreSQL + pgvector)] --- LEDGER
    DB --- SNAPSHOT
    DB --- RAG
    DB --- LLM
    DB --- REPORT
    STORAGE[(S3-compatible Object Storage)] --> RAG
    WORKER[Cron / Worker] --> SNAPSHOT
    WORKER --> RAG
    WORKER --> LLM
    TRACE[Evaluation & Trace] -. observes .-> RAG
    TRACE -. observes .-> LLM
    TRACE -. observes .-> RULES
    TRACE -. observes .-> REVIEW
```

设计边界：确定性引擎负责账本、估值、风险和目标权重；LLM 只解释结构化结果与已检索证据，不能直接修改目标权重。

## 5 分钟 Quick Start

项目目标运行时为 Python 3.12，CI 使用 Python 3.12 验证。以下应用命令已在当前仓库实际执行；macOS/Linux 使用示例中的激活命令，Windows 可改用 `venv\Scripts\activate`。

### A. 最小只读研究演示

该路径不连接外部账户、不调用真实模型、不写 PostgreSQL 业务表，只读取仓库内自行构造的 fixture，并在 `cache/` 生成本地报告。

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt

python -m evaluation.run_v2_eval \
  --mode synthetic_smoke \
  --output cache/evaluation/v2/synthetic_smoke/quickstart.json

python -m backtest.run_backtest \
  --portfolio data/portfolios/example_multi_asset_portfolio.csv \
  --prices data/prices/example_historical_prices.csv \
  --output cache/backtest_report.json \
  --train-window 5 \
  --holding-window 3 \
  --rebalance-frequency 3 \
  --transaction-cost-bps 5 \
  --slippage-bps 2 \
  --turnover-limit 1.0
```

检查报告中的披露字段：

```bash
python -c "import json; p=json.load(open('cache/evaluation/v2/synthetic_smoke/quickstart.json')); print({k:p[k] for k in ('evaluation_mode','git_commit_sha','dataset_version','sample_count','mock_response_used','human_reviewed_count')})"
python -c "import json; p=json.load(open('cache/backtest_report.json')); print({k:p[k] for k in ('data_source','run_mode','mock_price_data_used','execution_convention')})"
```

预期：评测报告明确显示 `evaluation_mode=synthetic_smoke`、`dataset_version=2.0.0`、
`sample_count=60`、`mock_response_used=true` 和 `human_reviewed_count=0`；这些字段只证明 V2
fixture 的工程链路，不是模型效果。回测使用本地 CSV，`mock_price_data_used=false` 只表示没有触发
随机行情生成器，不表示该示例 CSV 是经授权的生产行情。

### B. 完整本地开发

复制配置后，在 `.env` 中使用下面的本地开发值。无 Qwen Key 时可以启动，但模型调用会明确标记不可用/fallback；hashing embedding 只用于验证工程链路，不是语义检索质量证明。

```env
ENVIRONMENT=development
APP_MODE=fund_research
READ_ONLY_DEMO=false
OBJECT_STORAGE_BACKEND=local
OBJECT_STORAGE_LOCAL_ROOT=data/object_storage
EMBEDDING_PROVIDER=hashing
RAG_ALLOW_HASHING_FALLBACK=true
QWEN_API_KEY=
```

```bash
cp .env.example .env
docker compose up -d postgres
alembic upgrade head
python scripts/bootstrap_portfolio.py --base-currency CNY
python -m app.core.preflight
python main.py
```

启动后访问：

- Dashboard：<http://localhost:8000>
- Swagger API：<http://localhost:8000/docs>
- 存活检查：<http://localhost:8000/health/live>
- 依赖就绪检查：<http://localhost:8000/health/ready>

可选 Qwen 配置：

```env
AI_PROVIDER=qwen
QWEN_API_KEY=your_qwen_api_key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
```

独立 Worker/Cron 入口可先用以下已验证命令检查参数，再按 [演示脚本](docs/demo-script.md) 运行实际任务：

```bash
python -m app.workers.run_market_sync --help
python -m app.workers.run_position_rebuild --help
python -m app.workers.run_knowledge_ingestion --help
python -m app.workers.run_research_workflow --help
python -m app.workers.run_daily_pipeline --help
```

## 核心业务流程

1. 标准交易流水通过 `/api/portfolios/{id}/imports/transactions` 幂等写入账本；旧持仓 CSV 只能形成明确标记的 `opening_balance`。
2. Position Rebuilder 按 `as_of` 重放交易，组合估值关联当时可得的 PriceBar 和历史 FX，不使用未来数据。
3. 确定性风险引擎计算收益、波动、回撤、集中度和数据质量；回测保存成交口径、漂移权重、成本和输入哈希。
4. 研究文档进入对象存储和 PostgreSQL，由 Worker 解析、切片、生成 embedding，经发布与权限检查后参与 Hybrid RAG。
5. 已发布 Prompt 约束结构化模型输出；Trace 记录模型、Prompt、证据、usage、成本、代码版本与数据截止时间。
6. 规则校验通过后进入人工审核；批准、退回或拒绝均形成审计记录，只有批准结果可发布。

完整的 5-8 分钟演示顺序见 [docs/demo-script.md](docs/demo-script.md)。

## Verified Quality

- 实时状态：[GitHub Actions CI](https://github.com/xjy0526/PortfolioPilot/actions/workflows/ci.yml) 与 README 顶部 badge。
- CI 独立执行 `lint-type-unit`、`postgres-integration`、`postgres-restart-e2e`、`security-audit`、`coverage-report` 和 `docker-build`。
- Restart E2E 会真实重启 Compose PostgreSQL，并验证交易、持仓、估值、文档/chunk/embedding、Prompt、LLM Trace、Workflow、人工审核和发布报告仍存在且幂等。
- 历史质量快照：commit `d6582e76c95188b915ff8fd87eb2a440064c808e`、2026-08-19 UTC、[Actions run 32286512635](https://github.com/xjy0526/PortfolioPilot/actions/runs/32286512635)，合并 statement coverage 为 `62%`。该数字只对应此 SHA，不代表后续提交。
- `pip-audit` 与 Gitleaks 属于 CI 必跑项；通过不等于完整渗透测试或机构合规认证。

评测模式不能混用：

| 模式 | 数据/模型边界 | CI 默认执行 |
|---|---|:---:|
| `synthetic_smoke` | 自行构造 fixture + deterministic mock，只验证工程链路和规则 | 是 |
| `live_model_eval` | V1 真实模型调用或 V2 可追溯 live prediction bundle；不回退 mock | 否 |
| `human_gold_eval` | V2 必须存在 independently approved labels | 否 |
| `production_monitoring` | 必须存在真实生产观测数据 | 否 |

## Data & Evaluation Disclosure

- 仓库示例交易、组合、行情和研究材料均为自行构造或公开培训素材，不是真实个人持仓、内部研报或授权受限数据。
- 原始价格保留证券原币，FX 独立存储，估值时转换为组合基准币种；每个快照保留数据截止时间与 lineage。
- yfinance 只用于研究演示，没有生产 SLA；Tushare、Qwen 和其他 Provider 凭据必须由本地环境或 Secret Manager 注入。
- Mock、fallback、估算 usage 和 synthetic 数据必须在 API/JSON 中显式标记，不能作为真实模型质量、真实行情或生产效果宣传。
- `hallucination_rate=0` 等单一数字在 synthetic 模式下只描述规则夹具，不是真实模型“零幻觉”的证明。

## Known Limitations

- 回测具备 point-in-time 成交约束，但还不是生产级完整 walk-forward 平台。
- yfinance 行情、Basic Auth 和本地对象存储只适合研究演示或单机开发。
- 生产可写模式需要 PostgreSQL/pgvector、S3-compatible storage、正式认证、密钥管理、备份恢复和集中审计。
- 最新 PostgreSQL 主线与旧 Dashboard demo 激活接口尚未完全统一；不要用 `/api/demo/activate` 代替 ledger/valuation 演示。
- 完整边界见 [docs/current-limitations.md](docs/current-limitations.md)，发布审计见 [docs/audits/release_readiness_2026.md](docs/audits/release_readiness_2026.md)。

## 文档导航

| 文档 | 内容 |
|---|---|
| [docs/demo-script.md](docs/demo-script.md) | 5-8 分钟面试演示流程、预期输出与 fallback |
| [docs/demo-recording-guide.md](docs/demo-recording-guide.md) | 真实截图/GIF 录制、脱敏与验收清单 |
| [docs/architecture.md](docs/architecture.md) | 当前架构、边界和数据流 |
| [docs/api.md](docs/api.md) | API 索引与兼容接口 |
| [docs/testing.md](docs/testing.md) | 测试分层与 PostgreSQL restart E2E |
| [docs/deployment.md](docs/deployment.md) | Web、Cron、对象存储、preflight 与恢复边界 |
| [docs/current-limitations.md](docs/current-limitations.md) | 当前已知限制和 mock/fallback 条件 |
| [docs/evaluation-v2.md](docs/evaluation-v2.md) | V2 数据 schema、模式隔离、人工标注与指标解释 |
| [docs/case_studies/ai_hardware_portfolio_case.md](docs/case_studies/ai_hardware_portfolio_case.md) | AI 硬件组合研究案例 |
| [docs/audits/release_readiness_2026.md](docs/audits/release_readiness_2026.md) | 带日期和 SHA 的发布准备审计 |

## Experimental / Legacy Extensions

以下能力保留用于兼容或实验，但不属于核心叙事，默认关闭：Telegram、Shadow Agent、Tech Radar/Tech Picks、Trade Advisor、Parqet compatibility、Polymarket 和旧 SQLite Dashboard 数据流。

```env
ENABLE_POLYMARKET=false
ENABLE_TELEGRAM=false
ENABLE_PARQET=false
ENABLE_SHADOW_AGENT=false
```

关闭这些扩展不影响 PostgreSQL 账本、风险分析、Hybrid RAG、Prompt/Trace、人工审核和回测。`APP_MODE=fund_research` 会进一步隐藏个人化和交易式表达入口。

## 深入配置与模块说明

### PostgreSQL 交易账本与估值

核心组合链路使用 PostgreSQL 16、SQLAlchemy 2.x Async ORM、asyncpg 和 Alembic。启动数据库并应用 migration：

```bash
docker compose up -d postgres
alembic upgrade head
```

默认连接配置：

```env
DATABASE_URL=postgresql+asyncpg://portfoliopilot:portfoliopilot@localhost:5432/portfoliopilot
```

核心表包括 `users`、`portfolios`、`securities`、`provider_symbols`、`transactions`、`import_batches`、`price_bars`、`fx_rates`、`portfolio_valuation_snapshots`、`position_snapshots`、`sync_runs` 和 `risk_runs`。表结构只允许通过 Alembic 变更，应用 import 和 startup 都不会调用 `Base.metadata.create_all()`。

每个 FastAPI 请求和每个 Worker 任务使用独立 `AsyncSession`。Repository 负责数据访问，Session 的事务边界由请求或 Worker unit of work 管理。

旧 SQLite 数据库继续提供兼容读取。仅当本地确实存在旧库 `cache/portfoliopilot.db` 时，才在应用 migration 后显式迁移支持的数据：

```bash
python scripts/migrate_sqlite_to_postgres.py \
  --sqlite-path cache/portfoliopilot.db
```

该脚本幂等迁移旧组合总览快照和可选 Shadow 模拟交易。研究治理数据使用本文后续的独立迁移与校验命令。

### 交易流水导入

先运行 bootstrap 脚本并记录输出的 `portfolio_id`，再导入标准流水：

```bash
curl -X POST \
  "http://localhost:8000/api/portfolios/<portfolio_id>/imports/transactions" \
  -F "file=@data/portfolios/example_transactions.csv"
```

标准字段：

```csv
external_id,transaction_type,ticker,exchange,trade_date,settlement_date,quantity,price,fees,taxes,currency,note
```

`quantity`、本金、费用和税均使用非负绝对值，方向由 `transaction_type` 决定。证券买卖的本金由 `quantity * price` 计算；现金类流水在当前 CSV 合同中使用 `quantity` 表示现金绝对金额。重复文件按 SHA256 返回 `idempotent_replay=true`，错误行可通过响应中的 `error_report_url` 下载。

响应中的 `accepted_rows` 与 `persisted_rows` 只统计当前事务实际提交的新交易，`duplicate_rows` 统计文件重放或幂等键冲突，`rejected_rows` 统计校验失败；`inserted_rows` 暂时保留为 `persisted_rows` 的兼容别名。无 `external_id` 的行使用文件 SHA256 与 CSV 行号形成稳定幂等标识：同一文件中的相同行是两笔独立交易，同一文件重放不会再次写入；上游提供的相同 `external_id` 始终视为同一交易。

旧 `ticker,shares,buy_price,...` 持仓 CSV 仍可上传，但证券只会转换成 `opening_balance`，现金行转换成 opening cash deposit，响应明确返回：

```json
{"history_completeness":"opening_balance_only"}
```

通过标准交易导入 API 接收的这类数据只表示缺少完整交易历史的期初状态。

### 行情同步与估值

yfinance 默认仅同步美股/ETF 研究行情。同步 A 股前配置 `TUSHARE_TOKEN` 和 `MARKET_DATA_PROVIDERS=tushare,yfinance`：

```bash
python -m app.workers.run_market_sync --start 2025-01-01 --end 2026-08-12
python -m app.workers.run_position_rebuild \
  --portfolio-id <portfolio_id> \
  --as-of 2026-08-12T15:00:00+08:00
```

也可以顺序运行完整日任务：

```bash
python -m app.workers.run_daily_pipeline --portfolio-id <portfolio_id>
```

每次任务使用独立 Session、PostgreSQL advisory lock 和 `sync_run` 状态。Web API 不运行 APScheduler；生产调度应调用这些 Worker 入口。

`run_daily_pipeline` 是运行完成即退出的一次性批处理，不是常驻 Worker。它为整条流水线创建父级 `sync_run`，使用 PostgreSQL session advisory lock，并按 `UTC 日期 + portfolio scope` 生成唯一 `run_key`；同日重复触发返回 `idempotent_replay=true`，不会再次执行子任务。

流水线先冻结一次 `valuation_as_of` 作为交易账本和估值日期，再执行行情同步；同步成功后生成一个统一的 `knowledge_cutoff` 过滤行情与 FX 的 `data_as_of`。这样，任务启动后但本次 cutoff 之前到达的数据能够进入估值，cutoff 之后的数据仍被排除。部分 Provider 失败会记录成功源、失败源和 `degraded=true`；全部失败时不创建估值，失败 run 的 `data_as_of` 为 `null`。

核心 API：

```text
GET  /api/portfolios
GET  /api/portfolios/{id}
GET  /api/portfolios/{id}/transactions?as_of=
POST /api/portfolios/{id}/imports/transactions
GET  /api/portfolios/{id}/positions?as_of=
GET  /api/portfolios/{id}/valuation?as_of=
POST /api/portfolios/{id}/rebuild?as_of=
POST /api/market-data/sync
```

估值接口不会使用 `valuation_as_of` 之后的交易或交易日，也不会使用统一 `knowledge_cutoff` 之后才可见的行情或 FX。响应包含 snapshot ID、`input_hash`、实际使用数据的 `data_as_of`、价格/FX ID、warning 和 `history_completeness`。

Dashboard 原有 `POST /api/portfolio/upload-csv` 以及 `GET/POST/PUT/DELETE /api/portfolio/csv-positions` 继续兼容，但现在共同调用 PostgreSQL snapshot replacement 服务，不再写本地持仓 CSV，也不更新 `state.portfolio_data`。Dashboard 持仓被定义为“当前持仓快照”：每次修改创建可审计 generation，旧 generation 标记为 `superseded`，只有唯一 `active` generation 参与持仓重建。因此先上传 10 股、再上传 12 股的结果是 12 股，不会叠加成 22 股；其他 `standard_csv`、`manual` 等来源的正式交易不会被 supersede。

兼容响应保留 `status`、`positions_imported`、`position` 和 `positions` 等旧字段，并新增 `portfolio_id`、`import_batch_id`、`snapshot_generation`、真实导入计数、`position_rebuild` 和 `valuation_snapshot`。相同 snapshot 重放不会创建交易或 generation。CSV 自带 `current_price` 或缺失时使用的成本价回退都会绑定到 active import batch，并在 snapshot lineage 中标为用户提供/研究演示来源，不会伪装成 Provider 行情。生产响应不包含本地 `csv_path`。

## AI Provider 配置

默认使用千问兼容接口：

```env
AI_PROVIDER=qwen
QWEN_API_KEY=your_qwen_api_key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
```

也支持通用 OpenAI-compatible endpoint：

```env
AI_PROVIDER=openai_compatible
OPENAI_COMPATIBLE_API_KEY=your_api_key
OPENAI_COMPATIBLE_BASE_URL=https://your-endpoint.example/v1
OPENAI_COMPATIBLE_MODEL=your-model
```

业务层仅依赖 `LLMProvider`，内置 `QwenProvider`、`MockProvider` 和 `OptionalOpenAICompatibleProvider`。本地还可以在 Dashboard 的“操作 → API 设置”中临时设置千问、FMP 和联系邮箱；该入口必须同时满足 development、localhost、`platform_admin` 和 `ALLOW_RUNTIME_SECRET_CONFIGURATION=true`，只修改当前进程内存且不写 `.env`，密钥不会在页面回显。Production 永久禁用该入口。

## 旧持仓 CSV 兼容

推荐字段如下：

```csv
ticker,shares,buy_price,current_price,buy_date,currency,sector,name,asset_type,market,exchange,country
AAPL,15,142.50,,2024-03-15,USD,Technology,Apple Inc.,equity,US,NASDAQ,US
600519.SS,3,1680.00,,2024-05-10,CNY,Consumer Defensive,Kweichow Moutai,cn_equity,CN-A,SSE,CN
POLY-BTC-150K-2026,80,0.31,0.36,2026-01-05,USD,Prediction Markets,BTC above 150k in 2026?,prediction_market,Polymarket,Polymarket,WEB3
```

- `ticker`、`shares`、`buy_price`：旧期初持仓字段。
- `current_price`：可选；预测市场持仓建议显式提供。
- `currency`：如 `USD`、`EUR`、`CNY`。
- `asset_type`：如 `equity`、`cn_equity`、`prediction_market`。
- 其他字段用于展示、行业聚合和研究过滤。

通过新的 transaction import API 上传后，这些行会原子写入 PostgreSQL ledger，不再把 `portfolio.csv` 或 `state.portfolio_data` 作为核心组合事实源。`PARQET_PORTFOLIO_CSV` 仅保留给默认关闭的旧兼容扩展。

仓库中的 `data/portfolios/test_*.csv` 均为模拟数据，可用于验证不同风险场景，不代表真实持仓或投资观点。

## PostgreSQL 历史行情与风险指标

核心风险 API 从 PostgreSQL `price_bars` 读取 `as_of` 之前可得的 adjusted close，并返回数据 lineage。以下旧 Provider 配置仍供尚未迁移的非核心页面使用：

```env
PRICE_HISTORY_PROVIDER=yfinance
# PRICE_HISTORY_PROVIDER=csv
# PRICE_HISTORY_CSV=data/prices/example_historical_prices.csv
PRICE_HISTORY_LOOKBACK_DAYS=365
PRICE_HISTORY_STALE_AFTER_DAYS=5
RISK_MIN_OBSERVATIONS=20
```

PostgreSQL price history adapter 返回价格矩阵以及 `source=postgres_price_bars`、`as_of`、起止日期、`missing_tickers`、`stale_tickers` 和 `coverage_ratio`。A 股优先 `tushare`，美股/ETF 优先显式标记的 `yfinance_research`。

当历史数据不足时：

- `annual_volatility`、`max_drawdown`、`sharpe_ratio` 返回 `null`；
- `metric_status` 说明指标是否有效；
- `data_quality` 披露覆盖率、缺失 ticker、陈旧 ticker 和样本区间；
- 系统不会用 `0` 表示未知风险。

主要接口：

```bash
curl "http://localhost:8000/api/portfolio/risk-summary?portfolio_id=<portfolio_id>"

curl -X POST http://localhost:8000/api/ai/analyze-portfolio \
  -H "Content-Type: application/json" \
  -d '{"portfolio_id":"<portfolio_id>","lang":"zh","top_k":5}'
```

## PostgreSQL 知识库与 Hybrid RAG

知识库使用 PostgreSQL/pgvector 持久化以下对象：

- `DocumentMetadata`
- `DocumentVersion`
- `DocumentChunk`
- `IngestionJob`
- `ChunkEmbedding`
- 服务端 `Principal` 权限上下文

文档支持 `.txt`、`.md`、`.csv` 和 `.pdf`。解析器优先按标题、章节、段落和 PDF 页码切片，固定长度仅作为 fallback。版本同时保存源文件 checksum 和实际持久化内容 checksum；内容不变时不会重复索引，内容变化会创建新版本。只有已发布且当前有效的版本能够进入正常检索。

上传只创建待处理 Job，文档解析与 Embedding 由独立 Worker 执行：

```bash
curl -X POST http://localhost:8000/api/knowledge/documents \
  -H "Idempotency-Key: notice-2026-001" \
  -F 'file=@data/research_docs/public_fund_research_faq.md' \
  -F 'metadata={"title":"公告摘要","source_type":"public_notice","permission_groups":["public"]}'

python -m app.workers.run_knowledge_ingestion --once

curl -X POST http://localhost:8000/api/knowledge/documents/<document_uuid>/publish
```

上传使用 multipart `UploadFile`，支持 txt/md/csv/pdf。文件大小、PDF 页数、Chunk 数和解析超时由 `RAG_MAX_*` 配置限制；非公开文档必须设置非 `public` 的明确权限组。权限和上传者来自服务端 Principal，客户端不能通过 Header 或 Body 提升权限。

检索顺序为：

```text
query normalization
→ intent extraction
→ metadata filter
→ permission filter
→ temporal filter
→ PostgreSQL tsvector + pgvector cosine retrieval
→ vector similarity floor
→ reciprocal rank fusion
→ optional reranker
→ deduplication
→ score threshold
→ citation objects
```

权限和时效条件在数据库内、候选 Chunk 进入排名前执行。向量候选先通过 `RAG_VECTOR_SCORE_THRESHOLD` 独立相似度下限，再参与 RRF，避免无关的向量近邻仅因排名靠前成为证据。默认不召回未发布、失效、过期或无权限文档。Embedding 在入库时生成并持久化，查询时只编码 query。默认语义模型是 384 维的 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`；hashing 仅允许 development/test 显式 fallback，生产模型加载失败时 `/health/ready` 返回 503，不会静默降级。

模型依赖已包含在 `requirements.txt`，Docker 镜像构建时会将模型缓存进不可变镜像，生产启动不依赖在线下载。

检索示例：

```bash
curl -X POST http://localhost:8000/api/rag/retrieve \
  -H "Content-Type: application/json" \
  -d '{
    "query":"ticker:NVDA 2025 research report concentration",
    "top_k":5
  }'
```

Citation 包含 `document_id`、`version`、`chunk_id`、标题、来源类型、发布日期、页码、章节、分数、quote 和权限等级。低于阈值时返回 `evidence_insufficient=true`。

## Prompt Registry、结构化输出与 Trace

金融分析 Prompt 不再硬编码于业务服务。PostgreSQL Prompt Registry 支持：

- 创建 Prompt 和不可变版本；
- `draft`、`testing`、`published`、`deprecated` 生命周期；
- 发布、回滚和同测试集版本对比；
- Prompt owner、change log、schema、模型参数和 baseline metrics；
- 每次 Provider 调用关联 `prompt_id` 与 `prompt_version`。

```bash
curl http://localhost:8000/api/prompts

curl -X POST \
  http://localhost:8000/api/prompts/financial-analysis/versions/1/publish

curl -X POST http://localhost:8000/api/prompts/financial-analysis/rollback \
  -H "Content-Type: application/json" \
  -d '{"target_version":1}'
```

Pydantic 模型是唯一输出契约，生成的 JSON Schema 禁止额外字段。输出会校验：

- ticker 必须属于当前组合；
- evidence ID 必须来自本次检索结果；
- 金融数值必须能映射到结构化输入；
- 首次校验失败后只重试一次，再进入安全 fallback。

Trace 记录运行、用户、业务场景、Prompt、模型、输入/响应哈希、数据截止时间、代码版本、检索证据、延迟、token、成本、fallback 和错误。`usage_source=provider` 表示 token 来自 Provider；`cost_source=provider` 仅在 Provider 返回真实金额时使用，否则明确标记估算来源。

## 受控公募基金研究报告 Workflow

Workflow 固定执行以下节点：

```text
validate_input → load_portfolio → calculate_risk → retrieve_evidence
→ generate_draft → validate_numbers → validate_citations
→ run_compliance_rules → request_human_review
→ approve_or_reject → publish_report
```

运行状态包括 `PENDING`、`RUNNING`、`RETRY`、`PENDING_REVIEW`、`REJECTED`、`PUBLISHED` 和 `FAILED`。每个节点持久化输入/输出摘要、状态、时间和错误类型，并受 `max_steps`、timeout 与 cost budget 限制。

启动一个幂等工作流：

```bash
curl -X POST http://localhost:8000/api/workflows/research-report \
  -H "Content-Type: application/json" \
  -d '{
    "idempotency_key":"research-run-2026-001",
    "portfolio_id":"<portfolio_id>",
    "max_steps":20,
    "node_timeout_seconds":45
  }'
```

该 POST 只创建 `WorkflowRun` 并以 HTTP 202 返回 `status=PENDING`；请求线程不会执行 RAG 或 LLM。独立 Worker 使用 `FOR UPDATE SKIP LOCKED`、租约和短事务推进节点：

```bash
python -m app.workers.run_research_workflow --once
```

租约过期后其他 Worker 可以接管；已完成节点按 `run + node + iteration` 跳过，LLM Trace 使用稳定 key，发布报告按 run 唯一，因此崩溃恢复不会重复模型 Trace 或重复发布。

人工审核：

```bash
curl -X POST http://localhost:8000/api/reviews/<review_id>/approve \
  -H "Content-Type: application/json" \
  -d '{"feedback":"同意发布"}'
```

也可使用 `/reject` 或 `/request-changes`。`user_id`、permission groups 和 `reviewer_id` 均来自认证后的服务端 Principal，请求 Body 中同名字段不会成为审计身份。幂等键按 `user_id + business_scene + idempotency_key` 唯一；总 timeout 与单节点 timeout 都会真正取消超时协程。报告发布前必须通过数值、引用、权限和禁用表达检查。

### SQLite 研究治理迁移

以下命令只适用于确实存在 `cache/portfoliopilot.db` 的 legacy 环境；全新安装应跳过。

```bash
python scripts/migrate_governance_sqlite_to_postgres.py \
  --sqlite cache/portfoliopilot.db \
  --report cache/governance_migration_report.json

python scripts/validate_governance_migration.py \
  --sqlite cache/portfoliopilot.db \
  --report cache/governance_validation_report.json

python scripts/compare_sqlite_postgres_retrieval.py \
  --sqlite cache/portfoliopilot.db \
  --query "ticker:NVDA concentration risk" \
  --query "行业政策 风险"
```

迁移会用旧库保存的已解析正文重建 Chunk 和 Embedding，不会声称恢复原始二进制文件；版本保留原始 source checksum，并另存实际重建内容 checksum。由于迁移会按当前规则重新切片，校验脚本不强求新旧 Chunk 数相等，而是核对文档 key/version/source checksum，并确保 PostgreSQL 中版本声明 Chunk 数、实际 Chunk 数与 Embedding 数一致；Prompt key/version/deployment 和稳定映射 ID 也会检查。召回对比让两侧复用同一个 Embedder，只比较公共文档，出现 PostgreSQL 缺失项时以非零状态退出。

本地开发未启用 Basic Auth 时，Principal 由 `LOCAL_PRINCIPAL_*` 配置提供。非 development 环境未认证请求固定为 `anonymous/public`，不能管理知识、Prompt、Trace 或审核任务；正式部署仍建议接入 OIDC 与细粒度 RBAC。

## 策略回测（研究演示）

```bash
python -m backtest.run_backtest \
  --portfolio data/portfolios/example_multi_asset_portfolio.csv \
  --prices data/prices/example_historical_prices.csv \
  --output cache/backtest_report.json \
  --train-window 5 \
  --holding-window 3 \
  --rebalance-frequency 3 \
  --transaction-cost-bps 5 \
  --slippage-bps 2 \
  --turnover-limit 1.0
```

第一版成交口径固定为：`t-1` 收盘后仅使用当时可得数据估计权重，订单在 `t` 收盘成交，新权重从 `t+1` 的收盘到收盘收益开始生效。报告的 `execution_convention` 保存该口径，新权重不会取得成交前或成交日已经发生的收益。

引擎逐日更新 NAV 与漂移权重。报告中的 `turnover` 比较 `pre_trade_weights` 与 `target_weights`；`executed_turnover` 比较成交前与实际成交权重，并作为成本计提依据。每次调仓保存 `pre_trade_weights`、`target_weights`、`executed_weights`、下一有效收益日的 `post_return_weights`、两种换手率和 `costs`。策略名称严格区分：

- `buy_and_hold`：初始持仓随收益漂移，不做周期调仓；
- `periodic_rebalanced_original`：周期恢复原始配比，属于 constant-mix，不称为 buy-and-hold；
- `equal_weight`、`risk_parity`、`minimum_variance`、`mean_variance`：确定性优化策略。

报告默认写入 `cache/backtest_report.json`，还包含：

- 方法论、训练/持有窗口、调仓日期、成本假设和置信区间；
- benchmark return、active return、tracking error、information ratio、beta、alpha；
- downside volatility、Sortino、historical VaR/CVaR、风险贡献和主动权重；
- 股票市场、科技行业、利率和汇率压力情景；
- `out_of_sample` 与 `data_leakage_checks`。

缺失行情不会无条件当作 0 收益。长表价格 CSV 可增加 `availability_status`，取值包括 `observed`、`exchange_closed`、`suspended`、`data_missing`、`not_listed`。只有明确的休市或停牌可合法持价，收益率本身不前向填充；覆盖率不足的资产会记录在每个调仓日的 `excluded_assets`，并退出协方差估计。Benchmark 采用同一日期对齐，覆盖不足时相关指标返回 `null`。

LLM 不参与目标权重计算。`/api/portfolio/rebalance` 返回确定性的 allocation research；LLM 只通过 `research_observations` 和 `review_priorities` 解释风险与证据，旧 `rebalance_suggestions` 保留为空数组并标记 deprecated。未提供价格 CSV 时使用固定种子的 mock 行情，并明确标记 `mock_price_data_used=true` 与 `run_mode=synthetic_smoke`。

显式持久化回测 provenance：

```bash
python -m backtest.run_backtest --persist \
  --portfolio data/portfolios/example_multi_asset_portfolio.csv \
  --prices data/prices/example_historical_prices.csv \
  --output cache/backtest_report.json \
  --train-window 5 \
  --holding-window 3 \
  --rebalance-frequency 3 \
  --portfolio-id <postgres-portfolio-uuid> \
  --portfolio-snapshot-id <valuation-snapshot-uuid>
```

`backtest_runs`、`backtest_rebalance_snapshots` 和 `backtest_strategy_results` 保存数据截止时间、代码版本、组合快照、价格来源、配置和输入哈希、成交口径、成本、调仓审计与指标。缓存键由组合快照、价格数据、配置和代码版本共同决定。

该模块尚不是生产级严格 point-in-time walk-forward 系统，具体边界见 [当前限制](docs/current-limitations.md)。

## Evaluation 与 Trace 页面

推荐的 V2 离线评测入口：

```bash
python -m scripts.build_evaluation_v2_dataset
python -m evaluation.run_v2_eval --mode synthetic_smoke
```

V2 在 `evaluation/datasets/v2/` 保存 60 个唯一案例，并将同一批 case 投影到 Retrieval、
Generation 和 Workflow 三层。数据集包含 manifest、公开来源元数据、模拟组合、train/dev/test split、
文件 SHA256 和待审核人工标签。公开资料正文均为项目自行撰写的短摘要；组合全部明确标记为
`synthetic_portfolio`。完整 schema 和审核步骤见
[V2 数据集说明](evaluation/datasets/v2/README.md) 与 [V2 评测治理](docs/evaluation-v2.md)。

V2 Retrieval 指标包括 Recall@K、Precision@K、MRR、nDCG@K、越权命中数和 stale hit rate；
Generation 包括 JSON compliance、事实正确性、数值一致性、claim support、引用精确率/完整率、
unsupported claim 与正确拒答；Workflow 分别记录工具选择、参数、执行、规则、审核路由、发布安全、
延迟以及 Provider 实际成本/估算成本。报告同时保存样本数、数据截止时间、95% Wilson 区间与
badcase 分布，不压缩成单一“准确率”。

四种模式使用独立目录，禁止互相覆盖：

```text
cache/evaluation/v2/synthetic_smoke/
cache/evaluation/v2/live_model_eval/
cache/evaluation/v2/human_gold_eval/
cache/evaluation/v2/production_monitoring/
```

当前 V2 的 60 条标签全部为 `pending`。自动生成的问题不会自行批准，所以
`human_gold_eval` 会拒绝执行，直到独立人工审核者填写完整标签并同步 manifest checksum。
`live_model_eval` 必须传入明确标记为非 mock 的真实模型预测 bundle；
`production_monitoring` 必须传入真实生产观测文件，二者都不会静默降级。

以下 V1 入口继续保留，用于兼容原有报告和回归测试。

运行检索评测：

```bash
python -m evaluation.run_retrieval_eval
```

输出 `cache/retrieval_evaluation_report.json`，包括 Recall@K、Precision@K、MRR、`citation_reference_validity`、过期命中率和未授权命中数。该引用指标只证明引用 Chunk 存在；`claim_support_rate` 另行评估 Claim 是否由引用证据支持。

运行 V1 三层完整评测：

```bash
python -m evaluation.run_full_eval --mode synthetic_smoke
```

CI 只运行可复现的 `synthetic_smoke`。真实模型评测必须显式运行，且没有所选 `AI_PROVIDER` 对应的 API Key 时直接失败，不会回退到 mock：

```bash
python -m evaluation.run_full_eval --mode live_model_eval
```

V1 输出 `cache/full_evaluation_report.json`，覆盖：

- Retrieval：召回、排序、权限泄漏和过期证据；
- Generation：JSON、风险识别、数字一致性、Groundedness、引用与幻觉；
- Workflow：`workflow_allowlist_completion_rate`、真实工具调用的 selection/required arguments/argument values/execution success、规则校验、人工采纳/修改、延迟、成本与失败率；
- 20 条组合风险用例、公开检索 golden set、权限/过期/证据不足/冲突证据；
- 中英文切片统计及标准 badcase 标签。

评测模式分为 `synthetic_smoke`、`live_model_eval`、`human_gold_eval` 和
`production_monitoring`。V1 黄金集位于 `evaluation/datasets/*_gold_v1.jsonl`，仍作为兼容夹具；
新的人工复核流程只使用 V2。两套数据都不包含真实持仓、授权研报或私有数据。

每份评测 JSON 都保存 `evaluation_mode`、UTC 生成时间、完整 Git commit SHA、模型 Provider/名称、数据集名称/版本，以及 mock、人工标签和生产数据使用标记。`hallucination_flag_rate` 必须与报告模式、样本量和有效响应数一起解释；例如 synthetic 报告中的 0 只表示该批规则夹具没有触发标记，不是“模型零幻觉”。仓库不在 README 中长期写死测试数或覆盖率，当前状态以 [CI](https://github.com/xjy0526/PortfolioPilot/actions/workflows/ci.yml) 和带日期/SHA 的审计快照为准。

Prompt Registry 当前的 `static_render_success_rate` 和 `static_expected_token_hit_rate` 仅是模板静态检查，不是模型效果 A/B。真实 Prompt A/B 必须使用相同模型、相同测试集、相同温度及其他采样参数。

Dashboard 的 `Eval & Trace` 页面同时展示评测模式、mock/live、数据集版本、样本量、数据截止日、
人工复核数量、95% 置信区间和 badcase 分布，以及 Prompt Trace 与实际/估算成本。它优先读取最新
V2 分模式报告，并兼容 V1；缺少完整 provenance 的旧报告不会显示为有效评测。只读接口包括：

```text
GET /api/evaluation/dashboard
GET /api/evaluation/traces
```

## 测试与质量检查

```bash
# 普通套件明确排除需要控制 Docker daemon 的 restart E2E。
python -m pytest -q

# PostgreSQL integration 使用已启动并迁移到 head 的测试数据库。
TEST_DATABASE_URL=postgresql+asyncpg://portfoliopilot:portfoliopilot@localhost:5432/portfoliopilot \
  python -m pytest -o addopts="" -m "postgres and not postgres_restart" -q

# 独立 Compose E2E 会构建 app、创建隔离数据库、真实重启 PostgreSQL 并自动清理。
python -m pytest --confcutdir=tests/e2e -o addopts="" \
  -m postgres_restart -q -s tests/e2e/test_postgres_restart_persistence.py

ruff check .
mypy .
python -m compileall -q app analytics backtest evaluation prompts rag routes services workflows
node --check static/app.js
pip check
python scripts/check_evaluation_integrity.py

# API 迁移前后应保持 route 与核心 OpenAPI contract 快照一致。
python -m scripts.export_api_contract --check
python -m pytest -q tests/unit/test_api_contracts.py
```

普通 `pytest` 对 restart 场景显示 deselected，而不是 skipped；GitHub Actions 的 `PostgreSQL restart persistence E2E` job 必须单独成功，后续 production container job 才会执行。该 E2E 不只检查端口恢复，还验证交易、重建持仓、估值、文档、chunk、pgvector embedding、Prompt 版本、LLM Trace、Workflow、人工审核和发布报告在重启前后具有相同 ID/checksum，且幂等复跑不产生重复行。详细分层、前置条件和故障排查见 [docs/testing.md](docs/testing.md)。

完整评测用于模型与流程质量，不替代单元测试、PostgreSQL integration 和 restart persistence E2E。

## Render 部署

仓库包含 [Dockerfile](Dockerfile) 和 [render.yaml](render.yaml)。Blueprint 定义两个不同的服务：

- `portfolio-pilot`：FastAPI Web Service；
- `portfolio-pilot-daily-pipeline`：一次性 Cron Job，周一至周五 `22:00 UTC` 执行 `python -m app.workers.run_daily_pipeline`，完成后退出。

使用 Render Blueprint 时：

1. 连接 GitHub 仓库并读取 `render.yaml`；
2. 为 Web 和 Cron 分别配置同一 PostgreSQL 的 `DATABASE_URL`；Cron 使用 A 股行情时还需配置 `TUSHARE_TOKEN`；
3. Secret 只通过 Render Dashboard 或 Secret Manager 注入，`render.yaml` 仅保留 `sync: false`；
4. 部署前执行 `alembic upgrade head` 和 `python -m app.core.preflight`；
5. 将 PostgreSQL 与 S3 bucket 作为一组纳入备份、恢复和一致性验证。

Render Web、Cron 和 Background Worker 是独立实例，本地目录和 Persistent Disk 不会在这些服务之间自动共享，Cron 也不能依赖 Web 的 `/app/cache`。`OBJECT_STORAGE_BACKEND=local` 仅用于 development/test 或只读演示；生产可写模式必须配置共享 S3-compatible storage：

```env
ENVIRONMENT=production
READ_ONLY_DEMO=false
OBJECT_STORAGE_BACKEND=s3
S3_ENDPOINT_URL=https://s3.example.com
S3_BUCKET=portfolio-pilot
S3_REGION=ap-southeast-1
S3_ACCESS_KEY_ID=from-secret-manager
S3_SECRET_ACCESS_KEY=from-secret-manager
S3_PREFIX=research-ingestion
S3_FORCE_PATH_STYLE=false
```

AWS S3 可以省略 `S3_ENDPOINT_URL`；第三方 S3-compatible 服务应提供 HTTPS endpoint。旧 `OBJECT_STORAGE_*` 名称暂时兼容，新部署应使用 `S3_*`。

只读公开演示可以匿名访问，但任何可写公网部署都必须配置认证。当前仓库提供的是可选 Basic Auth；正式机构环境仍应接入 OIDC/SAML、个人身份、RBAC/ABAC、职责分离、密钥管理、备份和集中审计。

Production 默认 `READ_ONLY_DEMO=true`，所有 HTTP mutation 返回 403。若显式关闭只读模式，则启动前必须配置认证和 S3-compatible 对象存储；开发身份 Header 在 production 被拒绝。部署流水线仅在 `main` 的 CI 成功后使用 Git SHA 镜像，先执行 Alembic migration job，再执行 readiness 与只读 smoke test。

完整服务拓扑、环境变量、preflight、对象权限及备份恢复边界见 [docs/deployment.md](docs/deployment.md)。

## 数据与安全边界

- 示例组合、FAQ、公告和研报摘要均为公开或模拟内容。
- 不要提交真实持仓、内部研报、实习单位文件、API Key 或客户数据。
- `cache/`、`portfolio.csv`、`rag_documents/` 和 `.env` 默认不进入 Git。
- Knowledge、Workflow 和 Review API 的权限与身份只来自服务端 Principal；当前 Basic Auth 仍是演示级认证，机构部署应接入 OIDC/SAML。
- Shadow Agent 是默认关闭的模拟扩展，不连接券商，也不属于受控研究报告 Workflow。
- SQLite、行情许可、回测和 fallback 边界见 [docs/current-limitations.md](docs/current-limitations.md)。
- 机构差距与整改状态见 [docs/audits/institutional_gap_analysis.md](docs/audits/institutional_gap_analysis.md)。
- 完整 API 列表见 [docs/api.md](docs/api.md)。

## 项目结构

```text
analytics/             风险与组合指标
app/db/                SQLAlchemy Async ORM、Session 和 Repository
app/api/               DB-backed portfolio、market-data、health、governed evaluation API
app/domain/            账本重建的不可变领域结果
app/providers/         Tushare/yfinance 市场数据适配器
app/services/          账本、CSV 导入、持仓重建、估值和旧 API 适配
app/workers/           独立 Session、advisory lock 与任务入口
backtest/              策略比较与研究演示报告
evaluation/            Retrieval / Generation / Workflow 评测
portfolio_optimizer/   组合权重研究策略
prompts/               Prompt 模型、Registry 与输出契约
rag/                   文档、版本、解析、检索与权限过滤
routes/                兼容/实验路由及待迁移的 governed router；evaluation 已为 shim
migrations/            Alembic async migration 环境与版本
scripts/               SQLite 兼容迁移、API contract 导出等运维入口
services/llm_client.py Qwen/OpenAI-Compatible 中立客户端
services/llm/          结构化 Provider 与旧调用兼容层
services/market_data/  PostgreSQL 风险价格适配与旧历史价格兼容服务
static/                Dashboard 前端
workflows/             受控研究报告状态机
tests/contracts/       全量 route 与核心 OpenAPI contract 快照
tests/                 单元、API contract 与 PostgreSQL 集成测试
```

## License

本项目使用 [MIT License](LICENSE)。
