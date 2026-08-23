# Parqet 兼容集成

Parqet 支持属于 PortfolioPilot 的可选兼容扩展，不是 PostgreSQL 交易账本、时点估值或受治理研究工作流的核心依赖。该扩展默认由 `ENABLE_PARQET=false` 关闭。

## 使用场景

- 从已有 Parqet 账户读取持仓或活动，帮助旧版 Dashboard 用户迁移展示数据。
- 在本地研究环境中验证旧 PortfolioSummary 与导入适配器。
- 作为历史兼容入口，而不是新的组合事实源。

正式主线仍要求交易进入 PostgreSQL `transactions`，并由 Position Rebuilder 和 Portfolio Valuation Service 生成派生快照。Parqet 返回值不能绕过账本直接成为正式估值事实。

## 字段映射

兼容 fetcher 会根据上游响应格式读取以下常见字段：

| Parqet/兼容字段 | PortfolioPilot 兼容字段 | 说明 |
|---|---|---|
| `ticker` / `symbol` | `ticker` | 缺失时可能使用已知 ISIN 映射；正式证券主数据仍以 PostgreSQL 为准 |
| `isin` / `asset.isin` | `isin` | 外部标识，不用于猜测币种 |
| `name` / `asset.name` | `name` | 展示名称 |
| `shares` / `quantity` / `amount` | `shares` | 兼容层持仓数量 |
| `purchasePrice` / `avgCost` | `avg_cost` | 旧展示成本，不替代 ledger cost basis |
| `currentPrice` / `price` | `current_price` | 仅用于兼容展示，必须保留来源和时点 |
| `currency` | `currency` | 缺省行为属于旧实现限制，不应用于正式证券主数据 |
| cash position/activity | `CASH` | 旧展示现金；正式主线按币种保存现金账本 |

## 当前兼容状态

- 实现位于 `fetchers/parqet.py` 和 `fetchers/parqet_auth.py`，归类为 Experimental / Legacy。
- 扩展支持缓存和多种历史响应格式，因此不应被视为稳定的核心 API 合同。
- Token 只允许通过环境或本地安全配置注入，不得提交到仓库。
- Parqet 不参与 A 股/美股价格事实源优先级，也不参与 governed RAG、Prompt Registry 或人工审核链路。
- 新功能不应继续依赖该 fetcher；需要迁移时应将标准化结果导入 PostgreSQL ledger。

## 官方文档

官方开发者入口名称为 **Parqet Developer Hub**：<https://developer.parqet.com/>。

仓库不再保存该站点的完整文本副本。第三方文档可能随时更新，字段和认证方式应以官方页面及实际授权为准。

## 为什么不是核心主线

PortfolioPilot 的目标是展示可追溯的多市场投研流程，而不是绑定某个个人投资组合服务。以 PostgreSQL ledger 为事实源可以统一 CSV/API 导入、时点重建、行情/FX lineage、风险分析和研究治理；Parqet 兼容层只服务旧数据迁移与实验场景。
