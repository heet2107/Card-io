"""
CardioReport – MedHab CSV Ingestion (separate cohort)
=====================================================
The MedHab cohort ships as a clean 10-column CSV per patient per month, a
different *physical* schema from the wide PAM Health device-export Excel. This
module reads that shape and emits the **identical post-normalization frame** as
``excel_ingest.load_vitals`` so every downstream stage (signal_engine, episodes,
narrative, charts, pdf_render, quality_gates, window_intelligence) is unchanged.

Design guarantees:
  * The reader dispatches on FILE SHAPE only — extension + header — never on the
    patient (Device) or company name. See ``read_vitals_file`` / ``detect_shape``.
  * Available months are discovered from the DATA, not hardcoded: a ``June`` file
    dropped into the folder later is picked up with no code change.
  * ``cnt`` is carried through verbatim (real device data) and drives the
    low-confidence flag downstream — never fabricated or constant-filled here.

MedHab CSV columns (exact):
    Company, Device, Hour (UTC), HR avg, HR max, HR min, RR avg, RR max, RR min, cnt
"""

from __future__ import annotations
import re
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import Locations
from .excel_ingest import apply_rr_noise_filter, apply_rr_physiologic_ceiling


# ── Canonical schema (must match excel_ingest.load_vitals output exactly) ──────

# Column order + names are asserted identical to the Excel path by MH_001.
CANONICAL_COLUMNS = [
    "patient_id", "timestamp",
    "hr_avg", "hr_max", "hr_min",
    "rr_avg", "rr_max", "rr_min",
    "cnt", "gap_flag", "location",
]

# MedHab CSV header → canonical internal name. The Device column maps to the
# patient identity and the UTC hour string maps to the timestamp; both are
# handled specially in _normalise_medhab below.
_MEDHAB_COL_MAP = {
    "HR avg": "hr_avg",
    "HR max": "hr_max",
    "HR min": "hr_min",
    "RR avg": "rr_avg",
    "RR max": "rr_max",
    "RR min": "rr_min",
    "cnt":    "cnt",
}

# Header signature that identifies the MedHab CSV shape (order-independent).
_MEDHAB_HEADER_SIGNATURE = {
    "Company", "Device", "Hour (UTC)",
    "HR avg", "HR max", "HR min",
    "RR avg", "RR max", "RR min", "cnt",
}


# ── Shape-agnostic reader (dispatch by extension + header only) ────────────────

def read_vitals_file(path) -> pd.DataFrame:
    """Read a single vitals file, dispatching on FILE EXTENSION only.

    ``.csv``  → :func:`pandas.read_csv`
    ``.xlsx`` / ``.xls`` → :func:`pandas.read_excel`

    The function takes a path and nothing else — no patient id, no company
    name. It returns the *raw* frame (original headers); column normalization
    is a separate step so the CSV and Excel shapes converge downstream.

    This is the data-shape-agnostic detector guarded by MH_004: the dispatch
    keys on the file's physical shape, never on who the data belongs to.
    """
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".csv":
        return pd.read_csv(path)
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(path)
    raise ValueError(f"Unsupported vitals file extension: {ext!r} ({path.name})")


def detect_shape(raw: pd.DataFrame) -> str:
    """Classify a raw frame by its HEADER (not its contents/owner).

    Returns ``"medhab_csv"`` when the MedHab header signature is present,
    otherwise ``"unknown"``. Keys on column names only — blind to whose data
    it is or which site produced it.
    """
    cols = set(str(c).strip() for c in raw.columns)
    if _MEDHAB_HEADER_SIGNATURE.issubset(cols):
        return "medhab_csv"
    return "unknown"


# ── Normalisation (MedHab raw → canonical frame) ──────────────────────────────

def _parse_utc_hour(series: pd.Series) -> pd.Series:
    """Parse ``"2026-04-01 00:00 UTC"`` → naive UTC datetime.

    The trailing " UTC" is stripped and the value is treated as naive UTC, so
    timestamps are directly comparable with the PAM Health frames (which are
    also naive). No timezone conversion is applied.
    """
    cleaned = (
        series.astype(str)
        .str.replace(r"\s*UTC\s*$", "", regex=True)
        .str.strip()
    )
    return pd.to_datetime(cleaned, errors="coerce")


def _normalise_medhab(raw: pd.DataFrame) -> pd.DataFrame:
    """Map a raw MedHab CSV frame onto the canonical schema.

    Mirrors the rename → numeric-coerce → dropna sequence in excel_ingest so the
    output is structurally identical to the Excel path.
    """
    df = raw.rename(columns=_MEDHAB_COL_MAP).copy()

    # Patient identity comes from the Device column (friendly name).
    df["patient_id"] = raw["Device"].astype(str).str.strip()

    # Timestamp from the UTC hour string.
    df["timestamp"] = _parse_utc_hour(raw["Hour (UTC)"])

    # Numeric coercion, identical column set + dtype to excel_ingest. The Excel
    # path yields float64 for these (its NaN handling forces float); cast to
    # float so a clean MedHab file with no missing cnt doesn't land on int64 and
    # break frame interchangeability.
    for col in ["hr_avg", "hr_max", "hr_min", "rr_avg", "rr_max", "rr_min", "cnt"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")

    # Drop rows with no parseable timestamp or no vitals (same rule as Excel path).
    df = df.dropna(subset=["timestamp"])
    df = df[df["hr_avg"].notna() | df["rr_avg"].notna()]

    # MedHab has no gap flag column and no positional sensor; fill to match the
    # canonical frame. Location is genuinely unknown (single wearable-style
    # stream), never fabricated to a Chair/Bed position.
    df["gap_flag"] = 0
    df["location"] = Locations.UNKNOWN

    keep = [c for c in CANONICAL_COLUMNS if c in df.columns]
    return df[keep].copy()


def load_medhab_file(path) -> pd.DataFrame:
    """Read + normalise a single MedHab file into the canonical frame."""
    raw = read_vitals_file(path)
    if detect_shape(raw) != "medhab_csv":
        raise ValueError(
            f"{Path(path).name}: header does not match the MedHab CSV shape "
            f"(got columns {list(raw.columns)})"
        )
    return _normalise_medhab(raw)


# ── Month discovery (data-driven) ─────────────────────────────────────────────

def _modal_period(ts: pd.Series) -> pd.Period:
    """Return the dominant calendar month (YYYY-MM) of a timestamp series.

    Monthly exports include a trailing boundary hour (e.g. a May file ends at
    Jun 01 00:00); using the modal month attributes the file to the month that
    actually owns its data instead of spawning a spurious 1-hour June group.
    """
    return ts.dt.to_period("M").mode().iloc[0]


def _month_label(period: pd.Period) -> str:
    """``Period('2026-04')`` → ``"April_2026"`` (used in output filenames)."""
    return period.to_timestamp().strftime("%B_%Y")


def _iter_medhab_files(folder) -> list[Path]:
    """All ingestable files in the folder, sorted; CSV first, then Excel."""
    folder = Path(folder)
    files = sorted(folder.glob("*.csv")) + sorted(folder.glob("*.xlsx"))
    # Ignore Excel lock/temp files (~$...).
    return [f for f in files if not f.name.startswith("~$")]


def _month_matches(period: pd.Period, label: str, month_filter: Optional[str]) -> bool:
    """True when the filter selects this month. Accepts ``None``/``"all"``,
    a month name (``"April"``), a label (``"April_2026"``) or an ISO key
    (``"2026-04"``). Case-insensitive.
    """
    if not month_filter or month_filter.lower() == "all":
        return True
    f = month_filter.strip().lower()
    iso_key = str(period)                                  # "2026-04"
    month_name = period.to_timestamp().strftime("%B").lower()  # "april"
    return f in (iso_key, label.lower(), month_name)


def discover_report_windows(folder, month: Optional[str] = None) -> list[dict]:
    """Discover one 30DayPeriod report spec per patient-month from the data.

    Each ingestable file yields one spec. The reporting window is the file's
    actual data extent (min..max date); the month is the modal calendar month,
    discovered from the timestamps — never a hardcoded list.

    Returns a list of dicts sorted by (patient, month) with keys:
        patient, month_key ("2026-04"), month_label ("April_2026"),
        start ("YYYY-MM-DD"), end ("YYYY-MM-DD"), span_days, is_partial, file
    ``is_partial`` is True when the available data spans fewer than 30 days,
    which drives the partial-period fallback note downstream.
    """
    specs: list[dict] = []
    for path in _iter_medhab_files(folder):
        df = load_medhab_file(path)
        if df.empty:
            continue
        ts = df["timestamp"]
        period = _modal_period(ts)
        label = _month_label(period)
        if not _month_matches(period, label, month):
            continue
        span_days = int((ts.max().normalize() - ts.min().normalize()).days) + 1
        specs.append({
            "patient":     df["patient_id"].iloc[0],
            "month_key":   str(period),
            "month_label": label,
            "start":       ts.min().strftime("%Y-%m-%d"),
            "end":         ts.max().strftime("%Y-%m-%d"),
            "span_days":   span_days,
            "is_partial":  span_days < 30,
            "file":        path.name,
        })
    specs.sort(key=lambda s: (s["patient"], s["month_key"]))
    return specs


def discover_months(folder) -> list[str]:
    """Sorted unique month labels present in the folder (e.g. ``["April_2026",
    "May_2026"]``). Purely data-driven."""
    seen: dict[str, str] = {}
    for spec in discover_report_windows(folder):
        seen[spec["month_key"]] = spec["month_label"]
    return [seen[k] for k in sorted(seen)]


# ── Loader (canonical {patient: frame}, parity with load_vitals) ──────────────

def load_medhab_vitals(folder, month: Optional[str] = None) -> dict[str, pd.DataFrame]:
    """Load the MedHab cohort as ``{patient_id: DataFrame}`` — the same return
    contract as :func:`excel_ingest.load_vitals`.

    All of a patient's months are concatenated into a single frame (so the
    downstream trajectory comparison can see prior months); the per-month
    reporting window is applied later via ``apply_window``. When ``month`` is
    given, only files belonging to that month are loaded.

    The RR-noise filter is applied to the combined frame, matching the Excel
    ingest path exactly.
    """
    frames: list[pd.DataFrame] = []
    for path in _iter_medhab_files(folder):
        df = load_medhab_file(path)
        if df.empty:
            continue
        if month and month.lower() != "all":
            period = _modal_period(df["timestamp"])
            if not _month_matches(period, _month_label(period), month):
                continue
        frames.append(df)

    if not frames:
        raise FileNotFoundError(f"No MedHab vitals files found in {folder}"
                                + (f" for month {month!r}" if month else ""))

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(["patient_id", "timestamp"]).reset_index(drop=True)
    combined = combined.drop_duplicates(
        subset=["patient_id", "timestamp", "location"], keep="first"
    )
    combined = apply_rr_noise_filter(combined)
    combined = apply_rr_physiologic_ceiling(combined)

    return {pid: grp.reset_index(drop=True)
            for pid, grp in combined.groupby("patient_id")}
