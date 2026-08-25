"""Guards for the real-browser deterministic demo capture workflow."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_capture_demo_dry_run_is_side_effect_free(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.capture_demo",
            "--dry-run",
            "--output-dir",
            str(tmp_path / "captures"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.removeprefix("CAPTURE_DEMO="))
    assert payload["source"] == "live deterministic demo page"
    assert payload["screenshots"] == [
        "overview.png",
        "risk-and-evidence.png",
        "trace-and-review.png",
        "published-report.png",
    ]
    assert payload["gif"] == "end-to-end-demo.gif"
    assert payload["viewports"] == ["1366x768", "1440x900"]
    assert not (tmp_path / "captures").exists()


def test_capture_demo_rejects_non_local_urls() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.capture_demo",
            "--dry-run",
            "--base-url",
            "https://example.com",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 1
    assert "restricted to a local deterministic demo URL" in result.stdout


def test_capture_script_checks_required_disclosures_and_current_selectors() -> None:
    source = (ROOT / "scripts" / "capture_demo.py").read_text(encoding="utf-8")
    assert "Synthetic Demo" in source
    assert "Mock Model" in source
    assert "Not Investment Advice" in source
    assert ".showcase-risk-band" in source
    assert ".showcase-evidence-band" in source
    assert ".showcase-trace-review" in source
    assert ".showcase-report-band" in source
