import json

from pothole.analyst.excerpt import excerpt


def test_a_short_result_is_shown_whole() -> None:
    assert excerpt('[["a", 1]]', 300) == '[["a", 1]]'


def test_a_long_list_is_cut_between_items_and_says_how_many_are_shown() -> None:
    items = [[f"value {i}", i] for i in range(100)]
    out = excerpt(json.dumps(items), 200)
    assert len(out) <= 200
    body, note = out.rsplit(" (", 1)
    kept = json.loads(body)  # whole items only: it still parses
    assert kept == items[: len(kept)] and 0 < len(kept) < 100
    assert note == f"first {len(kept)} of 100)"


def test_a_series_keeps_its_latest_days_because_the_spike_is_at_the_end() -> None:
    rows = [[f"2026-10-{d:02d}", d] for d in range(1, 31)]
    out = json.loads(excerpt(json.dumps({"series": rows, "note": "data starts 2026-10-01"}), 250))
    kept = out["series"]
    assert kept == rows[-len(kept) :] and kept[-1] == ["2026-10-30", 30]
    assert out["shown"] == f"last {len(kept)} of 30" and out["note"] == "data starts 2026-10-01"


def test_a_long_object_with_a_list_keeps_its_other_fields() -> None:
    result = json.dumps({"scope": "s", "truncated": False, "events": [{"name": "n" * 40}] * 20})
    out = json.loads(excerpt(result, 250))
    assert out["scope"] == "s" and out["truncated"] is False
    assert out["shown"].startswith("first ") and out["shown"].endswith(" of 20")


def test_text_that_is_not_a_list_is_cut_with_a_mark() -> None:
    out = excerpt("x" * 500, 100)
    assert len(out) <= 100 and out.endswith("…")
