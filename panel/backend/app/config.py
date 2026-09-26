from functools import lru_cache
from pathlib import Path, PurePath

from pydantic_settings import BaseSettings, SettingsConfigDict


def default_scripts_dir(module_file: PurePath) -> PurePath:
    """client/server_scripts of a repository checkout, or /app/server_scripts next to the app in the image."""
    parents = module_file.parents
    if len(parents) > 4 and parents[1].name == "backend" and parents[2].name == "panel":
        return parents[3] / "client" / "server_scripts"
    return parents[1] / "server_scripts"


REPO_SCRIPTS_DIR = Path(default_scripts_dir(Path(__file__).resolve()))


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
