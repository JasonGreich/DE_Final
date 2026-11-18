"""Stage 2 tests for the API extractors, using fake HTTP responses (no network, no waiting)."""
import json

import pytest
import requests

from src.extract import frankfurter, manifest, nager
from src.extract.http import ExtractError, http_get


class FakeResponse:
    """Just enough of requests.Response for our code."""

    def __init__(self, status=200, body=b"[]", headers=None):
        self.status_code = status
        self.content = body
        self.text = body.decode("utf-8", errors="replace")
        self.headers = headers or {}


def scripted_getter(*responses):
    """A getter that returns (or raises) the given items one by one, and records its calls."""
    calls = []
    queue = list(responses)

    def getter(url, params=None, timeout=None, headers=None):
        calls.append({"url": url, "params": params, "timeout": timeout, "headers": headers})
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    getter.calls = calls
    return getter


# ---------------------------------------------------------------- http_get

def test_http_get_sends_user_agent_and_timeout():
    getter = scripted_getter(FakeResponse())
    http_get("http://x", getter=getter, sleep=lambda s: None)
    call = getter.calls[0]
    assert "DSC620" in call["headers"]["User-Agent"]
    assert call["timeout"] == 30


def test_http_get_retries_server_errors_with_growing_waits():
    waits = []
    getter = scripted_getter(FakeResponse(503), FakeResponse(500), FakeResponse(200, b"ok"))
    response = http_get("http://x", getter=getter, sleep=waits.append)
    assert response.content == b"ok"
    assert waits == [2, 4]  # backoff: 2s then 4s


def test_http_get_retries_timeouts():
    getter = scripted_getter(requests.Timeout("slow"), FakeResponse(200))
    assert http_get("http://x", getter=getter, sleep=lambda s: None).status_code == 200


def test_http_get_respects_retry_after_on_rate_limit():
    waits = []
    getter = scripted_getter(FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(200))
    http_get("http://x", getter=getter, sleep=waits.append)
    assert waits == [7]


def test_http_get_does_not_retry_client_errors():
    getter = scripted_getter(FakeResponse(404, b"not found"))
    with pytest.raises(ExtractError, match="HTTP 404"):
        http_get("http://x", getter=getter, sleep=lambda s: None)
    assert len(getter.calls) == 1


def test_http_get_gives_up_with_a_clear_message():
    getter = scripted_getter(*[FakeResponse(500)] * 3)
    with pytest.raises(ExtractError, match="failed after 3 attempts"):
        http_get("http://x", tries=3, getter=getter, sleep=lambda s: None)


# ---------------------------------------------------------------- Nager

def test_nager_saves_response_untouched_and_writes_manifest(tmp_path):
    body = json.dumps([{"date": "2016-01-01", "name": "New Year's Day", "global": True}],
                      indent=3).encode()  # odd formatting on purpose: it must survive byte for byte
    getter = scripted_getter(FakeResponse(200, body), FakeResponse(200, body), FakeResponse(200, body))
    counts = nager.extract_holidays(raw_dir=tmp_path, getter=getter, sleep=lambda s: None)
    assert counts == {2016: 1, 2017: 1, 2018: 1}
    files = sorted(tmp_path.glob("nager_holidays_2016_*.json"))
    assert files[0].read_bytes() == body
    lines = manifest.read_manifest(tmp_path)
    assert [line["year"] for line in lines] == [2016, 2017, 2018]
    assert lines[0]["url_or_query"].endswith("/PublicHolidays/2016/BR")
    assert lines[0]["records"] == 1


def test_nager_rejects_an_empty_answer(tmp_path):
    getter = scripted_getter(FakeResponse(200, b"[]"))
    with pytest.raises(ExtractError, match="no holidays"):
        nager.extract_holidays(years=(2016,), raw_dir=tmp_path, getter=getter, sleep=lambda s: None)


# ---------------------------------------------------------------- Frankfurter

def rates_body(first, last):
    return json.dumps([{"date": first, "base": "BRL", "quote": "USD", "rate": 0.25},
                       {"date": last, "base": "BRL", "quote": "USD", "rate": 0.27}]).encode()


def test_frankfurter_saves_response_and_manifest(tmp_path):
    body = rates_body("2015-12-31", "2018-12-31")
    getter = scripted_getter(FakeResponse(200, body))
    n = frankfurter.extract_rates(raw_dir=tmp_path, getter=getter, sleep=lambda s: None)
    assert n == 2
    assert getter.calls[0]["params"]["providers"] == "ecb"
    assert next(tmp_path.glob("frankfurter_brl_usd_*.json")).read_bytes() == body
    line = manifest.read_manifest(tmp_path)[0]
    assert "providers=ecb" in line["url_or_query"] and line["records"] == 2


def test_frankfurter_truncated_answer_is_an_error(tmp_path):
    getter = scripted_getter(FakeResponse(200, rates_body("2016-01-04", "2017-03-01")))
    with pytest.raises(ExtractError, match="truncated"):
        frankfurter.extract_rates(raw_dir=tmp_path, getter=getter, sleep=lambda s: None)
    assert not list(tmp_path.glob("frankfurter_*.json"))  # nothing saved, no manifest line
