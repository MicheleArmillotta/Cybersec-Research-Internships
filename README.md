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

## Run locally

```
pip install -r requirements.txt
python -m scraper                 # all sources
ONLY="Google,Anthropic" python -m scraper   # a subset, for debugging
```

## Adding a company

Append an entry to `scraper/sources.py`. For Greenhouse/Lever/Ashby you only need the board slug; `slug` accepts a
list of candidates and the first one that responds is used.
