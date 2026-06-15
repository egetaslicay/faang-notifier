# FAANG Job Notifier

Get an email the moment a new FAANG / quant **internship** posting shows up in the
community-maintained [speedyapply/2026-SWE-College-Jobs](https://github.com/speedyapply/2026-SWE-College-Jobs)
job list — fully automated, running on free GitHub Actions. No server, no always-on PC.

## What it does

- Polls the source repo's markdown job tables on a schedule (default: every 5 minutes).
- Tracks only the sections you care about (`FAANG+` and `Quant` by default).
- Watches **internship** listings only — New Grad files are intentionally excluded.
- Remembers what it has already seen (`seen_jobs.json`, committed back after each run),
  so you're emailed **only when a genuinely new posting appears** — no duplicates.
- Each alert includes the company, role, posting age (e.g. `4d`), source file, and a
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

## Notes

- **Billing:** Actions minutes are free/unlimited on public repos. On a private repo you
  get 2,000 free minutes/month — frequent schedules can exceed that, so keep it public or
  reduce the frequency.
- **Scheduler lag:** GitHub's cron often fires a few minutes late and may skip ticks under
  load. "Every 5 minutes" is really "roughly every 5–15 minutes." This is normal.
- **State:** `seen_jobs.json` is committed back to the repo by the workflow; deleting it
  resets the notifier (the next run will re-send everything currently listed).

## License

MIT 
