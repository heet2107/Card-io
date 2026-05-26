# CardioReport — System Architecture & Technical Documentation

> **Version:** 1.0.0 · **Last Updated:** March 8, 2026
> **Status:** All 5 robustness tests passing ✅

---

## 1. Project Vision

CardioReport is a **clinician-grade Remote Patient Monitoring (RPM) intelligence engine**. It transforms raw vital sign data (Heart Rate and Breathing Rate) collected from passive sensors (Chair, Bed, Living Room) into structured, actionable clinical reports.

The system is designed around a core principle: **a bad report is worse than no report**. Every output passes through quality gates, deterministic logic, and configurable thresholds before it reaches a clinician.

---

## 2. Codebase Structure

```text
/Cardio-io/
├── Code/
│   ├── backend/                   ← Python/FastAPI backend
│   │   ├── __init__.py
│   │   ├── main.py                ← API endpoints & 6-stage pipeline orchestration
│   │   ├── config.py              ← Single source of truth (all settings, labels, thresholds)
│   │   ├── models.py              ← Pydantic data contracts (request/response schemas)
│   │   ├── excel_ingest.py        ← Stage 1: Data loading, normalization, deduplication
│   │   ├── signal_engine.py       ← Stage 2: Stats, triage, trend, positional analysis
│   │   ├── episodes.py            ← Stage 3: Episode detection & severity scoring
│   │   ├── quality_gates.py       ← Stage 0: Pre-report validation (5 gates)
│   │   ├── window_intelligence.py ← Stage 3.5: Phase detection & report priority
│   │   ├── narrative_ai.py        ← Stage 4: Deterministic + optional LLM narrative
│   │   ├── charts.py              ← Stage 5: Matplotlib chart generation (4 chart types)
│   │   ├── pdf_render.py          ← Stage 6: ReportLab PDF generation
│   │   └── tests/
│   │       └── test_robustness.py ← Automated robustness verification suite
│   ├── frontend/                  ← Vanilla JS/CSS clinical dashboard
│   │   ├── index.html             ← Main report interface (semantic HTML5)
│   │   ├── style.css              ← Premium clinical design system
│   │   └── app.js                 ← API interaction, rendering, location intelligence
│   ├── .env                       ← Environment overrides (optional)
│   └── requirements.txt           ← Python dependencies
├── Files/                         ← Excel data source ("the database")
│   ├── 934297-0122_*.xlsx         ← Patient 934297-0122 (Chair/Living Room sensor)
│   ├── 934298-0293_*.xlsx         ← Patient 934297-0134 (alias, Bed sensor)
│   └── Juanita-Bed-*.xlsx         ← Patient 934297-0134 (Bed sensor)
└── SYSTEM_OVERVIEW.md             ← This file
```

---

## 3. The Analysis Pipeline

Every report follows a strict **6-stage pipeline**. Each stage has a single responsibility and uses only centralized configuration from `config.py`.

```
┌─────────────────────────────────────────────────────────────────────┐
│  API Request: { patient_id, range_type, use_ai }                   │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
  ┌─────────────────────────▼──────────────────────────────┐
  │  Stage 0: Quality Gates (quality_gates.py)              │
  │  5 gates → PASS / WARN / REJECT (422)                   │
  │  Coverage, Min Days, Confidence, Range Sanity, Gaps      │
  └─────────────────────────┬──────────────────────────────┘
                            │ (only if PASS/WARN)
  ┌─────────────────────────▼──────────────────────────────┐
  │  Stage 1: Ingest (excel_ingest.py)                      │
  │  Load Excel → normalize columns → deduplicate            │
  │  Extract location (Chair/Bed/Living Room)                │
  └─────────────────────────┬──────────────────────────────┘
                            │
  ┌─────────────────────────▼──────────────────────────────┐
  │  Stage 2: Compute (signal_engine.py)                    │
  │  HR/RR stats (Mean, Min, Max, P5, P95)                  │
  │  Data quality, resolution, positional stats              │
  │  Triage (RED/YELLOW/GREEN) — computed BEFORE AI          │
  │  Trend assessment (Stable/Intermittent/Progressive)      │
  │  Action posture (Routine → Urgent)                       │
  └─────────────────────────┬──────────────────────────────┘
                            │
  ┌─────────────────────────▼──────────────────────────────┐
  │  Stage 3: Detect (episodes.py + window_intelligence.py) │
  │  Episode detection with configurable thresholds          │
  │  Severity scoring (S0→S3) with clinical weighting        │
  │  Co-occurrence / coupling detection                      │
  │  Phase detection (group days into clinical phases)       │
  │  Report priority classification (HIGH/MEDIUM/LOW/SKIP)   │
  └─────────────────────────┬──────────────────────────────┘
                            │
  ┌─────────────────────────▼──────────────────────────────┐
  │  Stage 4: Narrate (narrative_ai.py)                     │
  │  Deterministic: phrase taxonomy (default, USE_LLM=false) │
  │  AI-Enhanced: structured LLM prompt (USE_LLM=true)       │
  │  Generates: narrative + suggested clinical actions        │
  └─────────────────────────┬──────────────────────────────┘
                            │
  ┌─────────────────────────▼──────────────────────────────┐
  │  Stage 5: Chart (charts.py)                             │
  │  A. Daily candlestick (HR + RR dual panel)              │
  │  B. Distribution histogram                               │
  │  C. Positional comparison (HR/RR by location)            │
  │  D. Activity trend (hours/day with rolling average)      │
  └─────────────────────────┬──────────────────────────────┘
                            │
  ┌─────────────────────────▼──────────────────────────────┐
  │  Stage 6: Render (pdf_render.py)                        │
  │  ReportLab PDF with header, stats, episodes, charts      │
  │  100% parity with web preview                            │
  └─────────────────────────┬──────────────────────────────┘
                            │
  ┌─────────────────────────▼──────────────────────────────┐
  │  Output: JSON (web preview) or PDF (download)           │
  └─────────────────────────────────────────────────────────┘
```

---

## 4. Feature Details

### 4.1 Quality Gates (Decision 1)

Five validation checks run **before** any report computation begins. A failed gate returns HTTP 422 with a human-readable rejection reason.

| Gate | Check | Reject Threshold | Warn Threshold |
|------|-------|-------------------|----------------|
| 1. Coverage | recorded hours / expected hours | < 30% | < 50% |
| 2. Min Days | days with ≥ 4 hours of data | < 3 days | — |
| 3. Confidence | ratio of low-confidence readings | > 50% | > 25% |
| 4. Range Sanity | HR 10–250 bpm, RR 2–60 brpm | — | Out of range |
| 5. Gaps | largest gap between readings | — | > 72 hours |

**Implementation:** `quality_gates.py` — all thresholds are read from `config.py` `Settings`.

**Verified:** Requesting a single-day window (June 30, ~3 hours) correctly returns 422: *"Only 1 day(s) have sufficient data (need 3)"*.

---

### 4.2 Episode Detection & Severity Scoring (Decision 2)

Episodes are continuous time periods where vitals exceed defined thresholds.

| Condition | Internal Name | Threshold | Display Name |
|-----------|---------------|-----------|--------------|
| Severe Bradycardia | `Conditions.SEVERE_BRADY` | HR min < 45 bpm | Very Low Heart Rate |
| Bradycardia | `Conditions.BRADYCARDIAC` | HR avg < 50 bpm | Low Heart Rate |
| Tachycardia | `Conditions.TACHYCARDIA` | HR avg > 100 bpm | High Heart Rate |
| Tachypnea | `Conditions.TACHYPNEA` | RR avg > 24 brpm | Elevated Breathing Rate |

**Severity scoring formula:**
```
score = base_weight[condition]
      + (duration_hours - 1) × duration_bonus_per_hour
      + coupling_bonus     (if HR + RR co-occur)
      - low_conf_penalty   (if any hour is low confidence)
```

**Severity bands:**

| Band | Score Range | Clinical Meaning |
|------|------------|------------------|
| S0 | 0–4 | Brief deviation; continue monitoring |
| S1 | 5–8 | Sustained deviation; review context |
| S2 | 9–12 | Sustained pattern; consider provider review |
| S3 | 13+ | Critical sustained pattern; urgent review advised |

**Implementation:** `episodes.py` — all weights and boundaries in `config.py` `Settings`.

---

### 4.3 Triage Classification (Decision 3)

Automatic RED / YELLOW / GREEN classification computed **before** the AI narrative (safety boundary).

| Color | Trigger |
|-------|---------|
| **RED** | Severe bradycardia ≥ 4h, OR Tachypnea ≥ 8h, OR coupled + severity ≥ 9 |
| **YELLOW** | Max severity score ≥ 5 |
| **GREEN** | All else |

**Implementation:** `signal_engine.py` → `compute_triage()`.

---

### 4.4 Phase Detection (Decision 4)

The monitoring window is segmented into clinically distinct phases.

**Algorithm:**
1. Each day is classified as `stable`, `low_hr`, `high_hr`, or `mixed` based on episode burden
2. Consecutive same-class days are grouped into phases
3. Single-day phases are absorbed into neighbors (prevents noise)
4. Maximum 5 phases (top-scored phases kept)
5. Label format: "Phase 1: Stable", "Phase 2: Mixed Instability"

**Implementation:** `window_intelligence.py` → `detect_phases()`.

---

### 4.5 Report Priority (Decision 5)

| Priority | Criteria |
|----------|----------|
| **HIGH** | Coupled episodes + severity ≥ S2, OR severity ≥ S2, OR ≥ 3 phases |
| **MEDIUM** | Has episodes + severity ≥ S1, OR ≥ 3 episodes |
| **LOW** | Everything else |
| **SKIP** | Quality gate rejected |

**Implementation:** `window_intelligence.py` → `compute_report_priority()`.

---

### 4.6 Positional Vital Sign Comparison

When data comes from multiple locations (e.g., Chair vs Living Room), the system shows side-by-side vital sign comparisons.

**Clinical Significance:** A positional respiratory difference (e.g., +2.2 brpm in Living Room vs Chair) can indicate fluid redistribution and correlates with orthopnea assessment.

**Components:**
- **Comparison Table:** Location × HR Avg × RR Avg × Hours Recorded
- **Positional Chart:** Paired bar chart (Chart C)
- **Narrative Injection:** Auto-generated interpretation of the positional difference

**Implementation:** `signal_engine.py` → `compute_positional_stats()` + `charts.py` → `generate_positional_chart()`.

---

### 4.7 Activity Trend Analysis

Tracks daily activity hours (time outside of Bed) with color-coded day classification.

| Color | Threshold | Meaning |
|-------|-----------|---------|
| 🟢 Green | ≥ 20h/day | High activity |
| 🟡 Amber | 14–20h/day | Medium activity |
| 🔴 Red | < 14h/day | Low activity |

Includes a 7-day rolling average trend line for recovery tracking.

**Implementation:** `signal_engine.py` → `compute_activity_data()` + `charts.py` → `generate_activity_trend_chart()`.

---

### 4.8 Dual Narrative Engine

| Mode | Toggle | Behavior |
|------|--------|----------|
| **Deterministic** (default) | `USE_LLM=false` | Phrase taxonomy — predictable, auditable, regulatory-safe |
| **AI-Enhanced** (opt-in) | `USE_LLM=true` | Structured LLM prompt with clinical guardrails |

**Deterministic narrative structure:**
1. Opening: days + hours + coverage
2. Episode summary with clinical display names
3. Phase narrative with per-phase statistics
4. HR spread observation
5. Coupling observation (if applicable)
6. Quality warnings (if applicable)

**AI guardrails:** When LLM is enabled, it receives pre-computed stats and is constrained to clinical language ("may indicate", "warrants correlation with"), never making diagnostic claims.

**Implementation:** `narrative_ai.py` → `generate_narrative()`.

---

### 4.9 Patient Location Intelligence (Frontend)

**New feature:** When a patient is selected, the frontend fetches their available sensor locations from `/api/patients/{id}/locations`.

- **934297-0122:** Shows `🏠 Living Room` badge (Chair/Living Room sensor, data 2024-04-26 to 2024-08-16)
- **934297-0134:** Shows `🛏️ Bed` badge (Bed sensor, data 2023-10-23 to 2023-12-06)

Date pickers are automatically bounded to the patient's actual data range.

**Implementation:** `excel_ingest.py` → `get_patient_metadata()` + `main.py` → `/api/patients/{id}/locations` endpoint + `app.js` → `onPatientChange()`.

---

### 4.10 Deterministic Chart Generation

Charts are **byte-identical** when generated from the same data (verified by SHA-256 hash comparison). This is achieved by:
- Fixed numpy random seed (`np.random.seed(42)`)
- Matplotlib Agg backend (no GUI dependency)
- Settings-driven DPI and color palette

**Implementation:** `charts.py` — fixed seed at module level.

---

## 5. Configuration Architecture

All tuneable parameters live in a single `Settings` class in `config.py`, inheriting from Pydantic `BaseSettings`. This means every value can be overridden via:
- `.env` file in the project root
- Environment variables (e.g., `THRESHOLD_BRADYCARDIA=48`)

### Key Configuration Classes

| Class | Purpose | Example |
|-------|---------|---------|
| `Settings` | All thresholds, weights, boundaries | `threshold_bradycardia=50.0` |
| `Conditions` | Canonical internal condition names | `SEVERE_BRADY = "Severe Bradycardia"` |
| `TriageLabels` | Triage classification labels | `RED = "Red"` |
| `PhaseTypes` | Phase classification types | `STABLE = "stable"` |
| `GateStatus` | Quality gate result codes | `REJECT = "REJECT"` |
| `Locations` | Sensor location names | `CHAIR = "Chair"` |
| `ChartColors` | Centralized color palette | `HR = "#2563EB"` |
| `CONDITION_DISPLAY` | Internal → clinical display name map | `SEVERE_BRADY → "Very Low Heart Rate"` |
| `STATS_LABELS` | Technical → clinical stat labels | `"Avg HR (bpm)" → "Heart Rate Avg (bpm)"` |

**Zero magic numbers policy:** No module defines its own thresholds. Every number comes from `Settings`.

---

## 6. API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/health` | Health check (version info) |
| `GET` | `/api/patients` | List all patient IDs from Excel |
| `GET` | `/api/patients/{id}/locations` | Get locations & date range for a patient |
| `POST` | `/api/report/preview` | Generate full report as JSON (web preview) |
| `POST` | `/api/report/pdf` | Generate and download PDF report |
| `GET` | `/api/report/events.json` | Export detected episodes as JSON |

---

## 7. Technology Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| **API** | FastAPI + Uvicorn | Async HTTP server with automatic OpenAPI docs |
| **Data Models** | Pydantic v2 | Request/response validation & serialization |
| **Data Engine** | Pandas + NumPy | Time-series analysis & statistical computation |
| **Charts** | Matplotlib | 4 chart types with deterministic output |
| **PDF** | ReportLab | Clinical-grade PDF generation |
| **Data Source** | openpyxl | Excel file parsing (multi-format) |
| **Cache** | cachetools (TTLCache) | 5-minute report cache for repeated requests |
| **Frontend** | HTML5 + CSS3 + Vanilla JS | Zero-dependency clinical dashboard |
| **Fonts** | Inter (Google Fonts) | Premium typography |

---

## 8. Robustness Test Results

All 5 tests pass as of March 8, 2026:

```
╔══════════════════════════════════════════════════════════════════╗
║  CardioReport – Robustness Verification Suite                  ║
╚══════════════════════════════════════════════════════════════════╝

  ✅ PASS  1_quality_gate_reject   (June 30 only → 422 rejection)
  ✅ PASS  2_quality_gate_warn     (Low coverage → warnings in response)
  ✅ PASS  3_determinism           (Same input → byte-identical output)
  ✅ PASS  4_threshold_cascade     (No hardcoded values leak into output)
  ✅ PASS  5_patient_locations     (Location metadata endpoint works)

  5/5 tests passed
```

Run the suite with: `python backend/tests/test_robustness.py` (requires server running on port 8000).

---

## 9. Design Decisions

1. **Safety boundary:** Triage, trend, and action posture are computed **before** the AI narrative and cannot be overridden by the LLM. This prevents AI hallucination from affecting clinical classification.

2. **Phase merging:** Single-day phases are absorbed into neighbors to prevent "phase explosion" (12+ micro-phases) in long monitoring windows. Maximum 5 phases.

3. **Deduplication:** Overlapping Excel files are automatically deduplicated by timestamp (966 duplicates removed in test data). This prevents inflated episode counts and corrupted statistics.

4. **Deterministic by default:** The LLM toggle defaults to `false`. The deterministic phrase taxonomy is auditable and reproducible — critical for regulatory compliance.

5. **Pipeline parity:** Web preview and PDF use the exact same computation pipeline. Charts share a common rendering function with resolution/size parameters.

6. **Threshold cascade:** Changing any threshold in `config.py` (or via environment variable) automatically propagates to episode detection, chart threshold lines, histogram markers, and narrative labels. No hardcoded values exist.
