# 金融研究评测集 V2

## 目标与边界

V2 用于建立公开、可追溯、可人工审核的离线评测流程，而不是制造一个好看的总准确率。数据集不含
真实客户持仓、实习单位研报、付费正文或无授权材料。组合是模拟配置；公开事实带来源和数据截止日；
自动生成的标签默认 `pending`，不能进入 `human_gold_eval`。

数据文件、逐字段 schema 和 checksum 规则见
[`evaluation/datasets/v2/README.md`](../evaluation/datasets/v2/README.md)。

## 数据构成

- 60 个唯一 case，分别映射为 Retrieval、Generation 和 Workflow 输入；
- 36/12/12 的 train/dev/test split，case 不跨 split；
- 12 个明确标记的 synthetic portfolio；
- SEC、Microsoft Investor Relations、上海证券交易所等官方公开来源元数据；
- 公开正文只保留项目自行撰写的短摘要，原文件仍受发布方条款约束；
- synthetic restricted document 只用于权限测试，不代表任何内部资料；
- SHA256 覆盖每份文档内容、六个 JSONL 文件及数据集汇总 digest。

场景分布：持仓集中 8、行业集中 6、地域与币种 5、波动与回撤 6、流动性与现金拖累 5、
多资产风险 5、公开资料问答 8、跨文档冲突 4、时效过期 4、权限隔离 4、证据不足与拒答 5。

## 四种模式

| 模式 | 输入 | 强制约束 |
| --- | --- | --- |
| `synthetic_smoke` | V2 fixture 和 deterministic counterexamples | `mock_response_used=true`，只验证工程和指标链路 |
| `live_model_eval` | 显式捕获的真实模型 prediction bundle | mock/fixture Provider 直接拒绝，不 fallback |
| `human_gold_eval` | 真实模型 bundle + approved human labels | 无 reviewer、时间或完整标签即拒绝 |
| `production_monitoring` | 显式生产观测文件 | `production_data_used=true`，不能以 synthetic 代替 |

默认报告写入 `cache/evaluation/v2/<mode>/report-<timestamp>-<sha>.json`，不同模式和不同运行不会
覆盖。报告保存 commit SHA、worktree dirty 状态、dataset version、cutoff、模型、mock/live、
人工/生产数据标记、输入 checksum、样本数、置信区间和 badcase。dirty 状态为 `true` 的结果不能
被描述为该 SHA 的可复现发布快照。

Live/Human 模式读取的 prediction bundle 必须包含 `metadata`、`retrieval`、`generation` 和
`workflow`。其中 metadata 至少声明 `mock_response_used=false`、`response_source=live_model`、
Provider、模型、模型参数、带时区生成时间和运行时 commit SHA；缺任一字段均拒绝执行。bundle 的
SHA256 会进入报告，模型输出不会静默替换为 fixture。

Synthetic smoke 的 latency 和 cost 都是 fixture estimate，`actual_cost_case_count=0`。只有真实
Provider usage 字段才能计入 actual cost；没有账单 usage 时必须继续标为 estimated。

## 指标定义

Retrieval 分别计算 Recall@K、Precision@K、MRR、nDCG@K、unauthorized hit count 与 stale hit
rate。权限与时效指标不能被召回平均值掩盖。

Generation 分别计算 JSON compliance、事实正确性、数值一致性、claim support rate、citation
precision/completeness、unsupported claim rate 和 correct refusal rate。没有拒答案例时拒答指标为
`null`，而不是伪造 0 或 1。

Workflow 分别计算 tool selection、argument validity、execution success、rule validation、review
routing、publication safety 和 end-to-end latency。成本拆成 Provider usage 对应的 actual 与缺少账单
字段时的 estimated，不混成一个“真实成本”。

二元比例附 95% Wilson interval。Dashboard 同时展示样本数和 badcase 分布，不把不同指标平均成
一个未经定义的准确率。

## 人工审核流程

1. Reviewer 只审核固定版本 case，不直接改问题以迁就输出；
2. 填写 `reviewer_id`、四类 label、notes 和带时区的 `reviewed_at`；
3. 将 label 的 `review_status` 和三层 case 的 `reviewer_status` 同步设置为 `approved` 或 `rejected`；
4. 由另一名维护者复核来源、数字、引用和 split；
5. 更新 manifest 的 approved 数量并重新计算 SHA256；
6. 运行 V2 校验与 pytest 后才允许执行 `human_gold_eval`。

当前仓库没有独立人工审核结果，因此 approved 数量为 0。这意味着可以公开展示数据集 schema、
覆盖场景和 synthetic smoke 工程结果，但不能公开宣称真实模型的 factuality、groundedness、拒答率
或生产效果。
