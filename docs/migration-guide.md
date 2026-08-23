# PortfolioPilot 数据迁移指南

本文记录从旧 SQLite/本地兼容数据迁移到 PostgreSQL 主线的显式步骤。全新安装不需要执行这些脚本；迁移前应备份原数据库，并在隔离环境演练。

## 1. 前置条件

1. 配置目标 `DATABASE_URL`，确认使用 `postgresql+asyncpg://`。
2. 启动 PostgreSQL/pgvector。
3. 应用当前 Alembic schema：

```bash
docker compose up -d postgres
alembic upgrade head
```

应用 import 或 startup 不会调用 `Base.metadata.create_all()`；表结构只通过 Alembic 管理。

## 2. 旧组合与 Shadow 兼容数据

仅当本地确实存在旧库 `cache/portfoliopilot.db` 时运行：

```bash
python scripts/migrate_sqlite_to_postgres.py \
  --sqlite-path cache/portfoliopilot.db \
  --base-currency CNY
```

该脚本创建明确标记的 legacy 用户/组合，并迁移脚本支持的旧组合总览或 Shadow 模拟交易。它不会把旧持仓快照描述成完整交易历史。

## 3. 研究治理数据

文档、Prompt、Trace、Workflow、审核和发布记录使用独立迁移器：

```bash
python scripts/migrate_governance_sqlite_to_postgres.py \
  --sqlite cache/portfoliopilot.db \
  --report cache/governance_migration_report.json
```

迁移后执行一致性校验：

```bash
python scripts/validate_governance_migration.py \
  --sqlite cache/portfoliopilot.db \
  --report cache/governance_validation_report.json
```

公共文档召回对比：

```bash
python scripts/compare_sqlite_postgres_retrieval.py \
  --sqlite cache/portfoliopilot.db \
  --query "ticker:NVDA concentration risk" \
  --query "行业政策 风险"
```

召回对比只用于发现意外回归，不证明生产检索质量。迁移会按当前规则重新切片，因此不要求新旧 Chunk 数完全相同；校验关注文档 key、版本、source checksum、实际 Chunk/Embedding 数、Prompt 版本和稳定映射 ID。

## 4. 原始文件边界

- 旧 SQLite 若只保存解析后的正文，迁移只能重建 Chunk 和 Embedding，不能恢复缺失的原始二进制文件。
- `legacy-file://` 不是有效对象存储地址。缺失原文的 job 必须重新上传或明确归档。
- production 可写模式的原始文件应进入 S3-compatible storage；数据库只保存 object key、checksum、content type、owner 和权限元数据。

## 5. 验证清单

- `alembic current` 与唯一 head 一致；
- 目标表记录数与迁移报告一致；
- accepted/duplicate/rejected/persisted 计数与事务提交结果一致；
- 文档、Chunk 和 Embedding checksum 可复核；
- Prompt deployment、Trace、Workflow、Review 和 PublishedReport 关联完整；
- 核心服务不再依赖 `database._get_conn`；
- PostgreSQL restart persistence E2E 通过；
- legacy 数据保留明确 source，不覆盖 standard CSV 或 manual transaction。

## 6. 回滚边界

迁移脚本不删除 SQLite 源库。失败时应停止写流量、保留迁移报告和日志，并恢复目标数据库备份或删除本次隔离 namespace 后重新执行。不要通过回写 SQLite 建立第二个事实源。

Alembic schema 回滚与生产备份恢复要求见 [部署说明](deployment.md)；当前 legacy 范围和最早删除版本见 [迁移地图](legacy-migration-map.md)。
