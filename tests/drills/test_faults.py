import pytest

from pothole.drills import catalog, faults
from pothole.drills.catalog import Case
from pothole.spec.model import PipelineSpec

ALL = catalog.load()


@pytest.mark.parametrize("case", ALL, ids=lambda c: c.id)
def test_every_fault_is_detected(case: Case, spec: PipelineSpec):
    rows, _ = catalog.seen_batch(case)
    checks = catalog.run_engine(spec, rows, case.batch_seen).checks
    assert faults.detected(catalog.fault_of(case), checks), [
        (c.name, c.level) for c in checks if c.level != "pass"
    ]


@pytest.mark.parametrize("case", [c for c in ALL if c.family == "spike"], ids=lambda c: c.id)
def test_spike_control_trips_only_volume(case: Case, spec: PipelineSpec):
    rows, _ = catalog.seen_batch(case)
    failed = [
        c.name for c in catalog.run_engine(spec, rows, case.batch_seen).checks if c.level == "fail"
    ]
    assert failed == ["volume"]
    factor = faults.SPIKE_FACTORS[case.variant]
    assert len(rows) == round(2000 * factor)
    assert sum(r["unique_key"].startswith("S") for r in rows) == len(rows) - 2000


@pytest.mark.parametrize("case", [c for c in ALL if c.family == "benign_field"], ids=lambda c: c.id)
def test_benign_field_only_warns(case: Case, spec: PipelineSpec):
    rows, fields = catalog.seen_batch(case)
    checks = catalog.run_engine(spec, rows, case.batch_seen).checks
    assert [c.name for c in checks if c.level == "fail"] == []
    assert faults.BENIGN_FIELDS[case.variant][0] in fields


def test_inject_does_not_change_the_recorded_rows():
    clean = catalog.clean_batch("2026-11-03")
    before = [dict(r) for r in clean]
    for family in faults.FAMILY_NAMES:
        faults.inject(clean, faults.Fault(family, 0, 1))
    assert clean == before


def test_same_fault_gives_the_same_rows():
    clean = catalog.clean_batch("2026-11-03")
    fault = faults.Fault("null_flood", 1, 7)
    assert faults.inject(clean, fault) == faults.inject(clean, fault)


def test_source_fields_follow_the_fault():
    clean = catalog.clean_batch("2026-11-03")
    _, renamed = faults.inject(clean, faults.Fault("renamed_field", 0, 1))
    assert "agency_code" in renamed and "agency" not in renamed
    _, empty = faults.inject(clean, faults.Fault("source_empty", 0, 1))
    assert empty["agency"] == "text"
    _, comma = faults.inject(clean, faults.Fault("comma_thousands", 0, 1))
    assert comma["x_coordinate_state_plane"] == "text"
    assert comma["y_coordinate_state_plane"] == "int"
