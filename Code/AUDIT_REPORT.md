# CardioReport Codebase Audit Report

**Date:** 2026-04-13
**Scope:** Full backend codebase audit — data flow, bug investigation, config coverage, test gaps
**Working directory:** `/Users/heetbarot/Documents/Cardio-io/Code/`

---

## Section 1: Codebase Map

### File Inventory

| File | Lines | Purpose |
|------|-------|---------|
| `backend/__init__.py` | 1 | Package init |
| `backend/main.py` | 487 | FastAPI app, 6-stage pipeline orchestrator |
| `backend/config.py` | 335 | Centralized Pydantic settings, constants |
| `backend/models.py` | 200 | Pydantic data models (Episode, ReportResponse, etc.) |
| `backend/quality_gates.py` | 134 | 5-gate data quality validator |
| `backend/episodes.py` | 209 | Episode detection and severity scoring |
| `backend/signal_engine.py` | 384 | Stats, triage, trend, action posture computation |
| `backend/excel_ingest.py` | 607 | Excel loader, registry integration, multi-sensor |
| `backend/window_intelligence.py` | 426 | Phase detection, interesting week finder |
| `backend/data_registry_v2.py` | 382 | Sensor/patient discovery from filenames |
| `backend/narrative_ai.py` | 1088 | Narrative generation, trajectory, reconciliation |
| `backend/charts.py` | 1136 | All chart generation (candlestick, histogram, activity, timeline) |
| `backend/pdf_render.py` | 1096 | ReportLab PDF assembly (2-page and 3-page layouts) |
| `backend/tests/test_robustness.py` | 271 | Integration tests (requires running server) |
| **TOTAL** | **6756** | |

### Top-Level Functions (Key Files)

#### episodes.py
```
_severity_score(condition, hours, coupled, low_conf)          :16
_severity_band(score)                                         :42
detect_episodes(df)                                           :52
compute_rollups(episodes, df)                                 :188
```

#### signal_engine.py
```
apply_window(df, range_type, start, end)                      :24
compute_stats(df)                                             :53
compute_full_stats(df)                                        :72
compute_data_resolution(df)                                   :111
compute_data_quality(df)                                      :140
compute_triage(episodes, coupled_fraction, df)                :177
compute_trend_assessment(df, episodes)                        :244
compute_positional_stats(df)                                  :313
compute_activity_data(df)                                     :340
compute_action_posture(triage, trend, coupled_fraction, max_band) :375
```

#### window_intelligence.py
```
detect_phases(df, episodes)                                   :17
find_most_interesting_week(df)                                :382
compute_report_priority(episodes, phases, max_severity_score, quality_warnings) :303
```

#### narrative_ai.py
```
compute_reporting_period_days(window_start, window_end)       :22
build_coverage_string(...)                                    :43
reconcile_counts(episodes, display_phases)                    :124
generate_deterministic_narrative(...)                         :174
classify_trajectory_direction(delta_episodes, delta_hours)    :549
compute_trajectory(full_df, window_start, window_end, report_type) :598
build_trajectory_line(trajectory)                             :678
build_specific_action_posture(eps, phases, triage, counts, trajectory) :723
generate_narrative(...)                                       :~1050
```

#### charts.py
```
_daily_agg(df)                                                :38
classify_day_severity(date, episodes)                         :90
get_severity_color_and_width(severity)                        :127
choose_candlestick_strategy(reporting_days)                   :138
aggregate_to_weekly(dly, eps)                                 :145
chart_candlestick_weekly(dly, eps, phases, window_start, window_end) :189
_generate_generic_candlestick(daily, ep_days, ...)            :451
generate_candlestick_for_pdf(df, episodes, ...)               :648
_generate_generic_histogram(df, figsize, ...)                 :674
generate_histogram_for_pdf(df)                                :754
generate_activity_trend_chart(df, figsize)                    :816
generate_activity_trend_chart_for_pdf(df)                     :875 (DUPLICATE at :1059)
chart_episode_timeline_for_pdf(episodes, start_date, end_date) :1066
```

#### pdf_render.py
```
_styles()                                                     :95
_build_header(report, st, page_w)                             :254
_build_compact_header(report, st, page_w, page_num, total_pages) :301
build_status_timeline_segments(window_start, window_end, display_phases) :338
_get_phase_label_for_width(phase_type, segment_width_inches, full_label) :393
render_status_timeline_bar(window_start, window_end, display_phases, ...) :419
render_timeline_date_axis(segments, reporting_days, ...)       :458
format_episode_date_phrase(ep_start_ts, ep_end_ts)            :504
build_intelligent_key_findings(eps, daily_summary, ...)        :550
generate_pdf(report, df, episodes)                            :736
```

### Call Chain: Single Report Generation

```
POST /api/report/pdf (ReportRequest)
  └─ _run_pipeline(req)                         [main.py:78]
       ├─ load_vitals()                          [excel_ingest.py]
       ├─ apply_window() / find_most_interesting_week()  [signal_engine / window_intelligence]
       ├─ run_quality_gates()                    [quality_gates.py]
       ├─ compute_stats() + compute_data_quality() + compute_data_resolution()
       ├─ detect_episodes(df)                    [episodes.py:52]
       ├─ compute_rollups(episodes, df)          [episodes.py:188]
       ├─ compute_triage()                       [signal_engine.py:177]
       ├─ compute_trend_assessment()             [signal_engine.py:244]
       ├─ compute_action_posture()               [signal_engine.py:375]
       ├─ detect_phases(df, episodes)            [window_intelligence.py:17]
       ├─ compute_report_priority()              [window_intelligence.py:303]
       ├─ generate_narrative()                   [narrative_ai.py]
       │    ├─ reconcile_counts(episodes, display_phases)  [:124]
       │    ├─ compute_trajectory()              [:598]
       │    ├─ build_trajectory_line()           [:678]
       │    └─ build_specific_action_posture()   [:723]
       ├─ generate_combined_chart() / generate_histogram() / generate_activity_trend_chart()
       └─ generate_pdf(report, df, episodes)     [pdf_render.py:736]
            ├─ chart_episode_timeline_for_pdf()  [charts.py:1066]
            ├─ build_status_timeline_segments()  [:338]
            ├─ render_status_timeline_bar()       [:419]
            ├─ build_intelligent_key_findings()   [:550]
            ├─ generate_candlestick_for_pdf()     [charts.py:648]
            ├─ generate_histogram_for_pdf()       [charts.py:754]
            └─ generate_activity_trend_chart_for_pdf()  [charts.py:1059]
```

### Config Settings Usage

85+ settings fields used across 12 modules. Key categories:

| Category | Example Settings | Used In |
|----------|-----------------|---------|
| Episode thresholds | `brady_hr_avg=45`, `elevated_hr_avg=80`, `tachy_hr_avg=100` | episodes.py |
| Severity scoring | `base_severe_brady`, `duration_bonus_per_hour`, `coupling_bonus` | episodes.py |
| Triage rules | `red_severe_brady_hours`, `yellow_min_severity`, `critical_hr_low` | signal_engine.py |
| Quality gates | `gate_coverage_reject=0.30`, `gate_min_days=3` | quality_gates.py |
| Episode merging | `episode_merge_gap_hours=1` | episodes.py |
| Chart dimensions | `candlestick_width_inches`, `histogram_height_inches` | charts.py, pdf_render.py |
| Timeline rendering | `PHASE_ACRONYMS`, `PHASE_SINGLE_LETTERS`, `timeline_single_letter_width_inches` | pdf_render.py |
| Page layout | `full_period_allow_3_pages`, `full_period_three_page_threshold_days=90` | pdf_render.py |

---

## Section 2: Data Flow Trace

### Step 1: Excel Ingestion → DataFrame

**`load_vitals()`** (`excel_ingest.py:187`) scans `.xlsx` files, normalizes columns, builds timestamps, resolves multi-sensor patients via `data_registry_v2`.

**Output DataFrame columns:**
`patient_id, timestamp, hr_avg, hr_max, hr_min, rr_avg, rr_max, rr_min, cnt, gap_flag, location`

**Multi-sensor handling:** `PATIENT_GROUPS` in `data_registry_v2.py` defines patients with multiple devices (EG, JB, RSanchez). All sheets for the same patient are concatenated and deduplicated on `(patient_id, timestamp, location)`.

### Step 2: Episode Detection

**`detect_episodes(df)`** (`episodes.py:52`) operates in 5 stages:

1. **Hourly violation scan** (lines 83-103): Each row checked against 6 thresholds. A single row can produce up to 2 raw episodes (one HR condition + Tachypnea).
2. **Sort + merge** (lines 109-131): Same-condition episodes sorted, consecutive ones merged if gap ≤ `episode_merge_gap_hours` (default: 1h).
3. **Coupling detection** (lines 133-143): Brady + Tachypnea temporal overlap → `cooccurrence=True`.
4. **Duration verification** (lines 145-148): Recomputes `duration_hours` from timestamps.
5. **Severity scoring** (lines 150-183): `_severity_score()` → `_severity_band()` (S0-S3).

### Step 3: Phase Detection

**`detect_phases(df, episodes)`** (`window_intelligence.py:17`):

1. Builds daily aggregates with episode burden scores.
2. **Single-condition-per-day classification** (lines 130-149): Each day gets ONE phase type via priority elif chain (Severe Brady > Brady > Very High HR > Tachy > Elevated HR > Tachypnea > normal).
3. Consecutive same-type days grouped into phases.
4. Three merge passes to consolidate adjacent phases.
5. **Phase capping** (lines 237-241): `max_phases = max(4, min(8, n // 10))`. Top phases by score kept.

### Step 4: Count Reconciliation

**`reconcile_counts(episodes, display_phases)`** (`narrative_ai.py:124`):

- Assigns each episode to a display_phase by checking if `episode.start_time` falls within `[phase_start, phase_end + 1 day)`.
- Returns two key counts:
  - `total_episodes`: `len(episodes)` — raw detection count
  - `display_episode_count`: episodes assigned to display phases (excludes episodes in "normal" phases)
- `reconciled` flag: whether `total == display`

### Step 5: Narrative Composition

**Opening sentence** (`narrative_ai.py:244`):
```python
f"{counts['display_episode_count']} episodic events spanning {counts['display_total_hours']} total hours detected: {types_str}."
```
Uses reconciled `display_episode_count`. BUT `types_str` is built from ALL episodes (not just displayed ones), so the opening can mention condition types that have no phase table row.

**Phase table** (`narrative_ai.py:249-297`): One row per display_phase. Only shows phases that survived the detect_phases → PHASE_LABELS filter pipeline.

### Step 6: PDF Assembly

**`generate_pdf()`** (`pdf_render.py:736`):

- **2-page layout** (standard): Page 1 = header + timeline + narrative + phase table + pattern observations + candlestick. Page 2 = histogram + activity + threshold legend.
- **3-page layout** (Full Period ≥ 90 days): Page 1 = header + timeline + narrative + phase table + "continue to page 2" hint. Page 2 = pattern observations + candlestick. Page 3 = histogram + activity + threshold legend.

### Key Data Flow Locations

#### Where `len(eps)` or `len(episodes)` is used for display:

| File:Line | Usage | Risk |
|-----------|-------|------|
| `narrative_ai.py:133` | `total_episodes = len(episodes)` in reconcile_counts | Internal — not displayed directly |
| `narrative_ai.py:244` | Opening uses `counts['display_episode_count']` | Safe — uses reconciled count |
| `pdf_render.py:563` | `total_episodes = counts['display_episode_count'] if counts else len(eps)` | Falls back to raw count if counts missing |
| `pdf_render.py:572` | Diagnostic mismatch check | Logging only |
| `narrative_ai.py:736` | Fallback in `build_specific_action_posture` | Uses raw count if counts unavailable |

#### Where day counts are computed:

| File:Line | Expression | Note |
|-----------|------------|------|
| `narrative_ai.py:24` | `(Timestamp(end) - Timestamp(start)).days + 1` | Canonical function |
| `pdf_render.py:335` | Same formula (duplicated) | Should call narrative_ai version |
| `episodes.py:202` | `max(1, (ts.max() - ts.min()).days + 1)` | In compute_rollups |
| `charts.py:656` | Same formula | Duplicated again |
| `window_intelligence.py:395` | `(data_end - data_start).days` | **Missing +1** — off-by-one |

#### Where triage is determined:

Single canonical source: `signal_engine.py:177` `compute_triage()`. Called once in `main.py:169`.

#### Where window_start/window_end are set:

| File:Line | How |
|-----------|-----|
| `main.py:123-124` | From windowed DataFrame min/max timestamp |
| `main.py:108-117` | For `smart_week`: from `find_most_interesting_week()` |
| `signal_engine.py:24-48` | `apply_window()` filters by range_type or custom dates |
| `window_intelligence.py:382-426` | `find_most_interesting_week()` slides a 7-day window |

---

## Section 3: Known Bug Investigation

### Bug A: Episode Count Explosion (JB)

**Diagnostic results:**

```
Total episodes: 748
Unique dates with episodes: 156
Total hours: 1574h

Condition distribution:
  Elevated HR:   360 episodes, 788h
  Bradycardia:   247 episodes, 510h
  Tachypnea:      63 episodes,  85h
  Tachycardia:    51 episodes,  65h
  Very High HR:   27 episodes, 126h

Top dates by episode count:
  2024-05-08: 10 episodes (Elevated HR: 7, Bradycardia: 2, Tachypnea: 1)
  2024-05-23:  9 episodes (Bradycardia: 1, Very High HR: 1, Elevated HR: 5, Tachycardia: 2)
  2024-07-05:  9 episodes (Elevated HR: 5, Tachycardia: 4)
```

**Root cause analysis:**

The episode count is genuinely high but not physically impossible. With 5 condition types and a 1-hour merge gap, a single day can legitimately produce 5-10 episodes:

1. **Multiple condition types per hour:** A row with `hr_avg=85` (> `elevated_hr_avg=80`) and `rr_avg=25` (> `tachy_rr_avg=24`) generates TWO raw episodes (Elevated HR + Tachypnea).
2. **1-hour merge gap too restrictive:** If Elevated HR occurs at hours 14:00, 15:00, then a gap at 16:00, then again at 17:00 — that produces TWO Elevated HR episodes (2h + 1h) instead of one 4h episode.
3. **Elevated HR threshold too low:** At `elevated_hr_avg=80`, any hour with avg HR > 80 bpm triggers an episode. For an elderly patient with a resting HR of ~78-82, minor fluctuations create hundreds of 1-2h fragments.

**The earlier "11 of 158 days" bug is a SEPARATE issue:** The pattern detection in `build_intelligent_key_findings` uses `eps` from `report.episodes` which flows through the API/batch pipeline. If the batch pipeline stores Episode objects as dicts with slightly different field names, the `start_time` accessor might return empty strings, causing the day-walking loop to find very few dates. This is a serialization/field-name mismatch, not a detection bug.

**Fix layer:** Detection layer (episodes.py) — increase merge gap and/or raise elevated_hr_avg threshold. Rendering layer — the >5 eps/day suppression already added in Round 8 is a reasonable safety net.

### Bug B: Activity Chart Collapse (Nancy)

**Root cause:** `generate_activity_trend_chart()` (`charts.py:816`) is missing an explicit `DateLocator`.

Line 860 sets:
```python
ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
```

But does NOT set `ax.xaxis.set_major_locator(...)`. Without a locator, matplotlib's `AutoDateLocator` picks degenerate tick positions for ~197 days, producing repeated "Jan 01" labels.

**Compare with candlestick chart** (lines 591-592) which correctly uses:
```python
ax_rr.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=12))
```

**Additional issue:** There are TWO definitions of `generate_activity_trend_chart_for_pdf`:
- Line 875: passes `dpi=settings.activity_dpi` (but the function doesn't accept `dpi` — would crash)
- Line 1059: no extra args (shadows the first definition)

Python silently uses the second definition, so the crash at line 875 never fires. But the duplicate is a landmine.

**Fix layer:** Charts layer (charts.py) — add `set_major_locator` with `minticks`/`maxticks`, add period-aware formatting for > 90 days. Delete the dead duplicate at line 875.

### Bug C: Empty Status Timeline Bar (Nancy)

**Root cause:** When `display_phases` is empty (all phases are "normal"), `build_status_timeline_segments()` (`pdf_render.py:338`) produces a single segment spanning all days with:
- `type='normal'`
- `color='#F0F0F0'` (near-white)
- `label=None`

The bar renders as a near-invisible light gray rectangle against the white page. The green "Within normal range" fallback (line 801) never triggers because it requires `elif not all_eps` — Nancy HAS episodes, so the episode timeline bar renders above, but the phase timeline is invisible gray.

**The logic gap:**

```
if all_eps:                           → renders episode timeline (red bars)
if pd.notna(ws_v) and pd.notna(we_v): → renders phase timeline (BUT invisible if display_phases empty)
elif not all_eps:                     → renders green "normal" bar (NEVER reached if episodes exist)
```

When a patient has episodes but zero display_phases, the phase timeline renders as invisible. It should either show the green "Within normal range" bar or be omitted entirely.

**Fix layer:** Rendering layer (pdf_render.py) — check if `display_phases` is empty before rendering the phase timeline; if empty, skip it or render the green bar instead.

### Bug D: Phase Table Missing Episodes (Nancy)

**Root cause:** Two interacting problems in the phase detection → display pipeline.

**Problem 1 — Single-condition-per-day classification** (`window_intelligence.py:130-149`):

Each day gets exactly ONE phase type via priority elif chain:
```
Severe Brady → Brady → Very High HR → Tachy → Elevated HR → Tachypnea → normal
```

If a day has BOTH Bradycardia and Tachypnea, it's classified as only `low_hr`. The Tachypnea episodes are silently dropped — they can never form their own "elevated_rr" phase if any day they appear on also has a higher-priority HR condition.

**Problem 2 — Phase capping includes normal phases** (`window_intelligence.py:237-241`):

```python
max_phases = max(4, min(8, n // 10))
```

The cap selects top phases by `phase_score`, but normal phases with many days can score higher than small clinical phases. After capping, `PHASE_LABELS` filter removes normal phases. So the cap can eliminate real clinical phases in favor of large normal phases that are subsequently hidden.

**Result:** Nancy's opening sentence mentions "Elevated Breathing, Elevated Heart Rate, Low Heart Rate" (from ALL episodes), but the phase table only has 1 row (Low Heart Rate) because the other condition types never survived the phase detection pipeline.

**Fix layer:** Detection layer (window_intelligence.py) — either allow multi-condition-per-day phases, or cap AFTER removing normal phases, not before.

### Bug E: Coverage String Missing Sensor Label (Nancy)

**Root cause:** `build_coverage_string()` (`narrative_ai.py:43-66`) only adds sensor labels when `len(rows) > 1` (multi-sensor patients). Single-sensor patients get bare `"Coverage: 3033/4700h (64.5%)"` with no "Bed:" prefix.

Same pattern in `main.py:332-342` — the pipeline coverage builder also omits the label for single-sensor patients.

Nancy is single-sensor (Bed only, from `934298-0013_Nancy_Bdrm`), so she hits the fallback branch.

**Fix layer:** Rendering layer (narrative_ai.py + main.py) — include the sensor label even for single-sensor patients when sensor type is known.

### Bug F: Trajectory Mixed Direction

**Diagnostic results (current code):**

```
JB CritWeek: +1 eps, -17h         → direction=mixed, magnitude=moderate  ✓
Both worsening moderately (9, 10)  → direction=mixed, magnitude=moderate  ✗ (see below)
Pure stable (0, 0)                 → direction=stable, magnitude=minimal  ✓
Both worsening significantly       → direction=worsening, magnitude=significant  ✓
Eps improving, hours worsening     → direction=mixed, magnitude=moderate  ✓
Both improving (-6, -12)           → direction=improving, magnitude=moderate  ✓
Eps worsening, hours improving     → direction=mixed, magnitude=moderate  ✓
```

**Remaining bug:** The case `(9, 10)` — both metrics worsening moderately — returns `mixed` instead of `worsening`. This is because `_metric_direction` uses strict `>` comparison: `10 > 10` is False, so `hr_dir='stable'`. Then one-stable-one-directional → `mixed`.

**Fix:** Change `>` to `>=` in `_metric_direction` for the worsening check (or reduce threshold by 1). The threshold comparison should be `delta > thresh` for worsening means `delta_hours=10` with `HOURS_THRESHOLD_MODERATE=10` barely misses. Using `>=` would include boundary values.

**Fix layer:** Detection layer (narrative_ai.py) — change strict `>` to `>=` in `_metric_direction`.

---

## Section 4: Data Shape Distribution

| Patient | Window (days) | Data Rows | Episodes | Total Hours | Unique Ep Days | Density (eps/day) | Conditions | Sensors |
|---------|---------------|-----------|----------|-------------|----------------|-------------------|------------|---------|
| EG | 52 | 1,585 | 46 | 55h | 31 | 1.5 | EHR, Tachy, Brady | Bed+Chair |
| **JB** | **158** | **5,667** | **748** | **1,574h** | **156** | **4.8** | **5 types** | **Bed+Chair** |
| Nancy | 196 | 3,033 | 32 | 39h | 25 | 1.3 | EHR, Tachy, Brady | Bed |
| PHolst | 65 | 1,432 | 90 | 130h | 45 | 2.0 | 5 types | Chair |
| RSanchez | 65 | 2,280 | 100 | 184h | 45 | 2.2 | EHR, Tachy, Brady, Tachy | Bed+Chair |
| S (Bed) | 164 | 997 | 16 | 18h | 15 | 1.1 | EHR, Tachy, Brady | Bed |
| S (Chair) | 172 | 3,525 | 23 | 84h | 14 | 1.6 | 4 types | Chair |
| SAllen | 42 | 676 | 24 | 26h | 13 | 1.8 | EHR, Tachy | Chair |
| TMiller | 101 | 1,815 | 66 | 110h | 43 | 1.5 | Tachy, Brady | Chair |
| Wimberley | 61 | 1,100 | 86 | 122h | 40 | 2.1 | EHR, Tachy | Chair |

### Key Data Shape Observations

- **Long periods (≥90 days):** JB (158), Nancy (196), S Bed (164), S Chair (172), TMiller (101) — 5 of 10 patients trigger 3-page layout
- **High episode density:** JB (4.8/day) is the outlier. All others are 1.1-2.2/day.
- **Low episode count:** Nancy (32), S Bed (16), S Chair (23) — these expose sparse-data rendering paths
- **Multi-sensor:** EG, JB, RSanchez — these need positional comparison rendering
- **Single sensor:** Nancy (Bed only), PHolst, S Bed, S Chair, SAllen, TMiller, Wimberley (all Chair)

---

## Section 5: Config Coverage Check

### Hardcoded Values That Should Be in Config

**charts.py — Severity tier thresholds (DUPLICATED):**
- Lines 183-186 AND 319-325: `40, 15, 5, 1` hours for critical/severe/moderate/mild. Duplicated within the same file, not sourced from config.

**charts.py — Font sizes and rendering constants:**
- Lines 485-486, 681-683: Font sizes `7, 10, 6, 8` hardcoded inline
- Lines 519-520: Line widths `3.5, 2.5` and alphas `0.8, 0.6` hardcoded
- Line 784: Bar width `0.35` hardcoded
- Line 886: DPI `150` hardcoded as default

**pdf_render.py — Pattern detection thresholds:**
- Lines 585, 597, 628, 668: Episode count thresholds `≥ 2, ≥ 3, ≥ 100, ≥ 4` hardcoded
- Lines 654, 660: Cluster ratio thresholds `≥ 0.7, < 0.15` hardcoded
- Lines 674, 232: Night definition `h >= 19 or h < 7` duplicated in two places
- Lines 462-469: Date axis intervals `14, 60, 180` days and `1, 7, 14, 30` intervals hardcoded

**pdf_render.py — 19 hardcoded hex colors:**
`#1E40AF, #1E3A5F, #6B7280, #F9FAFB, #D1D5DB, #FEF2F2, #991B1B, #FFFBEB, #92400E, #F0FDF4, #166534, #10B981, #F8F9FA, #DDDDDD, #888888, #666666` — none reference `ChartColors` or config.

**narrative_ai.py — 3 hardcoded colors:**
Lines 704, 707, 710: `#27864A, #D4850A` — duplicate config values instead of referencing `settings.color_episode_red` etc.

### Threshold Comparisons Outside Config

40+ threshold comparisons scattered across `charts.py` and `pdf_render.py` use inline numbers rather than config settings. The most dangerous duplications:
- Severity tiers (`40/15/5/1`) appear twice in `charts.py`
- Night definition (`h >= 19 or h < 7`) appears twice in `pdf_render.py`
- Pattern detection thresholds are all inline with no config backing

---

## Section 6: Test Coverage Gap

### Existing Tests

**One test file:** `backend/tests/test_robustness.py` (271 lines)

- Requires a **running server** on `localhost:8000` — not standalone unit tests
- Tests 5 scenarios: quality gate rejection, quality gate warning, determinism, threshold cascade, patient locations
- Uses raw `requests` HTTP calls, no mocking
- **Does not test:** episode detection, phase detection, chart rendering, PDF assembly, narrative generation, trajectory classification, count reconciliation

### Missing Infrastructure

- No `pytest.ini`, `tox.ini`, or CI configuration (no `.github/` directory)
- No unit tests for any module
- No edge case tests (empty data, single row, malformed timestamps)
- No config propagation tests

### Post-Generation Validation

`batch_generate.py` has **zero** post-generation validation beyond null-filtering failed results:
```python
valid_results = [r for r in summary_results if r]
```

No checks for:
- PDF integrity or page count
- Episode count sanity
- Cross-patient consistency
- Output file size anomalies
- Content verification (triage level rendered, no blank sections)

---

## Summary: Top 3 Systemic Bugs

### 1. Phase Detection Single-Condition-Per-Day Classification (HIGHEST PRIORITY)

**Location:** `window_intelligence.py:130-149`

**The bug:** Each day gets exactly ONE phase type. Multi-condition days drop lower-priority conditions entirely. Combined with phase capping (which includes then hides normal phases), this means condition types that never win the priority elif chain can NEVER appear in the phase table.

**Data shapes affected:** Any patient with multiple concurrent condition types — especially Nancy (3 types, 1 phase row), JB (5 types), PHolst (5 types), RSanchez (4 types). The more condition types a patient has, the more the phase table misrepresents their clinical picture.

**Impact:** The opening sentence lists all condition types, but the phase table only shows the dominant one. A cardiologist seeing "3 condition types detected" with only 1 table row will question the report's completeness.

**Fix layer:** Detection layer (window_intelligence.py). Options:
- Allow multiple phase types per day (split day into sub-phases)
- Build one phase track per condition type instead of one unified track
- At minimum: cap phases AFTER removing normal phases, not before

### 2. Activity Chart Missing DateLocator for Long Periods

**Location:** `charts.py:816-872`

**The bug:** `generate_activity_trend_chart()` sets a `DateFormatter` but no `DateLocator`. For periods > ~60 days, matplotlib's default locator produces degenerate x-axis labels (repeated "Jan 01" or severely overlapping text). The candlestick chart doesn't have this bug because it correctly sets `AutoDateLocator(minticks=4, maxticks=12)`.

**Additional hazard:** Duplicate `generate_activity_trend_chart_for_pdf` definitions at lines 875 and 1059. The first would crash if called (passes unsupported `dpi` kwarg). Python silently uses the second.

**Data shapes affected:** All patients with ≥60-day windows: JB (158d), Nancy (196d), S Bed (164d), S Chair (172d), TMiller (101d). 5 of 10 patients.

**Fix layer:** Charts layer (charts.py). Add `set_major_locator` call, add period-length-aware formatting, delete the dead duplicate function.

### 3. Empty Phase Timeline Bar When display_phases Is Empty

**Location:** `pdf_render.py:338-390` and `pdf_render.py:753-818`

**The bug:** When a patient has episodes but all their phases are "normal" (no display_phases), the status timeline bar renders as an invisible near-white (#F0F0F0) rectangle. The green "Within normal range" bar never triggers because it requires NO episodes to exist. The result is a blank section in the PDF that looks like a rendering error.

**Data shapes affected:** Patients with low episode density where episodes occur sporadically on mostly-normal days: Nancy (32 eps in 196 days, only 1 display phase), S Bed (16 eps in 164 days), SAllen (24 eps in 42 days). Any GREEN-triage patient with widely scattered episodes.

**Fix layer:** Rendering layer (pdf_render.py). Check if `display_phases` is empty; if so, either render the green "Within normal range" bar or skip the phase timeline entirely (the episode timeline bar above it already shows event positions).

---

### Bug Priority × Fix Complexity Matrix

| Bug | Priority | Fix Complexity | Fix Layer | Patients Affected |
|-----|----------|---------------|-----------|-------------------|
| Phase single-condition-per-day | **CRITICAL** | High | Detection (window_intelligence.py) | 6/10 |
| Activity chart DateLocator | **HIGH** | Low | Charts (charts.py) | 5/10 |
| Empty phase timeline bar | **HIGH** | Low | Rendering (pdf_render.py) | 3/10 |
| Coverage missing sensor label | Medium | Low | Rendering (narrative_ai.py + main.py) | 7/10 |
| Trajectory `>` vs `>=` threshold | Medium | Trivial | Detection (narrative_ai.py) | All |
| Episode merge gap too restrictive | Low | Low | Config (config.py) | 1/10 (JB) |
