"""PortfolioPilot - Tests für den CurrencyConverter."""
import pytest
from unittest.mock import patch, AsyncMock
from services.currency_converter import CurrencyConverter, ExchangeRates


@pytest.fixture
def converter():
    """CurrencyConverter mit bekannten Test-Rates."""
    rates = ExchangeRates(eur_usd=1.10, eur_dkk=7.45, eur_gbp=0.85)
    return CurrencyConverter(rates)


class TestToEur:
    def test_usd_to_eur(self, converter):
        """US-Ticker werden von USD nach EUR konvertiert."""
        result = converter.to_eur(110.0, "AAPL")
        assert result == 100.0  # 110 / 1.10

    def test_de_ticker_no_conversion(self, converter):
        """Deutsche Ticker (.DE) bleiben in EUR."""
        result = converter.to_eur(100.0, "SAP.DE")
        assert result == 100.0

    def test_frankfurt_ticker_no_conversion(self, converter):
        """Frankfurter Ticker (.F) bleiben in EUR."""
        result = converter.to_eur(50.0, "BMW.F")
        assert result == 50.0

    def test_copenhagen_to_eur(self, converter):
        """Kopenhagen (.CO) wird von DKK nach EUR konvertiert."""
        result = converter.to_eur(745.0, "NOVO-B.CO")
        assert result == 100.0  # 745 / 7.45

    def test_london_to_eur(self, converter):
        """London (.L) wird von GBP nach EUR konvertiert."""
        result = converter.to_eur(85.0, "HSBA.L")
        assert result == 100.0  # 85 / 0.85

    def test_isin_fund_no_conversion(self, converter):
        """ISIN-basierte Fonds bleiben in EUR."""
        result = converter.to_eur(42.50, "DE000A2QJLA8")
        assert result == 42.50

    def test_zero_price(self, converter):
        """Preis 0 bleibt 0."""
        result = converter.to_eur(0.0, "AAPL")
        assert result == 0.0

    def test_negative_price(self, converter):
        """Negativer Preis wird nicht konvertiert."""
        result = converter.to_eur(-10.0, "AAPL")
        assert result == -10.0

    def test_yfinance_alias_resolution(self, converter):
        """Ticker werden über YFINANCE_ALIASES aufgelöst (z.B. DTEGY → DTE.DE)."""
        with patch("services.currency_converter.YFINANCE_ALIASES", {"DTEGY": "DTE.DE"}):
            result = converter.to_eur(50.0, "DTEGY")
            assert result == 50.0  # DTE.DE = EUR, keine Konvertierung


class TestIsEurNative:
    def test_de_is_native(self, converter):
        assert converter.is_eur_native("SAP.DE") is True

    def test_us_is_not_native(self, converter):
        assert converter.is_eur_native("AAPL") is False

    def test_isin_is_native(self, converter):
        assert converter.is_eur_native("DE000A2QJLA8") is True


class TestExchangeRates:
    def test_defaults(self):
        rates = ExchangeRates()
        assert 0.5 < rates.eur_usd < 2.0
        assert 5.0 < rates.eur_dkk < 10.0
        assert 0.5 < rates.eur_gbp < 1.5


class TestCurrencyConverterCreate:
    @pytest.mark.asyncio
    async def test_create_with_override(self):
        """eur_usd_override wird bevorzugt."""
        with patch("services.currency_converter.fetch_eur_usd_rate", new_callable=AsyncMock, return_value=1.08):
            with patch("services.currency_converter.fetch_eur_dkk_rate", new_callable=AsyncMock, return_value=7.46):
                with patch("services.currency_converter.fetch_eur_gbp_rate", new_callable=AsyncMock, return_value=0.855):
                    converter = await CurrencyConverter.create(eur_usd_override=1.12)
                    assert converter.rates.eur_usd == 1.12
                    assert converter.rates.eur_dkk == 7.46
