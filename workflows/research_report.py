"""Allowlisted PostgreSQL research workflow with server-owned review identity."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.principal import Principal
from app.db.models import (
    LLMCallTrace,
    PublishedReport,
    ReviewDecision,
    ReviewTask,
    WorkflowRun,
    WorkflowStep,
)
from app.db.repositories.governance import WorkflowRepository
from app.services.research_knowledge import PostgresKnowledgeService
from config import settings
from services.financial_analysis import analyze_portfolio_with_llm
from time_utils import utc_now

WORKFLOW_NODES = [
    "validate_input",
    "load_portfolio",
    "calculate_risk",
    "retrieve_evidence",
    "generate_draft",
    "validate_numbers",
    "validate_citations",
    "run_compliance_rules",
    "request_human_review",
    "approve_or_reject",
    "publish_report",
]
ALLOWLISTED_TOOLS = frozenset(WORKFLOW_NODES)
SHADOW_TRADING_TOOLS = frozenset({"shadow_trade", "shadow_agent", "execute_trade", "auto_trade"})
FORBIDDEN_EXPRESSIONS = (
    "立即买入",
    "立即卖出",
    "自动交易",
    "保证收益",
    "稳赚",
    "guaranteed return",
    "trade now",
    "execute trade",
    "auto trading",
)


class WorkflowLimitError(RuntimeError):
    pass


class ResearchReportWorkflow:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = WorkflowRepository(session)

    async def start(self, payload: dict[str, Any], *, principal: Principal) -> dict[str, Any]:
        business_scene = str(payload.get("business_scene") or "public_fund_research_report")
        if business_scene != "public_fund_research_report":
            raise ValueError("Unsupported business_scene")
        supplied_key = str(payload.get("idempotency_key") or "").strip()
        idempotency_key = supplied_key or f"generated:{uuid.uuid4()}"
        sanitized_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"user_id", "permission_groups", "reviewer_id"}
        }
        context = {
            "request": {
                **sanitized_payload,
                "user_id": principal.user_id,
                "permission_groups": sorted(principal.permission_groups),
            },
            "permission_groups": sorted(principal.permission_groups),
            "language": payload.get("language", "zh"),
        }
        run_id = uuid.uuid4()
        statement = (
            insert(WorkflowRun)
            .values(
                id=run_id,
                user_id=principal.user_id,
                business_scene=business_scene,
                idempotency_key=idempotency_key,
                status="DRAFT",
                max_steps=max(1, int(payload.get("max_steps", 30))),
                timeout_seconds=max(1, int(payload.get("timeout_seconds", 120))),
                node_timeout_seconds=max(1, int(payload.get("node_timeout_seconds", 45))),
                cost_budget=Decimal(str(payload.get("cost_budget", "0.20"))),
                cost_amount=Decimal("0"),
                cost_is_estimated=False,
                current_step="",
                iteration=1,
                context_json=context,
                started_at=utc_now(),
                error_type="",
                code_version=settings.CODE_VERSION,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    WorkflowRun.user_id,
                    WorkflowRun.business_scene,
                    WorkflowRun.idempotency_key,
                ]
            )
            .returning(WorkflowRun)
        )
        run = (await self.session.execute(statement)).scalar_one_or_none()
        if run is None:
            existing = await self.repository.idempotent_run(
                principal.user_id, business_scene, idempotency_key
            )
            if existing is None:
                raise RuntimeError("Idempotent workflow insert did not return a run")
            result = await self.get_run(existing.id) or {}
            result["idempotent_replay"] = bool(supplied_key)
            return result

        await self._execute_until_review(run.id)
        result = await self.get_run(run.id) or {}
        result["idempotent_replay"] = False
        return result

    async def _execute_until_review(
        self,
        run_id: uuid.UUID,
        *,
        revision_feedback: str = "",
    ) -> None:
        run = await self._require_run(run_id)
        context = dict(run.context_json)
        if revision_feedback:
            context["revision_feedback"] = revision_feedback
        start_at = "generate_draft" if revision_feedback else "validate_input"
        nodes = WORKFLOW_NODES[
            WORKFLOW_NODES.index(start_at) : WORKFLOW_NODES.index("request_human_review") + 1
        ]
        run.status = "RUNNING"
        try:
            async with asyncio.timeout(run.timeout_seconds):
                for node in nodes:
                    await self._check_limits(run)
                    context = await self._run_step(run, node, context)
                    if run.status == "FAILED":
                        return
        except TimeoutError:
            running_step = await self.session.scalar(
                select(WorkflowStep).where(
                    WorkflowStep.workflow_run_id == run.id,
                    WorkflowStep.status == "RUNNING",
                )
            )
            if running_step is not None:
                running_step.status = "FAILED"
                running_step.completed_at = utc_now()
                running_step.error_type = "WorkflowTimeoutError"
                running_step.output_summary = {"error": "workflow timeout exceeded"}
            run.status = "FAILED"
            run.error_type = "WorkflowTimeoutError"
            run.completed_at = utc_now()
            await self.session.flush()
            return
        except WorkflowLimitError as exc:
            run.status = "FAILED"
            run.error_type = type(exc).__name__
            run.completed_at = utc_now()
            context["workflow_error"] = str(exc)
            run.context_json = context
            await self.session.flush()
            return
        run.context_json = context
        run.status = "PENDING_REVIEW"
        run.current_step = "request_human_review"
        await self.session.flush()

    async def decide(
        self,
        review_id: uuid.UUID,
        decision: str,
        *,
        principal: Principal,
        feedback: str = "",
    ) -> dict[str, Any] | None:
        principal.require_group("research_reviewer")
        if decision not in {"approve", "reject", "request_changes"}:
            raise ValueError("Unsupported review decision")
        task = await self.repository.get_review(review_id, for_update=True)
        if task is None:
            return None
        existing = await self.session.scalar(
            select(ReviewDecision).where(
                ReviewDecision.review_task_id == review_id,
                ReviewDecision.decision == decision,
                ReviewDecision.reviewer_id == principal.user_id,
            )
        )
        if existing:
            return await self.get_run(task.workflow_run_id)
        if task.status != "PENDING":
            raise ValueError("Review task is already completed")

        status = {
            "approve": "APPROVED",
            "reject": "REJECTED",
            "request_changes": "CHANGES_REQUESTED",
        }[decision]
        task.status = status
        task.completed_at = utc_now()
        self.session.add(
            ReviewDecision(
                review_task_id=task.id,
                workflow_run_id=task.workflow_run_id,
                decision=decision,
                reviewer_id=principal.user_id,
                feedback=feedback,
            )
        )
        await self.session.execute(
            update(LLMCallTrace)
            .where(LLMCallTrace.workflow_run_id == task.workflow_run_id)
            .values(review_decision=decision, review_feedback=feedback)
        )
        run = await self._require_run(task.workflow_run_id)
        context = dict(run.context_json)
        context["review_decision"] = decision
        context["review_feedback"] = feedback
        context["reviewer_id"] = principal.user_id
        run.context_json = context
        context = await self._run_step(run, "approve_or_reject", context)
        if run.status == "FAILED":
            return await self.get_run(run.id)
        if decision == "approve":
            run.status = "APPROVED"
            context = await self._run_step(run, "publish_report", context)
            if run.status != "FAILED":
                run.status = "PUBLISHED"
                run.current_step = "publish_report"
                run.completed_at = utc_now()
        elif decision == "reject":
            run.status = "REJECTED"
            run.current_step = "approve_or_reject"
            run.completed_at = utc_now()
        else:
            run.status = "DRAFT"
            run.iteration += 1
            await self.session.flush()
            await self._execute_until_review(run.id, revision_feedback=feedback)
        run.context_json = context if decision != "request_changes" else run.context_json
        await self.session.flush()
        return await self.get_run(run.id)

    async def get_run(self, run_id: uuid.UUID) -> dict[str, Any] | None:
        run = await self.repository.get_run(run_id)
        if run is None:
            return None
        steps = await self.repository.run_steps(run_id)
        reviews = await self.repository.run_reviews(run_id)
        report = await self.repository.report_for_run(run_id)
        return {
            "run_id": str(run.id),
            "user_id": run.user_id,
            "business_scene": run.business_scene,
            "status": run.status,
            "max_steps": run.max_steps,
            "timeout_seconds": run.timeout_seconds,
            "node_timeout_seconds": run.node_timeout_seconds,
            "cost_budget": float(run.cost_budget),
            "cost_amount": float(run.cost_amount),
            "cost_is_estimated": run.cost_is_estimated,
            "estimated_cost": float(run.cost_amount),
            "current_step": run.current_step,
            "iteration": run.iteration,
            "created_at": run.created_at.isoformat(),
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "error_type": run.error_type,
            "steps": [
                {
                    "step_id": str(item.id),
                    "run_id": str(item.workflow_run_id),
                    "step_name": item.step_name,
                    "iteration": item.iteration,
                    "status": item.status,
                    "input_summary": item.input_summary,
                    "output_summary": item.output_summary,
                    "started_at": item.started_at.isoformat(),
                    "completed_at": item.completed_at.isoformat() if item.completed_at else None,
                    "error_type": item.error_type,
                }
                for item in steps
            ],
            "review_tasks": [
                {
                    "review_id": str(item.id),
                    "run_id": str(item.workflow_run_id),
                    "status": item.status,
                    "assigned_to": item.assigned_group,
                    "created_at": item.created_at.isoformat(),
                    "completed_at": item.completed_at.isoformat() if item.completed_at else None,
                }
                for item in reviews
            ],
            "report_id": str(report.id) if report else None,
        }

    async def get_report(
        self,
        report_id: uuid.UUID,
        *,
        principal: Principal,
    ) -> dict[str, Any] | None:
        report = await self.repository.get_report(report_id)
        if report is None:
            return None
        run = await self.repository.get_run(report.workflow_run_id)
        if run is None or (
            run.user_id != principal.user_id
            and not principal.has_group("research_reviewer")
        ):
            return None
        return {
            "report_id": str(report.id),
            "run_id": str(report.workflow_run_id),
            "report": report.report_json,
            "published_by": report.published_by,
            "published_at": report.published_at.isoformat(),
        }

    async def _run_step(
        self,
        run: WorkflowRun,
        node: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if node not in ALLOWLISTED_TOOLS or (
            settings.fund_research_mode and node in SHADOW_TRADING_TOOLS
        ):
            raise PermissionError(f"Tool is not allowlisted: {node}")
        existing = await self.session.scalar(
            select(WorkflowStep).where(
                WorkflowStep.workflow_run_id == run.id,
                WorkflowStep.step_name == node,
                WorkflowStep.iteration == run.iteration,
                WorkflowStep.status == "COMPLETED",
            )
        )
        if existing:
            return context
        step = WorkflowStep(
            workflow_run_id=run.id,
            step_name=node,
            iteration=run.iteration,
            status="RUNNING",
            input_summary=_summarize_context(context),
            output_summary={},
            started_at=utc_now(),
            error_type="",
        )
        self.session.add(step)
        run.current_step = node
        await self.session.flush()
        try:
            async with asyncio.timeout(run.node_timeout_seconds):
                context = await self._execute_node(node, run, context)
            step.status = "COMPLETED"
            step.output_summary = _summarize_context(context)
            step.completed_at = utc_now()
            run.context_json = context
            await self.session.flush()
            return context
        except Exception as exc:
            step.status = "FAILED"
            step.completed_at = utc_now()
            step.error_type = "NodeTimeoutError" if isinstance(exc, TimeoutError) else type(exc).__name__
            step.output_summary = {"error": str(exc)[:500]}
            run.status = "FAILED"
            run.error_type = step.error_type
            run.completed_at = utc_now()
            await self.session.flush()
            return context

    async def _execute_node(
        self,
        node: str,
        run: WorkflowRun,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        request = context["request"]
        if node == "validate_input":
            context["input_valid"] = bool(run.user_id)
        elif node == "load_portfolio":
            db_portfolio = request.get("_db_portfolio") or {}
            if not db_portfolio.get("valuation_snapshot_id"):
                raise ValueError("No PostgreSQL valuation snapshot available")
            context["portfolio_tickers"] = list(db_portfolio.get("tickers") or [])
            context["portfolio_lineage"] = {
                key: db_portfolio.get(key)
                for key in ("portfolio_id", "valuation_snapshot_id", "valuation_input_hash", "as_of")
            }
        elif node == "calculate_risk":
            risk_summary = (request.get("_db_portfolio") or {}).get("risk_summary")
            if not isinstance(risk_summary, dict):
                raise ValueError("Database-backed risk summary is required")
            context["risk_summary"] = risk_summary
        elif node == "retrieve_evidence":
            query = str(request.get("query") or "public fund portfolio risk policy evidence")
            principal = Principal(run.user_id, frozenset(context["permission_groups"]))
            result = await PostgresKnowledgeService(self.session).retrieve_with_status(
                query,
                top_k=int(request.get("top_k", 5)),
                principal=principal,
            )
            context["evidence"] = result.get("citations", [])
            context["evidence_insufficient"] = result.get("evidence_insufficient", True)
        elif node == "generate_draft":
            risk_input = dict(context["risk_summary"])
            if context.get("revision_feedback"):
                risk_input["review_feedback"] = context["revision_feedback"]
            draft = await analyze_portfolio_with_llm(
                risk_input,
                context.get("evidence", []),
                language=context["language"],
                run_id=str(run.id),
                user_id=run.user_id,
                business_scene=run.business_scene,
                session=self.session,
            )
            context["draft"] = draft
            trace = await self.session.scalar(
                select(LLMCallTrace).where(LLMCallTrace.trace_key == draft.get("llm_trace_id"))
            )
            total_cost, trace_count, non_provider_cost_count = (
                await self.session.execute(
                    select(
                        func.coalesce(func.sum(LLMCallTrace.cost_amount), 0),
                        func.count(LLMCallTrace.id),
                        func.count(LLMCallTrace.id).filter(
                            LLMCallTrace.cost_source != "provider"
                        ),
                    ).where(LLMCallTrace.workflow_run_id == run.id)
                )
            ).one()
            run.cost_amount, run.cost_is_estimated = _workflow_cost_summary(
                total_cost,
                trace_count,
                non_provider_cost_count,
            )
            context["llm_usage_source"] = trace.usage_source if trace else "estimated"
            context["llm_cost_source"] = trace.cost_source if trace else "estimated_from_text"
        elif node == "validate_numbers":
            from prompts.financial_analysis_models import validate_financial_analysis_output

            validate_financial_analysis_output(
                _contract_fields(context["draft"]),
                portfolio_risk_summary=context["risk_summary"],
                evidence=context.get("evidence", []),
            )
            context["numbers_valid"] = True
        elif node == "validate_citations":
            allowed = {
                (item["document_id"], item["chunk_id"])
                for item in context.get("evidence", [])
            }
            used = {
                (item["document_id"], item["chunk_id"])
                for item in context["draft"].get("evidence_used", [])
            }
            if not used.issubset(allowed):
                raise ValueError("Draft contains ungrounded citations")
            context["citations_valid"] = True
            groups = set(context["permission_groups"])
            context["permissions_valid"] = all(
                item.get("permission_level", "public") == "public"
                or bool(groups.intersection(item.get("permission_groups", [])))
                for item in context.get("evidence", [])
            )
            if not context["permissions_valid"]:
                raise PermissionError("Citation permission validation failed")
        elif node == "run_compliance_rules":
            raw = json.dumps(context["draft"], ensure_ascii=False).lower()
            matches = [item for item in FORBIDDEN_EXPRESSIONS if item.lower() in raw]
            if matches:
                raise ValueError(f"Forbidden expressions: {', '.join(matches)}")
            context["rules_valid"] = True
        elif node == "request_human_review":
            review = ReviewTask(
                workflow_run_id=run.id,
                status="PENDING",
                assigned_group="research_reviewer",
            )
            self.session.add(review)
            await self.session.flush()
            context["review_id"] = str(review.id)
        elif node == "approve_or_reject":
            if context.get("review_decision") not in {"approve", "reject", "request_changes"}:
                raise ValueError("Human review decision is required")
        elif node == "publish_report":
            if context.get("review_decision") != "approve":
                raise PermissionError("Human approval is required")
            if not all(
                context.get(flag)
                for flag in ("numbers_valid", "citations_valid", "permissions_valid", "rules_valid")
            ):
                raise ValueError("Pre-publication controls have not all passed")
            report = PublishedReport(
                workflow_run_id=run.id,
                report_json=context["draft"],
                published_by=str(context["reviewer_id"]),
                published_at=utc_now(),
            )
            self.session.add(report)
            await self.session.flush()
            context["report_id"] = str(report.id)
        return context

    async def _check_limits(self, run: WorkflowRun) -> None:
        count = await self.session.scalar(
            select(func.count(WorkflowStep.id)).where(WorkflowStep.workflow_run_id == run.id)
        )
        if int(count or 0) >= run.max_steps:
            raise WorkflowLimitError("max_steps exceeded")
        if run.cost_amount > run.cost_budget:
            raise WorkflowLimitError("cost budget exceeded")

    async def _require_run(self, run_id: uuid.UUID) -> WorkflowRun:
        run = await self.repository.get_run(run_id)
        if run is None:
            raise ValueError("Workflow run not found")
        return run


def _summarize_context(context: dict[str, Any]) -> dict[str, Any]:
    raw = json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)
    return {
        "keys": sorted(context.keys()),
        "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "size_bytes": len(raw.encode("utf-8")),
        "ticker_count": len(context.get("portfolio_tickers", [])),
        "evidence_count": len(context.get("evidence", [])),
    }


def _workflow_cost_summary(
    total_cost: Any,
    trace_count: Any,
    non_provider_cost_count: Any,
) -> tuple[Decimal, bool]:
    return (
        Decimal(str(total_cost)),
        int(trace_count) == 0 or int(non_provider_cost_count) > 0,
    )


def _contract_fields(payload: dict[str, Any]) -> dict[str, Any]:
    observations = payload.get("research_observations") or [
        {"ticker": item.get("ticker"), "observation": item.get("comment", ""), "evidence_ids": []}
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
        "portfolio_summary",
        "risk_score",
        "main_risks",
        "asset_level_comments",
        "research_observations",
        "review_priorities",
        "rebalance_suggestions",
        "deprecated_fields",
        "evidence_used",
        "disclaimer",
    )
    return {field: normalized[field] for field in fields}
