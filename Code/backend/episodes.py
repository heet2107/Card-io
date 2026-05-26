"""
CardioReport – Episode Detection (Decision 2)
Detects Bradycardia, Severe Bradycardia, Tachycardia, and Tachypnea.
Merges consecutive occurrences, scores severity, and identifies coupling.
"""

from __future__ import annotations
from datetime import datetime
import pandas as pd
from .config import settings, Conditions, SEVERITY_BAND_PHRASES
from .models import Episode, EpisodeRollups


# ── Severity Scoring (Decision 2) ───────────────────────────────────────────

def _severity_score(condition: str, hours: int, coupled: bool, low_conf: bool) -> int:
    """Compute numerical score based on clinical burden.
    Weights from settings, multiplied by duration, plus bonuses/penalties.
    """
    weights = {
        Conditions.SEVERE_BRADY: settings.base_severe_brady,
        Conditions.BRADYCARDIAC: settings.base_low_hr,
        Conditions.ELEVATED_HR: settings.base_elevated_hr,
        Conditions.TACHYCARDIA: settings.base_high_hr,
        Conditions.VERY_HIGH_HR: settings.base_very_high_hr,
        Conditions.TACHYPNEA: settings.base_elevated_rr,
        Conditions.HIGH_RR: settings.base_high_rr,
        Conditions.VERY_HIGH_RR: settings.base_very_high_rr,
    }
    base = weights.get(condition, 1)
    
    # Simple burden: base weight + (additional hours * bonus)
    score = base + (max(0, hours - 1) * settings.duration_bonus_per_hour)
    
    if coupled:
        score += settings.coupling_bonus
    
    if low_conf:
        score = max(1, score - settings.low_conf_penalty)
        
    return score


def _severity_band(score: int) -> str:
    """Classify score into S0-S3 bands based on settings."""
    if score >= settings.band_s3_min: return "S3"
    if score >= settings.band_s2_min: return "S2"
    if score >= settings.band_s1_min: return "S1"
    return "S0"


# ── Detection ───────────────────────────────────────────────────────────────

def detect_episodes(df: pd.DataFrame) -> list[Episode]:
    """Identify episodic events from hourly vital signal trends.

    Algorithm (Stages 1-3 of implementation):
      1. Flag hourly violations of HR/RR thresholds from Settings.
      2. Group consecutive violations into single episodes.
      3. Merge episodes separated by <= Settings.episode_merge_gap_hours.
      4. Score by burden and identify coupling.
    """
    if df.empty:
        return []

    df = df.sort_values("timestamp")
    episodes: list[dict] = []

    # Helper to append raw episodes
    def _add_raw(cond: str, row: pd.Series):
        episodes.append({
            "condition": cond,
            "start_time": row["timestamp"].isoformat(),
            "end_time": row["timestamp"].isoformat(),
            "duration_hours": 1,
            "is_low_confidence": row.get("gap_flag", 0) == 1 or row.get("cnt", 10) < settings.low_confidence_cnt_threshold,
            "cooccurrence": False,
            "hr_avg": row["hr_avg"],
            "rr_avg": row["rr_avg"],
            "hr_min": row.get("hr_min", row["hr_avg"]),
            "hr_max": row.get("hr_max", row["hr_avg"]),
            "rr_min": row.get("rr_min", row["rr_avg"]),
            "rr_max": row.get("rr_max", row["rr_avg"]),
        })

    # Stage 1: Hourly Violation Detection
    for _, row in df.iterrows():
        # Severe Bradycardia: HR < 40 bpm
        if row["hr_avg"] < settings.severe_brady_min:
            _add_raw(Conditions.SEVERE_BRADY, row)
        # Bradycardia: HR < 45 bpm
        elif row["hr_avg"] < settings.brady_hr_avg:
            _add_raw(Conditions.BRADYCARDIAC, row)

        # Very High HR: HR > 110 bpm (check first, more severe)
        if row["hr_avg"] > settings.very_high_hr_avg:
            _add_raw(Conditions.VERY_HIGH_HR, row)
        # Tachycardia: HR > 100 bpm
        elif row["hr_avg"] > settings.tachy_hr_avg:
            _add_raw(Conditions.TACHYCARDIA, row)
        # Elevated HR: HR > 80 bpm (lower severity)
        elif row["hr_avg"] > settings.elevated_hr_avg:
            _add_raw(Conditions.ELEVATED_HR, row)

        # R15 A2: Three-tier RR detection — Very High > High > Elevated
        # Very High RR: > 40 brpm (Medicare threshold, check first — most severe)
        if row["rr_avg"] > settings.very_high_rr_avg:
            _add_raw(Conditions.VERY_HIGH_RR, row)
        # High RR: > 30 brpm
        elif row["rr_avg"] > settings.high_rr_avg:
            _add_raw(Conditions.HIGH_RR, row)
        # Elevated breathing (Tachypnea): > 24 brpm
        elif row["rr_avg"] > settings.tachy_rr_avg:
            _add_raw(Conditions.TACHYPNEA, row)

    if not episodes:
        return []

    # Stage 2 & 3: Group and Merge consecutive Same-Type Episodes
    merged: list[dict] = []
    if episodes:
        episodes.sort(key=lambda x: (x["condition"], x["start_time"]))
        curr = episodes[0]

        for i in range(1, len(episodes)):
            nxt = episodes[i]
            gap = (pd.Timestamp(nxt["start_time"]) - pd.Timestamp(curr["end_time"])).total_seconds() / 3600
            
            if nxt["condition"] == curr["condition"] and gap <= settings.episode_merge_gap_hours:
                # Merge
                curr["end_time"] = nxt["end_time"]
                curr["duration_hours"] = int((pd.Timestamp(curr["end_time"]) - pd.Timestamp(curr["start_time"])).total_seconds() / 3600) + 1
                curr["hr_avg"] = (curr["hr_avg"] + nxt["hr_avg"]) / 2
                curr["rr_avg"] = (curr["rr_avg"] + nxt["rr_avg"]) / 2
                curr["hr_min"] = min(curr["hr_min"], nxt["hr_min"])
                curr["hr_max"] = max(curr["hr_max"], nxt["hr_max"])
                curr["rr_min"] = min(curr["rr_min"], nxt["rr_min"])
                curr["rr_max"] = max(curr["rr_max"], nxt["rr_max"])
                if nxt["is_low_confidence"]: curr["is_low_confidence"] = True
            else:
                merged.append(curr)
                curr = nxt
        merged.append(curr)

    # ── Step 4: Coupling detection (Concurrent cross-condition abnormalities) ────────────
    # Let's just use 2 hours as the default.
    for i, ep in enumerate(merged):
        for j, ep2 in enumerate(merged):
            if i != j and ep["condition"] != ep2["condition"]:
                s1, e1 = pd.Timestamp(ep["start_time"]), pd.Timestamp(ep["end_time"])
                s2, e2 = pd.Timestamp(ep2["start_time"]), pd.Timestamp(ep2["end_time"])
                ov_start = max(s1, s2)
                ov_end = min(e1, e2)
                if ov_start < ov_end:
                    ov_hours = (ov_end - ov_start).total_seconds() / 3600
                    if ov_hours >= 2.0:
                        ep["cooccurrence"] = True
                        ep2["cooccurrence"] = True

        # ── FIX 44: Verify duration_hours matches start and end timestamps ──────────────
    for ep_dict in merged:
        expected_hours = (pd.Timestamp(ep_dict["end_time"]) - pd.Timestamp(ep_dict["start_time"])).total_seconds() / 3600 + 1
        if abs(expected_hours - ep_dict["duration_hours"]) > 1:
            ep_dict["duration_hours"] = int(expected_hours)

    # ── Step 5: Finalize and construct models ─────────────────────────────────
    final_episodes = []
    for ep_dict in merged:
        score = _severity_score(
            ep_dict["condition"], 
            ep_dict["duration_hours"], 
            ep_dict["cooccurrence"],
            ep_dict["is_low_confidence"]
        )
        band = _severity_band(score)
        
        # Format key vitals string — always include both for context
        # Format key vitals string — consistent format for HR and RR
        hr_min_val = round(ep_dict.get('hr_min', ep_dict['hr_avg']))
        hr_max_val = round(ep_dict.get('hr_max', ep_dict['hr_avg']))
        rr_min_val = round(ep_dict.get('rr_min', ep_dict['rr_avg']))
        rr_max_val = round(ep_dict.get('rr_max', ep_dict['rr_avg']))

        kv = (
            f"HR avg {round(ep_dict['hr_avg'])} / min {hr_min_val} / max {hr_max_val} | "
            f"RR avg {round(ep_dict['rr_avg'])} / min {rr_min_val} / max {rr_max_val}"
        )

        final_episodes.append(Episode(
            condition=ep_dict["condition"],
            start_time=ep_dict["start_time"],
            end_time=ep_dict["end_time"],
            duration_hours=ep_dict["duration_hours"],
            key_vitals=kv,
            confidence="low" if ep_dict["is_low_confidence"] else "high",
            cooccurrence=ep_dict["cooccurrence"],
            severity_score=score,
            severity_band=band,
            concern_phrase=SEVERITY_BAND_PHRASES.get(band, ""),
            qualifier_phrase="Clinical coupling: low HR + elevated RR" if ep_dict["cooccurrence"] else ""
        ))

    # Sort: Severity descending, then Chronological
    final_episodes.sort(key=lambda x: (-x.severity_score, x.start_time))
    
    return final_episodes


def compute_rollups(episodes: list[Episode], df: pd.DataFrame) -> EpisodeRollups:
    """Compute summary counts for the report header/dashboard."""
    total = len(episodes)
    if total == 0:
        return EpisodeRollups()

    counts = {}
    coupled = 0
    for ep in episodes:
        counts[ep.condition] = counts.get(ep.condition, 0) + 1
        if ep.cooccurrence:
            coupled += 1

    ts = df["timestamp"]
    days = max(1, (ts.max() - ts.min()).days + 1)

    return EpisodeRollups(
        counts_by_type=counts,
        total_events=total,
        events_per_day=round(total / days, 1),
        coupled_fraction=round(coupled / total, 2)
    )
