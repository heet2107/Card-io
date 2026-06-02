# Technical Reference: Patient Vital Data & Clinical Calculations

This reference document details the specific columns extracted from the patient vital sign Excel sheets and the step-by-step mathematical and clinical logic used to process and analyze the data.

---

## 1. Excel Column Mapping

When a patient's vitals file is ingested, the system dynamically scans the column headers (case-insensitively, ignoring leading/trailing spaces) and maps them to the following canonical variables:

| Original Column Header | Internal Mapping | Description |
| :--- | :--- | :--- |
| **`Date`** / **`date`** | `Date` / `timestamp` | Naive date/time stamp representing the start of the hourly recording window. |
| **`avg_hr`** / **`Avg_HR`** | `avg_hr` | Average Heart Rate (bpm) computed over the hour. |
| **`min_hr`** / **`Min_HR`** | `min_hr` | Minimum Heart Rate (bpm) detected during the hour. |
| **`max_hr`** / **`Max_HR`** | `max_hr` | Maximum Heart Rate (bpm) detected during the hour. |
| **`avg_rr`** / **`Avg_RR`** | `avg_rr` | Average Respiratory/Breathing Rate (brpm) computed over the hour. |
| **`min_rr`** / **`Min_RR`** | `min_rr` | Minimum Respiratory Rate (brpm) detected during the hour. |
| **`max_rr`** / **`Max_RR`** | `max_rr` | Maximum Respiratory Rate (brpm) detected during the hour. |
| **`cnt`** / **`CNT`** | `cnt` | Number of valid 1-minute samples recorded during the hour (max 60). Used to assess data confidence. |

---

## 2. Step-by-Step Clinical Calculations

The signal processing engine performs analysis in a cascade of sequential stages:

- **Stage 1: Hourly Violation Detection**: Each hour is evaluated against physiological thresholds.
- **Stage 2 & 3: Grouping & Merging**: Adjacent violating hours are grouped, and separate runs are merged if gap is $\le 1$ hour.
- **Stage 4: Coupling**: Co-occurring HR and RR abnormalities are flagged if they overlap $\ge 2$ hours.
- **Stage 5: Severity Scoring & Bands**: Burden score is calculated by: `Base Weight + Duration Bonus + Coupling Bonus - Low Confidence Penalty`.
- **Stage 6: Triage & Action Posture**: RED, YELLOW, or GREEN category is classified, which dictates the recommended action posture.
- **Stage 7: Trend Assessment**: STABLE, INTERMITTENT, or PROGRESSIVE classification.

### Stage 1: Hourly Violation Detection
*   **Heart Rate Tiers**:
    *   **Severe Bradycardia**: `avg_hr < 40 bpm`
    *   **Bradycardia (Low HR)**: `avg_hr < 45 bpm`
    *   **Elevated Heart Rate**: `avg_hr > 95 bpm`
    *   **Tachycardia (High HR)**: `avg_hr > 100 bpm`
    *   **Very High Heart Rate**: `avg_hr > 110 bpm`
*   **Respiratory Rate Tiers**:
    *   **Elevated Breathing (Tachypnea)**: `avg_rr > 24 brpm`
    *   **High Breathing**: `avg_rr > 30 brpm`
    *   **Very High Breathing**: `avg_rr > 40 brpm`

---

### Stage 2 & 3: Episode Grouping & Merging
*   **Merging**: Separate episodes of the same condition type are merged if the gap between them is $\le 1$ hour.
*   **Duration**: Calculated as:
    $$\text{Duration (hours)} = \frac{\text{End Time} - \text{Start Time}}{\text{3600 seconds}} + 1\text{ hour}$$

---

### Stage 4: Clinical Coupling (Co-occurrence)
*   Flagged as **Coupled / Co-occurring** if overlap duration is $\ge 2$ hours.

---

### Stage 5: Severity Scoring & Bands
#### 1. Severity Score Formula:
$$\text{Score} = \text{Base Weight} + (\text{Duration} - 1) \times \text{Duration Bonus} + \text{Coupling Bonus} - \text{Low Confidence Penalty}$$

*   **Base Weights**:
    *   Very Low HR / Very High HR / Very High RR = **5**
    *   Low HR / High HR / High RR = **3**
    *   Elevated HR / Elevated RR = **2**
*   **Duration Bonus**: $+1$ point per hour beyond the first.
*   **Coupling Bonus**: $+2$ points if coupled.
*   **Low Confidence Penalty**: $-1$ point if any hour contains `cnt < 30` or `gap_flag == 1` (capped at minimum score of 1).

#### 2. Severity Bands:
*   **S3 (Critical)**: Score $\ge 13$
*   **S2 (High)**: Score $\ge 9$
*   **S1 (Moderate)**: Score $\ge 5$
*   **S0 (Mild)**: Score $< 5$

---

### Stage 6: Triage & Action Posture Classification
#### 1. Triage Band:
*   **RED (Provider Review Recommended)**:
    *   **Critical Single-Value Override**: Any single hour where `avg_hr < 38 bpm`, `avg_hr > 120 bpm`, or `avg_rr > 32 brpm`.
    *   Any Severe Bradycardia episode lasting $\ge 4$ hours.
    *   Any Tachypnea (Elevated Breathing) episode lasting $\ge 8$ hours.
    *   Any coupled episode with a severity score $\ge 9$.
*   **YELLOW (Closer Observation Suggested)**:
    *   Maximum severity score of any detected episode $\ge 5$.
*   **GREEN (Routine Review)**:
    *   All other cases.

#### 2. Action Posture:
*   **RED** $\to$ *Urgent provider review advised (per protocol)*
*   **YELLOW** $\to$ *Closer clinical observation is suggested*
*   **GREEN** $\to$ *Routine review*

---

### Stage 7: Trend Assessment
*   **PROGRESSIVE**: Maximum severity score $\ge 9$ OR (coupled episodes are present and total abnormal hours > 10).
*   **INTERMITTENT**: Maximum severity score $\ge 5$ OR total abnormal hours > 5.
*   **STABLE**: All other cases.
*   **Late-vs-Early Ratio**: Computes:
    $$\text{Ratio} = \frac{\text{Episodes in Late } 25\%}{\max(\text{Episodes in Early } 25\%, 0.5)}$$

---

## 3. Location & Activity Comparisons

*   **Positional Stats**: Groups data by `location` (excluding `Bed` and `Unknown`). Computes average HR and RR per location, and reports the difference in RR:
    $$\text{RR Diff} = \text{Avg RR (Living Room)} - \text{Avg RR (Chair)}$$
*   **Daily Activity**: Computes active hours (all hours where `location != 'Bed'`). Each day is classified as:
    *   **Green**: Active hours $\ge 20$ hours
    *   **Amber**: Active hours $\ge 12$ hours
    *   **Red**: Active hours $< 12$ hours
