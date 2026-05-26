"""
CardioReport – Configuration
All tuneable parameters live here; override via .env or env variables.
Every threshold, weight, label, and boundary is defined here.
No module should define its own magic numbers.
"""

from __future__ import annotations
import os
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# ── Label Constants ─────────────────────────────────────────────────────────

class Conditions:
    """Canonical internal names for detected conditions."""
    SEVERE_BRADY = "Severe Bradycardia"
    BRADYCARDIAC = "Bradycardia"
    ELEVATED_HR = "Elevated HR"          # 95-100 bpm sustained (R15 A1: was 80, now 95)
    TACHYCARDIA = "Tachycardia"
    VERY_HIGH_HR = "Very High HR"        # above 110 bpm
    TACHYPNEA = "Tachypnea"              # Elevated breathing >24 brpm
    HIGH_RR = "High RR"                  # R15 A2: 30-40 brpm sustained
    VERY_HIGH_RR = "Very High RR"        # R15 A2: 40+ brpm sustained (Medicare threshold)


class TrendLabels:
    STABLE = "Stable vital sign pattern"
    INTERMITTENT = "Intermittently unstable vital sign pattern"
    PROGRESSIVE = "Progressively unstable vital sign pattern"
    ESCALATING = "Escalating instability toward end of monitoring"


class TriageLabels:
    GREEN = "Green"
    YELLOW = "Yellow"
    RED = "Red"


class ActionPostureLabels:
    ROUTINE = "Routine review"
    CLOSER = "Closer clinical observation is suggested"
    PROVIDER = "Provider review advised"
    URGENT = "Urgent provider review advised (per protocol)"


class PhaseTypes:
    NORMAL = "normal"          # Skip entirely — HR within normal range, no episodes
    LOW_HR = "low_hr"
    VERY_LOW_HR = "very_low_hr"
    ELEVATED_HR = "elevated_hr"
    HIGH_HR = "high_hr"
    VERY_HIGH_HR = "very_high_hr"
    ELEVATED_RR = "elevated_rr"
    HIGH_RR = "high_rr"             # R15 A2
    VERY_HIGH_RR = "very_high_rr"   # R15 A2
    # Keep old names as aliases for backward compat during transition
    STABLE = "normal"
    MIXED = "normal"


class GateStatus:
    PASS = "PASS"
    WARN = "WARN"
    REJECT = "REJECT"


class Locations:
    LIVING_ROOM = "Living Room"
    CHAIR = "Chair"
    BED = "Bed"
    UNKNOWN = "Unknown"


class ChartColors:
    """Centralized color palette for all visualizations."""
    HR = "#2563EB"        # Royal blue
    HR_FILL = "#93C5FD"   # Light blue
    RR = "#E67E22"        # Orange
    RR_FILL = "#F5CBA7"   # Light orange
    EPISODE = "#EF4444"   # Red (errors/episodes)
    WARNING = "#F59E0B"   # Amber/Orange (thresholds)
    GRID = "#E5E7EB"
    BG = "#FAFBFC"
    TEXT = "#1F2937"

    # Activity Trend palette
    ACTIVITY_HIGH = "#10B981"   # Green
    ACTIVITY_MEDIUM = "#F59E0B" # Amber
    ACTIVITY_LOW = "#EF4444"    # Red


# ── Label Maps ──────────────────────────────────────────────────────────────

CONDITION_DISPLAY = {
    Conditions.SEVERE_BRADY: "Very Low Heart Rate",
    Conditions.BRADYCARDIAC: "Low Heart Rate",
    Conditions.ELEVATED_HR: "Elevated Heart Rate",
    Conditions.TACHYCARDIA: "High Heart Rate",
    Conditions.VERY_HIGH_HR: "Very High Heart Rate",
    Conditions.TACHYPNEA: "Elevated Breathing",           # Dropped "Rate" per Sajol
    Conditions.HIGH_RR: "High Breathing",                 # R15 A2
    Conditions.VERY_HIGH_RR: "Very High Breathing",       # R15 A2
}

# Episode condition string → phase_type. Single source of truth used by
# Round 16 reconcile_counts dedup and batch summary comment template lookup.
# R20.A — hour label pixel-space staggering on candlestick chart. Sajol's
# Round 19 review surfaced "108h26h8h" collisions where three labels collapsed
# into one illegible string on dense day-clusters. The staggering helper offsets
# colliding labels vertically. Operates in pixel space so it scales correctly
# across all chart widths and period lengths.
LABEL_STAGGER_GAP_PX = 28      # estimated max label width plus padding
LABEL_STAGGER_ROW_PT = 11      # vertical offset per stagger row (points)
# R23.C — bumped 2 → 3 so 3 consecutive close badges don't cycle 0/1/0 and
# collide on the third position. JB CriticalWeek Sep 28 to Sep 30 was the
# audit case where the third badge landed on top of the first.
LABEL_STAGGER_MAX_ROWS = 3     # row count; cycles back to 0 after


# R23.B — Hour badge clearance from the in-chart legend strip.
# The HR severity legend in _generate_generic_candlestick is anchored at axes
# y=1.18 (above the data area). Staggered badges on bars that reach ymax can
# rise to ~axes-y 1.12 in points-space, intruding into the legend strip
# (JB CriticalWeek Sep 25 audit case). When a badge's final position would
# cross LEGEND_BOTTOM_AXES_FRACTION minus the clearance, place it INSIDE the
# bar instead with white text (contrasts against dark red severe/critical fills).
LEGEND_BOTTOM_AXES_FRACTION = 1.10  # axes y of the legend strip's bottom edge
BADGE_LEGEND_CLEARANCE_PT = 4        # gap between badge top and legend bottom


# R24.2 — HR / Breathing color index in the per-patient summary title row.
# Maps each family ("hr", "rr") to a canonical phase type whose color in
# PHASE_COLORS drives the swatch fill. If the phase strip palette changes,
# the title swatches follow automatically. low_hr and elevated_rr are
# representative entries for each family (both render in the family color).
phase_strip_index_swatch_family = {
    "hr": "low_hr",        # red-family swatch beside the "HR" label
    "rr": "elevated_rr",   # blue-family swatch beside the "Breathing" label
}


# R21.A — y position in axes coordinates for asterisk legend below candlestick chart.
# Must clear the rotated date tick label baseline. Sajol's Round 20 review showed
# the asterisk overlapping the rightmost dates (Feb 01 / Feb 08 on Wimberley FP,
# Feb 23 / Feb 24 on SAllen CW) when emitted at fig.text(0.99, 0.02).
#
# R23 Hotfix Sprint A — split into path-scoped constants. The single shared
# value couldn't clear the date band on both daily and weekly aggregate paths
# at once: daily labels rotate to ~-0.28, but weekly aggregate uses 30° rotation
# on longer month-day strings that extend further below the chart frame. JB
# FullPeriod and S(Chair) FullPeriod showed the asterisk intersecting weekly
# date labels (May 06/20, Jun 03 on JB; Jan 22 etc on S(Chair)). Each path now
# uses its own tuned value.
ASTERISK_LEGEND_Y_AXES_DAILY = -0.32   # daily + short-period (CriticalWeek) charts
ASTERISK_LEGEND_Y_AXES_WEEKLY = -0.42  # weekly aggregate (FullPeriod long-span) charts
# Deprecated alias for R21 invariant test and any straggler refs.
ASTERISK_LEGEND_Y_AXES = ASTERISK_LEGEND_Y_AXES_DAILY


# R19 C: Threshold legend color palette. Pre-R19 the legend recycled 5
# candlestick severity colors across 8 swatches, so Very Low HR and Very High HR
# rendered identical. Sajol May 4 review flagged this. Each tier now has a
# distinct shade within its metric family (HR red-family, RR blue-family).
THRESHOLD_LEGEND_COLORS = {
    "very_low_hr":  "#8B0000",  # dark crimson — most severe low
    "low_hr":       "#E57373",  # light coral red
    "elevated_hr":  "#FFB300",  # warm amber/orange
    "high_hr":      "#F4511E",  # deep red-orange
    "very_high_hr": "#B71C1C",  # dark vivid red — most severe high
    "elevated_rr":  "#90CAF9",  # light blue
    "high_rr":      "#1976D2",  # medium blue
    "very_high_rr": "#0D47A1",  # dark navy
}


CONDITION_TO_PHASE_TYPE = {
    Conditions.SEVERE_BRADY: "very_low_hr",
    Conditions.BRADYCARDIAC: "low_hr",
    Conditions.ELEVATED_HR:  "elevated_hr",
    Conditions.TACHYCARDIA:  "high_hr",
    Conditions.VERY_HIGH_HR: "very_high_hr",
    Conditions.TACHYPNEA:    "elevated_rr",
    Conditions.HIGH_RR:      "high_rr",
    Conditions.VERY_HIGH_RR: "very_high_rr",
}

# R16 K1: dominant-phase selection for batch summary Comments column uses this
# priority order, not burden hours. Reason: under burden-hours selection JB FP
# rendered "Sustained low HR (avg 65, min 40)" — internally contradictory,
# because 65 bpm avg is not low; the avg is period-wide, while "low HR" was
# selected by his 361h of Low HR phases. Picking by priority surfaces the
# clinically-alarming tier (Very High HR, peak 148) instead.
PHASE_PRIORITY_ORDER = [
    "very_high_hr",
    "very_high_rr",
    "very_low_hr",
    "high_hr",
    "high_rr",
    "low_hr",
    "elevated_hr",
    "elevated_rr",
]


def select_dominant_phase_type(phase_types_present):
    """R16 K1: pick the highest-priority phase type present in the patient's
    episodes, regardless of burden hours. `phase_types_present` is any iterable
    yielding phase_type strings (e.g. set, list, dict keys).
    """
    types_set = set(phase_types_present)
    for phase_type in PHASE_PRIORITY_ORDER:
        if phase_type in types_set:
            return phase_type
    return None


# R22.C3: removed per Sajol May 5 call. The system must be blind to mortality
# and any per-patient metadata; every row uses template-driven comments based
# on episode data only. Reverses R16 K3.
BATCH_SUMMARY_SPECIAL_CASE_COMMENTS: dict[str, str] = {}

# R24.3 — 30 day report type constants. Parallel to the 90 day naming.
REPORT_TYPE_30DAY = "30DayPeriod"
WINDOW_DAYS_30 = 30

# R22.A: RR noise filter threshold when HR is missing.
# Per Sajol May 5 call, real high RR co-occurs with valid HR. RR alone with
# no HR is sensor noise (motion artifact, lost radar lock); zero it before
# episode detection so it cannot trigger false breathing alerts.
RR_NOISE_THRESHOLD_WHEN_HR_MISSING = 50  # brpm; values above this with HR=0 zeroed

# Phase labels: None = don't display (skip entirely)
PHASE_LABELS = {
    "normal":       None,                   # Don't display — skip entirely
    "low_hr":       "Low Heart Rate",
    "very_low_hr":  "Very Low Heart Rate",
    "elevated_hr":  "Elevated Heart Rate",  # R15 A1: 95-100 bpm sustained
    "high_hr":      "High Heart Rate",
    "very_high_hr": "Very High Heart Rate", # above 110
    "elevated_rr":  "Elevated Breathing",
    "high_rr":      "High Breathing",       # R15 A2: 30-40 brpm sustained
    "very_high_rr": "Very High Breathing",  # R15 A2: 40+ brpm sustained
    # Legacy aliases — map to None (hide)
    "stable":       None,
    "mixed":        None,
    "mixed_low":    None,
    "mixed_high":   None,
}

# Phase colors for PDF timeline bar — R15 C1: HR=red, RR=blue (was HR=blue, RR=orange)
# Qualifier (low/high/elevated) conveyed by text label, not color.
PHASE_COLORS = {
    "normal":       "#10B981",   # green (only used for "within normal range" bar)
    "low_hr":       "#DC2626",   # red — HR family (R15 C1)
    "very_low_hr":  "#DC2626",   # red — HR family (R15 C1)
    "elevated_hr":  "#DC2626",   # red — HR family (R15 C1)
    "high_hr":      "#DC2626",   # red — HR family (R15 C1)
    "very_high_hr": "#DC2626",   # red — HR family (R15 C1)
    "elevated_rr":  "#3B82F6",   # blue — RR family (R15 C1)
    "high_rr":      "#3B82F6",   # blue — RR family (R15 A2 + C1)
    "very_high_rr": "#3B82F6",   # blue — RR family (R15 A2 + C1)
}

STATS_LABELS = {
    "Avg HR (bpm)":  "Heart Rate Avg (bpm)",
    "Min HR (bpm)":  "Heart Rate Min (bpm)",
    "Max HR (bpm)":  "Heart Rate Max (bpm)",
    "Avg RR (brpm)": "Breathing Rate Avg (breaths/min)",
    "Min RR (brpm)": "Breathing Rate Min (breaths/min)",
    "Max RR (brpm)": "Breathing Rate Max (breaths/min)",
}

SEVERITY_BAND_PHRASES = {
    "S0": "Brief deviation; continue monitoring",
    "S1": "Sustained deviation; review context",
    "S2": "Sustained pattern; consider provider review",
    "S3": "Critical sustained pattern; urgent review advised",
}


# ── Settings Object ─────────────────────────────────────────────────────────

class Settings(BaseSettings):
    # ── Data source ──────────────────────────────────────────────────────
    excel_path: str = Field(
        default=str(_DATA_DIR.resolve()),
        description="Path to the Excel vitals directory (Code/data/).",
    )

    # ── Timezone ─────────────────────────────────────────────────────────
    default_timezone: str = Field(default="US/Central", description="Default timezone for interpreting naive timestamps.")

    # ── Episode detection thresholds ─────────────────────────────────────
    brady_hr_avg: float = Field(default=45.0, description="HR avg < threshold → low HR.")
    severe_brady_min: float = Field(default=40.0, description="HR min < threshold → severe low HR.")
    elevated_hr_avg: float = Field(default=95.0, description="HR avg > threshold → elevated HR (sustained). R15 A1: was 80, now 95.")
    tachy_hr_avg: float = Field(default=100.0, description="HR avg > threshold → high HR.")
    very_high_hr_avg: float = Field(default=110.0, description="HR avg > threshold → very high HR.")
    tachy_rr_avg: float = Field(default=24.0, description="RR avg > threshold → elevated breathing.")
    high_rr_avg: float = Field(default=30.0, description="RR avg > threshold → high breathing. R15 A2 (new tier).")
    very_high_rr_avg: float = Field(default=40.0, description="RR avg > threshold → very high breathing. R15 A2 (Medicare 40+ threshold).")

    # ── Data quality ─────────────────────────────────────────────────────
    low_confidence_cnt_threshold: int = Field(default=30, description="cnt below this ⇒ low-confidence hour.")

    # ── Data Resolution Inference (hours) ────────────────────────────────
    res_15min_max: float = 0.25
    res_hourly_max: float = 1.5
    res_multihour_max: float = 6.0

    # ── Activity Trend Thresholds (hours detected per day) ───────────────
    activity_high_min: int = 20
    activity_medium_min: int = 12

    activity_green_threshold: int = 20
    activity_amber_threshold: int = 12
    monitoring_target_hours: int = 12
    activity_color_green: str = "#27864A"
    activity_color_amber: str = "#D4850A"
    activity_color_red: str = "#C0392B"

    # Quality Gate Thresholds
    gate_coverage_reject: float = Field(default=0.30, description="Reject if data coverage < N.")
    gate_coverage_warn: float = Field(default=0.50, description="Warn if data coverage < N.")
    gate_min_days: int = Field(default=3, description="Reject if total days of data < N.")
    gate_conf_reject_ratio: float = Field(default=0.50, description="Reject if low-confidence hours ratio > N.")
    gate_conf_warn_ratio: float = Field(default=0.25, description="Warn if low-confidence hours ratio > N.")
    gate_max_gap_hours: int = Field(default=72, description="Reject if max data gap > N hours.")

    # Coverage decline narrative
    coverage_decline_threshold_pct: float = Field(default=40.0, description="Emit coverage decline note only when drop exceeds this %.")

    # ── Episode detection ────────────────────────────────────────────────
    # R15 B2: aligned to 1 hour per Sajol's explicit confirmation on the call.
    # Was 2; reduced to match the documented rule shown to clinicians.
    episode_merge_gap_hours: int = Field(default=1, description="Merge consecutive episodes separated by ≤ N hours of normal.")
    episodic_event_min_hours: int = 1

    # ── Severity scoring weights ─────────────────────────────────────────
    base_severe_brady: int = Field(default=5, description="Base severity weight for Severe Bradycardia.")
    base_low_hr: int = Field(default=3, description="Base severity weight for Bradycardia.")
    base_elevated_hr: int = Field(default=2, description="Base severity weight for Elevated HR.")
    base_high_hr: int = Field(default=3, description="Base severity weight for Tachycardia.")
    base_very_high_hr: int = Field(default=5, description="Base severity weight for Very High HR.")
    base_elevated_rr: int = Field(default=2, description="Base severity weight for Tachypnea.")
    base_high_rr: int = Field(default=3, description="Base severity weight for High RR. R15 A2.")
    base_very_high_rr: int = Field(default=5, description="Base severity weight for Very High RR. R15 A2.")
    duration_bonus_per_hour: int = Field(default=1, description="+N per hour beyond the first.")
    coupling_bonus: int = Field(default=2, description="+N if HR/RR co-occur in same window.")
    low_conf_penalty: int = Field(default=1, description="-N if any hour in episode is low confidence.")

    # ── Severity band boundaries ─────────────────────────────────────────
    band_s1_min: int = Field(default=5, description="Score >= N → S1 band.")
    band_s2_min: int = Field(default=9, description="Score >= N → S2 band.")
    band_s3_min: int = Field(default=13, description="Score >= N → S3 band.")

    # ── Triage rules ─────────────────────────────────────────────────────
    red_severe_brady_hours: int = Field(default=4, description="Severe low HR >= N hours → RED.")
    red_elevated_rr_hours: int = Field(default=8, description="Elevated breathing >= N hours → RED.")
    red_coupled_severity: int = Field(default=9, description="Coupled + max severity >= N → RED.")
    yellow_min_severity: int = Field(default=5, description="Max severity >= N → YELLOW.")

    # ── Critical single-value overrides (instant RED) ─────────────────────
    critical_hr_low: int = Field(default=38, description="Any hour HR < N → instant RED.")
    critical_hr_high: int = Field(default=120, description="Any hour HR > N → instant RED.")
    critical_rr_high: int = Field(default=32, description="Any hour RR > N → instant RED.")

    # ── Trend assessment rules ───────────────────────────────────────────
    progressive_min_severity: int = Field(default=9, description="Max severity >= N → Progressive trend.")
    progressive_coupled_hours: int = Field(default=10, description="Coupled + total hours > N → Progressive trend.")
    intermittent_min_severity: int = Field(default=5, description="Max severity >= N → Intermittent trend.")
    intermittent_min_hours: int = Field(default=5, description="Total episode hours > N → Intermittent trend.")

    # ── Report caps and formatting ───────────────────────────────────────
    # R22.D: header line includes start/end dates so the date span replaces
    # the "62 days" line that used to sit above the bar.
    status_timeline_heading: str = "Patient clinical status over {days} days from {start} to {end}"
    sustained_bold_threshold_hours: int = 4
    section_1_heading: str = "SECTION 1 — High Priority Episodic Events and Suggested Actions"
    # Shared rendered width for the candlestick chart Image and the phase
    # strip Table directly above it. Both render paths must read this symbol
    # so the strip tracks the chart automatically if the value is ever
    # parameterized per report type.
    plot_width_inches: float = 7.0
    # R15 F: reduced from 3.5 to 3.0 inches to recover page-1 budget after the
    # R15 additions (longer trajectory line, split burden phrasing, new threshold rows).
    candlestick_height_inches: float = 3.0
    candlestick_dpi: int = 150
    content_width_inches: float = 7.2
    normal_periods_note: str = ""
    timeline_acronym_width_inches: float = 0.5
    timeline_abbreviated_width_inches: float = 1.0
    color_normal_gap: str = "#F0F0F0"
    timeline_bar_height_inches: float = 0.45
    timeline_show_date_axis: bool = True
    histogram_width_inches: float = 7.0
    histogram_height_inches: float = 2.0
    histogram_dpi: int = 150
    activity_width_inches: float = 7.0
    activity_height_inches: float = 2.2
    activity_dpi: int = 150
    chart_title_fontsize: int = 9
    chart_axis_label_fontsize: int = 8
    chart_tick_fontsize: int = 7
    chart_legend_fontsize: int = 6
    chart_annotation_fontsize: int = 7
    
    # Candlestick rendering strategy thresholds
    # R15 D1: bumped daily threshold from 21 to 90 — periods <=90d now show daily bars,
    # periods >90d aggregate to weekly. Future state may add a 30/60/90 selector.
    # R17 M1: bumped 90 → 91 to absorb the inclusive-day off-by-one. The R17 90DayPeriod
    # auto-detected window resolves to 91 inclusive days (e.g. JB Jul 02 → Sep 30 spans
    # 91 days inclusive of both endpoints), which previously fell into weekly aggregation
    # — defeating the point of a standalone 90-day report. Minimum-targeted bump; future
    # widening (e.g. to 95) is a config-only change.
    candlestick_daily_max_days: int = 91
    candlestick_weekly_max_days: int = 91
    candlestick_aggregate_above: int = 91
    daily_view_threshold_days: int = 91
    candlestick_long_period_height_inches: float = 3.0
    full_period_allow_3_pages: bool = True
    full_period_three_page_threshold_days: int = 90
    color_episode_red: str = "#C0392B"

    PHASE_ACRONYMS: dict = {
        'low_hr':       'LHR',
        'very_low_hr':  'VLHR',
        'elevated_hr':  'EHR',
        'high_hr':      'HHR',
        'very_high_hr': 'VHHR',
        'elevated_rr':  'EB',
        'high_rr':      'HB',
        'very_high_rr': 'VHB',
    }

    PHASE_SINGLE_LETTERS: dict = {
        'low_hr':       'L',
        'very_low_hr':  'V',
        'elevated_hr':  'E',
        'high_hr':      'H',
        'very_high_hr': 'X',
        'elevated_rr':  'B',
        'high_rr':      'Y',
        'very_high_rr': 'Z',
    }

    timeline_single_letter_width_inches: float = 0.3

    hr_gridline_values: list = [40, 50, 60, 70, 80, 90, 100, 110]
    rr_gridline_values: list = [10, 15, 20, 25, 30, 35]
    gridline_color: str = "#DDDDDD"
    gridline_width: float = 0.4
    gridline_alpha: float = 0.6

    # Candlestick severity color gradient (FIX 34)
    candlestick_color_normal: str = "#2C5F8A"
    candlestick_color_mild: str = "#F4B860"
    candlestick_color_moderate: str = "#E8843C"
    candlestick_color_severe: str = "#C0392B"
    candlestick_color_critical: str = "#8B1A1A"
    candlestick_mild_threshold_hours: int = 1
    candlestick_moderate_threshold_hours: int = 3
    candlestick_severe_threshold_hours: int = 6
    candlestick_critical_threshold_hours: int = 12
    candlestick_normal_linewidth: float = 1.8
    candlestick_mild_linewidth: float = 2.4
    candlestick_moderate_linewidth: float = 3.0
    candlestick_severe_linewidth: float = 3.8
    candlestick_critical_linewidth: float = 4.5

    max_events_table: int = Field(default=6, description="Maximum rows in the events table (sorted by severity).")
    max_actions: int = Field(default=5, description="Maximum suggested action bullets.")
    chart_dpi: int = Field(default=200, description="Chart resolution in DPI.")
    hr_spread_annotation_min: float = Field(default=20.0, description="Only annotate histogram spread if P5-P95 exceeds this value (bpm).")

    # ── Batch Summary (Round 14 A1, R15 C3 font shrink for 1-page fit) ─
    # R15 C3: collapsed coverage cell to single line — was "{rec}\nout of {exp} ({pct}%)"
    # which forced two-line cell heights and overflowed 20 rows past page 1.
    # R16 J2: replaced "/" with " of " for readability — Sajol called the slash
    # form hard to scan on the Apr 27 call.
    # R18 B2: hours → days, 0 decimals. Caller computes recorded_days/expected_days
    # from the hour totals so the cell reads "38 of 52d (73%)".
    batch_summary_coverage_format: str = "{recorded_days} of {expected_days}d ({pct:.0f}%)"
    batch_summary_vitals_decimal_places: int = 0
    batch_summary_episodes_per_day_round: int = 0
    # R15 C3: shrink font sizes to ensure all 20 patient-report rows fit on 1 page.
    # Padding also tightened (4pt → 2pt) in batch_generate.build_summary_pdf.
    batch_summary_header_font_size: float = 6.5   # was 7.5
    batch_summary_body_font_size: float = 6.0     # was 7.0
    batch_summary_subtitle_font_size: float = 7.5 # was 8.5
    batch_summary_footer_font_size: float = 5.5   # was 6.5
    # R16 J3: Comments column standardized to a single template family, keyed by
    # dominant phase type. Replaces the prior mix of patient-id-hardcoded enrichment,
    # generic guidance text, and ad-hoc descriptors.
    # R18 B3: parenthetical wraps to a second line via <br/> so the cell reads
    # "Sustained very high HR" / "(avg 65, peak 148)" — Sajol asked for this on
    # the May 4 review. Greens and S(Chair) (special-case) keep single line.
    batch_summary_comment_templates: dict = Field(default={
        "stable":       "Stable baseline",
        "very_low_hr":  "Sustained very low HR<br/>(avg {avg_hr}, min {min_hr})",
        "low_hr":       "Sustained low HR<br/>(avg {avg_hr}, min {min_hr})",
        "elevated_hr":  "Sustained elevated HR<br/>(avg {avg_hr}, peak {peak_hr})",
        "high_hr":      "Sustained high HR<br/>(avg {avg_hr}, peak {peak_hr})",
        "very_high_hr": "Sustained very high HR<br/>(avg {avg_hr}, peak {peak_hr})",
        "elevated_rr":  "Sustained elevated breathing<br/>(avg {avg_rr}, peak {peak_rr})",
        "high_rr":      "Sustained high breathing<br/>(avg {avg_rr}, peak {peak_rr})",
        "very_high_rr": "Sustained very high breathing<br/>(avg {avg_rr}, peak {peak_rr})",
    })
    # R16: column widths rebalanced so headers and patient names don't wrap mid-word.
    # "Triage", "Coupled", "Yellow", "Wimberley", "RSanchez", "FullPeriod",
    # "CriticalWeek" all fit in their cells at the current 6pt body / 6.5pt header.
    # Comments column shrunk slightly (0.30 → 0.27) to free up space; total = 1.00.
    # R18 B1 + N3: column width rebalance per Sajol May 4 review.
    # - Period 0.13 fits "Apr 26 → Sep 30" on one line
    # - Coverage 0.115 fits "147 of 171d (86%)" on one line
    # - Report Type 0.09 fits "CriticalWeek" (12 chars) at 6pt body
    # - Coupled 0.085 fits "Coupled" header on one line at 6.5pt bold (R18 N3:
    #   was 0.07, header still wrapped to "Couple d" — bumped to 0.085)
    # - Comments 0.18 — comments wrap via <br/> per B3, so two lines × ~28 chars fit
    # Total = 1.000 (unchanged).
    batch_summary_column_widths: dict = Field(default={
        "number": 0.035,
        "patient": 0.085,
        "report_type": 0.09,
        "period": 0.13,
        "coverage": 0.115,
        "hr": 0.04,
        "rr": 0.04,
        "episodic_burden": 0.085,
        "episodes_per_day": 0.05,
        "coupled": 0.085,
        "triage": 0.065,
        "comments": 0.18,
    })

    # ── Phase Strip Data Gaps (Round 14 B2) ────────────────────────────
    phase_strip_min_gap_hours: int = Field(default=24, description="Coalesce no-data gaps shorter than N hours into surrounding gray.")
    phase_strip_no_data_color: str = "#FFFFFF"   # white — sensor offline
    phase_strip_no_episode_color: str = "#E8E8E8" # gray — data present, no episodes

    # ── Phase Numbering (Round 14 C1-C3) ─────────────────────────────
    phase_strip_show_numbers: bool = True
    phase_strip_number_font_size: float = 7.0
    phase_strip_number_color: str = "#FFFFFF"  # white text on colored blocks

    # ── Phase Strip Labels (Round 14 D1) ─────────────────────────────
    phase_strip_label_full: dict = Field(default={
        "low_hr":       "Low Heart Rate",
        "elevated_hr":  "Elevated Heart Rate",
        "high_hr":      "High Heart Rate",
        "very_high_hr": "Very High Heart Rate",
        "very_low_hr":  "Very Low Heart Rate",
        "elevated_rr":  "Elevated Breathing",
        "high_rr":      "High Breathing",
        "very_high_rr": "Very High Breathing",
    })
    phase_strip_label_abbrev: dict = Field(default={
        "low_hr":       "Low HR",
        "elevated_hr":  "Elev HR",
        "high_hr":      "High HR",
        "very_high_hr": "V.High HR",
        "very_low_hr":  "V.Low HR",
        "elevated_rr":  "Elev Br",
        "high_rr":      "High Br",
        "very_high_rr": "V.High Br",
    })
    phase_strip_abbrev_width_threshold_inches: float = 0.55  # below this, use abbrev
    phase_strip_min_text_width_inches: float = 0.15   # below this, use sub-threshold indicator
    phase_strip_narrow_font_size: float = 5.0          # font for segments 0.15-0.30 inches
    phase_strip_subthreshold_indicator: str = "•"      # shown on segments too narrow for text
    phase_strip_number_repetition: str = "all_matches"  # "all_matches" or "first_only"

    # ── Phase Strip Day Coloring Mode (Round 14 F) ───────────────────────
    phase_strip_day_coloring_mode: str = "episode_hours"  # "episode_hours" or "phase_window"
    phase_strip_min_episode_hours_per_day: int = 1         # day needs >= N episode hours to color
    phase_strip_episode_merge_max_gap_days: int = 1        # merge episode days within N days of each other

    # ── Phase Strip Index Legend (Round 14 G) ─────────────────────────
    phase_strip_index_enabled: bool = True
    phase_strip_index_line1: list = Field(default=[
        {"swatch_color": "hr", "label": "HR episode"},
        {"swatch_color": "rr", "label": "RR episode"},
        {"swatch_color": "no_episode", "label": "Recorded, no episode"},
        {"swatch_color": "no_data", "label": "No data", "border": True},
    ])
    # R20.B: removed legacy "Episode day 1, 2, 3 See events table" entries.
    # The events table on page 1 already enumerates episodes via the # column;
    # cross-reference is trivial without the legend hint. Sajol's Round 19 review
    # flagged the entries as referencing markers that collide on dense clusters.
    # Per R18 D, narrow strip segments now render as "(#N)" \u2014 the cross-reference
    # is built into the strip label itself.
    phase_strip_index_line2: list = Field(default=[])
    phase_strip_index_font_size: float = 5.0
    phase_strip_index_text_color: str = "#666666"
    phase_strip_index_swatch_size_pt: float = 5.0

    # ── Phase Strip Colors (Round 14 D2 → R15 C1: flipped to HR=red, RR=blue) ──
    phase_strip_color_by_condition_type: dict = Field(default={
        "hr": "#DC2626",       # red family for all HR conditions (R15 C1)
        "rr": "#3B82F6",       # blue family for all RR conditions (R15 C1)
    })

    # ── Trajectory Line Templates (Round 14 A2 → R15 B4: explicit current dates on CriticalWeek) ──
    trajectory_line_template_fullperiod: str = "{early_count} episodes in first {window_days} days → {late_count} episodes in last {window_days} days"
    trajectory_line_template_criticalweek: str = "{early_count} episodes in prior window {prior_start} to {prior_end} → {late_count} episodes this window {current_start} to {current_end}"
    # R17 D: 90DayPeriod trajectory templates. With-prior is used when ≥90 days
    # of history exists before the active window; within-window is the fallback
    # (first 30 vs last 30 days inside the active 90-day window).
    trajectory_line_template_90day_with_prior: str = "{early_count} episodes in prior 90 days {prior_start} to {prior_end} → {late_count} episodes in this window {current_start} to {current_end}"
    trajectory_line_template_90day_within_window: str = "{early_count} episodes in first 30 days → {late_count} episodes in last 30 days"
    trajectory_line_template_firstreport: str = "first reporting period with detected episodes; no prior comparison available"

    # R15 B3: Trajectory ratio multiplier appended to trajectory line.
    # Stable when |ratio - 1| < (threshold - 1), e.g. threshold 1.1 means within ±10% of 1.0 → no ratio shown.
    trajectory_ratio_template_increase: str = " ({ratio:.1f}x increase)"
    trajectory_ratio_template_decrease: str = " ({ratio:.1f}x decrease)"
    trajectory_ratio_threshold_stable: float = 1.1

    # R15 B1: Episodic Burden phrasing — split into two sentences.
    # R18 C1: trailing word changed "total hours." → "total." so the sentence reads
    # naturally for both below-threshold ("spanning 45h total.") and above-threshold
    # ("spanning 4 days total.") forms after format_hours_or_days returns days-only
    # for long durations.
    episodic_burden_template: str = "{count} episodic events Detected, spanning {hours_str} total."
    episodic_burden_conditions_template: str = "Conditions: {condition_list}."

    # R15 B5: Hours-to-days display threshold for narrative text only.
    # Above this, append "(~N days)" to hour counts. Events table stays in pure hours.
    hours_to_days_display_threshold: int = 72
    # R18 C1: above-threshold form is "{days} days" only (was "{hours}h (~{days} days)").
    # Sajol asked for days-only when crossing the 72h threshold — the compound
    # form was redundant once the duration is long enough to warrant days.
    hours_to_days_display_format: str = "{days} days"

    # ── Weekly Trend Severity Bands (Round 14 A3) ────────────────────────
    weekly_trend_severity_bands: list = Field(default=[
        {"label": "Normal week",             "min_hours": 0,  "max_hours": 1,    "color_key": "candlestick_color_normal"},
        {"label": "Brief events (1-5h)",     "min_hours": 1,  "max_hours": 5,    "color_key": "candlestick_color_mild"},
        {"label": "Moderate (5-12h)",        "min_hours": 5,  "max_hours": 12,   "color_key": "candlestick_color_moderate"},
        {"label": "Severe (12-24h)",         "min_hours": 12, "max_hours": 24,   "color_key": "candlestick_color_severe"},
        {"label": "Critical (24h+)",         "min_hours": 24, "max_hours": None,  "color_key": "candlestick_color_critical"},
    ])

    # ── LLM ──────────────────────────────────────────────────────────────
    use_llm: bool = Field(default=False, description="Toggle AI narrative (True) vs deterministic (False).")
    llm_model: str = Field(default="claude-sonnet-4-6", description="Anthropic model name.")
    anthropic_api_key: str = Field(default="", description="Anthropic API key.")

    # ── App meta ─────────────────────────────────────────────────────────
    app_version: str = "1.0.0"

    model_config = {"env_file": str(_PROJECT_ROOT / ".env"), "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()


# ── Render Config (Round 10 — Single Source of Truth) ───────────────────────
# All downstream rendering and analysis modules read from this dict.
# No hardcoded thresholds, labels, or phrases elsewhere.

RENDER_CONFIG = {
    "phase_strip": {
        "max_phases_by_period_days": [
            {"max_days": 7,    "max_phases": 6},
            {"max_days": 30,   "max_phases": 8},
            {"max_days": 90,   "max_phases": 10},
            {"max_days": 9999, "max_phases": 12},
        ],
        "label_abbreviations": {
            "Elevated Heart Rate":  "Elev HR",
            "Elevated Breathing":   "Elev Br",
            "Low Heart Rate":       "Low HR",
            "High Heart Rate":      "High HR",
            "Very High Heart Rate": "V.High HR",
            "Very Low Heart Rate":  "V.Low HR",
            "High Breathing":       "High Br",
            "Very High Breathing":  "V.High Br",
        },
        "min_chars_before_abbreviate": 6,
        "min_chars_before_initialize": 3,
        "merge_label_for_overflow": "Mixed activity",
        "date_label_rotation_deg": 30,
        "date_label_min_spacing_px": 40,
        "date_label_thin_factor": 2,
        "empty_strip_fallback_text": "No episodic events in period",
    },

    "trajectory": {
        "min_prior_coverage_pct": 40,
        "min_prior_hours_absolute": 48,
        "insufficient_text": "Trajectory unavailable: prior window coverage below {pct}%",
        "suppress_arrow_when_insufficient": True,
    },

    "events_table": {
        "longest_continuous_header": "Longest Continuous",
        "total_hours_header":        "Total Hours",
        "average_header":            "Average",
        "peak_header":               "Min/Max",
        "show_both_columns": True,
        # R18 N2 (revised): max_rows back to 6 for real phase rows. Brief rows
        # (R18 C3) bypass this cap in pdf_render so they always render as
        # visible rows. First attempt bumped to 7 globally, but PHolst 90DayPeriod
        # and RSanchez 90DayPeriod (fallback reports with extra header note)
        # spilled to page 3. Bypass-the-cap approach gives brief rows visibility
        # without the page-fit risk.
        "max_rows": 6,
        "overflow_template": "+ {n} additional condition(s) across {conditions}; details in monitoring data.",
        # R22.D: Episodes/day inserted between Longest Continuous and Average,
        # per Sajol May 5 call ("Why would we not put it here? It's a pretty
        # powerful statement, right? When you see four episodes a day").
        # Date column tightened (0.26 → 0.18) to make room; total = 1.00.
        "columns": [
            {"key": "number",              "label": "#",                   "width": 0.04},
            {"key": "category",            "label": "Episode",             "width": 0.19},
            {"key": "peak",                "label": "Min/Max",             "width": 0.11},
            {"key": "total_hours",         "label": "Total Hours",         "width": 0.11},
            {"key": "longest_continuous",  "label": "Longest Continuous",  "width": 0.13},
            {"key": "episodes_per_day",    "label": "Episodes/day",        "width": 0.10},
            {"key": "average",             "label": "Average",             "width": 0.12},
            {"key": "date",                "label": "Date",                "width": 0.20},
        ],
        "priority_order": [
            "very_high_hr", "very_low_hr", "high_hr", "low_hr",
            "elevated_hr", "very_high_rr", "high_rr", "elevated_rr",
        ],
    },

    "monitoring_decline": {
        "min_pct_drop": 30,
        "min_period_days": 14,
    },

    # R12 Fix 5: Physiologic plausibility bounds.
    # HR retains both bounds for clipping rare extreme sensor noise.
    # R22.B: RR upper-bound clipping reversed per Sajol May 5 call. The (6, 60)
    # range is kept here for the descriptive quality_gates note only — values
    # outside are no longer rewritten to render as ">60*" / "<6*".
    "physiologic_bounds": {
        "hr_bpm":  {"min": 25, "max": 220},
        "rr_brpm": {"min": 6,  "max": 60},
    },

    # R17 C: header note appended to 90DayPeriod reports for patients whose
    # monitoring window is shorter than 90 days. The note appears between the
    # meta-line band and the standard disclaimer.
    "fallback_note_90day": (
        "Patient's monitoring period was less than 90 days; this report "
        "covers all available data."
    ),
    # R24.3 — analogous fallback note for the 30 day report type when the
    # patient has fewer than 30 days of monitoring data.
    "fallback_note_30day": (
        "Patient's monitoring period was less than 30 days; this report "
        "covers all available data."
    ),

    "nocturnal_heuristic": {
        "exclude_episodes_longer_than_hours": 12,
        "night_hour_range": [20, 6],
        "min_episodes_for_pattern_claim": 3,
        "min_fraction_for_pattern_claim": 0.6,
    },

    "spread_annotation": {
        "min_spread_bpm": 20,
        # R19 B: metric-specific spread thresholds. Sajol May 4 review asked why
        # RR spread observation rarely appeared. HR P5-P95 commonly exceeds 20
        # but RR P5-P95 typically sits at 8-12 brpm. Lowering RR threshold to 10
        # surfaces breathing variability for the cases that warrant it (e.g.
        # Wimberley) while still rare-trigger semantic.
        "min_spread_by_metric": {
            "hr": 20,
            "rr": 10,
        },
        "min_sample_hours": 168,
        "annotation_template": "P5 to P95 spread {spread} bpm ({p5} to {p95})",
    },

    "clinical_guidance": {
        "min_specificity_tokens": 3,
        "required_token_categories": ["condition", "count_or_duration", "suggested_assessment"],
        "fallback_template": (
            "{count} {condition} episodes detected over {hours}h. "
            "Assess {assessment_focus}; correlate with clinical context."
        ),
        "assessment_focus_by_condition": {
            "Low Heart Rate":       "symptoms, blood pressure, and rate controlling medications",
            "Elevated Heart Rate":  "pain, hydration, infection, and activity correlation",
            "High Heart Rate":      "pain, hydration, infection, and activity correlation",
            "Very High Heart Rate": "symptoms, blood pressure, and possible arrhythmia",
            "Elevated Breathing":   "respiratory status and possible underlying cause",
            "High Breathing":       "respiratory status, oxygen levels, and infection",
            "Very High Breathing":  "respiratory status, oxygen levels, and acute respiratory compromise",
        },
        "dominance_threshold": 0.60,
        "CLINICAL_GUIDANCE_LINES": {
            "GREEN":  "Routine monitoring is appropriate.",
            "YELLOW": "Closer clinical observation is suggested.",
            "RED":    "Urgent provider review advised (per protocol).",
        },
        "mixed_templates": {
            "RED":    "Urgent: high episodic burden detected ({count} events, {hours}h). Provider review advised within 24 hours.",
            "YELLOW": "Multi-condition episodic burden ({count} events, {hours}h). Closer observation suggested.",
            "GREEN":  "Routine monitoring. No specific intervention indicated.",
        },
    },

    "coverage": {
        "always_split_multi_sensor": True,
        "format_template": "{sensor}: {hours}/{total}h ({pct}%)",
        "sensor_display_names": {
            "bed":   "Bed",
            "chair": "Chair",
            "wrist": "Wrist",
        },
    },

    "pattern_observations": {
        "skip_worsening_if_delta_under": 5,
        "skip_worsening_if_prior_coverage_pct_under": 40,
        # R12 Fix 7: Hash-based phrase variation removed. One canonical phrase.
        "max_observations": 3,
        # R13 Fix 3: Clustered pattern dual signal
        "clustered_temporal_max_ratio": 0.30,   # events concentrated in ≤30% of days
        "clustered_intensity_min": 3.0,          # or ≥3 events per active day
        "continuous_min_ratio": 0.70,            # events on ≥70% of days
        # R13 Fix 4: Coupled pattern overlap
        "coupled_min_overlap_hours": 2,
        "coupled_min_overlap_count": 1,
        # R12 Fix 9: Deterministic priority scores for observation ranking.
        "priority_scores": {
            "coupled":               100,
            "monitoring_decline":     90,
            "sustained_finding":      80,
            "high_variability":       70,
            "high_rr_variability":    68,  # R19 B: ranks just below HR variability
            "continuous":             65,
            "clustered":              60,
            "nocturnal":              50,
            "daytime":                40,
            "worsening_trajectory":   30,
            "improving_trajectory":   30,
        },
    },
}


# ── Multi-Sensor Patient Groups ─────────────────────────────────────────────
# Devices that belong to the same physical patient.
# Imported here from data_registry_v2 so the rest of the backend can reference
# config.PATIENT_GROUPS without importing from the registry directly.
from .data_registry_v2 import PATIENT_GROUPS  # noqa: E402
# Post-initialization helpers
_PHASE_COLOR_MAP = PHASE_COLORS
