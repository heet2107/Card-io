"""Last-24h triage layer (Round 28, Items 1 & 2) — shared by the live API and
the batch generator so the web preview and the PDFs render the identical layer.

Produces, scoped to the final 24h of the data:
  * status_24h — GREEN/YELLOW/RED via the SAME ``compute_triage`` severity logic
    as the 30-day tier, scoped to the 24h window (one engine, two windows).
  * events — episodic-events rows built by the SAME narrative row-builder as the
    30-day unified table (same columns, same bold-linking). No separate
    detection logic.
  * summary — one-line, plain-language summary of the window.

AI is never used here: the 24h glance is deterministic.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd


async def compute_24h_layer(patient_id: str, df) -> Optional[dict]:
    """Return the 24h triage layer dict, or ``None`` when the window is empty."""
    from .episodes import detect_episodes, compute_rollups
    from .signal_engine import (
        compute_triage, compute_stats, compute_data_quality,
        compute_trend_assessment, compute_action_posture, build_24h_summary,
    )
    from .window_intelligence import detect_phases
    from .narrative_ai import generate_narrative

    if df is None or df.empty or "timestamp" not in df.columns:
        return None
    last_ts = df["timestamp"].max()
    if pd.isna(last_ts):
        return None
    cutoff = last_ts - pd.Timedelta(hours=24)
    df24 = df[df["timestamp"] > cutoff].reset_index(drop=True)
    if df24.empty:
        return None

    eps24 = detect_episodes(df24)
    rollups24 = compute_rollups(eps24, df24)
    triage24 = compute_triage(eps24, rollups24.coupled_fraction, df=df24)

    max_band = "S0"
    for ep in eps24:
        if ep.severity_band > max_band:
            max_band = ep.severity_band

    rows: list = []
    try:
        raw_phases24 = detect_phases(df24, eps24)
        hr24, rr24 = compute_stats(df24)
        dq24 = compute_data_quality(df24)
        trend24, _ = compute_trend_assessment(df24, eps24)
        posture24 = compute_action_posture(triage24, trend24, rollups24.coupled_fraction, max_band)
        ws = df24["timestamp"].min().strftime("%Y-%m-%d")
        we = last_ts.strftime("%Y-%m-%d")
        narr24, _actions24, _src24 = await generate_narrative(
            patient_id, ws, we, hr24, rr24, dq24,
            eps24, rollups24, triage24, trend24, posture24,
            use_llm_override=False, quality_warnings=[], phases=raw_phases24,
        )
        if isinstance(narr24, dict):
            rows = narr24.get("phase_table_rows", []) or []
    except Exception as e:
        print(f"DEBUG 24h layer: narrative row build failed ({e}); rendering events-empty")
        rows = []

    return {
        "status_24h": triage24,
        "summary": build_24h_summary(eps24),
        "events": rows,
        "event_count": len(eps24),
    }
