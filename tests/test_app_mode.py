from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import Settings, settings
from routes import app_settings
from routes import shadow_portfolio


def test_personal_mode_keeps_personal_features(monkeypatch):
    monkeypatch.setattr(settings, "APP_MODE", "personal")
    monkeypatch.setattr(settings, "ENABLE_POLYMARKET", False)
    monkeypatch.setattr(settings, "ENABLE_TELEGRAM", False)
    monkeypatch.setattr(settings, "ENABLE_PARQET", False)
    monkeypatch.setattr(settings, "ENABLE_SHADOW_AGENT", False)

    payload = app_settings._public_settings()

    assert payload["app_mode"] == "personal"
    assert payload["feature_flags"] == {
        "tech_picks": True,
        "shadow_agent": False,
        "trade_advisor": True,
        "polymarket": False,
        "telegram": False,
        "parqet": False,
    }


def test_fund_research_mode_hides_personal_features(monkeypatch):
    monkeypatch.setattr(settings, "APP_MODE", "fund_research")
    monkeypatch.setattr(settings, "ENABLE_POLYMARKET", False)
    monkeypatch.setattr(settings, "ENABLE_TELEGRAM", False)
    monkeypatch.setattr(settings, "ENABLE_PARQET", False)
    monkeypatch.setattr(settings, "ENABLE_SHADOW_AGENT", False)

    payload = app_settings._public_settings()

    assert payload["app_mode"] == "fund_research"
    assert payload["feature_flags"] == {
        "tech_picks": False,
        "shadow_agent": False,
        "trade_advisor": False,
        "polymarket": False,
        "telegram": False,
        "parqet": False,
    }


def test_settings_accepts_both_app_modes():
    personal = Settings(_env_file=None, APP_MODE="personal")
    fund = Settings(_env_file=None, APP_MODE="fund_research")

    assert personal.fund_research_mode is False
    assert fund.fund_research_mode is True


def test_shadow_agent_requires_explicit_feature_flag():
    disabled = Settings(_env_file=None, APP_MODE="personal")
    enabled = Settings(
        _env_file=None,
        APP_MODE="personal",
        ENABLE_SHADOW_AGENT=True,
    )

    assert disabled.shadow_agent_enabled is False
    assert enabled.shadow_agent_enabled is True


def test_frontend_marks_personal_only_entries_and_neutral_labels():
    root = Path(__file__).resolve().parent.parent
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    js = (root / "static" / "translations.js").read_text(encoding="utf-8")

    assert 'data-personal-only="tech_picks"' in html
    assert 'data-personal-only="shadow_agent"' in html
    assert 'data-personal-only="trade_advisor"' in html
    assert "研究关注" in js
    assert "维持观察" in js
    assert "降低风险暴露" in js
    assert "人工复核" in js


def test_fund_research_mode_blocks_shadow_mutation(monkeypatch):
    monkeypatch.setattr(settings, "APP_MODE", "fund_research")
    app = FastAPI()
    app.include_router(shadow_portfolio.router)

    response = TestClient(app).post("/api/shadow-portfolio/run")

    assert response.status_code == 403
    assert "disabled" in response.json()["error"]
