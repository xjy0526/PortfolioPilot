from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import settings
from routes import app_settings


def test_upsert_env_values_updates_and_quotes(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=bar\nQWEN_MODEL=old\n", encoding="utf-8")

    app_settings._upsert_env_values(
        env_file,
        {
            "QWEN_MODEL": "qwen plus",
            "CONTACT_EMAIL": "team@example.com",
        },
    )

    text = env_file.read_text(encoding="utf-8")
    assert "FOO=bar" in text
    assert 'QWEN_MODEL="qwen plus"' in text
    assert "CONTACT_EMAIL=team@example.com" in text


def test_app_settings_endpoint_saves_without_echoing_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "BASE_DIR", tmp_path)
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
    assert "secret-qwen-key" not in response.text
    assert "QWEN_API_KEY=secret-qwen-key" in (tmp_path / ".env").read_text(encoding="utf-8")
