"""Every run that starts leaves a record: an exception in any step ends in an error record."""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import openai
import pytest
from daily_kit import SUNDAY, TUESDAY, env_for, window_rows
from store.store_fake import outage_on
from typer.testing import CliRunner

from pothole import cli, daily, dailyai, dailyrepair, runrecord
from pothole.dailyerror import describe


def boom(*a: Any, **k: Any) -> Any:
    raise RuntimeError("the step broke")


class Case:
    def __init__(self, step: str, patch: Callable[[pytest.MonkeyPatch], None], **env: Any) -> None:
        self.step, self.patch, self.env = step, patch, env


def patch_attr(owner: Any, name: str) -> Callable[[pytest.MonkeyPatch], None]:
    return lambda mp: mp.setattr(owner, name, boom)


CASES = {
    "spec": Case("spec", patch_attr(daily.PipelineSpec, "load")),
    "load": Case("load", lambda mp: None),  # a non-source error from the source
    "reconcile": Case("weekly reconcile", patch_attr(daily.reconcile, "weekly"), now=SUNDAY),
    "repair": Case("repair", patch_attr(dailyrepair, "real_failure"), status=""),
    "drill": Case("drill", patch_attr(dailyai, "drill"), now=SUNDAY, ai=True),
    "counts": Case("counts", patch_attr(daily, "refresh_counts")),
    "analysis": Case("analysis", patch_attr(daily, "screen")),
    "investigation": Case(
        "investigation", patch_attr(dailyai, "investigate_day"), spike=True, ai=True
    ),
    "analyst file": Case("analyst file", patch_attr(daily, "write_json")),
    "record and site": Case("record and site", patch_attr(daily.sitedata, "build")),
}


@pytest.mark.parametrize("name", list(CASES))
def test_an_exception_in_any_step_ends_in_an_error_record(
    name: str, repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = CASES[name]
    now: datetime = case.env.get("now", TUESDAY)
    rows = window_rows(
        now, spike=case.env.get("spike", False), status=case.env.get("status", "Open")
    )
    env, _, fake = env_for(repo, isolated_state, rows, now, ai=case.env.get("ai", False))
    if name == "load":
        fake.before_query = lambda n, params: boom()
    case.patch(monkeypatch)
    with pytest.raises(RuntimeError):
        daily.run(env)
    last = runrecord.read(repo / "data" / "runs.jsonl")[-1]
    assert last.status == "error" and last.run_id == env.run_id
    assert last.error is not None and last.error.startswith(f"{case.step}: RuntimeError: ")
    assert "the step broke" in last.error
    assert Decimal(last.ai_cost) >= 0


def test_the_error_record_keeps_what_was_known_and_the_cost_so_far(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def paid_then_broke(*a: Any, **k: Any) -> Any:
        a[6].add(Decimal("0.02"))  # the spend of the step that ran
        raise RuntimeError("after the model call")

    monkeypatch.setattr(dailyai, "investigate_day", paid_then_broke)
    env, _, _ = env_for(repo, isolated_state, window_rows(TUESDAY, spike=True), ai=True)
    with pytest.raises(RuntimeError):
        daily.run(env)
    last = runrecord.read(repo / "data" / "runs.jsonl")[-1]
    assert last.ai_cost == "0.02" and last.ai_enabled and last.rows_loaded == 200
    assert last.generation and last.checks and last.analyst_day == "2026-11-08"


def test_an_outage_is_not_an_error_run(repo: Path, isolated_state: Path) -> None:
    env, _, fake = env_for(repo, isolated_state, window_rows(TUESDAY))
    fake.before_query = outage_on(0)
    assert daily.run(env).status == "source_unavailable"


def test_the_error_text_has_no_secrets_and_no_service_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "sk-very-secret-key")
    text = describe(RuntimeError("401 for key sk-very-secret-key " + "x" * 500), "repair")
    assert "sk-very-secret-key" not in text and "***" in text and len(text) < 400
    request = httpx.Request("POST", "https://example.invalid/v1")
    api = openai.APIConnectionError(message="body with sk-very-secret-key", request=request)
    assert describe(api, "repair") == "repair: APIConnectionError"


def test_a_failure_before_the_run_starts_is_recorded_as_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "555")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    result = CliRunner().invoke(cli.app, ["daily", "--repo", str(tmp_path)])
    assert result.exit_code == 1 and chained(result.exception, ValueError)
    [last] = runrecord.read(tmp_path / "data" / "runs.jsonl")
    assert last.status == "error" and last.run_id == "555" and last.attempt == 2
    assert last.ai_enabled and last.ai_cost == "0"
    assert last.error is not None and last.error.startswith("setup: ValueError: ")
    assert "not set" in last.error


def chained(exc: BaseException | None, kind: type[BaseException]) -> bool:
    """Whether `kind` is anywhere in the chain of causes and contexts."""
    while exc is not None:
        if isinstance(exc, kind):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


class FakeLog:
    def __init__(self) -> None:
        self.lines: list[dict[str, Any]] = []

    def error(self, message: str, extra: dict[str, Any] | None = None) -> None:
        self.lines.append({"message": message, **(extra or {})})


def test_a_failed_record_write_never_hides_the_original_error(
    repo: Path, isolated_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOCRATA_APP_TOKEN", "tok-secret-1234")
    fake = FakeLog()
    monkeypatch.setattr(daily, "log", fake)

    def failing_append(*a: Any, **k: Any) -> None:
        raise OSError("disk full")

    def broken(*a: Any, **k: Any) -> Any:
        raise RuntimeError("step broke for tok-secret-1234")

    monkeypatch.setattr(daily.runrecord, "append", failing_append)
    monkeypatch.setattr(daily, "refresh_counts", broken)
    env, _, _ = env_for(repo, isolated_state, window_rows(TUESDAY))
    with pytest.raises(RuntimeError, match="step broke"):  # the original error is the one raised
        daily.run(env)
    [line] = [x for x in fake.lines if x["message"] == "the error record could not be written"]
    assert line["error"].startswith("counts: RuntimeError: step broke for ***")
    assert line["record_error"] == "record write: OSError: disk full"
    assert "tok-secret-1234" not in str(fake.lines)
    # the same for a setup failure: the command still exits 1, chained to the setup error
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    result = CliRunner().invoke(cli.app, ["daily", "--repo", str(repo)])
    assert result.exit_code == 1 and chained(result.exception, ValueError)
