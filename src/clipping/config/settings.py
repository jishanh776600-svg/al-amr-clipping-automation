"""Central Configuration Module for Clipping Automation."""

from typing import Literal, Optional
from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Unified application configuration loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # 1. APP SETTINGS
    ENVIRONMENT: Literal["development", "staging", "production", "test"] = "development"
    PROJECT_NAME: str = "Clipping Automation"
    PRODUCT_NAME: str = "AL AMR Clipping Automation"
    API_PORT: int = 8000
    API_HOST: str = "0.0.0.0"
    OPERATOR_TOKEN: Optional[SecretStr] = None  # Master Control secret token for mutating endpoints
    GITHUB_PAT: Optional[SecretStr] = None       # GitHub PAT for workflow dispatch
    GITHUB_REPO: Optional[str] = None            # e.g. "org/clipping-automation"

    # 2. STORAGE SETTINGS
    STORAGE_DRIVER: Literal["local", "gdrive", "s3"] = "local"
    LOCAL_STORAGE_ROOT: str = "./project_vault"
    STORAGE_BUCKET_NAME: Optional[str] = None

    # 3. DATABASE SETTINGS
    DATABASE_URL: str = "sqlite+aiosqlite:///./project_vault/clipping.db"
    DATABASE_ECHO: bool = False

    # 4. TELEGRAM SETTINGS
    TELEGRAM_BOT_TOKEN: Optional[SecretStr] = None
    TELEGRAM_CHAT_ID: Optional[int] = None
    TELEGRAM_ALLOWED_USER_IDS: Optional[str] = None  # Comma-separated user IDs e.g. "12345678,87654321"
    TELEGRAM_ALLOWED_CHAT_IDS: Optional[str] = None  # Comma-separated chat IDs
    TELEGRAM_WEBHOOK_URL: Optional[str] = None
    TELEGRAM_SECRET_TOKEN: Optional[SecretStr] = None

    def get_allowed_telegram_user_ids(self) -> set[int]:
        ids: set[int] = set()
        if self.TELEGRAM_ALLOWED_USER_IDS:
            for item in self.TELEGRAM_ALLOWED_USER_IDS.split(","):
                item_str = item.strip()
                if item_str:
                    try:
                        ids.add(int(item_str))
                    except ValueError:
                        pass
        return ids

    def get_allowed_telegram_chat_ids(self) -> set[int]:
        ids: set[int] = set()
        if self.TELEGRAM_CHAT_ID is not None:
            ids.add(self.TELEGRAM_CHAT_ID)
        if self.TELEGRAM_ALLOWED_CHAT_IDS:
            for item in self.TELEGRAM_ALLOWED_CHAT_IDS.split(","):
                item_str = item.strip()
                if item_str:
                    try:
                        ids.add(int(item_str))
                    except ValueError:
                        pass
        return ids

    # 5. YOUTUBE SETTINGS
    YOUTUBE_CLIENT_ID: Optional[str] = None
    YOUTUBE_CLIENT_SECRET: Optional[SecretStr] = None
    YOUTUBE_REFRESH_TOKEN: Optional[SecretStr] = None
    YOUTUBE_CHANNEL_ID: Optional[str] = None
    YOUTUBE_DEFAULT_PRIVACY: Literal["private", "unlisted", "public"] = "private"
    YOUTUBE_CLIENT_SECRETS_FILE: Optional[str] = "./secrets/client_secrets.json"
    YOUTUBE_CREDENTIALS_STORAGE_DIR: str = "./secrets/youtube_tokens"
    YOUTUBE_DEFAULT_CHANNEL_ID: Optional[str] = None

    # 6. GOOGLE & GOOGLE DRIVE SETTINGS
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = None
    GOOGLE_SERVICE_ACCOUNT_JSON: Optional[SecretStr] = None
    GOOGLE_DRIVE_ROOT_FOLDER_ID: Optional[str] = None

    # 7. LOGGING SETTINGS
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "json"

    # 8. WORKER & INFERENCE SETTINGS
    WORKER_CONCURRENCY: int = Field(default=2, ge=1)
    INFERENCE_DEVICE: Literal["auto", "cpu", "cuda", "mps"] = "auto"
    LOCAL_LLM_BASE_URL: str = "http://localhost:11434/v1"
    LOCAL_LLM_MODEL: str = "qwen2.5:7b-instruct-q4_K_M"

    # 9. FEATURE FLAGS
    ENABLE_TALKNET_ASD: bool = False
    ENABLE_C2PA_PROVENANCE: bool = False
    ENABLE_AUTO_PUBLISH_ON_APPROVAL: bool = True

    @computed_field
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @computed_field
    @property
    def is_testing(self) -> bool:
        return self.ENVIRONMENT == "test"


_settings_instance: Optional[Settings] = None


def get_settings() -> Settings:
    """Singleton getter for application settings."""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance
