"""Tests for the provider-neutral LLM client and legacy import shim."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from config import Settings
from services import vertex_ai
from services.llm import compat
from services.llm_client import (
    ChatMessage,
    ChatRequest,
    OpenAICompatibleClient,
    QwenClient,
    get_llm_client,
)


def _settings(**overrides):
    values = {
        "AI_PROVIDER": "qwen",
        "QWEN_API_KEY": "qwen-test-key",
        "QWEN_BASE_URL": "https://dashscope.example/v1",
        "QWEN_MODEL": "qwen-plus",
        "OPENAI_COMPATIBLE_API_KEY": "",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_builds_explicit_qwen_client():
    client = get_llm_client(_settings())

    assert isinstance(client, QwenClient)
    assert client.provider_name == "qwen"
    assert client.default_model == "qwen-plus"


@pytest.mark.asyncio
async def test_qwen_request_preserves_openai_compatible_base_path(monkeypatch):
    seen: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            seen["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, url, json):
            seen["url"] = url
            seen["payload"] = json
            return FakeResponse()

    monkeypatch.setattr("services.llm_client.httpx.AsyncClient", FakeAsyncClient)
    client = QwenClient(
        api_key="qwen-test-key",
        base_url="https://dashscope.example/compatible-mode/v1/",
        default_model="qwen-plus",
    )

    response = await client.generate_chat(
        ChatRequest(messages=[ChatMessage(role="user", content="analyze")])
    )

    assert seen["url"] == "https://dashscope.example/compatible-mode/v1/chat/completions"
    assert response.text == "ok"


def test_builds_explicit_openai_compatible_client():
    client = get_llm_client(
        _settings(
            AI_PROVIDER="openai_compatible",
            QWEN_API_KEY="",
            OPENAI_COMPATIBLE_API_KEY="openai-test-key",
            OPENAI_COMPATIBLE_BASE_URL="https://llm.example/v1",
            OPENAI_COMPATIBLE_MODEL="research-model",
        )
    )

    assert isinstance(client, OpenAICompatibleClient)
    assert client.provider_name == "openai_compatible"
    assert client.default_model == "research-model"


def test_missing_qwen_key_is_not_silently_replaced():
    with pytest.raises(RuntimeError, match="QWEN_API_KEY"):
        get_llm_client(_settings(QWEN_API_KEY=""))


def test_legacy_vertex_import_reexports_compatibility_surface():
    assert vertex_ai.get_client is compat.get_client
    assert vertex_ai.Content is compat.Content
    assert vertex_ai.Part is compat.Part


def test_legacy_adapter_uses_new_client(monkeypatch):
    fake_client = MagicMock()
    fake_client.api_key = "test-key"
    fake_client.base_url = "https://llm.example/v1"
    monkeypatch.setattr(compat, "get_llm_client", lambda: fake_client)
    compat._daily_call_count = 0
    compat._daily_call_date = None

    client = compat.get_client()

    assert client.client is fake_client
    assert client.api_key == "test-key"


def test_legacy_inline_audio_converts_to_openai_compatible_content():
    contents = [
        compat.Content(
            role="user",
            parts=[
                compat.Part(text="Transcribe this"),
                compat.Part.from_bytes(data=b"audio-bytes", mime_type="audio/ogg"),
            ],
        )
    ]

    request = compat._legacy_request("qwen-audio", contents, {})
    content = request.messages[0].content

    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "Transcribe this"}
    assert content[1]["type"] == "input_audio"
    assert content[1]["input_audio"]["format"] == "ogg"


def test_daily_limit_blocks_after_max(monkeypatch):
    monkeypatch.setattr(compat, "_daily_call_count", compat._MAX_DAILY_CALLS)
    monkeypatch.setattr(compat, "_daily_call_date", compat.utc_now().date())

    with pytest.raises(RuntimeError, match="Daily AI call limit"):
        compat.get_client()


def test_grounded_config_does_not_fake_google_search():
    assert compat.get_grounded_config() == {}


@pytest.mark.asyncio
async def test_context_cache_skips_without_real_ai_key(monkeypatch):
    monkeypatch.setattr(compat.settings, "AI_PROVIDER", "qwen")
    monkeypatch.setattr(compat.settings, "QWEN_API_KEY", "")

    assert await compat.cache_portfolio_context(MagicMock()) is None


def test_qwen_configuration_and_legacy_boolean_alias():
    configured = _settings()
    missing = _settings(QWEN_API_KEY="")

    assert configured.ai_configured is True
    assert configured.gemini_configured is True
    assert missing.ai_configured is False
    assert missing.gemini_configured is False


def test_optional_extensions_are_disabled_by_default():
    current = Settings(_env_file=None)

    assert current.ENABLE_POLYMARKET is False
    assert current.ENABLE_TELEGRAM is False
    assert current.ENABLE_PARQET is False
    assert current.ENABLE_SHADOW_AGENT is False
    assert current.ENABLE_TECH_RADAR is False
    assert current.ENABLE_TRADE_ADVISOR is False
    assert current.ENABLE_LEGACY_SQLITE_COMPAT is False
