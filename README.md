# PortfolioPilot

PortfolioPilot 是一个面向 A 股与美股的多市场投资组合风险分析与证据驱动投研平台。当前版本基于 FastAPI、PostgreSQL 数据库基础、SQLite 兼容读取和原生 Web 前端，并提供两种使用模式：

- `personal`：个人投资组合看板和研究辅助功能。
- `fund_research`：面向受控投研流程，使用中性研究表达，并提供知识治理、Prompt 治理、模型 Trace 和人工审核工作流。

核心链路聚焦 A 股、美股和 ETF。Polymarket、Telegram、Parqet 与 Shadow Agent 作为可选扩展保留且默认关闭。所有分析与报告仅用于研究和软件演示，不构成投资建议、交易指令或收益承诺。

## 项目导览

**核心定位：** PortfolioPilot 的重点不是“让 LLM 推荐股票”，而是把多市场持仓、确定性风险计算、证据检索、结构化生成、人工审核和评测组织成一条可追溯的研究链路。

**快速体验：**

1. 导入示例组合，查看收益、波动、回撤和行情覆盖率。
2. 切换到 `fund_research`，运行一份受控研究报告。
3. 从报告引用回看知识片段、Prompt 版本和模型 Trace。
4. 展示人工审核与发布状态，再打开 Evaluation 页面说明如何定位 badcase。

**设计主线：** 数据质量 -> 确定性风险计算 -> Hybrid RAG -> 结构化输出校验 -> Human-in-the-loop。项目可在无真实账户和付费模型时离线演示，但所有 mock/fallback 都必须显式标记。

## 核心能力

| 领域 | 当前能力 |
|---|---|
| 组合数据 | PostgreSQL 交易账本、标准流水/旧持仓 CSV 导入、任意 `as_of` 持仓重建 |
| 市场数据 | Tushare A 股、yfinance 美股/ETF 研究行情、历史 FX、Provider symbol 映射 |
| 风险分析 | PostgreSQL 复权行情、收益/波动/回撤/Sharpe、覆盖率、陈旧与缺失行情语义 |
| 知识库 | `txt/md/csv/pdf` 接入、版本、checksum 去重、发布/失效、权限与有效期过滤 |
| 检索 | Query intent、metadata/permission/temporal filter、BM25、dense、RRF、可插拔 reranker |
| LLM | Provider 抽象、Prompt Registry、严格 Pydantic 输出、ticker/引用/数字一致性校验 |
| Workflow | 固定节点、工具 allowlist、幂等运行、人工审核、规则校验、审计记录、受控发布 |
| Evaluation | Retrieval、Generation、Workflow 三层评测，Trace、badcase 与前端指标页面 |
| 回测 | t-1 估计/t 收盘成交/t+1 生效、逐日漂移、成本、可用性矩阵、Benchmark 与 PostgreSQL provenance；尚非生产级完整 walk-forward 系统 |

## 快速开始

建议使用 Python 3.12。

macOS / Linux：

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
docker compose up -d postgres
alembic upgrade head
python scripts/bootstrap_portfolio.py --base-currency CNY
python3 main.py
```

Windows：

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
docker compose up -d postgres
alembic upgrade head
python scripts/bootstrap_portfolio.py --base-currency CNY
python main.py
```

也可以使用仓库中的 [start.sh](start.sh) 或 [start.bat](start.bat)。启动后访问：

- Dashboard：<http://localhost:8000>
- Swagger API：<http://localhost:8000/docs>
- 兼容健康检查：<http://localhost:8000/health>
- 存活检查：<http://localhost:8000/health/live>
- PostgreSQL 就绪检查：<http://localhost:8000/health/ready>

未配置模型 API Key 时，结构化分析会明确返回 `ai_available=false` 的安全 fallback。市场同步不会生成 mock 行情；Provider 不可用时 `sync_run` 失败并保留错误摘要。仅独立回测 CLI 在没有价格 CSV 时允许生成显式标记的可复现 mock 行情。

## PostgreSQL 交易账本与估值

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

旧 SQLite 数据库继续提供兼容读取。应用 migration 后可以显式迁移支持的数据：

```bash
python scripts/migrate_sqlite_to_postgres.py \
  --sqlite-path cache/portfoliopilot.db
```

脚本当前幂等迁移旧组合总览快照和可选 Shadow 模拟交易；知识库、Prompt、Workflow 等 SQLite 数据仍按原数据契约保留。

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

旧 `ticker,shares,buy_price,...` 持仓 CSV 仍可上传，但证券只会转换成 `opening_balance`，现金行转换成 opening cash deposit，响应明确返回：

```json
{"history_completeness":"opening_balance_only"}
```

这类数据只表示缺少完整交易历史的期初状态。

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

估值接口不会使用 `as_of` 之后的交易、行情或 FX。响应包含 snapshot ID、`input_hash`、`data_as_of`、价格/FX ID、warning 和 `history_completeness`。

## 应用模式

在 `.env` 中选择模式：

```env
APP_MODE=personal
# APP_MODE=fund_research
```

| 功能 | `personal` | `fund_research` |
|---|:---:|:---:|
| 个人组合分析 | ✓ | ✓ |
| Tech Picks | ✓ | 隐藏 |
| Shadow Agent | 默认关闭，可选模拟扩展 | 禁用入口与定时运行 |
| 自动交易类入口 | 保留原个人功能 | 隐藏 |
| 中性研究表达 | 部分 | 强制 |
| 受控研究报告 Workflow | ✓ | ✓，推荐流程 |
| 当前模式标识 | ✓ | ✓ |

`fund_research` 中的建议会表述为“研究关注”“维持观察”“降低风险暴露”“人工复核”等，不提供 Buy/Sell 式执行指令。

## 可选扩展

以下扩展默认关闭，需要在 `.env` 中显式启用：

```env
ENABLE_POLYMARKET=false
ENABLE_TELEGRAM=false
ENABLE_PARQET=false
ENABLE_SHADOW_AGENT=false
```

关闭扩展不影响 CSV 导入、A 股/美股风险分析、RAG、结构化 LLM 分析和回测命令。

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

业务层仅依赖 `LLMProvider`，内置 `QwenProvider`、`MockProvider` 和 `OptionalOpenAICompatibleProvider`。本地还可以在 Dashboard 的“操作 → API 设置”中保存千问、FMP 和联系邮箱配置；该入口只允许 localhost 或已启用 Dashboard 认证的请求使用，密钥不会在页面回显。

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
  -d '{"portfolio_id":"<portfolio_id>","lang":"zh","top_k":5,"permission_groups":["public"]}'
```

## 轻量企业知识库与 Hybrid RAG

知识库使用 SQLite 持久化以下对象：

- `DocumentMetadata`
- `DocumentVersion`
- `DocumentChunk`
- `IngestionJob`
- `PermissionContext`

文档支持 `.txt`、`.md`、`.csv` 和 `.pdf`。解析器优先按标题、章节、段落和 PDF 页码切片，固定长度仅作为 fallback。内容 checksum 不变时不会重复索引；内容变化会创建新版本；只有已发布且当前有效的版本能够进入正常检索。

上传并发布公开模拟文档：

```bash
curl -X POST http://localhost:8000/api/knowledge/documents \
  -H "Content-Type: application/json" \
  -d '{
    "filename":"notice.md",
    "content":"# 公告摘要\n\n这是公开模拟内容。",
    "metadata":{
      "title":"公告摘要",
      "source_type":"public_notice",
      "permission_groups":["public"]
    }
  }'

curl -X POST http://localhost:8000/api/knowledge/documents/<document_id>/publish
```

PDF 必须通过 `content_base64` 上传。非公开文档必须设置非 `public` 的明确权限组。

检索顺序为：

```text
query normalization
→ intent extraction
→ metadata filter
→ permission filter
→ temporal filter
→ BM25 + dense retrieval
→ reciprocal rank fusion
→ optional reranker
→ deduplication
→ score threshold
→ citation objects
```

权限和时效条件在候选 chunk 进入相似度计算之前执行。默认不召回未发布、失效、过期或无权限文档。Dense retrieval 在无外部模型时保留 hashing fallback；可选安装：

```bash
pip install sentence-transformers faiss-cpu
```

检索示例：

```bash
curl -X POST http://localhost:8000/api/rag/retrieve \
  -H "Content-Type: application/json" \
  -d '{
    "query":"ticker:NVDA 2025 research report concentration",
    "top_k":5,
    "permission_groups":["public"]
  }'
```

Citation 包含 `document_id`、`version`、`chunk_id`、标题、来源类型、发布日期、页码、章节、分数、quote 和权限等级。低于阈值时返回 `evidence_insufficient=true`。

## Prompt Registry、结构化输出与 Trace

金融分析 Prompt 不再硬编码于业务服务。SQLite Prompt Registry 支持：

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

Trace 记录运行、用户、业务场景、Prompt、模型参数、输入哈希、检索证据、工具调用、延迟、token、估算成本、schema 状态、fallback、错误和人工审核结果。

## 受控公募基金研究报告 Workflow

Workflow 固定执行以下节点：

```text
validate_input → load_portfolio → calculate_risk → retrieve_evidence
→ generate_draft → validate_numbers → validate_citations
→ run_compliance_rules → request_human_review
→ approve_or_reject → publish_report
```

运行状态包括 `DRAFT`、`RUNNING`、`PENDING_REVIEW`、`APPROVED`、`REJECTED`、`PUBLISHED` 和 `FAILED`。每个节点持久化输入/输出摘要、状态、时间和错误类型，并受 `max_steps`、timeout 与 cost budget 限制。

启动一个幂等工作流：

```bash
curl -X POST http://localhost:8000/api/workflows/research-report \
  -H "Content-Type: application/json" \
  -d '{
    "user_id":"analyst-1",
    "idempotency_key":"research-run-2026-001",
    "max_steps":20
  }'
```

人工审核：

```bash
curl -X POST http://localhost:8000/api/reviews/<review_id>/approve \
  -H "Content-Type: application/json" \
  -d '{"reviewer_id":"reviewer-1","feedback":"同意发布"}'
```

也可使用 `/reject` 或 `/request-changes`。报告发布前必须通过数值、引用、权限和禁用表达检查。LLM 不能跳过规则检查或人工审核，工具 allowlist 不包含 Shadow Trading 或真实交易工具。

## 策略回测（研究演示）

```bash
python -m backtest.run_backtest \
  --portfolio data/portfolios/example_multi_asset_portfolio.csv \
  --prices data/prices/example_historical_prices.csv \
  --benchmark data/prices/benchmark.csv \
  --train-window 60 \
  --holding-window 20 \
  --rebalance-frequency 20 \
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
  --portfolio-id <postgres-portfolio-uuid> \
  --portfolio-snapshot-id <valuation-snapshot-uuid>
```

`backtest_runs`、`backtest_rebalance_snapshots` 和 `backtest_strategy_results` 保存数据截止时间、代码版本、组合快照、价格来源、配置和输入哈希、成交口径、成本、调仓审计与指标。缓存键由组合快照、价格数据、配置和代码版本共同决定。

该模块尚不是生产级严格 point-in-time walk-forward 系统，具体边界见 [当前限制](docs/current-limitations.md)。

## Evaluation 与 Trace 页面

运行检索评测：

```bash
python -m evaluation.run_retrieval_eval
```

输出 `cache/retrieval_evaluation_report.json`，包括 Recall@K、Precision@K、MRR、`citation_reference_validity`、过期命中率和未授权命中数。该引用指标只证明引用 Chunk 存在；`claim_support_rate` 另行评估 Claim 是否由引用证据支持。

运行三层完整评测：

```bash
python -m evaluation.run_full_eval --mode synthetic_smoke
```

CI 只运行可复现的 `synthetic_smoke`。真实 Qwen 评测必须显式运行，且没有 `QWEN_API_KEY` 时直接失败，不会回退到 mock：

```bash
python -m evaluation.run_full_eval --mode live_model
```

输出 `cache/full_evaluation_report.json`，覆盖：

- Retrieval：召回、排序、权限泄漏和过期证据；
- Generation：JSON、风险识别、数字一致性、Groundedness、引用与幻觉；
- Workflow：`workflow_allowlist_completion_rate`、真实工具调用的 selection/required arguments/argument values/execution success、规则校验、人工采纳/修改、延迟、成本与失败率；
- 20 条组合风险用例、公开检索 golden set、权限/过期/证据不足/冲突证据；
- 中英文切片统计及标准 badcase 标签。

评测模式分为 `synthetic_smoke`、`live_model_eval`、`human_gold_eval` 和 `production_monitoring`。后两类必须接入人工标注或生产观测后才能执行，不能用 mock 冒充。版本化黄金集位于 `evaluation/datasets/*_gold_v1.jsonl`，全部为本项目自行构造的公开培训夹具，不包含真实持仓、授权研报或私有数据。

Prompt Registry 当前的 `static_render_success_rate` 和 `static_expected_token_hit_rate` 仅是模板静态检查，不是模型效果 A/B。真实 Prompt A/B 必须使用相同模型、相同测试集、相同温度及其他采样参数。

Dashboard 的 `Eval & Trace` 页面展示 Prompt 版本对比、badcase、延迟/成本和人工采纳率。只读接口包括：

```text
GET /api/evaluation/dashboard
GET /api/evaluation/traces
```

## 测试与质量检查

```bash
python -m pytest -q
TEST_DATABASE_URL=postgresql+asyncpg://portfoliopilot:portfoliopilot@localhost:5432/portfoliopilot \
  python -m pytest -m postgres -q
ruff check .
mypy
python -m compileall -q app analytics backtest evaluation prompts rag routes services workflows
node --check static/app.js
pip check
```

完整评测用于模型与流程质量，不替代单元测试和 API 集成测试。

## Render 部署

仓库包含 [Dockerfile](Dockerfile) 和 [render.yaml](render.yaml)。使用 Render Blueprint 时建议：

1. 连接 GitHub 仓库并读取 `render.yaml`；
2. 配置托管 PostgreSQL 的 `DATABASE_URL`，并设置 `QWEN_API_KEY`、`FMP_API_KEY`、`DASHBOARD_USER` 和 `DASHBOARD_PASSWORD`；
3. 将 Persistent Disk 挂载到 `/app/cache`；
4. 将真实组合、SQLite、行情和运行报告保存在持久化目录；
5. 生产环境使用受信任的权限主体生成知识库 permission groups。

公网部署必须配置 Dashboard 认证。当前仓库提供的是可选 Basic Auth；正式机构环境仍应接入 OIDC/SAML、个人身份、RBAC/ABAC、职责分离、密钥管理、备份和集中审计。

## 数据与安全边界

- 示例组合、FAQ、公告和研报摘要均为公开或模拟内容。
- 不要提交真实持仓、内部研报、实习单位文件、API Key 或客户数据。
- `cache/`、`portfolio.csv`、`rag_documents/` 和 `.env` 默认不进入 Git。
- Knowledge API 中客户端传入的权限组仅适合本地开发；生产环境必须由认证网关注入可信权限上下文。
- Shadow Agent 是默认关闭的模拟扩展，不连接券商，也不属于受控研究报告 Workflow。
- SQLite、行情许可、回测和 fallback 边界见 [docs/current-limitations.md](docs/current-limitations.md)。
- 机构差距与整改状态见 [docs/audits/institutional_gap_analysis.md](docs/audits/institutional_gap_analysis.md)。
- 完整 API 列表见 [docs/api.md](docs/api.md)。

## 项目结构

```text
analytics/             风险与组合指标
app/db/                SQLAlchemy Async ORM、Session 和 Repository
app/api/               DB-backed portfolio、import、market-data、health API
app/domain/            账本重建的不可变领域结果
app/providers/         Tushare/yfinance 市场数据适配器
app/services/          账本、CSV 导入、持仓重建、估值和旧 API 适配
app/workers/           独立 Session、advisory lock 与任务入口
backtest/              策略比较与研究演示报告
evaluation/            Retrieval / Generation / Workflow 评测
portfolio_optimizer/   组合权重研究策略
prompts/               Prompt 模型、Registry 与输出契约
rag/                   文档、版本、解析、检索与权限过滤
routes/                FastAPI 路由
migrations/            Alembic async migration 环境与版本
scripts/               SQLite 兼容迁移等运维入口
services/llm_client.py Qwen/OpenAI-Compatible 中立客户端
services/llm/          结构化 Provider 与旧调用兼容层
services/market_data/  PostgreSQL 风险价格适配与旧历史价格兼容服务
static/                Dashboard 前端
workflows/             受控研究报告状态机
tests/                 单元与 API 集成测试
```

## License

本项目使用 [MIT License](LICENSE)。
