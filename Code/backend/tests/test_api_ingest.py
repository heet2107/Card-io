"""
CardioReport – API ingest + parity tests

Runs entirely against a fake data client (no network, no credentials), the
same pattern reports-advanced uses in tests/test_sample_report.py.

What these lock down:
  * the API adapter emits the canonical frame, byte-identical in shape to the
    Excel and MedHab paths — that is the whole premise of the migration
  * the three unverified mappings (cnt field, location, timezone) behave as
    documented, so correcting one later is a one-line change with a test
  * frame_diff catches the two silent failures worth catching: a wrong cnt
    field, and an hour shift

Run:  cd Code && python -m pytest backend/tests/test_api_ingest.py -v
"""

from __future__ import annotations

import math
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from backend import api_ingest
from backend.api_ingest import load_api_vitals, normalise_hours
from backend.config import Locations
from backend.medhab_ingest import CANONICAL_COLUMNS
from backend.parity import detect_hour_shift, frame_diff


# ── Fakes ────────────────────────────────────────────────────────────────────

def _hours(n=48, start="2026-04-01T00:00:00Z", hr=62.0, rr=15.0, samples=60):
    """Hourly rows with a diurnal HR rhythm — real vitals have one, and a
    flat series carries no phase information for the shift detector."""
    ts = pd.date_range(start, periods=n, freq="h", tz="UTC")
    return [
        {
            "hourStart": t.isoformat().replace("+00:00", "Z"),
            "hrAvg": hr + 10 * math.sin(2 * math.pi * t.hour / 24),
            "hrMin": hr - 8, "hrMax": hr + 9,
            "rrAvg": rr, "rrMin": rr - 3, "rrMax": rr + 4,
            "hrSamples": samples, "rrSamples": samples - 2, "samples": samples + 5,
            "occupiedSamples": 50, "offlineSamples": 0,
        }
        for t in ts
    ]


class FakeReports:
    def __init__(self, per_mac):
        self._per_mac = per_mac

    def hourly_vitals(self, mac, start, end):
        return self._per_mac.get(mac, [])


class FakeV3:
    def __init__(self, names):
        self._names = names

    def device(self, mac):
        return {"id": mac, "friendlyName": self._names.get(mac, mac)}


class FakeClient:
    def __init__(self, per_mac, names=None):
        self.reports = FakeReports(per_mac)
        self.v3 = FakeV3(names or {})


BED, CHAIR = "AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"


def _client(**kw):
    return FakeClient(
        {BED: _hours(), CHAIR: _hours(hr=70.0)},
        {BED: "Bedroom", CHAIR: "LivRm Chair"},
        **kw,
    )


# ── Canonical schema (the migration's whole premise) ──────────────────────────

def test_emits_exactly_the_canonical_columns_in_order():
    out = load_api_vitals(_client(), [BED], "2026-04-01", "2026-04-03")
    df = out["Bedroom"]
    assert list(df.columns) == CANONICAL_COLUMNS


def test_numeric_dtypes_match_the_file_paths():
    """float64 across the board — a clean API page must not land on int64 and
    break frame interchangeability, the same trap medhab_ingest calls out."""
    df = load_api_vitals(_client(), [BED], "2026-04-01", "2026-04-03")["Bedroom"]
    for col in ["hr_avg", "hr_max", "hr_min", "rr_avg", "rr_max", "rr_min", "cnt"]:
        assert df[col].dtype == "float64", col


def test_returns_one_frame_per_patient_sorted_by_timestamp():
    out = load_api_vitals(_client(), [BED, CHAIR], "2026-04-01", "2026-04-03")
    assert set(out) == {"Bedroom", "LivRm Chair"}
    for df in out.values():
        assert df["timestamp"].is_monotonic_increasing


def test_empty_response_raises_rather_than_returning_an_empty_frame():
    with pytest.raises(ValueError, match="No vitals returned"):
        load_api_vitals(FakeClient({}), ["ZZ:ZZ"], "2026-04-01", "2026-04-03")


# ── Unverified mapping 1: cnt ────────────────────────────────────────────────

def test_cnt_comes_from_the_configured_field():
    rows = _hours(n=3)
    assert normalise_hours(rows, "p", cnt_field="hrSamples")["cnt"].iloc[0] == 60
    assert normalise_hours(rows, "p", cnt_field="rrSamples")["cnt"].iloc[0] == 58
    assert normalise_hours(rows, "p", cnt_field="samples")["cnt"].iloc[0] == 65


def test_cnt_falls_back_when_the_configured_field_is_absent():
    rows = [{k: v for k, v in r.items() if k != "hrSamples"} for r in _hours(n=3)]
    assert normalise_hours(rows, "p", cnt_field="hrSamples")["cnt"].iloc[0] == 58


def test_missing_sample_counts_are_nan_never_zero():
    """A fabricated 0 reads downstream as 'no samples this hour' and flags every
    hour low-confidence, shifting coverage % and triage."""
    rows = [
        {k: v for k, v in r.items() if k not in ("hrSamples", "rrSamples", "samples")}
        for r in _hours(n=3)
    ]
    assert normalise_hours(rows, "p")["cnt"].isna().all()


# ── Unverified mapping 2: location from friendlyName ─────────────────────────

@pytest.mark.parametrize(
    "friendly,expected",
    [
        ("Bedroom", Locations.BED),
        ("Room 12 Bed A", Locations.BED),
        ("LivRm Chair", Locations.LIVING_ROOM),
        ("Recliner Chair", Locations.CHAIR),
        ("Office-B", Locations.UNKNOWN),
    ],
)
def test_location_is_derived_from_friendly_name(friendly, expected):
    client = FakeClient({BED: _hours(n=2)}, {BED: friendly})
    df = load_api_vitals(client, [BED], "2026-04-01", "2026-04-02")[friendly]
    assert df["location"].iloc[0] == expected


def test_unreadable_device_falls_back_to_the_mac():
    class Broken(FakeV3):
        def device(self, mac):
            raise RuntimeError("scope denied")

    client = FakeClient({BED: _hours(n=2)})
    client.v3 = Broken({})
    out = load_api_vitals(client, [BED], "2026-04-01", "2026-04-02")
    assert BED in out


# ── Unverified mapping 3: timezone ───────────────────────────────────────────

def test_default_is_naive_utc_matching_the_medhab_path():
    df = normalise_hours(_hours(n=2, start="2026-04-01T00:00:00Z"), "p", tz=None)
    assert df["timestamp"].iloc[0] == pd.Timestamp("2026-04-01 00:00:00")
    assert df["timestamp"].dt.tz is None


def test_tz_knob_converts_then_drops_tzinfo():
    df = normalise_hours(_hours(n=2, start="2026-04-01T12:00:00Z"), "p", tz="US/Central")
    assert df["timestamp"].iloc[0] == pd.Timestamp("2026-04-01 07:00:00")  # UTC-5 in April
    assert df["timestamp"].dt.tz is None


# ── Downstream filters are applied, not reimplemented ────────────────────────

def test_rr_physiologic_ceiling_is_applied():
    rows = _hours(n=2, rr=99.0)  # above RR_PHYSIOLOGIC_CEILING (60)
    client = FakeClient({BED: rows}, {BED: "Bedroom"})
    df = load_api_vitals(client, [BED], "2026-04-01", "2026-04-02")["Bedroom"]
    assert df["rr_avg"].isna().all()


# ── Parity harness ───────────────────────────────────────────────────────────

def _canonical(n=48, start="2026-04-01T00:00:00Z", **kw):
    return normalise_hours(_hours(n=n, start=start, **kw), "p", Locations.BED, tz=None)


def test_identical_frames_report_match():
    d = frame_diff(_canonical(), _canonical())
    assert d["verdict"] == "MATCH"
    assert d["rows_joined"] == 48


def test_a_wrong_cnt_field_is_caught():
    file_side = normalise_hours(_hours(), "p", cnt_field="hrSamples", tz=None)
    api_side = normalise_hours(_hours(), "p", cnt_field="samples", tz=None)
    d = frame_diff(file_side, api_side)
    assert d["verdict"].startswith("DIFFERS")
    assert d["fields"]["cnt"]["status"] == "differs"
    assert d["fields"]["hr_avg"]["status"] == "ok"


def test_a_timezone_shift_is_caught_and_named():
    """The failure this whole harness exists for.

    A timezone bug is the SAME readings carrying the WRONG hours — not a
    different window. So shift the labels and leave the values alone.
    """
    rows = _hours(n=96)
    moved = [
        {**r, "hourStart": (pd.Timestamp(r["hourStart"]) + pd.Timedelta(hours=5))
                            .isoformat().replace("+00:00", "Z")}
        for r in rows
    ]
    d = frame_diff(normalise_hours(rows, "p", tz=None),
                   normalise_hours(moved, "p", tz=None))
    assert d["shift"]["conclusive"]
    assert d["shift"]["best_shift_hours"] == -5
    assert "timezone mismatch" in d["verdict"]


def test_shift_detector_stays_quiet_on_aligned_frames():
    s = detect_hour_shift(_canonical(), _canonical())
    assert s["best_shift_hours"] == 0
    assert not s["conclusive"]


def test_disjoint_windows_do_not_masquerade_as_a_shift():
    d = frame_diff(_canonical(start="2026-04-01T00:00:00Z"),
                   _canonical(start="2026-09-01T00:00:00Z"))
    assert d["rows_joined"] == 0
    assert "NO OVERLAP" in d["verdict"]
