# Cybersecurity & Research Internship Tracker

A small tracker that fetches internship postings (cybersecurity / research, plus a few false positives on purpose)
from ~120 companies every morning and publishes them as a sortable/filterable table on GitHub Pages.

- **Site:** https://michelearmillotta.github.io/Cybersec-Research-Internships/
- **Data:** `docs/data/jobs.json` (postings, with `first_seen` / `last_seen`) and `docs/data/status.json` (per-source health)
- **Schedule:** `.github/workflows/update.yml` runs daily at 06:00 UTC and commits the refreshed JSON.

## How it works

```
GitHub Actions (cron) -> python -m scraper -> docs/data/*.json -> git commit -> GitHub Pages
```

- `scraper/sources.py` — list of companies and which backend to use (Greenhouse, Lever, Ashby, Workable, Workday,
  SmartRecruiters, Eightfold, Oracle HCM, company-specific APIs, or a generic `page_scan` for small sites).
- `scraper/fetchers.py` — one fetcher per backend.
- `scraper/classify.py` — keeps postings that look like internships / student / PhD / fellowship roles and tags them
  `cyber`, `research`, `ai`, `other` by keywords. Tuned for recall over precision.
- `scraper/run.py` — runs everything in parallel, merges with the previous data (to keep `first_seen`), and keeps
  old postings from sources that failed today so a transient error doesn't wipe them.

## Known limitations

Some career sites block requests coming from GitHub's Actions runners (Microsoft, Meta, Tesla, Qualcomm, iCIMS-based
sites like AMD/Arm, and a few others). They are listed with direct links in the "Not trackable automatically" section
of the site instead. `docs/data/status.json` (and the "Source status" panel) shows which sources worked on the last run.

`.github/workflows/probe.yml` is a debugging helper: it runs an arbitrary shell command on a runner and publishes the
output to the `probe` branch. Handy when a source breaks and you need to see what the site actually returns.

## Run locally

```
pip install -r requirements.txt
python -m scraper                 # all sources
ONLY="Google,Anthropic" python -m scraper   # a subset, for debugging
```

## Adding a company

Append an entry to `scraper/sources.py`. For Greenhouse/Lever/Ashby you only need the board slug; `slug` accepts a
list of candidates and the first one that responds is used.
