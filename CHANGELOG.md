# Changelog

All notable changes to ApplyPilot will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## Tom's Fork — [thomascox/TomsApplyPilot](https://github.com/thomascox/TomsApplyPilot)

Changes made in this personal fork relative to upstream Pickle-Pixel/ApplyPilot.
Upstream changes are pulled in periodically; only fork-specific additions are listed here.

### 2026-04-01

#### Dashboard — reject modal refinements

- **"Closed" reason added**: new reject reason "Posting closed / no longer accepting
  applications" — stored in `reject_reason` but deliberately excluded from
  `REASON_AVOID_MESSAGES` so it never influences scoring feedback
- **"Duplicate" removed from scoring feedback**: duplicate is a logistics reason,
  not a preference signal — `applypilot feedback` no longer suggests avoid entries
  for it
- **"Other" + scoring flag**: when "Other" is selected, an optional "Include in
  scoring feedback review" checkbox appears. If checked, `reject_note` is prefixed
  with `[scoring]`. Running `applypilot feedback` surfaces these flagged notes
  separately for manual review — they are never auto-added (too freeform) but
  listed so you can hand-write `avoid` entries if warranted

### 2026-03-31 (3)

#### Code cleanup — simplify pass

- **Scoring batch efficiency**: `_load_scoring_feedback()` now called once per
  `run_scoring()` batch and passed as a parameter to `score_job()` — previously
  read and parsed `scoring_feedback.yaml` once per job (200 file reads per batch)
- **`_parse_salary()` single call**: computed once per job in `_job_to_dict` and
  unpacked (`sal_lo, sal_hi, _`) — previously called twice per job
- **Reject modal URL storage**: removed `_rejectUrl` module-level JS variable;
  URL is now stored as `modal.dataset.rejectUrl` and read back where needed
- **`REASON_AVOID_MESSAGES`**: moved from inside `feedback()` function body to
  module-level constant in cli.py
- **Reject modal radio loop**: replaced `for` loop with `.map().join()` pattern

### 2026-03-31 (2)

#### Scoring — anti-hallucination rule + feedback loop

**Anti-hallucination prompt instruction:**
- The scoring prompt now explicitly instructs the LLM to only assess skills
  that are present in the resume — never infer or extrapolate
- If a JD's core duties require specific technical skills, certifications, or
  domain expertise not found anywhere in the resume, a 2–3 point penalty is
  applied regardless of title or seniority match
- Prevents the class of error where a model assumes a senior technical manager
  has cloud/DevOps/platform skills just because the role is technical

**`scoring_feedback.yaml` — prompt tuning file:**
- New file at `~/.applypilot/scoring_feedback.yaml` with `avoid`, `prefer`,
  and `calibration_note` sections
- Read on every scoring run and injected into the LLM prompt as directional
  guidance (not hard rules — scores are still relative)
- Edit manually at any time; run `applypilot feedback` to generate suggestions
  from rejection history

**Reject modal — reason capture:**
- Reject button now opens an inline modal instead of a bare `confirm()` dialog
- 9 generic, profile-agnostic reason options: wrong role type, seniority
  mismatch, company type, salary below floor, location, industry, duplicate,
  overqualified, other
- Optional free-text note field for elaboration
- Reason and note persisted to `reject_reason` / `reject_note` DB columns
  (added via forward migration — no manual action needed)

**`applypilot feedback` — new CLI command:**
- Scans rejection history grouped by reason type
- Suggests `avoid` entries for patterns with 3+ rejections
- Shows a preview and asks for confirmation before writing to
  `scoring_feedback.yaml`
- Safe to run repeatedly — merges into existing file, does not overwrite

### 2026-03-31

#### Dashboard — salary color coding, sort fixes, Apply link fix

- **Salary color coding**: salary tag is green when posted max ≥ your salary
  floor (`salary_range_min` in profile), red when below. Gray when present
  but not parseable (e.g. "Depends on Experience"). Handles all observed
  formats: `USD###-USD###/yearly`, `$## - $##` (hourly auto-detected by
  magnitude), `USD##/hourly`, `USD ###,###.## per year`, etc.
- **`prune --below-salary`**: new opt-in flag (not included in `--all`) that
  permanently fails jobs with a posted salary max below your floor. Audit
  mode reports the below-floor count as an additional note.
- **Sort fix**: in score mode, applied jobs now sink to the bottom of their
  score group rather than the whole page — a score-9 applied job stays in
  the 9-section, just after all unapplied 9s. Date/alpha sorts unaffected.
- **Apply → link**: now always shown on every card; falls back to the job
  listing URL when a dedicated `application_url` isn't available (most cards
  only get that after enrichment runs for certain sites).

### 2026-03-30

#### Dashboard — CRM tracking features

The dashboard now doubles as a lightweight job search CRM, designed to be useful
after you've applied — not just before.

- **Mark as Applied** button (green ✔) — records a manual application (sets
  `applied_at`, `apply_status='applied'`). Job moves to the Applied stage and
  remains visible under the Applied filter
- **Interview stage tracker** — Phone Screen → Technical → Onsite → Offer → Closed
  pills on every Applied-stage card; click to set/toggle; persists to database
- **Follow-up date** — date picker per card with a relative label (green = future,
  amber = today, red = overdue)
- **Notes** — free-text field per card, debounced auto-save (saves 800ms after
  you stop typing)
- **Recruiter/Contact** — single-line field for recruiter or hiring manager name
- **Applied date tag** — shows "Applied Xd ago" on Applied-stage cards
- **In Interview** stat tile — Applied jobs with an active (non-closed) stage
- **Follow-up Due** stat tile — jobs with a follow-up date ≤ today; turns amber
  when non-zero; clicking filters to those jobs
- Search now includes notes and contact fields in the keyword haystack
- Four new database columns added via forward migration (no manual action needed):
  `notes`, `interview_stage`, `follow_up_due`, `recruiter_contact`

#### Dashboard — NoneType crash fixes

- Fixed `'NoneType' object is not subscriptable` crash when job title or site is
  NULL in the database — affected scoring, tailoring, and cover letter generation
- Fixed same crash pattern in LLM response parsing when Gemini or OpenAI-compatible
  APIs return null content (e.g. safety filter responses)

### 2026-03-26

#### Profile: switched from JSON to YAML with inline comments

`~/.applypilot/profile.json` → `~/.applypilot/profile.yaml`

JSON has no comment syntax, making the profile file hard to hand-edit without
separate documentation. The profile is now written as YAML so every section has
`#` comments explaining what each field does and how it affects scoring and tailoring.

- Each section has a header comment explaining its purpose
- Fields with non-obvious values have inline hints (e.g. `work_permit_type`, `education_level`, `earliest_start_date`)
- The `career_focus` block has detailed comments explaining primary vs secondary skills and the scoring deduction
- If `career_focus` is absent, a commented-out example block is written so users know exactly how to add it by hand
- **Backward compatible**: existing `profile.json` files load transparently as a fallback — no manual migration needed
- Running `applypilot init` regenerates the file in the new YAML format

#### Scoring: universal career-transition deduction (replaces hard cap)

Previously, roles outside the candidate's current career focus were hard-capped
at a fixed score, which lost relative signal and was too blunt.

New behaviour:
- The LLM scores the role on skill match first (1–10), as normal
- If the job's **core duties** centre on the candidate's **secondary (historical) skills**,
  the LLM subtracts 2–3 points from the initial score to preserve relative ranking
- A role that would score 9–10 lands around 6–8; a 7–8 lands around 4–6
- **No penalty** if secondary skills appear only as a bonus/preferred qualification
- Location ineligibility remains a separate hard cap (score 1–2), unchanged
- Job description truncation increased 6,000 → 12,000 characters so location/office
  requirements buried deep in long postings reach the LLM

### 2026-03-25

#### Dashboard — favorites and live refresh

- Added a star button per job card (hollow gray → filled yellow when clicked)
- Favorites persist to the database and survive page refresh and server restart
- Favorites-only filter toggle on the dashboard
- Favorites always sort to the top across all sort modes (score, date, etc.)
- Fixed: page refresh now re-reads the database on every GET request — favorites
  and rejects no longer disappear after refresh without restarting the server

#### Scoring — career focus profile support

- Added `career_focus` block to profile (primary skills, secondary skills, target roles,
  career note) which feeds into the LLM scoring prompt for skill-recency weighting
- `--stale` flag description corrected to "Permanently fail jobs older than 30 days"

#### Location filtering — Workday blank location bug fix

- Fixed a bug where jobs with a blank `locationsText` API field bypassed the location
  filter entirely and were treated as eligible
- Fallback: when `locationsText` is empty, location is now extracted from the job URL
  path slug (e.g. `/job/washington-dc-hybrid/` → `washington dc hybrid`)

#### Wizard (`applypilot init`) — multiple cities and remote toggle

- `init` now supports adding multiple local cities (loop until blank entry)
- Remote job search can be toggled on/off independently of local city searches
- Generated `searches.yaml` includes `location_accept` and `location_reject_non_remote`
  with 13 sensible default reject patterns

---

## Upstream Changelog

## [0.2.0] - 2026-02-17

### Added
- **Parallel workers for discovery/enrichment** - `applypilot run --workers N` enables
  ThreadPoolExecutor-based parallelism for Workday scraping, smart extract, and detail
  enrichment. Default is sequential (1); power users can scale up.
- **Apply utility modes** - `--gen` (generate prompt for manual debugging), `--mark-applied`,
  `--mark-failed`, `--reset-failed` flags on `applypilot apply`
- **Dry-run mode** - `applypilot apply --dry-run` fills forms without clicking Submit
- **5 new tracking columns** - `agent_id`, `last_attempted_at`, `apply_duration_ms`,
  `apply_task_id`, `verification_confidence` for better apply-stage observability
- **Manual ATS detection** - `manual_ats` list in `config/sites.yaml` skips sites with
  unsolvable CAPTCHAs (e.g. TCS iBegin)
- **Qwen3 `/no_think` optimization** - automatically saves tokens when using Qwen models
- **`config.DEFAULTS`** - centralized dict for magic numbers (`min_score`, `max_apply_attempts`,
  `poll_interval`, `apply_timeout`, `viewport`)

### Fixed
- **Config YAML not found after install** - moved `config/` into the package at
  `src/applypilot/config/` so YAML files (employers, sites, searches) ship with `pip install`
- **Search config format mismatch** - wizard wrote `searches:` key but discovery code
  expected `queries:` with tier support. Aligned wizard output and example config
- **JobSpy install isolation** - removed python-jobspy from package dependencies due to
  broken numpy==1.26.3 exact pin in jobspy metadata. Installed separately with `--no-deps`
- **Scoring batch limit** - default limit of 50 silently left jobs unscored across runs.
  Changed to no limit (scores all pending jobs in one pass)
- **Missing logging output** - added `logging.basicConfig(INFO)` so per-job progress for
  scoring, tailoring, and cover letters is visible during pipeline runs

### Changed
- **Blocked sites externalized** - moved from hardcoded sets in launcher.py to
  `config/sites.yaml` under `blocked:` key
- **Site base URLs externalized** - moved from hardcoded dict in detail.py to
  `config/sites.yaml` under `base_urls:` key
- **SSO domains externalized** - moved from hardcoded list in prompt.py to
  `config/sites.yaml` under `blocked_sso:` key
- **Prompt improvements** - screening context uses `target_role` from profile,
  salary section includes `currency_conversion_note` and dynamic hourly rate examples
- **`acquire_job()` fixed** - writes `agent_id` and `last_attempted_at` to proper columns
  instead of misusing `apply_error`
- **`profile.example.json`** - added `currency_conversion_note` and `target_role` fields

## [0.1.0] - 2026-02-17

### Added
- 6-stage pipeline: discover, enrich, score, tailor, cover letter, apply
- Multi-source job discovery: Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs
- Workday employer portal support (46 preconfigured employers)
- Direct career site scraping (28 preconfigured sites)
- 3-tier job description extraction cascade (JSON-LD, CSS selectors, AI fallback)
- AI-powered job scoring (1-10 fit scale with rationale)
- Resume tailoring with factual preservation (no fabrication)
- Cover letter generation per job
- Autonomous browser-based application submission via Playwright
- Interactive setup wizard (`applypilot init`)
- Cross-platform Chrome/Chromium detection (Windows, macOS, Linux)
- Multi-provider LLM support (Gemini, OpenAI, local models via OpenAI-compatible endpoints)
- Pipeline stats and HTML results dashboard
- YAML-based configuration for employers, career sites, and search queries
- Job deduplication across sources
- Configurable score threshold filtering
- Safety limits for maximum applications per run
- Detailed application results logging
