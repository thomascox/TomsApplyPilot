<!-- logo here -->

> **⚠️ ApplyPilot** is the original open-source project, created by [Pickle-Pixel](https://github.com/Pickle-Pixel) and first published on GitHub on **February 17, 2026**. We are **not affiliated** with applypilot.app, useapplypilot.com, or any other product using the "ApplyPilot" name. These sites are **not associated with this project** and may misrepresent what they offer. If you're looking for the autonomous, open-source job application agent — you're in the right place.

> **🍴 This is Tom's personal fork** ([thomascox/TomsApplyPilot](https://github.com/thomascox/TomsApplyPilot)) of the upstream Pickle-Pixel/ApplyPilot project. It includes personal customizations and improvements. See [CHANGELOG.md](CHANGELOG.md) for what's different in this fork.

# ApplyPilot

**Applied to 1,000 jobs in 2 days. Fully autonomous. Open source.**

[![PyPI version](https://img.shields.io/pypi/v/applypilot?color=blue)](https://pypi.org/project/applypilot/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-green.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/Pickle-Pixel/ApplyPilot?style=social)](https://github.com/Pickle-Pixel/ApplyPilot)
[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/S6S01UL5IO)




https://github.com/user-attachments/assets/7ee3417f-43d4-4245-9952-35df1e77f2df


---

## What It Does

ApplyPilot is a 6-stage autonomous job application pipeline. It discovers jobs across 5+ boards, scores them against your resume with AI, tailors your resume per job, writes cover letters, and **submits applications for you**. It navigates forms, uploads documents, answers screening questions, all hands-free.

```bash
pip install applypilot
pip install --no-deps python-jobspy && pip install pydantic tls-client requests markdownify regex
applypilot init          # one-time setup: resume, profile, preferences, API keys
applypilot doctor        # verify your setup — shows what's installed and what's missing
applypilot run           # discover > enrich > score > tailor > cover letters
applypilot run -w 4      # same but parallel (4 threads for discovery/enrichment)
applypilot apply         # autonomous browser-driven submission
applypilot apply -w 3    # parallel apply (3 Chrome instances)
applypilot apply --dry-run  # fill forms without submitting
```

> **Why two install commands?** `python-jobspy` pins an exact numpy version in its metadata that conflicts with pip's resolver, but works fine at runtime with any modern numpy. The `--no-deps` flag bypasses the resolver; the second command installs jobspy's actual runtime dependencies.

---

## Two Paths

### Full Pipeline (recommended)
**Requires:** Python 3.11+, Node.js (for npx), Gemini API key (free), Claude Code CLI, Chrome

Runs all 6 stages — job discovery through autonomous submission.

### Discovery + Tailoring Only
**Requires:** Python 3.11+, Gemini API key (free)

Runs stages 1–5: discovers jobs, scores them, tailors your resume, generates cover letters. You apply manually with the AI-prepared materials.

---

## The Pipeline

| Stage | What Happens |
|-------|-------------|
| **1. Discover** | Scrapes 5 job boards (Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs) + 48 Workday employer portals + 30 direct career sites |
| **2. Enrich** | Fetches full job descriptions via JSON-LD, CSS selectors, or AI-powered extraction |
| **3. Score** | AI rates every job 1–10 based on your resume and profile. Location-ineligible jobs are capped at 2. Only high-fit jobs proceed |
| **4. Tailor** | AI rewrites your resume per job: reorganizes, emphasizes relevant experience, adds keywords. Never fabricates |
| **5. Cover Letter** | AI generates a targeted cover letter per job |
| **6. Auto-Apply** | Claude Code navigates forms, uploads documents, answers screening questions, and submits |

Each stage is independent — run them all or pick what you need.

---

## ApplyPilot vs The Alternatives

| Feature | ApplyPilot | AIHawk | Manual |
|---------|-----------|--------|--------|
| Job discovery | 5 boards + Workday + direct sites | LinkedIn only | One board at a time |
| AI scoring | 1–10 fit score per job | Basic filtering | Your gut feeling |
| Resume tailoring | Per-job AI rewrite | Template-based | Hours per application |
| Auto-apply | Full form navigation + submission | LinkedIn Easy Apply only | Click, type, repeat |
| Supported sites | Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs, 48 Workday portals, 30 direct sites | LinkedIn | Whatever you open |
| License | AGPL-3.0 | MIT | N/A |

---

## Requirements

| Component | Required For | Details |
|-----------|-------------|---------|
| Python 3.11+ | Everything | Core runtime |
| Node.js 18+ | Auto-apply | Needed for `npx` to run Playwright MCP server |
| Gemini API key | Scoring, tailoring, cover letters | Free tier (15 RPM / 1M tokens/day) is sufficient |
| Chrome/Chromium | Auto-apply | Auto-detected on most systems |
| Claude Code CLI | Auto-apply | Install from [claude.ai/code](https://claude.ai/code) |

**Gemini API key is free.** Get one at [aistudio.google.com](https://aistudio.google.com). OpenAI and local models (Ollama/llama.cpp) are also supported.

### Optional

| Component | What It Does |
|-----------|-------------|
| CapSolver API key | Solves CAPTCHAs during auto-apply (hCaptcha, reCAPTCHA, Turnstile, FunCaptcha). Without it, CAPTCHA-blocked applications fail gracefully |

---

## Configuration

All files live in `~/.applypilot/` and are generated by `applypilot init`. You can edit them directly at any time.

---

### `profile.yaml`

Your personal data file. Powers scoring, tailoring, form auto-fill, and EEO responses. Fully commented — open it and every field has an inline explanation.

Sections:

| Section | What It's For |
|---------|--------------|
| `personal` | Contact info, URLs, job-site password for auto-apply login |
| `work_authorization` | Sponsorship, permit type |
| `compensation` | Salary expectation and range (used for salary questions during auto-apply) |
| `experience` | Years of experience, education level, current and target title |
| `skills_boundary` | The full list of skills you claim. The AI uses this as a boundary — it will only use skills listed here when tailoring your resume |
| `resume_facts` | Hard truths the AI must never change: company names, project names, school name, real metrics. Preserved exactly during every tailoring pass |
| `career_focus` | *(Optional)* If your career has shifted direction, define your current primary skills and historical secondary skills. The scorer uses this to avoid sending you roles that would require returning to work you've moved on from (see below) |
| `eeo_voluntary` | Self-identification for EEO forms. Defaults to "Decline to self-identify" |
| `availability` | Earliest start date, used for availability questions |

#### Career Focus Scoring

If your career has transitioned (e.g. from individual contributor engineering to management, or from one domain to another), add a `career_focus` block to `profile.yaml`:

```yaml
career_focus:
  target_roles:
    - Engineering Manager
    - Director of Product
  primary_skills:          # What you do day-to-day right now
    - team leadership
    - roadmap planning
  secondary_skills:        # Real background but no longer your daily focus
    - Python
    - hands-on coding
  career_note: "Moved from IC engineering to leadership around 2020"
```

**How it affects scoring:**
- Roles matching your `primary_skills` or `target_roles` score normally
- If a job's **core duties** are primarily about your `secondary_skills`, the AI subtracts 2–3 points from its initial score (a 9 becomes ~6–7; a 7 becomes ~4–6)
- No penalty if secondary skills appear only as bonus/preferred qualifications
- Location ineligibility is a separate hard cap (score 1–2) and is not affected

---

### `searches.yaml`

Controls where and what to search. Example:

```yaml
defaults:
  distance: 25
  hours_old: 72
  results_per_site: 50

# Substrings that make a non-remote location eligible (e.g. your city)
location_accept:
  - Orlando
  - Florida

# Substrings that always reject a location, even if it says "Remote"
# (catches things like "Remote - India" or "EMEA only")
location_reject_non_remote:
  - india
  - canada
  - emea
  - apac

locations:
  - location: "United States"
    remote: true              # US-wide remote search
  - location: "Orlando, FL"
    remote: false             # Local hybrid/onsite roles

queries:
  - query: "Senior Technical Project Manager"
    tier: 1
  - query: "Program Manager"
    tier: 2
```

**Location filtering:** Jobs are filtered at scrape time. `location_reject_non_remote` runs first (even on "remote" postings) to catch geo-restricted remote roles. `location_accept` then allows specific non-remote locations through for hybrid/onsite. Anything that doesn't match either list is filtered out before enrichment.

---

### `.env`

API keys and model configuration:

```
GEMINI_API_KEY=your-key-here
LLM_MODEL=gemini-2.0-flash        # or gpt-4o-mini, or a local model name
CAPSOLVER_API_KEY=optional
```

---

### Package configs (shipped with ApplyPilot)

- `config/employers.yaml` — Workday employer registry (48 preconfigured portals)
- `config/sites.yaml` — Direct career sites, blocked sites, manual ATS domains, base URLs
- `config/searches.example.yaml` — Example search configuration

---

## How Stages Work

### Discover
Queries Indeed, LinkedIn, Glassdoor, ZipRecruiter, and Google Jobs via JobSpy. Scrapes 48 Workday employer portals (configurable). Hits 30 direct career sites with custom extractors. Deduplicates by URL. Location filtering runs here — jobs in ineligible locations never enter the database.

### Enrich
Visits each job URL and extracts the full description. 3-tier cascade: JSON-LD structured data → CSS selector patterns → AI-powered extraction for unknown layouts.

### Score
AI scores every job 1–10 against your resume and profile.

- **9–10** Strong match — proceed immediately
- **7–8** Good match — proceed
- **5–6** Moderate — skipped by default (below threshold)
- **1–4** Poor fit or ineligible — skipped
- **1–2** Location ineligible (overseas, hybrid-only) — hard cap

If your profile includes a `career_focus` block, the scorer applies a 2–3 point deduction for roles whose core duties centre on your historical secondary skills (see [Career Focus Scoring](#career-focus-scoring) above).

To re-score jobs after changing your profile or switching models:

```bash
applypilot run score --rescore                       # re-score; protect jobs already >= 7
applypilot run score --rescore --no-rescore-protect  # force re-score everything
```

### Tailor
Generates a custom resume per job above your score threshold: reorders experience, emphasizes relevant skills, incorporates keywords. `resume_facts` (companies, projects, school, metrics) are preserved exactly — the AI reorganizes but never fabricates.

### Cover Letter
Writes a targeted cover letter per job, referencing the specific company, role, and how your experience maps to their requirements.

### PDF
Converts tailored resumes and cover letters from `.txt` to formatted `.pdf` for upload.

### Auto-Apply
Claude Code launches Chrome, navigates to each application URL, detects the form type, fills personal info and work history, uploads the tailored resume and cover letter, answers screening questions with AI, and submits. The Playwright MCP server is configured automatically per worker — no manual MCP setup needed.

---

## Dashboard

```bash
applypilot dashboard
```

Opens an interactive HTML dashboard in your browser showing all discovered jobs, their scores, status, and tailored resume links. The server stays live until you press Ctrl+C.

**Features:**
- Sort by score, date, or status
- Filter by stage (scored, tailored, applied, etc.)
- **Favorites** — click the star on any job card to mark it as a favorite. Favorites sort to the top in all views and are processed first by the tailor, cover, and apply stages. Persists across restarts.
- **Reject** — remove a job from your pipeline in one click (marks it permanently failed). No restart required.
- **Mark as Applied** — green button to record a manual application. Job moves to the Applied stage and stays visible via the Applied filter.
- **CRM fields** — per-card notes, recruiter/contact, interview stage (Phone Screen → Technical → Onsite → Offer → Closed), and follow-up date. All auto-save and persist to the database.
- **Applied date** — shows "Applied Xd ago" on Applied-stage cards.
- **In Interview** stat tile — count of applied jobs with an active interview stage.
- **Follow-up Due** stat tile — count of jobs with a follow-up date of today or earlier. Turns amber when non-zero; click to filter.

---

## CLI Reference

### `applypilot run`

```bash
applypilot run                                  # Full pipeline (all stages)
applypilot run discover enrich                  # Discovery only
applypilot run score tailor cover               # Scoring and tailoring only
applypilot run score --rescore                  # Re-score already-scored jobs (protects score >= 7)
applypilot run score --rescore --no-rescore-protect  # Force re-score everything
applypilot run score --rescore --min-score 8    # Protect only jobs already scoring >= 8
applypilot run --workers 4                      # Parallel discovery/enrichment (4 threads)
applypilot run --stream                         # Run stages concurrently as work flows
applypilot run --min-score 8                    # Raise the score threshold for this run
applypilot run --limit 20                       # Process at most 20 jobs per stage
applypilot run --dry-run                        # Preview which stages would run
applypilot run --validation lenient             # Skip banned-word checks (good for local LLMs)
applypilot run --validation strict              # Treat any banned word as a hard error
```

### `applypilot apply`

```bash
applypilot apply                                # Apply to 1 job
applypilot apply --limit 5                      # Apply to up to 5 jobs
applypilot apply --workers 3                    # 3 parallel Chrome instances
applypilot apply --continuous                   # Run forever, poll every 60s for new jobs
applypilot apply --poll-interval 30             # Poll every 30s in continuous mode
applypilot apply --dry-run                      # Fill forms but don't click Submit
applypilot apply --headless                     # No visible browser window
applypilot apply --url URL                      # Apply to one specific job
applypilot apply --model sonnet                 # Use a more capable Claude model (default: haiku)
applypilot apply --max-job-age 14               # Skip postings older than 14 days
applypilot apply --max-job-age 0                # Disable the age check entirely
applypilot apply --min-score 8                  # Only apply to jobs scoring >= 8

# Utility modes (no Chrome needed)
applypilot apply --mark-applied URL             # Record a manually submitted application
applypilot apply --mark-failed URL              # Permanently skip a job
applypilot apply --mark-failed URL --fail-reason requires_relocation
applypilot apply --reset-failed                 # Re-queue all failed jobs for retry
applypilot apply --gen --url URL                # Dump the agent prompt for debugging
```

### `applypilot prune`

Database cleanup. Run with no flags for a diagnostic report (nothing is changed).

```bash
applypilot prune                                # Show all issue counts — no changes
applypilot prune --stale                        # Permanently fail jobs older than 30 days (pre-tailoring)
applypilot prune --stale --max-days 14          # Use a tighter age cutoff
applypilot prune --location-ineligible          # Permanently fail location-ineligible jobs
applypilot prune --invalid-url                  # Permanently fail jobs with bad/missing URLs
applypilot prune --no-description               # Delete jobs that were never enriched
applypilot prune --all                          # Run all four cleanup operations above

# Reset operations
applypilot prune --reset-failed                 # Re-queue permanently failed apply jobs
applypilot prune --reset-stuck                  # Unstick jobs stuck in_progress after a crash
applypilot prune --reset-scores                 # Wipe all scores — re-score from scratch next run

# Nuclear option
applypilot prune --nuke --yes                   # Delete EVERY job and start fresh
```

> **Stale vs delete:** `--stale` marks jobs as permanently failed (records are kept for history). `--no-description` actually deletes records. `--reset-failed` can recover stale jobs if needed.

### Other commands

```bash
applypilot init                                 # First-time setup wizard
applypilot doctor                               # Verify setup, diagnose missing requirements
applypilot status                               # Pipeline statistics
applypilot dashboard                            # Open the interactive results dashboard
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and PR guidelines.

---

## License

ApplyPilot is licensed under the [GNU Affero General Public License v3.0](LICENSE).

You are free to use, modify, and distribute this software. If you deploy a modified version as a service, you must release your source code under the same license.
