#!/usr/bin/env python3
"""
CardioReport MedHab Batch Generator
===================================
Separate cohort entry point for the MedHab patients. Reuses the existing
pipeline engine (generate_one + build_summary_pdf from batch_generate) but reads
the MedHab CSV shape and writes to its OWN output tree — it never touches the
PAM Health cohort, registry, or Reports/ numbering.

For each patient-month discovered in the data folder, a 30DayPeriod report is
generated over that month's window (mirroring the PAM Health 30-day asset).
Months are discovered from the data, not hardcoded, so a future June file is
picked up with no code change.

Output (Reports/medhab/, separate from the PAM Health Reports/):
    <NN>_<Patient>_<Month>_30DayPeriod.pdf
    BatchSummary_MedHab.pdf

Usage:
    cd /Users/heetbarot/Documents/Cardio-io/Code
    python medhab_batch.py                      # all available months
    python medhab_batch.py --month April        # one month
    python medhab_batch.py --folder data/medhab --outdir ../Reports/medhab
"""

from __future__ import annotations
import sys, asyncio, traceback, argparse, time, zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backend.medhab_ingest import (
    load_medhab_vitals, discover_report_windows, discover_months,
)
from batch_generate import generate_one, build_summary_pdf, _safe_name


# Default MedHab input folder (the cohort's CSVs live here, separate from the
# PAM Health Excel files in data/). Overridable via --folder.
_DEFAULT_FOLDER = Path(__file__).parent / "data" / "medhab"


async def main(folder: Path, outdir: Path, month: str | None = None):
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"\n{'='*65}")
    print(f"  CardioReport MedHab Batch Generator")
    print(f"  Input  ← {folder}")
    print(f"  Output → {outdir}")
    print(f"{'='*65}\n")

    months = discover_months(folder)
    print(f"Available months (data-driven): {', '.join(months) or '(none)'}")
    if month and month.lower() != "all":
        print(f"Month filter: {month}")

    t0 = time.time()
    all_data = load_medhab_vitals(folder, month=month)
    specs = discover_report_windows(folder, month=month)
    print(f"  Loaded {len(all_data)} patients, {len(specs)} patient-month report(s) "
          f"in {time.time()-t0:.1f}s\n")

    summary_results: list[dict | None] = []
    report_num = 0
    flagged: list[str] = []  # patients where coverage / low-confidence shifted triage notes
    # (month_folder, filename) for each written report — drives the zip layout.
    written: list[tuple[str, str]] = []

    for i, spec in enumerate(specs, start=1):
        patient = spec["patient"]
        month_label = spec["month_label"]
        safe = _safe_name(patient)
        print(f"── [{i:02d}/{len(specs)}] {patient} {month_label}  "
              f"({spec['start']} → {spec['end']}, {spec['span_days']}d"
              f"{', partial' if spec['is_partial'] else ''})")

        if patient not in all_data:
            print(f"     WARN: {patient} not in loaded data. Skipping.")
            summary_results.append(None)
            continue

        try:
            result = await generate_one(
                patient, "custom", spec["start"], spec["end"], all_data,
                report_label="30DayPeriod",
                is_fallback_90d=spec["is_partial"],   # drives partial-period note
                allow_low_coverage=True,              # render low-coverage months w/ badge
            )
            if result is None:
                print(f"     SKIPPED (quality gate)")
                summary_results.append(None)
                continue

            report_num += 1
            fname = f"{report_num:02d}_{safe}_{month_label}_30DayPeriod.pdf"
            (outdir / fname).write_bytes(result["pdf_bytes"])
            # month_label is e.g. "April_2026" → zip folder "April".
            written.append((month_label.split("_")[0], fname))
            size_kb = len(result["pdf_bytes"]) / 1024
            print(f"     ✅  {fname}  ({size_kb:.0f} KB)  triage={result['triage']}  "
                  f"eps={result['episodes']}  coverage={result['coverage']}")

            # Surface patients where low coverage or low-confidence flags are
            # material (warnings present), per the report-back ask.
            warns = result.get("quality_warnings") or []
            if warns:
                flagged.append(f"{patient} {month_label}: {result['triage']} — {'; '.join(warns)}")

            result["file_label"] = "30DayPeriod"
            result["num"] = f"{report_num:02d}"
            summary_results.append(result)

        except Exception as e:
            print(f"     ❌  FAILED: {e}")
            traceback.print_exc()
            summary_results.append(None)

    # ── Batch summary (MedHab roster, no PAM PATIENT_ORDER) ───────────────────
    valid = [r for r in summary_results if r]
    roster = sorted({r["patient_id"] for r in valid})
    summary_bytes = build_summary_pdf(
        valid,
        patient_order=roster,
        per_patient_report_label=None,   # most-severe-month per patient
        title="CardioReport — MedHab Batch Summary",
    )
    (outdir / "BatchSummary_MedHab.pdf").write_bytes(summary_bytes)
    print(f"\n   ✅  BatchSummary_MedHab.pdf  ({len(summary_bytes)//1024} KB)")

    # ── Package as one zip, with each month in its own folder ─────────────────
    # Reports are grouped into per-month folders (April/, May/, …) inside the
    # zip; the cross-month batch summary sits at the zip root.
    zip_path = outdir.parent / "CardioReport_MedHab.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for month_folder, fname in written:
            zf.write(outdir / fname, arcname=f"{month_folder}/{fname}")
        zf.write(outdir / "BatchSummary_MedHab.pdf", arcname="BatchSummary_MedHab.pdf")
    months = sorted({m for m, _ in written})
    print(f"   📦  {zip_path}  ({zip_path.stat().st_size//1024} KB, "
          f"{len(written)} reports across folders: {', '.join(months)})")

    # ── Final status ──────────────────────────────────────────────────────────
    success = len(valid)
    failed = len(summary_results) - success
    print(f"\n{'='*65}")
    print(f"  MEDHAB BATCH COMPLETE")
    print(f"  {success} reports generated  |  {failed} skipped/failed")
    print(f"  Output directory: {outdir}")
    for f in sorted(outdir.glob("*.pdf")):
        print(f"    {f.name}  ({f.stat().st_size // 1024} KB)")
    if flagged:
        print(f"\n  Patients with coverage / low-confidence warnings:")
        for line in flagged:
            print(f"    • {line}")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CardioReport MedHab Batch PDF Generator")
    parser.add_argument("--folder", default=str(_DEFAULT_FOLDER),
                        help="MedHab CSV input folder")
    parser.add_argument("--outdir", default=str(Path(__file__).parent.parent / "Reports" / "medhab"),
                        help="Output directory (default: Reports/medhab/)")
    parser.add_argument("--month", default=None,
                        help="Month filter: name ('April'), label ('April_2026'), "
                             "ISO key ('2026-04'), or 'all' (default).")
    args = parser.parse_args()
    asyncio.run(main(Path(args.folder), Path(args.outdir), month=args.month))
