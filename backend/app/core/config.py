from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "AgentOS"
    database_url: str = "postgresql+asyncpg://agentos:agentos@localhost:5432/agentos"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "development-secret-change-me"
    jwt_algorithm: str = "HS256"
    openai_api_key: str = ""
    github_webhook_secret: str = ""
    encryption_key: str = ""


settings = Settings()
