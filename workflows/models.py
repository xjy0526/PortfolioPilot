from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


WorkflowStatus = Literal["DRAFT", "RUNNING", "PENDING_REVIEW", "APPROVED", "REJECTED", "PUBLISHED", "FAILED"]
StepStatus = Literal["PENDING", "RUNNING", "COMPLETED", "FAILED", "SKIPPED"]


class WorkflowRun(BaseModel):
    model_config = ConfigDict(extra="allow")
    run_id: str
    user_id: str
    business_scene: str = "public_fund_research_report"
    status: WorkflowStatus = "DRAFT"
    max_steps: int = 30
    timeout_seconds: int = 120
    cost_budget: float = 0.20
    estimated_cost: float = 0.0
    current_step: str = ""
    iteration: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_type: str = ""


class WorkflowStep(BaseModel):
    step_id: str
    run_id: str
    step_name: str
    iteration: int
    status: StepStatus
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    completed_at: datetime | None = None
    error_type: str = ""


class ReviewTask(BaseModel):
    review_id: str
    run_id: str
    status: Literal["PENDING", "APPROVED", "REJECTED", "CHANGES_REQUESTED"] = "PENDING"
    assigned_to: str = "human_reviewer"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None


class ReviewDecision(BaseModel):
    decision_id: str
    review_id: str
    run_id: str
    decision: Literal["approve", "reject", "request_changes"]
    reviewer_id: str
    feedback: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
