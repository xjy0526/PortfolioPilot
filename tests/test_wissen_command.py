"""Tests für den /wissen Telegram-Befehl."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


class TestWissenCommandRouting:
    """Tests dass /wissen korrekt geroutet wird."""

    @pytest.mark.asyncio
    async def test_wissen_routed(self):
        """Prüft dass /wissen den Command-Handler aufruft."""
        with patch("services.telegram_bot.settings") as mock_s, \
             patch("services.telegram_bot._cmd_wissen", new_callable=AsyncMock) as mock_cmd:
            mock_s.TELEGRAM_CHAT_ID = "123"

            from services.telegram_bot import handle_update
            update = {"message": {"text": "/wissen", "chat": {"id": 123}}}
            await handle_update(update)

            mock_cmd.assert_called_once_with("123", [])

    @pytest.mark.asyncio
    async def test_wissen_with_args_routed(self):
        """Prüft dass /wissen portfoliopilot korrekt weiterleitet."""
        with patch("services.telegram_bot.settings") as mock_s, \
             patch("services.telegram_bot._cmd_wissen", new_callable=AsyncMock) as mock_cmd:
            mock_s.TELEGRAM_CHAT_ID = "123"

            from services.telegram_bot import handle_update
            update = {"message": {"text": "/wissen portfoliopilot", "chat": {"id": 123}}}
            await handle_update(update)

            mock_cmd.assert_called_once_with("123", ["portfoliopilot"])

    @pytest.mark.asyncio
    async def test_wissen_quiz_routed(self):
        """Prüft dass /wissen quiz korrekt weiterleitet."""
        with patch("services.telegram_bot.settings") as mock_s, \
             patch("services.telegram_bot._cmd_wissen", new_callable=AsyncMock) as mock_cmd:
            mock_s.TELEGRAM_CHAT_ID = "123"

            from services.telegram_bot import handle_update
            update = {"message": {"text": "/wissen quiz", "chat": {"id": 123}}}
            await handle_update(update)

            mock_cmd.assert_called_once_with("123", ["quiz"])


class TestWissenTipOfDay:
    """Tests für den Tipp des Tages."""

    def test_wissen_tip_has_required_fields(self):
        """Prüft dass der Tipp des Tages alle nötigen Felder hat."""
        from services.knowledge_data import get_daily_tip
        tip = get_daily_tip()
        assert isinstance(tip, dict)
        assert "title" in tip
        assert "text" in tip
        assert "category" in tip
        assert len(tip["text"]) > 10

    def test_wissen_tip_text_telegram_safe(self):
        """Prüft dass der Tipp-Text unter Telegram-Limit bleibt."""
        from services.knowledge_data import get_daily_tip
        tip = get_daily_tip()
        header = f"🧠 *Wissen des Tages*\n_{tip['category']}_ • {tip['title']}\n\n"
        full_message = header + tip["text"]
        assert len(full_message) < 4096  # Telegram-Limit


class TestWissenProjectSummary:
    """Tests für Projekt-Zusammenfassungen via /wissen."""

    def test_portfoliopilot_contains_key_info(self):
        from services.knowledge_data import get_project_summary
        summary = get_project_summary("portfoliopilot")
        assert "FastAPI" in summary
        assert "Docker" in summary

    def test_pokerpro_contains_key_info(self):
        from services.knowledge_data import get_project_summary
        summary = get_project_summary("pokerpro")
        assert "GTO" in summary or "Poker" in summary or "PokerPro" in summary

    def test_unknown_project_graceful(self):
        from services.knowledge_data import get_project_summary
        summary = get_project_summary("gibts_nicht")
        assert "Unbekanntes Projekt" in summary
        assert "Verfügbar" in summary


class TestHelpContainsWissen:
    """Prüft dass /help und /start den /wissen Befehl listen."""

    @pytest.mark.asyncio
    async def test_help_includes_wissen(self):
        with patch("services.telegram_bot.settings") as mock_s, \
             patch("services.telegram.send_message", new_callable=AsyncMock) as mock_send:
            mock_s.TELEGRAM_CHAT_ID = "123"
            mock_send.return_value = True

            from services.telegram_bot import _cmd_help
            await _cmd_help("123")

            sent_text = mock_send.call_args[0][0]
            assert "/wissen" in sent_text

    @pytest.mark.asyncio
    async def test_start_includes_wissen(self):
        with patch("services.telegram_bot.settings") as mock_s, \
             patch("services.telegram.send_message", new_callable=AsyncMock) as mock_send:
            mock_s.TELEGRAM_CHAT_ID = "123"
            mock_send.return_value = True

            from services.telegram_bot import _cmd_start
            await _cmd_start("123")

            sent_text = mock_send.call_args[0][0]
            assert "/wissen" in sent_text
