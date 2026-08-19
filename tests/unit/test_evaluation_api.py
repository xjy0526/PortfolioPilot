"""Runtime regressions for the migrated governed evaluation API."""
from __future__ import annotations

import ast
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import app.api.evaluation as evaluation_api
import main
from app.api.dependencies import get_db_session
from app.core.principal import Principal, get_principal
from routes import evaluation as legacy_evaluation


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def reviewer_overrides() -> Iterator[object]:
    session = object()

    async def fake_session() -> AsyncIterator[object]:
        yield session

    async def fake_principal() -> Principal:
        return Principal(
            user_id="contract-reviewer",
            roles=frozenset({"research_reviewer"}),
        )

    main.app.dependency_overrides[get_db_session] = fake_session
    main.app.dependency_overrides[get_principal] = fake_principal
    yield session
    main.app.dependency_overrides.clear()


def test_legacy_evaluation_module_is_a_pure_router_shim() -> None:
    tree = ast.parse((ROOT / "routes" / "evaluation.py").read_text(encoding="utf-8"))
    definitions = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    assert "Deprecated import shim" in (ast.get_docstring(tree) or "")
    assert not definitions
    assert main.evaluation_router is evaluation_api.router
    assert legacy_evaluation.router is evaluation_api.router
    assert legacy_evaluation.get_evaluation_dashboard is evaluation_api.get_evaluation_dashboard
    assert legacy_evaluation.get_evaluation_traces is evaluation_api.get_evaluation_traces


def test_main_registers_evaluation_from_app_api() -> None:
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "app.api.evaluation" in imported_modules
    assert "routes.evaluation" not in imported_modules


@pytest.mark.asyncio
async def test_evaluation_dashboard_contract(
    monkeypatch: pytest.MonkeyPatch,
    reviewer_overrides: object,
) -> None:
    async def fake_dashboard(session: object, limit: int) -> dict[str, Any]:
        assert session is reviewer_overrides
        assert limit == 25
        return {"status": "available", "evaluation_mode": "synthetic_smoke"}

    monkeypatch.setattr(evaluation_api, "evaluation_dashboard", fake_dashboard)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/evaluation/dashboard?limit=25")

    assert response.status_code == 200
    assert response.json() == {
        "status": "available",
        "evaluation_mode": "synthetic_smoke",
    }


@pytest.mark.asyncio
async def test_evaluation_trace_contract(
    monkeypatch: pytest.MonkeyPatch,
    reviewer_overrides: object,
) -> None:
    class FakeRegistry:
        def __init__(self, session: object):
            assert session is reviewer_overrides

        async def list_traces(self, limit: int) -> list[dict[str, object]]:
            assert limit == 2
            return [{"trace_id": "trace-public-fixture"}]

    monkeypatch.setattr(evaluation_api, "PromptRegistry", FakeRegistry)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/evaluation/traces?limit=2")

    assert response.status_code == 200
    assert response.json() == {
        "count": 1,
        "traces": [{"trace_id": "trace-public-fixture"}],
    }


@pytest.mark.asyncio
async def test_evaluation_api_keeps_role_guard() -> None:
    async def fake_session() -> AsyncIterator[object]:
        yield object()

    async def unauthorized_principal() -> Principal:
        return Principal(user_id="reader", roles=frozenset())

    main.app.dependency_overrides[get_db_session] = fake_session
    main.app.dependency_overrides[get_principal] = unauthorized_principal
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/evaluation/dashboard")
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 403
