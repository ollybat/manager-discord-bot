from __future__ import annotations
import logging,os,sys
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
@dataclass(frozen=True)
class Settings:
    token:str|None; prefix:str; database_path:Path; owner_id:int|None; log_level:str
    @classmethod
    def from_env(cls)->"Settings":
        owner_raw=os.getenv("OWNER_ID","").strip()
        try: owner=int(owner_raw) if owner_raw else None
        except ValueError: raise ValueError("OWNER_ID must be an integer Discord user ID") from None
        token = os.getenv("DISCORD_TOKEN")
        prefix = os.getenv("PREFIX", "!")
        if len(prefix) > 8 or "\n" in prefix or "\r" in prefix:
            raise ValueError("PREFIX must be at most 8 characters and contain no newlines")
        database_path = Path(os.getenv("DATABASE_PATH", "manager.sqlite3"))
        if database_path.is_dir():
            raise ValueError("DATABASE_PATH must point to a SQLite file")
        level = os.getenv("LOG_LEVEL", "INFO").upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        return cls(token, prefix, database_path, owner, level)
def validate_runtime(settings: Settings) -> list[str]:
    problems: list[str] = []
    if not settings.token: problems.append("DISCORD_TOKEN is missing")
    elif settings.token.lower() in {"put_your_bot_token_here", "your_token_here"}: problems.append("DISCORD_TOKEN still contains the example placeholder")
    if settings.owner_id is None: problems.append("OWNER_ID is not configured; owner diagnostics and sync are disabled")
    return problems

def configure_logging(level:str)->None:
    numeric=getattr(logging,level.upper(),logging.INFO)
    logging.basicConfig(level=numeric,format="%(asctime)s %(levelname)s %(name)s: %(message)s",stream=sys.stdout,force=True)
    logging.getLogger("discord.client").setLevel(logging.INFO)
    logging.getLogger("discord.gateway").setLevel(logging.INFO)
