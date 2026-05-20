"""PortfolioPilot - Pydantic Datenmodelle"""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field

TZ_BERLIN = ZoneInfo("Europe/Berlin")

def _now_berlin() -> datetime:
    return datetime.now(tz=TZ_BERLIN)


class Rating(str, Enum):
    BUY = "buy"
    HOLD = "hold"
    SELL = "sell"


class PortfolioPosition(BaseModel):
    """Eine einzelne Portfolio-Position aus Parqet."""
    ticker: str
    isin: Optional[str] = None
    name: str = ""
    asset_type: str = "equity"
    market: str = "global"
    exchange: str = ""
    country: str = ""
    shares: float = 0.0
    avg_cost: float = 0.0          # Durchschnittlicher Einstandskurs (EUR, aus CSV)
    current_price: float = 0.0      # Aktueller Kurs (in price_currency)
    currency: str = "EUR"           # Währung der Kostenbasis (immer EUR aus Parqet)
    price_currency: str = "EUR"     # Währung des aktuellen Kurses (USD, EUR, DKK, etc.)
    sector: str = "Unknown"
    daily_change_pct: Optional[float] = None  # Tagesveränderung in %

    @property
    def total_cost(self) -> float:
        """Gesamtkosten in EUR."""
        return self.shares * self.avg_cost

    @property
    def current_value(self) -> float:
        """Aktueller Wert in price_currency (Rohwert)."""
        return self.shares * self.current_price

    def value_eur(
        self,
        eur_usd: float = 1.08,
        eur_dkk: float = 7.46,
        eur_gbp: float = 0.855,
        eur_cny: float = 7.8,
    ) -> float:
        """Aktueller Wert in EUR (korrekt konvertiert).

        Args:
            eur_usd: EUR/USD-Kurs (z.B. 1.08 = 1 EUR = 1.08 USD)
            eur_dkk: EUR/DKK-Kurs (z.B. 7.46 = 1 EUR = 7.46 DKK)
            eur_gbp: EUR/GBP-Kurs (z.B. 0.855 = 1 EUR = 0.855 GBP)
            eur_cny: EUR/CNY-Kurs (z.B. 7.8 = 1 EUR = 7.8 CNY)
        """
        raw = self.current_value
        if self.price_currency == "EUR":
            return raw
        elif self.price_currency == "USD":
            return raw / eur_usd if eur_usd > 0 else raw
        elif self.price_currency == "DKK":
            return raw / eur_dkk if eur_dkk > 0 else raw
        elif self.price_currency == "GBP":
            return raw / eur_gbp if eur_gbp > 0 else raw
        elif self.price_currency == "CNY":
            return raw / eur_cny if eur_cny > 0 else raw
        else:
            # Unbekannte Währung → als EUR behandeln
            return raw

    @property
    def pnl(self) -> float:
        return self.current_value - self.total_cost

    @property
    def pnl_percent(self) -> float:
        if self.total_cost == 0:
            return 0.0
        return (self.pnl / self.total_cost) * 100


class FundamentalData(BaseModel):
    """Fundamentaldaten von FMP."""
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    ps_ratio: Optional[float] = None
    roe: Optional[float] = None
    roa: Optional[float] = None
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    gross_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    net_margin: Optional[float] = None
    revenue_growth: Optional[float] = None
    earnings_growth: Optional[float] = None
    dividend_yield: Optional[float] = None
    market_cap: Optional[float] = None
    beta: Optional[float] = None
    altman_z_score: Optional[float] = None
    piotroski_score: Optional[int] = None
    # v3: Valuation & Growth
    ev_to_ebitda: Optional[float] = None        # Enterprise Value / EBITDA
    free_cashflow_yield: Optional[float] = None  # FCF / Market Cap (0.05 = 5%)
    peg_ratio: Optional[float] = None            # PE / Earnings Growth
    roic: Optional[float] = None                 # Return on Invested Capital


class AnalystRating(BaseModel):
    """Ein einzelnes Analysten-Rating (Upgrade/Downgrade)."""
    firm: str = ""
    action: str = ""              # "upgrade", "downgrade", "reiterated", etc.
    from_grade: str = ""
    to_grade: str = ""
    date: str = ""                # ISO-Datum
    price_at_rating: Optional[float] = None


class AnalystTrackRecord(BaseModel):
    """Track Record einer Analysten-Firma."""
    firm: str = ""
    total_ratings: int = 0
    successful_ratings: int = 0
    success_rate: float = 0.0       # 0-100%
    avg_return_pct: float = 0.0     # Durchschnittliche 3-Monats-Rendite
    last_rating_date: str = ""


class AnalystData(BaseModel):
    """Analysten-Bewertungen."""
    consensus: Optional[str] = None  # "Buy", "Hold", "Sell"
    target_price: Optional[float] = None
    num_analysts: int = 0
    strong_buy_count: int = 0
    buy_count: int = 0
    hold_count: int = 0
    sell_count: int = 0
    strong_sell_count: int = 0
    # Track Record Felder
    individual_ratings: list[AnalystRating] = []
    verified_consensus: Optional[str] = None      # Konsens nur von verifizierten Analysten
    verified_target_price: Optional[float] = None  # Preisziel nur von Top-Analysten
    track_records: list[AnalystTrackRecord] = []   # Track Records pro Firma




class TechnicalIndicators(BaseModel):
    """Technische Indikatoren berechnet aus yfinance-Historiedaten."""
    rsi_14: Optional[float] = None         # 0-100
    sma_50: Optional[float] = None         # 50-Tage SMA
    sma_200: Optional[float] = None        # 200-Tage SMA
    price_vs_sma50: Optional[float] = None  # Preis / SMA50 - 1 (in %)
    sma_cross: Optional[str] = None        # "golden" (50>200), "death" (50<200), "neutral"
    momentum_30d: Optional[float] = None   # 30-Tage Preis-Momentum in %
    momentum_90d: Optional[float] = None   # 90-Tage (3M) Preis-Momentum in %
    momentum_180d: Optional[float] = None  # 180-Tage (6M) Preis-Momentum in %
    signal: str = "Neutral"                # "Bullish", "Bearish", "Neutral"


class YFinanceData(BaseModel):
    """Daten von Yahoo Finance (yfinance)."""
    recommendation_trend: Optional[str] = None  # "Buy", "Hold", "Sell"
    insider_buy_count: int = 0
    insider_sell_count: int = 0
    esg_risk_score: Optional[float] = None  # 0-100 (lower = better)
    earnings_growth_yoy: Optional[float] = None  # in %
    # Earnings Surprise (neu)
    earnings_beat_rate: Optional[float] = None      # 0-100%, z.B. 83.3 = 5/6 Quartale geschlagen
    earnings_surprise_avg: Optional[float] = None   # Durchschn. Surprise in %, z.B. +5.2
    next_earnings_date: Optional[str] = None        # Nächster Earnings-Termin (ISO)




class FearGreedData(BaseModel):
    """Fear & Greed Index (Markt-Sentiment)."""
    value: int = 50  # 0-100
    label: str = "Neutral"  # "Extreme Fear"..."Extreme Greed"
    source: str = "N/A"


class FmpRating(BaseModel):
    """FMP Ratings Snapshot."""
    rating: str = ""            # S, A, B, C, D, F
    rating_score: int = 0       # 1-5
    dcf_score: int = 0
    roe_score: int = 0
    roa_score: int = 0
    de_score: int = 0
    pe_score: int = 0
    pb_score: int = 0


class ScoreBreakdown(BaseModel):
    """Aufschluesselung des Gesamtscores (v5: 10 Faktoren)."""
    quality_score: float = 0.0        # 0-100 (ROE, Margins, D/E) — 19%
    valuation_score: float = 0.0      # 0-100 (PE, EV/EBITDA, PEG, FCF Yield) — 14%
    analyst_score: float = 0.0        # 0-100 (Konsens + Preisziel) — 15%
    technical_score: float = 0.0      # 0-100 (RSI + SMA + Momentum) — 13%
    growth_score: float = 0.0         # 0-100 (Revenue, Earnings YoY, ROIC) — 11%
    quantitative_score: float = 0.0   # 0-100 (Altman Z + Piotroski) — 10%
    sentiment_score: float = 0.0      # 0-100 (Fear&Greed Index) — 7%
    momentum_score: float = 0.0       # 0-100 (3M/6M Kurs-Momentum) — 6%
    insider_score: float = 0.0        # 0-100 (Insider Buy/Sell) — 3%
    esg_score: float = 0.0            # 0-100 (ESG Risk) — 2%


class StockScore(BaseModel):
    """Gesamtbewertung einer Aktie."""
    ticker: str
    name: str = ""
    total_score: float = 0.0  # 0-100
    rating: Rating = Rating.HOLD
    breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    confidence: float = 0.0  # 0-1, wie viele Datenquellen verfügbar
    summary: str = ""
    ai_comment: str = ""  # KI-generierter Score-Kommentar


class DataSourceStatus(BaseModel):
    """Zeigt an welche Datenquellen erfolgreich geladen wurden."""
    parqet: bool = True
    fmp: bool = False
    technical: bool = False       # Technische Indikatoren (aus yfinance berechnet)
    yfinance: bool = False
    fear_greed: bool = False


class DividendInfo(BaseModel):
    """Dividenden-Informationen."""
    yield_percent: Optional[float] = None  # Dividendenrendite in %
    annual_dividend: Optional[float] = None  # Jährliche Dividende pro Aktie (USD)
    ex_date: Optional[str] = None  # Nächstes Ex-Dividend-Datum
    payment_date: Optional[str] = None  # Nächstes Zahldatum
    frequency: Optional[str] = None  # "Quarterly", "Monthly", "Annual"


class EarningsInsight(BaseModel):
    """KI-generierte Earnings-Analyse."""
    ticker: str
    status: str = ""  # "reported", "upcoming", "none"
    quarter: str = ""
    beat: Optional[bool] = None
    key_takeaway: str = ""


class StockFullData(BaseModel):
    """Alle Daten zu einer Aktie kombiniert."""
    position: PortfolioPosition
    fundamentals: Optional[FundamentalData] = None
    analyst: Optional[AnalystData] = None
    technical: Optional[TechnicalIndicators] = None
    yfinance: Optional[YFinanceData] = None
    fmp_rating: Optional[FmpRating] = None
    score: Optional[StockScore] = None
    dividend: Optional[DividendInfo] = None
    data_sources: DataSourceStatus = Field(default_factory=DataSourceStatus)


class RebalancingAction(BaseModel):
    """Eine einzelne Rebalancing-Empfehlung."""
    ticker: str
    name: str = ""
    current_weight: float = 0.0  # in %
    target_weight: float = 0.0  # in %
    action: str = ""  # "Kaufen", "Verkaufen", "Halten"
    amount_eur: float = 0.0  # Betrag in EUR
    shares_delta: float = 0.0  # Anzahl Aktien +/-
    rating: Rating = Rating.HOLD
    reason: str = ""
    # v2: Erweiterte Felder
    priority: int = 0              # 1-10, höher = dringender
    sector: str = ""               # Sektor der Aktie
    score: float = 0.0             # Aktueller Score
    score_change: Optional[float] = None  # Seit letzter Analyse
    reasons: list[str] = []        # Detaillierte Gründe
    # v3: Conviction
    conviction: str = ""           # "high", "mid", "low"


class RebalancingAdvice(BaseModel):
    """Komplette Rebalancing-Empfehlung."""
    total_value: float = 0.0
    actions: list[RebalancingAction] = []
    summary: str = ""
    timestamp: datetime = Field(default_factory=_now_berlin)
    # v2: Erweiterte Metadaten
    sector_warnings: list[str] = []    # z.B. "Technology 42% > 35% Limit"
    total_buy_amount: float = 0.0      # Gesamter Kaufbetrag EUR
    total_sell_amount: float = 0.0     # Gesamter Verkaufsbetrag EUR
    net_rebalance: float = 0.0         # Netto-Differenz
    # v3: Cash & Health
    cash_current: float = 0.0          # Aktuelles Cash in EUR
    cash_current_pct: float = 0.0      # Aktuelle Cash-Quote %
    cash_target_pct: float = 5.0       # Ziel Cash-Quote %
    cash_reserve: float = 0.0          # Minimum Cash-Reserve EUR
    available_to_invest: float = 0.0   # Cash abzgl. Reserve EUR
    health_score: float = 0.0          # Portfolio-Health 0-100
    health_details: dict = {}          # Aufschlüsselung der Health-Categories



class TechRecommendation(BaseModel):
    """Eine Tech-Aktien-Empfehlung (Tech-Radar)."""
    ticker: str
    name: str = ""
    sector: str = "Technology"
    current_price: float = 0.0
    market_cap: Optional[float] = None
    pe_ratio: Optional[float] = None
    analyst_rating: Optional[str] = None
    target_price: Optional[float] = None
    upside_percent: Optional[float] = None
    ai_score: Optional[float] = None
    score: float = 0.0  # 0-100
    reason: str = ""
    tags: list[str] = []  # z.B. ["AI", "Cloud", "Semiconductor"]
    # v2: Tech-Radar Erweiterungen
    ai_summary: str = ""           # KI-generierte Investment-These
    revenue_growth: Optional[float] = None   # Revenue Growth in %
    roe: Optional[float] = None              # Return on Equity in %
    source: str = "PortfolioPilot Tech-Radar"     # Quellen-Label


class PortfolioSummary(BaseModel):
    """Gesamtübersicht des Portfolios."""
    total_value: float = 0.0
    total_cost: float = 0.0
    total_pnl: float = 0.0
    total_pnl_percent: float = 0.0
    num_positions: int = 0
    stocks: list[StockFullData] = []
    scores: list[StockScore] = []
    rebalancing: Optional[RebalancingAdvice] = None
    tech_picks: list[TechRecommendation] = []
    fear_greed: Optional[FearGreedData] = None
    last_updated: datetime = Field(default_factory=_now_berlin)
    is_demo: bool = False
    display_currency: str = "USD"
    eur_usd_rate: float = 1.0  # 1 EUR = X USD
    eur_cny_rate: float = 7.8  # 1 EUR = X CNY
    daily_total_change: float = 0.0       # Tagesänderung in EUR
    daily_total_change_pct: float = 0.0   # Tagesänderung in %


class SectorAllocation(BaseModel):
    """Sektor-Allokation."""
    sector: str
    weight: float = 0.0
    value: float = 0.0
    count: int = 0


# ─────────────────────────────────────────────────────────────
# Analyse-Report Modelle
# ─────────────────────────────────────────────────────────────

class PositionAnalysis(BaseModel):
    """Analyse-Ergebnis für eine einzelne Position."""
    ticker: str
    name: str = ""
    asset_type: str = "equity"
    market: str = "global"
    score: float = 0.0
    previous_score: Optional[float] = None
    score_change: Optional[float] = None   # Differenz zum letzten Report
    rating: Rating = Rating.HOLD
    breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    confidence: float = 0.0
    weight_in_portfolio: float = 0.0       # Anteil am Gesamtportfolio in %
    current_price: float = 0.0
    summary: str = ""


class AnalysisReport(BaseModel):
    """Vollständiger Portfolio-Analyse-Report."""
    timestamp: datetime = Field(default_factory=_now_berlin)
    analysis_level: str = "full"           # "full", "mid", "light"
    portfolio_score: float = 0.0           # Gewichteter Durchschnitt
    portfolio_rating: Rating = Rating.HOLD
    num_positions: int = 0
    positions: list[PositionAnalysis] = []
    top_buys: list[PositionAnalysis] = []    # Top 3 Kaufsignale
    top_sells: list[PositionAnalysis] = []   # Top 3 Verkaufssignale
    biggest_changes: list[PositionAnalysis] = []  # Größte Score-Änderungen
    avg_confidence: float = 0.0
    data_quality: dict = Field(default_factory=dict)  # {source: count_available}
    summary: str = ""
