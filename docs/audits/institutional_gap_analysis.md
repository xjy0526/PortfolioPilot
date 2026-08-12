# PortfolioPilot 机构级金融科技架构差距审计

> 审计日期：2026-07-15
> 审计范围：当前工作区中的 API、风险指标、策略回测、LLM/RAG、Prompt、Agent、数据库、前端与自动化测试。
> 审计方式：静态代码审阅 + 相关测试执行；本阶段未修改任何业务功能。
> 目标定位：机构投研与风险管理平台，而非面向用户的交易执行产品。

> **快照说明（整改后补记）**：正文保留整改前的基线证据与优先级，便于审计追溯；其行号和“当前”措辞均指 2026-07-15 首次审计时的代码快照。后续同一工作分支已实施整改，状态见下表，不以覆盖原始审计结论的方式改写历史记录。

### 整改状态（当前工作分支）

| 原问题 | 状态 | 当前实现与剩余边界 |
|---|---|---|
| P0-1 历史行情与缺失语义 | 已整改 | 两个风险 API 经统一行情服务传入复权价格；不足样本返回 `null`、`metric_status`、`data_quality` 与 `as_of`。 |
| P0-2 样本内回测偏差 | 已整改 | 回测改为 point-in-time walk-forward，保存逐调仓快照、成本、基准、压力测试和 leakage checks。 |
| P0-3 Agent 控制 | 机构流程已整改 | 新研究报告 Workflow 强制规则校验与人工审核，具备幂等键、节点日志和预算限制；旧 Shadow Agent 仅保留给 `personal` 模式，尚未升级为企业级交易执行系统，也不得用于真实交易。 |
| P1-1 历史 LLM 策略 | 已整改 | LLM 权重策略已移除；当前回测只运行六种确定性策略，LLM 仅解释风险与证据。 |
| P1-2 知识库治理 | 已整改 | SQLite 文档、版本、chunk、ingestion job 模型及检索前权限/时效过滤已落地。 |
| P1-3 Prompt 治理 | 已整改 | Prompt Registry 支持草稿、版本、发布、回滚、同测试集比较及调用 Trace。 |
| P1-4 治理数据库 | 已整改 | 已新增 Prompt、知识版本、模型 Trace、工作流、审核和发布报告相关幂等表结构。 |
| P1-5 产品定位 | 已整改 | `fund_research` 模式隐藏交易/Shadow/Tech Picks 入口并使用中性研究措辞；`personal` 保持原功能。 |
| P1-6 风险测试 | 已整改 | 当前全量回归 `493 passed`，并覆盖行情缺失、stale、权限、版本、Prompt、Trace、人工审核和未来数据泄漏。 |
| P2-1/P2-2 统计与可复现性 | 部分整改 | 已补数据质量、运行方法论、调仓快照、基准与风险指标；生产级行情许可证、交易日历和不可变对象存储仍属于部署层工作。 |
| P2-3 企业身份与职责分离 | 未整改 | 当前仍是可选 Basic Auth；正式机构部署前仍需 OIDC/SAML、个人身份、RBAC/ABAC 与 maker-checker 策略。 |

## 1. 执行摘要

当前仓库已具备结构化风险输出、RAG 检索、结构化 LLM JSON、组合优化、策略比较和模拟组合等原型能力，但尚不满足机构级投研系统对数据血缘、时间一致性、模型治理、内容权限、人工复核、幂等执行与不可抵赖审计的基本要求。

最关键结论如下：

1. `/api/portfolio/risk-summary` 和 `/api/ai/analyze-portfolio` **没有使用历史价格数据**。两者仅传入当前持仓；组合收益、年化收益、波动、回撤和 Sharpe 因空收益序列全部显示为 `0`，而单资产收益又回退为持仓成本收益，形成口径混用。
2. 策略报告存在明确的**样本内偏差**：完整样本同时用于估计期望收益、协方差、风险标签和权重，并用于评价同一权重的收益与风险；没有训练/验证切分或 walk-forward。
3. 回测中的 `llm_risk_adjusted` **不是历史 LLM 策略**：风险分固定为 `6.5`，资产评论由完整样本风险标签机械生成。在线再平衡虽然可读取最近一次 LLM 分析，但结果只在进程内存中保存，缺失时使用确定性模板，无法形成可复现历史策略。
4. RAG、Prompt、模型 Trace、审核任务、审计日志均缺少机构级持久化模型；RAG 文档没有 metadata、文档版本、发布日期或权限组。
5. Shadow Agent 会在没有人工审批、请求幂等键和事务边界的情况下直接修改模拟持仓和现金；现有 decision log 只是业务摘要，不是完整审计日志。
6. 前端大量使用 Buy/Sell、交易建议、调仓建议和“自主 Agent 管理组合”等表达。即使实际为模拟交易，仍与机构投研/风险研究定位冲突并带来误导风险。
7. 相关 22 个现有测试全部通过，但测试主要验证函数输出形状和正常路径，没有验证上述关键治理风险。

### 1.1 总体优先级

| 优先级 | 数量 | 机构化门槛 |
|---|---:|---|
| P0 | 3 | 上线机构投研环境前必须关闭；否则核心风险数据和策略评价不可采信，Agent 也不具备安全执行边界 |
| P1 | 6 | 进入受控试点前完成；解决模型、知识、Prompt、数据与产品定位治理 |
| P2 | 3 | 规模化使用前完成；增强统计严谨性、运行可复现性与质量门禁 |

## 2. 审计口径与结论边界

- “未发现”表示在当前仓库代码、模型和测试中没有发现相应能力，不代表外部基础设施一定不存在。
- Shadow Agent 当前修改的是本地 SQLite 中的模拟组合，并未发现券商下单接口；本报告不会把它描述为真实资金交易。但机构治理仍需覆盖模拟决策，因为其输出可能被展示、比较、复用或后续接入真实执行。
- 评级基于“机构投研系统”的目标状态，而不是个人仪表盘或功能演示的标准。
- 本次未评价模型投资观点是否正确，重点评价数据、时间、治理、权限、执行和审计控制。

## 3. P0 问题

### P0-1 风险 API 未接历史行情，缺数被伪装为零风险/零收益

**问题**

`/api/portfolio/risk-summary` 与 `/api/ai/analyze-portfolio` 都直接调用 `build_portfolio_risk_summary(summary.stocks)`，未传 `price_data`。这意味着两个 API 的组合时序指标并非由历史行情计算。无行情时：

- 组合 period return、annual return、annual volatility、max drawdown、Sharpe 全部为 `0`；
- 单资产 `return` 回退到当前价相对持仓成本的 P&L，而波动、回撤、Sharpe 仍为 `0`；
- 如果只有部分资产缺行情，缺行情资产仍会使用成本收益回退，但汇总级 `uses_position_return_fallback` 会错误显示为 `false`；
- `risk_score` 继续基于这些零值计算，并可能仅因集中度得到“低/中”风险；
- 前端把零值直接格式化为有效指标，LLM Prompt 也把它们作为风险事实输入。

**代码证据**

- `routes/research.py:28-36`：风险 API 只传持仓，没有加载或传入历史价格。
- `routes/research.py:42-56`：AI 分析 API 使用相同的无行情风险汇总，并将其送入 RAG 查询和 LLM。
- `analytics/risk_metrics.py:179-188`：`price_data` 默认为 `None`，由此产生空收益矩阵。
- `analytics/risk_metrics.py:195-201`：资产收益序列为空时，仅 `return` 回退到 `_position_return`，波动、回撤、Sharpe 继续由空序列计算。
- `analytics/risk_metrics.py:48-109`：累计收益、年化收益、波动、回撤和 Sharpe 对空/不足样本统一返回 `0.0`。
- `analytics/risk_metrics.py:217-246`：组合指标仍按普通数值返回；`data_quality` 虽标记历史点数为 0，但没有使指标失效。
- `analytics/risk_metrics.py:195-198,241-246`：回退发生在逐资产层面，但标记仅检查整个 `returns` 是否为空，无法披露部分资产回退。
- `analytics/risk_metrics.py:250-273`：风险评分把缺失产生的零波动、零回撤、零 Sharpe 当作真实值参与评级。
- `static/app.js:1867-1876`：前端直接展示这些指标，仅另列历史点数，没有 `N/A` 或不可用状态。

**业务风险**

- 将“未知风险”错误呈现为“零风险”，可能导致投研人员低估风险并错误排序组合。
- 成本收益与时序收益混用，无法进行跨账户、跨建仓日或跨资产比较。
- LLM 会在错误数值基础上生成看似完整的风险解释，放大自动化偏差。
- API 消费方无法区分真实零值、缺失值、估算值和成本口径值，不满足模型风险管理与数据血缘要求。
- 部分覆盖场景下的数据质量标记还会给出错误的“未使用回退”信号。

**建议方案**

1. 建立统一的 `HistoricalMarketDataService`，按资产、交易日、币种和复权口径提供价格矩阵；API 明确传入风险引擎。
2. 风险结果使用三态/多态字段：`value`、`status`（`valid`/`insufficient_data`/`estimated`/`stale`）、`observations`、`as_of`、`window`、`source`、`currency`、`adjustment`。
3. 缺历史数据时，时序指标返回 `null`，不可返回 `0`；成本收益另命名为 `unrealized_return_since_cost`，不得冒充期间收益。
4. 设定最小样本门槛、覆盖率和新鲜度门槛；组合指标仅在资产覆盖率达标时发布。
5. LLM 层在 `data_quality.status != valid` 时禁止引用相应指标作定量结论，并在输出中披露缺失。

**验收标准**

- API 集成测试证明：给定确定的历史价格，两个 API 返回与基准计算一致的收益、波动、回撤和 Sharpe。
- 未提供或样本不足时，上述时序字段为 `null` 且状态为 `insufficient_data`，不再返回 `0`。
- 仅部分资产有历史行情时，逐资产状态和组合覆盖率准确披露，且不会把回退标记为 `false`。
- 单资产成本收益与历史期间收益使用不同字段和清晰口径。
- 输出包含 `as_of`、观察数、起止日期、数据源、复权方式、币种和资产覆盖率。
- 前端与 Prompt 对不可用指标显示/表达为 `N/A`，不会生成“低波动/无回撤”结论。

### P0-2 策略回测使用完整样本估权并在同一完整样本评价

**问题**

当前策略报告把一整段价格数据先转成完整收益样本，再用同一完整样本估计期望收益、协方差、波动、回撤和风险标签，随后计算各策略固定权重，并在原完整收益样本上评价。这是明确的样本内优化/评价闭环。除 equal weight 外，风险平价、最小方差、均值方差和所谓 LLM 风险调整均直接或间接看到评价期数据。

**代码证据**

- `backtest/strategy_backtester.py:58-65`：完整价格样本被一次性转换，完整样本均值和协方差用于优化。
- `backtest/strategy_backtester.py:67-74`：完整样本风险指标生成资产风险标签。
- `backtest/strategy_backtester.py:76-110`：所有目标权重一次性由上述完整样本估计。
- `backtest/strategy_backtester.py:121-133`：相同的完整 `returns` 用于所有策略评价。
- 未发现 train/test split、rolling/walk-forward、rebalance calendar、信息可得时间、交易成本、滑点或延迟处理。
- `routes/research.py:115-129`：API 可直接生成并返回该报告，且默认使用示例组合与解析出的默认价格 CSV。

**业务风险**

- 回测收益与 Sharpe 系统性偏高，优化策略相对基准的优越性不可作为投资决策证据。
- 对完整样本风险较低或收益较高资产的事后偏好会被误认为事前能力。
- 无交易成本与再平衡约束，策略容量、换手和可执行性无法评价。
- 报告若进入模型验证、投委会材料或客户材料，会构成方法论误导。

**建议方案**

1. 改为 expanding-window 或 rolling-window walk-forward：每个决策日只使用该时点之前可获得的数据估计参数。
2. 明确训练窗、持有窗、再平衡频率、最小历史长度和 embargo；权重在下一个可交易时点生效。
3. 加入交易成本、滑点、换手惩罚、资产上市/退市与缺数规则。
4. 报告区分 in-sample diagnostics 与 out-of-sample performance；主结论只使用 OOS。
5. 保存每个再平衡时点的输入数据版本、参数、权重、模型/Prompt 版本和执行时间。

**验收标准**

- 测试使用“未来收益突变”数据证明：改变决策日之后的数据不会改变决策日权重。
- 报告逐期输出 `estimation_start/end`、`decision_time`、`effective_time`、`holding_period` 和 OOS 收益。
- 所有优化策略的主指标均由未参与估计的样本产生。
- 交易成本和滑点可配置，报告同时展示 gross/net performance 与 turnover。
- 有独立基准、参数敏感性和至少一种防过拟合检验。

### P0-3 Agent 缺少人工审核、幂等性、事务边界和完整审计日志

**问题**

Shadow Agent 在 LLM 返回后直接执行模拟买卖，没有“提议—审核—批准—执行”状态机。手动 POST、定时任务或重试可重复触发；请求没有 idempotency key，周期没有互斥锁/唯一业务键。一次交易又被拆为持仓更新、现金更新、交易日志三个独立提交，进程中断时可能部分成功。现有 decision log 只保存摘要，不包含完整输入输出、操作者、审批人、版本、关联交易、请求 ID 或前后状态。

**代码证据**

- `main.py:224-243`：工作日自动调度 Shadow Agent，无人工审核节点。
- `routes/shadow_portfolio.py:31-44`：任何通过可选 Basic Auth 的用户都可直接触发周期；没有请求 ID、权限角色或审批参数。
- `services/shadow_agent.py:74-112`：LLM 决策后立即调用 `_execute_trades`。
- `services/shadow_agent.py:700-884`：执行逻辑直接改写持仓和现金并写交易记录，没有审批状态或幂等校验。
- `database.py:395-449`：持仓 UPSERT 和交易 INSERT 各自 `commit`；`shadow_set_cash` 也独立提交，无法保证原子性。
- `database.py:119-128,508-536`：decision log 仅有周期摘要、数量、AI reasoning 和组合金额，不是完整模型/行为审计记录。
- `main.py:332-336`：认证是可选的全局 Basic Auth；未发现 RBAC、maker-checker 或职责分离。

**业务风险**

- 重复请求/调度重入会重复买卖；异常中断可能造成持仓、现金和流水不一致。
- 无法证明某次动作由谁、基于什么数据/Prompt/模型提出、由谁批准、最终执行了什么。
- 即使当前为纸面交易，也会污染策略评估与审计证据；未来接入真实执行时风险会直接放大。
- 单一共享凭据无法满足最小权限、职责分离和个人责任追踪。

**建议方案**

1. 引入 `agent_run`、`decision_proposal`、`review_task`、`approval_event`、`execution_event` 状态机；默认只产出 proposal。
2. 对每个运行和动作使用唯一 `run_id`、`decision_id` 与 idempotency key，并以数据库唯一约束阻止重复执行。
3. 将现金、持仓、交易和审计事件放入同一数据库事务；使用乐观锁/版本号防并发覆盖。
4. 高风险动作实行 maker-checker 双人复核；审批应绑定具体输入哈希和提案版本，变更后自动失效。
5. 建立 append-only audit log，保存 actor、role、request ID、时间、前后状态、输入/输出哈希、Prompt/模型/知识版本、审批与执行结果。

**验收标准**

- 未批准的 proposal 无法进入 executed；审批人与提案人可按策略要求强制分离。
- 同一 idempotency key 重放 100 次只产生一笔执行记录。
- 故障注入测试证明任意一步失败时现金、持仓、交易流水全部回滚。
- 并发触发测试只能有一个有效 run，其他请求返回已存在的 run 状态。
- 任意交易可从审计日志完整重建其输入、模型判断、人工审批和状态变化。

## 4. P1 问题

### P1-1 `llm_risk_adjusted` 回测不是历史 LLM 结果，在线结果也不可追溯

**问题**

回测策略名暗示 LLM 历史策略，但实际使用固定风险分 `6.5`，资产评论由同一回测样本的风险标签生成，评论文本固定为 `Backtest-derived risk proxy`。在线 `/api/portfolio/rebalance` 可使用最近一次结构化 LLM 结果，但它只保存在 `portfolio_data` 内存中；没有结果时改用确定性模板，因此同名方法可能代表真实 LLM、fallback 或固定参数三种不同机制。

**代码证据**

- `backtest/strategy_backtester.py:97-106`：`llm_risk_score=6.5`，评论由样本风险标签机械构造。
- `portfolio_optimizer/strategies.py:61-162`：所谓 LLM 调整本质是固定乘数规则（high `0.65`、medium `0.88`、low `1.05` 等）。
- `routes/research.py:56-57`：最近 LLM 分析只写入进程内 `portfolio_data`。
- `routes/research.py:89-100`：在线再平衡读取内存结果；缺失时使用 safe template。
- `services/financial_analysis.py:44-48,77-83`：未配置或调用失败时返回 mock/fallback。

**业务风险**

- 策略标签与实际方法不一致，使用者可能把规则代理误认为经过历史 LLM 决策验证。
- 服务重启、并发用户或不同组合会破坏结果关联，无法复现某次再平衡。
- 真实模型、fallback 与 mock 的表现混在一起，指标对比失真。

**建议方案**

- 将策略明确拆分为 `rule_based_risk_adjusted` 与 `historical_llm_risk_adjusted`。
- 只有存在逐时点、不可变的历史 LLM Trace 时才运行后者；每个决策时点使用当时可得输入。
- 在线结果持久化并绑定 portfolio snapshot、Prompt version、model deployment、RAG snapshot 和解析状态。
- 报告按 `qwen`/`fallback`/`mock` 分层，禁止混合计算策略业绩。

**验收标准**

- 报告中的每个 LLM 权重都能关联到真实历史 `trace_id`；无 Trace 时策略标记为不可用而非生成代理结果。
- 策略名称、method 字段和用户界面准确反映规则或模型来源。
- 服务重启后仍能复现同一历史决策。
- fallback/mock 不进入真实 LLM 策略 KPI。

### P1-2 RAG 文档模型缺少 metadata、版本、发布日期和权限组

**问题**

RAG 仅递归读取本地 txt/md/csv，并把 path、文件名、chunk index 和文本存入内存索引。没有文档实体、metadata schema、document version、publish/effective/expiry date、permission group、审批状态、来源可信度、租户或软删除。

**代码证据**

- `rag/retriever.py:30-37`：`DocumentChunk` 只有 id、text、source、path、chunk_index。
- `rag/retriever.py:107-114`：检索结果只返回上述字段和相似度。
- `rag/retriever.py:139-152`：文档加载只返回 path/source/text。
- `rag/retriever.py:189-203`：chunk id 由路径、序号和文本片段产生，没有文档版本实体。
- `rag/retriever.py:241-247`：缓存签名依赖文件 mtime/size，只用于重建索引，不构成可治理版本。
- `routes/research.py:69-78`：检索 API 没有用户/权限组过滤条件。

**业务风险**

- 过期、未发布、被撤回或无权限文档可能进入模型上下文。
- 无法回答“结论基于哪一版研究材料、当时是否已发布”。
- 文件覆盖后旧版本消失，历史模型输出不可复现。
- 不同团队/客户之间可能发生知识越权暴露。

**建议方案**

- 建立 `document`、`document_version`、`document_acl`、`ingestion_job`、`chunk` 模型。
- metadata 至少包含 owner、source、document_type、jurisdiction、language、publish/effective/expiry date、status、classification、permission groups、content hash。
- 检索前执行 RBAC/ABAC 权限过滤和生效日期过滤；检索结果返回版本与引用定位。
- 历史 Trace 固定到 immutable RAG snapshot/index version。

**验收标准**

- 未发布、过期、撤回或无权限版本在检索层无法返回。
- 同一 document 支持多版本并可指定 as-of 时间检索。
- 每个 evidence 返回 document_id、version_id、publish date、权限判定与精确 chunk/citation。
- 删除/替换当前文档后，历史 Trace 仍可重放其原始证据快照。

### P1-3 Prompt 没有版本管理、发布、回滚和指标对比

**问题**

结构化金融分析 Prompt 以 Python 常量和字符串拼接存在源码中。仓库未发现 Prompt registry、语义版本、草稿/审批/发布状态、环境绑定、灰度、回滚或版本维度 KPI。

**代码证据**

- `prompts/financial_analysis_prompt.py:8-59`：schema 与 system instruction 是源码常量。
- `prompts/financial_analysis_prompt.py:62-87`：用户 Prompt 由单个函数直接拼接。
- `services/financial_analysis.py:50-65`：运行时直接导入当前源码 Prompt，没有记录 Prompt ID/version/hash。
- `evaluation/llm_eval.py` 与 `evaluation/run_llm_eval.py` 提供输出评估能力，但未发现其与 Prompt 发布、流量分配、回滚或线上 Trace 关联。
- 数据库 schema 未包含 Prompt 表。

**业务风险**

- 代码部署即修改线上 Prompt，无法独立审批和快速回滚。
- 无法确定某个历史输出使用了哪一版 system/user Prompt 和 schema。
- 不同版本质量、成本、延迟、拒答率和人工通过率无法对比。

**建议方案**

- 建立 Prompt registry：prompt、prompt_version、release、deployment、evaluation_run、metric。
- Prompt 版本不可变，包含模板、schema、参数、owner、变更原因、审批人、内容哈希。
- 支持 draft → review → approved → published → retired；环境和流量绑定 release，可一键回滚。
- 线上 Trace 强制记录 Prompt version，离线/线上指标按版本切片。

**验收标准**

- 任一线上生成请求均返回/记录 prompt_version_id 和 content hash。
- 未审批版本不能发布到 production；回滚在约定 SLA 内完成且有审计事件。
- 仪表盘可对比至少正确性、格式成功率、人工通过率、延迟、token/cost 和安全指标。
- 同一输入、模型快照和 Prompt 版本可重放并解释差异。

### P1-4 数据库缺少机构 AI/内容治理核心实体

**问题**

SQLite 目前只持久化组合快照、分数历史、分析摘要和 Shadow 组合相关表。没有 Prompt、文档版本、模型 Trace、审核任务和通用审计日志；分析报告也只保存少量聚合字段。

**代码证据**

- `database.py:41-75`：核心表只有 portfolio snapshots、score history 和简化 analysis reports。
- `database.py:77-133`：其余为 Shadow portfolio、transactions、performance、decision log 和 key-value meta。
- `database.py:67-75`：analysis_reports 不保存输入快照、输出、模型、Prompt、RAG、状态或哈希。
- 全仓库未发现 `prompt_version`、`document_version`、`model_trace`、`review_task`、`approval_event`、`audit_log` 等持久化实体。

**业务风险**

- P1-1 至 P1-3 及 P0-3 的治理要求没有可靠落点。
- 无法满足可追溯、可重放、留痕、访问审计、模型事件调查和监管取证。
- 进程内结果与文件索引在重启/覆盖后丢失历史上下文。

**建议方案**

- 增加具备迁移机制的治理 schema，至少覆盖：Prompt/版本/发布、文档/版本/ACL、model trace/span、review task/decision、audit event。
- 所有实体使用稳定 UUID、tenant、created_by、timestamps、status、version、content hash；敏感字段分级与加密。
- 审计事件 append-only，必要时外发不可变存储；业务表与审计事件通过 correlation ID 关联。
- 从 SQLite 原型迁移到支持并发、备份、行级权限和迁移审计的托管关系数据库，SQLite 仅保留本地开发用途。

**验收标准**

- 数据库迁移可前滚/回滚并在 CI 中验证。
- 一条 AI 输出可单查询关联到输入数据、Prompt、模型、RAG 版本、解析结果、人工审核和后续动作。
- ACL 与租户隔离有数据库/服务层测试；审计日志不可通过普通业务 API 修改或删除。
- 定义并验证留存、删除、归档、备份恢复和敏感数据访问策略。

### P1-5 前端及产品语言与机构投研定位冲突

**问题**

前端突出 Buy/Sell 评级、交易建议、AI 调仓和自主 Shadow Agent。Shadow 页面宣称 Agent “自主”管理模拟组合，允许用户立即启动周期，并展示买卖流水。虽然 Prompt 中有研究免责声明、Shadow 也为虚拟组合，但主交互和标签仍容易被理解为交易导向产品。

**代码证据**

- `static/index.html:175-185`：首页核心统计直接使用 Buy/Hold/Sell。
- `static/index.html:548-565`：突出 “Shadow Portfolio Agent”、自主管理和“Agent 启动”操作。
- `static/index.html:803-817`：配置“最低买入分数”。
- `static/app.js:1926-1948`：一键生成结构化 AI 分析。
- `static/app.js:2009-2025`：展示 AI rebalance suggestions。
- `static/app.js:4439-4466`：前端手动触发 Agent 周期并报告交易数。
- `routes/analysis.py:282-320`：API 名称与描述为 AI Trade Advisor，可评价 buy/sell/increase。
- `docs/architecture.md:43-45,307-321`：架构文档明确描述 Trade Advisor 和自主 Buy/Sell/Hold 引擎。

**业务风险**

- 研究观点、风险提示和交易指令边界不清，带来合规、适当性和品牌定位风险。
- 用户可能把模型分数/建议直接当作可执行指令，忽略数据质量与人工判断。
- 后续机构客户难以通过界面区分 research recommendation、risk flag、proposal 和 approved order。

**建议方案**

- 建立统一产品词汇：`research view`、`risk flag`、`increase/decrease exposure scenario`、`proposal`，避免无上下文 Buy/Sell。
- 将 Shadow Agent 重定位为“策略沙盒/模拟研究”，默认关闭自动周期；显著标注模拟、非订单、未经审批。
- 研究输出与任何执行工作台视觉、权限和流程隔离；调仓只生成待复核方案。
- 免责声明之外增加数据状态、模型来源、as-of、置信度和审批状态。

**验收标准**

- 机构模式的主要页面不存在可被合理理解为直接下单的 Buy/Sell/自动交易文案。
- 所有建议明确标记 research/proposal、数据时点、来源和审核状态。
- Shadow/沙盒与真实组合在颜色、命名、URL、权限和数据模型上清晰隔离。
- 合规/投研用户验收测试能够一致识别“观点、提案、批准、执行”四类状态。

### P1-6 关键风险没有自动化测试保护

**问题**

现有测试覆盖了风险函数正常输入、RAG 基本检索、LLM JSON 解析、优化器规则和回测报告生成，但没有覆盖两个目标 API 的历史行情接入、缺数语义、回测时间隔离、真实历史 LLM Trace、RAG 权限/版本、Prompt 生命周期、Agent 审批/幂等/事务或审计完整性。

**代码证据**

- `tests/test_risk_metrics.py:36-62`：仅以显式价格 DataFrame 验证结果结构，没有测试 API 实际是否传行情。
- `tests/test_risk_metrics.py:65-77`：无价格用例只检查 ETF 分类，没有断言时序指标应为 unavailable。
- `tests/test_strategy_backtester.py:18-87`：只验证报告生成、mock/CSV 来源和策略名称，没有检测未来数据泄漏或 OOS。
- `tests/test_portfolio_optimizer.py:34-52`：LLM 调整测试使用手工风险分/评论，只验证高风险资产降权。
- `tests/test_rag_retriever.py:12-37`：只覆盖本地文档检索和 fallback，没有版本、日期或 ACL。
- `tests/test_financial_analysis.py:26-56`：只覆盖 JSON 解析和 fallback 结构。
- 全仓库测试未引用 `/api/portfolio/risk-summary`、`/api/ai/analyze-portfolio`、`/api/portfolio/rebalance`、`/api/backtest/report` 或 `/api/shadow-portfolio/run`。

**业务风险**

- 当前绿灯不能证明机构关键控制有效；重大数据/治理回归可以在全部测试通过时进入生产。
- 缺少针对时间泄漏、越权、重复执行和部分提交的负向测试。

**建议方案**

- 建立“机构关键控制测试矩阵”，每个 P0/P1 控制至少一条正向、一条负向和一条故障注入测试。
- 增加 API contract、时间旅行/未来数据隔离、property-based 风险计算、权限矩阵、并发幂等、事务回滚和审计重建测试。
- 将关键控制测试设为 CI 必过门禁，报告覆盖率之外同时报告 control coverage。

**验收标准**

- P0/P1 每条验收标准均映射到自动化测试 ID 和责任人。
- CI 能故意捕获：历史行情未传入、缺数返回 0、未来数据影响过去权重、无权限文档被召回、未审批执行、重复请求和部分提交。
- API schema 有版本化 contract test，关键字段语义变更会阻断合并。
- 关键控制测试在独立、可复现环境中运行，不依赖真实外部 API。

## 5. P2 问题

### P2-1 风险计算缺少机构级统计与数据口径控制

**问题**

即使未来接入历史价格，当前实现仍缺少最小观察数、交易日历、复权/公司行动、币种归一、无风险利率期限与来源、非同步交易及缺失覆盖率政策。`sanitize_price_frame` 会先 forward fill 再 backward fill，领先缺口会被未来价格回填；组合收益对资产缺失收益直接填 `0`。

**代码证据**

- `analytics/risk_metrics.py:31-36`：价格执行 `ffill().bfill()`，未返回插补标记。
- `analytics/risk_metrics.py:59-68`：任何非空样本都可年化，没有最小样本门槛。
- `analytics/risk_metrics.py:76-109`：不足两点返回 0，而非 unavailable。
- `analytics/risk_metrics.py:171-176`：组合收益对缺失资产日收益填 0。
- `analytics/risk_metrics.py:15-16`：固定 252 日和 2% 无风险利率，没有日期、币种、曲线来源或配置血缘。

**业务风险**

- 新上市、停牌、跨市场和跨币种资产的波动与相关性可能被系统性低估。
- 插补策略和年化假设对结果的影响不可见，跨期比较缺乏一致口径。

**建议方案**

- 引入交易日历、复权价格与 FX 归一；禁止默认 backward fill，插补必须带 lineage。
- 定义最小观察数、资产/日期覆盖率、异常值与停牌规则。
- 无风险利率使用与币种/期限匹配的曲线快照并记录来源。
- 对现金、预测市场、股票和 ETF 使用明确的资产类别方法策略。

**验收标准**

- 领先缺失值不会使用未来价格回填；任何插补都有标记和覆盖率指标。
- 低于样本/覆盖率门槛的指标返回 unavailable。
- 多市场、多币种、停牌、IPO、拆股和分红测试与独立基准一致。
- 风险结果可追溯到交易日历、FX、复权和无风险曲线版本。

### P2-2 回测数据与报告缓存缺少可复现运行标识

**问题**

策略报告默认写入单一 JSON 文件，API 在文件存在时直接返回，不校验组合、价格、参数或代码是否变化。缺少 run ID、配置哈希、数据哈希、代码版本和生成时间。价格缺失时可生成 mock 数据；当前默认示例 CSV 存在时会优先使用它，但报告仍容易被误当作当前组合回测。

**代码证据**

- `routes/research.py:115-129`：固定缓存路径；`force=false` 时直接返回旧文件。
- `routes/research.py:123-127`：固定使用 `example_portfolio.csv`，并非当前 `portfolio_data`。
- `backtest/strategy_backtester.py:135-155`：报告有数据路径和日期，但没有 run/config/data/code hash 或生成时间。
- `backtest/strategy_backtester.py:159-179,194-210`：价格文件不存在时生成确定性 mock。

**业务风险**

- 用户可能把示例组合、旧参数或旧数据报告理解为当前组合结果。
- 无法确认两个报告差异源于数据、配置、代码还是随机过程。

**建议方案**

- 每次运行生成 immutable run artifact，记录 portfolio/data/config/code/prompt/model hashes 与 `generated_at`。
- 缓存键包含所有输入哈希；API 明确返回 `portfolio_scope=example/current` 和数据等级。
- 机构模式禁止自动 mock fallback；mock 只能在显式 demo/test 模式运行并加水印。

**验收标准**

- 任意输入或代码版本变化都会产生新 run，不会命中旧缓存。
- 报告可完整复现，且 UI 显著显示组合范围、数据来源、as-of 和 demo 状态。
- 机构环境无真实合格数据时返回 unavailable，不生成 mock 业绩。

### P2-3 身份权限模型不足以支持机构职责分离

**问题**

当前只有可选 Basic Auth，认证未配置时所有路由直接放行；配置后也只有共享用户名/密码，没有个人身份、角色、组合/文档权限、操作级授权或会话审计。

**代码证据**

- `main.py:332-336`：仅在配置用户名/密码时启用中间件。
- `middleware/auth.py:26-35`：未配置认证时直接放行。
- `middleware/auth.py:37-58`：单一 Basic Auth 校验，没有角色或资源级权限。
- `routes/shadow_portfolio.py:31-130`：run/reset/config 等高影响操作没有独立授权策略。

**业务风险**

- 无法落实最小权限、个人责任、maker-checker、离职撤权和权限审计。
- RAG permission group 与审核流程即使建模，也缺少可信主体进行判定。

**建议方案**

- 接入企业 IdP（OIDC/SAML），使用个人身份、MFA 和短期 token。
- 建立 RBAC + 资源级 ABAC：viewer、analyst、reviewer、approver、admin；组合、文档组和环境均纳入策略。
- 高影响操作强制 step-up authentication，并记录授权决策。

**验收标准**

- 生产环境无法在未配置认证时启动或暴露业务 API。
- 权限矩阵测试覆盖每个角色对每类资源/动作的 allow/deny。
- 审计记录使用稳定个人主体 ID，不使用共享账号作为最终责任主体。
- 审核人与执行权限满足职责分离策略。

## 6. 十项重点检查结论对照

| # | 检查项 | 结论 | 对应问题 |
|---:|---|---|---|
| 1 | 两个目标 API 是否真正使用历史价格 | 否；均只传当前持仓 | P0-1 |
| 2 | 缺行情时收益、波动、回撤、Sharpe 如何处理 | 单资产收益回退成本收益；其余及组合指标返回 0 | P0-1、P2-1 |
| 3 | backtest 是否存在完整样本估权并同样本评价 | 是，存在明确样本内偏差 | P0-2 |
| 4 | `llm_risk_adjusted` 是否用真实 LLM 历史结果 | 回测否；固定 6.5 + 样本代理标签 | P1-1 |
| 5 | RAG 是否支持 metadata/version/publish date/permission group | 否 | P1-2 |
| 6 | Prompt 是否支持版本、发布、回滚、指标对比 | 否 | P1-3 |
| 7 | Agent 是否包含人工审核、幂等性和审计日志 | 无人工审核和幂等；仅有不完整业务摘要日志 | P0-3 |
| 8 | 前端是否有定位冲突内容 | 是，Buy/Sell、Trade Advisor、AI rebalance、自主 Shadow Agent | P1-5 |
| 9 | 数据库是否支持五类治理对象 | 否；只有简化分析/Shadow 表 | P1-4 |
| 10 | 测试是否覆盖关键风险 | 否；覆盖功能正常路径，不覆盖治理控制 | P1-6 |

## 7. 建议实施顺序

### 阶段 A：先让数字“可采信”

1. 接历史行情与数据质量状态，缺数改为 `null/unavailable`。
2. 将 LLM 与前端对无效风险指标的消费阻断。
3. 把回测改为 walk-forward/OOS，并禁止机构模式 mock 业绩。

### 阶段 B：建立“可治理、可重放”基础

1. 落地 Prompt、文档版本/ACL、模型 Trace 和 immutable run artifact。
2. 建立 Prompt 发布/回滚和版本指标。
3. 将历史 LLM 策略绑定真实逐时点 Trace，规则策略单独命名。

### 阶段 C：建立“人控、幂等、可审计”工作流

1. Agent 默认只提议，不直接执行。
2. 上线审核任务、职责分离、幂等键、事务和 append-only 审计。
3. 接企业身份与资源级权限。

### 阶段 D：重塑机构投研产品表达

1. 将 Buy/Sell/Trade/Autonomous 语言重构为研究观点、风险标记、情景方案和待审核提案。
2. 沙盒与真实组合/流程清晰隔离。
3. 将关键控制测试纳入 CI 发布门禁。

## 8. 测试执行记录

执行命令：

```bash
pytest -q \
  tests/test_risk_metrics.py \
  tests/test_strategy_backtester.py \
  tests/test_portfolio_optimizer.py \
  tests/test_rag_retriever.py \
  tests/test_financial_analysis.py
```

结果：`22 passed in 51.73s`。

该结果说明当前相关单元测试在审计时工作区可通过，不代表 P0/P1 控制已实现；具体缺口见 P1-6。

## 9. 已有可复用基础

为避免把报告理解为全面否定，以下能力可以作为整改基础：

- 风险计算已集中在 `analytics/risk_metrics.py`，便于统一改造数据质量语义。
- LLM 输出有 JSON schema、解析校验、fallback 来源标记与研究免责声明。
- RAG evidence 已返回 chunk ID、source、path 和 score，可扩展为版本化引用。
- 回测报告已区分 historical CSV 与 mock，并输出策略权重和解释。
- Shadow Agent 已有现金、仓位、单笔/单周期限制及交易/决策记录，可迁移到正式 proposal/review/execution 状态机。

这些基础解决的是“功能可运行”，后续整改重点是把它们提升到“数据可信、时间一致、权限受控、全程可追溯”。
