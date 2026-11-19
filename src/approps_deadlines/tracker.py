"""Compare each crawl with the last good one, keep a dated history, and write the outputs.

Files (all under the data folder unless noted):
  state.json          last good fingerprints + fields per subcommittee
  history.csv         one row per subcommittee per run, never rewritten
  reports/<time>.md   the report for each run; reports/latest.md is the newest
  text/<id>.txt       extracted PDF text, so `git diff` shows what changed in a PDF
  output/tracker.csv  the sheet-ready table (path set by the caller)
  downloads/<id>.pdf  the PDFs themselves (git-ignored)

"Updated this run" values:
  first check  no earlier fingerprint to compare with
  yes          the testimony block, its link, the PDF address, or the PDF file changed
  no           nothing changed since the last good check
  unknown      the page or PDF was broken this run; the last good fingerprint is kept
  not checked  left out of this run with --only
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .crawl import CrawlResult, Fetcher, crawl_one, normalize_space
from .extract import Fields, extract_fields
from .sources import Subcommittee

TRACKER_COLUMNS = [
    # The tracker's own columns, in the order the proposal lists them.
    "Subcommittee",
    "Fiscal year",
    "Deadline",
    "Format",
    "Page limit",
    "Delivery address",
    "Subject line",
    "Source URL",
    "Last changed",
    # Extras that help whoever reads the sheet.
    "Chamber",
    "Updated this run",
    "Status",
    "Problem",
    "Needs review",
    "Notes",
    "Deadline text",
    "PDF last modified (server)",
    "Page URL",
    "Checked at",
]

HISTORY_COLUMNS = [
    "checked_at",
    "subcommittee_id",
    "subcommittee",
    "status",
    "updated",
    "what_changed",
    "problem",
    "deadline",
    "pdf_url",
    "pdf_sha256",
    "section_sha256",
]


def sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@dataclass
class Paths:
    data_dir: Path
    output_csv: Path
    downloads_dir: Path

    @property
    def state(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def history(self) -> Path:
        return self.data_dir / "history.csv"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def text_dir(self) -> Path:
        return self.data_dir / "text"


@dataclass
class Row:
    sub: Subcommittee
    status: str  # "ok", "broken", "not checked"
    updated: str
    what_changed: list[str] = field(default_factory=list)
    problem: str = ""
    entry: dict | None = None  # the state entry the row's fields come from (may be from an earlier run)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def fingerprints(cr: CrawlResult) -> dict:
    return {
        "section_sha256": sha256(cr.section_text),
        "link_url": cr.link_url,
        "pdf_url": cr.pdf_url,
        "pdf_sha256": sha256(cr.pdf_bytes),
        "pdf_text_sha256": sha256(normalize_space(cr.pdf_text)),
    }


def what_changed(prev: dict, now: dict) -> list[str]:
    changes = []
    if prev.get("section_sha256") != now["section_sha256"]:
        changes.append("testimony section on the page")
    if prev.get("link_url") != now["link_url"]:
        changes.append(f"testimony link (was {prev.get('link_url')})")
    if prev.get("pdf_url") != now["pdf_url"]:
        changes.append(f"PDF address (was {prev.get('pdf_url')})")
    if prev.get("pdf_sha256") != now["pdf_sha256"]:
        same_text = prev.get("pdf_text_sha256") == now["pdf_text_sha256"]
        changes.append("PDF file (text unchanged)" if same_text else "PDF content")
    return changes


def _safe_extract(text: str) -> Fields:
    try:
        return extract_fields(text)
    except Exception as e:  # a rule bug should flag the row, not stop the run
        f = Fields()
        f.needs_review.append(f"field extraction failed ({type(e).__name__}: {e})")
        return f


def check_all(
    subs: list[Subcommittee],
    fetcher: Fetcher,
    paths: Paths,
    now: datetime,
    progress: Callable[[str], None] = lambda _m: None,
    only: set[str] | None = None,
) -> list[Row]:
    state = load_state(paths.state)
    today = now.date().isoformat()
    checked_at = now.isoformat(timespec="seconds")
    rows: list[Row] = []
    to_check = [s for s in subs if only is None or s.id in only]

    for sub in subs:
        prev = state.get(sub.id)
        if sub not in to_check:
            rows.append(Row(sub, "not checked", "not checked", entry=prev))
            continue
        progress(f"[{to_check.index(sub) + 1}/{len(to_check)}] {sub.chamber} {sub.name}")
        cr = crawl_one(sub, fetcher)
        if not cr.ok:
            # Keep the last good fingerprint untouched so a broken week doesn't
            # turn into a false "updated" (or hide a real one) next week.
            progress(f"    broken at {cr.stage}: {cr.error}")
            rows.append(Row(sub, "broken", "unknown", problem=f"{cr.stage}: {cr.error}", entry=prev))
            continue

        fp = fingerprints(cr)
        if prev is None:
            updated, changes = "first check", []
            last_changed = today
        else:
            changes = what_changed(prev, fp)
            updated = "yes" if changes else "no"
            last_changed = today if changes else prev.get("last_changed", today)
        fields = _safe_extract(cr.pdf_text)
        if not cr.section_found:
            fields.needs_review.append("page layout changed: testimony block not found, used a fallback link")
        entry = {
            "subcommittee": sub.name,
            "chamber": sub.chamber,
            "page_url": sub.page_url,
            **fp,
            "hops": cr.hops,
            "pdf_last_modified": cr.pdf_last_modified,
            "first_seen": prev.get("first_seen", today) if prev else today,
            "last_changed": last_changed,
            "last_ok": checked_at,
            "section_text": cr.section_text,
            "fields": fields.as_dict(),
        }
        state[sub.id] = entry
        _save_pdf_and_text(paths, sub, cr)
        progress(f"    ok, updated: {updated}" + (f" ({'; '.join(changes)})" if changes else ""))
        rows.append(Row(sub, "ok", updated, what_changed=changes, entry=entry))

    _write_atomic(paths.state, json.dumps(state, indent=2, sort_keys=True) + "\n")
    append_history(paths.history, rows, checked_at)
    write_tracker_csv(paths.output_csv, rows, checked_at)
    return rows


def _save_pdf_and_text(paths: Paths, sub: Subcommittee, cr: CrawlResult) -> None:
    paths.downloads_dir.mkdir(parents=True, exist_ok=True)
    (paths.downloads_dir / f"{sub.id}.pdf").write_bytes(cr.pdf_bytes)
    _write_atomic(paths.text_dir / f"{sub.id}.txt", cr.pdf_text.strip() + "\n")


def append_history(path: Path, rows: list[Row], checked_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new_file:
            w.writerow(HISTORY_COLUMNS)
        for r in rows:
            if r.status == "not checked":
                continue
            e = r.entry or {}
            fresh = r.status == "ok"
            w.writerow(
                [
                    checked_at,
                    r.sub.id,
                    r.sub.name,
                    r.status,
                    r.updated,
                    "; ".join(r.what_changed),
                    r.problem,
                    e.get("fields", {}).get("deadline", "") if fresh else "",
                    e.get("pdf_url", "") if fresh else "",
                    e.get("pdf_sha256", "")[:16] if fresh else "",
                    e.get("section_sha256", "")[:16] if fresh else "",
                ]
            )


def tracker_row(r: Row, checked_at: str) -> dict:
    e = r.entry or {}
    f = e.get("fields", {})
    problem = r.problem
    if r.status == "broken" and e:
        problem += f" (fields below are from the last good check, {e.get('last_ok', '')[:10]})"
    return {
        "Subcommittee": r.sub.name,
        "Fiscal year": f.get("fiscal_year", ""),
        "Deadline": f.get("deadline", ""),
        "Format": f.get("format", ""),
        "Page limit": f.get("page_limit", ""),
        "Delivery address": f.get("delivery_address", ""),
        "Subject line": f.get("subject_line", ""),
        "Source URL": e.get("pdf_url", ""),
        "Last changed": e.get("last_changed", ""),
        "Chamber": r.sub.chamber,
        "Updated this run": r.updated,
        "Status": r.status,
        "Problem": problem,
        "Needs review": "; ".join(f.get("needs_review", [])),
        "Notes": " ".join(f.get("notes", [])),
        "Deadline text": f.get("deadline_text", ""),
        "PDF last modified (server)": e.get("pdf_last_modified", ""),
        "Page URL": r.sub.page_url,
        "Checked at": checked_at if r.status != "not checked" else e.get("last_ok", ""),
    }


def write_tracker_csv(path: Path, rows: list[Row], checked_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=TRACKER_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(tracker_row(r, checked_at))
    os.replace(tmp, path)


def _md(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def render_report(rows: list[Row], now: datetime, request_count: int | None = None) -> str:
    checked = [r for r in rows if r.status != "not checked"]
    broken = [r for r in checked if r.status == "broken"]
    counts = {k: sum(1 for r in checked if r.updated == k) for k in ("yes", "no", "first check", "unknown")}
    lines = [
        f"# Appropriations testimony guidelines check, {now:%Y-%m-%d %H:%M %Z}".rstrip(),
        "",
        f"Checked {len(checked)} subcommittee page(s): "
        f"{counts['yes']} updated, {counts['no']} unchanged, {counts['first check']} first check, "
        f"{len(broken)} broken." + (f" {request_count} requests." if request_count is not None else ""),
        "",
        "| Subcommittee | Updated? | Status | Deadline | What changed / problem |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        if r.status == "not checked":
            continue
        f = (r.entry or {}).get("fields", {})
        detail = r.problem or "; ".join(r.what_changed)
        if r.updated == "first check":
            detail = "fingerprints saved; next run compares against this one"
        lines.append(
            f"| {r.sub.chamber} {_md(r.sub.name)} | {r.updated} | {r.status} | {f.get('deadline', '') or '—'} | {_md(detail)} |"
        )

    lines += ["", f"## Broken pages ({len(broken)})", ""]
    lines += [
        f"- **{r.sub.name}**: {r.problem}" + ("" if r.sub.page_url in r.problem else f" ({r.sub.page_url})")
        for r in broken
    ] or ["None."]

    review = [(r, (r.entry or {}).get("fields", {})) for r in checked if r.status == "ok"]
    review = [(r, f) for r, f in review if f.get("needs_review")]
    lines += ["", f"## Needs a human look ({len(review)})", ""]
    lines += [f"- **{r.sub.name}**: {'; '.join(f['needs_review'])}" for r, f in review] or ["Nothing flagged."]
    return "\n".join(lines) + "\n"


def save_report(paths: Paths, report: str, now: datetime) -> Path:
    path = paths.reports_dir / f"{now:%Y-%m-%d_%H%M%S}.md"
    _write_atomic(path, report)
    _write_atomic(paths.reports_dir / "latest.md", report)
    return path
