"""Crawl, change detection and broken-page handling, against a fake website."""

import csv
import json
from datetime import datetime, timezone

import pytest

from approps_deadlines.fetch import FetchError, Response
from approps_deadlines.sources import Subcommittee
from approps_deadlines.tracker import TRACKER_COLUMNS, Paths, check_all, render_report

from .pdfgen import guidelines_pdf

SITE = "https://www.appropriations.senate.gov"
PAGE = f"{SITE}/subcommittees/legislative-branch"
DOWNLOAD = f"{SITE}/download/owt_guidelines_leg_branch_fy_27"
PDF_URL = f"{SITE}/imo/media/doc/owt_guidelines_leg_branch_fy_27.pdf"
SUB = Subcommittee("senate-leg-branch", "Senate", "Legislative Branch", PAGE)

T1 = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
T3 = datetime(2026, 11, 30, 9, 0, tzinfo=timezone.utc)


def page_html(href="../../../download/owt_guidelines_leg_branch_fy_27", news="Senate passes bill", link_html=None):
    link = link_html or f'<a class="btn btn-lg testimony" href="{href}" target="_blank">Submit Written Testimony</a>'
    return f"""<html><body>
      <h1 class="main_page_title">Legislative Branch</h1>
      <div class="tab-content">
        <div id="sam-main" class="amend-textarea written-testimony">
          <div class="submit-written-testimony"><p>{link}</p></div>
        </div>
        <div id="news"><a href="/news/1">{news}</a></div>
      </div></body></html>"""


def html(url, body, **headers):
    return Response(url=url, status=200, headers={"content-type": "text/html", **headers}, body=body.encode())


def pdf(url, data, redirected_from=()):
    headers = {"content-type": "application/pdf", "last-modified": "Tue, 21 Apr 2026 17:10:00 GMT"}
    return Response(url=url, status=200, headers=headers, body=data, redirected_from=list(redirected_from))


class FakeSite:
    """Maps URLs to responses (or exceptions). Unknown URLs are a 404."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        r = self.routes.get(url)
        if r is None:
            raise FetchError(f"HTTP 404 Not Found ({url})")
        if isinstance(r, Exception):
            raise r
        return r


def healthy_site(pdf_bytes=None, news="Senate passes bill"):
    return FakeSite(
        {
            PAGE: html(PAGE, page_html(news=news)),
            # The Senate's intermediate page: a 200 with a Refresh header, not a redirect.
            DOWNLOAD: html(DOWNLOAD, "<html><body>Your download will begin shortly.</body></html>",
                           refresh=f"2; url={DOWNLOAD}&download=1"),
            f"{DOWNLOAD}&download=1": pdf(PDF_URL, pdf_bytes or guidelines_pdf(), [f"{DOWNLOAD}&download=1"]),
        }
    )


@pytest.fixture
def paths(tmp_path):
    return Paths(data_dir=tmp_path / "data", output_csv=tmp_path / "output" / "tracker.csv", downloads_dir=tmp_path / "dl")


def run(site, paths, now):
    return check_all([SUB], site, paths, now)[0]


def read_csv(path):
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def test_first_run_is_baseline_then_unchanged(paths):
    first = run(healthy_site(), paths, T1)
    assert (first.status, first.updated) == ("ok", "first check")
    assert first.entry["pdf_url"] == PDF_URL
    assert first.entry["hops"] == [DOWNLOAD, f"{DOWNLOAD}&download=1", PDF_URL]
    assert first.entry["fields"]["deadline"] == "2026-05-15"

    second = run(healthy_site(), paths, T2)
    assert (second.status, second.updated, second.what_changed) == ("ok", "no", [])
    assert second.entry["last_changed"] == "2026-11-16"  # unchanged, so still the first-seen date

    history = read_csv(paths.history)
    assert [h["updated"] for h in history] == ["first check", "no"]
    assert (paths.text_dir / "senate-leg-branch.txt").exists()


def test_tracker_csv_has_tracker_columns_first(paths):
    run(healthy_site(), paths, T1)
    with paths.output_csv.open(newline="") as fh:
        header = next(csv.reader(fh))
    assert header[:9] == [
        "Subcommittee", "Fiscal year", "Deadline", "Format", "Page limit",
        "Delivery address", "Subject line", "Source URL", "Last changed",
    ]
    assert header == TRACKER_COLUMNS
    row = read_csv(paths.output_csv)[0]
    assert row["Subject line"] == "FY27 LegisBranch OWT"
    assert row["Source URL"] == PDF_URL
    assert row["PDF last modified (server)"] == "2026-04-21"


def test_new_pdf_is_reported_as_updated(paths):
    run(healthy_site(), paths, T1)
    changed = run(healthy_site(pdf_bytes=guidelines_pdf(deadline="Friday, May 14, 2027", fy="2028")), paths, T2)
    assert changed.updated == "yes"
    assert changed.what_changed == ["PDF content"]
    assert changed.entry["fields"]["fiscal_year"] == "FY2028"
    assert changed.entry["fields"]["deadline"] == "2027-05-14"
    assert changed.entry["last_changed"] == "2026-11-23"


def test_news_on_the_page_is_not_a_change(paths):
    run(healthy_site(news="Old news"), paths, T1)
    assert run(healthy_site(news="Brand new press release"), paths, T2).updated == "no"


def test_new_link_target_is_reported(paths):
    run(healthy_site(), paths, T1)
    site = healthy_site()
    new_download = f"{SITE}/download/owt_guidelines_leg_branch_fy_28"
    site.routes[PAGE] = html(PAGE, page_html(href=new_download))
    site.routes[new_download] = pdf(f"{SITE}/imo/media/doc/fy28.pdf", guidelines_pdf(fy="2028"))
    row = run(site, paths, T2)
    assert row.updated == "yes"
    assert "testimony section on the page" in row.what_changed
    assert any(c.startswith("testimony link") for c in row.what_changed)
    assert any(c.startswith("PDF address") for c in row.what_changed)


BROKEN_CASES = {
    "page down": ({PAGE: FetchError("HTTP 503 Service Unavailable (page)")}, "subcommittee page", "503"),
    "page timeout": ({PAGE: FetchError("timed out after 30s (page)", transient=True)}, "subcommittee page", "timed out"),
    "no testimony link": ({PAGE: html(PAGE, "<html><body><p>Under construction</p></body></html>")}, "testimony link", "no 'Submit"),
    "intermediate page dead end": (
        {DOWNLOAD: html(DOWNLOAD, "<html><body>Sorry, file not found.</body></html>")}, "intermediate page", "no link to a PDF"
    ),
    "pdf link 404": ({f"{DOWNLOAD}&download=1": None}, "PDF", "404"),
    "html instead of pdf": (
        {f"{DOWNLOAD}&download=1": Response(PDF_URL, 200, {"content-type": "application/msword"}, b"\xd0\xcf\x11\xe0")},
        "PDF", "expected a PDF",
    ),
    "damaged pdf": ({f"{DOWNLOAD}&download=1": pdf(PDF_URL, b"%PDF-1.4 this is not really a pdf")}, "PDF", "could not be read"),
}


@pytest.mark.parametrize("case", sorted(BROKEN_CASES))
def test_broken_pages_are_reported_not_fatal(paths, case):
    overrides, stage, words = BROKEN_CASES[case]
    run(healthy_site(), paths, T1)
    state_before = json.loads(paths.state.read_text())

    site = healthy_site()
    for url, resp in overrides.items():
        if resp is None:
            site.routes.pop(url)
        else:
            site.routes[url] = resp
    broken = run(site, paths, T2)
    assert (broken.status, broken.updated) == ("broken", "unknown")
    assert broken.problem.startswith(stage + ":")
    assert words in broken.problem
    # The last good fingerprint is kept...
    assert json.loads(paths.state.read_text()) == state_before
    # ...so when the page recovers unchanged, it reads "no", not a false "yes".
    assert run(healthy_site(), paths, T3).updated == "no"

    rows = read_csv(paths.output_csv)
    assert rows[0]["Status"] == "ok"
    history = read_csv(paths.history)
    assert [h["status"] for h in history] == ["ok", "broken", "ok"]


def test_broken_row_keeps_last_good_fields_in_csv(paths):
    run(healthy_site(), paths, T1)
    run(FakeSite({PAGE: FetchError("HTTP 500 Internal Server Error (page)")}), paths, T2)
    row = read_csv(paths.output_csv)[0]
    assert row["Status"] == "broken"
    assert row["Deadline"] == "2026-05-15"
    assert "last good check, 2026-11-16" in row["Problem"]


def test_broken_on_very_first_run(paths):
    row = run(FakeSite({}), paths, T1)
    assert (row.status, row.updated) == ("broken", "unknown")
    report = render_report([row], T1)
    assert "## Broken pages (1)" in report
    assert "Legislative Branch" in report


def test_direct_pdf_link_like_defense(paths):
    site = FakeSite({PAGE: html(PAGE, page_html(href=PDF_URL)), PDF_URL: pdf(PDF_URL, guidelines_pdf())})
    row = run(site, paths, T1)
    assert row.status == "ok"
    assert row.entry["hops"] == [PDF_URL]


def test_class_on_span_like_milcon_va(paths):
    link = '<a href="../../../download/owt_guidelines_leg_branch_fy_27"><span class="btn testimony">Submit Written Testimony</span></a>'
    site = healthy_site()
    site.routes[PAGE] = html(PAGE, page_html(link_html=link))
    assert run(site, paths, T1).status == "ok"


def test_meta_refresh_intermediate_page(paths):
    site = healthy_site()
    site.routes[DOWNLOAD] = html(
        DOWNLOAD, f'<html><head><meta http-equiv="refresh" content="0; url={DOWNLOAD}&download=1"></head></html>'
    )
    assert run(site, paths, T1).status == "ok"


def test_only_checks_some_and_keeps_the_rest(paths):
    other = Subcommittee("senate-defense", "Senate", "Defense", f"{SITE}/subcommittees/defense")
    rows = check_all([SUB, other], healthy_site(), paths, T1, only={SUB.id})
    assert [(r.sub.id, r.updated) for r in rows] == [("senate-leg-branch", "first check"), ("senate-defense", "not checked")]
    assert len(read_csv(paths.output_csv)) == 2
    assert len(read_csv(paths.history)) == 1
