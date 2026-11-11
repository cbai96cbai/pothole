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


def volume(count: int, typical: int = 2000) -> CheckResult:
    low, high = typical // 2, typical * 2
    detail = (
        f"{count} rows against a typical {typical} over 4 earlier same-weekday counts; "
        f"allowed {low} to {high}"
    )
    return CheckResult("volume", "fail", detail, [])


def test_a_volume_only_failure_is_decided_by_the_rule_from_its_direction() -> None:
    from pothole.repair.routing import rule_outcome

    low, high = rule_outcome([volume(300)]), rule_outcome([volume(6000)])
    assert low is not None and (low.action, low.decided_by) == ("issue", "rule")
    assert low.reason == "rows are missing: 300 against a usual 2000"
    assert high is not None and (high.action, high.decided_by) == ("no_change", "rule")
    assert high.reason == "a real rise: 6000 against a usual 2000; every other check passes"


def test_the_rule_leaves_every_other_failure_to_a_repairer() -> None:
    from pothole.repair.routing import rule_outcome

    other = CheckResult("cast:created_date", "fail", "x", [])
    assert rule_outcome([volume(6000), other]) is None
    assert rule_outcome([other]) is None
    assert rule_outcome([]) is None


def test_a_ruled_repairer_is_not_called_for_a_volume_only_failure() -> None:
    from pothole.repair.outcome import RepairOutcome
    from pothole.repair.routing import ruled

    called: list[int] = []

    def repairer(*_: object) -> RepairOutcome:
        called.append(1)
        return RepairOutcome("issue", reason="asked", decided_by="baseline")

    decide = ruled(repairer)
    assert decide(None, [volume(300)], [], {}).decided_by == "rule" and called == []
    assert decide(None, [volume(300), r("fail")], [], {}).decided_by == "baseline"
