"""ApplyPilot CLI — the main entry point."""

from __future__ import annotations

import logging
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from applypilot import __version__

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)

app = typer.Typer(
    name="applypilot",
    help="AI-powered end-to-end job application pipeline.",
    no_args_is_help=True,
)
console = Console()
log = logging.getLogger(__name__)

# Valid pipeline stages (in execution order)
VALID_STAGES = ("discover", "enrich", "score", "tailor", "cover", "pdf")

# Generic avoid-message text for each rejection reason (used by `feedback` command).
# Keys match the reject_reason enum stored in the database.
# Keys present here → auto-suggest avoid entries when threshold is hit.
# Omitted keys (duplicate, closed, other) are logistics reasons with no scoring signal.
REASON_AVOID_MESSAGES: dict[str, str] = {
    "wrong_role_type":    "Roles whose core duties do not match target role type",
    "seniority_mismatch": "Roles with significant seniority mismatch (over or under-leveled)",
    "company_type":       "Roles at agencies, staffing firms, or outsourcing companies",
    "salary_below_floor": "Roles with stated salary below the candidate's minimum floor",
    "location":           "Roles with unfavourable location or remote policy",
    "industry":           "Roles in industries that are not a good fit",
    "overqualified":      "Roles where the candidate is significantly overqualified",
}

# Prefix stored in reject_note when user flags an "other" rejection for scoring review.
SCORING_FLAG_PREFIX = "[scoring] "


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bootstrap() -> None:
    """Common setup: load env, create dirs, init DB."""
    from applypilot.config import load_env, ensure_dirs
    from applypilot.database import init_db

    load_env()
    ensure_dirs()
    init_db()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"[bold]applypilot[/bold] {__version__}")
        raise typer.Exit()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Enable DEBUG logging. Shows full tracebacks, LLM request details, and per-attempt validation output.",
        is_eager=True,
    ),
) -> None:
    """ApplyPilot — AI-powered end-to-end job application pipeline.

    Typical workflow:

    \b
      applypilot init                        Set up profile, resume, and search config
      applypilot run discover enrich         Find and enrich job listings
      applypilot run score                   Score jobs by fit (1-10) and location eligibility
      applypilot run tailor cover            Generate tailored resumes and cover letters
      applypilot apply --limit 5             Submit applications

    Run any stage individually or chain them:

    \b
      applypilot run                         Full pipeline (all stages)
      applypilot run score tailor cover      LLM stages only
      applypilot run score --rescore         Re-score all jobs (e.g. after profile changes)

    Check setup with:  applypilot doctor
    View DB stats with: applypilot status
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger("applypilot").setLevel(logging.DEBUG)


@app.command()
def init() -> None:
    """Run the first-time setup wizard (profile, resume, search config)."""
    from applypilot.wizard.init import run_wizard

    run_wizard()


@app.command()
def run(
    stages: Optional[list[str]] = typer.Argument(
        None,
        help=(
            "Pipeline stages to run. "
            f"Valid: {', '.join(VALID_STAGES)}, all. "
            "Defaults to 'all' if omitted. "
            "Examples: 'score tailor cover', 'discover enrich', 'score'."
        ),
    ),
    min_score: int = typer.Option(
        7, "--min-score",
        help=(
            "Minimum fit score (1-10) for tailor and cover stages. "
            "Jobs scoring below this threshold are skipped. "
            "Lower values process more jobs but spend more LLM tokens on poor fits."
        ),
    ),
    workers: int = typer.Option(
        1, "--workers", "-w",
        help="Parallel threads for discover and enrich stages. Has no effect on score/tailor/cover (sequential).",
    ),
    stream: bool = typer.Option(
        False, "--stream",
        help=(
            "Run stages concurrently — each stage starts as soon as the previous one produces work. "
            "Useful for large batches where you want the pipeline to flow without waiting for each stage to fully complete."
        ),
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Preview which stages would run without executing anything.",
    ),
    validation: str = typer.Option(
        "normal", "--validation",
        help=(
            "Tailor/cover validation strictness. "
            "strict: banned filler words are hard errors that trigger retries; LLM judge must pass. "
            "normal: banned words are warnings only; judge failure accepted on last retry (recommended). "
            "lenient: banned words ignored; LLM judge skipped entirely. Fastest, fewest API calls. "
            "Use 'lenient' when using a local LLM that struggles with the judge step."
        ),
    ),
    rescore: bool = typer.Option(
        False, "--rescore",
        help=(
            "Re-score already-scored jobs in addition to new ones. "
            "Use after updating your profile (e.g. adding skills), switching LLM providers, "
            "or after the location eligibility rules changed. "
            "Only applies when the 'score' stage is included."
        ),
    ),
    rescore_protect: bool = typer.Option(
        True, "--rescore-protect/--no-rescore-protect",
        help=(
            "When rescoring, skip jobs whose current score is already >= --min-score "
            "(default: enabled). This prevents model calibration differences from silently "
            "downgrading jobs that are already pipeline-eligible (tailored, ready to apply). "
            "Use --no-rescore-protect to force-rescore everything regardless of current score."
        ),
    ),
    limit: int = typer.Option(
        0, "--limit", "-l",
        help=(
            "Maximum number of jobs to process per stage in this run (0 = no limit). "
            "Useful for testing or when you want to process in smaller batches. "
            "Applies to score, tailor, and cover stages."
        ),
    ),
) -> None:
    """Run pipeline stages: discover, enrich, score, tailor, cover, pdf.

    \b
    STAGES:
      discover   Scrape job boards (JobSpy: Indeed, LinkedIn, ZipRecruiter, Glassdoor)
                 and corporate Workday portals for new job listings.
      enrich     Fetch full job descriptions and direct apply URLs for discovered jobs.
      score      LLM evaluates each job on a 1-10 fit scale. Also checks location
                 eligibility — overseas/hybrid roles are capped at score 2.
      tailor     Generate a tailored resume for each job scoring >= --min-score.
                 Uses LLM with validation (banned words, fabrication check, LLM judge).
      cover      Generate a cover letter for each job that has a tailored resume.
      pdf        Convert tailored resumes and cover letters from .txt to PDF.

    \b
    EXAMPLES:
      applypilot run                         Full pipeline
      applypilot run discover enrich         Discovery only
      applypilot run score --rescore                    Re-score ineligible jobs (protects score >= 7)
      applypilot run score --rescore --min-score 8      Protect only jobs scored >= 8
      applypilot run score --rescore --no-rescore-protect   Force-rescore everything
      applypilot run score --limit 20        Score up to 20 jobs
      applypilot run tailor --min-score 8    Only tailor high-fit jobs
      applypilot run tailor --validation lenient   Use lenient mode for local LLMs
      applypilot run --stream                All stages concurrently
    """
    _bootstrap()

    from applypilot.pipeline import run_pipeline

    stage_list = stages if stages else ["all"]

    # Validate stage names
    for s in stage_list:
        if s != "all" and s not in VALID_STAGES:
            console.print(
                f"[red]Unknown stage:[/red] '{s}'. "
                f"Valid stages: {', '.join(VALID_STAGES)}, all"
            )
            raise typer.Exit(code=1)

    # Gate AI stages behind Tier 2
    llm_stages = {"score", "tailor", "cover"}
    if any(s in stage_list for s in llm_stages) or "all" in stage_list:
        from applypilot.config import check_tier
        check_tier(2, "AI scoring/tailoring")

    # Validate the --validation flag value
    valid_modes = ("strict", "normal", "lenient")
    if validation not in valid_modes:
        console.print(
            f"[red]Invalid --validation value:[/red] '{validation}'. "
            f"Choose from: {', '.join(valid_modes)}"
        )
        raise typer.Exit(code=1)

    result = run_pipeline(
        stages=stage_list,
        min_score=min_score,
        dry_run=dry_run,
        stream=stream,
        workers=workers,
        validation_mode=validation,
        rescore=rescore,
        rescore_protect=rescore_protect,
        limit=limit,
    )

    if result.get("errors"):
        raise typer.Exit(code=1)


@app.command()
def apply(
    limit: Optional[int] = typer.Option(
        None, "--limit", "-l",
        help=(
            "Max number of applications to submit in this session. "
            "Defaults to 1 unless --continuous is set. "
            "Use 0 with --continuous for unlimited."
        ),
    ),
    workers: int = typer.Option(
        1, "--workers", "-w",
        help="Number of parallel browser workers (each opens its own Chrome instance).",
    ),
    min_score: int = typer.Option(
        7, "--min-score",
        help="Minimum fit score (1-10) for job selection. Jobs below this score are skipped.",
    ),
    model: str = typer.Option(
        "haiku", "--model", "-m",
        help=(
            "Claude model for the apply agent. "
            "Valid: haiku (fastest, cheapest), sonnet (balanced), opus (most capable). "
            "haiku is recommended — apply tasks are well-defined and don't need a large model."
        ),
    ),
    continuous: bool = typer.Option(
        False, "--continuous", "-c",
        help="Run forever, polling the DB every --poll-interval seconds for new jobs.",
    ),
    poll_interval: int = typer.Option(
        60, "--poll-interval",
        help=(
            "Seconds between DB polls when the queue is empty (continuous mode only). "
            "Default: 60."
        ),
    ),
    max_job_age: int = typer.Option(
        30, "--max-job-age",
        help=(
            "Skip jobs whose posting date (or discovery date if unknown) is older than "
            "this many days. Marked permanently failed as 'expired_posting' so they are "
            "never retried. Default: 30. Set to 0 to disable the age check."
        ),
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Navigate and fill forms but do not click the final Submit button.",
    ),
    headless: bool = typer.Option(
        False, "--headless",
        help="Run Chrome in headless mode (no visible browser window).",
    ),
    url: Optional[str] = typer.Option(
        None, "--url",
        help="Apply to one specific job URL instead of pulling from the queue.",
    ),
    gen: bool = typer.Option(
        False, "--gen",
        help=(
            "Generate the agent prompt file and print the manual claude CLI command. "
            "Use with --url for debugging a specific job without running the full pipeline. "
            "Does not launch Chrome or submit anything."
        ),
    ),
    mark_applied: Optional[str] = typer.Option(
        None, "--mark-applied",
        help="Manually mark a job URL as applied (bypasses the agent). Useful when you applied manually.",
    ),
    mark_failed: Optional[str] = typer.Option(
        None, "--mark-failed",
        help="Permanently mark a job URL as failed so it is never retried. Use with --fail-reason.",
    ),
    fail_reason: Optional[str] = typer.Option(
        None, "--fail-reason",
        help="Reason string for --mark-failed (e.g. 'requires_relocation', 'salary_too_low').",
    ),
    reset_failed: bool = typer.Option(
        False, "--reset-failed",
        help=(
            "Reset all previously-failed jobs so they can be retried. "
            "Useful after fixing a bug or changing configuration."
        ),
    ),
) -> None:
    """Launch auto-apply: open Chrome, fill forms, and submit applications via Claude agent.

    Requires Tier 3 setup (Claude Code CLI + Chrome + Node.js). Run 'applypilot doctor' to check.
    Jobs must have a tailored resume before they can be applied to — run 'applypilot run tailor' first.

    \b
    NORMAL USAGE:
      applypilot apply                       Apply to 1 job (default)
      applypilot apply --limit 5             Apply to up to 5 jobs
      applypilot apply --continuous          Run forever, apply as jobs become ready
      applypilot apply --url <url>           Apply to a specific job URL
      applypilot apply --dry-run             Fill forms but don't submit
      applypilot apply --max-job-age 14      Skip jobs posted more than 14 days ago
      applypilot apply --max-job-age 0       Disable age check entirely

    \b
    UTILITY MODES (no Chrome needed):
      applypilot apply --mark-applied <url>           Record a manually submitted application
      applypilot apply --mark-failed <url>            Permanently skip a job
        --fail-reason requires_relocation
      applypilot apply --reset-failed                 Re-queue all failed jobs for retry
      applypilot apply --gen --url <url>              Dump the agent prompt for debugging

    \b
    LOCATION INELIGIBLE JOBS:
      Jobs in overseas or hybrid-only locations are scored <=2 by the scorer
      and will not reach the apply queue. If a job slipped through before the
      location check was in place, mark it failed:
        applypilot apply --mark-failed <url> --fail-reason not_eligible_location
    """
    _bootstrap()

    from applypilot.config import check_tier, PROFILE_PATH as _profile_path
    from applypilot.database import get_connection

    # --- Utility modes (no Chrome/Claude needed) ---

    if mark_applied:
        from applypilot.apply.launcher import mark_job
        mark_job(mark_applied, "applied")
        console.print(f"[green]Marked as applied:[/green] {mark_applied}")
        return

    if mark_failed:
        from applypilot.apply.launcher import mark_job
        mark_job(mark_failed, "failed", reason=fail_reason)
        console.print(f"[yellow]Marked as failed:[/yellow] {mark_failed} ({fail_reason or 'manual'})")
        return

    if reset_failed:
        from applypilot.apply.launcher import reset_failed as do_reset
        count = do_reset()
        console.print(f"[green]Reset {count} failed job(s) for retry.[/green]")
        return

    # --- Full apply mode ---

    # Check 1: Tier 3 required (Claude Code CLI + Chrome)
    check_tier(3, "auto-apply")

    # Check 2: Profile exists
    if not _profile_path.exists():
        console.print(
            "[red]Profile not found.[/red]\n"
            "Run [bold]applypilot init[/bold] to create your profile first."
        )
        raise typer.Exit(code=1)

    # Check 3: Tailored resumes exist (skip for --gen with --url)
    if not (gen and url):
        conn = get_connection()
        ready = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL AND applied_at IS NULL"
        ).fetchone()[0]
        if ready == 0:
            console.print(
                "[red]No tailored resumes ready.[/red]\n"
                "Run [bold]applypilot run score tailor[/bold] first to prepare applications."
            )
            raise typer.Exit(code=1)

    if gen:
        from applypilot.apply.launcher import gen_prompt, BASE_CDP_PORT
        target = url or ""
        if not target:
            console.print("[red]--gen requires --url to specify which job.[/red]")
            raise typer.Exit(code=1)
        prompt_file = gen_prompt(target, min_score=min_score, model=model)
        if not prompt_file:
            console.print("[red]No matching job found for that URL.[/red]")
            raise typer.Exit(code=1)
        mcp_path = _profile_path.parent / ".mcp-apply-0.json"
        console.print(f"[green]Wrote prompt to:[/green] {prompt_file}")
        console.print(f"\n[bold]Run manually:[/bold]")
        console.print(
            f"  claude --model {model} -p "
            f"--mcp-config {mcp_path} "
            f"--permission-mode bypassPermissions < {prompt_file}"
        )
        return

    from applypilot.apply.launcher import main as apply_main

    effective_limit = limit if limit is not None else (0 if continuous else 1)

    console.print("\n[bold blue]Launching Auto-Apply[/bold blue]")
    console.print(f"  Limit:    {'unlimited' if continuous else effective_limit}")
    console.print(f"  Workers:  {workers}")
    console.print(f"  Model:    {model}")
    console.print(f"  Headless: {headless}")
    console.print(f"  Dry run:  {dry_run}")
    if url:
        console.print(f"  Target:   {url}")
    console.print()

    apply_main(
        limit=effective_limit,
        target_url=url,
        min_score=min_score,
        headless=headless,
        model=model,
        dry_run=dry_run,
        continuous=continuous,
        poll_interval=poll_interval,
        workers=workers,
        max_job_age_days=max_job_age,
    )


@app.command()
def status() -> None:
    """Show pipeline statistics from the database."""
    _bootstrap()

    from applypilot.database import get_stats

    stats = get_stats()

    console.print("\n[bold]ApplyPilot Pipeline Status[/bold]\n")

    # Summary table
    summary = Table(title="Pipeline Overview", show_header=True, header_style="bold cyan")
    summary.add_column("Metric", style="bold")
    summary.add_column("Count", justify="right")

    summary.add_row("Total jobs discovered", str(stats["total"]))
    summary.add_row("With full description", str(stats["with_description"]))
    summary.add_row("Pending enrichment", str(stats["pending_detail"]))
    summary.add_row("Enrichment errors", str(stats["detail_errors"]))
    summary.add_row("Scored by LLM", str(stats["scored"]))
    summary.add_row("Pending scoring", str(stats["unscored"]))
    summary.add_row("Tailored resumes", str(stats["tailored"]))
    summary.add_row("Pending tailoring (7+)", str(stats["untailored_eligible"]))
    summary.add_row("Cover letters", str(stats["with_cover_letter"]))
    summary.add_row("Ready to apply", str(stats["ready_to_apply"]))
    summary.add_row("Applied", str(stats["applied"]))
    summary.add_row("Apply errors", str(stats["apply_errors"]))

    console.print(summary)

    # Score distribution
    if stats["score_distribution"]:
        dist_table = Table(title="\nScore Distribution", show_header=True, header_style="bold yellow")
        dist_table.add_column("Score", justify="center")
        dist_table.add_column("Count", justify="right")
        dist_table.add_column("Bar")

        max_count = max(count for _, count in stats["score_distribution"]) or 1
        for score, count in stats["score_distribution"]:
            bar_len = int(count / max_count * 30)
            if score >= 7:
                color = "green"
            elif score >= 5:
                color = "yellow"
            else:
                color = "red"
            bar = f"[{color}]{'=' * bar_len}[/{color}]"
            dist_table.add_row(str(score), str(count), bar)

        console.print(dist_table)

    # By site
    if stats["by_site"]:
        site_table = Table(title="\nJobs by Source", show_header=True, header_style="bold magenta")
        site_table.add_column("Site")
        site_table.add_column("Count", justify="right")

        for site, count in stats["by_site"]:
            site_table.add_row(site or "Unknown", str(count))

        console.print(site_table)

    console.print()


@app.command()
def prune(
    stale: bool = typer.Option(
        False, "--stale",
        help=(
            "Permanently fail jobs older than --max-days that have not been tailored or applied. "
            "Marks them apply_attempts=99 / expired_posting so they are excluded from scoring, "
            "tailoring, and the dashboard — but the records are kept for history. "
            "Use --reset-failed to recover them if needed."
        ),
    ),
    max_days: int = typer.Option(
        30, "--max-days",
        help="Age threshold (days) used by --stale. Default: 30.",
    ),
    location_ineligible: bool = typer.Option(
        False, "--location-ineligible",
        help=(
            "Permanently fail queued jobs scored as location-ineligible "
            "(location_eligible=0). They will never be re-queued."
        ),
    ),
    invalid_url: bool = typer.Option(
        False, "--invalid-url",
        help="Permanently fail jobs with a missing or non-HTTP URL.",
    ),
    no_description: bool = typer.Option(
        False, "--no-description",
        help="Delete jobs that were never successfully enriched (no full_description).",
    ),
    all_issues: bool = typer.Option(
        False, "--all",
        help="Run all cleanup operations: --stale, --location-ineligible, --invalid-url, --no-description.",
    ),
    below_salary: bool = typer.Option(
        False, "--below-salary",
        help=(
            "Permanently fail jobs whose posted salary maximum is below your salary floor "
            "(salary_range_min from profile). Only acts on jobs with a parseable salary."
        ),
    ),
    reset_failed: bool = typer.Option(
        False, "--reset-failed",
        help=(
            "Reopen permanently failed apply jobs (apply_attempts=99) so they "
            "re-enter the apply queue. Does not affect non-apply failures."
        ),
    ),
    reset_stuck: bool = typer.Option(
        False, "--reset-stuck",
        help=(
            "Clear apply_status='in_progress' jobs left over from a crashed session "
            "so they can be picked up again."
        ),
    ),
    reset_scores: bool = typer.Option(
        False, "--reset-scores",
        help=(
            "Wipe all fit_score values so every job is re-scored from scratch on the "
            "next 'run score' or 'run' invocation."
        ),
    ),
    nuke: bool = typer.Option(
        False, "--nuke",
        help="[DANGER] Delete every job from the database. Requires --yes.",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Skip confirmation prompts.",
    ),
) -> None:
    """Audit and clean up the jobs database.

    Run with no flags to see a full diagnostic report of all issues — nothing
    is changed. Add specific flags to fix individual categories.

    \b
    AUDIT (no flags):
      applypilot prune                        Show all issue counts, no changes

    \b
    CLEANUP (delete or permanently skip):
      applypilot prune --stale                Permanently fail jobs older than 30 days (pre-tailoring)
      applypilot prune --stale --max-days 14  Use a tighter age threshold
      applypilot prune --location-ineligible  Permanently fail location-ineligible jobs
      applypilot prune --below-salary         Permanently fail jobs with salary below your floor
      applypilot prune --invalid-url          Permanently fail jobs with bad URLs
      applypilot prune --no-description       Delete jobs that never got enriched
      applypilot prune --all                  Run all four cleanup operations above

    \b
    RESET (reopen for retry):
      applypilot prune --reset-failed         Re-queue permanently failed apply jobs
      applypilot prune --reset-stuck          Unstick in_progress jobs from crashed sessions
      applypilot prune --reset-scores         Clear all scores (re-score everything next run)

    \b
    NUCLEAR:
      applypilot prune --nuke --yes           Delete ALL jobs and start fresh
    """
    _bootstrap()

    from datetime import datetime, timedelta, timezone as _tz
    from applypilot.database import get_connection

    conn = get_connection()
    any_flag = any([stale, location_ineligible, invalid_url, no_description,
                    all_issues, below_salary, reset_failed, reset_stuck, reset_scores, nuke])

    # ------------------------------------------------------------------
    # AUDIT MODE — no flags: show a report of all issue categories
    # ------------------------------------------------------------------
    if not any_flag:
        cutoff = (datetime.now(_tz.utc) - timedelta(days=max_days)).strftime("%Y-%m-%d")

        issues = [
            (
                "Stale (no tailoring/apply, older than 30d)",
                conn.execute(
                    "SELECT COUNT(*) FROM jobs "
                    "WHERE tailored_resume_path IS NULL AND applied_at IS NULL "
                    "AND COALESCE(apply_status,'') NOT IN ('applied','failed') "
                    "AND COALESCE(posted_date, substr(discovered_at,1,10)) < ?",
                    (cutoff,),
                ).fetchone()[0],
                "--stale",
            ),
            (
                "Location-ineligible (in queue)",
                conn.execute(
                    "SELECT COUNT(*) FROM jobs "
                    "WHERE location_eligible = 0 "
                    "AND COALESCE(apply_status,'') NOT IN ('applied','failed')",
                ).fetchone()[0],
                "--location-ineligible",
            ),
            (
                "Invalid URL (no http)",
                conn.execute(
                    "SELECT COUNT(*) FROM jobs "
                    "WHERE (url IS NULL OR url = '' OR url = 'None' "
                    "       OR (url NOT LIKE 'http%' AND (application_url IS NULL "
                    "           OR application_url NOT LIKE 'http%'))) "
                    "AND COALESCE(apply_status,'') NOT IN ('applied','failed')",
                ).fetchone()[0],
                "--invalid-url",
            ),
            (
                "No description (enrichment never succeeded)",
                conn.execute(
                    "SELECT COUNT(*) FROM jobs "
                    "WHERE full_description IS NULL",
                ).fetchone()[0],
                "--no-description",
            ),
            (
                "Permanently failed (apply_attempts=99)",
                conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE apply_attempts = 99",
                ).fetchone()[0],
                "--reset-failed (to retry) or --stale (to permanently fail)",
            ),
            (
                "Stuck in-progress (crashed session)",
                conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE apply_status = 'in_progress'",
                ).fetchone()[0],
                "--reset-stuck",
            ),
        ]

        totals = conn.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN applied_at IS NOT NULL THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN fit_score IS NOT NULL THEN 1 ELSE 0 END) "
            "FROM jobs"
        ).fetchone()

        console.print("\n[bold]Database Audit[/bold]\n")

        info = Table(show_header=False, box=None, padding=(0, 2))
        info.add_column("", style="dim")
        info.add_column("", justify="right")
        info.add_row("Total jobs", str(totals[0]))
        info.add_row("Applied", str(totals[1]))
        info.add_row("Scored", str(totals[2]))
        console.print(info)
        console.print()

        issue_table = Table(title="Issues", show_header=True, header_style="bold yellow")
        issue_table.add_column("Issue")
        issue_table.add_column("Count", justify="right")
        issue_table.add_column("Fix with")

        any_issues = False
        for label, count, fix in issues:
            color = "red" if count > 0 else "green"
            issue_table.add_row(label, f"[{color}]{count}[/{color}]", f"[dim]{fix}[/dim]")
            if count > 0:
                any_issues = True

        console.print(issue_table)

        # Below-salary count requires parsing salary strings — shown separately
        try:
            from applypilot.config import load_profile
            from applypilot.view import _parse_salary, _salary_floor_from_profile
            annual_floor, _ = _salary_floor_from_profile()
            sal_rows = conn.execute(
                "SELECT salary FROM jobs WHERE salary IS NOT NULL AND salary != '' "
                "AND COALESCE(apply_attempts,0) < 99 AND applied_at IS NULL"
            ).fetchall()
            below = sum(
                1 for (s,) in sal_rows
                if _parse_salary(s)[1] is not None and _parse_salary(s)[1] < annual_floor
            )
            if below > 0:
                console.print(
                    f"\n[yellow]{below} job(s) have a posted salary below your floor "
                    f"(${annual_floor:,.0f}/yr) — run [bold]applypilot prune --below-salary[/bold] to remove.[/yellow]"
                )
                any_issues = True
        except Exception:
            pass

        if not any_issues:
            console.print("\n[green]No issues found.[/green]")
        else:
            console.print("\nRun with specific flags to fix issues. Use [bold]-y[/bold] to skip confirmations.")
        return

    # ------------------------------------------------------------------
    # Helper: confirm before destructive actions
    # ------------------------------------------------------------------
    def _confirm(msg: str) -> bool:
        if yes:
            return True
        return typer.confirm(msg, default=False)

    def _show_table(rows, columns: list[str], title: str) -> None:
        t = Table(title=title, show_header=True, header_style="bold")
        for c in columns:
            t.add_column(c)
        for row in rows:
            t.add_row(*[str(v or "?")[:60] for v in row])
        console.print(t)

    # ------------------------------------------------------------------
    # --nuke  (must be first — exit after)
    # ------------------------------------------------------------------
    if nuke:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        console.print(f"\n[bold red]NUKE: This will permanently delete all {total} jobs from the database.[/bold red]")
        if not _confirm("Are you absolutely sure?"):
            console.print("[yellow]Aborted.[/yellow]")
            return
        conn.execute("DELETE FROM jobs")
        conn.commit()
        console.print(f"[green]Deleted {total} jobs. Database is empty.[/green]")
        return

    # ------------------------------------------------------------------
    # --stale / --all
    # ------------------------------------------------------------------
    if stale or all_issues:
        cutoff = (datetime.now(_tz.utc) - timedelta(days=max_days)).strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT url, title, site, "
            "COALESCE(posted_date, substr(discovered_at,1,10)) AS age_date, fit_score "
            "FROM jobs "
            "WHERE tailored_resume_path IS NULL AND applied_at IS NULL "
            "AND COALESCE(apply_status,'') NOT IN ('applied','failed') "
            "AND COALESCE(posted_date, substr(discovered_at,1,10)) < ? "
            "ORDER BY age_date",
            (cutoff,),
        ).fetchall()
        if rows:
            _show_table(
                [(r[3], r[4], r[2], r[1]) for r in rows],
                ["Date", "Score", "Site", "Title"],
                f"Stale jobs (older than {max_days} days, not yet tailored/applied)",
            )
            console.print(f"{len(rows)} job(s) will be permanently failed (expired_posting).")
            if _confirm(f"Permanently fail {len(rows)} stale job(s)?"):
                urls = [r[0] for r in rows]
                # Mark permanently failed — same semantics as the apply pre-flight
                # age guard in acquire_job(). Records are kept for history; use
                # --reset-failed to recover them if needed.
                conn.execute(
                    f"UPDATE jobs SET apply_attempts=99, apply_status='failed', "
                    f"apply_error='expired_posting' "
                    f"WHERE url IN ({','.join('?'*len(urls))})",
                    urls,
                )
                conn.commit()
                console.print(f"[green]Permanently failed {len(rows)} stale job(s).[/green]")
            else:
                console.print("[yellow]Skipped --stale.[/yellow]")
        else:
            console.print(f"[green]--stale: no jobs older than {max_days} days found.[/green]")

    # ------------------------------------------------------------------
    # --location-ineligible / --all
    # ------------------------------------------------------------------
    if location_ineligible or all_issues:
        rows = conn.execute(
            "SELECT url, title, site, location, fit_score FROM jobs "
            "WHERE location_eligible = 0 "
            "AND COALESCE(apply_status,'') NOT IN ('applied','failed') "
            "ORDER BY title",
        ).fetchall()
        if rows:
            _show_table(
                [(r[2], r[4], r[3], r[1]) for r in rows],
                ["Site", "Score", "Location", "Title"],
                "Location-ineligible jobs",
            )
            console.print(f"{len(rows)} job(s) to permanently fail.")
            if _confirm(f"Permanently fail {len(rows)} location-ineligible job(s)?"):
                for r in rows:
                    conn.execute(
                        "UPDATE jobs SET apply_status='failed', apply_error='not_eligible_location', "
                        "apply_attempts=99 WHERE url=?",
                        (r[0],),
                    )
                conn.commit()
                console.print(f"[green]Marked {len(rows)} location-ineligible job(s) as permanently failed.[/green]")
            else:
                console.print("[yellow]Skipped --location-ineligible.[/yellow]")
        else:
            console.print("[green]--location-ineligible: none found.[/green]")

    # ------------------------------------------------------------------
    # --below-salary  (not included in --all — opt-in only)
    # ------------------------------------------------------------------
    if below_salary:
        from applypilot.view import _parse_salary, _salary_floor_from_profile
        annual_floor, _ = _salary_floor_from_profile()
        sal_rows = conn.execute(
            "SELECT url, title, site, salary, fit_score FROM jobs "
            "WHERE salary IS NOT NULL AND salary != '' "
            "AND COALESCE(apply_attempts,0) < 99 AND applied_at IS NULL "
            "ORDER BY title"
        ).fetchall()
        below_rows = [
            r for r in sal_rows
            if _parse_salary(r[3])[1] is not None and _parse_salary(r[3])[1] < annual_floor
        ]
        if below_rows:
            _show_table(
                [(r[2], r[4], r[3], r[1]) for r in below_rows],
                ["Site", "Score", "Salary", "Title"],
                f"Jobs below salary floor (${annual_floor:,.0f}/yr)",
            )
            console.print(f"{len(below_rows)} job(s) will be permanently failed (not_eligible_salary).")
            if _confirm(f"Permanently fail {len(below_rows)} below-salary job(s)?"):
                for r in below_rows:
                    conn.execute(
                        "UPDATE jobs SET apply_status='failed', apply_error='not_eligible_salary', "
                        "apply_attempts=99 WHERE url=?",
                        (r[0],),
                    )
                conn.commit()
                console.print(f"[green]Marked {len(below_rows)} below-salary job(s) as permanently failed.[/green]")
            else:
                console.print("[yellow]Skipped --below-salary.[/yellow]")
        else:
            console.print(f"[green]--below-salary: no jobs found below ${annual_floor:,.0f}/yr (with parseable salary).[/green]")

    # ------------------------------------------------------------------
    # --invalid-url / --all
    # ------------------------------------------------------------------
    if invalid_url or all_issues:
        rows = conn.execute(
            "SELECT url, title, site FROM jobs "
            "WHERE (url IS NULL OR url = '' OR url = 'None' "
            "       OR (url NOT LIKE 'http%' AND (application_url IS NULL "
            "           OR application_url NOT LIKE 'http%'))) "
            "AND COALESCE(apply_status,'') NOT IN ('applied','failed') "
            "ORDER BY title",
        ).fetchall()
        if rows:
            _show_table([(r[2], r[1], r[0]) for r in rows], ["Site", "Title", "URL"], "Invalid-URL jobs")
            console.print(f"{len(rows)} job(s) to permanently fail.")
            if _confirm(f"Permanently fail {len(rows)} invalid-URL job(s)?"):
                for r in rows:
                    conn.execute(
                        "UPDATE jobs SET apply_status='failed', apply_error='invalid_url', "
                        "apply_attempts=99 WHERE url=?",
                        (r[0],),
                    )
                conn.commit()
                console.print(f"[green]Marked {len(rows)} invalid-URL job(s) as permanently failed.[/green]")
            else:
                console.print("[yellow]Skipped --invalid-url.[/yellow]")
        else:
            console.print("[green]--invalid-url: none found.[/green]")

    # ------------------------------------------------------------------
    # --no-description / --all
    # ------------------------------------------------------------------
    if no_description or all_issues:
        rows = conn.execute(
            "SELECT url, title, site, discovered_at FROM jobs "
            "WHERE full_description IS NULL "
            "ORDER BY discovered_at",
        ).fetchall()
        if rows:
            _show_table(
                [(r[2], r[3][:10] if r[3] else "?", r[1]) for r in rows],
                ["Site", "Discovered", "Title"],
                "Jobs with no description",
            )
            console.print(f"{len(rows)} job(s) to delete.")
            if _confirm(f"Delete {len(rows)} never-enriched job(s)?"):
                urls = [r[0] for r in rows]
                conn.execute(f"DELETE FROM jobs WHERE url IN ({','.join('?'*len(urls))})", urls)
                conn.commit()
                console.print(f"[green]Deleted {len(rows)} unenriched job(s).[/green]")
            else:
                console.print("[yellow]Skipped --no-description.[/yellow]")
        else:
            console.print("[green]--no-description: none found.[/green]")

    # ------------------------------------------------------------------
    # --reset-failed
    # ------------------------------------------------------------------
    if reset_failed:
        rows = conn.execute(
            "SELECT url, title, site, apply_error FROM jobs "
            "WHERE apply_attempts = 99 AND applied_at IS NULL "
            "ORDER BY title",
        ).fetchall()
        if rows:
            _show_table(
                [(r[2], r[3], r[1]) for r in rows],
                ["Site", "Failure Reason", "Title"],
                "Permanently failed apply jobs",
            )
            console.print(f"{len(rows)} job(s) to reopen.")
            if _confirm(f"Reset {len(rows)} failed job(s) for retry?"):
                conn.execute(
                    "UPDATE jobs SET apply_status=NULL, apply_error=NULL, apply_attempts=0 "
                    "WHERE apply_attempts=99 AND applied_at IS NULL",
                )
                conn.commit()
                console.print(f"[green]Reset {len(rows)} job(s). They will re-enter the apply queue.[/green]")
            else:
                console.print("[yellow]Skipped --reset-failed.[/yellow]")
        else:
            console.print("[green]--reset-failed: no permanently failed jobs found.[/green]")

    # ------------------------------------------------------------------
    # --reset-stuck
    # ------------------------------------------------------------------
    if reset_stuck:
        rows = conn.execute(
            "SELECT url, title, site, last_attempted_at FROM jobs "
            "WHERE apply_status = 'in_progress' ORDER BY last_attempted_at",
        ).fetchall()
        if rows:
            _show_table(
                [(r[2], r[3][:16] if r[3] else "?", r[1]) for r in rows],
                ["Site", "Last Attempt", "Title"],
                "Stuck in-progress jobs",
            )
            console.print(f"{len(rows)} job(s) to unstick.")
            if _confirm(f"Clear in_progress status for {len(rows)} job(s)?"):
                conn.execute(
                    "UPDATE jobs SET apply_status=NULL, agent_id=NULL "
                    "WHERE apply_status='in_progress'",
                )
                conn.commit()
                console.print(f"[green]Unstuck {len(rows)} job(s). They will re-enter the apply queue.[/green]")
            else:
                console.print("[yellow]Skipped --reset-stuck.[/yellow]")
        else:
            console.print("[green]--reset-stuck: no stuck jobs found.[/green]")

    # ------------------------------------------------------------------
    # --reset-scores
    # ------------------------------------------------------------------
    if reset_scores:
        count = conn.execute("SELECT COUNT(*) FROM jobs WHERE fit_score IS NOT NULL").fetchone()[0]
        if count:
            console.print(f"\n[yellow]--reset-scores: {count} scored job(s) will have their scores cleared.[/yellow]")
            if _confirm(f"Clear scores for all {count} job(s)?"):
                conn.execute(
                    "UPDATE jobs SET fit_score=NULL, score_reasoning=NULL, scored_at=NULL, "
                    "previous_score=NULL, location_eligible=NULL WHERE fit_score IS NOT NULL",
                )
                conn.commit()
                console.print(f"[green]Cleared scores for {count} job(s). Run 'applypilot run score' to re-score.[/green]")
            else:
                console.print("[yellow]Skipped --reset-scores.[/yellow]")
        else:
            console.print("[green]--reset-scores: no scored jobs found.[/green]")


@app.command()
def dashboard() -> None:
    """Start the interactive dashboard in your browser.

    Generates the dashboard HTML, starts a local HTTP server on a random
    port, and opens the browser. The dashboard stays live until you press
    Ctrl+C.

    While running, each job card has a Reject button that permanently
    removes the job from your pipeline (marks it failed) without leaving
    the browser.
    """
    _bootstrap()

    from applypilot.view import open_dashboard

    open_dashboard()


@app.command()
def doctor() -> None:
    """Check your setup and diagnose missing requirements."""
    import shutil
    from applypilot.config import (
        load_env, PROFILE_PATH, RESUME_PATH, RESUME_PDF_PATH,
        SEARCH_CONFIG_PATH, ENV_PATH, get_chrome_path,
    )

    load_env()

    ok_mark = "[green]OK[/green]"
    fail_mark = "[red]MISSING[/red]"
    warn_mark = "[yellow]WARN[/yellow]"

    results: list[tuple[str, str, str]] = []  # (check, status, note)

    # --- Tier 1 checks ---
    # Profile
    if PROFILE_PATH.exists():
        results.append(("profile.json", ok_mark, str(PROFILE_PATH)))
    else:
        results.append(("profile.json", fail_mark, "Run 'applypilot init' to create"))

    # Resume
    if RESUME_PATH.exists():
        results.append(("resume.txt", ok_mark, str(RESUME_PATH)))
    elif RESUME_PDF_PATH.exists():
        results.append(("resume.txt", warn_mark, "Only PDF found — plain-text needed for AI stages"))
    else:
        results.append(("resume.txt", fail_mark, "Run 'applypilot init' to add your resume"))

    # Search config
    if SEARCH_CONFIG_PATH.exists():
        results.append(("searches.yaml", ok_mark, str(SEARCH_CONFIG_PATH)))
    else:
        results.append(("searches.yaml", warn_mark, "Will use example config — run 'applypilot init'"))

    # jobspy (discovery dep installed separately)
    try:
        import jobspy  # noqa: F401
        results.append(("python-jobspy", ok_mark, "Job board scraping available"))
    except ImportError:
        results.append(("python-jobspy", warn_mark,
                        "pip install --no-deps python-jobspy && pip install pydantic tls-client requests markdownify regex"))

    # --- Tier 2 checks ---
    import os
    has_gemini = bool(os.environ.get("GEMINI_API_KEY"))
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))
    has_local = bool(os.environ.get("LLM_URL"))
    if has_gemini:
        model = os.environ.get("LLM_MODEL", "gemini-2.0-flash")
        results.append(("LLM API key", ok_mark, f"Gemini ({model})"))
    elif has_openai:
        model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
        results.append(("LLM API key", ok_mark, f"OpenAI ({model})"))
    elif has_local:
        local_models = os.environ.get("LLM_LOCAL_MODELS", os.environ.get("LLM_MODEL", "local-model"))
        results.append(("LLM (local)", ok_mark, f"URL: {os.environ.get('LLM_URL')}  models: {local_models}"))
    else:
        results.append(("LLM API key", fail_mark,
                        "Set GEMINI_API_KEY in ~/.applypilot/.env (run 'applypilot init')"))

    # --- Tier 3 checks ---
    # Claude Code CLI
    claude_bin = shutil.which("claude")
    if claude_bin:
        results.append(("Claude Code CLI", ok_mark, claude_bin))
    else:
        results.append(("Claude Code CLI", fail_mark,
                        "Install from https://claude.ai/code (needed for auto-apply)"))

    # Chrome
    try:
        chrome_path = get_chrome_path()
        results.append(("Chrome/Chromium", ok_mark, chrome_path))
    except FileNotFoundError:
        results.append(("Chrome/Chromium", fail_mark,
                        "Install Chrome or set CHROME_PATH env var (needed for auto-apply)"))

    # Node.js / npx (for Playwright MCP)
    npx_bin = shutil.which("npx")
    if npx_bin:
        results.append(("Node.js (npx)", ok_mark, npx_bin))
    else:
        results.append(("Node.js (npx)", fail_mark,
                        "Install Node.js 18+ from nodejs.org (needed for auto-apply)"))

    # CapSolver (optional)
    capsolver = os.environ.get("CAPSOLVER_API_KEY")
    if capsolver:
        results.append(("CapSolver API key", ok_mark, "CAPTCHA solving enabled"))
    else:
        results.append(("CapSolver API key", "[dim]optional[/dim]",
                        "Set CAPSOLVER_API_KEY in .env for CAPTCHA solving"))

    # --- Render results ---
    console.print()
    console.print("[bold]ApplyPilot Doctor[/bold]\n")

    col_w = max(len(r[0]) for r in results) + 2
    for check, status, note in results:
        pad = " " * (col_w - len(check))
        console.print(f"  {check}{pad}{status}  [dim]{note}[/dim]")

    console.print()

    # Tier summary
    from applypilot.config import get_tier, TIER_LABELS
    tier = get_tier()
    console.print(f"[bold]Current tier: Tier {tier} — {TIER_LABELS[tier]}[/bold]")

    if tier == 1:
        console.print("[dim]  → Tier 2 unlocks: scoring, tailoring, cover letters (needs LLM API key)[/dim]")
        console.print("[dim]  → Tier 3 unlocks: auto-apply (needs Claude Code CLI + Chrome + Node.js)[/dim]")
    elif tier == 2:
        console.print("[dim]  → Tier 3 unlocks: auto-apply (needs Claude Code CLI + Chrome + Node.js)[/dim]")

    console.print()


@app.command()
def feedback() -> None:
    """Analyze rejection history and update scoring_feedback.yaml.

    Queries rejected jobs that have a reason set, groups and counts by reason,
    and suggests updates to scoring_feedback.yaml. Asks for confirmation before
    writing.
    """
    _bootstrap()

    import yaml
    from applypilot.config import SCORING_FEEDBACK_PATH
    from applypilot.database import get_connection

    conn = get_connection()

    # ── Query rejection counts by reason ──
    rows = conn.execute("""
        SELECT reject_reason, COUNT(*) AS cnt
        FROM jobs
        WHERE reject_reason IS NOT NULL AND reject_reason != ''
        GROUP BY reject_reason
        ORDER BY cnt DESC
    """).fetchall()

    if not rows:
        console.print("[dim]No rejections with a reason found. Reject jobs from the dashboard first.[/dim]")
        return

    reason_counts: dict[str, int] = {r[0]: r[1] for r in rows}
    total_rejections = sum(reason_counts.values())

    console.print(f"\n[bold]Rejection history[/bold] ({total_rejections} total with reason)\n")
    for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
        bar = "=" * count
        console.print(f"  {reason:<25} {count:>3}  {bar}")
    console.print()

    # ── Load existing feedback file ──
    existing: dict = {}
    if SCORING_FEEDBACK_PATH.exists():
        try:
            existing = yaml.safe_load(SCORING_FEEDBACK_PATH.read_text(encoding="utf-8")) or {}
        except Exception:
            existing = {}

    existing_avoid  = list(existing.get("avoid") or [])
    existing_prefer = list(existing.get("prefer") or [])
    existing_note   = str(existing.get("calibration_note") or "").strip()

    # ── Surface flagged "other" notes for manual review ──
    flagged_rows = conn.execute("""
        SELECT title, reject_note FROM jobs
        WHERE reject_reason = 'other'
        AND reject_note LIKE '[scoring]%'
        ORDER BY discovered_at DESC
    """).fetchall()
    if flagged_rows:
        console.print("[bold yellow]Flagged 'other' rejections[/bold yellow] "
                      "(review manually — add to scoring_feedback.yaml if relevant):\n")
        for row in flagged_rows:
            note_text = (row[1] or "").removeprefix("[scoring]").strip()
            title = (row[0] or "?")[:60]
            console.print(f"  [dim]{title}[/dim]")
            if note_text:
                console.print(f"    [italic]{note_text}[/italic]")
        console.print()

    # ── Generate suggestions based on top-pattern heuristics ──
    THRESHOLD = 3

    new_avoid: list[str] = []
    for reason, count in reason_counts.items():
        if count >= THRESHOLD and reason in REASON_AVOID_MESSAGES:
            msg = REASON_AVOID_MESSAGES[reason]
            if msg not in existing_avoid:
                new_avoid.append(msg)

    if not new_avoid:
        console.print("[dim]No new suggestions — no reason type has reached the threshold of "
                      f"{THRESHOLD}+ rejections, or all suggestions are already present.[/dim]\n")

        # Still show current file state
        if SCORING_FEEDBACK_PATH.exists():
            console.print(f"[dim]Current file: {SCORING_FEEDBACK_PATH}[/dim]")
            console.print(f"[dim]  avoid:  {len(existing_avoid)} entr{'y' if len(existing_avoid)==1 else 'ies'}[/dim]")
            console.print(f"[dim]  prefer: {len(existing_prefer)} entr{'y' if len(existing_prefer)==1 else 'ies'}[/dim]")
        return

    # ── Summarise what will be written ──
    console.print(f"[bold yellow]Suggested additions[/bold yellow] (reasons with {THRESHOLD}+ rejections):\n")
    for msg in new_avoid:
        console.print(f"  [red]+[/red] avoid: {msg}")
    console.print()

    # ── Ask for confirmation ──
    confirmed = typer.confirm("Write these suggestions to scoring_feedback.yaml?", default=True)
    if not confirmed:
        console.print("[dim]Aborted — no changes written.[/dim]")
        return

    # ── Merge and write ──
    merged_avoid  = existing_avoid + new_avoid
    merged_prefer = existing_prefer  # prefer untouched (no heuristics for it yet)

    calibration_note = existing_note or (
        "Based on rejection history. Use as directional guidance, not hard rules."
    )

    output = (
        "# Scoring feedback — read by the scorer and injected into the LLM scoring prompt.\n"
        "# Edit freely. Entries here are used as directional guidance, not hard rules.\n"
        "# Run 'applypilot feedback' to update from your rejection history.\n\n"
    )
    output += yaml.dump(
        {
            "avoid":            merged_avoid,
            "prefer":           merged_prefer,
            "calibration_note": calibration_note,
        },
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )

    SCORING_FEEDBACK_PATH.write_text(output, encoding="utf-8")
    console.print(f"[green]Written:[/green] {SCORING_FEEDBACK_PATH}")
    console.print(
        f"[dim]  avoid:  {len(merged_avoid)} entr{'y' if len(merged_avoid)==1 else 'ies'}[/dim]"
    )
    console.print(
        f"[dim]  prefer: {len(merged_prefer)} entr{'y' if len(merged_prefer)==1 else 'ies'}[/dim]"
    )
    console.print(
        "\n[dim]Run [bold]applypilot run score --rescore[/bold] to apply updated feedback to existing jobs.[/dim]\n"
    )


if __name__ == "__main__":
    app()
