"""Allowlisted, idempotent public-fund research report workflow."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from config import settings
from rag import PermissionContext, retrieve_evidence_with_status
from services.financial_analysis import analyze_portfolio_with_llm
from workflows.models import ReviewDecision, ReviewTask, WorkflowRun


WORKFLOW_NODES = [
    "validate_input", "load_portfolio", "calculate_risk", "retrieve_evidence",
    "generate_draft", "validate_numbers", "validate_citations", "run_compliance_rules",
    "request_human_review", "approve_or_reject", "publish_report",
]
ALLOWLISTED_TOOLS = frozenset(WORKFLOW_NODES)
SHADOW_TRADING_TOOLS = frozenset({"shadow_trade", "shadow_agent", "execute_trade", "auto_trade"})
FORBIDDEN_EXPRESSIONS = (
    "立即买入", "立即卖出", "自动交易", "保证收益", "稳赚", "guaranteed return",
    "trade now", "execute trade", "auto trading",
)


class WorkflowLimitError(RuntimeError):
    pass


class ResearchReportWorkflow:
    def __init__(self, connection: sqlite3.Connection | None = None):
        if connection is None:
            from database import _get_conn
            connection = _get_conn()
        self.conn = connection
        self.conn.row_factory = sqlite3.Row
        ensure_workflow_schema(self.conn)

    async def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        if idempotency_key:
            existing = self.conn.execute(
                "SELECT run_id FROM workflow_runs WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if existing:
                result = self.get_run(existing["run_id"]) or {}
                result["idempotent_replay"] = True
                return result
        run_id = str(uuid.uuid4())
        now = _now()
        context = {
            "request": payload,
            "permission_groups": payload.get("permission_groups") or ["public"],
            "language": payload.get("language", "zh"),
        }
        self.conn.execute(
            """INSERT INTO workflow_runs
               (run_id, idempotency_key, user_id, business_scene, status, max_steps,
                timeout_seconds, cost_budget, estimated_cost, current_step, iteration,
                context_json, created_at, started_at)
               VALUES (?, ?, ?, 'public_fund_research_report', 'DRAFT', ?, ?, ?, 0, '', 1, ?, ?, ?)""",
            (run_id, idempotency_key or None, str(payload.get("user_id") or "anonymous"),
             int(payload.get("max_steps", 30)), int(payload.get("timeout_seconds", 120)),
             float(payload.get("cost_budget", 0.20)), _json(context), now, now),
        )
        self.conn.commit()
        await self._execute_until_review(run_id)
        result = self.get_run(run_id) or {}
        result["idempotent_replay"] = False
        return result

    async def _execute_until_review(self, run_id: str, *, revision_feedback: str = "") -> None:
        context = self._context(run_id)
        if revision_feedback:
            context["revision_feedback"] = revision_feedback
        iteration = int(self._run_row(run_id)["iteration"])
        start_at = "generate_draft" if revision_feedback else "validate_input"
        nodes = WORKFLOW_NODES[WORKFLOW_NODES.index(start_at):WORKFLOW_NODES.index("request_human_review") + 1]
        self._set_run(run_id, status="RUNNING")
        started = time.monotonic()
        try:
            for node in nodes:
                self._check_limits(run_id, started)
                context = await self._run_step(run_id, node, iteration, context)
                if self._run_row(run_id)["status"] == "FAILED":
                    return
        except WorkflowLimitError as exc:
            self._set_run(
                run_id, status="FAILED", error_type=type(exc).__name__, completed_at=_now(),
            )
            return
        self._save_context(run_id, context)
        self._set_run(run_id, status="PENDING_REVIEW", current_step="request_human_review")

    async def decide(
        self, review_id: str, decision: str, *, reviewer_id: str, feedback: str = "",
    ) -> dict[str, Any] | None:
        task = self.conn.execute("SELECT * FROM review_tasks WHERE review_id=?", (review_id,)).fetchone()
        if not task:
            return None
        run_id = task["run_id"]
        existing = self.conn.execute(
            "SELECT * FROM review_decisions WHERE review_id=? AND decision=? AND feedback=?",
            (review_id, decision, feedback),
        ).fetchone()
        if existing:
            return self.get_run(run_id)
        if task["status"] != "PENDING":
            raise ValueError("Review task is already completed")
        if decision not in {"approve", "reject", "request_changes"}:
            raise ValueError("Unsupported review decision")
        now = _now()
        status = {"approve": "APPROVED", "reject": "REJECTED", "request_changes": "CHANGES_REQUESTED"}[decision]
        decision_model = ReviewDecision(
            decision_id=str(uuid.uuid4()), review_id=review_id, run_id=run_id,
            decision=decision, reviewer_id=reviewer_id, feedback=feedback,
        )
        self.conn.execute(
            "UPDATE review_tasks SET status=?, completed_at=? WHERE review_id=?",
            (status, now, review_id),
        )
        self.conn.execute(
            """INSERT INTO review_decisions
               (decision_id, review_id, run_id, decision, reviewer_id, feedback, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (decision_model.decision_id, review_id, run_id, decision, reviewer_id, feedback, now),
        )
        self.conn.commit()
        from prompts.registry import PromptRegistry
        PromptRegistry(self.conn).update_trace_review(run_id, decision, feedback)
        context = self._context(run_id)
        context["review_decision"] = decision
        context["review_feedback"] = feedback
        self._save_context(run_id, context)
        context = await self._run_step(run_id, "approve_or_reject", int(self._run_row(run_id)["iteration"]), context)
        if self._run_row(run_id)["status"] == "FAILED":
            return self.get_run(run_id)
        if decision == "approve":
            self._set_run(run_id, status="APPROVED")
            await self._run_step(run_id, "publish_report", int(self._run_row(run_id)["iteration"]), context)
            if self._run_row(run_id)["status"] != "FAILED":
                self._set_run(run_id, status="PUBLISHED", current_step="publish_report", completed_at=now)
        elif decision == "reject":
            self._set_run(run_id, status="REJECTED", current_step="approve_or_reject", completed_at=now)
        else:
            next_iteration = int(self._run_row(run_id)["iteration"]) + 1
            self.conn.execute(
                "UPDATE workflow_runs SET status='DRAFT', iteration=? WHERE run_id=?",
                (next_iteration, run_id),
            )
            self.conn.commit()
            await self._execute_until_review(run_id, revision_feedback=feedback)
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result.pop("context_json", None)
        result["steps"] = [
            _decode_json_fields(dict(item), "input_summary", "output_summary")
            for item in self.conn.execute(
                "SELECT * FROM workflow_steps WHERE run_id=? ORDER BY started_at", (run_id,)
            ).fetchall()
        ]
        reviews = [dict(item) for item in self.conn.execute(
            "SELECT * FROM review_tasks WHERE run_id=? ORDER BY created_at", (run_id,)
        ).fetchall()]
        result["review_tasks"] = reviews
        report = self.conn.execute("SELECT report_id FROM published_reports WHERE run_id=?", (run_id,)).fetchone()
        result["report_id"] = report["report_id"] if report else None
        return result

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM published_reports WHERE report_id=?", (report_id,)).fetchone()
        if not row:
            return None
        payload = dict(row)
        payload["report"] = json.loads(payload.pop("report_json"))
        return payload

    async def _run_step(
        self, run_id: str, node: str, iteration: int, context: dict[str, Any],
    ) -> dict[str, Any]:
        if node not in ALLOWLISTED_TOOLS or (settings.fund_research_mode and node in SHADOW_TRADING_TOOLS):
            raise PermissionError(f"Tool is not allowlisted: {node}")
        existing = self.conn.execute(
            "SELECT * FROM workflow_steps WHERE run_id=? AND step_name=? AND iteration=? AND status='COMPLETED'",
            (run_id, node, iteration),
        ).fetchone()
        if existing:
            return context
        step_id = str(uuid.uuid4())
        now = _now()
        input_summary = _summarize_context(context)
        self.conn.execute(
            """INSERT OR REPLACE INTO workflow_steps
               (step_id, run_id, step_name, iteration, status, input_summary,
                output_summary, started_at, completed_at, error_type)
               VALUES (?, ?, ?, ?, 'RUNNING', ?, '{}', ?, NULL, '')""",
            (step_id, run_id, node, iteration, _json(input_summary), now),
        )
        self._set_run(run_id, current_step=node)
        try:
            context = await self._execute_node(node, run_id, context)
            self.conn.execute(
                """UPDATE workflow_steps SET status='COMPLETED', output_summary=?, completed_at=?
                   WHERE step_id=?""",
                (_json(_summarize_context(context)), _now(), step_id),
            )
            self.conn.commit()
            self._save_context(run_id, context)
            return context
        except Exception as exc:
            error_type = type(exc).__name__
            self.conn.execute(
                """UPDATE workflow_steps SET status='FAILED', completed_at=?, error_type=?,
                   output_summary=? WHERE step_id=?""",
                (_now(), error_type, _json({"error": str(exc)[:500]}), step_id),
            )
            self.conn.commit()
            self._set_run(run_id, status="FAILED", error_type=error_type, completed_at=_now())
            return context

    async def _execute_node(self, node: str, run_id: str, context: dict[str, Any]) -> dict[str, Any]:
        if node == "validate_input":
            if not context["request"].get("user_id"):
                raise ValueError("user_id is required")
            context["input_valid"] = True
        elif node == "load_portfolio":
            db_portfolio = context["request"].get("_db_portfolio") or {}
            if not db_portfolio.get("valuation_snapshot_id"):
                raise ValueError("No PostgreSQL valuation snapshot available")
            context["portfolio_tickers"] = list(db_portfolio.get("tickers") or [])
            context["portfolio_lineage"] = {
                key: db_portfolio.get(key)
                for key in (
                    "portfolio_id",
                    "valuation_snapshot_id",
                    "valuation_input_hash",
                    "as_of",
                )
            }
        elif node == "calculate_risk":
            db_portfolio = context["request"].get("_db_portfolio") or {}
            risk_summary = db_portfolio.get("risk_summary")
            if not isinstance(risk_summary, dict):
                raise ValueError("Database-backed risk summary is required")
            context["risk_summary"] = risk_summary
        elif node == "retrieve_evidence":
            query = str(context["request"].get("query") or "public fund portfolio risk policy evidence")
            result = retrieve_evidence_with_status(
                query, top_k=int(context["request"].get("top_k", 5)),
                permission_context=PermissionContext(
                    user_id=str(context["request"]["user_id"]),
                    permission_groups=context["permission_groups"],
                ),
            )
            context["evidence"] = result.get("citations", [])
            context["evidence_insufficient"] = result.get("evidence_insufficient", True)
        elif node == "generate_draft":
            risk_input = dict(context["risk_summary"])
            if context.get("revision_feedback"):
                risk_input["review_feedback"] = context["revision_feedback"]
            draft = await analyze_portfolio_with_llm(
                risk_input, context.get("evidence", []), language=context["language"],
                run_id=run_id, user_id=str(context["request"]["user_id"]),
                business_scene="public_fund_research_report",
            )
            context["draft"] = draft
            estimated = 0.0 if draft.get("source") in {"mock", "fallback"} else 0.01
            self.conn.execute(
                "UPDATE workflow_runs SET estimated_cost=estimated_cost+? WHERE run_id=?",
                (estimated, run_id),
            )
            self.conn.commit()
        elif node == "validate_numbers":
            from prompts.financial_analysis_models import validate_financial_analysis_output
            validate_financial_analysis_output(
                _contract_fields(context["draft"]),
                portfolio_risk_summary=context["risk_summary"],
                evidence=context.get("evidence", []),
            )
            context["numbers_valid"] = True
        elif node == "validate_citations":
            allowed = {(item["document_id"], item["chunk_id"]) for item in context.get("evidence", [])}
            used = {(item["document_id"], item["chunk_id"]) for item in context["draft"].get("evidence_used", [])}
            if not used.issubset(allowed):
                raise ValueError("Draft contains ungrounded citations")
            context["citations_valid"] = True
            context["permissions_valid"] = all(
                item.get("permission_level", "public") == "public"
                or bool(set(context["permission_groups"]) - {"public"})
                for item in context.get("evidence", [])
            )
            if not context["permissions_valid"]:
                raise PermissionError("Citation permission validation failed")
        elif node == "run_compliance_rules":
            text = json.dumps(context["draft"], ensure_ascii=False).lower()
            matches = [expression for expression in FORBIDDEN_EXPRESSIONS if expression.lower() in text]
            if matches:
                raise ValueError(f"Forbidden expressions: {', '.join(matches)}")
            context["rules_valid"] = True
        elif node == "request_human_review":
            review = ReviewTask(review_id=str(uuid.uuid4()), run_id=run_id)
            self.conn.execute(
                """INSERT INTO review_tasks
                   (review_id, run_id, status, assigned_to, created_at, completed_at)
                   VALUES (?, ?, 'PENDING', ?, ?, NULL)""",
                (review.review_id, run_id, review.assigned_to, review.created_at.isoformat()),
            )
            self.conn.commit()
            context["review_id"] = review.review_id
        elif node == "approve_or_reject":
            if context.get("review_decision") not in {"approve", "reject", "request_changes"}:
                raise ValueError("Human review decision is required")
        elif node == "publish_report":
            if context.get("review_decision") != "approve":
                raise PermissionError("Human approval is required")
            required = ("numbers_valid", "citations_valid", "permissions_valid", "rules_valid")
            if not all(context.get(flag) for flag in required):
                raise ValueError("Pre-publication controls have not all passed")
            report_id = str(uuid.uuid4())
            self.conn.execute(
                """INSERT INTO published_reports
                   (report_id, run_id, report_json, published_by, published_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (report_id, run_id, _json(context["draft"]),
                 str(context["request"]["user_id"]), _now()),
            )
            self.conn.commit()
            context["report_id"] = report_id
        return context

    def _check_limits(self, run_id: str, started: float) -> None:
        run = self._run_row(run_id)
        steps = self.conn.execute("SELECT COUNT(*) FROM workflow_steps WHERE run_id=?", (run_id,)).fetchone()[0]
        if steps >= int(run["max_steps"]):
            raise WorkflowLimitError("max_steps exceeded")
        if time.monotonic() - started > int(run["timeout_seconds"]):
            raise WorkflowLimitError("workflow timeout exceeded")
        if float(run["estimated_cost"]) > float(run["cost_budget"]):
            raise WorkflowLimitError("cost budget exceeded")

    def _run_row(self, run_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError("Workflow run not found")
        return row

    def _context(self, run_id: str) -> dict[str, Any]:
        return json.loads(self._run_row(run_id)["context_json"])

    def _save_context(self, run_id: str, context: dict[str, Any]) -> None:
        self.conn.execute("UPDATE workflow_runs SET context_json=? WHERE run_id=?", (_json(context), run_id))
        self.conn.commit()

    def _set_run(self, run_id: str, **updates: Any) -> None:
        if not updates:
            return
        columns = ", ".join(f"{key}=?" for key in updates)
        self.conn.execute(f"UPDATE workflow_runs SET {columns} WHERE run_id=?", [*updates.values(), run_id])
        self.conn.commit()


def ensure_workflow_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS workflow_runs (
            run_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, user_id TEXT NOT NULL,
            business_scene TEXT NOT NULL, status TEXT NOT NULL, max_steps INTEGER NOT NULL,
            timeout_seconds INTEGER NOT NULL, cost_budget REAL NOT NULL, estimated_cost REAL NOT NULL DEFAULT 0,
            current_step TEXT NOT NULL DEFAULT '', iteration INTEGER NOT NULL DEFAULT 1,
            context_json TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT,
            completed_at TEXT, error_type TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS workflow_steps (
            step_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, step_name TEXT NOT NULL,
            iteration INTEGER NOT NULL, status TEXT NOT NULL, input_summary TEXT NOT NULL,
            output_summary TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT,
            error_type TEXT NOT NULL DEFAULT '', UNIQUE(run_id, step_name, iteration),
            FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id)
        );
        CREATE TABLE IF NOT EXISTS review_tasks (
            review_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, status TEXT NOT NULL,
            assigned_to TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT,
            FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id)
        );
        CREATE TABLE IF NOT EXISTS review_decisions (
            decision_id TEXT PRIMARY KEY, review_id TEXT NOT NULL, run_id TEXT NOT NULL,
            decision TEXT NOT NULL, reviewer_id TEXT NOT NULL, feedback TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, FOREIGN KEY(review_id) REFERENCES review_tasks(review_id)
        );
        CREATE TABLE IF NOT EXISTS published_reports (
            report_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE, report_json TEXT NOT NULL,
            published_by TEXT NOT NULL, published_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_workflow_status ON workflow_runs(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_review_run ON review_tasks(run_id, status);
        """
    )
    conn.commit()


def _summarize_context(context: dict[str, Any]) -> dict[str, Any]:
    raw = _json(context)
    return {
        "keys": sorted(context.keys()),
        "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "size_bytes": len(raw.encode("utf-8")),
        "ticker_count": len(context.get("portfolio_tickers", [])),
        "evidence_count": len(context.get("evidence", [])),
    }


def _contract_fields(payload: dict[str, Any]) -> dict[str, Any]:
    observations = payload.get("research_observations") or [
        {
            "ticker": item.get("ticker"),
            "observation": item.get("comment", ""),
            "evidence_ids": [],
        }
        for item in payload.get("asset_level_comments", [])
    ]
    priorities = payload.get("review_priorities") or [
        {
            "priority": "high" if item.get("action") in {"watch", "reduce"} else "medium",
            "ticker": item.get("ticker"),
            "reason": item.get("reason", ""),
        }
        for item in payload.get("rebalance_suggestions", [])
    ]
    normalized = {
        **payload,
        "research_observations": observations,
        "review_priorities": priorities,
        "rebalance_suggestions": payload.get("rebalance_suggestions", []),
        "deprecated_fields": payload.get("deprecated_fields", ["rebalance_suggestions"]),
    }
    fields = (
        "portfolio_summary", "risk_score", "main_risks", "asset_level_comments",
        "research_observations", "review_priorities", "rebalance_suggestions",
        "deprecated_fields", "evidence_used", "disclaimer",
    )
    return {field: normalized[field] for field in fields}


def _decode_json_fields(payload: dict[str, Any], *fields: str) -> dict[str, Any]:
    for field in fields:
        payload[field] = json.loads(payload[field])
    return payload


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
