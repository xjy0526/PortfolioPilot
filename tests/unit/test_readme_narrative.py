from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"


def test_readme_uses_governed_research_positioning() -> None:
    content = README.read_text(encoding="utf-8")

    assert "面向基金投研场景的可追溯 AI 工作流平台" in content
    assert "PostgreSQL 交易账本和时点估值" in content
    for capability in (
        "Portfolio Ledger & Valuation",
        "Risk Analytics & Backtest",
        "Governed Hybrid RAG",
        "Prompt Registry & LLM Trace",
        "Human-in-the-loop Research Workflow",
    ):
        assert capability in content

    legacy_heading = content.index("## Experimental / Legacy Extensions")
    assert legacy_heading > content.index("## 文档导航")
    assert "docs/screenshots" not in content
    assert "Personal AI investing dashboard" not in content


def test_readme_first_screen_is_recruiter_focused() -> None:
    content = README.read_text(encoding="utf-8")
    first_screen = "\n".join(content.splitlines()[:80])

    assert content.splitlines()[0] == "# PortfolioPilot"
    assert "branch=main&event=push" in first_screen
    assert first_screen.index("Main CI") < first_screen.index("Pull request CI")
    for section in (
        "## 三个核心差异点",
        "## 简化架构",
        "## 最快体验入口",
        "## 当前真实 CI 状态",
    ):
        assert section in first_screen
    for differentiator in (
        "Point-in-time ledger and valuation",
        "Governed hybrid RAG and citation lineage",
        "Deterministic validation plus human review",
    ):
        assert differentiator in first_screen


def test_readme_is_compact_and_discloses_demo_boundaries() -> None:
    content = README.read_text(encoding="utf-8")

    assert len(content.splitlines()) <= 350
    assert "没有与最新 PostgreSQL、RAG 治理和人工审核主线完全一致的截图" in content
    assert "没有与当前主线一致的公开在线 Demo" in content
    assert "synthetic_smoke" in content
    assert "不代表真实模型质量" in content
    assert "不执行真实交易" in content


def test_readme_architecture_covers_governed_flow() -> None:
    content = README.read_text(encoding="utf-8")

    for component in (
        "Transaction Ledger",
        "Point-in-time Valuation",
        "Risk Analytics",
        "Governed Hybrid RAG",
        "Structured LLM Draft",
        "Rule Validation",
        "Human Review",
        "Published Research Report",
        "PostgreSQL + pgvector",
        "S3-compatible Storage",
        "Cron / Worker",
        "Prompt Registry / LLM Trace / Evaluation",
    ):
        assert component in content


def test_new_demo_docs_have_no_broken_local_links() -> None:
    missing: list[str] = []
    documents = (
        README,
        ROOT / "docs" / "demo-script.md",
        ROOT / "docs" / "demo-recording-guide.md",
        ROOT / "docs" / "evaluation-v2.md",
        ROOT / "docs" / "design-system.md",
        ROOT / "docs" / "providers.md",
        ROOT / "docs" / "migration-guide.md",
        ROOT / "docs" / "integrations" / "parqet.md",
        ROOT / "docs" / "releases" / "v2.0.0-draft.md",
        ROOT / "docs" / "archive" / "pre-merge" / "v0.9.0-rc.md",
        ROOT / "docs" / "archive" / "pre-merge" / "release_readiness_2026.md",
        ROOT / "docs" / "assets" / "README.md",
        ROOT / "docs" / "assets" / "legacy-ui" / "README.md",
        ROOT / "evaluation" / "datasets" / "v2" / "README.md",
    )

    for document in documents:
        content = document.read_text(encoding="utf-8")
        markdown_links = re.findall(r"(?<!!)\[[^]]+\]\(([^)]+)\)", content)
        for raw_target in markdown_links:
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or target.startswith("#"):
                continue
            relative_path = unquote(parsed.path)
            resolved = (document.parent / relative_path).resolve()
            if relative_path and not resolved.exists():
                missing.append(f"{document.relative_to(ROOT)} -> {relative_path}")

    assert missing == []


def test_historical_docs_and_third_party_copy_are_clearly_scoped() -> None:
    banner = (
        "Historical pre-merge snapshot. Superseded by the current main branch "
        "and the v2.0.0 release documentation. This file is preserved only for audit history."
    )
    archived = (
        ROOT / "docs" / "archive" / "pre-merge" / "v0.9.0-rc.md",
        ROOT / "docs" / "archive" / "pre-merge" / "release_readiness_2026.md",
    )

    for document in archived:
        assert banner in document.read_text(encoding="utf-8").splitlines()[0]

    assert not (ROOT / "docs" / "releases" / "v0.9.0-rc.md").exists()
    assert not (ROOT / "docs" / "audits" / "release_readiness_2026.md").exists()
    assert not (ROOT / "docs" / "Parqet API" / "Developer Hub _ Parqet.txt").exists()
    assert (ROOT / "docs" / "integrations" / "parqet.md").exists()


def test_design_system_is_product_documentation() -> None:
    content = (ROOT / "docs" / "design-system.md").read_text(encoding="utf-8")

    assert "Google Stitch" not in content
    for section in (
        "视觉设计目标",
        "信息层级",
        "颜色与状态",
        "可访问性原则",
        "组件规范",
        "数据展示规范",
    ):
        assert section in content
