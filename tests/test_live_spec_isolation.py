"""Only two tests may read the live spec, the file a repair pull request changes. Any other test
that fed old-shape rows to it would turn a correct rename red. Everything else uses the pinned
copy beside the recorded batches or an inline spec."""

from pathlib import Path

TESTS = Path(__file__).parent
# read the live spec: the parse check and the regression collector (which gets the path from
# each case file). test_sink and the site fixture only use the path as a location inside a
# scratch repository.
ALLOWED = {
    "spec/test_live_spec.py",
    "regression/test_cases.py",
    "repair/test_sink.py",
    "webdata/site_repo.py",
}
NEEDLES = ("311" + ".yaml", "SPEC" + "_PATH")


def test_no_other_test_reads_the_live_spec() -> None:
    offenders = []
    for path in sorted(TESTS.rglob("*.py")):
        rel = path.relative_to(TESTS).as_posix()
        if rel in ALLOWED or rel == Path(__file__).name:
            continue
        text = path.read_text()
        if any(n in text for n in NEEDLES):
            offenders.append(rel)
    assert offenders == []
