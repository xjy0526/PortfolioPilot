"""Compose E2E proving governed business data survives PostgreSQL restart."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.postgres, pytest.mark.postgres_restart]
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPOSITORY_ROOT / "docker-compose.e2e.yml"
POSTGRES_USER = "portfoliopilot"
POSTGRES_DATABASE = "portfoliopilot_restart_e2e"


class ComposeE2E:
    def __init__(self) -> None:
        configured = os.getenv("POSTGRES_RESTART_E2E_PROJECT", "").strip().lower()
        self.project_name = configured or f"pprestart-{uuid.uuid4().hex[:12]}"
        self.environment = {
            **os.environ,
            "COMPOSE_PROJECT_NAME": self.project_name,
        }

    def run(
        self,
        *arguments: str,
        timeout: int = 300,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), *arguments],
            cwd=REPOSITORY_ROOT,
            env=self.environment,
            check=check,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def app_command(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self.run("exec", "-T", "app", *arguments, timeout=120)

    def wait_until_healthy(self, service: str) -> None:
        self.run(
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            "90",
            service,
            timeout=120,
        )

    def postmaster_start_time(self) -> str:
        result = self.run(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            POSTGRES_USER,
            "-d",
            POSTGRES_DATABASE,
            "-Atc",
            "SELECT pg_postmaster_start_time();",
            timeout=30,
        )
        return result.stdout.strip()

    def logs(self) -> str:
        result = self.run("logs", "--no-color", check=False, timeout=60)
        return f"{result.stdout}\n{result.stderr}".strip()

    def cleanup(self) -> subprocess.CompletedProcess[str]:
        return self.run(
            "down",
            "--volumes",
            "--remove-orphans",
            "--timeout",
            "10",
            check=False,
            timeout=120,
        )


def _manifest(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    for line in reversed(result.stdout.splitlines()):
        if line.startswith("E2E_MANIFEST="):
            parsed = json.loads(line.removeprefix("E2E_MANIFEST="))
            if isinstance(parsed, dict):
                return parsed
    raise AssertionError(f"E2E manifest missing from command output:\n{result.stdout}")


def test_governed_business_data_survives_real_postgres_restart() -> None:
    if shutil.which("docker") is None:
        pytest.fail("postgres_restart E2E requires Docker with the Compose plugin")

    harness = ComposeE2E()
    namespace = f"run-{uuid.uuid4().hex[:12]}"
    failure: Exception | None = None
    try:
        harness.run("version", timeout=30)
        harness.run("build", "app", timeout=900)
        harness.wait_until_healthy("postgres")
        harness.run("run", "--rm", "app", "alembic", "upgrade", "head", timeout=180)
        harness.wait_until_healthy("app")

        before_restart = harness.postmaster_start_time()
        seeded = _manifest(
            harness.app_command(
                "python",
                "-m",
                "scripts.postgres_restart_e2e",
                "seed",
                "--namespace",
                namespace,
            )
        )

        harness.run("restart", "postgres", timeout=120)
        harness.wait_until_healthy("postgres")
        harness.wait_until_healthy("app")
        after_restart = harness.postmaster_start_time()
        if before_restart == after_restart:
            raise AssertionError("PostgreSQL postmaster start time did not change")

        verified = _manifest(
            harness.app_command(
                "python",
                "-m",
                "scripts.postgres_restart_e2e",
                "verify",
                "--namespace",
                namespace,
            )
        )
        assert verified["idempotent_replay_verified"] is True
        assert seeded["manifest"] == verified["manifest"]
        assert len(verified["manifest"]["embedding_checksum"]) == 64
        assert len(verified["manifest"]["ids"]) == 13
        assert verified["manifest"]["counts"] == {
            "document_chunks": 1,
            "document_versions": 1,
            "embeddings": 1,
            "llm_traces": 1,
            "portfolio_transactions": 2,
            "prompt_versions": 1,
            "published_reports": 1,
            "rebuilt_positions": 1,
            "review_decisions": 1,
            "review_tasks": 1,
            "valuation_snapshots": 1,
            "workflow_runs": 1,
        }
    except Exception as exc:
        failure = exc
        print("\n--- PostgreSQL restart E2E container logs ---")
        print(harness.logs())
    finally:
        cleanup = harness.cleanup()
        if cleanup.returncode != 0:
            print("\n--- PostgreSQL restart E2E cleanup failure ---")
            print(cleanup.stdout)
            print(cleanup.stderr)
            if failure is None:
                failure = AssertionError("Compose E2E cleanup failed")
    if failure is not None:
        raise failure
