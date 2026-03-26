"""ApplyPilot first-time setup wizard.

Interactive flow that creates ~/.applypilot/ with:
  - resume.txt (and optionally resume.pdf)
  - profile.json
  - searches.yaml
  - .env (LLM API key)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from applypilot.config import (
    APP_DIR,
    ENV_PATH,
    PROFILE_PATH,
    RESUME_PATH,
    RESUME_PDF_PATH,
    SEARCH_CONFIG_PATH,
    ensure_dirs,
)

console = Console()


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------

def _setup_resume() -> None:
    """Prompt for resume file and copy into APP_DIR."""
    console.print(Panel("[bold]Step 1: Resume[/bold]\nPoint to your master resume file (.txt or .pdf)."))

    while True:
        path_str = Prompt.ask("Resume file path")
        src = Path(path_str.strip().strip('"').strip("'")).expanduser().resolve()

        if not src.exists():
            console.print(f"[red]File not found:[/red] {src}")
            continue

        suffix = src.suffix.lower()
        if suffix not in (".txt", ".pdf"):
            console.print("[red]Unsupported format.[/red] Provide a .txt or .pdf file.")
            continue

        if suffix == ".txt":
            shutil.copy2(src, RESUME_PATH)
            console.print(f"[green]Copied to {RESUME_PATH}[/green]")
        elif suffix == ".pdf":
            shutil.copy2(src, RESUME_PDF_PATH)
            console.print(f"[green]Copied to {RESUME_PDF_PATH}[/green]")

            # Also ask for a plain-text version for LLM consumption
            txt_path_str = Prompt.ask(
                "Plain-text version of your resume (.txt)",
                default="",
            )
            if txt_path_str.strip():
                txt_src = Path(txt_path_str.strip().strip('"').strip("'")).expanduser().resolve()
                if txt_src.exists():
                    shutil.copy2(txt_src, RESUME_PATH)
                    console.print(f"[green]Copied to {RESUME_PATH}[/green]")
                else:
                    console.print("[yellow]File not found, skipping plain-text copy.[/yellow]")
        break


# ---------------------------------------------------------------------------
# Profile YAML writer
# ---------------------------------------------------------------------------

def _yaml_str(value: Any) -> str:
    """Render a scalar value as a safe YAML string.

    Quotes strings that contain special YAML characters, look like booleans,
    or are purely numeric (to preserve the string type when round-tripped).
    """
    if value is None or value == "":
        return '""'
    s = str(value)
    # Detect numeric strings — quote so YAML parses them back as strings
    is_numeric = False
    try:
        float(s)
        is_numeric = True
    except ValueError:
        pass
    needs_quote = (
        is_numeric
        or any(c in s for c in (':', '#', '{', '}', '[', ']', ',', '&', '*', '?', '|', '>', '!', "'", '"', '%', '@', '`'))
        or s.lower() in ("true", "false", "yes", "no", "null")
    )
    if needs_quote:
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _yaml_list(items: list, indent: int = 4) -> str:
    """Render a list as YAML block sequence lines (no trailing newline).

    Default indent of 4 matches keys nested at 2-space section indent.
    """
    pad = " " * indent
    if not items:
        return "[]"
    return "\n" + "".join(f"{pad}- {_yaml_str(item)}\n" for item in items).rstrip("\n")


def _build_profile_yaml(p: dict) -> str:
    """Build a fully-commented profile.yaml string from a profile dict."""
    personal = p.get("personal", {})
    wa = p.get("work_authorization", {})
    comp = p.get("compensation", {})
    exp = p.get("experience", {})
    skills = p.get("skills_boundary", {})
    facts = p.get("resume_facts", {})
    cf = p.get("career_focus")
    eeo = p.get("eeo_voluntary", {})
    avail = p.get("availability", {})

    lines: list[str] = [
        "# ApplyPilot Profile",
        "# Edit this file directly — comments explain each field.",
        "# Re-run 'applypilot init' to regenerate it.",
        "",
        "# ── Personal ─────────────────────────────────────────────────────────────────",
        "# Used for application form auto-fill and cover letter salutations.",
        "personal:",
        f"  full_name: {_yaml_str(personal.get('full_name', ''))}",
        f"  preferred_name: {_yaml_str(personal.get('preferred_name', ''))}   # First name or nickname used in cover letters",
        f"  email: {_yaml_str(personal.get('email', ''))}",
        f"  phone: {_yaml_str(personal.get('phone', ''))}",
        f"  city: {_yaml_str(personal.get('city', ''))}",
        f"  province_state: {_yaml_str(personal.get('province_state', ''))}",
        f"  country: {_yaml_str(personal.get('country', ''))}",
        f"  postal_code: {_yaml_str(personal.get('postal_code', ''))}",
        f"  address: {_yaml_str(personal.get('address', ''))}   # Street address (optional, for form auto-fill)",
        f"  linkedin_url: {_yaml_str(personal.get('linkedin_url', ''))}",
        f"  github_url: {_yaml_str(personal.get('github_url', ''))}",
        f"  portfolio_url: {_yaml_str(personal.get('portfolio_url', ''))}",
        f"  website_url: {_yaml_str(personal.get('website_url', ''))}",
        f"  password: {_yaml_str(personal.get('password', ''))}   # Job-site password used during auto-apply login",
        "",
        "# ── Work Authorization ────────────────────────────────────────────────────────",
        "work_authorization:",
        f"  legally_authorized_to_work: {str(wa.get('legally_authorized_to_work', True)).lower()}",
        f"  require_sponsorship: {str(wa.get('require_sponsorship', False)).lower()}",
        f"  work_permit_type: {_yaml_str(wa.get('work_permit_type', ''))}   # e.g. Citizen, PR, Open Work Permit, H-1B, TN",
        "",
        "# ── Compensation ─────────────────────────────────────────────────────────────",
        "# Used to filter jobs and answer salary questions during auto-apply.",
        "compensation:",
        f"  salary_expectation: {_yaml_str(comp.get('salary_expectation', ''))}",
        f"  salary_currency: {_yaml_str(comp.get('salary_currency', 'USD'))}",
        f"  salary_range_min: {_yaml_str(comp.get('salary_range_min', ''))}",
        f"  salary_range_max: {_yaml_str(comp.get('salary_range_max', ''))}",
        "",
        "# ── Experience ───────────────────────────────────────────────────────────────",
        "experience:",
        f"  years_of_experience_total: {_yaml_str(exp.get('years_of_experience_total', ''))}",
        f"  education_level: {_yaml_str(exp.get('education_level', ''))}   # e.g. High School, Associates, Bachelor's, Master's, PhD",
        f"  current_title: {_yaml_str(exp.get('current_title', ''))}",
        f"  target_role: {_yaml_str(exp.get('target_role', ''))}",
        "",
        "# ── Skills Boundary ──────────────────────────────────────────────────────────",
        "# The outer boundary of skills you genuinely possess.",
        "# The AI uses this list to keep tailored resumes honest — it will only use",
        "# skills listed here. Add everything you know, even if rusty or historical.",
        "skills_boundary:",
        f"  programming_languages: {_yaml_list(skills.get('programming_languages', []))}",
        f"  frameworks: {_yaml_list(skills.get('frameworks', []))}",
        f"  tools: {_yaml_list(skills.get('tools', []))}",
        "",
        "# ── Resume Facts ─────────────────────────────────────────────────────────────",
        "# Hard facts the AI must never change, invent, or omit during tailoring.",
        "resume_facts:",
        f"  preserved_companies: {_yaml_list(facts.get('preserved_companies', []))}   # Exact company names as they appear on your resume",
        f"  preserved_projects: {_yaml_list(facts.get('preserved_projects', []))}    # Project names to always keep",
        f"  preserved_school: {_yaml_str(facts.get('preserved_school', ''))}",
        f"  real_metrics: {_yaml_list(facts.get('real_metrics', []))}   # Verified numbers only (e.g. \"99.9% uptime\", \"50k users\")",
    ]

    # Career focus — only if present
    if cf:
        lines += [
            "",
            "# ── Career Focus ─────────────────────────────────────────────────────────────",
            "# Fill this in if your career has shifted direction in recent years.",
            "# Helps the AI scorer weight your roles correctly.",
            "#",
            "# PRIMARY skills   — what you actively do day-to-day right now.",
            "#                   These drive your score up for matching roles.",
            "# SECONDARY skills — real parts of your background, but no longer your",
            "#                   daily focus. Historical experience you have moved on from.",
            "#                   These may add context but should not drive a score up.",
            "#",
            "# Scoring effect: if a job's core duties centre on your secondary skills,",
            "# the AI subtracts 2-3 points from its initial score to preserve relative",
            "# signal (a 9 becomes ~6-7, not a hard cap). Roles matching your primary",
            "# skills or target roles score normally.",
            "#",
            "# Remove this entire block if your current work already matches your targets.",
            "career_focus:",
            f"  target_roles: {_yaml_list(cf.get('target_roles', []))}",
            f"  primary_skills: {_yaml_list(cf.get('primary_skills', []))}",
            f"  secondary_skills: {_yaml_list(cf.get('secondary_skills', []))}",
            f"  career_note: {_yaml_str(cf.get('career_note', ''))}",
        ]
    else:
        lines += [
            "",
            "# ── Career Focus (optional) ──────────────────────────────────────────────────",
            "# Uncomment and fill in if your career has shifted direction in recent years.",
            "# See 'applypilot init' or the README for full explanation.",
            "#",
            "# career_focus:",
            "#   target_roles:",
            "#     - Engineering Manager",
            "#     - Director of Product",
            "#   primary_skills:",
            "#     - team leadership",
            "#     - roadmap planning",
            "#   secondary_skills:",
            "#     - Python",
            "#     - hands-on coding",
            "#   career_note: \"Moved from IC engineering to leadership around 2020\"",
        ]

    lines += [
        "",
        "# ── EEO Voluntary ────────────────────────────────────────────────────────────",
        "# Optional self-identification for Equal Employment Opportunity forms.",
        "# Always voluntary — change any value or leave as \"Decline to self-identify\".",
        "eeo_voluntary:",
        f"  gender: {_yaml_str(eeo.get('gender', 'Decline to self-identify'))}",
        f"  race_ethnicity: {_yaml_str(eeo.get('race_ethnicity', 'Decline to self-identify'))}",
        f"  veteran_status: {_yaml_str(eeo.get('veteran_status', 'Decline to self-identify'))}",
        f"  disability_status: {_yaml_str(eeo.get('disability_status', 'Decline to self-identify'))}",
        "",
        "# ── Availability ─────────────────────────────────────────────────────────────",
        "availability:",
        f"  earliest_start_date: {_yaml_str(avail.get('earliest_start_date', 'Immediately'))}   # e.g. Immediately, 2 weeks notice, 2025-06-01",
        "",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def _setup_profile() -> dict:
    """Walk through profile questions and return a nested profile dict."""
    console.print(Panel("[bold]Step 2: Profile[/bold]\nTell ApplyPilot about yourself. This powers scoring, tailoring, and auto-fill."))

    profile: dict = {}

    # -- Personal --
    console.print("\n[bold cyan]Personal Information[/bold cyan]")
    full_name = Prompt.ask("Full name")
    profile["personal"] = {
        "full_name": full_name,
        "preferred_name": Prompt.ask("Preferred/nickname (leave blank to use first name)", default=""),
        "email": Prompt.ask("Email address"),
        "phone": Prompt.ask("Phone number", default=""),
        "city": Prompt.ask("City"),
        "province_state": Prompt.ask("Province/State (e.g. Ontario, California)", default=""),
        "country": Prompt.ask("Country"),
        "postal_code": Prompt.ask("Postal/ZIP code", default=""),
        "address": Prompt.ask("Street address (optional, used for form auto-fill)", default=""),
        "linkedin_url": Prompt.ask("LinkedIn URL", default=""),
        "github_url": Prompt.ask("GitHub URL (optional)", default=""),
        "portfolio_url": Prompt.ask("Portfolio URL (optional)", default=""),
        "website_url": Prompt.ask("Personal website URL (optional)", default=""),
        "password": Prompt.ask("Job site password (used for login walls during auto-apply)", password=True, default=""),
    }

    # -- Work Authorization --
    console.print("\n[bold cyan]Work Authorization[/bold cyan]")
    profile["work_authorization"] = {
        "legally_authorized_to_work": Confirm.ask("Are you legally authorized to work in your target country?"),
        "require_sponsorship": Confirm.ask("Will you now or in the future need sponsorship?"),
        "work_permit_type": Prompt.ask("Work permit type (e.g. Citizen, PR, Open Work Permit — leave blank if N/A)", default=""),
    }

    # -- Compensation --
    console.print("\n[bold cyan]Compensation[/bold cyan]")
    salary = Prompt.ask("Expected annual salary (number)", default="")
    salary_currency = Prompt.ask("Currency", default="USD")
    salary_range = Prompt.ask("Acceptable range (e.g. 80000-120000)", default="")
    range_parts = salary_range.split("-") if "-" in salary_range else [salary, salary]
    profile["compensation"] = {
        "salary_expectation": salary,
        "salary_currency": salary_currency,
        "salary_range_min": range_parts[0].strip(),
        "salary_range_max": range_parts[1].strip() if len(range_parts) > 1 else range_parts[0].strip(),
    }

    # -- Experience --
    console.print("\n[bold cyan]Experience[/bold cyan]")
    current_title = Prompt.ask("Current/most recent job title", default="")
    target_role = Prompt.ask("Target role (what you're applying for, e.g. 'Senior Backend Engineer')", default=current_title)
    profile["experience"] = {
        "years_of_experience_total": Prompt.ask("Years of professional experience", default=""),
        "education_level": Prompt.ask("Highest education (e.g. Bachelor's, Master's, PhD, Self-taught)", default=""),
        "current_title": current_title,
        "target_role": target_role,
    }

    # -- Skills Boundary --
    console.print("\n[bold cyan]Skills[/bold cyan] (comma-separated)")
    langs = Prompt.ask("Programming languages", default="")
    frameworks = Prompt.ask("Frameworks & libraries", default="")
    tools = Prompt.ask("Tools & platforms (e.g. Docker, AWS, Git)", default="")
    profile["skills_boundary"] = {
        "programming_languages": [s.strip() for s in langs.split(",") if s.strip()],
        "frameworks": [s.strip() for s in frameworks.split(",") if s.strip()],
        "tools": [s.strip() for s in tools.split(",") if s.strip()],
    }

    # -- Resume Facts (preserved truths for tailoring) --
    console.print("\n[bold cyan]Resume Facts[/bold cyan]")
    console.print("[dim]These are preserved exactly during resume tailoring — the AI will never change them.[/dim]")
    companies = Prompt.ask("Companies to always keep (comma-separated)", default="")
    projects = Prompt.ask("Projects to always keep (comma-separated)", default="")
    school = Prompt.ask("School name(s) to preserve", default="")
    metrics = Prompt.ask("Real metrics to preserve (e.g. '99.9% uptime, 50k users')", default="")
    profile["resume_facts"] = {
        "preserved_companies": [s.strip() for s in companies.split(",") if s.strip()],
        "preserved_projects": [s.strip() for s in projects.split(",") if s.strip()],
        "preserved_school": school.strip(),
        "real_metrics": [s.strip() for s in metrics.split(",") if s.strip()],
    }

    # -- Career Focus (optional) --
    # Helps the scorer down-weight roles whose primary duties are centred on
    # skills the candidate has not been using recently. Entirely optional —
    # candidates whose current work matches their target roles can skip this.
    console.print("\n[bold cyan]Career Focus (optional)[/bold cyan]")
    console.print(
        "[dim]If your career has shifted direction (e.g. from IC engineering to management,\n"
        "from one domain to another), this lets the AI scorer account for skill recency.\n"
        "Roles that primarily require your historical skills will be scored lower.[/dim]"
    )
    if Confirm.ask("Has your career shifted focus in recent years?", default=False):
        target_roles_raw = Prompt.ask(
            "Target role types you want to land (comma-separated)\n"
            "  e.g. Engineering Manager, Director of Product, Release Manager",
            default="",
        )
        primary_skills_raw = Prompt.ask(
            "Your CURRENT primary skills — actively used in your recent work (comma-separated)\n"
            "  e.g. team leadership, roadmap planning, stakeholder communication",
            default="",
        )
        secondary_skills_raw = Prompt.ask(
            "Historical/background skills — real but no longer your day-to-day focus (comma-separated)\n"
            "  e.g. Python, AWS, software architecture, hands-on coding",
            default="",
        )
        career_note = Prompt.ask(
            "Brief career trajectory note (free text, used as context for AI scoring)\n"
            "  e.g. 'Moved from IC software engineering to engineering leadership around 2020'",
            default="",
        )
        profile["career_focus"] = {
            "target_roles": [s.strip() for s in target_roles_raw.split(",") if s.strip()],
            "primary_skills": [s.strip() for s in primary_skills_raw.split(",") if s.strip()],
            "secondary_skills": [s.strip() for s in secondary_skills_raw.split(",") if s.strip()],
            "career_note": career_note.strip(),
        }
        console.print("[green]Career focus saved — the scorer will apply recency weighting.[/green]")
    else:
        console.print("[dim]Skipped. You can add a career_focus block to profile.yaml manually later (see the commented example in the file).[/dim]")

    # -- EEO Voluntary (defaults) --
    profile["eeo_voluntary"] = {
        "gender": "Decline to self-identify",
        "race_ethnicity": "Decline to self-identify",
        "veteran_status": "Decline to self-identify",
        "disability_status": "Decline to self-identify",
    }

    # -- Availability --
    profile["availability"] = {
        "earliest_start_date": Prompt.ask("Earliest start date", default="Immediately"),
    }

    # Save as YAML with inline comments
    PROFILE_PATH.write_text(_build_profile_yaml(profile), encoding="utf-8")
    console.print(f"\n[green]Profile saved to {PROFILE_PATH}[/green]")
    return profile


# ---------------------------------------------------------------------------
# Search config
# ---------------------------------------------------------------------------

def _setup_searches() -> None:
    """Generate a searches.yaml from user input."""
    console.print(Panel(
        "[bold]Step 3: Job Search Config[/bold]\n"
        "Configure where to search. You can combine:\n"
        "  • US-wide remote search (fully-remote postings nationwide)\n"
        "  • One or more local cities (hybrid/onsite roles near you)"
    ))

    # Remote search toggle
    include_remote = Confirm.ask("Include US-wide remote job search?", default=True)

    # Local cities — allow multiple
    local_cities: list[str] = []
    console.print("[dim]Add local cities for hybrid/onsite searches. Leave blank to finish.[/dim]")
    while True:
        city = Prompt.ask(
            f"Local city #{len(local_cities) + 1} (e.g. 'Orlando, FL') — blank to skip/finish",
            default="",
        )
        if not city.strip():
            break
        local_cities.append(city.strip())

    if not include_remote and not local_cities:
        console.print("[yellow]No locations configured — defaulting to US-wide remote.[/yellow]")
        include_remote = True

    distance_str = Prompt.ask("Local search radius in miles", default="25")
    try:
        distance = int(distance_str)
    except ValueError:
        distance = 25

    roles_raw = Prompt.ask(
        "Target job titles (comma-separated, e.g. 'Project Manager, Product Manager')"
    )
    roles = [r.strip() for r in roles_raw.split(",") if r.strip()]

    if not roles:
        console.print("[yellow]No roles provided. Using a default set.[/yellow]")
        roles = ["Software Engineer"]

    # Build location entries
    location_entries: list[str] = []
    if include_remote:
        location_entries.append('  - location: "United States"\n    remote: true')
    for city in local_cities:
        location_entries.append(f'  - location: "{city}"\n    remote: false')

    # Build YAML content
    lines = [
        "# ApplyPilot search configuration",
        "# Edit this file to refine your job search queries.",
        "",
        "defaults:",
        '  location: "United States"',
        f"  distance: {distance}",
        "  hours_old: 72",
        "  results_per_site: 50",
        "",
        "# location_accept: substrings that make a non-remote location eligible.",
        "# Leave empty if you only want remote jobs (most common).",
        "# Example: ['Orlando', 'Florida'] would allow hybrid Orlando roles.",
        "location_accept: []",
        "",
        "# location_reject_non_remote: substrings that permanently reject a location.",
        "# Checked BEFORE the remote short-circuit, so 'Remote India' is caught.",
        "# Add any countries/regions you are NOT eligible to work in.",
        "location_reject_non_remote:",
        '  - "india"',
        '  - "canada"',
        '  - "germany"',
        '  - "france"',
        '  - "united kingdom"',
        '  - "australia"',
        '  - "brazil"',
        '  - "mexico"',
        '  - "japan"',
        '  - "emea"',
        '  - "apac"',
        '  - "latam"',
        '  - "teletravail"',
        "",
        "locations:",
    ]
    lines.extend(location_entries)
    lines += ["", "queries:"]
    for i, role in enumerate(roles):
        lines.append(f'  - query: "{role}"')
        lines.append(f"    tier: {min(i + 1, 3)}")

    SEARCH_CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"[green]Search config saved to {SEARCH_CONFIG_PATH}[/green]")
    console.print(
        "[dim]Tip: edit location_reject_non_remote in searches.yaml to add any other "
        "countries/regions you cannot work in.[/dim]"
    )


# ---------------------------------------------------------------------------
# AI Features
# ---------------------------------------------------------------------------

def _setup_ai_features() -> None:
    """Ask about AI scoring/tailoring — optional LLM configuration."""
    console.print(Panel(
        "[bold]Step 4: AI Features (optional)[/bold]\n"
        "An LLM powers job scoring, resume tailoring, and cover letters.\n"
        "Without this, you can still discover and enrich jobs."
    ))

    if not Confirm.ask("Enable AI scoring and resume tailoring?", default=True):
        console.print("[dim]Discovery-only mode. You can configure AI later with [bold]applypilot init[/bold].[/dim]")
        return

    console.print("Supported providers: [bold]Gemini[/bold] (recommended, free tier), OpenAI, local (Ollama/llama.cpp)")
    provider = Prompt.ask(
        "Provider",
        choices=["gemini", "openai", "local"],
        default="gemini",
    )

    env_lines = ["# ApplyPilot configuration", ""]

    if provider == "gemini":
        api_key = Prompt.ask("Gemini API key (from aistudio.google.com)")
        model = Prompt.ask("Model", default="gemini-2.0-flash")
        env_lines.append(f"GEMINI_API_KEY={api_key}")
        env_lines.append(f"LLM_MODEL={model}")
    elif provider == "openai":
        api_key = Prompt.ask("OpenAI API key")
        model = Prompt.ask("Model", default="gpt-4o-mini")
        env_lines.append(f"OPENAI_API_KEY={api_key}")
        env_lines.append(f"LLM_MODEL={model}")
    elif provider == "local":
        url = Prompt.ask("Local LLM endpoint URL", default="http://localhost:8080/v1")
        model = Prompt.ask("Model name", default="local-model")
        env_lines.append(f"LLM_URL={url}")
        env_lines.append(f"LLM_MODEL={model}")

    env_lines.append("")
    ENV_PATH.write_text("\n".join(env_lines), encoding="utf-8")
    console.print(f"[green]AI configuration saved to {ENV_PATH}[/green]")


# ---------------------------------------------------------------------------
# Auto-Apply
# ---------------------------------------------------------------------------

def _setup_auto_apply() -> None:
    """Configure autonomous job application (requires Claude Code CLI)."""
    console.print(Panel(
        "[bold]Step 5: Auto-Apply (optional)[/bold]\n"
        "ApplyPilot can autonomously fill and submit job applications\n"
        "using Claude Code as the browser agent."
    ))

    if not Confirm.ask("Enable autonomous job applications?", default=True):
        console.print("[dim]You can apply manually using the tailored resumes ApplyPilot generates.[/dim]")
        return

    # Check for Claude Code CLI
    if shutil.which("claude"):
        console.print("[green]Claude Code CLI detected.[/green]")
    else:
        console.print(
            "[yellow]Claude Code CLI not found on PATH.[/yellow]\n"
            "Install it from: [bold]https://claude.ai/code[/bold]\n"
            "Auto-apply won't work until Claude Code is installed."
        )

    # Optional: CapSolver for CAPTCHAs
    console.print("\n[dim]Some job sites use CAPTCHAs. CapSolver can handle them automatically.[/dim]")
    if Confirm.ask("Configure CapSolver API key? (optional)", default=False):
        capsolver_key = Prompt.ask("CapSolver API key")
        # Append to existing .env or create
        if ENV_PATH.exists():
            existing = ENV_PATH.read_text(encoding="utf-8")
            if "CAPSOLVER_API_KEY" not in existing:
                ENV_PATH.write_text(
                    existing.rstrip() + f"\nCAPSOLVER_API_KEY={capsolver_key}\n",
                    encoding="utf-8",
                )
        else:
            ENV_PATH.write_text(f"# ApplyPilot configuration\nCAPSOLVER_API_KEY={capsolver_key}\n", encoding="utf-8")
        console.print("[green]CapSolver key saved.[/green]")
    else:
        console.print("[dim]Skipped. Add CAPSOLVER_API_KEY to .env later if needed.[/dim]")


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def run_wizard() -> None:
    """Run the full interactive setup wizard."""
    console.print()
    console.print(
        Panel.fit(
            "[bold green]ApplyPilot Setup Wizard[/bold green]\n\n"
            "This will create your configuration at:\n"
            f"  [cyan]{APP_DIR}[/cyan]\n\n"
            "You can re-run this anytime with [bold]applypilot init[/bold].",
            border_style="green",
        )
    )

    ensure_dirs()
    console.print(f"[dim]Created {APP_DIR}[/dim]\n")

    # Step 1: Resume
    _setup_resume()
    console.print()

    # Step 2: Profile
    _setup_profile()
    console.print()

    # Step 3: Search config
    _setup_searches()
    console.print()

    # Step 4: AI features (optional LLM)
    _setup_ai_features()
    console.print()

    # Step 5: Auto-apply (Claude Code detection)
    _setup_auto_apply()
    console.print()

    # Done — show tier status
    from applypilot.config import get_tier, TIER_LABELS, TIER_COMMANDS

    tier = get_tier()

    tier_lines: list[str] = []
    for t in range(1, 4):
        label = TIER_LABELS[t]
        cmds = ", ".join(f"[bold]{c}[/bold]" for c in TIER_COMMANDS[t])
        if t <= tier:
            tier_lines.append(f"  [green]✓ Tier {t} — {label}[/green]  ({cmds})")
        elif t == tier + 1:
            tier_lines.append(f"  [yellow]→ Tier {t} — {label}[/yellow]  ({cmds})")
        else:
            tier_lines.append(f"  [dim]✗ Tier {t} — {label}  ({cmds})[/dim]")

    unlock_hint = ""
    if tier == 1:
        unlock_hint = "\n[dim]To unlock Tier 2: configure an LLM API key (re-run [bold]applypilot init[/bold]).[/dim]"
    elif tier == 2:
        unlock_hint = "\n[dim]To unlock Tier 3: install Claude Code CLI + Chrome.[/dim]"

    console.print(
        Panel.fit(
            "[bold green]Setup complete![/bold green]\n\n"
            f"[bold]Your tier: Tier {tier} — {TIER_LABELS[tier]}[/bold]\n\n"
            + "\n".join(tier_lines)
            + unlock_hint,
            border_style="green",
        )
    )
