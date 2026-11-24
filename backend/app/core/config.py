from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "AgentOS"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://agentos:agentos@localhost:5432/agentos"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "development-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 7
    cors_origins: str = "http://localhost:3000"
    docs_enabled: bool = True
    openai_api_key: str = ""
    github_webhook_secret: str = ""
    encryption_key: str = ""
    http_allowed_hosts: str = ""
    database_allowed_hosts: str = ""

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if self.environment.lower() in {"production", "prod"}:
            if self.jwt_secret == "development-secret-change-me" or len(self.jwt_secret) < 32:
                raise ValueError("JWT_SECRET must contain at least 32 characters in production")
            if not self.github_webhook_secret:
                raise ValueError("GITHUB_WEBHOOK_SECRET must be configured in production")
            if not self.encryption_key:
                raise ValueError("ENCRYPTION_KEY must be configured in production")
        return self

    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def http_allowed_host_list(self) -> list[str]:
        return [host.strip().lower() for host in self.http_allowed_hosts.split(",") if host.strip()]

    def database_allowed_host_list(self) -> list[str]:
        return [host.strip().lower() for host in self.database_allowed_hosts.split(",") if host.strip()]

settings = Settings()
