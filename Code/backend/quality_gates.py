"""
CardioReport – Quality Gates (Decision 1)
Five validation gates that run BEFORE any report is built.
All thresholds from settings. No magic numbers.
"""

from __future__ import annotations
import pandas as pd
from .config import settings, GateStatus


# ── Gate 1: Coverage ─────────────────────────────────────────────────────────

def validate_coverage(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> tuple[str, str | None]:
    """Coverage = recorded hours / expected hours."""
    expected_hours = max(1, int((end - start).total_seconds() / 3600) + 1)
    recorded_hours = len(df)
    coverage = recorded_hours / expected_hours if expected_hours else 0

    if coverage < settings.gate_coverage_reject:
        return GateStatus.REJECT, f"Insufficient data coverage ({coverage:.0%}, below {settings.gate_coverage_reject:.0%})"
    if coverage < settings.gate_coverage_warn:
        return GateStatus.WARN, f"Low data coverage ({coverage:.0%}); interpret with caution"
    return GateStatus.PASS, None


# ── Gate 2: Minimum days with data ───────────────────────────────────────────

def validate_days(df: pd.DataFrame) -> tuple[str, str | None]:
    """At least `min_days` days must have >= 4 hours of data each."""
    df_copy = df.copy()
    df_copy["_date"] = df_copy["timestamp"].dt.date
    daily_counts = df_copy.groupby("_date").size()
    days_with_enough = len(daily_counts[daily_counts >= 4])

    if days_with_enough < settings.gate_min_days:
        return GateStatus.REJECT, f"Only {days_with_enough} day(s) have sufficient data (need {settings.gate_min_days})"
    return GateStatus.PASS, None


# ── Gate 3: Low confidence ratio ─────────────────────────────────────────────

def validate_confidence(df: pd.DataFrame) -> tuple[str, str | None]:
    """If > N readings are low confidence → REJECT. > M → WARN."""
    if "cnt" not in df.columns or len(df) == 0:
        return GateStatus.PASS, None

    cnt_valid = df["cnt"].dropna()
    if len(cnt_valid) == 0:
        return GateStatus.PASS, None

    low_conf = (cnt_valid < settings.low_confidence_cnt_threshold).sum()
    ratio = low_conf / len(cnt_valid)

    if ratio > settings.gate_conf_reject_ratio:
        return GateStatus.REJECT, "Majority of readings are low confidence"
    if ratio > settings.gate_conf_warn_ratio:
        return GateStatus.WARN, f"{ratio:.0%} of readings are low confidence"
    return GateStatus.PASS, None


# ── Gate 4: Vital sign range sanity ──────────────────────────────────────────

def validate_ranges(df: pd.DataFrame) -> tuple[str, str | None]:
    """Check for physiologic plausibility. RR bounds read from RENDER_CONFIG.physiologic_bounds."""
    from .config import RENDER_CONFIG
    issues: list[str] = []

    hr = df["hr_avg"].dropna()
    rr = df["rr_avg"].dropna()

    rr_bounds = RENDER_CONFIG["physiologic_bounds"]["rr_brpm"]
    rr_min = rr_bounds["min"]
    rr_max = rr_bounds["max"]

    if len(hr) > 0:
        if hr.min() < 10 or hr.max() > 250:
            issues.append("HR values outside physiologic range (10–250 bpm)")
        # Flatline detection: std < 0.5 over 24+ hours
        if len(hr) >= 24 and hr.std() < 0.5:
            issues.append("HR shows near-zero variability (possible sensor malfunction)")

    if len(rr) > 0:
        if rr.min() < rr_min or rr.max() > rr_max:
            issues.append(f"RR values outside physiologic range ({rr_min}–{rr_max} breaths/min)")

    if issues:
        return GateStatus.WARN, "; ".join(issues)
    return GateStatus.PASS, None


# ── Gate 5: Consecutive gap detection ────────────────────────────────────────

def validate_gaps(df: pd.DataFrame) -> tuple[str, str | None]:
    """Detect the largest gap between consecutive readings."""
    if len(df) < 2:
        return GateStatus.PASS, None

    sorted_ts = df["timestamp"].sort_values()
    gaps = sorted_ts.diff()
    max_gap = gaps.max()
    max_gap_hours = max_gap.total_seconds() / 3600 if pd.notna(max_gap) else 0

    if max_gap_hours > settings.gate_max_gap_hours:
        return GateStatus.WARN, f"Largest data gap: {max_gap_hours:.0f} hours"
    return GateStatus.PASS, None


# ── Combined Quality Gate Orchestrator ───────────────────────────────────────

def run_quality_gates(
    df: pd.DataFrame,
    window_start: pd.Timestamp,
    window_end: pd.Timestamp,
) -> dict:
    """Run all 5 gates and return a consolidated result."""
    gates = [
        validate_coverage(df, window_start, window_end),
        validate_days(df),
        validate_confidence(df),
        validate_ranges(df),
        validate_gaps(df),
    ]

    rejects = [msg for status, msg in gates if status == GateStatus.REJECT]
    warnings = [msg for status, msg in gates if status == GateStatus.WARN and msg]

    if rejects:
        return {
            "can_generate": False,
            "reason": rejects[0],
            "warnings": warnings,
        }

    return {
        "can_generate": True,
        "reason": None,
        "warnings": warnings,
    }
