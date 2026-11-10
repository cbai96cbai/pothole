"""A screen for causal wording in a summary. It is a screen, not a guarantee: the prompt's
instruction to say "associated with" is the main control, and a paraphrase can get past a list.

The text is normalised first (Unicode NFKC, case-folded, dashes and every kind of whitespace read
as one space) so spacing and hyphen tricks do not hide a phrase. An invisible character can join
two words or split one, so the text is checked both with invisible characters read as a space and
with them dropped.
"""

import re
import unicodedata

PHRASES = (
    "caused",
    "causing",
    "cause of",
    "because of",
    "due to",
    "owing to",
    "led to",
    "leads to",
    "leading to",
    "result of",
    "resulted in",
    "results in",
    "as a result",
    "triggered by",
)
_BANNED = re.compile(r"\b(" + "|".join(PHRASES) + r")\b")
_DASHES = re.compile(r"[‐-―−\-_]")
_SPACE = re.compile(r"\s+")


def normalise(text: str, invisible: str = "") -> str:
    """`invisible` replaces each invisible format character: "" drops it, " " reads it as one."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = "".join(invisible if unicodedata.category(ch) == "Cf" else ch for ch in folded)
    return _SPACE.sub(" ", _DASHES.sub(" ", folded)).strip()


def causal_phrase(text: str) -> str | None:
    """The first banned phrase in `text`, or None."""
    for invisible in ("", " "):
        hit = _BANNED.search(normalise(text, invisible))
        if hit:
            return hit.group(1)
    return None
