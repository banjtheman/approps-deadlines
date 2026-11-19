"""Write deadlines as an iCalendar (.ics) file that Outlook, Google Calendar and Apple Calendar can import.

One all-day event per fixed deadline, with how to submit in the description and
a reminder a week before. Relative deadlines (e.g. "seven days after the
hearing") have no date, so they're left out; the web page explains them.
"""

from __future__ import annotations

from datetime import date, timedelta


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Fold to 75-octet lines as RFC 5545 requires (continuations start with a space)."""
    out, current = [], ""
    for ch in line:
        limit = 75 if not out else 74
        if len((current + ch).encode("utf-8")) > limit:
            out.append(current)
            current = ch
        else:
            current += ch
    out.append(current)
    return "\r\n ".join(out)


def build_ics(events: list[dict]) -> str:
    """events: dicts with uid, date (datetime.date), summary, description, url, stamp (datetime.date)."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Civic Tech DC//approps-deadlines//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Appropriations testimony deadlines",
    ]
    for e in events:
        d: date = e["date"]
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e['uid']}",
            f"DTSTAMP:{e['stamp']:%Y%m%d}T000000Z",
            f"DTSTART;VALUE=DATE:{d:%Y%m%d}",
            f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_escape(e['summary'])}",
            f"DESCRIPTION:{_escape(e['description'])}",
            "TRANSP:TRANSPARENT",
        ]
        if e.get("url"):
            lines.append(f"URL:{e['url']}")
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_escape('In one week: ' + e['summary'])}",
            "TRIGGER:-P7D",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
