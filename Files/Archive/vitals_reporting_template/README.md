# Vitals → Clinical Intelligence Reporter (Template)

This is a reusable CLI that turns hourly vitals aggregates into clinician-ready PDFs:
- **A) Nurse Dashboard** (shift-friendly: *where/when* + what to check + who to notify)
- **B) Physician/Cardiology Summary** (interpretation prompts + top episodes)
- **C) Automated RPM Summary** (triage + top episodes list)

It also writes plot PNGs that can be embedded in a portal.

## Install
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run
```bash
python vitals_reporter.py --input "/path/to/patient.xlsx" --config config.yaml --outdir outputs --cadence weekly
```

## Input columns (flexible)
Your current format is supported (uses `XL-time`, `avg_Hr`, `min_Hr`, `max_Hr`, `avg_Rr`, `min_Rr`, `max_Rr`, `cnt`).
The script also works with similar names (avg_hr/hr_avg/etc).

## Configure thresholds
Edit `config.yaml` to match your clinical policy per patient population.
