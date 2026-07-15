"""SQLite-backed Prompt Registry with version, publish, rollback and comparison."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from prompts.registry_models import (
    PromptDeployment,
    PromptEvaluationResult,
    PromptTemplate,
    PromptVersion,
)


class PromptRegistry:
    def __init__(self, connection: sqlite3.Connection | None = None):
        if connection is None:
            from database import _get_conn

            connection = _get_conn()
        self.conn = connection
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        ensure_prompt_schema(self.conn)

    def list_prompts(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM prompt_templates ORDER BY business_scene, name"
        ).fetchall()
        return [_decode_template(row).model_dump(mode="json") for row in rows]

    def create_prompt(self, payload: dict[str, Any]) -> tuple[PromptTemplate, PromptVersion]:
        prompt_id = str(payload.get("prompt_id") or uuid.uuid4())
        now = _now()
        template = PromptTemplate(
            prompt_id=prompt_id,
            name=payload["name"],
            business_scene=payload["business_scene"],
            owner=payload.get("owner", "Research Platform"),
            status="draft",
            current_version=1,
            created_at=now,
        )
        version = PromptVersion(
            prompt_id=prompt_id,
            version=1,
            template=payload["template"],
            variables=payload.get("variables", []),
            input_schema=payload["input_schema"],
            output_schema=payload["output_schema"],
            model=payload.get("model", "qwen-plus"),
            temperature=payload.get("temperature", 0.2),
            owner=payload.get("owner", "Research Platform"),
            status="draft",
            change_log=payload.get("change_log", "Initial version"),
            baseline_metrics=payload.get("baseline_metrics", {}),
            created_at=now,
        )
        try:
            self.conn.execute("BEGIN")
            self.conn.execute(
                """INSERT INTO prompt_templates
                   (prompt_id, name, business_scene, owner, status, current_version,
                    published_version, created_at, published_at)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL)""",
                (prompt_id, template.name, template.business_scene, template.owner,
                 template.status, 1, now, ),
            )
            self._insert_version(version)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return template, version

    def create_version(self, prompt_id: str, payload: dict[str, Any]) -> PromptVersion | None:
        template = self.get_prompt(prompt_id)
        if not template:
            return None
        latest = self.get_version(prompt_id, template.current_version)
        assert latest is not None
        version = PromptVersion(
            prompt_id=prompt_id,
            version=template.current_version + 1,
            template=payload.get("template", latest.template),
            variables=payload.get("variables", latest.variables),
            input_schema=payload.get("input_schema", latest.input_schema),
            output_schema=payload.get("output_schema", latest.output_schema),
            model=payload.get("model", latest.model),
            temperature=payload.get("temperature", latest.temperature),
            owner=payload.get("owner", latest.owner),
            status=payload.get("status", "draft"),
            change_log=payload.get("change_log", ""),
            baseline_metrics=payload.get("baseline_metrics", latest.baseline_metrics),
        )
        try:
            self.conn.execute("BEGIN")
            self._insert_version(version)
            self.conn.execute(
                "UPDATE prompt_templates SET current_version=?, status=? WHERE prompt_id=?",
                (version.version, version.status, prompt_id),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return version

    def publish(self, prompt_id: str, version: int) -> PromptDeployment | None:
        target = self.get_version(prompt_id, version)
        template = self.get_prompt(prompt_id)
        if not target or not template:
            return None
        deployment = PromptDeployment(
            deployment_id=str(uuid.uuid4()), prompt_id=prompt_id, version=version,
            action="publish", previous_version=template.published_version,
        )
        self._deploy(template, target, deployment)
        return deployment

    def rollback(self, prompt_id: str, target_version: int | None = None) -> PromptDeployment | None:
        template = self.get_prompt(prompt_id)
        if not template or template.published_version is None:
            return None
        if target_version is None:
            row = self.conn.execute(
                """SELECT previous_version FROM prompt_deployments
                   WHERE prompt_id=? AND version=? AND previous_version IS NOT NULL
                   ORDER BY created_at DESC LIMIT 1""",
                (prompt_id, template.published_version),
            ).fetchone()
            target_version = int(row["previous_version"]) if row else template.published_version - 1
        target = self.get_version(prompt_id, target_version)
        if not target:
            return None
        deployment = PromptDeployment(
            deployment_id=str(uuid.uuid4()), prompt_id=prompt_id, version=target_version,
            action="rollback", previous_version=template.published_version,
        )
        self._deploy(template, target, deployment)
        return deployment

    def compare_versions(
        self,
        prompt_id: str,
        version_a: int,
        version_b: int,
        test_cases: list[dict[str, Any]],
    ) -> PromptEvaluationResult:
        first = self.get_version(prompt_id, version_a)
        second = self.get_version(prompt_id, version_b)
        if not first or not second:
            raise ValueError("Prompt version not found")
        metrics_a = _evaluate_prompt(first, test_cases)
        metrics_b = _evaluate_prompt(second, test_cases)
        keys = sorted(set(metrics_a) | set(metrics_b))
        result = PromptEvaluationResult(
            evaluation_id=str(uuid.uuid4()), prompt_id=prompt_id,
            version_a=version_a, version_b=version_b, test_case_count=len(test_cases),
            metrics_a=metrics_a, metrics_b=metrics_b,
            metric_delta={key: round(metrics_b.get(key, 0.0) - metrics_a.get(key, 0.0), 6) for key in keys},
        )
        self.conn.execute(
            """INSERT INTO prompt_evaluation_results
               (evaluation_id, prompt_id, version_a, version_b, test_case_count,
                metrics_a, metrics_b, metric_delta, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (result.evaluation_id, prompt_id, version_a, version_b, len(test_cases),
             _json(metrics_a), _json(metrics_b), _json(result.metric_delta),
             result.created_at.isoformat()),
        )
        self.conn.commit()
        return result

    def get_prompt(self, prompt_id: str) -> PromptTemplate | None:
        row = self.conn.execute(
            "SELECT * FROM prompt_templates WHERE prompt_id=?", (prompt_id,)
        ).fetchone()
        return _decode_template(row) if row else None

    def get_version(self, prompt_id: str, version: int) -> PromptVersion | None:
        row = self.conn.execute(
            "SELECT * FROM prompt_versions WHERE prompt_id=? AND version=?",
            (prompt_id, version),
        ).fetchone()
        return _decode_version(row) if row else None

    def get_published_by_scene(self, business_scene: str) -> PromptVersion | None:
        row = self.conn.execute(
            """SELECT v.* FROM prompt_templates t JOIN prompt_versions v
               ON v.prompt_id=t.prompt_id AND v.version=t.published_version
               WHERE t.business_scene=? AND t.status='published'
               ORDER BY t.published_at DESC LIMIT 1""",
            (business_scene,),
        ).fetchone()
        return _decode_version(row) if row else None

    def record_llm_call(
        self, *, trace_id: str, prompt_id: str, prompt_version: int,
        provider: str, model: str, status: str, validation_error: str = "",
        run_id: str = "", user_id: str = "", business_scene: str = "",
        model_parameters: dict[str, Any] | None = None, input_hash: str = "",
        retrieved_document_ids: list[str] | None = None,
        retrieved_chunk_ids: list[str] | None = None,
        tool_calls: list[dict[str, Any]] | None = None, latency_ms: float = 0.0,
        input_tokens: int = 0, output_tokens: int = 0, estimated_cost: float = 0.0,
        output_schema_valid: bool = False, fallback_used: bool = False,
        error_type: str = "", review_decision: str = "", review_feedback: str = "",
    ) -> None:
        self.conn.execute(
            """INSERT INTO llm_call_traces
               (trace_id, run_id, user_id, business_scene, prompt_id, prompt_version,
                provider, model, model_parameters, input_hash, retrieved_document_ids,
                retrieved_chunk_ids, tool_calls, latency_ms, input_tokens, output_tokens,
                estimated_cost, output_schema_valid, fallback_used, status,
                validation_error, error_type, review_decision, review_feedback, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (trace_id, run_id, user_id, business_scene, prompt_id, prompt_version,
             provider, model, _json(model_parameters or {}), input_hash,
             _json(retrieved_document_ids or []), _json(retrieved_chunk_ids or []),
             _json(tool_calls or []), latency_ms, input_tokens, output_tokens,
             estimated_cost, int(output_schema_valid), int(fallback_used), status,
             validation_error, error_type, review_decision, review_feedback, _now()),
        )
        self.conn.commit()

    def update_trace_review(self, run_id: str, decision: str, feedback: str) -> None:
        self.conn.execute(
            "UPDATE llm_call_traces SET review_decision=?, review_feedback=? WHERE run_id=?",
            (decision, feedback, run_id),
        )
        self.conn.commit()

    def mark_trace_fallback(self, trace_id: str) -> None:
        self.conn.execute(
            "UPDATE llm_call_traces SET fallback_used=1 WHERE trace_id=?", (trace_id,)
        )
        self.conn.commit()

    def list_traces(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM llm_call_traces ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 1000)),)
        ).fetchall()
        results = []
        for row in rows:
            payload = dict(row)
            for field in ("model_parameters", "retrieved_document_ids", "retrieved_chunk_ids", "tool_calls"):
                fallback = "{}" if field == "model_parameters" else "[]"
                payload[field] = json.loads(payload.get(field) or fallback)
            results.append(payload)
        return results

    def _insert_version(self, version: PromptVersion) -> None:
        self.conn.execute(
            """INSERT INTO prompt_versions
               (prompt_id, version, template, variables, input_schema, output_schema,
                model, temperature, owner, status, change_log, created_at,
                published_at, baseline_metrics)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (version.prompt_id, version.version, version.template, _json(version.variables),
             _json(version.input_schema), _json(version.output_schema), version.model,
             version.temperature, version.owner, version.status, version.change_log,
             version.created_at.isoformat(),
             version.published_at.isoformat() if version.published_at else None,
             _json(version.baseline_metrics)),
        )

    def _deploy(
        self, template: PromptTemplate, target: PromptVersion,
        deployment: PromptDeployment,
    ) -> None:
        now = _now()
        try:
            self.conn.execute("BEGIN")
            self.conn.execute(
                "UPDATE prompt_versions SET status='deprecated' WHERE prompt_id=? AND status='published'",
                (template.prompt_id,),
            )
            self.conn.execute(
                "UPDATE prompt_versions SET status='published', published_at=? WHERE prompt_id=? AND version=?",
                (now, template.prompt_id, target.version),
            )
            self.conn.execute(
                """UPDATE prompt_templates SET status='published', published_version=?,
                   published_at=? WHERE prompt_id=?""",
                (target.version, now, template.prompt_id),
            )
            self.conn.execute(
                """INSERT INTO prompt_deployments
                   (deployment_id, prompt_id, version, action, previous_version,
                    environment, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (deployment.deployment_id, deployment.prompt_id, deployment.version,
                 deployment.action, deployment.previous_version, deployment.environment,
                 deployment.created_at.isoformat()),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise


def ensure_prompt_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS prompt_templates (
            prompt_id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            business_scene TEXT NOT NULL, owner TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft', current_version INTEGER NOT NULL DEFAULT 1,
            published_version INTEGER, created_at TEXT NOT NULL, published_at TEXT
        );
        CREATE TABLE IF NOT EXISTS prompt_versions (
            prompt_id TEXT NOT NULL, version INTEGER NOT NULL, template TEXT NOT NULL,
            variables TEXT NOT NULL DEFAULT '[]', input_schema TEXT NOT NULL,
            output_schema TEXT NOT NULL, model TEXT NOT NULL, temperature REAL NOT NULL,
            owner TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', change_log TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, published_at TEXT, baseline_metrics TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(prompt_id, version),
            FOREIGN KEY(prompt_id) REFERENCES prompt_templates(prompt_id)
        );
        CREATE TABLE IF NOT EXISTS prompt_deployments (
            deployment_id TEXT PRIMARY KEY, prompt_id TEXT NOT NULL, version INTEGER NOT NULL,
            action TEXT NOT NULL, previous_version INTEGER, environment TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(prompt_id) REFERENCES prompt_templates(prompt_id)
        );
        CREATE TABLE IF NOT EXISTS prompt_evaluation_results (
            evaluation_id TEXT PRIMARY KEY, prompt_id TEXT NOT NULL,
            version_a INTEGER NOT NULL, version_b INTEGER NOT NULL,
            test_case_count INTEGER NOT NULL, metrics_a TEXT NOT NULL,
            metrics_b TEXT NOT NULL, metric_delta TEXT NOT NULL, created_at TEXT NOT NULL,
            FOREIGN KEY(prompt_id) REFERENCES prompt_templates(prompt_id)
        );
        CREATE TABLE IF NOT EXISTS llm_call_traces (
            trace_id TEXT PRIMARY KEY, run_id TEXT NOT NULL DEFAULT '', user_id TEXT NOT NULL DEFAULT '',
            business_scene TEXT NOT NULL DEFAULT '', prompt_id TEXT NOT NULL,
            prompt_version INTEGER NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
            model_parameters TEXT NOT NULL DEFAULT '{}', input_hash TEXT NOT NULL DEFAULT '',
            retrieved_document_ids TEXT NOT NULL DEFAULT '[]', retrieved_chunk_ids TEXT NOT NULL DEFAULT '[]',
            tool_calls TEXT NOT NULL DEFAULT '[]', latency_ms REAL NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
            estimated_cost REAL NOT NULL DEFAULT 0, output_schema_valid INTEGER NOT NULL DEFAULT 0,
            fallback_used INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
            validation_error TEXT NOT NULL DEFAULT '', error_type TEXT NOT NULL DEFAULT '',
            review_decision TEXT NOT NULL DEFAULT '', review_feedback TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_prompt_scene ON prompt_templates(business_scene, status);
        CREATE INDEX IF NOT EXISTS idx_prompt_deployments ON prompt_deployments(prompt_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_llm_call_prompt ON llm_call_traces(prompt_id, prompt_version, created_at);
        """
    )
    _ensure_trace_columns(conn)
    conn.commit()


def _ensure_trace_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(llm_call_traces)").fetchall()}
    columns = {
        "run_id": "TEXT NOT NULL DEFAULT ''", "user_id": "TEXT NOT NULL DEFAULT ''",
        "business_scene": "TEXT NOT NULL DEFAULT ''", "model_parameters": "TEXT NOT NULL DEFAULT '{}'",
        "input_hash": "TEXT NOT NULL DEFAULT ''", "retrieved_document_ids": "TEXT NOT NULL DEFAULT '[]'",
        "retrieved_chunk_ids": "TEXT NOT NULL DEFAULT '[]'", "tool_calls": "TEXT NOT NULL DEFAULT '[]'",
        "latency_ms": "REAL NOT NULL DEFAULT 0", "input_tokens": "INTEGER NOT NULL DEFAULT 0",
        "output_tokens": "INTEGER NOT NULL DEFAULT 0", "estimated_cost": "REAL NOT NULL DEFAULT 0",
        "output_schema_valid": "INTEGER NOT NULL DEFAULT 0", "fallback_used": "INTEGER NOT NULL DEFAULT 0",
        "error_type": "TEXT NOT NULL DEFAULT ''", "review_decision": "TEXT NOT NULL DEFAULT ''",
        "review_feedback": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE llm_call_traces ADD COLUMN {name} {definition}")


def _evaluate_prompt(version: PromptVersion, cases: list[dict[str, Any]]) -> dict[str, float]:
    if not cases:
        return {"render_success_rate": 0.0, "expected_token_hit_rate": 0.0}
    rendered = 0
    token_hits = 0
    token_total = 0
    for case in cases:
        try:
            text = version.template.format_map(_StrictVariables(case.get("variables", {})))
            rendered += 1
            expected = [str(item) for item in case.get("expected_tokens", [])]
            token_total += len(expected)
            token_hits += sum(token in text for token in expected)
        except (KeyError, ValueError):
            continue
    return {
        "render_success_rate": round(rendered / len(cases), 6),
        "expected_token_hit_rate": round(token_hits / token_total, 6) if token_total else 1.0,
    }


class _StrictVariables(dict):
    def __missing__(self, key: str) -> Any:
        raise KeyError(key)


def _decode_template(row: sqlite3.Row) -> PromptTemplate:
    return PromptTemplate(**dict(row))


def _decode_version(row: sqlite3.Row) -> PromptVersion:
    payload = dict(row)
    for field in ("variables", "input_schema", "output_schema", "baseline_metrics"):
        payload[field] = json.loads(payload[field])
    return PromptVersion(**payload)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
