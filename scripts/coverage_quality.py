"""Report and enforce total, core, group, and changed-core coverage."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "quality" / "coverage_policy.json"
HUNK_PATTERN = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


CORE_GROUPS: dict[str, Callable[[str], bool]] = {
    "app/db": lambda path: path.startswith("app/db/"),
    "app/services": lambda path: path.startswith("app/services/"),
    "app/providers": lambda path: path.startswith("app/providers/"),
    "rag": lambda path: path.startswith("rag/"),
    "prompts": lambda path: path.startswith("prompts/"),
    "workflows": lambda path: path.startswith("workflows/"),
    "analytics/risk_metrics.py": lambda path: path == "analytics/risk_metrics.py",
    "backtest/strategy_backtester.py": (
        lambda path: path == "backtest/strategy_backtester.py"
    ),
    "evaluation": lambda path: path.startswith("evaluation/"),
}
PRODUCTION_EXCLUDED_PREFIXES = ("tests/", "migrations/")


@dataclass(frozen=True, slots=True)
class FileCoverage:
    statements: int
    missed: int
    line_hits: dict[int, int]

    @property
    def covered(self) -> int:
        return self.statements - self.missed

    @property
    def percent(self) -> float:
        return _percent(self.covered, self.statements)


def parse_coverage_xml(path: Path) -> tuple[dict[str, FileCoverage], dict[str, float | int]]:
    root = ET.parse(path).getroot()
    line_hits_by_file: dict[str, dict[int, int]] = {}
    for class_node in root.findall(".//class"):
        filename = _normalize_path(class_node.attrib["filename"])
        hits = line_hits_by_file.setdefault(filename, {})
        for line in class_node.findall("./lines/line"):
            number = int(line.attrib["number"])
            hits[number] = max(hits.get(number, 0), int(line.attrib.get("hits", "0")))

    files = {
        filename: FileCoverage(
            statements=len(hits),
            missed=sum(1 for value in hits.values() if value <= 0),
            line_hits=hits,
        )
        for filename, hits in line_hits_by_file.items()
    }
    statements = int(root.attrib.get("lines-valid", sum(item.statements for item in files.values())))
    covered = int(root.attrib.get("lines-covered", sum(item.covered for item in files.values())))
    return files, {
        "statements": statements,
        "missed": statements - covered,
        "covered": covered,
        "coverage_percent": _percent(covered, statements),
    }


def coverage_groups(files: dict[str, FileCoverage]) -> dict[str, dict[str, float | int]]:
    groups: dict[str, dict[str, float | int]] = {}
    for name, matches in CORE_GROUPS.items():
        selected = [item for path, item in files.items() if matches(path)]
        statements = sum(item.statements for item in selected)
        missed = sum(item.missed for item in selected)
        groups[name] = {
            "statements": statements,
            "missed": missed,
            "covered": statements - missed,
            "coverage_percent": _percent(statements - missed, statements),
            "file_count": len(selected),
        }
    return groups


def aggregate_core(groups: dict[str, dict[str, float | int]]) -> dict[str, float | int]:
    statements = sum(int(item["statements"]) for item in groups.values())
    missed = sum(int(item["missed"]) for item in groups.values())
    return {
        "statements": statements,
        "missed": missed,
        "covered": statements - missed,
        "coverage_percent": _percent(statements - missed, statements),
    }


def parse_changed_lines(diff_text: str) -> dict[str, set[int]]:
    changed: dict[str, set[int]] = {}
    current_path: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_path = _normalize_path(line[6:])
            continue
        if line.startswith("+++ /dev/null"):
            current_path = None
            continue
        match = HUNK_PATTERN.match(line)
        if match and current_path:
            start = int(match.group(1))
            count = int(match.group(2) or "1")
            changed.setdefault(current_path, set()).update(range(start, start + count))
    return changed


def git_changed_lines(base: str, head: str, *, repository: Path = ROOT) -> dict[str, set[int]]:
    completed = subprocess.run(
        ["git", "diff", "--unified=0", f"{base}...{head}", "--"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return parse_changed_lines(completed.stdout)


def changed_core_coverage(
    files: dict[str, FileCoverage],
    changed_lines: dict[str, set[int]],
) -> dict[str, Any]:
    return _changed_coverage(
        files,
        changed_lines,
        eligible=lambda path: any(matches(path) for matches in CORE_GROUPS.values()),
    )


def changed_production_coverage(
    files: dict[str, FileCoverage],
    changed_lines: dict[str, set[int]],
) -> dict[str, Any]:
    return _changed_coverage(
        files,
        changed_lines,
        eligible=lambda path: path.endswith(".py")
        and not path.startswith(PRODUCTION_EXCLUDED_PREFIXES),
    )


def _changed_coverage(
    files: dict[str, FileCoverage],
    changed_lines: dict[str, set[int]],
    *,
    eligible: Callable[[str], bool],
) -> dict[str, Any]:
    eligible_paths = {path for path in files if eligible(path)}
    executable: list[tuple[str, int, int]] = []
    for path, lines in changed_lines.items():
        if path not in eligible_paths:
            continue
        executable.extend(
            (path, line, files[path].line_hits[line])
            for line in sorted(lines)
            if line in files[path].line_hits
        )
    covered = sum(1 for _, _, hits in executable if hits > 0)
    statements = len(executable)
    missed_by_file: dict[str, list[int]] = {}
    for path, line, hits in executable:
        if hits <= 0:
            missed_by_file.setdefault(path, []).append(line)
    return {
        "statements": statements,
        "missed": statements - covered,
        "covered": covered,
        "coverage_percent": _percent(covered, statements) if statements else None,
        "status": "measured" if statements else "no_executable_lines_changed",
        "missed_lines": missed_by_file,
    }


def build_report(
    coverage_xml: Path,
    policy: dict[str, Any],
    *,
    head: str,
    repository: Path = ROOT,
) -> dict[str, Any]:
    files, total = parse_coverage_xml(coverage_xml)
    groups = coverage_groups(files)
    core = aggregate_core(groups)
    baseline = str(policy["baseline"]["commit_sha"])
    changed_lines = git_changed_lines(baseline, head, repository=repository)
    changed = changed_production_coverage(files, changed_lines)
    changed_core = changed_core_coverage(files, changed_lines)
    return {
        "policy_version": policy["policy_version"],
        "baseline_commit_sha": baseline,
        "head": head,
        "total": total,
        "core": core,
        "groups": groups,
        "changed_lines": changed,
        "changed_core_lines": changed_core,
        "thresholds": policy["thresholds"],
        "high_risk_groups": policy["high_risk_groups"],
    }


def policy_violations(report: dict[str, Any]) -> list[str]:
    thresholds = report["thresholds"]
    violations: list[str] = []
    if report["total"]["coverage_percent"] < thresholds["total_coverage_percent"]:
        violations.append(
            f"total coverage {report['total']['coverage_percent']:.2f}% is below "
            f"{thresholds['total_coverage_percent']:.2f}%"
        )
    if report["core"]["coverage_percent"] < thresholds["core_coverage_percent"]:
        violations.append(
            f"core coverage {report['core']['coverage_percent']:.2f}% is below "
            f"{thresholds['core_coverage_percent']:.2f}%"
        )
    for name in report["high_risk_groups"]:
        measured = report["groups"].get(name)
        if measured is None or measured["statements"] == 0:
            violations.append(f"high-risk coverage group is missing: {name}")
        elif measured["coverage_percent"] < thresholds["high_risk_group_percent"]:
            violations.append(
                f"{name} coverage {measured['coverage_percent']:.2f}% is below "
                f"{thresholds['high_risk_group_percent']:.2f}%"
            )
    changed = report["changed_lines"]
    if (
        changed["coverage_percent"] is not None
        and changed["coverage_percent"] < thresholds["changed_lines_percent"]
    ):
        violations.append(
            f"changed production coverage {changed['coverage_percent']:.2f}% is below "
            f"{thresholds['changed_lines_percent']:.2f}%"
        )
    return violations


def markdown_report(report: dict[str, Any], violations: list[str]) -> str:
    lines = [
        "## Coverage quality",
        "",
        "| Scope | Statements | Missed | Coverage |",
        "|---|---:|---:|---:|",
        _markdown_row("total", report["total"]),
        _markdown_row("core", report["core"]),
    ]
    lines.extend(_markdown_row(name, values) for name, values in report["groups"].items())
    changed = report["changed_lines"]
    changed_core = report["changed_core_lines"]
    changed_percent = (
        f"{changed['coverage_percent']:.2f}%"
        if changed["coverage_percent"] is not None
        else "n/a"
    )
    changed_core_percent = (
        f"{changed_core['coverage_percent']:.2f}%"
        if changed_core["coverage_percent"] is not None
        else "n/a"
    )
    lines.extend(
        [
            "",
            f"Changed production lines since `{report['baseline_commit_sha']}`: "
            f"{changed['statements']} executable, {changed['missed']} missed, {changed_percent}.",
            f"Changed core lines: {changed_core['statements']} executable, "
            f"{changed_core['missed']} missed, {changed_core_percent}.",
            "",
            "Gate: " + ("PASS" if not violations else "FAIL"),
        ]
    )
    if violations:
        lines.extend(f"- {item}" for item in violations)
    return "\n".join(lines) + "\n"


def _markdown_row(name: str, values: dict[str, Any]) -> str:
    return (
        f"| {name} | {values['statements']} | {values['missed']} | "
        f"{values['coverage_percent']:.2f}% |"
    )


def _normalize_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    return normalized[2:] if normalized.startswith("./") else normalized


def _percent(covered: int, statements: int) -> float:
    return round(covered / statements * 100, 4) if statements else 100.0


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-xml", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--github-summary", type=Path)
    parser.add_argument("--enforce", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    report = build_report(args.coverage_xml, policy, head=args.head)
    violations = policy_violations(report)
    markdown = markdown_report(report, violations)
    print(markdown, end="")
    if args.json_output:
        args.json_output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if args.markdown_output:
        args.markdown_output.write_text(markdown, encoding="utf-8")
    if args.github_summary:
        with args.github_summary.open("a", encoding="utf-8") as handle:
            handle.write(markdown)
    if args.enforce and violations:
        for item in violations:
            print(f"coverage gate: {item}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
