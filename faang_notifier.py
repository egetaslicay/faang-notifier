"""
FAANG Job Notifier
Polls two sources for new postings and emails you when something new drops:
  - the speedyapply 2026-SWE-College-Jobs GitHub repo (FAANG/quant sections)
  - curated Simplify.jobs list pages

Setup:
1. pip install requests
2. Set the EMAIL_SENDER, EMAIL_PASSWORD and EMAIL_RECEIVER environment variables
   (when running in GitHub Actions these come from repository secrets).
3. Run manually or set up a cron job / scheduled task
"""

import requests
import json
import os
import smtplib
import re
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# ── CONFIG ──────────────────────────────────────────────────────────────────
# Read email credentials from environment variables (GitHub Actions secrets).
EMAIL_SENDER   = os.environ.get("EMAIL_SENDER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER")

# Section headers to include (case-insensitive, partial match)
# Anything under these ### headers gets tracked; everything else is ignored
WATCHED_SECTIONS = ["faang", "quant"]

# Which markdown files to scan (relative to repo root).
# Internship listings only — New Grad files are intentionally excluded.
#   README.md      -> 2026 USA SWE Internships
#   INTERN_INTL.md -> 2026 International SWE Internships
FILES_TO_WATCH = [
    "README.md",
    "INTERN_INTL.md",
]

REPO = "speedyapply/2026-SWE-College-Jobs"

# Curated Simplify.jobs list pages. These are already filtered to big tech, so
# WATCHED_SECTIONS does not apply -- every posting on them is tracked.
SIMPLIFY_LISTS = [
    "https://simplify.jobs/l/List-Big-Tech-SWE-Internships",
]

SEEN_FILE = "seen_jobs.json"   # local file to track what you've already been notified about

# The default python-requests user agent gets blocked by some hosts.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# ── GITHUB FETCHING ─────────────────────────────────────────────────────────

def fetch_file(filename):
    url = f"https://raw.githubusercontent.com/{REPO}/main/{filename}"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.text


def parse_jobs_from_markdown(content, source_file):
    """
    Parses only rows that fall under watched section headers (### FAANG+, ### Quant).
    Stops collecting when it hits any other ### header (e.g. ### Other).
    """
    jobs = []
    in_watched_section = False

    for line in content.splitlines():
        # detect section headers like ### FAANG+ or ### Quant
        if line.startswith("###"):
            section_name = line.lstrip("#").strip().lower()
            in_watched_section = any(w in section_name for w in WATCHED_SECTIONS)
            continue

        if not in_watched_section:
            continue

        if not line.startswith("|"):
            continue

        # Keep blank cells so column positions stay stable across files
        # (README has a Salary column, INTERN_INTL does not).
        cells = [c.strip() for c in line.split("|")]
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        if len(cells) < 3:
            continue

        # Skip the header row and the |---|---| separator row.
        if cells[0].startswith("-") or cells[0].lower() in ("company", ""):
            continue

        company = re.sub(r"\[.*?\]\(.*?\)", "", cells[0])
        company = re.sub(r"<[^>]+>", "", company)
        company = re.sub(r"[*_`]", "", company).strip()

        role = cells[1] if len(cells) > 1 else ""
        role = re.sub(r"\[.*?\]\(.*?\)", "", role)
        role = re.sub(r"<[^>]+>", "", role).strip()

        # The "Posting" cell wraps an Apply image: href="URL"><img ...>.
        # That href is the application link (distinct from the company
        # website link in the first column). Fall back to any href, then
        # to a markdown-style (url) for good measure.
        link_match = (
            re.search(r'href="(https?://[^"]+)"\s*>\s*<img', line)
            or re.search(r'href="(https?://[^"]+)"', line)
            or re.search(r"\((https?://[^\)]+)\)", line)
        )
        link = link_match.group(1) if link_match else ""

        # "Age" is always the last column (e.g. "4d", "2mo", "1y").
        age = cells[-1]
        if not re.match(r"^\d+\s*(d|h|w|mo|m|y|yr|day|days)$", age, re.IGNORECASE):
            age = ""

        if company:
            jobs.append({
                "company": company,
                "role": role,
                "link": link,
                "age": age,
                "source": source_file,
            })

    return jobs

# ── SIMPLIFY.JOBS FETCHING ───────────────────────────────────────────────────

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def format_locations(value, limit=3):
    """
    `locations` comes back as either a plain string ("Phoenix, AZ, USA") or a
    list of them, so normalise both. A few postings list 8+ offices, which would
    swamp the email, hence the cap.
    """
    if not value:
        return ""
    if isinstance(value, str):
        return value.strip()

    places = [str(v).strip() for v in value if str(v).strip()]
    if len(places) > limit:
        return " / ".join(places[:limit]) + f" (+{len(places) - limit} more)"
    return " / ".join(places)


def format_age(updated_epoch):
    """
    Simplify gives an epoch timestamp; the GitHub rows give a string like "4d".
    Convert to the same shape so both sources fill the Age column.
    """
    if not updated_epoch:
        return ""
    try:
        delta = datetime.now() - datetime.fromtimestamp(float(updated_epoch))
    except (TypeError, ValueError, OSError, OverflowError):
        return ""

    days = delta.days
    if days < 0:
        return ""
    if days == 0:
        return f"{max(delta.seconds // 3600, 1)}h"
    if days < 30:
        return f"{days}d"
    if days < 365:
        return f"{days // 30}mo"
    return f"{days // 365}y"


def fetch_simplify_jobs(list_url):
    """
    Simplify list pages are Next.js, so every posting is already embedded in the
    page as JSON under __NEXT_DATA__. Reading that is far less brittle than
    scraping the rendered DOM (which would need a real browser anyway).

    Note this sees the postings in the initial page payload, not everything
    behind pagination -- enough for a "what's new" alert.
    """
    r = requests.get(list_url, timeout=15, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()

    match = NEXT_DATA_RE.search(r.text)
    if not match:
        raise ValueError("no __NEXT_DATA__ found -- page layout may have changed")

    page_props = json.loads(match.group(1))["props"]["pageProps"]

    # Name the source after the list itself so emails say where a job came from.
    source = (page_props.get("jobList") or {}).get("title") or list_url

    jobs = []
    for hit in page_props.get("initialJobHits", []):
        company = (hit.get("company_name") or "").strip()
        if not company:
            continue

        # /p/<posting_id> redirects to the canonical posting URL.
        posting_id = hit.get("posting_id") or hit.get("id")
        link = f"https://simplify.jobs/p/{posting_id}" if posting_id else list_url

        jobs.append({
            "id": posting_id,
            "company": company,
            "role": (hit.get("title") or "").strip(),
            "location": format_locations(hit.get("locations")),
            "age": format_age(hit.get("updated_date")),
            "link": link,
            "source": source,
        })

    return jobs

# ── STATE TRACKING ───────────────────────────────────────────────────────────

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)


def job_id(job):
    # unique key per job so we don't notify twice. Simplify supplies a stable
    # posting id; the GitHub rows have none, so they keep the original
    # company/role/source key -- which also keeps existing seen_jobs.json valid.
    if job.get("id"):
        return f"simplify::{job['id']}"
    return f"{job['company']}::{job['role']}::{job['source']}"

# ── EMAIL ────────────────────────────────────────────────────────────────────

def send_email(new_jobs):
    subject = f"[FAANG Alert] {len(new_jobs)} new job(s) dropped"

    rows = ""
    for j in new_jobs:
        age = j.get("age", "")
        location = j.get("location") or "&mdash;"
        link_text = f'<a href="{j["link"]}">Apply</a>' if j["link"] else "No link"
        rows += (
            f"<tr><td>{j['company']}</td><td>{j['role']}</td><td>{location}</td>"
            f"<td>{age}</td><td>{j['source']}</td><td>{link_text}</td></tr>"
        )

    html = f"""
    <html><body>
    <h2>New FAANG/Target Company Jobs</h2>
    <p>Found {len(new_jobs)} new posting(s) as of {datetime.now().strftime('%Y-%m-%d %H:%M')}:</p>
    <table border="1" cellpadding="6" cellspacing="0">
        <tr><th>Company</th><th>Role</th><th>Location</th><th>Age</th><th>Source</th><th>Link</th></tr>
        {rows}
    </table>
    <p><a href="https://github.com/{REPO}">View full list on GitHub</a></p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = EMAIL_RECEIVER
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())

    print(f"Email sent: {subject}")

# ── MAIN ─────────────────────────────────────────────────────────────────────

def run():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Checking for new FAANG jobs...")

    # Fail early with a clear message if the email config is missing.
    missing = [name for name in ("EMAIL_SENDER", "EMAIL_PASSWORD", "EMAIL_RECEIVER")
               if not os.environ.get(name)]
    if missing:
        print(f"ERROR: missing required environment variable(s): {', '.join(missing)}")
        sys.exit(1)

    seen = load_seen()
    all_jobs = []

    for filename in FILES_TO_WATCH:
        try:
            content = fetch_file(filename)
            jobs = parse_jobs_from_markdown(content, filename)
            all_jobs.extend(jobs)
            print(f"  {filename}: {len(jobs)} total rows parsed")
        except Exception as e:
            print(f"  WARNING: could not fetch {filename}: {e}")

    for list_url in SIMPLIFY_LISTS:
        try:
            jobs = fetch_simplify_jobs(list_url)
            all_jobs.extend(jobs)
            print(f"  {list_url}: {len(jobs)} postings parsed")
        except Exception as e:
            print(f"  WARNING: could not fetch {list_url}: {e}")

    new_jobs = [j for j in all_jobs if job_id(j) not in seen]

    print(f"  Jobs in watched sections: {len(all_jobs)} | New this run: {len(new_jobs)}")

    if new_jobs:
        send_email(new_jobs)
        for j in new_jobs:
            seen.add(job_id(j))
        save_seen(seen)
    else:
        print("  No new jobs. Nothing sent.")


if __name__ == "__main__":
    run()
