"""Allowlisted PostgreSQL research workflow with server-owned review identity."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.principal import Principal, require_tenant_access
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


class WorkflowNodeExecutionError(RuntimeError):
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
            and not key.startswith("_")
        }
        context = {
            "request": {
                **sanitized_payload,
                "user_id": principal.user_id,
                "permission_groups": sorted(principal.permission_groups),
            },
            "permission_groups": sorted(principal.permission_groups),
            "roles": sorted(principal.roles),
            "tenant_id": principal.tenant_id,
            "language": payload.get("language", "zh"),
        }
        run_id = uuid.uuid4()
        statement = (
            insert(WorkflowRun)
            .values(
                id=run_id,
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                business_scene=business_scene,
                idempotency_key=idempotency_key,
                status="PENDING",
                max_steps=max(1, int(payload.get("max_steps", 30))),
                timeout_seconds=max(1, int(payload.get("timeout_seconds", 120))),
                node_timeout_seconds=max(1, int(payload.get("node_timeout_seconds", 45))),
                cost_budget=Decimal(str(payload.get("cost_budget", "0.20"))),
                cost_amount=Decimal("0"),
                cost_is_estimated=False,
                current_step="",
                iteration=1,
                context_json=context,
                started_at=None,
                error_type="",
                code_version=settings.CODE_VERSION,
                lease_owner=None,
                heartbeat_at=None,
                lease_expires_at=None,
                attempt=0,
                next_retry_at=None,
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
            _persist_context(run, context)
            await self.session.flush()
            return
        _persist_context(run, context)
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
        principal.require_role("research_reviewer", "platform_admin")
        if decision not in {"approve", "reject", "request_changes"}:
            raise ValueError("Unsupported review decision")
        task = await self.repository.get_review(review_id, for_update=True)
        if task is None:
            return None
        run = await self._require_run(task.workflow_run_id)
        require_tenant_access(principal, run.tenant_id)
        existing = await self.session.scalar(
            select(ReviewDecision).where(ReviewDecision.review_task_id == review_id)
        )
        if existing:
            raise ValueError("Review task is already completed")
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
        context = dict(run.context_json)
        context["review_decision"] = decision
        context["review_feedback"] = feedback
        context["reviewer_id"] = principal.user_id
        _persist_context(run, context)
        run.status = "PENDING"
        run.current_step = ""
        # ``started_at`` is the start of the current active execution window.
        # Human review can take hours or days and must not consume the worker
        # execution timeout; individual step timestamps retain the audit trail.
        run.started_at = None
        run.completed_at = None
        run.error_type = ""
        run.lease_owner = None
        run.heartbeat_at = None
        run.lease_expires_at = None
        run.next_retry_at = None
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
            "tenant_id": run.tenant_id,
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
            "lease_owner": run.lease_owner,
            "heartbeat_at": run.heartbeat_at.isoformat() if run.heartbeat_at else None,
            "lease_expires_at": (
                run.lease_expires_at.isoformat() if run.lease_expires_at else None
            ),
            "attempt": run.attempt,
            "next_retry_at": run.next_retry_at.isoformat() if run.next_retry_at else None,
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

    async def process_next_node(
        self,
        run_id: uuid.UUID,
        *,
        lease_owner: str,
    ) -> dict[str, Any]:
        """Execute one idempotent node for a run currently owned by a worker."""
        run = await self.repository.get_run(run_id, for_update=True)
        if run is None:
            raise ValueError("Workflow run not found")
        if run.status != "RUNNING" or run.lease_owner != lease_owner:
            raise ValueError("Workflow lease is not owned by this worker")
        await self._check_limits(run)
        context = dict(run.context_json)
        node = await self._next_node(run, context)
        if node is None:
            await self._converge_completed_run(run, context)
            return {"run_id": str(run.id), "status": run.status, "node": None}
        context = await self._run_step(run, node, context)
        if run.status == "FAILED":
            return {"run_id": str(run.id), "status": run.status, "node": node}

        if node == "request_human_review":
            run.status = "PENDING_REVIEW"
            self._clear_lease(run)
        elif node == "approve_or_reject":
            decision = str(context.get("review_decision") or "")
            if decision == "reject":
                run.status = "REJECTED"
                run.completed_at = utc_now()
                self._clear_lease(run)
            elif decision == "request_changes":
                run.iteration += 1
                context["revision_feedback"] = str(
                    context.get("review_feedback") or ""
                )
                context.pop("review_decision", None)
                context.pop("reviewer_id", None)
                run.status = "PENDING"
                self._clear_lease(run)
        elif node == "publish_report":
            run.status = "PUBLISHED"
            run.completed_at = utc_now()
            self._clear_lease(run)
        else:
            run.heartbeat_at = utc_now()
            run.lease_expires_at = utc_now() + timedelta(
                seconds=settings.WORKFLOW_LEASE_SECONDS
            )
        _persist_context(run, context)
        await self.session.flush()
        return {"run_id": str(run.id), "status": run.status, "node": node}

    async def mark_retry_or_failed(
        self,
        run_id: uuid.UUID,
        *,
        lease_owner: str,
        error_type: str,
        terminal: bool = False,
    ) -> None:
        run = await self.repository.get_run(run_id, for_update=True)
        if run is None or run.lease_owner != lease_owner:
            return
        run.error_type = error_type[:255]
        self._clear_lease(run)
        if terminal or run.attempt >= settings.WORKFLOW_MAX_ATTEMPTS:
            run.status = "FAILED"
            run.completed_at = utc_now()
            return
        run.status = "RETRY"
        delay = settings.WORKFLOW_RETRY_BASE_SECONDS * (2 ** max(0, run.attempt - 1))
        run.next_retry_at = utc_now() + timedelta(seconds=delay)

    async def _converge_completed_run(
        self, run: WorkflowRun, context: dict[str, Any]
    ) -> None:
        decision = str(context.get("review_decision") or "")
        if decision == "approve":
            run.status = "PUBLISHED"
            run.completed_at = utc_now()
        elif decision == "reject":
            run.status = "REJECTED"
            run.completed_at = utc_now()
        elif decision == "request_changes":
            run.iteration += 1
            context["revision_feedback"] = str(context.get("review_feedback") or "")
            context.pop("review_decision", None)
            context.pop("reviewer_id", None)
            run.status = "PENDING"
        else:
            run.status = "PENDING_REVIEW"
        self._clear_lease(run)
        _persist_context(run, context)
        await self.session.flush()

    async def _next_node(
        self, run: WorkflowRun, context: dict[str, Any]
    ) -> str | None:
        decision = str(context.get("review_decision") or "")
        if decision in {"approve", "reject", "request_changes"}:
            candidates = ["approve_or_reject"]
            if decision == "approve":
                candidates.append("publish_report")
        else:
            start = "generate_draft" if context.get("revision_feedback") else "validate_input"
            candidates = WORKFLOW_NODES[
                WORKFLOW_NODES.index(start) : WORKFLOW_NODES.index("request_human_review") + 1
            ]
        for node in candidates:
            step = await self.repository.get_step(run.id, node, run.iteration)
            if step is None or step.status != "COMPLETED":
                return node
        return None

    @staticmethod
    def _clear_lease(run: WorkflowRun) -> None:
        run.lease_owner = None
        run.heartbeat_at = None
        run.lease_expires_at = None

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
            and not (
                principal.has_role("research_reviewer")
                and principal.tenant_id == run.tenant_id
            )
            and not principal.is_platform_admin
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
        existing = await self.repository.get_step(
            run.id, node, run.iteration, for_update=True
        )
        if existing is not None and existing.status == "COMPLETED":
            return context
        if existing is None:
            step_count = await self.session.scalar(
                select(func.count(WorkflowStep.id)).where(
                    WorkflowStep.workflow_run_id == run.id
                )
            )
            if int(step_count or 0) >= run.max_steps:
                raise WorkflowLimitError("max_steps exceeded")
        if existing is None:
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
        else:
            step = existing
            step.status = "RUNNING"
            step.input_summary = _summarize_context(context)
            step.output_summary = {}
            step.started_at = utc_now()
            step.completed_at = None
            step.error_type = ""
        run.current_step = node
        await self.session.flush()
        if node in {"retrieve_evidence", "generate_draft"}:
            # Persist the RUNNING checkpoint before CPU/network-bound work.
            checkpoint_time = utc_now()
            run.heartbeat_at = checkpoint_time
            run.lease_expires_at = checkpoint_time + timedelta(
                seconds=max(
                    settings.WORKFLOW_LEASE_SECONDS,
                    run.node_timeout_seconds + 5,
                )
            )
            await self.session.commit()
        try:
            async with asyncio.timeout(run.node_timeout_seconds):
                context = await self._execute_node(node, run, context)
            step.status = "COMPLETED"
            step.output_summary = _summarize_context(context)
            step.completed_at = utc_now()
            _persist_context(run, context)
            await self.session.flush()
            return context
        except Exception as exc:
            step.status = "FAILED"
            step.completed_at = utc_now()
            step.error_type = "NodeTimeoutError" if isinstance(exc, TimeoutError) else type(exc).__name__
            step.output_summary = {"error": str(exc)[:500]}
            run.error_type = step.error_type
            _persist_context(run, context)
            # Persist the failed-node checkpoint before the worker schedules a
            # retry in a fresh transaction.
            await self.session.commit()
            raise WorkflowNodeExecutionError(step.error_type) from exc

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
            from app.services.legacy_portfolio_adapter import LegacyPortfolioAdapter

            portfolio_id = uuid.UUID(str(request["portfolio_id"]))
            principal = Principal(
                run.user_id,
                frozenset(context["permission_groups"]),
                authenticated=True,
                tenant_id=run.tenant_id,
                roles=frozenset(context.get("roles", [])),
            )
            loaded = await LegacyPortfolioAdapter(self.session).load(
                portfolio_id=portfolio_id,
                principal=principal,
            )
            if loaded is None:
                raise ValueError("No PostgreSQL valuation snapshot available")
            context["portfolio_summary"] = loaded.summary.model_dump(mode="json")
            context["portfolio_tickers"] = [
                item.position.ticker for item in loaded.summary.stocks
            ]
            context["portfolio_lineage"] = {
                "portfolio_id": str(loaded.portfolio.id),
                "valuation_snapshot_id": str(loaded.valuation.id),
                "valuation_input_hash": loaded.valuation.input_hash,
                "as_of": loaded.valuation.as_of.isoformat(),
                "valuation_status": loaded.valuation.valuation_status,
                "coverage_ratio": float(loaded.valuation.coverage_ratio),
            }
        elif node == "calculate_risk":
            from models import PortfolioSummary
            from routes.research import _build_portfolio_risk_summary

            portfolio_summary = PortfolioSummary.model_validate(context["portfolio_summary"])
            risk_summary = await _build_portfolio_risk_summary(
                portfolio_summary,
                session=self.session,
                as_of=datetime.fromisoformat(
                    context["portfolio_lineage"]["as_of"].replace("Z", "+00:00")
                ),
            )
            risk_summary["valuation_status"] = context["portfolio_lineage"][
                "valuation_status"
            ]
            risk_summary["valuation_coverage_ratio"] = context["portfolio_lineage"][
                "coverage_ratio"
            ]
            context["risk_summary"] = risk_summary
        elif node == "retrieve_evidence":
            query = str(request.get("query") or "public fund portfolio risk policy evidence")
            principal = Principal(
                run.user_id,
                frozenset(context["permission_groups"]),
                authenticated=True,
                tenant_id=run.tenant_id,
                roles=frozenset(context.get("roles", [])),
            )
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
                trace_key=f"workflow:{run.id}:generate_draft:{run.iteration}",
                release_transaction_before_provider=True,
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
            statement = (
                insert(ReviewTask)
                .values(
                    workflow_run_id=run.id,
                    iteration=run.iteration,
                    status="PENDING",
                    assigned_group="research_reviewer",
                )
                .on_conflict_do_nothing(
                    index_elements=[ReviewTask.workflow_run_id, ReviewTask.iteration]
                )
                .returning(ReviewTask)
            )
            review = (await self.session.execute(statement)).scalar_one_or_none()
            if review is None:
                review = await self.session.scalar(
                    select(ReviewTask).where(
                        ReviewTask.workflow_run_id == run.id,
                        ReviewTask.iteration == run.iteration,
                    )
                )
            if review is None:
                raise RuntimeError("Idempotent review task insert failed")
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
            publish_statement = (
                insert(PublishedReport)
                .values(
                    workflow_run_id=run.id,
                    report_json=context["draft"],
                    published_by=str(context["reviewer_id"]),
                    published_at=utc_now(),
                )
                .on_conflict_do_nothing(index_elements=[PublishedReport.workflow_run_id])
                .returning(PublishedReport)
            )
            published_report = (
                await self.session.execute(publish_statement)
            ).scalar_one_or_none()
            if published_report is None:
                published_report = await self.repository.report_for_run(run.id)
            if published_report is None:
                raise RuntimeError("Idempotent report publication failed")
            context["report_id"] = str(published_report.id)
        return context

    async def _check_limits(self, run: WorkflowRun) -> None:
        count = await self.session.scalar(
            select(func.count(WorkflowStep.id)).where(WorkflowStep.workflow_run_id == run.id)
        )
        if int(count or 0) > run.max_steps:
            raise WorkflowLimitError("max_steps exceeded")
        if run.cost_amount > run.cost_budget:
            raise WorkflowLimitError("cost budget exceeded")
        if run.started_at is not None:
            elapsed = utc_now() - run.started_at
            if elapsed.total_seconds() > run.timeout_seconds:
                raise WorkflowLimitError("workflow timeout exceeded")

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


def _persist_context(run: WorkflowRun, context: dict[str, Any]) -> None:
    """Persist each JSONB workflow checkpoint, including in-place mutations."""
    run.context_json = context
    flag_modified(run, "context_json")


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
