"""Community board codes from the two datasets, read into (borough, number)."""

import re

BOARDS_PER_BOROUGH = {
    "Manhattan": 12,
    "Bronx": 12,
    "Brooklyn": 18,
    "Queens": 14,
    "Staten Island": 3,
}
_BOROUGHS = {name.upper(): name for name in BOARDS_PER_BOROUGH}
_CODE = re.compile(r"^\s*(\d{1,2})\s+([A-Za-z ]+?)\s*$")


def _regular(borough: str, number: int) -> tuple[str, int] | None:
    name = _BOROUGHS.get(" ".join(borough.upper().split()))
    if name is None or not 1 <= number <= BOARDS_PER_BOROUGH[name]:
        return None
    return name, number


def board(raw: str | None) -> tuple[str, int] | None:
    """A 311 community_board such as "01 BROOKLYN"; anything outside the 59 districts is None."""
    match = _CODE.match(raw or "")
    return _regular(match.group(2), int(match.group(1))) if match else None


def event_boards(raw_list: list[str | None], borough: str) -> list[tuple[str, int]]:
    """Event community_board values such as "1," or "1,2," with the borough given apart."""
    found: list[tuple[str, int]] = []
    for raw in raw_list:
        if raw is None:
            continue
        for part in raw.split(","):
            part = part.strip()
            if not part.isdigit():
                continue
            district = _regular(borough, int(part))
            if district is not None and district not in found:
                found.append(district)
    return found
