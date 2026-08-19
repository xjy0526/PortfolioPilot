# 核心路径覆盖率矩阵（2026）

## 1. 口径与可复核性

本阶段把以下生产路径定义为 core packages：

- `app/db`
- `app/services`
- `app/providers`
- `rag`
- `prompts`
- `workflows`
- `analytics/risk_metrics.py`
- `backtest/strategy_backtester.py`
- `evaluation`

选择依据是它们直接承载 PostgreSQL 事实源、组合重建与估值、行情 Provider、
RAG/Prompt/Workflow 治理、风险与回测以及评测可信度。`app/storage`、`app/workers`
仍是重要支撑路径，但本轮不把大量 CLI 壳层并入 core 分母；存储 preflight、每日任务幂等
和 restart persistence 继续由独立 P0 测试及 CI job 保护。

总覆盖率沿用 `pytest --cov=.`，包含 legacy、测试文件和迁移等现有统计对象；没有通过
`omit`、删除测试或排除低覆盖 legacy 制造更高数字。Core coverage 只是额外聚合视图，
不能替代 total coverage。

## 2. 基线与本轮快照

| 字段 | 阶段开始基线 | 本轮本地实测 |
|---|---|---|
| 基础 Git commit SHA | `6415132031b143d3b3999bedbc63da757037c4ce` | `6415132031b143d3b3999bedbc63da757037c4ce` + 未提交工作树 |
| 日期 | 2026-08-19（CI） | 2026-08-20（Asia/Shanghai） |
| Python | GitHub Actions Python 3.12 | Python 3.13.9 |
| PostgreSQL | pgvector PostgreSQL 16 service | `pgvector/pgvector:0.8.2-pg16` 独立容器 |
| Total coverage | 63.04% | 65.05% |
| Core coverage | 81.68% | 85.03% |
| Mock/外部网络 | deterministic hashing；不调用真实模型 | deterministic hashing；不调用真实模型或行情 API |
| 人工标注 | 否 | 否 |

本轮数字是 SHA `6415132...` 之上的未提交工作树快照，不能表述为该 SHA 的 CI 结果。
提交后由 `.github/workflows/ci.yml` 重新生成 commit 级 coverage artifact 和 changed-lines
结果；在对应 CI 完成前，不应把本地数字写成远程验证结果。

## 3. Core 矩阵

| 模块 | Statements | Missed | Coverage | 主要业务风险 | 优先级 |
|---|---:|---:|---:|---|---|
| `app/db` | 1,032 | 86 | 91.67% | 账本、lineage、幂等约束或事务错误会破坏事实源 | P0 |
| `app/services` | 1,111 | 215 | 80.65% | 导入、重建、估值和快照错误会直接产生错误持仓 | P0 |
| `app/providers` | 346 | 23 | 93.35% | Provider 字段、复权、币种和失败降级错误会污染行情 | P1 |
| `rag` | 843 | 121 | 85.65% | ACL、过期过滤或引用映射错误会泄露或误用证据 | P0 |
| `prompts` | 412 | 66 | 83.98% | Schema、发布与回滚错误会破坏结构化输出可追溯性 | P0 |
| `workflows` | 390 | 77 | 80.26% | 崩溃恢复、人工审核或发布幂等错误会绕过治理 | P0 |
| `analytics/risk_metrics.py` | 266 | 40 | 84.96% | 缺失行情和数值异常会扭曲风险指标 | P0 |
| `backtest/strategy_backtester.py` | 550 | 61 | 88.91% | 时点、权重漂移或成本语义错误会造成前视偏差 | P0 |
| `evaluation` | 1,241 | 238 | 80.82% | 模式混用或 provenance 缺失会产生失真指标 | P0 |
| **Core aggregate** | **6,191** | **927** | **85.03%** | 核心金融数据与 AI 治理链路 | **P0** |

## 4. 门槛

`quality/coverage_policy.json` 固定以下门槛：

- total coverage：63.04%，等于阶段开始时 commit `6415132...` 的真实 CI 基线；
- core coverage：81.68%，等于同一基线的 core 实测值；
- 每个高风险 core group：80%；
- 自基线 SHA 起新增或修改的 production Python 可执行行：85%。

Changed-lines 会统计 coverage XML 中的 production Python，排除 `tests/` 与
`migrations/`；它不会用来替代 total/core 指标。本轮新增两个质量脚本的定向覆盖率为
98%（209 statements，5 missed），但正式 changed-lines 百分比必须以提交后的 CI artifact
为准。

## 5. P0 测试映射

| 场景 | 保护性测试 |
|---|---|
| transaction import 文件与行级幂等 | `tests/integration/test_ledger_market_data.py` |
| position rebuild、买卖、拆股、多币种 | `tests/unit/test_position_rebuilder.py`、ledger integration |
| stale/missing market data | `tests/test_market_data.py`、ledger integration |
| valuation snapshot 与历史 FX | `tests/integration/test_ledger_market_data.py` |
| permission filtering before ranking | `tests/integration/test_rag_governance.py` |
| expired document filtering | `tests/integration/test_rag_governance.py` |
| citation validation | `tests/unit/test_research_workflow_controls.py` |
| prompt publish/rollback | `tests/test_prompt_registry.py` |
| structured output validation | `tests/test_financial_analysis.py`、Prompt tests |
| workflow crash recovery | `tests/integration/test_rag_governance.py` |
| review approve/reject/request changes | `tests/unit/test_research_workflow_controls.py` |
| report publication idempotency | Workflow unit/integration tests |
| production storage preflight | `tests/unit/test_preflight.py`、`test_production_hardening.py` |
| Cron duplicate execution | `tests/integration/test_daily_pipeline.py` |
| PostgreSQL restart persistence | 独立 `postgres-restart-e2e` job |

## 6. 未关闭风险

- `app/services` 聚合已达 80%，但 `market_data_sync.py` 等单文件仍低于聚合值；应按故障
  注入继续补齐 Provider 写入失败、部分同步和 retry 测试。
- `app/storage/s3.py` 的异常重试和 multipart 边界仍是 P1；当前已有 mock S3
  上传、读取、删除和权限测试，但未覆盖所有 SDK 异常。
- Worker 入口脚本主要由 CLI contract 与 integration job 间接覆盖，不能把 core 85.03%
  解读为全部部署代码达到 85%。
- 本地验证使用 Python 3.13.9；目标 Python 3.12 的权威结果仍以 GitHub Actions 为准。
- `synthetic_smoke` 只证明 deterministic 工程链路，不代表真实 Qwen、真实检索效果或
  production monitoring 指标。
