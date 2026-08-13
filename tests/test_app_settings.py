from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import settings
from routes import app_settings


def test_app_settings_endpoint_updates_memory_without_persisting_secret(
    tmp_path: Path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "READ_ONLY_DEMO", False)
    monkeypatch.setattr(settings, "ALLOW_RUNTIME_SECRET_CONFIGURATION", True)
    monkeypatch.setattr(settings, "LOCAL_PRINCIPAL_ROLES", "platform_admin")
    monkeypatch.setattr(settings, "QWEN_API_KEY", "")
    monkeypatch.setattr(settings, "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(settings, "QWEN_MODEL", "qwen-plus")
    monkeypatch.setattr(settings, "QWEN_REASONING_MODEL", "")
    monkeypatch.setattr(settings, "FMP_API_KEY", "")
    monkeypatch.setattr(settings, "CONTACT_EMAIL", "")

    app = FastAPI()
    app.include_router(app_settings.router)
    client = TestClient(app)

    response = client.post(
        "/api/app-settings",
        json={
            "qwen_api_key": "secret-qwen-key",
            "qwen_model": "qwen-plus",
            "contact_email": "team@example.com",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["settings"]["qwen_configured"] is True
    assert payload["settings"]["contact_email"] == "team@example.com"
    assert payload["settings"]["runtime_configuration_persisted"] is False
    assert "secret-qwen-key" not in response.text
    assert not (tmp_path / ".env").exists()


def test_app_settings_write_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "READ_ONLY_DEMO", False)
    monkeypatch.setattr(settings, "ALLOW_RUNTIME_SECRET_CONFIGURATION", False)

    app = FastAPI()
    app.include_router(app_settings.router)
    response = TestClient(app).post(
        "/api/app-settings",
        json={"qwen_api_key": "must-not-be-applied"},
    )

    assert response.status_code == 403
