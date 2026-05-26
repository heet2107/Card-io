#!/usr/bin/env python3
"""
CardioReport Batch Generator
=============================
Generates 20 patient PDFs (FullPeriod + CriticalWeek per patient)
plus PDF #21 — a master summary table.

Naming convention:
  01_S_Chair_FullPeriod.pdf / 01_S_Chair_CriticalWeek.pdf
  ...
  10_Wimberley_FullPeriod.pdf / 10_Wimberley_CriticalWeek.pdf
  31_BatchSummary.pdf

Usage:
  cd /Users/heetbarot/Documents/Cardio-io/Code
  python batch_generate.py [--outdir /path/to/output]
"""

from __future__ import annotations
import sys, os, asyncio, traceback, argparse, time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
from backend.excel_ingest import load_vitals, get_patient_ids, get_patient_metadata
from backend.signal_engine import (
    apply_window, compute_stats, compute_full_stats,
    compute_data_quality, compute_data_resolution,
    compute_triage, compute_trend_assessment, compute_action_posture,
    compute_positional_stats, compute_activity_data,
)
from backend.episodes import detect_episodes, compute_rollups
from backend.narrative_ai import generate_narrative
from backend.charts import (
    generate_combined_chart, generate_histogram,
    generate_positional_chart, generate_activity_trend_chart,
)
from backend.pdf_render import generate_pdf
from backend.quality_gates import run_quality_gates
from backend.window_intelligence import detect_phases, compute_report_priority
from backend.models import Phase
from backend.config import settings, Locations


# ── Patient ordering: alphabetical by display name ────────────────────────────

PATIENT_ORDER = [
    ("01", "EG"),
    ("02", "JB"),
    ("03", "Nancy"),
    ("04", "PHolst"),
    ("05", "RSanchez"),
    ("06", "S (Bed)"),
    ("07", "S (Chair)"),
    ("08", "SAllen"),
    ("09", "TMiller"),
    ("10", "Wimberley"),
]

# Filesystem-safe name for each patient (strip parens/spaces)
def _safe_name(pid: str) -> str:
    return pid.replace(" ", "_").replace("(", "").replace(")", "").replace("/", "_")


# ── Coverage string helper (consistent with main.py fix) ─────────────────────

def _coverage_summary(data_quality, positional_stats) -> str:
    if positional_stats and len(positional_stats.rows) > 1:
        expected_h = data_quality.expected_hours
        parts = []
        for row in positional_stats.rows:
            loc_pct = min(round(row.hours / max(expected_h, 1) * 100, 1), 100.0)
            parts.append(f"{row.location}: {row.hours}/{expected_h}h ({loc_pct}%)")
        return "  |  ".join(parts)
    else:
        capped_pct = min(data_quality.quality_pct, 100.0)
        return f"{data_quality.total_hours}/{data_quality.expected_hours}h ({capped_pct}%)"


# ── Single report generator ───────────────────────────────────────────────────

async def generate_one(patient_id: str, range_type: str,
                        start: str | None, end: str | None,
                        all_data: dict,
                        one_page_only: bool = False,
                        report_label: str = "",
                        is_fallback_90d: bool = False) -> dict | None:
    """
    Run the full pipeline for one patient+window.
    Returns a result dict (for the summary table) or None on failure.

    R17: report_label is one of "FullPeriod", "90DayPeriod", "CriticalWeek"
    (empty string preserves pre-R17 callers). is_fallback_90d=True signals that
    a 90DayPeriod request fell back to full-period coverage because the
    patient's monitoring window is < 90 days; the renderer adds an explanatory
    note in the header.
    """
    if patient_id not in all_data:
        print(f"    WARN: '{patient_id}' not in loaded data. Skipping.")
        return None

    full_df = all_data[patient_id]
    df = apply_window(full_df.copy(), range_type, start, end)
    if df.empty:
        print(f"    WARN: empty window for {patient_id} ({range_type}). Skipping.")
        return None

    window_start_ts = df["timestamp"].min()
    window_end_ts   = df["timestamp"].max()
    window_start    = window_start_ts.strftime("%Y-%m-%d")
    window_end      = window_end_ts.strftime("%Y-%m-%d")

    gate = run_quality_gates(df, window_start_ts, window_end_ts)
    if not gate["can_generate"]:
        print(f"    WARN: Quality gate rejected {patient_id}: {gate['reason']}")
        return None

    hr_stats, rr_stats = compute_stats(df)
    full_stats   = compute_full_stats(df)
    data_quality = compute_data_quality(df)
    data_res     = compute_data_resolution(df)

    episodes = detect_episodes(df)
    rollups  = compute_rollups(episodes, df)

    triage         = compute_triage(episodes, rollups.coupled_fraction, df=df)
    trend, _       = compute_trend_assessment(df, episodes)
    max_band       = max((ep.severity_band for ep in episodes), default="S0")
    max_score      = max((ep.severity_score for ep in episodes), default=0)
    action_posture = compute_action_posture(triage, trend, rollups.coupled_fraction, max_band)

    raw_phases  = detect_phases(df, episodes)
    phases      = [Phase(**p) for p in raw_phases]
    report_prio = compute_report_priority(episodes, raw_phases, max_score, gate["warnings"])

    sensor_type = "chair"
    if "location" in df.columns and Locations.BED in df["location"].values:
        sensor_type = "bed" if Locations.CHAIR not in df["location"].values else "bed+chair"

    positional_stats = compute_positional_stats(df)
    activity_data    = compute_activity_data(df)

    narrative, actions, narrative_src = await generate_narrative(
        patient_id, window_start, window_end,
        hr_stats, rr_stats, data_quality,
        episodes, rollups, triage, trend, action_posture,
        use_llm_override=False,
        quality_warnings=gate["warnings"],
        phases=raw_phases,
        bed_summary=None,
        activity_trend=activity_data,
        positional_stats=positional_stats,
    )

    coverage = _coverage_summary(data_quality, positional_stats)

    chart_b64      = generate_combined_chart(df, episodes)
    histogram_b64  = generate_histogram(df)
    positional_b64 = generate_positional_chart(df)
    activity_b64   = generate_activity_trend_chart(df)

    report_dict = {
        "patient_id": patient_id,
        "window_start": window_start,
        "window_end": window_end,
        "report_date": (window_end_ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        "data_resolution": data_res,
        "coverage_summary": coverage,
        "disclaimer": "Decision-support summary derived from longitudinal vital sign trends; interpret in clinical context.",
        "hr_summaries": hr_stats.model_dump(),
        "rr_summaries": rr_stats.model_dump(),
        "full_stats": full_stats.model_dump() if full_stats else None,
        "data_quality": data_quality.model_dump(),
        "episodes": [e.model_dump() for e in episodes[:settings.max_events_table]],
        "episode_rollups": rollups.model_dump(),
        "triage": triage,
        "trend_assessment": trend,
        "overall_action_posture": action_posture,
        "max_severity_score": max_score,
        "narrative": narrative,
        "suggested_actions": actions[:settings.max_actions],
        "use_ai": False,
        "narrative_source": narrative_src,
        "report_priority": report_prio,
        "phases": [p.model_dump() for p in phases],
        "quality_warnings": gate["warnings"],
        "positional_comparison": positional_stats.model_dump() if positional_stats else None,
        "activity_trend": activity_data.model_dump() if activity_data else None,
        "chart_combined_b64": chart_b64,
        "chart_histogram_b64": histogram_b64,
        "chart_positional_b64": positional_b64,
        "chart_activity_b64": activity_b64,
        "sensor_type": sensor_type,
        "bed_summary": None,
        "chart_bed_hours_b64": "",
        "prior_comparison": None,
        # R17 B/C: report-type label and fallback flag flow into PDF render
        # (header note for fallback) and trajectory selection (90DayPeriod path).
        "report_label": report_label,
        "is_fallback_90d": is_fallback_90d,
    }

    # FIX 35: Compute trajectory comparison
    try:
        from backend.narrative_ai import compute_trajectory, build_trajectory_line
        # Determine report type from the label in the calling loop
        # Determine report type: if window covers >80% of full data range, it's FullPeriod
        data_start = full_df['timestamp'].min().normalize()
        data_end = full_df['timestamp'].max().normalize()
        data_days = max(1, (data_end - data_start).days + 1)
        traj_window_days = (window_end_ts - window_start_ts).days + 1
        # R17 D: explicit report_label wins over heuristic when caller knows.
        # 90DayPeriod fallback uses FullPeriod trajectory (same window, same comparison).
        if report_label == "90DayPeriod" and not is_fallback_90d:
            traj_report_type = '90DayPeriod'
        elif report_label == "30DayPeriod" and not is_fallback_90d:
            # R24.3 — 30 day reports use the 30 day trajectory branch.
            # is_fallback_90d is reused as the carrier signal for "this window
            # is actually the patient's full coverage because the requested
            # window length exceeded available data" — same semantics whether
            # the requested length was 30 or 90.
            traj_report_type = '30DayPeriod'
        elif report_label == "FullPeriod" or (report_label in ("90DayPeriod", "30DayPeriod") and is_fallback_90d):
            traj_report_type = 'FullPeriod'
        elif report_label == "CriticalWeek":
            traj_report_type = 'CriticalWeek'
        else:
            traj_report_type = 'FullPeriod' if traj_window_days / data_days > 0.8 else 'CriticalWeek'
        trajectory_raw = compute_trajectory(full_df, window_start, window_end, report_type=traj_report_type)
        report_dict['trajectory_raw'] = trajectory_raw
        report_dict['trajectory'] = trajectory_raw
        report_dict['trajectory_line'] = build_trajectory_line(trajectory_raw)
    except Exception as e:
        print(f"    WARN: Trajectory computation failed: {e}")
        report_dict['trajectory'] = None
        report_dict['trajectory_line'] = None

    # FIX 33: Log reconciliation status
    if isinstance(narrative, dict):
        counts = narrative.get('counts')
        if counts and not counts.get('reconciled', True):
            print(f"    \u26a0\ufe0f  RECONCILIATION FAILURE: {patient_id}")
            print(f"       Detected: {counts['total_episodes']}, In phases: {counts['display_episode_count']}")
            print(f"       Unassigned episodes: {len(counts.get('unassigned', []))}")

        # FIX R7-5: Override trajectory current values with reconciled counts
        # so trajectory line matches the opening sentence exactly.
        #
        # R15 F (post-Sprint): The override is only valid when the trajectory's
        # current window equals the full reporting window — i.e. CriticalWeek.
        # For FullPeriod, the current window is the LAST third of the period;
        # overwriting it with the all-period reconciled count produced a
        # structurally wrong trajectory (e.g. TMiller showed 17 → 95 when the
        # actual last-third count was 12). The R15 B3 ratio multiplier made this
        # bug visible by displaying it as "5.6x increase" instead of leaving the
        # discrepancy implicit. Skip the override for FullPeriod.
        if (counts and report_dict.get('trajectory') is not None
                and report_dict['trajectory'].get('report_type') == 'CriticalWeek'):
            traj = report_dict['trajectory']
            display_ep_count = counts['display_episode_count']
            display_total_hrs = counts['display_total_hours']
            traj['current']['episode_count'] = display_ep_count
            traj['current']['episode_hours'] = display_total_hrs
            traj['delta_episodes'] = display_ep_count - traj['prior']['episode_count']
            traj['delta_hours'] = display_total_hrs - traj['prior']['episode_hours']
            # Reclassify direction with corrected deltas
            d_eps = traj['delta_episodes']
            d_hrs = traj['delta_hours']
            if d_eps > 5 or d_hrs > 10:
                traj['direction'] = 'worsening'
                traj['magnitude'] = 'significant' if (d_eps > 10 or d_hrs > 20) else 'moderate'
            elif d_eps < -5 or d_hrs < -10:
                traj['direction'] = 'improving'
                traj['magnitude'] = 'significant' if (d_eps < -10 or d_hrs < -20) else 'moderate'
            else:
                traj['direction'] = 'stable'
                traj['magnitude'] = 'minimal'
            # Rebuild trajectory line with corrected values
            from backend.narrative_ai import build_trajectory_line
            report_dict['trajectory_line'] = build_trajectory_line(traj)

    pdf_bytes = generate_pdf(report_dict, df=df, episodes=episodes,
                              one_page_only=one_page_only)

    # Compute bed-only coverage for multi-sensor summary (A1a)
    if sensor_type == "bed+chair" and positional_stats:
        bed_row = next((r for r in positional_stats.rows if r.location.lower() == "bed"), None)
        bed_hours = bed_row.hours if bed_row else data_quality.total_hours
    else:
        bed_hours = data_quality.total_hours
    expected_hours = data_quality.expected_hours

    # Extract phase-level peaks for comment enrichment (A1e)
    phase_peak_hr = hr_stats.max
    phase_min_hr = hr_stats.min
    phase_peak_rr = rr_stats.max

    # R16 L1: Dominant phase type for the Comments column = events-table row 1's
    # phase type. Reads from narrative['events_table_row_1_phase_type'], computed
    # once in narrative_ai using the same priority + longest-continuous + date
    # sort that pdf_render applies.
    #
    # Replaces K1's priority-tier-only rule (PHASE_PRIORITY_ORDER + select_dominant_
    # phase_type), which over-surfaced brief peak excursions on patients whose
    # primary phenotype was a lower tier (PHolst FP showing very_high_hr from a
    # 1–2h excursion when his 86h Low HR was the actual clinical concern).
    # K1 helpers remain in config but are no longer called by Comments-column logic.
    dominant_phase_type = (
        narrative.get('events_table_row_1_phase_type') if isinstance(narrative, dict) else None
    )

    # R23.A — condition-window means for the Comments column. Pulled from the
    # same narrative dict that drives the per-patient Major Findings line so the
    # batch cell and per-patient surface report consistent numbers.
    findings_hr_avg = (
        narrative.get('findings_hr_avg') if isinstance(narrative, dict) else None
    )
    findings_rr_avg = (
        narrative.get('findings_rr_avg') if isinstance(narrative, dict) else None
    )

    # Clinical guidance line for Yellow/Red comment fallback (A1f)
    clinical_guidance_line = ""
    from backend.config import RENDER_CONFIG
    guidance_cfg = RENDER_CONFIG.get("clinical_guidance", {})
    guidance_lines = guidance_cfg.get("CLINICAL_GUIDANCE_LINES", {})
    clinical_guidance_line = guidance_lines.get(triage.upper(), "")

    return {
        "patient_id":    patient_id,
        "window_type":   range_type,
        "window_start":  window_start,
        "window_end":    window_end,
        "triage":        triage,
        "trend":         trend,
        "episodes":      len(episodes),
        "coupled":       "Yes" if rollups.coupled_fraction > 0 else "No",
        "coverage":      coverage,
        "hr_avg":        hr_stats.mean,
        "rr_avg":        rr_stats.mean,
        "sensor_type":   sensor_type,
        "pdf_bytes":     pdf_bytes,
        "pages":         1 if one_page_only else 2,
        "success":       True,
        # Round 14 A1 additions
        "bed_hours":     bed_hours,
        "expected_hours": expected_hours,
        "peak_hr":       round(phase_peak_hr),
        "min_hr":        round(phase_min_hr),
        "peak_rr":       round(phase_peak_rr),
        "clinical_guidance": clinical_guidance_line,
        "action_posture": action_posture,
        # R16 J3: dominant phase type for batch summary comment template lookup
        "dominant_phase_type": dominant_phase_type,
        # R23.A: condition-window means for the Comments column.
        "findings_hr_avg": findings_hr_avg,
        "findings_rr_avg": findings_rr_avg,
    }


# ── Most Critical Week finder ─────────────────────────────────────────────────

def detect_most_active_window(
    df: pd.DataFrame,
    episodes: list,
    window_size_days: int,
) -> tuple[str, str] | None:
    """R17 A: Parameterized window scanner. Slides a window of `window_size_days`
    across the patient's data and returns (start_str, end_str) for the window
    with the most overlapping episodes (count-only scoring, preserved from the
    pre-R17 find_critical_week behavior).

    Returns None if monitoring period is shorter than `window_size_days` —
    caller decides fallback behavior. (R17 C uses this signal to fall back to
    full-period coverage with an explanatory note for under-90-day patients.)
    """
    if df is None or len(df) == 0:
        return None

    monitoring_days = (df["timestamp"].max() - df["timestamp"].min()).days + 1
    if monitoring_days < window_size_days:
        return None

    if not episodes:
        latest = df["timestamp"].max()
        cutoff = latest - pd.Timedelta(days=window_size_days - 1)
        return cutoff.strftime("%Y-%m-%d"), latest.strftime("%Y-%m-%d")

    ep_intervals = []
    for ep in episodes:
        st = pd.Timestamp(ep.start_time if hasattr(ep, "start_time") else ep["start_time"])
        en = pd.Timestamp(ep.end_time   if hasattr(ep, "end_time")   else ep["end_time"])
        ep_intervals.append((st, en))

    best_start, best_count = None, 0
    step = pd.Timedelta(hours=6)
    window = pd.Timedelta(days=window_size_days)
    t = df["timestamp"].min()
    t_max = df["timestamp"].max() - window
    while t <= t_max:
        wend = t + window
        count = sum(1 for (st, en) in ep_intervals if st < wend and en > t)
        if count > best_count:
            best_count = count
            best_start = t
        t += step

    if best_start is None:
        latest = df["timestamp"].max()
        cutoff = latest - pd.Timedelta(days=window_size_days - 1)
        return cutoff.strftime("%Y-%m-%d"), latest.strftime("%Y-%m-%d")

    best_end = best_start + window
    best_end = min(best_end, df["timestamp"].max())
    return best_start.strftime("%Y-%m-%d"), best_end.strftime("%Y-%m-%d")


def find_critical_week(df: pd.DataFrame, episodes: list) -> tuple[str, str]:
    """Backward-compat wrapper. Returns 7-day window with most episode overlap.

    Always returns a window (never None) since all production patients have
    monitoring periods well above 7 days. For monitoring < 7 days the caller
    historically got an out-of-range fallback; preserved for compat.
    """
    result = detect_most_active_window(df, episodes, window_size_days=7)
    if result is not None:
        return result
    # Edge case: monitoring < 7 days. Preserve pre-R17 fallback behavior.
    latest = df["timestamp"].max()
    cutoff = latest - pd.Timedelta(days=6)
    return cutoff.strftime("%Y-%m-%d"), latest.strftime("%Y-%m-%d")


# ── Summary PDF builder ───────────────────────────────────────────────────────

def build_summary_pdf(results: list[dict]) -> bytes:
    """Generate a single-page master summary table PDF (report #21)."""
    import io, html as _html
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.lib.enums import TA_CENTER, TA_LEFT

    buf = io.BytesIO()
    ss  = getSampleStyleSheet()
    page_w_total = letter[0] - 1.0 * inch

    BRAND_BLUE = colors.HexColor("#1E40AF")
    HEADER_BG  = colors.HexColor("#1E3A5F")
    BORDER     = colors.HexColor("#D1D5DB")
    RED_BG     = colors.HexColor("#FEF2F2")
    AMB_BG     = colors.HexColor("#FFFBEB")
    GRN_BG     = colors.HexColor("#F0FDF4")
    RED_TEXT   = colors.HexColor("#991B1B")
    AMB_TEXT   = colors.HexColor("#92400E")
    GRN_TEXT   = colors.HexColor("#166534")

    TRIAGE_COLORS = {"Red": (RED_BG, RED_TEXT), "Yellow": (AMB_BG, AMB_TEXT), "Green": (GRN_BG, GRN_TEXT)}

    # R15 C3: shrunk header/body/sub/footer font sizes by ~0.5pt to ensure 10
    # patient rows fit on a single page. Sizes from settings.
    _th_fs = settings.batch_summary_header_font_size
    _td_fs = settings.batch_summary_body_font_size
    _sub_fs = settings.batch_summary_subtitle_font_size
    _ftr_fs = settings.batch_summary_footer_font_size
    th  = ParagraphStyle("th",  parent=ss["Normal"], fontSize=_th_fs, fontName="Helvetica-Bold",
                          textColor=colors.white, leading=_th_fs + 2.5, alignment=TA_CENTER)
    td  = ParagraphStyle("td",  parent=ss["Normal"], fontSize=_td_fs, fontName="Helvetica",
                          textColor=colors.HexColor("#1F2937"), leading=_td_fs + 3)
    tds = ParagraphStyle("tds", parent=ss["Normal"], fontSize=_td_fs, fontName="Helvetica",
                          textColor=colors.HexColor("#1F2937"), leading=_td_fs + 3, alignment=TA_CENTER)
    ttl = ParagraphStyle("ttl", parent=ss["Normal"], fontSize=12, fontName="Helvetica-Bold",
                          textColor=BRAND_BLUE, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=ss["Normal"], fontSize=_sub_fs, fontName="Helvetica",
                          textColor=colors.HexColor("#6B7280"), spaceAfter=4)
    ftr = ParagraphStyle("ftr", parent=ss["Normal"], fontSize=_ftr_fs, fontName="Helvetica",
                          textColor=colors.HexColor("#9CA3AF"), alignment=TA_CENTER)

    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.5*inch, rightMargin=0.5*inch,
        topMargin=0.5*inch, bottomMargin=0.35*inch,
    )
    elems = []

    now = datetime.now().strftime("%B %d, %Y at %H:%M")
    # R16 J1: counts derived from rendered rows so the header never overstates the
    # cohort when a patient is dropped (e.g. S(Bed) excluded for 25% coverage —
    # below the 30% gate). Was hardcoded "10-Patient Study | 20 individual reports".
    unique_patients = len({r["patient_id"] for r in results if r})
    total_reports = sum(1 for r in results if r)
    elems.append(Paragraph("CardioReport — Batch Study Summary", ttl))
    elems.append(Paragraph(
        f"{unique_patients}-Patient Study  |  Generated {now}  |  "
        f"{total_reports} individual reports + this summary", sub
    ))
    elems.append(HRFlowable(width="100%", thickness=0.5, color=BORDER))
    elems.append(Spacer(1, 4))

    # Header row — Round 14 A1: Sensor dropped, Coverage/Episodes/Day/Episodic Burden added
    cw = settings.batch_summary_column_widths
    cols = ["#", "Patient", "Report<br/>Type", "Period", "Coverage", "HR", "RR",
            "Episodic<br/>Burden", "Eps/<br/>Day", "Coupled", "Triage", "Comments"]
    widths = [page_w_total * cw[k] for k in [
        "number", "patient", "report_type", "period", "coverage", "hr", "rr",
        "episodic_burden", "episodes_per_day", "coupled", "triage", "comments",
    ]]
    rows = [[Paragraph(c, th) for c in cols]]
    cmds = [
        ("BACKGROUND",   (0, 0), (-1, 0), HEADER_BG),
        ("GRID",         (0, 0), (-1, -1), 0.3, BORDER),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
    ]

    # Sort: by patient number then FullPeriod before CriticalWeek
    def sort_key(r):
        order = {"full": 0, "critical": 1}
        wt = "full" if "Full" in r.get("file_label", "") else "critical"
        return (r.get("num", "99"), order[wt])

    dec = settings.batch_summary_vitals_decimal_places
    # R22.C2: episodes/day formatting moved to batch_summary.format_episodes_per_day.
    # batch_summary_episodes_per_day_round retained in settings for backward compat
    # but no longer consulted at render time.
    # R16 J3: standardized comment templates keyed by dominant phase type.
    # Replaces the prior mix of patient-id-hardcoded enrichment, generic guidance
    # text, and ad-hoc descriptors. Triage column already conveys triage band;
    # Comments now convey clinical specifics only.
    comment_templates = settings.batch_summary_comment_templates
    # R16 K2 + R22.B: physiologic clipping for HR peak/min values so batch
    # summary cells match per-patient reports. RR is no longer clipped (R22.B
    # reversed R18 C2); raw RR peak shown.
    from backend.narrative_ai import _clip_physiologic
    from backend.batch_summary import format_episodes_per_day

    def _clip_for_display(v, metric):
        """Format a peak/min value for the batch summary cell.

        R22.B: RR is no longer clipped at the physiologic ceiling — raw peak
        is shown so any residual data quality issue stays visible. HR keeps
        its physiologic floor/ceiling guard (rare extreme sensor noise).
        """
        if v is None or v == '':
            return ''
        try:
            v_num = float(v)
        except (TypeError, ValueError):
            return str(v)
        if metric == "rr_brpm":
            return f"{int(round(v_num))}"
        cv, was_clipped = _clip_physiologic(v_num, metric)
        if not was_clipped:
            return f"{int(round(cv))}"
        return f"<{int(round(cv))}*" if v_num < cv else f">{int(round(cv))}*"

    for i, res in enumerate(results, start=1):
        if not res:
            continue
        triage = res["triage"]
        bg, tc = TRIAGE_COLORS.get(triage, (GRN_BG, GRN_TEXT))

        # Period — shortened format (month-day only)
        try:
            ws = pd.Timestamp(res['window_start']).strftime('%b %d')
            we = pd.Timestamp(res['window_end']).strftime('%b %d')
            period = f"{ws} → {we}"
        except Exception:
            period = f"{res['window_start']} → {res['window_end']}"

        # Coverage — bed-only for multi-sensor, single line.
        # R18 B2: hours → days, 0 decimals. Sajol asked for days on May 4
        # review; the hour totals are still computed for the % calc.
        bed_h = res.get("bed_hours", 0)
        exp_h = res.get("expected_hours", 1)
        cov_pct = min(bed_h / max(exp_h, 1) * 100, 100.0)
        bed_days = max(1, round(bed_h / 24)) if bed_h > 0 else 0
        exp_days = max(1, round(exp_h / 24))
        coverage_cell = settings.batch_summary_coverage_format.format(
            recorded_days=bed_days, expected_days=exp_days,
            recorded=int(bed_h), expected=int(exp_h),  # kept for backward-compat templates
            pct=cov_pct,
        ).replace("\n", "<br/>")

        # HR / RR — integer (A1c)
        hr_cell = f"{res['hr_avg']:.{dec}f}"
        rr_cell = f"{res['rr_avg']:.{dec}f}"

        # Episodes/Day — R22.C2: zero prints "0", positive sub-1 rate prints
        # "<1" (Sajol's "your zero better be the right zero" rule).
        if exp_h > 0 and bed_h > 0:
            episodes_per_day = format_episodes_per_day(res["episodes"], bed_h / 24)
        else:
            episodes_per_day = "—"

        # R16 J3 + R22 C1/C3: Comments column.
        #   J3: Greens get "Stable baseline"; non-Green use template keyed by
        #       dominant phase type.
        #   K1: dominant phase chosen by priority tier (in generate_one).
        #   K2: peak/min HR values pass through _clip_for_display (RR raw per R22.B).
        #   R22.C3: per-patient overrides removed; the dispatch is gone.
        #   R22.C1: red rows bold only the trigger phrase (everything before the
        #     parenthetical) so the headline stands out without flattening the cell.
        #     Yellow rows remain unbolded.
        comments = ""
        pid = res["patient_id"]
        if triage == "Green":
            comments = comment_templates.get("stable", "Stable baseline")
        else:
            dominant_phase = res.get("dominant_phase_type")
            tmpl = comment_templates.get(dominant_phase) if dominant_phase else None
            if tmpl:
                # R23.A — avg_hr / avg_rr use condition-window scoped means when
                # available so the parenthetical agrees with the "Sustained [tier]"
                # parent. Fall back to overall mean only when the scoped value is
                # missing (no dominant row, or upstream did not populate it).
                scoped_hr = res.get('findings_hr_avg')
                scoped_rr = res.get('findings_rr_avg')
                comments = tmpl.format(
                    avg_hr=round(scoped_hr if scoped_hr is not None else res.get('hr_avg', 0)),
                    peak_hr=_clip_for_display(res.get('peak_hr'), "hr_bpm"),
                    min_hr=_clip_for_display(res.get('min_hr'), "hr_bpm"),
                    avg_rr=round(scoped_rr if scoped_rr is not None else res.get('rr_avg', 0)),
                    peak_rr=_clip_for_display(res.get('peak_rr'), "rr_brpm"),
                )

        if triage == "Red" and comments:
            paren_idx = comments.find("(")
            br_paren_idx = comments.find("<br/>(")
            split_idx = br_paren_idx if br_paren_idx != -1 else paren_idx
            if split_idx > 0:
                trigger = comments[:split_idx].rstrip()
                rest = comments[split_idx:]
                comments = f"<b>{trigger}</b>{rest}"
            else:
                comments = f"<b>{comments}</b>"

        rows.append([
            Paragraph(str(i),                                  tds),
            Paragraph(_html.escape(pid),                       td),
            Paragraph(_html.escape(res.get("file_label", "")), td),
            Paragraph(period,                                   td),
            Paragraph(coverage_cell,                            td),
            Paragraph(hr_cell,                                  tds),
            Paragraph(rr_cell,                                  tds),
            Paragraph(str(res["episodes"]),                     tds),
            Paragraph(episodes_per_day,                         tds),
            Paragraph(_html.escape(res.get("coupled", "No")),  tds),
            Paragraph(f'<font color="#{tc.hexval()[2:]}">{triage}</font>', tds),
            # R18 B3 + R22 C1: comments text is a controlled template (numeric
            # values interpolated; <br/> for two-line wrap; <b>...</b> on red
            # rows wraps the trigger phrase). Skip _html.escape so reportlab
            # renders the markup.
            Paragraph(comments,                                 td),
        ])
        row_i = len(rows) - 1
        cmds.append(("BACKGROUND", (0, row_i), (-1, row_i), bg))

    tbl = Table(rows, colWidths=widths, repeatRows=1)
    tbl.setStyle(TableStyle(cmds))
    elems.append(tbl)
    elems.append(Spacer(1, 6))

    # Triage counts (Overall)
    red_n    = sum(1 for r in results if r and r["triage"] == "Red")
    yellow_n = sum(1 for r in results if r and r["triage"] == "Yellow")
    green_n  = sum(1 for r in results if r and r["triage"] == "Green")
    
    # Triage counts (Per Patient - defined by Full Period triage)
    p_triage = {}
    for _, p in PATIENT_ORDER:
        p_triage[p] = "Skipped"  # default if no reports generated
        
    for r in results:
        if r and r.get("file_label") == "FullPeriod":
            p_triage[r["patient_id"]] = r["triage"]
            
    p_red = sum(1 for v in p_triage.values() if v == "Red")
    p_yel = sum(1 for v in p_triage.values() if v == "Yellow")
    p_grn = sum(1 for v in p_triage.values() if v == "Green")
    p_skp = sum(1 for v in p_triage.values() if v == "Skipped")
    
    elems.append(Paragraph(
        f"<b>Triage distribution across all reports:</b>  "
        f'<font color="#991B1B">Red: {red_n}</font>  |  '
        f'<font color="#92400E">Yellow: {yellow_n}</font>  |  '
        f'<font color="#166534">Green: {green_n}</font><br/>'
        f"<b>Per patient distribution:</b> "
        f'<font color="#991B1B">Red: {p_red}</font>, '
        f'<font color="#92400E">Yellow: {p_yel}</font>, '
        f'<font color="#166534">Green: {p_grn}</font>, '
        f'Insufficient data: {p_skp}.',
        ParagraphStyle("tri", parent=ss["Normal"], fontSize=_sub_fs, fontName="Helvetica",
                       textColor=colors.HexColor("#1F2937"), spaceAfter=4, leading=_sub_fs + 2.5)
    ))
    elems.append(HRFlowable(width="100%", thickness=0.3, color=BORDER))
    elems.append(Paragraph(
        f"All PDFs generated by CardioReport v{settings.app_version}. "
        "Decision-support summaries only — interpret in full clinical context.",
        ftr
    ))

    doc.build(elems)
    buf.seek(0)
    return buf.read()


# ── Main ──────────────────────────────────────────────────────────────────────

async def main(outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"\n{'='*65}")
    print(f"  CardioReport Batch Generator")
    print(f"  Output → {outdir}")
    print(f"{'='*65}\n")

    # Load all data once
    print("Loading all patient data (this may take a moment)...")
    t0 = time.time()
    all_data = load_vitals()
    print(f"  Loaded {len(all_data)} patients in {time.time()-t0:.1f}s\n")

    summary_results = []
    report_num = 0      # R17 B: contiguous 01-N file numbering across successful reports
    attempt_num = 0     # planned-attempt counter for progress display
    # R24.3 — four report types per patient: FullPeriod, 30DayPeriod, 90DayPeriod, CriticalWeek
    total_planned = sum(1 for _, _ in PATIENT_ORDER) * 4

    for num, patient_id in PATIENT_ORDER:
        safe = _safe_name(patient_id)
        meta = get_patient_metadata(patient_id)
        dr   = meta.get("date_range", {})
        full_start = dr.get("start") or None
        full_end   = dr.get("end")   or None

        print(f"── Patient {num}: {patient_id}  ({full_start} → {full_end})")

        # Pre-load full df once to find critical week + 90-day window
        raw_df = all_data.get(patient_id)
        full_eps = detect_episodes(raw_df) if raw_df is not None else []
        crit_start, crit_end = find_critical_week(raw_df, full_eps) if raw_df is not None else (full_start, full_end)

        # R17 A+B: detect 90-day window (None signals fallback for under-90-day patients)
        ninety = detect_most_active_window(raw_df, full_eps, window_size_days=90) if raw_df is not None else None
        if ninety is not None:
            ninety_start, ninety_end = ninety
            is_fallback_90d = False
        else:
            ninety_start, ninety_end = full_start, full_end
            is_fallback_90d = True

        # R24.3 — 30-day window mirrors the 90-day path. detect_most_active_window
        # is already parameterized; same fallback semantics when patient has
        # fewer than 30 days of monitoring data (Sajol May 19 call: 30 day is
        # the new Pam Health primary asset between CriticalWeek and 90Day).
        thirty = detect_most_active_window(raw_df, full_eps, window_size_days=30) if raw_df is not None else None
        if thirty is not None:
            thirty_start, thirty_end = thirty
            is_fallback_30d = False
        else:
            thirty_start, thirty_end = full_start, full_end
            is_fallback_30d = True

        for label, rtype, wstart, wend, is_fb in [
            ("FullPeriod",   "custom", full_start,    full_end,    False),
            ("30DayPeriod",  "custom", thirty_start,  thirty_end,  is_fallback_30d),
            ("90DayPeriod",  "custom", ninety_start,  ninety_end,  is_fallback_90d),
            ("CriticalWeek", "custom", crit_start,    crit_end,    False),
        ]:
            attempt_num += 1
            print(f"   [{attempt_num:02d}/{total_planned}] {patient_id} {label} ...")

            try:
                result = await generate_one(
                    patient_id, rtype, wstart, wend, all_data,
                    report_label=label, is_fallback_90d=is_fb,
                )
                if result is None:
                    print(f"         SKIPPED (no data / quality gate)")
                    summary_results.append(None)
                    continue

                # R17 B: report_num assigned only on success, so files are contiguous
                # 01-27 even when patients (e.g. S(Bed)) are quality-gated out.
                report_num += 1
                fname = f"{report_num:02d}_{safe}_{label}.pdf"
                pdf_path = outdir / fname
                pdf_path.write_bytes(result["pdf_bytes"])
                size_kb = len(result["pdf_bytes"]) / 1024
                print(f"         ✅  {fname}  ({size_kb:.0f} KB)  triage={result['triage']}  "
                      f"eps={result['episodes']}  coverage={result['coverage']}")

                result["file_label"] = label
                result["num"] = num
                result["is_fallback_90d"] = is_fb
                summary_results.append(result)

            except Exception as e:
                print(f"         ❌  FAILED: {e}")
                traceback.print_exc()
                summary_results.append(None)

    # R17 B: batch summary numbered as the next file after the last patient
    # report, packed contiguously (e.g. 28_BatchSummary.pdf when 27 patient
    # reports succeed).
    summary_num = report_num + 1
    print(f"\n── Generating PDF #{summary_num}: Batch Summary Table ...")
    valid_results = [r for r in summary_results if r]
    summary_bytes = build_summary_pdf(valid_results)
    summary_fname = f"{summary_num:02d}_BatchSummary.pdf"
    summary_path  = outdir / summary_fname
    summary_path.write_bytes(summary_bytes)
    print(f"   ✅  {summary_fname}  ({len(summary_bytes)//1024} KB)")

    # R15 E1+E2: PAMHealth study packaging — 9 one-page FullPeriod + 3 CriticalWeek
    await generate_study_package(outdir, all_data)

    # Final status
    success = sum(1 for r in summary_results if r)
    failed  = len(summary_results) - success
    print(f"\n{'='*65}")
    print(f"  BATCH COMPLETE")
    print(f"  {success} standard PDFs generated  |  {failed} skipped/failed")
    print(f"  Output directory: {outdir}")
    print(f"  Files:")
    for f in sorted(outdir.glob("*.pdf")):
        print(f"    {f.name}  ({f.stat().st_size // 1024} KB)")
    study_dir = outdir / "Study"
    if study_dir.exists():
        print(f"  Study packaging variant ({study_dir.name}/):")
        for f in sorted(study_dir.glob("*.pdf")):
            print(f"    {f.name}  ({f.stat().st_size // 1024} KB)")
    print(f"{'='*65}\n")


# ── R15 Sprint E: PAMHealth Study Packaging Variant ───────────────────────────

# E2: 3 patients chosen for CriticalWeek one-pagers (cover burden spectrum well)
_STUDY_CRITICAL_WEEK_PATIENTS = ["JB", "TMiller", "Wimberley"]


async def generate_study_package(outdir: Path, all_data: dict) -> None:
    """Generate the PAMHealth study packaging variant.

    Output: ``{outdir}/Study/`` containing:
      - 9 one-page FullPeriod reports (E1)
      - 9 one-page 90DayPeriod reports (R17 F — 4 with auto-detected windows,
        5 fallback variants for under-90-day patients with explanatory note)
      - One-page CriticalWeek reports for JB, TMiller, Wimberley (E2)

    Total: 21 one-pagers. Plug into Sajol's final compiled PDF after his
    hand-written intro page and the standard batch summary.
    """
    study_dir = outdir / "Study"
    study_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n── Generating PAMHealth study packaging variant → {study_dir}")

    generated_full = []
    generated_90d = []
    generated_30d = []  # R24.3
    generated_crit = []

    for num, patient_id in PATIENT_ORDER:
        safe = _safe_name(patient_id)
        meta = get_patient_metadata(patient_id)
        dr = meta.get("date_range", {})
        full_start = dr.get("start") or None
        full_end = dr.get("end") or None

        # E1: 9 candidate FullPeriod one-pagers
        try:
            result = await generate_one(
                patient_id, "custom", full_start, full_end,
                all_data, one_page_only=True,
                report_label="FullPeriod",
            )
            if result is not None:
                fname = f"{num}_{safe}_FullPeriod_OnePage.pdf"
                (study_dir / fname).write_bytes(result["pdf_bytes"])
                kb = len(result["pdf_bytes"]) // 1024
                print(f"   ✅  {fname}  ({kb} KB)  triage={result['triage']}")
                generated_full.append(patient_id)
            else:
                print(f"   ⊘   {patient_id} FullPeriod skipped (quality gate)")
        except Exception as e:
            print(f"   ❌  {patient_id} FullPeriod failed: {e}")

        # R17 F: 9 candidate 90DayPeriod one-pagers (5 with fallback note)
        raw_df = all_data.get(patient_id)
        if raw_df is not None:
            full_eps_for_90d = detect_episodes(raw_df)
            ninety = detect_most_active_window(raw_df, full_eps_for_90d, window_size_days=90)
            if ninety is not None:
                ninety_start, ninety_end = ninety
                is_fb = False
            else:
                ninety_start, ninety_end = full_start, full_end
                is_fb = True
            try:
                result = await generate_one(
                    patient_id, "custom", ninety_start, ninety_end,
                    all_data, one_page_only=True,
                    report_label="90DayPeriod", is_fallback_90d=is_fb,
                )
                if result is not None:
                    fname = f"{num}_{safe}_90DayPeriod_OnePage.pdf"
                    (study_dir / fname).write_bytes(result["pdf_bytes"])
                    kb = len(result["pdf_bytes"]) // 1024
                    fb_marker = " (fallback)" if is_fb else ""
                    print(f"   ✅  {fname}  ({kb} KB)  triage={result['triage']}{fb_marker}")
                    generated_90d.append(patient_id)
            except Exception as e:
                print(f"   ❌  {patient_id} 90DayPeriod failed: {e}")

        # R24.3 — 30DayPeriod one-pagers, parallel to the 90DayPeriod block.
        if raw_df is not None:
            full_eps_for_30d = detect_episodes(raw_df)
            thirty = detect_most_active_window(raw_df, full_eps_for_30d, window_size_days=30)
            if thirty is not None:
                thirty_start, thirty_end = thirty
                is_fb_30 = False
            else:
                thirty_start, thirty_end = full_start, full_end
                is_fb_30 = True
            try:
                result = await generate_one(
                    patient_id, "custom", thirty_start, thirty_end,
                    all_data, one_page_only=True,
                    report_label="30DayPeriod", is_fallback_90d=is_fb_30,
                )
                if result is not None:
                    fname = f"{num}_{safe}_30DayPeriod_OnePage.pdf"
                    (study_dir / fname).write_bytes(result["pdf_bytes"])
                    kb = len(result["pdf_bytes"]) // 1024
                    fb_marker = " (fallback)" if is_fb_30 else ""
                    print(f"   ✅  {fname}  ({kb} KB)  triage={result['triage']}{fb_marker}")
                    generated_30d.append(patient_id)
            except Exception as e:
                print(f"   ❌  {patient_id} 30DayPeriod failed: {e}")

        # E2: CriticalWeek one-pagers only for the 3 study-cohort patients
        if patient_id in _STUDY_CRITICAL_WEEK_PATIENTS:
            if raw_df is not None:
                full_eps = detect_episodes(raw_df)
                crit_start, crit_end = find_critical_week(raw_df, full_eps)
                try:
                    result = await generate_one(
                        patient_id, "custom", crit_start, crit_end,
                        all_data, one_page_only=True,
                        report_label="CriticalWeek",
                    )
                    if result is not None:
                        fname = f"{num}_{safe}_CriticalWeek_OnePage.pdf"
                        (study_dir / fname).write_bytes(result["pdf_bytes"])
                        kb = len(result["pdf_bytes"]) // 1024
                        print(f"   ✅  {fname}  ({kb} KB)  triage={result['triage']}")
                        generated_crit.append(patient_id)
                except Exception as e:
                    print(f"   ❌  {patient_id} CriticalWeek failed: {e}")

    print(f"\n   Study package: {len(generated_full)} FullPeriod + "
          f"{len(generated_30d)} 30DayPeriod + "
          f"{len(generated_90d)} 90DayPeriod + "
          f"{len(generated_crit)} CriticalWeek one-pagers")


if __name__ == "__main__":
    _batch_ts = datetime.now().strftime("%Y%m%d_%H%M")
    parser = argparse.ArgumentParser(description="CardioReport Batch PDF Generator")
    parser.add_argument(
        "--outdir",
        default=str(Path(__file__).parent.parent / "Reports" / f"Batch_{_batch_ts}"),
        help="Output directory for generated PDFs",
    )
    args = parser.parse_args()
    asyncio.run(main(Path(args.outdir)))
