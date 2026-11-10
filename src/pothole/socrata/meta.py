"""Dataset metadata read from the response headers and simple aggregates."""

import json
from dataclasses import dataclass

from pothole.socrata.client import Socrata, SourceRejected


@dataclass(frozen=True)
class Field:
    name: str
    type: str


def fields(client: Socrata, dataset: str) -> list[Field]:
    """Column names and types, in dataset order, from X-SODA2-Fields and X-SODA2-Types."""
    response = client.get(dataset, {"$limit": "1"})
    try:
        names = json.loads(response.headers["X-SODA2-Fields"])
        types = json.loads(response.headers["X-SODA2-Types"])
    except (KeyError, ValueError) as exc:
        raise SourceRejected(f"{dataset}: field headers missing or unreadable") from exc
    if len(names) != len(types):
        raise SourceRejected(f"{dataset}: {len(names)} field names but {len(types)} types")
    return [Field(n, t) for n, t in zip(names, types, strict=True)]


def max_updated_at(client: Socrata, dataset: str, where: str) -> str:
    """The largest :updated_at among rows matching `where`, as the API prints it."""
    rows = client.query(dataset, {"$select": "max(:updated_at) as latest", "$where": where})
    if not rows or "latest" not in rows[0]:
        raise SourceRejected(f"{dataset}: no rows match {where!r}")
    return str(rows[0]["latest"])
