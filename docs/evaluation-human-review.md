# 人工金融 RAG/LLM 评测流程

本文说明 PortfolioPilot V3 候选集如何由真实 Reviewer 完成独立标注、冲突裁决和正式 human-gold 评测。

## 1. 真实性边界

V3 只使用：

- 仓库内已有的公开来源元数据与项目自行撰写的短摘要；
- 项目自行构造的规则、权限、时效和证据冲突 fixture；
- 明确标记为 synthetic 的组合与风险指标。

V3 不包含真实客户持仓、个人持仓、实习单位材料、内部研报、付费内容或 API Key。公开来源文档本身没有被重新分发。

当前 committed dataset 全部为 pending，approved 和 adjudicated approved 均为 0。因此仓库当前没有可对外宣传的 human-gold 准确率。

## 2. 准备候选集

验证 committed 候选集：

```bash
python -m evaluation.prepare_human_review
```

预期输出必须包含：

```text
candidate_case_count=45
human_gold_ready=false
automated_approval_performed=false
```

如需机械重建固定 fixture，可运行：

```bash
python -m scripts.build_human_gold_v3_dataset
```

重建会覆盖 v3 committed template，始终写回 pending，不会产生人工结论。不要在 Reviewer 已经开始工作的目录运行该命令。

## 3. 捕获待审核的真实模型运行

预测 bundle 必须记录：

- `evaluation_mode`；
- provider、model；
- prompt key/version；
- temperature；
- 完整 Git commit SHA；
- UTC 生成时间；
- dataset name/version；
- `mock_response_used=false`；
- `real_model_used=true`；
- `response_source=live_model`；
- raw output 保存策略。

原始模型输出只有在完成脱敏且获得授权后才可保存。否则使用：

```json
{"raw_output_policy":"not_stored"}
```

允许的另一状态为 `redacted_and_authorized`。不得把 mock/fallback bundle 改名为 live。

Retriever bundle 至少包含：

```text
bm25_fts_only
vector_only
hybrid_rrf
hybrid_rrf_reranker
```

前三种必须有完整 case results。reranker 未配置时必须写 `available=false` 和明确的 `unavailable_reason`，不能补造分数。

## 4. 分配两位 Reviewer

Reviewer identity 必须来自真实人员分配，可使用稳定的内部匿名 ID；不得由自动化程序虚构第二位审核者。

```bash
python -m evaluation.prepare_human_review \
  --reviewer-a <real-reviewer-a-id> \
  --reviewer-b <real-reviewer-b-id> \
  --predictions <captured-predictions.json> \
  --output-dir <review-workspace>
```

两个 ID 必须不同。生成的 packet 仍全部为 pending，且 `automation_generated=true`。每位 Reviewer 必须独立阅读问题、候选证据和模型输出，并在完成时亲自填写：

```text
review_status=approved|rejected
reviewed_at=<timezone-aware timestamp>
label_origin=human_review
automation_generated=false
```

approved 标签必须填写所有评分字段；rejected 标签必须说明原因。

## 5. 校验双人标签

```bash
python -m evaluation.validate_human_labels \
  --dataset-dir evaluation/datasets/v3 \
  --reviewer-a <review-workspace>/labels_reviewer_a.jsonl \
  --reviewer-b <review-workspace>/labels_reviewer_b.jsonl \
  --require-complete
```

以下情况会失败：

- 只有一位 Reviewer 完成；
- A/B 使用相同 identity；
- case 缺失或重复；
- source version 不一致；
- 两位 Reviewer 审核的 prediction SHA 不一致；
- completed 标签仍标记为 automation generated；
- 时间、字段值或相关性分级非法。

## 6. 生成裁决队列

只有两位 Reviewer 都完成后才能生成：

```bash
python -m evaluation.adjudicate_human_labels \
  --dataset-dir evaluation/datasets/v3 \
  --reviewer-a <review-workspace>/labels_reviewer_a.jsonl \
  --reviewer-b <review-workspace>/labels_reviewer_b.jsonl \
  --output <review-workspace>/adjudicated_labels.jsonl
```

该命令只比较两份标签并生成 pending queue：

- 一致项会预填双方一致的值；
- 冲突项会记录 `conflict_fields` 和两侧输入；
- 所有项仍保持 pending；
- `automated_approval_performed=false`。

两位 Reviewer 完全一致的项由其双人共识进入评测，不要求系统伪造第三位审核者；对应裁决模板可以继续保持 pending。只有 `conflict_fields` 非空的项必须由与 Reviewer A/B 不同的真实 adjudicator 完成。任一冲突未裁决时，正式 human-gold 评测整体拒绝运行。

## 7. 运行 human-gold 评测

先执行严格校验：

```bash
python -m evaluation.validate_human_labels \
  --dataset-dir evaluation/datasets/v3 \
  --reviewer-a <review-workspace>/labels_reviewer_a.jsonl \
  --reviewer-b <review-workspace>/labels_reviewer_b.jsonl \
  --adjudicated <review-workspace>/adjudicated_labels.jsonl \
  --require-human-gold
```

再运行：

```bash
python -m evaluation.run_human_gold_eval \
  --mode human_gold_eval \
  --dataset-dir evaluation/datasets/v3 \
  --predictions <captured-predictions.json> \
  --reviewer-a <review-workspace>/labels_reviewer_a.jsonl \
  --reviewer-b <review-workspace>/labels_reviewer_b.jsonl \
  --adjudicated <review-workspace>/adjudicated_labels.jsonl \
  --output cache/evaluation/v3/human_gold_eval/evaluation_report.json
```

正式运行要求：

- 两位 Reviewer 身份不同且全部完成；
- 所有 A/B 冲突项完成独立裁决；
- 至少一条 adjudicated label 为 approved；
- 标签的 `prediction_sha256` 与当前 prediction bundle 完全一致；
- bundle 是 real model 输出且没有 mock fallback；
- synthetic、production 和 human-gold 报告目录互不覆盖。

## 8. Live model 模式

```bash
python -m evaluation.run_human_gold_eval \
  --mode live_model_eval \
  --predictions <captured-live-predictions.json>
```

Live 模式必须存在对应 Provider credential，例如 `QWEN_API_KEY` 或 `OPENAI_COMPATIBLE_API_KEY`。缺少凭据时直接失败，不会静默改用 mock。

Live report 只证明真实模型运行 provenance，human labels 不存在时 `human_gold_metrics=null`，状态为 `awaiting_human_adjudication`。

## 9. 指标解释

Retriever 对照输出：

- `Recall@5`：分级相关性大于 0 的文档被前五名召回的比例；
- `MRR`：首个相关文档倒数排名的平均值；
- `nDCG@5`：使用人工 0–3 相关性等级计算的排序质量；
- `P50/P95 latency`：该 Retriever 在纳入案例上的延迟分位数。

Generation 输出：

- `Citation Precision`：引用文档中人工判定相关的比例；
- `Citation Support Rate`：人工判断引用能够支持 Claim 的比例；
- `Evidence Insufficient Accuracy`：仅在 `evidence_insufficient` 类案例上比较模型拒答行为与人工期望；
- `Refusal Precision`：实际拒答中人工认为应拒答的比例；
- 数字一致性、答案完整性和 unsupported claim 作为独立指标展示。

不要把这些维度压缩成一个没有样本量和模式披露的“准确率”。

## 10. 不得对外宣传的情况

以下任一成立时，不得宣传 human-gold 模型效果：

- committed dataset 仍全部 pending；
- approved adjudicated label 数量为 0；
- 只有一位 Reviewer；
- Reviewer identity 相同或无法证明；
- 冲突尚未裁决；
- prediction SHA 或 dataset version 不一致；
- 报告来自 synthetic smoke；
- live 模型发生 mock/fallback；
- 原始输出、公开来源或 Reviewer 数据未经授权与脱敏。

当前仓库满足机制准备，不代表已完成人工评测。
