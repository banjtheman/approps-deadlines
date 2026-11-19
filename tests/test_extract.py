"""The text rules against the real FY2027 Senate PDFs (text saved in tests/fixtures)."""

from pathlib import Path

import pytest

from approps_deadlines.crawl import pdf_to_text
from approps_deadlines.extract import extract_fields

from .pdfgen import guidelines_pdf

FIXTURES = Path(__file__).parent / "fixtures"
WORD_WP = "Word or WordPerfect (no PDF)"

# id: (deadline, format, page limit, delivery address, subject line)
EXPECTED = {
    "ag": ("2026-05-20", "", "4", "agri@appro.senate.gov", "FY27 AG OWT"),
    "cjs": ("2026-05-22", WORD_WP, "4", "cjs@appro.senate.gov", "FY27 CJS OWT"),
    "defense": ("2026-06-18", WORD_WP, "4", "def@appro.senate.gov", "FY27 DEF OWT"),
    "energy-water": ("2026-04-23", WORD_WP, "4", "ew@appro.senate.gov", "FY27 EW OWT"),
    "fsgg": ("2026-05-29", "Word (no PDF)", "4", "finsec@appro.senate.gov", "FY27 FSGG OWT"),
    "homeland": ("2026-05-24", WORD_WP, "4", "homelandsec@appro.senate.gov", "FY27 Homeland OWT"),
    "interior": ("2026-05-15", WORD_WP, "4", "int@appro.senate.gov", "FY27 Int OWT"),
    "labor-hhs": ("2026-05-22", "Word (no PDF)", "4", "lhhs@appro.senate.gov", "FY27 OWT"),
    "leg-branch": ("2026-05-15", WORD_WP, "4", "legislativebranch@appro.senate.gov", "FY27 LegisBranch OWT"),
    "milcon-va": ("2026-05-15", WORD_WP, "4", "", ""),
    "sfops": ("2026-06-30", "Word (no PDF)", "4", "SFOPS@appro.senate.gov", "FY27 SFOPS OWT"),
    "thud": ("", WORD_WP, "4", "thud@appro.senate.gov", "FY27 THUD Outside Witness Testimony – [Department]"),
}


@pytest.mark.parametrize("sub_id", sorted(EXPECTED))
def test_fields_from_real_fy27_pdfs(sub_id):
    f = extract_fields((FIXTURES / f"senate-{sub_id}.txt").read_text())
    deadline, fmt, pages, address, subject = EXPECTED[sub_id]
    assert f.fiscal_year == "FY2027"
    assert f.deadline == deadline
    assert f.format == fmt
    assert f.page_limit == pages
    assert f.delivery_address == address
    assert f.subject_line == subject
    # Every blank tracked field is listed for a human to check.
    for name, value in [("deadline", deadline), ("format", fmt), ("delivery address", address), ("subject line", subject)]:
        assert bool(value) or any(r.startswith(name) for r in f.needs_review), name


def test_relative_deadline_is_left_blank_and_explained():
    f = extract_fields((FIXTURES / "senate-thud.txt").read_text())
    assert f.deadline == ""
    assert "seven days after" in f.deadline_text
    assert any("relative" in n for n in f.notes)


def test_member_office_policy_is_noted():
    f = extract_fields((FIXTURES / "senate-milcon-va.txt").read_text())
    assert any(n.startswith("Policy: For outside") for n in f.notes)


def test_several_dates_are_flagged_not_guessed():
    text = (
        "Fiscal Year 2028 Appropriations. Testimony must be received no later than May 1, 2027. "
        "Requests to testify in person must be received no later than April 10, 2027."
    )
    f = extract_fields(text)
    assert f.deadline == ""
    assert any("several dates" in r for r in f.needs_review)


def test_empty_text_is_flagged():
    f = extract_fields("   ")
    assert f.needs_review == ["PDF has no readable text (scanned image?)"]


def test_generated_pdf_round_trip():
    f = extract_fields(pdf_to_text(guidelines_pdf(deadline="Monday, March 1, 2027", fy="2028")))
    assert (f.fiscal_year, f.deadline, f.subject_line) == ("FY2028", "2027-03-01", "FY28 LegisBranch OWT")
    assert f.format == WORD_WP
