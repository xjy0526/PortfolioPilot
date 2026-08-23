"""Regression guards for API routes, frontend calls, and CLI entry points."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from main import app
from scripts.export_api_contract import (
    CORE_API_PATHS,
    build_core_contract_snapshot,
    build_route_snapshot,
)


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = ROOT / "tests" / "contracts"
API_STRING_PATTERN = re.compile(r"(?P<quote>['\"`])(?P<url>/api/.*?)(?P=quote)")

MAJOR_CLI_MODULES = (
    "app.workers.run_market_sync",
    "app.workers.run_position_rebuild",
    "app.workers.run_daily_pipeline",
    "app.workers.run_knowledge_ingestion",
    "app.workers.run_research_workflow",
    "app.workers.run_embedding_backfill",
    "app.workers.run_storage_cleanup",
    "backtest.run_backtest",
    "evaluation.run_full_eval",
    "evaluation.run_llm_eval",
    "evaluation.run_v2_eval",
    "scripts.build_evaluation_v2_dataset",
    "scripts.export_api_contract",
    "scripts.rebuild_stale_legacy_valuations",
)


def _load_snapshot(name: str) -> dict[str, object]:
    return json.loads((CONTRACT_DIR / name).read_text(encoding="utf-8"))


def test_all_fastapi_routes_match_committed_snapshot() -> None:
    current = build_route_snapshot(app.openapi())
    expected = _load_snapshot("fastapi_routes_v1.json")

    assert current == expected


def test_core_api_contract_matches_committed_snapshot() -> None:
    current = build_core_contract_snapshot(app.openapi())
    expected = _load_snapshot("core_api_contract_v1.json")

    assert current == expected
    assert tuple(current["paths"]) == CORE_API_PATHS


def test_every_frontend_api_path_is_registered() -> None:
    frontend_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "static").glob("*"))
        if path.suffix in {".html", ".js"}
    )
    frontend_paths = {
        match.group("url").split("?", maxsplit=1)[0]
        for match in API_STRING_PATTERN.finditer(frontend_source)
    }
    registered_paths = set(app.openapi()["paths"])
    missing = sorted(
        path
        for path in frontend_paths
        if not any(_path_matches(path, registered) for registered in registered_paths)
    )

    assert len(frontend_paths) >= 40, "Frontend API extraction unexpectedly found too few paths"
    assert not missing, f"Frontend calls unregistered API paths: {missing}"


def _path_matches(frontend_path: str, registered_path: str) -> bool:
    frontend_parts = frontend_path.strip("/").split("/")
    registered_parts = registered_path.strip("/").split("/")
    if len(frontend_parts) != len(registered_parts):
        return False
    return all(
        (registered.startswith("{") and registered.endswith("}")) or frontend == registered
        for frontend, registered in zip(frontend_parts, registered_parts, strict=True)
    )


@pytest.mark.parametrize("module", MAJOR_CLI_MODULES)
def test_major_cli_help_is_executable(module: str) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "ENVIRONMENT": "test",
            "EMBEDDING_PROVIDER": "hashing",
            "RAG_ALLOW_HASHING_FALLBACK": "true",
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()
