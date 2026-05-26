"""
CardioReport – FastAPI Main Application

Six-stage pipeline per Implementation Guide Section 9:
  Stage 1: Ingest (excel_ingest.py)
  Stage 2: Compute (signal_engine.py)
  Stage 3: Detect (episodes.py)
  Stage 4: Narrate (narrative_ai.py) — triage/trend computed BEFORE AI
  Stage 5: Chart (charts.py)
  Stage 6: Render (pdf_render.py)
"""

from __future__ import annotations
import hashlib
from datetime import datetime
from typing import Optional, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from cachetools import TTLCache

from .config import settings, Locations
from .models import (
    ReportRequest, ReportResponse,
    BedDaySummary, BedActivitySummary,
)
from .excel_ingest import (
    load_vitals, get_patient_ids, get_patient_metadata,
    load_bed_summary, load_low_hr_alerts, _find_bed_excel,
)
from .signal_engine import (
    apply_window, compute_stats, compute_full_stats, compute_data_quality,
    compute_data_resolution, compute_triage, compute_trend_assessment,
    compute_action_posture, compute_positional_stats, compute_activity_data,
)
from .episodes import detect_episodes, compute_rollups
from .narrative_ai import generate_narrative
from .charts import (
    generate_combined_chart, generate_histogram,
    generate_positional_chart, generate_activity_trend_chart,
    generate_bed_hours_chart,
)
from .pdf_render import generate_pdf


# ── App setup ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="CardioReport API",
    version=settings.app_version,
    description="Clinician-grade RPM intelligence report engine",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


# ── Cache ────────────────────────────────────────────────────────────────────

_report_cache: TTLCache = TTLCache(maxsize=64, ttl=300)  # 5 min TTL


def _cache_key(req: ReportRequest) -> str:
    raw = f"{req.patient_id}|{req.range_type}|{req.start}|{req.end}|{req.use_ai}"
    return hashlib.md5(raw.encode()).hexdigest()


# ── Pipeline ─────────────────────────────────────────────────────────────────

async def _run_pipeline(req: ReportRequest) -> tuple[dict, "pd.DataFrame"]:
    """Run the analysis pipeline.

    Steps:
      1. Look up patient
      2. Apply time window
      3. Run quality gates (REJECT → 422)
      4. Compute stats + data quality
      5. Detect episodes
      6. Compute triage + trend + action posture (BEFORE AI)
      7. Detect phases
      8. Compute report priority
      9. Generate narrative
     10. Generate charts
     11. Build response
    """
    import pandas as pd
    from .quality_gates import run_quality_gates
    from .window_intelligence import detect_phases, compute_report_priority
    from .models import Phase

    # ── Step 1: Look up patient ──────────────────────────────────────────
    all_data = load_vitals()
    if req.patient_id not in all_data:
        raise HTTPException(status_code=404, detail=f"Patient '{req.patient_id}' not found.")

    df = all_data[req.patient_id]

    # ── Step 2: Apply time window ────────────────────────────────────────
    from .window_intelligence import find_most_interesting_week
    if req.range_type == "smart_week":
        result = find_most_interesting_week(df)
        if result:
            ws_ts, we_ts = result["start"], result["end"]
            df = df[(df["timestamp"] >= ws_ts) & (df["timestamp"] <= we_ts)]
        else:
            # Fallback if no episodes found at all
            df = apply_window(df, "last_7d")
    else:
        df = apply_window(df, req.range_type, req.start, req.end)
    
    if df.empty:
        raise HTTPException(status_code=400, detail="No data in the selected time window.")

    # ── Step 1b: Prior Week Comparison (Now relative to window start) ────
    window_start_ts = df["timestamp"].min()
    window_end_ts = df["timestamp"].max()
    
    prior_start = window_start_ts - pd.Timedelta(days=7)
    prior_end = window_start_ts - pd.Timedelta(seconds=1)
    
    p_df = all_data[req.patient_id]
    p_df = p_df[(p_df["timestamp"] >= prior_start) & (p_df["timestamp"] <= prior_end)]
    
    prior_comparison = None
    if not p_df.empty:
        p_hr, p_rr = compute_stats(p_df)
        p_eps = detect_episodes(p_df)
        from .models import PriorComparison
        prior_comparison = PriorComparison(
            hr_avg=round(p_hr.mean, 1),
            rr_avg=round(p_rr.mean, 1),
            episode_count=len(p_eps),
            start_date=prior_start.strftime("%Y-%m-%d"),
            end_date=prior_end.strftime("%Y-%m-%d")
        )

    # ── Step 4: Run Quality Gates ────────────────────────────────────────
    gate_result = run_quality_gates(df, window_start_ts, window_end_ts)

    if not gate_result["can_generate"]:
        raise HTTPException(
            status_code=422,
            detail=gate_result["reason"],
        )
    quality_warnings = gate_result["warnings"]

    # ── Step 5: Compute stats + daily aggregates ─────────────────────────
    hr_stats, rr_stats = compute_stats(df)
    full_stats = compute_full_stats(df)
    data_quality = compute_data_quality(df)
    data_resolution = compute_data_resolution(df)

    # ── Step 6: Detect episodes ──────────────────────────────────────────
    episodes = detect_episodes(df)

    # Rollups
    rollups = compute_rollups(episodes, df)

    # ── Step 7: Compute triage + trend + action posture ──────────────────
    # SAFETY BOUNDARY — computed before AI, cannot be changed by AI
    triage = compute_triage(episodes, rollups.coupled_fraction, df=df)
    trend, _ = compute_trend_assessment(df, episodes)

    # Max severity band and score
    max_band = "S0"
    max_severity_score = 0
    for ep in episodes:
        if ep.severity_score > max_severity_score:
            max_severity_score = ep.severity_score
        if ep.severity_band > max_band:
            max_band = ep.severity_band

    action_posture = compute_action_posture(triage, trend, rollups.coupled_fraction, max_band)

    # Window bounds
    window_start = df["timestamp"].min().strftime("%Y-%m-%d")
    window_end = df["timestamp"].max().strftime("%Y-%m-%d")

    # ── Step 8: Detect phases ────────────────────────────────────────────
    raw_phases = detect_phases(df, episodes)
    phases = [Phase(**p) for p in raw_phases]

    # ── Step 9: Compute report priority ──────────────────────────────────
    report_priority = compute_report_priority(
        episodes, raw_phases, max_severity_score, quality_warnings
    )

    # ── Step 9b: Detect sensor type & load bed data ──────────────────────
    sensor_type = "chair"
    bed_summary_model = None
    bed_summary_df = None
    alerts_df = None
    chart_bed_hours_b64 = ""

    if "location" in df.columns:
        locations = df["location"].unique()
        if Locations.BED in locations:
            sensor_type = "bed"

    # Also detect from bed excel presence — but only if it belongs to THIS patient
    raw_bed_df = load_bed_summary()
    raw_alerts_df = load_low_hr_alerts()
    if raw_bed_df is not None:
        # Verify bed file belongs to this patient by checking sheet names
        bed_file = _find_bed_excel()
        if bed_file:
            import openpyxl
            try:
                wb = openpyxl.load_workbook(str(bed_file), read_only=True)
                first_sheet = wb.sheetnames[0] if wb.sheetnames else ""
                wb.close()
                if req.patient_id in first_sheet:
                    sensor_type = "bed"
                # else: bed file exists but belongs to a different patient
            except Exception:
                pass

    if sensor_type == "bed" and raw_bed_df is not None:
        bed_summary_df = raw_bed_df.copy()
        alerts_df = raw_alerts_df

        # Filter bed summary to the report window
        ws_ts = pd.Timestamp(window_start)
        we_ts = pd.Timestamp(window_end)
        bed_summary_df = bed_summary_df[
            (bed_summary_df["date"] >= ws_ts) & (bed_summary_df["date"] <= we_ts)
        ].reset_index(drop=True)

        if alerts_df is not None and not alerts_df.empty:
            # Strip timezone info to match naive timestamps
            if alerts_df["timestamp"].dt.tz is not None:
                alerts_df = alerts_df.copy()
                alerts_df["timestamp"] = alerts_df["timestamp"].dt.tz_localize(None)
            alerts_df = alerts_df[
                (alerts_df["timestamp"] >= ws_ts) &
                (alerts_df["timestamp"] <= we_ts + pd.Timedelta(days=1))
            ].reset_index(drop=True)

        if not bed_summary_df.empty:
            # Build BedActivitySummary
            hours = bed_summary_df["hours_in_bed"].dropna()
            hr_lows = bed_summary_df["hr_low"].dropna()

            # Days above 16h
            days_above_16 = int((hours > 16).sum())

            # Alert stats
            total_alerts = len(alerts_df) if alerts_df is not None else 0
            alert_dates = set()
            if alerts_df is not None and not alerts_df.empty:
                alert_dates = set(alerts_df["timestamp"].dt.normalize().unique())

            # HR min on high-bed-days vs normal
            high_bed_mask = bed_summary_df["hours_in_bed"] > 16
            hr_min_high = bed_summary_df.loc[high_bed_mask, "hr_low"].dropna()
            hr_min_normal = bed_summary_df.loc[~high_bed_mask, "hr_low"].dropna()

            daily_data = []
            for _, row in bed_summary_df.iterrows():
                h = row["hours_in_bed"]
                if pd.isna(h):
                    color = "gray"
                elif h > 16:
                    color = "red"
                elif h >= 13:
                    color = "amber"
                else:
                    color = "green"

                daily_data.append(BedDaySummary(
                    date=row["date"].strftime("%Y-%m-%d"),
                    hours_in_bed=float(h) if not pd.isna(h) else 0,
                    hr_min=float(row["hr_low"]) if pd.notna(row.get("hr_low")) else 0,
                    has_alert=row["date"] in alert_dates,
                    color=color,
                ))

            bed_summary_model = BedActivitySummary(
                mean_daily_hours=float(hours.mean()) if len(hours) > 0 else 0,
                min_hours=float(hours.min()) if len(hours) > 0 else 0,
                max_hours=float(hours.max()) if len(hours) > 0 else 0,
                days_above_16h=days_above_16,
                alert_days=len(alert_dates),
                total_alerts=total_alerts,
                hr_min_high_bed_days=float(hr_min_high.mean()) if len(hr_min_high) > 0 else 0,
                hr_min_normal_days=float(hr_min_normal.mean()) if len(hr_min_normal) > 0 else 0,
                daily_data=daily_data,
            )

            # Generate bed hours chart
            chart_bed_hours_b64 = generate_bed_hours_chart(bed_summary_df, alerts_df)

    # ── Step 9c: Activity & Location Stats ───────────────────────────────
    positional_stats = compute_positional_stats(df)
    activity_data = compute_activity_data(df)

    # ── Step 10: Generate narrative ──────────────────────────────────────
    narrative, actions, narrative_source = await generate_narrative(
        req.patient_id, window_start, window_end,
        hr_stats, rr_stats, data_quality,
        episodes, rollups, triage, trend, action_posture,
        use_llm_override=req.use_ai,
        quality_warnings=quality_warnings,
        phases=raw_phases,
        bed_summary=bed_summary_model,
        activity_trend=activity_data,
        positional_stats=positional_stats,
    )

    # Cap actions at max_actions
    actions = actions[:settings.max_actions]

    # ── Step 11: Generate charts ─────────────────────────────────────────
    chart_b64 = generate_combined_chart(df, episodes)
    histogram_b64 = generate_histogram(df)

    chart_positional_b64 = generate_positional_chart(df)
    chart_activity_b64 = generate_activity_trend_chart(df)

    # ── Coverage Summary ─────────────────────────────────────────────────
    # Build coverage summary — never show > 100%.
    # For multi-sensor patients the combined row count exceeds calendar hours,
    # so report per-sensor instead (each sensor vs. the same expected hours).
    if positional_stats and len(positional_stats.rows) >= 1:
        expected_h = data_quality.expected_hours
        parts = []
        for row in positional_stats.rows:
            loc_hours = row.hours
            loc_pct = min(round(loc_hours / max(expected_h, 1) * 100, 1), 100.0)
            parts.append(f"{row.location}: {loc_hours}/{expected_h}h ({loc_pct}%)")
        coverage_summary = "  |  ".join(parts)
    else:
        capped_pct = min(data_quality.quality_pct, 100.0)
        coverage_summary = f"{data_quality.total_hours}/{data_quality.expected_hours}h ({capped_pct}%)"

    print(f"DEBUG PIPELINE: hr_stats is {hr_stats}")
    report_dict = {
        "patient_id": req.patient_id,
        "window_start": window_start,
        "window_end": window_end,
        "report_date": (window_end_ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        "data_resolution": data_resolution,
        "coverage_summary": coverage_summary,
        "disclaimer": "Decision-support summary derived from longitudinal vital sign trends; interpret in clinical context.",
        "hr_summaries": hr_stats.model_dump() if hasattr(hr_stats, "model_dump") else hr_stats,
        "rr_summaries": rr_stats.model_dump() if hasattr(rr_stats, "model_dump") else rr_stats,
        "full_stats": full_stats.model_dump() if full_stats else None,
        "data_quality": data_quality.model_dump() if hasattr(data_quality, "model_dump") else data_quality,
        "episodes": [e.model_dump() if hasattr(e, "model_dump") else e for e in episodes[:settings.max_events_table]],
        "episode_rollups": rollups.model_dump() if hasattr(rollups, "model_dump") else rollups,
        "triage": triage,
        "trend_assessment": trend,
        "overall_action_posture": action_posture,
        "max_severity_score": max_severity_score,
        "narrative": narrative,
        "suggested_actions": actions,
        "use_ai": req.use_ai,
        "narrative_source": narrative_source,
        "report_priority": report_priority,
        "phases": [p.model_dump() if hasattr(p, "model_dump") else p for p in phases],
        "quality_warnings": quality_warnings,
        "positional_comparison": positional_stats.model_dump() if positional_stats else None,
        "activity_trend": activity_data.model_dump() if activity_data else None,
        "chart_combined_b64": chart_b64,
        "chart_histogram_b64": histogram_b64,
        "chart_positional_b64": chart_positional_b64,
        "chart_activity_b64": chart_activity_b64,
        "sensor_type": sensor_type,
        "bed_summary": bed_summary_model.model_dump() if bed_summary_model else None,
        "chart_bed_hours_b64": chart_bed_hours_b64,
        "prior_comparison": prior_comparison.model_dump() if prior_comparison else None,
    }
    return report_dict, df


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/api/patients")
async def list_patients():
    """Return list of patient IDs from Excel."""
    try:
        patients = get_patient_ids()
        return {"patients": patients}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/patients/{patient_id}/locations")
async def patient_locations(patient_id: str):
    """Return the locations (Chair, Bed, Living Room) and date range for a patient.
    
    Enables the frontend to intelligently disable unavailable report types.
    """
    try:
        meta = get_patient_metadata(patient_id)
        if not meta["locations"]:
            raise HTTPException(status_code=404, detail=f"Patient '{patient_id}' not found or has no data.")
        return meta
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/patients/{patient_id}/interesting-week")
async def interesting_week(patient_id: str):
    """Find the 7-day window with the highest clinical burden for a patient.

    Slides a 7-day window across the entire dataset, runs episode detection
    on each position, and returns the top-scoring result.
    """
    from .window_intelligence import find_most_interesting_week

    all_data = load_vitals()
    if patient_id not in all_data:
        raise HTTPException(status_code=404, detail=f"Patient '{patient_id}' not found.")

    df = all_data[patient_id]
    result = find_most_interesting_week(df)

    if result is None:
        raise HTTPException(status_code=404, detail="No window with sufficient data found.")

    return result

@app.post("/api/report/preview")
async def report_preview(req: ReportRequest):
    """Generate and return the report as JSON for web preview."""
    key = _cache_key(req)
    if key in _report_cache:
        return _report_cache[key]

    report_dict, df = await _run_pipeline(req)
    from fastapi.encoders import jsonable_encoder
    result = jsonable_encoder(report_dict)
    _report_cache[key] = result
    return result


@app.post("/api/report/pdf")
async def report_pdf(req: ReportRequest):
    """Generate and return the PDF report."""
    from .pdf_render import _v
    report_obj, df = await _run_pipeline(req)

    episodes = _v(report_obj, "episodes", [])
    pdf_bytes = generate_pdf(report_obj, df=df, episodes=episodes)

    pid = _v(report_obj, "patient_id")
    ws = _v(report_obj, "window_start")
    we = _v(report_obj, "window_end")
    filename = f"CardioReport_{pid}_{ws}_{we}.pdf"
    
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/report/events.json")
async def export_events(patient_id: str, range_type: Literal["last_24h", "last_7d", "last_15d", "last_1m", "last_3m", "custom", "smart_week"] = "last_3m",                        start: Optional[str] = None, end: Optional[str] = None):
    """Export detected episodes as JSON."""
    req = ReportRequest(patient_id=patient_id, range_type=range_type,
                        start=start, end=end)
    report, _ = await _run_pipeline(req)
    return {
        "patient_id": report["patient_id"],
        "window": {"start": report["window_start"], "end": report["window_end"]},
        "triage": report["triage"],
        "trend_assessment": report["trend_assessment"],
        "episodes": report["episodes"],
        "rollups": report["episode_rollups"],
    }


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": settings.app_version}
