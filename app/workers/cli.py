"""Shared command-line parsing for standalone workers."""
from __future__ import annotations

import argparse
import uuid
from datetime import UTC, date, datetime


def optional_uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


def iso_date(value: str) -> date:
    return date.fromisoformat(value)


def iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def print_result(run) -> None:
    print(
        {
            "sync_run_id": str(run.id),
            "status": run.status,
            "retry_count": run.retry_count,
            "result": run.result_snapshot,
            "error": run.error_message or None,
        }
    )


def base_parser(description: str) -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description=description)

