from __future__ import annotations
import logging,os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
@dataclass(frozen=True)
class Settings:
    token:str|None; prefix:str; database_path:Path; test_guild_id:int; owner_id:int|None; log_level:str
    @classmethod
    def from_env(cls)->"Settings":
        raw=os.getenv("TEST_GUILD_ID","0")
        try:gid=int(raw or 0)
        except ValueError:raise ValueError("TEST_GUILD_ID must be an integer") from None
        owner_raw=os.getenv("OWNER_ID","").strip()
        try:owner=int(owner_raw) if owner_raw else None
        except ValueError:raise ValueError("OWNER_ID must be an integer Discord user ID") from None
        return cls(os.getenv("DISCORD_TOKEN"),os.getenv("PREFIX","!"),Path(os.getenv("DATABASE_PATH","manager.sqlite3")),gid,owner,os.getenv("LOG_LEVEL","INFO").upper())
def configure_logging(level:str)->None:logging.basicConfig(level=getattr(logging,level,logging.INFO),format="%(asctime)s %(levelname)s %(name)s: %(message)s")
