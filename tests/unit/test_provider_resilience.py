"""Deterministic edge-case coverage for market-data and embedding providers."""
from __future__ import annotations

import importlib
import sys
import types
import uuid
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.providers import embeddings as embedding_module
from app.providers.embeddings import (
    EmbeddingConfigurationError,
    HashingEmbeddingProvider,
    SentenceTransformerEmbeddingProvider,
    build_embedding_provider,
)
from app.providers.market_data.base import FxPair, MarketSecurity
from app.providers.market_data.tushare import TushareProvider
from app.providers.market_data.yfinance import YFinanceProvider


def _security(symbol: str = "600519.SH", *, currency: str = "CNY") -> MarketSecurity:
    return MarketSecurity(
        security_id=uuid.uuid4(),
        canonical_symbol=symbol,
        provider_symbol=symbol,
        exchange="SSE" if symbol.endswith(".SH") else "NASDAQ",
        market="CN-A" if symbol.endswith(".SH") else "US",
        native_currency=currency,
        asset_type="equity",
    )


class _TushareClient:
    def stock_basic(self, **_kwargs):
        return pd.DataFrame(
            [
                {
                    "ts_code": "600519.SH",
                    "name": "Kweichow Moutai",
                    "area": "Guizhou",
                    "industry": "Beverage",
                    "market": "Main Board",
                    "list_date": "20010827",
                },
                {"ts_code": "", "name": "ignored"},
            ]
        )

    def daily(self, **_kwargs):
        return pd.DataFrame(
            [
                {
                    "trade_date": "20260102",
                    "open": "99",
                    "high": "102",
                    "low": "98",
                    "close": "100",
                    "vol": "1000",
                },
                {
                    "trade_date": "20260105",
                    "open": "108",
                    "high": "112",
                    "low": "107",
                    "close": "110",
                    "vol": "1200",
                },
            ]
        )

    def adj_factor(self, **_kwargs):
        return pd.DataFrame(
            [
                {"trade_date": "20260102", "adj_factor": "1"},
                {"trade_date": "20260105", "adj_factor": "2"},
            ]
        )

    def trade_cal(self, **_kwargs):
        return pd.DataFrame({"cal_date": ["20260102", "20260105"]})


@pytest.mark.asyncio
async def test_tushare_adapter_preserves_adjustment_lineage_without_network(monkeypatch):
    provider = TushareProvider("test-token")
    client = _TushareClient()
    monkeypatch.setattr(provider, "_client", lambda: client)
    security = _security()

    master = await provider.fetch_security_master()
    bars = await provider.fetch_price_bars(
        [security], start=date(2026, 1, 1), end=date(2026, 1, 6)
    )
    actions = await provider.fetch_corporate_actions(
        [security], start=date(2026, 1, 1), end=date(2026, 1, 6)
    )
    calendar = await provider.fetch_trade_calendar(
        start=date(2026, 1, 1), end=date(2026, 1, 6)
    )

    assert [item.canonical_symbol for item in master] == ["600519.SH"]
    assert master[0].currency == "CNY"
    assert bars[0].adjusted_close == bars[0].raw_close / 2
    assert bars[1].adjusted_close == bars[1].raw_close
    assert all(item.raw_payload["research_only"] is False for item in bars)
    assert [item.value for item in actions] == [bar.adjustment_factor for bar in bars]
    assert calendar == [date(2026, 1, 2), date(2026, 1, 5)]
    assert await provider.fetch_fx_rates(
        [FxPair("USD", "CNY")], start=date(2026, 1, 1), end=date(2026, 1, 6)
    ) == []


def test_tushare_provider_fails_closed_for_missing_dependency_and_invalid_price(monkeypatch):
    with pytest.raises(RuntimeError, match="TUSHARE_TOKEN"):
        TushareProvider("")._client()

    def missing_module(_name: str):
        raise ImportError("not installed")

    monkeypatch.setattr(importlib, "import_module", missing_module)
    with pytest.raises(RuntimeError, match="tushare is not installed"):
        TushareProvider("token")._client()

    class InvalidClient(_TushareClient):
        def daily(self, **_kwargs):
            return pd.DataFrame([{"trade_date": "20260102", "close": "nan"}])

    provider = TushareProvider("token")
    monkeypatch.setattr(provider, "_client", lambda: InvalidClient())
    with pytest.raises(ValueError, match="invalid close"):
        provider._fetch_price_bars_sync(
            [_security()], date(2026, 1, 1), date(2026, 1, 6)
        )


class _YFinanceTicker:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def get_info(self):
        if self.symbol == "BROKEN":
            raise RuntimeError("metadata unavailable")
        return {"longName": "Apple Inc.", "country": "United States", "sector": "Technology"}

    def history(self, **_kwargs):
        index = pd.to_datetime(["2026-01-02", "2026-01-05"], utc=True)
        if self.symbol.endswith("=X"):
            return pd.DataFrame({"Close": [7.1, 7.2]}, index=index)
        return pd.DataFrame(
            {
                "Open": [100, 101],
                "High": [102, 103],
                "Low": [99, 100],
                "Close": [101, 102],
                "Adj Close": [100.5, 101.5],
                "Volume": [1000, 1200],
                "Dividends": [0.5, 0],
                "Stock Splits": [0, 2],
            },
            index=index,
        )


class _YFinanceModule:
    @staticmethod
    def Ticker(symbol: str) -> _YFinanceTicker:
        return _YFinanceTicker(symbol)


@pytest.mark.asyncio
async def test_yfinance_metadata_fx_and_actions_are_research_only(monkeypatch):
    monkeypatch.setattr(YFinanceProvider, "_module", staticmethod(lambda: _YFinanceModule()))
    provider = YFinanceProvider()
    securities = [_security("AAPL", currency="USD"), _security("BROKEN", currency="USD")]

    master = await provider.fetch_security_master(securities)
    rates = await provider.fetch_fx_rates(
        [FxPair("usd", "cny")], start=date(2026, 1, 1), end=date(2026, 1, 6)
    )
    actions = await provider.fetch_corporate_actions(
        securities[:1], start=date(2026, 1, 1), end=date(2026, 1, 6)
    )

    assert master[0].name == "Apple Inc."
    assert master[1].name == "BROKEN"
    assert all(item.metadata == {"research_only": True} for item in master)
    assert [float(item.rate) for item in rates] == pytest.approx([7.1, 7.2])
    assert all(item.raw_payload["provider_symbol"] == "USDCNY=X" for item in rates)
    assert [(item.action_type, float(item.value)) for item in actions] == [
        ("dividend", 0.5),
        ("split", 2.0),
    ]


def test_yfinance_provider_rejects_missing_dependency_and_invalid_market_facts(monkeypatch):
    def missing_module(_name: str):
        raise ImportError("not installed")

    monkeypatch.setattr(importlib, "import_module", missing_module)
    with pytest.raises(RuntimeError, match="yfinance is not installed"):
        YFinanceProvider._module()

    class InvalidTicker(_YFinanceTicker):
        def history(self, **_kwargs):
            return pd.DataFrame(
                {"Close": [0]},
                index=pd.to_datetime(["2026-01-02"], utc=True),
            )

    class InvalidModule:
        @staticmethod
        def Ticker(symbol: str) -> InvalidTicker:
            return InvalidTicker(symbol)

    monkeypatch.setattr(YFinanceProvider, "_module", staticmethod(lambda: InvalidModule()))
    provider = YFinanceProvider()
    with pytest.raises(ValueError, match="invalid close"):
        provider._fetch_price_bars_sync(
            [_security("AAPL", currency="USD")], date(2026, 1, 1), date(2026, 1, 3)
        )
    with pytest.raises(ValueError, match="invalid FX rate"):
        provider._fetch_fx_rates_sync(
            [FxPair("USD", "CNY")], date(2026, 1, 1), date(2026, 1, 3)
        )


def _embedding_configuration(**overrides):
    values = {
        "EMBEDDING_PROVIDER": "sentence_transformers",
        "ENVIRONMENT": "test",
        "RAG_ALLOW_HASHING_FALLBACK": False,
        "hashing_fallback_allowed": False,
        "RAG_EMBEDDING_MODEL": "local-test-model",
        "RAG_EMBEDDING_DIMENSION": 2,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _install_fake_sentence_transformer(monkeypatch, model_class) -> None:
    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = model_class
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)


def test_sentence_transformer_provider_validates_dimensions_and_output_shape(monkeypatch):
    class Model:
        def __init__(self, model_name: str, *, local_files_only: bool) -> None:
            assert model_name == "fixture-model"
            assert local_files_only is True
            self.last_normalize = None

        def get_embedding_dimension(self):
            return 2

        def encode(self, texts, normalize_embeddings=False):
            self.last_normalize = normalize_embeddings
            return np.ones((len(texts), 2), dtype=np.float32)

        def __getitem__(self, index):
            assert index == 0
            return SimpleNamespace(
                auto_model=SimpleNamespace(config=SimpleNamespace(_commit_hash="fixture-sha"))
            )

    _install_fake_sentence_transformer(monkeypatch, Model)
    provider = SentenceTransformerEmbeddingProvider(
        "fixture-model", expected_dimensions=2, local_files_only=True
    )

    vectors = provider.encode(["AAPL risk"])

    assert vectors.shape == (1, 2)
    assert provider.model.last_normalize is True
    assert provider.model_version == "fixture-sha"

    provider.model.encode = lambda *_args, **_kwargs: np.ones((1, 3), dtype=np.float32)
    with pytest.raises(EmbeddingConfigurationError, match="invalid shape"):
        provider.encode(["AAPL risk"])


def test_embedding_factory_never_silently_falls_back_in_strict_mode(monkeypatch):
    class BrokenProvider:
        def __init__(self, *_args, **_kwargs):
            raise OSError("model unavailable")

    monkeypatch.setattr(
        embedding_module,
        "SentenceTransformerEmbeddingProvider",
        BrokenProvider,
    )
    strict = _embedding_configuration()
    with pytest.raises(EmbeddingConfigurationError, match="OSError"):
        build_embedding_provider(strict)

    fallback = _embedding_configuration(
        RAG_ALLOW_HASHING_FALLBACK=True,
        hashing_fallback_allowed=True,
    )
    provider = build_embedding_provider(fallback)
    assert isinstance(provider, HashingEmbeddingProvider)
    assert provider.semantic is False

    unsupported = _embedding_configuration(EMBEDDING_PROVIDER="remote-magic")
    with pytest.raises(EmbeddingConfigurationError, match="Unsupported embedding provider"):
        build_embedding_provider(unsupported)


def test_hashing_embedding_is_deterministic_and_handles_empty_text():
    provider = HashingEmbeddingProvider(16)
    first = provider.encode(["AAPL concentration", ""])
    second = provider.encode(["AAPL concentration", ""])

    assert np.array_equal(first, second)
    assert np.linalg.norm(first[0]) == pytest.approx(1.0)
    assert np.count_nonzero(first[1]) == 0
