#!/usr/bin/env python3
"""R26 before/after snapshot — captures per-report triage, Major Findings line,
dominant finding, and coverage on both surfaces, for BOTH cohorts, without
rendering PDFs. Run before and after the engine changes; diff the JSON.

Usage: python snapshot_r26.py <out.json>
"""
from __future__ import annotations
import sys, json, asyncio, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))

import inspect
import pandas as pd
from backend.excel_ingest import load_vitals, get_patient_metadata
from backend.medhab_ingest import load_medhab_vitals, discover_report_windows
from backend.signal_engine import (apply_window, compute_stats, compute_data_quality,
                                    compute_triage, compute_trend_assessment,
                                    compute_positional_stats)
from backend.episodes import detect_episodes, compute_rollups
from backend.narrative_ai import generate_narrative
from backend.window_intelligence import detect_phases
from backend.batch_summary import build_findings_text
from backend.quality_gates import run_quality_gates
from batch_generate import (PATIENT_ORDER, find_critical_week,
                            detect_most_active_window)

# build_findings_text gains an end_of_period kwarg in R26; pass it only if present.
_BFT_PARAMS = set(inspect.signature(build_findings_text).parameters)


async def _snap_one(pid, start, end, all_data, label, allow_low_cov=False):
    if pid not in all_data:
        return None
    df = apply_window(all_data[pid].copy(), "custom", start, end)
    if df.empty:
        return None
    ws, we = df["timestamp"].min(), df["timestamp"].max()
    gate = run_quality_gates(df, ws, we, downgrade_coverage_reject=allow_low_cov)
    if not gate["can_generate"]:
        return {"patient": pid, "report": label, "skipped": gate["reason"]}
    hr, rr = compute_stats(df)
    dq = compute_data_quality(df)
    eps = detect_episodes(df)
    rollups = compute_rollups(eps, df)
    triage = compute_triage(eps, rollups.coupled_fraction, df=df)
    trend, _ = compute_trend_assessment(df, eps)
    narrative, actions, _ = await generate_narrative(
        pid, ws.strftime("%Y-%m-%d"), we.strftime("%Y-%m-%d"),
        hr, rr, dq, eps, rollups, triage, trend, "",
        use_llm_override=False, quality_warnings=gate["warnings"],
        phases=detect_phases(df, eps),
        bed_summary=None, activity_trend=None, positional_stats=compute_positional_stats(df),
    )
    nd = narrative if isinstance(narrative, dict) else {}
    dom = nd.get("events_table_row_1_phase_type")
    bft_kwargs = dict(triage=triage, dominant_phase_type=dom,
                      hr_avg=nd.get("findings_hr_avg"), peak_hr=hr.max, min_hr=hr.min,
                      rr_avg=nd.get("findings_rr_avg"), peak_rr=rr.max)
    if "end_of_period" in _BFT_PARAMS:
        # Mirror generate_one: compute end-of-period clustering from episodes.
        from backend.signal_engine import compute_end_of_period_clustering
        bft_kwargs["end_of_period"] = compute_end_of_period_clustering(eps, ws, we)
    mf = build_findings_text(**bft_kwargs)
    cov_meta = round(100 * (dq.total_hours) / max(dq.expected_hours, 1), 1)  # raw
    return {
        "patient": pid, "report": label, "triage": triage, "episodes": len(eps),
        "dominant": dom, "major_findings": mf,
        "coverage_quality_pct": dq.quality_pct,
        "coverage_raw_pct": cov_meta,
    }


async def main(out):
    rows = []
    # PAM cohort
    pam = load_vitals()
    for num, pid in PATIENT_ORDER:
        meta = get_patient_metadata(pid); dr = meta.get("date_range", {})
        fs, fe = dr.get("start") or None, dr.get("end") or None
        raw = pam.get(pid)
        eps = detect_episodes(raw) if raw is not None else []
        crit = find_critical_week(raw, eps) if raw is not None else (fs, fe)
        thirty = detect_most_active_window(raw, eps, 30) if raw is not None else None
        ts, te = thirty if thirty else (fs, fe)
        for lbl, s, e in [("FullPeriod", fs, fe), ("30DayPeriod", ts, te), ("CriticalWeek", crit[0], crit[1])]:
            r = await _snap_one(pid, s, e, pam, lbl)
            if r: r["cohort"] = "PAM"; rows.append(r)
    # MedHab cohort
    folder = str(Path(__file__).parent / "data" / "medhab")
    mh = load_medhab_vitals(folder)
    for spec in discover_report_windows(folder):
        r = await _snap_one(spec["patient"], spec["start"], spec["end"], mh,
                            f"{spec['month_label']}", allow_low_cov=True)
        if r: r["cohort"] = "MedHab"; rows.append(r)
    Path(out).write_text(json.dumps(rows, indent=2))
    print(f"snapshot: {len(rows)} reports -> {out}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "snap.json"))
