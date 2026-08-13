"""PortfolioPilot - Zentrale Konfiguration (Pydantic Settings v2)

Alle Werte werden aus .env oder Umgebungsvariablen geladen.
Type-Safety und Validierung durch Pydantic.
"""
from pathlib import Path
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import computed_field, field_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BASE_DIR = Path(__file__).parent


class Settings(BaseSettings):
    """App-Konfiguration aus Environment-Variablen."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Branding
    APP_NAME: str = "PortfolioPilot"
    APP_TAGLINE: str = "Multi-market portfolio risk analysis and evidence-driven research."
    CONTACT_EMAIL: str = ""
    APP_MODE: Literal["personal", "fund_research"] = "personal"
    DISPLAY_TIMEZONE: str = "Asia/Shanghai"
    DEFAULT_PORTFOLIO_ID: str = ""

    # Optional extensions are opt-in. Core CSV, risk, RAG and backtest flows do
    # not depend on these integrations.
    ENABLE_POLYMARKET: bool = False
    ENABLE_TELEGRAM: bool = False
    ENABLE_PARQET: bool = False
    ENABLE_SHADOW_AGENT: bool = False

    # Financial Modeling Prep
    FMP_API_KEY: str = ""
    FMP_BASE_URL: str = "https://financialmodelingprep.com/stable"

    # Parqet Connect API (OAuth2)
    PARQET_CLIENT_ID: str = ""
    PARQET_CLIENT_SECRET: str = ""
    PARQET_ACCESS_TOKEN: str = ""
    PARQET_REFRESH_TOKEN: str = ""
    PARQET_PORTFOLIO_ID: str = ""
    PARQET_API_BASE_URL: str = "https://connect.parqet.com"

    # Parqet CSV Fallback
    PARQET_PORTFOLIO_CSV: str = "portfolio.csv"

    # Server
    SERVER_HOST: str = "0.0.0.0"
    PORT: int = 8000
    SERVER_PORT: int = 8000

    # Environment
    ENVIRONMENT: str = "development"

    # PostgreSQL. Engine construction is lazy and never creates tables; schema
    # changes are owned exclusively by Alembic.
    DATABASE_URL: str = (
        "postgresql+asyncpg://portfoliopilot:portfoliopilot@localhost:5432/portfoliopilot"
    )
    DATABASE_ECHO: bool = False
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 10
    DATABASE_POOL_TIMEOUT_SECONDS: int = 30

    # Transaction import and ledger workers
    MAX_TRANSACTION_IMPORT_BYTES: int = 5 * 1024 * 1024
    CODE_VERSION: str = "unknown"

    # Market-data providers. Tushare is the preferred A-share source;
    # yfinance remains a research-only source for US equities and ETFs.
    TUSHARE_TOKEN: str = ""
    MARKET_SYNC_LOOKBACK_DAYS: int = 365
    MARKET_DATA_PROVIDERS: str = "yfinance"

    # Scheduler
    DAILY_REFRESH_TIME: str = "06:00"
    PRICE_UPDATE_INTERVAL_MIN: int = 15

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_WEBHOOK_SECRET: str = ""  # Geheimes Token in der Webhook-URL

    # AI Provider (Qwen / DashScope compatible OpenAI API)
    AI_PROVIDER: str = "qwen"
    QWEN_API_KEY: str = ""
    QWEN_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    QWEN_MODEL: str = "qwen-plus"
    QWEN_REASONING_MODEL: str = ""
    OPENAI_COMPATIBLE_API_KEY: str = ""
    OPENAI_COMPATIBLE_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_COMPATIBLE_MODEL: str = "gpt-4.1-mini"

    # AI Finance Agent
    AI_AGENT_TIME: str = "16:30"

    # Local RAG for financial evidence
    RAG_DOCUMENT_DIR: str = "rag_documents"
    RAG_EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    RAG_CHUNK_SIZE: int = 900
    RAG_TOP_K: int = 5
    RAG_VECTOR_BACKEND: str = "pgvector"
    RAG_SCORE_THRESHOLD: float = 0.15
    RAG_VECTOR_SCORE_THRESHOLD: float = 0.20
    RAG_RRF_K: int = 60
    RAG_RETRIEVAL_POOL_SIZE: int = 20
    RAG_EMBEDDING_DIMENSION: int = 384
    RAG_RERANKER_ENABLED: bool = False
    RAG_MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024
    RAG_MAX_PDF_PAGES: int = 200
    RAG_MAX_CHUNKS: int = 5000
    RAG_PARSE_TIMEOUT_SECONDS: int = 60
    RAG_INGESTION_DIR: str = "data/ingestion"

    # Server-derived local principal used only when Basic Auth is disabled.
    LOCAL_PRINCIPAL_USER: str = "local-user"
    LOCAL_PRINCIPAL_GROUPS: str = "public,knowledge_admin,research_reviewer"

    # Strategy backtest
    BACKTEST_PRICE_CSV: str = ""

    # Historical adjusted-close prices for portfolio risk analytics
    PRICE_HISTORY_PROVIDER: Literal["yfinance", "csv"] = "yfinance"
    PRICE_HISTORY_CSV: str = ""
    PRICE_HISTORY_LOOKBACK_DAYS: int = 365
    PRICE_HISTORY_STALE_AFTER_DAYS: int = 5
    RISK_MIN_OBSERVATIONS: int = 20

    # Caching
    CACHE_TTL_HOURS: int = 12

    # Dashboard-Zugangsschutz (Basic Auth)
    DASHBOARD_USER: str = ""
    DASHBOARD_PASSWORD: str = ""

    # ── Computed Fields ──

    @computed_field
    @property
    def CACHE_DIR(self) -> Path:
        d = BASE_DIR / "cache"
        d.mkdir(exist_ok=True)
        return d

    @computed_field
    @property
    def parqet_api_configured(self) -> bool:
        """True wenn Parqet API-Zugang konfiguriert ist."""
        has_token = bool(self.PARQET_ACCESS_TOKEN or self.PARQET_REFRESH_TOKEN)
        return bool(self.ENABLE_PARQET and has_token and self.PARQET_PORTFOLIO_ID)

    @computed_field
    @property
    def telegram_configured(self) -> bool:
        return bool(
            self.ENABLE_TELEGRAM
            and self.TELEGRAM_BOT_TOKEN
            and self.TELEGRAM_CHAT_ID
        )

    @computed_field
    @property
    def qwen_configured(self) -> bool:
        return bool(self.QWEN_API_KEY)

    @computed_field
    @property
    def ai_configured(self) -> bool:
        provider = str(self.AI_PROVIDER or "qwen").lower()
        if provider == "qwen":
            return self.qwen_configured
        if provider in {"openai", "openai_compatible"}:
            return bool(self.OPENAI_COMPATIBLE_API_KEY)
        return False

    @computed_field
    @property
    def configured_ai_model(self) -> str:
        provider = str(self.AI_PROVIDER or "qwen").lower()
        if provider in {"openai", "openai_compatible"}:
            return self.OPENAI_COMPATIBLE_MODEL
        return self.QWEN_MODEL

    @computed_field
    @property
    def gemini_configured(self) -> bool:
        """Deprecated compatibility alias for legacy business modules."""
        return self.ai_configured

    @computed_field
    @property
    def shadow_agent_enabled(self) -> bool:
        return bool(self.ENABLE_SHADOW_AGENT and not self.fund_research_mode)

    @computed_field
    @property
    def demo_mode(self) -> bool:
        return not self.FMP_API_KEY or self.FMP_API_KEY == "your_fmp_api_key_here"

    @computed_field
    @property
    def auth_configured(self) -> bool:
        return bool(self.DASHBOARD_USER and self.DASHBOARD_PASSWORD)

    @computed_field
    @property
    def fund_research_mode(self) -> bool:
        return self.APP_MODE == "fund_research"

    @field_validator("DISPLAY_TIMEZONE")
    @classmethod
    def validate_display_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown DISPLAY_TIMEZONE: {value}") from exc
        return value

    @field_validator("DATABASE_URL")
    @classmethod
    def normalize_async_database_url(cls, value: str) -> str:
        normalized = value.strip()
        if normalized.startswith("postgres://"):
            normalized = "postgresql+asyncpg://" + normalized.removeprefix("postgres://")
        elif normalized.startswith("postgresql://"):
            normalized = "postgresql+asyncpg://" + normalized.removeprefix("postgresql://")
        if not normalized.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use PostgreSQL with the asyncpg driver")
        return normalized

    @field_validator("RAG_EMBEDDING_DIMENSION")
    @classmethod
    def validate_rag_embedding_dimension(cls, value: int) -> int:
        if value != 384:
            raise ValueError(
                "RAG_EMBEDDING_DIMENSION is fixed at 384 by the current pgvector schema; "
                "change it only with an Alembic migration"
            )
        return value

    def model_post_init(self, __context) -> None:
        # Sync PORT → SERVER_PORT (Cloud Run setzt PORT)
        if self.PORT != 8000 and self.SERVER_PORT == 8000:
            object.__setattr__(self, "SERVER_PORT", self.PORT)


settings = Settings()
