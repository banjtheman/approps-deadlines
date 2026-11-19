"""The fetcher pauses before every request, redirects included, and retries once."""

import pytest
import requests

from approps_deadlines.fetch import FetchError, PoliteFetcher


class FakeHTTP:
    def __init__(self, status, headers=None, body=b""):
        self.status_code, self.headers, self.body = status, headers or {}, body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, _size):
        yield self.body


class FakeSession:
    def __init__(self, script):
        self.script = list(script)  # responses or exceptions, in order
        self.headers = {}
        self.urls = []

    def get(self, url, **kwargs):
        assert kwargs["allow_redirects"] is False
        self.urls.append(url)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make(script, delay=3.0):
    sleeps = []
    f = PoliteFetcher(delay=delay, session=FakeSession(script), sleep=sleeps.append, retry_wait=15)
    return f, sleeps


def test_redirects_are_followed_one_polite_request_at_a_time():
    f, sleeps = make([FakeHTTP(302, {"Location": "/doc.pdf"}), FakeHTTP(200, {"Content-Type": "application/pdf"}, b"%PDF-1.6")])
    r = f.get("https://example.gov/download/x")
    assert r.url == "https://example.gov/doc.pdf"
    assert r.redirected_from == ["https://example.gov/download/x"]
    assert r.is_pdf
    assert f.request_count == 2
    assert len(sleeps) == 1 and 2.5 < sleeps[0] <= 3.0  # paused before the redirect hop


def test_timeout_is_retried_once_then_reported():
    f, sleeps = make([requests.Timeout(), requests.Timeout()])
    with pytest.raises(FetchError, match="timed out"):
        f.get("https://example.gov/slow")
    assert f.request_count == 2
    assert 15 in sleeps


def test_server_error_then_success():
    f, _ = make([FakeHTTP(503), FakeHTTP(200, {"Content-Type": "text/html"}, b"<html></html>")])
    assert f.get("https://example.gov/flaky").status == 200


def test_not_found_is_not_retried():
    f, _ = make([FakeHTTP(404)])
    with pytest.raises(FetchError, match="HTTP 404 Not Found"):
        f.get("https://example.gov/missing")
    assert f.request_count == 1
