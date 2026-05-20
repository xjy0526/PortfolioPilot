"""Tests für Tech-Radar: Multi-Faktor-Scoring und KI-Analyse."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from models import TechRecommendation, AnalystData


class TestCalcTechRadarScore:
    """Tests für den Multi-Faktor-Score."""

    def test_high_quality_stock(self):
        """Aktie mit starken Fundamentals bekommt hohen Score."""
        from fetchers.fmp import _calc_tech_radar_score

        score = _calc_tech_radar_score(
            roe=35.0,        # Exzellent
            gross_margin=75.0,  # Exzellent
            op_margin=35.0,     # Exzellent
            revenue_growth=30.0,  # Stark
            analyst_data=AnalystData(
                consensus="Buy",
                strong_buy_count=20, buy_count=15,
                hold_count=5, sell_count=0, strong_sell_count=0,
            ),
            upside=25.0,  # Starkes Upside
        )
        assert score >= 80.0, f"Starke Aktie sollte Score >= 80 haben, ist {score}"

    def test_low_quality_stock(self):
        """Aktie mit schwachen Fundamentals bekommt niedrigen Score."""
        from fetchers.fmp import _calc_tech_radar_score

        score = _calc_tech_radar_score(
            roe=-5.0,           # Negativ
            gross_margin=20.0,  # Niedrig
            op_margin=-10.0,    # Negativ
            revenue_growth=-15.0,  # Schrumpfend
            analyst_data=AnalystData(
                consensus="Sell",
                strong_buy_count=1, buy_count=2,
                hold_count=5, sell_count=8, strong_sell_count=4,
            ),
            upside=-15.0,  # Downside
        )
        assert score <= 35.0, f"Schwache Aktie sollte Score <= 35 haben, ist {score}"

    def test_medium_quality_stock(self):
        """Aktie mit mittleren Fundamentals bekommt mittleren Score."""
        from fetchers.fmp import _calc_tech_radar_score

        score = _calc_tech_radar_score(
            roe=15.0,
            gross_margin=45.0,
            op_margin=15.0,
            revenue_growth=10.0,
            analyst_data=AnalystData(
                consensus="Hold",
                strong_buy_count=3, buy_count=5,
                hold_count=8, sell_count=3, strong_sell_count=1,
            ),
            upside=5.0,
        )
        assert 40.0 <= score <= 70.0, f"Mittlere Aktie sollte Score 40-70 haben, ist {score}"

    def test_no_data_returns_default(self):
        """Ohne Daten: alles auf 50 (default)."""
        from fetchers.fmp import _calc_tech_radar_score

        score = _calc_tech_radar_score()
        assert score == 50.0, f"Default-Score sollte 50.0 sein, ist {score}"

    def test_partial_data(self):
        """Nur teilweise Daten: Fehlende Faktoren auf 50 (default)."""
        from fetchers.fmp import _calc_tech_radar_score

        score = _calc_tech_radar_score(
            roe=25.0,
            # Kein gross_margin, op_margin, growth, analyst, valuation
        )
        # Quality nur von ROE → 75, Rest default 50
        # 75*0.3 + 50*0.3 + 50*0.25 + 50*0.15 = 22.5 + 15 + 12.5 + 7.5 = 57.5
        assert 55.0 <= score <= 60.0, f"Partial-Score sollte ~57.5 sein, ist {score}"


class TestBuildTechTags:
    """Tests für Tag-Generierung aus Industry."""

    def test_semiconductor_industry(self):
        from fetchers.fmp import _build_tech_tags
        tags = _build_tech_tags("Semiconductors")
        assert "Tech" in tags
        assert "Semiconductor" in tags

    def test_software_cloud(self):
        from fetchers.fmp import _build_tech_tags
        tags = _build_tech_tags("Software - Infrastructure")
        assert "Tech" in tags
        assert "Software" in tags

    def test_cybersecurity(self):
        from fetchers.fmp import _build_tech_tags
        tags = _build_tech_tags("Software - Cybersecurity")
        assert "Cybersecurity" in tags
        assert "Software" in tags

    def test_empty_industry(self):
        from fetchers.fmp import _build_tech_tags
        tags = _build_tech_tags("")
        assert tags == ["Tech"]

    def test_unknown_industry(self):
        from fetchers.fmp import _build_tech_tags
        tags = _build_tech_tags("Some Unknown Industry XYZ")
        assert tags == ["Tech"]


class TestNormalizePctValue:
    """Tests für Prozent-Normalisierung."""

    def test_decimal_format(self):
        from fetchers.fmp import _normalize_pct_value
        assert _normalize_pct_value(0.25) == 25.0

    def test_already_percent(self):
        from fetchers.fmp import _normalize_pct_value
        assert _normalize_pct_value(25.0) == 25.0

    def test_none(self):
        from fetchers.fmp import _normalize_pct_value
        assert _normalize_pct_value(None) is None

    def test_negative_decimal(self):
        from fetchers.fmp import _normalize_pct_value
        assert _normalize_pct_value(-0.15) == -15.0


class TestTechRadarAI:
    """Tests für die KI-Analyse."""

    def test_parse_ai_response(self):
        from services.tech_radar_ai import _parse_ai_response
        text = (
            "NVDA: KI-Chip-Dominanz treibt Umsatz\n"
            "CRWD: Cybersecurity-Leader mit 35% Wachstum\n"
            "SNOW: Cloud-Data-Platform wächst stark\n"
        )
        result = _parse_ai_response(text)
        assert "NVDA" in result
        assert "CRWD" in result
        assert "SNOW" in result
        assert "KI-Chip" in result["NVDA"]

    def test_parse_ai_response_empty(self):
        from services.tech_radar_ai import _parse_ai_response
        result = _parse_ai_response("")
        assert result == {}

    def test_parse_ai_response_with_markdown(self):
        from services.tech_radar_ai import _parse_ai_response
        text = "* AAPL: **Starkes** Ökosystem\n- MSFT: Cloud-Wachstum"
        result = _parse_ai_response(text)
        assert "AAPL" in result
        assert "MSFT" in result

    @pytest.mark.asyncio
    async def test_enrich_skips_when_not_configured(self):
        """Ohne Gemini-Key bleiben ai_summary-Felder leer."""
        from services.tech_radar_ai import enrich_with_ai_analysis

        recommendations = [
            TechRecommendation(ticker="AAPL", name="Apple", score=80.0),
            TechRecommendation(ticker="MSFT", name="Microsoft", score=75.0),
        ]

        with patch("services.tech_radar_ai.settings") as mock_settings:
            mock_settings.gemini_configured = False
            result = await enrich_with_ai_analysis(recommendations)

        assert len(result) == 2
        assert result[0].ai_summary == ""
        assert result[1].ai_summary == ""

    @pytest.mark.asyncio
    async def test_enrich_calls_gemini(self):
        """Mit Mock-Gemini werden ai_summaries befüllt."""
        from services.tech_radar_ai import enrich_with_ai_analysis

        recommendations = [
            TechRecommendation(ticker="AAPL", name="Apple", score=80.0),
            TechRecommendation(ticker="MSFT", name="Microsoft", score=75.0),
        ]

        mock_response = MagicMock()
        mock_response.text = "AAPL: iPhone-Ökosystem bleibt stark\nMSFT: Cloud-Wachstum durch Azure"

        mock_aio_models = AsyncMock()
        mock_aio_models.generate_content.return_value = mock_response
        mock_aio = MagicMock()
        mock_aio.models = mock_aio_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with patch("services.tech_radar_ai.settings") as mock_settings, \
             patch("services.vertex_ai.get_client", return_value=mock_client), \
             patch("services.vertex_ai.get_grounded_config", return_value={}):
            mock_settings.gemini_configured = True
            result = await enrich_with_ai_analysis(recommendations)

        assert result[0].ai_summary == "iPhone-Ökosystem bleibt stark"
        assert result[1].ai_summary == "Cloud-Wachstum durch Azure"

    @pytest.mark.asyncio
    async def test_enrich_handles_gemini_error(self):
        """Bei Fehler → Empfehlungen unverändert."""
        from services.tech_radar_ai import enrich_with_ai_analysis

        recommendations = [
            TechRecommendation(ticker="AAPL", name="Apple", score=80.0),
        ]

        with patch("services.tech_radar_ai.settings") as mock_settings, \
             patch("services.vertex_ai.get_client", side_effect=Exception("API Error")):
            mock_settings.gemini_configured = True
            result = await enrich_with_ai_analysis(recommendations)

        assert len(result) == 1
        assert result[0].ai_summary == ""

    @pytest.mark.asyncio
    async def test_enrich_empty_list(self):
        """Leere Liste → leere Liste."""
        from services.tech_radar_ai import enrich_with_ai_analysis

        with patch("services.tech_radar_ai.settings") as mock_settings:
            mock_settings.gemini_configured = True
            result = await enrich_with_ai_analysis([])

        assert result == []


class TestTechRecommendationModel:
    """Tests für das erweiterte Model."""

    def test_new_fields_defaults(self):
        rec = TechRecommendation(ticker="TEST")
        assert rec.ai_summary == ""
        assert rec.revenue_growth is None
        assert rec.roe is None
        assert rec.source == "PortfolioPilot Tech-Radar"

    def test_new_fields_populated(self):
        rec = TechRecommendation(
            ticker="NVDA", name="NVIDIA", score=85.0,
            ai_summary="KI-Chip-Leader", revenue_growth=30.0, roe=45.0,
        )
        assert rec.ai_summary == "KI-Chip-Leader"
        assert rec.revenue_growth == 30.0
        assert rec.roe == 45.0
