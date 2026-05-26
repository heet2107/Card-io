"""
CardioReport – Pydantic Models
Shared request / response schemas.

Rule: Only fields that are truly optional should have defaults.
Every field the pipeline MUST compute has no default, so missing
computation causes an immediate validation error.
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field


# ── Request ──────────────────────────────────────────────────────────────────

class ReportRequest(BaseModel):
    patient_id: str
    range_type: Literal[
        "last_24h", "last_7d", "last_15d", "last_1m", "last_3m", "custom", "smart_week"
    ]
    start: Optional[str] = None  # ISO date string for custom range
    end: Optional[str] = None
    use_ai: bool = False         # Toggle AI-powered narrative


# ── Domain Objects ───────────────────────────────────────────────────────────

class VitalStats(BaseModel):
    mean: float
    min: float
    max: float
    p5: float
    p95: float


class StatsRow(BaseModel):
    """A single row in the 6-row stats table."""
    label: str
    mean: float
    min: float
    max: float
    p5: float
    p95: float


class FullStatsTable(BaseModel):
    """Fixed 6-row × 5-column stats table per Implementation Guide."""
    rows: list[StatsRow] = []


class DataQuality(BaseModel):
    low_confidence_hours: int
    gap_hours: int
    expected_hours: int
    total_hours: int
    quality_pct: float  # (total - low_conf) / expected * 100


class Episode(BaseModel):
    condition: str  # Bradycardia | Severe Bradycardia | Tachycardia | Tachypnea
    start_time: str
    end_time: str
    duration_hours: int
    key_vitals: str
    confidence: Literal["high", "medium", "low"]
    cooccurrence: bool = False
    severity_score: int = 0
    severity_band: str = "S0"
    concern_phrase: str = ""
    qualifier_phrase: str = ""


class EpisodeRollups(BaseModel):
    counts_by_type: dict[str, int] = {}
    total_events: int = 0
    events_per_day: float = 0.0
    cluster_ratio: float = 0.0
    coupled_fraction: float = 0.0
    late_vs_early_ratio: float = 1.0


# ── NEW: Positional / Activity Models ────────────────────────────────────────

class PositionalVitals(BaseModel):
    location: str
    hr_avg: float
    rr_avg: float
    hours: int


class PositionalComparisonTable(BaseModel):
    rows: list[PositionalVitals] = []
    br_diff_living_vs_chair: float = 0.0


class ActivityDay(BaseModel):
    date: str
    hours: float
    color: str  # Red, Amber, Green


class ActivityTrend(BaseModel):
    days: list[ActivityDay] = []
    rolling_avg_7d: list[float] = []


# ── Bed Sensor Models ────────────────────────────────────────────────────────

class BedDaySummary(BaseModel):
    """Single day of bed sensor data."""
    date: str
    hours_in_bed: float
    hr_min: float = 0.0
    has_alert: bool = False
    color: str = "green"  # green (<13h), amber (13-16h), red (>16h)


class BedActivitySummary(BaseModel):
    """Aggregated bed activity summary for the report."""
    mean_daily_hours: float = 0.0
    min_hours: float = 0.0
    max_hours: float = 0.0
    days_above_16h: int = 0
    alert_days: int = 0
    total_alerts: int = 0
    hr_min_high_bed_days: float = 0.0    # avg HR min on days with >16h bed time
    hr_min_normal_days: float = 0.0      # avg HR min on days with <=16h bed time
    daily_data: list[BedDaySummary] = []


class Phase(BaseModel):
    """A clinically distinct phase within the monitoring window."""
    type: str               # stable | low_hr | high_hr | mixed
    start_date: str
    end_date: str
    days: int
    label: str              # e.g. "Phase 1: Stable"
    date_range: str = ""    # e.g. "Jun 24 to Jun 26"


class PriorComparison(BaseModel):
    hr_avg: float = 0.0
    rr_avg: float = 0.0
    episode_count: int = 0
    start_date: str = ""
    end_date: str = ""


# ── Response ─────────────────────────────────────────────────────────────────

class ReportResponse(BaseModel):
    patient_id: str
    window_start: str
    window_end: str
    report_date: str
    data_resolution: str       # REQUIRED — computed by signal_engine.compute_data_resolution()
    coverage_summary: str = "" # NEW: e.g. "122/168h, 72.6%"
    disclaimer: str = "Decision-support summary derived from longitudinal vital sign trends; interpret in clinical context."

    hr_summaries: Optional[VitalStats] = None
    rr_summaries: Optional[VitalStats] = None
    data_quality: DataQuality

    episodes: list[Episode] = []
    episode_rollups: EpisodeRollups = EpisodeRollups()

    triage: str                        # REQUIRED — computed by signal_engine.compute_triage()
    trend_assessment: str              # REQUIRED — computed by signal_engine.compute_trend_assessment()
    overall_action_posture: str        # REQUIRED — computed by signal_engine.compute_action_posture()

    narrative: str                     # REQUIRED — computed by narrative_ai
    suggested_actions: list[str] = []

    full_stats: Optional[FullStatsTable] = None  # 6-row × 5-col stats table
    max_severity_score: int = 0

    # Robustness spec fields
    report_priority: str = "LOW"             # HIGH | MEDIUM | LOW | SKIP
    phases: list[Phase] = []                 # Phase detection results
    quality_warnings: list[str] = []         # Quality gate warnings

    positional_comparison: Optional[PositionalComparisonTable] = None
    activity_trend: Optional[ActivityTrend] = None
    use_ai: bool = False
    narrative_source: str = "Rule-based phrase taxonomy"  # or "AI-generated (GPT-4o, constrained prompt)"
    prior_comparison: Optional[PriorComparison] = None

    # Bed sensor specific
    sensor_type: str = "chair"       # "bed" or "chair"
    bed_summary: Optional[BedActivitySummary] = None

    chart_hr_b64: str = ""   # base64-encoded PNG
    chart_rr_b64: str = ""
    chart_combined_b64: str = ""
    chart_histogram_b64: str = ""  # histogram chart
    chart_positional_b64: str = ""  # positional comparison chart
    chart_activity_b64: str = ""   # activity trend chart
    chart_bed_hours_b64: str = ""  # bed hours chart (bed sensor only)
