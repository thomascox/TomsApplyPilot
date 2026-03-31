"""ApplyPilot HTML Dashboard Generator.

Generates a self-contained HTML dashboard with:
  - Summary stats (total, scored, tailored, applied, failed)
  - Issues callout (location-ineligible, stuck, expired)
  - Score distribution bar chart
  - Clickable source breakdown filter
  - Score / Stage / Eligibility / Sort filters + text search
  - Active-source banner with one-click clear
  - Job cards with stage badges, posted date, eligibility flags
  - Expandable full job descriptions
"""

from __future__ import annotations

import json
import socket
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from rich.console import Console

from applypilot.config import APP_DIR
from applypilot.database import get_connection

console = Console()

SITE_COLORS: dict[str, str] = {
    "RemoteOK": "#10b981",
    "WelcomeToTheJungle": "#f59e0b",
    "Job Bank Canada": "#3b82f6",
    "CareerJet Canada": "#8b5cf6",
    "Hacker News Jobs": "#ff6600",
    "BuiltIn Remote": "#ec4899",
    "TD Bank": "#00a651",
    "CIBC": "#c41f3e",
    "RBC": "#003168",
    "indeed": "#2164f3",
    "linkedin": "#0a66c2",
    "Dice": "#eb1c26",
    "Glassdoor": "#0caa41",
}
_DEFAULT_COLOR = "#6b7280"

# ── CSS ───────────────────────────────────────────────────────────────────────
# Regular string (no f-string) so CSS braces don't need escaping.

_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  background: #0f172a; color: #e2e8f0; padding: 2rem;
  max-width: 1600px; margin: 0 auto;
}
h1 { font-size: 1.8rem; font-weight: 700; margin-bottom: 0.25rem; }
.subtitle { color: #94a3b8; margin-bottom: 2rem; font-size: 0.88rem; }

/* Summary tiles */
.summary {
  display: grid; grid-template-columns: repeat(6, 1fr);
  gap: 0.75rem; margin-bottom: 1.5rem;
}
.stat-card {
  background: #1e293b; border-radius: 10px; padding: 1rem;
  text-align: center; border-top: 3px solid transparent;
}
.stat-num { font-size: 1.8rem; font-weight: 700; line-height: 1; }
.stat-label { color: #64748b; font-size: 0.72rem; margin-top: 0.3rem;
              text-transform: uppercase; letter-spacing: 0.06em; }
.stat-total   { border-color: #475569; } .stat-total   .stat-num { color: #e2e8f0; }
.stat-scored  { border-color: #3b82f6; } .stat-scored  .stat-num { color: #60a5fa; }
.stat-high    { border-color: #f59e0b; } .stat-high    .stat-num { color: #fbbf24; }
.stat-tailored{ border-color: #8b5cf6; } .stat-tailored .stat-num{ color: #a78bfa; }
.stat-applied { border-color: #10b981; } .stat-applied .stat-num { color: #34d399; }
.stat-failed  { border-color: #ef4444; } .stat-failed  .stat-num { color: #f87171; }
.stat-interview    { border-color: #06b6d4; } .stat-interview    .stat-num { color: #67e8f9; }
.stat-followup     { border-color: #334155; } .stat-followup     .stat-num { color: #94a3b8; }
.stat-followup-alert { border-color: #f59e0b; } .stat-followup-alert .stat-num { color: #fcd34d; }

/* Issues panel */
.issues-panel {
  background: #1c1917; border: 1px solid #92400e; border-radius: 8px;
  padding: 0.65rem 1rem; margin-bottom: 1.5rem;
  display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem;
  font-size: 0.8rem;
}
.issues-label { color: #fbbf24; font-weight: 600; flex-shrink: 0; }
.issue-item {
  background: #292524; color: #fcd34d; padding: 0.18rem 0.55rem;
  border-radius: 4px; border: 1px solid #78350f;
}
.issues-fix { color: #64748b; margin-left: auto; font-size: 0.75rem; }
.issues-fix code {
  background: #1e293b; padding: 0.1rem 0.35rem; border-radius: 3px;
  color: #a5b4fc; font-size: 0.78rem;
}

/* Filters panel */
.filters-panel {
  background: #1e293b; border-radius: 10px; padding: 0.9rem 1.2rem;
  margin-bottom: 1.5rem; display: flex; flex-direction: column; gap: 0.55rem;
}
.filter-row { display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: center; }
.filter-label {
  color: #64748b; font-size: 0.72rem; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.06em; min-width: 3.5rem;
  flex-shrink: 0;
}
.filter-btn {
  background: #334155; border: none; color: #94a3b8;
  padding: 0.32rem 0.7rem; border-radius: 5px; cursor: pointer;
  font-size: 0.76rem; transition: all 0.14s; white-space: nowrap;
}
.filter-btn:hover { background: #475569; color: #e2e8f0; }
.filter-btn.active { background: #3b82f6; color: #fff; font-weight: 600; }
.filter-btn.active.st-pending  { background: #7c3aed; }
.filter-btn.active.st-ready    { background: #0e7490; }
.filter-btn.active.st-applied  { background: #065f46; }
.filter-btn.active.st-failed   { background: #991b1b; }
.filter-btn.active.st-unscored { background: #374151; }
.filter-btn.active.st-scored   { background: #475569; }
.filter-btn.active.el-no       { background: #92400e; }
.filter-btn.active.sort-btn    { background: #1d4ed8; }
.search-input {
  background: #334155; border: 1px solid #475569; color: #e2e8f0;
  padding: 0.32rem 0.7rem; border-radius: 5px; font-size: 0.76rem; width: 220px;
}
.search-input::placeholder { color: #64748b; }
.clear-btn {
  background: transparent; border: 1px solid #475569; color: #64748b;
  padding: 0.32rem 0.7rem; border-radius: 5px; cursor: pointer; font-size: 0.76rem;
  margin-left: auto;
}
.clear-btn:hover { border-color: #94a3b8; color: #94a3b8; }

/* Charts row */
.charts-row {
  display: grid; grid-template-columns: 300px 1fr;
  gap: 1.25rem; margin-bottom: 2rem;
}
.chart-card { background: #1e293b; border-radius: 10px; padding: 1.2rem; }
.chart-card h3 {
  font-size: 0.8rem; color: #64748b; text-transform: uppercase;
  letter-spacing: 0.06em; margin-bottom: 0.9rem;
}
.chart-card h3 small { font-size: 0.72rem; color: #475569;
                        text-transform: none; letter-spacing: 0; }
.score-row { display: flex; align-items: center; gap: 0.45rem; margin-bottom: 0.3rem; }
.score-label { width: 1.1rem; text-align: right; font-size: 0.8rem;
               font-weight: 600; color: #94a3b8; }
.score-bar-track { flex: 1; height: 11px; background: #334155; border-radius: 3px; overflow: hidden; }
.score-bar-fill  { height: 100%; border-radius: 3px; }
.score-count { width: 2.2rem; font-size: 0.72rem; color: #64748b; text-align: right; }

/* Source rows */
.site-row {
  padding: 0.45rem 0.55rem; border-radius: 6px; cursor: pointer;
  transition: background 0.14s; margin-bottom: 0.2rem;
  border: 1px solid transparent;
}
.site-row:hover { background: #334155; }
.site-row.active { background: #1e3a5f; border-color: #3b82f6; }
.site-row-top { display: flex; justify-content: space-between; align-items: baseline; }
.site-name { font-weight: 600; font-size: 0.86rem; }
.site-nums { color: #64748b; font-size: 0.7rem; }
.site-bar-track { height: 4px; background: #334155; border-radius: 2px;
                  display: flex; overflow: hidden; margin-top: 0.28rem; }
.site-bar-fill { height: 100%; }

/* Filter status + active-site banner */
.filter-status { color: #64748b; font-size: 0.8rem; margin-bottom: 0.75rem; min-height: 1.2em; }
.active-site-banner {
  background: #1e3a5f; border: 1px solid #3b82f6; border-radius: 6px;
  padding: 0.45rem 0.75rem; margin-bottom: 1rem; font-size: 0.8rem;
  display: flex; align-items: center; gap: 0.6rem;
}
.banner-clear {
  background: transparent; border: 1px solid #3b82f640; color: #60a5fa;
  padding: 0.18rem 0.5rem; border-radius: 4px; cursor: pointer;
  font-size: 0.72rem; margin-left: auto;
}
.banner-clear:hover { background: #3b82f620; }

/* Score section headers */
.score-header {
  font-size: 1.05rem; font-weight: 600; margin: 2rem 0 0.7rem;
  padding-bottom: 0.35rem; border-bottom: 2px solid;
  display: flex; align-items: center; gap: 0.55rem;
}
.score-badge {
  display: inline-flex; align-items: center; justify-content: center;
  width: 1.7rem; height: 1.7rem; border-radius: 5px;
  color: #0f172a; font-weight: 700; font-size: 0.88rem; flex-shrink: 0;
}

/* Job grid */
.job-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(370px, 1fr));
  gap: 0.8rem; margin-bottom: 0.5rem;
}
.job-card {
  background: #1e293b; border-radius: 8px; padding: 0.85rem;
  border-left: 3px solid #334155; transition: transform 0.12s, box-shadow 0.12s;
}
.job-card:hover { transform: translateY(-1px); box-shadow: 0 4px 18px #00000055; }

.card-header { display: flex; align-items: flex-start; gap: 0.45rem; margin-bottom: 0.4rem; }
.score-pill {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 1.55rem; height: 1.55rem; border-radius: 5px;
  color: #0f172a; font-weight: 700; font-size: 0.76rem; flex-shrink: 0; margin-top: 1px;
}
.job-title {
  color: #e2e8f0; text-decoration: none; font-weight: 600;
  font-size: 0.88rem; flex: 1; line-height: 1.35;
}
.job-title:hover { color: #60a5fa; }
.badges { display: flex; flex-wrap: wrap; gap: 0.25rem; flex-shrink: 0; align-items: flex-start; }

/* Stage / status badges */
.badge {
  font-size: 0.62rem; padding: 0.13rem 0.42rem; border-radius: 3px;
  font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; white-space: nowrap;
}
.badge-applied    { background: #064e3b; color: #6ee7b7; }
.badge-ready      { background: #164e63; color: #67e8f9; }
.badge-pending    { background: #3b0764; color: #d8b4fe; }
.badge-failed     { background: #450a0a; color: #fca5a5; }
.badge-ineligible { background: #431407; color: #fb923c; }
.badge-unscored   { background: #1e293b; color: #64748b; border: 1px solid #334155; }
.badge-scored     { background: #1e293b; color: #64748b; border: 1px solid #334155; }

/* Meta tags */
.meta-row { display: flex; flex-wrap: wrap; gap: 0.28rem; margin-bottom: 0.32rem; }
.meta-tag { font-size: 0.68rem; padding: 0.1rem 0.4rem; border-radius: 3px; }
.meta-tag.site-tag { /* colored per site, set inline */ }
.meta-tag.salary   { background: #064e3b33; color: #6ee7b7; border: 1px solid #064e3b55; }
.meta-tag.location { background: #1e3a5f; color: #93c5fd; }
.meta-tag.posted   { background: #1e293b; color: #64748b; border: 1px solid #334155; }
.meta-tag.posted.fresh { color: #10b981; border-color: #064e3b; }
.meta-tag.applied-tag  { background: #064e3b22; color: #34d399; border: 1px solid #10b98140; }

.keywords-row { font-size: 0.7rem; color: #10b981; margin-bottom: 0.22rem; line-height: 1.45; }
.reasoning-row { font-size: 0.7rem; color: #64748b; margin-bottom: 0.38rem;
                 font-style: italic; line-height: 1.45; }
.desc-preview {
  font-size: 0.76rem; color: #475569; line-height: 1.5; margin-bottom: 0.55rem;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
}
.full-desc-details { margin: 0.35rem 0 0.55rem; }
.expand-btn { font-size: 0.73rem; color: #60a5fa; cursor: pointer;
              list-style: none; padding: 0.22rem 0; }
.expand-btn::-webkit-details-marker { display: none; }
.expand-btn:hover { color: #93c5fd; }
.full-desc {
  font-size: 0.76rem; color: #cbd5e1; line-height: 1.6; margin-top: 0.35rem;
  padding: 0.7rem; background: #0f172a; border-radius: 6px;
  max-height: 400px; overflow-y: auto; white-space: pre-wrap; word-break: break-word;
}
.card-footer { display: flex; justify-content: space-between; align-items: center; margin-top: 0.35rem; }
.apply-link {
  font-size: 0.76rem; color: #60a5fa; text-decoration: none;
  padding: 0.22rem 0.6rem; border: 1px solid #3b82f633; border-radius: 5px; font-weight: 500;
}
.apply-link:hover { background: #3b82f622; }
.error-note { font-size: 0.68rem; color: #f87171; }

/* Star / favorite button — hollow gray by default, fills yellow when favorited */
.star-btn {
  background: transparent; border: none; cursor: pointer;
  font-size: 1.1rem; line-height: 1; padding: 0 0.1rem; flex-shrink: 0;
  color: #475569; transition: color 0.14s, transform 0.1s;
}
.star-btn:hover    { color: #fbbf24; transform: scale(1.2); }
.star-btn.favorited { color: #fbbf24; }

/* Reject button — shown on every non-applied card; calls /api/reject via fetch */
.reject-btn {
  background: transparent; border: 1px solid #ef444430; color: #64748b;
  padding: 0.2rem 0.5rem; border-radius: 5px; cursor: pointer;
  font-size: 0.7rem; transition: all 0.14s; flex-shrink: 0; margin-left: auto;
}
.reject-btn:hover { background: #ef444415; border-color: #f87171; color: #f87171; }
.reject-btn:disabled { opacity: 0.4; cursor: default; }

/* Mark-applied button — green counterpart to reject; calls /api/mark_applied */
.mark-applied-btn {
  background: transparent; border: 1px solid #10b98130; color: #64748b;
  padding: 0.2rem 0.5rem; border-radius: 5px; cursor: pointer;
  font-size: 0.7rem; transition: all 0.14s; flex-shrink: 0;
}
.mark-applied-btn:hover { background: #10b98115; border-color: #34d399; color: #34d399; }
.mark-applied-btn:disabled { opacity: 0.4; cursor: default; }

/* CRM: interview stage pills */
.interview-row { display: flex; gap: 0.3rem; flex-wrap: wrap; margin: 0.4rem 0 0.2rem; align-items: center; }
.interview-label { font-size: 0.68rem; color: #475569; flex-shrink: 0; }
.stage-pill {
  font-size: 0.65rem; padding: 0.15rem 0.45rem; border-radius: 20px; cursor: pointer;
  border: 1px solid #334155; color: #64748b; background: transparent;
  transition: all 0.12s; white-space: nowrap;
}
.stage-pill:hover { border-color: #60a5fa; color: #93c5fd; }
.stage-pill.active { background: #1e40af; border-color: #3b82f6; color: #bfdbfe; }
.stage-pill.active.offer  { background: #065f46; border-color: #10b981; color: #6ee7b7; }
.stage-pill.active.closed { background: #1e1b4b; border-color: #818cf8; color: #c7d2fe; }

/* CRM: follow-up date */
.followup-row { display: flex; align-items: center; gap: 0.4rem; margin: 0.3rem 0 0.1rem; }
.followup-label { font-size: 0.68rem; color: #475569; flex-shrink: 0; }
.followup-input {
  font-size: 0.68rem; background: #0f172a; border: 1px solid #334155; color: #94a3b8;
  border-radius: 4px; padding: 0.15rem 0.35rem; cursor: pointer;
}
.followup-input:focus { outline: none; border-color: #60a5fa; }
.followup-tag { font-size: 0.68rem; padding: 0.1rem 0.4rem; border-radius: 4px; }
.followup-tag.due-future { background: #064e3b22; color: #6ee7b7; border: 1px solid #10b98140; }
.followup-tag.due-today  { background: #78350f22; color: #fcd34d; border: 1px solid #f59e0b40; }
.followup-tag.due-past   { background: #450a0a22; color: #fca5a5; border: 1px solid #ef444440; }

/* CRM: notes + contact fields */
.crm-fields { display: flex; flex-direction: column; gap: 0.3rem; margin: 0.3rem 0 0; }
.crm-field-row { display: flex; align-items: flex-start; gap: 0.4rem; }
.crm-field-label { font-size: 0.68rem; color: #475569; flex-shrink: 0; padding-top: 0.2rem; min-width: 4rem; }
.crm-input {
  font-size: 0.72rem; background: #0f172a; border: 1px solid #1e293b; color: #94a3b8;
  border-radius: 4px; padding: 0.2rem 0.4rem; width: 100%; resize: none;
  font-family: inherit; transition: border-color 0.12s;
}
.crm-input:focus { outline: none; border-color: #334155; }
.crm-input.saving { border-color: #1d4ed8; }
.crm-input.saved  { border-color: #065f46; }

/* Toast notification — fades in/out after a reject action */
.toast {
  position: fixed; bottom: 1.5rem; right: 1.5rem; z-index: 9999;
  background: #1e293b; border: 1px solid #334155; border-radius: 8px;
  padding: 0.7rem 1.1rem; color: #e2e8f0; font-size: 0.83rem;
  opacity: 0; transform: translateY(6px);
  transition: opacity 0.25s, transform 0.25s; pointer-events: none;
}
.toast.show { opacity: 1; transform: translateY(0); }

.empty-state { color: #475569; text-align: center; padding: 3rem; font-size: 0.9rem; }
.hidden { display: none !important; }

@media (max-width: 960px) {
  .summary    { grid-template-columns: repeat(3, 1fr); }
  .charts-row { grid-template-columns: 1fr; }
  .job-grid   { grid-template-columns: 1fr; }
  body        { padding: 1rem; }
}
"""

# ── JavaScript ────────────────────────────────────────────────────────────────
# Regular string with __JOBS__ / __SITES__ placeholders replaced at render time.

_JS_TEMPLATE = """
const JOBS  = __JOBS__;
const SITES = __SITES__;

// ── State ──
const state = {
  minScore:      0,
  search:        '',
  site:          '',
  stage:         '',
  eligible:      '',
  sort:          'score',
  favoritesOnly: false,
  followupDueOnly: false,
};

// ── Helpers ──
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function daysAgo(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr.slice(0,10) + 'T00:00:00Z');
  const days = Math.floor((Date.now() - d.getTime()) / 86400000);
  if (isNaN(days) || days < 0) return '';
  if (days === 0) return 'today';
  if (days === 1) return '1d ago';
  if (days < 30)  return days + 'd ago';
  if (days < 365) return Math.floor(days / 30) + 'mo ago';
  return Math.floor(days / 365) + 'y ago';
}

function isFresh(dateStr) {
  if (!dateStr) return false;
  const d = new Date(dateStr.slice(0,10) + 'T00:00:00Z');
  return (Date.now() - d.getTime()) < 7 * 86400000;
}

// ── Score colors ──
function scoreColor(s) {
  if (s == null) return '#6b7280';
  if (s >= 9)    return '#10b981';
  if (s >= 7)    return '#34d399';
  if (s >= 5)    return '#f59e0b';
  return '#ef4444';
}

function borderColor(s) {
  if (s == null) return '#334155';
  if (s >= 9)    return '#10b981';
  if (s >= 7)    return '#34d399';
  if (s >= 5)    return '#f59e0b';
  if (s >= 3)    return '#ef444488';
  return '#334155';
}

// ── Stage badge ──
const STAGE_META = {
  applied:  ['Applied',       'badge-applied'],
  ready:    ['Ready',         'badge-ready'],
  pending:  ['Needs Tailor',  'badge-pending'],
  failed:   ['Failed',        'badge-failed'],
  unscored: ['Unscored',      'badge-unscored'],
  scored:   ['Low Score',     'badge-scored'],
};
const ERROR_LABELS = {
  not_eligible_location: 'location',
  expired_posting:       'expired',
  invalid_url:           'bad url',
  manual_ats:            'manual ATS',
  manually_rejected:     'rejected',
  manually_applied:      'manual',
};

function stageBadge(stage, error) {
  const meta = STAGE_META[stage];
  if (!meta || !meta[0]) return '';
  let label = meta[0];
  if (stage === 'failed' && error) {
    const hint = ERROR_LABELS[error] || error.replace(/_/g, ' ');
    label += ': ' + hint;
  }
  return '<span class="badge ' + meta[1] + '" title="' + esc(error||'') + '">' + esc(label) + '</span>';
}

function eligBadge(eligible) {
  if (eligible === false)
    return '<span class="badge badge-ineligible">Not Eligible</span>';
  return '';
}

// ── Card renderer ──
const _siteColorMap = {};
SITES.forEach(function(s) { _siteColorMap[s.name] = s.color; });

function makeCard(j) {
  const sc     = j.score;
  const col    = scoreColor(sc);
  const border = borderColor(sc);
  const scDisp = sc !== null ? sc : '?';
  const sColor = _siteColorMap[j.site] || '#6b7280';

  const postedStr = daysAgo(j.posted);
  const fresh     = isFresh(j.posted);

  const salaryTag = j.salary
    ? '<span class="meta-tag salary">' + esc(j.salary) + '</span>' : '';
  const locTag = j.location
    ? '<span class="meta-tag location">' + esc(j.location.slice(0,50)) + '</span>' : '';
  const postedTag = postedStr
    ? '<span class="meta-tag posted' + (fresh ? ' fresh' : '') + '">' + postedStr + '</span>' : '';
  const appliedTag = (j.stage === 'applied' && j.applied_at)
    ? '<span class="meta-tag applied-tag">Applied ' + daysAgo(j.applied_at.slice(0,10)) + '</span>' : '';

  const kwRow = j.keywords
    ? '<div class="keywords-row">' + esc(j.keywords) + '</div>' : '';
  const reaRow = j.reasoning
    ? '<div class="reasoning-row">' + esc(j.reasoning) + '</div>' : '';
  const descRow = j.desc_preview
    ? '<p class="desc-preview">' + esc(j.desc_preview) + '</p>' : '';

  const fullDescRow = j.full_desc
    ? '<details class="full-desc-details"><summary class="expand-btn">&#9656; Full Description</summary>'
      + '<div class="full-desc">' + esc(j.full_desc) + '</div></details>'
    : '';

  const applyBtn = j.apply_url
    ? '<a href="' + esc(j.apply_url) + '" class="apply-link" target="_blank">Apply &rarr;</a>' : '';
  const errNote = (j.stage === 'failed' && j.apply_error)
    ? '<span class="error-note">' + esc((ERROR_LABELS[j.apply_error]||j.apply_error).replace(/_/g,' ')) + '</span>' : '';
  // Reject button: shown for all non-applied jobs.
  const rejectBtn = (j.stage !== 'applied')
    ? '<button class="reject-btn" onclick="rejectJob(this)" title="Remove from pipeline (permanently fail)">&#x2715; Reject</button>'
    : '';
  // Mark-applied button: shown for all non-applied jobs so manual applications can be recorded.
  const markAppliedBtn = (j.stage !== 'applied')
    ? '<button class="mark-applied-btn" onclick="markApplied(this)" title="Mark as manually applied">&#x2714; Applied</button>'
    : '';

  // ── CRM rows (shown for applied + interview-stage jobs, or always visible when populated) ──
  const urlE = esc(j.url);

  // Interview stage pills (show for all applied jobs)
  var interviewHtml = '';
  if (j.stage === 'applied' || j.interview_stage) {
    var pills = INTERVIEW_STAGES.map(function(s) {
      var active = j.interview_stage === s.key ? ' active' + (s.cls ? ' ' + s.cls : '') : '';
      return '<button class="stage-pill' + active + '" data-stage="' + s.key + '" onclick="setInterviewStage(this)">' + s.label + '</button>';
    }).join('');
    interviewHtml = '<div class="interview-row"><span class="interview-label">Interview:</span>' + pills + '</div>';
  }

  // Follow-up date
  var today = new Date().toISOString().slice(0,10);
  var fuVal = j.follow_up_due || '';
  var fuTag = '';
  if (fuVal) {
    var fuCls = fuVal < today ? 'due-past' : (fuVal === today ? 'due-today' : 'due-future');
    var fuDiff = Math.round((new Date(fuVal) - new Date(today)) / 86400000);
    var fuLabel = fuVal === today ? 'Today' : (fuDiff < 0 ? Math.abs(fuDiff) + 'd overdue' : 'in ' + fuDiff + 'd');
    fuTag = '<span class="followup-tag ' + fuCls + '">' + fuLabel + '</span>';
  }
  var followupHtml = '<div class="followup-row"><span class="followup-label">Follow-up:</span>'
    + '<input class="followup-input" type="date" value="' + esc(fuVal) + '" onchange="setFollowupDue(this)">'
    + fuTag + '</div>';

  // Notes + contact fields
  var crmHtml = '<div class="crm-fields">'
    + '<div class="crm-field-row"><span class="crm-field-label">Contact:</span>'
    + '<input class="crm-input" type="text" placeholder="Recruiter or hiring manager\u2026" value="' + esc(j.recruiter_contact) + '"'
    + ' data-field="recruiter_contact" oninput="saveCrmField(this)"></div>'
    + '<div class="crm-field-row"><span class="crm-field-label">Notes:</span>'
    + '<textarea class="crm-input" rows="2" placeholder="Notes\u2026"'
    + ' data-field="notes" oninput="saveCrmField(this)">' + esc(j.notes) + '</textarea></div>'
    + '</div>';

  const starCls = j.favorite ? ' favorited' : '';
  const starIcon = j.favorite ? '\u2605' : '\u2606';
  const starTitle = j.favorite ? 'Remove favorite' : 'Add to favorites';
  const starBtn = '<button class="star-btn' + starCls + '" onclick="toggleFavorite(this)" title="' + starTitle + '">' + starIcon + '</button>';

  return '<div class="job-card" data-url="' + esc(j.url) + '" style="border-left-color:' + border + '">'
    + '<div class="card-header">'
    +   '<span class="score-pill" style="background:' + col + '">' + scDisp + '</span>'
    +   '<a href="' + esc(j.url) + '" class="job-title" target="_blank">' + esc(j.title) + '</a>'
    +   '<div class="badges">' + stageBadge(j.stage, j.apply_error) + eligBadge(j.eligible) + '</div>'
    +   starBtn
    + '</div>'
    + '<div class="meta-row">'
    +   '<span class="meta-tag site-tag" style="background:' + sColor + '22;color:' + sColor + ';border:1px solid ' + sColor + '44">' + esc(j.site) + '</span>'
    +   salaryTag + locTag + postedTag + appliedTag
    + '</div>'
    + kwRow + reaRow + descRow + fullDescRow
    + interviewHtml + followupHtml + crmHtml
    + '<div class="card-footer">' + applyBtn + errNote + markAppliedBtn + rejectBtn + '</div>'
    + '</div>';
}

// ── Score section header ──
const SCORE_LABELS = {
  10: 'Perfect Match', 9: 'Excellent Fit', 8: 'Strong Fit',
  7: 'Good Fit', 6: 'Moderate+', 5: 'Moderate',
  4: 'Weak', 3: 'Poor', 2: 'Very Poor', 1: 'Not Eligible', 0: 'Unscored'
};

function makeScoreHeader(score, count) {
  const col   = scoreColor(score === 0 ? null : score);
  const label = SCORE_LABELS[score] || ('Score ' + score);
  return '<h2 class="score-header" style="border-color:' + col + '">'
    + '<span class="score-badge" style="background:' + col + '">' + (score || '?') + '</span>'
    + esc(label)
    + ' <span style="color:#475569;font-size:0.82rem;font-weight:400">(' + count + ')</span>'
    + '</h2><div class="job-grid">';
}

// ── Source list renderer ──
function renderSources() {
  const el = document.getElementById('sources-list');
  if (!el) return;
  el.innerHTML = SITES.map(function(s) {
    const active   = state.site === s.name;
    const highPct  = s.total ? (s.high_fit / s.total * 100).toFixed(1) : 0;
    const midPct   = s.total ? (s.mid_fit  / s.total * 100).toFixed(1) : 0;
    return '<div class="site-row' + (active ? ' active' : '') + '"'
      + ' data-site="' + esc(s.name) + '" onclick="filterSite(this.dataset.site)">'
      + '<div class="site-row-top">'
      +   '<span class="site-name" style="color:' + s.color + '">' + esc(s.name) + '</span>'
      +   '<span class="site-nums">' + s.total + ' jobs &middot; ' + s.high_fit + ' strong &middot; avg ' + s.avg_score + '</span>'
      + '</div>'
      + '<div class="site-bar-track">'
      +   '<div class="site-bar-fill" style="width:' + highPct + '%;background:' + s.color + '"></div>'
      +   '<div class="site-bar-fill" style="width:' + midPct  + '%;background:' + s.color + '66"></div>'
      + '</div></div>';
  }).join('');
}

// ── Active-source banner ──
function updateBanner() {
  const el = document.getElementById('active-site-banner');
  if (state.site) {
    el.innerHTML = 'Filtering by source: <strong>' + esc(state.site) + '</strong>'
      + '<button class="banner-clear" onclick="filterSite(\\'\\')">&#x2715; Clear</button>';
    el.style.display = 'flex';
  } else {
    el.style.display = 'none';
  }
}

// ── Main render ──
function render() {
  // 1. Filter
  var today = new Date().toISOString().slice(0,10);
  var jobs = JOBS.filter(function(j) {
    var sc = j.score !== null ? j.score : 0;
    if (state.favoritesOnly && !j.favorite) return false;
    if (state.followupDueOnly && !(j.follow_up_due && j.follow_up_due <= today)) return false;
    if (state.minScore > 0 && sc < state.minScore) return false;
    if (state.site && j.site !== state.site) return false;
    if (state.stage && j.stage !== state.stage) return false;
    if (state.eligible === 'yes' && j.eligible === false) return false;
    if (state.eligible === 'no'  && j.eligible !== false) return false;
    if (state.search) {
      var hay = (j.title + ' ' + j.site + ' ' + j.location + ' ' + j.keywords + ' ' + j.reasoning + ' ' + j.recruiter_contact + ' ' + j.notes).toLowerCase();
      if (hay.indexOf(state.search) === -1) return false;
    }
    return true;
  });

  // 2. Sort — favorites float to top; applied jobs sink to bottom of every section,
  //    then sub-sort applied by applied_at descending (most recent applied first).
  function favFirst(a, b)     { return (b.favorite ? 1 : 0) - (a.favorite ? 1 : 0); }
  function appliedLast(a, b)  {
    var aApp = a.stage === 'applied', bApp = b.stage === 'applied';
    if (aApp !== bApp) return aApp ? 1 : -1;
    // both applied: most recently applied first
    if (aApp) return (b.applied_at||'').localeCompare(a.applied_at||'');
    return 0;
  }
  if (state.sort === 'score') {
    jobs.sort(function(a, b) {
      return favFirst(a, b) || appliedLast(a, b) || ((b.score||0) - (a.score||0)) || (b.posted||'').localeCompare(a.posted||'') || a.title.localeCompare(b.title);
    });
  } else if (state.sort === 'date') {
    jobs.sort(function(a, b) {
      return favFirst(a, b) || appliedLast(a, b) || (b.posted||'').localeCompare(a.posted||'') || ((b.score||0) - (a.score||0));
    });
  } else {
    jobs.sort(function(a, b) { return favFirst(a, b) || appliedLast(a, b) || a.title.localeCompare(b.title); });
  }

  // 3. Render cards
  var container = document.getElementById('job-list');
  if (!jobs.length) {
    container.innerHTML = '<div class="empty-state">No jobs match the current filters.</div>';
    document.getElementById('filter-status').textContent = 'Showing 0 of ' + JOBS.length + ' jobs';
    updateBanner();
    renderSources();
    return;
  }

  var html = '';
  var currentScore = null;
  var openGrid = false;

  if (state.sort === 'score') {
    jobs.forEach(function(j) {
      var s = j.score !== null ? j.score : 0;
      if (s !== currentScore) {
        if (openGrid) html += '</div>';
        var cnt = jobs.filter(function(x) { return (x.score !== null ? x.score : 0) === s; }).length;
        html += makeScoreHeader(s, cnt);
        currentScore = s;
        openGrid = true;
      }
      html += makeCard(j);
    });
    if (openGrid) html += '</div>';
  } else {
    html += '<div class="job-grid">';
    jobs.forEach(function(j) { html += makeCard(j); });
    html += '</div>';
  }

  container.innerHTML = html;
  document.getElementById('filter-status').textContent =
    'Showing ' + jobs.length + ' of ' + JOBS.length + ' jobs';
  updateBanner();
  renderSources();
}

// ── Filter action handlers ──
function setScore(val, btn) {
  state.minScore = val;
  document.querySelectorAll('.score-filter-btn').forEach(function(b) { b.classList.remove('active'); });
  btn.classList.add('active');
  render();
}

function setStage(val, btn) {
  state.stage = (state.stage === val) ? '' : val;
  document.querySelectorAll('.stage-filter-btn').forEach(function(b) { b.classList.remove('active'); });
  if (state.stage) btn.classList.add('active');
  render();
}

function setEligible(val, btn) {
  state.eligible = (state.eligible === val) ? '' : val;
  document.querySelectorAll('.elig-filter-btn').forEach(function(b) { b.classList.remove('active'); });
  if (state.eligible) btn.classList.add('active');
  render();
}

function setSort(val, btn) {
  state.sort = val;
  document.querySelectorAll('.sort-btn').forEach(function(b) { b.classList.remove('active'); });
  btn.classList.add('active');
  render();
}

function filterSite(name) {
  state.site = (state.site === name && name !== '') ? '' : name;
  render();
}

function setSearch(val) {
  state.search = val.toLowerCase();
  render();
}

function clearAll() {
  state.minScore = 0; state.search = ''; state.site = '';
  state.stage = ''; state.eligible = ''; state.sort = 'score';
  state.favoritesOnly = false; state.followupDueOnly = false;
  document.querySelectorAll('.score-filter-btn').forEach(function(b) { b.classList.remove('active'); });
  document.querySelectorAll('.stage-filter-btn').forEach(function(b) { b.classList.remove('active'); });
  document.querySelectorAll('.elig-filter-btn').forEach(function(b)  { b.classList.remove('active'); });
  document.querySelectorAll('.sort-btn').forEach(function(b)         { b.classList.remove('active'); });
  document.querySelectorAll('.fav-filter-btn').forEach(function(b)   { b.classList.remove('active'); });
  var d0 = document.querySelector('.score-filter-btn[data-val="0"]');
  if (d0) d0.classList.add('active');
  var ss = document.querySelector('.sort-btn[data-val="score"]');
  if (ss) ss.classList.add('active');
  var si = document.querySelector('.search-input');
  if (si) si.value = '';
  render();
}

// ── Toggle favorite ──
// Calls /api/favorite to persist the starred state, then updates the in-memory
// JOBS array and re-renders so the star fills/empties immediately.
function toggleFavorite(btn) {
  var card = btn.closest('.job-card');
  var url  = card.dataset.url;
  var nowFav = !btn.classList.contains('favorited');
  fetch('/api/favorite', {
    method:  'POST',
    headers: {'Content-Type': 'application/json'},
    body:    JSON.stringify({url: url, favorite: nowFav})
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.ok) {
      for (var i = 0; i < JOBS.length; i++) {
        if (JOBS[i].url === url) { JOBS[i].favorite = nowFav; break; }
      }
      render();
      showToast(nowFav ? '\u2605 Added to favorites' : 'Removed from favorites');
    } else {
      showToast('Error: ' + (d.error || 'unknown'));
    }
  })
  .catch(function() {
    showToast('Server not available \u2014 re-run: applypilot dashboard');
  });
}

function toggleFavoritesOnly(btn) {
  state.favoritesOnly = !state.favoritesOnly;
  btn.classList.toggle('active', state.favoritesOnly);
  render();
}

function filterFollowupDue() {
  state.followupDueOnly = !state.followupDueOnly;
  render();
}

// ── CRM: interview stage ──
const INTERVIEW_STAGES = [
  {key: 'phone_screen', label: 'Phone Screen'},
  {key: 'technical',    label: 'Technical'},
  {key: 'onsite',       label: 'Onsite'},
  {key: 'offer',        label: 'Offer', cls: 'offer'},
  {key: 'closed',       label: 'Closed', cls: 'closed'},
];

function setInterviewStage(el) {
  var url   = el.closest('.job-card').dataset.url;
  var stage = el.dataset.stage;
  var newStage = '';
  for (var i = 0; i < JOBS.length; i++) {
    if (JOBS[i].url === url) {
      newStage = JOBS[i].interview_stage === stage ? '' : stage;
      JOBS[i].interview_stage = newStage;
      break;
    }
  }
  fetch('/api/update_crm', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({url: url, field: 'interview_stage', value: newStage})
  }).catch(function() { showToast('Server not available \u2014 re-run: applypilot dashboard'); });
  render();
}

// ── CRM: follow-up date ──
function setFollowupDue(el) {
  var url = el.closest('.job-card').dataset.url;
  var date = el.value;
  for (var i = 0; i < JOBS.length; i++) {
    if (JOBS[i].url === url) { JOBS[i].follow_up_due = date; break; }
  }
  fetch('/api/update_crm', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({url: url, field: 'follow_up_due', value: date})
  }).catch(function() { showToast('Server not available \u2014 re-run: applypilot dashboard'); });
}

// ── CRM: notes + contact (debounced auto-save) ──
var _crmSaveTimers = {};
function saveCrmField(el) {
  var url   = el.closest('.job-card').dataset.url;
  var field = el.dataset.field;
  var value = el.value;
  var key = url + ':' + field;
  clearTimeout(_crmSaveTimers[key]);
  el.classList.remove('saved');
  el.classList.add('saving');
  _crmSaveTimers[key] = setTimeout(function() {
    for (var i = 0; i < JOBS.length; i++) {
      if (JOBS[i].url === url) { JOBS[i][field] = value; break; }
    }
    fetch('/api/update_crm', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url: url, field: field, value: value})
    })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      el.classList.remove('saving');
      if (d.ok) { el.classList.add('saved'); setTimeout(function(){ el.classList.remove('saved'); }, 1200); }
      else showToast('Save error: ' + (d.error || 'unknown'));
    })
    .catch(function() {
      el.classList.remove('saving');
      showToast('Server not available \u2014 re-run: applypilot dashboard');
    });
  }, 800);
}

// ── Reject job ──
// Calls the local dashboard server's /api/reject endpoint to permanently
// fail a job (apply_attempts=99, apply_error='manually_rejected').
// On success, removes the job from the in-memory JOBS array and re-renders
// so it disappears immediately without a page reload.
function rejectJob(btn) {
  var card = btn.closest('.job-card');
  var url  = card.dataset.url;
  if (!confirm('Remove this job from your pipeline? It will be marked permanently failed and hidden.')) return;
  btn.disabled    = true;
  btn.textContent = 'Removing\u2026';
  fetch('/api/reject', {
    method:  'POST',
    headers: {'Content-Type': 'application/json'},
    body:    JSON.stringify({url: url})
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.ok) {
      for (var i = 0; i < JOBS.length; i++) {
        if (JOBS[i].url === url) { JOBS.splice(i, 1); break; }
      }
      render();
      showToast('Job removed from pipeline');
    } else {
      btn.disabled    = false;
      btn.textContent = '\u2715 Reject';
      showToast('Error: ' + (d.error || 'unknown'));
    }
  })
  .catch(function() {
    btn.disabled    = false;
    btn.textContent = '\u2715 Reject';
    showToast('Server not available \u2014 re-run: applypilot dashboard');
  });
}

// ── Mark as manually applied ──
// Calls /api/mark_applied to record a manual application (applied_at + apply_error='manually_applied').
// On success, updates the job's stage in memory and re-renders so it moves to the Applied filter.
function markApplied(btn) {
  var card = btn.closest('.job-card');
  var url  = card.dataset.url;
  if (!confirm('Mark this job as applied? It will be moved to the Applied stage.')) return;
  btn.disabled    = true;
  btn.textContent = 'Saving\u2026';
  fetch('/api/mark_applied', {
    method:  'POST',
    headers: {'Content-Type': 'application/json'},
    body:    JSON.stringify({url: url})
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.ok) {
      for (var i = 0; i < JOBS.length; i++) {
        if (JOBS[i].url === url) { JOBS[i].stage = 'applied'; JOBS[i].apply_error = 'manually_applied'; break; }
      }
      render();
      showToast('\u2714 Marked as applied');
    } else {
      btn.disabled    = false;
      btn.textContent = '\u2714 Applied';
      showToast('Error: ' + (d.error || 'unknown'));
    }
  })
  .catch(function() {
    btn.disabled    = false;
    btn.textContent = '\u2714 Applied';
    showToast('Server not available \u2014 re-run: applypilot dashboard');
  });
}

function showToast(msg) {
  var t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(function() { t.classList.remove('show'); }, 3000);
}

// ── Boot ──
renderSources();
render();
"""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _compute_stage(j: dict) -> str:
    if j.get("applied_at"):
        return "applied"
    if j.get("apply_attempts") == 99 or j.get("apply_status") == "failed":
        return "failed"
    if j.get("tailored_resume_path"):
        return "ready"
    score = j.get("fit_score")
    if score is not None and score >= 7:
        return "pending"
    if score is not None:
        return "scored"
    return "unscored"


def _job_to_dict(j: dict) -> dict:
    stage = _compute_stage(j)

    eligible_raw = j.get("location_eligible")
    eligible = None if eligible_raw is None else bool(eligible_raw)

    age_date = j.get("posted_date") or (j.get("discovered_at") or "")[:10]

    loc = (j.get("location") or "").lower()
    is_remote = any(r in loc for r in ("remote", "anywhere", "distributed", "work from home", "wfh"))

    reasoning_raw = j.get("score_reasoning") or ""
    lines = reasoning_raw.split("\n", 1)
    keywords = lines[0][:200] if lines else ""
    reasoning = (lines[1][:300] if len(lines) > 1 else "").strip()

    full_desc = j.get("full_description") or ""

    return {
        "url":          j.get("url") or "",
        "title":        j.get("title") or "Untitled",
        "score":        j.get("fit_score"),
        "site":         j.get("site") or "",
        "location":     j.get("location") or "",
        "salary":       j.get("salary") or "",
        "stage":        stage,
        "eligible":     eligible,
        "posted":       age_date,
        "is_remote":    is_remote,
        "keywords":     keywords,
        "reasoning":    reasoning,
        "desc_preview": full_desc[:400],
        "full_desc":    full_desc[:3000],
        "apply_url":         j.get("application_url") or "",
        "apply_error":       j.get("apply_error") or "",
        "applied_at":        j.get("applied_at") or "",
        "favorite":          bool(j.get("favorite")),
        "notes":             j.get("notes") or "",
        "interview_stage":   j.get("interview_stage") or "",
        "follow_up_due":     j.get("follow_up_due") or "",
        "recruiter_contact": j.get("recruiter_contact") or "",
    }


# ── Generator ────────────────────────────────────────────────────────────────

def generate_dashboard(output_path: str | None = None) -> str:
    """Generate an HTML dashboard of all jobs.

    Args:
        output_path: Where to write the HTML file. Defaults to ~/.applypilot/dashboard.html.

    Returns:
        Absolute path to the generated HTML file.
    """
    out = Path(output_path) if output_path else APP_DIR / "dashboard.html"
    conn = get_connection()

    # ── Summary stats ──
    total    = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    scored   = conn.execute("SELECT COUNT(*) FROM jobs WHERE fit_score IS NOT NULL").fetchone()[0]
    high_fit = conn.execute("SELECT COUNT(*) FROM jobs WHERE fit_score >= 7").fetchone()[0]
    tailored = conn.execute("SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL").fetchone()[0]
    applied  = conn.execute("SELECT COUNT(*) FROM jobs WHERE applied_at IS NOT NULL").fetchone()[0]
    failed   = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE apply_attempts = 99 AND applied_at IS NULL"
    ).fetchone()[0]

    # ── Issue counts ──
    cutoff_30 = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    issue_ineligible = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE location_eligible = 0 "
        "AND COALESCE(apply_status,'') NOT IN ('applied','failed') AND COALESCE(apply_attempts,0) != 99"
    ).fetchone()[0]
    issue_stuck = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE apply_status = 'in_progress'"
    ).fetchone()[0]
    issue_expired = conn.execute(
        "SELECT COUNT(*) FROM jobs "
        "WHERE tailored_resume_path IS NULL AND applied_at IS NULL "
        "AND COALESCE(apply_status,'') NOT IN ('applied','failed') "
        "AND COALESCE(posted_date, substr(discovered_at,1,10)) < ? "
        "AND COALESCE(posted_date, substr(discovered_at,1,10)) != ''",
        (cutoff_30,),
    ).fetchone()[0]
    total_issues = issue_ineligible + issue_stuck + issue_expired

    # ── Score distribution ──
    score_dist: dict[int, int] = {}
    for r in conn.execute(
        "SELECT fit_score, COUNT(*) FROM jobs WHERE fit_score IS NOT NULL "
        "GROUP BY fit_score ORDER BY fit_score DESC"
    ).fetchall():
        score_dist[r[0]] = r[1]

    # ── Site stats ──
    site_raw = conn.execute("""
        SELECT site,
               COUNT(*) AS total,
               SUM(CASE WHEN fit_score >= 7 THEN 1 ELSE 0 END) AS high_fit,
               SUM(CASE WHEN fit_score BETWEEN 5 AND 6 THEN 1 ELSE 0 END) AS mid_fit,
               SUM(CASE WHEN fit_score < 5 AND fit_score IS NOT NULL THEN 1 ELSE 0 END) AS low_fit,
               SUM(CASE WHEN fit_score IS NULL THEN 1 ELSE 0 END) AS unscored,
               ROUND(AVG(fit_score), 1) AS avg_score
        FROM jobs GROUP BY site ORDER BY high_fit DESC, total DESC
    """).fetchall()

    site_stats = []
    for s in site_raw:
        name = s[0] or "?"
        site_stats.append({
            "name":      name,
            "color":     SITE_COLORS.get(name, _DEFAULT_COLOR),
            "total":     s[1],
            "high_fit":  s[2] or 0,
            "mid_fit":   s[3] or 0,
            "low_fit":   s[4] or 0,
            "unscored":  s[5] or 0,
            "avg_score": s[6] or 0,
        })

    # ── Active jobs (excludes permanently failed) ──
    # Jobs with apply_attempts=99 are permanently out of the pipeline — they are
    # already counted in the "Perm. Failed" stat tile and need no further action.
    # Showing them in the card list just adds noise and causes confusion after
    # manual rejection or expired-posting cleanup.
    in_interview = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE interview_stage IS NOT NULL AND interview_stage != '' "
        "AND interview_stage NOT IN ('closed') AND applied_at IS NOT NULL"
    ).fetchone()[0]
    today_str = datetime.now().strftime("%Y-%m-%d")
    followup_due = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE follow_up_due IS NOT NULL AND follow_up_due != '' "
        "AND follow_up_due <= ? AND COALESCE(apply_attempts, 0) < 99",
        (today_str,),
    ).fetchone()[0]

    rows = conn.execute("""
        SELECT url, title, salary, location, site,
               full_description, application_url,
               fit_score, score_reasoning,
               tailored_resume_path, cover_letter_path,
               applied_at, apply_status, apply_error, apply_attempts,
               location_eligible, posted_date, discovered_at,
               COALESCE(favorite, 0) AS favorite,
               notes, interview_stage, follow_up_due, recruiter_contact
        FROM jobs
        WHERE COALESCE(apply_attempts, 0) < 99
        ORDER BY COALESCE(favorite, 0) DESC, COALESCE(fit_score, -1) DESC, title
    """).fetchall()

    if rows:
        cols = rows[0].keys()
        jobs_data = [_job_to_dict(dict(zip(cols, r))) for r in rows]
    else:
        jobs_data = []

    # ── Score distribution bars (static HTML) ──
    max_count = max(score_dist.values()) if score_dist else 1
    score_bars_html = ""
    for s in range(10, 0, -1):
        count = score_dist.get(s, 0)
        pct   = count / max_count * 100 if max_count else 0
        color = "#10b981" if s >= 7 else ("#f59e0b" if s >= 5 else "#ef4444")
        score_bars_html += (
            f'<div class="score-row">'
            f'<span class="score-label">{s}</span>'
            f'<div class="score-bar-track">'
            f'<div class="score-bar-fill" style="width:{pct:.1f}%;background:{color}"></div>'
            f'</div>'
            f'<span class="score-count">{count}</span>'
            f'</div>'
        )

    # ── Issues panel (static HTML) ──
    issues_html = ""
    if total_issues > 0:
        parts = []
        if issue_ineligible:
            parts.append(f'<span class="issue-item">&#9888; {issue_ineligible} location-ineligible in queue</span>')
        if issue_expired:
            parts.append(f'<span class="issue-item">&#8987; {issue_expired} expired postings (&gt;30d)</span>')
        if issue_stuck:
            parts.append(f'<span class="issue-item">&#9646; {issue_stuck} stuck in-progress</span>')
        issues_html = (
            '<div class="issues-panel">'
            '<span class="issues-label">Issues detected:</span>'
            + "".join(parts)
            + '<span class="issues-fix">&rarr; run <code>applypilot prune --all</code> to fix</span>'
            + "</div>"
        )

    # ── JSON blobs ──
    jobs_json  = json.dumps(jobs_data,  ensure_ascii=False)
    sites_json = json.dumps(site_stats, ensure_ascii=False)
    js = _JS_TEMPLATE.replace("__JOBS__", jobs_json).replace("__SITES__", sites_json)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ApplyPilot Dashboard</title>
<style>{_CSS}</style>
</head>
<body>

<h1>ApplyPilot Dashboard</h1>
<p class="subtitle">Generated {ts} &middot; {total} jobs &middot; {scored} scored &middot; {high_fit} strong matches (7+)</p>

<div class="summary">
  <div class="stat-card stat-total">
    <div class="stat-num">{total}</div><div class="stat-label">Total</div>
  </div>
  <div class="stat-card stat-scored">
    <div class="stat-num">{scored}</div><div class="stat-label">Scored</div>
  </div>
  <div class="stat-card stat-high">
    <div class="stat-num">{high_fit}</div><div class="stat-label">Strong Fit (7+)</div>
  </div>
  <div class="stat-card stat-tailored">
    <div class="stat-num">{tailored}</div><div class="stat-label">Tailored</div>
  </div>
  <div class="stat-card stat-applied">
    <div class="stat-num">{applied}</div><div class="stat-label">Applied</div>
  </div>
  <div class="stat-card stat-failed">
    <div class="stat-num">{failed}</div><div class="stat-label">Perm. Failed</div>
  </div>
  <div class="stat-card stat-interview" onclick="setStage('applied',document.querySelector('.st-applied'))" style="cursor:pointer" title="Filter to Applied jobs">
    <div class="stat-num">{in_interview}</div><div class="stat-label">In Interview</div>
  </div>
  <div class="stat-card stat-followup{' stat-followup-alert' if followup_due > 0 else ''}" onclick="filterFollowupDue()" style="cursor:pointer" title="Filter to jobs with follow-up due">
    <div class="stat-num">{followup_due}</div><div class="stat-label">Follow-up Due</div>
  </div>
</div>

{issues_html}

<div class="filters-panel">
  <div class="filter-row">
    <span class="filter-label">Score</span>
    <button class="filter-btn score-filter-btn active" data-val="0"  onclick="setScore(0,this)">All</button>
    <button class="filter-btn score-filter-btn"        data-val="5"  onclick="setScore(5,this)">5+</button>
    <button class="filter-btn score-filter-btn"        data-val="7"  onclick="setScore(7,this)">7+ Strong</button>
    <button class="filter-btn score-filter-btn"        data-val="8"  onclick="setScore(8,this)">8+ Excellent</button>
    <button class="filter-btn score-filter-btn"        data-val="9"  onclick="setScore(9,this)">9+ Perfect</button>
  </div>
  <div class="filter-row">
    <span class="filter-label">Stage</span>
    <button class="filter-btn stage-filter-btn st-unscored" onclick="setStage('unscored',this)">Unscored</button>
    <button class="filter-btn stage-filter-btn st-scored"   onclick="setStage('scored',this)">Low Score</button>
    <button class="filter-btn stage-filter-btn st-pending"  onclick="setStage('pending',this)">Needs Tailor</button>
    <button class="filter-btn stage-filter-btn st-ready"    onclick="setStage('ready',this)">Ready to Apply</button>
    <button class="filter-btn stage-filter-btn st-applied"  onclick="setStage('applied',this)">Applied</button>
    <button class="filter-btn stage-filter-btn st-failed"   onclick="setStage('failed',this)">Failed</button>
  </div>
  <div class="filter-row">
    <span class="filter-label">Eligible</span>
    <button class="filter-btn elig-filter-btn el-yes" onclick="setEligible('yes',this)">Eligible only</button>
    <button class="filter-btn elig-filter-btn el-no"  onclick="setEligible('no',this)">Not Eligible</button>
    <button class="filter-btn fav-filter-btn" style="margin-left:0.5rem" onclick="toggleFavoritesOnly(this)">&#9733; Favorites</button>
    <span class="filter-label" style="margin-left:1rem">Sort</span>
    <button class="filter-btn sort-btn active" data-val="score" onclick="setSort('score',this)">Score &darr;</button>
    <button class="filter-btn sort-btn"        data-val="date"  onclick="setSort('date',this)">Date &darr;</button>
    <button class="filter-btn sort-btn"        data-val="alpha" onclick="setSort('alpha',this)">A &rarr; Z</button>
    <span class="filter-label" style="margin-left:1rem">Search</span>
    <input  class="search-input" type="text" placeholder="title, site, keywords..." oninput="setSearch(this.value)">
    <button class="clear-btn" onclick="clearAll()">Clear all</button>
  </div>
</div>

<div class="charts-row">
  <div class="chart-card">
    <h3>Score Distribution</h3>
    {score_bars_html}
  </div>
  <div class="chart-card">
    <h3>By Source <small>(click to filter)</small></h3>
    <div id="sources-list"></div>
  </div>
</div>

<div id="filter-status" class="filter-status"></div>
<div id="active-site-banner" class="active-site-banner" style="display:none"></div>
<div id="job-list"></div>

<div id="toast" class="toast"></div>
<script>{js}</script>
</body>
</html>"""

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    abs_path = str(out.resolve())
    console.print(f"[green]Dashboard written to {abs_path}[/green]")
    return abs_path


def _find_free_port() -> int:
    """Bind to port 0 to let the OS assign a free port, then release it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_handler(output_path: str):
    """Build a BaseHTTPRequestHandler class that serves the dashboard HTML
    and handles mutation endpoints: /api/reject and /api/favorite.

    The HTML is regenerated from the current DB state on every GET request
    so that changes (favorites, rejects) are always reflected on page refresh
    without needing to restart the server.
    """

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                # Regenerate from the current DB state so favorites/rejects
                # are always fresh on page reload.
                generate_dashboard(output_path)
                html_bytes = Path(output_path).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html_bytes)))
                self.end_headers()
                self.wfile.write(html_bytes)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if self.path == "/api/favorite":
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                try:
                    data = json.loads(body)
                    url = str(data.get("url", "")).strip()
                    fav = bool(data.get("favorite", False))
                    if not url:
                        raise ValueError("missing url")
                    conn = get_connection()
                    conn.execute(
                        "UPDATE jobs SET favorite=? WHERE url=?",
                        (1 if fav else 0, url),
                    )
                    conn.commit()
                    resp = json.dumps({"ok": True}).encode()
                except Exception as exc:
                    resp = json.dumps({"ok": False, "error": str(exc)}).encode()

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

            elif self.path == "/api/reject":
                # Read request body
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                try:
                    data = json.loads(body)
                    url = str(data.get("url", "")).strip()
                    if not url:
                        raise ValueError("missing url")

                    # Permanently fail the job — same semantics as prune --location-ineligible
                    conn = get_connection()
                    conn.execute(
                        "UPDATE jobs SET apply_attempts=99, apply_status='failed', "
                        "apply_error='manually_rejected' WHERE url=?",
                        (url,),
                    )
                    conn.commit()

                    resp = json.dumps({"ok": True}).encode()
                except Exception as exc:
                    resp = json.dumps({"ok": False, "error": str(exc)}).encode()

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

            elif self.path == "/api/mark_applied":
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                try:
                    data = json.loads(body)
                    url = str(data.get("url", "")).strip()
                    if not url:
                        raise ValueError("missing url")

                    now = datetime.now(timezone.utc).isoformat()
                    conn = get_connection()
                    conn.execute(
                        "UPDATE jobs SET applied_at=?, apply_status='applied', "
                        "apply_error='manually_applied' WHERE url=?",
                        (now, url),
                    )
                    conn.commit()

                    resp = json.dumps({"ok": True}).encode()
                except Exception as exc:
                    resp = json.dumps({"ok": False, "error": str(exc)}).encode()

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

            elif self.path == "/api/update_crm":
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                try:
                    data = json.loads(body)
                    url   = str(data.get("url", "")).strip()
                    field = str(data.get("field", "")).strip()
                    value = data.get("value", "")
                    _ALLOWED_CRM_FIELDS = {"notes", "interview_stage", "follow_up_due", "recruiter_contact"}
                    if not url:
                        raise ValueError("missing url")
                    if field not in _ALLOWED_CRM_FIELDS:
                        raise ValueError(f"invalid field: {field!r}")

                    conn = get_connection()
                    conn.execute(f"UPDATE jobs SET {field}=? WHERE url=?", (value or None, url))
                    conn.commit()

                    resp = json.dumps({"ok": True}).encode()
                except Exception as exc:
                    resp = json.dumps({"ok": False, "error": str(exc)}).encode()

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, fmt: str, *args) -> None:
            pass  # silence per-request access logs; use applypilot's own logger

    return _Handler


def open_dashboard(output_path: str | None = None) -> None:
    """Generate the dashboard and serve it via a local HTTP server.

    Starts a lightweight HTTP server on localhost, opens the browser, and
    blocks until the user presses Ctrl+C. While running, the dashboard's
    Reject buttons call POST /api/reject to permanently fail a job and
    remove it from the live view without a page reload.

    Args:
        output_path: Optional path to also write the static HTML file.
    """
    # Generate the initial HTML and determine the file path.
    # Subsequent GET requests will regenerate from the DB automatically.
    path = generate_dashboard(output_path)

    port = _find_free_port()
    url = f"http://127.0.0.1:{port}"

    server = HTTPServer(("127.0.0.1", port), _make_handler(path))

    console.print(f"[green]Dashboard:[/green] {url}")
    console.print("[dim]Press Ctrl+C to stop[/dim]")
    webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        console.print("\n[dim]Dashboard stopped.[/dim]")
