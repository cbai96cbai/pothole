"""Azure OpenAI Chat Completions client that enforces limits and the charging rule.

A call that returns usage is charged that usage. A call that was sent but returned no usage
(timeout, dropped connection, any 5xx) is charged its maximum and not retried. HTTP 429 is
rejected before processing: retried up to 3 times (4 attempts in all, waiting what the service's
Retry-After header says, else 2s, 5s, 15s; no wait over 60s) and charged nothing. A call that was
rate limited on every attempt raises CallFailed with rate_limited set.

Contract: once a response with usage arrives, call() always returns a CallResult carrying that
charge; it never raises afterwards. Callers tell outcomes apart by finish_reason ("stop",
"tool_calls", "length", "content_filter", or "no_choices" for an empty choices list).
"""

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import openai

from pothole.budget.limits import DEFAULT_LIMITS, CallLimits, call_cost, call_max_cost
from pothole.config import Settings
from pothole.llm.tokens import count_input_tokens

API_VERSION = "2025-04-01-preview"
TIMEOUT_S = 300
MAX_ATTEMPTS = 4
BACKOFF_S = (2.0, 5.0, 15.0)
MAX_WAIT_S = 60.0
# fields the SDK adds to its message objects that the API does not accept back
_DROP_MESSAGE = ("annotations", "audio")


class InputTooLarge(Exception):
    """The conservative input count exceeds the call's limit; nothing was sent."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cost: Decimal
    charged_max: bool = False


class CallFailed(Exception):
    def __init__(self, message: str, usage: Usage, *, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.usage = usage
        self.rate_limited = rate_limited  # refused before processing on every attempt


def _retry_wait(exc: openai.RateLimitError, attempt: int) -> float:
    """Seconds to wait before the next try: the service's own figure when readable, capped."""
    headers = exc.response.headers
    for name, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
        try:
            return min(max(float(headers[name]) * scale, 0.0), MAX_WAIT_S)
        except (KeyError, ValueError):
            continue
    return min(BACKOFF_S[attempt], MAX_WAIT_S)


@dataclass(frozen=True)
class CallResult:
    message: dict[str, Any]
    usage: Usage
    finish_reason: str
    latency_s: float

    @property
    def cost(self) -> Decimal:
        return self.usage.cost


def _plain_message(message: Any) -> dict[str, Any]:
    out: dict[str, Any] = message.model_dump(exclude_none=True)
    for key in _DROP_MESSAGE:
        out.pop(key, None)
    for call in out.get("tool_calls") or []:
        call.get("function", {}).pop("parsed_arguments", None)
    return out


class LLMClient:
    def __init__(
        self,
        settings: Settings,
        *,
        sdk: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self._sleep = sleep
        self._sdk = sdk or openai.AzureOpenAI(
            azure_endpoint=settings.azure_endpoint,
            api_key=settings.azure_api_key,
            api_version=API_VERSION,
            max_retries=0,
            timeout=TIMEOUT_S,
        )

    def call(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] | None = None,
        limits: CallLimits = DEFAULT_LIMITS,
    ) -> CallResult:
        counted = count_input_tokens(messages, tools)
        if counted > limits.input_tokens:
            raise InputTooLarge(f"{counted} input tokens exceed the {limits.input_tokens} limit")
        max_cost = call_max_cost(limits)
        kwargs: dict[str, Any] = {
            "model": self.settings.deployment,
            "messages": list(messages),
            "max_completion_tokens": limits.completion_tokens,
        }
        if limits.reasoning_effort:
            kwargs["reasoning_effort"] = limits.reasoning_effort
        if tools:
            kwargs["tools"] = list(tools)
        started = time.monotonic()
        completion = self._send(kwargs, max_cost)
        latency = time.monotonic() - started
        if completion.usage is None:
            usage = Usage(0, 0, max_cost, charged_max=True)
        else:
            u = completion.usage
            usage = Usage(
                u.prompt_tokens,
                u.completion_tokens,
                call_cost(u.prompt_tokens, u.completion_tokens),
            )
        # from here on the charge is fixed: nothing below may raise
        if not completion.choices:
            return CallResult({}, usage, "no_choices", latency)
        choice = completion.choices[0]
        return CallResult(_plain_message(choice.message), usage, str(choice.finish_reason), latency)

    def _send(self, kwargs: dict[str, Any], max_cost: Decimal) -> Any:
        endpoint = self._sdk.chat.completions.create
        for attempt in range(MAX_ATTEMPTS):
            try:
                return endpoint(**kwargs)
            except openai.RateLimitError as exc:
                if attempt + 1 == MAX_ATTEMPTS:
                    raise CallFailed(
                        "rate limited on every attempt",
                        Usage(0, 0, Decimal(0)),
                        rate_limited=True,
                    ) from exc
                self._sleep(_retry_wait(exc, attempt))
            except openai.APIConnectionError as exc:  # includes timeouts
                raise CallFailed(
                    f"no response ({type(exc).__name__}); charged the maximum",
                    Usage(0, 0, max_cost, charged_max=True),
                ) from exc
            except openai.APIStatusError as exc:
                if exc.status_code >= 500:
                    raise CallFailed(
                        f"server error {exc.status_code}; charged the maximum",
                        Usage(0, 0, max_cost, charged_max=True),
                    ) from exc
                raise CallFailed(
                    f"request rejected with {exc.status_code}", Usage(0, 0, Decimal(0))
                ) from exc
        raise AssertionError("unreachable")
