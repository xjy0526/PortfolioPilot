"""Static and failure-contract tests for the deterministic demo."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.services.demo_fixture import require_demo_fixture_mode
from config import Settings
from scripts import demo_reset, demo_smoke, seed_demo

ROOT = Path(__file__).resolve().parents[2]


def test_production_rejects_fixture_mode() -> None:
    configuration = Settings(
        _env_file=None,
        ENVIRONMENT="production",
        READ_ONLY_DEMO=True,
        DEMO_FIXTURE_MODE=True,
    )

    with pytest.raises(RuntimeError, match="DEMO_FIXTURE_MODE is forbidden"):
        configuration.validate_runtime_configuration()
    with pytest.raises(RuntimeError, match="DEMO_FIXTURE_MODE is forbidden"):
        require_demo_fixture_mode(configuration)


def test_fixture_operations_require_explicit_opt_in() -> None:
    configuration = Settings(
        _env_file=None,
        ENVIRONMENT="development",
        DEMO_FIXTURE_MODE=False,
    )

    with pytest.raises(RuntimeError, match="DEMO_FIXTURE_MODE=true"):
        require_demo_fixture_mode(configuration)


def test_demo_compose_is_parseable_and_network_is_runtime_isolated() -> None:
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "portfoliopilot-demo-config-test",
            "-f",
            "docker-compose.demo.yml",
            "config",
            "--quiet",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    compose = (ROOT / "docker-compose.demo.yml").read_text(encoding="utf-8")
    assert "target: e2e" in compose
    assert "DEMO_FIXTURE_MODE: \"true\"" in compose
    assert "READ_ONLY_DEMO: \"true\"" in compose
    assert "EMBEDDING_PROVIDER: hashing" in compose
    assert "internal: true" in compose
    assert "127.0.0.1:${DEMO_PORT:-8000}:8080" in compose
    assert "demo_ingress" in compose
    assert "demo_fixture_model" in compose
    assert "QWEN_API_KEY: \"\"" in compose
    assert "TUSHARE_TOKEN: \"\"" in compose


def test_makefile_exposes_complete_demo_lifecycle() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in (
        "demo:",
        "demo-up:",
        "demo-down:",
        "demo-reset:",
        "demo-smoke:",
        "demo-logs:",
    ):
        assert target in makefile
    assert "alembic upgrade head" not in makefile
    assert "scripts.seed_demo" not in makefile
    assert "run --rm --no-deps migrate" in makefile
    assert "run --rm --no-deps seed" in makefile
    assert "port web 8080" in makefile
    assert '127.0.0.1:$(DEMO_PORT)' in makefile


def test_smoke_cli_returns_nonzero_when_a_check_fails(monkeypatch, capsys) -> None:
    async def fail(_base_url: str, _timeout: float):
        raise AssertionError("intentional smoke failure")

    monkeypatch.setattr(demo_smoke, "_run", fail)

    assert demo_smoke.main(["--base-url", "http://invalid.test"]) == 1
    assert "DEMO_SMOKE_FAILED=AssertionError" in capsys.readouterr().err


def test_demo_cli_success_outputs_are_machine_readable(monkeypatch, capsys) -> None:
    async def seeded():
        return {"portfolio_id": "demo", "is_demo": True}

    async def reset():
        return {"portfolios": 1}

    async def smoke(_base_url: str, _timeout: float):
        return {"status": "passed", "mock_response_used": True}

    monkeypatch.setattr(seed_demo, "_seed", seeded)
    assert seed_demo.main() == 0
    assert "DEMO_MANIFEST=" in capsys.readouterr().out

    monkeypatch.setattr(demo_reset, "_reset", reset)
    assert demo_reset.main() == 0
    assert "DEMO_RESET=" in capsys.readouterr().out

    monkeypatch.setattr(demo_smoke, "_run", smoke)
    assert demo_smoke.main(["--base-url", "http://demo.test"]) == 0
    assert "DEMO_SMOKE=" in capsys.readouterr().out
