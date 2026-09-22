from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Settings:
    token: str | None
    prefix: str
    database_path: Path
    test_guild_id: int
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        raw = os.getenv("TEST_GUILD_ID", "0")
        try:
            test_guild_id = int(raw or 0)
        except ValueError:
            raise ValueError("TEST_GUILD_ID must be an integer") from None
        return cls(os.getenv("DISCORD_TOKEN"), os.getenv("PREFIX", "!"), Path(os.getenv("DATABASE_PATH", "manager.sqlite3")), test_guild_id, os.getenv("LOG_LEVEL", "INFO").upper())


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
