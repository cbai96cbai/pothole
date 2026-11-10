from decimal import Decimal
from typing import Any

import httpx
import openai
import pytest

from pothole.budget.limits import CallLimits, call_max_cost
from pothole.config import Settings
from pothole.llm.client import CallFailed, InputTooLarge, LLMClient

REQ = httpx.Request("POST", "https://x.openai.azure.com/chat")
SETTINGS = Settings(azure_endpoint="https://x.openai.azure.com", azure_api_key="k")
MSGS = [{"role": "user", "content": "hi"}]
TOOLS = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}]


def ok(
    content: str | None = "hello",
    finish: str = "stop",
    pt: int = 100,
    ct: int = 50,
    tool_calls: Any = None,
) -> Any:
    from openai.types.chat import ChatCompletion

    return ChatCompletion.model_validate(
        {
            "id": "c1",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish,
                    "message": {"role": "assistant", "content": content, "tool_calls": tool_calls},
                }
            ],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct},
        }
    )


def status_error(code: int, headers: dict[str, str] | None = None) -> openai.APIStatusError:
    resp = httpx.Response(code, request=REQ, headers=headers)
    cls = openai.RateLimitError if code == 429 else openai.InternalServerError
    return cls("boom", response=resp, body=None)


class FakeSDK:
    """Plays a script of responses or exceptions, one per call."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def client(script: list[Any], sleeps: list[float] | None = None) -> tuple[LLMClient, FakeSDK]:
    sdk = FakeSDK(script)
    rec = sleeps if sleeps is not None else []
    return LLMClient(SETTINGS, sdk=sdk, sleep=rec.append), sdk


def test_charges_returned_usage_and_sends_the_agreed_parameters() -> None:
    c, sdk = client([ok(pt=1000, ct=100)])
    r = c.call(MSGS)
    assert r.cost == r.usage.cost == Decimal("0.00045")
    assert not r.usage.charged_max
    assert (r.usage.input_tokens, r.usage.output_tokens) == (1000, 100)
    assert r.message["content"] == "hello" and r.finish_reason == "stop"
    kw = sdk.calls[0]
    assert kw["model"] == "pothole-agent"
    assert kw["max_completion_tokens"] == 2500 and kw["reasoning_effort"] == "low"
    assert "temperature" not in kw and "store" not in kw and "tools" not in kw


def test_deployment_follows_settings() -> None:
    sdk = FakeSDK([ok()])
    LLMClient(Settings(deployment="mine"), sdk=sdk).call(MSGS)
    assert sdk.calls[0]["model"] == "mine"


def test_429_retried_three_times_charged_zero() -> None:
    # 429, 429, 200: three attempts, only the usage is charged, waits 2s then 5s
    sleeps: list[float] = []
    c, sdk = client([status_error(429), status_error(429), ok(pt=10, ct=10)], sleeps)
    r = c.call(MSGS)
    assert len(sdk.calls) == 3 and sleeps == [2, 5]
    assert r.cost == Decimal("0.0000225") and not r.usage.charged_max
    # 429 on all four attempts: three retries, failed, marked rate limited, charged 0
    sleeps = []
    c, sdk = client([status_error(429)] * 4, sleeps)
    with pytest.raises(CallFailed) as e:
        c.call(MSGS)
    assert len(sdk.calls) == 4 and sleeps == [2, 5, 15]
    assert e.value.usage.cost == 0 and not e.value.usage.charged_max and e.value.rate_limited


def test_timeout_charged_max_not_retried() -> None:
    maxc = call_max_cost(CallLimits())
    assert maxc == Decimal("0.008")
    for exc in (
        openai.APITimeoutError(request=REQ),
        openai.APIConnectionError(request=REQ),
        status_error(500),
    ):
        c, sdk = client([exc, ok()])
        with pytest.raises(CallFailed) as e:
            c.call(MSGS)
        assert len(sdk.calls) == 1
        assert e.value.usage.charged_max and e.value.usage.cost == maxc
        assert not e.value.rate_limited


def test_retry_after_headers_are_honored_and_capped() -> None:
    sleeps: list[float] = []
    script = [
        status_error(429, {"retry-after-ms": "1500"}),
        status_error(429, {"retry-after": "600"}),
        status_error(429, {"retry-after": "soon"}),  # unreadable: the default backoff
        ok(),
    ]
    c, _ = client(script, sleeps)
    c.call(MSGS)
    assert sleeps == [1.5, 60, 15]


def test_other_client_error_is_failed_free_and_not_rate_limited() -> None:
    err = openai.BadRequestError("bad", response=httpx.Response(400, request=REQ), body=None)
    c, sdk = client([err])
    with pytest.raises(CallFailed) as e:
        c.call(MSGS)
    assert len(sdk.calls) == 1 and e.value.usage.cost == 0 and not e.value.rate_limited


def test_missing_usage_on_success_is_charged_max() -> None:
    resp = ok()
    resp.usage = None
    c, _ = client([resp])
    r = c.call(MSGS)
    assert r.usage.charged_max and r.cost == Decimal("0.008")


def test_input_too_large_raises_before_send() -> None:
    c, sdk = client([ok()])
    with pytest.raises(InputTooLarge):
        c.call(MSGS, limits=CallLimits(100, 2500, "low"))
    assert sdk.calls == []


def test_finish_reason_exposed_and_no_effort_means_none_sent() -> None:
    c, sdk = client([ok(finish="length")])
    r = c.call(MSGS, limits=CallLimits(16000, 16, None))
    assert r.finish_reason == "length" and "reasoning_effort" not in sdk.calls[0]


def test_empty_choices_is_charged_and_signalled() -> None:
    resp = ok(pt=500, ct=0)
    resp.choices = []
    c, _ = client([resp])
    r = c.call(MSGS)
    assert r.finish_reason == "no_choices" and r.message == {}
    assert r.usage.input_tokens == 500 and r.cost == Decimal("0.000125")


def test_tools_passed_through_and_tool_calls_in_message() -> None:
    resp = ok(
        content=None,
        finish="tool_calls",
        tool_calls=[{"id": "t1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
    )
    c, sdk = client([resp])
    r = c.call(MSGS, TOOLS)
    assert sdk.calls[0]["tools"] == TOOLS
    assert r.message["tool_calls"][0]["function"]["name"] == "f"
    assert r.finish_reason == "tool_calls"
