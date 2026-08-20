"""Reject unqualified quality claims in public project documentation."""
from __future__ import annotations

import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPOSITORY_ROOT / "README.md"

_ABSOLUTE_CLAIMS = (
    re.compile(r"100\s*%\s*(?:准确|accuracy)", re.IGNORECASE),
    re.compile(r"(?:零幻觉|zero[- ]hallucination)", re.IGNORECASE),
    re.compile(r"(?:绝对准确|guaranteed accuracy)", re.IGNORECASE),
)
_PRODUCTION_CLAIM = re.compile(
    r"(?:生产级(?:平台|系统|产品|能力)|production[- ]grade(?: platform| system| product)?)",
    re.IGNORECASE,
)
_BOUNDARY_TERMS = (
    "不是",
    "尚非",
    "尚不是",
    "不属于",
    "不得",
    "未达到",
    "限制",
    "研究演示",
    "not ",
    "non-production",
    "must not",
    "limitation",
)


def find_unqualified_claims(text: str) -> list[str]:
    """Return public-facing lines that make absolute or unbounded claims."""
    issues: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        is_bounded = any(term in stripped.lower() for term in _BOUNDARY_TERMS)
        if any(pattern.search(stripped) for pattern in _ABSOLUTE_CLAIMS) and not is_bounded:
            issues.append(f"line {line_number}: {stripped}")
            continue
        if _PRODUCTION_CLAIM.search(stripped) and not is_bounded:
            issues.append(f"line {line_number}: {stripped}")
    return issues


def main() -> None:
    issues = find_unqualified_claims(README_PATH.read_text(encoding="utf-8"))
    if issues:
        details = "\n".join(issues)
        raise SystemExit(f"Unqualified README quality claims detected:\n{details}")
    print("README evaluation claims are explicitly bounded.")


if __name__ == "__main__":
    main()
