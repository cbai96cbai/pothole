"""Who handles a failed run: the repair agent, an issue, or nobody."""

from typing import Literal

from pothole.spec.checks import CheckResult

Route = Literal["agent", "issue", "nothing"]


def route(check_results: list[CheckResult], fetch_error: BaseException | str | None) -> Route:
    """A fetch failure or an empty fetch is an outage: it goes to an issue and never to the model.
    Otherwise any failed check goes to the agent, and a run with none needs nothing."""
    if fetch_error is not None:
        return "issue"
    return "agent" if any(r.level == "fail" for r in check_results) else "nothing"
