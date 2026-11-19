"""Follow one subcommittee page to its testimony-guidelines PDF.

    subcommittee page
      -> "Submit Written Testimony" link (inside the written-testimony block)
      -> zero or more intermediate pages (Refresh header, meta refresh, or a PDF link)
      -> the PDF

crawl_one() never raises. Anything that goes wrong comes back as
CrawlResult(ok=False, stage=..., error=...) so one broken page can't stop the run.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urljoin

from pypdf import PdfReader

from .fetch import FetchError, Response
from .sources import Subcommittee

logging.getLogger("pypdf").setLevel(logging.ERROR)

MAX_INTERMEDIATE_PAGES = 3


class Fetcher(Protocol):
    def get(self, url: str) -> Response: ...


class CrawlProblem(Exception):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


@dataclass
class CrawlResult:
    sub: Subcommittee
    ok: bool = False
    stage: str = ""  # where it broke: "subcommittee page", "testimony link", "intermediate page", "PDF"
    error: str = ""
    section_text: str = ""  # normalized text + links of the testimony block (what we fingerprint)
    section_found: bool = False
    link_url: str = ""
    hops: list[str] = field(default_factory=list)  # every URL visited after the subcommittee page
    pdf_url: str = ""
    pdf_bytes: bytes = b""
    pdf_text: str = ""
    pdf_last_modified: str = ""  # YYYY-MM-DD from the server's Last-Modified header, if any


def normalize_space(s: str) -> str:
    return " ".join(s.split())


class _PageParser(HTMLParser):
    """Collects the written-testimony block, every link, and any meta refresh."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.section_depth = 0
        self.section_done = False
        self.section_found = False
        self.section_text: list[str] = []
        self.section_links: list[tuple[str, str]] = []
        self.all_links: list[tuple[str, str]] = []
        self.meta_refresh: str | None = None
        self._a: tuple[str | None, list[str], bool] | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "div":
            if self.section_depth:
                self.section_depth += 1
            elif not self.section_done and "written-testimony" in a.get("class", ""):
                self.section_depth = 1
                self.section_found = True
        elif tag == "a":
            self._a = (a.get("href") or None, [], self.section_depth > 0)
        elif tag == "meta" and a.get("http-equiv", "").lower() == "refresh":
            self.meta_refresh = a.get("content")

    def handle_endtag(self, tag):
        if tag == "div" and self.section_depth:
            self.section_depth -= 1
            if self.section_depth == 0:
                self.section_done = True
        elif tag == "a" and self._a is not None:
            href, parts, in_section = self._a
            text = normalize_space("".join(parts))
            if href and not href.startswith(("#", "javascript:", "mailto:")):
                self.all_links.append((href, text))
                if in_section:
                    self.section_links.append((href, text))
            self._a = None

    def handle_data(self, data):
        if self.section_depth:
            self.section_text.append(data)
        if self._a is not None:
            self._a[1].append(data)


def parse_page(html: str) -> _PageParser:
    p = _PageParser()
    p.feed(html)
    p.close()
    return p


_BUTTON_TEXT = re.compile(r"\b(?:written|submit)\s+testimony\b", re.I)


def find_testimony_link(html: str, base_url: str) -> tuple[str | None, str, bool]:
    """Return (absolute link URL or None, fingerprint text of the block, block found?)."""
    p = parse_page(html)
    links = p.section_links
    if not links:
        # The block's markup changed or is missing: fall back to a link that reads like the
        # button ("Submit Written Testimony"). Plain "testimony" is too loose; news and hearing
        # links use it, and following one would report the wrong PDF as ok.
        links = [(h, t) for h, t in p.all_links if _BUTTON_TEXT.search(t)]
    preferred = [(h, t) for h, t in links if "testimony" in t.lower()] or links
    link = urljoin(base_url, preferred[0][0]) if preferred else None

    block_text = normalize_space(" ".join(p.section_text)) if p.section_found else ""
    hrefs = sorted({urljoin(base_url, h) for h, _ in links})
    fingerprint_text = "\n".join([block_text, *hrefs]).strip()
    return link, fingerprint_text, p.section_found


_REFRESH_URL = re.compile(r"url\s*=\s*['\"]?([^'\";]+)", re.I)


def find_next_hop(resp: Response) -> str | None:
    """On an intermediate page, find where it sends you next."""
    p = parse_page(resp.text())
    for refresh in (resp.headers.get("refresh"), p.meta_refresh):
        if refresh:
            m = _REFRESH_URL.search(refresh)
            if m:
                return urljoin(resp.url, m.group(1).strip())
    for href, _text in p.all_links:
        if re.search(r"\.pdf($|[?#])", href, re.I) or "download=1" in href:
            return urljoin(resp.url, href)
    return None


def follow_to_pdf(url: str, fetcher: Fetcher, hops: list[str]) -> Response:
    current = url
    seen: set[str] = set()
    for _ in range(MAX_INTERMEDIATE_PAGES + 1):
        if current in seen:
            raise CrawlProblem("intermediate page", f"{current} points back to itself instead of to a PDF")
        seen.add(current)
        try:
            resp = fetcher.get(current)
        except FetchError as e:
            raise CrawlProblem("PDF" if hops else "testimony link", str(e)) from None
        hops.extend(resp.redirected_from)
        hops.append(resp.url)
        if resp.is_pdf:
            return resp
        if resp.looks_like_html:
            nxt = find_next_hop(resp)
            if not nxt:
                raise CrawlProblem("intermediate page", f"{resp.url} has no link to a PDF")
            current = nxt
            continue
        kind = resp.content_type or "an unknown file type"
        raise CrawlProblem("PDF", f"expected a PDF but got {kind} from {resp.url}")
    raise CrawlProblem("intermediate page", f"no PDF after {MAX_INTERMEDIATE_PAGES} intermediate pages from {url}")


def pdf_to_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _last_modified(resp: Response) -> str:
    raw = resp.headers.get("last-modified")
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        return ""


def crawl_one(sub: Subcommittee, fetcher: Fetcher) -> CrawlResult:
    result = CrawlResult(sub=sub)
    try:
        try:
            page = fetcher.get(sub.page_url)
        except FetchError as e:
            raise CrawlProblem("subcommittee page", str(e)) from None
        if not page.looks_like_html:
            raise CrawlProblem("subcommittee page", f"expected a web page but got {page.content_type or 'unknown content'}")

        link, section_text, found = find_testimony_link(page.text(), page.url)
        result.section_text, result.section_found = section_text, found
        if not link:
            raise CrawlProblem("testimony link", "no 'Submit Written Testimony' link on the page")
        result.link_url = link

        pdf = follow_to_pdf(link, fetcher, result.hops)
        result.pdf_url = pdf.url
        result.pdf_bytes = pdf.body
        result.pdf_last_modified = _last_modified(pdf)
        try:
            result.pdf_text = pdf_to_text(pdf.body)
        except Exception as e:  # pypdf raises many types on damaged files
            raise CrawlProblem("PDF", f"PDF could not be read ({type(e).__name__}: {e})") from None
        result.ok = True
    except CrawlProblem as e:
        result.stage, result.error = e.stage, str(e)
    except Exception as e:  # never let one subcommittee crash the run
        result.stage, result.error = "unexpected", f"{type(e).__name__}: {e}"
    return result
