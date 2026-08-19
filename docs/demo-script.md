# PortfolioPilot 面试演示脚本

本文用于一次 5-8 分钟的本地面试演示。演示数据必须来自仓库内的 synthetic/public fixture；不要导入真实持仓、内部研报或个人数据。系统输出仅用于研究与风险提示，不构成投资建议或交易指令。

## 演示前准备

使用 README 的“完整本地开发”路径启动 PostgreSQL、应用 migration 和 FastAPI。执行 bootstrap 后保存终端输出中的 `portfolio_id`，下文记为 `<portfolio_id>`。

```bash
docker compose up -d postgres
alembic upgrade head
python scripts/bootstrap_portfolio.py --base-currency CNY
python main.py
```

另开终端准备 Worker 命令。Dashboard 位于 <http://localhost:8000>，API 操作建议在 <http://localhost:8000/docs> 完成，以便同时展示请求、响应和状态码。

## 1. 导入模拟交易流水

- **页面或 API**：Swagger `POST /api/portfolios/{portfolio_id}/imports/transactions`
- **输入**：`data/portfolios/example_transactions.csv`，其中包含模拟 CNY/USD 入金、AAPL 买入和贵州茅台 A 股买入。
- **预期输出**：返回 `import_batch_id`、`accepted_rows`、`rejected_rows`、`idempotent_replay` 和 `history_completeness`。服务端以文件 SHA256 做幂等判定；重复上传同一文件时 `idempotent_replay=true`，不会重复生成交易。
- **面试讲解要点**：`transactions` 是持仓唯一事实源；旧持仓 CSV 只会转成 `opening_balance`，系统不会把期初快照伪装成完整历史。
- **失败时 fallback**：展示 422 的行级错误和 `/api/import-batches/{batch_id}/errors` 问题报告。不要手改数据库绕过校验。

## 2. 按时间重建持仓

- **页面或 API**：`GET /api/portfolios/{portfolio_id}/positions?as_of=2026-08-19T15:00:00%2B08:00`
- **输入**：上一步的 `<portfolio_id>` 和明确的 `as_of`。
- **预期输出**：证券数量、原币加权平均成本、分币种现金、`last_transaction_at`、warnings 和 `history_completeness`。
- **面试讲解要点**：Position Rebuilder 按 `occurred_at, created_at` 稳定重放交易；买卖方向来自 `transaction_type`，并阻止未显式支持的负持仓。
- **失败时 fallback**：先调用交易列表确认导入是否成功。若只有 opening balance，保留并解释 `opening_balance_only` 警告，不补造历史流水。

## 3. 同步或加载行情快照

- **页面或 API**：Swagger `POST /api/market-data/sync`，随后调用 `POST /api/portfolios/{portfolio_id}/rebuild?as_of=...`。
- **输入**：例如 `{"providers":["yfinance"],"start":"2026-01-01","end":"2026-08-19","portfolio_id":"<portfolio_id>"}`。A 股真实同步需另行配置 Tushare Token。
- **预期输出**：Market sync 返回 `sync_run_id`、状态、`data_as_of` 和写入计数；重建返回 valuation snapshot ID、`input_hash`、价格/FX lineage、基准币种市值和未实现盈亏。
- **面试讲解要点**：原始价格保留原币，FX 独立存储；估值只读取 `as_of` 之前可得且有效的数据，同一输入产生相同哈希与幂等快照。
- **失败时 fallback**：外部 Provider 不可用时，展示失败的 `sync_run` 和数据缺口，不把 mock 当真实行情。风险链路可改用 README 的本地 CSV 回测演示，但要明确它不等于 PostgreSQL 实时估值。

## 4. 生成风险摘要

- **页面或 API**：`GET /api/portfolio/risk-summary?portfolio_id=<portfolio_id>`
- **输入**：已生成 valuation snapshot 的组合。
- **预期输出**：收益、波动、最大回撤、Sharpe、资产权重、行业集中度、市场暴露、`metric_status` 和行情质量信息。
- **面试讲解要点**：风险数字由 pandas/numpy 确定性计算；缺行情返回 `null` 和原因，不用 0 掩盖未知风险。
- **失败时 fallback**：若覆盖率不足，直接展示 `missing_tickers`、`stale_tickers` 或 invalid metric 状态，并切换到本地历史 CSV 回测说明计算口径。

## 5. 检索公开研究资料

- **页面或 API**：先用 `POST /api/knowledge/documents` 上传 `data/research_docs/public_fund_research_faq.md`，运行 ingestion Worker 并发布文档，再调用 `POST /api/rag/retrieve`。
- **输入**：上传时使用唯一 `Idempotency-Key` 和 public 权限 metadata；检索体可用 `{"query":"组合集中度与过期证据如何处理","top_k":5}`。
- **预期输出**：上传返回 ingestion job；发布后检索返回 document/version/chunk ID、quote、融合分数和 `evidence_insufficient`。
- **面试讲解要点**：正文存对象存储，版本、Chunk、embedding 和权限存 PostgreSQL；检索组合 tsvector、pgvector 与 RRF，权限和有效期在排序前过滤。
- **失败时 fallback**：若 embedding Provider 不可用，开发环境只能显式启用 hashing 并标注为非语义质量演示；无合格证据时保留 `evidence_insufficient=true`。

```bash
curl -X POST http://localhost:8000/api/knowledge/documents \
  -H "Idempotency-Key: demo-public-faq-v1" \
  -F 'file=@data/research_docs/public_fund_research_faq.md' \
  -F 'metadata={"title":"PortfolioPilot Fund Research FAQ","source_type":"public_training_fixture","permission_groups":["public"]}'
python -m app.workers.run_knowledge_ingestion --once
```

## 6. 生成带引用的研究草稿

- **页面或 API**：`POST /api/workflows/research-report`，随后运行 `python -m app.workers.run_research_workflow --once`。
- **输入**：`{"idempotency_key":"interview-demo-v1","portfolio_id":"<portfolio_id>","language":"zh","max_steps":20,"node_timeout_seconds":45}`。
- **预期输出**：创建请求返回 202/PENDING；Worker 完成校验、风险、检索、生成和规则节点后，运行进入 `PENDING_REVIEW`。
- **面试讲解要点**：LLM 只解释已计算风险和已检索证据；目标权重归确定性优化器，结构化输出还需通过数字、引用和合规规则。
- **失败时 fallback**：没有 Qwen Key 时允许展示明确标记的 mock/fallback Trace，但必须说明它只验证流程；不要称为真实模型结果。需要真实模型演示时，先配置 Key 并重建新的运行。

## 7. 查看 Prompt Version 与 Trace

- **页面或 API**：`GET /api/prompts`、`GET /api/workflows/{run_id}`、`GET /api/evaluation/traces?limit=20`。
- **输入**：上一步返回的 `run_id`。
- **预期输出**：已发布 Prompt 的 key/version/schema，逐节点状态，以及关联的模型、Prompt、证据 ID、输入哈希、usage、成本来源、fallback 和代码版本。
- **面试讲解要点**：Prompt 不是散落在业务代码中的字符串；每次生成都能追溯到版本与输入，Provider 未返回 usage 时会明确标为 estimated。
- **失败时 fallback**：若 Trace 是 failed，保留错误类型并展示安全模板或失败状态；不要删除失败记录后重跑来美化结果。

## 8. 进入人工审核

- **页面或 API**：从 workflow 响应取 `review_tasks[0].review_id`，调用 `POST /api/reviews/{review_id}/approve`；也可以使用 `/reject` 或 `/request-changes`。
- **输入**：`{"feedback":"演示审核：数字和引用已复核"}`。
- **预期输出**：Review Task 记录审核身份、决策和时间，Workflow 回到 `PENDING` 等待发布节点。
- **面试讲解要点**：`reviewer_id` 来自服务端 Principal，不能由请求 Body 冒充；审批是发布前的强制治理节点。
- **失败时 fallback**：权限不足时展示 403/404，改用本地配置中具有 `research_reviewer` 角色的演示 Principal；不要在 Body 中伪造 reviewer。

## 9. 发布研究报告

- **页面或 API**：审核后再次运行 `python -m app.workers.run_research_workflow --once`，查询 `GET /api/workflows/{run_id}`，再访问 `GET /api/reports/{report_id}`。
- **输入**：审核通过的 workflow run。
- **预期输出**：状态变为 `PUBLISHED`，返回稳定 `report_id`；报告 metadata 包含来源运行、Prompt/Trace 和版本信息。同一运行不会重复发布。
- **面试讲解要点**：发布的是受控研究报告，不是交易指令；数据库保存 metadata 和审计关系，原始对象使用统一对象存储抽象。
- **失败时 fallback**：若规则、引用或权限未通过，保留 `REJECTED`/`FAILED`，回到对应节点说明原因，不手工改状态。

## 10. 查看评测面板

- **页面或 API**：Dashboard 的 `Eval & Trace` 页面，或 `GET /api/evaluation/dashboard`。
- **输入**：先执行 `python -m evaluation.run_full_eval --mode synthetic_smoke --output cache/full_evaluation_report.json`。
- **预期输出**：报告显示 `evaluation_mode`、时间、Git SHA、dataset/version、样本量，以及 mock、真实模型、人工标签和生产数据标记。
- **面试讲解要点**：CI 的 `synthetic_smoke` 只证明工程链路和规则未明显回归；`live_model_eval`、`human_gold_eval`、`production_monitoring` 有不同的前置条件，不能混成一个准确率。
- **失败时 fallback**：报告 provenance 不全时 Dashboard 应拒绝把它展示为有效评测。真实模型或人工数据缺失时停止对应模式，不静默降级。

## 收尾话术

用一句话收束：PortfolioPilot 的重点不是让 LLM 决定交易，而是把账本、行情、确定性风险、公开证据、Prompt、模型调用和人工决策串成可以复核的研究链路。
