# PortfolioPilot 当前限制

本文记录当前代码边界，避免把研究演示能力描述成生产级金融基础设施。所有分析仅用于研究和软件演示，不构成投资建议。

## 1. PostgreSQL 基础已建立，旧业务仍保留 SQLite

- PostgreSQL 已具备 async engine、Repository、Alembic 和首批核心表，但当前在线持仓构建、评分、知识文档、Prompt、LLM Trace、Workflow 和可选 Shadow 数据仍主要使用 `cache/portfoliopilot.db`。
- SQLite 访问主要是同步 `sqlite3`，部分调用通过线程池避免阻塞事件循环，但不具备 PostgreSQL 的并发、迁移、备份和权限能力。
- 当前组合在线状态仍保存在 `state.portfolio_data`，进程重启后需要从 CSV、缓存或外部 Provider 重建。
- PostgreSQL `transactions` 与 `position_snapshots` 表已经创建，但真实组合尚未切换为 transactions 唯一事实源；该业务迁移属于后续阶段。
- `scripts/migrate_sqlite_to_postgres.py` 只迁移明确支持的旧总览快照和 Shadow 模拟交易，不会把其他 SQLite 表静默映射到不兼容结构。

## 2. 当前还不是生产级严格 walk-forward 回测

当前策略回测已经按再平衡日使用此前窗口估计权重，并输出 OOS 区间和 leakage checks，但仍不能按生产级 point-in-time walk-forward 系统宣传，原因包括：

- 没有交易所交易日历和跨市场统一可交易时点；
- 没有逐时点行情可得性、公司行动、退市和停牌数据库；
- 输入文件和报告尚未进入不可变对象存储；
- 历史 Prompt、模型和 evidence snapshot 需要调用方自行提供，尚未持续采集；
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
| RAG embedding | sentence-transformers 不可用 | 使用确定性 hashing embedding |
| RAG 向量索引 | FAISS 不可用 | 使用 NumPy 相似度检索 |
| RAG evidence | 配置目录无文档时存在仓库示例文档 | 本地演示可读取 `data/research_docs/`；结果仍携带 evidence ID 和来源 |
| RAG evidence | 没有任何可用或有权限文档 | 返回空 citations 和 `evidence_insufficient=true` |
| 风险历史行情 | Provider 失败、覆盖率不足或样本不足 | 指标返回 unavailable/null 及数据质量状态，不生成 mock 风险值 |
| 本地组合 | 无持久化 CSV，且处于 demo 条件 | 可读取仓库示例组合，来源标记为 `sample_csv` |
| 历史图表 | CSV 只有当前快照 | 可生成明确标记为 estimated 的展示序列，不作为真实历史业绩 |

Mock、sample、estimated 和 fallback 结果不得用于收益承诺、模型效果宣传或真实投资决策。

## 5. 可选扩展默认关闭

Polymarket、Telegram、Parqet 和 Shadow Agent 分别由 `ENABLE_POLYMARKET`、`ENABLE_TELEGRAM`、`ENABLE_PARQET`、`ENABLE_SHADOW_AGENT` 控制，默认均为 `false`。这些扩展不是 A 股/美股风险分析主链路的依赖。

## 6. 其他工程限制

- 当前认证仍是可选 Basic Auth，尚未接入 OIDC、RBAC/ABAC 和个人审计身份。
- 原生前端与根目录模块仍依赖部分全局状态，尚未迁移到目标 `app/` 分层结构。
- 当前本地虚拟环境可能不是 Python 3.12；CI 和 Docker 使用 Python 3.12 作为验收环境。
- 示例持仓、价格和研究文档均为公开或模拟内容，不代表真实持仓或投资观点。
