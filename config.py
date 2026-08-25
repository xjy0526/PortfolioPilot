"""PortfolioPilot - Zentrale Konfiguration (Pydantic Settings v2)

Alle Werte werden aus .env oder Umgebungsvariablen geladen.
Type-Safety und Validierung durch Pydantic.
"""
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
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
    ENABLE_TECH_RADAR: bool = False
    ENABLE_TRADE_ADVISOR: bool = False

    # Explicit storage compatibility bridge. PostgreSQL-backed legacy URL
    # adapters do not require this flag; it controls only the old SQLite
    # initialization and JSON-to-SQLite migration path.
    ENABLE_LEGACY_SQLITE_COMPAT: bool = False

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
    ENVIRONMENT: Literal["development", "test", "production"] = "development"

    # Production safety. READ_ONLY_DEMO defaults to True in production and
    # False elsewhere when it is not explicitly configured.
    READ_ONLY_DEMO: bool | None = None
    # Enables deterministic fixture seeding for the local showcase only. This
    # mode is deliberately rejected by production preflight.
    DEMO_FIXTURE_MODE: bool = False
    ALLOW_RUNTIME_SECRET_CONFIGURATION: bool = False
    ALLOW_DEV_IDENTITY_HEADERS: bool = False

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
    PROVIDER_BASE_URL_ALLOWED_HOSTS: str = (
        "dashscope.aliyuncs.com,api.openai.com,financialmodelingprep.com"
    )

    # AI Finance Agent
    AI_AGENT_TIME: str = "16:30"

    # Local RAG for financial evidence
    RAG_DOCUMENT_DIR: str = "rag_documents"
    EMBEDDING_PROVIDER: Literal["sentence_transformers", "hashing"] = (
        "sentence_transformers"
    )
    RAG_EMBEDDING_MODEL: str = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    RAG_ALLOW_HASHING_FALLBACK: bool | None = None
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
    RAG_SUCCESS_SOURCE_RETENTION_DAYS: int = 0
    RAG_FAILURE_SOURCE_RETENTION_DAYS: int = 7

    # Object storage. Local storage is suitable for a single-host development
    # setup; production should use an S3-compatible shared bucket.
    OBJECT_STORAGE_BACKEND: Literal["local", "s3"] = "local"
    OBJECT_STORAGE_LOCAL_ROOT: str = "data/object_storage"
    S3_ENDPOINT_URL: str = ""
    S3_BUCKET: str = ""
    S3_REGION: str = ""
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    S3_PREFIX: str = ""
    S3_FORCE_PATH_STYLE: bool = False
    # Deprecated aliases retained for existing deployments during migration.
    OBJECT_STORAGE_BUCKET: str = ""
    OBJECT_STORAGE_PREFIX: str = "research-ingestion"
    OBJECT_STORAGE_ENDPOINT_URL: str = ""
    OBJECT_STORAGE_REGION: str = ""
    OBJECT_STORAGE_ACCESS_KEY_ID: str = ""
    OBJECT_STORAGE_SECRET_ACCESS_KEY: str = ""

    # Durable workflow worker recovery.
    WORKFLOW_LEASE_SECONDS: int = 60
    WORKFLOW_MAX_ATTEMPTS: int = 5
    WORKFLOW_RETRY_BASE_SECONDS: int = 2

    # Server-derived local principal used only when Basic Auth is disabled.
    LOCAL_PRINCIPAL_USER: str = "local-user"
    LOCAL_PRINCIPAL_TENANT: str = "default"
    LOCAL_PRINCIPAL_ROLES: str = (
        "platform_admin,operator,market_data_admin,knowledge_admin,research_reviewer"
    )
    LOCAL_PRINCIPAL_GROUPS: str = "public"

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
    DASHBOARD_TENANT_ID: str = "default"
    DASHBOARD_ROLES: str = "platform_admin"
    DASHBOARD_PERMISSION_GROUPS: str = "public"

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
    def read_only_demo(self) -> bool:
        return bool(self.READ_ONLY_DEMO)

    @computed_field
    @property
    def hashing_fallback_allowed(self) -> bool:
        return bool(self.RAG_ALLOW_HASHING_FALLBACK)

    @computed_field
    @property
    def object_storage_bucket(self) -> str:
        if self.OBJECT_STORAGE_BACKEND == "s3":
            return (self.S3_BUCKET or self.OBJECT_STORAGE_BUCKET).strip()
        return self.OBJECT_STORAGE_BUCKET.strip() or "portfoliopilot-ingestion"

    @computed_field
    @property
    def object_storage_prefix(self) -> str:
        if self.OBJECT_STORAGE_BACKEND == "s3":
            return (self.S3_PREFIX or self.OBJECT_STORAGE_PREFIX).strip().strip("/")
        return self.OBJECT_STORAGE_PREFIX.strip().strip("/")

    @computed_field
    @property
    def s3_endpoint_url(self) -> str:
        return (self.S3_ENDPOINT_URL or self.OBJECT_STORAGE_ENDPOINT_URL).strip()

    @computed_field
    @property
    def s3_region(self) -> str:
        return (self.S3_REGION or self.OBJECT_STORAGE_REGION).strip()

    @computed_field
    @property
    def s3_access_key_id(self) -> str:
        return (self.S3_ACCESS_KEY_ID or self.OBJECT_STORAGE_ACCESS_KEY_ID).strip()

    @computed_field
    @property
    def s3_secret_access_key(self) -> str:
        return (self.S3_SECRET_ACCESS_KEY or self.OBJECT_STORAGE_SECRET_ACCESS_KEY).strip()

    @computed_field
    @property
    def missing_s3_settings(self) -> tuple[str, ...]:
        required = {
            "S3_BUCKET": self.object_storage_bucket,
            "S3_REGION": self.s3_region,
            "S3_ACCESS_KEY_ID": self.s3_access_key_id,
            "S3_SECRET_ACCESS_KEY": self.s3_secret_access_key,
        }
        return tuple(name for name, value in required.items() if not value)

    @computed_field
    @property
    def resolved_code_version(self) -> str:
        configured = self.CODE_VERSION.strip()
        if configured and configured != "unknown":
            return configured
        return (
            os.getenv("RENDER_GIT_COMMIT", "").strip()
            or os.getenv("GITHUB_SHA", "").strip()
            or "unknown"
        )

    @computed_field
    @property
    def provider_base_url_allowed_hosts(self) -> frozenset[str]:
        return frozenset(
            item.strip().lower()
            for item in self.PROVIDER_BASE_URL_ALLOWED_HOSTS.split(",")
            if item.strip()
        )

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

    @field_validator(
        "WORKFLOW_LEASE_SECONDS",
        "WORKFLOW_MAX_ATTEMPTS",
        "WORKFLOW_RETRY_BASE_SECONDS",
    )
    @classmethod
    def validate_positive_worker_setting(cls, value: int) -> int:
        if value < 1:
            raise ValueError("Workflow worker settings must be positive")
        return value

    def validate_provider_base_url(self, value: str, *, setting_name: str) -> str:
        """Validate an operator-supplied provider URL without exposing secrets."""
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        host = (parsed.hostname or "").lower()
        local_host = host in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (
            self.ENVIRONMENT in {"development", "test"} and local_host
        ):
            raise ValueError(f"{setting_name} must use HTTPS")
        if host not in self.provider_base_url_allowed_hosts and not (
            self.ENVIRONMENT in {"development", "test"} and local_host
        ):
            raise ValueError(f"{setting_name} host is not allowlisted")
        return normalized

    def validate_runtime_configuration(self) -> None:
        """Fail closed when a production deployment has an unsafe identity mode."""
        if self.ENVIRONMENT == "production" and self.DEMO_FIXTURE_MODE:
            raise RuntimeError("DEMO_FIXTURE_MODE is forbidden in production")
        if self.ENVIRONMENT != "production":
            return
        if self.ALLOW_DEV_IDENTITY_HEADERS:
            raise RuntimeError("Development identity headers are forbidden in production")
        if self.ENABLE_LEGACY_SQLITE_COMPAT:
            raise RuntimeError("Legacy SQLite compatibility is forbidden in production")
        if not self.read_only_demo and not self.auth_configured:
            raise RuntimeError(
                "Production must enable READ_ONLY_DEMO or configure authentication"
            )
        if not self.read_only_demo and self.OBJECT_STORAGE_BACKEND != "s3":
            raise RuntimeError(
                "Writable production deployments require S3-compatible object storage"
            )
        if not self.read_only_demo and self.missing_s3_settings:
            raise RuntimeError(
                "Writable production S3 configuration is incomplete: "
                + ", ".join(self.missing_s3_settings)
            )
        if self.s3_endpoint_url:
            parsed_endpoint = urlparse(self.s3_endpoint_url)
            if parsed_endpoint.scheme != "https" or not parsed_endpoint.hostname:
                raise RuntimeError("Production S3_ENDPOINT_URL must be an absolute HTTPS URL")
        self.validate_provider_base_url(self.QWEN_BASE_URL, setting_name="QWEN_BASE_URL")
        self.validate_provider_base_url(
            self.OPENAI_COMPATIBLE_BASE_URL,
            setting_name="OPENAI_COMPATIBLE_BASE_URL",
        )
        self.validate_provider_base_url(self.FMP_BASE_URL, setting_name="FMP_BASE_URL")
        if self.ENABLE_PARQET:
            self.validate_provider_base_url(
                self.PARQET_API_BASE_URL,
                setting_name="PARQET_API_BASE_URL",
            )

    def model_post_init(self, __context) -> None:
        if self.READ_ONLY_DEMO is None:
            object.__setattr__(
                self,
                "READ_ONLY_DEMO",
                self.ENVIRONMENT == "production",
            )
        if self.RAG_ALLOW_HASHING_FALLBACK is None:
            object.__setattr__(
                self,
                "RAG_ALLOW_HASHING_FALLBACK",
                self.ENVIRONMENT in {"development", "test"},
            )
        # Sync PORT → SERVER_PORT (Cloud Run setzt PORT)
        if self.PORT != 8000 and self.SERVER_PORT == 8000:
            object.__setattr__(self, "SERVER_PORT", self.PORT)


settings = Settings()
