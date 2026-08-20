# 📡 PortfolioPilot API Reference

Alle Endpoints erfordern Basic Auth (`DASHBOARD_USER` / `DASHBOARD_PASSWORD`), sofern nicht anders angegeben.

## Health (`app/api/health.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| GET | `/health` | 旧版兼容健康检查 |
| GET | `/health/live` | 仅检查 FastAPI 进程存活，不查询外部依赖 |
| GET | `/health/ready` | 通过 Repository 执行 PostgreSQL readiness 查询；不可用时返回 503 |

## Portfolio (`routes/portfolio.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| GET | `/` | Dashboard (HTML) |
| GET | `/api/portfolio` | Portfolio-Daten (JSON) |
| GET | `/api/stock/{ticker}` | Einzelaktie Details |
| GET | `/api/stock/{ticker}/history` | Kurs-History einer Einzelaktie |
| GET | `/api/portfolio/history` | Portfolio-Wert-Entwicklung |
| GET | `/api/portfolio/activities` | Kauf-/Verkaufs-Aktivitäten |
| GET | `/api/rebalancing` | Rebalancing-Empfehlungen |
| GET | `/api/tech-picks` | Tech-Aktien Screening (yFinance Screener) |
| GET | `/api/sectors` | Sektor-Allokation |
| GET | `/api/fear-greed` | Fear & Greed Index |
| GET | `/api/status` | System-Status |
| GET | `/api/portfolio/csv-positions` | 读取 active dashboard holdings generation |
| POST | `/api/portfolio/csv-positions` | 新增/替换一项，并原子替换完整 dashboard snapshot |
| PUT | `/api/portfolio/csv-positions/{ticker}` | 编辑一项，并原子替换完整 dashboard snapshot |
| DELETE | `/api/portfolio/csv-positions/{ticker}` | 删除一项，并原子替换完整 dashboard snapshot |
| POST | `/api/portfolio/upload-csv` | Dashboard 旧 CSV 上传兼容入口；原子替换 active generation、重建持仓和估值 |

## PostgreSQL Ledger (`app/api/portfolios.py`)

这些接口以 PostgreSQL transactions 和 valuation snapshots 为数据源。`as_of` 使用带时区 ISO-8601；未带时区时按 UTC 处理。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/portfolios` | 列出 active portfolios |
| GET | `/api/portfolios/{portfolio_id}` | 读取基准币种、成本法、展示时区和 benchmark 配置 |
| GET | `/api/portfolios/{portfolio_id}/transactions?as_of=` | 按稳定顺序读取账本流水 |
| POST | `/api/portfolios/{portfolio_id}/imports/transactions` | 上传标准交易流水或旧持仓 CSV |
| GET | `/api/import-batches/{batch_id}/errors` | 下载行级 CSV 问题报告 |
| GET | `/api/portfolios/{portfolio_id}/positions?as_of=` | 从账本重建证券与分币种现金，不写快照 |
| GET | `/api/portfolios/{portfolio_id}/valuation?as_of=` | 读取不晚于 `as_of` 的最新估值快照 |
| POST | `/api/portfolios/{portfolio_id}/rebuild?as_of=` | 幂等生成组合与 position valuation snapshots |

导入响应包含 `accepted_rows`、`duplicate_rows`、`rejected_rows`、`persisted_rows`、兼容别名 `inserted_rows`、`idempotent_replay` 和 `history_completeness`。`accepted_rows` 与 `persisted_rows` 均只统计当前数据库事务实际提交的新交易；同文件重放和命中 `external_id` 幂等约束的行计入 `duplicate_rows`。没有 `external_id` 时，文件 SHA256 与 CSV 行号共同形成稳定行标识，因此同一文件中的两条相同行会分别保存，而同一文件重放不会重复写入。旧持仓 CSV 返回 `opening_balance_only`，不会被表示成完整交易历史。

Dashboard 兼容入口接受原有 JSON `positions` 数组，并保留 `status=ok|failed`、`positions_imported`、单项编辑响应等旧字段；新增 `import_status`、`portfolio_id`、`import_batch_id`、`snapshot_generation`、上述真实计数、`position_rebuild` 和 `valuation_snapshot` 状态。生产响应不再返回 `csv_path` 或 `write_csv_path`。

`legacy_dashboard_csv` 表示“当前持仓快照”，不是追加交易历史。新文件、POST、PUT 或 DELETE 都会在组合行锁和同一数据库事务内创建完整 generation：旧 generation 保留审计并标为 `superseded`，只有唯一 `active` generation 进入 `PositionRebuilder`。相同 active snapshot 重放返回 `idempotent_replay=true`，不新增交易或 generation。该切换只作用于 `legacy_dashboard_csv`，不会删除、覆盖或 supersede `standard_csv`、`manual` 等正式交易来源。

CSV 中的 `current_price` 会以带 import batch 后缀的 `legacy_csv_user_supplied:<batch_id>` 明确标记；缺失时使用 `buy_price` 的估值回退会标记为 `legacy_csv_cost_basis_fallback:<batch_id>`。估值只允许读取 active generation 对应 batch 的这类用户价格，两者都不是权威行情源。

## Market Data (`app/api/market_data.py`)

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/market-data/sync` | 显式运行 Provider 同步并返回 `sync_run`、计数和 data cutoff |

请求可包含 `providers`、`start`、`end` 和 `portfolio_id`。Tushare 缺 Token 或全部 Provider 失败时返回错误并将 run 标记 failed，不创建 mock 行情；部分失败会保留成功 Provider 的原子结果，并在 `successful_providers`、`failed_providers`、`provider_statuses` 和 `degraded` 中披露。

日任务使用两个可追溯时间：`valuation_as_of` 冻结交易账本和交易日，行情同步成功后再生成唯一 `knowledge_cutoff`，用于过滤 `data_as_of`。因此任务开始后、本次 cutoff 之前写入的行情可以进入估值，晚于 cutoff 的数据仍被排除。失败的同步 run 将 `data_as_of` 保持为 `null`，不会把失败时间伪装成有效数据截止时间。

## Demo Mode (`routes/demo.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| POST | `/api/demo/activate` | Demo-Portfolio laden (12 fiktive Positionen) |
| POST | `/api/demo/deactivate` | Demo deaktivieren, echter Refresh |
| GET | `/api/demo/status` | Demo-Modus aktiv? |

## Refresh (`routes/refresh.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| POST | `/api/refresh` | Kompletter Refresh |
| POST | `/api/refresh/prices` | Nur Kurse updaten |
| POST | `/api/refresh/portfolio` | Nur Portfolio-Positionen updaten |
| POST | `/api/refresh/parqet` | Nur Parqet-Positionen |
| POST | `/api/refresh/scores` | Nur Scores neuberechnen |
| POST | `/api/trigger-report` | AI-Report manuell auslösen |
| POST | `/api/trigger-weekly-digest` | Weekly Digest manuell auslösen |
| GET | `/api/refresh/status` | Refresh-Fortschritt |

## AI Advisor & Analysis (`routes/analysis.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| POST | `/api/analysis/run` | Analyse starten |
| GET | `/api/analysis/latest` | Letzte Analyse abrufen |
| GET | `/api/analysis/history` | Analyse-Historie |
| GET | `/api/analysis/trend/{ticker}` | Score-Trend einer Aktie |
| GET | `/api/backtest` | Score-Backtest |
| GET | `/api/sectors/rotation` | Sektor-Rotation-Analyse |
| POST | `/api/advisor/evaluate` | Trade-Bewertung (Kauf/Verkauf/Aufstocken) |
| POST | `/api/advisor/chat` | Freie Portfolio-Diskussion (Multi-Turn) |

## Analytics (`routes/analytics.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| GET | `/api/market-indices` | S&P 500, Nasdaq, DAX |
| GET | `/api/movers` | Top Gewinner/Verlierer |
| GET | `/api/heatmap` | Portfolio-Treemap |
| GET | `/api/dividends` | Dividenden-Übersicht |
| GET | `/api/benchmark` | Benchmark-Vergleich |
| GET | `/api/correlation` | Korrelationsmatrix |
| GET | `/api/earnings-calendar` | Earnings-Kalender (Portfolio-Positionen) |
| GET | `/api/stock/{ticker}/news` | Aktien-News |
| GET | `/api/risk` | Beta, VaR, Max Drawdown |
| GET | `/api/stock/{ticker}/score-history` | Score-Entwicklung einer Aktie |
| GET | `/api/attribution` | P&L Attribution |
| GET | `/api/portfolio/history-detail` | Detaillierte Portfolio-Historie (Einzelaktien) |
| GET | `/api/performance` | Performance-Kennzahlen |

## Shadow Portfolio Agent (`routes/shadow_portfolio.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| GET | `/api/shadow-portfolio` | Aktueller Shadow-Portfolio-Stand |
| POST | `/api/shadow-portfolio/run` | Agent-Zyklus manuell auslösen (30-90s) |
| GET | `/api/shadow-portfolio/transactions` | Transaktionshistorie (limit: 50) |
| GET | `/api/shadow-portfolio/performance` | Performance-Verlauf (days: 90) |
| GET | `/api/shadow-portfolio/decision-log` | AI-Entscheidungslog |
| POST | `/api/shadow-portfolio/reset` | Portfolio zurücksetzen (Config bleibt) |
| GET | `/api/shadow-portfolio/config` | Agenten-Konfiguration lesen |
| POST | `/api/shadow-portfolio/config` | Agenten-Konfiguration speichern |

## Streaming (`routes/streaming.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| GET | `/api/prices/stream` | SSE-Stream für Echtzeit-Kursänderungen |

## Knowledge Base (`routes/knowledge.py`)

Uploads use multipart form data with an `UploadFile`, JSON-encoded `metadata`,
and a required `Idempotency-Key` header. The API queues an `ingestion_job`; an
independent worker parses, chunks, embeds and persists the document. Identity
and permission groups come only from the server-derived Principal. Client
headers and JSON bodies cannot elevate document or review permissions.

`POST /api/rag/retrieve` returns both `evidence` (compatibility alias) and
`citations`. Each citation contains stable document/chunk identifiers, version,
source metadata, quote, permission level and fused score. `evidence_insufficient`
is true when no candidate reaches `score_threshold`. The query body may contain
`top_k` and an optional `score_threshold`; ticker, fund code, source type and
date constraints are extracted from the query itself. PostgreSQL FTS and
pgvector rankings are fused with RRF after ACL and temporal filtering.

| Methode | Pfad | Beschreibung |
|---|---|---|
| POST | `/api/knowledge/documents` | Dokument ingestieren oder identischen Checksum als Duplikat erkennen |
| GET | `/api/knowledge/documents` | Für die Permission-Gruppen sichtbare Dokumente auflisten |
| GET | `/api/knowledge/documents/{document_id}` | Metadaten und Versionen lesen |
| POST | `/api/knowledge/documents/{document_id}/publish` | Aktuelle Version veröffentlichen |
| POST | `/api/knowledge/documents/{document_id}/deactivate` | Dokument deaktivieren und aus Retrieval entfernen |
| GET | `/api/knowledge/ingestion-jobs/{job_id}` | Ingestion-Status oder Parserfehler lesen |
| GET | `/api/knowledge/ingestion-jobs/{job_id}/source` | 按服务端 Principal 权限下载仍在保留期内的源对象 |

## Prompt Registry (`routes/prompts.py`)

Prompt Registry 的读取、比较和变更均要求服务端 Principal 具有 `knowledge_admin`。

| Methode | Pfad | Beschreibung |
|---|---|---|
| GET | `/api/prompts` | Prompt-Definitionen und aktive Versionen auflisten |
| POST | `/api/prompts` | Prompt mit initialer Draft-Version erstellen |
| POST | `/api/prompts/{prompt_id}/versions` | Neue unveränderliche Version erstellen |
| POST | `/api/prompts/{prompt_id}/versions/{version}/publish` | Version veröffentlichen |
| POST | `/api/prompts/{prompt_id}/rollback` | Auf vorherige oder angegebene Version zurückrollen |
| POST | `/api/prompts/compare` | Zwei Versionen mit demselben Testset vergleichen |

Each provider call is written to `llm_call_traces` with the exact `prompt_id`
and `prompt_version`. Financial-analysis output is validated by one Pydantic
contract before it is returned; unknown portfolio tickers, unreturned citation
IDs, extra JSON fields and financial numbers absent from structured input fail
validation, trigger one retry and then use the safe fallback.

## Controlled Research Workflow (`routes/workflows.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| POST | `/api/workflows/research-report` | Allowlisted research-report workflow starten |
| GET | `/api/workflows/{run_id}` | Run, Steps und Review-Status lesen |
| POST | `/api/reviews/{review_id}/approve` | Bericht nach menschlicher Prüfung freigeben |
| POST | `/api/reviews/{review_id}/reject` | Bericht ablehnen |
| POST | `/api/reviews/{review_id}/request-changes` | Änderungen mit Feedback anfordern |
| GET | `/api/reports/{report_id}` | Ausschließlich freigegebenen Bericht lesen |

## Evaluation & Trace (`routes/evaluation.py`)

评测面板与全局 Trace 要求 `research_reviewer` 或 `knowledge_admin`；不会读取客户端自报身份。
`/api/evaluation/dashboard` 的 `evaluation_report` 只接受带完整 commit SHA、数据集版本和模式披露的报告；缺少这些字段的旧缓存返回 `status=unavailable`，不会显示为 0 分或有效结果。模式值固定为 `synthetic_smoke`、`live_model_eval`、`human_gold_eval`、`production_monitoring`。

| Methode | Pfad | Beschreibung |
|---|---|---|
| GET | `/api/evaluation/dashboard` | Trends, Prompt-Vergleiche, Badcases, Kosten und Adoption |
| GET | `/api/evaluation/traces` | Vollständige LLM Call Traces lesen |

## Telegram Webhook (`routes/telegram.py`)

| Methode | Pfad | Beschreibung |
|---|---|---|
| POST | `/api/telegram/webhook/{secret}` | Telegram Bot Webhook (Secret-Token im Pfad) |
