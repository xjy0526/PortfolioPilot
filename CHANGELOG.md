# Changelog

本项目采用语义化版本。v2.0.0 Tag 与 GitHub Release 尚未创建；本条目在 Release PR 合并前保持
`Unreleased`。

## [2.0.0] - Unreleased

### Added

- PostgreSQL/pgvector 持久化主线、SQLAlchemy AsyncSession 和 Alembic migrations。
- 交易账本、CSV 幂等导入、任意 `as_of` 持仓重建及多币种估值快照。
- PostgreSQL FTS + pgvector + RRF 的受治理 Hybrid RAG。
- Prompt Registry、LLM Trace、规则校验、人工审核和受控报告发布。
- 独立 Market/Position/Daily/Knowledge/Workflow Worker 与真实 PostgreSQL restart E2E。
- V2 synthetic/public 评测 fixture、模式隔离和 provenance 字段。

### Changed

- `transactions` 成为持仓事实源，position/valuation snapshot 成为可复现派生结果。
- 原始行情保留原币，FX 独立存储并在估值时转换到组合基准币种。
- 风险和目标权重由确定性引擎负责，LLM 只解释结果与证据。
- Production write mode 使用 S3-compatible object storage，readiness 反映真实依赖。
- 旧 Dashboard URL 通过兼容层逐步读取 PostgreSQL 主线。

### Fixed

- 修复回测成交前收益、权重漂移、缺失行情和 Benchmark 对齐语义。
- 修复无 `external_id` 相同行导入、文件重放和真实持久化计数的一致性。
- 修复 legacy dashboard snapshot 的追加语义，引入 active/superseded generation。
- 修复 daily pipeline 行情同步与 valuation cutoff 的时序和 lineage。
- 修复 squash merge 后 changed-lines coverage base 解析。

### Security

- 服务端 Principal、portfolio/tenant 权限和人工审核身份边界。
- Production local-storage fail-closed、S3 配置校验和 read-only mutation 403。
- 上传大小、类型、页数、Chunk 数与解析超时限制。
- CI 执行 pip-audit、Gitleaks、PostgreSQL integration、restart E2E 和 production Docker build。

### Known limitations

- 不执行真实交易，不构成投资建议或收益承诺。
- yfinance 仅用于研究演示，没有生产 SLA。
- 回测尚不是完整生产级 walk-forward 平台。
- approved human-gold labels 当前为 0，synthetic/mock 指标不代表真实模型效果。
- 当前 release tree 不含一键 governed demo、showcase UI、V3 双人标注或 stale valuation 自动 repair。
- SQLite 和部分旧 Dashboard/实验模块仍保留兼容路径。
