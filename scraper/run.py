import json
import hashlib
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from .sources import SOURCES
from .fetchers import FETCHERS
from .classify import is_internship, tags_for

log = logging.getLogger("run")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "data")
JOBS_PATH = os.path.join(DATA_DIR, "jobs.json")
STATUS_PATH = os.path.join(DATA_DIR, "status.json")


def _id(url):
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def _load_prev():
    try:
        with open(JOBS_PATH) as f:
            return {j["id"]: j for j in json.load(f).get("jobs", [])}
    except Exception:  # noqa: BLE001
        return {}


def _run_source(cfg):
    t0 = time.time()
    status = {"company": cfg["company"], "group": cfg["group"], "type": cfg["type"],
              "ok": False, "raw": 0, "kept": 0, "note": "", "error": ""}
    jobs = []
    try:
        fn = FETCHERS[cfg["type"]]
        raw, note = fn(cfg)
        status["note"] = note
        status["raw"] = len(raw)
        seen = set()
        for r in raw:
            if not r.get("url") or not r.get("title"):
                continue
            text = f"{r['title']} {r.get('extra', '')}"
            # page_scan items are already "mention-based"; keep them if the link text mentions internships.
            if not is_internship(text):
                continue
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            jobs.append({
                "id": _id(r["url"]),
                "company": cfg["company"],
                "group": cfg["group"],
                "title": r["title"][:200],
                "location": (r.get("location") or "")[:120],
                "url": r["url"],
                "posted": r.get("posted") or None,
                "tags": tags_for(f"{r['title']} {r.get('extra', '')} {cfg['company']}" if cfg["group"] == "boutique" else text),
                "via": cfg["type"],
            })
        status["ok"] = True
        status["kept"] = len(jobs)
    except Exception as e:  # noqa: BLE001
        status["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        log.warning("%s failed: %s", cfg["company"], status["error"])
        log.debug(traceback.format_exc())
    status["seconds"] = round(time.time() - t0, 1)
    return status, jobs


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    os.makedirs(DATA_DIR, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prev = _load_prev()

    only = os.environ.get("ONLY")  # comma-separated company names, for debugging
    sources = [s for s in SOURCES if not only or s["company"] in only.split(",")]

    statuses, all_jobs = [], {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_run_source, s): s for s in sources}
        for fut in as_completed(futs):
            status, jobs = fut.result()
            statuses.append(status)
            for j in jobs:
                p = prev.get(j["id"])
                j["first_seen"] = p["first_seen"] if p else today
                j["last_seen"] = today
                all_jobs[j["id"]] = j

    # Keep postings from sources that FAILED today (don't drop data because of a transient error).
    failed = {s["company"] for s in statuses if not s["ok"]}
    for jid, j in prev.items():
        if j["company"] in failed and jid not in all_jobs:
            all_jobs[jid] = j

    statuses.sort(key=lambda s: (s["ok"], s["company"]))
    jobs_list = sorted(all_jobs.values(), key=lambda j: (j["first_seen"], j["company"], j["title"]), reverse=True)

    with open(JOBS_PATH, "w") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "count": len(jobs_list), "jobs": jobs_list}, f, indent=1, ensure_ascii=False)
    with open(STATUS_PATH, "w") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "sources": statuses}, f, indent=1, ensure_ascii=False)

    ok = sum(1 for s in statuses if s["ok"])
    print(f"\n=== {ok}/{len(statuses)} sources ok, {len(jobs_list)} internship postings ===")
    for s in statuses:
        flag = "OK " if s["ok"] else "ERR"
        print(f"{flag} {s['company']:<34} {s['type']:<14} raw={s['raw']:<5} kept={s['kept']:<4} {s['note']} {s['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
