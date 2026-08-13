"""Import every ORM model so Alembic can discover complete metadata."""

from app.db.models.base import Base
from app.db.models.backtest import (
    BacktestRebalanceSnapshot,
    BacktestRun,
    BacktestStrategyResult,
)
from app.db.models.identity import Portfolio, PortfolioMembership, User
from app.db.models.governance import (
    ChunkEmbedding,
    DocumentChunk,
    DocumentVersion,
    IngestionJob,
    LLMCallTrace,
    PromptDeployment,
    PromptTemplate,
    PromptVersion,
    PublishedReport,
    ResearchDocument,
    ReviewDecision,
    ReviewTask,
    WorkflowRun,
    WorkflowStep,
)
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
    "ChunkEmbedding",
    "DocumentChunk",
    "DocumentVersion",
    "ImportBatch",
    "IngestionJob",
    "LLMCallTrace",
    "Portfolio",
    "PortfolioMembership",
    "PortfolioValuationSnapshot",
    "PositionSnapshot",
    "PriceBar",
    "PromptDeployment",
    "PromptTemplate",
    "PromptVersion",
    "ProviderSymbol",
    "PublishedReport",
    "ResearchDocument",
    "ReviewDecision",
    "ReviewTask",
    "RiskRun",
    "Security",
    "SyncRun",
    "Transaction",
    "User",
    "WorkflowRun",
    "WorkflowStep",
]
