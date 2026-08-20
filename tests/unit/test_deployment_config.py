"""Static deployment contracts that do not require Render credentials."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_daily_pipeline_is_declared_as_one_shot_render_cron():
    blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")

    assert "type: cron" in blueprint
    assert "name: portfolio-pilot-daily-pipeline" in blueprint
    assert 'schedule: "0 22 * * 1-5"' in blueprint
    assert "dockerCommand: python -m app.workers.run_daily_pipeline" in blueprint
    assert "type: worker\n" not in blueprint


def test_render_blueprint_does_not_contain_object_storage_credentials():
    blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")

    for secret_name in (
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "QWEN_API_KEY",
        "DATABASE_URL",
        "TUSHARE_TOKEN",
    ):
        if f"key: {secret_name}" in blueprint:
            section = blueprint.split(f"key: {secret_name}", maxsplit=1)[1].split(
                "- key:", maxsplit=1
            )[0]
            assert "sync: false" in section


def test_postgres_restart_e2e_is_a_required_independent_ci_layer():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    for job in (
        "lint-type-unit:",
        "postgres-integration:",
        "postgres-restart-e2e:",
        "security-audit:",
        "docker-build:",
    ):
        assert f"  {job}" in workflow
    assert "- postgres-restart-e2e" in workflow
    assert "-m postgres_restart" in workflow
    assert "tests/e2e/test_postgres_restart_persistence.py" in workflow
    assert "RUN_POSTGRES_RESTART_TEST" not in workflow


def test_default_pytest_and_dedicated_e2e_have_distinct_execution_scopes():
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    compose = (ROOT / "docker-compose.e2e.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert configuration["tool"]["pytest"]["ini_options"]["addopts"] == (
        "-m 'not postgres_restart'"
    )
    assert "restart_postgres_data:/var/lib/postgresql/data" in compose
    assert "target: e2e" in compose
    assert "EMBEDDING_PROVIDER: hashing" in compose
    assert "FROM dependencies AS e2e" in dockerfile
    assert dockerfile.rfind("FROM dependencies AS production") > dockerfile.rfind(
        "FROM dependencies AS e2e"
    )


def test_ci_reports_total_core_changed_and_test_runtime_quality():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    policy = (ROOT / "quality/coverage_policy.json").read_text(encoding="utf-8")

    assert "scripts/coverage_quality.py" in workflow
    assert "--coverage-xml coverage.xml" in workflow
    assert '--head "${{ github.event.pull_request.head.sha || github.sha }}"' in workflow
    assert "--enforce" in workflow
    assert "coverage-quality.json" in workflow
    assert workflow.count("scripts/summarize_test_results.py") == 3
    assert workflow.count("--junitxml=") == 3
    assert "--durations=20" in workflow
    assert '"core_coverage_percent": 81.68' in policy
    assert '"changed_lines_percent": 85.0' in policy
