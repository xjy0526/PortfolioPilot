# PortfolioPilot 测试分层

本文说明普通测试、PostgreSQL integration 和 PostgreSQL restart persistence E2E 的边界。测试通过只代表对应 commit 和执行环境中的工程验证，不代表真实模型效果、生产可用性或投资表现。

## CI 分层

`.github/workflows/ci.yml` 包含以下独立检查：

| Job | 验证范围 |
|---|---|
| `lint-type-unit` | Ruff、Mypy、synthetic evaluation integrity、非 PostgreSQL 测试 |
| `postgres-integration` | Alembic upgrade、downgrade/upgrade 往返、PostgreSQL/pgvector integration |
| `postgres-restart-e2e` | Compose 应用与 PostgreSQL 的真实 restart persistence 场景 |
| `coverage-report` | 合并普通测试和 PostgreSQL integration 的 coverage data |
| `security-audit` | `pip-audit` 与 Gitleaks |
| `docker-build` | 在所有前置 job 成功后构建默认 production image |

`docker-build` 显式依赖 `postgres-restart-e2e`。因此整体 CI 不能通过跳过 restart 测试获得绿灯。README 的 CI badge 和 GitHub PR checks 页面展示实时 workflow 状态，不在文档中长期硬编码测试数量或覆盖率。

每个 pytest job 生成 JUnit XML，并在 GitHub Step Summary 中明确输出 passed、failed、
errors、skipped 和最慢测试。`coverage-report` 同时输出：

- 保留原始 `--cov=.` 口径的 total coverage；
- 九个正式 core package 的 core coverage 与模块矩阵；
- 相对版本化基线的 changed production lines coverage；
- 质量门槛的 PASS/FAIL 与具体失败原因。

门槛配置位于 `quality/coverage_policy.json`，报告工具为
`scripts/coverage_quality.py`。基线值只能由可追溯 CI artifact 更新，不能为了让分支变绿
而降低。详细矩阵见 [核心路径覆盖率矩阵](audits/core_coverage_matrix_2026.md)。

## 普通测试

```bash
python -m pytest -q
```

`pyproject.toml` 默认使用 `-m 'not postgres_restart'`。这会把需要控制 Docker daemon 的 restart E2E 标记为 deselected，而不是把它报告为 skipped 或已验证。普通测试可以连接开发 PostgreSQL 执行 integration，但不会擅自重启数据库。

只运行 PostgreSQL integration：

```bash
docker compose up -d postgres
alembic upgrade head
TEST_DATABASE_URL=postgresql+asyncpg://portfoliopilot:portfoliopilot@localhost:5432/portfoliopilot \
  python -m pytest -o addopts="" -m "postgres and not postgres_restart" -q
```

生成与 CI 同口径的两层 coverage：

```bash
COVERAGE_FILE=.coverage.unit python -m pytest -o addopts="" \
  -m "not postgres" --cov=. --cov-report=term \
  --durations=20 --junitxml=non-postgres.xml

COVERAGE_FILE=.coverage.postgres \
TEST_DATABASE_URL=postgresql+asyncpg://portfoliopilot:portfoliopilot@localhost:5432/portfoliopilot \
  python -m pytest -o addopts="" -m "postgres and not postgres_restart" \
  --cov=. --cov-report=term --durations=20 --junitxml=postgres.xml

COVERAGE_FILE=.coverage.combined python -m coverage combine \
  .coverage.unit .coverage.postgres
COVERAGE_FILE=.coverage.combined python -m coverage xml -o coverage.xml
python scripts/coverage_quality.py --coverage-xml coverage.xml --enforce
```

上述命令使用 deterministic hashing embedding；测试不得访问 Qwen、Tushare、
yfinance 或其他真实外部 API。Restart E2E 独立运行，不并入普通 coverage 分母。

## Restart Persistence E2E

本地前置条件：

- Docker Engine 或 Docker Desktop 正在运行；
- `docker compose version` 可执行；
- 当前用户可以访问宿主 Docker socket；
- 至少有足够空间构建 Python 3.12 E2E image。

执行命令：

```bash
python -m pytest --confcutdir=tests/e2e -o addopts="" \
  -m postgres_restart -q -s tests/e2e/test_postgres_restart_persistence.py
```

测试使用 `docker-compose.e2e.yml`，不会控制开发环境 `docker-compose.yml` 的 PostgreSQL，也不会删除开发数据。每次运行生成唯一 Compose project 和业务 namespace，并执行：

1. 构建 Dockerfile 的 `e2e` target；该 target 使用 deterministic hashing embedding，不下载或调用外网模型；
2. 创建独立 PostgreSQL/pgvector volume 和测试对象存储 volume；
3. 执行 `alembic upgrade head`，启动 FastAPI app，并等待 `/health/ready`；
4. 写入业务夹具并输出关键 UUID、文档 checksum、embedding checksum、估值 input hash；
5. 执行 `docker compose restart postgres`，比较 restart 前后的 `pg_postmaster_start_time()`；
6. 使用 Compose health/readiness 条件等待 PostgreSQL 和应用恢复，不依赖固定时长 sleep；
7. 先只读验证持久化数据，再用同 namespace 重放 seed，验证 ID、checksum、input hash 和行数不变；
8. 成功或失败后执行 `docker compose down --volumes --remove-orphans`；失败时先输出 app/PostgreSQL 容器日志。

应用容器不挂载 Docker socket。只有宿主 pytest 编排器调用 Docker CLI，因此该 job 必须运行在具有 Docker daemon 控制权限的独立 GitHub-hosted runner 上。GitHub Actions 的普通 `services.postgres` 容器不属于 Compose project，不能被 `docker compose restart postgres` 稳定控制，这也是旧测试不能在原 quality job 中真实执行的原因。

## 业务验证范围

restart 前后必须存在且 lineage 保持一致：

1. portfolio transactions，包括 deposit 和 buy；
2. 由账本重新计算的证券持仓；
3. portfolio valuation snapshot 与 position snapshot；
4. research document、version 和 chunk；
5. 384 维 pgvector chunk embedding；
6. published prompt version；
7. 与 Prompt/Workflow 关联的 LLM call trace；
8. workflow run；
9. review task 和 reviewer decision；
10. published report metadata。

测试不调用 Qwen、Tushare、yfinance 或外部对象存储。Embedding 明确标记为 deterministic hashing 测试实现，不应解读为语义检索质量指标。

## 常见失败

- `docker: command not found`：安装并启动 Docker Desktop；
- Compose health timeout：查看测试自动输出的 app/PostgreSQL 日志；
- image build 失败：检查 Docker registry 和 Python package 下载网络，不要改成 mock 数据库；
- migration 失败：先修复 Alembic 往返，不要绕过 schema 检查；
- cleanup 中断：使用测试输出的 project name 执行 `docker compose -f docker-compose.e2e.yml -p <project> down -v --remove-orphans`。
