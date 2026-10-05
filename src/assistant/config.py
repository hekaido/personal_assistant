from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class LLMTaskConfig(BaseModel):
    provider: str
    model: str


class LLMConfig(BaseModel):
    summarize: LLMTaskConfig
    extraction: LLMTaskConfig
    chat: LLMTaskConfig
    brief: LLMTaskConfig
    embeddings: LLMTaskConfig


class PathsConfig(BaseModel):
    db_path: str
    telegram_session_path: str


class TelegramConfig(BaseModel):
    sync_interval_minutes: int
    initial_history_months: int


class GmailConfig(BaseModel):
    sync_interval_minutes: int


class BriefConfig(BaseModel):
    send_hour: int
    send_minute: int


class DedupConfig(BaseModel):
    similarity_threshold: float


class AppConfig(BaseModel):
    llm: LLMConfig
    paths: PathsConfig
    telegram: TelegramConfig
    gmail: GmailConfig
    brief: BriefConfig
    dedup: DedupConfig

    @property
    def db_path(self) -> Path:
        return (PROJECT_ROOT / self.paths.db_path).resolve()

    @property
    def telegram_session_path(self) -> Path:
        return (PROJECT_ROOT / self.paths.telegram_session_path).resolve()


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_api_id: int
    telegram_api_hash: str
    telegram_bot_token: str = ""
    routerai_api_key: str = ""
    anthropic_api_key: str = ""
    gmail_client_secret_path: str = "./data/gmail_client_secret.json"


def load_config(config_path: Path | None = None) -> AppConfig:
    path = config_path or (PROJECT_ROOT / "config.yaml")
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return AppConfig.model_validate(raw)


def load_secrets() -> Secrets:
    return Secrets()
