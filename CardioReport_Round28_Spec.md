# CardioReport Round 28 Spec

**Driver:** Sajol review call, June 23, 2026 (Fathom 157673048, https://fathom.video/calls/717301150)
**Context:** Sajol was happy with the current report ("much better aligned, simpler, coming out good"). This round adds the nurse triage layer he is building toward: the 24 hour view becomes the daily decision tool. Four items.

**Through line (his framing):** "the more important thing for the nurse is the last 24 hours, and the more important thing for the cardiologist is the last 30 days." Two audiences, two windows. The 24h view must let a nurse triage at a glance; the 30 day view stays the cardiologist's trend tool.

---

## Item 1: 24 hour snapshot gets episodic events + a one line summary

Currently the "Last 24 Hours" block shows only HR/RR avg/min/max. Sajol: "although this is average, min, and max, we want a column that says episodic events ... otherwise I have to say it had a max of 109, that has to be an episodic event. I should be able to qualify that: short window, long window, how many repetitions."

### Add to the 24h block

1. **Episodic events within the last 24h window**, rendered in the SAME style as the 30 day episodic events table (Sajol: "just make it very similar to that"). Columns mirror the unified table: Time Span, Duration, Episodes, Condition, Avg, Min, Max, Comment (Date is implied since it is the last 24h, can be dropped or shown as the clock window). Bold linking same as the main table (condition + triggering metric bold).
2. **One line plain language summary** of the 24h window. Sajol confirmed: "the episode and probably a one line of summary." Plain language, e.g. "Last 24 hours: one low heart rate episode overnight, otherwise within normal range." No jargon.

### Rule (data shape agnostic)

- The 24h window is the last 24 hours from the data's final timestamp (already implemented for the snapshot stats; reuse the same window).
- Episode detection over the 24h window uses the SAME episode detection engine as the 30 day view, just scoped to the 24h window. No separate detection logic.
- If there are no episodes in the last 24h, the summary says so plainly (e.g. "Last 24 hours: no episodic events, vitals within normal range") and the episodic table shows empty or a "no events" note. Do not fabricate events.

---

## Item 2: 24 hour status banner (color coded)

A prominent color coded banner classifying the LAST 24 HOURS specifically, separate from the 30 day triage tier.

### The key distinction (Sajol, verbatim intent)

"Even though the rating above it is elevated, over the last 24 hours it is normal, or it is elevated if there was elevated data." The 24h banner is a SEPARATE classification of just the recent window. A patient can be YELLOW over 30 days but GREEN in the last 24h, or vice versa. This is the whole point: the nurse triages on recent status.

### Classification rule (Heet confirmed)

Use the SAME severity logic as the 30 day triage tier, computed over the 24h window only. One classification engine, two windows:

```
status_24h = classify_severity(episodes_in_last_24h, vitals_in_last_24h)
  # same function that produces the 30 day GREEN/YELLOW/RED tier
  # scoped to the 24h window instead of the full period
```

- GREEN: no concerning episodes in last 24h
- ORANGE/YELLOW: elevated activity in last 24h
- RED: high severity in last 24h

Use the same color vocabulary as the existing tier (GREEN/YELLOW/RED) so the banner and the tier are visually consistent even when they disagree on classification. (Sajol used "orange" loosely; map to the existing YELLOW unless he asks for a distinct orange.)

### Placement and form

A prominent banner at the top of the 24h section (or top of the report), color filled, stating the 24h status. Sajol wants it scannable: "this is red, this is orange, this is green, I will skip that." Built so the banner color can later feed the daily summary email (future, post cloud) where the nurse scans a list of patients by color.

### Why separate from the tier

The report header already shows the 30 day tier (e.g. "GREEN: Routine Review"). The 24h banner is ADDITIONAL and may differ. Label them so they do not read as a contradiction: the header tier is the 30 day classification, the banner is the 24h classification. Make the window explicit on each ("30 day: GREEN" vs "Last 24h: GREEN") so a nurse is never confused about which window a color refers to.

---

## Item 3: Patient library, client scoped (data separation)

Sajol: "I can't share one patient's data with somebody else by mistake." A real privacy requirement, not cosmetic.

### Structure (Heet confirmed: both physical + UI)

**Physical:** data organized by client on disk.
```
Code/data/
  medhab/      <- MedHab patients
  pam_health/  <- PAM Health patients
```
Each client is a separate folder. A patient belongs to exactly one client. The pipeline loads a patient only from its client folder. No code path mixes clients.

**UI:** the app gets a Client/Library selector ABOVE the patient dropdown:
```
Library: [ MedHab | PAM Health ]   ->   Patient: [ ...patients in that library... ]
```
Selecting a library filters the patient dropdown to only that client's patients. Switching library swaps the patient list. A patient from one library can never appear under another.

### Rules (data shape agnostic, privacy preserving)

- The library is discovered from the folder structure (each subfolder under data/ is a client), not hardcoded. Adding a third client later is a new folder, no code change.
- The pipeline NEVER loads a patient from outside the selected client's folder.
- `/api/patients` becomes client scoped: `/api/clients` lists libraries, `/api/patients?client=medhab` lists that client's patients.
- No patient identifier or data crosses client boundaries. Add an invariant: a patient resolved under client A is never loadable under client B.

This directly fixes the "hardwired to the next set of patients" problem Sajol named.

---

## Item 4: PAM Health rerun, all three views, one zip

Sajol: rerun PAM Health with the new design. Stick with 30 day for now but send all three (24h, 30 day, 90 day) since 90 day will be needed later. Deliver as a single zip.

### Action

- Move PAM Health data into `Code/data/pam_health/` (the new library structure).
- Regenerate all PAM Health patients with the current R28 design (unified table, 24h snapshot + episodic events + summary + status banner, bold linking, tier consistent grade) across three views: 24h, 30Day, 90Day.
- Package as one zip for Sajol.
- This also clears the standing PAM regeneration debt (PAM Reports/ on disk has been pre R26).

### Note

PAM Health uses the wide device Excel schema, MedHab uses the clean CSV. The CSV/Excel ingest dispatch (added for MedHab) already handles both. Confirm PAM patients still ingest correctly through the library structure.

---

## Invariant tests (current suite 147)

- **R28_001** 24h block contains an episodic events sub table when events exist in the last 24h, empty/no-events note otherwise (no fabricated events)
- **R28_002** 24h episodic detection uses the same engine as the 30 day detection, scoped to the 24h window (source inspection)
- **R28_003** 24h one line summary present, plain language, reflects the actual 24h episodes
- **R28_004** 24h status banner present, classified by the same severity logic as the tier scoped to 24h
- **R28_005** 24h banner and 30 day tier each label their window explicitly (no ambiguous color)
- **R28_006** Library is folder discovered, not hardcoded; adding a folder adds a client (source inspection)
- **R28_007** A patient under client A is not loadable under client B (privacy boundary)
- **R28_008** `/api/patients?client=X` returns only client X patients
- **R28_009** PAM Health patients ingest and render through the library structure across 24h/30Day/90Day

---

## Do not

- Do not build a separate severity/threshold scheme for the 24h banner. Reuse the tier logic over the 24h window. One engine.
- Do not let the 24h banner and 30 day tier read as a contradiction. Label each with its window.
- Do not fabricate 24h episodes or a 24h summary when the window is quiet. Say "no events" plainly.
- Do not allow any code path to load a patient from outside its client folder. This is a privacy requirement.
- Do not hardcode the client list. Discover from folders.
- Do not touch the phase strip, the main 30 day unified table, or the actions block (all approved). This round ADDS the 24h triage layer and the library; it does not redesign what is done.

---

## Deliverables

1. 24h snapshot with episodic events + one line summary + color status banner (MedHab cohort regenerated to show it).
2. Patient library: physical folder separation (medhab/, pam_health/) + UI library selector in the app.
3. PAM Health rerun, 24h + 30Day + 90Day, delivered as one zip.
4. Invariants 147 -> 156 passing.
5. Live app: library selector working, 24h triage layer visible, same engine as PDFs.

---

## Open items carried forward (for Sajol, decisions not bugs)

- Priority grade vocabulary (Low/Medium/High vs P1/P2/P3) — one line config, his preference
- "Stable baseline" headline on nocturnal bradycardia patients — duration weighting / headline logic, clinical judgment call, raise verbally
- Per finding vs per episode table grouping — confirm he is happy with current per finding read
- Patient ID convention (Device names vs study IDs) — before external sharing
- Daily summary email using banner colors — future, post cloud integration with Sean
