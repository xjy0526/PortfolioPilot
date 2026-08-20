"""Unit coverage for the legacy dashboard-to-ledger compatibility adapter."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.principal import Principal
from app.domain import PositionRebuildResult
from app.services import legacy_csv_import as legacy_import
from app.services.legacy_csv_import import LegacyCsvPortfolioImportService
from app.services.transaction_import import TransactionImportResult
from config import settings


class _NestedTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _Session:
    def __init__(self, batch=None) -> None:
        self.batch = batch

    def begin_nested(self) -> _NestedTransaction:
        return _NestedTransaction()

    async def get(self, model, entity_id):
        return self.batch


def _principal() -> Principal:
    return Principal(
        user_id="legacy-unit-operator",
        tenant_id="legacy-unit-tenant",
        roles=frozenset({"operator"}),
    )


def _import_result(*, status: str = "completed") -> TransactionImportResult:
    persisted = 0 if status == "failed" else 1
    rejected = 1 if status == "failed" else 0
    return TransactionImportResult(
        import_batch_id=uuid.uuid4(),
        status=status,
        total_rows=1,
        accepted_rows=persisted,
        duplicate_rows=0,
        rejected_rows=rejected,
        persisted_rows=persisted,
        inserted_rows=persisted,
        idempotent_replay=False,
        history_completeness="opening_balance_only",
        errors=({"line": 2, "error": "invalid row", "row": {}},) if rejected else (),
    )


@pytest.mark.asyncio
async def test_adapter_runs_import_rebuild_and_valuation_with_one_cutoff(monkeypatch) -> None:
    batch = SimpleNamespace(
        id=uuid.uuid4(),
        file_sha256="a" * 64,
        source_filename="dashboard-positions.csv",
    )
    session = _Session(batch)
    service = LegacyCsvPortfolioImportService(session)  # type: ignore[arg-type]
    portfolio_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    cutoff = datetime(2026, 8, 20, 12, tzinfo=UTC)
    portfolio = SimpleNamespace(
        id=portfolio_id,
        created_at=datetime(2026, 1, 2, 8),
    )
    imported = _import_result()
    batch.id = imported.import_batch_id
    importer = SimpleNamespace(import_bytes=AsyncMock(return_value=imported))
    rebuilt = PositionRebuildResult(
        portfolio_id=portfolio_id,
        as_of=cutoff,
        positions=(),
        cash_balances={"USD": Decimal("25")},
        warnings=("opening_balance_only",),
        history_completeness="opening_balance_only",
    )
    ledger = SimpleNamespace(rebuild=AsyncMock(return_value=rebuilt))
    valuation = SimpleNamespace(
        id=snapshot_id,
        valuation_status="complete",
        as_of=cutoff,
        data_as_of=cutoff,
        warnings=(),
    )
    valuation_result = SimpleNamespace(valuation=valuation, positions=())
    valuator = SimpleNamespace(value=AsyncMock(return_value=valuation_result))
    generation = SimpleNamespace(
        id=uuid.uuid4(),
        import_batch_id=imported.import_batch_id,
        status="active",
        generation_number=1,
        activated_at=cutoff,
    )

    service._writable_portfolio = AsyncMock(return_value=portfolio)  # type: ignore[method-assign]
    service.transactions = SimpleNamespace(
        list_for_import_batch=AsyncMock(return_value=[SimpleNamespace()])
    )
    service.generations = SimpleNamespace(
        activate=AsyncMock(return_value=(generation, True, None))
    )
    service._persist_declared_prices = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"source": legacy_import.LEGACY_DECLARED_PRICE_SOURCE}]
    )
    monkeypatch.setattr(legacy_import, "TransactionCsvImporter", lambda _: importer)
    monkeypatch.setattr(legacy_import, "TransactionLedgerService", lambda _: ledger)
    monkeypatch.setattr(legacy_import, "PortfolioValuationService", lambda _: valuator)

    result = await service.import_positions(
        positions=[
            {
                "ticker": "AAPL",
                "shares": "1",
                "buy_price": "100",
                "current_price": "110",
                "currency": "USD",
            }
        ],
        principal=_principal(),
        portfolio_id=portfolio_id,
        as_of=cutoff,
    )

    payload = result.as_dict()
    assert payload["status"] == "ok"
    assert payload["portfolio_id"] == str(portfolio_id)
    assert payload["snapshot_generation"]["generation_number"] == 1
    assert payload["position_rebuild"]["cash_balances"] == {"USD": "25"}
    assert payload["valuation_snapshot"]["snapshot_id"] == str(snapshot_id)
    content = importer.import_bytes.await_args.kwargs["content"].decode("utf-8")
    assert "2026-01-02" in content
    ledger.rebuild.assert_awaited_once_with(
        portfolio_id,
        as_of=cutoff,
        knowledge_as_of=cutoff,
    )
    assert valuator.value.await_args.kwargs["knowledge_as_of"] == cutoff
    assert importer.import_bytes.await_args.kwargs["allow_empty"] is True
    assert importer.import_bytes.await_args.kwargs["require_all_rows_valid"] is True
    assert importer.import_bytes.await_args.kwargs["validate_portfolio_state"] is False


@pytest.mark.asyncio
async def test_adapter_stops_before_rebuild_when_atomic_import_fails(monkeypatch) -> None:
    service = LegacyCsvPortfolioImportService(_Session())  # type: ignore[arg-type]
    portfolio_id = uuid.uuid4()
    portfolio = SimpleNamespace(
        id=portfolio_id,
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    importer = SimpleNamespace(import_bytes=AsyncMock(return_value=_import_result(status="failed")))
    service._writable_portfolio = AsyncMock(return_value=portfolio)  # type: ignore[method-assign]
    monkeypatch.setattr(legacy_import, "TransactionCsvImporter", lambda _: importer)

    result = await service.import_positions(
        positions=[{"ticker": "BAD", "shares": "-1", "buy_price": "100"}],
        principal=_principal(),
        as_of=datetime(2026, 8, 20, 12),
    )

    assert result.as_dict()["status"] == "failed"
    assert result.position_rebuild == {
        "status": "not_run",
        "reason": "transaction_import_failed",
    }
    assert result.snapshot_generation == {"status": "not_activated"}
    assert result.valuation_snapshot == {"status": "not_created"}


@pytest.mark.asyncio
async def test_writable_portfolio_resolution_is_server_authorized(monkeypatch) -> None:
    service = LegacyCsvPortfolioImportService(SimpleNamespace())  # type: ignore[arg-type]
    principal = _principal()
    explicit_id = uuid.uuid4()
    service.portfolios = SimpleNamespace(get_accessible=AsyncMock(return_value=None))
    with pytest.raises(ValueError, match="portfolio not found"):
        await service._writable_portfolio(principal, explicit_id)

    configured_id = uuid.uuid4()
    configured = SimpleNamespace(id=configured_id)
    monkeypatch.setattr(settings, "DEFAULT_PORTFOLIO_ID", str(configured_id))
    service.portfolios = SimpleNamespace(get_accessible=AsyncMock(return_value=configured))
    assert await service._writable_portfolio(principal, None) is configured

    first = SimpleNamespace(id=uuid.uuid4())
    second = SimpleNamespace(id=uuid.uuid4())
    writable = SimpleNamespace(id=second.id)
    monkeypatch.setattr(settings, "DEFAULT_PORTFOLIO_ID", "invalid-uuid")
    service.portfolios = SimpleNamespace(
        list_accessible=AsyncMock(return_value=[first, second]),
        get_accessible=AsyncMock(side_effect=[None, writable]),
    )
    assert await service._writable_portfolio(principal, None) is writable

    service.portfolios = SimpleNamespace(
        list_accessible=AsyncMock(return_value=[]),
        get_accessible=AsyncMock(),
    )
    with pytest.raises(ValueError, match="no writable PostgreSQL portfolio"):
        await service._writable_portfolio(principal, None)


@pytest.mark.asyncio
async def test_declared_price_lineage_distinguishes_current_and_fallback_prices() -> None:
    service = LegacyCsvPortfolioImportService(SimpleNamespace())  # type: ignore[arg-type]
    batch_id = uuid.uuid4()
    current_security = uuid.uuid4()
    fallback_security = uuid.uuid4()
    rows = [
        SimpleNamespace(
            security_id=None,
            transaction_type="deposit",
            raw_payload={},
            price=None,
            currency="USD",
        ),
        SimpleNamespace(
            security_id=current_security,
            transaction_type="opening_balance",
            raw_payload={"current_price": "1,250.50"},
            price=Decimal("1000"),
            currency="USD",
        ),
        SimpleNamespace(
            security_id=fallback_security,
            transaction_type="opening_balance",
            raw_payload={"current_price": "not-a-price"},
            price=Decimal("80"),
            currency="USD",
        ),
    ]
    service.transactions = SimpleNamespace(list_for_import_batch=AsyncMock(return_value=rows))
    service.prices = SimpleNamespace(
        upsert=AsyncMock(
            side_effect=[
                SimpleNamespace(id=uuid.uuid4()),
                SimpleNamespace(id=uuid.uuid4()),
            ]
        )
    )

    sources = await service._persist_declared_prices(
        import_batch_id=batch_id,
        generation_id=uuid.uuid4(),
        cutoff=datetime(2026, 8, 20, 12, tzinfo=UTC),
    )

    assert str(sources[0]["source"]).startswith(
        legacy_import.LEGACY_DECLARED_PRICE_SOURCE
    )
    assert str(sources[1]["source"]).startswith(
        legacy_import.LEGACY_COST_FALLBACK_SOURCE
    )
    assert [item["fallback"] for item in sources] == [False, True]
    first_bar, second_bar = [call.args[0] for call in service.prices.upsert.await_args_list]
    assert first_bar.close == Decimal("1250.50")
    assert first_bar.raw_payload["supplied_field"] == "current_price"
    assert second_bar.close == Decimal("80")
    assert second_bar.raw_payload["fallback"] is True
