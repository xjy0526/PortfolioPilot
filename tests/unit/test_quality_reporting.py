"""Tests for CI coverage gates and explicit pytest result reporting."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import coverage_quality, summarize_test_results
from scripts.coverage_quality import (
    CORE_GROUPS,
    ZERO_SHA,
    CoverageBaseError,
    FileCoverage,
    aggregate_core,
    build_report,
    changed_core_coverage,
    changed_production_coverage,
    coverage_groups,
    git_changed_lines,
    markdown_report,
    parse_changed_lines,
    parse_coverage_xml,
    policy_violations,
    resolve_changed_lines_base,
    resolve_ci_base,
)
from scripts.summarize_test_results import markdown_summary, parse_junit


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_git_repository(repository: Path) -> str:
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Coverage Test")
    _git(repository, "config", "user.email", "coverage@example.invalid")
    _write(repository / "sample.py", "value = 1\n")
    _git(repository, "add", "sample.py")
    _git(repository, "commit", "-m", "base")
    return _git(repository, "rev-parse", "HEAD")


def _commit(repository: Path, content: str, message: str) -> str:
    _write(repository / "sample.py", content)
    _git(repository, "add", "sample.py")
    _git(repository, "commit", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


def test_coverage_xml_builds_total_and_core_matrix(tmp_path: Path) -> None:
    coverage_xml = _write(
        tmp_path / "coverage.xml",
        """<?xml version="1.0" ?>
<coverage lines-valid="6" lines-covered="4">
  <packages>
    <package name="app.providers">
      <classes>
        <class filename="app/providers/tushare.py">
          <lines>
            <line number="10" hits="1"/>
            <line number="11" hits="0"/>
            <line number="12" hits="2"/>
          </lines>
        </class>
      </classes>
    </package>
    <package name="workflows">
      <classes>
        <class filename="workflows/engine.py">
          <lines>
            <line number="20" hits="1"/>
            <line number="21" hits="0"/>
          </lines>
        </class>
      </classes>
    </package>
    <package name="legacy">
      <classes>
        <class filename="legacy.py">
          <lines><line number="1" hits="1"/></lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
""",
    )

    files, total = parse_coverage_xml(coverage_xml)
    groups = coverage_groups(files)
    core = aggregate_core(groups)

    assert total == {
        "statements": 6,
        "missed": 2,
        "covered": 4,
        "coverage_percent": pytest.approx(66.6667),
    }
    assert groups["app/providers"]["statements"] == 3
    assert groups["app/providers"]["missed"] == 1
    assert groups["workflows"]["coverage_percent"] == 50.0
    assert core["statements"] == 5
    assert core["coverage_percent"] == 60.0


def test_changed_core_coverage_counts_only_executable_core_lines() -> None:
    diff = """diff --git a/app/providers/source.py b/app/providers/source.py
--- a/app/providers/source.py
+++ b/app/providers/source.py
@@ -8,0 +9,3 @@
+covered
+missed
+comment
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
+documentation
"""
    files = {
        "app/providers/source.py": FileCoverage(
            statements=2,
            missed=1,
            line_hits={9: 1, 10: 0},
        ),
        "README.md": FileCoverage(statements=1, missed=0, line_hits={1: 1}),
    }

    changed = parse_changed_lines(diff)
    result = changed_core_coverage(files, changed)
    production_result = changed_production_coverage(files, changed)

    assert changed["app/providers/source.py"] == {9, 10, 11}
    assert result["statements"] == 2
    assert result["covered"] == 1
    assert result["missed"] == 1
    assert result["coverage_percent"] == 50.0
    assert result["missed_lines"] == {"app/providers/source.py": [10]}
    assert production_result == result


def test_build_report_prefers_explicit_base_for_changed_lines(
    tmp_path: Path,
    monkeypatch,
) -> None:
    coverage_xml = _write(
        tmp_path / "coverage.xml",
        """<coverage lines-valid="2" lines-covered="1">
  <packages><package><classes>
    <class filename="app/providers/source.py"><lines>
      <line number="9" hits="1"/><line number="10" hits="0"/>
    </lines></class>
  </classes></package></packages>
</coverage>""",
    )
    policy = {
        "policy_version": 1,
        "baseline": {"commit_sha": "a" * 40},
        "thresholds": {},
        "high_risk_groups": [],
    }

    def fake_resolve(base: str, head: str, *, repository: Path):
        assert base == "b" * 40
        assert head == "phase8"
        assert repository == tmp_path
        return "c" * 40, "d" * 40

    def fake_changed_lines(base: str, head: str, *, repository: Path):
        assert base == "c" * 40
        assert head == "d" * 40
        assert repository == tmp_path
        return {"app/providers/source.py": {9, 10}}

    monkeypatch.setattr(coverage_quality, "resolve_changed_lines_base", fake_resolve)
    monkeypatch.setattr(coverage_quality, "_git_changed_lines", fake_changed_lines)
    report = build_report(
        coverage_xml,
        policy,
        base="b" * 40,
        head="phase8",
        repository=tmp_path,
    )

    assert report["changed_lines"]["coverage_percent"] == 50.0
    assert report["changed_core_lines"]["coverage_percent"] == 50.0
    assert report["baseline_commit_sha"] == "a" * 40
    assert report["resolved_base_sha"] == "c" * 40
    assert report["resolved_head_sha"] == "d" * 40


def test_changed_lines_accepts_a_real_ancestor_base(tmp_path: Path) -> None:
    repository = tmp_path / "ancestor"
    base = _init_git_repository(repository)
    head = _commit(repository, "value = 1\nadded = 2\n", "head")

    assert resolve_changed_lines_base(base, head, repository=repository) == (base, head)
    assert git_changed_lines(base, head, repository=repository) == {"sample.py": {2}}


def test_changed_lines_rejects_a_squash_diverged_base(tmp_path: Path) -> None:
    repository = tmp_path / "diverged"
    common = _init_git_repository(repository)
    head = _commit(repository, "value = 2\n", "main head")
    _git(repository, "switch", "-c", "feature", common)
    diverged_base = _commit(repository, "value = 3\n", "feature head")

    with pytest.raises(CoverageBaseError, match="is not an ancestor"):
        resolve_changed_lines_base(diverged_base, head, repository=repository)

    with pytest.raises(CoverageBaseError, match="divergent merge base"):
        git_changed_lines(diverged_base, head, repository=repository)


def test_changed_lines_rejects_an_unknown_ref(tmp_path: Path) -> None:
    repository = tmp_path / "unknown-ref"
    _init_git_repository(repository)

    with pytest.raises(CoverageBaseError, match="cannot resolve coverage ref"):
        resolve_changed_lines_base("missing", "HEAD", repository=repository)


def test_changed_lines_surfaces_ancestor_validation_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        coverage_quality,
        "resolve_commit",
        lambda ref, *, repository: "a" * 40 if ref == "base" else "b" * 40,
    )
    monkeypatch.setattr(
        coverage_quality.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Result",
            (),
            {"returncode": 128, "stderr": "repository unavailable"},
        )(),
    )

    with pytest.raises(CoverageBaseError, match="repository unavailable"):
        resolve_changed_lines_base("base", "head", repository=tmp_path)


@pytest.mark.parametrize(
    ("event_name", "pull_request_base", "push_before", "expected_candidate"),
    [
        ("pull_request", "pr-base", None, "pr-base"),
        ("push", None, "push-before", "push-before"),
        ("workflow_dispatch", None, None, "head^"),
    ],
)
def test_ci_base_resolution_uses_event_specific_candidate(
    tmp_path: Path,
    monkeypatch,
    event_name: str,
    pull_request_base: str | None,
    push_before: str | None,
    expected_candidate: str,
) -> None:
    observed: list[tuple[str, str]] = []

    def fake_resolve(base: str, head: str, *, repository: Path):
        observed.append((base, head))
        assert repository == tmp_path
        return "a" * 40, "b" * 40

    monkeypatch.setattr(coverage_quality, "resolve_changed_lines_base", fake_resolve)

    assert resolve_ci_base(
        event_name=event_name,
        head="head",
        policy_baseline="policy-base",
        pull_request_base=pull_request_base,
        push_before=push_before,
        repository=tmp_path,
    ) == "a" * 40
    assert observed == [(expected_candidate, "head")]


def test_zero_push_before_sha_falls_back_without_using_zero(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observed: list[str] = []

    def fake_resolve(base: str, head: str, *, repository: Path):
        observed.append(base)
        return "a" * 40, "b" * 40

    monkeypatch.setattr(coverage_quality, "resolve_changed_lines_base", fake_resolve)

    assert resolve_ci_base(
        event_name="push",
        head="head",
        policy_baseline="policy-base",
        push_before=ZERO_SHA,
        repository=tmp_path,
    ) == "a" * 40
    assert observed == ["head^"]


def test_manual_base_resolution_falls_back_to_policy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observed: list[str] = []

    def fake_resolve(base: str, head: str, *, repository: Path):
        observed.append(base)
        if base == "head^":
            raise CoverageBaseError("head has no parent")
        return "a" * 40, "b" * 40

    monkeypatch.setattr(coverage_quality, "resolve_changed_lines_base", fake_resolve)

    assert resolve_ci_base(
        event_name="workflow_dispatch",
        head="head",
        policy_baseline="policy-base",
        repository=tmp_path,
    ) == "a" * 40
    assert observed == ["head^", "policy-base"]


def test_ci_base_resolution_rejects_missing_or_unsupported_events() -> None:
    with pytest.raises(CoverageBaseError, match="requires a base SHA"):
        resolve_ci_base(
            event_name="pull_request",
            head="head",
            policy_baseline="policy-base",
        )

    with pytest.raises(CoverageBaseError, match="unsupported coverage event"):
        resolve_ci_base(
            event_name="schedule",
            head="head",
            policy_baseline="policy-base",
        )


def test_coverage_policy_reports_each_failed_gate() -> None:
    report = {
        "total": {"coverage_percent": 62.0},
        "core": {"coverage_percent": 79.0},
        "groups": {
            "app/providers": {"statements": 10, "coverage_percent": 70.0},
        },
        "changed_lines": {"coverage_percent": 80.0},
        "changed_core_lines": {"coverage_percent": 80.0},
        "thresholds": {
            "total_coverage_percent": 63.0,
            "core_coverage_percent": 81.0,
            "high_risk_group_percent": 80.0,
            "changed_lines_percent": 85.0,
        },
        "high_risk_groups": ["app/providers", "workflows"],
    }

    violations = policy_violations(report)

    assert len(violations) == 5
    assert any("total coverage" in item for item in violations)
    assert any("core coverage" in item for item in violations)
    assert any("app/providers coverage" in item for item in violations)
    assert any("group is missing: workflows" in item for item in violations)
    assert any("changed production coverage" in item for item in violations)


def test_junit_summary_reports_skips_failures_and_slowest(tmp_path: Path) -> None:
    junit_xml = _write(
        tmp_path / "junit.xml",
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="suite">
    <testcase classname="tests.test_fast" name="test_pass" time="0.10"/>
    <testcase classname="tests.test_slow" name="test_skip" time="1.25">
      <skipped message="requires Docker"/>
    </testcase>
    <testcase classname="tests.test_bad" name="test_failure" time="0.50">
      <failure message="assertion failed"/>
    </testcase>
    <testcase classname="tests.test_error" name="test_error" time="0.25">
      <error message="setup failed"/>
    </testcase>
  </testsuite>
</testsuites>
""",
    )

    summary = parse_junit(junit_xml, slowest_count=2)
    markdown = markdown_summary("unit", summary)

    assert summary.total == 4
    assert summary.passed == 1
    assert summary.failures == 1
    assert summary.errors == 1
    assert summary.skipped == 1
    assert summary.duration_seconds == 2.1
    assert summary.slowest[0] == ("tests.test_slow::test_skip", 1.25)
    assert "passed=1" in markdown
    assert "skipped=1" in markdown


def test_coverage_policy_file_has_a_reproducible_baseline() -> None:
    policy_path = Path(__file__).resolve().parents[2] / "quality" / "coverage_policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))

    assert len(policy["baseline"]["commit_sha"]) == 40
    assert policy["baseline"]["ci_run_url"].startswith("https://github.com/")
    assert policy["thresholds"]["total_coverage_percent"] >= 63.0
    assert policy["thresholds"]["core_coverage_percent"] >= 81.0
    assert policy["baseline"]["commit_sha"] == (
        "2464f6bd08a6e91c9c301b1fd300a2f91aca27f0"
    )
    assert policy["baseline"]["total_coverage_percent"] == 66.7585
    assert policy["baseline"]["core_coverage_percent"] == 86.0451
    assert policy["thresholds"]["changed_lines_percent"] == 85.0


def _passing_report() -> dict:
    perfect = {
        "statements": 10,
        "missed": 0,
        "covered": 10,
        "coverage_percent": 100.0,
    }
    return {
        "policy_version": 1,
        "baseline_commit_sha": "b" * 40,
        "head": "HEAD",
        "total": dict(perfect),
        "core": dict(perfect),
        "groups": {name: dict(perfect) for name in CORE_GROUPS},
        "changed_lines": {**perfect, "status": "measured", "missed_lines": {}},
        "changed_core_lines": {
            **perfect,
            "status": "measured",
            "missed_lines": {},
        },
        "thresholds": {
            "total_coverage_percent": 63.0,
            "core_coverage_percent": 81.0,
            "high_risk_group_percent": 80.0,
            "changed_lines_percent": 85.0,
        },
        "high_risk_groups": list(CORE_GROUPS),
    }


def test_coverage_cli_writes_machine_and_human_reports(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    policy_path = _write(tmp_path / "policy.json", json.dumps({"baseline": {}}))
    coverage_xml = _write(tmp_path / "coverage.xml", "<coverage/>")
    json_output = tmp_path / "quality.json"
    markdown_output = tmp_path / "quality.md"
    github_summary = tmp_path / "github.md"
    report = _passing_report()

    observed: dict[str, object] = {}

    def fake_build_report(*args, **kwargs):
        observed.update(kwargs)
        return report

    monkeypatch.setattr(coverage_quality, "build_report", fake_build_report)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "coverage_quality.py",
            "--coverage-xml",
            str(coverage_xml),
            "--policy",
            str(policy_path),
            "--base",
            "c" * 40,
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
            "--github-summary",
            str(github_summary),
            "--enforce",
        ],
    )

    assert coverage_quality.main() == 0
    assert observed["base"] == "c" * 40
    assert json.loads(json_output.read_text(encoding="utf-8"))["head"] == "HEAD"
    assert "Gate: PASS" in markdown_output.read_text(encoding="utf-8")
    assert "Changed production lines" in github_summary.read_text(encoding="utf-8")
    assert "Coverage quality" in capsys.readouterr().out

    report["total"]["coverage_percent"] = 1.0
    assert coverage_quality.main() == 1
    assert "coverage gate: total coverage" in capsys.readouterr().err


def test_coverage_cli_resolves_ci_base_or_fails_clearly(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    policy_path = _write(
        tmp_path / "policy.json",
        json.dumps({"baseline": {"commit_sha": "policy-base"}}),
    )
    monkeypatch.setattr(sys, "argv", [
        "coverage_quality.py",
        "--policy",
        str(policy_path),
        "--resolve-ci-base",
        "--event-name",
        "workflow_dispatch",
        "--head",
        "head",
    ])
    monkeypatch.setattr(coverage_quality, "resolve_ci_base", lambda **kwargs: "a" * 40)

    assert coverage_quality.main() == 0
    assert capsys.readouterr().out.strip() == "a" * 40

    def fail_base(**kwargs):
        raise CoverageBaseError("diverged")

    monkeypatch.setattr(coverage_quality, "resolve_ci_base", fail_base)
    assert coverage_quality.main() == 2
    assert "coverage base error: diverged" in capsys.readouterr().err


def test_junit_cli_appends_github_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    junit_xml = _write(
        tmp_path / "junit.xml",
        "<testsuite><testcase classname='unit' name='ok' time='0.1'/></testsuite>",
    )
    github_summary = tmp_path / "github.md"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "summarize_test_results.py",
            str(junit_xml),
            "--label",
            "unit",
            "--slowest",
            "1",
            "--github-summary",
            str(github_summary),
        ],
    )

    assert summarize_test_results.main() == 0
    assert "passed=1" in capsys.readouterr().out
    assert "Test results: unit" in github_summary.read_text(encoding="utf-8")


def test_markdown_report_discloses_failed_gate() -> None:
    report = _passing_report()
    markdown = markdown_report(report, ["example failure"])

    assert "Gate: FAIL" in markdown
    assert "- example failure" in markdown
