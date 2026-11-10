"""Client for the Socrata SODA 2.1 API on NYC Open Data.

A 5xx, a 429, a timeout or a dropped connection is retried 3 times (waits of 2, 4 and 8 s);
if the last try also fails the call raises SourceUnavailable. Any other 4xx raises
SourceRejected carrying the SoQL error text. Keyset continuation is the caller's job: pages()
only repeats one query until a page comes back shorter than the limit.
"""

import time
from collections.abc import Iterator, Mapping
from typing import Any

import httpx

from pothole.logs import get_logger

DOMAIN = "data.cityofnewyork.us"
TIMEOUT_S = 120.0
BACKOFF_S = (2.0, 4.0, 8.0)

log = get_logger(__name__)


class SourceUnavailable(Exception):
    """The source did not answer usefully after the retries (5xx, 429, timeout, connection)."""


class SourceRejected(Exception):
    """The source refused the request (4xx other than 429); the message is its own error text.

    `code` is the source's own error code when it sent one, such as `query.soql.no-such-column`."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code

    @property
    def names_missing_column(self) -> bool:
        """The query named a field the dataset no longer has: a schema change, not an outage."""
        return "no-such-column" in (self.code or "") or "no such column" in str(self).lower()


def _error_body(response: httpx.Response) -> tuple[str, str | None]:
    """The error text and the source's error code (`errorCode` or `code`) when it sent them."""
    try:
        body = response.json()
        if isinstance(body, dict) and body.get("message"):
            code = body.get("errorCode") or body.get("code")
            text = f"{body['message']} ({code})" if code else str(body["message"])
            return text, str(code) if code else None
    except ValueError:
        pass
    return response.text[:500] or f"HTTP {response.status_code}", None


class Socrata:
    def __init__(self, domain: str = DOMAIN, token: str | None = None) -> None:
        headers = {"X-App-Token": token} if token else {}
        self._http = httpx.Client(base_url=f"https://{domain}", headers=headers, timeout=TIMEOUT_S)

    def get(self, dataset: str, params: Mapping[str, str]) -> httpx.Response:
        """GET /resource/{dataset}.json with retries; returns the successful response."""
        url = f"/resource/{dataset}.json"
        last = ""
        for attempt in range(len(BACKOFF_S) + 1):
            if attempt:
                time.sleep(BACKOFF_S[attempt - 1])
            try:
                response = self._http.get(url, params={k: v for k, v in params.items() if v})
            except httpx.TransportError as exc:  # timeouts and connection failures
                last = f"{type(exc).__name__}: {exc}"
            else:
                status = response.status_code
                if status < 400:
                    return response
                if status != 429 and status < 500:
                    text, code = _error_body(response)
                    raise SourceRejected(f"{dataset}: HTTP {status}: {text}", code)
                last = f"HTTP {status}"
            log.warning("socrata call failed", extra={"dataset": dataset, "attempt": attempt + 1})
        raise SourceUnavailable(f"{dataset}: {last} after {len(BACKOFF_S) + 1} tries")

    def query(self, dataset: str, params: Mapping[str, str]) -> list[dict[str, Any]]:
        response = self.get(dataset, params)
        try:
            rows = response.json()
        except ValueError as exc:
            raise SourceUnavailable(
                f"{dataset}: HTTP {response.status_code} body is not JSON: {response.text[:200]!r}"
            ) from exc
        if not isinstance(rows, list):
            raise SourceRejected(f"{dataset}: expected a list of rows, got {type(rows).__name__}")
        return rows

    def pages(
        self, dataset: str, select: str, where: str, order: str, limit: int
    ) -> Iterator[list[dict[str, Any]]]:
        """Yield pages of `limit` rows; stop after the first page shorter than `limit`."""
        offset = 0
        while True:
            rows = self.query(
                dataset,
                {
                    "$select": select,
                    "$where": where,
                    "$order": order,
                    "$limit": str(limit),
                    "$offset": str(offset),
                },
            )
            if rows:
                yield rows
            if len(rows) < limit:
                return
            offset += limit
