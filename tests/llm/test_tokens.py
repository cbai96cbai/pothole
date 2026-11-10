from pothole.llm.tokens import count_input_tokens, count_text_tokens

MSGS = [{"role": "user", "content": "How many noise complaints came in on Sunday?"}]
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "top_types",
            "description": "Top complaint types",
            "parameters": {"type": "object", "properties": {"day": {"type": "string"}}},
        },
    }
]


def test_count_text_tokens() -> None:
    assert count_text_tokens("hello world") == 2
    assert count_text_tokens("") == 0


def test_input_count_base_is_content_plus_4_plus_300() -> None:
    assert count_input_tokens(MSGS, None) == count_text_tokens(MSGS[0]["content"]) + 4 + 300


def test_input_count_includes_tools_and_tool_calls() -> None:
    assert count_input_tokens(MSGS, TOOLS) > count_input_tokens(MSGS, None) + 20
    call = {"id": "t1", "function": {"name": "top_types", "arguments": '{"day": "x"}'}}
    with_call = [*MSGS, {"role": "assistant", "content": None, "tool_calls": [call]}]
    assert count_input_tokens(with_call, None) > count_input_tokens(MSGS, None) + 4
