"""Import every ORM model so Alembic can discover complete metadata."""

from app.db.models.base import Base
from app.db.models.backtest import (
    BacktestRebalanceSnapshot,
    BacktestRun,
    BacktestStrategyResult,
)
from app.db.models.identity import Portfolio, User
from app.db.models.market import FxRate, PriceBar, ProviderSymbol, Security
from app.db.models.portfolio import (
    ImportBatch,
    PortfolioValuationSnapshot,
    PositionSnapshot,
    Transaction,
)
from app.db.models.runs import RiskRun, SyncRun

__all__ = [
    "Base",
    "BacktestRebalanceSnapshot",
    "BacktestRun",
    "BacktestStrategyResult",
    "FxRate",
    "ImportBatch",
    "Portfolio",
    "PortfolioValuationSnapshot",
    "PositionSnapshot",
    "PriceBar",
    "ProviderSymbol",
    "RiskRun",
    "Security",
    "SyncRun",
    "Transaction",
    "User",
]
