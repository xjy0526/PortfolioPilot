# PortfolioPilot 部署说明

本文描述当前仓库实际支持的生产拓扑、对象存储边界和上线检查。系统只用于投研与软件演示，不连接券商、不执行真实交易。

## 1. 运行拓扑

同一个 Docker image 支持不同进程入口：

| 进程 | 服务类型 | 入口 | 生命周期 |
|---|---|---|---|
| FastAPI | Web Service | `uvicorn main:app ...` | 持续运行 |
| 日流水线 | Cron Job | `python -m app.workers.run_daily_pipeline` | 完成即退出 |
| 知识入库 | Worker/受控任务 | `python -m app.workers.run_knowledge_ingestion` | 按显式部署策略运行 |
| Migration | Pre-deploy/Job | `alembic upgrade head` | 完成即退出 |

`run_daily_pipeline` 不是队列消费者。它顺序执行行情同步和组合估值，成功返回退出码 0；未处理异常向 CLI 传播并返回非零退出码。PostgreSQL session advisory lock 防止跨实例并发，日期作用域 `sync_runs.run_key` 防止同一日重复写入。

## 2. Render Blueprint

`render.yaml` 当前定义：

- `portfolio-pilot`：Docker Web Service，健康检查为 `/health/ready`；
- `portfolio-pilot-daily-pipeline`：Docker Cron Job，schedule 为 `0 22 * * 1-5`。

Render cron expression 使用 UTC。`22:00 UTC` 运行时 A 股与美股当日常规交易通常都已收盘；具体数据是否 final 仍由 Provider 返回值和 `price_bars.is_final` 决定，调度时间本身不构成数据完整性保证。

Render Cron 没有 Persistent Disk。Web、Cron 和 Background Worker 也是独立实例，即使路径字符串相同，本地目录也不是共享介质。不要让 Cron/Worker 从 Web 的 `/app/cache` 或 `data/object_storage` 读取生产对象。

## 3. 环境模式

| 环境与模式 | Local storage | S3 storage | 启动行为 |
|---|:---:|:---:|---|
| development/test | 支持 | 支持 | 按配置检查 backend |
| production + `READ_ONLY_DEMO=true` | 允许但不作为共享写存储 | 可选 | 写 API 被拒绝，storage readiness 为 `not_required` |
| production + `READ_ONLY_DEMO=false` | 禁止 | 必需 | local 或配置不完整时启动失败 |

系统不会在 S3 失败时静默退回 local。

## 4. 必填环境变量

基础依赖：

```env
ENVIRONMENT=production
DATABASE_URL=postgresql+asyncpg://...
READ_ONLY_DEMO=true
EMBEDDING_PROVIDER=sentence_transformers
RAG_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
RAG_ALLOW_HASHING_FALLBACK=false
ENABLE_LEGACY_SQLITE_COMPAT=false
ENABLE_TELEGRAM=false
ENABLE_PARQET=false
ENABLE_SHADOW_AGENT=false
ENABLE_TECH_RADAR=false
ENABLE_TRADE_ADVISOR=false
```

生产可写模式额外要求：

```env
READ_ONLY_DEMO=false
DASHBOARD_USER=from-secret-manager
DASHBOARD_PASSWORD=from-secret-manager
OBJECT_STORAGE_BACKEND=s3
S3_BUCKET=portfolio-pilot
S3_REGION=ap-southeast-1
S3_ACCESS_KEY_ID=from-secret-manager
S3_SECRET_ACCESS_KEY=from-secret-manager
S3_PREFIX=research-ingestion
S3_FORCE_PATH_STYLE=false
```

AWS S3 可不设置 `S3_ENDPOINT_URL`。MinIO、Cloudflare R2 等兼容实现应设置其 HTTPS endpoint；需要 path-style addressing 时设置 `S3_FORCE_PATH_STYLE=true`。旧 `OBJECT_STORAGE_BUCKET/PREFIX/ENDPOINT_URL/REGION/ACCESS_KEY_ID/SECRET_ACCESS_KEY` 暂时兼容，但不应继续用于新部署。

行情与模型按需配置：

```env
TUSHARE_TOKEN=from-secret-manager
MARKET_DATA_PROVIDERS=tushare,yfinance
QWEN_API_KEY=from-secret-manager
```

Secret 不得写入 `render.yaml`、Dockerfile、GitHub Actions 日志或仓库文件。Render Blueprint 使用 `sync: false`，并且 Web 与 Cron 的 Secret 需要分别配置。

## 5. 上线顺序与 Preflight

推荐顺序：

```bash
alembic upgrade head
python -m app.core.preflight
```

Preflight 使用与 `/health/ready` 相同的规则：

1. PostgreSQL 可连接；
2. Alembic 当前 revision 等于唯一 head；
3. `vector` extension 可用且核心表齐全；
4. Embedding provider 可加载；
5. 模式要求的 object storage bucket 可访问；
6. production auth 与 read-only/write-mode 配置一致。
7. legacy SQLite compatibility 未启用，实验 Router 只按已审批的 feature flag 注册。

任一必要检查失败时 CLI 返回非零，`/health/ready` 返回 HTTP 503。`/health/live` 始终只检查进程，不代表数据库、模型或 bucket 可用。

production 不允许 `ENABLE_LEGACY_SQLITE_COMPAT=true`。该开关只用于 development/test 的旧库
迁移验证；它会执行旧 SQLite 初始化和 JSON-to-SQLite migration，任何失败都会阻止启动。Core
模式的 liveness/readiness 不依赖 SQLite。模块与 Router 分类见
[module-boundaries.md](module-boundaries.md)。

## 6. 对象权限与数据记录

上传内容通过统一 `ObjectStorage` 写入，数据库仅保存：

- object key 与 provider version；
- SHA256 checksum、content type 和长度；
- owner、permission groups 与 retention 时间；
- ingestion 状态、代码版本和错误摘要。

数据库不保存容器本地绝对路径。上传权限和 metadata ACL 来自服务端 Principal；源文件下载再次检查 owner、`knowledge_admin/platform_admin` 或 permission group。默认成功保留期为 0 天时，Embedding 完成后源对象会被删除，后续下载会返回 not found；需要源文件审计时应提高保留期并配置 bucket 生命周期。

## 7. 备份与恢复边界

生产恢复必须把 PostgreSQL 和 S3 bucket 视为同一数据集：

1. 对 PostgreSQL 启用供应商备份或 PITR；
2. 对 bucket 启用 versioning、生命周期和必要的跨区域复制；
3. 记录数据库恢复点与 bucket version 时间；
4. 恢复后运行 Alembic、preflight、对象 checksum 抽检和知识检索 smoke test；
5. 最后再启用 Cron 和写 API。

仓库目前不提供自动备份、跨区域复制或一键灾难恢复。Local backend 的文件不属于生产备份承诺。若回滚 migration `20260819_0006`，旧 `storage_path` 只保存原 object key，`storage_uri` 保持为空；系统不会从 object key 伪造 provider URI，旧版本入库 Worker 需要重新配置或回迁。

## 8. Cloud Run 边界

`.github/workflows/deploy.yml` 当前在 `main` CI 成功后构建 Git SHA image，执行 migration job，部署只读 Cloud Run Web，并调用 readiness 与只读 smoke test。它尚未创建 Cloud Scheduler 或日流水线 Cloud Run Job；不要把 Render Cron 配置误认为 Cloud Run 已具备同等调度。
