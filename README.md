# FAANG Job Notifier

Get an email the moment a new FAANG / quant **internship** posting shows up in the
community-maintained [speedyapply/2026-SWE-College-Jobs](https://github.com/speedyapply/2026-SWE-College-Jobs)
job list — fully automated, running on free GitHub Actions. No server, no always-on PC.

## What it does

- Polls two sources on a schedule (default: every 5 minutes) — the source repo's markdown
  job tables, and the curated Simplify.jobs list pages.
- Tracks only the sections you care about (`FAANG+` and `Quant` by default). The Simplify
  lists are already big-tech-only, so every posting on them is tracked.
- Watches **internship** listings only — New Grad files are intentionally excluded.
- Remembers what it has already seen (`seen_jobs.json`, committed back after each run),
  so you're emailed **only when a genuinely new posting appears** — no duplicates.
- **Deduplicates across sources**, so a posting carried by both the GitHub list and
  Simplify emails you once, not twice (see below).
- Each alert includes the company, role, location, posting age (e.g. `4d`), source, and a
  direct **apply link**.

## How it works

GitHub Actions spins up a fresh VM on each scheduled run, installs Python + `requests`,
runs `faang_notifier.py`, sends the email via Gmail SMTP, then commits the updated
`seen_jobs.json` back to the repo. Everything runs in GitHub's cloud — your computer can
be off.

## Setup (use it yourself)

1. **Fork** this repository (or click *Use this template* / copy the files).

2. **Create a Gmail App Password** (not your normal password):
   - Enable 2-Step Verification on your Google account.
   - Go to <https://myaccount.google.com/apppasswords>, create one, copy the 16-character value.

3. **Add three repository secrets** — *Settings → Secrets and variables → Actions → New repository secret*:

   | Secret | Value |
   |---|---|
   | `EMAIL_SENDER` | the Gmail address you send **from** |
   | `EMAIL_PASSWORD` | the 16-char Gmail App Password |
   | `EMAIL_RECEIVER` | the address you want alerts sent **to** |

   These are encrypted and never appear in the code or logs. The script reads them from
   environment variables — nothing is hardcoded.

4. **Enable Actions** on your fork (*Actions* tab → enable workflows). Done — it now runs
   on the schedule.

### Run it manually / test it

In the **Actions** tab, open *Daily FAANG Notifier* → **Run workflow**. Or locally:

```bash
pip install requests
export EMAIL_SENDER="you@gmail.com"
export EMAIL_PASSWORD="your app password"
export EMAIL_RECEIVER="you@gmail.com"
python faang_notifier.py
```

## Configuration

All knobs are near the top of [`faang_notifier.py`](faang_notifier.py):

- `WATCHED_SECTIONS` — markdown section headers to track (default `["faang", "quant"]`,
  case-insensitive partial match).
- `FILES_TO_WATCH` — which source files to scan. Add `NEW_GRAD_USA.md` /
  `NEW_GRAD_INTL.md` if you also want New Grad roles.
- **Schedule** — edit the `cron` in [`.github/workflows/daily.yml`](.github/workflows/daily.yml).
  `*/5 * * * *` is every 5 minutes (the GitHub minimum); `0 9 * * *` is daily at 09:00 UTC.

## How duplicates are avoided

The two sources describe the same posting differently — different ids, different link
targets — so tracking each source's own key alone would email you twice. Every posting
therefore also gets **identity keys** computed the same way whatever the source, and it is
only emailed if none of its keys has been claimed before:

| Key | Matches on |
|---|---|
| `url::…` | the apply link, minus scheme, `www.`, tracking params (`utm_*`, `gh_src`, `ref`, …) and trailing slash. Only used for links that point at a single posting — a careers root or a Simplify list page is ignored. |
| `title::…` | company + role + city, with case and punctuation flattened. |

The matching is deliberately **conservative — it would rather send you a duplicate than
swallow a real posting**:

- No words are dropped from the role. `SWE Intern - Ads Interface` and
  `SWE Intern - Commerce Ads` stay distinct, as do 2026 and 2027 postings.
- The city is part of the key, because firms like Jane Street run the identical title in
  New York, London, Singapore and Hong Kong — four separate jobs. It's matched coarsely
  (text before the first comma, first two words) so `New York City, NY` and
  `New York, NY, USA` still count as one place.
- Where the two sources word a location differently enough to miss, you get one extra
  email — never a missed job.

When a duplicate *is* caught, the two rows are merged: the email shows whichever location
and apply link is present, and credits both sources (`README.md + Big Tech SWE Internships`).

Existing `seen_jobs.json` files keep working — identity keys are filled in as each posting
is next seen, so switching to this costs no re-notification.

## Notes

- **Billing:** Actions minutes are free/unlimited on public repos. On a private repo you
  get 2,000 free minutes/month — frequent schedules can exceed that, so keep it public or
  reduce the frequency.
- **Scheduler lag:** GitHub's cron often fires a few minutes late and may skip ticks under
  load. "Every 5 minutes" is really "roughly every 5–15 minutes." This is normal.
- **State:** `seen_jobs.json` is committed back to the repo by the workflow; deleting it
  resets the notifier (the next run will re-send everything currently listed). It is
  written sorted, so it is only re-committed when the contents actually change.

## License

MIT 
