"""Settings read from the environment. Nothing loads a .env file."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    azure_endpoint: str = ""
    azure_api_key: str = ""
    deployment: str = "pothole-agent"
    socrata_app_token: str | None = None
    state: Path = Path("~/.cache/pothole")

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            azure_endpoint=env.get("AZURE_OPENAI_ENDPOINT", ""),
            azure_api_key=env.get("AZURE_OPENAI_API_KEY", ""),
            deployment=env.get("POTHOLE_DEPLOYMENT") or "pothole-agent",
            socrata_app_token=env.get("SOCRATA_APP_TOKEN") or None,
            state=Path(env.get("POTHOLE_STATE") or "~/.cache/pothole").expanduser(),
        )
