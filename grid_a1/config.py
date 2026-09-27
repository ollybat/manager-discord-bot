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
        return cls(os.getenv("DISCORD_TOKEN"),os.getenv("PREFIX","!"),Path(os.getenv("DATABASE_PATH","manager.sqlite3")),owner,os.getenv("LOG_LEVEL","INFO").upper())
def configure_logging(level:str)->None:
    numeric=getattr(logging,level.upper(),logging.INFO)
    logging.basicConfig(level=numeric,format="%(asctime)s %(levelname)s %(name)s: %(message)s",stream=sys.stdout,force=True)
    logging.getLogger("discord.client").setLevel(logging.INFO)
    logging.getLogger("discord.gateway").setLevel(logging.INFO)
