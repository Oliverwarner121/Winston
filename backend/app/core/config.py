from functools import lru_cache
import ipaddress
from pathlib import Path
import re

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class APISettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=list)


class DatabaseSettings(BaseModel):
    url: str = "sqlite:///./winston.db"


class FeatureSettings(BaseModel):
    memory_enabled: bool = False
    voice_enabled: bool = False
    tools_enabled: bool = False


class ToolSettings(BaseModel):
    projects_config_path: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[2]
        / "config"
        / "projects.yaml"
    )
    project_roots: list[Path] = Field(default_factory=list, max_length=20)
    workspace_roots: list[Path] = Field(
        max_length=50,
        default_factory=lambda: [Path(__file__).resolve().parents[3]]
    )
    max_file_size_bytes: int = Field(default=1_000_000, ge=1, le=10_000_000)
    max_search_results: int = Field(default=100, ge=1, le=1000)
    max_search_files_scanned: int = Field(default=50_000, ge=100, le=1_000_000)
    terminal_allowed_programs: list[str] = Field(
        default_factory=lambda: ["pytest"],
        max_length=20,
    )
    terminal_timeout_seconds: float = Field(default=60, gt=0, le=300)
    terminal_max_output_chars: int = Field(default=12_000, ge=100, le=100_000)
    git_allowed_remotes: list[str] = Field(default_factory=list, max_length=20)
    web_search_enabled: bool = False
    browser_allowed_hosts: list[str] = Field(default_factory=list, max_length=100)
    browser_timeout_seconds: float = Field(default=20, gt=0, le=60)
    browser_max_text_chars: int = Field(default=15_000, ge=100, le=100_000)
    docker_allowed_containers: list[str] = Field(default_factory=list, max_length=100)
    docker_timeout_seconds: float = Field(default=15, gt=0, le=60)
    docker_logs_tail_lines: int = Field(default=500, ge=1, le=5000)
    agent_max_steps: int = Field(default=3, ge=1, le=5)
    confirmation_ttl_seconds: int = Field(default=300, ge=10, le=3600)

    @field_validator("terminal_allowed_programs")
    @classmethod
    def validate_terminal_programs(cls, programs: list[str]) -> list[str]:
        for program in programs:
            if (
                not program
                or Path(program).name != program
                or not re.fullmatch(r"[A-Za-z0-9_.-]+", program)
            ):
                raise ValueError("Terminal allowlist entries must be executable names.")
        return list(dict.fromkeys(programs))

    @field_validator("git_allowed_remotes")
    @classmethod
    def validate_git_remotes(cls, remotes: list[str]) -> list[str]:
        for remote in remotes:
            if (
                not remote
                or remote.startswith("-")
                or ".." in remote
                or not re.fullmatch(r"[A-Za-z0-9_./-]+", remote)
            ):
                raise ValueError("Git remote allowlist entries are invalid.")
        return list(dict.fromkeys(remotes))

    @field_validator("browser_allowed_hosts")
    @classmethod
    def validate_browser_hosts(cls, hosts: list[str]) -> list[str]:
        normalized: list[str] = []
        for host in hosts:
            value = host.strip().rstrip(".").casefold()
            if (
                not value
                or value == "localhost"
                or value.endswith(".localhost")
                or "*" in value
                or "/" in value
                or ":" in value
                or any(
                    not label
                    or len(label) > 63
                    or label.startswith("-")
                    or label.endswith("-")
                    or not re.fullmatch(r"[a-z0-9-]+", label)
                    for label in value.split(".")
                )
            ):
                raise ValueError("Browser allowlist entries must be exact public hostnames.")
            try:
                address = ipaddress.ip_address(value)
            except ValueError:
                address = None
            if address is not None and not address.is_global:
                raise ValueError("Browser allowlist cannot include private addresses.")
            normalized.append(value)
        return list(dict.fromkeys(normalized))

    @field_validator("docker_allowed_containers")
    @classmethod
    def validate_docker_containers(cls, containers: list[str]) -> list[str]:
        for container in containers:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container):
                raise ValueError("Docker container allowlist entries are invalid.")
        return list(dict.fromkeys(containers))


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
    tools: ToolSettings = Field(default_factory=ToolSettings)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
