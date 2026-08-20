# PortfolioPilot 真实演示录制指南

本文说明如何为 README 录制真实截图或 GIF。仓库当前只有旧版 UI 截图；在最新 PostgreSQL、RAG、Trace 和审核链路实际运行前，不应制作占位图或把设计稿标成产品实录。

## 录制原则

1. 只使用仓库内 synthetic/public fixture，禁止出现真实持仓、内部研报、姓名、邮箱、Token、密码或云服务凭据。
2. 画面中保留 `as_of`、行情来源、Prompt version、Trace ID 和 evaluation mode 等可追溯字段。
3. Mock、fallback、hashing embedding 和 synthetic 数据必须在画面或字幕中明确标注。
4. 不剪掉错误、数据不足或人工审核步骤，也不把旧版截图包装成当前实现。
5. 不展示自动交易、收益承诺或“零幻觉”等无法由录制过程证明的表述。

## 环境准备

- 按 [README Quick Start](../README.md#5-分钟-quick-start) 启动完整本地开发环境。
- 按 [面试演示脚本](demo-script.md) 完成一次从交易导入到报告发布的演练。
- 浏览器建议使用 1440x900 或 1512x982 视口，缩放保持 100%，关闭个人书签栏和通知。
- 录制前清理浏览器自动填充、终端历史中的 Secret，以及 Dashboard 中不属于演示的数据。
- 若调用真实 Qwen，只展示 Provider/模型名和 usage，不展示 Key 或完整敏感 Prompt 输入。

## 建议镜头

1. Swagger 导入 synthetic 交易 CSV，并展示幂等响应。
2. 持仓重建与 valuation snapshot，突出 `as_of`、`input_hash` 和 price/FX lineage。
3. 风险摘要，突出集中度、缺失数据语义和 deterministic 标记。
4. 公共文档上传、ingestion job、发布和带 chunk ID 的检索结果。
5. Workflow 节点、Prompt version 与 LLM Trace。
6. 人工 approve/request changes 后的审计记录。
7. Published report metadata 与 Eval & Trace 的模式披露。

完整视频控制在 5-8 分钟；README GIF 建议只截取 20-40 秒的关键链路，并链接到完整录屏。加速片段必须标注倍速，不能通过剪辑掩盖外部调用失败或人工步骤。

## 文件与目录

新产物统一放入：

```text
docs/assets/current-demo/
  dashboard-overview.png
  governed-research-flow.gif
  demo-provenance.json
```

`demo-provenance.json` 建议记录录制日期、完整 Git SHA、fixture 名称、evaluation mode、是否使用 mock、是否使用真实模型和是否包含人工标注。截图/GIF 加入 README 前，应逐个检查相对链接，并在全新 clone 中打开确认。

## 发布前检查

- [ ] 画面来自当前 commit 的真实运行，不是设计稿或合成 UI。
- [ ] 没有 API Key、Cookie、Authorization Header、真实邮箱或真实持仓。
- [ ] Mock、synthetic、fallback 和研究演示边界可见。
- [ ] 旧 FinanceBro 品牌与 legacy 页面没有被当作当前主线。
- [ ] GIF 尺寸、文字清晰度和文件大小适合 GitHub README。
- [ ] README、演示脚本和录屏中的 API 路径一致。
- [ ] 录制用 commit SHA 与 CI 结果可查询。
