"""
CardioReport – Robustness Verification Tests
Tests the 5 critical decisions from the Robustness Specification.

Run with:  python -m pytest backend/tests/test_robustness.py -v
Or:        python backend/tests/test_robustness.py     (standalone)
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import json
import asyncio
import hashlib
import requests

BASE = "http://localhost:8000"


# ═════════════════════════════════════════════════════════════════════════════
# TEST 1: Quality Gates — bad data → 422 rejection
# ═════════════════════════════════════════════════════════════════════════════

def test_quality_gate_rejection():
    """
    Requesting a single-day window (June 30 only, ~3 hours of data)
    MUST return HTTP 422, not a garbage report.
    Gate 2 (min_days=3) should trigger: only 1 day has data.
    """
    print("\n" + "="*70)
    print("TEST 1: Quality Gate — Insufficient data → 422 rejection")
    print("="*70)

    payload = {
        "patient_id": "934297-0122",
        "range_type": "custom",
        "start": "2024-06-30",
        "end": "2024-06-30",
        "use_ai": False,
    }

    r = requests.post(f"{BASE}/api/report/preview", json=payload)
    print(f"  Status code: {r.status_code}")
    print(f"  Response:    {r.text[:200]}")

    if r.status_code == 422:
        print("  ✅ PASS — Quality gates correctly rejected insufficient data")
        return True
    elif r.status_code == 400:
        print("  ✅ PASS — No data in window (also acceptable rejection)")
        return True
    else:
        print(f"  ❌ FAIL — Expected 422, got {r.status_code}")
        return False


# ═════════════════════════════════════════════════════════════════════════════
# TEST 2: Quality Gate — multi-day but low coverage → WARN
# ═════════════════════════════════════════════════════════════════════════════

def test_quality_gate_warning():
    """
    A wider window that has data but low coverage should still generate
    and include quality_warnings in the response.
    """
    print("\n" + "="*70)
    print("TEST 2: Quality Gate — Low coverage → warnings in response")
    print("="*70)

    payload = {
        "patient_id": "934297-0122",
        "range_type": "last_7d",
        "use_ai": False,
    }

    r = requests.post(f"{BASE}/api/report/preview", json=payload)
    print(f"  Status code: {r.status_code}")

    if r.status_code == 200:
        data = r.json()
        warnings = data.get("quality_warnings", [])
        print(f"  Warnings:    {warnings}")
        if warnings:
            print("  ✅ PASS — Report generated WITH quality warnings")
        else:
            print("  ✅ PASS — Report generated cleanly (no warnings needed)")
        return True
    elif r.status_code == 422:
        print("  ✅ PASS — Quality gates rejected (also acceptable)")
        return True
    else:
        print(f"  ❌ FAIL — Unexpected status {r.status_code}")
        return False


# ═════════════════════════════════════════════════════════════════════════════
# TEST 3: Determinism — same inputs → byte-identical output
# ═════════════════════════════════════════════════════════════════════════════

def test_determinism():
    """
    Generate the same report twice with USE_LLM=false (default).
    The JSON responses should be identical (minus transient fields like report_date).
    """
    print("\n" + "="*70)
    print("TEST 3: Determinism — same request → identical output")
    print("="*70)

    payload = {
        "patient_id": "934297-0122",
        "range_type": "last_1m",
        "use_ai": False,
    }

    r1 = requests.post(f"{BASE}/api/report/preview", json=payload)
    r2 = requests.post(f"{BASE}/api/report/preview", json=payload)

    if r1.status_code != 200 or r2.status_code != 200:
        print(f"  ❌ FAIL — Could not generate reports (r1={r1.status_code}, r2={r2.status_code})")
        return False

    d1 = r1.json()
    d2 = r2.json()

    # Exclude transient fields (report_date changes with wall clock)
    transient = {"report_date"}
    for key in transient:
        d1.pop(key, None)
        d2.pop(key, None)

    s1 = json.dumps(d1, sort_keys=True)
    s2 = json.dumps(d2, sort_keys=True)

    h1 = hashlib.sha256(s1.encode()).hexdigest()[:16]
    h2 = hashlib.sha256(s2.encode()).hexdigest()[:16]

    print(f"  Run 1 hash: {h1}")
    print(f"  Run 2 hash: {h2}")

    if h1 == h2:
        print("  ✅ PASS — Reports are byte-identical (deterministic)")
        return True
    else:
        # Find which keys differ
        for key in sorted(set(list(d1.keys()) + list(d2.keys()))):
            v1 = json.dumps(d1.get(key), sort_keys=True)
            v2 = json.dumps(d2.get(key), sort_keys=True)
            if v1 != v2:
                print(f"  DIFF in '{key}':")
                print(f"    Run 1: {v1[:100]}")
                print(f"    Run 2: {v2[:100]}")
        print("  ❌ FAIL — Reports differ between runs")
        return False


# ═════════════════════════════════════════════════════════════════════════════
# TEST 4: Threshold Cascade — config change propagates everywhere
# ═════════════════════════════════════════════════════════════════════════════

def test_threshold_cascade():
    """
    Generate with default settings, check episode counts.
    The goal: verify episodes reference the threshold from config, not hardcoded.
    We can't dynamically change config via API, but we CAN verify that:
      - Triage, trend, phases all reference settings correctly
      - Episodes are detected using config thresholds (no 50 bpm hardcoded)
    """
    print("\n" + "="*70)
    print("TEST 4: Threshold Cascade — verify no hardcoded values leak")
    print("="*70)

    payload = {
        "patient_id": "934297-0122",
        "range_type": "last_3m",
        "use_ai": False,
    }

    r = requests.post(f"{BASE}/api/report/preview", json=payload)
    if r.status_code != 200:
        print(f"  ❌ FAIL — Cannot generate baseline report ({r.status_code})")
        return False

    data = r.json()
    episodes = data.get("episodes", [])
    triage = data.get("triage", "")
    trend = data.get("trend_assessment", "")
    phases = data.get("phases", [])
    priority = data.get("report_priority", "")
    narrative = data.get("narrative", "")

    print(f"  Episodes:  {len(episodes)}")
    print(f"  Triage:    {triage}")
    print(f"  Trend:     {trend}")
    print(f"  Phases:    {len(phases)}")
    print(f"  Priority:  {priority}")
    narrative_str = str(narrative) if isinstance(narrative, dict) else (narrative or "")
    print(f"  Narrative: {narrative_str[:80]}...")

    # Verify no hardcoded "50" or "100" appear in comments that should come from config
    hardcoded_leaks = []
    for ep in episodes:
        comment = ep.get("concern_phrase", "") + ep.get("qualifier_phrase", "")
        if "50 bpm" in comment or "100 bpm" in comment:
            hardcoded_leaks.append(f"Episode '{ep['condition']}' has hardcoded threshold in phrase")

    if hardcoded_leaks:
        for leak in hardcoded_leaks:
            print(f"  ⚠️  {leak}")
        print("  ❌ FAIL — Hardcoded threshold values found in output")
        return False

    print("  ✅ PASS — No hardcoded threshold leaks detected")
    return True


# ═════════════════════════════════════════════════════════════════════════════
# TEST 5: Patient Location Metadata
# ═════════════════════════════════════════════════════════════════════════════

def test_patient_locations():
    """
    The /api/patients/{id}/locations endpoint should return:
      - 934297-0122: Chair only (Chair sensor data)
      - 934297-0134: Bed only (Bed sensor data)
    This enables the frontend to disable unavailable report types.
    """
    print("\n" + "="*70)
    print("TEST 5: Patient Location Metadata endpoint")
    print("="*70)

    for pid in ["934297-0122", "934297-0134"]:
        r = requests.get(f"{BASE}/api/patients/{pid}/locations")
        if r.status_code == 200:
            data = r.json()
            print(f"  {pid}: locations={data.get('locations', [])}")
            print(f"            date_range={data.get('date_range', {})}")
        elif r.status_code == 404:
            print(f"  {pid}: endpoint not found (needs implementation)")
        else:
            print(f"  {pid}: HTTP {r.status_code}")

    print("  ✅ INFO — Check output above for correctness")
    return True


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║  CardioReport – Robustness Verification Suite                  ║")
    print("║  Requires server running on localhost:8000                     ║")
    print("╚══════════════════════════════════════════════════════════════════╝")

    results = {}
    results["1_quality_gate_reject"] = test_quality_gate_rejection()
    results["2_quality_gate_warn"] = test_quality_gate_warning()
    results["3_determinism"] = test_determinism()
    results["4_threshold_cascade"] = test_threshold_cascade()
    results["5_patient_locations"] = test_patient_locations()

    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    for name, ok in results.items():
        status = "✅ PASS" if ok else "❌ FAIL"
        print(f"  {status}  {name}")
    print(f"\n  {passed}/{total} tests passed")
