from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """Application settings from environment variables."""

    # App
    app_env: str = "development"
    secret_key: str = "dev-secret-key-change-in-production"
    cors_origins: str = "http://localhost:5173,http://localhost:7070,http://localhost:3000,http://localhost:3002"

    # Database
    database_url: str = "postgresql://postgres:postgres@localhost:5432/navigator"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # API Keys - Market Data
    polygon_api_key: str = ""
    finnhub_api_key: str = ""
    alpha_vantage_api_key: str = ""

    # API Keys - News
    marketaux_api_key: str = ""

    # API Keys - FX
    exchangerate_api_key: str = ""

    # API Keys - AI
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # Cache TTL (seconds)
    quote_cache_ttl: int = 60
    news_cache_ttl: int = 900
    fx_cache_ttl: int = 3600

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
