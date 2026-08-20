from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"


def test_readme_uses_governed_research_positioning() -> None:
    content = README.read_text(encoding="utf-8")

    assert "面向基金投研场景的可追溯投资组合研究平台" in content
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


def test_readme_architecture_covers_governed_flow() -> None:
    content = README.read_text(encoding="utf-8")

    for component in (
        "Transaction Ledger",
        "Position Rebuilder",
        "Market Data & Valuation Snapshot",
        "Risk Analytics",
        "Governed Hybrid RAG",
        "Structured LLM Output",
        "Rule Validation",
        "Human Review",
        "Published Research Report",
        "PostgreSQL + pgvector",
        "S3-compatible Object Storage",
        "Cron / Worker",
        "Evaluation & Trace",
    ):
        assert component in content


def test_new_demo_docs_have_no_broken_local_links() -> None:
    missing: list[str] = []
    documents = (
        README,
        ROOT / "docs" / "demo-script.md",
        ROOT / "docs" / "demo-recording-guide.md",
        ROOT / "docs" / "evaluation-v2.md",
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
