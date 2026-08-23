"""Unit coverage for legacy valuation lineage errors and repair reporting."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services.legacy_portfolio_adapter import PortfolioRebuildRequired
from app.services.legacy_valuation_repair import (
    LegacyValuationRepairCandidate,
    LegacyValuationRepairOutcome,
    LegacyValuationScanResult,
    _mismatch_reason,
)
from scripts import rebuild_stale_legacy_valuations as repair_script


def _candidate() -> LegacyValuationRepairCandidate:
    return LegacyValuationRepairCandidate(
        portfolio_id=uuid.uuid4(),
        active_generation_id=uuid.uuid4(),
        generation_number=2,
        latest_valuation_id=uuid.uuid4(),
        latest_valuation_generation_id=None,
        reason="valuation_generation_lineage_missing",
    )


def test_rebuild_required_payload_is_machine_readable() -> None:
    portfolio_id = uuid.uuid4()
    generation_id = uuid.uuid4()
    error = PortfolioRebuildRequired(
        portfolio_id=portfolio_id,
        active_generation_id=generation_id,
    )

    assert str(error) == "valuation_generation_mismatch"
    assert error.as_dict() == {
        "error": "portfolio_rebuild_required",
        "portfolio_id": str(portfolio_id),
        "active_generation_id": str(generation_id),
        "reason": "valuation_generation_mismatch",
    }


def test_repair_value_objects_and_mismatch_reasons() -> None:
    candidate = _candidate()
    scan = LegacyValuationScanResult(scanned_portfolios=3, candidates=(candidate,))
    outcome = LegacyValuationRepairOutcome(
        portfolio_id=candidate.portfolio_id,
        active_generation_id=candidate.active_generation_id,
        status="rebuilt",
        valuation_snapshot_id=uuid.uuid4(),
    )

    assert scan.affected_portfolios == 1
    assert scan.as_dict()["candidates"] == [candidate.as_dict()]
    assert outcome.as_dict()["status"] == "rebuilt"
    assert _mismatch_reason(None, candidate.active_generation_id) == "valuation_missing"
    assert (
        _mismatch_reason(
            SimpleNamespace(config_snapshot={}, valuation_status="complete"),
            candidate.active_generation_id,
        )
        == "valuation_generation_lineage_missing"
    )
    assert (
        _mismatch_reason(
            SimpleNamespace(
                config_snapshot={
                    "data_source_context": {
                        "legacy_snapshot_generation_id": str(uuid.uuid4())
                    }
                },
                valuation_status="complete",
                total_market_value=1,
            ),
            candidate.active_generation_id,
        )
        == "valuation_generation_mismatch"
    )
    assert (
        _mismatch_reason(
            SimpleNamespace(
                config_snapshot={
                    "data_source_context": {
                        "legacy_snapshot_generation_id": str(
                            candidate.active_generation_id
                        )
                    }
                },
                valuation_status="partial",
                total_market_value=None,
            ),
            candidate.active_generation_id,
        )
        == "valuation_incomplete"
    )
    assert (
        _mismatch_reason(
            SimpleNamespace(
                config_snapshot={
                    "data_source_context": {
                        "legacy_snapshot_generation_id": str(
                            candidate.active_generation_id
                        )
                    }
                },
                valuation_status="complete",
                total_market_value=1,
            ),
            candidate.active_generation_id,
        )
        == "valuation_integrity_invalid"
    )


class _SessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


class _SessionFactory:
    def __call__(self) -> _SessionContext:
        return _SessionContext()

    def begin(self) -> _SessionContext:
        return _SessionContext()


@pytest.mark.asyncio
async def test_repair_script_dry_run_success_and_failure_reporting(monkeypatch) -> None:
    candidate = _candidate()
    scan = LegacyValuationScanResult(scanned_portfolios=1, candidates=(candidate,))

    class FakeRepairService:
        should_fail = False

        def __init__(self, _session: object) -> None:
            pass

        async def scan(self, **_kwargs: object) -> LegacyValuationScanResult:
            return scan

        async def repair_portfolio(
            self, portfolio_id: uuid.UUID, **_kwargs: object
        ) -> LegacyValuationRepairOutcome:
            if self.should_fail:
                raise RuntimeError("fixture repair failure")
            return LegacyValuationRepairOutcome(
                portfolio_id=portfolio_id,
                active_generation_id=candidate.active_generation_id,
                status="rebuilt",
                valuation_snapshot_id=uuid.uuid4(),
            )

    monkeypatch.setattr(
        repair_script,
        "LegacyValuationRepairService",
        FakeRepairService,
    )
    kwargs = {
        "portfolio_id": candidate.portfolio_id,
        "limit": 1,
        "continue_on_error": False,
        "as_of": datetime(2026, 8, 23, tzinfo=UTC),
        "session_factory": _SessionFactory(),
    }

    dry_run = await repair_script.run_repair(dry_run=True, **kwargs)
    assert dry_run["status"] == "dry_run"
    assert dry_run["affected_portfolios"] == 1
    assert dry_run["outcomes"][0]["status"] == "would_rebuild"

    completed = await repair_script.run_repair(dry_run=False, **kwargs)
    assert completed["status"] == "completed"
    assert completed["processed_portfolios"] == 1

    FakeRepairService.should_fail = True
    failed = await repair_script.run_repair(dry_run=False, **kwargs)
    assert failed["status"] == "completed_with_errors"
    assert failed["failed_portfolios"] == 1
    assert failed["aborted"] is True
    assert failed["failures"][0]["error_type"] == "RuntimeError"


def test_repair_cli_parses_options_and_returns_report(monkeypatch, capsys) -> None:
    portfolio_id = uuid.uuid4()

    async def fake_run_repair(**kwargs: object) -> dict[str, object]:
        assert kwargs["dry_run"] is True
        assert kwargs["portfolio_id"] == portfolio_id
        assert kwargs["limit"] == 2
        assert kwargs["continue_on_error"] is True
        return {"status": "dry_run", "failed_portfolios": 0}

    monkeypatch.setattr(repair_script, "run_repair", fake_run_repair)
    assert (
        repair_script.main(
            [
                "--dry-run",
                "--portfolio-id",
                str(portfolio_id),
                "--limit",
                "2",
                "--continue-on-error",
            ]
        )
        == 0
    )
    assert '"status": "dry_run"' in capsys.readouterr().out

    with pytest.raises(SystemExit):
        repair_script._arguments(["--limit", "0"])
