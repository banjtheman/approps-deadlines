# Draft post for #congressional-tech (or a GitHub issue)

> Nothing here has been posted.

---

**Appropriations deadlines tracker (IV.3): weekly crawler for the 12 Senate subcommittees is working**

At tonight's meetup we built the first piece of proposal IV.3, the top priority from the June meeting with AGI: https://civictechdc.github.io/congressional-tech/proposals/IV.3/

**See it**
- Live page (updated weekly, refreshed daily): https://banjtheman.github.io/approps-deadlines/
- Demo (FY2027 guidance shown as it looked mid-cycle, since nothing is open right now): https://banjtheman.github.io/approps-deadlines/demo/
- Code: https://github.com/banjtheman/approps-deadlines

**What it does**
- Visits each of the 12 Senate Appropriations subcommittee pages and follows the "Submit Written Testimony" link through the intermediate `/download/` page to the guidelines PDF.
- Fingerprints the testimony section of each page and the PDF, and reports **updated: yes / no** per subcommittee. The first run records a baseline, and every run is kept in a dated history.
- Broken pages (down, timed out, missing link, HTML where a PDF should be, unreadable PDF) are listed in the report instead of crashing the run. A broken week never overwrites the last good fingerprint.
- Pulls subcommittee, fiscal year, deadline, format, page limit, delivery address, subject line, source URL and last changed into a CSV you can paste straight into a Google Sheet.
- Polite: one request at a time with a 3-second pause, about 46 requests per run (2.5 minutes).
- A GitHub Actions workflow runs it every Monday and commits the new history. There's also a cron line for running it on a laptop.
- **A simple page for staff** (`output/index.html`): what's due soon and exactly how to send it, with one-click email (subject line filled in), copy buttons, an "Add to calendar" file with a one-week reminder, a timeline of the cycle, and plain-language flags where a PDF is unclear. It works on phones and in dark mode. Right now it honestly says "Nothing is open" because every FY2027 deadline has passed; `--as-of 2026-05-12` previews what staff would see mid-cycle.

**Current results (FY2027 guidance, posted April 2026):** all 12 pages work, and 9 of 12 rows are complete from text rules alone, with no API key. Three are flagged for a human:
- Agriculture's PDF doesn't say which file format it accepts.
- MilCon-VA only takes testimony through a Committee member's office, so there's no email or subject line.
- THUD's deadline is "seven days after the respective Departmental budget hearing", not a date.

**Run it**
```
git clone https://github.com/banjtheman/approps-deadlines
cd approps-deadlines
uv run approps-deadlines
```
Results land in `output/index.html` (the staff page), `output/deadlines.ics`, `output/tracker.csv` and `data/reports/latest.md`.

**What's next (help wanted)**
1. Check the rows against Daniel's hand-kept tracker, and get his column list.
2. Add the House subcommittee pages.
3. AI parsing as a fallback for PDFs the text rules can't read, especially when FY2028 guidance comes out in the spring.
4. Push the CSV into a Google Sheet automatically, and move the repo under the civictechdc org.
5. Add an appropriations calendar tab in BillTrax (the .ics file is a start).
6. Alert (email or Slack) when a page says "updated: yes".
