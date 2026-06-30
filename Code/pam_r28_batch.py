#!/usr/bin/env python3
"""
CardioReport — PAM Health Round 28 Batch Generator
==================================================
Regenerates the PAM Health cohort with the current R28 design, through the
new client-library structure (data/pam_health/), and packages the result as a
single zip for Sajol.

Per Sajol's June 23 call, the 24h "view" ships as the embedded triage layer
that now sits at the top of every report (color status banner + 24h snapshot +
episodic-events sub-table + one-line summary). Each patient therefore gets two
trend PDFs — 30DayPeriod and 90DayPeriod — both carrying that 24h layer. (A
standalone 24h-window PDF is intentionally not emitted: a 1-day window is below
the clinical minimum-substantive-days gate; the 24h intelligence lives inside
the 30/90-day reports instead.)

PAM data is loaded ONLY from its own client folder via the registry, so no
other cohort's data can be mixed in (privacy boundary, R28_007).

Output (Reports/pam_health_r28/ + a zip alongside it):
    <NN>_<Patient>_30DayPeriod.pdf
    <NN>_<Patient>_90DayPeriod.pdf
    BatchSummary_PAM_Health.pdf
    CardioReport_PAM_Health_R28.zip   (all of the above)

Usage:
    cd /Users/heetbarot/Documents/Cardio-io/Code
    python pam_r28_batch.py
    python pam_r28_batch.py --outdir ../Reports/pam_health_r28
"""

from __future__ import annotations
import sys, asyncio, traceback, argparse, time, zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backend.client_registry import load_client_data, client_is_csv
from batch_generate import (
    generate_one, build_summary_pdf, _safe_name,
    detect_episodes, detect_most_active_window,
)

_CLIENT = "pam_health"


def _windows(raw_df):
    """Compute the 30-day and 90-day windows (most-active window, with
    full-period fallback for under-N-day patients) — same logic the PAM batch
    has always used, so the assets line up with prior rounds."""
    full_start = raw_df["timestamp"].min().strftime("%Y-%m-%d")
    full_end = raw_df["timestamp"].max().strftime("%Y-%m-%d")
    eps = detect_episodes(raw_df)

    def _win(days):
        w = detect_most_active_window(raw_df, eps, window_size_days=days)
        if w is not None:
            return w[0], w[1], False
        return full_start, full_end, True

    t_start, t_end, t_fb = _win(30)
    n_start, n_end, n_fb = _win(90)
    return [
        ("30DayPeriod", t_start, t_end, t_fb),
        ("90DayPeriod", n_start, n_end, n_fb),
    ]


async def main(outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"\n{'='*65}")
    print(f"  CardioReport PAM Health R28 Batch Generator")
    print(f"  Library → {_CLIENT}  (Excel-shape: {not client_is_csv(_CLIENT)})")
    print(f"  Output  → {outdir}")
    print(f"{'='*65}\n")

    t0 = time.time()
    all_data = load_client_data(_CLIENT)        # ONLY the PAM client folder
    patients = sorted(all_data.keys())
    print(f"  Loaded {len(patients)} PAM patients in {time.time()-t0:.1f}s: "
          f"{', '.join(patients)}\n")

    summary_results: list[dict | None] = []
    report_num = 0
    flagged: list[str] = []

    for i, patient in enumerate(patients, start=1):
        safe = _safe_name(patient)
        raw_df = all_data[patient]
        print(f"── [{i:02d}/{len(patients)}] {patient}  "
              f"({raw_df['timestamp'].min().date()} → {raw_df['timestamp'].max().date()})")

        for label, wstart, wend, is_fb in _windows(raw_df):
            try:
                result = await generate_one(
                    patient, "custom", wstart, wend, all_data,
                    report_label=label,
                    is_fallback_90d=is_fb,
                    allow_low_coverage=True,
                )
                if result is None:
                    print(f"     SKIPPED {label} (quality gate)")
                    continue

                report_num += 1
                fname = f"{report_num:02d}_{safe}_{label}.pdf"
                (outdir / fname).write_bytes(result["pdf_bytes"])
                snap = result.get("snapshot_24h") or {}
                print(f"     ✅  {fname}  ({len(result['pdf_bytes'])//1024} KB)  "
                      f"30d={result['triage']}  24h={snap.get('status_24h')}  "
                      f"24h-events={snap.get('event_count')}")

                warns = result.get("quality_warnings") or []
                if warns:
                    flagged.append(f"{patient} {label}: {result['triage']} — {'; '.join(warns)}")

                # Only the 30DayPeriod report feeds the batch summary roster, so
                # each patient appears once (its current primary asset).
                if label == "30DayPeriod":
                    result["file_label"] = label
                    result["num"] = f"{report_num:02d}"
                    summary_results.append(result)

            except Exception as e:
                print(f"     ❌  FAILED {label}: {e}")
                traceback.print_exc()

    # ── Batch summary (PAM roster) ────────────────────────────────────────────
    valid = [r for r in summary_results if r]
    roster = sorted({r["patient_id"] for r in valid})
    summary_bytes = build_summary_pdf(
        valid,
        patient_order=roster,
        per_patient_report_label="30DayPeriod",
        title="CardioReport — PAM Health Batch Summary (R28)",
    )
    (outdir / "BatchSummary_PAM_Health.pdf").write_bytes(summary_bytes)
    print(f"\n   ✅  BatchSummary_PAM_Health.pdf  ({len(summary_bytes)//1024} KB)")

    # ── Package as one zip ────────────────────────────────────────────────────
    zip_path = outdir.parent / "CardioReport_PAM_Health_R28.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(outdir.glob("*.pdf")):
            zf.write(f, arcname=f.name)
    print(f"   📦  {zip_path}  ({zip_path.stat().st_size//1024} KB, "
          f"{len(list(outdir.glob('*.pdf')))} PDFs)")

    print(f"\n{'='*65}")
    print(f"  PAM HEALTH R28 BATCH COMPLETE")
    print(f"  {report_num} reports across {len(patients)} patients (30Day + 90Day each)")
    if flagged:
        print(f"\n  Patients with coverage / low-confidence warnings:")
        for line in flagged:
            print(f"    • {line}")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CardioReport PAM Health R28 Batch")
    parser.add_argument("--outdir",
                        default=str(Path(__file__).parent.parent / "Reports" / "pam_health_r28"),
                        help="Output directory (default: Reports/pam_health_r28/)")
    args = parser.parse_args()
    asyncio.run(main(Path(args.outdir)))
