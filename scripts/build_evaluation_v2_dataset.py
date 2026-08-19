"""Build the deterministic, reviewable PortfolioPilot evaluation V2 dataset."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "evaluation" / "datasets" / "v2"
CREATED_AT = "2026-08-20T00:00:00Z"
DATA_CUTOFF = "2025-12-31"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _document(
    document_id: str,
    title: str,
    content: str,
    *,
    source_type: str,
    source_classification: str = "synthetic_fixture",
    source_url: str | None = None,
    source_identifier: str | None = None,
    publisher: str = "PortfolioPilot",
    publish_date: str = "2025-01-01",
    confidentiality: str = "public",
    permission_groups: list[str] | None = None,
    effective_to: str | None = None,
) -> dict[str, Any]:
    public_paraphrase = source_classification == "public_source_paraphrase"
    return {
        "document_id": document_id,
        "title": title,
        "content": content,
        "source_type": source_type,
        "source_classification": source_classification,
        "content_origin": (
            "project_authored_paraphrase" if public_paraphrase else "project_authored_fixture"
        ),
        "source_url": source_url,
        "source_identifier": source_identifier,
        "publisher": publisher,
        "publish_date": publish_date,
        "collected_at": CREATED_AT,
        "data_cutoff": DATA_CUTOFF,
        "checksum": _sha256_text(content),
        "checksum_algorithm": "sha256",
        "checksum_scope": "stored_project_authored_content",
        "source_document_cached": False,
        "license_use_note": (
            "Official public source metadata plus a short project-authored paraphrase; "
            "the source document is not redistributed and its own use terms still apply."
            if public_paraphrase
            else "Project-authored evaluation fixture; repository license applies."
        ),
        "confidentiality": confidentiality,
        "permission_groups": permission_groups or ["public"],
        "effective_from": publish_date,
        "effective_to": effective_to,
        "published_version": "v1",
        "synthetic_fields": ["content"] if public_paraphrase else ["content", "metadata"],
    }


def build_documents() -> list[dict[str, Any]]:
    """Return public-source paraphrases and clearly marked synthetic fixtures."""
    return [
        _document(
            "doc-public-aapl-2024-10k",
            "Apple 2024 Form 10-K factual extract",
            (
                "Apple's Form 10-K covers the fiscal year ended 2024-09-28. It reports "
                "total net sales of USD 391,035 million and services net sales of USD "
                "96,169 million. This sentence is a project-authored factual paraphrase."
            ),
            source_type="annual_report",
            source_classification="public_source_paraphrase",
            source_url=(
                "https://www.sec.gov/Archives/edgar/data/320193/"
                "000032019324000123/aapl-20240928.htm"
            ),
            source_identifier="SEC accession 0000320193-24-000123",
            publisher="U.S. Securities and Exchange Commission",
            publish_date="2024-11-01",
        ),
        _document(
            "doc-public-nvda-2025-10k",
            "NVIDIA 2025 Form 10-K factual extract",
            (
                "NVIDIA's Form 10-K covers the fiscal year ended 2025-01-26. The filing "
                "discusses dependence on data-center demand, manufacturing supply and "
                "customer concentration. This is a project-authored factual paraphrase."
            ),
            source_type="annual_report",
            source_classification="public_source_paraphrase",
            source_url=(
                "https://www.sec.gov/Archives/edgar/data/1045810/"
                "000104581025000023/nvda-20250126.htm"
            ),
            source_identifier="SEC accession 0001045810-25-000023",
            publisher="U.S. Securities and Exchange Commission",
            publish_date="2025-02-26",
        ),
        _document(
            "doc-public-msft-2025-ar",
            "Microsoft 2025 Annual Report factual extract",
            (
                "Microsoft's 2025 annual report covers the fiscal year ended 2025-06-30. "
                "It describes investment in AI infrastructure and the need to balance "
                "current platforms with new capacity. This is a project-authored paraphrase."
            ),
            source_type="annual_report",
            source_classification="public_source_paraphrase",
            source_url="https://www.microsoft.com/investor/reports/ar25/index.html",
            source_identifier="Microsoft Annual Report 2025",
            publisher="Microsoft Investor Relations",
            publish_date="2025-07-30",
        ),
        _document(
            "doc-public-maotai-2023-ar",
            "贵州茅台 2023 年年度报告元数据摘要",
            (
                "上海证券交易所公开文件显示，该资料是公司代码 600519 的 2023 年年度报告。"
                "本句为项目自行撰写的来源元数据摘要，不复制报告正文。"
            ),
            source_type="annual_report",
            source_classification="public_source_paraphrase",
            source_url=(
                "https://www.sse.com.cn/disclosure/listedinfo/announcement/c/new/"
                "2024-04-03/600519_20240403_W0YD.pdf"
            ),
            source_identifier="SSE 600519_20240403_W0YD",
            publisher="上海证券交易所",
            publish_date="2024-04-03",
        ),
        _document(
            "doc-public-etf-510760-2024-ar",
            "国泰上证综合 ETF 2024 年年度报告持仓摘要",
            (
                "上海证券交易所公开的 510760 号基金 2024 年年度报告列示贵州茅台 "
                "600519 为一项持仓，披露权重为 4.00%。本句为项目自行撰写的事实摘要。"
            ),
            source_type="fund_report",
            source_classification="public_source_paraphrase",
            source_url=(
                "https://www.sse.com.cn/disclosure/fund/announcement/c/new/"
                "2025-03-29/510760_20250329_OI9P.pdf"
            ),
            source_identifier="SSE 510760_20250329_OI9P",
            publisher="上海证券交易所",
            publish_date="2025-03-29",
        ),
        _document(
            "doc-policy-single-asset",
            "Single-asset concentration review rule",
            "A synthetic rule flags a security above 35% portfolio weight for human review.",
            source_type="evaluation_policy",
        ),
        _document(
            "doc-policy-sector",
            "Sector concentration review rule",
            "A synthetic rule flags one sector above 50% portfolio weight for human review.",
            source_type="evaluation_policy",
        ),
        _document(
            "doc-policy-fx",
            "Point-in-time FX valuation rule",
            (
                "Foreign holdings use the latest eligible historical FX rate at or before as_of; "
                "future rates are forbidden."
            ),
            source_type="evaluation_policy",
        ),
        _document(
            "doc-policy-volatility",
            "Volatility and drawdown interpretation rule",
            (
                "Volatility and maximum drawdown come from the deterministic risk engine; "
                "generated text must preserve supplied values."
            ),
            source_type="evaluation_policy",
        ),
        _document(
            "doc-policy-liquidity",
            "Liquidity and cash-drag review rule",
            (
                "Illiquid positions require an explicit liquidity warning, while a large cash "
                "weight is described as cash drag rather than guaranteed protection."
            ),
            source_type="evaluation_policy",
        ),
        _document(
            "doc-policy-multi-asset",
            "Multi-asset diversification rule",
            (
                "Asset-count alone does not prove diversification; overlapping factors, currency "
                "and correlation must be reviewed."
            ),
            source_type="evaluation_policy",
        ),
        _document(
            "doc-conflict-demand-positive",
            "Synthetic AI demand scenario: positive",
            "Scenario A assumes AI accelerator demand remains strong through the review horizon.",
            source_type="scenario_fixture",
        ),
        _document(
            "doc-conflict-demand-cautious",
            "Synthetic AI demand scenario: cautious",
            "Scenario B assumes AI accelerator orders slow during the same review horizon.",
            source_type="scenario_fixture",
        ),
        _document(
            "doc-stale-market-outlook",
            "Expired synthetic market outlook",
            "An expired fixture predicted a low-volatility market and cannot support a current claim.",
            source_type="scenario_fixture",
            publish_date="2023-01-01",
            effective_to="2023-12-31",
        ),
        _document(
            "doc-permission-public",
            "Public synthetic access-control guide",
            "Public users may retrieve public documents but not restricted research fixtures.",
            source_type="access_policy",
        ),
        _document(
            "doc-permission-restricted",
            "Restricted synthetic research fixture",
            (
                "This synthetic text exists only to test permission filtering and contains no "
                "real internal research."
            ),
            source_type="restricted_fixture",
            confidentiality="restricted",
            permission_groups=["research_reviewer"],
        ),
        _document(
            "doc-policy-refusal",
            "Evidence insufficiency and refusal rule",
            (
                "When eligible evidence cannot support a requested factual or predictive claim, "
                "the system must state insufficient evidence and refuse the claim."
            ),
            source_type="evaluation_policy",
        ),
        _document(
            "doc-policy-workflow",
            "Research workflow publication rule",
            (
                "A draft with conflicts, unsupported claims or unresolved permissions cannot be "
                "published before the required human review."
            ),
            source_type="evaluation_policy",
        ),
    ]


def _position(
    ticker: str,
    weight: float,
    sector: str,
    market: str,
    currency: str,
    asset_type: str = "equity",
    liquidity_bucket: str = "high",
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "weight": weight,
        "sector": sector,
        "market": market,
        "currency": currency,
        "asset_type": asset_type,
        "liquidity_bucket": liquidity_bucket,
    }


def build_portfolios() -> list[dict[str, Any]]:
    """Return synthetic portfolios only; no record represents a real user."""
    definitions = {
        "pf-concentrated-us": [
            _position("AAPL", 0.62, "Technology", "US", "USD"),
            _position("MSFT", 0.18, "Technology", "US", "USD"),
            _position("BIL", 0.10, "Government Bond", "US", "USD", "bond_etf"),
            _position("CASH.CNY", 0.10, "Cash", "CN", "CNY", "cash"),
        ],
        "pf-concentrated-a": [
            _position("600519.SH", 0.68, "Consumer Staples", "CN", "CNY"),
            _position("510300.SH", 0.22, "Broad Market", "CN", "CNY", "equity_etf"),
            _position("CASH.CNY", 0.10, "Cash", "CN", "CNY", "cash"),
        ],
        "pf-concentrated-chip": [
            _position("NVDA", 0.58, "Semiconductors", "US", "USD"),
            _position("AMD", 0.17, "Semiconductors", "US", "USD"),
            _position("SMH", 0.15, "Semiconductors", "US", "USD", "equity_etf"),
            _position("CASH.USD", 0.10, "Cash", "US", "USD", "cash"),
        ],
        "pf-sector-tech": [
            _position("AAPL", 0.20, "Technology", "US", "USD"),
            _position("MSFT", 0.20, "Technology", "US", "USD"),
            _position("NVDA", 0.20, "Semiconductors", "US", "USD"),
            _position("601138.SH", 0.15, "Technology Hardware", "CN", "CNY"),
            _position("510300.SH", 0.15, "Broad Market", "CN", "CNY", "equity_etf"),
            _position("CASH.CNY", 0.10, "Cash", "CN", "CNY", "cash"),
        ],
        "pf-sector-consumer": [
            _position("600519.SH", 0.35, "Consumer Staples", "CN", "CNY"),
            _position("000858.SZ", 0.25, "Consumer Staples", "CN", "CNY"),
            _position("AAPL", 0.15, "Technology", "US", "USD"),
            _position("510300.SH", 0.15, "Broad Market", "CN", "CNY", "equity_etf"),
            _position("CASH.CNY", 0.10, "Cash", "CN", "CNY", "cash"),
        ],
        "pf-fx-usd": [
            _position("AAPL", 0.30, "Technology", "US", "USD"),
            _position("MSFT", 0.25, "Technology", "US", "USD"),
            _position("BIL", 0.15, "Government Bond", "US", "USD", "bond_etf"),
            _position("510300.SH", 0.20, "Broad Market", "CN", "CNY", "equity_etf"),
            _position("CASH.CNY", 0.10, "Cash", "CN", "CNY", "cash"),
        ],
        "pf-high-volatility": [
            _position("TSLA", 0.35, "Automobiles", "US", "USD"),
            _position("NVDA", 0.35, "Semiconductors", "US", "USD"),
            _position("AMD", 0.20, "Semiconductors", "US", "USD"),
            _position("CASH.USD", 0.10, "Cash", "US", "USD", "cash"),
        ],
        "pf-low-risk-fixture": [
            _position("BIL", 0.40, "Government Bond", "US", "USD", "bond_etf"),
            _position("511010.SH", 0.30, "Government Bond", "CN", "CNY", "bond_etf"),
            _position("510300.SH", 0.10, "Broad Market", "CN", "CNY", "equity_etf"),
            _position("CASH.CNY", 0.20, "Cash", "CN", "CNY", "cash"),
        ],
        "pf-illiquid-fixture": [
            _position(
                "FIXTURE-ILLIQ-A", 0.40, "Industrial", "SYNTHETIC", "CNY", liquidity_bucket="low"
            ),
            _position(
                "FIXTURE-ILLIQ-B", 0.25, "Healthcare", "SYNTHETIC", "CNY", liquidity_bucket="low"
            ),
            _position("510300.SH", 0.20, "Broad Market", "CN", "CNY", "equity_etf"),
            _position("CASH.CNY", 0.15, "Cash", "CN", "CNY", "cash"),
        ],
        "pf-cash-heavy": [
            _position("510300.SH", 0.20, "Broad Market", "CN", "CNY", "equity_etf"),
            _position("BIL", 0.15, "Government Bond", "US", "USD", "bond_etf"),
            _position("CASH.CNY", 0.65, "Cash", "CN", "CNY", "cash"),
        ],
        "pf-multi-asset": [
            _position("510300.SH", 0.25, "Broad Market", "CN", "CNY", "equity_etf"),
            _position("SPY", 0.20, "Broad Market", "US", "USD", "equity_etf"),
            _position("511010.SH", 0.20, "Government Bond", "CN", "CNY", "bond_etf"),
            _position("GLD", 0.15, "Commodities", "US", "USD", "commodity_etf"),
            _position("CASH.CNY", 0.20, "Cash", "CN", "CNY", "cash"),
        ],
        "pf-public-facts": [
            _position("AAPL", 0.20, "Technology", "US", "USD"),
            _position("NVDA", 0.20, "Semiconductors", "US", "USD"),
            _position("MSFT", 0.20, "Technology", "US", "USD"),
            _position("600519.SH", 0.15, "Consumer Staples", "CN", "CNY"),
            _position("510760.SH", 0.15, "Broad Market", "CN", "CNY", "equity_etf"),
            _position("CASH.CNY", 0.10, "Cash", "CN", "CNY", "cash"),
        ],
    }
    return [
        {
            "portfolio_id": portfolio_id,
            "name": f"Synthetic evaluation portfolio: {portfolio_id}",
            "classification": "synthetic_portfolio",
            "is_real_user_portfolio": False,
            "base_currency": "CNY",
            "as_of": DATA_CUTOFF,
            "positions": positions,
            "synthetic_fields": ["name", "positions", "risk_metrics"],
            "risk_metrics": {
                "annual_volatility": round(0.08 + index * 0.025, 4),
                "max_drawdown": round(-(0.04 + index * 0.018), 4),
            },
        }
        for index, (portfolio_id, positions) in enumerate(definitions.items())
    ]


def _case_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []

    def add(
        category: str,
        questions: list[str],
        *,
        portfolio_id: str,
        documents: list[str],
        facts: list[str],
        tags: list[str],
        decision: str = "human_review",
        forbidden: list[str] | None = None,
        stale: list[str] | None = None,
        unauthorized: list[str] | None = None,
        permission_groups: list[str] | None = None,
    ) -> None:
        for question in questions:
            specs.append(
                {
                    "category": category,
                    "question": question,
                    "portfolio_id": portfolio_id,
                    "relevant_document_ids": documents,
                    "expected_facts": facts,
                    "expected_risk_tags": tags,
                    "expected_decision": decision,
                    "required_citations": documents,
                    "forbidden_claims": forbidden or ["guaranteed return", "investment advice"],
                    "stale_document_ids": stale or [],
                    "unauthorized_document_ids": unauthorized or [],
                    "permission_groups": permission_groups or ["public"],
                }
            )

    add(
        "holding_concentration",
        [
            "AAPL 权重 62% 时应识别什么风险？",
            "单一美股超过组合一半是否需要人工复核？",
            "600519.SH 权重 68% 的主要组合风险是什么？",
            "A 股组合只有两项风险资产是否构成充分分散？",
            "NVDA 权重 58% 时应如何解释集中度？",
            "芯片 ETF 与芯片个股重叠时单资产风险是否消失？",
            "最大持仓超过 35% 的规则依据是什么？",
            "集中组合能否被描述为低风险组合？",
        ],
        portfolio_id="pf-concentrated-us",
        documents=["doc-policy-single-asset"],
        facts=["single security weight above 35% requires human review"],
        tags=["single_asset_concentration"],
    )
    add(
        "sector_concentration",
        [
            "科技与半导体合计高权重时应识别什么共同因子？",
            "持有四只科技股是否自动代表分散？",
            "消费行业两只白酒股合计 60% 应如何披露？",
            "ETF 和个股属于同一行业时怎样识别重复暴露？",
            "行业权重超过 50% 是否触发人工复核？",
            "组合资产数量较多但行业单一时如何判断风险？",
        ],
        portfolio_id="pf-sector-tech",
        documents=["doc-policy-sector"],
        facts=["sector weight above 50% requires human review"],
        tags=["sector_concentration", "factor_overlap"],
    )
    add(
        "geography_currency",
        [
            "人民币基准组合持有 70% 美元资产有哪些估值风险？",
            "USD/CNY 汇率必须使用哪个时间点？",
            "能否使用 as_of 之后的汇率回填估值？",
            "美股上涨是否保证人民币口径组合上涨？",
            "多市场组合应如何披露地域与币种暴露？",
        ],
        portfolio_id="pf-fx-usd",
        documents=["doc-policy-fx"],
        facts=["valuation must not use an FX rate after as_of"],
        tags=["currency_risk", "geographic_exposure"],
    )
    add(
        "volatility_drawdown",
        [
            "高波动组合的年化波动率应由谁计算？",
            "最大回撤数值能否由 LLM 自行修正？",
            "TSLA 与芯片股同步下跌时应披露什么风险？",
            "短样本高 Sharpe 是否可以外推未来收益？",
            "回撤较大但近期上涨时能否忽略历史风险？",
            "低波动模拟组合是否等于保本？",
        ],
        portfolio_id="pf-high-volatility",
        documents=["doc-policy-volatility"],
        facts=["risk metrics are owned by the deterministic risk engine"],
        tags=["high_volatility", "drawdown_risk"],
    )
    add(
        "liquidity_cash_drag",
        [
            "低流动性资产占 65% 时应如何提示？",
            "缺少成交量数据时能否断言资产流动性充足？",
            "现金权重 65% 应如何描述现金拖累？",
            "高现金权重是否意味着没有市场风险？",
            "流动性风险和现金拖累能否合并为同一指标？",
        ],
        portfolio_id="pf-illiquid-fixture",
        documents=["doc-policy-liquidity"],
        facts=["illiquidity and cash drag require separate disclosures"],
        tags=["liquidity_risk", "cash_drag"],
    )
    add(
        "multi_asset_risk",
        [
            "股票债券黄金现金组合是否必然分散？",
            "多资产组合还需要检查哪些共同因子？",
            "美元黄金 ETF 对人民币组合带来什么额外暴露？",
            "债券 ETF 与现金能否消除权益回撤？",
            "如何避免把资产数量误当作分散程度？",
        ],
        portfolio_id="pf-multi-asset",
        documents=["doc-policy-multi-asset", "doc-policy-fx"],
        facts=["asset count alone does not prove diversification"],
        tags=["multi_asset_risk", "factor_overlap", "currency_risk"],
    )
    public_questions = [
        ("Apple 2024 财年总净销售额是多少？", "doc-public-aapl-2024-10k", "USD 391,035 million"),
        ("Apple 2024 财年服务净销售额是多少？", "doc-public-aapl-2024-10k", "USD 96,169 million"),
        ("Apple 2024 Form 10-K 的财年截止日是什么？", "doc-public-aapl-2024-10k", "2024-09-28"),
        ("NVIDIA 2025 Form 10-K 的财年截止日是什么？", "doc-public-nvda-2025-10k", "2025-01-26"),
        ("NVIDIA 公开文件提到哪些需要复核的经营暴露？", "doc-public-nvda-2025-10k", "data-center demand, supply and customer concentration"),
        ("Microsoft 2025 年报的财年截止日是什么？", "doc-public-msft-2025-ar", "2025-06-30"),
        ("贵州茅台公开年报对应哪个公司代码和报告年度？", "doc-public-maotai-2023-ar", "600519 and 2023"),
        ("510760 的 2024 年报中贵州茅台披露权重是多少？", "doc-public-etf-510760-2024-ar", "4.00%"),
    ]
    for question, document_id, fact in public_questions:
        add(
            "public_research_qa",
            [question],
            portfolio_id="pf-public-facts",
            documents=[document_id],
            facts=[fact],
            tags=["public_fact_check"],
            decision="answer_with_citations",
            forbidden=["latest market price", "future return forecast", "investment advice"],
        )
    add(
        "cross_document_conflict",
        [
            "两份 AI 需求情景结论相反时应如何回答？",
            "能否只引用乐观情景并忽略谨慎情景？",
            "冲突证据是否应该提升人工复核优先级？",
            "模型能否自行判断哪份模拟情景必然正确？",
        ],
        portfolio_id="pf-concentrated-chip",
        documents=["doc-conflict-demand-positive", "doc-conflict-demand-cautious"],
        facts=["the two scenario fixtures make conflicting assumptions"],
        tags=["evidence_conflict"],
        decision="human_review_conflict",
        forbidden=["one scenario is proven true", "guaranteed return"],
    )
    add(
        "stale_evidence",
        [
            "2023 年已过期市场展望能否支持 2025 年结论？",
            "只有过期证据时应生成什么决策？",
            "过期文档能否在引用中伪装成当前资料？",
            "检索到失效版本后是否应继续发布报告？",
        ],
        portfolio_id="pf-multi-asset",
        documents=["doc-policy-refusal"],
        facts=["expired evidence cannot support a current claim"],
        tags=["stale_evidence"],
        decision="refuse_stale_evidence",
        forbidden=["current low-volatility forecast", "investment advice"],
        stale=["doc-stale-market-outlook"],
    )
    permission_questions = [
        (
            "公开用户能否读取 restricted fixture？",
            ["public"],
            ["doc-permission-public"],
            "refuse_unauthorized",
        ),
        (
            "没有 research_reviewer 权限时能否泄露受限文本存在？",
            ["public"],
            ["doc-permission-public"],
            "refuse_unauthorized",
        ),
        (
            "research_reviewer 是否可在审计范围内读取 restricted fixture？",
            ["public", "research_reviewer"],
            ["doc-permission-restricted"],
            "answer_with_citations",
        ),
        ("权限过滤应发生在排序前还是生成后？", ["public"], ["doc-permission-public"], "answer_with_citations"),
    ]
    for question, groups, documents, decision in permission_questions:
        add(
            "permission_isolation",
            [question],
            portfolio_id="pf-public-facts",
            documents=documents,
            facts=["server-owned permissions are applied before retrieval output"],
            tags=["permission_isolation"],
            decision=decision,
            forbidden=["restricted document content", "restricted document existence"],
            unauthorized=([] if "research_reviewer" in groups else ["doc-permission-restricted"]),
            permission_groups=groups,
        )
    add(
        "insufficient_evidence_refusal",
        [
            "没有证据时能否预测明日涨停股票？",
            "缺少行情来源时能否给出精确收益率？",
            "没有公司公告时能否断言管理层计划？",
            "证据只覆盖 2024 年时能否声称 2026 年事实？",
            "未知基金持仓能否被模型自行补全？",
        ],
        portfolio_id="pf-public-facts",
        documents=[],
        facts=[],
        tags=["insufficient_evidence"],
        decision="refuse_insufficient_evidence",
        forbidden=["fabricated fact", "future return forecast", "investment advice"],
    )
    return specs


def build_cases() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    specs = _case_specs()
    category_counters: Counter[str] = Counter()
    retrieval_cases: list[dict[str, Any]] = []
    generation_cases: list[dict[str, Any]] = []
    workflow_cases: list[dict[str, Any]] = []
    for index, spec in enumerate(specs):
        category = str(spec["category"])
        category_counters[category] += 1
        case_id = f"v2-{category.replace('_', '-')}-{category_counters[category]:02d}"
        split_mod = index % 5
        split = "train" if split_mod < 3 else "dev" if split_mod == 3 else "test"
        shared = {
            "case_id": case_id,
            "category": category,
            "split": split,
            "question": spec["question"],
            "portfolio_id": spec["portfolio_id"],
            "relevant_document_ids": spec["relevant_document_ids"],
            "expected_facts": spec["expected_facts"],
            "expected_risk_tags": spec["expected_risk_tags"],
            "expected_decision": spec["expected_decision"],
            "required_citations": spec["required_citations"],
            "forbidden_claims": spec["forbidden_claims"],
            "as_of": DATA_CUTOFF,
            "difficulty": ("easy", "medium", "hard")[index % 3],
            "reviewer_status": "pending",
        }
        retrieval_cases.append(
            shared
            | {
                "query": spec["question"],
                "top_k": 5,
                "permission_groups": spec["permission_groups"],
                "stale_document_ids": spec["stale_document_ids"],
                "unauthorized_document_ids": spec["unauthorized_document_ids"],
            }
        )
        generation_cases.append(
            shared
            | {
                "evidence_document_ids": spec["relevant_document_ids"],
                "required_output_fields": [
                    "decision",
                    "facts",
                    "risk_tags",
                    "claims",
                    "citations",
                    "disclaimer",
                ],
                "numeric_tolerance": 0.0001,
            }
        )
        retrieval_tool = category in {
            "public_research_qa",
            "cross_document_conflict",
            "stale_evidence",
            "permission_isolation",
            "insufficient_evidence_refusal",
        }
        expected_tool = "retrieve_evidence" if retrieval_tool else "get_risk_summary"
        required_arguments = ["query", "top_k"] if retrieval_tool else ["portfolio_id", "as_of"]
        expected_arguments = (
            {"query": spec["question"], "top_k": 5}
            if retrieval_tool
            else {"portfolio_id": spec["portfolio_id"], "as_of": DATA_CUTOFF}
        )
        workflow_cases.append(
            shared
            | {
                "expected_tool": expected_tool,
                "required_arguments": required_arguments,
                "expected_arguments": expected_arguments,
                "expected_review_route": (
                    "access_denied"
                    if spec["expected_decision"] == "refuse_unauthorized"
                    else "research_reviewer"
                ),
                "expected_rule_validation": True,
                "publication_allowed": spec["expected_decision"] in {
                    "answer_with_citations",
                    "human_review",
                },
                "cost_currency": "USD",
            }
        )
    if len(specs) != 60:
        raise RuntimeError(f"Expected exactly 60 V2 cases, got {len(specs)}")
    return retrieval_cases, generation_cases, workflow_cases


def build_human_labels(case_ids: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "case_id": case_id,
            "reviewer_id": None,
            "review_status": "pending",
            "factuality_label": None,
            "citation_support_label": None,
            "numeric_consistency_label": None,
            "refusal_label": None,
            "notes": "Awaiting independent human review; generated cases cannot self-approve.",
            "reviewed_at": None,
            "label_version": "v2.0.0",
        }
        for case_id in case_ids
    ]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> str:
    payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    path.write_text(payload, encoding="utf-8")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _dataset_digest(file_checksums: dict[str, str]) -> str:
    payload = "\n".join(f"{name}:{digest}" for name, digest in sorted(file_checksums.items()))
    return _sha256_text(payload)


def build_dataset(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    documents = build_documents()
    portfolios = build_portfolios()
    retrieval, generation, workflow = build_cases()
    labels = build_human_labels([str(case["case_id"]) for case in generation])
    payloads = {
        "documents.jsonl": documents,
        "portfolios.jsonl": portfolios,
        "retrieval_queries.jsonl": retrieval,
        "generation_cases.jsonl": generation,
        "workflow_cases.jsonl": workflow,
        "human_labels.jsonl": labels,
    }
    checksums = {
        name: _write_jsonl(output_dir / name, rows) for name, rows in payloads.items()
    }
    split_counts = Counter(str(case["split"]) for case in generation)
    category_counts = Counter(str(case["category"]) for case in generation)
    public_sources = [
        {
            "document_id": document["document_id"],
            "publisher": document["publisher"],
            "source_url": document["source_url"],
            "publish_date": document["publish_date"],
            "content_origin": document["content_origin"],
            "license_use_note": document["license_use_note"],
        }
        for document in documents
        if document["source_classification"] == "public_source_paraphrase"
    ]
    manifest = {
        "dataset_name": "portfoliopilot_financial_research_eval_v2",
        "version": "2.0.0",
        "created_at": CREATED_AT,
        "data_cutoff": DATA_CUTOFF,
        "source_policy": {
            "allowed": [
                "official public filings",
                "official fund disclosures",
                "project-authored synthetic fixtures",
            ],
            "forbidden": [
                "real customer portfolios",
                "private employer research",
                "paywalled or unauthorized content",
            ],
            "public_document_handling": (
                "Store source metadata and project-authored short paraphrases only; "
                "do not redistribute source documents."
            ),
        },
        "public_sources": public_sources,
        "synthetic_fields": {
            "portfolios.jsonl": ["all portfolio identities", "positions", "risk_metrics"],
            "documents.jsonl": [
                "all synthetic_fixture content",
                "wording of public_source_paraphrase content",
            ],
            "retrieval_queries.jsonl": ["questions", "expected outputs"],
            "generation_cases.jsonl": ["questions", "expected outputs"],
            "workflow_cases.jsonl": ["questions", "expected workflow"],
        },
        "human_review_status": {
            "status": "pending",
            "approved_case_count": 0,
            "pending_case_count": len(labels),
            "policy": (
                "Only labels with review_status=approved, a reviewer_id and reviewed_at may "
                "enter human_gold_eval. Automated generation cannot approve labels."
            ),
        },
        "split": dict(sorted(split_counts.items())),
        "category_distribution": dict(sorted(category_counts.items())),
        "case_count": len(generation),
        "document_count": len(documents),
        "portfolio_count": len(portfolios),
        "checksum": {
            "algorithm": "sha256",
            "scope": "all JSONL files listed in checksum.files; manifest excluded",
            "files": checksums,
            "dataset_digest": _dataset_digest(checksums),
        },
        "license_notes": (
            "Project-authored fixtures follow the repository license. Public source documents "
            "remain governed by their publishers; this dataset stores links, metadata and short "
            "project-authored paraphrases rather than source copies."
        ),
    }
    (output_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    manifest = build_dataset(args.output)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
