"""Persist a completed backtest report without sharing sessions across tasks."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.backtest import (
    BacktestRebalanceSnapshot,
    BacktestRun,
    BacktestStrategyResult,
)
from app.db.repositories.backtest import BacktestRunRepository


class BacktestPersistenceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.runs = BacktestRunRepository(session)

    async def persist(
        self,
        report: dict[str, Any],
        *,
        portfolio_id: uuid.UUID | None = None,
    ) -> tuple[BacktestRun, bool]:
        """Store one immutable run; return an existing run on cache replay."""
        input_hash = str(report["input_hash"])
        existing = await self.runs.get_by_input_hash(input_hash)
        if existing is not None:
            return existing, True

        run = BacktestRun(
            portfolio_id=portfolio_id,
            portfolio_snapshot_id=_uuid_or_none(report.get("portfolio_snapshot_id")),
            data_as_of=_utc_datetime(report["data_as_of"]),
            code_version=str(report["code_version"]),
            config_hash=str(report["config_hash"]),
            input_hash=input_hash,
            price_source=str(report["price_source"]),
            benchmark=(report.get("benchmark") or {}).get("name"),
            execution_convention=str(report["execution_convention"]),
            cost_assumptions=dict(report.get("cost_assumptions", {})),
            config_snapshot=dict(report.get("config", {})),
            output_metrics={
                item["strategy"]: {
                    key: value
                    for key, value in item.items()
                    if key not in {"nav_series", "weights", "risk_contribution"}
                }
                for item in _strategy_results(report)
            },
            status=str(report.get("status", "completed")),
            error=str(report.get("error") or ""),
            mock_price_data_used=bool(report.get("mock_price_data_used", False)),
        )
        await self.runs.add(run)

        for snapshot in report.get("rebalance_snapshots", []):
            for strategy, audit in snapshot.get("strategies", {}).items():
                self.session.add(
                    BacktestRebalanceSnapshot(
                        backtest_run_id=run.id,
                        strategy=strategy,
                        execution_at=_utc_datetime(snapshot["execution_date"]),
                        effective_from=_utc_datetime_or_none(snapshot.get("effective_from")),
                        eligible_universe=list(snapshot.get("eligible_universe", [])),
                        excluded_assets=dict(snapshot.get("excluded_assets", {})),
                        pre_trade_weights=dict(audit.get("pre_trade_weights", {})),
                        target_weights=dict(audit.get("target_weights", {})),
                        executed_weights=dict(audit.get("executed_weights", {})),
                        post_return_weights=audit.get("post_return_weights"),
                        turnover=Decimal(str(audit.get("turnover", 0))),
                        executed_turnover=Decimal(
                            str(audit.get("executed_turnover", audit.get("turnover", 0)))
                        ),
                        costs=dict(audit.get("costs", {})),
                        input_hash=str(snapshot.get("input_hash", "")),
                    )
                )

        for result in _strategy_results(report):
            excluded = {"strategy", "nav_series", "weights", "turnover", "total_costs"}
            self.session.add(
                BacktestStrategyResult(
                    backtest_run_id=run.id,
                    strategy=str(result["strategy"]),
                    metrics={key: value for key, value in result.items() if key not in excluded},
                    final_weights=dict(result.get("weights", {})),
                    nav_series=list(result.get("nav_series", [])),
                    turnover=Decimal(str(result.get("turnover", 0))),
                    total_costs=Decimal(str(result.get("total_costs", 0))),
                )
            )
        await self.session.flush()
        return run, False


def _uuid_or_none(value: object) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value else None


def _strategy_results(report: dict[str, Any]) -> list[dict[str, Any]]:
    values = report.get("results") or report.get("strategies") or []
    return [item for item in values if isinstance(item, dict)]


def _utc_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _utc_datetime_or_none(value: object) -> datetime | None:
    return _utc_datetime(value) if value else None
