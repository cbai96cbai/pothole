"""The words the investigator is given: instructions, tool schemas and nudges."""

from typing import Any

from pothole.analyst.tools import BREAKDOWN_BY, MAX_DAYS, MAX_TOP
from pothole.analyst.wording import PHRASES

SYSTEM_PROMPT = """\
You look into one unusual day in the public NYC 311 service-request data: one complaint type in \
one community district had far more requests than the usual for that weekday. Use the tools to \
look at the day, then write a short finding with submit_finding.

Tools: series (daily counts), breakdown (the day's requests split by descriptor, zip code, hour, \
agency, location type or status), neighbours (the same complaint type that day in other \
districts), weather (rain and the day's high at Central Park) and events (city-permitted events \
in the district that day).

Rules for the finding:
- Say what the spike is associated with, using only what the tools returned. Use the words \
"associated with" or "coincided with". Never write any of these: {banned}. A submission \
containing one is refused.
- If the tools show nothing that goes with the spike, say so plainly. Do not guess.
- Two to four plain sentences, under 450 characters (a summary over 600 is refused), with the \
numbers you rely on.
- Every tool result begins "ref <n>:", a number. evidence lists the numbers of the tool calls the \
summary relies on, for example [1, 3]. You can cite only results you have already read, so call \
the tools first and submit in a later reply: a submission in the same reply as new tool calls \
cannot cite them. Set wording_check to true only after you re-read the summary for the banned \
words.
Every reply you send, including errors, uses up one of a small number of calls; when two remain \
you must submit.\
""".format(banned=", ".join(f'"{p}"' for p in PHRASES))

CALLS_LEFT = "You have {n} calls left."
SUBMIT_NEXT = "You have 2 calls left: call submit_finding now."
LAST_CALL = "This is your last call: submit_finding now."
CALL_BUDGET = (
    "You have {n} calls in all; each reply uses one. Read the tool results, then submit in a "
    "separate reply."
)
TOO_FEW_CALLS = (
    " With {n} calls you cannot read a result and then submit: the submission is forced at once "
    "and has nothing to cite."
)
NUDGE_NO_TOOL = "Call one of the tools."
NUDGE_LENGTH = "Your reply was cut off. Answer with a shorter tool call."


def _tool(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


TOOLS: list[dict[str, Any]] = [
    _tool(
        "series",
        "Daily counts of this complaint type in this district, ending on the spike day.",
        {
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_DAYS,
                "description": f"how many days, 1 to {MAX_DAYS}",
            }
        },
        ["days"],
    ),
    _tool(
        "breakdown",
        "The spike day's requests in this district and type, counted by one field.",
        {
            "by": {"type": "string", "enum": list(BREAKDOWN_BY)},
            "top": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_TOP,
                "description": f"how many values, 1 to {MAX_TOP}",
            },
        },
        ["by", "top"],
    ),
    _tool(
        "neighbours",
        "Counts of this complaint type on the spike day in the other districts, largest first.",
        {},
        [],
    ),
    _tool("weather", "Rain (mm) and the high (C) at Central Park on the spike day.", {}, []),
    _tool("events", "City-permitted events in this district that overlap the spike day.", {}, []),
    _tool(
        "submit_finding",
        "Submit the finding. Refused if it says any of: " + ", ".join(PHRASES) + ".",
        {
            "summary": {
                "type": "string",
                "description": "2 to 4 plain sentences, under 450 characters",
            },
            "evidence": {
                "type": "array",
                "items": {"type": "integer"},
                "description": 'the ref numbers ("ref <n>:" at the start of a tool result) of '
                "the tool calls the summary relies on, only ones from earlier replies",
            },
            "wording_check": {
                "type": "boolean",
                "description": "true once you re-read the summary for the banned words",
            },
        },
        ["summary", "evidence", "wording_check"],
    ),
]
