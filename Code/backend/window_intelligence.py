"""
CardioReport – Phase Detection & Report Priority
Decision 4: Phase Detection (finding the clinical story)
Decision 5: Report Priority Classification
"""

from __future__ import annotations

import pandas as pd
import numpy as np

from .config import settings, PHASE_LABELS


# ── Phase Detection ─────────────────────────────────────────────────────────

def detect_phases(df: pd.DataFrame, episodes: list) -> list[dict]:
    """Segment the monitoring window into clinically distinct phases.

    Each day is classified as: stable, low_hr, high_hr, or mixed.
    Consecutive days of the same class are grouped into phases.
    Absorb 1-day phases into neighbors, merge consecutive same-type, cap at 5.

    Returns list of dicts:
        [{"type": "stable", "start_date": "2024-06-22", "end_date": "2024-06-24",
          "days": 3, "label": "Phase 1: Stable", "date_range": "Jun 22 to Jun 24"}]
    """
    if df.empty:
        return []

    # Build daily aggregates
    df_copy = df.copy()
    df_copy["_date"] = df_copy["timestamp"].dt.normalize()

    daily = (
        df_copy.groupby("_date")
        .agg(
            hr_avg=("hr_avg", "mean"),
            rr_avg=("rr_avg", "mean"),
            hr_min=("hr_min", "min") if "hr_min" in df_copy.columns else ("hr_avg", "min"),
            hours=("hr_avg", "count"),
        )
        .reset_index()
        .rename(columns={"_date": "date"})
        .sort_values("date")
        .reset_index(drop=True)
    )

    n = len(daily)
    if n < 1:
        return []

    # Compute daily episode burden
    daily["ep_score"] = 0.0
    for ep in episodes:
        try:
            if isinstance(ep, dict):
                st = ep.get("start_time")
                et = ep.get("end_time")
                sc = ep.get("severity_score", 1)
            else:
                st = getattr(ep, "start_time", None)
                et = getattr(ep, "end_time", None)
                sc = getattr(ep, "severity_score", 1)
                
            if not st or not et:
                continue
                
            ep_start = pd.Timestamp(st).normalize()
            ep_end = pd.Timestamp(et).normalize()
            
            # Unify tz if needed
            if ep_start.tz is not None:
                ep_start = ep_start.tz_localize(None)
            if ep_end.tz is not None:
                ep_end = ep_end.tz_localize(None)
            if daily["date"].dt.tz is not None:
                daily["date"] = daily["date"].dt.tz_localize(None)

            score = sc
        except Exception as e:
            continue
        mask = (daily["date"] >= ep_start) & (daily["date"] <= ep_end)
        daily.loc[mask, "ep_score"] += score

    # Short windows (< 3 days): single phase
    if n < 3:
        phase_type = "normal" if daily["ep_score"].sum() == 0 else "elevated_rr"
        ph_hr = daily["hr_avg"].mean()
        ph_rr = daily["rr_avg"].mean()
        return [{
            "type": phase_type,
            "start_date": daily.iloc[0]["date"].strftime("%Y-%m-%d"),
            "end_date": daily.iloc[n - 1]["date"].strftime("%Y-%m-%d"),
            "days": n,
            "label": "Full period",
            "date_range": f"{daily.iloc[0]['date'].strftime('%b %d')} to {daily.iloc[n-1]['date'].strftime('%b %d')}",
            "hr_avg": round(ph_hr, 1) if pd.notna(ph_hr) else 0.0,
            "rr_avg": round(ph_rr, 1) if pd.notna(ph_rr) else 0.0,
        }]

    # Multi-track classification: each condition type gets its own track.
    # A day can belong to multiple tracks simultaneously.
    # Build episode conditions by day
    ep_by_day = {}
    for ep in episodes:
        try:
            if isinstance(ep, dict):
                st = ep.get("start_time")
                cond = ep.get("condition", "")
            else:
                st = getattr(ep, "start_time", None)
                cond = getattr(ep, "condition", "")
            if not st:
                continue
            ep_day = pd.Timestamp(st).normalize()
            if ep_day.tz is not None:
                ep_day = ep_day.tz_localize(None)
            ep_by_day.setdefault(ep_day, set()).add(cond)
        except Exception:
            continue

    # Map condition names to phase types
    COND_TO_PHASE = {
        "Severe Bradycardia": "very_low_hr",
        "Bradycardia": "low_hr",
        "Very High HR": "very_high_hr",
        "Tachycardia": "high_hr",
        "Elevated HR": "elevated_hr",
        "Tachypnea": "elevated_rr",
        "High RR": "high_rr",                # R15 A2
        "Very High RR": "very_high_rr",      # R15 A2
    }

    # Build per-condition-track day flags
    all_phase_types = set()
    for day_conds in ep_by_day.values():
        for cond in day_conds:
            pt = COND_TO_PHASE.get(cond)
            if pt:
                all_phase_types.add(pt)

    # For each condition track, find consecutive runs of days
    all_raw_phases = []
    for phase_type in sorted(all_phase_types):
        # Which days have this condition?
        day_flags = []
        for i, row in daily.iterrows():
            day_date = row["date"]
            if daily["date"].dt.tz is not None:
                day_date = day_date.tz_localize(None)
            day_conds = ep_by_day.get(day_date, set())
            # Check if any condition maps to this phase_type
            has_this_type = any(COND_TO_PHASE.get(c) == phase_type for c in day_conds)
            day_flags.append(has_this_type)

        # Find consecutive runs
        in_run = False
        run_start = None
        for i, flag in enumerate(day_flags):
            if flag:
                if not in_run:
                    run_start = i
                    in_run = True
            else:
                if in_run:
                    all_raw_phases.append({
                        "type": phase_type,
                        "start_idx": run_start,
                        "end_idx": i - 1,
                        "days_list": list(range(run_start, i)),
                    })
                    in_run = False
        if in_run:
            all_raw_phases.append({
                "type": phase_type,
                "start_idx": run_start,
                "end_idx": len(day_flags) - 1,
                "days_list": list(range(run_start, len(day_flags))),
            })

    # ── Phase merging ────────────────────────────────────────────────────
    # Group by type, then merge within each type
    from collections import defaultdict
    by_type = defaultdict(list)
    for p in all_raw_phases:
        by_type[p["type"]].append(p)

    merged = []
    for phase_type, phases_of_type in by_type.items():
        # Sort by start index
        phases_of_type.sort(key=lambda p: p["start_idx"])
        # Merge runs separated by ≤ 1 day gap
        current = None
        for p in phases_of_type:
            if current is None:
                current = p.copy()
            elif p["start_idx"] - current["end_idx"] <= 2:
                # Close enough to merge (≤ 1 day gap between runs)
                current["end_idx"] = p["end_idx"]
                current["days_list"] = list(range(current["start_idx"], p["end_idx"] + 1))
            else:
                merged.append(current)
                current = p.copy()
        if current is not None:
            merged.append(current)

    # Compute phase_score for all phases
    for p in merged:
        p_eps_sum = 0
        p_start_d = daily.iloc[p["start_idx"]]["date"]
        p_end_d = daily.iloc[p["end_idx"]]["date"]
        for ep in episodes:
            if isinstance(ep, dict):
                st = ep.get("start_time")
                sc = ep.get("severity_score", 1)
            else:
                st = getattr(ep, "start_time", None)
                sc = getattr(ep, "severity_score", 1)
            
            if not st:
                continue
            
            ep_start = pd.Timestamp(st).normalize()
            if ep_start.tz is not None:
                ep_start = ep_start.tz_localize(None)

            if p_start_d <= ep_start <= p_end_d:
                p_eps_sum += sc
        p["phase_score"] = p_eps_sum + len(p["days_list"])

    # Cap phases — all phases here are clinical (no normal phases in multi-track output)
    max_phases = max(6, min(12, n // 8))
    if len(merged) > max_phases:
        top_indices = sorted(range(len(merged)), key=lambda i: merged[i]["phase_score"], reverse=True)[:max_phases]
        top_indices = sorted(top_indices)
        merged = [merged[i] for i in top_indices]

    # ── Build output ─────────────────────────────────────────────────────
    phases = []
    for idx, p in enumerate(merged, 1):
        sd = daily.iloc[p["start_idx"]]["date"]
        ed = daily.iloc[p["end_idx"]]["date"]
        phase_name = PHASE_LABELS.get(p['type'])
        label = phase_name if phase_name is not None else str(p['type'])
        date_range_str = f"{sd.strftime('%b %d')} to {ed.strftime('%b %d')}"
        
        ph_rows = daily.iloc[p["days_list"]]
        ph_hr = ph_rows["hr_avg"].mean()
        ph_rr = ph_rows["rr_avg"].mean()

        phases.append({
            "type": p["type"],
            "start_date": sd.strftime("%Y-%m-%d"),
            "end_date": ed.strftime("%Y-%m-%d"),
            "days": len(p["days_list"]),
            "label": label,
            "date_range": date_range_str,
            "hr_avg": round(ph_hr, 1) if pd.notna(ph_hr) else 0.0,
            "rr_avg": round(ph_rr, 1) if pd.notna(ph_rr) else 0.0,
            "phase_score": p["phase_score"],
        })

    return phases


# ── Report Priority ─────────────────────────────────────────────────────────

def compute_report_priority(
    episodes: list,
    phases: list[dict],
    max_severity_score: int,
    quality_warnings: list[str],
) -> str:
    """Classify report as HIGH / MEDIUM / LOW / SKIP.

    HIGH: coupled episodes, severity >= band_s2_min, or multiple phases
    MEDIUM: some episodes, max severity >= band_s1_min
    LOW: stable patient, minor events only
    SKIP: data quality insufficient
    """
    if any("insufficient" in w.lower() for w in quality_warnings) or \
       any("reject" in w.lower() for w in quality_warnings):
        return "SKIP"

    # Support both Pydantic models (Episode) and raw dicts
    has_coupled = False
    for e in episodes:
        if hasattr(e, "cooccurrence"):
            if e.cooccurrence:
                has_coupled = True
                break
        elif isinstance(e, dict) and e.get("cooccurrence"):
            has_coupled = True
            break
    num_phases = len(phases)

    # HIGH
    if has_coupled and max_severity_score >= settings.band_s2_min:
        return "HIGH"
    if max_severity_score >= settings.band_s2_min:
        return "HIGH"
    if num_phases >= 3:
        return "HIGH"

    # MEDIUM
    if len(episodes) > 0 and max_severity_score >= settings.band_s1_min:
        return "MEDIUM"
    if len(episodes) >= 3:
        return "MEDIUM"

    # LOW
    return "LOW"


# ── Most Interesting Week Scanner (Decision 6) ──────────────────────────────

def compute_window_score(df_window: pd.DataFrame, episodes: list) -> int:
    """Score a 7-day window by clinical burden.

    Weights (validated by sliding-window analysis):
      - Each severity point:       ×2
      - Each coupled episode:      +10  (highest-value clinical signal)
      - HR spread > 40 bpm:        +15
      - HR spread > 25 bpm:        +8
      - 3+ condition types:        +12
      - 2 condition types:         +5
      - Longest episode ≥ 6h:      +10
      - Longest episode ≥ 3h:      +5
    """
    score = 0

    # Severity sum ×2
    for ep in episodes:
        sev = ep.severity_score if hasattr(ep, "severity_score") else ep.get("severity_score", 0)
        score += sev * 2

    # Coupled episodes ×10
    coupled = 0
    for ep in episodes:
        co = ep.cooccurrence if hasattr(ep, "cooccurrence") else ep.get("cooccurrence", False)
        if co:
            coupled += 1
    score += coupled * 10

    # HR spread bonus
    hr = df_window["hr_avg"].dropna()
    if len(hr) > 10:
        spread = hr.quantile(0.95) - hr.quantile(0.05)
        if spread > 40:
            score += 15
        elif spread > 25:
            score += 8

    # Condition diversity bonus
    types = set()
    for ep in episodes:
        cond = ep.condition if hasattr(ep, "condition") else ep.get("condition", "")
        types.add(cond)
    if len(types) >= 3:
        score += 12
    elif len(types) >= 2:
        score += 5

    # Duration bonus (longest single episode)
    max_dur = 0
    for ep in episodes:
        dur = ep.duration_hours if hasattr(ep, "duration_hours") else ep.get("duration_hours", 0)
        max_dur = max(max_dur, dur)
    if max_dur >= 6:
        score += 10
    elif max_dur >= 3:
        score += 5

    return score


def find_most_interesting_week(df: pd.DataFrame) -> dict | None:
    """Slide a 7-day window across the dataset and return the highest-scoring position.

    Returns:
        {"start": "2024-06-23", "end": "2024-06-29", "score": 72}
        or None if no valid window exists.
    """
    from .episodes import detect_episodes

    ts = df["timestamp"]
    data_start = ts.min().normalize()
    data_end = ts.max().normalize()

    total_days = (data_end - data_start).days + 1
    if total_days < 7:
        return None

    all_starts = pd.date_range(data_start, data_end - pd.Timedelta(days=6), freq="D")

    best_score = -1
    best_start = None

    for start in all_starts:
        end = start + pd.Timedelta(days=6, hours=23, minutes=59)
        window = df[(df["timestamp"] >= start) & (df["timestamp"] <= end)]

        # Skip windows with too little data (< 20 hourly readings)
        if len(window) < 20:
            continue

        episodes = detect_episodes(window)
        score = compute_window_score(window, episodes)

        if score > best_score:
            best_score = score
            best_start = start

    if best_start is None:
        return None

    return {
        "start": best_start.strftime("%Y-%m-%d"),
        "end": (best_start + pd.Timedelta(days=6)).strftime("%Y-%m-%d"),
        "score": best_score,
    }
