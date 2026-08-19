# PortfolioPilot 发布前基线审计（2026）

> 审计性质：release readiness audit，不是生产验收，也不是投资、模型效果或合规认证。
>
> 审计分支：`agent/postgres-rag-governance`
>
> 审计提交：`bb4e5d9e176503cc7d19b047583a70c1ac9fdde6`
>
> 执行日期：2026-08-19；本轮命令完成时间约为 2026-08-19 10:37 UTC（18:37 Asia/Shanghai）。

## 1. 执行结论

建议继续把 `agent/postgres-rag-governance` 作为唯一候选主线，但当前**不建议直接合并或发布**。

正向证据：

- 当前分支相对 `origin/main` 为 ahead 9 / behind 0，与远端同名分支一致；
- PR #2 当前 head 与本次审计 SHA 一致，GitHub 上质量检查、Secret scan 和容器构建均成功；
- 本地连接真实 PostgreSQL/pgvector 测试服务后，完整测试为 `570 passed, 1 skipped`；
- 被默认跳过的 PostgreSQL 重启持久化测试已单独启用并通过；
- Alembic 最新版本 downgrade/upgrade 往返、正式入口启动、liveness 和 readiness 均通过。

发布阻断项：

1. 风险引擎仍会无条件前向填充价格并把缺失收益填成 0；完全缺少时序指标时仍生成数值型低风险分。
2. 部署没有把 Git SHA 注入 `CODE_VERSION`，且当前风险 API 没有实际写入 `risk_runs`，风险与缓存 provenance 不完整。
3. `data/object_storage/` 未被 `.gitignore` 或 `.dockerignore` 保护，`rag_documents/` 和本地历史行情也未全部排除出 Docker build context。

## 2. Git 与 PR 状态

### 2.1 分支状态

本轮先执行了 `git fetch --all --prune`。结果如下：

| 项目 | 结果 |
|---|---|
| 当前分支 | `agent/postgres-rag-governance` |
| 当前 SHA | `bb4e5d9e176503cc7d19b047583a70c1ac9fdde6` |
| 对远端同名分支 | ahead 0 / behind 0 |
| 对 `origin/main` | ahead 9 / behind 0 |
| 初始工作区 | 干净 |
| 初始 `git diff --check` | 通过 |

### 2.2 PR #1

| 项目 | GitHub 实时结果 |
|---|---|
| URL | <https://github.com/xjy0526/PortfolioPilot/pull/1> |
| 标题 | Upgrade PortfolioPilot for institutional fund research |
| 状态 | OPEN、Draft |
| base / head | `main` / `agent/institutional-research-platform` |
| head SHA | `83fbfb7b7029c4c32253a92e5408fe6c2349cbe1` |
| commits | 4 |
| changed files | 155 |
| diff | +12,006 / -1,966 |
| merge 状态 | CLEAN、MERGEABLE |
| CI | `Lint, type check, and test`: SUCCESS；`Build container`: SUCCESS |

PR #1 的上述检查完成于 2026-08-11 UTC。该 PR 没有当前 PR #2 中新增的 Secret scan 检查。

### 2.3 PR #2

| 项目 | GitHub 实时结果 |
|---|---|
| URL | <https://github.com/xjy0526/PortfolioPilot/pull/2> |
| 标题 | Migrate governed research workflows and hybrid retrieval to PostgreSQL |
| 状态 | OPEN、Draft |
| base / head | `main` / `agent/postgres-rag-governance` |
| head SHA | `bb4e5d9e176503cc7d19b047583a70c1ac9fdde6` |
| commits | 9 |
| changed files | 233 |
| diff | +26,848 / -2,734 |
| merge 状态 | CLEAN、MERGEABLE |
| CI | `Lint, type check, and test`: SUCCESS；`Secret scan`: SUCCESS；`Build container`: SUCCESS |

PR #2 的上述检查完成于 2026-08-13 UTC，检查对象就是本次审计 SHA。

### 2.4 PR 包含关系

`git merge-base --is-ancestor` 返回 0，且两个 head 的差异为 left 0 / right 5。因此 PR #1 的 4 个提交全部位于 PR #2 历史中，PR #2 在其上新增 5 个提交。结论：**PR #1 已被 PR #2 在 Git 提交拓扑上完整覆盖，不应再单独合并 PR #1。**

本阶段没有合并、关闭或修改任何 PR，也没有删除或 force push 任何分支。

## 3. 当前架构主线

### 3.1 正式应用入口

- ASGI 应用：`main.py:app`。
- 本地入口：`start.sh`、`start.bat` 或 `uvicorn main:app`。
- 容器入口：`Dockerfile` 中的 `uvicorn main:app`。
- `main.py` 同时挂载新 DB API 和兼容路由，并通过 lifespan 复用 Embedder、对象存储和 LLM HTTP 资源。

### 3.2 `app/api` 与 `routes`

- `app/api/health.py`、`app/api/portfolios.py`、`app/api/market_data.py` 是 PostgreSQL 原生 API，依赖每请求独立 `AsyncSession`。
- 根目录 `routes/` 是兼容与迁移中的 HTTP 层，不等同于“全部 legacy”。
- `routes/knowledge.py`、`routes/prompts.py`、`routes/workflows.py` 和 `routes/research.py` 已调用 PostgreSQL 服务。
- `routes/portfolio.py`、`routes/analytics.py` 部分通过 `LegacyPortfolioAdapter` 读取 PostgreSQL 快照，但仍保留 `state.portfolio_data` 的 demo/旧页面分支。
- `routes/analysis.py`、`routes/refresh.py`、`routes/streaming.py` 仍主要依赖进程内全局状态和旧 service。

### 3.3 `app/services` 与 `services`

- `app/services/` 是新主线：交易账本、CSV 导入、证券主数据、持仓重建、组合估值、行情同步、研究知识库、回测持久化和兼容适配。
- 根目录 `services/` 主要是旧 dashboard 能力与过渡模块，包括 refresh、FMP/yfinance enrichment、Telegram、Shadow Agent 和旧组合构建。
- 根目录中的 `services/financial_analysis.py`、`services/llm/`、`services/llm_client.py` 和 `services/market_data/postgres_provider.py` 已承担新主线的一部分，当前还不能简单按目录整体删除。
- `services/vertex_ai.py` 是兼容导入 shim，实际实现已转向 Qwen/OpenAI-Compatible 客户端。

### 3.4 `app/db` 与根目录 `database.py` / `models.py`

- `app/db/models/`：SQLAlchemy 2.x Declarative PostgreSQL 模型。
- `app/db/repositories/`：PostgreSQL 查询和原子写入边界。
- `app/db/session.py`：asyncpg engine、`async_sessionmaker`、每请求和每 worker 独立 Session。
- 根目录 `database.py`：SQLite 兼容层，仍服务于历史分析和默认关闭的 Shadow 扩展；不是核心账本事实源。
- 根目录 `models.py`：旧 API/UI 使用的 Pydantic 展示模型，不是 ORM 模型。
- `state.py`：旧 dashboard/demo 的进程内状态，不是持久化事实源。

### 3.5 `app/providers` 与 `fetchers`

- `app/providers/market_data/` 定义统一的 `MarketDataProvider`，主实现为 Tushare 和 yfinance research-only adapter。
- `app/providers/embeddings.py` 定义可复用 Embedder，并由应用生命周期资源管理器持有。
- 根目录 `fetchers/` 是旧 dashboard 的直接数据抓取层，包含 FMP、Parqet、yfinance、技术指标和 demo 数据。
- FMP 当前仍用于基本面 enrichment，不是核心价格事实源。

### 3.6 RAG 主实现与兼容实现

- 主实现：`app/services/research_knowledge.py` + `app/db/repositories/governance.py` + PostgreSQL FTS + pgvector + RRF。
- 主入口：`routes/knowledge.py`、`routes/research.py` 和 `workflows/research_report.py`。
- 兼容实现：`rag/retriever.py` 的本地目录/FAISS/NumPy fallback，以及 `rag/repository.py`、`rag/service.py` 的 SQLite 工具链。
- 正常 API 不会静默切换到本地 RAG；兼容实现仍被离线工具和部分测试使用。

### 3.7 Workflow 主实现

- 状态机：`workflows/research_report.py`。
- HTTP：`routes/workflows.py`。
- PostgreSQL 状态：`workflow_runs`、`workflow_steps`、`review_tasks`、`review_decisions`、`published_reports`。
- 执行器：`app/workers/run_research_workflow.py`，采用 lease、checkpoint 和短事务恢复。

### 3.8 Worker / Cron 入口

正式 Worker：

- `python -m app.workers.run_market_sync`
- `python -m app.workers.run_position_rebuild`
- `python -m app.workers.run_daily_pipeline`
- `python -m app.workers.run_knowledge_ingestion --once`
- `python -m app.workers.run_research_workflow --once`
- `python -m app.workers.run_embedding_backfill`
- `python -m app.workers.run_storage_cleanup`

旧入口：`run_job.py` + `Dockerfile.job`。它仍描述 Parqet/FMP/Telegram 和 Europe/Berlin 旧流程，不属于当前 PostgreSQL worker 主线。Web 进程没有 APScheduler。

### 3.9 部署与 CI 入口

- 主容器：`Dockerfile`。
- PostgreSQL 开发服务：`docker-compose.yml`，镜像为 `pgvector/pgvector:0.8.2-pg16`。
- GCP 部署：`.github/workflows/deploy.yml`，只在 `main` 的 CI 成功后执行 migration job 和 Cloud Run 部署。
- Render Blueprint：`render.yaml`，与 GCP 部署并存。
- CI：`.github/workflows/ci.yml`，包含 Python 3.12、PostgreSQL、Ruff、Mypy、Alembic 往返、synthetic smoke、pytest coverage、pip-audit、Gitleaks 和 Docker build。

### 3.10 测试目录与 marker

- `tests/`：旧模块、API contract 和跨模块回归测试。
- `tests/unit/`：新 PostgreSQL模型、provider、ledger、RAG、worker 和存储单元测试。
- `tests/integration/`：PostgreSQL persistence、ledger/market data、RAG governance。
- `tests/e2e/`：Docker PostgreSQL 重启持久化。
- 自定义 marker：`postgres`、`postgres_restart`；异步测试使用 pytest-asyncio 的 `asyncio` marker。

## 4. 实际质量基线

### 4.1 执行环境

| 项目 | 实际值 |
|---|---|
| macOS 工具默认 `python` | 3.11.15 |
| 仓库 `venv/bin/python` | 3.13.9 |
| 项目目标 / GitHub CI | 3.12 |
| Node.js | v26.7.0 |
| Docker Desktop | 4.86.0 |
| Docker Engine | 29.7.2，linux/arm64 |
| Docker Compose | v5.3.1 |
| PostgreSQL image | pgvector 0.8.2 + PostgreSQL 16 |

测试数据库为本地 Docker 中的 `portfoliopilot_test`。数据库和 PostgreSQL 集成测试使用真实 PostgreSQL/pgvector，不是 mock 数据库；Embedding 使用 hashing test provider，不是生产语义模型。

### 4.2 命令与结果

| 命令 | 结果 |
|---|---|
| `ruff check .` | PASS，`All checks passed!` |
| 宿主机 `mypy .` | FAIL：`app/providers/embeddings.py:74` 1 个类型错误；宿主环境为 mypy 1.14.1 / torch 2.11.0 |
| `venv/bin/python -m mypy .` | PASS：74 source files；venv 为 mypy 2.3.0 / torch 2.13.0 |
| `venv/bin/python -m pytest -q` | PASS：570 passed，1 skipped，1 warning，3.63s |
| `venv/bin/python -m pytest --cov=. --cov-report=term-missing` | PASS：570 passed，1 skipped，2 warnings，6.36s |
| `venv/bin/python -m compileall -q app analytics backtest evaluation prompts rag routes services workflows` | PASS |
| `node --check static/app.js` | PASS |
| `venv/bin/python -m pip check` | PASS：No broken requirements found；另有本机 pip cache 权限 warning |
| `git diff --check` | PASS |
| Alembic `upgrade head` | PASS，最终 revision `20260813_0005` |
| Alembic `downgrade -1` 后 `upgrade head` | PASS |
| `pytest -m postgres -q -rs` | PASS：15 passed，1 skipped，555 deselected |
| `RUN_POSTGRES_RESTART_TEST=1 ... pytest tests/e2e/...` | PASS：1 passed，1.92s |
| 正式入口 `uvicorn main:app` | PASS，启动和优雅关闭正常 |
| `GET /health/live` | HTTP 200，`status=ok` |
| `GET /health/ready` | HTTP 200；DB available、pgvector 0.8.2、schema current、无缺表 |

readiness 本次使用 `EMBEDDING_PROVIDER=hashing`，返回 `semantic=false`。这不能证明生产 SentenceTransformer 模型可以在目标 Cloud Run 环境成功加载。

### 4.3 Mypy 环境差异

不能把本地 Mypy 结果简单写成“全部环境通过”。项目 venv 和 GitHub CI 通过，但未限定环境的宿主 `mypy .` 在同一 SHA 上失败。直接原因是 Python、Mypy 和 torch 类型定义版本不同。该现象说明本地开发环境没有收敛到项目声明的 Python 3.12，工具依赖也仍可能漂移。

## 5. 覆盖率结果

本次实际命令结果：

- Statements：23,279
- Missed：9,148
- Total coverage：61%
- 测试：570 passed，1 skipped

关键模块覆盖率：

| 模块 | 覆盖率 |
|---|---:|
| `app/api/portfolios.py` | 41% |
| `app/services/market_data_sync.py` | 37% |
| `app/providers/market_data/tushare.py` | 28% |
| `app/providers/market_data/yfinance.py` | 51% |
| `app/workers/jobs.py` | 24% |
| 多个 `app/workers/run_*.py` | 0% |
| `app/storage/s3.py` | 21% |
| `workflows/research_report.py` | 56% |
| `app/services/research_knowledge.py` | 85% |
| `app/services/portfolio_valuation.py` | 86% |
| `app/services/transaction_import.py` | 88% |
| `backtest/strategy_backtester.py` | 89% |

注意：`--cov=.` 把测试代码、migration、脚本和 legacy 模块也纳入分母；当前 CI 没有 `fail_under`。因此 61% 只能描述这次命令的仓库级语句覆盖，不能直接代表核心生产路径覆盖或发布质量。

## 6. Skipped Tests 与 warning

默认完整测试唯一跳过项：

- `tests/e2e/test_postgres_restart_persistence.py`
- 原因：默认未设置 `RUN_POSTGRES_RESTART_TEST=1`，避免普通测试擅自重启数据库。

本轮 Docker 条件满足后已显式启用该测试，结果为 1 passed。因此没有因为外部数据库不可用而遗留的未执行 PostgreSQL测试。

仍存在两个 warning：

1. FastAPI TestClient 报 Starlette/httpx 兼容弃用提示。
2. coverage 运行中出现一次未关闭 SQLite connection 的 `ResourceWarning`，来源于旧 knowledge test 路径。

## 7. Synthetic Evaluation 核验

本轮额外执行：

```text
python -m evaluation.run_full_eval \
  --mode synthetic_smoke \
  --output cache/release_readiness_synthetic_smoke.json
```

评测 provenance：

| 字段 | 实际值 |
|---|---|
| 执行时间 | 2026-08-19T10:16:20.190485+00:00 |
| 审计 Git SHA | `bb4e5d9e176503cc7d19b047583a70c1ac9fdde6` |
| evaluation mode | `synthetic_smoke` |
| mock | 是 |
| 真实模型 | 否 |
| 人工标注 | 否 |
| Generation cases | 20 |
| Retrieval cases | 8 |
| Workflow run count | 0 |

本次生成层的 JSON compliance、risk detection、numeric consistency、groundedness、citation 和 refusal 指标均为 1.0，hallucination rate 为 0.0。这些值来自按 expected label 构造的确定性 mock response，只能证明规则链路没有明显回归，**不得表述为 Qwen 准确率、真实 RAG 效果或生产质量**。

检索层使用内存 SQLite + hashing compatibility retriever，不是 PostgreSQL FTS + pgvector 主实现。Workflow 层的 `data_source=synthetic_static_only` 且 `run_count=0`；非零 tool-call 分数来自版本化静态 JSONL 中的 expected/actual fixture，不是本轮真实工具执行。

此外，生成和检索的版本化 JSONL 在当前 full eval 中只被统计数量，主评测案例仍来自代码内构造数据；这会削弱“版本化黄金集驱动评测”的可信度。

## 8. P0 问题

### P0-1 风险引擎仍会把未知数据变成持价或零收益

证据：

- `analytics/risk_metrics.py:34` 对所有价格缺口无条件 `ffill()`，没有交易所休市、停牌或数据缺失状态输入。
- `analytics/risk_metrics.py:176` 对组合收益无条件 `fillna(0.0)`。
- `analytics/risk_metrics.py:328-332` 将缺失波动、回撤和 Sharpe 转成 0 后继续输出数值风险分。
- 本轮最小探针显示缺失价格产生 `A: 0.0` 日收益；三个时序指标全为 `None` 且无集中度 flag 时，内部风险分仍返回 `2.0`。

影响：跨 A 股/美股日历、停牌或行情缺失会被混为“价格不变”，可能压低组合波动；完全未知的时序风险仍可能以低风险分呈现并进入 LLM Prompt。

发布前要求：风险计算必须消费可用性矩阵；未知时 `risk_score` 应为 `null/unavailable` 或明确拆分为仅集中度分，且增加跨市场缺口回归测试。

### P0-2 代码版本和风险运行 provenance 未闭环

证据：

- `config.py:81` 的 `CODE_VERSION` 默认是 `unknown`。
- `.github/workflows/deploy.yml:47-94` 使用 SHA 标记镜像，但没有把 SHA 注入 migration、Web 或 Worker 的 `CODE_VERSION`。
- `sync_runs`、LLM Trace、Workflow、ingestion 和 backtest 都消费该字段；backtest cache key 也包含它。
- `risk_runs` 表和 Repository 已存在，但静态引用检查未发现风险 API/Service 写入 `RiskRun`；当前 API 只即时返回字典。
- 本次 synthetic report 本身也没有内建 `commit_sha`、`real_model_used` 和 `human_annotation_included` 字段，本审计只能在外层关联 SHA。

影响：生产记录可能长期显示 `unknown`，不同代码版本可能共享错误的 backtest cache key，风险结果无法按 run 重放或审计。

发布前要求：构建时和部署时强制 `CODE_VERSION=$GIT_SHA`，启动时拒绝 production 的 `unknown`；风险计算新增持久化 service，并把 valuation snapshot、价格数据、配置和代码版本纳入 input hash。

### P0-3 本地研究原文缺少完整的提交和镜像隔离

证据：

- 默认本地对象存储为 `data/object_storage/`，本轮目录存在但为空。
- `.gitignore` 没有 `data/object_storage/`。
- `.dockerignore` 没有 `data/object_storage/`、`rag_documents/` 或 `historical_prices*.csv`。
- `Dockerfile` 使用 `COPY . .`，本地构建时这些未跟踪文件可能被打入镜像。

当前检查没有发现该目录中的文件，远端 PR #2 的 Gitleaks 也成功；因此没有证据表明当前提交已包含私有数据。问题在于缺少预防控制。

发布前要求：同时完善 `.gitignore` 和 `.dockerignore`，增加自动测试，确保本地对象存储、上传原文、真实行情、真实持仓和缓存报告不会进入 Git 或镜像。

## 9. P1 问题

1. **关键路径覆盖不足。** Market sync、Tushare、worker、S3 和 portfolio API 覆盖率明显低于账本核心，且 CI 没有按核心包设置覆盖阈值。
2. **生产运行时未在 PR CI 实测。** CI 只 `docker build`；本轮 readiness 使用 hashing。SentenceTransformer 离线加载、生产身份和 S3 配置只有部署后 smoke 才会验证。
3. **评测主线与生产主线不一致。** synthetic retrieval 使用 SQLite/hashing；generation/retrieval JSONL 没有实际驱动对应 full eval；Workflow 没有真实 run。
4. **legacy 双轨仍容易误操作。** `run_job.py` / `Dockerfile.job` 仍描述 Parqet、Telegram、Gemini 和 Berlin 时区；根目录 route/service/state 与 PostgreSQL主线并存，需要明确归档计划。
5. **开发环境不可复现。** 本机默认 Python 3.11、venv Python 3.13，目标是 3.12；同一 SHA 的宿主 Mypy 失败而 venv/CI 通过。
6. **部署入口存在双轨。** GitHub Actions Cloud Run 与 Render Blueprint 并存，尚未声明唯一生产发布平台、回滚责任和环境配置权威来源。
7. **开发 PostgreSQL 暴露面偏大。** Compose 默认把 5432 绑定到所有主机接口并使用公开示例密码；应默认绑定 localhost。
8. **API 错误与资源边界需收紧。** 部分 API 把内部 exception 文本作为 `detail` 返回；RAG `top_k` 缺少统一上限；`GET /api/backtest/report?force=true` 会执行并写缓存。
9. **机构身份仍未完成。** Production read-only public demo 是可接受边界，但 writable 机构部署仍只有 Basic Auth，而非 OIDC/SAML 和职责分离。

## 10. P2 问题

1. Ruff 当前只启用 E9/F63/F7/F82，Mypy 明确排除了多数 legacy、analytics、backtest、evaluation、RAG 和 route 模块；这是渐进基线，不是全仓严格检查。
2. Starlette/httpx 弃用 warning 和 SQLite connection `ResourceWarning` 应清理，避免依赖升级后变成失败。
3. 应给 `run_job.py`、`Dockerfile.job`、SQLite RAG 和旧 refresh route 添加统一 deprecated 标识、所有者和最晚移除条件。
4. 示例研究文档、截图及第三方 Parqet 文档应补充来源/许可证清单；当前只能确认仓库声明其为公开或模拟材料。
5. 233 个 changed files 的 Draft PR 审查面很大，建议按领域提供 reviewer checklist，而不是仅依赖总测试数。

## 11. 部署风险与回滚

### 11.1 当前部署风险

- PR #2 仍是 Draft，且 deploy workflow 只接受 `main`，所以本分支没有真实部署结果。
- GitHub CI 的容器 build 成功，不等于镜像在 Cloud Run 中成功加载语义模型。
- Alembic migration job 在 Web 部署前执行；若新版本应用失败，数据库可能已经升级。
- GCP 与 Render 两套声明可能产生配置漂移。
- `CODE_VERSION=unknown` 会破坏运行 trace 和缓存版本隔离。
- legacy job 若仍存在于云端调度，可能继续触发旧 Parqet/Telegram 路径。

### 11.2 回滚方法

1. 应保留 SHA 镜像 `.../portfolio-pilot:<git-sha>`，应用回滚只切回上一已验证 SHA，不覆盖 tag。
2. migration 前完成数据库备份；仅在已实测 downgrade 且确认数据兼容时执行 Alembic downgrade。
3. 如果 migration 不可逆，采用 forward fix，不能只回滚容器。
4. Worker 与 Web 使用同一兼容 schema 窗口；发布时先暂停写 Worker，再迁移、部署 Web、验证 readiness，最后恢复 Worker。
5. 本阶段没有改变数据库 schema、业务代码、PR 或远程分支；审计文档可通过删除该单文件回滚。

## 12. 数据与隐私风险

本轮确认：

- `.env`、`cache/`、`portfolio.csv`、`rag_documents/` 和 `data/ingestion/` 已被 Git 忽略；
- 本地 secret pattern 检查只命中 `.env.example`/README 的占位值和公开开发数据库示例；
- PR #2 的全历史 Gitleaks check 为 SUCCESS；
- 本轮测试生成的 SQLite、coverage 和 evaluation report 都位于已忽略目录；
- `data/object_storage/` 当前为空，没有发现上传原文。

未关闭风险：

- `data/object_storage/` 没有 Git/Docker ignore 保护；
- Docker build context 仍可能包含本地 `rag_documents/` 和真实历史行情；
- Basic Auth 不满足机构身份治理；
- 本轮没有也不应验证任何真实持仓、内部研报或真实 API Key。

## 13. 评测可信度风险

1. `synthetic_smoke` 使用 mock，不能证明真实 Qwen 或生产 RAG 质量。
2. 生成 mock 直接读取 expected risk tags，因此 1.0 指标是工程夹具行为，不是泛化能力。
3. synthetic retrieval 不走 PostgreSQL/pgvector 主路径。
4. Workflow `run_count=0`，延迟、成本、人工采纳和失败率的 0 不是生产统计。
5. 没有运行 `live_model_eval`，没有真实模型 usage，也没有人工黄金标注。
6. 当前报告缺少内建 commit SHA、真实模型布尔值和人工标注布尔值。
7. 版本化 generation/retrieval JSONL 与实际 full eval case source 尚未统一。

在补齐上述问题前，只能对外表述为：“在 SHA `bb4e5d9` 上，synthetic smoke 工程链路通过”；不能表述为模型准确率、生产召回率或机构业务效果。

## 14. 推荐提交与合并顺序

本阶段建议只提交本审计文档：

```text
docs: add release readiness baseline audit
```

后续建议顺序：

1. 单独修复风险引擎缺失数据语义并补回归测试。
2. 单独修复 `CODE_VERSION`、`RiskRun` 和 evaluation provenance。
3. 单独补齐本地对象存储/研究文档的 Git 与 Docker ignore 保护。
4. 加强核心包覆盖、production-container readiness smoke 和版本化 golden dataset 驱动评测。
5. 在新的候选 SHA 上重跑本文件列出的全部命令，并更新审计附录。
6. 仅将 PR #2 作为候选合并到 `main`；PR #1 已被覆盖，不应先单独合并。

在收到“最终分支收口”前，不执行合并、关闭 PR、删除远程分支或改写历史。

## 15. 当前不应删除的文件或分支

### 分支

- `main`：当前默认基线和回滚参照。
- `agent/institutional-research-platform`：PR #1 审计历史；最终收口前保留。
- `agent/postgres-rag-governance`：唯一候选主线。

### 文件与目录

- 全部 `migrations/versions/*`：schema 历史和 downgrade/upgrade 链路。
- `database.py`、`models.py`、`state.py`：旧 API/Shadow/迁移兼容仍有引用。
- `routes/`、`services/`、`fetchers/`：部分是 compatibility，部分已经承载新主线；不能整目录删除。
- `app/services/legacy_portfolio_adapter.py`：旧前端读取 PostgreSQL 快照的关键桥接层。
- `services/vertex_ai.py`：旧导入兼容 shim。
- `rag/retriever.py`、`rag/repository.py`、`rag/service.py`：离线兼容、迁移对比和现有测试仍使用。
- `scripts/migrate_*`、`scripts/validate_*`、`scripts/compare_*`：SQLite 到 PostgreSQL 的迁移与一致性证据。
- `run_job.py`、`Dockerfile.job`：当前应先标记和核对云端调度引用，确认无基础设施依赖后再删除。
- `evaluation/datasets/*_gold_v1.jsonl`：版本化培训集，下一阶段应接入真实评测 runner，而不是删除。
- 现有 legacy tests：在兼容 API 退役前仍是回归保护。

## 16. 最终回答

- **是否继续以 `agent/postgres-rag-governance` 为唯一候选主线：是。** 它不落后于 main，包含 PR #1，并拥有最新 PostgreSQL/RAG/Workflow 变更和成功 CI。
- **PR #1 是否已被 PR #2 实质覆盖：是。** Git 祖先关系和 left 0 / right 5 已验证。
- **下一阶段最优先的三个问题：**
  1. 修正风险引擎的缺失行情、零收益和未知风险分语义；
  2. 打通 Git SHA、`RiskRun`、LLM/evaluation report 与缓存的完整 provenance；
  3. 保护 `data/object_storage/`、RAG 原文和真实行情不进入 Git 或 Docker 镜像。
