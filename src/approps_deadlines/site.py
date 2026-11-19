"""Build the page staffers read (output/index.html) and the calendar file (output/deadlines.ics).

Who it's for: congressional staff and the people who brief them (AGI keeps this
tracker by hand today). The one question it answers: what testimony is due
soon, and exactly how do I send it?

The page is one self-contained HTML file with no build step and no server. It
is made from what the crawler saved (data/state.json and data/history.csv),
so it can be rebuilt at any time with `approps-deadlines --page-only`.
Everything that came from a PDF is HTML-escaped before it goes on the page.

Design notes (frontend-design, Hallmark and ui-ux-pro-max guidance):
editorial "docket" look on paper, Public Sans (the U.S. government's typeface)
for text and Newsreader for headings (system fonts if Google Fonts can't load). Mobile-first,
OKLCH colour tokens, visible focus rings, skip link, 44px touch targets on
touch screens, no decorative motion, and text alternatives for the timeline.
"""

from __future__ import annotations

import csv
import html
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from .ics import build_ics
from .sources import Subcommittee
from .tracker import Paths, load_state

PROPOSAL_URL = "https://civictechdc.github.io/congressional-tech/proposals/IV.3/"


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


ASSETS = Path(__file__).parent / "assets"

# Small line icons (24px grid, drawn with currentColor) so labels carry a picture, not just a word.
ICONS = {
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3.5 6.5 12 13l8.5-6.5"/>',
    "subject": '<path d="M4 7h16M4 12h11M4 17h7"/>',
    "file": '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/>',
    "person": '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.6-4 4.6-6 8-6s6.4 2 8 6"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    "alert": '<path d="M12 3.5 21.5 20h-19z"/><path d="M12 10v4.5M12 17.5v.01"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
}


def icon(name: str) -> str:
    return f'<svg class="ic" viewBox="0 0 24 24" aria-hidden="true" focusable="false">{ICONS[name]}</svg>'


def art(name: str, cls: str) -> str:
    """A decorative illustration from the package's assets folder, inlined so the page stays one file."""
    import base64

    path = ASSETS / f"{name}.webp"
    if not path.exists():
        return ""
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f'<img class="{cls}" src="data:image/webp;base64,{data}" alt="" decoding="async">'


# ---------------------------------------------------------------------------
# Data


@dataclass
class Item:
    sub: Subcommittee
    entry: dict  # last good state entry; {} if never read successfully
    status: str  # "ok", "broken", "unchecked"
    updated: str
    problem: str
    checked_at: str

    @property
    def f(self) -> dict:
        return self.entry.get("fields", {})

    @property
    def deadline(self) -> date | None:
        d = self.f.get("deadline")
        return date.fromisoformat(d) if d else None


def read_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_items(subs: list[Subcommittee], paths: Paths) -> tuple[list[Item], list[dict]]:
    state = load_state(paths.state)
    history = read_history(paths.history)
    latest: dict[str, dict] = {}
    for h in history:
        latest[h["subcommittee_id"]] = h
    items = []
    for s in subs:
        h = latest.get(s.id)
        items.append(
            Item(
                sub=s,
                entry=state.get(s.id, {}),
                status=h["status"] if h else "unchecked",
                updated=h["updated"] if h else "",
                problem=h["problem"] if h else "",
                checked_at=h["checked_at"] if h else "",
            )
        )
    return items, history


# ---------------------------------------------------------------------------
# Plain-language helpers


def long_date(d: date) -> str:
    return f"{d:%A}, {d:%B} {d.day}, {d.year}"


def short_date(d: date) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def when_label(ts: str) -> str:
    dt = parse_ts(ts)
    if not dt:
        return "Not yet"
    offset = dt.utcoffset()
    zone = "ET" if offset in (timedelta(hours=-5), timedelta(hours=-4)) else ("UTC" if offset == timedelta(0) else "")
    clock = dt.strftime("%I:%M %p").lstrip("0")
    return f"{dt:%b} {dt.day}, {dt.year}, {clock} {zone}".rstrip()


def _pretty_time(text: str) -> str:
    def fix(m: re.Match) -> str:
        minutes = m.group(2)
        return f"{m.group(1)}{':' + minutes if minutes and minutes != '00' else ''} {m.group(3).lower()}.m."

    return re.sub(r"\b(\d{1,2})(?::(\d{2}))?\s?([ap])m\b", fix, text, flags=re.I)


def due_phrase(item: Item) -> str:
    text = item.f.get("deadline_text", "")
    d = item.deadline
    if d:
        if re.search(r"close of business", text, re.I):
            return f"Due by close of business, {long_date(d)}"
        m = re.search(r"\b\d{1,2}(?::\d{2})?\s?[ap]m\b", text, re.I)
        if m:
            return f"Due by {_pretty_time(m.group(0))}, {long_date(d)}"
        return f"Due {long_date(d)}"
    m = re.search(r"received (?:by|no later than) (.+?)\.?(?:\s\||$)", text, re.I)
    if m:
        return "Due by " + _pretty_time(m.group(1))
    return "No deadline found in the PDF. Check the official guidelines."


def policy_plain(item: Item) -> str:
    for n in item.f.get("notes", []):
        if n.startswith("Policy:"):
            if re.search(r"personal office", n, re.I) and re.search(r"Member", n):
                return (
                    "Only through a senator’s office: testimony must come at the request of a member of the "
                    "Senate Appropriations Committee. Testimony sent straight to the subcommittee isn’t considered."
                )
            return n[len("Policy:"):].strip()
    return ""


def plain_notes(item: Item) -> list[str]:
    out = []
    for n in item.f.get("notes", []):
        if n.startswith(("Deadline is relative", "Policy:")):
            continue  # shown in the due line / "Send via" row instead
        if n.startswith("No outside-witness hearing"):
            out.append("No hearing for outside witnesses; written testimony only.")
        else:
            out.append(n)
    return out


def plain_checks(item: Item) -> list[str]:
    f = item.f
    out = []
    for r in f.get("needs_review", []):
        if r.startswith("format"):
            out.append("The PDF doesn’t say which file format to send.")
        elif r.startswith("delivery address"):
            if not policy_plain(item):
                out.append("The PDF gives no email address.")
        elif r.startswith("subject line"):
            if f.get("delivery_address"):
                out.append("The PDF gives no subject line.")
        elif r.startswith("deadline: not a fixed date"):
            continue
        elif r.startswith("deadline: several dates"):
            out.append("The PDF lists more than one date. Check which one applies.")
        elif r.startswith("page limit"):
            out.append("The PDF gives no page limit." if "not found" in r else "The PDF gives more than one page limit.")
        elif r.startswith("page layout changed"):
            out.append("The subcommittee page changed its layout; the link was found another way.")
        elif r.startswith("PDF has no readable text"):
            out.append("The PDF has no readable text (it may be a scan). Read it directly.")
        else:
            out.append(r)
    return out


def format_plain(item: Item) -> str:
    parts = []
    fmt = item.f.get("format", "")
    parts.append(fmt.replace(" (no PDF)", ", no PDFs") if fmt else "File format not stated")
    if item.f.get("page_limit"):
        parts.append(f"{item.f['page_limit']} pages max")
    return " · ".join(parts)


def cycle_fy(items: list[Item]) -> str:
    """The newest fiscal year any subcommittee has posted: as soon as one posts FY2028, that's the cycle."""
    years = [i.f.get("fiscal_year", "") for i in items if re.fullmatch(r"FY\d{4}", i.f.get("fiscal_year", ""))]
    return max(years) if years else ""


def countdown_text(d: date, today: date) -> str:
    days = (d - today).days
    if days < 0:
        return "Closed"
    if days == 0:
        return "Due today"
    if days == 1:
        return "Due tomorrow"
    return f"In {days} days"


# ---------------------------------------------------------------------------
# HTML pieces


def copy_button(value: str, what: str) -> str:
    return f'<button type="button" class="copy" data-copy="{esc(value)}" aria-label="Copy {esc(what)}">Copy</button>'


def how_to_submit(item: Item) -> str:
    f = item.f
    email, subject = f.get("delivery_address", ""), f.get("subject_line", "")
    rows = []
    if email:
        first = email.split(";")[0].strip()
        href = f"mailto:{first}" + (f"?subject={quote(subject)}" if subject else "")
        rows.append(("mail", "Email it to", f'<a href="{esc(href)}">{esc(email)}</a>{copy_button(email, "email address")}'))
    else:
        rows.append(("person", "How to send", esc(policy_plain(item) or "No email address in the PDF. See the official guidelines.")))
    if subject:
        rows.append(("subject", "Subject line", f'<span class="subj">{esc(subject)}</span>{copy_button(subject, "subject line")}'))
    rows.append(("file", "Format", esc(format_plain(item))))
    cells = "".join(f"<div><dt>{icon(i)}{k}</dt><dd>{v}</dd></div>" for i, k, v in rows)
    return f'<dl class="how">{cells}</dl>'


def notes_and_checks(item: Item) -> str:
    out = ""
    if item.status == "broken":
        last = item.entry.get("last_ok", "")[:10]
        tail = f" Showing guidance from the last good check ({short_date(date.fromisoformat(last))})." if last else ""
        out += f'<p class="flag flag-warn">{icon("alert")}<span><b>Couldn’t check this week.</b> {esc(item.problem)}.{tail}</span></p>'
    for c in plain_checks(item):
        out += f'<p class="flag">{icon("alert")}<span><b>Please check:</b> {esc(c)}</span></p>'
    for n in plain_notes(item):
        out += f'<p class="note">{esc(n)}</p>'
    return out


def source_links(item: Item) -> str:
    bits = []
    if item.entry.get("pdf_url"):
        bits.append(f'<a href="{esc(item.entry["pdf_url"])}">Official guidelines (PDF)</a>')
    bits.append(f'<a href="{esc(item.sub.page_url)}">Subcommittee page</a>')
    if item.entry.get("last_changed"):
        bits.append(f"Last changed {esc(short_date(date.fromisoformat(item.entry['last_changed'])))}")
    return f'<p class="links">{" · ".join(bits)}</p>'


def date_block(d: date | None, countdown: str) -> str:
    if not d:
        return (
            '<div class="tear tear-rel" aria-hidden="true"><span class="dow">Rolling</span>'
            '<span class="mon">7 days</span><span class="day small">after</span></div>'
            '<p class="count">After each budget hearing</p>'
        )
    return (
        f'<div class="tear" aria-hidden="true"><span class="dow">{d:%a}</span><span class="mon">{d:%b}</span>'
        f'<span class="day">{d.day}</span></div><p class="count" data-count>{esc(countdown)}</p>'
    )


def entry_html(item: Item, today: date) -> str:
    d = item.deadline
    attr = f' data-deadline="{d.isoformat()}"' if d else ""
    return f"""
<article class="entry"{attr}>
  <div class="when">{date_block(d, countdown_text(d, today) if d else "")}</div>
  <div class="what">
    <h3>{esc(item.sub.label)}</h3>
    <p class="due">{esc(due_phrase(item))}</p>
    {how_to_submit(item)}
    {notes_and_checks(item)}
    {source_links(item)}
  </div>
</article>"""


def card_html(item: Item, today: date) -> str:
    d = item.deadline
    if item.status == "unchecked" or not item.entry:
        state = '<span class="badge badge-warn">Not checked yet</span>'
    elif d and d < today:
        state = '<span class="badge">Closed</span>'
    elif d:
        state = '<span class="badge badge-open">Open</span>'
    else:
        state = '<span class="badge">No fixed date</span>'
    attr = f' data-deadline="{d.isoformat()}"' if d else ""
    return f"""
<article class="card"{attr}>
  <header><h3>{esc(item.sub.label)}</h3>{state}</header>
  <p class="full">{esc(item.sub.chamber)} Subcommittee on {esc(item.sub.name)}</p>
  <p class="due">{esc(due_phrase(item)) if item.entry else "No guidance on file yet."}</p>
  {how_to_submit(item) if item.entry else ""}
  {notes_and_checks(item)}
  {source_links(item)}
</article>"""


def timeline(items: list[Item], fy: str, today: date) -> str:
    """The cycle at a glance: an SVG for wide screens plus a text list (phones, screen readers)."""
    fixed = sorted((i for i in items if i.deadline and i.f.get("fiscal_year") == fy), key=lambda i: i.deadline or today)
    rolling = [i for i in items if not i.deadline and i.entry and i.f.get("fiscal_year") == fy]
    if not fixed:
        return ""
    first, last = fixed[0].deadline, fixed[-1].deadline
    assert first and last
    start = first.replace(day=1)
    end = (last.replace(day=28) + timedelta(days=4)).replace(day=1)  # first day of the next month
    rows = fixed + rolling
    W, label_w, right, top, rh = 880, 236, 16, 34, 30
    H = top + len(rows) * rh + 10
    span = (end - start).days

    def x(d: date) -> float:
        return label_w + (d - start).days / span * (W - label_w - right)

    svg = [f'<svg viewBox="0 0 {W} {H}" class="tl-svg" aria-hidden="true" focusable="false">']
    m = start
    while m <= end:
        svg.append(f'<line class="grid" x1="{x(m):.1f}" y1="{top - 12}" x2="{x(m):.1f}" y2="{H - 4}"/>')
        if m < end:
            svg.append(f'<text class="axis" x="{x(m) + 6:.1f}" y="18">{m:%b}</text>')
        m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
    if start <= today <= end:
        svg.append(f'<line class="today" x1="{x(today):.1f}" y1="{top - 14}" x2="{x(today):.1f}" y2="{H - 4}"/>')
        svg.append(f'<text class="today-label" x="{x(today) + 5:.1f}" y="{top - 4}">Today</text>')
    for n, item in enumerate(rows):
        y = top + n * rh + rh / 2
        svg.append(f'<text class="label" x="{label_w - 14}" y="{y + 4:.1f}" text-anchor="end">{esc(item.sub.label)}</text>')
        svg.append(f'<line class="row" x1="{label_w}" y1="{y:.1f}" x2="{W - right}" y2="{y:.1f}"/>')
        if item.deadline:
            closed = item.deadline < today
            svg.append(f'<circle class="dot{" closed" if closed else ""}" cx="{x(item.deadline):.1f}" cy="{y:.1f}" r="7"/>')
            flag = " · check PDF" if plain_checks(item) else ""
            label = f"{item.deadline:%b} {item.deadline.day}{flag}"
            near_edge = x(item.deadline) + 13 + len(label) * 8 > W - right
            lx, anchor = (x(item.deadline) - 13, "end") if near_edge else (x(item.deadline) + 13, "start")
            svg.append(f'<text class="val" x="{lx:.1f}" y="{y + 4:.1f}" text-anchor="{anchor}">{label}</text>')
        else:
            svg.append(f'<text class="val rolling" x="{label_w + 8}" y="{y + 4:.1f}">Rolling: 7 days after each budget hearing</text>')
    svg.append("</svg>")

    li = []
    for item in rows:
        if item.deadline:
            state = "closed" if item.deadline < today else "open"
            li.append(
                f'<li><span class="tl-date">{item.deadline:%b} {item.deadline.day}</span>'
                f'<span class="tl-name">{esc(item.sub.label)}</span><span class="tl-state">{state}</span></li>'
            )
        else:
            li.append(
                f'<li><span class="tl-date">Rolling</span><span class="tl-name">{esc(item.sub.label)}</span>'
                '<span class="tl-state">7 days after its hearing</span></li>'
            )
    legend = (
        '<p class="tl-legend" aria-hidden="true"><span class="key key-open"></span>Open'
        '<span class="key key-closed"></span>Closed</p>'
    )
    return f'{legend}<div class="tl-chart">{"".join(svg)}</div><ol class="tl-list">{"".join(li)}</ol>'


def change_log(history: list[dict], names: dict[str, str], limit: int = 12) -> str:
    runs: dict[str, list[dict]] = {}
    for h in history:
        runs.setdefault(h["checked_at"], []).append(h)
    if not runs:
        return '<p class="muted">No checks have run yet.</p>'
    items = []
    for ts in list(runs)[::-1][:limit]:
        rows = runs[ts]
        ok = [r for r in rows if r["status"] == "ok"]
        yes = [r for r in rows if r["updated"] == "yes"]
        broken = [r for r in rows if r["status"] == "broken"]
        lines = []
        if ok and all(r["updated"] == "first check" for r in ok):
            lines.append(f"Started watching {'all ' if len(ok) == 12 else ''}{len(ok)} subcommittee pages.")
        lines += [f"<b>{esc(names.get(r['subcommittee_id'], r['subcommittee']))}</b> posted new guidance." for r in yes]
        lines += [
            f"<b>{esc(names.get(r['subcommittee_id'], r['subcommittee']))}</b> couldn’t be reached."
            for r in broken
        ]
        if not lines:
            lines.append(f"Checked {'all ' if len(rows) == 12 else ''}{len(rows)}. Nothing new.")
        tag = ""
        if yes:
            tag = '<span class="tag tag-new">New</span>'
        elif broken:
            tag = '<span class="tag tag-warn">Problem</span>'
        items.append(
            f'<li class="logrow"><div class="logwhen"><time datetime="{esc(ts)}">{esc(when_label(ts))}</time>{tag}</div>'
            f'<div>{"<br>".join(lines)}</div></li>'
        )
    more = f'<p class="muted">Showing the latest {limit} checks. The full record is in data/history.csv.</p>' if len(runs) > limit else ""
    return f'<ol class="log">{"".join(items)}</ol>{more}'


# ---------------------------------------------------------------------------
# Page


def status_sentence(total: int, reachable: int, changed: int, last_run: str) -> str:
    dt = parse_ts(last_run)
    if not dt:
        return "Not checked yet."
    when = f"{dt:%B} {dt.day}, {dt.year}"
    pages = f"All {total} subcommittee pages" if reachable == total else f"{reachable} of {total} subcommittee pages"
    news = "Nothing new this week." if not changed else f"{changed} subcommittee{'s' if changed > 1 else ''} posted new guidance this week."
    return f"{pages} checked on {esc(when)}. {news}"


def render_page(items: list[Item], history: list[dict], today: date, preview: bool = False) -> str:
    fy = cycle_fy(items)
    last_run = max((h["checked_at"] for h in history), default="")
    latest = [h for h in history if h["checked_at"] == last_run]
    reachable = sum(1 for h in latest if h["status"] == "ok")
    changed = [i for i in items if i.updated == "yes" and i.checked_at == last_run]
    broken = [i for i in items if i.status == "broken" and i.checked_at == last_run]
    names = {i.sub.id: i.sub.label for i in items}

    open_fixed = sorted((i for i in items if i.deadline and i.deadline >= today), key=lambda i: i.deadline or today)
    rolling = [i for i in items if not i.deadline and i.entry and i.f.get("fiscal_year") == fy]
    upcoming = open_fixed + (rolling if open_fixed else [])

    alerts = ""
    if preview:
        alerts += (
            f'<p class="alert"><span class="sq" aria-hidden="true"></span><b>Preview.</b> '
            f"This page pretends today is {esc(long_date(today))}, using the latest saved guidance.</p>"
        )
    if changed:
        who = ", ".join(esc(i.sub.label) for i in changed)
        alerts += (
            f'<p class="alert alert-new" role="status"><span class="sq" aria-hidden="true"></span>'
            f"<b>New or changed guidance this week:</b> {who}. Details below.</p>"
        )
    if broken:
        who = ", ".join(esc(i.sub.label) for i in broken)
        alerts += (
            f'<p class="alert alert-warn" role="status"><span class="sq" aria-hidden="true"></span>'
            f"<b>Couldn’t check {len(broken)} page{'s' if len(broken) > 1 else ''} this week:</b> {who}. "
            "They show their last known guidance; confirm on the Senate site.</p>"
        )

    if upcoming:
        coming = "".join(entry_html(i, today) for i in upcoming)
        n = len(open_fixed)
        lede = f"{n} open deadline{'s' if n != 1 else ''}, soonest first."
    else:
        past = sorted(d for d in (i.deadline for i in items if i.f.get("fiscal_year") == fy) if d)
        posted = sorted(i.entry.get("pdf_last_modified", "") for i in items if i.entry.get("pdf_last_modified"))
        span = f"They ran from {short_date(past[0])} to {short_date(past[-1])}." if past else ""
        posted_txt = ""
        if posted:
            p = date.fromisoformat(posted[len(posted) // 2])
            posted_txt = f" The current guidance files on the Senate site are dated {p:%B} {p.year}."
        coming = (
            f'<div class="empty">{art("empty", "empty-art")}<div><p class="empty-big">Nothing is open right now.</p>'
            f"<p>All {esc(fy)} deadlines have passed. {esc(span)}{esc(posted_txt)} "
            "When a subcommittee posts new guidance, it shows up here within a week, "
            "flagged at the top of the page.</p></div></div>"
        )
        lede = "No open deadlines."

    cards = "".join(card_html(i, today) for i in items)
    actions = (
        f'<p class="actions"><a class="btn" href="deadlines.ics" download>{icon("calendar")}Add to calendar</a></p>'
        if open_fixed
        else ""
    )
    tl = timeline(items, fy, today)
    as_of_attr = f' data-as-of="{today.isoformat()}"' if preview else ""
    title = "Appropriations testimony deadlines" + (" (preview)" if preview else "")
    cycle_section = (
        f'<section id="cycle" aria-labelledby="h-cy"><div class="sechead"><h2 id="h-cy">{esc(fy)} at a glance</h2>'
        f"<p>One mark per written-testimony deadline.</p></div>{tl}</section>"
        if tl
        else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{esc(title)}</title>
<meta name="description" content="When each Senate appropriations subcommittee wants outside-witness written testimony, and how to send it. Checked weekly.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,500&family=Public+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body{as_of_attr}>
<a class="skip" href="#upcoming">Skip to deadlines</a>
<header class="mast">
  <div class="wrap mast-grid">
    <div class="mast-text">
      <p class="dateline">Senate appropriations · Outside-witness testimony</p>
      <h1>Testimony deadlines</h1>
      <p class="dek">When each of the 12 Senate appropriations subcommittees accepts written testimony from the public, and exactly how to send it.</p>
      <p class="status-line">{icon("alert" if broken else "check")}<span>{status_sentence(len(items), reachable, len(changed), last_run)}</span></p>
      {actions}
    </div>
    <div class="mast-art">{art("header", "hero-art")}</div>
  </div>
</header>
<main class="wrap" id="main">
  {alerts}
  <section id="upcoming" aria-labelledby="h-up">
    <div class="sechead"><h2 id="h-up">Coming up</h2><p>{esc(lede)}</p></div>
    {coming}
  </section>
  {cycle_section}
  <section id="all" aria-labelledby="h-all">
    <div class="sechead"><h2 id="h-all">All subcommittees</h2><p>How each one takes testimony, from its latest guidelines.</p></div>
    <div class="cards">{cards}</div>
  </section>
  <section id="log" aria-labelledby="h-log">
    <div class="sechead"><h2 id="h-log">What’s changed</h2><p>Every weekly check, newest first.</p></div>
    {change_log(history, names)}
  </section>
</main>
<footer class="foot">
  <div class="wrap">
    <p><b>Always confirm in the official PDF before you submit.</b> Everything here is read each week from the
    <a href="https://www.appropriations.senate.gov/subcommittees">Senate Appropriations subcommittee pages</a>;
    a blank means the PDF didn’t say.</p>
    <p>Keeping your own tracker? <a href="tracker.csv" download>Download the full table as a spreadsheet</a>.</p>
    <p class="fine">An independent volunteer project by Civic Tech DC for
    <a href="{PROPOSAL_URL}">Congressional Tech proposal IV.3</a>. Not affiliated with the U.S. Senate.</p>
  </div>
</footer>
<script>{JS}</script>
</body>
</html>
"""


def calendar_events(items: list[Item], today: date) -> list[dict]:
    """Calendar events for deadlines that are still open (today or later)."""
    events = []
    for i in items:
        d = i.deadline
        if not d or d < today:
            continue
        f = i.f
        lines = [due_phrase(i) + "."]
        if f.get("delivery_address"):
            lines.append(f"Email to: {f['delivery_address']}")
        elif policy_plain(i):
            lines.append(policy_plain(i))
        if f.get("subject_line"):
            lines.append(f"Subject line: {f['subject_line']}")
        lines.append(f"File: {format_plain(i)}")
        lines += [f"Check the PDF: {c}" for c in plain_checks(i)]
        if i.entry.get("pdf_url"):
            lines.append(f"Official guidelines: {i.entry['pdf_url']}")
        lines.append("Confirm in the official PDF before submitting.")
        stamp = date.fromisoformat(i.entry.get("last_changed") or d.isoformat())
        events.append(
            {
                "uid": f"{i.sub.id}-{f.get('fiscal_year', '')}-{d:%Y%m%d}@approps-deadlines",
                "date": d,
                "stamp": stamp,
                "summary": f"Testimony due: {i.sub.chamber} {i.sub.label} ({f.get('fiscal_year', '')})",
                "description": "\n".join(lines),
                "url": i.entry.get("pdf_url", ""),
            }
        )
    return sorted(events, key=lambda e: e["date"])


def build_site(
    subs: list[Subcommittee], paths: Paths, site_dir: Path, today: date, preview: bool = False
) -> Path:
    """Write index.html and deadlines.ics into site_dir. A preview also gets a copy of the CSV it links to."""
    items, history = load_items(subs, paths)
    site_dir.mkdir(parents=True, exist_ok=True)
    page = site_dir / "index.html"
    page.write_text(render_page(items, history, today, preview=preview), encoding="utf-8")
    (site_dir / "deadlines.ics").write_text(build_ics(calendar_events(items, today)), encoding="utf-8", newline="")
    csv_path = site_dir / "tracker.csv"
    if preview and paths.output_csv.exists() and paths.output_csv.resolve() != csv_path.resolve():
        shutil.copyfile(paths.output_csv, csv_path)
    return page


CSS = """
/* Hallmark · genre: editorial · macrostructure: Catalogue (docket variant) · theme: Newsprint
 * tone: calm civic, plain-language · anchor hue: 29 (Senate red) · enrichment: Tier C generated stills (header, empty state)
 * nav: N6 masthead (left-set) · footer: Ft4 colophon · motion: none beyond 150ms colour transitions
 * ui-ux-pro-max style: Accessible & Ethical (focus rings, skip link, 44px touch targets, AA contrast) */
:root{
  --color-paper:oklch(96.5% 0.012 85);--color-paper-2:oklch(98.8% 0.005 90);
  --color-ink:oklch(22% 0.008 80);--color-ink-2:oklch(38.5% 0.013 80);--color-muted:oklch(50% 0.025 85);
  --color-rule:oklch(87% 0.02 85);--color-rule-2:oklch(76% 0.028 85);
  --color-accent:oklch(48% 0.165 29);--color-accent-bg:oklch(93.5% 0.028 35);
  --color-warn:oklch(46% 0.1 75);--color-warn-bg:oklch(94.5% 0.04 85);
  --color-ok:oklch(46% 0.1 150);--color-ok-bg:oklch(94.5% 0.03 150);
  --color-focus:oklch(45% 0.16 260);--color-plate:transparent;
  --font-display:"Newsreader",Georgia,"Times New Roman",serif;
  --font-body:"Public Sans",-apple-system,"Segoe UI",Roboto,sans-serif;
  --space-1:4px;--space-2:8px;--space-3:12px;--space-4:16px;--space-5:24px;--space-6:32px;--space-7:48px;--space-8:64px;
  --radius:4px;--radius-lg:16px;--dur:150ms;--ease-out:cubic-bezier(.2,.7,.2,1);
  color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{
  --color-paper:oklch(18% 0.008 85);--color-paper-2:oklch(22% 0.01 85);
  --color-ink:oklch(94% 0.014 85);--color-ink-2:oklch(83% 0.018 85);--color-muted:oklch(71% 0.022 85);
  --color-rule:oklch(31% 0.012 85);--color-rule-2:oklch(43% 0.016 85);
  --color-accent:oklch(73% 0.14 30);--color-accent-bg:oklch(27% 0.05 30);
  --color-warn:oklch(81% 0.12 80);--color-warn-bg:oklch(27% 0.04 80);
  --color-ok:oklch(81% 0.11 150);--color-ok-bg:oklch(26% 0.04 150);
  --color-focus:oklch(78% 0.12 260);--color-plate:oklch(93% 0.016 85)}}
*{box-sizing:border-box}
html,body{overflow-x:clip}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--color-paper);color:var(--color-ink);font:17px/1.6 var(--font-body);font-variant-numeric:tabular-nums}
a{color:var(--color-accent);text-underline-offset:3px;text-decoration-thickness:1px;transition:text-decoration-thickness var(--dur) var(--ease-out)}
a:hover{text-decoration-thickness:2px}
:focus-visible{outline:3px solid var(--color-focus);outline-offset:2px}
.skip{position:absolute;left:var(--space-4);top:-100px;background:var(--color-ink);color:var(--color-paper);padding:var(--space-2) var(--space-4);z-index:10}
.skip:focus{top:var(--space-2)}
.ic{width:20px;height:20px;flex:none;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.wrap{max-width:1080px;margin:0 auto;padding-inline:max(var(--space-5),env(safe-area-inset-left))}
.mast{border-bottom:1px solid var(--color-rule);padding:var(--space-6) 0 var(--space-6);background:var(--color-paper-2)}
.mast-grid{display:grid;grid-template-columns:minmax(0,1fr);gap:var(--space-5);align-items:center}
.mast-art{display:none}
.hero-art{display:block;width:100%;height:auto;border-radius:var(--radius-lg);background:var(--color-plate)}
.dateline{font:600 14px/1.4 var(--font-body);color:var(--color-accent);margin:0 0 var(--space-2)}
h1{font:500 clamp(2.6rem,7vw,4.4rem)/1 var(--font-display);letter-spacing:-.02em;margin:0 0 var(--space-3);overflow-wrap:anywhere;min-width:0}
.dek{font:400 1.2rem/1.5 var(--font-display);color:var(--color-ink-2);max-width:34em;margin:0 0 var(--space-5)}
.status-line{display:flex;gap:var(--space-2);align-items:flex-start;margin:0 0 var(--space-5);font-weight:500;color:var(--color-ink-2)}
.status-line .ic{color:var(--color-ok);margin-top:3px}
.actions{display:flex;flex-wrap:wrap;gap:var(--space-3);margin:0}
.btn{display:inline-flex;align-items:center;gap:var(--space-2);min-height:48px;padding:0 var(--space-5);border:1.5px solid var(--color-ink);background:var(--color-ink);color:var(--color-paper);
  text-decoration:none;font:600 16px/1 var(--font-body);border-radius:var(--radius);white-space:nowrap;transition:background-color var(--dur) var(--ease-out),border-color var(--dur) var(--ease-out)}
.btn:hover{background:var(--color-accent);border-color:var(--color-accent)}
.btn:active{transform:translateY(1px)}
section{margin:var(--space-7) 0}
.sechead{border-bottom:1px solid var(--color-ink);margin-bottom:var(--space-1);padding-bottom:var(--space-2)}
.sechead h2{font:500 1.8rem/1.2 var(--font-display);margin:0}
.sechead p{margin:var(--space-1) 0 0;color:var(--color-muted);font-size:15px}
.alert{margin:var(--space-5) 0 0;padding:var(--space-3) var(--space-4);border:1px solid var(--color-rule-2);background:var(--color-paper-2);border-radius:var(--radius)}
.alert-new{border-color:var(--color-accent);background:var(--color-accent-bg)}
.alert-warn{border-color:var(--color-warn);background:var(--color-warn-bg)}
.sq{display:inline-block;width:9px;height:9px;background:var(--color-ink-2);margin-right:var(--space-2);vertical-align:1px}
.alert-new .sq{background:var(--color-accent)}
.alert-warn .sq{background:var(--color-warn)}
.entry{display:grid;grid-template-columns:84px minmax(0,1fr);gap:var(--space-4);padding:var(--space-6) 0;border-bottom:1px solid var(--color-rule)}
.tear{border:1.5px solid var(--color-ink);background:var(--color-paper-2);text-align:center;display:flex;flex-direction:column;border-radius:var(--radius);overflow:hidden}
.tear .dow{background:var(--color-accent);color:var(--color-paper);font:600 12px/1 var(--font-body);letter-spacing:.1em;text-transform:uppercase;padding:7px 0}
.tear .mon{font:600 13px/1 var(--font-body);letter-spacing:.1em;text-transform:uppercase;padding-top:var(--space-2);color:var(--color-ink-2)}
.tear .day{font:500 40px/1 var(--font-display);padding:2px 0 var(--space-2)}
.tear .day.small{font-size:22px;padding:6px 0 var(--space-3)}
.tear-rel .dow{background:var(--color-ink-2)}
.count{font:600 14px/1.3 var(--font-body);color:var(--color-accent);text-align:center;margin:var(--space-2) 0 0}
.closed .count{color:var(--color-muted)}
.what h3{font:500 1.55rem/1.2 var(--font-display);margin:0}
.due{font-weight:600;margin:6px 0 var(--space-4)}
.how{margin:0 0 var(--space-3);display:grid;gap:var(--space-3)}
.how div{display:grid;grid-template-columns:minmax(0,1fr);gap:2px}
.how dt{display:flex;align-items:center;gap:6px;font:500 14px/1.4 var(--font-body);color:var(--color-muted)}
.how dt .ic{width:18px;height:18px}
.how dd{margin:0;overflow-wrap:anywhere}
.subj{font-weight:600;background:var(--color-paper-2);border:1px solid var(--color-rule-2);padding:2px 8px;border-radius:var(--radius)}
.copy{margin-left:var(--space-2);min-height:34px;min-width:56px;font:600 13px/1 var(--font-body);color:var(--color-ink-2);cursor:pointer;
  background:var(--color-paper-2);border:1px solid var(--color-rule-2);border-radius:var(--radius);padding:0 var(--space-3);vertical-align:middle;
  transition:border-color var(--dur) var(--ease-out),color var(--dur) var(--ease-out)}
.copy:hover{border-color:var(--color-ink);color:var(--color-ink)}
.copy.done{border-color:var(--color-ok);color:var(--color-ok)}
@media (pointer:coarse){.copy{min-height:44px;min-width:64px}}
.flag{display:flex;gap:var(--space-2);align-items:flex-start;font-size:15px;margin:var(--space-2) 0}
.flag .ic{width:18px;height:18px;color:var(--color-warn);margin-top:2px}
.flag b{color:var(--color-warn)}
.flag-warn{background:var(--color-warn-bg);border:1px solid var(--color-warn);padding:var(--space-2) var(--space-3);border-radius:var(--radius)}
.note{font-size:15px;margin:var(--space-2) 0;color:var(--color-ink-2)}
.links{font-size:14px;color:var(--color-muted);margin:var(--space-3) 0 0}
.empty{display:grid;grid-template-columns:minmax(0,1fr);gap:var(--space-4);align-items:center;padding:var(--space-6) 0 var(--space-2);max-width:48em}
.empty-art{width:120px;height:auto;border-radius:var(--radius-lg);background:var(--color-plate)}
.empty-big{font:500 1.75rem/1.2 var(--font-display);margin:0 0 var(--space-2)}
.empty p{color:var(--color-ink-2);margin-top:0}
.tl-legend{display:none;font-size:14px;color:var(--color-muted);margin:var(--space-3) 0 0;gap:var(--space-2);align-items:center}
.key{display:inline-block;width:12px;height:12px;border-radius:50%;margin-left:var(--space-3)}
.key:first-child{margin-left:0}
.key-open{background:var(--color-accent)}
.key-closed{border:2px solid var(--color-ink-2)}
.tl-chart{display:none}
.tl-svg{width:100%;height:auto;display:block}
.tl-svg .grid{stroke:var(--color-rule);stroke-width:1}
.tl-svg .row{stroke:var(--color-rule);stroke-dasharray:2 4}
.tl-svg .axis{font:600 13px var(--font-body);fill:var(--color-muted);text-transform:uppercase;letter-spacing:.08em}
.tl-svg .label{font:500 15px var(--font-body);fill:var(--color-ink)}
.tl-svg .val{font:500 14px var(--font-body);fill:var(--color-ink-2)}
.tl-svg .rolling{fill:var(--color-warn)}
.tl-svg .dot{fill:var(--color-accent);stroke:var(--color-paper);stroke-width:2.5}
.tl-svg .dot.closed{fill:var(--color-paper);stroke:var(--color-ink-2);stroke-width:2}
.tl-svg .today{stroke:var(--color-accent);stroke-width:2}
.tl-svg .today-label{font:600 12px var(--font-body);fill:var(--color-accent);text-transform:uppercase;letter-spacing:.08em}
.tl-list{list-style:none;margin:var(--space-2) 0 0;padding:0}
.tl-list li{display:grid;grid-template-columns:64px minmax(0,1fr) auto;gap:var(--space-3);padding:var(--space-2) 0;border-bottom:1px solid var(--color-rule);font-size:15px}
.tl-date{font-weight:600}
.tl-state{color:var(--color-muted);font-size:14px}
.cards{display:grid;grid-template-columns:minmax(0,1fr);gap:0 var(--space-6)}
.card{padding:var(--space-5) 0;border-bottom:1px solid var(--color-rule)}
.card header{display:flex;justify-content:space-between;align-items:baseline;gap:var(--space-3)}
.card h3{font:500 1.35rem/1.2 var(--font-display);margin:0}
.card .full{font-size:14px;color:var(--color-muted);margin:2px 0 var(--space-2)}
.card .due{font-size:15px;margin:0 0 var(--space-3)}
.badge,.tag{font:600 12px/1 var(--font-body);padding:5px 8px;border:1px solid var(--color-rule-2);color:var(--color-muted);white-space:nowrap;border-radius:var(--radius)}
.badge-open{border-color:var(--color-ok);color:var(--color-ok);background:var(--color-ok-bg)}
.badge-warn,.tag-warn{border-color:var(--color-warn);color:var(--color-warn);background:var(--color-warn-bg)}
.tag-new{border-color:var(--color-accent);color:var(--color-accent);background:var(--color-accent-bg)}
.log{list-style:none;margin:0;padding:0}
.logrow{display:grid;grid-template-columns:minmax(0,1fr);gap:var(--space-1);padding:var(--space-3) 0;border-bottom:1px solid var(--color-rule);font-size:15px}
.logwhen{display:flex;flex-wrap:wrap;gap:var(--space-2);align-items:center}
.logwhen time{font-size:14px;color:var(--color-muted)}
.muted{color:var(--color-muted);font-size:14px}
.foot{border-top:1px solid var(--color-rule);background:var(--color-paper-2);margin-top:var(--space-8);padding:var(--space-6) 0 max(var(--space-7),env(safe-area-inset-bottom));font-size:15px;color:var(--color-ink-2)}
.foot p{max-width:48em;margin:0 0 var(--space-3)}
.foot .fine{color:var(--color-muted);font-size:14px}
@media (min-width:40rem){
  .mast{padding:var(--space-7) 0}
  .sechead{display:flex;align-items:baseline;justify-content:space-between;gap:var(--space-4);flex-wrap:wrap}
  .sechead h2{font-size:1.9rem}
  .entry{grid-template-columns:128px minmax(0,1fr);gap:var(--space-6)}
  .tear .day{font-size:60px}
  .tear .day.small{font-size:28px}
  .what h3{font-size:1.6rem}
  .how div{grid-template-columns:132px minmax(0,1fr);gap:var(--space-3);align-items:baseline}
  .card .how div{grid-template-columns:minmax(0,1fr);gap:2px}
  .empty{grid-template-columns:160px minmax(0,1fr);gap:var(--space-6)}
  .empty-art{width:160px}
  .tl-legend{display:flex}
  .tl-chart{display:block;padding-top:var(--space-2)}
  .tl-list{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
  .logrow{grid-template-columns:240px minmax(0,1fr);gap:var(--space-5)}
}
@media (min-width:48rem){
  .mast-grid{grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:var(--space-7)}
  .mast-art{display:block}
}
@media (min-width:60rem){.cards{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (min-width:75rem){.cards{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
@media print{.actions,.copy,.skip,.mast-art{display:none}}
"""

JS = """
(function(){
  var asOf=document.body.getAttribute('data-as-of');
  var now=asOf?new Date(asOf+'T12:00:00'):new Date();
  var today=new Date(now.getFullYear(),now.getMonth(),now.getDate());
  document.querySelectorAll('[data-deadline]').forEach(function(el){
    var p=el.getAttribute('data-deadline').split('-');
    var d=new Date(+p[0],+p[1]-1,+p[2]);
    var days=Math.round((d-today)/86400000);
    var c=el.querySelector('[data-count]');
    if(c){c.textContent=days<0?'Closed':days===0?'Due today':days===1?'Due tomorrow':'In '+days+' days';}
    if(days<0){el.classList.add('closed');}
  });
  function fallbackCopy(text){var t=document.createElement('textarea');t.value=text;t.setAttribute('readonly','');
    t.style.position='absolute';t.style.left='-9999px';document.body.appendChild(t);t.select();
    try{document.execCommand('copy');}catch(e){}document.body.removeChild(t);return Promise.resolve();}
  document.addEventListener('click',function(e){
    var b=e.target.closest('button[data-copy]');if(!b)return;
    var text=b.getAttribute('data-copy');
    var p=(navigator.clipboard&&window.isSecureContext)?navigator.clipboard.writeText(text).catch(function(){return fallbackCopy(text);}):fallbackCopy(text);
    p.then(function(){b.textContent='Copied';b.classList.add('done');setTimeout(function(){b.textContent='Copy';b.classList.remove('done');},1600);});
  });
})();
"""
