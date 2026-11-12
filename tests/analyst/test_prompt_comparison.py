from investigator_kit import Scripted, candidate, make_store, reply, submit, tool_call

from pothole.analyst.prompt import SYSTEM_PROMPT, TOOLS


def test_the_prompt_and_the_submit_tool_ask_for_both_numbers_of_a_comparison() -> None:
    submit_tool = next(t for t in TOOLS if t["function"]["name"] == "submit_finding")
    described = submit_tool["function"]["parameters"]["properties"]["summary"]["description"]
    for text in (SYSTEM_PROMPT, described):
        assert "both numbers" in text


def test_a_scripted_investigation_sends_the_rule_to_the_model(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from pothole.analyst import investigate

    store = make_store(tmp_path / "raw.duckdb")
    model = Scripted([reply(tool_call("neighbours", {}, "n")), reply(submit("30 against 3.", [1]))])
    investigate.run(candidate(), store, model, weather_for=lambda d: None, events_for=lambda *a: [])  # type: ignore[arg-type,return-value]
    assert "both numbers" in model.sent[0]["messages"][0]["content"]
