from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"


def load_dotenv(path: Path = BASE_DIR / ".env") -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    whatsapp_access_token: str
    whatsapp_phone_number_id: str
    whatsapp_verify_token: str
    openai_api_key: str
    openai_model: str
    brave_search_api_key: str
    agent_disclosure: bool
    human_handoff_number: str
    port: int
    catalog_path: Path
    database_path: Path


def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        whatsapp_access_token=os.getenv("WHATSAPP_ACCESS_TOKEN", ""),
        whatsapp_phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID", ""),
        whatsapp_verify_token=os.getenv("WHATSAPP_VERIFY_TOKEN", "change-me"),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        brave_search_api_key=os.getenv("BRAVE_SEARCH_API_KEY", ""),
        agent_disclosure=os.getenv("AGENT_DISCLOSURE", "on").lower() in {"1", "true", "yes", "on"},
        human_handoff_number=os.getenv("HUMAN_HANDOFF_NUMBER", ""),
        port=int(os.getenv("PORT", "8080")),
        catalog_path=DATA_DIR / "catalog.md",
        database_path=DATA_DIR / "agent.sqlite3",
    )

