from pothole.repair import route
from pothole.socrata.client import SourceUnavailable
from pothole.spec.checks import CheckResult


def r(level: str) -> CheckResult:
    return CheckResult("x", level, "d", [])  # type: ignore[arg-type]


def test_an_outage_goes_to_an_issue_even_with_failed_checks() -> None:
    assert route([], SourceUnavailable("503")) == "issue"
    assert route([r("fail")], "fetched 0 rows") == "issue"


def test_failed_checks_go_to_the_agent() -> None:
    assert route([r("pass"), r("fail")], None) == "agent"


def test_warnings_and_passes_need_nothing() -> None:
    assert route([r("pass"), r("warn")], None) == "nothing"
    assert route([], None) == "nothing"
