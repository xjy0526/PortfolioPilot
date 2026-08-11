"""Health endpoint behavior without requiring a live PostgreSQL server."""
from __future__ import annotations

import httpx
import pytest

import app.db.session as session_module
from app.db.repositories.health import HealthRepository
from main import app


@pytest.mark.asyncio
async def test_liveness_does_not_query_database(monkeypatch):
    async def fail_if_called(self):
        raise AssertionError("liveness must not query PostgreSQL")

    monkeypatch.setattr(HealthRepository, "ping", fail_if_called)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_returns_503_when_database_query_fails(monkeypatch):
    async def unavailable(self):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(HealthRepository, "ping", unavailable)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "database": "unavailable"}


@pytest.mark.asyncio
async def test_each_fastapi_request_gets_a_distinct_session(monkeypatch):
    sessions: list[FakeSession] = []

    class FakeSession:
        def in_transaction(self):
            return False

        async def rollback(self):
            return None

    class FakeContext:
        def __init__(self):
            self.session = FakeSession()

        async def __aenter__(self):
            sessions.append(self.session)
            return self.session

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    class FakeFactory:
        def __call__(self):
            return FakeContext()

    monkeypatch.setattr(session_module, "AsyncSessionFactory", FakeFactory())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        assert (await client.get("/health/live")).status_code == 200
        assert (await client.get("/health/live")).status_code == 200

    assert len(sessions) == 2
    assert sessions[0] is not sessions[1]
