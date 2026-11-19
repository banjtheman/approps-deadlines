"""The staff-facing page and the calendar file, built from saved crawl results."""

import json
from datetime import date

import pytest

from approps_deadlines.fetch import FetchError
from approps_deadlines.ics import build_ics
from approps_deadlines.site import build_site, calendar_events, cycle_fy, load_items, render_page
from approps_deadlines.sources import Subcommittee
from approps_deadlines.tracker import check_all

from .test_crawl import PAGE, SUB, T1, T2, FakeSite, healthy_site, paths  # noqa: F401  (paths is a fixture)


def build(paths, today, preview=False):
    page = build_site([SUB], paths, paths.output_csv.parent, today, preview=preview)
    # read_bytes: read_text would turn the calendar's required CRLF line endings into \n
    return page.read_text(), (paths.output_csv.parent / "deadlines.ics").read_bytes().decode("utf-8")


def test_open_deadline_shows_in_coming_up_with_how_to_send(paths):
    check_all([SUB], healthy_site(), paths, T1)
    html, _ = build(paths, date(2026, 5, 1))
    coming_up = html.split('id="upcoming"')[1].split("</section>")[0]
    assert "Legislative Branch" in coming_up
    assert "In 14 days" in coming_up
    assert "Due by close of business, Friday, May 15, 2026" in coming_up
    assert 'href="mailto:legislativebranch@appro.senate.gov?subject=FY27%20LegisBranch%20OWT"' in coming_up
    assert "Word or WordPerfect, no PDFs · 4 pages max" in coming_up


def test_after_the_deadline_the_page_says_nothing_is_open(paths):
    check_all([SUB], healthy_site(), paths, T1)
    html, _ = build(paths, date(2026, 11, 18))
    assert "Nothing is open right now." in html
    assert "All 1 subcommittee pages checked on November 16, 2026. Nothing new this week." in html
    assert 'class="badge">Closed' in html
    assert 'href="deadlines.ics"' not in html  # no calendar button when nothing is open


def test_calendar_leaves_out_deadlines_that_have_passed(paths):
    check_all([SUB], healthy_site(), paths, T1)
    _, ics = build(paths, date(2026, 5, 16))
    assert "BEGIN:VEVENT" not in ics


def test_newest_fiscal_year_is_the_cycle(paths):
    other = Subcommittee("senate-defense", "Senate", "Defense", PAGE.replace("legislative-branch", "defense"), "Defense")
    check_all([SUB], healthy_site(), paths, T1)
    state = json.loads(paths.state.read_text())
    newer = json.loads(json.dumps(state[SUB.id]))
    newer["fields"].update(fiscal_year="FY2028", deadline="2027-05-14")
    old_rolling = json.loads(json.dumps(state[SUB.id]))
    old_rolling["fields"].update(fiscal_year="FY2027", deadline="")
    state[SUB.id], state[other.id] = old_rolling, newer
    paths.state.write_text(json.dumps(state))
    items, _ = load_items([SUB, other], paths)
    assert cycle_fy(items) == "FY2028"
    html = render_page(items, [], date(2027, 4, 1))
    coming_up = html.split('id="upcoming"')[1].split("</section>")[0]
    assert "Defense" in coming_up
    assert "Legislative Branch" not in coming_up  # last year's rolling rule isn't "coming up"
    assert "FY2028 at a glance" in html


def test_broken_page_is_flagged_at_the_top(paths):
    check_all([SUB], healthy_site(), paths, T1)
    check_all([SUB], FakeSite({PAGE: FetchError("HTTP 503 Service Unavailable (page)")}), paths, T2)
    html, _ = build(paths, date(2026, 5, 1))
    assert "Couldn’t check 1 page this week" in html
    assert "0 of 1 subcommittee pages checked" in html
    assert "Due by close of business, Friday, May 15, 2026" in html  # last good guidance still shown


def test_text_from_the_pdf_is_escaped(paths):
    check_all([SUB], healthy_site(), paths, T1)
    state = json.loads(paths.state.read_text())
    state[SUB.id]["fields"]["subject_line"] = '<script>alert("x")</script>'
    paths.state.write_text(json.dumps(state))
    html, _ = build(paths, date(2026, 5, 1))
    assert "<script>alert" not in html
    assert "&lt;script&gt;alert" in html


def test_calendar_has_one_event_per_fixed_deadline(paths):
    check_all([SUB], healthy_site(), paths, T1)
    _, ics = build(paths, date(2026, 5, 1))
    assert ics.count("BEGIN:VEVENT") == 1
    assert "DTSTART;VALUE=DATE:20260515" in ics
    assert "TRIGGER:-P7D" in ics
    lines = ics.split("\r\n")
    assert all(len(line.encode("utf-8")) <= 75 for line in lines)
    unfolded = ics.replace("\r\n ", "")
    assert "Email to: legislativebranch@appro.senate.gov" in unfolded
    assert "Due by close of business\\, Friday\\, May 15\\, 2026." in unfolded


def test_preview_copies_the_csv_it_links_to(paths, tmp_path):
    check_all([SUB], healthy_site(), paths, T1)
    page = build_site([SUB], paths, tmp_path / "preview", date(2026, 5, 1), preview=True)
    assert (tmp_path / "preview" / "tracker.csv").exists()
    assert "Preview." in page.read_text()


@pytest.mark.parametrize("text", ["Café, “quotes” and a very long subject line " * 3])
def test_ics_folds_long_unicode_lines(text):
    ics = build_ics([{"uid": "x@y", "date": date(2026, 5, 1), "stamp": date(2026, 4, 1),
                      "summary": text, "description": text, "url": ""}])
    assert all(len(line.encode("utf-8")) <= 75 for line in ics.split("\r\n"))
    assert text.replace(",", "\\,") in ics.replace("\r\n ", "")


def test_rolling_deadline_has_no_calendar_event(paths):
    check_all([SUB], healthy_site(), paths, T1)
    state = json.loads(paths.state.read_text())
    state[SUB.id]["fields"]["deadline"] = ""
    paths.state.write_text(json.dumps(state))
    items, _ = load_items([SUB], paths)
    assert calendar_events(items, date(2026, 5, 1)) == []
