"""The site publishes exactly the runs `results/published.json` names, only when each is a
finished, clean, frozen run whose records agree, with every score split by who decided it."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import site_repo

from pothole import sitedata

REAL = site_repo.REAL
LISTED = [
    "baseline-final-dev",
    "agent-final-dev-a",
    "agent-final-dev-b",
    "baseline-final-test",
    "agent-final-test",
]
GONE = object()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return site_repo.make(tmp_path / "repo", with_agent=True)


def results(repo: Path) -> Path:
    return repo / "results"


def edit(repo: Path, run: str, file: str, key: str, value: object) -> None:
    path = results(repo) / run / f"{file}.json"
    data = json.loads(path.read_text())
    if value is GONE:
        del data[key]
    else:
        data[key] = value
    path.write_text(json.dumps(data))


def build(repo: Path) -> dict[str, Any]:
    out = repo.parent / "out"
    sitedata.build(repo, out)
    return json.loads((out / "repairs.json").read_text())


def manifest(repo: Path, change: Callable[[dict[str, Any]], None]) -> None:
    path = results(repo) / "published.json"
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


def test_only_the_listed_runs_are_published_in_the_listed_order(repo: Path) -> None:
    rows = build(repo)["summaries"]
    assert [s["run_id"] for s in rows] == LISTED
    assert [s["split"] for s in rows] == ["dev"] * 3 + ["test"] * 2


def test_an_unlisted_folder_is_not_published(repo: Path) -> None:
    for name, complete in (("agent-tuning", True), ("agent-stopped", False)):
        (results(repo) / name).mkdir()
        for f in ("summary.json", "meta.json"):
            (results(repo) / name / f).write_text(
                (REAL / "results/agent-final-test" / f).read_text()
            )
        edit(repo, name, "summary", "complete", complete)
        edit(repo, name, "summary", "run_id", name)
        edit(repo, name, "meta", "run_id", name)
    assert [s["run_id"] for s in build(repo)["summaries"]] == LISTED


def test_no_manifest_publishes_nothing(repo: Path) -> None:
    (results(repo) / "published.json").unlink()
    assert build(repo) == {"summaries": [], "agent_run": False, "compare": None}


def test_baseline_only_manifest_has_no_agent_run(tmp_path: Path) -> None:
    repairs = build(site_repo.make(tmp_path / "repo"))
    assert repairs["agent_run"] is False and repairs["compare"] is None
    assert [s["run_id"] for s in repairs["summaries"]] == site_repo.BASELINE_RUNS


def lacks(file: str, key: str) -> tuple[str, str, str, object]:
    return ("agent-final-test", file, key, GONE)


BROKEN = {
    "complete false": ("agent-final-test", "summary", "complete", False),
    "complete 1": ("agent-final-test", "summary", "complete", 1),
    "complete missing": lacks("summary", "complete"),
    "dirty": ("agent-final-test", "meta", "dirty", True),
    "dirty not a boolean": ("agent-final-test", "meta", "dirty", "false"),
    "dirty missing": lacks("meta", "dirty"),
    "commit empty": ("agent-final-test", "meta", "commit", ""),
    "commit missing": lacks("meta", "commit"),
    "commit differs": ("agent-final-test", "meta", "commit", "0" * 40),
    "meta run_id": ("agent-final-test", "meta", "run_id", "other"),
    "summary run_id": ("agent-final-test", "summary", "run_id", "other"),
    "summary run_id missing": lacks("summary", "run_id"),
    "meta split": ("agent-final-test", "meta", "split", "dev"),
    "summary split": ("agent-final-test", "summary", "split", "dev"),
    "repairer is not the system": ("agent-final-test", "summary", "repairer", "baseline"),
    "no decided_by": lacks("summary", "decided_by"),
    "no repairer_decided": lacks("summary", "repairer_decided"),
    "unknown decider": (
        "agent-final-test",
        "summary",
        "decided_by",
        {"agent": 20, "rule": 7, "x": 1},
    ),
    "the other repairer decided": (
        "agent-final-test",
        "summary",
        "decided_by",
        {"baseline": 20, "rule": 7},
    ),
}


@pytest.mark.parametrize("case", BROKEN)
def test_a_listed_run_that_is_not_frozen_and_consistent_stops_the_build(
    repo: Path, case: str
) -> None:
    run, file, key, value = BROKEN[case]
    edit(repo, run, file, key, value)
    with pytest.raises(ValueError, match=run):
        build(repo)


@pytest.mark.parametrize("file", ["summary.json", "meta.json"])
def test_a_listed_run_missing_a_file_stops_the_build(repo: Path, file: str) -> None:
    (results(repo) / "agent-final-test" / file).unlink()
    with pytest.raises(ValueError, match="agent-final-test"):
        build(repo)


def test_a_listed_run_with_no_folder_stops_the_build(repo: Path) -> None:
    manifest(repo, lambda d: d["test"].append("agent-final-gone"))
    with pytest.raises(ValueError, match="agent-final-gone"):
        build(repo)


def test_the_same_run_listed_twice_stops_the_build(repo: Path) -> None:
    manifest(repo, lambda d: d["dev"].append("agent-final-dev-a"))
    with pytest.raises(ValueError, match="agent-final-dev-a"):
        build(repo)


def test_a_run_id_that_is_not_a_folder_name_stops_the_build(repo: Path) -> None:
    manifest(repo, lambda d: d["dev"].append("../data"))
    with pytest.raises(ValueError, match="data"):
        build(repo)


def summaries(repo: Path) -> dict[str, dict[str, Any]]:
    return {s["run_id"]: s for s in build(repo)["summaries"]}


def test_the_rule_decided_rates_are_overall_minus_the_repairers(repo: Path) -> None:
    s = summaries(repo)["agent-final-test"]
    assert s["repairer_decided"]["issue_accuracy"] == {"correct": 8, "of": 8, "rate": 1.0}
    assert s["rule_decided"]["issue_accuracy"] == {"correct": 4, "of": 4, "rate": 1.0}
    assert s["repairer_decided"]["no_change_accuracy"] is None
    assert s["rule_decided"]["no_change_accuracy"] == {"correct": 3, "of": 3, "rate": 1.0}
    assert s["rule_decided"]["repair_success"] is None  # the rule decided none of those
    assert "note" not in s["repairer_decided"]


def test_a_rule_share_below_zero_stops_the_build(repo: Path) -> None:
    path = results(repo) / "agent-final-test" / "summary.json"
    data = json.loads(path.read_text())
    data["repairer_decided"]["issue_accuracy"] = {"correct": 12, "of": 13, "rate": 0.9231}
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="agent-final-test"):
        build(repo)


def test_each_family_is_split_by_who_decided_it(repo: Path) -> None:
    path = results(repo) / "agent-final-test" / "summary.json"
    data = json.loads(path.read_text())
    data["failures"].append(
        {
            **data["failures"][0],
            "id": "source_empty-1",
            "family": "source_empty",
            "decided_by": "agent",
        }
    )
    path.write_text(json.dumps(data))
    fam = summaries(repo)["agent-final-test"]["per_family"]
    assert {
        k: fam["source_empty"][k] for k in fam["source_empty"] if k.endswith(("cases", "correct"))
    } == {
        "cases": 6,
        "correct": 6,
        "rule_cases": 4,
        "rule_correct": 4,
        "repairer_cases": 2,
        "repairer_correct": 1,
    }
    assert fam["spike"]["rule_cases"] == 3 and fam["spike"]["repairer_cases"] == 0
    assert fam["comma_thousands"]["repairer_correct"] == 5
    assert fam["benign_field"]["repairer_cases"] == 0


def add_failure(repo: Path, family: str, by: str, n: int) -> None:
    path = results(repo) / "agent-final-test" / "summary.json"
    data = json.loads(path.read_text())
    base = data["failures"][0]
    for i in range(n):
        data["failures"].append(
            {**base, "id": f"{family}-x{i}", "family": family, "decided_by": by}
        )
    path.write_text(json.dumps(data))


def test_a_miss_the_rule_decided_is_not_charged_to_the_repairer(repo: Path) -> None:
    add_failure(repo, "source_empty", "rule", 1)
    fam = summaries(repo)["agent-final-test"]["per_family"]["source_empty"]
    assert (fam["rule_cases"], fam["rule_correct"]) == (4, 3)
    assert (fam["repairer_cases"], fam["repairer_correct"]) == (2, 2)


def test_misses_of_both_deciders_in_one_family_are_counted_apart(repo: Path) -> None:
    add_failure(repo, "source_empty", "rule", 1)
    add_failure(repo, "source_empty", "agent", 1)
    fam = summaries(repo)["agent-final-test"]["per_family"]["source_empty"]
    assert (fam["rule_cases"], fam["rule_correct"]) == (4, 3)
    assert (fam["repairer_cases"], fam["repairer_correct"]) == (2, 1)


def test_more_rule_misses_than_rule_cases_stops_the_build(repo: Path) -> None:
    add_failure(repo, "source_empty", "rule", 5)
    with pytest.raises(ValueError, match="agent-final-test"):
        build(repo)


def test_runs_of_one_split_that_differ_in_what_the_checks_noticed_stop_the_build(
    repo: Path,
) -> None:
    path = results(repo) / "agent-final-dev-b" / "summary.json"
    data = json.loads(path.read_text())
    data["detection_coverage_fault_cases"]["detected"] -= 1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="dev"):
        build(repo)


def test_a_crashed_case_counts_for_the_repairer(repo: Path) -> None:
    path = results(repo) / "agent-final-test" / "summary.json"
    data = json.loads(path.read_text())
    data["decided_by"] = {"agent": 19, "code": 1, "rule": 7}
    data["per_family"]["comma_thousands"]["decided_by"] = {"agent": 5, "code": 1}
    data["failures"][0]["decided_by"] = "code"
    path.write_text(json.dumps(data))
    fam = summaries(repo)["agent-final-test"]["per_family"]["comma_thousands"]
    assert (fam["repairer_cases"], fam["repairer_correct"], fam["rule_cases"]) == (6, 5, 0)


def test_the_test_comparison_is_published_with_its_interval(repo: Path) -> None:
    c = build(repo)["compare"]
    assert (c["a"], c["b"]) == ("agent-final-test", "baseline-final-test")
    assert c["repair_success"] == {
        "cases": 12,
        "a": 0.9167,
        "b": 0.5,
        "difference": 0.4167,
        "interval_95": [-0.1667, 1.0],
        "families": 2,
    }
    assert (c["resamples"], c["seed"]) == (10000, 7)
    assert "issue_accuracy" not in c and "no_change_accuracy" not in c


def swap(d: dict[str, Any]) -> None:
    d["a"], d["b"] = d["b"], d["a"]


def same(d: dict[str, Any]) -> None:
    d["b"] = d["a"]


def outside(d: dict[str, Any]) -> None:
    d["a"] = "agent-final-dev-a"


def unlisted(d: dict[str, Any]) -> None:
    d["a"] = "agent-v3-dev-a"


@pytest.mark.parametrize("change", [swap, same, outside, unlisted])
def test_a_comparison_of_the_wrong_runs_stops_the_build(
    repo: Path, change: Callable[[dict[str, Any]], None]
) -> None:
    path = results(repo) / site_repo.COMPARE
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="compare"):
        build(repo)


def test_a_named_comparison_that_is_missing_stops_the_build(repo: Path) -> None:
    (results(repo) / site_repo.COMPARE).unlink()
    with pytest.raises(ValueError, match="compare"):
        build(repo)


def test_the_real_repo_publishes_the_five_final_runs_at_one_commit(tmp_path: Path) -> None:
    sitedata.build(REAL, tmp_path / "out")
    repairs = json.loads((tmp_path / "out" / "repairs.json").read_text())
    assert [s["run_id"] for s in repairs["summaries"]] == LISTED
    commits = {
        json.loads((REAL / "results" / r / "meta.json").read_text())["commit"] for r in LISTED
    }
    assert len(commits) == 1 and all(commits)
    assert repairs["agent_run"] is True and repairs["compare"]["a"] == "agent-final-test"
