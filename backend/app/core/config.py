from functools import lru_cache

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class APISettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=list)


class DatabaseSettings(BaseModel):
    url: str = "sqlite:///./winston.db"


class FeatureSettings(BaseModel):
    memory_enabled: bool = False
    voice_enabled: bool = False
    tools_enabled: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_name: str = "Winston"
    app_description: str = "Self-hosted personal AI assistant"
    app_version: str = "0.1.0"
    environment: str = "development"
    log_level: str = "INFO"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_timeout_seconds: float = Field(default=120, gt=0)

    api: APISettings = Field(default_factory=APISettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    features: FeatureSettings = Field(default_factory=FeatureSettings)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
