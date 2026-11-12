"""The repair agent: a tool loop of at most `max_calls` model calls that ends in a decision.

Whatever goes wrong (a refused call, a context that cannot fit, no valid submission), the outcome
is an issue: the safe default is to ask a person.
"""

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from pothole.budget.limits import DEFAULT_LIMITS, CallLimits
from pothole.llm.client import CallFailed, CallResult, InputTooLarge, LLMClient
from pothole.llm.tokens import count_input_tokens
from pothole.repair.outcome import RepairOutcome
from pothole.repair.prompt import (
    LAST_REPLY,
    NUDGE_LENGTH,
    NUDGE_NO_TOOL,
    REPLIES_LEFT,
    SUBMIT_NEXT,
    SYSTEM_PROMPT,
    TOOLS,
)
from pothole.repair.tools import Submission, Toolbox, cap, render_results
from pothole.repair.validate import ToolError
from pothole.spec.checks import CheckResult
from pothole.spec.model import PipelineSpec
from pothole.toolargs import check_arguments

FORCE_WHEN_LEFT = 2
FORCE: dict[str, Any] = {"type": "function", "function": {"name": "submit"}}
TRIMMED = 240


class _Run:
    def __init__(self, box: Toolbox, client: LLMClient, limits: CallLimits, max_calls: int) -> None:
        self.box, self.client, self.limits, self.max_calls = box, client, limits, max_calls
        self.calls = 0
        self.cost = Decimal(0)
        self.trace: list[dict[str, Any]] = []
        self.full: dict[str, str] = {}  # tool_call_id -> untrimmed content
        user = "Failed checks on today's batch:\n" + render_results(box.results)
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]

    def _issue(self, reason: str) -> RepairOutcome:
        """An issue the code files because the model gave no valid decision."""
        return RepairOutcome("issue", [], None, reason, self.trace, self.cost, decided_by="code")

    def _decided(self, sub: Submission) -> RepairOutcome:
        return RepairOutcome(sub.action, sub.ops, sub.regression, sub.reason, self.trace, self.cost)

    def _fit(self) -> bool:
        """Cut the oldest tool results down until the next call fits its input limit."""
        for m in self.messages:
            if count_input_tokens(self.messages, TOOLS, FORCE) <= self.limits.input_tokens:
                return True
            if m.get("role") == "tool" and len(m["content"]) > TRIMMED:
                m["content"] = m["content"][:TRIMMED] + "…[trimmed]"
        return count_input_tokens(self.messages, TOOLS, FORCE) <= self.limits.input_tokens

    def _left(self) -> str:
        n = self.max_calls - self.calls
        if n == 1:
            return LAST_REPLY
        return SUBMIT_NEXT if n == FORCE_WHEN_LEFT else REPLIES_LEFT.format(n=n)

    def run(self) -> RepairOutcome:
        while self.calls < self.max_calls:
            if not self._fit():
                return self._issue("the conversation no longer fits the input limit")
            forced = self.max_calls - self.calls <= FORCE_WHEN_LEFT
            sent = [dict(m) for m in self.messages]
            try:
                res = self.client.call(self.messages, TOOLS, self.limits, FORCE if forced else None)
            except InputTooLarge as exc:
                return self._issue(f"call not made: {exc}")
            except CallFailed as exc:
                self.cost += exc.usage.cost
                self.trace.append(
                    {"call": self.calls + 1, "error": str(exc), "cost": str(exc.usage.cost)}
                )
                return self._issue(f"the model call failed: {exc}")
            self.calls += 1
            self.cost += res.cost
            step = self._record(sent, res)
            done = self._handle(res, step)
            if done is not None:
                return done
        return self._issue(f"no valid decision was submitted in {self.max_calls} calls")

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

    def _handle(self, res: CallResult, step: dict[str, Any]) -> RepairOutcome | None:
        if res.finish_reason in ("content_filter", "no_choices"):
            return self._issue(f"the model returned {res.finish_reason}")
        calls = res.message.get("tool_calls") or []
        if res.finish_reason == "length" or not calls:
            if res.finish_reason != "length":
                self.messages.append(
                    {"role": "assistant", "content": res.message.get("content") or ""}
                )
            nudge = NUDGE_LENGTH if res.finish_reason == "length" else NUDGE_NO_TOOL
            self.messages.append({"role": "user", "content": f"{nudge} {self._left()}"})
            return None
        self.messages.append(res.message)
        final: RepairOutcome | None = None
        for call in calls:
            name = str(call.get("function", {}).get("name"))
            if final is not None or (
                name == "submit" and any(t["name"] == "submit" for t in step["tool_results"])
            ):
                content = "ignored: one submission per reply"
            else:
                content, final = self._run_tool(name, call)
            self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
            step["tool_results"].append({"name": name, "content": cap(content)})
        if final is None and self.messages[-1]["role"] == "tool":
            self.messages[-1]["content"] += "\n" + self._left()
        return final

    def _run_tool(self, name: str, call: dict[str, Any]) -> tuple[str, RepairOutcome | None]:
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
            if not isinstance(args, dict):
                raise ToolError("arguments must be a JSON object")
            check_arguments(TOOLS, name, args)
            if name == "submit":
                sub = self.box.submit(args)
                return "accepted", self._decided(sub)
            return self.box.run(name, args), None
        except json.JSONDecodeError as exc:
            return f"error: bad arguments: {exc}", None
        except ToolError as exc:
            return f"error: {exc}", None


def repair(
    spec: PipelineSpec,
    failing_batch_rows: list[dict[str, str]],
    check_results: list[CheckResult],
    source_fields: dict[str, str],
    client: LLMClient,
    limits: CallLimits = DEFAULT_LIMITS,
    max_calls: int = 6,
    *,
    now: datetime | None = None,
    history: list[int] | None = None,
) -> RepairOutcome:
    """Decide what to do about a failed batch."""
    box = Toolbox(
        spec,
        failing_batch_rows,
        check_results,
        source_fields,
        now,
        history or [],
    )
    return _Run(box, client, limits, max_calls).run()
