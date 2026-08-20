"""Export stable FastAPI route and core API contract snapshots."""
from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROUTES_OUTPUT = ROOT / "tests" / "contracts" / "fastapi_routes_v1.json"
DEFAULT_CONTRACT_OUTPUT = ROOT / "tests" / "contracts" / "core_api_contract_v1.json"
HTTP_METHODS = frozenset({"delete", "get", "head", "options", "patch", "post", "put"})

CORE_API_PATHS = (
    "/health/live",
    "/health/ready",
    "/api/portfolios",
    "/api/portfolios/{portfolio_id}",
    "/api/portfolios/{portfolio_id}/transactions",
    "/api/portfolios/{portfolio_id}/imports/transactions",
    "/api/import-batches/{batch_id}/errors",
    "/api/portfolios/{portfolio_id}/positions",
    "/api/portfolios/{portfolio_id}/valuation",
    "/api/portfolios/{portfolio_id}/rebuild",
    "/api/market-data/sync",
    "/api/portfolio/risk-summary",
    "/api/ai/analyze-portfolio",
    "/api/rag/retrieve",
    "/api/backtest/report",
    "/api/portfolio/rebalance",
    "/api/knowledge/documents",
    "/api/knowledge/documents/{document_id}",
    "/api/knowledge/documents/{document_id}/publish",
    "/api/knowledge/documents/{document_id}/deactivate",
    "/api/knowledge/ingestion-jobs/{job_id}",
    "/api/knowledge/ingestion-jobs/{job_id}/source",
    "/api/prompts",
    "/api/prompts/{prompt_id}/versions",
    "/api/prompts/{prompt_id}/versions/{version}/publish",
    "/api/prompts/compare",
    "/api/prompts/{prompt_id}/rollback",
    "/api/workflows/research-report",
    "/api/workflows/{run_id}",
    "/api/reviews/{review_id}/approve",
    "/api/reviews/{review_id}/reject",
    "/api/reviews/{review_id}/request-changes",
    "/api/reports/{report_id}",
    "/api/evaluation/dashboard",
    "/api/evaluation/traces",
)


def build_route_snapshot(openapi_schema: Mapping[str, Any]) -> dict[str, Any]:
    """Return every OpenAPI operation in a compact, deterministic form."""
    routes: list[dict[str, Any]] = []
    paths = openapi_schema.get("paths", {})
    for path in sorted(paths):
        path_item = paths[path]
        for method in sorted(HTTP_METHODS.intersection(path_item)):
            operation = path_item[method]
            routes.append(
                {
                    "path": path,
                    "method": method.upper(),
                    "operation_id": operation.get("operationId"),
                    "deprecated": bool(operation.get("deprecated", False)),
                    "tags": operation.get("tags", []),
                }
            )
    return {
        "snapshot_version": 1,
        "api_title": openapi_schema.get("info", {}).get("title"),
        "api_version": openapi_schema.get("info", {}).get("version"),
        "path_count": len(paths),
        "operation_count": len(routes),
        "routes": routes,
    }


def build_core_contract_snapshot(openapi_schema: Mapping[str, Any]) -> dict[str, Any]:
    """Return core path contracts and only the schemas reachable from them."""
    all_paths = openapi_schema.get("paths", {})
    missing = sorted(set(CORE_API_PATHS) - set(all_paths))
    if missing:
        raise ValueError(f"Core API paths missing from OpenAPI: {', '.join(missing)}")

    selected_paths = {path: all_paths[path] for path in CORE_API_PATHS}
    all_schemas = openapi_schema.get("components", {}).get("schemas", {})
    schema_names = _reachable_schema_names(selected_paths, all_schemas)
    selected_schemas = {name: all_schemas[name] for name in sorted(schema_names)}
    return {
        "snapshot_version": 1,
        "info": {
            "title": openapi_schema.get("info", {}).get("title"),
            "version": openapi_schema.get("info", {}).get("version"),
        },
        "paths": selected_paths,
        "components": {"schemas": selected_schemas},
    }


def _reachable_schema_names(
    selected_paths: Mapping[str, Any],
    all_schemas: Mapping[str, Any],
) -> set[str]:
    names = _schema_references(selected_paths)
    pending = list(names)
    while pending:
        name = pending.pop()
        schema = all_schemas.get(name)
        if schema is None:
            raise ValueError(f"Referenced OpenAPI schema is missing: {name}")
        for dependency in _schema_references(schema):
            if dependency not in names:
                names.add(dependency)
                pending.append(dependency)
    return names


def _schema_references(value: Any) -> set[str]:
    references: set[str] = set()
    if isinstance(value, Mapping):
        reference = value.get("$ref")
        prefix = "#/components/schemas/"
        if isinstance(reference, str) and reference.startswith(prefix):
            references.add(reference.removeprefix(prefix))
        for nested in value.values():
            references.update(_schema_references(nested))
    elif isinstance(value, list):
        for nested in value:
            references.update(_schema_references(nested))
    return references


def write_snapshot(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def snapshot_matches(path: Path, payload: Mapping[str, Any]) -> bool:
    if not path.exists():
        return False
    return json.loads(path.read_text(encoding="utf-8")) == payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Export PortfolioPilot API contract snapshots")
    parser.add_argument("--routes-output", type=Path, default=DEFAULT_ROUTES_OUTPUT)
    parser.add_argument("--contract-output", type=Path, default=DEFAULT_CONTRACT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail instead of writing when committed snapshots differ from current OpenAPI",
    )
    args = parser.parse_args()

    from main import app

    schema = app.openapi()
    route_snapshot = build_route_snapshot(schema)
    contract_snapshot = build_core_contract_snapshot(schema)
    if args.check:
        mismatches = [
            str(path)
            for path, payload in (
                (args.routes_output, route_snapshot),
                (args.contract_output, contract_snapshot),
            )
            if not snapshot_matches(path, payload)
        ]
        if mismatches:
            raise SystemExit("API contract snapshot mismatch: " + ", ".join(mismatches))
        print("API contract snapshots match current OpenAPI.")
        return

    write_snapshot(args.routes_output, route_snapshot)
    write_snapshot(args.contract_output, contract_snapshot)
    print(f"Wrote route snapshot to {args.routes_output}")
    print(f"Wrote core API contract to {args.contract_output}")


if __name__ == "__main__":
    main()
