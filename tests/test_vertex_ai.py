"""Tests für den AI compatibility service."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


class TestGetClient:
    """Tests für die Client-Erstellung."""

    def setup_method(self):
        """Reset daily counter between tests."""
        from services import vertex_ai
        vertex_ai._daily_call_count = 0
        vertex_ai._daily_call_date = None

    def test_qwen_client_when_configured(self):
        """Qwen Client wird erstellt wenn QWEN_API_KEY gesetzt ist."""
        from services.vertex_ai import get_client

        with patch("services.vertex_ai.settings") as mock_settings:
            mock_settings.QWEN_API_KEY = "test-qwen-key"
            mock_settings.GEMINI_API_KEY = ""
            mock_settings.QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            mock_settings.QWEN_MODEL = "qwen-plus"

            client = get_client()

        assert client.api_key == "test-qwen-key"
        assert "dashscope" in client.base_url

    def test_legacy_api_key_fallback(self):
        """Legacy GEMINI_API_KEY darf als kompatibler API-Key weiterverwendet werden."""
        from services.vertex_ai import get_client

        with patch("services.vertex_ai.settings") as mock_settings:
            mock_settings.QWEN_API_KEY = ""
            mock_settings.GEMINI_API_KEY = "test-api-key"
            mock_settings.QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            mock_settings.QWEN_MODEL = "qwen-plus"

            client = get_client()

        assert client.api_key == "test-api-key"

    def test_raises_when_nothing_configured(self):
        """RuntimeError wenn kein kompatibler API-Key konfiguriert ist."""
        from services.vertex_ai import get_client

        with patch("services.vertex_ai.settings") as mock_settings:
            mock_settings.QWEN_API_KEY = ""
            mock_settings.GEMINI_API_KEY = ""

            with pytest.raises(RuntimeError, match="QWEN_API_KEY"):
                get_client()

    def test_daily_limit_blocks_after_max(self):
        """Nach 100 Calls wird RuntimeError geworfen."""
        from services import vertex_ai
        vertex_ai._daily_call_count = 100  # Already at limit
        vertex_ai._daily_call_date = vertex_ai.date.today()

        with pytest.raises(RuntimeError, match="Tägliches AI-Call-Limit"):
            vertex_ai.get_client()

    def test_daily_limit_resets_at_midnight(self):
        """Counter resettet sich an neuem Tag."""
        from services import vertex_ai
        from datetime import date, timedelta
        vertex_ai._daily_call_count = 100
        vertex_ai._daily_call_date = date.today() - timedelta(days=1)  # Yesterday

        # Should NOT raise — new day resets counter
        with patch("services.vertex_ai.settings") as mock_settings:
            mock_settings.QWEN_API_KEY = "test-key"
            mock_settings.GEMINI_API_KEY = "test-key"
            mock_settings.QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            mock_settings.QWEN_MODEL = "qwen-plus"
            vertex_ai.get_client()

        assert vertex_ai._daily_call_count == 1


class TestGroundedConfig:
    """Tests für Search Grounding Config."""

    def test_returns_tools_config(self):
        """Config enthält Google Search Tool."""
        from services.vertex_ai import get_grounded_config

        config = get_grounded_config()

        assert "tools" in config
        assert len(config["tools"]) == 1


class TestContextCache:
    """Tests für Context Caching."""

    def test_get_cached_content_returns_none_initially(self):
        """Ohne Cache → None."""
        from services import vertex_ai
        vertex_ai._active_cache_name = None

        result = vertex_ai.get_cached_content()
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_skips_without_ai_key(self):
        """Context Caching wird übersprungen ohne AI-Key."""
        from services.vertex_ai import cache_portfolio_context

        mock_summary = MagicMock()

        with patch("services.vertex_ai.settings") as mock_settings:
            mock_settings.gemini_configured = False

            result = await cache_portfolio_context(mock_summary)

        assert result is None


class TestConfigSettings:
    """Tests für die Vertex AI Config-Properties."""

    def test_vertex_ai_configured_true(self):
        """vertex_ai_configured ist True mit GCP_PROJECT_ID."""
        with patch.dict("os.environ", {"GCP_PROJECT_ID": "test-project"}):
            from config import Settings
            s = Settings()
            s.GCP_PROJECT_ID = "test-project"
            assert s.vertex_ai_configured is True

    def test_vertex_ai_configured_false(self):
        """vertex_ai_configured ist False ohne GCP_PROJECT_ID."""
        from config import Settings
        s = Settings()
        s.GCP_PROJECT_ID = ""
        assert s.vertex_ai_configured is False

    def test_gemini_configured_via_vertex(self):
        """gemini_configured ist True wenn Vertex AI konfiguriert."""
        from config import Settings
        s = Settings()
        s.GCP_PROJECT_ID = "test-project"
        s.GEMINI_API_KEY = ""
        assert s.gemini_configured is True

    def test_qwen_configured_via_api_key(self):
        """gemini_configured bleibt für Kompatibilität True wenn QWEN_API_KEY vorhanden."""
        from config import Settings
        s = Settings()
        s.GCP_PROJECT_ID = ""
        s.QWEN_API_KEY = "test-key"
        assert s.gemini_configured is True

    def test_gemini_configured_neither(self):
        """gemini_configured ist False ohne Vertex AI und ohne API Key."""
        from config import Settings
        s = Settings()
        s.GCP_PROJECT_ID = ""
        s.QWEN_API_KEY = ""
        s.GEMINI_API_KEY = ""
        assert s.gemini_configured is False
