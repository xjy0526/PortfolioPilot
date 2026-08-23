# PortfolioPilot Provider 说明

本文集中说明市场数据、LLM 和 Embedding Provider 的职责、优先级与研究边界。任何凭据都必须通过本地环境或 Secret Manager 注入，不能写入仓库。

## 1. 市场数据统一接口

核心市场数据 Provider 实现统一合同：

```text
fetch_security_master()
fetch_price_bars()
fetch_fx_rates()
fetch_corporate_actions()
```

证券的币种、交易所、市场和国家来自 PostgreSQL security master，不通过 ticker 后缀临时猜测。`provider_symbols` 保存 canonical symbol 与外部代码的显式映射：

| Canonical | Tushare | yfinance | FMP |
|---|---|---|---|
| `600519.SH` | `600519.SH` | `600519.SS` | - |
| `AAPL` | - | `AAPL` | `AAPL` |

每条 PriceBar 保存 native currency、raw/adjusted close、adjustment factor、source、`data_as_of`、`is_final`、sync run 和 quality status。FX 独立保存，不覆盖原币价格。

## 2. Tushare Provider

Tushare 用于 A 股证券主数据、日频 OHLCV、复权因子和交易日历。启用前配置：

```env
TUSHARE_TOKEN=from-secret-manager
MARKET_DATA_PROVIDERS=tushare,yfinance
```

缺少 Token、依赖或网络时，本次同步必须标记失败或降级；系统不会生成 mock `price_bars` 冒充本次同步结果。

## 3. yfinance Provider

yfinance 用于美股和 ETF 的研究演示行情：

```env
MARKET_DATA_PROVIDERS=yfinance
```

落库来源使用 `yfinance_research` 并保留 research-only 标记。yfinance 没有生产 SLA，可能出现限流、缺失、映射差异和复权口径变化；正式环境需要授权行情源与质量协议。

## 4. FMP 边界

FMP 可作为可选基本面或旧页面 Provider，但不参与核心价格事实源优先级。即使配置 FMP，也不能用其临时返回值覆盖已经落库并带 lineage 的 PriceBar 或 FX。

## 5. LLM Provider

业务层通过 Qwen/OpenAI-Compatible 抽象调用模型，不把项目绑定到单一 SDK。

Qwen 示例：

```env
AI_PROVIDER=qwen
QWEN_API_KEY=from-secret-manager
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
```

通用 OpenAI-compatible endpoint：

```env
AI_PROVIDER=openai_compatible
OPENAI_COMPATIBLE_API_KEY=from-secret-manager
OPENAI_COMPATIBLE_BASE_URL=https://provider.example/v1
OPENAI_COMPATIBLE_MODEL=provider-model-name
```

模型只解释确定性风险结果和本次 evidence。结构化输出失败可进入安全 fallback，但 Trace 必须保存错误、重试和 fallback 状态；live evaluation 不允许静默回退 mock。

## 6. Embedding Provider

默认语义模型配置：

```env
EMBEDDING_PROVIDER=sentence_transformers
RAG_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
RAG_ALLOW_HASHING_FALLBACK=false
```

Embedding 在文档入库时生成并写入 pgvector，查询时只编码 query。Hashing provider 只适用于 development/test 的确定性工程测试；production 模型不可用时 readiness 和 Worker 应明确失败。

## 7. 失败与披露

- Provider 返回的数据必须带 source 和 `data_as_of`。
- 部分行情源失败时记录成功源、失败源和 degraded 状态。
- 全部行情源失败时不伪造有效 data cutoff，也不生成基于假行情的估值。
- Provider 未返回 token usage 或账单金额时，Trace 标记 estimated，而不是 provider actual。
- 完整触发矩阵见 [当前限制](current-limitations.md)。
