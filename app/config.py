from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_base_url: str
    database_url: str
    telegram_bot_token: str
    telegram_api_base_url: str = "http://telegram-bot-api:8081"
    telegram_webhook_secret: str
    # Mount the local Bot API server's files directory at the same path in the worker.
    # Local Bot API returns an absolute file_path when this is configured.
    telegram_local_files: bool = True
    s3_endpoint_url: str | None = None
    s3_region: str = "auto"
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str
    download_link_ttl_hours: int = 168
    max_batch_files: int = 100
    worker_poll_seconds: int = 3

    @property
    def telegram_bot_url(self) -> str:
        return f"{self.telegram_api_base_url.rstrip('/')}/bot{self.telegram_bot_token}"

    @property
    def telegram_file_url(self) -> str:
        return f"{self.telegram_api_base_url.rstrip('/')}/file/bot{self.telegram_bot_token}"


@lru_cache
def settings() -> Settings:
    return Settings()
