"""24h Library Summary — one 24h triage overview per library (Sajol, June 30
2026 call).

The remote-nurse morning scan: every patient in a library, one compact row,
critical on top, 24h ONLY (no 30-day data). The 24h status is the SAME
severity classification as the per-patient 24h banner (``compute_triage``
scoped to that patient's last 24h), so a patient's summary status matches their
individual report's banner. One engine.

Each patient's "last 24 hours" is derived from THAT patient's final data
timestamp (rolling, data-shape-agnostic — no hardcoded date, no patient
identity in the logic). Patients across a library have different final
timestamps, so the summary is "each patient's most recent 24 hours," not one
shared calendar window.

A patient with no readings in their last 24h is flagged "No Data" (never a
falsely confident GREEN); a patient with thin/low coverage carries a note. No
events or status are fabricated.
"""

from __future__ import annotations

import pandas as pd

from .config import CONDITION_DISPLAY, TriageLabels
from .signal_engine import compute_last_24h_snapshot, compute_triage, build_24h_summary
from .episodes import detect_episodes, compute_rollups

# Sentinel status for a patient with no readings in the last 24h — distinct from
# GREEN so a data gap is never read as "confirmed normal."
NO_DATA = "No Data"

# Critical-first ordering: RED, then YELLOW, then flagged No-Data (a gap the
# nurse should notice), then GREEN. The nurse reads down and stops at green.
_STATUS_RANK = {
    TriageLabels.RED: 0,
    TriageLabels.YELLOW: 1,
    NO_DATA: 2,
    TriageLabels.GREEN: 3,
}

# Below this 24h coverage a status carries a "limited data" note (matches the
# per-patient snapshot's low-coverage threshold).
_LOW_COVERAGE_PCT = 75.0
# Below this, a GREEN can't be trusted as "confirmed normal" — the day is mostly
# unmonitored. Such a patient is shown as LOW DATA and sorted into the attention
# band (above confirmed greens), never buried as a silent green.
_SEVERE_LOW_COVERAGE_PCT = 33.0


def compute_patient_24h_summary(patient: str, df) -> dict:
    """Summarise one patient's last 24 hours for the library summary row.

    Reuses the exact 24h engine of the per-patient banner: the window is the
    last 24h of this patient's final timestamp, and the status is
    ``compute_triage`` over that window.
    """
    snap = compute_last_24h_snapshot(df)
    if snap is None:
        # No readings in the last 24h — flag honestly, do not fabricate.
        return {
            "patient": patient,
            "status": NO_DATA,
            "display_label": "NO DATA",
            "flagged": True,
            "note": "Insufficient recent data — no readings in the last 24h.",
            "event_count": 0,
            "conditions": [],
            "summary": "Insufficient recent data in the last 24 hours.",
            "hr": None,
            "rr": None,
            "coverage_pct": 0.0,
            "hours_present": 0,
            "burden_hours": 0,
            "window_end": None,
        }

    last_ts = df["timestamp"].max()
    cutoff = last_ts - pd.Timedelta(hours=24)
    df24 = df[df["timestamp"] > cutoff].reset_index(drop=True)

    eps24 = detect_episodes(df24)
    rollups24 = compute_rollups(eps24, df24)
    # SAME severity engine as the per-patient 24h banner, scoped to 24h.
    status = compute_triage(eps24, rollups24.coupled_fraction, df=df24)

    conditions = sorted({CONDITION_DISPLAY.get(e.condition, e.condition) for e in eps24})
    burden_hours = sum(int(e.duration_hours or 0) for e in eps24)
    coverage_pct = float(snap.get("coverage_pct", 0) or 0)
    low_coverage = coverage_pct < _LOW_COVERAGE_PCT
    severe_low = coverage_pct < _SEVERE_LOW_COVERAGE_PCT

    note = ""
    if low_coverage:
        note = (f"Limited data in the last 24h "
                f"({snap.get('hours_present', 0)}/{snap.get('expected_hours', 24)}h).")

    # `status` is the true 24h banner classification (parity with the per-patient
    # report). `display_label` is what the row shows: a GREEN that we can't trust
    # (severely-low coverage) reads "LOW DATA", not a confident green.
    if severe_low and status == TriageLabels.GREEN:
        display_label = "LOW DATA"
    else:
        display_label = str(status).upper()

    return {
        "patient": patient,
        "status": status,
        "display_label": display_label,
        "flagged": low_coverage,
        "severe_low": severe_low,
        "note": note,
        "event_count": len(eps24),
        "conditions": conditions,
        "summary": build_24h_summary(eps24),
        "hr": snap.get("hr"),
        "rr": snap.get("rr"),
        "coverage_pct": coverage_pct,
        "hours_present": snap.get("hours_present"),
        "burden_hours": burden_hours,
        "window_end": snap.get("window_end"),
    }


# The single attention band for unreviewable rows (No-Data, and a GREEN we can't
# trust because coverage is severely low). It sits ABOVE confirmed green so the
# nurse — who stops scanning at green — never skips a patient she couldn't
# actually review.
_ATTENTION_RANK = _STATUS_RANK[NO_DATA]  # 2
_GREEN_RANK = _STATUS_RANK[TriageLabels.GREEN]  # 3


def status_band_rank(row: dict) -> int:
    """The band a row sorts into: 0 RED, 1 YELLOW, 2 attention (No-Data or a
    severely-low-coverage GREEN we can't confirm), 3 confirmed GREEN. The band
    order is enforced here, so LOW DATA / NO DATA always rank ABOVE green
    regardless of dataset."""
    status = row["status"]
    if status == NO_DATA:
        return _ATTENTION_RANK
    if row.get("severe_low") and status == TriageLabels.GREEN:
        return _ATTENTION_RANK
    return _STATUS_RANK.get(status, 9)


def _sort_key(row: dict):
    # Critical first (band), then higher 24h episode burden, then name.
    return (status_band_rank(row), -row.get("burden_hours", 0), row["patient"])


def build_library_summary(client: str) -> dict:
    """Build the 24h summary for one library: one row per patient, critical
    first. Reads ONLY that client's folder (privacy boundary) — never mixes
    libraries."""
    from .client_registry import load_client_data, client_label

    data = load_client_data(client)
    rows = [compute_patient_24h_summary(p, df) for p, df in sorted(data.items())]
    rows.sort(key=_sort_key)
    return {"client": client, "label": client_label(client), "patients": rows}
