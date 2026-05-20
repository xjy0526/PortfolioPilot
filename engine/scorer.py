"""PortfolioPilot - Scoring Engine v5

Multi-Faktor Bewertungssystem fuer Aktien.
10 Faktoren mit einheitlicher Prozent-Normalisierung.

v5 Aenderungen (gegenueber v4):
  - 9 -> 10 Faktoren (Momentum als separater Faktor)
  - Gewichtung angepasst: quality 19%, valuation 14%, analyst 15%,
    technical 13%, growth 11%, quant 10%, sentiment 7%, momentum 6%,
    insider 3%, esg 2%
  - Revenue Growth / Earnings Growth jetzt echte YoY-Wachstumsraten
  - PEG Ratio direkt von FMP (kein manueller Proxy)
  - _normalize_pct Schwellwert auf < 1.0 verschaerft
"""
import logging
from typing import Optional

from models import (
    AnalystData,
    FearGreedData,
    FmpRating,
    FundamentalData,
    Rating,
    ScoreBreakdown,
    StockScore,
    TechnicalIndicators,
    YFinanceData,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# v5 Gewichtung (10 Faktoren, sum = 1.0)
# ─────────────────────────────────────────────────────────────
WEIGHTS = {
    "quality": 0.19,        # ROE, Margins, D/E, Current Ratio
    "valuation": 0.14,      # PE, EV/EBITDA, PEG, FCF Yield (sektorbasiert)
    "analyst": 0.15,        # Konsens + Preisziel (merged)
    "technical": 0.13,      # RSI, SMA, Momentum 30d
    "growth": 0.11,         # Revenue Growth, Earnings Growth YoY, ROIC
    "quantitative": 0.10,   # Altman Z-Score, Piotroski Score
    "momentum": 0.06,       # 3M/6M Kurs-Momentum
    "sentiment": 0.07,      # Fear & Greed Index
    "insider": 0.03,        # Insider Buy/Sell Ratio
    "esg": 0.02,            # ESG Risk Score
}

# Schwellenwerte
BUY_THRESHOLD = 63
SELL_THRESHOLD = 40

# ─────────────────────────────────────────────────────────────
# Sektorbasierte Bewertungsschwellen
# ─────────────────────────────────────────────────────────────
SECTOR_THRESHOLDS = {
    "Technology": {
        "pe_fair": 30, "pe_cheap": 20, "pe_expensive": 45,
        "ev_ebitda_fair": 20, "ev_ebitda_cheap": 14, "ev_ebitda_expensive": 30,
    },
    "Communication Services": {
        "pe_fair": 25, "pe_cheap": 16, "pe_expensive": 40,
        "ev_ebitda_fair": 16, "ev_ebitda_cheap": 10, "ev_ebitda_expensive": 25,
    },
    "Healthcare": {
        "pe_fair": 25, "pe_cheap": 16, "pe_expensive": 40,
        "ev_ebitda_fair": 16, "ev_ebitda_cheap": 10, "ev_ebitda_expensive": 25,
    },
    "Consumer Cyclical": {
        "pe_fair": 22, "pe_cheap": 14, "pe_expensive": 35,
        "ev_ebitda_fair": 14, "ev_ebitda_cheap": 9, "ev_ebitda_expensive": 22,
    },
    "Consumer Defensive": {
        "pe_fair": 20, "pe_cheap": 14, "pe_expensive": 30,
        "ev_ebitda_fair": 13, "ev_ebitda_cheap": 9, "ev_ebitda_expensive": 20,
    },
    "Industrials": {
        "pe_fair": 20, "pe_cheap": 13, "pe_expensive": 30,
        "ev_ebitda_fair": 13, "ev_ebitda_cheap": 8, "ev_ebitda_expensive": 20,
    },
    "Financial Services": {
        "pe_fair": 14, "pe_cheap": 9, "pe_expensive": 22,
        "ev_ebitda_fair": 10, "ev_ebitda_cheap": 6, "ev_ebitda_expensive": 16,
    },
    "Energy": {
        "pe_fair": 14, "pe_cheap": 8, "pe_expensive": 22,
        "ev_ebitda_fair": 8, "ev_ebitda_cheap": 5, "ev_ebitda_expensive": 14,
    },
    "Utilities": {
        "pe_fair": 18, "pe_cheap": 12, "pe_expensive": 26,
        "ev_ebitda_fair": 11, "ev_ebitda_cheap": 7, "ev_ebitda_expensive": 17,
    },
    "Real Estate": {
        "pe_fair": 35, "pe_cheap": 20, "pe_expensive": 55,
        "ev_ebitda_fair": 18, "ev_ebitda_cheap": 12, "ev_ebitda_expensive": 28,
    },
    "Basic Materials": {
        "pe_fair": 16, "pe_cheap": 10, "pe_expensive": 25,
        "ev_ebitda_fair": 10, "ev_ebitda_cheap": 6, "ev_ebitda_expensive": 16,
    },
}

_DEFAULT_THRESHOLDS = {
    "pe_fair": 20, "pe_cheap": 13, "pe_expensive": 32,
    "ev_ebitda_fair": 14, "ev_ebitda_cheap": 9, "ev_ebitda_expensive": 22,
}


# ─────────────────────────────────────────────────────────────
# Hilfsfunktionen
# ─────────────────────────────────────────────────────────────

def _normalize_pct(val: Optional[float]) -> Optional[float]:
    """Normalisiert Prozentwerte: 0.25 -> 25, 25 -> 25.

    FMP liefert z.B. ROE als 0.25 (=25%), yfinance auch.
    Unser yfinance-Fallback konvertiert zu 25.0.
    Diese Funktion stellt sicher, dass immer % rauskommt.
    """
    if val is None:
        return None
    if abs(val) < 1:  # Dezimalformat (z.B. 0.25 = 25%, 0.03 = 3%)
        return val * 100
    return val  # Bereits in % (25.0)


def _get_sector_thresholds(sector: str) -> dict:
    """Gibt sektorspezifische Bewertungsschwellen zurueck."""
    return SECTOR_THRESHOLDS.get(sector or "", _DEFAULT_THRESHOLDS)


def _has_fundamental_data(f: FundamentalData) -> bool:
    """Prueft ob FundamentalData tatsaechlich Daten enthaelt."""
    return any([
        f.pe_ratio is not None,
        f.roe is not None,
        f.market_cap is not None,
        f.revenue_growth is not None,
        f.net_margin is not None,
    ])


# ─────────────────────────────────────────────────────────────
# Haupt-Score-Berechnung
# ─────────────────────────────────────────────────────────────

def calculate_score(
    ticker: str,
    name: str,
    fundamentals: Optional[FundamentalData],
    analyst: Optional[AnalystData],
    current_price: float = 0.0,
    fmp_rating: Optional[FmpRating] = None,
    yfinance_data: Optional[YFinanceData] = None,
    fear_greed: Optional[FearGreedData] = None,
    technical: Optional[TechnicalIndicators] = None,
    sector: str = "",
    asset_type: str = "equity",
    pnl_percent: Optional[float] = None,
    daily_change_pct: Optional[float] = None,
    **kwargs,  # Ignoriere unbekannte Legacy-Parameter
) -> StockScore:
    """Berechnet den Gesamtscore fuer eine Aktie (v5).

    10 Faktoren, gewichteter Durchschnitt.
    Nur verfuegbare Faktoren werden beruecksichtigt (Rest skaliert hoch).
    """
    if asset_type == "prediction_market":
        return _build_prediction_market_score(
            ticker=ticker,
            name=name,
            pnl_percent=pnl_percent,
            daily_change_pct=daily_change_pct,
            sector=sector,
        )

    breakdown = ScoreBreakdown()
    available_weight = 0.0
    calculated_factors: set[str] = set()

    # --- 1. Quality Score (20%) ---
    if fundamentals and _has_fundamental_data(fundamentals):
        breakdown.quality_score = _calc_quality_score(fundamentals)
        available_weight += WEIGHTS["quality"]
        calculated_factors.add("quality")

    # --- 2. Valuation Score (15%) — sektorbasiert ---
    if fundamentals and _has_valuation_data(fundamentals):
        breakdown.valuation_score = _calc_valuation_score(fundamentals, sector)
        available_weight += WEIGHTS["valuation"]
        calculated_factors.add("valuation")

    # --- 3. Analyst Score (15%) — inkl. Preisziel ---
    if analyst and (analyst.num_analysts > 0 or analyst.target_price):
        breakdown.analyst_score = _calc_analyst_score(analyst, current_price)
        available_weight += WEIGHTS["analyst"]
        calculated_factors.add("analyst")

    # --- 4. Technical Score (15%) ---
    if technical and technical.rsi_14 is not None:
        breakdown.technical_score = _calc_technical_score(technical)
        available_weight += WEIGHTS["technical"]
        calculated_factors.add("technical")

    # --- 5. Growth Score (12%) ---
    if fundamentals and _has_growth_data(fundamentals, yfinance_data):
        breakdown.growth_score = _calc_growth_score(fundamentals, yfinance_data)
        available_weight += WEIGHTS["growth"]
        calculated_factors.add("growth")

    # --- 6. Quantitative Score (10%) ---
    if fundamentals and (fundamentals.altman_z_score is not None or fundamentals.piotroski_score is not None):
        breakdown.quantitative_score = _calc_quantitative_score(fundamentals)
        available_weight += WEIGHTS["quantitative"]
        calculated_factors.add("quantitative")

    # --- 7. Sentiment Score (7%) — A5: Makro-aware Fear&Greed ---
    if fear_greed and fear_greed.value is not None:
        fg_value = float(fear_greed.value)
        # Basis: Fear&Greed mit leichtem Spread um Neutral
        # Neutrales Sentiment (40-60) sollte ~52-55 Score geben, nicht exakt F&G
        if 40 <= fg_value <= 65:
            sentiment_base = fg_value + 5  # Leichter Bonus für neutrale/leicht positive Werte
        else:
            sentiment_base = fg_value

        # A5: Makro-Kontext-Anpassung
        # In "Extreme Fear" (<20): Qualitätsaktien bekommen Contrarian-Bonus
        if fg_value <= 20 and "quality" in calculated_factors and breakdown.quality_score >= 65:
            sentiment_base = min(100, sentiment_base + 15)  # Kaufchance für Qualität
        # In "Extreme Greed" (>80): Teure Aktien bekommen Risiko-Malus
        elif fg_value >= 80 and "valuation" in calculated_factors and breakdown.valuation_score <= 35:
            sentiment_base = max(0, sentiment_base - 15)  # Überhitzungswarnung

        breakdown.sentiment_score = min(100, sentiment_base)
        available_weight += WEIGHTS["sentiment"]
        calculated_factors.add("sentiment")

    # --- 8. Insider Score (3%) ---
    if yfinance_data and (yfinance_data.insider_buy_count > 0 or yfinance_data.insider_sell_count > 0):
        breakdown.insider_score = _calc_insider_score(yfinance_data)
        available_weight += WEIGHTS["insider"]
        calculated_factors.add("insider")

    # --- 9. ESG Score (2%) ---
    if yfinance_data and yfinance_data.esg_risk_score is not None:
        breakdown.esg_score = _calc_esg_score(yfinance_data.esg_risk_score)
        available_weight += WEIGHTS["esg"]
        calculated_factors.add("esg")

    # --- 10. Momentum Score (6%) ---
    if technical and (technical.momentum_90d is not None or technical.momentum_180d is not None):
        breakdown.momentum_score = _calc_momentum_score(technical)
        available_weight += WEIGHTS["momentum"]
        calculated_factors.add("momentum")

    # Calculate weighted total score
    if available_weight > 0:
        total = 0.0
        factor_map = {
            "quality": breakdown.quality_score,
            "valuation": breakdown.valuation_score,
            "analyst": breakdown.analyst_score,
            "technical": breakdown.technical_score,
            "growth": breakdown.growth_score,
            "quantitative": breakdown.quantitative_score,
            "momentum": breakdown.momentum_score,
            "sentiment": breakdown.sentiment_score,
            "insider": breakdown.insider_score,
            "esg": breakdown.esg_score,
        }
        for key, score_val in factor_map.items():
            if key in calculated_factors:
                total += score_val * WEIGHTS[key]

        total_score = total / available_weight
    else:
        total_score = 50.0

    # Determine rating
    if total_score >= BUY_THRESHOLD:
        rating = Rating.BUY
    elif total_score < SELL_THRESHOLD:
        rating = Rating.SELL
    else:
        rating = Rating.HOLD

    # A4: Confidence based on data quality + weight importance
    # High-weight factors (quality, analyst, valuation) matter more for confidence
    if calculated_factors:
        weighted_confidence = sum(WEIGHTS[f] for f in calculated_factors)
        # Penalty if analyst data is thin (less than 3 analysts)
        if "analyst" in calculated_factors and analyst:
            if analyst.num_analysts < 3:
                weighted_confidence -= WEIGHTS["analyst"] * 0.3  # 30% confidence reduction
        confidence = min(1.0, weighted_confidence / 0.85)  # 85% of weight = full confidence
    else:
        confidence = 0.0

    summary = _build_summary(
        ticker, rating, breakdown, fundamentals, analyst,
        fmp_rating, yfinance_data, technical, sector,
    )

    return StockScore(
        ticker=ticker,
        name=name,
        total_score=round(total_score, 1),
        rating=rating,
        breakdown=breakdown,
        confidence=round(confidence, 2),
        summary=summary,
    )


def _build_prediction_market_score(
    ticker: str,
    name: str,
    pnl_percent: Optional[float],
    daily_change_pct: Optional[float],
    sector: str,
) -> StockScore:
    """Vereinfachter Score für Polymarket/Prediction-Market-Kontrakte."""
    pnl_component = max(0.0, min(100.0, 50.0 + (pnl_percent or 0.0)))
    daily_component = max(0.0, min(100.0, 50.0 + (daily_change_pct or 0.0) * 3.0))
    total_score = round((pnl_component * 0.7) + (daily_component * 0.3), 1)

    if total_score >= BUY_THRESHOLD:
        rating = Rating.BUY
    elif total_score < SELL_THRESHOLD:
        rating = Rating.SELL
    else:
        rating = Rating.HOLD

    summary = (
        "Prediction-market Position ohne Fundamentaldaten. "
        "Score basiert auf Marktpreis und Performance."
    )
    if sector:
        summary += f" Kategorie: {sector}."

    return StockScore(
        ticker=ticker,
        name=name,
        total_score=total_score,
        rating=rating,
        confidence=0.25,
        summary=summary,
    )


# ─────────────────────────────────────────────────────────────
# Einzelne Score-Berechnungen
# ─────────────────────────────────────────────────────────────

def _calc_momentum_score(tech: TechnicalIndicators) -> float:
    """Momentum-Score basierend auf 3M und 6M Kurs-Momentum.

    Bewertet mittelfristige Kurstrends:
      - 3M Momentum (90d): Gewicht 60%
      - 6M Momentum (180d): Gewicht 40%
      - Bonus/Malus fuer Trend-Konsistenz
    """
    scores = []

    # 3M Momentum (stärker gewichtet, aktueller)
    if tech.momentum_90d is not None:
        m90 = tech.momentum_90d
        if m90 > 30:
            s = 85
        elif m90 > 15:
            s = 75
        elif m90 > 5:
            s = 65
        elif m90 > 0:
            s = 55
        elif m90 > -5:
            s = 45
        elif m90 > -15:
            s = 35
        else:
            s = 20
        scores.append((s, 0.6))

    # 6M Momentum (längerfristiger Trend)
    if tech.momentum_180d is not None:
        m180 = tech.momentum_180d
        if m180 > 40:
            s = 85
        elif m180 > 20:
            s = 75
        elif m180 > 5:
            s = 65
        elif m180 > 0:
            s = 55
        elif m180 > -10:
            s = 40
        else:
            s = 20
        scores.append((s, 0.4))

    if not scores:
        return 50.0

    total = sum(s * w for s, w in scores)
    total_w = sum(w for _, w in scores)
    base = total / total_w

    # Trend-Konsistenz Bonus: Wenn beide Zeiträume gleiche Richtung
    if tech.momentum_90d is not None and tech.momentum_180d is not None:
        both_positive = tech.momentum_90d > 5 and tech.momentum_180d > 5
        both_negative = tech.momentum_90d < -5 and tech.momentum_180d < -5
        if both_positive:
            base = min(100, base + 5)  # Konsistenter Aufwärtstrend
        elif both_negative:
            base = max(0, base - 5)    # Konsistenter Abwärtstrend

    return round(max(0, min(100, base)), 1)

def _calc_quality_score(fd: FundamentalData) -> float:
    """Qualitaets-Score: ROE, Margins, D/E, Current Ratio.

    Alle %-Werte werden normalisiert (0.25 und 25 -> 25%).
    """
    scores = []

    # ROE (higher is better) — in %
    roe = _normalize_pct(fd.roe)
    if roe is not None:
        if roe > 30:
            scores.append(90)
        elif roe > 20:
            scores.append(75)
        elif roe > 15:
            scores.append(65)
        elif roe > 10:
            scores.append(50)
        elif roe > 0:
            scores.append(35)
        else:
            scores.append(20)

    # Gross Margin (higher is better) — in %
    gm = _normalize_pct(fd.gross_margin)
    if gm is not None:
        if gm > 70:
            scores.append(90)
        elif gm > 50:
            scores.append(75)
        elif gm > 35:
            scores.append(60)
        elif gm > 20:
            scores.append(45)
        else:
            scores.append(30)

    # Operating Margin (higher is better) — in %
    om = _normalize_pct(fd.operating_margin)
    if om is not None:
        if om > 40:
            scores.append(90)
        elif om > 25:
            scores.append(75)
        elif om > 15:
            scores.append(60)
        elif om > 5:
            scores.append(45)
        else:
            scores.append(25)

    # Debt-to-Equity (lower is better) — Ratio, kein %
    if fd.debt_to_equity is not None:
        de = fd.debt_to_equity
        # FMP/yfinance liefert D/E als Ratio * 100 (z.B. 150 statt 1.5)
        # Aber echte hohe D/E (Finanzsektor) koennen bei 10-30 liegen
        if de > 50:
            de = de / 100  # Normalisieren: 150 -> 1.5
        if de < 0.3:
            scores.append(90)
        elif de < 0.5:
            scores.append(75)
        elif de < 1.0:
            scores.append(60)
        elif de < 1.5:
            scores.append(45)
        else:
            scores.append(30)

    # Net Margin (higher is better) — in %
    nm = _normalize_pct(fd.net_margin)
    if nm is not None:
        if nm > 25:
            scores.append(90)
        elif nm > 15:
            scores.append(75)
        elif nm > 8:
            scores.append(60)
        elif nm > 0:
            scores.append(40)
        else:
            scores.append(20)

    if scores:
        return round(sum(scores) / len(scores), 1)
    return 50.0


def _has_valuation_data(fd: FundamentalData) -> bool:
    """Prueft ob genug Bewertungsdaten vorhanden sind."""
    return any([
        fd.pe_ratio is not None,
        fd.ev_to_ebitda is not None,
        fd.free_cashflow_yield is not None,
        fd.peg_ratio is not None,
    ])


def _calc_valuation_score(fd: FundamentalData, sector: str = "") -> float:
    """Sektorbasierter Bewertungs-Score: PE, EV/EBITDA, FCF Yield, PEG."""
    thresholds = _get_sector_thresholds(sector)
    scores = []

    # PE Ratio (sektorbasiert)
    if fd.pe_ratio is not None:
        if fd.pe_ratio < 0:
            scores.append(20)  # Negative Earnings
        elif fd.pe_ratio <= thresholds["pe_cheap"]:
            scores.append(90)
        elif fd.pe_ratio <= thresholds["pe_fair"]:
            scores.append(70)
        elif fd.pe_ratio <= thresholds["pe_expensive"]:
            scores.append(45)
        else:
            scores.append(25)

    # EV/EBITDA (sektorbasiert)
    if fd.ev_to_ebitda is not None:
        if fd.ev_to_ebitda < 0:
            scores.append(20)
        elif fd.ev_to_ebitda <= thresholds["ev_ebitda_cheap"]:
            scores.append(90)
        elif fd.ev_to_ebitda <= thresholds["ev_ebitda_fair"]:
            scores.append(70)
        elif fd.ev_to_ebitda <= thresholds["ev_ebitda_expensive"]:
            scores.append(45)
        else:
            scores.append(25)

    # FCF Yield (universell)
    if fd.free_cashflow_yield is not None:
        fcf_pct = fd.free_cashflow_yield
        if fcf_pct < 1:
            fcf_pct = fcf_pct * 100  # 0.05 -> 5%
        if fcf_pct > 8:
            scores.append(90)
        elif fcf_pct > 5:
            scores.append(78)
        elif fcf_pct > 3:
            scores.append(65)
        elif fcf_pct > 1:
            scores.append(45)
        else:
            scores.append(25)

    # PEG Ratio (ideal 0.5-1.5)
    if fd.peg_ratio is not None and fd.peg_ratio > 0:
        peg = fd.peg_ratio
        if peg < 0.5:
            scores.append(85)
        elif peg <= 1.0:
            scores.append(80)
        elif peg <= 1.5:
            scores.append(65)
        elif peg <= 2.5:
            scores.append(45)
        else:
            scores.append(25)

    if scores:
        return round(sum(scores) / len(scores), 1)
    return 50.0


def _calc_analyst_score(analyst: AnalystData, current_price: float = 0.0) -> float:
    """Analyst Score: Konsens + Preisziel + Verified Consensus (merged).

    Mit Track Record:  40% Verified + 30% Konsens + 30% Preisziel
    Ohne Track Record: 60% Konsens + 40% Preisziel (Fallback)
    """
    consensus_score = 50.0
    target_score = None
    verified_score = None

    # --- Konsens-Teil ---
    total = (
        analyst.strong_buy_count + analyst.buy_count +
        analyst.hold_count +
        analyst.sell_count + analyst.strong_sell_count
    )
    if total > 0:
        consensus_score = (
            analyst.strong_buy_count * 100 +
            analyst.buy_count * 85 +
            analyst.hold_count * 50 +
            analyst.sell_count * 15 +
            analyst.strong_sell_count * 0
        ) / total

        # Bonus fuer starken Konsens (>80% Einigkeit)
        max_count = max(
            analyst.strong_buy_count + analyst.buy_count,
            analyst.hold_count,
            analyst.sell_count + analyst.strong_sell_count,
        )
        if max_count / total > 0.8:
            consensus_score = min(100, consensus_score * 1.05)
    elif analyst.consensus:
        # Fallback: Consensus-String (z.B. von yfinance)
        c = analyst.consensus.lower()
        if c in ("strong_buy", "strongbuy", "strong buy"):
            consensus_score = 95
        elif c in ("buy", "outperform", "overweight"):
            consensus_score = 80
        elif c in ("hold", "neutral", "equal-weight"):
            consensus_score = 50
        elif c in ("sell", "underperform", "underweight"):
            consensus_score = 20
        elif c in ("strong_sell", "strongsell", "strong sell"):
            consensus_score = 5

    # --- Verified Consensus-Teil (nur von Analysten mit Track Record) ---
    if analyst.verified_consensus:
        vc = analyst.verified_consensus.lower()
        if vc in ("strong_buy", "strongbuy", "strong buy"):
            verified_score = 95.0
        elif vc in ("buy", "outperform", "overweight"):
            verified_score = 82.0
        elif vc in ("hold", "neutral", "equal-weight"):
            verified_score = 50.0
        elif vc in ("sell", "underperform", "underweight"):
            verified_score = 18.0
        elif vc in ("strong_sell", "strongsell", "strong sell"):
            verified_score = 5.0

    # --- Preisziel-Teil ---
    if analyst.target_price and current_price > 0:
        upside = ((analyst.target_price - current_price) / current_price) * 100
        if upside > 30:
            target_score = 95.0
        elif upside > 20:
            target_score = 85.0
        elif upside > 10:
            target_score = 70.0
        elif upside > 0:
            target_score = 55.0
        elif upside > -10:
            target_score = 40.0
        elif upside > -20:
            target_score = 25.0
        else:
            target_score = 10.0

    # Merge: Gewichtung abhaengig davon ob Verified Consensus vorhanden
    if verified_score is not None and target_score is not None:
        # 40% Verified + 30% Normal + 30% Preisziel
        return round(verified_score * 0.4 + consensus_score * 0.3 + target_score * 0.3, 1)
    elif verified_score is not None:
        # 55% Verified + 45% Normal
        return round(verified_score * 0.55 + consensus_score * 0.45, 1)
    elif target_score is not None:
        # Fallback: 60% Konsens + 40% Preisziel
        return round(consensus_score * 0.6 + target_score * 0.4, 1)
    return round(consensus_score, 1)


def _calc_technical_score(tech: TechnicalIndicators) -> float:
    """Technical Score: RSI + SMA Cross + Momentum + Price vs SMA50."""
    scores = []

    if tech.rsi_14 is not None:
        rsi = tech.rsi_14
        if rsi < 25:
            scores.append(85)  # Stark ueberverkauft (Kaufchance)
        elif rsi < 35:
            scores.append(72)  # Ueberverkauft
        elif rsi < 45:
            scores.append(62)  # Leicht ueberverkauft
        elif rsi <= 55:
            scores.append(55)  # Neutral (nicht schlecht!)
        elif rsi <= 65:
            scores.append(50)  # Leicht ueberkauft
        elif rsi <= 75:
            scores.append(38)
        else:
            scores.append(22)  # Stark ueberkauft

    if tech.sma_cross is not None:
        if tech.sma_cross == "golden":
            scores.append(80)
        elif tech.sma_cross == "death":
            scores.append(25)
        else:
            scores.append(50)

    if tech.momentum_30d is not None:
        mom = tech.momentum_30d
        if mom > 15:
            scores.append(85)
        elif mom > 8:
            scores.append(74)
        elif mom > 2:
            scores.append(63)
        elif mom >= -2:
            scores.append(52)
        elif mom >= -8:
            scores.append(38)
        elif mom >= -15:
            scores.append(25)
        else:
            scores.append(15)

    if tech.price_vs_sma50 is not None:
        pvs = tech.price_vs_sma50
        if pvs > 10:
            scores.append(75)
        elif pvs > 3:
            scores.append(65)
        elif pvs >= -3:
            scores.append(50)
        elif pvs >= -10:
            scores.append(35)
        else:
            scores.append(20)

    if scores:
        return round(sum(scores) / len(scores), 1)
    return 50.0


def _has_growth_data(fd: FundamentalData, yf_data: Optional[YFinanceData] = None) -> bool:
    """Prueft ob genug Wachstumsdaten vorhanden sind."""
    has_fd = any([
        fd.roic is not None,
        fd.revenue_growth is not None,
    ])
    has_yf = yf_data is not None and yf_data.earnings_growth_yoy is not None
    return has_fd or has_yf


def _calc_growth_score(fd: FundamentalData, yf_data: Optional[YFinanceData] = None) -> float:
    """Wachstums-Score: ROIC, Revenue Growth, Earnings Growth YoY.

    Kein net_margin mehr (war doppelt mit Qualitaet).
    Echtes Earnings Growth YoY aus yfinance statt netIncomePerShare.
    """
    scores = []

    # ROIC (Return on Invested Capital)
    if fd.roic is not None:
        roic = _normalize_pct(fd.roic)
        if roic is not None:
            if roic > 25:
                scores.append(90)
            elif roic > 15:
                scores.append(75)
            elif roic > 10:
                scores.append(60)
            elif roic > 5:
                scores.append(45)
            elif roic > 0:
                scores.append(30)
            else:
                scores.append(15)

    # Revenue Growth (in %)
    rg = _normalize_pct(fd.revenue_growth)
    if rg is not None:
        if rg > 30:
            scores.append(90)
        elif rg > 15:
            scores.append(75)
        elif rg > 5:
            scores.append(60)
        elif rg > 0:
            scores.append(45)
        elif rg > -10:
            scores.append(30)
        else:
            scores.append(15)

    # Earnings Growth YoY (echtes YoY aus yfinance)
    if yf_data and yf_data.earnings_growth_yoy is not None:
        eg = yf_data.earnings_growth_yoy  # Bereits in %
        if eg > 50:
            scores.append(90)
        elif eg > 20:
            scores.append(78)
        elif eg > 10:
            scores.append(65)
        elif eg > 0:
            scores.append(50)
        elif eg > -20:
            scores.append(30)
        else:
            scores.append(15)

    # Earnings Beat Rate (Konsistenz: wie oft Schätzungen geschlagen)
    if yf_data and yf_data.earnings_beat_rate is not None:
        beat = yf_data.earnings_beat_rate
        if beat >= 80:      # 4/5+ Quartale geschlagen
            scores.append(85)
        elif beat >= 60:    # 3/5 geschlagen
            scores.append(70)
        elif beat >= 40:    # Gemischt
            scores.append(50)
        else:               # Meistens verfehlt
            scores.append(30)

    if scores:
        return round(sum(scores) / len(scores), 1)
    return 50.0


def _calc_quantitative_score(fd: FundamentalData) -> float:
    """Quantitative Score: Altman Z-Score + Piotroski Score.

    Kein DCF mehr (war Teil von FMP Rating).
    """
    scores = []

    if fd.altman_z_score is not None:
        if fd.altman_z_score > 3.0:
            scores.append(90)
        elif fd.altman_z_score > 2.5:
            scores.append(75)
        elif fd.altman_z_score > 1.8:
            scores.append(55)
        elif fd.altman_z_score > 1.0:
            scores.append(35)
        else:
            scores.append(15)

    if fd.piotroski_score is not None:
        if fd.piotroski_score >= 8:
            scores.append(95)
        elif fd.piotroski_score >= 6:
            scores.append(75)
        elif fd.piotroski_score >= 4:
            scores.append(55)
        elif fd.piotroski_score >= 2:
            scores.append(35)
        else:
            scores.append(15)

    if scores:
        return round(sum(scores) / len(scores), 1)
    return 50.0


def _calc_insider_score(yf_data: YFinanceData) -> float:
    """Insider-Score aus Kauf/Verkauf-Transaktionen."""
    buys = yf_data.insider_buy_count
    sells = yf_data.insider_sell_count
    total = buys + sells

    if total == 0:
        return 50.0

    buy_ratio = buys / total

    if buy_ratio >= 0.7:
        return 85.0
    elif buy_ratio >= 0.5:
        return 70.0
    elif buy_ratio >= 0.3:
        return 50.0
    elif buy_ratio >= 0.1:
        return 35.0
    else:
        return 20.0


def _calc_esg_score(esg_risk: float) -> float:
    """ESG Score aus Risk-Rating (lower = better)."""
    if esg_risk <= 10:
        return 90.0
    elif esg_risk <= 20:
        return 75.0
    elif esg_risk <= 30:
        return 55.0
    elif esg_risk <= 40:
        return 35.0
    else:
        return 15.0


# ─────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────

def _build_summary(
    ticker: str,
    rating: Rating,
    breakdown: ScoreBreakdown,
    fd: Optional[FundamentalData],
    analyst: Optional[AnalystData],
    fmp_rating: Optional[FmpRating] = None,
    yf_data: Optional[YFinanceData] = None,
    technical: Optional[TechnicalIndicators] = None,
    sector: str = "",
) -> str:
    """Erstellt eine kurze Zusammenfassung der Bewertung."""
    parts = []

    emoji = {"buy": "\U0001f7e2", "hold": "\U0001f7e1", "sell": "\U0001f534"}[rating.value]
    parts.append(f"{emoji} {rating.value.upper()}")

    if analyst and analyst.consensus:
        parts.append(f"Analysten: {analyst.consensus}")

    if fd and fd.piotroski_score is not None:
        parts.append(f"Piotroski: {fd.piotroski_score}/9")

    if fd and fd.altman_z_score is not None:
        z = fd.altman_z_score
        z_label = "\u2705" if z > 3 else "\u26a0\ufe0f" if z > 1.8 else "\U0001f534"
        parts.append(f"Z-Score: {z:.1f}{z_label}")

    if technical and technical.signal:
        tech_emoji = {
            "Bullish": "\U0001f4c8", "Bearish": "\U0001f4c9", "Neutral": "\u27a1\ufe0f"
        }.get(technical.signal, "\u27a1\ufe0f")
        parts.append(f"Technik: {tech_emoji}")

    if fd and fd.pe_ratio:
        thresholds = _get_sector_thresholds(sector)
        pe_label = (
            "guenstig" if fd.pe_ratio <= thresholds["pe_cheap"]
            else "fair" if fd.pe_ratio <= thresholds["pe_fair"]
            else "teuer"
        )
        parts.append(f"PE: {fd.pe_ratio:.1f} ({pe_label})")

    if fd and fd.ev_to_ebitda is not None:
        parts.append(f"EV/EBITDA: {fd.ev_to_ebitda:.1f}")

    if analyst and analyst.target_price:
        parts.append(f"Ziel: ${analyst.target_price:.0f}")

    return " | ".join(parts)
