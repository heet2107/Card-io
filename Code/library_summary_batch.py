#!/usr/bin/env python3
"""
CardioReport — 24h Library Summary Generator
============================================
One 24-hour summary report per library (the remote-nurse morning triage scan),
covering every patient in that library, critical on top. 24h ONLY — no 30-day
data. Same 24h severity engine as the per-patient banner, so statuses match.

Libraries are discovered from data/ sub-folders (same structure as the rest of
the app); a third library later produces a third summary with no code change.
Libraries are never mixed — each summary contains only its own client's
patients (the library privacy boundary).

Output:
    Reports/CardioReport_<Client>_24h_Summary.pdf   (one per library)

Usage:
    cd /Users/heetbarot/Documents/Cardio-io/Code
    python library_summary_batch.py
    python library_summary_batch.py --date 2026-06-30   # override "Generated" date
"""

from __future__ import annotations
import sys, argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from backend.client_registry import discover_clients, client_label
from backend.library_summary import build_library_summary
from backend.pdf_render import generate_library_summary_pdf


def main(outdir: Path, gen_date: str):
    outdir.mkdir(parents=True, exist_ok=True)
    clients = discover_clients()
    print(f"\n{'='*65}")
    print(f"  CardioReport 24h Library Summary")
    print(f"  Libraries discovered: {', '.join(clients) or '(none)'}")
    print(f"  Generated: {gen_date}")
    print(f"{'='*65}\n")

    for client in clients:
        summary = build_library_summary(client)
        pdf = generate_library_summary_pdf(summary, gen_date)
        safe = client.replace(" ", "_")
        fname = f"CardioReport_{safe}_24h_Summary.pdf"
        (outdir / fname).write_bytes(pdf)

        rows = summary["patients"]
        by_status: dict[str, int] = {}
        for r in rows:
            by_status[r["display_label"]] = by_status.get(r["display_label"], 0) + 1
        breakdown = ", ".join(f"{k}:{v}" for k, v in sorted(by_status.items()))
        print(f"   ✅  {fname}  ({len(pdf)//1024} KB)  "
              f"{len(rows)} patients  [{breakdown}]")
        print(f"        order: {', '.join(r['patient'] + '(' + r['display_label'] + ')' for r in rows)}")

    print(f"\n{'='*65}")
    print(f"  {len(clients)} library summary report(s) written to {outdir}")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CardioReport 24h Library Summary Generator")
    parser.add_argument("--outdir", default=str(Path(__file__).parent.parent / "Reports"),
                        help="Output directory (default: Reports/)")
    parser.add_argument("--date", default=datetime.now().strftime("%B %d, %Y"),
                        help="'Generated' date label (default: today)")
    args = parser.parse_args()
    main(Path(args.outdir), args.date)
