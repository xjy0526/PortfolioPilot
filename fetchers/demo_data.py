"""PortfolioPilot - Demo-Daten

Stellt ein Demo-Portfolio mit realistischen Daten bereit,
wenn keine API-Keys konfiguriert sind oder Parqet nicht erreichbar ist.
"""
from models import (
    AnalystData,
    FearGreedData,
    FmpRating,
    FundamentalData,
    PortfolioPosition,
    TechRecommendation,
    YFinanceData,
)


def get_demo_positions() -> list[PortfolioPosition]:
    """Demo-Portfolio mit populären Tech- und Blue-Chip-Aktien."""
    return [
        PortfolioPosition(ticker="AAPL", isin="US0378331005", name="Apple Inc.", shares=15, avg_cost=142.50, current_price=178.72, currency="USD", sector="Technology", daily_change_pct=1.8),
        PortfolioPosition(ticker="MSFT", isin="US5949181045", name="Microsoft Corp.", shares=10, avg_cost=285.00, current_price=415.30, currency="USD", sector="Technology", daily_change_pct=0.9),
        PortfolioPosition(ticker="NVDA", isin="US67066G1040", name="NVIDIA Corp.", shares=8, avg_cost=450.00, current_price=875.40, currency="USD", sector="Technology", daily_change_pct=3.2),
        PortfolioPosition(ticker="GOOGL", isin="US02079K3059", name="Alphabet Inc.", shares=12, avg_cost=105.00, current_price=167.85, currency="USD", sector="Technology", daily_change_pct=-0.4),
        PortfolioPosition(ticker="AMZN", isin="US0231351067", name="Amazon.com Inc.", shares=20, avg_cost=128.00, current_price=195.60, currency="USD", sector="Consumer Cyclical", daily_change_pct=1.1),
        PortfolioPosition(ticker="META", isin="US30303M1027", name="Meta Platforms Inc.", shares=6, avg_cost=280.00, current_price=505.75, currency="USD", sector="Technology", daily_change_pct=2.4),
        PortfolioPosition(ticker="TSLA", isin="US88160R1014", name="Tesla Inc.", shares=5, avg_cost=195.00, current_price=178.50, currency="USD", sector="Consumer Cyclical", daily_change_pct=-2.8),
        PortfolioPosition(ticker="ASML", isin="NL0010273215", name="ASML Holding N.V.", shares=3, avg_cost=620.00, current_price=910.25, currency="EUR", sector="Technology", daily_change_pct=0.6),
        PortfolioPosition(ticker="SAP", isin="DE0007164600", name="SAP SE", shares=25, avg_cost=125.00, current_price=198.40, currency="EUR", sector="Technology", daily_change_pct=1.5),
        PortfolioPosition(ticker="AVGO", isin="US11135F1012", name="Broadcom Inc.", shares=4, avg_cost=850.00, current_price=1680.50, currency="USD", sector="Technology", daily_change_pct=1.9),
        PortfolioPosition(ticker="CRM", isin="US79466L3024", name="Salesforce Inc.", shares=8, avg_cost=210.00, current_price=312.40, currency="USD", sector="Technology", daily_change_pct=-1.2),
        PortfolioPosition(ticker="AMD", isin="US0079031078", name="AMD Inc.", shares=15, avg_cost=95.00, current_price=168.75, currency="USD", sector="Technology", daily_change_pct=2.1),
    ]


def get_demo_fundamentals() -> dict[str, FundamentalData]:
    """Demo-Fundamentaldaten (inkl. Altman Z-Score & Piotroski)."""
    return {
        "AAPL": FundamentalData(pe_ratio=28.5, pb_ratio=45.2, roe=1.56, debt_to_equity=1.87, current_ratio=0.98, gross_margin=0.462, operating_margin=0.304, net_margin=0.261, market_cap=2750000000000, beta=1.21, dividend_yield=0.005, altman_z_score=5.2, piotroski_score=7),
        "MSFT": FundamentalData(pe_ratio=35.2, pb_ratio=12.8, roe=0.39, debt_to_equity=0.42, current_ratio=1.77, gross_margin=0.705, operating_margin=0.445, net_margin=0.362, market_cap=3080000000000, beta=0.89, dividend_yield=0.007, altman_z_score=8.1, piotroski_score=8),
        "NVDA": FundamentalData(pe_ratio=65.4, pb_ratio=52.3, roe=1.15, debt_to_equity=0.41, current_ratio=4.17, gross_margin=0.760, operating_margin=0.620, net_margin=0.553, market_cap=2150000000000, beta=1.68, altman_z_score=12.5, piotroski_score=8),
        "GOOGL": FundamentalData(pe_ratio=24.1, pb_ratio=6.8, roe=0.28, debt_to_equity=0.10, current_ratio=2.10, gross_margin=0.574, operating_margin=0.321, net_margin=0.257, market_cap=2080000000000, beta=1.05, altman_z_score=9.8, piotroski_score=7),
        "AMZN": FundamentalData(pe_ratio=58.3, pb_ratio=8.9, roe=0.21, debt_to_equity=0.56, current_ratio=1.05, gross_margin=0.478, operating_margin=0.076, net_margin=0.062, market_cap=2020000000000, beta=1.15, altman_z_score=4.2, piotroski_score=6),
        "META": FundamentalData(pe_ratio=25.8, pb_ratio=8.2, roe=0.30, debt_to_equity=0.32, current_ratio=2.68, gross_margin=0.810, operating_margin=0.406, net_margin=0.356, market_cap=1290000000000, beta=1.24, altman_z_score=7.6, piotroski_score=7),
        "TSLA": FundamentalData(pe_ratio=48.7, pb_ratio=11.5, roe=0.21, debt_to_equity=0.11, current_ratio=1.73, gross_margin=0.182, operating_margin=0.087, net_margin=0.078, market_cap=568000000000, beta=2.05, altman_z_score=3.1, piotroski_score=4),
        "ASML": FundamentalData(pe_ratio=42.3, pb_ratio=22.1, roe=0.76, debt_to_equity=0.44, current_ratio=1.45, gross_margin=0.512, operating_margin=0.365, net_margin=0.282, market_cap=362000000000, beta=1.15, altman_z_score=6.8, piotroski_score=7),
        "SAP": FundamentalData(pe_ratio=38.5, pb_ratio=5.8, roe=0.15, debt_to_equity=0.48, current_ratio=1.12, gross_margin=0.725, operating_margin=0.288, net_margin=0.168, market_cap=243000000000, beta=0.95, dividend_yield=0.011, altman_z_score=5.5, piotroski_score=6),
        "AVGO": FundamentalData(pe_ratio=35.8, pb_ratio=11.2, roe=0.42, debt_to_equity=1.64, current_ratio=1.10, gross_margin=0.740, operating_margin=0.465, net_margin=0.392, market_cap=780000000000, beta=1.18, dividend_yield=0.012, altman_z_score=4.8, piotroski_score=7),
        "CRM": FundamentalData(pe_ratio=52.1, pb_ratio=4.5, roe=0.08, debt_to_equity=0.20, current_ratio=1.02, gross_margin=0.755, operating_margin=0.218, net_margin=0.148, market_cap=303000000000, beta=1.12, altman_z_score=5.9, piotroski_score=5),
        "AMD": FundamentalData(pe_ratio=42.8, pb_ratio=4.2, roe=0.04, debt_to_equity=0.04, current_ratio=2.51, gross_margin=0.498, operating_margin=0.235, net_margin=0.052, market_cap=272000000000, beta=1.72, altman_z_score=8.3, piotroski_score=6),
    }


def get_demo_analyst_data() -> dict[str, AnalystData]:
    """Demo-Analysten-Daten (inkl. Strong Buy/Sell)."""
    return {
        "AAPL": AnalystData(consensus="Buy", target_price=200.00, num_analysts=42, strong_buy_count=12, buy_count=18, hold_count=10, sell_count=1, strong_sell_count=1),
        "MSFT": AnalystData(consensus="Buy", target_price=470.00, num_analysts=48, strong_buy_count=20, buy_count=20, hold_count=7, sell_count=1, strong_sell_count=0),
        "NVDA": AnalystData(consensus="Buy", target_price=1050.00, num_analysts=52, strong_buy_count=28, buy_count=18, hold_count=5, sell_count=1, strong_sell_count=0),
        "GOOGL": AnalystData(consensus="Buy", target_price=195.00, num_analysts=45, strong_buy_count=18, buy_count=20, hold_count=6, sell_count=1, strong_sell_count=0),
        "AMZN": AnalystData(consensus="Buy", target_price=225.00, num_analysts=50, strong_buy_count=22, buy_count=20, hold_count=6, sell_count=1, strong_sell_count=1),
        "META": AnalystData(consensus="Buy", target_price=575.00, num_analysts=44, strong_buy_count=18, buy_count=20, hold_count=5, sell_count=1, strong_sell_count=0),
        "TSLA": AnalystData(consensus="Hold", target_price=195.00, num_analysts=40, strong_buy_count=3, buy_count=9, hold_count=18, sell_count=7, strong_sell_count=3),
        "ASML": AnalystData(consensus="Buy", target_price=1050.00, num_analysts=28, strong_buy_count=10, buy_count=12, hold_count=5, sell_count=1, strong_sell_count=0),
        "SAP": AnalystData(consensus="Buy", target_price=225.00, num_analysts=32, strong_buy_count=10, buy_count=14, hold_count=7, sell_count=1, strong_sell_count=0),
        "AVGO": AnalystData(consensus="Buy", target_price=1900.00, num_analysts=30, strong_buy_count=14, buy_count=12, hold_count=3, sell_count=1, strong_sell_count=0),
        "CRM": AnalystData(consensus="Hold", target_price=340.00, num_analysts=38, strong_buy_count=6, buy_count=12, hold_count=16, sell_count=3, strong_sell_count=1),
        "AMD": AnalystData(consensus="Buy", target_price=200.00, num_analysts=42, strong_buy_count=14, buy_count=18, hold_count=8, sell_count=2, strong_sell_count=0),
    }


def get_demo_fmp_ratings() -> dict[str, FmpRating]:
    """Demo-FMP Ratings."""
    return {
        "AAPL": FmpRating(rating="A", rating_score=4, dcf_score=4, roe_score=5, roa_score=4, de_score=3, pe_score=4, pb_score=3),
        "MSFT": FmpRating(rating="A+", rating_score=5, dcf_score=4, roe_score=4, roa_score=4, de_score=5, pe_score=4, pb_score=4),
        "NVDA": FmpRating(rating="A", rating_score=4, dcf_score=3, roe_score=5, roa_score=5, de_score=5, pe_score=2, pb_score=2),
        "GOOGL": FmpRating(rating="A+", rating_score=5, dcf_score=5, roe_score=4, roa_score=4, de_score=5, pe_score=5, pb_score=4),
        "AMZN": FmpRating(rating="B+", rating_score=3, dcf_score=3, roe_score=3, roa_score=3, de_score=4, pe_score=2, pb_score=3),
        "META": FmpRating(rating="A", rating_score=4, dcf_score=4, roe_score=4, roa_score=4, de_score=5, pe_score=4, pb_score=4),
        "TSLA": FmpRating(rating="C+", rating_score=2, dcf_score=2, roe_score=3, roa_score=2, de_score=5, pe_score=2, pb_score=2),
        "ASML": FmpRating(rating="A-", rating_score=4, dcf_score=3, roe_score=5, roa_score=4, de_score=4, pe_score=3, pb_score=3),
        "SAP": FmpRating(rating="B+", rating_score=3, dcf_score=3, roe_score=3, roa_score=3, de_score=4, pe_score=3, pb_score=4),
        "AVGO": FmpRating(rating="A-", rating_score=4, dcf_score=4, roe_score=4, roa_score=4, de_score=3, pe_score=4, pb_score=3),
        "CRM": FmpRating(rating="B", rating_score=3, dcf_score=3, roe_score=2, roa_score=3, de_score=5, pe_score=2, pb_score=4),
        "AMD": FmpRating(rating="B+", rating_score=3, dcf_score=3, roe_score=2, roa_score=2, de_score=5, pe_score=3, pb_score=4),
    }




def get_demo_tech_picks() -> list[TechRecommendation]:
    """Demo Tech-Empfehlungen (Tech-Radar v2)."""
    return [
        TechRecommendation(ticker="PLTR", name="Palantir Technologies", current_price=24.50, market_cap=54000000000, pe_ratio=62.5, analyst_rating="Buy", target_price=30.00, upside_percent=22.4, ai_score=8.2, score=82.0, reason="ROE 28% | Revenue +25% | Marge 62% | Konsens: Buy | Upside: 22.4%", tags=["AI", "Software", "Data"], ai_summary="KI-Leader im Government & Enterprise – starkes AIP-Wachstum treibt Profitabilität", revenue_growth=25.0, roe=28.0),
        TechRecommendation(ticker="CRWD", name="CrowdStrike Holdings", current_price=315.00, market_cap=75000000000, pe_ratio=85.0, analyst_rating="Buy", target_price=380.00, upside_percent=20.6, ai_score=7.8, score=78.0, reason="ROE 18% | Revenue +33% | Marge 75% | Konsens: Buy | Upside: 20.6%", tags=["Tech", "Cybersecurity", "Cloud"], ai_summary="Cybersecurity-Marktführer mit XDR-Plattform – Recovery nach Vorjahres-Incident läuft", revenue_growth=33.0, roe=18.0),
        TechRecommendation(ticker="SNOW", name="Snowflake Inc.", current_price=168.00, market_cap=55000000000, pe_ratio=None, analyst_rating="Buy", target_price=210.00, upside_percent=25.0, ai_score=7.5, score=75.0, reason="Revenue +30% | Marge 68% | Konsens: Buy | Upside: 25.0%", tags=["Tech", "Cloud", "Data", "AI"], ai_summary="Cloud-Data-Platform mit AI-Workloads – starkes Kundenwachstum, aber noch nicht profitabel", revenue_growth=30.0, roe=None),
        TechRecommendation(ticker="PANW", name="Palo Alto Networks", current_price=310.00, market_cap=98000000000, pe_ratio=48.0, analyst_rating="Buy", target_price=365.00, upside_percent=17.7, ai_score=7.9, score=79.0, reason="ROE 22% | Revenue +20% | Marge 72% | Konsens: Buy | Upside: 17.7%", tags=["Tech", "Cybersecurity", "Cloud"], ai_summary="Plattform-Konsolidierung zahlt sich aus – SASE & Cortex XSIAM wachsen zweistellig", revenue_growth=20.0, roe=22.0),
        TechRecommendation(ticker="MDB", name="MongoDB Inc.", current_price=365.00, market_cap=26000000000, pe_ratio=None, analyst_rating="Buy", target_price=440.00, upside_percent=20.5, ai_score=7.2, score=72.0, reason="Revenue +22% | Marge 71% | Konsens: Buy | Upside: 20.5%", tags=["Tech", "Cloud", "Data"], ai_summary="Führende NoSQL-Datenbank – AI-Workloads und Atlas-Cloud treiben Wachstum", revenue_growth=22.0, roe=None),
    ]


def get_demo_yfinance_data() -> dict[str, YFinanceData]:
    """Demo-YFinance-Daten (Insider, ESG, Recommendations, Earnings)."""
    from datetime import datetime, timedelta
    # Earnings-Termine realistisch über nächste 3 Monate verteilen
    base = datetime.now()
    return {
        "AAPL": YFinanceData(recommendation_trend="Buy", insider_buy_count=3, insider_sell_count=8, esg_risk_score=16.7, earnings_growth_yoy=8.5, next_earnings_date=(base + timedelta(days=12)).strftime("%Y-%m-%d"), earnings_beat_rate=0.89, earnings_surprise_avg=4.2),
        "MSFT": YFinanceData(recommendation_trend="Buy", insider_buy_count=5, insider_sell_count=4, esg_risk_score=14.2, earnings_growth_yoy=18.3, next_earnings_date=(base + timedelta(days=18)).strftime("%Y-%m-%d"), earnings_beat_rate=0.92, earnings_surprise_avg=5.1),
        "NVDA": YFinanceData(recommendation_trend="Buy", insider_buy_count=2, insider_sell_count=12, esg_risk_score=12.8, earnings_growth_yoy=265.0, next_earnings_date=(base + timedelta(days=25)).strftime("%Y-%m-%d"), earnings_beat_rate=0.95, earnings_surprise_avg=12.8),
        "GOOGL": YFinanceData(recommendation_trend="Buy", insider_buy_count=4, insider_sell_count=6, esg_risk_score=18.5, earnings_growth_yoy=42.1, next_earnings_date=(base + timedelta(days=30)).strftime("%Y-%m-%d"), earnings_beat_rate=0.88, earnings_surprise_avg=6.3),
        "AMZN": YFinanceData(recommendation_trend="Buy", insider_buy_count=1, insider_sell_count=15, esg_risk_score=28.4, earnings_growth_yoy=155.0, next_earnings_date=(base + timedelta(days=35)).strftime("%Y-%m-%d"), earnings_beat_rate=0.82, earnings_surprise_avg=8.5),
        "META": YFinanceData(recommendation_trend="Buy", insider_buy_count=0, insider_sell_count=10, esg_risk_score=24.3, earnings_growth_yoy=73.2, next_earnings_date=(base + timedelta(days=14)).strftime("%Y-%m-%d"), earnings_beat_rate=0.90, earnings_surprise_avg=7.2),
        "TSLA": YFinanceData(recommendation_trend="Hold", insider_buy_count=1, insider_sell_count=18, esg_risk_score=32.5, earnings_growth_yoy=-23.4, next_earnings_date=(base + timedelta(days=20)).strftime("%Y-%m-%d"), earnings_beat_rate=0.65, earnings_surprise_avg=-2.1),
        "ASML": YFinanceData(recommendation_trend="Buy", insider_buy_count=3, insider_sell_count=2, esg_risk_score=11.5, earnings_growth_yoy=12.8, next_earnings_date=(base + timedelta(days=42)).strftime("%Y-%m-%d"), earnings_beat_rate=0.85, earnings_surprise_avg=3.8),
        "SAP": YFinanceData(recommendation_trend="Buy", insider_buy_count=6, insider_sell_count=3, esg_risk_score=8.9, earnings_growth_yoy=21.5, next_earnings_date=(base + timedelta(days=50)).strftime("%Y-%m-%d"), earnings_beat_rate=0.78, earnings_surprise_avg=2.5),
        "AVGO": YFinanceData(recommendation_trend="Buy", insider_buy_count=2, insider_sell_count=5, esg_risk_score=15.3, earnings_growth_yoy=44.8, next_earnings_date=(base + timedelta(days=55)).strftime("%Y-%m-%d"), earnings_beat_rate=0.91, earnings_surprise_avg=6.8),
        "CRM": YFinanceData(recommendation_trend="Hold", insider_buy_count=2, insider_sell_count=7, esg_risk_score=19.8, earnings_growth_yoy=35.2, next_earnings_date=(base + timedelta(days=22)).strftime("%Y-%m-%d"), earnings_beat_rate=0.80, earnings_surprise_avg=3.2),
        "AMD": YFinanceData(recommendation_trend="Buy", insider_buy_count=4, insider_sell_count=3, esg_risk_score=13.6, earnings_growth_yoy=62.0, next_earnings_date=(base + timedelta(days=28)).strftime("%Y-%m-%d"), earnings_beat_rate=0.87, earnings_surprise_avg=5.5),
    }




def get_demo_fear_greed() -> FearGreedData:
    """Demo Fear & Greed Index."""
    return FearGreedData(value=62, label="Greed", source="Demo")


def get_demo_technical_indicators() -> dict[str, "TechnicalIndicators"]:
    """Demo-Technische Indikatoren für alle Demo-Positionen."""
    from models import TechnicalIndicators
    return {
        "AAPL": TechnicalIndicators(rsi_14=58.3, sma_50=172.40, sma_200=165.80, price_vs_sma50=3.7, sma_cross="golden", momentum_30d=4.2, momentum_90d=8.5, momentum_180d=12.1, signal="Bullish"),
        "MSFT": TechnicalIndicators(rsi_14=62.1, sma_50=405.20, sma_200=378.50, price_vs_sma50=2.5, sma_cross="golden", momentum_30d=3.8, momentum_90d=10.2, momentum_180d=15.3, signal="Bullish"),
        "NVDA": TechnicalIndicators(rsi_14=71.5, sma_50=820.00, sma_200=650.00, price_vs_sma50=6.8, sma_cross="golden", momentum_30d=8.5, momentum_90d=25.4, momentum_180d=45.2, signal="Bullish"),
        "GOOGL": TechnicalIndicators(rsi_14=55.8, sma_50=162.30, sma_200=148.70, price_vs_sma50=3.4, sma_cross="golden", momentum_30d=2.1, momentum_90d=7.8, momentum_180d=14.5, signal="Bullish"),
        "AMZN": TechnicalIndicators(rsi_14=60.4, sma_50=188.50, sma_200=172.30, price_vs_sma50=3.8, sma_cross="golden", momentum_30d=5.2, momentum_90d=12.3, momentum_180d=18.7, signal="Bullish"),
        "META": TechnicalIndicators(rsi_14=64.7, sma_50=485.00, sma_200=420.50, price_vs_sma50=4.3, sma_cross="golden", momentum_30d=6.1, momentum_90d=15.8, momentum_180d=28.4, signal="Bullish"),
        "TSLA": TechnicalIndicators(rsi_14=42.3, sma_50=195.80, sma_200=210.50, price_vs_sma50=-8.8, sma_cross="death", momentum_30d=-5.2, momentum_90d=-12.1, momentum_180d=-8.5, signal="Bearish"),
        "ASML": TechnicalIndicators(rsi_14=57.2, sma_50=880.00, sma_200=820.00, price_vs_sma50=3.4, sma_cross="golden", momentum_30d=3.5, momentum_90d=9.2, momentum_180d=16.8, signal="Bullish"),
        "SAP": TechnicalIndicators(rsi_14=61.8, sma_50=190.20, sma_200=172.50, price_vs_sma50=4.3, sma_cross="golden", momentum_30d=4.8, momentum_90d=11.5, momentum_180d=20.3, signal="Bullish"),
        "AVGO": TechnicalIndicators(rsi_14=66.5, sma_50=1580.00, sma_200=1350.00, price_vs_sma50=6.4, sma_cross="golden", momentum_30d=7.2, momentum_90d=18.5, momentum_180d=32.1, signal="Bullish"),
        "CRM": TechnicalIndicators(rsi_14=48.5, sma_50=305.00, sma_200=280.00, price_vs_sma50=2.4, sma_cross="golden", momentum_30d=1.5, momentum_90d=5.8, momentum_180d=10.2, signal="Neutral"),
        "AMD": TechnicalIndicators(rsi_14=53.2, sma_50=158.00, sma_200=142.50, price_vs_sma50=6.8, sma_cross="golden", momentum_30d=3.2, momentum_90d=14.5, momentum_180d=22.8, signal="Bullish"),
    }


def get_demo_portfolio_history(days: int = 180) -> list[dict]:
    """Generiert synthetische Portfolio-Verlaufsdaten für den Demo-Modus.

    Simuliert ein realistisch wachsendes Portfolio mit:
    - Steigendem investiertem Kapital (monatliche Einzahlungen)
    - Marktschwankungen (±2% täglich)
    - Insgesamt positivem Trend
    """
    import random
    from datetime import datetime, timedelta

    random.seed(42)  # Reproduzierbar
    data = []
    invested = 18000.0  # Startwert investiertes Kapital
    value = 20500.0     # Startwert Portfoliowert

    for i in range(days):
        date = (datetime.now() - timedelta(days=days - i)).strftime("%Y-%m-%d")

        # Monatliche Einzahlung (~1500 EUR)
        if i > 0 and i % 30 == 0:
            invested += random.uniform(1200, 1800)

        # Tägliche Marktschwankung
        daily_return = random.gauss(0.0004, 0.012)  # Leicht positiver Bias
        value = value * (1 + daily_return)

        # Invested wächst langsamer als der Wert
        data.append({
            "date": date,
            "total_value": round(value, 2),
            "invested_capital": round(invested, 2),
        })

    return data


def get_demo_market_indices() -> list[dict]:
    """Demo-Marktindizes: S&P 500, Nasdaq, DAX."""
    return [
        {"name": "S&P 500", "symbol": "^GSPC", "price": 5248.72, "change": 32.15, "change_pct": 0.62},
        {"name": "Nasdaq", "symbol": "^IXIC", "price": 16428.89, "change": 128.54, "change_pct": 0.79},
        {"name": "DAX", "symbol": "^GDAXI", "price": 18205.45, "change": -42.30, "change_pct": -0.23},
    ]


def get_demo_benchmark(symbol: str = "SPY", days: int = 180) -> dict:
    """Synthetische Benchmark-Kurve für Demo-Modus."""
    import random
    from datetime import datetime, timedelta
    random.seed(123)

    benchmark_data = []
    portfolio_data_series = []
    bench_price = 480.0
    port_value = 35000.0

    for i in range(days):
        date = (datetime.now() - timedelta(days=days - i)).strftime("%Y-%m-%d")
        bench_ret = random.gauss(0.0003, 0.010)
        port_ret = random.gauss(0.0005, 0.013)
        bench_price *= (1 + bench_ret)
        port_value *= (1 + port_ret)

        bench_pct = ((bench_price - 480.0) / 480.0) * 100
        port_pct = ((port_value - 35000.0) / 35000.0) * 100

        benchmark_data.append({"date": date, "price": round(bench_price, 2), "return_pct": round(bench_pct, 2)})
        portfolio_data_series.append({"date": date, "value": round(port_value, 2), "return_pct": round(port_pct, 2)})

    names = {"SPY": "S&P 500", "IWDA.AS": "MSCI World", "QQQ": "Nasdaq 100"}
    return {
        "benchmark_symbol": symbol,
        "benchmark_name": names.get(symbol, symbol),
        "period": "6month",
        "benchmark": benchmark_data,
        "portfolio": portfolio_data_series,
        "is_demo": True,
    }


def get_demo_correlation() -> dict:
    """Synthetische Korrelationsmatrix für Demo-Ticker."""
    tickers = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "ASML", "SAP", "AVGO", "CRM", "AMD"]
    # Realistische Korrelationen für Tech-Portfolio
    import random
    random.seed(99)
    matrix = {}
    for t1 in tickers:
        row = {}
        for t2 in tickers:
            if t1 == t2:
                row[t2] = 1.0
            else:
                key = tuple(sorted([t1, t2]))
                base = 0.55 + random.uniform(-0.2, 0.25)
                if "TSLA" in key:
                    base -= 0.15  # Tesla weniger korreliert
                if t1 in ("SAP", "ASML") or t2 in ("SAP", "ASML"):
                    base -= 0.10  # EU-Aktien etwas weniger
                row[t2] = round(max(0.15, min(0.95, base)), 2)
        matrix[t1] = row

    # Symmetrie sicherstellen
    for t1 in tickers:
        for t2 in tickers:
            matrix[t2][t1] = matrix[t1][t2]

    avg_corr = sum(matrix[t1][t2] for t1 in tickers for t2 in tickers if t1 != t2) / (len(tickers) * (len(tickers) - 1))
    return {
        "tickers": tickers,
        "matrix": matrix,
        "avg_correlation": round(avg_corr, 3),
        "diversification_score": round((1 - avg_corr) * 100, 1),
        "is_demo": True,
    }


def get_demo_risk() -> dict:
    """Synthetische Risikokennzahlen."""
    return {
        "portfolio_beta": 1.18,
        "var_95_pct": -1.82,
        "var_95_eur": -728.0,
        "max_drawdown_pct": -12.4,
        "sharpe_ratio": 1.45,
        "sortino_ratio": 2.12,
        "volatility_annualized": 18.5,
        "risk_level": "Moderat",
        "is_demo": True,
    }


def get_demo_score_history(ticker: str, days: int = 30) -> list[dict]:
    """Synthetischer Score-Verlauf pro Aktie."""
    import random
    from datetime import datetime, timedelta

    base_scores = {
        "AAPL": 72, "MSFT": 78, "NVDA": 82, "GOOGL": 70, "AMZN": 58,
        "META": 68, "TSLA": 35, "ASML": 74, "SAP": 62, "AVGO": 76, "CRM": 48, "AMD": 65,
    }
    base = base_scores.get(ticker, 55)
    random.seed(hash(ticker) + 42)

    result = []
    score = base - random.uniform(2, 8)  # Start etwas niedriger
    for i in range(days):
        date = (datetime.now() - timedelta(days=days - i)).strftime("%Y-%m-%d")
        score += random.gauss(0.2, 1.5)
        score = max(20, min(95, score))
        rating = "buy" if score >= 65 else ("sell" if score < 40 else "hold")
        result.append({"date": date, "score": round(score, 1), "rating": rating})

    return result


def get_demo_analysis_history(days: int = 7) -> list[dict]:
    """Synthetische Analyse-Historie."""
    import random
    from datetime import datetime, timedelta
    random.seed(77)

    tickers = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "ASML", "SAP", "AVGO", "CRM", "AMD"]
    base_scores = {
        "AAPL": 72, "MSFT": 78, "NVDA": 82, "GOOGL": 70, "AMZN": 58,
        "META": 68, "TSLA": 35, "ASML": 74, "SAP": 62, "AVGO": 76, "CRM": 48, "AMD": 65,
    }

    history = []
    for i in range(days):
        date = (datetime.now() - timedelta(days=days - i)).strftime("%Y-%m-%d")
        scores = {}
        for t in tickers:
            s = base_scores[t] + random.gauss(0, 2) + i * 0.1
            s = max(20, min(95, s))
            scores[t] = {
                "score": round(s, 1),
                "rating": "buy" if s >= 65 else ("sell" if s < 40 else "hold"),
            }
        avg = sum(v["score"] for v in scores.values()) / len(scores)
        history.append({
            "date": date,
            "portfolio_score": round(avg, 1),
            "num_positions": 12,
            "scores": scores,
        })

    return history


def get_demo_stock_news(ticker: str) -> list[dict]:
    """Demo-News für beliebige Aktien."""
    from datetime import datetime, timedelta
    now = datetime.now()
    news_db = {
        "AAPL": [
            {"title": "Apple stellt neues KI-Framework für Entwickler vor", "summary": "Mit dem neuen AI SDK können Drittentwickler Apple Intelligence direkt in ihre Apps integrieren."},
            {"title": "iPhone 17 Pro: Erste Leaks zum neuen Kamera-System", "summary": "Laut Analysten soll das Periskop-Teleobjektiv einen deutlichen Qualitätssprung machen."},
            {"title": "Apple Services Umsatz erreicht neues Allzeithoch", "summary": "Der Abo-Bereich wächst weiter zweistellig und nähert sich der 100-Mrd-Dollar-Marke."},
        ],
        "NVDA": [
            {"title": "NVIDIA: Blackwell B200 Nachfrage übersteigt Angebot deutlich", "summary": "Die neue GPU-Generation ist bei Cloud-Providern bereits für Q4 ausverkauft."},
            {"title": "Jensen Huang kündigt neue AI Enterprise Partnerschaften an", "summary": "Partnerschaften mit SAP, Siemens und BMW für industrielle KI-Anwendungen."},
            {"title": "Analysten erhöhen Kursziel nach starkem Datacenter-Umsatz", "summary": "Das durchschnittliche Kursziel steigt auf 1.050 USD nach Rekord-Quartalszahlen."},
        ],
        "TSLA": [
            {"title": "Tesla Robotaxi: Marktstart in Austin verzögert sich", "summary": "Der geplante Launch im Sommer wird auf Q4 verschoben, Regulierungshürden als Grund."},
            {"title": "Preissenkungen in Europa drücken Marge weiter", "summary": "Model Y Preise in Deutschland um weitere 3.000€ gesenkt."},
            {"title": "Elon Musk bestätigt: FSD V13 kommt nach Europa", "summary": "Full Self-Driving soll noch dieses Jahr die europäische Zulassung erhalten."},
        ],
    }

    # Fallback für Ticker ohne spezifische News
    default_news = [
        {"title": f"{ticker}: Analysten bestätigen Kursziel", "summary": f"Mehrere Investmentbanken haben ihre Einschätzung für {ticker} bekräftigt."},
        {"title": f"{ticker} meldet solides Quartalsergebnis", "summary": "Die Erwartungen der Analysten wurden leicht übertroffen."},
        {"title": f"Branchentrend: {ticker} profitiert von KI-Nachfrage", "summary": "Wachsende Investitionen in künstliche Intelligenz treiben den Umsatz."},
    ]

    articles = news_db.get(ticker, default_news)
    result = []
    for i, art in enumerate(articles):
        result.append({
            "title": art["title"],
            "text": art["summary"],
            "url": f"https://example.com/news/{ticker.lower()}-{i+1}",
            "published": (now - timedelta(hours=i * 8 + 2)).strftime("%Y-%m-%dT%H:%M:%S"),
            "source": ["Reuters", "Bloomberg", "Handelsblatt"][i % 3],
            "ticker": ticker,
        })
    return result


def get_demo_performance() -> dict:
    """Synthetische Performance-KPIs (Parqet-like)."""
    return {
        "kpis": {
            "total_return": 8420.50,
            "total_return_pct": 22.4,
            "annualized_return_pct": 18.2,
            "total_dividends": 385.20,
            "total_fees": 42.80,
            "total_taxes": 0.0,
            "invested_capital": 37580.00,
            "current_value": 46000.50,
        },
        "holdings": [
            {"ticker": "AAPL", "name": "Apple Inc.", "return_pct": 25.4, "return_eur": 542.80, "weight": 5.8, "status": "active"},
            {"ticker": "MSFT", "name": "Microsoft Corp.", "return_pct": 45.7, "return_eur": 1303.00, "weight": 9.0, "status": "active"},
            {"ticker": "NVDA", "name": "NVIDIA Corp.", "return_pct": 94.5, "return_eur": 3403.20, "weight": 15.2, "status": "active"},
            {"ticker": "GOOGL", "name": "Alphabet Inc.", "return_pct": 59.9, "return_eur": 755.20, "weight": 4.4, "status": "active"},
            {"ticker": "AMZN", "name": "Amazon.com Inc.", "return_pct": 52.8, "return_eur": 1352.00, "weight": 8.5, "status": "active"},
            {"ticker": "META", "name": "Meta Platforms", "return_pct": 80.6, "return_eur": 1354.50, "weight": 6.6, "status": "active"},
        ],
        "is_demo": True,
    }


def get_demo_sector_rotation() -> dict:
    """Synthetische Sektor-Rotations-Daten."""
    return {
        "sectors": [
            {"name": "Technology", "etf": "XLK", "performance_1m": 4.2, "performance_3m": 12.8, "relative_1m": 2.1, "relative_3m": 5.5, "phase": "Leading", "in_portfolio": True},
            {"name": "Consumer Discretionary", "etf": "XLY", "performance_1m": 1.8, "performance_3m": 5.2, "relative_1m": -0.3, "relative_3m": -2.1, "phase": "Weakening", "in_portfolio": True},
            {"name": "Healthcare", "etf": "XLV", "performance_1m": 2.5, "performance_3m": 8.1, "relative_1m": 0.4, "relative_3m": 0.8, "phase": "Improving", "in_portfolio": False},
            {"name": "Financials", "etf": "XLF", "performance_1m": 3.1, "performance_3m": 9.4, "relative_1m": 1.0, "relative_3m": 2.1, "phase": "Leading", "in_portfolio": False},
            {"name": "Energy", "etf": "XLE", "performance_1m": -1.2, "performance_3m": 2.8, "relative_1m": -3.3, "relative_3m": -4.5, "phase": "Lagging", "in_portfolio": False},
            {"name": "Industrials", "etf": "XLI", "performance_1m": 2.0, "performance_3m": 7.2, "relative_1m": -0.1, "relative_3m": -0.1, "phase": "Improving", "in_portfolio": False},
        ],
        "benchmark": {"name": "S&P 500", "performance_1m": 2.1, "performance_3m": 7.3},
        "is_demo": True,
    }


def get_demo_backtest() -> dict:
    """Synthetische Backtest-Ergebnisse."""
    return {
        "period": {"lookback_days": 30, "forward_days": 14},
        "results": [
            {"ticker": "NVDA", "initial_score": 78.5, "initial_rating": "buy", "price_change_pct": 8.2, "correct": True},
            {"ticker": "MSFT", "initial_score": 75.0, "initial_rating": "buy", "price_change_pct": 3.1, "correct": True},
            {"ticker": "AAPL", "initial_score": 70.2, "initial_rating": "buy", "price_change_pct": 1.8, "correct": True},
            {"ticker": "TSLA", "initial_score": 32.5, "initial_rating": "sell", "price_change_pct": -5.4, "correct": True},
            {"ticker": "CRM", "initial_score": 45.0, "initial_rating": "hold", "price_change_pct": -1.2, "correct": True},
            {"ticker": "GOOGL", "initial_score": 68.0, "initial_rating": "buy", "price_change_pct": -0.8, "correct": False},
        ],
        "summary": {
            "total_predictions": 12,
            "correct": 9,
            "accuracy_pct": 75.0,
            "avg_buy_return": 3.8,
            "avg_sell_return": -4.2,
        },
        "is_demo": True,
    }


def get_demo_activities() -> list[dict]:
    """Synthetische Portfolio-Transaktionen."""
    from datetime import datetime, timedelta
    base = datetime.now()
    activities = [
        {"type": "Buy", "ticker": "NVDA", "name": "NVIDIA Corp.", "shares": 4, "price": 420.00, "total": 1680.00, "date": (base - timedelta(days=180)).strftime("%Y-%m-%d"), "currency": "USD"},
        {"type": "Buy", "ticker": "AAPL", "name": "Apple Inc.", "shares": 10, "price": 148.50, "total": 1485.00, "date": (base - timedelta(days=170)).strftime("%Y-%m-%d"), "currency": "USD"},
        {"type": "Buy", "ticker": "MSFT", "name": "Microsoft Corp.", "shares": 5, "price": 290.00, "total": 1450.00, "date": (base - timedelta(days=160)).strftime("%Y-%m-%d"), "currency": "USD"},
        {"type": "Buy", "ticker": "SAP", "name": "SAP SE", "shares": 15, "price": 120.00, "total": 1800.00, "date": (base - timedelta(days=150)).strftime("%Y-%m-%d"), "currency": "EUR"},
        {"type": "Buy", "ticker": "NVDA", "name": "NVIDIA Corp.", "shares": 4, "price": 480.00, "total": 1920.00, "date": (base - timedelta(days=120)).strftime("%Y-%m-%d"), "currency": "USD"},
        {"type": "Buy", "ticker": "META", "name": "Meta Platforms", "shares": 6, "price": 280.00, "total": 1680.00, "date": (base - timedelta(days=110)).strftime("%Y-%m-%d"), "currency": "USD"},
        {"type": "Dividend", "ticker": "AAPL", "name": "Apple Inc.", "shares": 10, "price": 0.96, "total": 9.60, "date": (base - timedelta(days=95)).strftime("%Y-%m-%d"), "currency": "USD"},
        {"type": "Buy", "ticker": "ASML", "name": "ASML Holding", "shares": 3, "price": 620.00, "total": 1860.00, "date": (base - timedelta(days=90)).strftime("%Y-%m-%d"), "currency": "EUR"},
        {"type": "Buy", "ticker": "AAPL", "name": "Apple Inc.", "shares": 5, "price": 136.50, "total": 682.50, "date": (base - timedelta(days=80)).strftime("%Y-%m-%d"), "currency": "USD"},
        {"type": "Buy", "ticker": "MSFT", "name": "Microsoft Corp.", "shares": 5, "price": 280.00, "total": 1400.00, "date": (base - timedelta(days=70)).strftime("%Y-%m-%d"), "currency": "USD"},
        {"type": "Buy", "ticker": "AMD", "name": "AMD Inc.", "shares": 15, "price": 95.00, "total": 1425.00, "date": (base - timedelta(days=60)).strftime("%Y-%m-%d"), "currency": "USD"},
        {"type": "Buy", "ticker": "SAP", "name": "SAP SE", "shares": 10, "price": 130.00, "total": 1300.00, "date": (base - timedelta(days=50)).strftime("%Y-%m-%d"), "currency": "EUR"},
        {"type": "Buy", "ticker": "AVGO", "name": "Broadcom Inc.", "shares": 4, "price": 850.00, "total": 3400.00, "date": (base - timedelta(days=40)).strftime("%Y-%m-%d"), "currency": "USD"},
        {"type": "Dividend", "ticker": "MSFT", "name": "Microsoft Corp.", "shares": 10, "price": 0.75, "total": 7.50, "date": (base - timedelta(days=35)).strftime("%Y-%m-%d"), "currency": "USD"},
        {"type": "Buy", "ticker": "CRM", "name": "Salesforce Inc.", "shares": 8, "price": 210.00, "total": 1680.00, "date": (base - timedelta(days=30)).strftime("%Y-%m-%d"), "currency": "USD"},
        {"type": "Buy", "ticker": "GOOGL", "name": "Alphabet Inc.", "shares": 12, "price": 105.00, "total": 1260.00, "date": (base - timedelta(days=20)).strftime("%Y-%m-%d"), "currency": "USD"},
        {"type": "Buy", "ticker": "AMZN", "name": "Amazon.com Inc.", "shares": 20, "price": 128.00, "total": 2560.00, "date": (base - timedelta(days=15)).strftime("%Y-%m-%d"), "currency": "USD"},
        {"type": "Buy", "ticker": "TSLA", "name": "Tesla Inc.", "shares": 5, "price": 195.00, "total": 975.00, "date": (base - timedelta(days=10)).strftime("%Y-%m-%d"), "currency": "USD"},
    ]
    return activities
