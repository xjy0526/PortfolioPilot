"""PortfolioPilot - Analytics Engine

Berechnungen für:
  - Korrelationsmatrix & Diversifikations-Score
  - Portfolio-Risiko: Beta, VaR (95%), Max Drawdown
  - Dividenden-Aggregation: Yield on Cost, jährliche Einnahmen, Prognose
"""
import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Korrelationsmatrix & Diversifikations-Score
# ─────────────────────────────────────────────────────────────

def calculate_correlation_matrix(
    price_data: dict[str, list[float]],
) -> dict:
    """Berechnet Pearson-Korrelationsmatrix aus historischen Tagesrenditen.

    Args:
        price_data: {ticker: [close_prices]} — alle gleich lang

    Returns:
        {"tickers": [...], "matrix": [[...]], "diversification_score": float}
    """
    tickers = list(price_data.keys())
    n = len(tickers)
    if n < 2:
        return {"tickers": tickers, "matrix": [[1.0]], "diversification_score": 100.0}

    # Tagesrenditen berechnen
    returns = {}
    for t in tickers:
        prices = price_data[t]
        if len(prices) < 2:
            returns[t] = []
        else:
            returns[t] = [
                (prices[i] - prices[i - 1]) / prices[i - 1]
                for i in range(1, len(prices))
                if prices[i - 1] > 0
            ]

    # Minimale gemeinsame Länge
    min_len = min(len(r) for r in returns.values()) if returns else 0
    if min_len < 10:
        return {"tickers": tickers, "matrix": [], "diversification_score": 50.0}

    # Auf gleiche Länge kürzen
    for t in tickers:
        returns[t] = returns[t][:min_len]

    # Korrelationsmatrix berechnen
    matrix = []
    for i in range(n):
        row = []
        for j in range(n):
            if i == j:
                row.append(1.0)
            else:
                corr = _pearson(returns[tickers[i]], returns[tickers[j]])
                row.append(round(corr, 3))
        matrix.append(row)

    # Diversifikations-Score: je niedriger die mittlere Korrelation, desto besser
    off_diag = []
    for i in range(n):
        for j in range(i + 1, n):
            off_diag.append(abs(matrix[i][j]))

    avg_corr = sum(off_diag) / len(off_diag) if off_diag else 0
    # 0.0 avg → 100 Score, 1.0 avg → 0 Score
    div_score = round(max(0, min(100, (1.0 - avg_corr) * 100)), 1)

    return {
        "tickers": tickers,
        "matrix": matrix,
        "diversification_score": div_score,
        "avg_correlation": round(avg_corr, 3),
    }


def _pearson(x: list[float], y: list[float]) -> float:
    """Berechnet Pearson-Korrelationskoeffizient."""
    n = len(x)
    if n == 0:
        return 0.0

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x))
    std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y))

    if std_x == 0 or std_y == 0:
        return 0.0

    return cov / (std_x * std_y)


# ─────────────────────────────────────────────────────────────
# Portfolio-Risiko: Beta, VaR, Max Drawdown
# ─────────────────────────────────────────────────────────────

def calculate_portfolio_risk(
    stocks: list,  # list[StockFullData]
    portfolio_returns: Optional[list[float]] = None,
) -> dict:
    """Berechnet Portfolio-Risikokennzahlen.

    Returns:
        {
            "portfolio_beta": float,
            "var_95_daily": float,      # Value at Risk (95%, täglich) in %
            "var_95_monthly": float,    # Value at Risk (95%, monatlich) in %
            "max_drawdown": float,      # Max Drawdown in %
            "volatility_annual": float, # Annualisierte Volatilität in %
            "risk_level": str,          # "Niedrig", "Mittel", "Hoch"
            "risk_score": int,          # 1-10
        }
    """
    total_value = sum(
        s.position.current_value
        for s in stocks
        if s.position.ticker != "CASH"
    )

    if total_value <= 0:
        return _empty_risk()

    # Gewichtetes Portfolio-Beta
    weighted_beta = 0.0
    beta_available = 0
    for s in stocks:
        if s.position.ticker == "CASH":
            continue
        weight = s.position.current_value / total_value
        beta = None
        if s.fundamentals and s.fundamentals.beta is not None:
            beta = s.fundamentals.beta
        if beta is not None and beta > 0:
            weighted_beta += weight * beta
            beta_available += 1

    if beta_available == 0:
        weighted_beta = 1.0

    # VaR und Volatilität aus Portfolio-Returns
    var_95_daily = 0.0
    var_95_monthly = 0.0
    volatility_annual = 0.0
    max_dd = 0.0

    if portfolio_returns and len(portfolio_returns) >= 20:
        # Sortierte Returns für VaR
        sorted_returns = sorted(portfolio_returns)
        idx_95 = int(len(sorted_returns) * 0.05)
        var_95_daily = abs(sorted_returns[idx_95]) * 100
        var_95_monthly = var_95_daily * math.sqrt(21)  # ~21 Handelstage

        # Annualisierte Volatilität
        mean_ret = sum(portfolio_returns) / len(portfolio_returns)
        variance = sum((r - mean_ret) ** 2 for r in portfolio_returns) / len(portfolio_returns)
        daily_vol = math.sqrt(variance)
        volatility_annual = daily_vol * math.sqrt(252) * 100

        # Max Drawdown
        max_dd = _calculate_max_drawdown(portfolio_returns) * 100

    # Risk Score (1-10)
    risk_score = _calculate_risk_score(weighted_beta, volatility_annual, max_dd)
    risk_level = "Niedrig" if risk_score <= 3 else "Mittel" if risk_score <= 6 else "Hoch"

    return {
        "portfolio_beta": round(weighted_beta, 2),
        "var_95_daily": round(var_95_daily, 2),
        "var_95_monthly": round(var_95_monthly, 2),
        "max_drawdown": round(max_dd, 2),
        "volatility_annual": round(volatility_annual, 2),
        "risk_level": risk_level,
        "risk_score": risk_score,
    }


def _calculate_max_drawdown(returns: list[float]) -> float:
    """Berechnet maximalen Drawdown aus Tagesrenditen."""
    cumulative = 1.0
    peak = 1.0
    max_dd = 0.0

    for r in returns:
        cumulative *= (1 + r)
        if cumulative > peak:
            peak = cumulative
        drawdown = (peak - cumulative) / peak
        if drawdown > max_dd:
            max_dd = drawdown

    return max_dd


def _calculate_risk_score(beta: float, vol: float, max_dd: float) -> int:
    """Berechnet Risk-Score 1-10."""
    score = 0

    # Beta Beitrag (0-3)
    if beta > 1.5:
        score += 3
    elif beta > 1.2:
        score += 2
    elif beta > 0.8:
        score += 1

    # Volatilität Beitrag (0-4)
    if vol > 30:
        score += 4
    elif vol > 22:
        score += 3
    elif vol > 15:
        score += 2
    elif vol > 10:
        score += 1

    # Max Drawdown Beitrag (0-3)
    if max_dd > 25:
        score += 3
    elif max_dd > 15:
        score += 2
    elif max_dd > 8:
        score += 1

    return max(1, min(10, score))


def _empty_risk() -> dict:
    return {
        "portfolio_beta": 1.0,
        "var_95_daily": 0.0,
        "var_95_monthly": 0.0,
        "max_drawdown": 0.0,
        "volatility_annual": 0.0,
        "risk_level": "Unbekannt",
        "risk_score": 5,
    }


# ─────────────────────────────────────────────────────────────
# Dividenden-Aggregation
# ─────────────────────────────────────────────────────────────

def calculate_dividend_summary(stocks: list) -> dict:
    """Aggregiert Dividenden-Daten über alle Portfolio-Positionen.

    Returns:
        {
            "total_annual_income": float,   # Jährliche Dividenden in EUR
            "portfolio_yield": float,       # Portfolio-Dividendenrendite in %
            "portfolio_yield_on_cost": float,
            "positions": [{ticker, name, yield_pct, annual_income, yield_on_cost, ex_date, frequency}],
            "monthly_forecast": [float * 12],  # Monatliche Prognose
        }
    """
    total_value = 0.0
    total_cost = 0.0
    total_annual_income = 0.0
    positions = []

    for s in stocks:
        if s.position.ticker == "CASH":
            continue

        pos = s.position
        total_value += pos.current_value
        total_cost += pos.total_cost

        div = s.dividend
        fd = s.fundamentals

        # Dividendenrendite aus verschiedenen Quellen
        yield_pct = None
        annual_div_per_share = None

        if div and div.yield_percent and div.yield_percent > 0:
            yield_pct = div.yield_percent
            annual_div_per_share = div.annual_dividend
        elif fd and fd.dividend_yield and fd.dividend_yield > 0:
            yield_pct = fd.dividend_yield
            if pos.current_price > 0:
                annual_div_per_share = pos.current_price * yield_pct / 100

        if yield_pct and yield_pct > 0 and annual_div_per_share:
            annual_income = annual_div_per_share * pos.shares
            yield_on_cost = (annual_div_per_share / pos.avg_cost * 100) if pos.avg_cost > 0 else 0

            total_annual_income += annual_income

            positions.append({
                "ticker": pos.ticker,
                "name": pos.name,
                "shares": pos.shares,
                "yield_pct": round(yield_pct, 2),
                "annual_income": round(annual_income, 2),
                "annual_div_per_share": round(annual_div_per_share, 2),
                "yield_on_cost": round(yield_on_cost, 2),
                "ex_date": div.ex_date if div else None,
                "frequency": div.frequency if div else "Quarterly",
            })

    # Sortiere nach jährlichem Einkommen (absteigend)
    positions.sort(key=lambda x: x["annual_income"], reverse=True)

    # Portfolio-Yield
    portfolio_yield = (total_annual_income / total_value * 100) if total_value > 0 else 0
    portfolio_yoc = (total_annual_income / total_cost * 100) if total_cost > 0 else 0

    # Monatliche Prognose (gleichmäßig verteilt)
    monthly = [round(total_annual_income / 12, 2)] * 12

    return {
        "total_annual_income": round(total_annual_income, 2),
        "portfolio_yield": round(portfolio_yield, 2),
        "portfolio_yield_on_cost": round(portfolio_yoc, 2),
        "num_dividend_payers": len(positions),
        "positions": positions,
        "monthly_forecast": monthly,
    }
