"""Pull tracker fields out of a guidelines PDF's text with plain text rules.

Rules come from reading all 12 FY2027 Senate PDFs. The policy is: if a rule
isn't sure, leave the field blank and say so in `needs_review`. A blank cell
someone checks is better than a wrong date someone trusts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_RE = re.compile(rf"\b({MONTHS})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.I)
MONTH_NUM = {m.lower(): i for i, m in enumerate(MONTHS.split("|"), 1)}

DEADLINE_CUE = re.compile(
    r"no later than|must be received|received by|submitted by|due (?:by|on)|deadline (?:is|for|of)", re.I
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
SUBJECT_RE = re.compile(r"subject(?:\s+line)?\s*(?:of|:)?\s*[\"“]([^\"”]{2,150})[\"”]", re.I)
PAGE_LIMIT_RES = [
    re.compile(r"maximum of\s+(?:[a-z]+\s+)?\(?(\d{1,2})\)?\s+pages?", re.I),
    re.compile(r"(?:not (?:to )?exceed|no (?:more|longer) than|limited to)\s+(?:[a-z]+\s+)?\(?(\d{1,2})\)?\s+pages?", re.I),
    re.compile(r"\b(\d{1,2})[- ]page (?:limit|maximum)", re.I),
]

TRACKED = ["fiscal_year", "deadline", "format", "page_limit", "delivery_address", "subject_line"]


@dataclass
class Fields:
    fiscal_year: str = ""  # "FY2027"
    deadline: str = ""  # ISO date "2026-05-15", blank if not one clear date
    deadline_text: str = ""  # the sentence the deadline came from, for a human to check
    format: str = ""  # accepted file types, e.g. "Word or WordPerfect (no PDF)"
    page_limit: str = ""  # "4"
    delivery_address: str = ""  # email address(es)
    subject_line: str = ""
    notes: list[str] = field(default_factory=list)
    needs_review: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "fiscal_year": self.fiscal_year,
            "deadline": self.deadline,
            "deadline_text": self.deadline_text,
            "format": self.format,
            "page_limit": self.page_limit,
            "delivery_address": self.delivery_address,
            "subject_line": self.subject_line,
            "notes": list(self.notes),
            "needs_review": list(self.needs_review),
        }


def normalize(text: str) -> str:
    text = " ".join(text.split())
    # "5 p.m." would otherwise end a sentence early.
    text = re.sub(r"\b([ap])\.\s?m\.", r"\1m", text, flags=re.I)
    return text


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


_LEADING_JUNK = re.compile(
    r"^(?:GUIDELINES\b|[A-Z][A-Za-z ]{1,14}:|[•▪●\-–]|x(?=\s))\s*"  # headings like "Deadline:", bullets ("x" in one PDF)
)


def _clean_sentence(s: str) -> str:
    s = s.strip()
    while True:
        stripped = _LEADING_JUNK.sub("", s, count=1)
        if stripped == s:
            return s
        s = stripped


def _fiscal_year(text: str) -> str:
    m = re.search(r"Fiscal Year\s+(\d{4})", text, re.I)
    if m:
        return f"FY{m.group(1)}"
    m = re.search(r"\bFY\s?(\d{4}|\d{2})\b", text)
    if m:
        yy = m.group(1)
        return f"FY{yy if len(yy) == 4 else '20' + yy}"
    return ""


def _deadline(sents: list[str], f: Fields) -> None:
    cue_sents = [s for s in sents if DEADLINE_CUE.search(s)]
    dates: list[str] = []
    for s in cue_sents:
        for month, day, year in DATE_RE.findall(s):
            iso = f"{int(year):04d}-{MONTH_NUM[month.lower()]:02d}-{int(day):02d}"
            if iso not in dates:
                dates.append(iso)
    dated = [s for s in cue_sents if DATE_RE.search(s)]
    f.deadline_text = " | ".join(_clean_sentence(s) for s in (dated or cue_sents))
    if len(dates) == 1:
        f.deadline = dates[0]
    elif len(dates) > 1:
        f.needs_review.append(f"deadline: several dates found ({', '.join(dates)})")
    elif cue_sents:
        f.needs_review.append("deadline: not a fixed date, see deadline text")
        f.notes.append("Deadline is relative, not a calendar date: " + _clean_sentence(cue_sents[0]))


def _format(sents: list[str], text: str) -> str:
    accept = next(
        (s for s in sents if re.search(r"\baccept\b", s, re.I) and re.search(r"\bWord\b|\bPDF\b|\.docx?\b", s, re.I)),
        None,
    )
    no_pdf = bool(re.search(r"do not send pdf|pdfs? (?:files )?will not be accepted|not accept pdf", text, re.I))
    if not accept:
        return ""
    types = []
    if re.search(r"\bWord\b(?!\s?Perfect)|\.docx?\b", accept, re.I):
        types.append("Word")
    if re.search(r"Word\s?Perfect", accept, re.I):
        types.append("WordPerfect")
    if re.search(r"\bPDF\b", accept, re.I) and not no_pdf:
        types.append("PDF")
    if not types:
        return ""
    out = " or ".join(types)
    if no_pdf:
        out += " (no PDF)"
    return out


def _page_limit(text: str) -> tuple[str, list[str]]:
    found: list[str] = []
    for rx in PAGE_LIMIT_RES:
        for n in rx.findall(text):
            if n not in found:
                found.append(n)
    if len(found) == 1:
        return found[0], []
    if len(found) > 1:
        return "", [f"page limit: several numbers found ({', '.join(found)})"]
    return "", []


def extract_fields(raw_text: str) -> Fields:
    f = Fields()
    text = normalize(raw_text)
    if not text:
        f.needs_review.append("PDF has no readable text (scanned image?)")
        return f
    sents = sentences(text)

    f.fiscal_year = _fiscal_year(text)
    _deadline(sents, f)
    f.format = _format(sents, text)
    f.page_limit, page_issues = _page_limit(text)
    f.needs_review.extend(page_issues)

    emails = list(dict.fromkeys(EMAIL_RE.findall(text)))
    f.delivery_address = "; ".join(emails)

    m = SUBJECT_RE.search(text)
    if m:
        f.subject_line = normalize(m.group(1)).rstrip(" .")

    if re.search(r"not scheduled any (?:outside|public) witness hearings?", text, re.I):
        f.notes.append("No outside-witness hearing scheduled; written testimony only.")
    for s in sents:
        if re.search(r"at the request of|will not be considered", s, re.I):
            f.notes.append("Policy: " + _clean_sentence(s))
            break

    for name in TRACKED:
        if not getattr(f, name) and not any(r.startswith(name.replace("_", " ")) for r in f.needs_review):
            f.needs_review.append(f"{name.replace('_', ' ')}: not found in PDF")
    return f
