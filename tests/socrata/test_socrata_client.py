import httpx
import pytest
import respx

from pothole.socrata import client as client_mod
from pothole.socrata.client import Socrata, SourceRejected, SourceUnavailable
from pothole.socrata.meta import Field, fields, max_updated_at

URL = "https://data.cityofnewyork.us/resource/abcd-1234.json"


@pytest.fixture(autouse=True)
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr(client_mod.time, "sleep", slept.append)
    return slept


@respx.mock
def test_token_header_only_when_set() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=[]))
    Socrata().query("abcd-1234", {})
    assert "x-app-token" not in route.calls[0].request.headers
    Socrata(token="t0k").query("abcd-1234", {})
    assert route.calls[1].request.headers["x-app-token"] == "t0k"


@respx.mock
def test_429_then_200_succeeds(waits: list[float]) -> None:
    respx.get(URL).mock(side_effect=[httpx.Response(429), httpx.Response(200, json=[{"a": "1"}])])
    assert Socrata().query("abcd-1234", {}) == [{"a": "1"}]
    assert waits == [2.0]


@respx.mock
def test_three_503s_raise_unavailable_after_all_retries(waits: list[float]) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(503))
    with pytest.raises(SourceUnavailable):
        Socrata().query("abcd-1234", {})
    assert route.call_count == 4  # first try + 3 retries
    assert waits == [2.0, 4.0, 8.0]


@respx.mock
def test_timeout_is_unavailable_after_all_retries(waits: list[float]) -> None:
    route = respx.get(URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    with pytest.raises(SourceUnavailable):
        Socrata().query("abcd-1234", {})
    assert route.call_count == 4
    assert waits == [2.0, 4.0, 8.0]


@respx.mock
def test_503_then_200_succeeds(waits: list[float]) -> None:
    respx.get(URL).mock(side_effect=[httpx.Response(503), httpx.Response(200, json=[])])
    assert Socrata().query("abcd-1234", {}) == []
    assert waits == [2.0]


@respx.mock
def test_non_json_body_is_unavailable_with_a_clear_message() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, text="<html>proxy error</html>"))
    with pytest.raises(SourceUnavailable, match="body is not JSON.*proxy error"):
        Socrata().query("abcd-1234", {})


@respx.mock
def test_pages_full_last_page_costs_exactly_one_extra_request() -> None:
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(200, json=[{"i": 1}, {"i": 2}]), httpx.Response(200, json=[])]
    )
    assert list(Socrata().pages("abcd-1234", "i", "i > 0", "i", 2)) == [[{"i": 1}, {"i": 2}]]
    assert route.call_count == 2


@respx.mock
def test_400_is_rejected_with_the_soql_error_text(waits: list[float]) -> None:
    body = {"message": "Unable to parse query", "errorCode": "query.soql.no-such-column"}
    route = respx.get(URL).mock(return_value=httpx.Response(400, json=body))
    with pytest.raises(SourceRejected, match="Unable to parse query"):
        Socrata().query("abcd-1234", {})
    assert route.call_count == 1 and waits == []


@respx.mock
def test_pages_stop_at_a_short_page_and_send_the_query() -> None:
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(200, json=[{"i": 1}, {"i": 2}]),
            httpx.Response(200, json=[{"i": 3}]),
        ]
    )
    got = list(Socrata().pages("abcd-1234", "i", "i > 0", "i", 2))
    assert got == [[{"i": 1}, {"i": 2}], [{"i": 3}]]
    first, second = (c.request.url.params for c in route.calls)
    assert first["$where"] == "i > 0" and first["$order"] == "i" and first["$limit"] == "2"
    assert first["$offset"] == "0" and second["$offset"] == "2"


@respx.mock
def test_pages_with_no_rows_yield_nothing() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json=[]))
    assert list(Socrata().pages("abcd-1234", "i", "i > 0", "i", 2)) == []


@respx.mock
def test_fields_parse_both_headers_in_order() -> None:
    respx.get(URL).mock(
        return_value=httpx.Response(
            200,
            json=[],
            headers={
                "X-SODA2-Fields": '["unique_key","created_date","agency"]',
                "X-SODA2-Types": '["text","floating_timestamp","text"]',
            },
        )
    )
    assert fields(Socrata(), "abcd-1234") == [
        Field("unique_key", "text"),
        Field("created_date", "floating_timestamp"),
        Field("agency", "text"),
    ]


@respx.mock
def test_fields_without_headers_is_rejected() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(SourceRejected):
        fields(Socrata(), "abcd-1234")


@respx.mock
def test_max_updated_at() -> None:
    route = respx.get(URL).mock(
        return_value=httpx.Response(200, json=[{"latest": "2026-11-10T02:37:11.932Z"}])
    )
    assert max_updated_at(Socrata(), "abcd-1234", "x > 1") == "2026-11-10T02:37:11.932Z"
    assert route.calls[0].request.url.params["$select"] == "max(:updated_at) as latest"


@pytest.mark.live_api
def test_live_five_rows_of_311() -> None:
    rows = Socrata().query("erm2-nwe9", {"$limit": "5", "$select": "unique_key,created_date"})
    assert len(rows) == 5 and "unique_key" in rows[0]
