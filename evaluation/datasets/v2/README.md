# PortfolioPilot 金融研究评测集 V2

本目录是一个公开可审计、可离线复现、等待独立人工复核的评测数据集。所有组合均为
`synthetic_portfolio`，不包含真实客户持仓；公开资料只保存来源元数据和项目自行撰写的短摘要，
不复制付费、受限或内部研报正文。

## 文件与 Schema

| 文件 | 作用 |
| --- | --- |
| `documents.jsonl` | 公开来源摘要与明确标记的 synthetic 文档 |
| `portfolios.jsonl` | 模拟组合、币种、市场、行业和风险指标 |
| `retrieval_queries.jsonl` | 检索问题、相关/过期/越权文档及权限上下文 |
| `generation_cases.jsonl` | 事实、风险标签、决策、引用和禁止声明 |
| `workflow_cases.jsonl` | 工具、参数、审核路由和发布安全预期 |
| `human_labels.jsonl` | 独立人工审核标签；初始状态全部为 `pending` |
| `dataset_manifest.json` | 来源策略、数据截止日、split 和 SHA256 |

三类 case 文件共享以下必填字段：

```text
case_id, question, portfolio_id, relevant_document_ids, expected_facts,
expected_risk_tags, expected_decision, required_citations, forbidden_claims,
as_of, difficulty, reviewer_status, category, split
```

`human_labels.jsonl` 每行包含：

```text
reviewer_id, review_status, factuality_label, citation_support_label,
numeric_consistency_label, refusal_label, notes, reviewed_at
```

四类标签使用 `pass | fail | not_applicable`。只有同时满足以下条件的记录才能进入
`human_gold_eval`：

1. `review_status=approved`；
2. `reviewer_id` 和带时区的 `reviewed_at` 均存在；
3. 四类人工标签均合法；
4. 三个 case 文件中的 `reviewer_status` 同步更新；
5. manifest 中 approved/pending 数量与标签文件一致；
6. 修改后重新生成并复核文件 SHA256。

自动生成器不会批准任何标签。当前 V2 的 60 个 case 均为 `pending`，因此尚不能用于声明
human-gold 模型效果。

## 分布与数据边界

60 个唯一 case 按 `36/12/12` 划分为 train/dev/test，并在检索、生成和工作流三层使用同一
`case_id`。场景包括集中度、行业、币种、波动回撤、流动性、多资产、公开事实问答、冲突、
时效、权限和拒答。

公开来源包括 SEC 官方申报、Microsoft Investor Relations 和上海证券交易所公开披露。
`content_origin=project_authored_paraphrase` 表示正文是项目自行概括，不代表源文件采用仓库许可。
每条 document 的 SHA256 scope 是 `stored_project_authored_content`，只校验仓库保存的摘要或 fixture；
它不是远端 SEC/SSE 原文件的 checksum，且 `source_document_cached=false`。
其余文档均标记为 `synthetic_fixture`，其中 restricted fixture 只测试权限逻辑，不是内部资料。

## 构建与校验

```bash
python -m scripts.build_evaluation_v2_dataset
python -m evaluation.run_v2_eval --mode synthetic_smoke
```

构建脚本是确定性的。`dataset_manifest.json` 的 `checksum.files` 覆盖六个 JSONL，
`dataset_digest` 再对文件名和各文件 SHA256 进行汇总。manifest 本身不进入 digest，避免自引用。

四种模式写入各自目录：

```text
cache/evaluation/v2/synthetic_smoke/
cache/evaluation/v2/live_model_eval/
cache/evaluation/v2/human_gold_eval/
cache/evaluation/v2/production_monitoring/
```

`live_model_eval` 必须传入真实模型预测 bundle，mock 或 fixture 会被拒绝；`human_gold_eval`
还必须存在 approved label；`production_monitoring` 只接受明确标记的生产观测文件。各模式不会
静默降级或互相覆盖。
