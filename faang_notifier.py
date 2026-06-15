"""
FAANG Job Notifier
Polls the speedyapply 2026-SWE-College-Jobs GitHub repo for new FAANG postings
and sends you an email when something new drops.

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
SEEN_FILE = "seen_jobs.json"   # local file to track what you've already been notified about

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

        cols = [c.strip() for c in line.split("|") if c.strip()]
        if len(cols) < 3:
            continue

        if cols[0].startswith("-") or cols[0].lower() in ("company", ""):
            continue

        company = re.sub(r"\[.*?\]\(.*?\)", "", cols[0]).strip()
        company = re.sub(r"[*_`]", "", company).strip()
        company = re.sub(r"<[^>]+>", "", company).strip()

        role = cols[1] if len(cols) > 1 else ""
        role = re.sub(r"\[.*?\]\(.*?\)", "", role).strip()
        role = re.sub(r"<[^>]+>", "", role).strip()

        link_match = re.search(r"\((https?://[^\)]+)\)", line)
        link = link_match.group(1) if link_match else ""

        if company:
            jobs.append({
                "company": company,
                "role": role,
                "link": link,
                "source": source_file,
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
    # unique key per job so we don't notify twice
    return f"{job['company']}::{job['role']}::{job['source']}"

# ── EMAIL ────────────────────────────────────────────────────────────────────

def send_email(new_jobs):
    subject = f"[FAANG Alert] {len(new_jobs)} new job(s) dropped"

    rows = ""
    for j in new_jobs:
        link_text = f'<a href="{j["link"]}">Apply</a>' if j["link"] else "No link"
        rows += f"<tr><td>{j['company']}</td><td>{j['role']}</td><td>{j['source']}</td><td>{link_text}</td></tr>"

    html = f"""
    <html><body>
    <h2>New FAANG/Target Company Jobs</h2>
    <p>Found {len(new_jobs)} new posting(s) as of {datetime.now().strftime('%Y-%m-%d %H:%M')}:</p>
    <table border="1" cellpadding="6" cellspacing="0">
        <tr><th>Company</th><th>Role</th><th>Source</th><th>Link</th></tr>
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
