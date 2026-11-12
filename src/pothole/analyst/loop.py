"""The investigator's tool loop: at most `max_calls` model calls, ending in a summary or not.

Each tool attempt gets the next ref number, and its result is shown to the model starting
`ref <n>: `. The model cites those numbers as evidence; only successful calls from earlier replies
are citable (see `refs`).
"""

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from pothole.analyst.detect import Candidate
from pothole.analyst.excerpt import excerpt
from pothole.analyst.finding import Evidence
from pothole.analyst.numbers import one_decimal
from pothole.analyst.prompt import (
    CALL_BUDGET,
    CALLS_LEFT,
    LAST_CALL,
    NUDGE_LENGTH,
    NUDGE_NO_TOOL,
    SUBMIT_NEXT,
    SYSTEM_PROMPT,
    TOO_FEW_CALLS,
    TOOLS,
)
from pothole.analyst.refs import cited, prefixed
from pothole.analyst.tools import Toolbox
from pothole.analyst.wording import causal_phrase
from pothole.budget.limits import CallLimits
from pothole.llm.client import CallFailed, CallResult, InputTooLarge, LLMClient
from pothole.llm.tokens import count_input_tokens
from pothole.repair.tools import cap
from pothole.repair.validate import ToolError
from pothole.toolargs import check_arguments

FORCE_WHEN_LEFT = 2
FORCE: dict[str, Any] = {"type": "function", "function": {"name": "submit_finding"}}
MAX_SUMMARY = 600
EXCERPT = 300
TRIMMED = 240


def _say(exc: Exception) -> str:
    return f"bad arguments: {exc}" if isinstance(exc, json.JSONDecodeError) else str(exc)


def _brief(c: Candidate) -> str:
    return (
        f"Date {c.day.isoformat()} ({c.day.strftime('%A')}). {c.complaint_type} in "
        f"{c.borough} community district {c.board}: {c.observed} requests, against about "
        f"{one_decimal(c.expected)} usual for that weekday ({one_decimal(c.ratio)} times)."
    )


class Run:
    def __init__(
        self, c: Candidate, box: Toolbox, client: LLMClient, limits: CallLimits, max_calls: int,
        stop: Callable[[], bool] | None = None,
    ) -> None:  # fmt: skip
        self.c, self.box, self.client, self.limits, self.max_calls = (
            c,
            box,
            client,
            limits,
            max_calls,
        )
        self.stop = stop
        self.calls = 0
        self.cost = Decimal(0)
        self.trace: list[dict[str, Any]] = []
        self.summary: str | None = None
        self.evidence: list[Evidence] = []
        self.reason = f"no valid submission in {max_calls} calls"
        self.next_ref = 1  # one number per tool attempt, shown at the start of its result
        self.seen = 0  # the highest ref whose result an earlier reply has shown the model
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{_brief(c)} {self._budget_note()}"},
        ]

    def _budget_note(self) -> str:
        note = CALL_BUDGET.format(n=self.max_calls)
        return note + (
            TOO_FEW_CALLS.format(n=self.max_calls) if self.max_calls <= FORCE_WHEN_LEFT else ""
        )

    def _fit(self, force: Any) -> bool:
        for m in self.messages:
            if count_input_tokens(self.messages, TOOLS, force) <= self.limits.input_tokens:
                return True
            if m.get("role") == "tool" and len(m["content"]) > TRIMMED:
                m["content"] = m["content"][:TRIMMED] + "…[trimmed]"
        return count_input_tokens(self.messages, TOOLS, force) <= self.limits.input_tokens

    def _left(self) -> str:
        n = self.max_calls - self.calls
        if n <= 1:
            return LAST_CALL
        return SUBMIT_NEXT if n == FORCE_WHEN_LEFT else CALLS_LEFT.format(n=n)

    def run(self) -> None:
        while self.calls < self.max_calls and self.summary is None:
            if self.stop is not None and self.stop():  # checked before every paid call
                self.reason = "a source failed: no further model calls"
                return
            force = FORCE if self.max_calls - self.calls <= FORCE_WHEN_LEFT else None
            if not self._fit(force):
                self.reason = "the conversation no longer fits the input limit"
                return
            sent = [dict(m) for m in self.messages]
            try:
                res = self.client.call(self.messages, TOOLS, self.limits, force)
            except InputTooLarge as exc:
                self.reason = f"call not made: {exc}"
                return
            except CallFailed as exc:
                self.cost += exc.usage.cost
                self.trace.append(
                    {"call": self.calls + 1, "error": str(exc), "cost": str(exc.usage.cost)}
                )
                self.reason = f"the model call failed: {exc}"
                return
            self.calls += 1
            self.cost += res.cost
            self._handle(res, self._record(sent, res))

    def _record(self, sent: list[dict[str, Any]], res: CallResult) -> dict[str, Any]:
        step: dict[str, Any] = {
            "call": self.calls,
            "messages": [
                {**m, "content": cap(m["content"])} if m.get("role") == "tool" else m for m in sent
            ],
            "reply": res.message,
            "finish_reason": res.finish_reason,
            "usage": {"input": res.usage.input_tokens, "output": res.usage.output_tokens},
            "cost": str(res.cost),
            "tool_results": [],
        }
        self.trace.append(step)
        return step

    def _handle(self, res: CallResult, step: dict[str, Any]) -> None:
        if res.finish_reason in ("content_filter", "no_choices"):
            self.reason = f"the model returned {res.finish_reason}"
            self.calls = self.max_calls
            return
        calls = res.message.get("tool_calls") or []
        if res.finish_reason == "length" or not calls:
            if res.finish_reason != "length":
                self.messages.append(
                    {"role": "assistant", "content": res.message.get("content") or ""}
                )
            nudge = NUDGE_LENGTH if res.finish_reason == "length" else NUDGE_NO_TOOL
            self.messages.append({"role": "user", "content": f"{nudge} {self._left()}"})
            return
        self.messages.append(res.message)
        self.seen = self.next_ref - 1  # results this reply produces are read only after it
        for call in calls:
            name = str(call.get("function", {}).get("name"))
            if self.summary is not None or (
                name == "submit_finding"
                and any(t["name"] == "submit_finding" for t in step["tool_results"])
            ):
                content = "ignored: one submission per reply"
            else:
                content = self._run_tool(name, call)
            self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
            step["tool_results"].append({"name": name, "content": cap(content)})
        if self.summary is None:
            self.messages[-1]["content"] += "\n" + self._left()

    def _run_tool(self, name: str, call: dict[str, Any]) -> str:
        if name == "submit_finding":
            try:
                self._submit(self._arguments(call))
                return "accepted"
            except (json.JSONDecodeError, ToolError) as exc:
                return f"error: {_say(exc)}"
        ref = self.next_ref
        self.next_ref += 1
        try:
            return prefixed(ref, self.box.run(name, self._arguments(call), ref))
        except (json.JSONDecodeError, ToolError) as exc:
            return prefixed(ref, f"error: {_say(exc)}")

    @staticmethod
    def _arguments(call: dict[str, Any]) -> dict[str, Any]:
        args = json.loads(call["function"].get("arguments") or "{}")
        if not isinstance(args, dict):
            raise ToolError("arguments must be a JSON object")
        name = str(call["function"].get("name"))
        # evidence is checked with the refs, so its refusal can list what is citable
        check_arguments(TOOLS, name, {k: v for k, v in args.items() if k != "evidence"})
        return args

    def _submit(self, args: dict[str, Any]) -> None:
        summary, refs = args.get("summary"), args.get("evidence")
        if not isinstance(summary, str) or not summary.strip():
            raise ToolError("summary is required")
        if len(summary) > MAX_SUMMARY:
            raise ToolError(f"summary is {len(summary)} characters; the limit is {MAX_SUMMARY}")
        hit = causal_phrase(summary)
        if hit:
            raise ToolError(f"the summary says {hit!r}; say what the spike is associated with")
        if args.get("wording_check") is not True:
            raise ToolError("wording_check must be true once you have re-read the summary")
        numbers = cited(refs, self.box.calls, self.seen)
        self.evidence = [
            Evidence(
                n,
                self.box.calls[n].name,
                self.box.calls[n].args,
                excerpt(self.box.calls[n].result, EXCERPT),
            )
            for n in sorted(numbers)
        ]
        self.summary = summary.strip()
