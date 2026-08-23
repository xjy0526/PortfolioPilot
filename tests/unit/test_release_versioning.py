"""Release metadata and disclosure contracts for PortfolioPilot v2.0.0."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

from app import __version__
from main import app


ROOT = Path(__file__).resolve().parents[2]
RELEASE_VERSION = "2.0.0"


def _read_json(relative_path: str) -> dict[str, object]:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def test_release_version_is_consistent_across_runtime_and_metadata() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    api_contract = _read_json("tests/contracts/fastapi_routes_v1.json")
    demo_manifest = _read_json("data/demo_fixture_manifest.json")
    evaluation_manifest = _read_json("evaluation/datasets/v2/dataset_manifest.json")

    assert __version__ == RELEASE_VERSION
    assert app.version == RELEASE_VERSION
    assert pyproject["project"]["version"] == RELEASE_VERSION
    assert api_contract["api_version"] == RELEASE_VERSION
    assert demo_manifest["schema_version"] == RELEASE_VERSION
    assert evaluation_manifest["version"] == RELEASE_VERSION


def test_demo_manifest_is_explicit_and_references_existing_fixtures() -> None:
    manifest = _read_json("data/demo_fixture_manifest.json")

    assert manifest["synthetic_data_used"] is True
    assert manifest["production_data_used"] is False
    assert manifest["real_customer_portfolio_used"] is False
    execution_boundary = manifest["execution_boundary"]
    assert isinstance(execution_boundary, dict)
    assert execution_boundary["deterministic_synthetic_smoke_available"] is True
    assert execution_boundary["one_command_governed_demo_available"] is False
    fixture_files = manifest["files"]
    assert isinstance(fixture_files, dict)
    assert all((ROOT / str(path)).is_file() for path in fixture_files.values())


def test_docker_and_api_documentation_publish_the_release_version() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    api_documentation = (ROOT / "docs/api.md").read_text(encoding="utf-8")

    assert f"ARG APP_VERSION={RELEASE_VERSION}" in dockerfile
    assert 'org.opencontainers.image.version="${APP_VERSION}"' in dockerfile
    assert f"API documentation version: `{RELEASE_VERSION}`" in api_documentation


def test_release_documentation_discloses_pre_tag_and_evaluation_boundaries() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    release_notes = (ROOT / "docs/releases/v2.0.0.md").read_text(encoding="utf-8")
    evaluation_manifest = _read_json("evaluation/datasets/v2/dataset_manifest.json")
    migration = (ROOT / "docs/releases/v2.0.0-migration.md").read_text(
        encoding="utf-8"
    )
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert "Latest release: [v2.0.0](docs/releases/v2.0.0.md)" in readme
    assert "Tag 和 GitHub Release 尚未创建" in readme
    assert "/releases/tag/v2.0.0" not in readme
    for disclosure in (
        "不执行真实交易",
        "不构成投资建议",
        "yfinance 仅用于研究演示",
        "一键 governed demo",
    ):
        assert disclosure in release_notes
    review_status = evaluation_manifest["human_review_status"]
    assert isinstance(review_status, dict)
    assert (
        f"approved_case_count={review_status['approved_case_count']}" in release_notes
    )
    assert f"pending_case_count={review_status['pending_case_count']}" in release_notes
    for section_number in range(1, 15):
        assert f"## {section_number}." in release_notes
    assert "scripts/rebuild_stale_legacy_valuations.py" in migration
    assert "当前 v2.0.0" in migration
    for heading in (
        "### Added",
        "### Changed",
        "### Fixed",
        "### Security",
        "### Known limitations",
    ):
        assert heading in changelog
