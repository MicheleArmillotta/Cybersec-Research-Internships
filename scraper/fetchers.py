"""Fetchers for the various career-site backends.

Each fetcher returns a list of raw postings: dicts with at least
  title, url, location (optional), posted (optional ISO date), extra (optional text used
  for classification, e.g. department/team).
"""
import re
import time
import logging
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("fetchers")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
TIMEOUT = 40

_session = requests.Session()
_session.headers.update({
    "User-Agent": UA,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
})


class SourceError(Exception):
    pass


def _get(url, **kw):
    r = _session.get(url, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


def _post(url, **kw):
    r = _session.post(url, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


def _first_ok(candidates, fn):
    """Try fn(candidate) for each candidate; return (candidate, result) of the first success."""
    errors = []
    for c in candidates:
        try:
            res = fn(c)
            return c, res
        except Exception as e:  # noqa: BLE001
            errors.append(f"{c}: {type(e).__name__}: {str(e)[:120]}")
    raise SourceError("; ".join(errors))


def _post_item(title, url, location="", posted=None, extra=""):
    return {"title": (title or "").strip(), "url": url, "location": (location or "").strip(),
            "posted": posted, "extra": extra or ""}


# ---------------------------------------------------------------------------
# Generic ATS backends
# ---------------------------------------------------------------------------

def greenhouse(cfg):
    def one(slug):
        data = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false").json()
        jobs = data.get("jobs")
        if jobs is None:
            raise SourceError("no jobs key")
        return jobs
    slug, jobs = _first_ok(_aslist(cfg["slug"]), one)
    out = []
    for j in jobs:
        deps = " ".join(d.get("name", "") for d in j.get("departments", []) or [])
        out.append(_post_item(j.get("title"), j.get("absolute_url"),
                              (j.get("location") or {}).get("name", ""),
                              (j.get("updated_at") or "")[:10], deps))
    return out, f"slug={slug}"


def lever(cfg):
    def one(slug):
        data = _get(f"https://api.lever.co/v0/postings/{slug}?mode=json").json()
        if not isinstance(data, list):
            raise SourceError("unexpected payload")
        return data
    slug, jobs = _first_ok(_aslist(cfg["slug"]), one)
    out = []
    for j in jobs:
        cat = j.get("categories") or {}
        extra = " ".join(str(cat.get(k, "")) for k in ("team", "department", "commitment"))
        posted = None
        if j.get("createdAt"):
            posted = time.strftime("%Y-%m-%d", time.gmtime(j["createdAt"] / 1000))
        out.append(_post_item(j.get("text"), j.get("hostedUrl"), cat.get("location", ""), posted, extra))
    return out, f"slug={slug}"


def ashby(cfg):
    def one(slug):
        data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false").json()
        jobs = data.get("jobs")
        if jobs is None:
            raise SourceError("no jobs key")
        return jobs
    slug, jobs = _first_ok(_aslist(cfg["slug"]), one)
    out = []
    for j in jobs:
        if j.get("isListed") is False:
            continue
        extra = " ".join(str(j.get(k, "")) for k in ("department", "team", "employmentType"))
        out.append(_post_item(j.get("title"), j.get("jobUrl"), j.get("location", ""),
                              (j.get("publishedAt") or "")[:10], extra))
    return out, f"slug={slug}"


def workable(cfg):
    def one(slug):
        url = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"
        body = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
        results, token = [], None
        for _ in range(20):
            if token:
                body["token"] = token
            data = _post(url, json=body).json()
            results.extend(data.get("results", []))
            token = data.get("nextPage")
            if not token:
                break
        if not results and "results" not in data:
            raise SourceError("unexpected payload")
        return [(slug, r) for r in results]
    slug, jobs = _first_ok(_aslist(cfg["slug"]), one)
    out = []
    for s, j in jobs:
        loc = j.get("location") or {}
        loc_txt = ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
        if j.get("remote"):
            loc_txt = (loc_txt + " (Remote)").strip()
        out.append(_post_item(j.get("title"), f"https://apply.workable.com/{s}/j/{j.get('shortcode')}/",
                              loc_txt, (j.get("published") or "")[:10], j.get("department", "")))
    return out, f"slug={slug}"


def workday(cfg):
    """cfg: host (e.g. nvidia.wd5.myworkdayjobs.com), tenant, site (str or list), queries."""
    host, tenant = cfg["host"], cfg["tenant"]
    queries = cfg.get("queries") or ["intern"]

    def one(site):
        url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        found = {}
        for q in queries:
            offset = 0
            for _ in range(15):
                body = {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": q}
                r = _session.post(url, json=body, timeout=TIMEOUT,
                                  headers={"Accept": "application/json", "Content-Type": "application/json"})
                if r.status_code == 404:
                    raise SourceError("404")
                r.raise_for_status()
                data = r.json()
                posts = data.get("jobPostings", [])
                for p in posts:
                    path = p.get("externalPath", "")
                    full = f"https://{host}/en-US/{site}{path}"
                    found[full] = _post_item(p.get("title"), full, p.get("locationsText", ""),
                                             None, " ".join(p.get("bulletFields", []) or []))
                total = data.get("total", 0)
                offset += 20
                if offset >= total or not posts:
                    break
        return list(found.values())
    candidates = list(_aslist(cfg["site"]))
    # auto-discover the site name from the tenant root redirect (e.g. https://host/ -> /en-US/<site>)
    try:
        r = _session.get(f"https://{host}/", timeout=TIMEOUT, allow_redirects=True)
        m = re.search(r"myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)", r.url)
        if m and m.group(1) not in candidates and m.group(1) != "wday":
            candidates.append(m.group(1))
        for m in re.finditer(r'href="/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_\-]+)/?"', r.text[:20000]):
            if m.group(1) not in candidates and m.group(1).lower() not in ("wday", "login", "en-us"):
                candidates.append(m.group(1))
                if len(candidates) > 8:
                    break
    except Exception:  # noqa: BLE001
        pass
    site, jobs = _first_ok(candidates, one)
    return jobs, f"site={site}"


def smartrecruiters(cfg):
    queries = cfg.get("queries") or ["intern"]

    def one(company):
        found = {}
        for q in queries:
            offset = 0
            for _ in range(10):
                data = _get(f"https://api.smartrecruiters.com/v1/companies/{company}/postings"
                            f"?q={quote(q)}&limit=100&offset={offset}").json()
                content = data.get("content")
                if content is None:
                    raise SourceError("no content key")
                for j in content:
                    loc = j.get("location") or {}
                    loc_txt = ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
                    url = f"https://jobs.smartrecruiters.com/{company}/{j.get('id')}"
                    found[url] = _post_item(j.get("name"), url, loc_txt,
                                            (j.get("releasedDate") or "")[:10],
                                            str((j.get("department") or {}).get("label", "")))
                offset += 100
                if offset >= data.get("totalFound", 0):
                    break
        return list(found.values())
    company, jobs = _first_ok(_aslist(cfg["slug"]), one)
    return jobs, f"company={company}"


def eightfold(cfg):
    host, domain = cfg["host"], cfg["domain"]
    queries = cfg.get("queries") or ["intern"]
    found = {}
    for q in queries:
        start = 0
        for _ in range(10):
            data = _get(f"https://{host}/api/apply/v2/jobs?domain={domain}&query={quote(q)}"
                        f"&start={start}&num=100&sort_by=relevance").json()
            positions = data.get("positions")
            if positions is None:
                raise SourceError("no positions key")
            for p in positions:
                url = p.get("canonicalPositionUrl") or f"https://{host}/careers?pid={p.get('id')}&domain={domain}"
                found[url] = _post_item(p.get("name"), url, p.get("location", ""), None,
                                        str(p.get("department", "")))
            start += 100
            if start >= data.get("count", 0) or not positions:
                break
    return list(found.values()), ""


def oracle_hcm(cfg):
    host, site = cfg["host"], cfg["site"]
    queries = cfg.get("queries") or ["intern"]
    found = {}
    for q in queries:
        url = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
               f"?onlyData=true&expand=requisitionList&finder=findReqs;siteNumber={site},"
               f"keyword={quote(q)},limit=200,offset=0")
        data = _get(url, headers={"Accept": "application/json"}).json()
        items = data.get("items") or []
        reqs = items[0].get("requisitionList", []) if items else []
        for r in reqs:
            jid = r.get("Id")
            u = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{jid}"
            found[u] = _post_item(r.get("Title"), u, r.get("PrimaryLocation", ""),
                                  (r.get("PostedDate") or "")[:10], "")
    return list(found.values()), ""


# ---------------------------------------------------------------------------
# Company-specific backends
# ---------------------------------------------------------------------------

def amazon(cfg):
    queries = cfg.get("queries") or ["intern"]
    found = {}
    for q in queries:
        offset = 0
        for _ in range(15):
            data = _get(f"https://www.amazon.jobs/en/search.json?base_query={quote(q)}"
                        f"&offset={offset}&result_limit=100&sort=recent").json()
            jobs = data.get("jobs")
            if jobs is None:
                raise SourceError("no jobs key")
            for j in jobs:
                url = "https://www.amazon.jobs" + j.get("job_path", "")
                found[url] = _post_item(j.get("title"), url,
                                        j.get("normalized_location") or j.get("location", ""),
                                        (j.get("posted_date") or "")[:10],
                                        " ".join(str(j.get(k, "")) for k in ("job_category", "job_schedule_type", "business_category")))
            offset += 100
            if offset >= data.get("hits", 0) or not jobs:
                break
    return list(found.values()), ""


def microsoft(cfg):
    queries = cfg.get("queries") or ["intern"]
    found = {}
    for q in queries:
        pg = 1
        for _ in range(25):
            path = f"/search/api/v1/search?q={quote(q)}&l=en_us&pg={pg}&pgSz=20&o=Recent&flt=true"
            data, last = None, None
            for host, verify in (("gcsservices.careers.microsoft.com", True), ("apijobs.careers.microsoft.com", True),
                                 ("gcsservices.careers.microsoft.com", False)):
                try:
                    r = _session.get(f"https://{host}{path}", timeout=TIMEOUT, verify=verify,
                                     headers={"Origin": "https://jobs.careers.microsoft.com",
                                              "Referer": "https://jobs.careers.microsoft.com/"})
                    r.raise_for_status()
                    data = r.json()
                    break
                except Exception as e:  # noqa: BLE001
                    last = e
            if data is None:
                raise SourceError(f"all hosts failed: {type(last).__name__}: {str(last)[:150]}")
            res = (data.get("operationResult") or {}).get("result") or {}
            jobs = res.get("jobs")
            if jobs is None:
                raise SourceError("no jobs key")
            for j in jobs:
                jid = j.get("jobId")
                url = f"https://jobs.careers.microsoft.com/global/en/job/{jid}"
                props = j.get("properties") or {}
                loc = props.get("primaryLocation") or ", ".join(props.get("locations", []) or [])
                extra = " ".join(str(props.get(k, "")) for k in ("employmentType", "profession", "discipline"))
                found[url] = _post_item(j.get("title"), url, loc, (j.get("postingDate") or "")[:10], extra)
            total = res.get("totalResults", 0)
            pg += 1
            if (pg - 1) * 20 >= total or not jobs:
                break
    return list(found.values()), ""


def apple(cfg):
    queries = cfg.get("queries") or ["intern"]
    r = _session.get("https://jobs.apple.com/api/csrfToken", timeout=TIMEOUT,
                     headers={"Referer": "https://jobs.apple.com/en-us/search"})
    csrf = r.headers.get("X-Apple-CSRF-Token") or r.headers.get("x-apple-csrf-token") or ""
    found = {}
    for q in queries:
        page = 1
        for _ in range(10):
            body = {"query": q, "filters": {}, "page": page, "locale": "en-us", "sort": "newest",
                    "format": {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"}}
            r = _session.post("https://jobs.apple.com/api/v1/search", json=body, timeout=TIMEOUT,
                              headers={"Content-Type": "application/json", "X-Apple-CSRF-Token": csrf,
                                       "Referer": "https://jobs.apple.com/en-us/search"})
            r.raise_for_status()
            data = r.json()
            res = data.get("res") if isinstance(data.get("res"), dict) else data
            results = res.get("searchResults")
            if results is None:
                raise SourceError(f"no searchResults key (keys={list(data)[:6]})")
            for j in results:
                pid = j.get("positionId") or j.get("id")
                url = f"https://jobs.apple.com/en-us/details/{pid}"
                loc = ", ".join(l.get("name", "") for l in (j.get("locations") or [])[:2])
                team = j.get("team") or {}
                found[url] = _post_item(j.get("postingTitle") or j.get("title"), url, loc,
                                        (j.get("postingDate") or "")[:10],
                                        team.get("teamName", "") if isinstance(team, dict) else "")
            total = res.get("totalRecords", 0)
            page += 1
            if (page - 1) * 20 >= total or not results:
                break
    return list(found.values()), ""


def google(cfg):
    queries = cfg.get("queries") or ["intern"]
    found = {}
    for q in queries:
        for page in range(1, 15):
            url = ("https://www.google.com/about/careers/applications/jobs/results"
                   f"?q={quote(q)}&page={page}")
            html = _get(url, headers={"Accept": "text/html"}).text
            soup = BeautifulSoup(html, "lxml")
            n_before = len(found)
            for a in soup.select("a[href*='jobs/results/']"):
                href = a.get("href", "")
                m = re.search(r"jobs/results/(\d+)-([^?/\"']+)", href)
                if not m:
                    continue
                jid = m.group(1)
                full = f"https://www.google.com/about/careers/applications/jobs/results/{jid}-{m.group(2)}"
                # title: try the aria-label ("Learn more about X") or nearby heading, else slug
                title = (a.get("aria-label") or "").replace("Learn more about", "").strip()
                if not title:
                    h = a.find_parent().find(["h2", "h3"]) if a.find_parent() else None
                    title = h.get_text(" ", strip=True) if h else m.group(2).replace("-", " ").title()
                loc = ""
                card = a.find_parent("li")
                if card:
                    for t in card.stripped_strings:
                        if t == title or len(t) > 80:
                            continue
                        if re.search(r"\b(USA|UK|Ireland|Switzerland|Germany|France|India|Canada|Poland|Japan|"
                                     r"Israel|Australia|Brazil|Singapore|Taiwan|Remote)\b|, [A-Z]{2}\b", t):
                            loc = t
                            break
                found[full] = _post_item(title, full, loc)
            if len(found) == n_before:
                break
    if not found:
        raise SourceError("no job links parsed (page layout may have changed)")
    return list(found.values()), ""


def meta(cfg):
    queries = cfg.get("queries") or ["intern"]
    found = {}
    for q in queries:
        html = ""
        for u in (f"https://www.metacareers.com/jobs/?q={quote(q)}", f"https://www.metacareers.com/jobs?q={quote(q)}",
                  "https://www.metacareers.com/jobs/"):
            try:
                html = _get(u, headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                        "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
                                        "Upgrade-Insecure-Requests": "1"}).text
                break
            except Exception as e:  # noqa: BLE001
                last = e
        if not html:
            raise SourceError(f"all urls failed: {last}")
        # Meta embeds job data in script tags; grab id+title pairs defensively.
        for m in re.finditer(r'"id":"(\d{6,})"[^{}]{0,400}?"title":"([^"]+)"', html):
            jid, title = m.group(1), m.group(2)
            url = f"https://www.metacareers.com/jobs/{jid}/"
            found[url] = _post_item(title.encode().decode("unicode_escape", "ignore"), url)
        for m in re.finditer(r'"title":"([^"]+)"[^{}]{0,400}?"id":"(\d{6,})"', html):
            title, jid = m.group(1), m.group(2)
            url = f"https://www.metacareers.com/jobs/{jid}/"
            found.setdefault(url, _post_item(title.encode().decode("unicode_escape", "ignore"), url))
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href*='/jobs/']"):
            m = re.search(r"/jobs/(\d{6,})", a.get("href", ""))
            if m:
                url = f"https://www.metacareers.com/jobs/{m.group(1)}/"
                found.setdefault(url, _post_item(a.get_text(" ", strip=True), url))
    if not found:
        raise SourceError("no jobs parsed (JS-rendered page)")
    return list(found.values()), ""


def cisco(cfg):
    queries = cfg.get("queries") or ["intern"]
    found = {}
    for q in queries:
        offset = 0
        for _ in range(15):
            url = f"https://jobs.cisco.com/jobs/SearchJobs/{quote(q)}?listFilterMode=1&projectOffset={offset}"
            soup = BeautifulSoup(_get(url, headers={"Accept": "text/html"}).text, "lxml")
            n_before = len(found)
            for a in soup.select("a[href*='ProjectDetail']"):
                title = a.get_text(" ", strip=True)
                if not title:
                    continue
                loc = ""
                tr = a.find_parent("tr")
                if tr:
                    tds = tr.find_all("td")
                    loc = tds[1].get_text(" ", strip=True) if len(tds) > 1 else ""
                href = urljoin("https://jobs.cisco.com", a.get("href"))
                found.setdefault(href, _post_item(title, href, loc))
            if len(found) == n_before:
                break
            offset += 25
    if not found:
        raise SourceError(f"no ProjectDetail links parsed (html {len(soup.get_text())} chars)")
    return list(found.values()), ""


def tesla(cfg):
    data = _get("https://www.tesla.com/cua-api/apps/careers/state",
                headers={"Accept": "application/json", "Referer": "https://www.tesla.com/careers/search/"}).json()
    listings = data.get("listings") or []
    lookup = data.get("lookup") or {}
    locs = lookup.get("locations") or {}
    deps = lookup.get("departments") or {}
    out = []
    for j in listings:
        jid = j.get("id")
        title = j.get("t") or j.get("title") or ""
        l = j.get("l")
        if isinstance(l, list):
            loc = ", ".join(str(_lk(locs, x)) for x in l[:2])
        else:
            loc = str(_lk(locs, l))
        url = f"https://www.tesla.com/careers/search/job/{jid}"
        out.append(_post_item(title, url, loc, None, str(_lk(deps, j.get("y")))))
    if not out:
        raise SourceError("no listings")
    return out, ""


def _lk(table, key):
    if key is None:
        return ""
    if isinstance(table, dict):
        v = table.get(str(key), table.get(key, ""))
    elif isinstance(table, list) and isinstance(key, int) and key < len(table):
        v = table[key]
    else:
        v = ""
    if isinstance(v, dict):
        return v.get("name") or v.get("title") or ""
    return v


def ibm(cfg):
    queries = cfg.get("queries") or ["intern"]
    found = {}
    errors = []
    for q in queries:
        bodies = [
            {"appId": "careers", "scopes": ["careers2"],
             "query": {"bool": {"must": [{"multi_match": {"query": q, "fields": ["title", "description"]}}]}},
             "size": 100, "from": 0, "sort": [{"dateModified": "desc"}],
             "_source": ["title", "url", "field_keyword_08", "field_keyword_18", "field_keyword_19", "field_keyword_17", "field_keyword_05"]},
            {"appId": "careers", "scopes": ["careers"], "query": {"bool": {"must": [{"query_string": {"query": q}}]}},
             "size": 100, "from": 0},
        ]
        for body in bodies:
            try:
                r = _session.post("https://www-api.ibm.com/search/api/v2", json=body, timeout=TIMEOUT,
                                  headers={"Content-Type": "application/json", "Origin": "https://www.ibm.com",
                                           "Referer": "https://www.ibm.com/careers/search"})
                r.raise_for_status()
                data = r.json()
            except Exception as e:  # noqa: BLE001
                errors.append(f"{type(e).__name__}: {str(e)[:80]}")
                continue
            hits = (data.get("hits") or {}).get("hits") or []
            for h in hits:
                src = h.get("_source", h)
                url = src.get("url") or ""
                if not url:
                    continue
                loc = src.get("field_keyword_05") or ""
                loc = ", ".join(loc) if isinstance(loc, list) else str(loc)
                found[url] = _post_item(src.get("title", ""), url, loc, None, str(src.get("field_keyword_08", "")))
            if hits:
                break
    if not found:
        raise SourceError("no hits; " + "; ".join(errors)[:200])
    return list(found.values()), ""


# ---------------------------------------------------------------------------
# Generic HTML page scan (fallback for small firms / academic labs)
# ---------------------------------------------------------------------------

_MENTION = re.compile(r"intern|internship|stage|stagiaire|praktik|werkstudent|student|phd|fellow|summer", re.I)


def page_scan(cfg):
    """Fetch one or more pages, return links whose text/href mention internships."""
    pages = _aslist(cfg["url"])
    found = {}
    n_pages_ok = 0
    errors = []
    for page in pages:
        try:
            html = _get(page, headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                                       "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}).text
        except Exception as e:  # noqa: BLE001
            errors.append(f"{page}: {type(e).__name__} {str(e)[:60]}")
            log.warning("page_scan %s failed: %s", page, e)
            continue
        n_pages_ok += 1
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            text = a.get_text(" ", strip=True)
            href = a["href"]
            if len(text) < 4 or len(text) > 160:
                continue
            if _MENTION.search(text) or _MENTION.search(href):
                full = urljoin(page, href)
                if full.startswith("mailto:") or full.startswith("javascript:"):
                    continue
                found.setdefault(full, _post_item(text, full, "", None, "page-scan"))
        # also headings mentioning internships (some pages list positions without links)
        for h in soup.find_all(["h1", "h2", "h3", "h4"]):
            text = h.get_text(" ", strip=True)
            if 4 <= len(text) <= 160 and _MENTION.search(text):
                found.setdefault(page + "#" + re.sub(r"\W+", "-", text.lower())[:60],
                                 _post_item(text, page, "", None, "page-scan"))
    if n_pages_ok == 0:
        raise SourceError("all pages failed: " + " | ".join(errors))
    note = f"pages_ok={n_pages_ok}/{len(pages)}" + (" | " + " | ".join(errors) if errors else "")
    return list(found.values()), note


def ats_any(cfg):
    """cfg['candidates']: list of {"type": "greenhouse"|"lever"|"ashby"|"workable", "slug": "..."}."""
    errors = []
    for c in cfg["candidates"]:
        try:
            jobs, note = FETCHERS[c["type"]]({**cfg, **c})
            if jobs:
                return jobs, f"{c['type']} {note}"
            errors.append(f"{c['type']}:{c['slug']} empty")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{c['type']}:{c['slug']} {type(e).__name__}")
    raise SourceError("; ".join(errors))


def _aslist(x):
    return x if isinstance(x, list) else [x]


FETCHERS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "workable": workable,
    "workday": workday,
    "smartrecruiters": smartrecruiters,
    "eightfold": eightfold,
    "oracle_hcm": oracle_hcm,
    "amazon": amazon,
    "microsoft": microsoft,
    "apple": apple,
    "google": google,
    "meta": meta,
    "cisco": cisco,
    "tesla": tesla,
    "ibm": ibm,
    "page_scan": page_scan,
    "ats_any": ats_any,
}
