"""PR-0 acceptance tests for identity, CI and time configuration."""
from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path

from cache_manager import CacheManager
from config import Settings
from time_utils import display_now, utc_now, utc_now_iso

ROOT = Path(__file__).resolve().parent.parent


def test_workflows_target_main_and_contain_no_legacy_identity():
    deploy = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    combined = (deploy + ci).lower()

    assert "workflow_run:" in deploy
    assert "head_branch == 'main'" in deploy
    assert "conclusion == 'success'" in deploy
    assert "branches: [main]" in ci
    assert "master" not in combined
    legacy_markers = (
        "job-automation-" + "jonas",
        "finance" + "bro",
        "finanz" + "bro",
        "gemini_" + "api_key",
    )
    for legacy in legacy_markers:
        assert legacy not in combined
    assert "QWEN_API_KEY" in deploy


def test_ci_runs_required_quality_commands():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "ruff check ." in ci
    assert "run: mypy" in ci
    assert "python -m pytest" in ci
    assert "--cov=." in ci
    assert "python -m coverage report" in ci
    assert "docker build" in ci


def test_utc_helpers_and_configurable_display_timezone(monkeypatch):
    persisted = utc_now()

    assert persisted.tzinfo is UTC
    assert utc_now_iso().endswith("Z")
    monkeypatch.setattr("time_utils.settings.DISPLAY_TIMEZONE", "Asia/Shanghai")
    assert display_now().utcoffset().total_seconds() == 8 * 3600


def test_disk_cache_writes_aware_utc_timestamp(tmp_path):
    cache = CacheManager("pr0-utc-test")
    cache.file = tmp_path / "cache.json"
    cache.set("value", 1)
    cache.flush()

    payload = json.loads(cache.file.read_text(encoding="utf-8"))
    assert payload["_cached_at"].endswith("+00:00")


def test_default_identity_and_timezone():
    current = Settings(_env_file=None)

    assert current.APP_NAME == "PortfolioPilot"
    assert current.DISPLAY_TIMEZONE == "Asia/Shanghai"
    assert "Polymarket" not in current.APP_TAGLINE


def test_current_limitations_disclose_required_boundaries():
    limitations = (ROOT / "docs/current-limitations.md").read_text(encoding="utf-8")

    assert "SQLite" in limitations
    assert "生产级严格 walk-forward" in limitations
    assert "yfinance 仅用于研究演示" in limitations
    assert "Mock 与 fallback 触发条件" in limitations
