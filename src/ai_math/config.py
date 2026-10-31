from __future__ import annotations

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = ""
    admin_ids: str = ""
    database_url: str = "sqlite+aiosqlite:///./ai_math.db"
    redis_url: str = ""
    port: int = 8000
    public_base_url: str = ""
    webhook_secret: str = ""
    use_webhook: bool = True
    log_level: str = "INFO"
    min_calibration_participants: int = 30

    @property
    def admin_id_set(self) -> set[int]:
        result: set[int] = set()
        for token in self.admin_ids.split(","):
            token = token.strip()
            if token.isdigit():
                result.add(int(token))
        return result

    @property
    def normalized_database_url(self) -> str:
        url = self.database_url.strip()
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
