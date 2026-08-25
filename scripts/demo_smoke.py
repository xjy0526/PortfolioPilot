"""End-to-end smoke checks for the deterministic read-only demo."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import httpx

from app.core.principal import Principal
from app.db.session import AsyncSessionFactory, dispose_async_engine
from app.providers.embeddings import HashingEmbeddingProvider
from app.services.demo_fixture import (
    DEMO_AS_OF,
    DEMO_DOCUMENT_SOURCE,
    DEMO_FLAGS,
    DEMO_MODEL_SOURCE,
    DEMO_PORTFOLIO_NAME,
    DEMO_PRINCIPAL_USER,
    DEMO_TENANT_ID,
    DEMO_TRACE_KEY,
    demo_record_counts,
    load_demo_manifest,
    require_demo_fixture_mode,
    seed_demo_fixture,
)
from app.services.research_knowledge import PostgresKnowledgeService
from app.storage import build_object_storage
from config import settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://web:8080")
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser


async def _response_json(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    expected_status: int = 200,
) -> Any:
    response = await client.request(method, path)
    if response.status_code != expected_status:
        raise AssertionError(
            f"{method} {path} returned {response.status_code}, expected "
            f"{expected_status}: {response.text[:500]}"
        )
    return response.json()


async def _verify_database_and_seed_replay() -> dict[str, Any]:
    principal = Principal(
        user_id=DEMO_PRINCIPAL_USER,
        permission_groups=frozenset({"public"}),
        authenticated=True,
        tenant_id=DEMO_TENANT_ID,
        roles=frozenset({"knowledge_admin"}),
    )
    async with AsyncSessionFactory.begin() as session:
        before = await load_demo_manifest(session)
        replay = await seed_demo_fixture(session)
        after_counts = await demo_record_counts(session)
        if before.record_counts != replay.record_counts or before.record_counts != after_counts:
            raise AssertionError("Repeated demo seed changed persisted record counts")
        if before.as_dict()["portfolio_id"] != replay.as_dict()["portfolio_id"]:
            raise AssertionError("Repeated demo seed changed stable portfolio identity")
        retrieval = await PostgresKnowledgeService(
            session,
            embedder=HashingEmbeddingProvider(settings.RAG_EMBEDDING_DIMENSION),
            storage=build_object_storage(),
        ).retrieve_with_status(
            "synthetic",
            principal=principal,
            top_k=5,
            as_of=DEMO_AS_OF.date(),
            score_threshold=0.0,
        )
        citations = list(retrieval.get("citations", []))
        if not citations:
            raise AssertionError("Governed PostgreSQL/pgvector retrieval returned no evidence")
        if any(item.get("source_type") != DEMO_DOCUMENT_SOURCE for item in citations):
            raise AssertionError("Demo retrieval mixed non-demo document sources")
        return {"manifest": replay.as_dict(), "citation_count": len(citations)}


async def _verify_http(
    base_url: str,
    timeout: float,
    manifest: dict[str, Any],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        timeout=timeout,
        trust_env=False,
        transport=transport,
    ) as client:
        live = await _response_json(client, "GET", "/health/live")
        ready = await _response_json(client, "GET", "/health/ready")
        if live.get("status") not in {"ok", "live"} or ready.get("status") != "ready":
            raise AssertionError("Health endpoints did not report live/ready")

        portfolios = await _response_json(client, "GET", "/api/portfolios")
        matches = [item for item in portfolios if item.get("name") == DEMO_PORTFOLIO_NAME]
        if len(matches) != 1:
            raise AssertionError("Demo portfolio is not uniquely readable through the API")
        portfolio_id = str(manifest["portfolio_id"])
        if matches[0].get("id") != portfolio_id:
            raise AssertionError("Portfolio API returned an unexpected demo identity")

        positions = await _response_json(
            client, "GET", f"/api/portfolios/{portfolio_id}/positions"
        )
        if len(positions.get("positions", [])) != 3:
            raise AssertionError("Demo portfolio does not contain three rebuilt positions")
        valuation = await _response_json(
            client, "GET", f"/api/portfolios/{portfolio_id}/valuation"
        )
        if valuation.get("valuation_status") != "complete":
            raise AssertionError("Demo valuation snapshot is not complete")

        risk = await _response_json(
            client,
            "GET",
            f"/api/portfolio/risk-summary?portfolio_id={portfolio_id}",
        )
        if risk.get("risk_score") is None or not risk.get("asset_metrics"):
            raise AssertionError("Demo risk summary is missing deterministic metrics")

        documents = await _response_json(client, "GET", "/api/knowledge/documents")
        demo_documents = [
            item
            for item in documents.get("documents", [])
            if item.get("source_type") == DEMO_DOCUMENT_SOURCE
        ]
        if len(demo_documents) != 2:
            raise AssertionError("Published demo evidence is not visible")

        workflow = await _response_json(
            client, "GET", f"/api/workflows/{manifest['workflow_id']}"
        )
        if workflow.get("status") != "PUBLISHED" or not workflow.get("review_tasks"):
            raise AssertionError("Demo workflow or review task is missing")
        if workflow.get("report_id") != manifest["report_id"]:
            raise AssertionError("Demo workflow is not linked to its published report")

        showcase = await _response_json(
            client,
            "GET",
            f"/api/portfolios/{portfolio_id}/research-run",
        )
        if showcase.get("run", {}).get("run_id") != manifest["workflow_id"]:
            raise AssertionError("Showcase did not resolve the portfolio's persisted workflow")
        truth_labels = showcase.get("truth_labels", {})
        if truth_labels.get("mock_response_used") is not True:
            raise AssertionError("Showcase lost the mock-response disclosure")
        if truth_labels.get("real_model_used") is not False:
            raise AssertionError("Showcase misrepresented the deterministic model as real")
        if showcase.get("evidence", {}).get("status") != "available":
            raise AssertionError("Showcase evidence is unavailable or permission-filtered")
        if not showcase.get("prompt") or not showcase.get("trace"):
            raise AssertionError("Showcase prompt or LLM trace lineage is missing")
        if not showcase.get("validation") or not showcase.get("review_timeline"):
            raise AssertionError("Showcase validation or review timeline is missing")
        if showcase.get("published_report", {}).get("report_id") != manifest["report_id"]:
            raise AssertionError("Showcase report provenance is missing")

        traces = await _response_json(client, "GET", "/api/evaluation/traces")
        trace = next(
            (item for item in traces.get("traces", []) if item.get("trace_id") == DEMO_TRACE_KEY),
            None,
        )
        if trace is None or trace.get("provider") != DEMO_MODEL_SOURCE:
            raise AssertionError("Synthetic/mock LLM trace is not visible")

        report = await _response_json(
            client, "GET", f"/api/reports/{manifest['report_id']}"
        )
        report_payload = report.get("report", {})
        if not all(report_payload.get(key) is value for key, value in DEMO_FLAGS.items()):
            raise AssertionError("Published demo report lost synthetic/mock disclosure")

        await _response_json(
            client,
            "POST",
            f"/api/portfolios/{portfolio_id}/rebuild",
            expected_status=403,
        )


async def _run_smoke_async(
    base_url: str,
    timeout: float,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    require_demo_fixture_mode()
    if not settings.read_only_demo:
        raise AssertionError("Demo Web must run with READ_ONLY_DEMO=true")
    database_check = await _verify_database_and_seed_replay()
    await _verify_http(
        base_url,
        timeout,
        database_check["manifest"],
        transport=transport,
    )
    return {
        "status": "passed",
        "base_url": base_url,
        "citation_count": database_check["citation_count"],
        "record_counts": database_check["manifest"]["record_counts"],
        **DEMO_FLAGS,
    }


async def _run(
    base_url: str,
    timeout: float,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    try:
        return await _run_smoke_async(base_url, timeout, transport=transport)
    finally:
        await dispose_async_engine()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args.base_url, args.timeout))
    except Exception as exc:
        print(
            f"DEMO_SMOKE_FAILED={type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    print("DEMO_SMOKE=" + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
