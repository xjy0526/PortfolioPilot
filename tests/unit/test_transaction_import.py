"""Unit tests for transaction import history metadata."""
from __future__ import annotations

from app.services.transaction_import import _NormalizedRow, _history_completeness


def _row(transaction_type: str) -> _NormalizedRow:
    return _NormalizedRow(
        line_number=2,
        values={"transaction_type": transaction_type},
        source_record_hash="0" * 64,
        security=None,
    )


def test_standard_opening_balance_is_not_reported_as_complete_history() -> None:
    assert _history_completeness([_row("opening_balance")], legacy=False) == (
        "opening_balance_only"
    )
    assert _history_completeness(
        [_row("opening_balance"), _row("buy")], legacy=False
    ) == "partial_history"


def test_transaction_only_csv_is_complete_and_legacy_is_explicit() -> None:
    assert _history_completeness([_row("deposit"), _row("buy")], legacy=False) == "complete"
    assert _history_completeness([_row("deposit")], legacy=True) == "opening_balance_only"
