# PortfolioPilot 当前限制

本文记录当前代码边界，避免把研究演示能力描述成生产级金融基础设施。所有分析仅用于研究和软件演示，不构成投资建议。

## 1. 核心组合已使用 PostgreSQL，研究治理仍保留 SQLite

- PostgreSQL `transactions` 已是核心组合持仓和现金的唯一事实源；持仓与估值快照由账本、历史行情和 FX 派生。
- 知识文档、Prompt、LLM Trace、Workflow 审批、部分评分历史和可选 Shadow 数据仍主要使用 `cache/portfoliopilot.db`。
- SQLite 访问主要是同步 `sqlite3`，部分调用通过线程池避免阻塞事件循环，但不具备 PostgreSQL 的并发、迁移、备份和权限能力。
- `state.portfolio_data` 仍被部分非核心旧模块引用，但当前组合页、DB API、风险汇总、AI 分析、调仓研究和 Workflow 的组合输入已来自 PostgreSQL valuation snapshot。
- `scripts/migrate_sqlite_to_postgres.py` 只迁移明确支持的旧总览快照和 Shadow 模拟交易，不会把其他 SQLite 表静默映射到不兼容结构。

## 2. 当前还不是生产级严格 walk-forward 回测

当前策略回测已经落实 point-in-time 成交边界：仅使用 `t-1` 前数据估计，在 `t` 收盘成交，并从 `t+1` 开始应用新权重；逐日 NAV、权重漂移、换手率、成本和调仓 lineage 均可审计。但仍不能按生产级完整 walk-forward 系统宣传，原因包括：

- 当前支持显式可用性矩阵，但尚未集成完整交易所交易日历和跨市场统一可交易时点；
- 尚无生产级公司行动、退市和停牌数据库；
- 输入文件和报告尚未进入不可变对象存储；
- LLM 已退出权重计算；历史 Prompt、模型和 evidence snapshot 仍需在解释评测中持续采集；
- 示例 CSV 只适合流程验证，不能代表可交易回测结果。

## 3. yfinance 仅用于研究演示

yfinance 是非授权的公开数据接口适配，不提供生产 SLA。它可能出现延迟、缺失、ticker 映射差异、复权口径变化或限流。当前系统会披露缺失 ticker、stale ticker、覆盖率和 `as_of`；Provider 失败时风险指标应进入 unavailable，而不是自动伪造真实行情。

生产场景需要有授权的数据源、交易日历、公司行动处理、质量校验、数据版本和供应商故障切换。

## 4. Mock 与 fallback 触发条件

系统允许离线演示，但必须保留来源标记：

| 模块 | 触发条件 | 对外标记/行为 |
|---|---|---|
| 结构化 LLM 分析 | 没有匹配 `AI_PROVIDER` 的 API Key | 使用安全模板或 mock provider，`ai_available=false`，`source=mock/fallback` |
| LLM 输出校验 | JSON/Schema 校验重试后仍失败 | 返回安全模板，记录失败 Trace 和 fallback 状态 |
| 回测行情 | 解析后没有可用真实价格 CSV | 生成固定种子 mock 行情，`mock_price_data_used=true`、`data_source=mock_price_data` |
| 完整模型评测 | 显式使用 `--mode live_model` 且缺少 Qwen Key | 直接失败，不回退 mock，也不生成伪 live 报告 |
| RAG embedding | sentence-transformers 不可用 | 使用确定性 hashing embedding |
| RAG 向量索引 | FAISS 不可用 | 使用 NumPy 相似度检索 |
| RAG evidence | 配置目录无文档时存在仓库示例文档 | 本地演示可读取 `data/research_docs/`；结果仍携带 evidence ID 和来源 |
| RAG evidence | 没有任何可用或有权限文档 | 返回空 citations 和 `evidence_insufficient=true` |
| 风险历史行情 | Provider 失败、覆盖率不足或样本不足 | 指标返回 unavailable/null 及数据质量状态，不生成 mock 风险值 |
| 显式 Demo 页面 | 测试或演示代码构造 `is_demo=true` 组合 | 返回标记为 demo 的固定数据，不写入真实账本 |
| 核心组合 | PostgreSQL 没有交易或估值快照 | 返回 empty/error state；不从本地 CSV state 自动伪造真实组合 |
| 行情同步 | Tushare Token、Provider 依赖或网络不可用 | 本次 `sync_run` 标记 failed；不生成 mock `price_bars` |

Mock、sample、estimated 和 fallback 结果不得用于收益承诺、模型效果宣传或真实投资决策。

## 5. 可选扩展默认关闭

Polymarket、Telegram、Parqet 和 Shadow Agent 分别由 `ENABLE_POLYMARKET`、`ENABLE_TELEGRAM`、`ENABLE_PARQET`、`ENABLE_SHADOW_AGENT` 控制，默认均为 `false`。这些扩展不是 A 股/美股风险分析主链路的依赖。

## 6. 其他工程限制

- 当前认证仍是可选 Basic Auth，尚未接入 OIDC、RBAC/ABAC 和个人审计身份。
- 原生前端与部分根目录旧接口仍依赖全局状态；核心组合路径已迁移，但完整模块归档尚未完成。
- Tushare 与 yfinance 当前按 Provider 逐证券串行获取，尚未实现生产级限流、断点续传、交易所级增量游标和授权行情 SLA。
- Corporate action 已有 Provider 查询契约，但尚未独立落库和自动生成账本事件；当前复权主要通过 `adjusted_close` 与 `adjustment_factor` 保存 lineage。
- 当前本地虚拟环境可能不是 Python 3.12；CI 和 Docker 使用 Python 3.12 作为验收环境。
- 示例持仓、价格和研究文档均为公开或模拟内容，不代表真实持仓或投资观点。
