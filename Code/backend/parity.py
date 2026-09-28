"""
CardioReport – Canonical-frame parity
=====================================
Phase 1 exit test for the Excel/CSV -> live-API migration: run the same patient
and window through the file adapter and the API adapter, then diff the two
canonical frames field by field.

The question is not "did it run" but "are the numbers the same". A wrong
``cnt`` field or a timezone mismatch produces a frame that loads cleanly,
renders cleanly, and is quietly wrong — the only way to catch that is to
compare against the frame we already trust.

``detect_hour_shift`` exists because the likeliest silent failure is a
timezone offset: this pipeline carries naive timestamps throughout
(``medhab_ingest`` treats its hours as naive UTC, ``excel_ingest`` applies no
conversion at all, and ``config.default_timezone`` is "US/Central"). If the
file cohort was recorded in local time and the API is UTC, every episode
shifts across day boundaries and events-per-day, Notable Days and the 24h
layer all change with nothing raising.
"""

from __future__ import annotations

import pandas as pd

_VITALS = ["hr_avg", "hr_max", "hr_min", "rr_avg", "rr_max", "rr_min", "cnt"]


def detect_hour_shift(a: pd.DataFrame, b: pd.DataFrame, max_shift: int = 14,
                      key: str = "hr_avg") -> dict:
    """The whole-hour shift of ``b`` that best aligns its VALUES with ``a``.

    Scored by value agreement, not timestamp overlap. Overlap counting cannot
    see this failure: over a 30-day window a 5-hour offset still shares 99% of
    its hours, so the shift hides in the rounding. The vitals curve gives it
    away immediately — a diurnal HR rhythm decorrelates the moment it is
    misaligned — so the score is the mean absolute delta of ``key`` on the
    joined hours, minimised over candidate shifts.

    ``conclusive`` is deliberately conservative: a flat or near-constant series
    carries no phase information, so it reports False rather than guessing.
    """
    if a.empty or b.empty or key not in a or key not in b:
        return {"best_shift_hours": 0, "score": None, "score_at_zero": None,
                "compared": 0, "overlap_at_zero": 0, "conclusive": False}

    left = a[["timestamp", key]].dropna()
    right = b[["timestamp", key]].dropna()

    scores: dict[int, tuple[float, int]] = {}
    for h in range(-max_shift, max_shift + 1):
        shifted = right.assign(timestamp=right["timestamp"] + pd.Timedelta(hours=h))
        j = left.merge(shifted, on="timestamp", suffixes=("_a", "_b"))
        if len(j) < 12:            # too little evidence to judge this shift
            continue
        scores[h] = (float((j[f"{key}_a"] - j[f"{key}_b"]).abs().mean()), len(j))

    if not scores:
        return {"best_shift_hours": 0, "score": None, "score_at_zero": None,
                "compared": 0, "overlap_at_zero": 0, "conclusive": False}

    best = min(scores, key=lambda h: scores[h][0])
    best_score, best_n = scores[best]
    zero_score, zero_n = scores.get(0, (None, 0))

    conclusive = (
        best != 0
        and best_n >= 24
        and zero_score is not None
        and zero_score > 0                     # a flat series proves nothing
        and best_score < zero_score * 0.5      # the winner must clearly win
    )
    return {
        "best_shift_hours": best,
        "score": round(best_score, 4),
        "score_at_zero": None if zero_score is None else round(zero_score, 4),
        "compared": best_n,
        "overlap_at_zero": zero_n,
        "conclusive": conclusive,
    }


def frame_diff(file_df: pd.DataFrame, api_df: pd.DataFrame, tolerance: float = 0.05) -> dict:
    """Compare two canonical frames for the same patient and window.

    ``file_df`` is the trusted side (Excel/CSV), ``api_df`` the candidate.
    Numeric columns are compared on the inner join of their timestamps.
    """
    out: dict = {
        "rows_file": len(file_df),
        "rows_api": len(api_df),
        "columns_match": list(file_df.columns) == list(api_df.columns),
        "columns_file": list(file_df.columns),
        "columns_api": list(api_df.columns),
        "shift": detect_hour_shift(file_df, api_df),
    }

    joined = file_df.merge(api_df, on="timestamp", suffixes=("_file", "_api"), how="inner")
    out["rows_joined"] = len(joined)
    out["only_in_file"] = len(set(file_df["timestamp"]) - set(api_df["timestamp"]))
    out["only_in_api"] = len(set(api_df["timestamp"]) - set(file_df["timestamp"]))

    fields: dict = {}
    for col in _VITALS:
        lhs, rhs = f"{col}_file", f"{col}_api"
        if lhs not in joined or rhs not in joined:
            fields[col] = {"status": "absent"}
            continue
        both = joined[[lhs, rhs]].dropna()
        if both.empty:
            fields[col] = {"status": "no_overlap"}
            continue
        delta = (both[lhs] - both[rhs]).abs()
        denom = both[lhs].abs().replace(0, float("nan"))
        fields[col] = {
            "status": "ok" if delta.max() <= tolerance else "differs",
            "compared": int(len(both)),
            "max_abs_delta": round(float(delta.max()), 4),
            "mean_abs_delta": round(float(delta.mean()), 4),
            "max_pct_delta": round(float((delta / denom).max() * 100), 2) if denom.notna().any() else None,
        }
    out["fields"] = fields
    out["verdict"] = _verdict(out)
    return out


def _verdict(d: dict) -> str:
    if d["rows_joined"] == 0:
        s = d["shift"]
        if s["conclusive"]:
            return f"NO OVERLAP — timestamps differ by {s['best_shift_hours']}h (timezone mismatch)"
        return "NO OVERLAP — no shared timestamps and no clean hour shift; different window or device"
    if d["shift"]["conclusive"] and d["shift"]["best_shift_hours"] != 0:
        return f"SHIFTED — best alignment at {d['shift']['best_shift_hours']}h (timezone mismatch)"
    bad = [k for k, v in d["fields"].items() if v.get("status") == "differs"]
    if bad:
        return f"DIFFERS — {', '.join(bad)}"
    if not d["columns_match"]:
        return "SCHEMA — same values, different column set"
    return "MATCH"


def format_diff(d: dict) -> str:
    """One-screen summary for a terminal."""
    lines = [
        f"verdict:  {d['verdict']}",
        f"rows:     file={d['rows_file']}  api={d['rows_api']}  joined={d['rows_joined']}",
        f"unmatched: file-only={d['only_in_file']}  api-only={d['only_in_api']}",
        f"shift:    best={d['shift']['best_shift_hours']}h  "
        f"agreement {d['shift']['score']} vs {d['shift']['score_at_zero']} at 0h "
        f"(n={d['shift']['compared']}, conclusive={d['shift']['conclusive']})",
        "fields:",
    ]
    for col, f in d["fields"].items():
        if f.get("status") in ("absent", "no_overlap"):
            lines.append(f"  {col:<8} {f['status']}")
        else:
            lines.append(
                f"  {col:<8} {f['status']:<8} n={f['compared']:<5} "
                f"max|d|={f['max_abs_delta']}  mean|d|={f['mean_abs_delta']}"
            )
    return "\n".join(lines)
