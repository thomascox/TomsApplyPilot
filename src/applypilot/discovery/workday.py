"""Workday ATS direct API scraper: searches employer career portals.

Scrapes Workday-powered career sites (TD, RBC, NVIDIA, Salesforce, etc.)
via the undocumented CXS JSON API. Zero LLM, zero browser -- pure HTTP.

Employer registry is loaded from config/employers.yaml instead of being
hardcoded. Supports sequential search + detail fetching with proxy.
"""

import json
import logging
import re
import sqlite3
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import yaml

from applypilot import config
from applypilot.config import CONFIG_DIR
from applypilot.database import get_connection, init_db

log = logging.getLogger(__name__)


# -- Employer registry from YAML --------------------------------------------

def load_employers() -> dict:
    """Load Workday employer registry from config/employers.yaml."""
    path = CONFIG_DIR / "employers.yaml"
    if not path.exists():
        log.warning("employers.yaml not found at %s", path)
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("employers", {})


# -- Location filtering from search config -----------------------------------

def _load_location_filter(search_cfg: dict | None = None):
    """Load location accept/reject lists from search config."""
    if search_cfg is None:
        search_cfg = config.load_search_config()

    accept = search_cfg.get("location_accept", [])
    reject = search_cfg.get("location_reject_non_remote", [])
    return accept, reject


def _location_ok(location: str | None, accept: list[str], reject: list[str]) -> bool:
    """Check if a job location passes the user's location filter.

    Evaluation order (important):
    1. Reject patterns are checked FIRST — before the "remote" short-circuit.
       This ensures "Remote India" or "Remote Germany" are caught when those
       countries are in the reject list, rather than slipping through as "remote".
    2. If the location contains a global-remote indicator ("remote", "anywhere",
       etc.) and no reject pattern matched, it is considered eligible.
    3. Accept patterns handle non-remote locations in the user's target area.
    4. Anything else is rejected.
    """
    if not location:
        return True

    loc = location.lower()

    # Reject patterns take priority — checked before the remote short-circuit.
    # This catches country-specific "remote" postings like "Remote India" or
    # "Remote Germany" that are not actually open to US-based candidates.
    for r in reject:
        if r.lower() in loc:
            return False

    # Fully remote (no geographic restriction) is eligible.
    if any(r in loc for r in ("remote", "anywhere", "work from home", "wfh", "distributed")):
        return True

    # Non-remote: accept only if the location matches the user's target area.
    for a in accept:
        if a.lower() in loc:
            return True

    return False


# -- HTML stripper -----------------------------------------------------------

class _HTMLStripper(HTMLParser):
    """Strip HTML tags, keep text content."""

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False
        elif tag in ("p", "div", "li", "tr"):
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"[^\S\n]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def strip_html(html: str) -> str:
    """Convert HTML to plain text."""
    if not html:
        return ""
    stripper = _HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()


# -- Proxy -------------------------------------------------------------------

_opener = None


def setup_proxy(proxy_str: str | None) -> None:
    """Configure a global urllib opener with proxy support."""
    global _opener
    if not proxy_str:
        _opener = urllib.request.build_opener()
        return

    parts = proxy_str.split(":")
    if len(parts) == 4:
        host, port, user, passwd = parts
        proxy_url = f"http://{user}:{passwd}@{host}:{port}"
    elif len(parts) == 2:
        proxy_url = f"http://{parts[0]}:{parts[1]}"
    else:
        log.warning("Proxy format not recognized: %s (expected host:port:user:pass or host:port)", proxy_str)
        _opener = urllib.request.build_opener()
        return

    proxy_handler = urllib.request.ProxyHandler({
        "http": proxy_url,
        "https": proxy_url,
    })
    _opener = urllib.request.build_opener(proxy_handler)
    log.info("Proxy configured: %s:%s", parts[0], parts[1])


def _urlopen(req, timeout=30):
    """Open a URL using the configured opener (with or without proxy)."""
    if _opener:
        return _opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


# -- Workday API -------------------------------------------------------------

def workday_search(employer: dict, search_text: str, limit: int = 20, offset: int = 0) -> dict:
    """Search jobs via Workday CXS API. Returns JSON with total + jobPostings."""
    url = f"{employer['base_url']}/wday/cxs/{employer['tenant']}/{employer['site_id']}/jobs"
    payload = json.dumps({
        "appliedFacets": {},
        "limit": limit,
        "offset": offset,
        "searchText": search_text,
    }).encode()

    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

    with _urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def workday_detail(employer: dict, external_path: str) -> dict:
    """Fetch full job detail via Workday CXS API."""
    url = f"{employer['base_url']}/wday/cxs/{employer['tenant']}/{employer['site_id']}{external_path}"

    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

    with _urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


# -- Date helpers ------------------------------------------------------------

def _parse_workday_date(posted_str: str) -> datetime | None:
    """Parse a Workday postedOn string into a UTC datetime.

    Handles formats observed in the wild:
      - ISO 8601:   "2026-03-22T00:00:00.000Z"  /  "2026-03-22"
      - US date:    "03/22/2026"  (with or without "Posted " prefix)
      - Relative:   "Posted 30+ Days Ago"  ->  treated as 30 days ago
    Returns None if the string cannot be parsed.
    """
    if not posted_str:
        return None

    s = posted_str.strip()

    # Strip common prefix
    s = re.sub(r"^[Pp]osted\s+", "", s).strip()

    # Relative: "30+ Days Ago", "5 Days Ago", "Today", "Just now"
    rel = re.match(r"(\d+)\+?\s+[Dd]ays?\s+[Aa]go", s)
    if rel:
        return datetime.now(timezone.utc) - timedelta(days=int(rel.group(1)))
    if re.search(r"[Tt]oday|[Jj]ust\s+[Nn]ow", s):
        return datetime.now(timezone.utc)
    if re.search(r"[Yy]esterday", s):
        return datetime.now(timezone.utc) - timedelta(days=1)

    # ISO 8601
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    # US date MM/DD/YYYY
    try:
        return datetime.strptime(s, "%m/%d/%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    return None


def _is_too_old(posted_str: str, max_days_old: int) -> bool:
    """Return True if the posting is older than max_days_old days."""
    if max_days_old <= 0:
        return False
    dt = _parse_workday_date(posted_str)
    if dt is None:
        return False  # can't determine age — keep it
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_days_old)
    return dt < cutoff


# -- Search + paginate -------------------------------------------------------

def search_employer(
    employer_key: str,
    employer: dict,
    search_text: str,
    location_filter: bool = True,
    max_results: int = 0,
    accept_locs: list[str] | None = None,
    reject_locs: list[str] | None = None,
    max_days_old: int = 0,
) -> list[dict]:
    """Search a single Workday employer, paginate through all results, and filter jobs.

    Args:
        employer_key:    Unique key for the employer (used in logging).
        employer:        Employer config dict with name, base_url, tenant, site_id.
        search_text:     Job search query string.
        location_filter: If True, filter postings using accept_locs/reject_locs.
        max_results:     Cap on total results returned (0 = no limit).
        accept_locs:     Location substrings that must appear for a job to be kept.
        reject_locs:     Location substrings that cause a job to be rejected.
        max_days_old:    Reject postings older than this many days (0 = no age filter).
                         Uses the Workday postedOn field — see _parse_workday_date().

    Returns:
        List of job dicts with title, url, location, posted_date, and description stub.
    """
    log.info("%s: searching \"%s\"...", employer["name"], search_text)

    all_jobs: list[dict] = []
    offset = 0
    page_size = 20
    max_pages = 25  # Cap at 500 results
    total = None

    while True:
        try:
            data = workday_search(employer, search_text, limit=page_size, offset=offset)
        except Exception as e:
            log.error("%s: API error at offset %d: %s", employer["name"], offset, e)
            break

        if total is None:
            total = data.get("total", 0)
            log.info("%s: %d total results", employer["name"], total)

        postings = data.get("jobPostings", [])
        if not postings:
            break

        for j in postings:
            loc = j.get("locationsText", "")
            # Some Workday postings omit locationsText in search results but encode
            # the location in the URL path, e.g. "/job/United-States-of-America-
            # Washington-District-of-Columbia/Title_REQ123". Fall back to that so
            # the location filter is not silently bypassed for blank-location jobs.
            if not loc:
                ext = j.get("externalPath", "")
                m = re.search(r"/job/([^/]+)/", ext)
                if m:
                    loc = m.group(1).replace("-", " ")
            if location_filter and accept_locs is not None and reject_locs is not None:
                if not _location_ok(loc, accept_locs, reject_locs):
                    continue

            posted_str = j.get("postedOn", "")
            if _is_too_old(posted_str, max_days_old):
                continue

            all_jobs.append({
                "title": j.get("title", ""),
                "location": loc,   # may have been populated from externalPath fallback above
                "posted": posted_str,
                "external_path": j.get("externalPath", ""),
                "employer_key": employer_key,
                "employer_name": employer["name"],
            })

        offset += page_size
        page_num = offset // page_size
        if offset >= total:
            break
        if page_num >= max_pages:
            log.info("%s: capped at %d pages (%d results scanned)", employer["name"], max_pages, offset)
            break
        if max_results and len(all_jobs) >= max_results:
            all_jobs = all_jobs[:max_results]
            break

    log.info("%s: %d jobs found%s", employer["name"], len(all_jobs),
             " (filtered)" if location_filter else "")
    return all_jobs


# -- Fetch details -----------------------------------------------------------

def _fetch_one_detail(employer: dict, job: dict) -> dict:
    """Fetch detail for a single job."""
    try:
        detail = workday_detail(employer, job["external_path"])
        info = detail.get("jobPostingInfo", {})

        raw_desc = info.get("jobDescription", "")
        job["full_description"] = strip_html(raw_desc)
        job["apply_url"] = info.get("externalUrl", "")
        job["job_req_id"] = info.get("jobReqId", "")
        job["time_type"] = info.get("timeType", "")
        job["remote_type"] = info.get("remoteType", "")

    except Exception as e:
        job["full_description"] = ""
        job["apply_url"] = ""
        job["detail_error"] = str(e)

    return job


def fetch_details(employer: dict, jobs: list[dict]) -> list[dict]:
    """Fetch full description + apply URL for each job sequentially."""
    log.info("%s: fetching details for %d jobs...", employer["name"], len(jobs))

    completed = 0
    errors = 0
    t0 = time.time()

    for job in jobs:
        _fetch_one_detail(employer, job)
        completed += 1
        if "detail_error" in job:
            errors += 1

        if completed % 20 == 0 or completed == len(jobs):
            elapsed = time.time() - t0
            rate = completed / elapsed if elapsed > 0 else 0
            log.info("%s: %d/%d (%d errors) [%.1f jobs/sec]",
                     employer["name"], completed, len(jobs), errors, rate)

    elapsed = time.time() - t0
    log.info("%s: done in %.1fs (%.1f jobs/sec)", employer["name"], elapsed, len(jobs) / elapsed if elapsed > 0 else 0)
    return jobs


# -- DB storage --------------------------------------------------------------

def store_results(conn: sqlite3.Connection, jobs: list[dict], employers: dict) -> tuple[int, int]:
    """Store corporate jobs in DB. Returns (new, existing)."""
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    existing = 0

    for job in jobs:
        url = job.get("apply_url", "")
        if not url:
            emp = employers.get(job.get("employer_key", ""), {})
            if emp and job.get("external_path"):
                url = f"{emp['base_url']}/{emp['site_id']}{job['external_path']}"
        if not url:
            continue

        description = job.get("full_description", "")
        short_desc = description[:500] if description else None
        full_description = description if len(description) > 200 else None
        detail_scraped_at = now if full_description else None
        detail_error = job.get("detail_error")

        site = job.get("employer_name", "Corporate")
        strategy = "workday_api"

        # Parse posted date to ISO format for storage
        posted_dt = _parse_workday_date(job.get("posted", ""))
        posted_date = posted_dt.strftime("%Y-%m-%d") if posted_dt else None

        try:
            conn.execute(
                "INSERT INTO jobs (url, title, salary, description, location, site, strategy, "
                "discovered_at, full_description, application_url, detail_scraped_at, detail_error, posted_date) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (url, job.get("title"), None, short_desc, job.get("location"),
                 site, strategy, now, full_description, url, detail_scraped_at, detail_error, posted_date),
            )
            new += 1
        except sqlite3.IntegrityError:
            existing += 1

    conn.commit()
    return new, existing


def _process_one(
    employer_key: str,
    employers: dict,
    search_text: str,
    location_filter: bool,
    accept_locs: list[str],
    reject_locs: list[str],
    max_days_old: int = 0,
) -> dict:
    """Search one employer, fetch details, store results."""
    emp = employers[employer_key]

    try:
        jobs = search_employer(
            employer_key, emp, search_text,
            location_filter=location_filter,
            accept_locs=accept_locs,
            reject_locs=reject_locs,
            max_days_old=max_days_old,
        )
    except Exception as e:
        log.error("%s: ERROR searching '%s': %s", emp["name"], search_text, e)
        return {"employer": emp["name"], "query": search_text,
                "found": 0, "new": 0, "existing": 0, "error": str(e)}

    if not jobs:
        return {"employer": emp["name"], "query": search_text,
                "found": 0, "new": 0, "existing": 0}

    try:
        jobs = fetch_details(emp, jobs)
    except Exception as e:
        log.error("%s: ERROR fetching details for '%s': %s", emp["name"], search_text, e)

    conn = get_connection()
    new, existing = store_results(conn, jobs, employers)
    log.info("%s: %d new, %d already in DB", emp["name"], new, existing)

    return {"employer": emp["name"], "query": search_text,
            "found": len(jobs), "new": new, "existing": existing}


# -- Main orchestrator -------------------------------------------------------

def scrape_employers(
    search_text: str,
    employers: dict,
    employer_keys: list[str] | None = None,
    location_filter: bool = True,
    max_results: int = 0,
    accept_locs: list[str] | None = None,
    reject_locs: list[str] | None = None,
    workers: int = 1,
    max_days_old: int = 0,
) -> dict:
    """Run full scrape: search -> filter -> detail -> store.

    Sequential by default. When workers > 1, processes employers in parallel
    using ThreadPoolExecutor.
    """
    if employer_keys is None:
        employer_keys = list(employers.keys())

    if accept_locs is None:
        accept_locs = []
    if reject_locs is None:
        reject_locs = []

    # Ensure DB schema
    init_db()

    total_new = 0
    total_existing = 0
    total_found = 0
    errors = 0
    t0 = time.time()

    valid_keys = [k for k in employer_keys if k in employers]

    if workers > 1 and len(valid_keys) > 1:
        # Parallel mode
        completed = 0
        with ThreadPoolExecutor(max_workers=min(workers, len(valid_keys))) as pool:
            futures = {
                pool.submit(
                    _process_one, key, employers, search_text,
                    location_filter, accept_locs, reject_locs, max_days_old,
                ): key
                for key in valid_keys
            }
            for future in as_completed(futures):
                result = future.result()
                completed += 1
                total_new += result["new"]
                total_existing += result["existing"]
                total_found += result["found"]
                if "error" in result:
                    errors += 1

                if completed % 10 == 0 or completed == len(valid_keys):
                    elapsed = time.time() - t0
                    log.info("[%s] Progress: %d/%d employers (%d new, %d dupes, %d errors) [%.0fs]",
                             search_text, completed, len(valid_keys), total_new, total_existing, errors, elapsed)
    else:
        # Sequential mode (default)
        completed = 0
        for key in valid_keys:
            result = _process_one(
                key, employers, search_text,
                location_filter, accept_locs, reject_locs, max_days_old,
            )
            completed += 1
            total_new += result["new"]
            total_existing += result["existing"]
            total_found += result["found"]
            if "error" in result:
                errors += 1

            if completed % 10 == 0 or completed == len(valid_keys):
                elapsed = time.time() - t0
                log.info("[%s] Progress: %d/%d employers (%d new, %d dupes, %d errors) [%.0fs]",
                         search_text, completed, len(valid_keys), total_new, total_existing, errors, elapsed)

    elapsed = time.time() - t0
    log.info("[%s] Done: %d found, %d new, %d dupes in %.0fs",
             search_text, total_found, total_new, total_existing, elapsed)

    return {"found": total_found, "new": total_new, "existing": total_existing}


# -- Public entry point ------------------------------------------------------

def run_workday_discovery(employers: dict | None = None, workers: int = 1) -> dict:
    """Main entry point for Workday-based corporate job discovery.

    Loads employer registry from config/employers.yaml (or uses the provided
    dict), then loads search queries from the user's search config to run
    a full crawl across all employers.

    Age filtering is controlled by searches.yaml:
        defaults:
          max_days_old: 30   # skip Workday postings older than 30 days (0 = no limit)

    Args:
        employers: Override the employer registry. If None, loads from YAML.
        workers:   Number of parallel threads for employer scraping. Default 1 (sequential).

    Returns:
        Dict with stats: found, new, existing, queries.
    """
    if employers is None:
        employers = load_employers()

    if not employers:
        log.warning("No employers configured. Create config/employers.yaml.")
        return {"found": 0, "new": 0, "existing": 0, "queries": 0}

    search_cfg = config.load_search_config()
    queries_cfg = search_cfg.get("queries", [])
    accept_locs, reject_locs = _load_location_filter(search_cfg)

    # Default to tier 1-2 queries for workday scraping
    max_tier = search_cfg.get("workday_max_tier", 2)
    queries = [q["query"] for q in queries_cfg if q.get("tier", 99) <= max_tier]

    if not queries:
        # Fallback: use all queries
        queries = [q["query"] for q in queries_cfg]

    if not queries:
        log.warning("No search queries configured in searches.yaml.")
        return {"found": 0, "new": 0, "existing": 0, "queries": 0}

    proxy = search_cfg.get("proxy")
    if proxy:
        setup_proxy(proxy)

    location_filter = search_cfg.get("workday_location_filter", True)
    max_days_old = search_cfg.get("defaults", {}).get("max_days_old", 30)

    log.info("Workday crawl: %d queries x %d employers (workers=%d, max_days_old=%d)",
             len(queries), len(employers), workers, max_days_old)

    grand_new = 0
    grand_existing = 0
    grand_found = 0

    for i, query in enumerate(queries, 1):
        log.info("Query %d/%d: \"%s\"", i, len(queries), query)
        result = scrape_employers(
            search_text=query,
            employers=employers,
            location_filter=location_filter,
            accept_locs=accept_locs,
            reject_locs=reject_locs,
            workers=workers,
            max_days_old=max_days_old,
        )
        grand_new += result["new"]
        grand_existing += result["existing"]
        grand_found += result["found"]

    log.info("Workday crawl done: %d found, %d new, %d existing across %d queries x %d employers",
             grand_found, grand_new, grand_existing, len(queries), len(employers))

    return {
        "found": grand_found,
        "new": grand_new,
        "existing": grand_existing,
        "queries": len(queries),
    }
