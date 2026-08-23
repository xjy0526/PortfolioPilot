# 确定性一键 Demo

PortfolioPilot 的确定性 Demo 用于本地展示完整治理链路。它只使用仓库内自行构造的
synthetic fixture，不需要 API Key，不访问行情或模型 Provider，也不代表真实用户、真实行情、
真实模型效果或人工评审结果。

## 前置条件

- Docker Desktop 已启动；
- `docker compose version` 可正常执行；
- 本机可使用 `make`；
- 默认端口 `8000` 未被占用。

首次构建仍需从容器镜像仓库和 Python 包索引获取基础镜像与依赖。镜像构建完成后，数据库、
migration、seed 和 smoke 只使用 Docker `internal` network；Web 额外挂载一个仅绑定宿主机
`127.0.0.1` 的 ingress bridge，以便浏览器访问。所有外部 Provider 均被禁用且没有凭据，seed、
检索、mock 研究流程和 Web smoke 不会调用 yfinance、Tushare、Qwen、OpenAI、FMP 或 Parqet。

## 一条命令启动

```bash
make demo
```

该命令按顺序执行：

1. 复用仓库 `Dockerfile` 的 `e2e` target 构建应用镜像；
2. 启动 PostgreSQL 16 与 pgvector；
3. 等待数据库健康检查通过；
4. 执行 `alembic upgrade head`；
5. 幂等写入固定交易、行情、FX、估值、文档、Prompt、Trace、Review 和 Report；
6. 以 `READ_ONLY_DEMO=true` 启动 FastAPI；
7. 等待 `/health/ready`；
8. 重放 seed 并执行 HTTP、数据库和 lineage smoke test。

成功后访问：

- Dashboard：<http://localhost:8000>
- Swagger：<http://localhost:8000/docs>
- Liveness：<http://localhost:8000/health/live>
- Readiness：<http://localhost:8000/health/ready>

端口冲突时可改用：

```bash
make demo DEMO_PORT=8010
```

## 数据与模型披露

所有 Demo 记录都包含以下 provenance：

```json
{
  "is_demo": true,
  "synthetic_data_used": true,
  "mock_response_used": true,
  "real_model_used": false,
  "production_data_used": false
}
```

专用来源如下：

| 数据 | source/provider |
|---|---|
| 交易 | `demo_fixture_transactions` |
| 价格 | `demo_fixture_prices` |
| 汇率 | `demo_fixture_fx` |
| 文档 | `demo_fixture_documents` |
| 模型 Trace | `demo_fixture_model` |

文档内容、价格路径和组合均为自行构造的 synthetic fixture。Review Decision 只是用于展示表结构和
流程状态的固定记录，明确保存 `human_label_used=false`，不能解释为真实人工审核。

## 常用命令

```bash
make demo-up       # build、migration、seed 并启动 Web
make demo-smoke    # 重放 seed，检查 API、RAG lineage 与只读边界
make demo-logs     # 查看 Web 和 PostgreSQL 日志
make demo-reset    # 只删除 demo namespace，不删除其他来源的数据
make demo-down     # 停止容器，保留 Demo volumes
```

`make demo-reset` 只匹配固定 UUID、业务场景、文档 key 和专用 source；它不会删除
`standard_csv`、`manual`、真实 Provider 或其他租户的数据。

## Smoke 验证范围

`scripts/demo_smoke.py` 会检查：

- `/health/live` 与 `/health/ready` 返回 `200`；
- Demo portfolio、重建持仓、完整 valuation 和 risk summary 可读取；
- 已发布文档、chunk、pgvector embedding 和 RAG citation 存在；
- Prompt Version、Workflow、mock Trace、Review 和 Published Report 相互关联；
- Trace 和 Report 保留 synthetic/mock 披露；
- 任意 HTTP mutation 在只读模式返回 `403`；
- 重复 seed 后各表记录数和稳定身份不变。

任何断言失败时脚本返回非零退出码，`make demo` 会随即失败，不会输出“Demo ready”。

## 安全边界

- `DEMO_FIXTURE_MODE` 默认值为 `false`；
- 只有 development/test 演示环境可以显式开启；
- production 检测到 `DEMO_FIXTURE_MODE=true` 时，启动校验和 readiness 会失败；
- Demo Web 在 seed 后以只读模式运行；
- 该流程不包含自动交易、券商连接或投资建议。
