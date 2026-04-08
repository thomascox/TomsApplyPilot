"""Job fit scoring: LLM-powered evaluation of candidate-job match quality.

Scores jobs on a 1-10 scale by comparing the user's resume against each
job description. All personal data is loaded at runtime from the user's
profile and resume file.

Location eligibility is evaluated as part of scoring: overseas or hybrid
roles that require in-person attendance where the candidate is not located
are capped at a score of 2, preventing them from flowing through the
tailoring, cover letter, and apply stages.

Career focus (optional): if the user's profile includes a career_focus block,
the scorer applies a recency-aware penalty for roles whose primary duties are
centred on skills the candidate has explicitly marked as historical/background.
This is entirely profile-driven — no domain assumptions are hardcoded here.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone

from applypilot.config import RESUME_PATH, SCORING_FEEDBACK_PATH, load_profile
from applypilot.database import get_connection, get_jobs_by_stage
from applypilot.llm import get_client

log = logging.getLogger(__name__)


# ── Scoring Prompt ────────────────────────────────────────────────────────

def _build_career_focus_block(career_focus: dict) -> str:
    """Build the career focus section of the scoring prompt from the profile's career_focus block.

    This section is injected only when the user has declared a career focus in their profile.
    It instructs the LLM to apply a recency-aware penalty when the JD's primary duties are
    centred on skills the candidate has marked as historical/background.

    The logic is entirely universal — no domain-specific assumptions (e.g. "management" or
    "coding") are hardcoded. The penalty fires based on whatever the user declared as
    primary vs secondary in their own profile.

    Args:
        career_focus: The career_focus dict from profile.json with keys:
                      target_roles, primary_skills, secondary_skills, career_note.

    Returns:
        A formatted prompt section string, or empty string if no useful data present.
    """
    primary_list = career_focus.get("primary_skills") or []
    secondary_list = career_focus.get("secondary_skills") or []

    # Nothing to inject if neither list is populated
    if not primary_list and not secondary_list:
        return ""

    primary = ", ".join(primary_list) if primary_list else "N/A"
    secondary = ", ".join(secondary_list) if secondary_list else "N/A"
    target_roles = ", ".join(career_focus.get("target_roles") or []) or "N/A"
    note = (career_focus.get("career_note") or "").strip()
    note_line = f"\nCareer context: {note}" if note else ""

    return (
        f"\nCANDIDATE CAREER FOCUS:{note_line}\n"
        f"Target roles: {target_roles}\n"
        f"Primary skills (currently active, used day-to-day): {primary}\n"
        f"Secondary skills (real but historical — no longer day-to-day): {secondary}\n"
        "\nCAREER TRAJECTORY SCORING RULES:\n"
        "- Score the role on skill match first, as you normally would (1-10).\n"
        "- Primary skills reflect the candidate's CURRENT day-to-day work. Weight these heavily.\n"
        "- Secondary skills are real but HISTORICAL — the candidate has moved on from them.\n"
        "  They may add modest value as background context but should not drive the score up.\n"
        "- PENALTY: If the JD's core duties are primarily centred on the candidate's secondary\n"
        "  skills (i.e. the role would require the candidate to return to work they have moved\n"
        "  away from as their main focus), subtract 2-3 points from the skill-match score.\n"
        "  A role that would otherwise score 9-10 should land around 6-8 after the penalty.\n"
        "  A role that would score 7-8 should land around 4-6. Preserve the relative signal.\n"
        "- Do NOT penalize if secondary skills appear only as a bonus/preferred qualification\n"
        "  or as background context — that is normal for the candidate's target roles.\n"
        "- If the JD aligns with the candidate's target roles or primary skills, score normally\n"
        "  with no penalty.\n"
    )


def _load_scoring_feedback() -> str:
    """Load scoring feedback from scoring_feedback.yaml and format it as a prompt block.

    Returns:
        A formatted prompt section string with avoid/prefer/calibration_note sections,
        or empty string if the file does not exist, is empty, or is malformed.
    """
    import yaml

    if not SCORING_FEEDBACK_PATH.exists():
        return ""

    try:
        data = yaml.safe_load(SCORING_FEEDBACK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ""

    if not data:
        return ""

    avoid = data.get("avoid") or []
    prefer = data.get("prefer") or []
    note = (data.get("calibration_note") or "").strip()

    # Nothing useful to inject if all sections are empty
    if not avoid and not prefer and not note:
        return ""

    lines = [
        "\nSCORING FEEDBACK (from your rejection history — treat as strong scoring guidance):",
    ]
    if avoid:
        lines.append("Avoid scoring highly:")
        for item in avoid:
            lines.append(f"- {item}")
    if prefer:
        lines.append("Prefer scoring highly:")
        for item in prefer:
            lines.append(f"- {item}")
    if note:
        lines.append(f"Note: {note}")

    return "\n".join(lines) + "\n"


def _build_score_prompt(
    candidate_location: str,
    career_focus: dict | None = None,
    feedback_block: str = "",
) -> str:
    """Build the scoring prompt including location eligibility and optional career focus context.

    Args:
        candidate_location: Candidate's city/state/country string for eligibility checks.
        career_focus:        Optional career_focus block from profile.json. When present,
                             a recency-aware penalty section is injected that instructs the
                             LLM to down-score roles whose primary duties are centred on
                             skills the candidate has marked as historical/background.
        feedback_block:      Optional scoring feedback block from _load_scoring_feedback().
                             Injected after career focus block and before scoring criteria.
    """
    career_block = _build_career_focus_block(career_focus) if career_focus else ""

    return f"""You are a job fit evaluator. Given a candidate's resume and a job description, score how well the candidate fits the role AND whether the location is eligible.

CANDIDATE LOCATION: {candidate_location}

LOCATION ELIGIBILITY RULES:
A job is LOCATION ELIGIBLE if ANY of the following are true:
- The role is fully remote (title or description uses "remote", "work from home", "distributed", "fully remote", "anywhere")
- The job is in the same city or state/province as the candidate
- The posting explicitly states that candidates in the candidate's location may apply remotely

A job is NOT ELIGIBLE if:
- It requires regular in-office or hybrid attendance in a city/country where the candidate does not live
- The posting is in a different country from the candidate with no primary remote option
- "Work from anywhere X weeks/year" is the ONLY flexibility mentioned -- this means the job is still onsite/hybrid, NOT a remote role
- The job is hybrid or onsite in an overseas city (Europe, Asia, etc.) even if there is some travel flexibility
{career_block}{feedback_block}
SCORING CRITERIA (apply only after determining eligibility and career focus):
- If NOT ELIGIBLE: score MUST be 1 or 2, regardless of skill match
- If ELIGIBLE, score on skill/experience fit:
  - 9-10: Perfect match. Candidate has direct experience in nearly all required skills and qualifications.
  - 7-8: Strong match. Candidate has most required skills, minor gaps easily bridged.
  - 5-6: Moderate match. Candidate has some relevant skills but missing key requirements.
  - 3-4: Weak match. Significant skill gaps, would need substantial ramp-up.
  - 1-2: Poor match. Completely different field or experience level.

IMPORTANT FACTORS (for eligible roles):
- Weight technical skills heavily (programming languages, frameworks, tools)
- Consider transferable experience (automation, scripting, API work)
- Factor in the candidate's project experience
- Be realistic about experience level vs. job requirements (years of experience, seniority)

ANTI-HALLUCINATION RULE:
- Only assess the candidate against skills and experience EXPLICITLY stated in their resume.
- Do NOT infer, assume, or extrapolate skills that are not mentioned (e.g. do not assume a senior manager knows a specific cloud platform just because the role is technical).
- If the JD's core duties require specific technical skills, certifications, platforms, or domain expertise that are NOT present anywhere in the resume, treat this as a meaningful gap and apply a 2-3 point penalty to the score.
- A strong title match or seniority match does NOT compensate for absent core technical requirements.

RESPOND IN EXACTLY THIS FORMAT (no other text):
SCORE: [1-10]
LOCATION: [ELIGIBLE or NOT_ELIGIBLE]
KEYWORDS: [comma-separated ATS keywords from the job description that match or could match the candidate]
REASONING: [2-3 sentences explaining the score and location decision if NOT_ELIGIBLE]"""


def _parse_score_response(response: str) -> dict:
    """Parse the LLM's score response into structured data.

    Args:
        response: Raw LLM response text.

    Returns:
        {"score": int, "keywords": str, "reasoning": str, "location_eligible": bool}
    """
    score = 0
    keywords = ""
    reasoning = response
    location_eligible = True  # default: assume eligible if not stated

    for line in response.split("\n"):
        line = line.strip()
        if line.startswith("SCORE:"):
            try:
                score = int(re.search(r"\d+", line).group())
                score = max(1, min(10, score))
            except (AttributeError, ValueError):
                score = 0
        elif line.startswith("LOCATION:"):
            val = line.replace("LOCATION:", "").strip().upper()
            location_eligible = val != "NOT_ELIGIBLE"
        elif line.startswith("KEYWORDS:"):
            keywords = line.replace("KEYWORDS:", "").strip()
        elif line.startswith("REASONING:"):
            reasoning = line.replace("REASONING:", "").strip()

    return {
        "score": score,
        "keywords": keywords,
        "reasoning": reasoning,
        "location_eligible": location_eligible,
    }


def score_job(resume_text: str, job: dict, profile: dict, feedback_block: str = "") -> dict:
    """Score a single job against the resume.

    Args:
        resume_text:    The candidate's full resume text.
        job:            Job dict with keys: title, site, location, full_description.
        profile:        User profile dict from load_profile().
        feedback_block: Pre-loaded scoring feedback block (from _load_scoring_feedback()).
                        Pass once from run_scoring() to avoid re-reading the file per job.

    Returns:
        {"score": int, "keywords": str, "reasoning": str, "location_eligible": bool}
    """
    personal = profile.get("personal", {})
    candidate_city    = personal.get("city", "")
    candidate_state   = personal.get("province_state", "")
    candidate_country = personal.get("country", "United States")
    candidate_location = ", ".join(p for p in [candidate_city, candidate_state, candidate_country] if p)

    # career_focus is optional — if absent the scoring prompt omits the recency section
    career_focus: dict | None = profile.get("career_focus") or None

    job_text = (
        f"TITLE: {job['title']}\n"
        f"COMPANY: {job['site']}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:12000]}"
    )

    messages = [
        {"role": "system", "content": _build_score_prompt(candidate_location, career_focus, feedback_block)},
        {"role": "user", "content": f"RESUME:\n{resume_text}\n\n---\n\nJOB POSTING:\n{job_text}"},
    ]

    try:
        client = get_client()
        response, _ = client.chat(messages, max_tokens=512, temperature=0.2)
        result = _parse_score_response(response)

        # Programmatic safety cap: if the LLM declared NOT_ELIGIBLE but scored
        # too high (LLM miscalibration), force the score down.
        if not result["location_eligible"] and result["score"] > 2:
            log.info(
                "Score capped 9->2 for '%s': location not eligible (candidate: %s)",
                job.get("title", "?"), candidate_location,
            )
            result["score"] = 2
            result["reasoning"] += (
                f" (Score capped to 2: role is not location-eligible for candidate in {candidate_location}.)"
            )

        return {**result, "error": False}

    except Exception as e:
        log.error("LLM error scoring job '%s': %s", job.get("title", "?"), e)
        return {
            "score": 0,
            "keywords": "",
            "reasoning": f"LLM error: {e}",
            "location_eligible": True,
            "error": True,
        }


def run_scoring(
    limit: int = 0,
    rescore: bool = False,
    rescore_protect: bool = True,
    min_score: int = 7,
) -> dict:
    """Score unscored jobs that have full descriptions.

    Args:
        limit:           Maximum number of jobs to score in this run (0 = no limit).
        rescore:         If True, re-score already-scored jobs in addition to new ones.
        rescore_protect: If True (default), jobs whose current score is >= min_score
                         are skipped during a rescore. This prevents model variance
                         from silently downgrading jobs that are already pipeline-eligible.
                         Pass False (--no-rescore-protect) to force-rescore everything.
        min_score:       The eligibility threshold. Scores at or above this value are
                         considered "protected" when rescore_protect is True.

    Returns:
        {"scored": int, "skipped_protected": int, "errors": int,
         "elapsed": float, "distribution": list}
    """
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    profile = load_profile()
    conn = get_connection()

    if rescore:
        if rescore_protect:
            # Skip jobs already at or above the eligibility threshold — they are
            # pipeline-eligible and protecting them avoids model-variance regressions.
            # Also skip permanently failed jobs (apply_attempts=99): these have been
            # explicitly removed from the pipeline (expired, ineligible location, etc.)
            # and rescoring them wastes LLM calls without any useful outcome.
            query = (
                "SELECT * FROM jobs WHERE full_description IS NOT NULL "
                "AND (fit_score IS NULL OR fit_score < ?) "
                "AND (apply_attempts IS NULL OR apply_attempts < 99)"
            )
            params: list = [min_score]
        else:
            # Even with --no-rescore-protect, skip permanently failed jobs.
            # To force-rescore those, run `applypilot prune --reset-failed` first.
            query = (
                "SELECT * FROM jobs WHERE full_description IS NOT NULL "
                "AND (apply_attempts IS NULL OR apply_attempts < 99)"
            )
            params = []
        if limit > 0:
            query += f" LIMIT {limit}"
        jobs = conn.execute(query, params).fetchall()

        # Count how many protected jobs were skipped so we can report it
        protected_count = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE full_description IS NOT NULL "
            "AND fit_score >= ? AND (apply_attempts IS NULL OR apply_attempts < 99)",
            (min_score,),
        ).fetchone()[0] if rescore_protect else 0
    else:
        jobs = get_jobs_by_stage(conn=conn, stage="pending_score", limit=limit)

    if not jobs:
        msg = "No jobs to score."
        if rescore and rescore_protect:
            msg += (
                f" All jobs with descriptions are already scored >= {min_score} "
                f"and are protected. Use --no-rescore-protect to force-rescore them."
            )
        log.info(msg)
        return {
            "scored": 0,
            "skipped_protected": protected_count if rescore and rescore_protect else 0,
            "errors": 0,
            "elapsed": 0.0,
            "distribution": [],
        }

    # Convert sqlite3.Row to dicts if needed
    if jobs and not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    if rescore and rescore_protect and protected_count:
        log.info(
            "Rescore: %d job(s) to score, %d protected (score >= %d) — skipped. "
            "Use --no-rescore-protect to include them.",
            len(jobs), protected_count, min_score,
        )
    else:
        log.info("Scoring %d jobs sequentially...", len(jobs))

    # Load scoring feedback once — injected into every prompt without re-reading the file.
    feedback_block = _load_scoring_feedback()

    t0 = time.time()
    completed = 0
    errors = 0
    ineligible = 0
    regressions: list[dict] = []

    for job in jobs:
        old_score: int | None = job.get("fit_score")
        result = score_job(resume_text, job, profile, feedback_block)
        completed += 1

        loc_flag = "" if result["location_eligible"] else " [NOT ELIGIBLE - location]"
        rescore_flag = f" (was {old_score})" if rescore and old_score is not None else ""

        if result["error"]:
            # LLM failure — do NOT write to DB so fit_score stays NULL and the
            # job remains in pending_score, picked up automatically on the next run.
            errors += 1
            log.warning(
                "[%d/%d] SKIPPED (LLM error — will retry next run)%s  %s",
                completed, len(jobs),
                rescore_flag,
                (job.get("title") or "?")[:60],
            )
            continue

        log.info(
            "[%d/%d] score=%d%s%s  %s",
            completed, len(jobs),
            result["score"], rescore_flag, loc_flag,
            (job.get("title") or "?")[:60],
        )

        if not result["location_eligible"]:
            ineligible += 1

        # Warn prominently if a previously eligible job dropped below the threshold
        if (
            rescore
            and old_score is not None
            and old_score >= min_score
            and result["score"] < min_score
        ):
            tailored_path = job.get("tailored_resume_path") or ""
            has_tailored = " It already has a tailored resume." if tailored_path else ""
            log.warning(
                "SCORE REGRESSION: '%s' dropped %d -> %d (threshold: %d).%s\n"
                "  This job is no longer eligible for tailoring/apply.\n"
                "  Reasoning: %s\n"
                "  To restore: lower --min-score, manually adjust, or discard with:\n"
                "    applypilot apply --mark-failed %s --fail-reason score_regression",
                job.get("title", "?"),
                old_score, result["score"], min_score,
                has_tailored,
                result["reasoning"],
                job.get("url", ""),
            )
            regressions.append(result)

        # Write immediately so Ctrl+C preserves all progress up to this point.
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE jobs "
            "SET fit_score=?, score_reasoning=?, scored_at=?, location_eligible=?, "
            "previous_score=? "
            "WHERE url=?",
            (
                result["score"],
                f"{result['keywords']}\n{result['reasoning']}",
                now,
                1 if result["location_eligible"] else 0,
                old_score,
                job["url"],
            ),
        )
        conn.commit()

    scored = completed - errors
    elapsed = time.time() - t0
    log.info(
        "Done: %d scored, %d LLM error(s) left in queue — %.1fs (%.1f jobs/sec)",
        scored, errors, elapsed, scored / elapsed if elapsed > 0 else 0,
    )

    # Score distribution
    dist = conn.execute("""
        SELECT fit_score, COUNT(*) FROM jobs
        WHERE fit_score IS NOT NULL
        GROUP BY fit_score ORDER BY fit_score DESC
    """).fetchall()
    distribution = [(row[0], row[1]) for row in dist]

    # Summary notices
    if ineligible:
        log.info(
            "%d job(s) scored <=2 due to location ineligibility. "
            "They will not proceed to tailoring or apply.",
            ineligible,
        )

    if regressions:
        log.warning(
            "%d job(s) dropped below the threshold (%d) after rescoring. "
            "See warnings above. Run 'applypilot status' to review.",
            len(regressions), min_score,
        )

    skipped = protected_count if (rescore and rescore_protect) else 0
    return {
        "scored": scored,
        "skipped_protected": skipped,
        "errors": errors,
        "elapsed": elapsed,
        "distribution": distribution,
    }
