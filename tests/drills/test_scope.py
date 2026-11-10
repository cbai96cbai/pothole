from pothole.drills import catalog
from pothole.drills.catalog import Case
from pothole.spec import ops, scope
from pothole.spec.checks import CheckResult
from pothole.spec.model import PipelineSpec


def _fail(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name, "fail", detail, [])


def test_columns_named_by_a_failed_check_are_in_scope(spec: PipelineSpec):
    checks = [
        _fail("cast:created_date"),
        _fail("values:borough"),
        _fail("range:latitude"),
        _fail("required:status"),
        _fail("null_rate:longitude"),
        CheckResult("cast:closed_date", "warn", "", []),
        _fail("volume"),
    ]
    assert scope.failing_columns(spec, checks) == {
        "created_date",
        "borough",
        "latitude",
        "status",
        "longitude",
    }


def test_schema_fail_names_columns_by_source(spec: PipelineSpec):
    detail = "required source fields missing from all 9 rows: agency, status"
    assert scope.failing_columns(spec, [_fail("schema", detail)]) == {"agency", "status"}


def test_check_reports_each_problem(spec: PipelineSpec):
    failing = [_fail("cast:created_date")]
    ok = [ops.SetFormat(column="created_date", format="%Y-%m-%d")]
    assert scope.check(ok, failing, ["created_date"], spec) == []
    stray = [*ok, ops.AddValueMapping(column="borough", raw="x", value="Queens")]
    assert len(scope.check(stray, failing, [], spec)) == 1
    remap = [ops.MapField(column="created_date", source="created_at")]
    assert len(scope.check(remap, failing, ["created_date"], spec)) == 1
    assert scope.check(remap, failing, ["created_at"], spec) == []


def test_scope_uses_the_seen_batch_of_a_case(cases: dict[str, Case], spec: PipelineSpec):
    case = cases["renamed_field-1"]
    rows, fields = catalog.seen_batch(case)
    checks = catalog.run_engine(spec, rows, case.batch_seen).checks
    fix = [ops.MapField(column="complaint_type", source="complaint_type_name")]
    assert scope.check(fix, checks, fields, spec) == []
