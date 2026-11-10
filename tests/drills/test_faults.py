import pytest

from pothole.drills import catalog, faults
from pothole.drills.catalog import Case
from pothole.spec.model import PipelineSpec

ALL = catalog.load()


@pytest.mark.parametrize("case", ALL, ids=lambda c: c.id)
def test_every_fault_is_detected(case: Case, spec: PipelineSpec):
    rows, _ = catalog.seen_batch(case)
    checks = catalog.run_engine(spec, rows, case.batch_seen).checks
    fault = catalog.fault_of(case)
    names, level = faults.intended_checks(fault)
    assert names, case.id
    found = {c.name for c in checks if c.level == level}
    assert set(names) <= found, (names, sorted(found))
    assert faults.detected(fault, checks)


@pytest.mark.parametrize("case", [c for c in ALL if c.family == "spike"], ids=lambda c: c.id)
def test_spike_control_trips_only_volume(case: Case, spec: PipelineSpec):
    rows, _ = catalog.seen_batch(case)
    failed = [
        c.name for c in catalog.run_engine(spec, rows, case.batch_seen).checks if c.level == "fail"
    ]
    assert failed == ["volume"]
    base = catalog.clean_batch(case.batch_seen)
    factor = case.params["factor"]
    assert len(rows) == round(len(base) * factor)
    assert sum(r["unique_key"].startswith("S") for r in rows) == len(rows) - len(base)


@pytest.mark.parametrize("case", [c for c in ALL if c.family == "benign_field"], ids=lambda c: c.id)
def test_benign_field_only_warns(case: Case, spec: PipelineSpec):
    rows, fields = catalog.seen_batch(case)
    checks = catalog.run_engine(spec, rows, case.batch_seen).checks
    assert [c.name for c in checks if c.level == "fail"] == []
    assert case.params["field"] in fields


def test_inject_does_not_change_the_recorded_rows(cases: dict[str, Case]):
    clean = catalog.clean_batch("2026-11-03")
    before = [dict(r) for r in clean]
    for family in faults.FAMILY_NAMES:
        faults.inject(clean, catalog.fault_of(cases[f"{family}-0"]))
    assert clean == before


def test_same_fault_gives_the_same_rows(cases: dict[str, Case]):
    clean = catalog.clean_batch("2026-11-03")
    fault = catalog.fault_of(cases["null_flood-1"])
    assert faults.inject(clean, fault) == faults.inject(clean, fault)


def test_a_fault_does_what_its_parameters_say(cases: dict[str, Case]):
    clean = catalog.clean_batch("2026-11-03")
    _, renamed = faults.inject(clean, catalog.fault_of(cases["renamed_field-0"]))
    assert "agency_code" in renamed and "agency" not in renamed
    _, empty = faults.inject(clean, catalog.fault_of(cases["source_empty-0"]))
    assert empty["agency"] == "text"
    _, comma = faults.inject(clean, catalog.fault_of(cases["comma_thousands-0"]))
    assert comma["x_coordinate_state_plane"] == "text"
    assert comma["y_coordinate_state_plane"] == "int"
    other = faults.Fault(
        "comma_thousands", 0, 1, {"fields": ["y_coordinate_state_plane"], "share": 1}
    )
    _, moved = faults.inject(clean, other)
    assert moved["y_coordinate_state_plane"] == "text"
