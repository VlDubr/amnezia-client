import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config(url: str) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


async def upgrade_head(url: str) -> None:
    await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
