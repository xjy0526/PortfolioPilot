"""Summarize JUnit results with explicit skipped and slow-test counts."""
from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TestSummary:
    total: int
    passed: int
    failures: int
    errors: int
    skipped: int
    duration_seconds: float
    slowest: tuple[tuple[str, float], ...]


def parse_junit(path: Path, *, slowest_count: int = 10) -> TestSummary:
    root = ET.parse(path).getroot()
    cases = root.findall(".//testcase")
    failures = sum(case.find("failure") is not None for case in cases)
    errors = sum(case.find("error") is not None for case in cases)
    skipped = sum(case.find("skipped") is not None for case in cases)
    durations = [
        (
            f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}".strip(":"),
            float(case.attrib.get("time", "0") or 0),
        )
        for case in cases
    ]
    durations.sort(key=lambda item: item[1], reverse=True)
    total = len(cases)
    return TestSummary(
        total=total,
        passed=total - failures - errors - skipped,
        failures=failures,
        errors=errors,
        skipped=skipped,
        duration_seconds=round(sum(item[1] for item in durations), 3),
        slowest=tuple(durations[: max(0, slowest_count)]),
    )


def markdown_summary(label: str, summary: TestSummary) -> str:
    lines = [
        f"## Test results: {label}",
        "",
        (
            f"total={summary.total}, passed={summary.passed}, failures={summary.failures}, "
            f"errors={summary.errors}, skipped={summary.skipped}, "
            f"testcase_time={summary.duration_seconds:.3f}s"
        ),
        "",
        "| Slowest test | Seconds |",
        "|---|---:|",
    ]
    lines.extend(f"| `{name}` | {duration:.3f} |" for name, duration in summary.slowest)
    return "\n".join(lines) + "\n"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("junit_xml", type=Path)
    parser.add_argument("--label", default="pytest")
    parser.add_argument("--slowest", type=int, default=10)
    parser.add_argument("--github-summary", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    summary = parse_junit(args.junit_xml, slowest_count=args.slowest)
    markdown = markdown_summary(args.label, summary)
    print(markdown, end="")
    if args.github_summary:
        with args.github_summary.open("a", encoding="utf-8") as handle:
            handle.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
