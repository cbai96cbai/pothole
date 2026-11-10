import pytest

from pothole.analyst.wording import causal_phrase

BANNED = [
    "caused", "causing", "cause of", "because of", "due to", "owing to", "led to", "leads to",
    "leading to", "result of", "resulted in", "results in", "as a result", "triggered by",
]  # fmt: skip


@pytest.mark.parametrize("phrase", BANNED)
def test_each_phrase_is_caught_in_a_sentence(phrase: str) -> None:
    assert causal_phrase(f"Requests rose {phrase} the cold.") == phrase


@pytest.mark.parametrize(
    "text",
    [
        "Rise BECAUSE OF rain",
        "Rise Due To rain",
        "because-of rain",
        "because‑of rain",
        "because—of rain",
        "because  of rain",
        "because of rain",
        "because　of rain",
        "because\nof rain",
        "be​cause of rain",
        "because​of rain",
        "due⁠to rain",
        "ｂｅｃａｕｓｅ ｏｆ rain",
        "Owing  To rain",
    ],
)
def test_spacing_case_and_dash_tricks_are_caught(text: str) -> None:
    assert causal_phrase(text) is not None


@pytest.mark.parametrize(
    "text",
    ["Associated with a cold night.", "Coincided with a street fair.", "The leader tooled up."],
)
def test_plain_associative_wording_passes(text: str) -> None:
    assert causal_phrase(text) is None


def test_the_model_is_told_every_refused_phrase() -> None:
    from pothole.analyst.prompt import SYSTEM_PROMPT, TOOLS
    from pothole.analyst.wording import PHRASES

    submit = next(t for t in TOOLS if t["function"]["name"] == "submit_finding")
    for phrase in PHRASES:
        assert f'"{phrase}"' in SYSTEM_PROMPT
        assert phrase in submit["function"]["description"]
