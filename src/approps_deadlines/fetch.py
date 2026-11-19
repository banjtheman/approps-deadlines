"""A polite HTTP fetcher: one request at a time, with a pause before each one.

Redirects are followed by hand so the pause also applies to every redirect
hop. Network failures become FetchError with a short, human-readable message
that goes straight into the report.
"""

from __future__ import annotations

import http
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin

import requests

USER_AGENT = (
    "approps-deadlines/0.1 (Civic Tech DC volunteer project; "
    "+https://github.com/civictechdc/congressional-tech)"
)

REDIRECT_CODES = {301, 302, 303, 307, 308}


class FetchError(Exception):
    """A request failed: timeout, connection error, HTTP error, or oversize body."""

    def __init__(self, message: str, *, transient: bool = False):
        super().__init__(message)
        self.transient = transient


@dataclass
class Response:
    url: str  # final URL after redirects
    status: int
    headers: dict[str, str]  # lower-cased header names
    body: bytes
    redirected_from: list[str] = field(default_factory=list)

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()

    @property
    def is_pdf(self) -> bool:
        return self.body[:1024].lstrip().startswith(b"%PDF")

    @property
    def looks_like_html(self) -> bool:
        if "html" in self.content_type:
            return True
        head = self.body[:512].lstrip().lower()
        return head.startswith(b"<!doctype html") or head.startswith(b"<html")

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def _status_message(code: int) -> str:
    try:
        return f"HTTP {code} {http.HTTPStatus(code).phrase}"
    except ValueError:
        return f"HTTP {code}"


class PoliteFetcher:
    def __init__(
        self,
        delay: float = 3.0,
        timeout: float = 30.0,
        max_bytes: int = 25_000_000,
        retries: int = 1,
        retry_wait: float = 15.0,
        max_redirects: int = 5,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] | None = None,
    ):
        self.delay = delay
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.retries = retries
        self.retry_wait = retry_wait
        self.max_redirects = max_redirects
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.sleep = sleep
        self.log = log or (lambda _msg: None)
        self.request_count = 0
        self._last_request: float | None = None

    def _pause(self) -> None:
        if self._last_request is None:
            return
        wait = self.delay - (time.monotonic() - self._last_request)
        if wait > 0:
            self.sleep(wait)

    def _request_once(self, url: str) -> tuple[int, dict[str, str], bytes]:
        self._pause()
        self.request_count += 1
        self.log(f"GET {url}")
        try:
            with self.session.get(url, timeout=self.timeout, allow_redirects=False, stream=True) as r:
                body = bytearray()
                for chunk in r.iter_content(64 * 1024):
                    body += chunk
                    if len(body) > self.max_bytes:
                        raise FetchError(f"response from {url} is larger than {self.max_bytes // 1_000_000} MB")
                return r.status_code, {k.lower(): v for k, v in r.headers.items()}, bytes(body)
        except requests.Timeout:
            raise FetchError(f"timed out after {self.timeout:g}s ({url})", transient=True) from None
        except requests.ConnectionError:
            raise FetchError(f"could not connect ({url})", transient=True) from None
        except requests.RequestException as e:
            raise FetchError(f"request failed: {type(e).__name__} ({url})") from None
        finally:
            self._last_request = time.monotonic()

    def _request(self, url: str) -> tuple[int, dict[str, str], bytes]:
        """One request, retried after a longer pause if the failure looks temporary."""
        attempts = self.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                status, headers, body = self._request_once(url)
            except FetchError as e:
                if e.transient and attempt < attempts:
                    self.log(f"  {e}; retrying in {self.retry_wait:g}s")
                    self.sleep(self.retry_wait)
                    continue
                raise
            if status >= 500 and attempt < attempts:
                self.log(f"  {_status_message(status)}; retrying in {self.retry_wait:g}s")
                self.sleep(self.retry_wait)
                continue
            return status, headers, body
        raise AssertionError("unreachable")

    def get(self, url: str) -> Response:
        seen: list[str] = []
        current = url
        for _ in range(self.max_redirects + 1):
            status, headers, body = self._request(current)
            if status in REDIRECT_CODES and headers.get("location"):
                seen.append(current)
                current = urljoin(current, headers["location"])
                continue
            if status >= 400:
                raise FetchError(f"{_status_message(status)} ({current})")
            return Response(url=current, status=status, headers=headers, body=body, redirected_from=seen)
        raise FetchError(f"too many redirects starting at {url}")
