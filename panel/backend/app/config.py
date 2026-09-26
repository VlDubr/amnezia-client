from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "client" / "server_scripts"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PANEL_", env_file=".env", extra="ignore")

    database_url: str
    master_key: str
    tz: str = "UTC"
    cookie_secure: bool = True
    scheduler_enabled: bool = True
    scripts_dir: Path = REPO_SCRIPTS_DIR
    dns1: str = "1.1.1.1"
    dns2: str = "1.0.0.1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
