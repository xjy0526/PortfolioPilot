"""PortfolioPilot - AI Trade Advisor Tests v2.

Tests für:
  - Portfolio Context Builder
  - Function Calling Tool-Definitionen & Execution
  - Response Parsing (Structured Output)
  - Error Handling (kein Gemini konfiguriert)
"""
import json
import pytest
from unittest.mock import patch, MagicMock

from services.trade_advisor import (
    _build_portfolio_context,
    _build_tool_declarations,
    _execute_tool_call,
    _parse_ai_response,
    ADVISOR_RESPONSE_SCHEMA,
)


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

class MockPosition:
    def __init__(self, ticker, name="", sector="", current_value=1000, current_price=100,
                 total_cost=900, pnl_percent=11.1, daily_change_pct=None):
        self.ticker = ticker
        self.name = name
        self.sector = sector
        self.current_value = current_value
        self.current_price = current_price
        self.total_cost = total_cost
        self.pnl_percent = pnl_percent
        self.daily_change_pct = daily_change_pct


class MockScore:
    def __init__(self, total_score=75.0, rating_value="buy", confidence=0.8):
        self.total_score = total_score
        self.confidence = confidence

        class _Rating:
            def __init__(self, v):
                self.value = v
        self.rating = _Rating(rating_value)

        class _Breakdown:
            quality_score = 70
            valuation_score = 65
            analyst_score = 80
            technical_score = 60
            momentum_score = 55
            sentiment_score = 50
        self.breakdown = _Breakdown()


class MockStock:
    def __init__(self, ticker, name="", sector="Tech", value=1000, score_val=75):
        self.position = MockPosition(ticker, name, sector, current_value=value)
        self.score = MockScore(score_val) if score_val else None


class MockSummary:
    def __init__(self):
        self.total_value = 10000
        self.total_pnl_percent = 15.5
        self.num_positions = 3
        self.stocks = [
            MockStock("AAPL", "Apple", "Technology", 4000, 78),
            MockStock("MSFT", "Microsoft", "Technology", 3000, 72),
            MockStock("JNJ", "Johnson & Johnson", "Healthcare", 3000, 65),
        ]

        class _FG:
            value = 55
            label = "Greed"
        self.fear_greed = _FG()


# ─────────────────────────────────────────────────────────────
# Tests: Portfolio Context
# ─────────────────────────────────────────────────────────────

class TestBuildPortfolioContext:
    def test_builds_sector_distribution(self):
        summary = MockSummary()
        ctx = _build_portfolio_context(summary, "NVDA", "buy", 2000)

        assert "Technology" in ctx["sector_distribution"]
        assert "Healthcare" in ctx["sector_distribution"]
        assert ctx["total_value"] == 10000
        assert ctx["num_positions"] == 3

    def test_includes_fear_greed(self):
        summary = MockSummary()
        ctx = _build_portfolio_context(summary, "NVDA", "buy", None)

        assert ctx["fear_greed"] == 55
        assert ctx["fear_greed_label"] == "Greed"

    def test_top_positions_ordered_by_weight(self):
        summary = MockSummary()
        ctx = _build_portfolio_context(summary, "NVDA", "buy", None)

        assert len(ctx["top_positions"]) == 3
        assert ctx["top_positions"][0]["ticker"] == "AAPL"  # Highest value


# ─────────────────────────────────────────────────────────────
# Tests: Function Calling Tools (Feature 2)
# ─────────────────────────────────────────────────────────────

class TestToolDeclarations:
    def test_has_four_tools(self):
        """Feature 2: Should define 4 callable tools (including fetch_url_content)."""
        tools = _build_tool_declarations()
        assert len(tools) == 4

    def test_tool_names(self):
        tools = _build_tool_declarations()
        names = {t["name"] for t in tools}
        assert "get_stock_score" in names
        assert "get_portfolio_overview" in names
        assert "get_sector_impact" in names
        assert "fetch_url_content" in names

    def test_tools_have_parameters(self):
        tools = _build_tool_declarations()
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "parameters" in tool


class TestToolExecution:
    @pytest.mark.asyncio
    async def test_get_stock_score(self):
        score_info = {"total_score": 75, "rating": "buy"}
        result = await _execute_tool_call("get_stock_score", {"ticker": "AAPL"}, score_info, {})
        parsed = json.loads(result)
        assert parsed["total_score"] == 75
        assert parsed["rating"] == "buy"

    @pytest.mark.asyncio
    async def test_get_portfolio_overview(self):
        portfolio_ctx = {
            "total_value": 10000,
            "num_positions": 5,
            "total_pnl_pct": 12.5,
            "fear_greed": 55,
            "fear_greed_label": "Greed",
            "sector_distribution": {"Tech": 40, "Health": 30},
            "top_positions": [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
        }
        result = await _execute_tool_call("get_portfolio_overview", {}, {}, portfolio_ctx)
        parsed = json.loads(result)
        assert parsed["total_value"] == 10000
        assert parsed["num_positions"] == 5
        assert "Tech" in parsed["sector_distribution"]

    @pytest.mark.asyncio
    async def test_get_sector_impact(self):
        portfolio_ctx = {
            "impact": {
                "sector": "Technology",
                "sector_weight_before": 40.0,
                "sector_weight_after": 45.0,
            },
        }
        result = await _execute_tool_call("get_sector_impact", {}, {}, portfolio_ctx)
        parsed = json.loads(result)
        assert parsed["sector"] == "Technology"
        assert parsed["sector_weight_after"] == 45.0

    @pytest.mark.asyncio
    async def test_get_sector_impact_no_data(self):
        result = await _execute_tool_call("get_sector_impact", {}, {}, {"impact": {}})
        parsed = json.loads(result)
        assert "info" in parsed

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        result = await _execute_tool_call("unknown_tool", {}, {}, {})
        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_fetch_url_content_tool(self):
        """fetch_url_content Tool ruft url_fetcher auf."""
        with patch("services.url_fetcher.fetch_url_text") as mock_fetch:
            mock_fetch.return_value = "Artikel über NVIDIA..."
            result = await _execute_tool_call(
                "fetch_url_content",
                {"url": "https://example.com/nvidia-article"},
                {}, {},
            )
        parsed = json.loads(result)
        assert parsed["url"] == "https://example.com/nvidia-article"
        assert "NVIDIA" in parsed["content"]

    @pytest.mark.asyncio
    async def test_fetch_url_content_no_url(self):
        result = await _execute_tool_call("fetch_url_content", {}, {}, {})
        parsed = json.loads(result)
        assert "error" in parsed


# ─────────────────────────────────────────────────────────────
# Tests: Structured Output Schema (Feature 1)
# ─────────────────────────────────────────────────────────────

class TestAdvisorSchema:
    def test_schema_has_required_fields(self):
        assert "properties" in ADVISOR_RESPONSE_SCHEMA
        props = ADVISOR_RESPONSE_SCHEMA["properties"]
        assert "recommendation" in props
        assert "confidence" in props
        assert "summary" in props
        assert "risks" in props

    def test_recommendation_has_enum(self):
        rec = ADVISOR_RESPONSE_SCHEMA["properties"]["recommendation"]
        assert "enum" in rec
        assert set(rec["enum"]) == {"buy", "hold", "reduce", "avoid"}


# ─────────────────────────────────────────────────────────────
# Tests: Response Parsing
# ─────────────────────────────────────────────────────────────

class TestParseAiResponse:
    def test_parses_valid_json(self):
        raw = '{"recommendation": "buy", "confidence": 85, "summary": "Gutes Investment"}'
        result = _parse_ai_response(raw)

        assert result["recommendation"] == "buy"
        assert result["confidence"] == 85
        assert result["summary"] == "Gutes Investment"

    def test_strips_markdown_code_block(self):
        raw = '```json\n{"recommendation": "hold", "confidence": 60}\n```'
        result = _parse_ai_response(raw)

        assert result["recommendation"] == "hold"
        assert result["confidence"] == 60

    def test_fills_defaults_for_missing_fields(self):
        raw = '{"recommendation": "buy"}'
        result = _parse_ai_response(raw)

        assert result["recommendation"] == "buy"
        assert "risks" in result
        assert isinstance(result["risks"], list)
        assert "bull_case" in result

    def test_handles_invalid_json(self):
        raw = "Dies ist kein JSON sondern ein normaler Text"
        result = _parse_ai_response(raw)

        assert result["recommendation"] == "hold"
        assert raw[:100] in result.get("summary", "") or "raw_response" in result

    def test_handles_empty_response(self):
        result = _parse_ai_response("")

        assert result["recommendation"] == "hold"


class TestEvaluateTradeNoGemini:
    @pytest.mark.asyncio
    async def test_without_gemini_returns_error(self):
        with patch("services.trade_advisor.settings") as mock_settings:
            mock_settings.gemini_configured = False
            from services.trade_advisor import evaluate_trade
            result = await evaluate_trade("NVDA", "buy")

            assert "error" in result
            assert "Qwen" in result["error"]
