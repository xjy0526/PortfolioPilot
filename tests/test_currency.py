"""PortfolioPilot - Tests für Währungsumrechnung."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fetchers.currency import fetch_eur_usd_rate, fetch_eur_dkk_rate, fetch_eur_gbp_rate, DEFAULT_EUR_USD, DEFAULT_EUR_DKK, DEFAULT_EUR_GBP


class TestFetchEurUsdRate:
    @pytest.mark.asyncio
    async def test_fallback_rate(self):
        """Ohne API-Key oder Netzwerk muss ein valider Fallback kommen."""
        with patch("fetchers.currency._cache") as mock_cache:
            mock_cache.get.return_value = None
            with patch("fetchers.currency._fetch_from_fmp", new_callable=AsyncMock, return_value=None):
                with patch("fetchers.currency._fetch_all_rates_from_exchangerate_api", new_callable=AsyncMock, return_value=None):
                    rate = await fetch_eur_usd_rate()
                    assert isinstance(rate, float)
                    assert rate == DEFAULT_EUR_USD

    def test_default_constants(self):
        assert 0.5 < DEFAULT_EUR_USD < 2.0
        assert DEFAULT_EUR_USD == 1.08
        assert 5.0 < DEFAULT_EUR_DKK < 10.0
        assert 0.5 < DEFAULT_EUR_GBP < 1.5

    @pytest.mark.asyncio
    async def test_cached_rate(self):
        """Gecachter Kurs sollte direkt zurückgegeben werden."""
        with patch("fetchers.currency._cache") as mock_cache:
            mock_cache.get.return_value = 1.10
            rate = await fetch_eur_usd_rate()
            assert rate == 1.10

    @pytest.mark.asyncio
    async def test_rate_sanity_check(self):
        """EUR/USD muss immer zwischen 0.5 und 2.0 liegen."""
        rate = await fetch_eur_usd_rate()
        assert 0.5 < rate < 2.0


class TestFetchEurDkkRate:
    @pytest.mark.asyncio
    async def test_fallback_rate(self):
        """Ohne Netzwerk muss ein valider Fallback kommen."""
        with patch("fetchers.currency._cache") as mock_cache:
            mock_cache.get.return_value = None
            with patch("fetchers.currency._fetch_all_rates_from_exchangerate_api", new_callable=AsyncMock, return_value=None):
                rate = await fetch_eur_dkk_rate()
                assert rate == DEFAULT_EUR_DKK

    @pytest.mark.asyncio
    async def test_from_api(self):
        """Korrekte Rate aus API-Response extrahieren."""
        with patch("fetchers.currency._cache") as mock_cache:
            mock_cache.get.return_value = None
            with patch("fetchers.currency._fetch_all_rates_from_exchangerate_api", new_callable=AsyncMock, return_value={"DKK": 7.45, "USD": 1.09}):
                rate = await fetch_eur_dkk_rate()
                assert rate == 7.45


class TestFetchEurGbpRate:
    @pytest.mark.asyncio
    async def test_fallback_rate(self):
        """Ohne Netzwerk muss ein valider Fallback kommen."""
        with patch("fetchers.currency._cache") as mock_cache:
            mock_cache.get.return_value = None
            with patch("fetchers.currency._fetch_all_rates_from_exchangerate_api", new_callable=AsyncMock, return_value=None):
                rate = await fetch_eur_gbp_rate()
                assert rate == DEFAULT_EUR_GBP
