"""
CardioReport – Narrative Generator
Deterministic phrase taxonomy (USE_LLM=false) and LLM-powered narrative (USE_LLM=true).
All label constants imported from config.py — no duplicated maps.

FIX 1: Narrative returns structured dict with opening, phase_lines, closing.
FIX 2: Action bullets contain per-phase vitals — no duplicate text.
FIX 3: Coverage routed through build_coverage_string with positional_stats.
"""

from __future__ import annotations
import json
from datetime import datetime
from typing import Optional

from .config import settings, CONDITION_DISPLAY, RENDER_CONFIG, PhaseTypes, Conditions
from .models import VitalStats, DataQuality, Episode, EpisodeRollups


# ── Helpers ──────────────────────────────────────────────────────────────────

def compute_reporting_period_days(window_start, window_end):
    import pandas as pd
    return (pd.Timestamp(window_end).normalize() - pd.Timestamp(window_start).normalize()).days + 1


def compute_data_coverage_days(dly):
    return len(dly)


# ── R15 B5: Hours → days display helper ─────────────────────────────────────

def format_hours_or_days(hours: int | float) -> str:
    """Format an hour count as a human-readable string.

    Below threshold: "Nh".
    At/above threshold: "Nh (~D days)".

    Used in narrative text only (Episodic Burden line, trajectory line, clinical
    pattern observations). NOT used in events table where hours are the precise
    per-episode metric.
    """
    h = int(round(float(hours)))
    if h >= settings.hours_to_days_display_threshold:
        days = h // 24
        return settings.hours_to_days_display_format.format(hours=h, days=days)
    return f"{h}h"



def _plain(condition: str) -> str:
    """Map internal condition name to clinical display name."""
    return CONDITION_DISPLAY.get(condition, condition)


def _clip_physiologic(value: float, metric: str) -> tuple[float, bool]:
    """R12 Fix 5: Clip value to physiologic bounds. Returns (clipped_value, was_clipped)."""
    bounds = RENDER_CONFIG.get("physiologic_bounds", {}).get(metric)
    if not bounds:
        return value, False
    if value < bounds["min"]:
        return bounds["min"], True
    if value > bounds["max"]:
        return bounds["max"], True
    return value, False


def _events_table_row_1_phase_type(phase_table_rows):
    """Return the phase_type of the headline (dominant) finding surfaced to the
    clinician — used by both the per-patient Major Findings line and the batch
    summary Comments column, so the two surfaces always agree.

    R26 Fix 2: the dominant finding is the one with the MOST total episode-hours
    (burden-dominant), not the highest tier. Tier is only a tiebreak within
    equal duration — the inverse of the pre-R26 priority-first rule, which let a
    1-hour high-tier spike outrank an 11-hour sustained lower-tier finding
    (Harris April: two 1h High-HR hours headlined over an 11h Elevated-HR run).

    Sort key (lowest tuple wins):
      1. -total_episode_hours (more burden wins)
      2. priority_order index (tier tiebreak; lower = more severe)
      3. start date ASC (earlier wins)

    Returns the phase_type string (e.g. "elevated_hr") or None if no rows.
    """
    if not phase_table_rows:
        return None
    import pandas as pd
    from .config import PHASE_LABELS
    priority_order = RENDER_CONFIG.get("events_table", {}).get("priority_order", [])
    label_to_type = {v: k for k, v in PHASE_LABELS.items() if v}

    def _key(row):
        cat = row.get('category', '')
        pt = label_to_type.get(cat)
        pri = priority_order.index(pt) if pt and pt in priority_order else 999
        total_hours = row.get('total_hours', row.get('sustained_hours', 0)) or 0
        date_str = row.get('date', '')
        try:
            first_date = date_str.split(' to ')[0].strip() if date_str else ''
            sort_date = pd.Timestamp(f"{first_date} 2000") if first_date else pd.Timestamp('2099-01-01')
        except Exception:
            sort_date = pd.Timestamp('2099-01-01')
        return (-total_hours, pri, sort_date)

    sorted_rows = sorted(phase_table_rows, key=_key)
    row_1 = sorted_rows[0]
    return label_to_type.get(row_1.get('category', ''))


def _condition_type_summary(counts: dict[str, int]) -> str:
    """Build English string like 'Very Low Heart Rate, Elevated Breathing Rate'."""
    parts = [_plain(cond) for cond, _ in sorted(counts.items(), key=lambda x: -x[1])]
    return ", ".join(parts) if parts else "no significant conditions"


def build_coverage_string(data_quality, positional_stats=None) -> str:
    """Build coverage display string. Round 10 Fix 7: Always split per-sensor.

    Uses RENDER_CONFIG template. Never combines sensors into a single percentage.
    """
    cov_cfg = RENDER_CONFIG["coverage"]
    fmt = cov_cfg["format_template"]

    if positional_stats is not None:
        rows = getattr(positional_stats, 'rows', None) or []
        if not rows and isinstance(positional_stats, dict):
            rows = positional_stats.get('rows', [])
        if len(rows) >= 1:
            expected = data_quality.expected_hours if hasattr(data_quality, 'expected_hours') else data_quality.get('expected_hours', 1)
            parts = []
            for r in rows:
                loc = getattr(r, 'location', None) or r.get('location', 'Unknown')
                hrs = getattr(r, 'hours', None) or r.get('hours', 0)
                pct = round(100 * hrs / max(expected, 1), 1)
                # Use config display name if available
                sensor_key = loc.lower()
                display_name = cov_cfg["sensor_display_names"].get(sensor_key, loc)
                parts.append(fmt.format(sensor=display_name, hours=hrs, total=expected, pct=pct))
            return "Coverage: " + "  |  ".join(parts)

    total = data_quality.total_hours if hasattr(data_quality, 'total_hours') else data_quality.get('total_hours', 0)
    expected = data_quality.expected_hours if hasattr(data_quality, 'expected_hours') else data_quality.get('expected_hours', 1)
    # R26 Fix 5 — read the canonical coverage value, not quality_pct.
    pct = data_quality.coverage_pct if hasattr(data_quality, 'coverage_pct') else data_quality.get('coverage_pct', 0)
    # Single-sensor: still use same template format for cohort visual consistency
    return "Coverage: " + fmt.format(sensor="Device", hours=total, total=expected, pct=pct)


# ── Unified events-table helpers ─────────────────────────────────────────────

def _epi_minmax(episodes, phase_type, attr, fn):
    """Aggregate a structured per-channel min/max (attr = min_hr/max_hr/min_rr/
    max_rr) over ONLY those episodes whose condition matches `phase_type` — the
    same condition-matched gate R26 applied to the average. Replaces the
    `_extract_vitals` 'Max' fallback, which matched BOTH channel segments of the
    key_vitals string and leaked a respiratory value onto a heart-rate row.
    Returns None when no matching episode carries the metric."""
    from .config import CONDITION_TO_PHASE_TYPE
    vals = [getattr(e, attr) for e in episodes
            if getattr(e, attr, None) is not None
            and CONDITION_TO_PHASE_TYPE.get(getattr(e, 'condition', None)) == phase_type]
    return fn(vals) if vals else None


def _fmt_clock(ts) -> str:
    """Clock-only time. Drops ':00' on whole hours ('7 PM'); keeps minutes
    otherwise ('7:30 PM'). No leading zero on the hour."""
    import pandas as pd
    t = pd.Timestamp(ts)
    fmt = '%I %p' if t.minute == 0 else '%I:%M %p'
    return t.strftime(fmt).lstrip('0')


def _episode_time_span(ep) -> str:
    """Clock window for an episode: start -> start + duration. A 1-hour episode
    at 8:00 reads '8 AM to 9 AM' (the hour window), not '8 AM to 8 AM' (the
    hourly aggregate's start==end timestamp)."""
    import pandas as pd
    start = pd.Timestamp(ep.start_time)
    dur = int(getattr(ep, 'duration_hours', 1) or 1)
    end = start + pd.Timedelta(hours=dur)
    return f"{_fmt_clock(start)} to {_fmt_clock(end)}"


def _first_condition_episode(episodes, phase_type):
    """The earliest episode whose condition matches this finding's phase type.
    Drives the Date (start date) and Time Span (clock window) cells."""
    import pandas as pd
    from .config import CONDITION_TO_PHASE_TYPE
    matched = [e for e in episodes
               if CONDITION_TO_PHASE_TYPE.get(getattr(e, 'condition', None)) == phase_type]
    pool = matched or list(episodes)
    if not pool:
        return None
    return min(pool, key=lambda e: pd.Timestamp(e.start_time))


def _row_comment(phase_type: str) -> str:
    """Per-finding Comment: the condition-keyed clinical-focus phrase, single-
    sourced from clinical_guidance.review_phrase_by_phase_type (identical for
    every row of the same condition; data-shape-agnostic, not per-patient)."""
    return RENDER_CONFIG.get("clinical_guidance", {}).get(
        "review_phrase_by_phase_type", {}).get(phase_type, "")


# ── A2: single-source HR P5–P95 spread formatter ─────────────────────────────

def format_spread_bounds(p5: float, p95: float) -> dict:
    """A2: the ONE place that turns the canonical (Compute-stage) HR percentiles
    into display strings. Every surface — page-1 sentence, page-2 pattern
    observation, and the histogram annotation — formats through this helper, so
    the three can never diverge (the pre-fix report showed 53–93 on page 1, 52–92
    on page 2, and no bounds on the chart).

    The spread is derived from the SAME rounded bounds it displays (hi − lo), so
    the printed spread always equals the printed bounds' difference — independent
    rounding of ``p95 − p5`` was itself a source of off-by-one skew.
    """
    lo = int(round(p5))
    hi = int(round(p95))
    spread = hi - lo
    return {
        "p5": str(lo),
        "p95": str(hi),
        "spread": str(spread),
        "spread_int": spread,
        "bounds": f"{lo} to {hi}",
        "text": f"{spread} bpm ({lo} to {hi})",
    }


# ── A1: per-episode events-table rows ────────────────────────────────────────

def _duration_descriptor(hours: int) -> str:
    """B6: plain-text duration severity for a single episode, kept off the
    value-severity colour axis. 'sustained' vs 'brief' by config threshold."""
    return "sustained" if int(hours or 0) >= settings.episode_sustained_min_hours else "brief"


def _low_coverage_dates(activity_trend) -> set:
    """B3: the set of calendar dates the monitoring-activity chart itself colours
    as reduced coverage (recorded hours below the config threshold). Reused as a
    lookup so a flagged episode falling on such a day gets the caution caveat —
    no new coverage math, just the per-day hours already computed in Compute."""
    import pandas as pd
    dates = set()
    days = getattr(activity_trend, "days", None) if activity_trend is not None else None
    for d in days or []:
        try:
            if float(getattr(d, "hours", 0)) < settings.reduced_coverage_hours_per_day:
                dates.add(pd.Timestamp(getattr(d, "date")).normalize())
        except Exception:
            continue
    return dates



def build_episode_table_rows(episodes, hr_stats, rr_stats, activity_trend=None):
    """A1: one row per DETECTED episode for the Major Findings and Last-24h
    episodic-events tables. Time Span and Duration are both sourced from the SAME
    episode object, so they always agree — unlike the prior per-condition rollup,
    which rendered one representative episode's clock window beside a summed
    duration ("5 PM to 6 PM / 13h / 4 episodes"). Detection is untouched; this is
    pure table assembly.

    avg/min/max come only from the episode's own channel (condition-matched
    structured fields), HR clipped to physiologic bounds. A B3 reduced-coverage
    caveat is appended to the comment when the episode overlaps a low-coverage
    day. Rows carry severity_score (Major Findings ranks by it, desc) and
    start_time (the 24h table ranks chronologically).
    """
    import pandas as pd
    from .config import PHASE_LABELS, CONDITION_TO_PHASE_TYPE

    low_cov = _low_coverage_dates(activity_trend)
    rows = []
    for ep in episodes:
        pt = CONDITION_TO_PHASE_TYPE.get(getattr(ep, "condition", None))
        if not pt:
            continue
        is_hr = pt in _HR_PHASE_TYPES
        is_rr = pt in _RR_PHASE_TYPES
        start = pd.Timestamp(ep.start_time)
        dur = int(getattr(ep, "duration_hours", 1) or 1)

        if is_hr:
            avg_v, mn_v, mx_v, unit, metric = ep.avg_hr, ep.min_hr, ep.max_hr, "bpm", "hr_bpm"
        elif is_rr:
            avg_v, mn_v, mx_v, unit, metric = ep.avg_rr, ep.min_rr, ep.max_rr, "brpm", "rr_brpm"
        else:
            avg_v = mn_v = mx_v = None
            unit, metric = "", None

        def _fmt(v):
            if v is None:
                return "—"
            if metric == "hr_bpm":
                cv, clip = _clip_physiologic(v, "hr_bpm")
                return f"{cv:.0f}{'*' if clip else ''} {unit}"
            return f"{v:.0f} {unit}"

        comment = _row_comment(pt)
        # B3 — flag (don't inline) the reduced-coverage caveat: the renderer marks
        # the row and prints the full caveat once as a table footnote, so every
        # affected comment cell stays compact (inlining the sentence ballooned
        # cells to ~4 lines and pushed dense reports to a third page).
        overlaps_low = False
        if low_cov:
            end = start + pd.Timedelta(hours=dur)
            span_days = pd.date_range(start.normalize(), end.normalize(), freq="D")
            overlaps_low = any(d.normalize() in low_cov for d in span_days)

        rows.append({
            "reduced_coverage": overlaps_low,
            "category": PHASE_LABELS.get(pt, pt),
            "phase_type": pt,
            "date": start.strftime("%b %d"),
            "start_time": ep.start_time,
            "time_span": _episode_time_span(ep),
            "total_hours": dur,
            "duration_hours": dur,
            "duration_descriptor": _duration_descriptor(dur),
            "average": _fmt(avg_v),
            "min": _fmt(mn_v),
            "max": _fmt(mx_v),
            "comment": comment,
            "severity_score": int(getattr(ep, "severity_score", 0) or 0),
            "cooccurrence": bool(getattr(ep, "cooccurrence", False)),
        })
    return rows


# ── Phase Description Templates ──────────────────────────────────────────────

def _phase_description(p_type, label, date_range, ph_hr, p_eps, hr_stats, rr_stats):
    """Build a trimmed, type-specific phase description string.
    
    Returns None for normal phases (should be skipped).
    """
    if p_type == 'normal':
        return None

    if p_type == 'low_hr':
        hr_mins = _extract_vitals(p_eps, 'Min HR')
        min_hr = min(hr_mins) if hr_mins else hr_stats.min
        coupled = sum(1 for e in p_eps if e.cooccurrence)
        txt = f"{label} ({date_range}): Average {ph_hr:.0f} bpm, minimum {min_hr:.0f} bpm"
        if coupled:
            txt += ", with concurrent elevated breathing"
        return txt + "."

    elif p_type == 'very_low_hr':
        hr_mins = _extract_vitals(p_eps, 'Min HR')
        min_hr = min(hr_mins) if hr_mins else hr_stats.min
        coupled = sum(1 for e in p_eps if e.cooccurrence)
        txt = f"{label} ({date_range}): Average {ph_hr:.0f} bpm, minimum {min_hr:.0f} bpm"
        if coupled:
            txt += ", with concurrent elevated breathing"
        return txt + ". Immediate review suggested."

    elif p_type == 'elevated_hr':
        hr_maxs = _extract_vitals(p_eps, 'hr_max') or _extract_vitals(p_eps, 'Max')
        max_hr = max(hr_maxs) if hr_maxs else (hr_stats.max if hr_stats else 0)
        total_h = sum(e.duration_hours for e in p_eps)
        return f"{label} ({date_range}): Average {ph_hr:.0f} bpm, peak {max_hr:.0f} bpm, sustained {total_h} hours."

    elif p_type == 'high_hr':
        hr_maxs = _extract_vitals(p_eps, 'hr_max') or _extract_vitals(p_eps, 'Max')
        max_hr = max(hr_maxs) if hr_maxs else (hr_stats.max if hr_stats else 0)
        return f"{label} ({date_range}): Average {ph_hr:.0f} bpm, peak {max_hr:.0f} bpm."

    elif p_type == 'very_high_hr':
        hr_maxs = _extract_vitals(p_eps, 'hr_max') or _extract_vitals(p_eps, 'Max')
        max_hr = max(hr_maxs) if hr_maxs else (hr_stats.max if hr_stats else 0)
        return f"{label} ({date_range}): Average {ph_hr:.0f} bpm, peak {max_hr:.0f} bpm. Immediate review suggested."

    elif p_type in ('elevated_rr', 'high_rr', 'very_high_rr'):
        # R15 A2: Three RR tiers share rendering — qualifier comes from the type label.
        # R22.B: RR upper-bound clipping reversed; show the actual peak so any
        # residual data-quality issue stays visible to the clinician.
        rr_maxs = _extract_vitals(p_eps, 'Max RR') or _extract_vitals(p_eps, 'RR')
        max_rr = max(rr_maxs) if rr_maxs else (rr_stats.max if rr_stats else 0)
        suffix = ". Immediate review suggested." if p_type == 'very_high_rr' else "."
        return f"{label} ({date_range}): Average breathing rate {ph_hr:.0f} bpm, peak {max_rr:.0f} breaths/min{suffix}"

    else:
        return None


# ── Episode Reconciliation (FIX 33) ─────────────────────────────────────────

_HR_PHASE_TYPES = {'low_hr', 'very_low_hr', 'elevated_hr', 'high_hr', 'very_high_hr'}
_RR_PHASE_TYPES = {'elevated_rr', 'high_rr', 'very_high_rr'}

# CONDITION_TO_PHASE_TYPE moved to config.py in R16 J3 so the batch summary
# comment-template lookup can share the same mapping. Reconcile_counts uses it
# below as the dedup key (preferred phase wins over family-only matches).
from .config import CONDITION_TO_PHASE_TYPE as _CONDITION_TO_PHASE_TYPE


def reconcile_counts(episodes, display_phases):
    """Single source of truth for episode/hour counts across the report.

    Assigns each episode to AT MOST ONE display phase (R16 dedup). Selection priority:
      tier 0 — phase_type matches condition's preferred phase exactly
      tier 1 — same family (HR vs RR) as the condition
      tier 2 — anything else (cross-family fallback)
    Tiebreak within a tier: longest temporal overlap between episode duration
    and phase window.

    Without this dedup, episodes whose start_time falls inside multiple overlapping
    display phases (e.g. a Low HR phase coexisting with an Elevated RR phase over
    the same week) were counted N times, inflating display_episode_count above
    len(episodes). After dedup, sum(phase_episode_counts) == len(episodes_assigned),
    which equals len(episodes) when every episode lands in some display phase.
    """
    import pandas as pd

    total_episodes = len(episodes)
    total_hours = sum(ep.duration_hours for ep in episodes)

    # Pre-compute phase windows + family/type metadata
    phase_meta = []
    for p in display_phases:
        p_start = pd.Timestamp(p['start_date']).normalize()
        p_end = pd.Timestamp(p['end_date']).normalize() + pd.Timedelta(days=1)
        p_type = p.get('type')
        if p_type in _HR_PHASE_TYPES:
            family = 'hr'
        elif p_type in _RR_PHASE_TYPES:
            family = 'rr'
        else:
            family = None
        phase_meta.append({'start': p_start, 'end': p_end, 'type': p_type, 'family': family})

    phase_episodes = {i: [] for i in range(len(display_phases))}
    episodes_assigned = set()

    for ep_idx, e in enumerate(episodes):
        ep_start = pd.Timestamp(e.start_time)
        ep_end = pd.Timestamp(e.end_time)
        ep_pref_type = _CONDITION_TO_PHASE_TYPE.get(e.condition)
        if ep_pref_type in _HR_PHASE_TYPES:
            ep_family = 'hr'
        elif ep_pref_type in _RR_PHASE_TYPES:
            ep_family = 'rr'
        else:
            ep_family = None

        candidates = []
        for i, w in enumerate(phase_meta):
            if w['start'] <= ep_start.normalize() < w['end']:
                overlap_start = max(ep_start, w['start'])
                overlap_end = min(ep_end, w['end'])
                overlap_secs = max(0.0, (overlap_end - overlap_start).total_seconds())
                if w['type'] == ep_pref_type:
                    tier = 0
                elif w['family'] == ep_family and ep_family is not None:
                    tier = 1
                else:
                    tier = 2
                candidates.append((tier, -overlap_secs, i))

        if not candidates:
            continue

        candidates.sort()
        best_idx = candidates[0][2]
        phase_episodes[best_idx].append(e)
        episodes_assigned.add(ep_idx)

    phase_episode_counts = {i: len(phase_episodes[i]) for i in range(len(display_phases))}
    phase_hour_counts = {i: sum(e.duration_hours for e in phase_episodes[i]) for i in range(len(display_phases))}

    sum_in_phases = sum(phase_episode_counts.values())
    sum_hours_in_phases = sum(phase_hour_counts.values())

    return {
        'total_episodes': total_episodes,
        'total_hours': total_hours,
        'display_episode_count': sum_in_phases,
        'display_total_hours': sum_hours_in_phases,
        'phase_episode_counts': phase_episode_counts,
        'phase_hour_counts': phase_hour_counts,
        'phase_episodes': phase_episodes,
        'reconciled': total_episodes == sum_in_phases,
        'unassigned': [episodes[i] for i in range(len(episodes)) if i not in episodes_assigned],
    }


# ── Deterministic Narrative ──────────────────────────────────────────────────

def generate_deterministic_narrative(
    patient_id: str,
    window_start: str,
    window_end: str,
    hr_stats: VitalStats,
    rr_stats: VitalStats,
    data_quality: DataQuality,
    episodes: list[Episode],
    rollups: EpisodeRollups,
    triage: str,
    trend_assessment: str,
    action_posture: str,
    quality_warnings: list[str] | None = None,
    phases: list[dict] | None = None,
    bed_summary=None,
    activity_trend=None,
    positional_stats=None,
) -> tuple[dict, list[str]]:
    """Build narrative as a structured dict.

    Returns:
        (narrative_dict, actions)
    
    narrative_dict keys:
        'opening': str — episode count + types + trajectory summary sentence
        'phase_lines': list[str] — one per phase, rendered as bullet points
        'closing': str — coupling, spread, bed data, activity, coverage metadata
    """
    import pandas as pd


    # ── Dates ──
    try:
        window_days = compute_reporting_period_days(window_start, window_end)
    except Exception:
        window_days = data_quality.expected_hours // 24 or 7

    # ── No episodes case ──
    if rollups.total_events == 0:
        coverage_str = build_coverage_string(data_quality, positional_stats)
        opening = (
            "No episodic events exceeded the defined monitoring thresholds during this period. "
            "Vital signs remained within expected ranges throughout."
        )
        closing = f"Monitoring period: {window_days} days. {coverage_str}."
        if quality_warnings:
            closing += " " + quality_warnings[0] + "."
        return {'opening': opening, 'phase_lines': [], 'closing': closing}, []

    from .config import PHASE_LABELS

    # ── FIX 1+8: Filter to display-worthy phases only ──
    display_phases = []
    if phases:
        display_phases = [p for p in phases if PHASE_LABELS.get(p.get("type"), None) is not None]

    coupled_count = sum(1 for ep in episodes if ep.cooccurrence)

    # ── FIX 33: Reconcile episode counts — single source of truth ──
    counts = reconcile_counts(episodes, display_phases)

    if not counts['reconciled'] and counts['unassigned']:
        print(
            f"[CardioReport] ⚠️  Episode reconciliation: {counts['total_episodes']} detected vs "
            f"{counts['display_episode_count']} in phases. "
            f"{len(counts['unassigned'])} unassigned."
        )

    # R13 Fix 1: Use canonical display-episode list for types, counts, hours
    canonical_display_eps = _canonical_display_episodes(counts, episodes)
    types_set = sorted(set(_plain(ep.condition) for ep in canonical_display_eps))
    types_str = ', '.join(types_set) if types_set else 'no displayed conditions'
    # R15 B1 + B5: split into two sentences; long durations (>=72h) display with day equivalent
    # R16: headline reports total detected episodes / total hours so Section 1 matches the
    # batch summary cell (len(episodes)). The per-phase breakdown below uses the deduped
    # display_* counts. They are different scopes — headline is "detected", phase rows are
    # "events characterized by a sustained phase" — and both remain internally consistent.
    hours_display = format_hours_or_days(counts['total_hours'])
    opening = settings.episodic_burden_template.format(
        count=counts['total_episodes'],
        hours_str=hours_display,
    )
    if types_str:
        opening += " " + settings.episodic_burden_conditions_template.format(condition_list=types_str)

    if not display_phases:
        opening += " Vital signs remained within expected ranges with isolated deviations."
    
    phase_table_rows = []
    for idx, p in enumerate(display_phases):
        # FIX 33: Use reconciled episode lists — single source of truth
        p_eps = counts['phase_episodes'][idx]

        is_hr_phase = p.get('type') in ('low_hr', 'very_low_hr', 'elevated_hr', 'high_hr', 'very_high_hr')
        is_rr_phase = p.get('type') in ('elevated_rr', 'high_rr', 'very_high_rr')

        ph_hr = p.get('hr_avg', 0)
        ph_rr = p.get('rr_avg', 0)

        # R26 Fix 3 — true mean DURING the episode hours (duration-weighted mean
        # of each episode's own average), not the phase/day baseline (ph_hr/ph_rr).
        # A High-HR episode (>100 by definition) now reports an avg above 100,
        # not the ~91 day baseline.
        #
        # Restricted to episodes whose CONDITION matches this phase's type:
        # reconcile_counts assigns episodes to a phase by time-overlap, so a
        # phase's episode bag can include off-condition episodes (e.g. an
        # elevated_hr phase absorbing low-HR hours), which would drag the mean
        # below the phase's own threshold and read contradictorily ("elevated
        # HR avg 84"). Falls back to the phase day-baseline only when no
        # matching episode is present.
        from .config import CONDITION_TO_PHASE_TYPE
        p_type = p.get('type')
        def _epi_mean(attr):
            pairs = [(getattr(e, attr), e.duration_hours) for e in p_eps
                     if getattr(e, attr, None) is not None and e.duration_hours
                     and CONDITION_TO_PHASE_TYPE.get(getattr(e, 'condition', None)) == p_type]
            if not pairs:
                return None
            return sum(v * w for v, w in pairs) / sum(w for _, w in pairs)
        epi_hr_mean = _epi_mean('avg_hr')
        epi_rr_mean = _epi_mean('avg_rr')

        # Unified table — avg/min/max all sourced from THIS condition's own
        # channel only, via condition-matched structured episode fields (no
        # cross-channel 'Max' fallback). `peak` kept for back-compat. R12 Fix 5
        # HR clipping retained; R22.B keeps RR unclipped.
        if is_hr_phase:
            is_low_hr = 'low' in p.get('type', '')
            mn = _epi_minmax(p_eps, p_type, 'min_hr', min)
            mx = _epi_minmax(p_eps, p_type, 'max_hr', max)
            mn = mn if mn is not None else hr_stats.min
            mx = mx if mx is not None else hr_stats.max
            cmn, mn_clip = _clip_physiologic(mn, "hr_bpm")
            cmx, mx_clip = _clip_physiologic(mx, "hr_bpm")
            min_str = f"{cmn:.0f}{'*' if mn_clip else ''} bpm"
            max_str = f"{cmx:.0f}{'*' if mx_clip else ''} bpm"
            peak = min_str if is_low_hr else max_str
            avg_hr_val = epi_hr_mean if epi_hr_mean is not None else ph_hr
            clipped_avg, _ = _clip_physiologic(avg_hr_val, "hr_bpm")
            avg = f"{clipped_avg:.0f} bpm"
        elif is_rr_phase:
            mn = _epi_minmax(p_eps, p_type, 'min_rr', min)
            mx = _epi_minmax(p_eps, p_type, 'max_rr', max)
            mn = mn if mn is not None else rr_stats.min
            mx = mx if mx is not None else rr_stats.max
            min_str = f"{mn:.0f} brpm"
            max_str = f"{mx:.0f} brpm"
            peak = max_str
            avg_rr_val = epi_rr_mean if epi_rr_mean is not None else ph_rr
            avg = f"{avg_rr_val:.0f} brpm"
        else:
            peak = "—"
            avg = "—"
            min_str = "—"
            max_str = "—"

        # FIX 33: Use reconciled hours — matches opening sentence exactly
        total_hours = counts['phase_hour_counts'][idx]

        # Round 10 Fix 3: Compute longest continuous run for this phase
        longest_continuous = 0
        if p_eps:
            longest_continuous = max(
                (e.duration_hours for e in p_eps), default=0
            )

        d_start = pd.Timestamp(p['start_date'])
        d_end = pd.Timestamp(p['end_date'])
        # Date = the finding's FIRST episode start date (single date); Time Span =
        # that episode's clock window (clock only; end date implied by the wrap).
        first_ep = _first_condition_episode(p_eps, p_type)
        if first_ep is not None:
            date_str = pd.Timestamp(first_ep.start_time).strftime('%b %d')
            time_span = _episode_time_span(first_ep)
        else:
            date_str = d_start.strftime('%b %d')
            time_span = ""

        # R22.D — span days for the row's Episodes/day cell. Inclusive of both
        # endpoints so a single-day phase reads "1 day".
        period_days = max(1, (d_end.normalize() - d_start.normalize()).days + 1)

        phase_table_rows.append({
            'category': PHASE_LABELS.get(p.get('type'), p.get('type')),
            'phase_type': p.get('type'),
            'peak': peak,
            'min': min_str,
            'max': max_str,
            'time_span': time_span,
            'comment': _row_comment(p.get('type')),
            'longest_continuous': longest_continuous,
            'longest_continuous_str': f"{longest_continuous}h",
            'total_hours': total_hours,
            'total_hours_str': f"{total_hours}h",
            # Keep sustained_* for backward compat with any downstream readers
            'sustained_hours': total_hours,
            'sustained_str': f"{total_hours}h",
            'average': avg,
            'date': date_str,
            'episodes': counts['phase_episode_counts'][idx],
            'period_days': period_days,
            'rr_clipped': False,  # R22.B: RR no longer clipped at the physiologic ceiling.
            # R26 Fix 3 — episode-hours mean (true mean during the episode hours),
            # so the Major Findings parenthetical agrees with the events-table
            # Average column and the "Sustained [tier]" parent claim. Falls back
            # to the phase/day baseline only when no episode carries the metric.
            'phase_hr_avg': float(epi_hr_mean) if epi_hr_mean is not None
                            else (float(ph_hr) if ph_hr is not None else None),
            'phase_rr_avg': float(epi_rr_mean) if epi_rr_mean is not None
                            else (float(ph_rr) if ph_rr is not None else None),
        })

    # R18 C3: brief aggregate rows for condition types present in episodes but
    # absent from display_phases. Sajol's May 4 review flagged Wimberley
    # 90DayPeriod strip showing Low HR coloring while the events table had only
    # RR rows — those brief HR episodes never sustained long enough to form a
    # phase, so the events-table priority sort never sees them. Append a
    # "(brief)" aggregate per missing condition so strip and table reconcile.
    # The category string ends in "(brief)", which fails the PHASE_LABELS reverse
    # lookup in pdf_render's sort → priority 999 → sorts last (after real rows).
    from .config import CONDITION_TO_PHASE_TYPE
    phase_types_in_table = {p.get('type') for p in display_phases}
    brief_eps_by_phase: dict[str, list] = {}
    for ep in episodes:
        pt = CONDITION_TO_PHASE_TYPE.get(ep.condition)
        if pt and pt not in phase_types_in_table:
            brief_eps_by_phase.setdefault(pt, []).append(ep)

    for pt in sorted(brief_eps_by_phase.keys()):
        brief_eps = brief_eps_by_phase[pt]
        label = PHASE_LABELS.get(pt)
        if not label:
            continue
        b_total_hours = sum(e.duration_hours for e in brief_eps)
        b_longest = max((e.duration_hours for e in brief_eps), default=0)
        is_low = 'low' in pt
        is_rr = pt in ('elevated_rr', 'high_rr', 'very_high_rr')
        # Condition-matched structured min/max/avg (brief_eps are all the same
        # condition, so the channel is pure); emit all three.
        if is_low or not is_rr:
            mn = _epi_minmax(brief_eps, pt, 'min_hr', min)
            mx = _epi_minmax(brief_eps, pt, 'max_hr', max)
            mn = mn if mn is not None else hr_stats.min
            mx = mx if mx is not None else hr_stats.max
            cmn, mn_clip = _clip_physiologic(mn, "hr_bpm")
            cmx, mx_clip = _clip_physiologic(mx, "hr_bpm")
            b_min = f"{cmn:.0f}{'*' if mn_clip else ''} bpm"
            b_max = f"{cmx:.0f}{'*' if mx_clip else ''} bpm"
            b_peak = b_min if is_low else b_max
            hr_avgs = [e.avg_hr for e in brief_eps if e.avg_hr is not None]
            avg_val = sum(hr_avgs) / len(hr_avgs) if hr_avgs else hr_stats.mean
            cv_avg, _ = _clip_physiologic(avg_val, "hr_bpm")
            b_avg = f"{cv_avg:.0f} bpm"
        else:  # RR — R22.B: no upper-bound clipping; raw values display directly.
            mn = _epi_minmax(brief_eps, pt, 'min_rr', min)
            mx = _epi_minmax(brief_eps, pt, 'max_rr', max)
            mn = mn if mn is not None else rr_stats.min
            mx = mx if mx is not None else rr_stats.max
            b_min = f"{mn:.0f} brpm"
            b_max = f"{mx:.0f} brpm"
            b_peak = b_max
            rr_avgs = [e.avg_rr for e in brief_eps if e.avg_rr is not None]
            avg_val = sum(rr_avgs) / len(rr_avgs) if rr_avgs else rr_stats.mean
            b_avg = f"{avg_val:.0f} brpm"

        starts = sorted(pd.Timestamp(e.start_time) for e in brief_eps)
        ends = sorted(pd.Timestamp(e.end_time) for e in brief_eps)
        # Date + Time Span from the finding's FIRST episode.
        first_b = min(brief_eps, key=lambda e: pd.Timestamp(e.start_time))
        b_date = pd.Timestamp(first_b.start_time).strftime('%b %d')
        b_time_span = _episode_time_span(first_b)
        # R22.D — inclusive day span for Episodes/day rendering.
        b_period_days = max(1, (ends[-1].normalize() - starts[0].normalize()).days + 1)

        phase_table_rows.append({
            'category': f"{label} (brief)",
            'phase_type': pt,
            'peak': b_peak,
            'min': b_min,
            'max': b_max,
            'time_span': b_time_span,
            'comment': _row_comment(pt),
            'longest_continuous': b_longest,
            'longest_continuous_str': f"{b_longest}h",
            'total_hours': b_total_hours,
            'total_hours_str': f"{b_total_hours}h",
            'sustained_hours': b_total_hours,
            'sustained_str': f"{b_total_hours}h",
            'average': b_avg,
            'date': b_date,
            'episodes': len(brief_eps),
            'period_days': b_period_days,
            'rr_clipped': False,
            # R18 N2: tag with underlying phase_type so pdf_render can rank brief
            # rows by clinical priority and surface the most alarming one.
            'brief_phase_type': pt,
            # R23.A — condition-window mean from the brief episode set itself.
            # Stored under the metric that matches phase type so Major Findings
            # can pull the value consistent with the parent claim.
            'phase_hr_avg': float(avg_val) if not is_rr else None,
            'phase_rr_avg': float(avg_val) if is_rr else None,
        })

    # ── FIX 1: Part 3 — Closing ──
    closing_parts = []

    # R13 Fix 5: HR spread — use unified gate shared with chart and pattern obs.
    # A2: bounds/spread strings come from the single format_spread_bounds helper
    # so page 1, page 2, and the histogram annotation are byte-identical.
    _sample_hours = data_quality.total_hours if hasattr(data_quality, 'total_hours') else (data_quality.get('total_hours', 0) if isinstance(data_quality, dict) else 0)
    if should_render_spread_annotation(int(_sample_hours), hr_stats.p5, hr_stats.p95):
        _sb = format_spread_bounds(hr_stats.p5, hr_stats.p95)
        closing_parts.append(
            f"The P5 to P95 heart rate spread was {_sb['text']}, "
            f"indicating significant cardiac variability."
        )

    # Coupling
    if coupled_count:
        closing_parts.append(
            f"{coupled_count} episodes showed concurrent low heart rate with elevated "
            "breathing rate, a coupling pattern that may reflect compensatory respiratory stress."
        )

    # Bed-specific narrative
    if bed_summary is not None:
        if bed_summary.days_above_16h > 0:
            closing_parts.append(
                f"Time in bed exceeded 16 hours on {bed_summary.days_above_16h} days."
            )
        if bed_summary.alert_days > 0:
            closing_parts.append(
                f"Low heart rate alerts occurred on {bed_summary.alert_days} separate days "
                f"({bed_summary.total_alerts} total alerts)."
            )
        if bed_summary.hr_min_high_bed_days > 0 and bed_summary.hr_min_normal_days > 0:
            diff = bed_summary.hr_min_normal_days - bed_summary.hr_min_high_bed_days
            if abs(diff) > 1:
                closing_parts.append(
                    f"Average heart rate minimum on high-bed-time days was "
                    f"{bed_summary.hr_min_high_bed_days:.0f} bpm versus "
                    f"{bed_summary.hr_min_normal_days:.0f} bpm on normal days."
                )

    # Activity Trend Connection
    if activity_trend and activity_trend.days:
        try:
            first_h = activity_trend.days[0].hours
            last_h = activity_trend.days[-1].hours
            if first_h > 0:
                decline_pct = ((first_h - last_h) / first_h) * 100
                if decline_pct >= settings.coverage_decline_threshold_pct:
                    closing_parts.append(
                        f"Daily recorded hours declined from {first_h:.1f} to "
                        f"{last_h:.1f} ({decline_pct:.0f}%) over the monitoring period."
                    )
        except Exception:
            pass


    # Data quality warnings at very end
    if quality_warnings:
        closing_parts.append(quality_warnings[0] + ".")

    closing = " ".join(closing_parts)

    # FIX 36: Specific action posture based on actual findings
    specific_action = build_specific_action_posture(
        episodes, display_phases, triage, counts
    )

    # R16 L1: Events-table row 1 phase type — same priority sort that pdf_render
    # applies to phase_table_rows. Stored in narrative dict so the batch summary
    # Comments column reads from the same canonical "what does the clinician see
    # as the headline finding?" answer. Replaces the K1 priority-tier-only rule
    # that surfaced brief peaks (e.g. PHolst FP very_high_hr from a transient
    # excursion) over sustained patterns (his 86h Low HR burden).
    events_table_row_1_phase_type = _events_table_row_1_phase_type(phase_table_rows)

    # R23.A — condition-window avg for the Major Findings parenthetical.
    # The avg X in "Sustained [tier] (avg X, peak Y)" must reflect samples during
    # the highest-burden tier's episode windows, not the overall vital mean —
    # otherwise PHolst FP reads "Sustained very high HR (avg 55, peak 119)" where
    # avg 55 is a low-range value that contradicts the parent claim. Pull the raw
    # phase avg from the matching phase_table_row.
    findings_hr_avg = None
    findings_rr_avg = None
    if events_table_row_1_phase_type and phase_table_rows:
        # R26 Fix 2/3 — read the avg from the MAX-BURDEN row of the dominant
        # phase type, not the first row of that type. A long FullPeriod yields
        # several phases of the same type; the headline must report the avg of
        # the dominant (largest) one — JB's 339h Low-HR run (avg 44), not a
        # smaller 76h Low-HR row (avg 49).
        same_type = [r for r in phase_table_rows
                     if r.get('phase_type') == events_table_row_1_phase_type]
        dominant_row = max(
            same_type,
            key=lambda r: r.get('total_hours', r.get('sustained_hours', 0)) or 0,
            default=None,
        )
        if dominant_row is not None:
            findings_hr_avg = dominant_row.get('phase_hr_avg')
            findings_rr_avg = dominant_row.get('phase_rr_avg')

    # A1 — one row per detected episode for the rendered events tables. Kept
    # separate from phase_table_rows (the per-condition aggregate that still
    # feeds the burden rollup, headline, actions, strip numbering, and batch
    # summary). Only the Major Findings and Last-24h tables consume this list.
    episode_table_rows = build_episode_table_rows(
        episodes, hr_stats, rr_stats, activity_trend=activity_trend
    )

    narrative_dict = {
        'opening': opening,
        'phase_table_rows': phase_table_rows,
        'episode_table_rows': episode_table_rows,
        'closing': closing,
        'trend': trend_assessment,
        'action_posture': action_posture,
        'specific_action': specific_action,
        'counts': counts,
        'events_table_row_1_phase_type': events_table_row_1_phase_type,
        'findings_hr_avg': findings_hr_avg,
        'findings_rr_avg': findings_rr_avg,
    }

    # ── FIX 2: Build phase-aware actions with per-phase vitals ──
    actions = _build_phase_actions(
        episodes, phases, hr_stats, rr_stats, coupled_count > 0,
        bed_summary=bed_summary,
        activity_trend=activity_trend,
        phase_table_rows=phase_table_rows,
    )

    return narrative_dict, actions


def _extract_vitals(episodes: list[Episode], prefix: str) -> list[float]:
    """Extract numeric values from key_vitals strings matching a prefix."""
    values = []
    for e in episodes:
        try:
            # key_vitals format: "HR avg 55 | Min HR 45 | Max RR 28"
            for part in e.key_vitals.split("|"):
                stripped = part.strip()
                if prefix.lower() in stripped.lower():
                    # Handle both "Min HR" and "Min"
                    v = stripped.split()[-1]
                    values.append(float(v))
        except Exception:
            pass
    return values


def _build_phase_actions(
    episodes: list[Episode],
    phases: list[dict] | None,
    hr_stats: VitalStats,
    rr_stats: VitalStats,
    has_coupling: bool,
    bed_summary=None,
    activity_trend=None,
    phase_table_rows: list[dict] | None = None,
) -> list[str]:
    """Clinical action bullets — ONE per condition type, valued from the same
    phase_table_rows the events table renders (so action numbers == table
    numbers), own-channel only (no RR on a heart-rate bullet), with the vetted
    condition-keyed review phrase.
    """
    import pandas as pd
    from .config import PHASE_LABELS

    if not episodes:
        return []

    phase_table_rows = phase_table_rows or []

    # Follow-up Fix 1 — the action cap is CONDITION AWARE, not a flat count.
    # `condition_actions` is the floor: one primary action per condition present
    # in the window. The floor is NEVER trimmed — a flat count cap could silently
    # drop a whole condition's guidance on a multi-condition patient (e.g. lose
    # S (Chair)'s Low-HR "review beta blockers and AV-node agents; ECG if
    # symptomatic" line). Only `optional_actions` (trajectory / bed / activity)
    # are subject to the count budget, trimmed first. If the floor alone exceeds
    # the budget, the floor wins and the block overflows — that surfaces as a
    # page-budget question, never as dropped clinical content.
    condition_actions: list[str] = []
    optional_actions: list[str] = []

    # FIX 2/3/4 — one bullet per condition TYPE, from the type's highest-burden
    # row (the row the table headlines for that type). Values come straight from
    # the row, so the action and the table never disagree; the row's avg/min/max
    # are condition-matched (own channel), so an HR bullet never shows RR.
    by_type: dict[str, dict] = {}
    for r in phase_table_rows:
        pt = r.get('phase_type')
        if not pt:
            continue
        cur = by_type.get(pt)
        if cur is None or (r.get('total_hours', 0) or 0) > (cur.get('total_hours', 0) or 0):
            by_type[pt] = r
    # Order condition actions by clinical severity tier (most concerning first)
    # so display order matches the events table; since the floor keeps ALL of
    # them, none is ever at risk of being trimmed.
    from .config import RENDER_CONFIG as _RC
    _priority = _RC.get("events_table", {}).get("priority_order", [])

    def _sev_rank(pt):
        return _priority.index(pt) if pt in _priority else 999
    for r in sorted(by_type.values(),
                    key=lambda x: (_sev_rank(x.get('phase_type')),
                                   -(x.get('total_hours', 0) or 0))):
        pt = r['phase_type']
        label = PHASE_LABELS.get(pt, pt)
        is_low = 'low' in pt
        avg = (r.get('average') or '').strip()
        trig = ((r.get('min') if is_low else r.get('max')) or '').strip()
        trig_word = 'min' if is_low else 'peak'
        phrase = (r.get('comment') or '').strip()
        detail = []
        if avg:
            detail.append(f"avg {avg}")
        if trig:
            detail.append(f"{trig_word} {trig}")
        stat = f" ({', '.join(detail)})" if detail else ""
        condition_actions.append(f"{label}{stat}: {phrase}".strip())

    # Overall trajectory action if multiple display phases (OPTIONAL)
    if phases:
        from .config import PHASE_LABELS as _PL
        disp = [p for p in phases if _PL.get(p.get("type"), None) is not None]
        if len(disp) >= 2:
            hr_spread = hr_stats.p95 - hr_stats.p5
            optional_actions.append(
                f"Overall trajectory shows {len(disp)} unstable phases with "
                f"{hr_spread:.0f} bpm variability. Consider increasing monitoring "
                "frequency and lowering escalation threshold."
            )

    # ── Bed-specific actions (OPTIONAL) ──
    if bed_summary is not None:
        if bed_summary.days_above_16h > 0:
            optional_actions.append(
                f"Time in bed exceeded 16 hours on {bed_summary.days_above_16h} days. "
                "Consider evaluating prolonged recumbency and repositioning schedule."
            )
        if bed_summary.alert_days > 0:
            optional_actions.append(
                f"Low heart rate alerts on {bed_summary.alert_days} days may correlate with "
                "prolonged recumbency. Evaluate for position-dependent low heart rate."
            )
        if (bed_summary.hr_min_high_bed_days > 0 and bed_summary.hr_min_normal_days > 0
                and abs(bed_summary.hr_min_normal_days - bed_summary.hr_min_high_bed_days) > 1):
            optional_actions.append(
                f"Heart rate minimum averaged {bed_summary.hr_min_high_bed_days:.0f} bpm on "
                f"high-bed-time days versus {bed_summary.hr_min_normal_days:.0f} bpm on normal "
                "days. Compare with positional hemodynamic assessment."
            )

    # ── Activity Trend Connection (OPTIONAL) ──
    if activity_trend and activity_trend.days:
        first_h = activity_trend.days[0].hours
        last_h = activity_trend.days[-1].hours
        if first_h > 0:
            decline_pct = ((first_h - last_h) / first_h) * 100
            if decline_pct >= settings.coverage_decline_threshold_pct:
                optional_actions.append(
                    f"Daily recorded hours declined from {first_h:.1f} to "
                    f"{last_h:.1f} ({decline_pct:.0f}%) over the monitoring period."
                )

    # Deduplicate across both lists, preserving the floor.
    seen = set()

    def _dedup(items):
        out = []
        for a in items:
            if a not in seen:
                seen.add(a)
                out.append(a)
        return out
    condition_actions = _dedup(condition_actions)
    optional_actions = _dedup(optional_actions)

    # Fallback if no condition/optional actions at all.
    if not condition_actions and not optional_actions:
        if episodes:
            return ["If event frequency or duration is increasing over days: consider earlier "
                    "provider notification, even without overt symptoms."]
        return ["No specific clinical actions indicated; continue routine monitoring per protocol."]

    # Condition floor is untrimmable; optional items fill the remaining budget.
    # If the floor alone exceeds max_actions, keep the whole floor anyway.
    budget = max(len(condition_actions), settings.max_actions)
    return (condition_actions + optional_actions)[:budget]

# ── FIX 35: Trajectory Comparison ────────────────────────────────────────────


def classify_trajectory_direction(delta_episodes, delta_hours):
    """Classify trajectory direction requiring agreement between episode count and hours.

    Returns: (direction, magnitude)
        direction: 'worsening' | 'improving' | 'stable' | 'mixed'
        magnitude: 'significant' | 'moderate' | 'minimal'
    """
    EPISODE_THRESHOLD_MODERATE = 5
    EPISODE_THRESHOLD_SIGNIFICANT = 10
    HOURS_THRESHOLD_MODERATE = 10
    HOURS_THRESHOLD_SIGNIFICANT = 20

    def _metric_direction(delta, thresh):
        if delta >= thresh:
            return 'worsening'
        elif delta <= -thresh:
            return 'improving'
        return 'stable'

    ep_dir = _metric_direction(delta_episodes, EPISODE_THRESHOLD_MODERATE)
    hr_dir = _metric_direction(delta_hours, HOURS_THRESHOLD_MODERATE)

    if ep_dir == hr_dir:
        direction = ep_dir
    elif ep_dir == 'stable' or hr_dir == 'stable':
        # One metric stable, one directional — partial signal, call it mixed
        direction = 'mixed'
    else:
        # One up, one down — genuinely contradictory
        direction = 'mixed'

    if direction == 'worsening':
        if delta_episodes >= EPISODE_THRESHOLD_SIGNIFICANT or delta_hours >= HOURS_THRESHOLD_SIGNIFICANT:
            magnitude = 'significant'
        else:
            magnitude = 'moderate'
    elif direction == 'improving':
        if delta_episodes <= -EPISODE_THRESHOLD_SIGNIFICANT or delta_hours <= -HOURS_THRESHOLD_SIGNIFICANT:
            magnitude = 'significant'
        else:
            magnitude = 'moderate'
    elif direction == 'mixed':
        magnitude = 'moderate'
    else:
        magnitude = 'minimal'

    return direction, magnitude


def compute_trajectory(full_df, window_start, window_end, report_type='CriticalWeek'):
    """Compare two periods to determine trajectory direction.

    For CriticalWeek: compare current window to the prior window of equal length.
    For FullPeriod: compare first third of window to last third of window.

    Returns trajectory dict or None if insufficient data.
    """
    import pandas as pd

    from .episodes import detect_episodes

    ws = pd.Timestamp(window_start).normalize()
    we = pd.Timestamp(window_end).normalize()
    window_days = (we - ws).days + 1

    if report_type == 'FullPeriod':
        # Split the full period into thirds — compare first vs last
        third_days = max(7, window_days // 3)

        prior_start = ws
        prior_end = ws + pd.Timedelta(days=third_days - 1)

        current_start = we - pd.Timedelta(days=third_days - 1)
        current_end = we
    elif report_type == '90DayPeriod':
        # R17 D: prefer "prior 90 days vs this 90 days" if full_df has ≥90 days
        # of data BEFORE the active window. Else fall back to "first 30 days
        # vs last 30 days within the active window."
        candidate_prior_end = ws - pd.Timedelta(days=1)
        candidate_prior_start = candidate_prior_end - pd.Timedelta(days=window_days - 1)
        full_df_min = full_df['timestamp'].min().normalize()
        if candidate_prior_start >= full_df_min:
            # Enough history before the active window for a 90-vs-90 comparison
            prior_start = candidate_prior_start
            prior_end = candidate_prior_end
            current_start = ws
            current_end = we
        else:
            # Not enough history — first 30 vs last 30 within the active window
            chunk_days = 30
            prior_start = ws
            prior_end = ws + pd.Timedelta(days=chunk_days - 1)
            current_start = we - pd.Timedelta(days=chunk_days - 1)
            current_end = we
    elif report_type == '30DayPeriod':
        # R24.3 — mirrors the 90DayPeriod logic with a 10-day comparison chunk
        # so the prior-vs-current split fits inside a 30-day window. When the
        # patient has ≥30 days of history before the active window, do a
        # 30-vs-30 prior comparison; otherwise split the active window into
        # first 10 vs last 10 days.
        candidate_prior_end = ws - pd.Timedelta(days=1)
        candidate_prior_start = candidate_prior_end - pd.Timedelta(days=window_days - 1)
        full_df_min = full_df['timestamp'].min().normalize()
        if candidate_prior_start >= full_df_min:
            prior_start = candidate_prior_start
            prior_end = candidate_prior_end
            current_start = ws
            current_end = we
        else:
            chunk_days = 10
            prior_start = ws
            prior_end = ws + pd.Timedelta(days=chunk_days - 1)
            current_start = we - pd.Timedelta(days=chunk_days - 1)
            current_end = we
    else:
        # CriticalWeek: look for prior window before the current window
        prior_end = ws - pd.Timedelta(days=1)
        prior_start = prior_end - pd.Timedelta(days=window_days - 1)

        current_start = ws
        current_end = we

    prior_df = full_df[
        (full_df['timestamp'] >= prior_start) &
        (full_df['timestamp'] <= prior_end + pd.Timedelta(hours=23))
    ]
    current_df = full_df[
        (full_df['timestamp'] >= current_start) &
        (full_df['timestamp'] <= current_end + pd.Timedelta(hours=23))
    ]

    # Need minimum data in both periods
    if len(prior_df) < 24 or len(current_df) < 24:
        return None

    # Round 10 Fix 2: Coverage guard — suppress misleading trajectory when
    # prior window has near-zero coverage
    traj_cfg = RENDER_CONFIG["trajectory"]
    prior_days = (prior_end - prior_start).days + 1
    prior_expected_hours = prior_days * 24
    prior_recorded_hours = len(prior_df)
    prior_coverage_pct = (prior_recorded_hours / max(prior_expected_hours, 1)) * 100

    if (prior_coverage_pct < traj_cfg["min_prior_coverage_pct"] or
            prior_recorded_hours < traj_cfg["min_prior_hours_absolute"]):
        return {
            'direction': 'insufficient',
            'magnitude': 'minimal',
            'current': {'episode_count': 0, 'episode_hours': 0, 'hr_avg': 0, 'coupled_count': 0},
            'prior': {'episode_count': 0, 'episode_hours': 0, 'hr_avg': 0, 'coupled_count': 0},
            'delta_episodes': 0,
            'delta_hours': 0,
            'prior_window': (prior_start, prior_end),
            'current_window': (current_start, current_end),
            'report_type': report_type,
            'insufficient': True,
            'insufficient_text': traj_cfg["insufficient_text"].format(
                pct=traj_cfg["min_prior_coverage_pct"]
            ),
        }

    # R16: Detect episodes once on full_df, then filter by start_time.
    # Re-running detect_episodes on small subsets fragments episodes at window
    # boundaries (the merge gap can't span across the cut), inflating counts.
    # That inflation produced TMiller's "17 → 95" trajectory on a window whose
    # batch summary total was 72 (95 alone exceeded the period total).
    all_full_eps = detect_episodes(full_df)

    def _eps_in_window(eps, w_start, w_end):
        w_end_inclusive = w_end + pd.Timedelta(hours=23)
        return [
            e for e in eps
            if w_start <= pd.Timestamp(e.start_time) <= w_end_inclusive
        ]

    prior_eps = _eps_in_window(all_full_eps, prior_start, prior_end)
    current_eps = _eps_in_window(all_full_eps, current_start, current_end)

    # R11 Fix 3: Zero prior episodes guard — "0 → N" is mathematically undefined
    if len(prior_eps) == 0:
        return {
            'direction': 'insufficient',
            'magnitude': 'minimal',
            'current': {'episode_count': len(current_eps), 'episode_hours': sum(e.duration_hours for e in current_eps), 'hr_avg': 0, 'coupled_count': 0},
            'prior': {'episode_count': 0, 'episode_hours': 0, 'hr_avg': 0, 'coupled_count': 0},
            'delta_episodes': 0,
            'delta_hours': 0,
            'prior_window': (prior_start, prior_end),
            'current_window': (current_start, current_end),
            'report_type': report_type,
            'insufficient': True,
            'insufficient_text': "Trajectory: first reporting period with detected episodes; no prior comparison available.",
        }

    current_metrics = {
        'episode_count': len(current_eps),
        'episode_hours': sum(e.duration_hours for e in current_eps),
        'hr_avg': current_df['hr_avg'].mean() if not current_df.empty else 0,
        'coupled_count': sum(1 for e in current_eps if e.cooccurrence),
    }
    prior_metrics = {
        'episode_count': len(prior_eps),
        'episode_hours': sum(e.duration_hours for e in prior_eps),
        'hr_avg': prior_df['hr_avg'].mean() if not prior_df.empty else 0,
        'coupled_count': sum(1 for e in prior_eps if e.cooccurrence),
    }

    delta_episodes = current_metrics['episode_count'] - prior_metrics['episode_count']
    delta_hours = current_metrics['episode_hours'] - prior_metrics['episode_hours']

    direction, magnitude = classify_trajectory_direction(delta_episodes, delta_hours)

    return {
        'direction': direction,
        'magnitude': magnitude,
        'current': current_metrics,
        'prior': prior_metrics,
        'delta_episodes': delta_episodes,
        'delta_hours': delta_hours,
        'prior_window': (prior_start, prior_end),
        'current_window': (current_start, current_end),
        'report_type': report_type,
    }


def build_trajectory_line(trajectory):
    if trajectory is None:
        return "<i>Insufficient data for trajectory comparison.</i>"

    # Round 10 Fix 2: Show coverage-based insufficient message
    if trajectory.get('insufficient'):
        return f"<i>{trajectory['insufficient_text']}</i>"

    direction = trajectory['direction']
    prior_eps = trajectory['prior']['episode_count']
    current_eps = trajectory['current']['episode_count']

    # Format summary using config templates — no hours, sentence form
    if trajectory.get('report_type') == 'FullPeriod':
        prior_days = (trajectory['prior_window'][1] - trajectory['prior_window'][0]).days + 1
        summary = settings.trajectory_line_template_fullperiod.format(
            early_count=prior_eps, late_count=current_eps, window_days=prior_days,
        )
    elif trajectory.get('report_type') == '90DayPeriod':
        # R17 D: 90DayPeriod uses the with-prior template when the active window
        # is the entire prior_window length (90 days); within-window template
        # otherwise (first 30 vs last 30 days inside the active window).
        prior_days = (trajectory['prior_window'][1] - trajectory['prior_window'][0]).days + 1
        if prior_days >= 60:  # ≥60 days indicates the prior-90 path was taken
            prior_start = trajectory['prior_window'][0].strftime('%b %d')
            prior_end = trajectory['prior_window'][1].strftime('%b %d')
            current_start = trajectory['current_window'][0].strftime('%b %d')
            current_end = trajectory['current_window'][1].strftime('%b %d')
            summary = settings.trajectory_line_template_90day_with_prior.format(
                early_count=prior_eps, late_count=current_eps,
                prior_start=prior_start, prior_end=prior_end,
                current_start=current_start, current_end=current_end,
            )
        else:
            summary = settings.trajectory_line_template_90day_within_window.format(
                early_count=prior_eps, late_count=current_eps,
            )
    else:
        # R15 B4: explicit prior AND current window dates on CriticalWeek
        prior_start = trajectory['prior_window'][0].strftime('%b %d')
        prior_end = trajectory['prior_window'][1].strftime('%b %d')
        current_start = trajectory['current_window'][0].strftime('%b %d')
        current_end = trajectory['current_window'][1].strftime('%b %d')
        summary = settings.trajectory_line_template_criticalweek.format(
            early_count=prior_eps, late_count=current_eps,
            prior_start=prior_start, prior_end=prior_end,
            current_start=current_start, current_end=current_end,
        )

    # R15 B3: Append ratio multiplier for non-stable trajectories with non-zero prior
    ratio_suffix = ""
    if prior_eps > 0:
        ratio = current_eps / prior_eps
        stable_band = settings.trajectory_ratio_threshold_stable  # e.g. 1.1 → ±10% from 1.0
        deviation = abs(ratio - 1.0)
        if deviation >= (stable_band - 1.0):
            if ratio >= 1.0:
                ratio_suffix = settings.trajectory_ratio_template_increase.format(ratio=ratio)
            else:
                # For decreases, show as e.g. "0.5x decrease" using the same ratio (<1)
                ratio_suffix = settings.trajectory_ratio_template_decrease.format(ratio=ratio)

    if direction == 'worsening':
        color = settings.color_episode_red
        arrow = "↑"
    elif direction == 'improving':
        # R18 E: down arrow renders red regardless of clinical direction. Sajol's
        # preference per May 4 feedback — color tracks "any change," not direction.
        color = settings.color_episode_red
        arrow = "↓"
    elif direction == 'mixed':
        color = "#D4850A"
        arrow = "↔"
    else:  # stable
        color = "#D4850A"
        arrow = "→"

    return (
        f"<font color='{color}'><b>Trajectory {arrow}:</b></font> "
        f"{summary}{ratio_suffix}"
    )

# ── FIX 36: Specific Action Posture ─────────────────────────────────────────

def _aggregate_by_condition(eps):
    """R12 Fix 2: Aggregate episode list by condition — single source of truth."""
    from collections import defaultdict
    by_cond = defaultdict(lambda: {'count': 0, 'total_hours': 0})
    for e in eps:
        cond = _plain(e.condition)
        by_cond[cond]['count'] += 1
        by_cond[cond]['total_hours'] += int(e.duration_hours)
    return dict(by_cond)


def should_render_spread_annotation(sample_hours: int, p5: float, p95: float, metric: str = "hr") -> bool:
    """R13 Fix 5 + R19 B: Single source of truth for spread annotation rendering.

    Called by chart, body text, and pattern observation paths. R19 B added a
    `metric` parameter — HR uses 20 bpm threshold, RR uses 10 brpm. Without the
    metric distinction, RR spread observations almost never triggered (typical
    P5-P95 sits at 8-12 brpm).
    """
    sa = RENDER_CONFIG.get("spread_annotation", {})
    spread = p95 - p5
    by_metric = sa.get("min_spread_by_metric", {})
    if metric in by_metric:
        min_spread = by_metric[metric]
    else:
        min_spread = sa.get("min_spread_bpm", 20)
    min_sample = sa.get("min_sample_hours", 168)
    return spread >= min_spread and sample_hours >= min_sample


def _canonical_display_episodes(counts, fallback_eps):
    """R13 Fix 1: Return the single canonical display-episode list.

    This is the flattened view of `counts['phase_episodes']` — the SAME
    episodes that render into the events table. Used by opening sentence,
    events table, and clinical guidance. No consumer may use raw eps.
    """
    if counts and isinstance(counts, dict):
        phase_eps_map = counts.get('phase_episodes')
        if phase_eps_map is not None:
            flat = []
            for _idx, ep_list in sorted(phase_eps_map.items()):
                flat.extend(ep_list)
            return flat
    return list(fallback_eps) if fallback_eps else []


def build_specific_action_posture(eps, phases, triage, counts, trajectory=None):
    """
    R12 Fix 2 + R13 Fix 1: Guidance aggregates over the SAME canonical list
    that renders the events table (counts['phase_episodes']).
    """
    triage_str = str(triage).upper().strip()

    # R13 Fix 1: Use canonical display list, not raw episode list
    canonical_eps = _canonical_display_episodes(counts, eps)

    if triage_str == 'GREEN' or not canonical_eps:
        return "Routine monitoring. No specific intervention indicated."

    cg = RENDER_CONFIG["clinical_guidance"]
    dominance_threshold = cg.get("dominance_threshold", 0.60)
    mixed_templates = cg.get("mixed_templates", {})

    # Aggregate by condition from the canonical list that renders the table
    by_cond = _aggregate_by_condition(canonical_eps)
    total_hours_all = sum(b['total_hours'] for b in by_cond.values())
    total_count_all = sum(b['count'] for b in by_cond.values())

    if total_hours_all == 0 or not by_cond:
        return mixed_templates.get(triage_str, "Closer observation suggested.")

    dominant_cond, dominant_stats = max(by_cond.items(), key=lambda kv: kv[1]['total_hours'])
    dominance_ratio = dominant_stats['total_hours'] / total_hours_all

    # Mixed template path: no single condition dominates
    if dominance_ratio < dominance_threshold:
        template = mixed_templates.get(triage_str, "Closer observation suggested.")
        return template.format(count=total_count_all, hours=total_hours_all)

    # Single-condition template path: use dominant condition's own counts
    # R13 Fix 1: Use canonical_eps for all downstream calculations
    longest_episode = max(canonical_eps, key=lambda e: e.duration_hours)
    coupled_count = sum(1 for e in canonical_eps if e.cooccurrence)
    # R12: counts scoped to dominant condition for interpolation
    total_episodes = dominant_stats['count']
    total_episode_hours = dominant_stats['total_hours']

    # R12 Fix 2: Only fire condition-specific templates when that condition is dominant.
    # Otherwise the named condition and the interpolated counts will mismatch.
    dom_is_low_hr = dominant_cond in ("Low Heart Rate", "Very Low Heart Rate")
    dom_is_high_hr = dominant_cond in ("High Heart Rate", "Very High Heart Rate", "Elevated Heart Rate")
    # R15 A2: any of three breathing tiers counts as "breathing-dominant"
    dom_is_breathing = dominant_cond in ("Elevated Breathing", "High Breathing", "Very High Breathing")
    dom_is_very_low = dominant_cond == "Very Low Heart Rate"

    has_sustained_low_hr = dom_is_low_hr and any(
        e.condition in (Conditions.BRADYCARDIAC, Conditions.SEVERE_BRADY) and e.duration_hours >= 4
        for e in canonical_eps
    )
    has_sustained_high_hr = dom_is_high_hr and any(
        e.condition in (Conditions.TACHYCARDIA, Conditions.VERY_HIGH_HR) and e.duration_hours >= 4
        for e in canonical_eps
    )
    has_sustained_breathing = dom_is_breathing and any(
        e.condition in (Conditions.TACHYPNEA, Conditions.HIGH_RR, Conditions.VERY_HIGH_RR)
        and e.duration_hours >= 4
        for e in canonical_eps
    )
    has_very_low_sustained = dom_is_very_low and any(
        e.condition == Conditions.SEVERE_BRADY and e.duration_hours >= 6
        for e in canonical_eps
    )

    if triage_str == 'RED':
        if has_very_low_sustained and coupled_count > 0:
            return (
                f"Urgent: Sustained very low heart rate with concurrent breathing "
                f"abnormality across {coupled_count} episode(s). "
                f"Total episodic burden: {total_episode_hours}h. "
                f"Immediate provider review and medication reconciliation advised."
            )
        elif has_very_low_sustained:
            return (
                f"Urgent: Persistent very low heart rate pattern with "
                f"{longest_episode.duration_hours}h longest sustained event. "
                f"Total episodic burden: {total_episode_hours}h across {total_episodes} events. "
                f"Provider review and medication assessment advised."
            )
        elif has_sustained_high_hr and has_sustained_breathing:
            return (
                f"Urgent: Concurrent sustained elevated heart rate and breathing. "
                f"Total episodic burden: {total_episode_hours}h. "
                f"Evaluate for infection, fluid overload, or respiratory compromise. "
                f"Provider review advised."
            )
        elif has_sustained_low_hr:
            return (
                f"Urgent: Recurrent sustained low heart rate pattern "
                f"({total_episodes} episodes, {total_episode_hours}h total). "
                f"Provider review, medication reconciliation, and symptom assessment advised."
            )
        elif has_sustained_high_hr:
            return (
                f"Urgent: Sustained elevated heart rate pattern "
                f"({total_episodes} episodes, {total_episode_hours}h total). "
                f"Assess for infection, pain, hydration, cardiac workup advised."
            )
        else:
            return (
                f"Urgent: High episodic burden detected "
                f"({total_episodes} events, {total_episode_hours}h). "
                f"Provider review advised within 24 hours."
            )
    
    if triage_str == 'YELLOW':
        if has_very_low_sustained and coupled_count > 0:
            return (
                f"Sustained very low heart rate with concurrent breathing abnormality "
                f"({coupled_count} coupled episode(s)). "
                f"Suggest provider review within 24 hours and medication assessment."
            )
        elif has_very_low_sustained:
            return (
                f"Sustained very low heart rate detected ({longest_episode.duration_hours}h duration). "
                f"Review heart rate lowering medications and consider provider consultation."
            )
        elif has_sustained_low_hr and coupled_count > 0:
            return (
                f"Recurrent low heart rate episodes with concurrent breathing changes. "
                f"Closer observation and medication review suggested."
            )
        elif has_sustained_low_hr:
            return (
                f"Recurrent low heart rate episodes ({total_episodes} events). "
                f"Review medication timing and assess patient symptoms."
            )
        elif has_sustained_high_hr and has_sustained_breathing:
            return (
                f"Concurrent elevated heart rate and breathing pattern. "
                f"Evaluate for infection, fluid status, or respiratory compromise."
            )
        elif has_sustained_high_hr:
            return (
                f"Sustained elevated heart rate ({longest_episode.duration_hours}h duration). "
                f"Assess for pain, infection, hydration, or activity correlation."
            )
        elif has_sustained_breathing:
            return (
                f"Sustained elevated breathing pattern. "
                f"Assess respiratory status and consider underlying cause."
            )
        # YELLOW FALLBACK — Round 10 Fix 6: run through specificity gate
        fallback = (
            f"Episodic events detected ({total_episodes} events, {int(total_episode_hours)}h total). "
            f"Closer clinical observation suggested."
        )
        return _validate_guidance(fallback, eps, counts)

    # Should never reach here — all triage levels handled above
    return _validate_guidance(
        f"Episodic events detected. Clinical review suggested ({total_episodes} events).",
        eps, counts
    )


def _validate_guidance(text, eps, counts=None):
    """R10 Fix 6 + R11 Fix 1 + R13 Fix 1: Specificity gate.

    R13: Use canonical display-episode list (counts['phase_episodes'] flattened)
    as the single source of truth for dominant condition AND counts.
    """
    import re
    cg = RENDER_CONFIG["clinical_guidance"]
    condition_names = list(CONDITION_DISPLAY.values())

    tokens_present = {
        "condition": any(c.lower() in text.lower() for c in condition_names),
        "count_or_duration": bool(re.search(r"\d+\s*(events?|h\b|hours?)", text)),
        "suggested_assessment": any(kw in text.lower() for kw in
                                    ["assess", "evaluate", "review", "correlate", "consider"]),
    }

    if sum(tokens_present.values()) >= cg["min_specificity_tokens"]:
        return text

    # R13 Fix 1: Always aggregate from canonical display list
    canonical = _canonical_display_episodes(counts, eps)
    if not canonical:
        return text
    by_cond = _aggregate_by_condition(canonical)
    if not by_cond:
        return text
    # Use dominant condition's own bucket for count + hours
    dominant_cond_name, dom_stats = max(by_cond.items(), key=lambda kv: kv[1]['total_hours'])
    assessment_focus = cg["assessment_focus_by_condition"].get(
        dominant_cond_name, "clinical context and patient symptoms"
    )
    display_count = dom_stats['count']
    display_hours = dom_stats['total_hours']
    return cg["fallback_template"].format(
        count=display_count,
        condition=dominant_cond_name.lower(),
        hours=display_hours,
        assessment_focus=assessment_focus,
    )


# ── LLM Narrative ────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a cardiology decision support writer. You summarize longitudinal heart rate "
    "and respiratory rate trend data from contactless radar based monitoring sensors for clinicians. "
    "You must not diagnose. You must use cautious language: 'may indicate', 'is suggested for clinical "
    "correlation', 'interpret in clinical context'. Never make definitive diagnostic claims. "
    "CRITICAL RULES:\n"
    "- Use plain language ONLY and STRICTLY avoid clinical diagnostic terminology. "
    "Say 'low heart rate' internally. Say 'high heart rate' internally. "
    "Say 'elevated breathing rate' directly. "
    "No 'rhythm instability' (say 'heart rate variability'). No 'cardiorespiratory coupling' (say 'concurrent low heart rate and elevated breathing rate'). No 'burden' or 'arrhythmia'.\n"
    "- NEVER use the word 'warranted'. Use 'suggested' or 'is suggested' instead.\n"
    "- NEVER recommend specific diagnostic tests, procedures, medications, or treatment changes. "
    "Use language like 'further evaluation is suggested' or 'may benefit from additional clinical assessment' instead of naming specific tests.\n"
    "- NEVER use hyphens (the - character) in your output. Use 'to' for ranges, "
    "spaces for compound words.\n"
    "- Write exactly 6 to 8 sentences for the narrative.\n"
    "- The triage color, trend assessment, and action posture are PRE COMPUTED FACTS. "
    "You MUST incorporate them verbatim. You CANNOT change them.\n"
    "- All numeric values (heart rate averages, episode counts, durations, coverage percentages) "
    "are immutable facts. Reference them accurately.\n"
    "- End the narrative with the Trend Assessment and Overall Action Posture statements.\n"
    "- Provide exactly 3 to 5 suggested clinical review action bullets.\n"
    "- Respond ONLY with valid JSON. No markdown formatting, no code fences."
)


def _build_user_prompt(
    patient_id: str,
    window_start: str,
    window_end: str,
    hr_stats: VitalStats,
    rr_stats: VitalStats,
    data_quality: DataQuality,
    episodes: list[Episode],
    rollups: EpisodeRollups,
    triage: str,
    trend_assessment: str,
    action_posture: str,
    phases: list[dict] | None = None,
    bed_summary=None,
    activity_trend=None,
) -> str:
    payload = {
        "patient_id": patient_id,
        "window": {"start": window_start, "end": window_end, "resolution": "hourly"},
        "triage": triage,
        "trend_assessment": trend_assessment,
        "overall_action_posture": action_posture,
        "data_quality": data_quality.model_dump(),
        "stats": {
            "hr": hr_stats.model_dump(),
            "rr": rr_stats.model_dump(),
        },
        "episodes_top": [ep.model_dump() for ep in episodes[:8]],
        "episode_rollups": rollups.model_dump(),
    }
    if phases:
        payload["phases"] = phases
    if bed_summary:
        try:
            payload["bed_summary"] = bed_summary.model_dump()
        except Exception:
            pass
    if activity_trend:
        try:
            payload["activity_trend"] = activity_trend.model_dump()
        except Exception:
            pass

    return (
        "Generate a clinical intelligence narrative for the following patient vitals data.\n\n"
        "CRITICAL CONSTRAINTS:\n"
        "Say 'elevated breathing rate' directly. Say 'high heart rate' directly. "
        "- Use plain language only and STRICTLY avoid clinical diagnostic terminology. Say 'low heart rate' smoothly. "
        "Say 'elevated breathing rate' independently. Say 'high heart rate' directly. "
        "Never use 'rhythm instability' (say 'heart rate variability') or 'cardiorespiratory coupling' (say 'concurrent low heart rate and elevated breathing rate').\n"
        "- NEVER recommend specific diagnostic tests, procedures, medications, or treatment changes. "
        "Use language like 'further evaluation is suggested' or 'may benefit from additional clinical assessment' instead.\n"
        "- NEVER use the word 'warranted'. Use 'suggested' or 'is suggested' instead.\n"
        "- NEVER use hyphens in your output.\n"
        "- The triage, trend assessment, and action posture values provided are PRE COMPUTED FACTS. "
        "You MUST NOT change them. Incorporate them verbatim into your narrative.\n"
        "- All numeric values are immutable facts. Do not alter any number.\n"
        "- Provide EXACTLY 3 to 5 suggested action bullets.\n"
        "- Use cautious clinical language throughout: 'may indicate', 'clinical correlation is suggested'.\n\n"
        "INPUT:\n"
        f"{json.dumps(payload, indent=2, default=str)}\n\n"
        "OUTPUT (JSON only, no markdown):\n"
        '{\n'
        '  "narrative": "Write exactly 6 to 8 sentences. End with Trend Assessment and Overall Action Posture.",\n'
        '  "suggested_actions": ["3 to 5 conditional clinical action bullets"]\n'
        '}'
    )


async def generate_llm_narrative(
    patient_id: str,
    window_start: str,
    window_end: str,
    hr_stats: VitalStats,
    rr_stats: VitalStats,
    data_quality: DataQuality,
    episodes: list[Episode],
    rollups: EpisodeRollups,
    triage: str,
    trend_assessment: str,
    action_posture: str,
    quality_warnings: list[str] | None = None,
    phases: list[dict] | None = None,
    bed_summary=None,
    activity_trend=None,
    positional_stats=None,
) -> tuple[str, list[str], str]:
    """Call OpenAI to generate the narrative. Falls back to deterministic on failure.
    
    Returns (narrative, actions, narrative_source).
    Note: LLM path returns plain string narrative (not structured dict).
    """
    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)

        user_prompt = _build_user_prompt(
            patient_id, window_start, window_end,
            hr_stats, rr_stats, data_quality,
            episodes, rollups, triage, trend_assessment, action_posture,
            phases=phases, bed_summary=bed_summary,
            activity_trend=activity_trend,
        )

        model_name = settings.llm_model
        response = await client.messages.create(
            model=model_name,
            system=_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=800,
        )

        content = response.content[0].text.strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
            if content.endswith("```"):
                content = content[:-3].strip()

        result = json.loads(content)

        narrative = result.get("narrative", "")
        actions = result.get("suggested_actions", [])
        actions = actions[:settings.max_actions]

        source_label = f"AI-generated ({model_name}, constrained prompt)"
        print(f"[CardioReport] LLM narrative generated successfully via {model_name}")
        return narrative, actions, source_label

    except Exception as e:
        print(f"[CardioReport] LLM narrative failed ({e}), using deterministic fallback")
        narrative_dict, actions = generate_deterministic_narrative(
            patient_id, window_start, window_end,
            hr_stats, rr_stats, data_quality,
            episodes, rollups, triage, trend_assessment, action_posture,
            quality_warnings=quality_warnings,
            phases=phases,
            bed_summary=bed_summary,
            positional_stats=positional_stats,
        )
        # Flatten dict to string for LLM fallback path
        flat = _flatten_narrative_dict(narrative_dict)
        return flat, actions, "Rule-based phrase taxonomy (AI fallback)"


def _flatten_narrative_dict(narrative_dict: dict) -> str:
    """Convert structured narrative dict to a single string for backward compatibility."""
    if isinstance(narrative_dict, str):
        return narrative_dict
    parts = []
    opening = narrative_dict.get('opening', '')
    if opening:
        parts.append(opening)
    for line in narrative_dict.get('phase_lines', []):
        parts.append(f"\u2022 {line}")
    closing = narrative_dict.get('closing', '')
    if closing:
        parts.append(closing)
    return "\n".join(parts)


# ── Main entry point ────────────────────────────────────────────────────────

async def generate_narrative(
    patient_id: str,
    window_start: str,
    window_end: str,
    hr_stats: VitalStats,
    rr_stats: VitalStats,
    data_quality: DataQuality,
    episodes: list[Episode],
    rollups: EpisodeRollups,
    triage: str,
    trend_assessment: str,
    action_posture: str,
    use_llm_override: Optional[bool] = None,
    quality_warnings: list[str] | None = None,
    phases: list[dict] | None = None,
    bed_summary=None,
    activity_trend=None,
    positional_stats=None,
) -> tuple[str | dict, list[str], str]:
    """Generate narrative using LLM or deterministic fallback.
    
    Returns (narrative, actions, narrative_source).
    narrative may be a str (LLM) or dict (deterministic).
    """
    use_llm = use_llm_override if use_llm_override is not None else settings.use_llm

    if use_llm and settings.anthropic_api_key:
        return await generate_llm_narrative(
            patient_id, window_start, window_end,
            hr_stats, rr_stats, data_quality,
            episodes, rollups, triage, trend_assessment, action_posture,
            quality_warnings=quality_warnings,
            phases=phases,
            bed_summary=bed_summary,
            activity_trend=activity_trend,
            positional_stats=positional_stats,
        )

    if use_llm and not settings.anthropic_api_key:
        print("[CardioReport] AI requested but no ANTHROPIC_API_KEY configured. Using deterministic.")

    narrative_dict, actions = generate_deterministic_narrative(
        patient_id, window_start, window_end,
        hr_stats, rr_stats, data_quality,
        episodes, rollups, triage, trend_assessment, action_posture,
        quality_warnings=quality_warnings,
        phases=phases,
        bed_summary=bed_summary,
        activity_trend=activity_trend,
        positional_stats=positional_stats,
    )
    return narrative_dict, actions, "Rule-based phrase taxonomy"
