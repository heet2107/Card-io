"""
CardioReport – Live API Ingestion (Cardio webportal Reports API)
================================================================
The third adapter behind ``client_registry``, alongside ``excel_ingest`` (wide
PAM device Excel) and ``medhab_ingest`` (clean MedHab CSV). It emits the
**identical canonical frame** as both, so every downstream stage (signal_engine,
episodes, narrative, charts, pdf_render, quality_gates, window_intelligence,
notable_days, triage_24h) is unchanged.

The data client is passed IN, never constructed here. Anything exposing
``.reports.hourly_vitals(mac, start, end)`` and ``.v3.device(mac)`` works:

  * Phase 1, local parity runs  -> ``cardio_data.DataClient``
  * Phase 2, hosted report      -> ``ctx.data`` inside ``build(ctx)``
  * tests                       -> a fake

That is the point: the same function serves both phases with no rewrite, and
this module takes no HTTP dependency of its own.

THREE UNVERIFIED MAPPINGS are module-level constants rather than buried
guesses. Each is one line to correct once ``scripts/peek.py`` returns real
data; none of them fail loudly if wrong, which is exactly why they are here
and not inline.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional, Protocol, Sequence

import pandas as pd

from .config import Locations
from .excel_ingest import (
    _extract_location_from_sheet,
    apply_rr_noise_filter,
    apply_rr_physiologic_ceiling,
)
from .medhab_ingest import CANONICAL_COLUMNS


# ── Unverified mappings — correct these once peek.py returns real data ────────

#: Which hourly-rollup field carries the per-hour sample count that the Excel
#: and MedHab paths call ``cnt``. It drives the low-confidence flag in
#: ``episodes.py`` and ``signal_engine.py``, so a wrong choice silently shifts
#: coverage %, quality gates and triage bands without raising anything.
#: V3's ``vital-aggregates`` returns ``count`` per hour bucket (60 == one
#: sample/minute); ``hrSamples`` is the rollup API's per-channel analogue.
CNT_FIELD = "hrSamples"

#: Tried in order when ``CNT_FIELD`` is missing from a row.
CNT_FALLBACKS: tuple[str, ...] = ("hrSamples", "rrSamples", "samples")

#: Timezone to convert the API's UTC hours into before dropping tzinfo.
#: ``None`` keeps naive UTC, which matches ``medhab_ingest._parse_utc_hour``
#: exactly. Set this (e.g. "US/Central") ONLY if a parity run shows the
#: file-based cohort was recorded in local time — see ``frame_diff``.
SOURCE_TZ: Optional[str] = None

#: Hourly-rollup field -> canonical column. Names per the report author guide
#: and ``cardio_data/reports.py``; verify against out/peek/hourly_vitals.json.
_API_COL_MAP = {
    "hrAvg": "hr_avg", "hrMax": "hr_max", "hrMin": "hr_min",
    "rrAvg": "rr_avg", "rrMax": "rr_max", "rrMin": "rr_min",
}

_NUMERIC = ["hr_avg", "hr_max", "hr_min", "rr_avg", "rr_max", "rr_min", "cnt"]


class _Client(Protocol):
    """The slice of the data client this module uses."""
    reports: Any
    v3: Any


# ── Identity ─────────────────────────────────────────────────────────────────

def device_identity(client: _Client, mac: str) -> tuple[str, str]:
    """``(patient_id, location)`` for one device.

    Both come from the device's ``friendlyName``, which is where sensor
    placement lives in this platform (the typed ``location`` attribute is a
    coarser building/area label — "West Wing" — not Bed/Chair). The keyword
    match is ``excel_ingest._extract_location_from_sheet``, reused verbatim so
    Bed/Chair/Living Room are decided by exactly one rule across all adapters.

    Falls back to the MAC when the lookup fails, so one unreadable device never
    takes down a batch.
    """
    try:
        dev = client.v3.device(mac) or {}
    except Exception:
        dev = {}
    name = str(dev.get("friendlyName") or dev.get("name") or mac).strip()
    return name or mac, _extract_location_from_sheet(name)


# ── Normalisation ────────────────────────────────────────────────────────────

def _hours_to_naive(values: Iterable[Any], tz: Optional[str]) -> pd.Series:
    """Parse ``hourStart`` values to naive datetimes.

    The API is unambiguously UTC. With ``tz=None`` the result is naive UTC,
    matching the MedHab path. With a tz, values are converted then made naive,
    for alignment against a file cohort recorded in local time.
    """
    ts = pd.to_datetime(pd.Series(list(values), dtype="object"), errors="coerce", utc=True)
    if tz:
        ts = ts.dt.tz_convert(tz)
    return ts.dt.tz_localize(None)


def _pick_cnt(df: pd.DataFrame, raw: pd.DataFrame, cnt_field: str) -> pd.Series:
    """The per-hour sample count, honouring CNT_FIELD then CNT_FALLBACKS."""
    for field in (cnt_field, *CNT_FALLBACKS):
        if field in raw.columns:
            return pd.to_numeric(raw[field], errors="coerce")
    # No sample-count field at all: NaN, never 0. A fabricated 0 would read as
    # "no samples this hour" and flag every hour low-confidence.
    return pd.Series([float("nan")] * len(df), index=df.index)


def normalise_hours(
    rows: Sequence[dict],
    patient_id: str,
    location: str = Locations.UNKNOWN,
    *,
    cnt_field: Optional[str] = None,
    tz: Optional[str] = ...,
) -> pd.DataFrame:
    """Map raw ``hourly_vitals`` rows onto the canonical frame.

    Mirrors the rename -> numeric-coerce -> dropna sequence in
    ``medhab_ingest._normalise_medhab`` so the output is structurally identical
    to both existing paths.
    """
    cnt_field = CNT_FIELD if cnt_field is None else cnt_field
    tz = SOURCE_TZ if tz is ... else tz

    raw = pd.DataFrame(list(rows))
    if raw.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    df = raw.rename(columns=_API_COL_MAP).copy()
    df["patient_id"] = patient_id
    df["timestamp"] = _hours_to_naive(raw.get("hourStart", pd.Series(dtype="object")), tz)
    df["cnt"] = _pick_cnt(df, raw, cnt_field)

    for col in _NUMERIC:
        if col not in df.columns:
            df[col] = float("nan")
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")

    # Same drop rule as both file paths: no parseable hour, or neither vital.
    df = df.dropna(subset=["timestamp"])
    df = df[df["hr_avg"].notna() | df["rr_avg"].notna()]

    # The rollup returns a row only for hours that exist, so an absent hour is
    # absent rather than flagged — identical to the MedHab path, where coverage
    # is measured downstream as recorded/expected.
    df["gap_flag"] = 0
    df["location"] = location

    return df[[c for c in CANONICAL_COLUMNS if c in df.columns]].copy()


# ── Loader ───────────────────────────────────────────────────────────────────

def load_api_vitals(
    client: _Client,
    device_ids: Sequence[str],
    start,
    end,
    *,
    cnt_field: Optional[str] = None,
    tz: Optional[str] = ...,
    device_patient_map: Optional[dict] = None,
) -> dict[str, pd.DataFrame]:
    """Load live vitals as ``{patient_id: DataFrame}``.

    The same return contract as ``excel_ingest.load_vitals`` and
    ``medhab_ingest.load_medhab_vitals``.

    ``device_patient_map`` maps MAC -> patient_id for multi-sensor patients
    (PAM's bed+chair pairs), whose two devices must merge under one key the way
    ``data_registry_v2.PATIENT_GROUPS`` does for the Excel cohort. Default is
    one patient per device: the API exposes no grouping of its own, and
    inventing one would silently merge unrelated patients.
    """
    frames: list[pd.DataFrame] = []
    for mac in device_ids:
        patient_id, location = device_identity(client, mac)
        if device_patient_map and mac in device_patient_map:
            patient_id = device_patient_map[mac]
        hours = client.reports.hourly_vitals(mac, start, end)
        df = normalise_hours(hours, patient_id, location, cnt_field=cnt_field, tz=tz)
        if not df.empty:
            frames.append(df)

    if not frames:
        raise ValueError(
            f"No vitals returned for {len(list(device_ids))} device(s) between {start} and {end}"
        )

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(["patient_id", "timestamp", "location"]).reset_index(drop=True)
    combined = combined.drop_duplicates(subset=["patient_id", "timestamp", "location"], keep="first")
    combined = apply_rr_noise_filter(combined)
    combined = apply_rr_physiologic_ceiling(combined)

    return {pid: grp.reset_index(drop=True) for pid, grp in combined.groupby("patient_id")}
