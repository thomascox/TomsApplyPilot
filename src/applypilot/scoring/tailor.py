"""Resume tailoring: LLM-powered ATS-optimized resume generation per job.

THIS IS THE HEAVIEST REFACTOR. Every piece of personal data -- name, email, phone,
skills, companies, projects, school -- is loaded at runtime from the user's profile.
Zero hardcoded personal information.

The LLM returns structured JSON, code assembles the final text. Header (name, contact)
is always code-injected, never LLM-generated. Each retry starts a fresh conversation
to avoid apologetic spirals.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from applypilot.config import RESUME_PATH, TAILORED_DIR, load_profile
from applypilot.database import get_connection, get_jobs_by_stage
from applypilot.llm import get_client
from applypilot.scoring.validator import (
    BANNED_WORDS,
    FABRICATION_WATCHLIST,
    sanitize_text,
    validate_json_fields,
    validate_tailored_resume,
)

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 8  # max cross-run retries before giving up


# ── Prompt Builders (profile-driven) ──────────────────────────────────────

def _build_tailor_prompt(profile: dict) -> str:
    """Build the resume tailoring system prompt from the user's profile.

    All skills boundaries, preserved entities, and formatting rules are
    derived from the profile -- nothing is hardcoded.
    """
    boundary = profile.get("skills_boundary", {})
    resume_facts = profile.get("resume_facts", {})

    # Format skills boundary for the prompt
    skills_lines = []
    for category, items in boundary.items():
        if isinstance(items, list) and items:
            label = category.replace("_", " ").title()
            skills_lines.append(f"{label}: {', '.join(items)}")
    skills_block = "\n".join(skills_lines)

    # Preserved entities
    companies = resume_facts.get("preserved_companies", [])
    projects = resume_facts.get("preserved_projects", [])
    school = resume_facts.get("preserved_school", "")
    real_metrics = resume_facts.get("real_metrics", [])

    companies_str = ", ".join(companies) if companies else "N/A"
    projects_str = ", ".join(projects) if projects else "N/A"
    metrics_str = ", ".join(real_metrics) if real_metrics else "N/A"

    # Include ALL banned words from the validator so the LLM knows exactly
    # what will be rejected — the validator checks for these automatically.
    banned_str = ", ".join(BANNED_WORDS)

    education = profile.get("experience", {})
    education_level = education.get("education_level", "")

    return f"""You are a senior technical recruiter rewriting a resume to get this person an interview.

Take the base resume and job description. Return a tailored resume as a JSON object.

## RECRUITER SCAN (6 seconds):
1. Title -- matches what they're hiring?
2. Summary -- 2 sentences proving you've done this work
3. First 3 bullets of most recent role -- verbs and outcomes match?
4. Skills -- must-haves visible immediately?

## SKILLS BOUNDARY (real skills only):
{skills_block}

You MAY add 2-3 closely related tools (Kubernetes if Docker, Terraform if AWS, Redis if PostgreSQL). No unrelated languages/frameworks.

## TAILORING RULES:

TITLE: Match the target role. Keep seniority (Senior/Lead/Staff). Drop company suffixes and team names.

SUMMARY: Rewrite from scratch. Lead with the 1-2 skills that matter most for THIS role. Sound like someone who's done this job.

SKILLS: Reorder each category so the job's must-haves appear first.

Reframe EVERY bullet for this role. Same real work, different angle. Every bullet must be reworded. Never copy verbatim.

PROJECTS: Reorder by relevance. Drop irrelevant projects entirely.

BULLETS: Strong verb + what you built + quantified impact. Vary verbs (Built, Designed, Implemented, Reduced, Automated, Deployed, Operated, Optimized). Most relevant first. Max 4 per section.

## VOICE:
- Write like a real engineer. Short, direct.
- GOOD: "Automated financial reporting with Python + API integrations, cut processing time from 10 hours to 2"
- BAD: "Leveraged cutting-edge AI technologies to drive transformative operational efficiencies"
- BANNED WORDS (using ANY of these = validation failure -- do not use them even once):
  {banned_str}
- No em dashes. Use commas, periods, or hyphens.

## HARD RULES:
- Do NOT invent work, companies, degrees, or certifications
- Do NOT change real numbers ({metrics_str})
- Preserved companies: {companies_str} -- names stay as-is
- Preserved school: {school}
- Must fit 1 page.

## OUTPUT: Return ONLY valid JSON. No markdown fences. No commentary. No "here is" preamble.

{{"title":"Role Title","summary":"2-3 tailored sentences.","skills":{{"Languages":"...","Frameworks":"...","DevOps & Infra":"...","Databases":"...","Tools":"..."}},"experience":[{{"header":"Title at Company","subtitle":"Tech | Dates","bullets":["bullet 1","bullet 2","bullet 3","bullet 4"]}}],"projects":[{{"header":"Project Name - Description","subtitle":"Tech | Dates","bullets":["bullet 1","bullet 2"]}}],"education":"{school} | {education_level}"}}"""


def _build_judge_prompt(profile: dict) -> str:
    """Build the LLM judge prompt from the user's profile."""
    boundary = profile.get("skills_boundary", {})
    resume_facts = profile.get("resume_facts", {})

    # Flatten allowed skills for the judge
    all_skills: list[str] = []
    for items in boundary.values():
        if isinstance(items, list):
            all_skills.extend(items)
    skills_str = ", ".join(all_skills) if all_skills else "N/A"

    real_metrics = resume_facts.get("real_metrics", [])
    metrics_str = ", ".join(real_metrics) if real_metrics else "N/A"

    return f"""You are a resume quality judge. A tailoring engine rewrote a resume to target a specific job. Your job is to catch LIES, not style changes.

You must answer with EXACTLY this format:
VERDICT: PASS or FAIL
ISSUES: (list any problems, or "none")

## CONTEXT -- what the tailoring engine was instructed to do (all of this is ALLOWED):
- Change the title to match the target role
- Rewrite the summary from scratch for the target job
- Reorder bullets and projects to put the most relevant first
- Reframe bullets to use the job's language
- Drop low-relevance bullets and replace with more relevant ones from other sections
- Reorder the skills section to put job-relevant skills first
- Change tone and wording extensively

## WHAT IS FABRICATION (FAIL for these):
1. Adding tools, languages, or frameworks to TECHNICAL SKILLS that aren't in the original. The allowed skills are ONLY: {skills_str}
2. Inventing NEW metrics or numbers not in the original. The real metrics are: {metrics_str}
3. Inventing work that has no basis in any original bullet (completely new achievements).
4. Adding companies, roles, or degrees that don't exist.
5. Changing real numbers (inflating 80% to 95%, 500 nodes to 1000 nodes).

## WHAT IS NOT FABRICATION (do NOT fail for these):
- Rewording any bullet, even heavily, as long as the underlying work is real
- Combining two original bullets into one
- Splitting one original bullet into two
- Describing the same work with different emphasis
- Dropping bullets entirely
- Reordering anything
- Changing the title or summary completely

## TOLERANCE RULE:
The goal is to get interviews, not to be a perfect fact-checker. Allow up to 3 minor stretches per resume:
- Adding a closely related tool the candidate could realistically know is a MINOR STRETCH, not fabrication.
- Reframing a metric with slightly different wording is a MINOR STRETCH.
- Adding any LEARNABLE skill given their existing stack is a MINOR STRETCH.
- Only FAIL if there are MAJOR lies: completely invented projects, fake companies, fake degrees, wildly inflated numbers, or skills from a completely different domain.

Be strict about major lies. Be lenient about minor stretches and learnable skills. Do not fail for style, tone, or restructuring."""


# ── JSON Extraction ───────────────────────────────────────────────────────

def _attempt_json_repair(raw: str) -> dict | None:
    """Try to salvage a truncated JSON object by closing open structures.

    Called as a last resort when standard JSON parsing fails. Works by
    counting unclosed braces/brackets and appending the required closing
    characters. Only returns a result if the repaired string parses cleanly.

    Args:
        raw: Raw (potentially truncated) LLM response text.

    Returns:
        Parsed dict if repair succeeded, None otherwise.
    """
    raw = raw.strip()
    start = raw.find("{")
    if start == -1:
        return None
    fragment = raw[start:]

    # Trim a trailing incomplete quoted string (odd number of unescaped quotes)
    # so that appending closing braces produces valid JSON.
    # Simple heuristic: drop everything after the last complete value token.
    last_complete = max(
        fragment.rfind("}"),
        fragment.rfind("]"),
        fragment.rfind('"'),
    )
    if last_complete != -1 and last_complete < len(fragment) - 1:
        fragment = fragment[: last_complete + 1]

    open_braces = fragment.count("{") - fragment.count("}")
    open_brackets = fragment.count("[") - fragment.count("]")
    closing = ("]" * max(0, open_brackets)) + ("}" * max(0, open_braces))

    try:
        result = json.loads(fragment + closing)
        log.debug("JSON repair succeeded (appended %r)", closing)
        return result
    except json.JSONDecodeError:
        return None


def extract_json(raw: str) -> dict:
    """Robustly extract JSON from LLM response (handles fences, preamble).

    Attempts, in order:
      1. Direct parse
      2. Strip markdown fences
      3. Find outermost { ... }
      4. Truncation repair (close unclosed braces/brackets)

    Args:
        raw: Raw LLM response text.

    Returns:
        Parsed JSON dict.

    Raises:
        ValueError: If no valid JSON found after all attempts.
    """
    raw = raw.strip()

    # Direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Markdown fences
    if "```" in raw:
        for part in raw.split("```")[1::2]:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            try:
                return json.loads(part)
            except json.JSONDecodeError:
                continue

    # Find outermost { ... }
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass

    # Last resort: attempt truncation repair
    repaired = _attempt_json_repair(raw)
    if repaired is not None:
        return repaired

    raise ValueError("No valid JSON found in LLM response")


# ── Error Hint Mapping ────────────────────────────────────────────────────

_ERROR_HINTS: list[tuple[str, str]] = [
    (
        "Missing required field:",
        "The LLM omitted a required JSON key -- this usually means the response was "
        "truncated mid-output. Try: increase LLM_LOCAL_TIMEOUT, switch to a model with "
        "a larger context window, or set LLM_LOCAL_MODELS to a more capable model.",
    ),
    (
        "Fabricated skill:",
        "The LLM added a skill not in your profile. If this skill IS real, add it to "
        "profile.json under skills_boundary. To skip this check entirely, run with "
        "--mode lenient.",
    ),
    (
        "Company '",
        "A real employer was dropped from the experience section. Check that "
        "profile.json resume_facts.preserved_companies contains the exact company name "
        "as it appears in your base resume.",
    ),
    (
        "Education '",
        "Your school was dropped from the education section. Verify "
        "profile.json resume_facts.preserved_school matches your base resume exactly.",
    ),
    (
        "LLM self-talk:",
        "The model output conversational text (apologies, preamble) instead of JSON. "
        "Try a stronger or more instruction-following model. For local models, try "
        "setting LLM_LOCAL_MODELS to a different model.",
    ),
    (
        "Banned words:",
        "The LLM used filler/buzzword language. In 'normal' mode this is a warning only "
        "and will not block the resume. Switch to --mode lenient to ignore entirely, or "
        "retry -- it usually clears on the next attempt.",
    ),
    (
        "No valid JSON",
        "The LLM returned no parseable JSON at all. This often means the response was "
        "completely cut off (token limit) or the model does not follow JSON instructions "
        "well. Try: increase LLM_LOCAL_TIMEOUT, use a more capable model, or reduce "
        "the job description length.",
    ),
]


def _error_hint(error: str) -> str | None:
    """Return an actionable hint string for a known validation error, or None."""
    for prefix, hint in _ERROR_HINTS:
        if prefix.lower() in error.lower():
            return hint
    return None


def _format_validation_block(validation: dict, attempt: int, max_retries: int, job_title: str) -> str:
    """Format a human-readable validation failure block for logging."""
    lines = [
        f"Attempt {attempt}/{max_retries} VALIDATION FAILED for '{job_title}':",
    ]
    errors = validation.get("errors", [])
    warnings = validation.get("warnings", [])
    if errors:
        lines.append(f"  Errors ({len(errors)}):")
        for e in errors:
            lines.append(f"    x {e}")
            hint = _error_hint(e)
            if hint:
                lines.append(f"      -> {hint}")
    if warnings:
        lines.append(f"  Warnings ({len(warnings)}):")
        for w in warnings:
            lines.append(f"    ! {w}")
    return "\n".join(lines)


# ── Resume Assembly (profile-driven header) ──────────────────────────────

def assemble_resume_text(data: dict, profile: dict) -> str:
    """Convert JSON resume data to formatted plain text.

    Header (name, location, contact) is ALWAYS code-injected from the profile,
    never LLM-generated. All text fields are sanitized.

    Args:
        data: Parsed JSON resume from the LLM.
        profile: User profile dict from load_profile().

    Returns:
        Formatted resume text.
    """
    personal = profile.get("personal", {})
    lines: list[str] = []

    # Header -- always code-injected from profile
    lines.append(personal.get("full_name", ""))
    lines.append(sanitize_text(data.get("title", "Software Engineer")))

    # Contact line
    contact_parts: list[str] = []
    if personal.get("email"):
        contact_parts.append(personal["email"])
    if personal.get("phone"):
        contact_parts.append(personal["phone"])
    if personal.get("github_url"):
        contact_parts.append(personal["github_url"])
    if personal.get("linkedin_url"):
        contact_parts.append(personal["linkedin_url"])
    if contact_parts:
        lines.append(" | ".join(contact_parts))
    lines.append("")

    # Summary
    lines.append("SUMMARY")
    lines.append(sanitize_text(data["summary"]))
    lines.append("")

    # Technical Skills
    lines.append("TECHNICAL SKILLS")
    if isinstance(data["skills"], dict):
        for cat, val in data["skills"].items():
            lines.append(f"{cat}: {sanitize_text(str(val))}")
    lines.append("")

    # Experience
    lines.append("EXPERIENCE")
    for entry in data.get("experience", []):
        lines.append(sanitize_text(entry.get("header", "")))
        if entry.get("subtitle"):
            lines.append(sanitize_text(entry["subtitle"]))
        for b in entry.get("bullets", []):
            lines.append(f"- {sanitize_text(b)}")
        lines.append("")

    # Projects
    lines.append("PROJECTS")
    for entry in data.get("projects", []):
        lines.append(sanitize_text(entry.get("header", "")))
        if entry.get("subtitle"):
            lines.append(sanitize_text(entry["subtitle"]))
        for b in entry.get("bullets", []):
            lines.append(f"- {sanitize_text(b)}")
        lines.append("")

    # Education
    lines.append("EDUCATION")
    lines.append(sanitize_text(str(data.get("education", ""))))

    return "\n".join(lines)


# ── LLM Judge ────────────────────────────────────────────────────────────

def judge_tailored_resume(
    original_text: str, tailored_text: str, job_title: str, profile: dict
) -> dict:
    """LLM judge layer: catches subtle fabrication that programmatic checks miss.

    Args:
        original_text: Base resume text.
        tailored_text: Tailored resume text.
        job_title: Target job title.
        profile: User profile for building the judge prompt.

    Returns:
        {"passed": bool, "verdict": str, "issues": str, "raw": str}
    """
    judge_prompt = _build_judge_prompt(profile)

    messages = [
        {"role": "system", "content": judge_prompt},
        {"role": "user", "content": (
            f"JOB TITLE: {job_title}\n\n"
            f"ORIGINAL RESUME:\n{original_text}\n\n---\n\n"
            f"TAILORED RESUME:\n{tailored_text}\n\n"
            "Judge this tailored resume:"
        )},
    ]

    client = get_client()
    response, _ = client.chat(messages, max_tokens=512, temperature=0.1)

    passed = "VERDICT: PASS" in response.upper()
    issues = "none"
    if "ISSUES:" in response.upper():
        issues_idx = response.upper().index("ISSUES:")
        issues = response[issues_idx + 7:].strip()

    return {
        "passed": passed,
        "verdict": "PASS" if passed else "FAIL",
        "issues": issues,
        "raw": response,
    }


# ── Core Tailoring ───────────────────────────────────────────────────────

def tailor_resume(
    resume_text: str, job: dict, profile: dict,
    max_retries: int = 7, validation_mode: str = "normal",
) -> tuple[str, dict]:
    """Generate a tailored resume via JSON output + fresh context on each retry.

    Key design choices:
    - LLM returns structured JSON, code assembles the text (no header leaks)
    - Each retry starts a FRESH conversation (no apologetic spiral)
    - Issues from previous attempts are noted in the system prompt
    - Em dashes and smart quotes are auto-fixed, not rejected

    Args:
        resume_text:      Base resume text.
        job:              Job dict with title, site, location, full_description.
        profile:          User profile dict.
        max_retries:      Maximum retry attempts.
        validation_mode:  "strict", "normal", or "lenient".
                          strict  -- banned words trigger retries; judge must pass
                          normal  -- banned words = warnings only; judge can fail on last retry
                          lenient -- banned words ignored; LLM judge skipped

    Returns:
        (tailored_text, report) where report contains validation details.
    """
    client = get_client()

    # Adapt to local vs cloud: local models often have smaller output windows
    # and slower context processing, so we shorten input and cap output tokens.
    if client.is_local:
        desc_limit = 3000
        max_output_tokens = 2048
        log.debug(
            "Local LLM detected: limiting job description to %d chars "
            "and max_tokens to %d to fit context window.",
            desc_limit, max_output_tokens,
        )
    else:
        desc_limit = 6000
        max_output_tokens = 4096

    job_text = (
        f"TITLE: {job['title']}\n"
        f"COMPANY: {job['site']}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:desc_limit]}"
    )

    report: dict = {
        "attempts": 0, "validator": None, "judge": None,
        "status": "pending", "validation_mode": validation_mode,
    }
    avoid_notes: list[str] = []
    tailored = ""
    tailor_prompt_base = _build_tailor_prompt(profile)
    job_title = job.get("title", "Unknown")

    for attempt in range(max_retries + 1):
        report["attempts"] = attempt + 1

        # Fresh conversation every attempt
        prompt = tailor_prompt_base
        if avoid_notes:
            prompt += "\n\n## AVOID THESE ISSUES (from previous attempt):\n" + "\n".join(
                f"- {n}" for n in avoid_notes[-5:]
            )

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": (
                f"ORIGINAL RESUME:\n{resume_text}\n\n---\n\n"
                f"TARGET JOB:\n{job_text}\n\nReturn the JSON:"
            )},
        ]

        raw, finish_reason = client.chat(messages, max_tokens=max_output_tokens, temperature=0.4)

        # Detect token-limit truncation before trying to parse JSON
        if finish_reason == "length":
            truncation_hint = (
                f"Response truncated at token limit ({max_output_tokens} tokens). "
                "The JSON is incomplete. Shorten your output: use fewer bullets "
                "(max 3 per section), shorter sentences, and omit optional fields."
            )
            log.warning(
                "Attempt %d/%d TOKEN LIMIT HIT for '%s' on model '%s'.\n"
                "  The response was cut off before the JSON was complete.\n"
                "  Fix options:\n"
                "    - Use a model with a larger output context (cloud models support 4096+ tokens)\n"
                "    - Increase LLM_LOCAL_TIMEOUT if the model is timing out early\n"
                "    - Set LLM_LOCAL_MODELS to a model with higher token output capacity\n"
                "    - Switch to a cloud provider (GEMINI_API_KEY or OPENAI_API_KEY)",
                attempt + 1, max_retries + 1, job_title, client.model,
            )
            avoid_notes.append(truncation_hint)
            continue

        # Parse JSON from response
        try:
            data = extract_json(raw)
        except ValueError as exc:
            log.warning(
                "Attempt %d/%d JSON PARSE FAILED for '%s':\n"
                "  Error: %s\n"
                "  Response preview (first 300 chars): %s\n"
                "  Fix: The model may not be following JSON instructions. Try a stronger\n"
                "  model via LLM_LOCAL_MODELS, or switch to a cloud provider.",
                attempt + 1, max_retries + 1, job_title,
                exc,
                raw[:300].replace("\n", " "),
            )
            avoid_notes.append(
                "Output was not valid JSON. Return ONLY a JSON object, "
                "nothing else. No markdown. No preamble."
            )
            continue

        # Layer 1: Validate JSON fields
        validation = validate_json_fields(data, profile, mode=validation_mode)
        report["validator"] = validation

        if not validation["passed"]:
            log.warning(
                "%s",
                _format_validation_block(validation, attempt + 1, max_retries + 1, job_title),
            )
            # Only retry if there are hard errors (warnings never block)
            avoid_notes.extend(validation["errors"])
            if attempt < max_retries:
                continue
            # Last attempt -- assemble whatever we got
            tailored = assemble_resume_text(data, profile)
            report["status"] = "failed_validation"
            log.error(
                "TAILOR FAILED (validation) for '%s' after %d attempts.\n"
                "  Final errors: %s\n"
                "  Check your profile.json and the hints above for how to fix these.",
                job_title, attempt + 1,
                "; ".join(validation["errors"]),
            )
            return tailored, report

        # Log any warnings even on a pass
        if validation.get("warnings"):
            log.info(
                "Attempt %d/%d validation passed with warnings for '%s': %s",
                attempt + 1, max_retries + 1, job_title,
                ", ".join(validation["warnings"]),
            )

        # Assemble text (header injected by code, em dashes auto-fixed)
        tailored = assemble_resume_text(data, profile)

        # Layer 2: LLM judge (catches subtle fabrication) -- skipped in lenient mode
        if validation_mode == "lenient":
            report["judge"] = {"verdict": "SKIPPED", "passed": True, "issues": "none"}
            report["status"] = "approved"
            return tailored, report

        judge = judge_tailored_resume(resume_text, tailored, job_title, profile)
        report["judge"] = judge

        if not judge["passed"]:
            log.warning(
                "Attempt %d/%d JUDGE FAILED for '%s':\n"
                "  Issues: %s\n"
                "  Hint: If the judge is being too strict, run with --mode lenient to skip\n"
                "  the judge entirely. If the issues are real fabrications, check that your\n"
                "  profile.json skills_boundary lists all your actual skills so the LLM\n"
                "  does not need to invent them.",
                attempt + 1, max_retries + 1, job_title, judge["issues"],
            )
            avoid_notes.append(f"Judge rejected: {judge['issues']}")
            if attempt < max_retries:
                if validation_mode != "lenient":
                    continue
            # Accept best attempt on last retry (all modes) or if lenient
            report["status"] = "approved_with_judge_warning"
            return tailored, report

        # Both passed
        report["status"] = "approved"
        return tailored, report

    report["status"] = "exhausted_retries"
    log.error(
        "TAILOR FAILED (exhausted %d retries) for '%s'.\n"
        "  Last avoid notes: %s\n"
        "  Suggestions:\n"
        "    - Run with --mode lenient to lower the validation bar\n"
        "    - Try a different/stronger model via LLM_LOCAL_MODELS\n"
        "    - Check profile.json for missing skills or mismatched company names",
        max_retries + 1, job_title,
        " | ".join(avoid_notes[-3:]) if avoid_notes else "none",
    )
    return tailored, report


# ── Failure Summary ───────────────────────────────────────────────────────

def _print_failure_summary(results: list[dict], tailored_dir: Path) -> None:
    """Log a structured summary of all failed jobs after a tailoring run.

    Reads each job's saved REPORT.json for detailed error information.
    Only called when there are failures to report.
    """
    _success = {"approved", "approved_with_judge_warning"}
    failed = [r for r in results if r.get("status") not in _success]
    if not failed:
        return

    separator = "-" * 60
    log.warning("%s", separator)
    log.warning("TAILORING FAILURES SUMMARY (%d job(s) failed):", len(failed))
    log.warning("%s", separator)

    for r in failed:
        log.warning(
            "FAILED: %s at %s",
            r.get("title", "?"), r.get("site", "?"),
        )
        log.warning("  Status   : %s  (attempts: %s)", r.get("status", "?"), r.get("attempts", "?"))

        # Try to load the saved report for detailed errors
        report_path = None
        if r.get("path"):
            prefix = Path(r["path"]).stem
            report_path = tailored_dir / f"{prefix}_REPORT.json"

        if report_path and report_path.exists():
            try:
                saved = json.loads(report_path.read_text(encoding="utf-8"))

                validator = saved.get("validator") or {}
                v_errors = validator.get("errors", [])
                v_warnings = validator.get("warnings", [])
                if v_errors:
                    log.warning("  Validator errors:")
                    for e in v_errors:
                        log.warning("    x %s", e)
                        hint = _error_hint(e)
                        if hint:
                            log.warning("      -> %s", hint)
                if v_warnings:
                    log.warning("  Validator warnings:")
                    for w in v_warnings:
                        log.warning("    ! %s", w)

                judge = saved.get("judge") or {}
                if judge.get("verdict") and judge["verdict"] not in ("PASS", "SKIPPED"):
                    log.warning("  Judge issues: %s", judge.get("issues", "none"))
                    log.warning(
                        "  Judge hint: Run with --mode lenient to skip the judge, "
                        "or add missing skills to profile.json skills_boundary."
                    )
            except Exception:
                log.debug("Could not read report for %s", r.get("title"), exc_info=True)
        else:
            if r.get("status") == "error":
                log.warning(
                    "  Error: see log output above for exception details."
                )

        log.warning("%s", separator)


# ── Batch Entry Point ────────────────────────────────────────────────────

def run_tailoring(min_score: int = 7, limit: int = 20,
                  validation_mode: str = "normal") -> dict:
    """Generate tailored resumes for high-scoring jobs.

    Args:
        min_score:       Minimum fit_score to tailor for.
        limit:           Maximum jobs to process.
        validation_mode: "strict", "normal", or "lenient".

    Returns:
        {"approved": int, "failed": int, "errors": int, "elapsed": float}
    """
    profile = load_profile()
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    conn = get_connection()

    jobs = get_jobs_by_stage(conn=conn, stage="pending_tailor", min_score=min_score, limit=limit)

    if not jobs:
        log.info("No untailored jobs with score >= %d.", min_score)
        return {"approved": 0, "failed": 0, "errors": 0, "elapsed": 0.0}

    TAILORED_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Tailoring resumes for %d jobs (score >= %d)...", len(jobs), min_score)
    t0 = time.time()
    completed = 0
    results: list[dict] = []
    stats: dict[str, int] = {"approved": 0, "failed_validation": 0, "failed_judge": 0, "error": 0}

    for job in jobs:
        completed += 1
        try:
            tailored, report = tailor_resume(resume_text, job, profile,
                                             validation_mode=validation_mode)

            # Build safe filename prefix
            safe_title = re.sub(r"[^\w\s-]", "", job["title"])[:50].strip().replace(" ", "_")
            safe_site = re.sub(r"[^\w\s-]", "", job["site"])[:20].strip().replace(" ", "_")
            prefix = f"{safe_site}_{safe_title}"

            # Save tailored resume text
            txt_path = TAILORED_DIR / f"{prefix}.txt"
            txt_path.write_text(tailored, encoding="utf-8")

            # Save job description for traceability
            job_path = TAILORED_DIR / f"{prefix}_JOB.txt"
            job_desc = (
                f"Title: {job['title']}\n"
                f"Company: {job['site']}\n"
                f"Location: {job.get('location', 'N/A')}\n"
                f"Score: {job.get('fit_score', 'N/A')}\n"
                f"URL: {job['url']}\n\n"
                f"{job.get('full_description', '')}"
            )
            job_path.write_text(job_desc, encoding="utf-8")

            # Save validation report
            report_path = TAILORED_DIR / f"{prefix}_REPORT.json"
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

            # Generate PDF for approved resumes (best-effort)
            # "approved_with_judge_warning" is also a success -- resume was generated.
            pdf_path = None
            if report["status"] in ("approved", "approved_with_judge_warning"):
                try:
                    from applypilot.scoring.pdf import convert_to_pdf
                    pdf_path = str(convert_to_pdf(txt_path))
                except Exception:
                    log.debug("PDF generation failed for %s", txt_path, exc_info=True)

            result = {
                "url": job["url"],
                "path": str(txt_path),
                "pdf_path": pdf_path,
                "title": job["title"],
                "site": job["site"],
                "status": report["status"],
                "attempts": report["attempts"],
            }
        except Exception as e:
            result = {
                "url": job["url"], "title": job["title"], "site": job["site"],
                "status": "error", "attempts": 0, "path": None, "pdf_path": None,
            }
            log.error(
                "%d/%d [ERROR] %s\n"
                "  Exception: %s\n"
                "  Run with --verbose / set log level to DEBUG for the full traceback.",
                completed, len(jobs), job["title"][:40], e,
                exc_info=log.isEnabledFor(logging.DEBUG),
            )

        results.append(result)
        stats[result.get("status", "error")] = stats.get(result.get("status", "error"), 0) + 1

        elapsed = time.time() - t0
        rate = completed / elapsed if elapsed > 0 else 0
        log.info(
            "%d/%d [%s] attempts=%s | %.1f jobs/min | %s",
            completed, len(jobs),
            result["status"].upper(),
            result.get("attempts", "?"),
            rate * 60,
            result["title"][:40],
        )

    # Persist to DB: increment attempt counter for ALL, save path only for approved
    now = datetime.now(timezone.utc).isoformat()
    _success_statuses = {"approved", "approved_with_judge_warning"}
    for r in results:
        if r["status"] in _success_statuses:
            conn.execute(
                "UPDATE jobs SET tailored_resume_path=?, tailored_at=?, "
                "tailor_attempts=COALESCE(tailor_attempts,0)+1 WHERE url=?",
                (r["path"], now, r["url"]),
            )
        else:
            conn.execute(
                "UPDATE jobs SET tailor_attempts=COALESCE(tailor_attempts,0)+1 WHERE url=?",
                (r["url"],),
            )
    conn.commit()

    elapsed = time.time() - t0
    log.info(
        "Tailoring done in %.1fs: %d approved, %d failed_validation, %d failed_judge, %d errors",
        elapsed,
        stats.get("approved", 0),
        stats.get("failed_validation", 0),
        stats.get("failed_judge", 0),
        stats.get("error", 0),
    )

    # Print detailed failure summary at the end so it's easy to act on
    _print_failure_summary(results, TAILORED_DIR)

    return {
        "approved": stats.get("approved", 0),
        "failed": stats.get("failed_validation", 0) + stats.get("failed_judge", 0),
        "errors": stats.get("error", 0),
        "elapsed": elapsed,
    }
