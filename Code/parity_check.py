#!/usr/bin/env python
"""
Phase 1 exit test: does the live API produce the same canonical frame as the
Excel/CSV file we already trust?

    python parity_check.py --patient "Bedroom" --client medhab \
        --devices C4:7F:51:91:1C:47 --from 2026-04-01 --to 2026-04-30

Auth and HTTP are reused from the reports-advanced checkout rather than
reimplemented here; the password is read by its getpass prompt and never
written to disk. Point REPORTS_ADVANCED at the checkout if it is not the
default sibling of this repo.

Exit code is 0 only on MATCH, so this can gate a merge.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

RA = pathlib.Path(
    os.environ.get("REPORTS_ADVANCED",
                   pathlib.Path.home() / "Documents" / "reports-advanced")
) / "service"


def _data_client(email: str | None):
    if not RA.exists():
        sys.exit(f"reports-advanced checkout not found at {RA}\n"
                 f"clone it, or set REPORTS_ADVANCED=/path/to/reports-advanced")
    sys.path.insert(0, str(RA))
    sys.path.insert(0, str(RA / "scripts"))
    from _auth import REPORTS, V3, get_token          # noqa: E402
    from cardio_data import DataClient                # noqa: E402

    _, token = get_token(email)
    return DataClient(token, v3_base=V3, reports_base=REPORTS)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--devices", required=True, help="comma-separated MACs for this patient")
    ap.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD inclusive, UTC")
    ap.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD inclusive, UTC")
    ap.add_argument("--patient", required=True, help="patient id on the FILE side")
    ap.add_argument("--client", required=True, help="library id, e.g. medhab / pam_health")
    ap.add_argument("--email", default=None)
    ap.add_argument("--cnt-field", default=None,
                    help="override the hourly field mapped to cnt (hrSamples|rrSamples|samples)")
    ap.add_argument("--tz", default=None,
                    help="convert API hours into this tz before comparing, e.g. US/Central")
    args = ap.parse_args()

    from backend.api_ingest import load_api_vitals
    from backend.client_registry import load_client_data
    from backend.parity import format_diff, frame_diff

    file_side = load_client_data(args.client)
    if args.patient not in file_side:
        sys.exit(f"patient {args.patient!r} not in library {args.client!r}; "
                 f"have: {', '.join(sorted(file_side))[:300]}")

    macs = [m.strip() for m in args.devices.split(",") if m.strip()]
    api_side = load_api_vitals(
        _data_client(args.email), macs, args.date_from, args.date_to,
        cnt_field=args.cnt_field, tz=args.tz,
        # every selected device belongs to the one patient being compared
        device_patient_map={m: args.patient for m in macs},
    )

    lhs = file_side[args.patient]
    lhs = lhs[(lhs["timestamp"] >= args.date_from) & (lhs["timestamp"] <= f"{args.date_to} 23:59:59")]

    diff = frame_diff(lhs, api_side[args.patient])
    print(f"\npatient={args.patient}  client={args.client}  devices={','.join(macs)}")
    print(f"window={args.date_from}..{args.date_to}  cnt_field={args.cnt_field or 'default'}  tz={args.tz or 'naive UTC'}\n")
    print(format_diff(diff))
    sys.exit(0 if diff["verdict"] == "MATCH" else 1)


if __name__ == "__main__":
    main()
