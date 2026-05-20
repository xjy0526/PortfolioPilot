"""PortfolioPilot - Zentrale Währungskonvertierung

Einheitliche EUR-Konvertierung basierend auf Ticker-Suffix und ISIN-Erkennung.
Ersetzt die 3× duplizierte Konvertierungslogik in refresh.py.
"""
import logging
from dataclasses import dataclass
from typing import Optional

from state import YFINANCE_ALIASES
from fetchers.currency import (
    fetch_eur_usd_rate, fetch_eur_dkk_rate, fetch_eur_gbp_rate,
    fetch_eur_cny_rate,
    DEFAULT_EUR_USD, DEFAULT_EUR_DKK, DEFAULT_EUR_GBP, DEFAULT_EUR_CNY,
)

logger = logging.getLogger(__name__)


@dataclass
class ExchangeRates:
    """Aktuelle Wechselkurse für EUR-Konvertierung."""
    eur_usd: float = DEFAULT_EUR_USD
    eur_dkk: float = DEFAULT_EUR_DKK
    eur_gbp: float = DEFAULT_EUR_GBP
    eur_cny: float = DEFAULT_EUR_CNY


class CurrencyConverter:
    """Konvertiert Aktienpreise in EUR basierend auf Ticker-Suffix.

    Erkennung:
      - .DE / .F     → Deutsche Börse (EUR, keine Konvertierung)
      - .CO           → Kopenhagen (DKK → EUR)
      - .L / .LON     → London (GBP → EUR)
      - .SS / .SZ      → China A-Shares (CNY → EUR)
      - ISIN (12-stellig, 2 Buchstaben am Anfang) → EUR
      - Alles andere  → USD → EUR
    """

    def __init__(self, rates: ExchangeRates):
        self.rates = rates

    @classmethod
    async def create(cls, eur_usd_override: Optional[float] = None) -> "CurrencyConverter":
        """Factory: Lädt aktuelle Wechselkurse und erstellt den Converter.

        Args:
            eur_usd_override: Optionaler EUR/USD-Kurs (z.B. aus Summary).
                              Wird bevorzugt, wenn > 0.
        """
        rates = ExchangeRates()

        try:
            rates.eur_usd = await fetch_eur_usd_rate()
        except Exception:
            logger.warning(f"EUR/USD nicht verfügbar, nutze Default {DEFAULT_EUR_USD}")

        if eur_usd_override and eur_usd_override > 0:
            rates.eur_usd = eur_usd_override

        try:
            rates.eur_dkk = await fetch_eur_dkk_rate()
        except Exception:
            logger.warning(f"EUR/DKK nicht verfügbar, nutze Default {DEFAULT_EUR_DKK}")

        try:
            rates.eur_gbp = await fetch_eur_gbp_rate()
        except Exception:
            logger.warning(f"EUR/GBP nicht verfügbar, nutze Default {DEFAULT_EUR_GBP}")

        try:
            rates.eur_cny = await fetch_eur_cny_rate()
        except Exception:
            logger.warning(f"EUR/CNY nicht verfügbar, nutze Default {DEFAULT_EUR_CNY}")

        logger.info(
            "💱 Wechselkurse: "
            f"USD={rates.eur_usd}, DKK={rates.eur_dkk}, "
            f"GBP={rates.eur_gbp}, CNY={rates.eur_cny}"
        )
        return cls(rates)

    def to_eur(self, price: float, ticker: str) -> float:
        """Konvertiert einen Preis nach EUR basierend auf Ticker-Suffix.

        Args:
            price: Rohpreis in Originalwährung
            ticker: Original-Ticker (wird durch YFINANCE_ALIASES aufgelöst)

        Returns:
            Preis in EUR (gerundet auf 2 Dezimalstellen)
        """
        if price <= 0:
            return price

        yf_ticker = YFINANCE_ALIASES.get(ticker, ticker)

        if yf_ticker.endswith(('.DE', '.F')):
            # Deutsche Börse → EUR
            return price

        if yf_ticker.endswith('.CO'):
            # Kopenhagen → DKK → EUR
            if self.rates.eur_dkk > 0:
                return round(price / self.rates.eur_dkk, 2)
            return price

        if yf_ticker.endswith(('.L', '.LON')):
            # London → GBP → EUR
            if self.rates.eur_gbp > 0:
                return round(price / self.rates.eur_gbp, 2)
            return price

        if yf_ticker.endswith(('.SS', '.SZ')):
            # China A-Shares → CNY → EUR
            if self.rates.eur_cny > 0:
                return round(price / self.rates.eur_cny, 2)
            return price

        if len(ticker) == 12 and ticker[:2].isalpha():
            # ISIN-basierte Fonds → EUR
            return price

        # Default: USD → EUR
        if self.rates.eur_usd > 0:
            return round(price / self.rates.eur_usd, 2)
        return price

    def is_eur_native(self, ticker: str) -> bool:
        """Prüft ob der Ticker nativ in EUR gehandelt wird."""
        yf_ticker = YFINANCE_ALIASES.get(ticker, ticker)
        if yf_ticker.endswith(('.DE', '.F')):
            return True
        if yf_ticker.endswith(('.SS', '.SZ')):
            return False
        if len(ticker) == 12 and ticker[:2].isalpha():
            return True
        return False
