import pytest

from models import PortfolioPosition, PortfolioSummary, Rating, StockFullData, StockScore
from services.holding_recommendations import (
    build_rule_based_recommendations,
    generate_holding_recommendations,
)


def _stock(
    ticker: str,
    score: float,
    rating: Rating = Rating.HOLD,
    shares: float = 10,
    price: float = 100,
    cost: float = 90,
    sector: str = "Technology",
    asset_type: str = "equity",
    market: str = "US",
) -> StockFullData:
    position = PortfolioPosition(
        ticker=ticker,
        name=ticker,
        shares=shares,
        avg_cost=cost,
        current_price=price,
        sector=sector,
        asset_type=asset_type,
        market=market,
    )
    return StockFullData(
        position=position,
        score=StockScore(
            ticker=ticker,
            name=ticker,
            total_score=score,
            rating=rating,
            confidence=0.8,
        ),
    )


def _summary(stocks: list[StockFullData]) -> PortfolioSummary:
    total_value = sum(s.position.current_value for s in stocks)
    total_cost = sum(s.position.total_cost for s in stocks)
    return PortfolioSummary(
        total_value=total_value,
        total_cost=total_cost,
        total_pnl=total_value - total_cost,
        total_pnl_percent=((total_value - total_cost) / total_cost * 100) if total_cost else 0,
        num_positions=len(stocks),
        stocks=stocks,
    )


def test_rule_based_recommendations_cover_each_non_cash_holding():
    summary = _summary([
        _stock("AAPL", 82, Rating.BUY, shares=5, price=100),
        _stock("WEAK", 35, Rating.SELL, shares=10, price=80, cost=100, sector="Retail"),
        StockFullData(position=PortfolioPosition(ticker="CASH", name="Cash", shares=1, current_price=500)),
    ])

    report = build_rule_based_recommendations(summary, lang="zh")

    tickers = {item["ticker"] for item in report["recommendations"]}
    assert tickers == {"AAPL", "WEAK"}
    assert report["portfolio_score"] > 0
    assert report["recommendations"][0]["priority"] >= report["recommendations"][-1]["priority"]


def test_prediction_market_high_weight_is_trimmed():
    summary = _summary([
        _stock(
            "POLY-BTC",
            60,
            shares=100,
            price=1,
            cost=0.5,
            sector="Prediction Markets",
            asset_type="prediction_market",
            market="Polymarket",
        ),
        _stock("AAPL", 78, shares=20, price=50),
    ])

    report = build_rule_based_recommendations(summary, lang="en")
    poly = next(item for item in report["recommendations"] if item["ticker"] == "POLY-BTC")

    assert poly["action"] in {"trim", "review", "hold"}
    assert poly["asset_type"] == "prediction_market"
    assert "event risk" in poly["rationale"].lower()


@pytest.mark.asyncio
async def test_generate_recommendations_falls_back_without_qwen(monkeypatch):
    from config import settings
    from state import portfolio_data

    summary = _summary([_stock("AAPL", 82, Rating.BUY)])
    monkeypatch.setitem(portfolio_data, "summary", summary)
    monkeypatch.setattr(settings, "AI_PROVIDER", "qwen")
    monkeypatch.setattr(settings, "QWEN_API_KEY", "")

    report = await generate_holding_recommendations(lang="zh")

    assert report["source"] == "rule_based"
    assert report["ai_available"] is False
    assert report["recommendations"][0]["ticker"] == "AAPL"
