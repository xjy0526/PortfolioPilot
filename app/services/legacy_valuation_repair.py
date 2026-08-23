"""Scan and repair legacy dashboard valuations with stale generation lineage."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PortfolioValuationSnapshot
from app.db.repositories import (
    LegacySnapshotGenerationRepository,
    PortfolioRepository,
    PortfolioValuationRepository,
)
from app.services.portfolio_valuation import PortfolioValuationService

LEGACY_SNAPSHOT_SOURCE = "legacy_dashboard_csv"
LEDGER_VALUATION_SOURCE = "ledger_rebuild"


@dataclass(frozen=True, slots=True)
class LegacyValuationRepairCandidate:
    portfolio_id: uuid.UUID
    active_generation_id: uuid.UUID
    generation_number: int
    latest_valuation_id: uuid.UUID | None
    latest_valuation_generation_id: str | None
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "portfolio_id": str(self.portfolio_id),
            "active_generation_id": str(self.active_generation_id),
            "generation_number": self.generation_number,
            "latest_valuation_id": (
                str(self.latest_valuation_id) if self.latest_valuation_id else None
            ),
            "latest_valuation_generation_id": self.latest_valuation_generation_id,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class LegacyValuationScanResult:
    scanned_portfolios: int
    candidates: tuple[LegacyValuationRepairCandidate, ...]

    @property
    def affected_portfolios(self) -> int:
        return len(self.candidates)

    def as_dict(self) -> dict[str, object]:
        return {
            "scanned_portfolios": self.scanned_portfolios,
            "affected_portfolios": self.affected_portfolios,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
        }


@dataclass(frozen=True, slots=True)
class LegacyValuationRepairOutcome:
    portfolio_id: uuid.UUID
    active_generation_id: uuid.UUID | None
    status: str
    valuation_snapshot_id: uuid.UUID | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "portfolio_id": str(self.portfolio_id),
            "active_generation_id": (
                str(self.active_generation_id) if self.active_generation_id else None
            ),
            "status": self.status,
            "valuation_snapshot_id": (
                str(self.valuation_snapshot_id) if self.valuation_snapshot_id else None
            ),
        }


class LegacyValuationRepairService:
    """Use the valuation service to restore active-generation lineage."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.portfolios = PortfolioRepository(session)
        self.generations = LegacySnapshotGenerationRepository(session)
        self.valuations = PortfolioValuationRepository(session)

    async def scan(
        self,
        *,
        as_of: datetime,
        portfolio_id: uuid.UUID | None = None,
        limit: int | None = None,
    ) -> LegacyValuationScanResult:
        cutoff = _as_utc(as_of)
        generations = await self.generations.list_active(
            portfolio_id=portfolio_id,
            source=LEGACY_SNAPSHOT_SOURCE,
            limit=limit,
        )
        candidates: list[LegacyValuationRepairCandidate] = []
        for generation in generations:
            matching = await self.valuations.latest_for_legacy_generation(
                generation.portfolio_id,
                generation.id,
                cutoff,
                valuation_source=LEDGER_VALUATION_SOURCE,
                legacy_source=LEGACY_SNAPSHOT_SOURCE,
            )
            if matching is not None:
                continue
            latest = await self.valuations.latest_for_source(
                generation.portfolio_id,
                source=LEDGER_VALUATION_SOURCE,
                as_of=cutoff,
            )
            candidates.append(
                LegacyValuationRepairCandidate(
                    portfolio_id=generation.portfolio_id,
                    active_generation_id=generation.id,
                    generation_number=generation.generation_number,
                    latest_valuation_id=latest.id if latest else None,
                    latest_valuation_generation_id=_generation_id(latest),
                    reason=_mismatch_reason(latest, generation.id),
                )
            )
        return LegacyValuationScanResult(
            scanned_portfolios=len(generations),
            candidates=tuple(candidates),
        )

    async def repair_portfolio(
        self,
        portfolio_id: uuid.UUID,
        *,
        as_of: datetime,
    ) -> LegacyValuationRepairOutcome:
        cutoff = _as_utc(as_of)
        portfolio = await self.portfolios.get_for_update(portfolio_id)
        if portfolio is None:
            raise ValueError("portfolio not found")
        generation = await self.generations.get_active(
            portfolio.id,
            source=LEGACY_SNAPSHOT_SOURCE,
            for_update=True,
        )
        if generation is None:
            return LegacyValuationRepairOutcome(
                portfolio_id=portfolio.id,
                active_generation_id=None,
                status="not_applicable",
            )
        existing = await self.valuations.latest_for_legacy_generation(
            portfolio.id,
            generation.id,
            cutoff,
            valuation_source=LEDGER_VALUATION_SOURCE,
            legacy_source=LEGACY_SNAPSHOT_SOURCE,
        )
        if existing is not None:
            return LegacyValuationRepairOutcome(
                portfolio_id=portfolio.id,
                active_generation_id=generation.id,
                status="unchanged",
                valuation_snapshot_id=existing.id,
            )

        result = await PortfolioValuationService(self.session).value(
            portfolio_id=portfolio.id,
            as_of=cutoff,
            knowledge_as_of=cutoff,
            source=LEDGER_VALUATION_SOURCE,
            data_source_context={
                "repair_operation": "stale_legacy_valuation_lineage",
                "repair_active_generation_id": str(generation.id),
            },
        )
        if (
            result.valuation.valuation_status != "complete"
            or result.valuation.total_market_value is None
        ):
            raise RuntimeError(
                "legacy valuation rebuild did not produce a complete valuation"
            )
        verified = await self.valuations.latest_for_legacy_generation(
            portfolio.id,
            generation.id,
            cutoff,
            valuation_source=LEDGER_VALUATION_SOURCE,
            legacy_source=LEGACY_SNAPSHOT_SOURCE,
        )
        if verified is None or verified.id != result.valuation.id:
            raise RuntimeError("rebuilt valuation failed generation-lineage verification")
        return LegacyValuationRepairOutcome(
            portfolio_id=portfolio.id,
            active_generation_id=generation.id,
            status="rebuilt",
            valuation_snapshot_id=verified.id,
        )


def _generation_id(snapshot: PortfolioValuationSnapshot | None) -> str | None:
    if snapshot is None or not isinstance(snapshot.config_snapshot, dict):
        return None
    context = snapshot.config_snapshot.get("data_source_context")
    if not isinstance(context, dict):
        return None
    value = context.get("legacy_snapshot_generation_id")
    return str(value) if value else None


def _mismatch_reason(
    snapshot: PortfolioValuationSnapshot | None,
    active_generation_id: uuid.UUID,
) -> str:
    if snapshot is None:
        return "valuation_missing"
    generation_id = _generation_id(snapshot)
    if generation_id is None:
        return "valuation_generation_lineage_missing"
    if generation_id != str(active_generation_id):
        return "valuation_generation_mismatch"
    if snapshot.valuation_status != "complete" or snapshot.total_market_value is None:
        return "valuation_incomplete"
    return "valuation_integrity_invalid"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
