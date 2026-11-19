"""Command line: `uv run approps-deadlines` crawls every subcommittee and prints the report."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from .fetch import PoliteFetcher
from .site import build_site
from .sources import SENATE_SUBCOMMITTEES
from .tracker import Paths, check_all, render_report, save_report


def _now() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:  # no time zone database on this machine
        return datetime.now(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="approps-deadlines",
        description="Check each Senate Appropriations subcommittee's testimony guidelines and report what changed.",
    )
    ap.add_argument("--delay", type=float, default=3.0, help="seconds to wait between requests (default: 3)")
    ap.add_argument("--timeout", type=float, default=30.0, help="seconds before a request times out (default: 30)")
    ap.add_argument("--only", nargs="+", metavar="ID", help="check only these subcommittees (see --list)")
    ap.add_argument("--list", action="store_true", help="list subcommittee ids and exit")
    ap.add_argument("--data-dir", type=Path, default=Path("data"), help="state, history and reports (default: data)")
    ap.add_argument("--output", type=Path, default=Path("output/tracker.csv"), help="sheet-ready CSV path")
    ap.add_argument("--downloads", type=Path, default=Path("downloads"), help="where PDFs are saved (git-ignored)")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every request")
    ap.add_argument("--page-only", action="store_true", help="rebuild the web page and calendar from saved data; no crawling")
    ap.add_argument(
        "--as-of",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="build the demo page as if today were this date (writes to <output folder>/demo/; no crawling)",
    )
    args = ap.parse_args(argv)

    subs = SENATE_SUBCOMMITTEES
    if args.list:
        for s in subs:
            print(f"{s.id:22} {s.chamber} {s.name}")
        return 0

    only = set(args.only) if args.only else None
    if only:
        unknown = only - {s.id for s in subs}
        if unknown:
            ap.error(f"unknown subcommittee id(s): {', '.join(sorted(unknown))}; see --list")

    def err(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    paths = Paths(data_dir=args.data_dir, output_csv=args.output, downloads_dir=args.downloads)
    now = _now()
    site_dir = args.output.parent

    if args.as_of:
        page = build_site(subs, paths, site_dir / "demo", args.as_of, preview=True)
        err(f"Demo as of {args.as_of}: {page}")
        return 0
    if args.page_only:
        page = build_site(subs, paths, site_dir, now.date())
        err(f"Web page: {page}\nCalendar: {site_dir / 'deadlines.ics'}")
        return 0

    fetcher = PoliteFetcher(delay=args.delay, timeout=args.timeout, log=err if args.verbose else None)
    err(f"Checking {len(only) if only else len(subs)} subcommittee page(s), one request every {args.delay:g}s...")
    rows = check_all(subs, fetcher, paths, now, progress=err, only=only)
    report = render_report(rows, now, request_count=fetcher.request_count)
    report_path = save_report(paths, report, now)
    page = build_site(subs, paths, site_dir, now.date())

    print(report)
    err(
        f"Report: {report_path}\nSheet-ready CSV: {args.output}\nWeb page: {page}\n"
        f"Calendar: {site_dir / 'deadlines.ics'}\nHistory: {paths.history}"
    )
    # Broken pages are reported, not fatal: exit 0 so a weekly job keeps its history.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
