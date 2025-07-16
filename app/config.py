"""Central configuration for agentgate, loaded from environment variables."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENTGATE_", env_file=".env", extra="ignore")

    # Database
    database_url: str = "sqlite+aiosqlite:///./agentgate.db"

    # Redis (rate limiting / budgets). "memory://" uses fakeredis (no network, test/dev default).
    redis_url: str = "memory://"

    # Auth
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    admin_api_key: str = "dev-admin-key-change-me"

    # OpenAPI spec the gateway ingests to build its tool catalog
    openapi_spec_path: str = "demo/ecommerce_openapi.json"

    # Downstream API the gateway proxies calls to (the sample e-commerce API by default)
    downstream_base_url: str = "http://localhost:9000"
    downstream_timeout_seconds: float = 10.0

    # Rate limiting defaults (used when an agent has no explicit override)
    default_rate_limit_per_minute: int = 60
    default_daily_cost_budget_cents: int = 500  # $5.00/day/agent default

    # OpenTelemetry
    otel_service_name: str = "agentgate"
    otel_console_export: bool = False  # set True to print spans to stdout (debugging)

    # Misc
    environment: str = "development"


settings = Settings()
