"""What a failed run records: the step it was in and what went wrong, without secrets."""

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import openai

from pothole.runrecord import CheckResult, RepairRecord

SECRET_VARS = ("AZURE_OPENAI_API_KEY", "SOCRATA_APP_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")
MAX_MESSAGE = 300


@dataclass
class Progress:
    """Where the run has got to, so a failure can say so and keep what was already known."""

    started: datetime
    step: str = "start"
    rows: int = 0
    pages: int = 0
    generation: str | None = None
    checks: list[CheckResult] = field(default_factory=list)
    repair: RepairRecord | None = None
    analyst_day: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def describe(exc: BaseException, step: str) -> str:
    """`step: Class: message`, the message cut short and with secret values removed. A model
    service error carries the request and response, so only its class is kept."""
    text = "" if isinstance(exc, openai.OpenAIError) else str(exc)
    for name in SECRET_VARS:
        secret = os.environ.get(name)
        if secret and len(secret) >= 4:
            text = text.replace(secret, "***")
    return f"{step}: {type(exc).__name__}: {text[:MAX_MESSAGE]}".rstrip(": ")
