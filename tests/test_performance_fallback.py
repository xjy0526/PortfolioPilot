from models import PortfolioPosition, PortfolioSummary, StockFullData
from routes.analytics import _build_summary_performance


def test_build_summary_performance_for_csv_holdings():
    summary = PortfolioSummary(
        stocks=[
            StockFullData(position=PortfolioPosition(
                ticker="NVDA",
                name="NVIDIA",
                shares=2,
                avg_cost=100,
                current_price=125,
                sector="Technology",
            )),
            StockFullData(position=PortfolioPosition(
                ticker="AMD",
                name="Advanced Micro Devices",
                shares=1,
                avg_cost=80,
                current_price=70,
                sector="Technology",
            )),
        ]
    )

    result = _build_summary_performance(summary, source="csv")

    assert result["source"] == "csv"
    assert result["activeHoldings"] == 2
    assert result["soldHoldings"] == 0
    assert len(result["holdingsActive"]) == 2
    assert result["kpis"]["valuation"] == 320.0
    assert result["kpis"]["unrealizedGains"]["gainGross"] == 40.0
    assert result["holdingsActive"][0]["ticker"] == "NVDA"
    assert result["holdingsActive"][0]["currentValue"] == 250.0
