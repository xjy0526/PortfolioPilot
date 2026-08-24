# PortfolioPilot Human Review Dataset V3

V3 是一个用于真实人工复核的小型金融 RAG/LLM 候选集。它当前包含 45 条候选案例，全部基于仓库内的公开来源释义或合成 fixture。所有组合均为 synthetic portfolio，不代表真实用户持仓。

## 当前状态

- Dataset version：`3.0.0`
- Candidate cases：45
- Reviewer A approved：0
- Reviewer B approved：0
- Adjudicated approved：0
- 正式 human-gold 指标：不存在

版本库中的三份标签文件全部为 `review_status=pending`、`reviewer_id=null`。它们只是模板，不是人工结论。自动化程序不得将其改成 approved。

## 文件

| 文件 | 用途 |
|---|---|
| `source_manifest.json` | 来源、许可、版本、数据截止日和 immutable source checksum |
| `documents.jsonl` | 公开来源的项目释义与 synthetic policy/scenario fixture |
| `portfolios.jsonl` | 明确标记的 synthetic portfolio |
| `candidate_cases.jsonl` | 45 条待人工审核问题 |
| `labels_reviewer_a.jsonl` | Reviewer A 的独立标签模板 |
| `labels_reviewer_b.jsonl` | Reviewer B 的独立标签模板 |
| `adjudicated_labels.jsonl` | 独立裁决模板 |

## 标签字段

每条 Reviewer 与 adjudication 标签至少包含：

```text
case_id
reviewer_id
review_status
retrieval_relevance
citation_supported
numeric_consistency
answer_completeness
unsupported_claim
appropriate_refusal
severity
notes
reviewed_at
source_version
```

另有 `prediction_sha256`、`label_origin` 和 `automation_generated` 用于证明标签对应的模型输出及其人工来源。完成的人工标签必须设置 `automation_generated=false`、`label_origin=human_review`，并记录真实 reviewer identity 与带时区的 `reviewed_at`。

`retrieval_relevance` 使用 `document_id -> 0..3` 的分级相关性：

- `0`：不相关；
- `1`：弱相关；
- `2`：相关；
- `3`：高度相关。

## 不可变来源与可变标签

Manifest checksum 只覆盖 `documents.jsonl`、`portfolios.jsonl` 和 `candidate_cases.jsonl`。人工标签是有意可变的审核产物，因此不包含在 immutable checksum 中；`source_version` 和 prediction bundle SHA 用于防止跨版本、跨运行误配。

完整审核流程见 [../../../docs/evaluation-human-review.md](../../../docs/evaluation-human-review.md)。
