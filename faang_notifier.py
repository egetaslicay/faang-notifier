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
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

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

        # Column 3 is Location in both files (README then has a Salary column,
        # INTERN_INTL does not). Several firms post the same title in several
        # cities, so this is what tells those rows apart.
        location = cells[2] if len(cells) > 2 else ""
        location = re.sub(r"<[^>]+>", " ", location)
        location = re.sub(r"[*_`]", "", location)
        location = re.sub(r"\s+", " ", location).strip()

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
                "location": location,
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
            # The employer's own application URL, when the payload carries it.
            # It is what the GitHub rows link to, so it is the one field that
            # can match the same posting across both sources exactly.
            "apply_url": first_url(hit, APPLY_URL_FIELDS),
            "source": source,
        })

    return jobs

# ── CROSS-SOURCE DEDUPING ────────────────────────────────────────────────────
# The same posting shows up on both the GitHub list and the Simplify list, and
# the two describe it differently (different ids, different link targets), so
# the per-source key alone would email about it twice. Each job therefore also
# gets "identity" keys that are computed the same way whatever the source; a
# job is a duplicate if any of its keys has been claimed already.

# Simplify's JSON has changed shape before, so try a few plausible names rather
# than betting on one.
APPLY_URL_FIELDS = (
    "job_url", "url", "apply_url", "application_url", "external_url", "link",
)

# Stripped before comparing links: same posting, different referral tagging.
# Deliberately excludes anything that identifies the posting itself -- gh_jid,
# for one, is Greenhouse's job id, and dropping it makes every job at a board
# look like the same URL.
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gh_src", "ref", "referrer", "source", "src",
}


def first_url(hit, fields):
    for field in fields:
        value = hit.get(field)
        if isinstance(value, str) and value.startswith("http"):
            return value.strip()
    return ""


def normalize_url(url):
    """
    Reduce a link to something stable enough to compare across sources: drop
    the scheme, "www.", tracking params and any trailing slash.

    Real query params are kept -- plenty of boards identify the posting with
    one (?jid=123), so dropping the query wholesale would collapse unrelated
    jobs at the same careers page into a single key.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""
    if not parts.netloc:
        return ""

    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    query = urlencode(sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    ))
    path = parts.path.rstrip("/")

    return urlunsplit(("", host, path, query, "")).lstrip("/")


def normalize_text(value):
    """Lowercase and flatten punctuation/spacing differences, nothing more."""
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def location_key(value):
    """
    Coarsen a location to something both sources agree on. The two write the
    same place differently -- "New York City, NY" vs "New York, NY, USA",
    "London, United Kingdom" vs "London, UK" -- so keep only the text before
    the first comma and at most its first two words.

    Coarse on purpose: this only has to tell Chicago from London, and being any
    more precise would make the same posting look like two.
    """
    head = (value or "").split("/")[0].split(",")[0]
    return " ".join(normalize_text(head).split()[:2])


def title_key(company, role, location=""):
    company, role = normalize_text(company), normalize_text(role)
    # Words are never dropped from the role: two postings that differ only in
    # team or year ("... - Ads Interface" vs "... - Commerce Ads") are genuinely
    # different jobs, and merging them would silently swallow an alert.
    if not company or not role:
        return ""
    # Same reasoning for the city: Jane Street runs the identical title in New
    # York, London, Singapore and Hong Kong, and each is its own posting.
    return f"title::{company}::{role}::{location_key(location)}"


def url_key(url):
    """
    A link is only usable as an identity key if it points at one posting.
    A careers root or a list page ("simplify.jobs/l/...") would otherwise make
    every job behind it look like the same one, so those are rejected and the
    job falls back to matching on company/role/location.
    """
    normalized = normalize_url(url)
    if not normalized:
        return ""

    path, _, query = normalized.partition("?")
    segments = [s for s in path.split("/")[1:] if s]

    if path.startswith("simplify.jobs/") and segments[:1] != ["p"]:
        return ""
    if len(segments) < 2 and not query:
        return ""

    return f"url::{normalized}"


def job_keys(job):
    """Every key that identifies this posting, across sources."""
    keys = {job_id(job)}

    for url in (job.get("apply_url"), job.get("link")):
        key = url_key(url)
        if key:
            keys.add(key)

    key = title_key(job.get("company"), job.get("role"), job.get("location"))
    if key:
        keys.add(key)

    return keys


def merge_duplicate(kept, duplicate):
    """
    Fold a duplicate's details into the row we're actually emailing: whichever
    source saw it first may be the one missing a location or an apply link.
    """
    for field in ("location", "age", "link"):
        if not kept.get(field) and duplicate.get(field):
            kept[field] = duplicate[field]

    # Recorded alongside "source" rather than rewritten into it, because
    # job_id() keys GitHub rows on their source file.
    extras = kept.setdefault("extra_sources", [])
    other = duplicate.get("source")
    if other and other != kept.get("source") and other not in extras:
        extras.append(other)


def describe_sources(job):
    return " + ".join([job.get("source", "")] + job.get("extra_sources", [])).strip(" +")


def select_new_jobs(all_jobs, seen):
    """
    Returns the postings to email plus the updated seen set. A posting is
    emailed only if none of its keys were claimed by a previous run or by an
    earlier job in this one.
    """
    new_jobs = []
    known = set(seen)
    claimed = {}   # key -> the job accepted this run that owns it

    for job in all_jobs:
        keys = job_keys(job)

        owner = next((claimed[k] for k in keys if k in claimed), None)
        if owner is not None:
            merge_duplicate(owner, job)
            # Record the duplicate's own keys too, so later runs match on them
            # directly instead of relying on the titles lining up again.
            claimed.update({k: owner for k in keys})
            known |= keys
            continue

        if keys & known:
            # Already known, but possibly under only one source's key. Record
            # the rest so the other source's copy is recognised when it turns
            # up -- this is also what migrates state saved before deduping.
            known |= keys
            continue

        new_jobs.append(job)
        claimed.update({k: job for k in keys})
        known |= keys

    return new_jobs, known

# ── STATE TRACKING ───────────────────────────────────────────────────────────

def load_seen():
    # State written before deduping existed holds only per-source keys; it stays
    # valid, and select_new_jobs fills in the identity keys as it re-sees each
    # posting, so no run re-notifies anything.
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    # Sorted so the file only changes when the contents do -- set iteration
    # order varies per process, which otherwise rewrites (and re-commits) the
    # whole file on every run.
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(seen), f)


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
            f"<td>{age}</td><td>{describe_sources(j)}</td><td>{link_text}</td></tr>"
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
    on_disk = set(seen)
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

    new_jobs, seen = select_new_jobs(all_jobs, seen)

    print(f"  Jobs in watched sections: {len(all_jobs)} | New this run: {len(new_jobs)}")

    if new_jobs:
        send_email(new_jobs)
    else:
        print("  No new jobs. Nothing sent.")

    # Also saved when nothing was emailed: deduping can learn identity keys for
    # postings that were already known under a different source's key.
    if seen != on_disk:
        save_seen(seen)


if __name__ == "__main__":
    run()
