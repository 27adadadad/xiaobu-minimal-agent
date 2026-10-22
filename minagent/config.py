import os
from dataclasses import dataclass, field
from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    timeout: float = 45
    db: str = "data/agent.sqlite3"

    embedding_model: str = ""
    embedding_url: str = "https://api.openai.com/v1"
    embedding_key: str = field(default="",repr=False)

    @classmethod
    def from_env(cls):
        load_dotenv(override=False)
        return cls(os.getenv("LLM_API_KEY", ""), os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
                   os.getenv("LLM_MODEL", ""), float(os.getenv("LLM_TIMEOUT", "45")), os.getenv("AGENT_DB", "data/agent.sqlite3"), os.getenv("EMBEDDING_MODEL", ""), os.getenv("EMBEDDING_BASE_URL",os.getenv("LLM_BASE_URL","https://api.openai.com/v1")), os.getenv("EMBEDDING_API_KEY",os.getenv("LLM_API_KEY","")))

    @property
    def configured(self):
        return bool(self.api_key and self.model)
