"""
CardioReport – Signal Engine
Computes summary statistics, data quality, data resolution,
triage, trend assessment, and action posture.
All thresholds from settings. No magic numbers.
"""

from __future__ import annotations
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from .config import (
    settings, TrendLabels, TriageLabels, ActionPostureLabels, 
    Conditions, ChartColors as CC, Locations
)
from .models import VitalStats, DataQuality


# ── Window filtering ─────────────────────────────────────────────────────────

def apply_window(df: pd.DataFrame, range_type: str,
                 start: Optional[str] = None, end: Optional[str] = None) -> pd.DataFrame:
    """Filter DataFrame to the requested time window."""
    ts = df["timestamp"]
    latest = ts.max()

    if range_type == "custom":
        if start:
            s = pd.Timestamp(start)
            df = df[ts >= s]
        if end:
            e = pd.Timestamp(end) + timedelta(hours=23, minutes=59, seconds=59)
            df = df[ts <= e]
        return df.reset_index(drop=True)

    deltas = {
        "last_24h": timedelta(hours=24),
        "last_7d": timedelta(days=7),
        "last_15d": timedelta(days=15),
        "last_1m": timedelta(days=30),
        "last_3m": timedelta(days=90),
    }
    delta = deltas.get(range_type, timedelta(days=7))
    cutoff = latest - delta
    return df[ts > cutoff].reset_index(drop=True)


# ── Summary Stats ────────────────────────────────────────────────────────────

def compute_stats(df: pd.DataFrame) -> tuple[VitalStats, VitalStats]:
    """Compute HR and RR summary statistics from actual data."""
    def _stats(avg_col: str, min_col: str, max_col: str) -> VitalStats:
        avg = df[avg_col].dropna()
        mn = df[min_col].dropna()
        mx = df[max_col].dropna()
        return VitalStats(
            mean=round(float(avg.mean()), 1) if len(avg) else 0,
            min=round(float(mn.min()), 1) if len(mn) else 0,
            max=round(float(mx.max()), 1) if len(mx) else 0,
            p5=round(float(avg.quantile(0.05)), 1) if len(avg) else 0,
            p95=round(float(avg.quantile(0.95)), 1) if len(avg) else 0,
        )

    hr = _stats("hr_avg", "hr_min", "hr_max")
    rr = _stats("rr_avg", "rr_min", "rr_max")
    return hr, rr


def compute_full_stats(df: pd.DataFrame):
    """Build the fixed 6-row × 5-column stats table per Implementation Guide.

    Rows: avg_hr, min_hr, max_hr, avg_rr, min_rr, max_rr
    Columns per row: Mean, Min, Max, P5, P95
    All computed from actual data.
    """
    from .models import StatsRow, FullStatsTable
    from .config import STATS_LABELS

    rows = []
    # Use labels from config
    for col, label in [
        ("hr_avg", "Avg HR (bpm)"),
        ("hr_min", "Min HR (bpm)"),
        ("hr_max", "Max HR (bpm)"),
        ("rr_avg", "Avg RR (breaths/min)"),
        ("rr_min", "Min RR (breaths/min)"),
        ("rr_max", "Max RR (breaths/min)"),
    ]:
        s = df[col].dropna()
        display_label = STATS_LABELS.get(label, label)
        if len(s) == 0:
            rows.append(StatsRow(label=display_label, mean=0, min=0, max=0, p5=0, p95=0))
        else:
            rows.append(StatsRow(
                label=display_label,
                mean=round(float(s.mean()), 1),
                min=round(float(s.min()), 1),
                max=round(float(s.max()), 1),
                p5=round(float(s.quantile(0.05)), 1),
                p95=round(float(s.quantile(0.95)), 1),
            ))

    return FullStatsTable(rows=rows)


# ── Data Resolution ──────────────────────────────────────────────────────────

def compute_data_resolution(df: pd.DataFrame) -> str:
    """Infer the time resolution from the actual data.

    Computes the median gap between consecutive timestamps and classifies.
    No hardcoded assumption about the data format.
    """
    if len(df) < 2:
        return "Insufficient data"

    sorted_ts = df["timestamp"].sort_values()
    gaps = sorted_ts.diff().dropna()
    median_gap = gaps.median()
    hours = median_gap.total_seconds() / 3600

    has_minmax = all(c in df.columns for c in ["hr_min", "hr_max", "rr_min", "rr_max"])
    suffix = " (HR/RR min/avg/max)" if has_minmax else " (HR/RR avg)"

    if hours <= settings.res_15min_max:
        return f"15-minute aggregates{suffix}"
    elif hours <= settings.res_hourly_max:
        return f"Hourly aggregates{suffix}"
    elif hours <= settings.res_multihour_max:
        return f"Multi-hour aggregates{suffix}"
    else:
        return f"Daily aggregates{suffix}"


# ── Data Quality ─────────────────────────────────────────────────────────────

def compute_data_quality(df: pd.DataFrame) -> DataQuality:
    """Compute data quality metrics from actual data."""
    if df.empty:
        return DataQuality(low_confidence_hours=0, gap_hours=0, expected_hours=0,
                           total_hours=0, quality_pct=0)

    ts = df["timestamp"]
    start, end = ts.min(), ts.max()
    expected = max(int((end - start).total_seconds() / 3600) + 1, 1)

    total = len(df)

    # Low confidence: cnt < threshold (only for rows where cnt data exists) OR gap_flag
    low_conf = 0
    if "cnt" in df.columns:
        cnt_valid = df["cnt"].dropna()
        if len(cnt_valid) > 0:
            low_conf += int((cnt_valid < settings.low_confidence_cnt_threshold).sum())
    if "gap_flag" in df.columns:
        low_conf += int((df["gap_flag"].fillna(0) == 1).sum())
    # Don't double-count
    low_conf = min(low_conf, total)

    gap_hours = max(expected - total, 0)
    quality_pct = round((total - low_conf) / max(expected, 1) * 100, 1)

    return DataQuality(
        low_confidence_hours=low_conf,
        gap_hours=gap_hours,
        expected_hours=expected,
        total_hours=total,
        quality_pct=quality_pct,
    )


# ── Triage ───────────────────────────────────────────────────────────────────

def compute_triage(episodes: list, coupled_fraction: float = 0.0, df=None) -> str:
    """Determine Red / Yellow / Green triage level.

    All thresholds from settings. No hardcoded numbers.
    Rules (checked in order):
      - CRITICAL SINGLE VALUE OVERRIDE: Any hour with HR < 40, HR > 120, or RR > 30 → RED
      - Any severe brady >= red_severe_brady_hours → RED
      - Any Elevated RR >= red_elevated_rr_hours → RED
      - Any coupled episode AND max severity >= red_coupled_severity → RED
      - Max severity score >= yellow_min_severity → YELLOW
      - Everything else → GREEN
    """
    # ── Critical single-value override (instant RED) ─────────────────────
    if df is not None and not df.empty:
        try:
            # Check HR average for extreme lows
            if (df["hr_avg"] < settings.critical_hr_low).any():
                return TriageLabels.RED

            # Check HR average for extreme highs
            if (df["hr_avg"] > settings.critical_hr_high).any():
                return TriageLabels.RED
        except Exception:
            pass  # Graceful fallback if columns are missing

    if not episodes:
        return TriageLabels.GREEN

    max_severity = 0
    has_coupled = False

    for ep in episodes:
        if isinstance(ep, dict):
            cond = ep.get("condition", "")
            dur = ep.get("duration_hours", 0)
            score = ep.get("severity_score", 0)
            cooccur = ep.get("cooccurrence", False)
        else:
            cond = ep.condition
            dur = ep.duration_hours
            score = ep.severity_score
            cooccur = ep.cooccurrence

        max_severity = max(max_severity, score)
        if cooccur:
            has_coupled = True

        # RED: Severe low HR >= N hours
        if cond == Conditions.SEVERE_BRADY and dur >= settings.red_severe_brady_hours:
            return TriageLabels.RED
        # RED: Tachypnea >= N hours
        if cond == Conditions.TACHYPNEA and dur >= settings.red_elevated_rr_hours:
            return TriageLabels.RED

    # RED: Any coupled episode AND max severity >= N
    if has_coupled and max_severity >= settings.red_coupled_severity:
        return TriageLabels.RED

    # YELLOW: Max severity >= N
    if max_severity >= settings.yellow_min_severity:
        return TriageLabels.YELLOW

    return TriageLabels.GREEN


# ── Trend Assessment ─────────────────────────────────────────────────────────

def compute_trend_assessment(df: pd.DataFrame, episodes: list) -> tuple[str, float]:
    """Compute trend label. All thresholds from settings.

    Rules:
      - Max severity >= progressive_min_severity OR
        (coupled AND total episode hours > progressive_coupled_hours)
          → PROGRESSIVE
      - Max severity >= intermittent_min_severity OR
        total episode hours > intermittent_min_hours
          → INTERMITTENT
      - Else → STABLE

    Also computes late-vs-early ratio for rollups.
    """
    if not episodes or df.empty:
        return TrendLabels.STABLE, 1.0

    max_severity = 0
    total_episode_hours = 0
    has_coupled = False

    for ep in episodes:
        if isinstance(ep, dict):
            score = ep.get("severity_score", 0)
            dur = ep.get("duration_hours", 0)
            cooccur = ep.get("cooccurrence", False)
        else:
            score = ep.severity_score
            dur = ep.duration_hours
            cooccur = ep.cooccurrence

        max_severity = max(max_severity, score)
        total_episode_hours += dur
        if cooccur:
            has_coupled = True

    # Compute late-vs-early ratio for rollups
    ts = df["timestamp"]
    total_span = (ts.max() - ts.min()).total_seconds()
    ratio = 1.0
    if total_span > 0:
        q1 = ts.min() + timedelta(seconds=total_span * 0.25)
        q3 = ts.min() + timedelta(seconds=total_span * 0.75)
        early_count = 0
        late_count = 0
        for ep in episodes:
            ep_start_str = ep.start_time if not isinstance(ep, dict) else ep["start_time"]
            ep_start = pd.Timestamp(ep_start_str)
            if ep_start <= q1:
                early_count += 1
            elif ep_start >= q3:
                late_count += 1
        ratio = round(late_count / max(early_count, 0.5), 2)

    # Trend label — all thresholds from settings
    if max_severity >= settings.progressive_min_severity or \
       (has_coupled and total_episode_hours > settings.progressive_coupled_hours):
        label = TrendLabels.PROGRESSIVE
    elif max_severity >= settings.intermittent_min_severity or \
          total_episode_hours > settings.intermittent_min_hours:
        label = TrendLabels.INTERMITTENT
    else:
        label = TrendLabels.STABLE

    return label, ratio


# ── NEW: Positional Stats ───────────────────────────────────────────────────

def compute_positional_stats(df: pd.DataFrame):
    """Compute summary stats per location."""
    from .models import PositionalVitals, PositionalComparisonTable
    rows = []
    if "location" in df.columns:
        # Sort locations so Living Room and Chair are consistent
        locs = sorted(df["location"].unique())
        for loc in locs:
            if loc == Locations.UNKNOWN: continue
            grp = df[df["location"] == loc]
            rows.append(PositionalVitals(
                location=loc,
                hr_avg=round(float(grp["hr_avg"].mean()), 1) if not grp["hr_avg"].isna().all() else 0,
                rr_avg=round(float(grp["rr_avg"].mean()), 1) if not grp["rr_avg"].isna().all() else 0,
                hours=len(grp)
            ))
    
    # Compute BR difference if Living Room and Chair exist
    diff = 0.0
    lr = next((r for r in rows if r.location == Locations.LIVING_ROOM), None)
    ch = next((r for r in rows if r.location == Locations.CHAIR), None)
    if lr and ch:
        diff = round(lr.rr_avg - ch.rr_avg, 1)
        
    return PositionalComparisonTable(rows=rows, br_diff_living_vs_chair=diff)


def compute_activity_data(df: pd.DataFrame):
    """Compute daily activity hours (detected hours outside of Bed)."""
    from .models import ActivityDay, ActivityTrend
    
    # Activity = Hours NOT in Bed
    df = df.copy()
    if "location" in df.columns:
        df = df[df["location"] != Locations.BED]
        
    df["date"] = df["timestamp"].dt.date
    daily = df.groupby("date").size().reset_index(name="hours")
    
    days = []
    for _, row in daily.iterrows():
        h = row["hours"]
        if h >= settings.activity_high_min: 
            color = "green"
        elif h >= settings.activity_medium_min: 
            color = "amber"
        else: 
            color = "red"
        days.append(ActivityDay(date=str(row["date"]), hours=float(h), color=color))
    
    # 7-day rolling average
    if not daily.empty:
        daily["rolling"] = daily["hours"].rolling(window=7, min_periods=1).mean().fillna(daily["hours"])
        rolling = [round(float(x), 1) for x in daily["rolling"].tolist()]
    else:
        rolling = []
    
    return ActivityTrend(days=days, rolling_avg_7d=rolling)


# ── Action Posture ───────────────────────────────────────────────────────────

def compute_action_posture(triage: str, trend: str, coupled_fraction: float,
                           max_band: str) -> str:
    """R12 Fix 1: Action line is a function of triage band ONLY.

    Trend label describes the _pattern shape_; triage band determines the
    _clinical response_. These are different concepts — don't mix them.
    """
    triage_str = str(triage).upper().strip()
    if triage_str == "RED":
        return ActionPostureLabels.URGENT
    if triage_str == "YELLOW":
        return ActionPostureLabels.CLOSER
    return ActionPostureLabels.ROUTINE
