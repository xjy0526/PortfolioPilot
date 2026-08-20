"""Worker audit metadata must never retain connection credentials."""
from app.workers.jobs import _error_summary
from config import settings


def test_worker_error_summary_redacts_postgres_password() -> None:
    error = RuntimeError(
        "connect postgresql+asyncpg://portfolio:very-secret@db.internal:5432/app failed"
    )

    summary = _error_summary(error)

    assert "very-secret" not in summary
    assert "portfolio:***@db.internal" in summary


def test_worker_error_summary_redacts_object_storage_secret(monkeypatch) -> None:
    monkeypatch.setattr(settings, "S3_SECRET_ACCESS_KEY", "storage-secret")

    summary = _error_summary(RuntimeError("S3 rejected storage-secret"))

    assert "storage-secret" not in summary
    assert "S3 rejected ***" in summary
