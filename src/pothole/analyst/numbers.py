"""How a number the investigator reads is written. The site writes the same numbers with the same
rule (`oneDecimal` in web/src/format.ts), and both are tested against one table of cases
(web/scripts/one-decimal-cases.json), so a summary and its card never differ in the last digit.
Calculations keep full precision; only the written form is rounded."""

import math


def one_decimal(x: float) -> str:
    """One decimal place, a half rounded away from zero (Python's own `.1f` rounds a half to
    even, JavaScript's `toFixed` does not, so neither is used on the raw value)."""
    rounded = math.copysign(math.floor(abs(x) * 10 + 0.5) / 10, x)
    return f"{rounded + 0.0:.1f}"  # + 0.0: a rounded -0.04 is written 0.0, not -0.0
