# PortfolioPilot

PortfolioPilot 是一个基于 FastAPI 的 AI 投资组合看板项目，适合个人部署和二次开发。这一版已经按个人仓库发布场景做过整理，默认接入千问兼容接口，并支持展示全球股票、中国 A 股和 Polymarket 持仓。

## 项目特点

- 基于 FastAPI + 原生前端脚本，部署简单，启动直接
- 支持通过 CSV 导入投资组合
- 默认使用千问兼容接口进行 AI 分析与交易建议
- 支持投资组合分析、调仓建议、历史记录、Telegram 推送
- 支持混合资产展示：
  - 美股及其他全球股票
  - 中国 A 股，如 `600519.SS`、`300750.SZ`
  - Polymarket 预测市场持仓
- 前端支持中英文切换

## 适用场景

这个项目适合以下用途：

- 作为你自己的投资组合分析面板
- 作为接入千问模型的个人 AI 金融助手原型
- 作为一个可继续扩展的 GitHub 开源项目基础版本

## 本地运行

推荐使用 Python 3.12。

macOS / Linux：

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 main.py
```

Windows：

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python main.py
```

启动后访问：

```text
http://localhost:8000
```

macOS 也可以直接运行 [start.sh](start.sh)：

```bash
chmod +x start.sh
./start.sh
```

如果你已经创建好虚拟环境，Windows 也可以直接运行 [start.bat](start.bat)。

## 千问配置

项目默认使用千问兼容模式。编辑 `.env`，至少配置以下参数：

```env
AI_PROVIDER=qwen
QWEN_API_KEY=你的千问API_KEY
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
```

如果需要调整推理模型或品牌信息，也可以继续补充：

```env
QWEN_REASONING_MODEL=qwen-plus
APP_NAME=PortfolioPilot
APP_TAGLINE=面向全球股票、中国A股与Polymarket的AI投资组合助手
```

说明：

- 未配置完整 API Key 时，项目仍可在演示模式下运行
- AI 分析、交易建议、部分自动化能力依赖千问接口配置

## Render 公网部署

项目已内置 [render.yaml](render.yaml)，适合通过 Render Blueprint 直接部署为公网 Web Service。

推荐配置：

- Runtime：Docker
- Region：Singapore
- Plan：Starter 或更高
- Persistent Disk：挂载到 `/app/cache`
- Portfolio CSV：`/app/cache/portfolio.csv`

部署步骤：

1. 将代码推送到 GitHub 仓库。
2. 打开 Render Dashboard，选择 `New` → `Blueprint`。
3. 连接这个 GitHub 仓库，Render 会自动读取 `render.yaml`。
4. 按提示填写 `sync: false` 的环境变量，至少建议设置：
   - `QWEN_API_KEY`
   - `FMP_API_KEY`
   - `DASHBOARD_USER`
   - `DASHBOARD_PASSWORD`
5. 创建服务后等待 Docker 构建完成。
6. 部署完成后访问 Render 提供的 `https://你的服务名.onrender.com`。

重要说明：

- `DASHBOARD_USER` 和 `DASHBOARD_PASSWORD` 强烈建议填写，否则公网地址会直接开放。
- Render 的普通文件系统是临时的，只有 `/app/cache` 下的数据会被 Persistent Disk 保留。
- 项目已将 `PARQET_PORTFOLIO_CSV` 指向 `/app/cache/portfolio.csv`，上传的 CSV 持仓会跟 SQLite 数据库一起保存在持久化磁盘里。
- 如果后续绑定自定义域名，可以在 Render 服务的 `Settings` → `Custom Domains` 中添加域名，再到 DNS 服务商处配置 CNAME。

## 支持的持仓类型

当前版本重点支持以下三类资产：

1. 全球股票
2. 中国 A 股
3. Polymarket 持仓

其中：

- 中国 A 股支持 `CNY` 币种
- A 股代码支持 `.SS` 和 `.SZ` 后缀
- Polymarket 持仓更适合通过 CSV 导入，并建议提供 `current_price`
- Polymarket 没有股票基本面数据，因此系统会使用轻量化评分逻辑分析其盈亏与价格变化

## CSV 导入格式

推荐使用以下表头：

```csv
ticker,shares,buy_price,current_price,buy_date,currency,sector,name,asset_type,market,exchange,country
AAPL,15,142.50,,2024-03-15,USD,Technology,Apple Inc.,equity,US,NASDAQ,US
600519.SS,3,1680.00,,2024-05-10,CNY,Consumer Defensive,Kweichow Moutai,cn_equity,CN-A,SSE,CN
POLY-BTC-150K-2026,80,0.31,0.36,2026-01-05,USD,Prediction Markets,BTC above 150k in 2026?,prediction_market,Polymarket,Polymarket,WEB3
```

字段说明：

- `ticker`：资产代码
- `shares`：持仓数量
- `buy_price`：买入价格
- `current_price`：当前价格，可选；Polymarket 建议填写
- `buy_date`：买入日期
- `currency`：币种，如 `USD`、`EUR`、`CNY`
- `sector`：行业
- `name`：资产名称
- `asset_type`：资产类型，如 `equity`、`cn_equity`、`prediction_market`
- `market`：市场标识
- `exchange`：交易所
- `country`：国家或来源标识

在 Dashboard 里通过 `CSV Import` 上传后，系统会把标准化后的持仓保存到项目目录下的 `portfolio.csv`（可通过 `.env` 里的 `PARQET_PORTFOLIO_CSV` 修改路径）。之后重启服务或执行刷新时，会优先读取这份本地 CSV，所以真实持仓可以长期保存在本地。`portfolio.csv` 已加入 `.gitignore`，避免误提交真实持仓。

## 新增投研与资管能力

这一版将项目升级为面向证券投研与基金资产管理场景的 LLM 金融资产分析、组合风控与智能调仓系统：

- `analytics/risk_metrics.py`：计算单资产和组合收益、年化波动、最大回撤、Sharpe、资产权重、行业集中度、资产类型暴露，并输出 `portfolio_risk_summary`
- `services/financial_analysis.py` + `prompts/financial_analysis_prompt.py`：调用千问兼容接口生成严格 JSON Schema 的组合分析；无 API Key 或 JSON 非法时自动回退到安全模板
- `rag/`：读取本地 `txt/md/csv`，执行 chunking、embedding 和本地向量检索；未安装 `sentence-transformers/faiss` 时自动使用轻量 hashing 检索
- `portfolio_optimizer/`：提供等权、简化风险平价、最小方差、均值-方差、LLM 风险调整权重策略，输出可解释的目标权重与调整原因
- `backtest/`：比较原始组合、等权、风险平价、最小方差、均值-方差、LLM 风险调整策略，输出收益、波动、回撤、Sharpe 和换手率

前端已新增：

- 组合风险总览
- 单资产风险解释
- AI 调仓建议
- RAG 证据来源
- 回测结果表格

## API 示例

风险总览：

```bash
curl http://localhost:8000/api/portfolio/risk-summary
```

结构化 AI 分析：

```bash
curl -X POST http://localhost:8000/api/ai/analyze-portfolio \
  -H "Content-Type: application/json" \
  -d '{"lang":"zh","top_k":5}'
```

RAG 检索：

```bash
curl -X POST http://localhost:8000/api/rag/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query":"科技行业集中度和AI监管风险","top_k":5}'
```

AI 风险调仓：

```bash
curl http://localhost:8000/api/portfolio/rebalance
```

策略回测报告：

```bash
curl http://localhost:8000/api/backtest/report
```

## RAG 本地证据

默认读取 `.env` 中的：

```env
RAG_DOCUMENT_DIR=rag_documents
RAG_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
RAG_CHUNK_SIZE=900
RAG_TOP_K=5
```

把新闻、公告、研报摘要、政策文本放入 `rag_documents/` 即可，支持 `.txt`、`.md`、`.csv`。该目录默认被 `.gitignore` 忽略，避免误提交非公开资料。

如果你想启用更强的本地语义检索，可以自行安装：

```bash
pip install sentence-transformers faiss-cpu
```

未安装这些库时，系统仍会用内置 hashing embedding 正常运行。

## 回测

运行：

```bash
python -m backtest.run_backtest
```

默认读取 [example_portfolio.csv](example_portfolio.csv)，并优先寻找：

```text
data/prices/example_historical_prices.csv
```

如果这个真实行情格式的 CSV 存在，回测会优先使用它；如果不存在，才会生成可复现的 mock price data。报告会生成到：

```text
cache/backtest_report.json
```

报告会包含：

- `data_source`：`historical_csv` 或 `mock_price_data`
- `start_date` / `end_date`：价格样本覆盖区间
- `asset_count`：实际参与回测的资产数量
- `mock_price_data_used`：是否使用 mock 行情

### 均值-方差优化器说明

`portfolio_optimizer/mean_variance_optimizer.py` 提供两个研究演示函数：

- `minimum_variance_portfolio`：在 long-only、权重和为 1、单资产最大权重、可选行业最大权重约束下，寻找低波动组合
- `mean_variance_portfolio`：在同样约束下，根据历史收益均值和协方差矩阵做均值-方差权衡

这两个函数已经接入 `python -m backtest.run_backtest`，回测报告会新增 `minimum_variance` 和 `mean_variance` 策略结果，并输出 `target_weight`、`weight_change`、`expected_return`、`expected_volatility`、`reason`。该模块仅用于投研流程演示和风险研究，不构成投资建议、交易建议或收益承诺。

如果没有真实历史行情数据，回测会生成固定随机种子的 mock price data，并在报告里标记：

```json
"mock_price_data_used": true
```

### 如何使用真实行情数据回测

推荐把组合和行情分别放在：

```text
data/portfolios/
data/prices/
```

项目已提供多资产组合示例：

```text
data/portfolios/example_multi_asset_portfolio.csv
```

`data/prices/example_historical_prices.csv` 是小型格式示例，适合验证流程；做真实评估时请替换为覆盖更长周期的日频行情。

历史行情 CSV 至少需要包含 `date`、`ticker`、`close` 三列，例如：

```csv
date,ticker,close
2026-04-20,AAPL,168.00
2026-04-21,AAPL,169.20
2026-04-20,600519.SS,1580.00
2026-04-21,600519.SS,1595.00
```

也支持宽表格式：

```csv
date,AAPL,MSFT,NVDA
2026-04-20,168,410,860
2026-04-21,169.2,412.3,875
```

使用真实行情文件运行：

```bash
python -m backtest.run_backtest \
  --portfolio data/portfolios/example_multi_asset_portfolio.csv \
  --prices data/prices/example_historical_prices.csv
```

也可以在 `.env` 中设置默认价格文件：

```env
BACKTEST_PRICE_CSV=data/prices/example_historical_prices.csv
```

## 测试

```bash
pytest
```

## LLM Evaluation

项目新增了结构化 LLM 金融分析质量评估模块：

```bash
python -m evaluation.run_llm_eval
```

默认输出：

```text
cache/evaluation_report.json
```

评估集包含 20 条组合风险测试用例，覆盖：

- 单资产集中
- 行业集中
- 高波动
- 高回撤
- 多资产分散
- 低风险组合
- 预测市场敞口

报告指标包括：

- `json_valid_rate`：LLM 输出是否能解析为合法 JSON
- `risk_detection_rate`：是否识别到测试用例预期风险
- `evidence_usage_rate`：是否引用了传入的本地证据来源
- `rebalance_explainability_rate`：调仓建议是否包含可解释理由
- `hallucination_flag_rate`：是否出现未知 ticker 或虚构证据来源

没有真实 `QWEN_API_KEY` 时会自动使用 mock LLM response。为了避免消耗模型额度，也可以强制 mock：

```bash
python -m evaluation.run_llm_eval --mock
```

如果已经配置千问 Key，并希望评估真实模型输出：

```bash
python -m evaluation.run_llm_eval --real
```

本项目在没有真实 `QWEN_API_KEY`、没有向量库、没有真实行情数据时也可以运行：AI 分析会使用安全模板，RAG 返回空证据或 hashing 检索，回测使用可复现 mock 行情。

## 后续可扩展方向

- 接入更多中国市场数据源
- 为 Polymarket 增加更细粒度的事件分析
- 补充千问语音或语音转写链路
- 增加 Docker / 容器化部署说明
- 增加更完整的多语言文档与截图
