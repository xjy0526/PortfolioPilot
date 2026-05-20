"""PortfolioPilot - Zentrale Konfiguration (Pydantic Settings v2)

Alle Werte werden aus .env oder Umgebungsvariablen geladen.
Type-Safety und Validierung durch Pydantic.
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import computed_field

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
    APP_TAGLINE: str = "AI portfolio copilot for global equities, China A-shares and Polymarket."

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

    # Legacy Google Gemini / Vertex AI settings (kept for compatibility)
    GEMINI_API_KEY: str = ""
    GCP_PROJECT_ID: str = ""
    GCP_LOCATION: str = "europe-west1"

    # AI Finance Agent
    AI_AGENT_TIME: str = "16:30"

    # Local RAG for financial evidence
    RAG_DOCUMENT_DIR: str = "rag_documents"
    RAG_EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    RAG_CHUNK_SIZE: int = 900
    RAG_TOP_K: int = 5
    RAG_VECTOR_BACKEND: str = "faiss"

    # Strategy backtest
    BACKTEST_PRICE_CSV: str = ""

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
        return bool(has_token and self.PARQET_PORTFOLIO_ID)

    @computed_field
    @property
    def telegram_configured(self) -> bool:
        return bool(self.TELEGRAM_BOT_TOKEN and self.TELEGRAM_CHAT_ID)

    @computed_field
    @property
    def vertex_ai_configured(self) -> bool:
        return bool(self.GCP_PROJECT_ID)

    @computed_field
    @property
    def qwen_configured(self) -> bool:
        return bool(self.QWEN_API_KEY)

    @computed_field
    @property
    def gemini_configured(self) -> bool:
        return (
            self.qwen_configured
            or self.vertex_ai_configured
            or bool(self.GEMINI_API_KEY)
        )

    @computed_field
    @property
    def demo_mode(self) -> bool:
        return not self.FMP_API_KEY or self.FMP_API_KEY == "your_fmp_api_key_here"

    @computed_field
    @property
    def auth_configured(self) -> bool:
        return bool(self.DASHBOARD_USER and self.DASHBOARD_PASSWORD)

    def model_post_init(self, __context) -> None:
        # Sync PORT → SERVER_PORT (Cloud Run setzt PORT)
        if self.PORT != 8000 and self.SERVER_PORT == 8000:
            object.__setattr__(self, "SERVER_PORT", self.PORT)


settings = Settings()
