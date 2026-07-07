/**
 * CardioReport – Frontend Application Logic
 * Handles API calls, rendering, and interactions.
 * All display labels match the PDF output exactly.
 */

const API_BASE = "http://localhost:8000";

// ── Clinical-friendly condition names (must match pdf_render.py) ────────────
const CONDITION_DISPLAY = {
    "Severe Bradycardia": "Very Low Heart Rate",
    "Bradycardia": "Low Heart Rate",
    "Tachycardia": "High Heart Rate",
    "Tachypnea": "Elevated Breathing Rate",
};

// ── Stats row labels (must match pdf_render.py) ────────────────────────────
const STATS_LABELS = {
    "Avg HR (bpm)":  "Heart Rate Avg (bpm)",
    "Min HR (bpm)":  "Heart Rate Min (bpm)",
    "Max HR (bpm)":  "Heart Rate Max (bpm)",
    "Avg RR (brpm)": "Breathing Rate Avg (breaths/min)",
    "Min RR (brpm)": "Breathing Rate Min (breaths/min)",
    "Max RR (brpm)": "Breathing Rate Max (breaths/min)",
};

// ── DOM Elements ────────────────────────────────────────────────────────────

const $clientSelect  = document.getElementById("client-select");
const $patientSelect = document.getElementById("patient-select");
const $rangeSelect   = document.getElementById("range-select");
const $startDate     = document.getElementById("start-date");
const $endDate       = document.getElementById("end-date");
const $customRange   = document.getElementById("custom-range");
const $customRangeEnd = document.getElementById("custom-range-end");
const $btnGenerate   = document.getElementById("btn-generate");
const $btnSmartWeek  = document.getElementById("btn-smart-week");
const $btnSummary    = document.getElementById("btn-summary");
const $btnSummaryDownload = document.getElementById("btn-summary-download");
const $btnDownload   = document.getElementById("btn-download");
const $btnExportJson = document.getElementById("btn-export-json");
const $loading       = document.getElementById("loading-overlay");
const $reportContainer = document.getElementById("report-container");
const $summaryContainer = document.getElementById("summary-container");
const $emptyState    = document.getElementById("empty-state");
const $aiToggle      = document.getElementById("ai-toggle");

// Status chip colors for the 24h library summary (match the PDF).
const SUMMARY_STATUS_FILL = {
    "RED": "#DC2626", "YELLOW": "#D97706", "ELEVATED": "#D97706",
    "GREEN": "#16A34A", "NORMAL": "#16A34A",
    "LOW DATA": "#6B7280", "NO DATA": "#6B7280",
};

// ── State ───────────────────────────────────────────────────────────────────

let currentReport = null;
let currentPdfUrl = null;  // object URL for the inline PDF preview (revoked on regen)
let patientMeta = {};  // Cache: { patientId: { locations, date_range, total_hours } }
let currentClient = null;  // selected library id, e.g. "medhab" / "pam_health"

// The default rolling-range options (used for Excel libraries that report over
// ranges rather than months). Captured once so loadMonths() can restore them
// when switching to a range-based library.
const DEFAULT_RANGE_OPTIONS_HTML = $rangeSelect ? $rangeSelect.innerHTML : "";

// ── Init ────────────────────────────────────────────────────────────────────

async function init() {
    try {
        // Discover libraries (clients), then load the first one's patients.
        const cres = await fetch(`${API_BASE}/api/clients`);
        if (!cres.ok) throw new Error("Failed to load libraries");
        const cdata = await cres.json();
        const clients = cdata.clients || [];

        $clientSelect.innerHTML = "";
        if (clients.length === 0) {
            $clientSelect.innerHTML = '<option value="">No libraries</option>';
        } else {
            clients.forEach((c) => {
                const opt = document.createElement("option");
                opt.value = c.id;
                opt.textContent = c.label;
                $clientSelect.appendChild(opt);
            });
            currentClient = clients[0].id;
        }
        $clientSelect.addEventListener("change", onClientChange);

        $patientSelect.addEventListener("change", onPatientChange);
        await loadPatients();
    } catch (err) {
        console.error("Init error:", err);
        $patientSelect.innerHTML = '<option value="">Error loading patients</option>';
    }
}

/**
 * Load the patient list for the currently-selected library and repopulate the
 * patient dropdown. A patient from one library never appears under another —
 * the list is scoped server-side by ?client=.
 */
async function loadPatients() {
    // Show an explicit loading state so the dropdown never appears frozen on a
    // cold/slow load (e.g. the first PAM Excel scan, or a future larger
    // cohort). Disable the controls until the scoped list is in, so a patient
    // from the previous library can't be picked mid-swap.
    $patientSelect.innerHTML = '<option value="">Loading…</option>';
    $patientSelect.disabled = true;
    $clientSelect.disabled = true;
    $btnGenerate.disabled = true;
    $btnSmartWeek.disabled = true;
    try {
        const url = currentClient
            ? `${API_BASE}/api/patients?client=${encodeURIComponent(currentClient)}`
            : `${API_BASE}/api/patients`;
        const res = await fetch(url);
        if (!res.ok) throw new Error("Failed to load patients");
        const data = await res.json();
        if (data.client) currentClient = data.client;

        patientMeta = {};  // metadata cache is per-library
        $patientSelect.innerHTML = "";
        if (!data.patients || data.patients.length === 0) {
            $patientSelect.innerHTML = '<option value="">No patients found</option>';
            return;
        }

        data.patients.forEach((pid) => {
            const opt = document.createElement("option");
            opt.value = pid;
            opt.textContent = pid;
            $patientSelect.appendChild(opt);
        });

        $btnGenerate.disabled = false;
        $btnSmartWeek.disabled = false;
        if ($btnSummary) $btnSummary.disabled = false;  // library loaded → summary available

        // Populate the Time Window dropdown for this library (data-driven).
        await loadMonths();

        // Auto-load metadata for the first patient
        await onPatientChange();
    } catch (err) {
        console.error("loadPatients error:", err);
        $patientSelect.innerHTML = '<option value="">Error loading patients</option>';
    } finally {
        // Re-enable the controls regardless of outcome.
        $patientSelect.disabled = false;
        $clientSelect.disabled = false;
    }
}

/** Switch library: reload the scoped patient list (and its windows). */
async function onClientChange() {
    currentClient = $clientSelect.value || null;
    await loadPatients();
}

/**
 * Load available report months for the current library and populate the Time
 * Window dropdown. CSV-shape libraries (MedHab) report by month; Excel-shape
 * libraries (PAM Health) have no months, so we restore the rolling-range
 * options instead.
 */
async function loadMonths() {
    try {
        const url = currentClient
            ? `${API_BASE}/api/months?client=${encodeURIComponent(currentClient)}`
            : `${API_BASE}/api/months`;
        const res = await fetch(url);
        const data = res.ok ? await res.json() : { months: [] };
        if (!data.months || data.months.length === 0) {
            // Range-based library — restore the default rolling-window options.
            $rangeSelect.innerHTML = DEFAULT_RANGE_OPTIONS_HTML;
            return;
        }
        $rangeSelect.innerHTML = "";
        data.months.forEach((m) => {
            const opt = document.createElement("option");
            opt.value = m.key;          // e.g. "2026-04"
            opt.textContent = m.label;  // e.g. "April 2026"
            $rangeSelect.appendChild(opt);
        });
        // Months are concrete windows — the custom-range inputs are not used here.
        if ($customRange) $customRange.style.display = "none";
        if ($customRangeEnd) $customRangeEnd.style.display = "none";
    } catch (err) {
        console.error("loadMonths error:", err);
    }
}

/** True when a dropdown value is a month key like "2026-04". */
function isMonthKey(v) {
    return /^\d{4}-\d{2}$/.test(v);
}

/**
 * When the user selects a different patient, fetch their location metadata
 * and update the UI accordingly (location badge, date range hints).
 */
async function onPatientChange() {
    const pid = $patientSelect.value;
    if (!pid) return;

    // Remove previous location info
    const existing = document.getElementById("location-info");
    if (existing) existing.remove();

    // Fetch metadata (cached)
    if (!patientMeta[pid]) {
        try {
            const locUrl = `${API_BASE}/api/patients/${encodeURIComponent(pid)}/locations`
                + (currentClient ? `?client=${encodeURIComponent(currentClient)}` : "");
            const res = await fetch(locUrl);
            if (res.ok) {
                patientMeta[pid] = await res.json();
            } else {
                patientMeta[pid] = { locations: [], date_range: {} };
            }
        } catch {
            patientMeta[pid] = { locations: [], date_range: {} };
        }
    }

    const meta = patientMeta[pid];

    // Show location info banner below the controls
    const panel = document.querySelector(".controls-inner");
    if (panel && meta.locations.length > 0) {
        const info = document.createElement("div");
        info.id = "location-info";
        info.className = "location-info";

        const locBadges = meta.locations.map(loc => {
            const icon = loc === "Chair" ? "🪑" : loc === "Bed" ? "🛏️" : loc === "Living Room" ? "🏠" : "📍";
            return `<span class="loc-badge">${icon} ${loc}</span>`;
        }).join("");

        const dateHint = meta.date_range?.start && meta.date_range?.end
            ? `<span class="date-hint">Data: ${meta.date_range.start} to ${meta.date_range.end} (${meta.total_hours}h)</span>`
            : "";

        info.innerHTML = `<span class="loc-label">Available sensors:</span> ${locBadges} ${dateHint}`;
        panel.appendChild(info);
    }

    // Pre-populate custom date inputs with patient's actual date range
    if (meta.date_range?.start) {
        $startDate.value = meta.date_range.start;
        $startDate.min = meta.date_range.start;
        $startDate.max = meta.date_range.end;
    }
    if (meta.date_range?.end) {
        $endDate.value = meta.date_range.end;
        $endDate.min = meta.date_range.start;
        $endDate.max = meta.date_range.end;
    }
}

// ── Event Listeners ─────────────────────────────────────────────────────────

$rangeSelect.addEventListener("change", () => {
    const isCustom = $rangeSelect.value === "custom";
    $customRange.style.display = isCustom ? "flex" : "none";
    $customRangeEnd.style.display = isCustom ? "flex" : "none";
});

$btnGenerate.addEventListener("click", generateReport);
if ($btnSummary) $btnSummary.addEventListener("click", showLibrarySummary);
if ($btnSummaryDownload) $btnSummaryDownload.addEventListener("click", downloadLibrarySummary);
$btnSmartWeek.addEventListener("click", smartWeekDetect);
$btnDownload.addEventListener("click", downloadPDF);
$btnExportJson.addEventListener("click", exportJSON);

// ── Most Critical Week ──────────────────────────────────────────────────────

async function smartWeekDetect() {
    const pid = $patientSelect.value;
    if (!pid) return;

    // Show loading state on the button
    const origText = $btnSmartWeek.innerHTML;
    $btnSmartWeek.innerHTML = `<span class="spinner-inline"></span> Scanning…`;
    $btnSmartWeek.disabled = true;

    try {
        const res = await fetch(`${API_BASE}/api/patients/${encodeURIComponent(pid)}/interesting-week`);
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            alert(err.detail || "Could not find a critical week for this patient.");
            return;
        }

        const data = await res.json();

        // Switch to custom range and fill in the discovered dates
        $rangeSelect.value = "custom";
        $customRange.style.display = "flex";
        $customRangeEnd.style.display = "flex";
        $startDate.value = data.start;
        $endDate.value = data.end;

        // Show a score toast in the location-info area
        const existing = document.getElementById("smart-week-toast");
        if (existing) existing.remove();
        const toast = document.createElement("div");
        toast.id = "smart-week-toast";
        toast.className = "smart-week-toast";
        toast.innerHTML = `🔍 <strong>Most Critical Week detected:</strong> ${data.start} → ${data.end} (clinical score: ${data.score})`;
        document.querySelector(".controls-inner")?.appendChild(toast);

        // Auto-generate the report for the discovered window
        await generateReport();
    } catch (err) {
        console.error("Smart week error:", err);
        alert("Failed to scan for the most critical week.");
    } finally {
        $btnSmartWeek.innerHTML = origText;
        $btnSmartWeek.disabled = false;
    }
}

// ── Generate Report ─────────────────────────────────────────────────────────

// ── 24h triage layer (banner + summary + episodic sub-table) ────────────────
// Mirrors the PDF block: same engine, same bold-linking, same plain-language
// summary. The 24h banner is a SEPARATE classification from the 30-day tier;
// each window is labeled so the two never read as a contradiction.

const BANNER_24H = {
    "Red":    { fill: "#DC2626", word: "RED" },
    "Yellow": { fill: "#D97706", word: "ELEVATED" },
    "Green":  { fill: "#16A34A", word: "NORMAL" },
};

function renderSnapshotBanner(snap) {
    const el = document.getElementById("snapshot-banner");
    if (!el) return;
    const status = snap.status_24h;
    if (!status || !BANNER_24H[status]) { el.style.display = "none"; return; }
    // The Last-24h status renders as a phase-strip-style bar of colored
    // condition blocks (same look as the 30-day Patient clinical status strip,
    // scoped to 24h), each showing the condition and its clock time span (a
    // timing, not a date). A quiet window shows a single green
    // "within normal range" block.
    const events = snap.events || [];
    el.className = "phases-container snapshot-24h-strip";
    el.style.background = "";
    if (events.length === 0) {
        el.innerHTML = `<div class="phase-block normal" style="flex:1">`
            + `<span class="phase-label">Last 24 Hours: within normal range</span></div>`;
        el.style.display = "flex";
        return;
    }
    el.innerHTML = events.slice(0, 6).map((e) => {
        const type = e.phase_type || e.brief_phase_type || "";
        const label = String(e.category || "").replace(" (brief)", "").replace("Heart Rate", "HR");
        const span = e.time_span || "";
        return `<div class="phase-block ${type}" style="flex:1">`
            + `<span class="phase-label">${escapeHtml(label)}</span>`
            + `<span class="phase-dates">${escapeHtml(span)}</span></div>`;
    }).join("");
    el.style.display = "flex";
}

function renderSnapshotSummary(snap) {
    const el = document.getElementById("snapshot-summary");
    if (!el) return;
    if (snap.summary) {
        el.textContent = snap.summary;
        el.style.display = "block";
    } else {
        el.style.display = "none";
    }
}

function renderSnapshotEvents(snap) {
    const head = document.getElementById("snapshot-events-head");
    const wrap = document.getElementById("snapshot-events-wrap");
    const none = document.getElementById("snapshot-events-none");
    const tbody = document.getElementById("snapshot-events-tbody");
    const rows = (snap.events || []).slice(0, 6);
    head.style.display = "block";
    tbody.innerHTML = "";
    if (rows.length === 0) {
        wrap.style.display = "none";
        none.style.display = "block";
        return;
    }
    none.style.display = "none";
    wrap.style.display = "block";
    const bare = (s) => String(s == null ? "" : s).replace(" bpm", "").replace(" brpm", "").trim();
    rows.forEach((row) => {
        const cond = String(row.category || "").replace(" (brief)", "");
        const isLow = String(row.phase_type || row.brief_phase_type || "").includes("low");
        const minCell = bare(row.min), maxCell = bare(row.max);
        const th = (row.total_hours != null ? row.total_hours : (row.sustained_hours != null ? row.sustained_hours : 0));
        const tr = document.createElement("tr");
        // No Date column — the window is the last 24h, so Time Span carries the timing.
        tr.innerHTML = `
            <td>${escapeHtml(row.time_span || "")}</td>
            <td>${th}h</td>
            <td>${row.episodes != null ? row.episodes : ""}</td>
            <td><strong>${escapeHtml(cond)}</strong></td>
            <td>${escapeHtml(bare(row.average))}</td>
            <td>${isLow ? "<strong>" + escapeHtml(minCell) + "</strong>" : escapeHtml(minCell)}</td>
            <td>${isLow ? escapeHtml(maxCell) : "<strong>" + escapeHtml(maxCell) + "</strong>"}</td>
            <td>${escapeHtml(row.comment || "")}</td>
        `;
        tbody.appendChild(tr);
    });
}

// ── 24h Library Summary (all patients in the library, critical first) ───────
let summaryClient = null;  // the library the on-screen summary was built for

async function showLibrarySummary() {
    if (!currentClient) return;
    showLoading(true);
    try {
        const res = await fetch(`${API_BASE}/api/summary?client=${encodeURIComponent(currentClient)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const s = await res.json();
        summaryClient = s.client || currentClient;

        document.getElementById("summary-title").textContent = `${s.label}: 24 Hour Patient Summary`;
        const now = new Date();
        document.getElementById("summary-gen").textContent =
            "Generated " + now.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });

        const bare = (ch) => (!ch || ch.avg == null) ? "—"
            : `${Math.round(ch.avg)}/${Math.round(ch.min)}/${Math.round(ch.max)}`;
        const tbody = document.getElementById("summary-tbody");
        tbody.innerHTML = "";
        (s.patients || []).forEach((r) => {
            const label = r.display_label || String(r.status || "").toUpperCase();
            const fill = SUMMARY_STATUS_FILL[label] || "#6B7280";
            const ev = r.event_count
                ? `${r.event_count} · ${(r.conditions || []).join(", ")}`
                : "None";
            let summ = escapeHtml(r.summary || "");
            if (r.note) summ += `<br><span class="summary-note">${escapeHtml(r.note)}</span>`;
            const vit = (r.hr == null && r.rr == null) ? "—"
                : `HR ${bare(r.hr)}<br>RR ${bare(r.rr)}`;
            const tr = document.createElement("tr");
            tr.innerHTML =
                `<td><span class="status-chip" style="background:${fill}">${escapeHtml(label)}</span></td>`
                + `<td><strong>${escapeHtml(String(r.patient || ""))}</strong></td>`
                + `<td>${escapeHtml(ev)}</td>`
                + `<td>${summ}</td>`
                + `<td>${vit}</td>`;
            tbody.appendChild(tr);
        });

        // Show the summary view, hide the per-patient report + empty state.
        $reportContainer.style.display = "none";
        $emptyState.style.display = "none";
        $summaryContainer.style.display = "block";
        window.scrollTo(0, 0);
    } catch (err) {
        alert("Error loading library summary: " + err.message);
        console.error(err);
    } finally {
        showLoading(false);
    }
}

async function downloadLibrarySummary() {
    const client = summaryClient || currentClient;
    if (!client) return;
    try {
        const res = await fetch(`${API_BASE}/api/summary/pdf?client=${encodeURIComponent(client)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        let fname = `CardioReport_${client}_24h_Summary.pdf`;
        const cd = res.headers.get("Content-Disposition") || "";
        const m = cd.match(/filename="?([^"]+)"?/);
        if (m) fname = m[1];
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = fname;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
    } catch (err) {
        alert("Error downloading summary PDF: " + err.message);
        console.error(err);
    }
}

async function generateReport() {
    const patientId = $patientSelect.value;
    const rangeType = $rangeSelect.value;
    const useAI     = $aiToggle.checked;
    if (!patientId) return;
    // Leaving the summary view — show the per-patient report instead.
    if ($summaryContainer) $summaryContainer.style.display = "none";

    const body = {
        patient_id: patientId,
        range_type: rangeType,
        client: currentClient,
        use_ai: useAI
    };
    if (isMonthKey(rangeType)) {
        body.range_type = "month";
        body.month = rangeType;
    } else if (rangeType === "custom") {
        body.start = $startDate.value || null;
        body.end = $endDate.value || null;
    }

    showLoading(true);

    try {
        // Render the report NATIVELY in the app (HTML, with hover) — not an
        // embedded PDF. Same report JSON drives the in-app view; Download PDF
        // still produces the full PDF.
        const res = await fetch(`${API_BASE}/api/report/preview`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }

        currentReport = await res.json();
        renderReport(currentReport);

        $btnDownload.disabled = false;
        $btnExportJson.disabled = false;
    } catch (err) {
        alert("Error generating report: " + err.message);
        console.error(err);
    } finally {
        showLoading(false);
    }
}

// ── Download PDF ────────────────────────────────────────────────────────────

async function downloadPDF() {
    const patientId = $patientSelect.value;
    const rangeType = $rangeSelect.value;
    const useAI     = $aiToggle.checked;
    if (!patientId) return;

    const body = {
        patient_id: patientId,
        range_type: rangeType,
        client: currentClient,
        use_ai: useAI
    };
    if (isMonthKey(rangeType)) {
        body.range_type = "month";
        body.month = rangeType;
    } else if (rangeType === "custom") {
        body.start = $startDate.value || null;
        body.end = $endDate.value || null;
    }

    showLoading(true);

    try {
        const res = await fetch(`${API_BASE}/api/report/pdf`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });

        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        // Use the server's filename (CardioReport_<patient>_<Month_Year>.pdf) so
        // the download name has the patient + month, single-sourced.
        let fname = `CardioReport_${patientId}.pdf`;
        const cd = res.headers.get("Content-Disposition") || "";
        const m = cd.match(/filename="?([^"]+)"?/);
        if (m) fname = m[1];

        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = fname;
        document.body.appendChild(a);
        a.click();
        // Delay cleanup to prevent browsers from aborting the download
        setTimeout(() => {
            a.remove();
            URL.revokeObjectURL(url);
        }, 250);
    } catch (err) {
        alert("Error downloading PDF: " + err.message);
    } finally {
        showLoading(false);
    }
}

// ── Export JSON ──────────────────────────────────────────────────────────────

async function exportJSON() {
    const patientId = $patientSelect.value;
    const rangeType = $rangeSelect.value;
    if (!patientId) return;

    try {
        let url = `${API_BASE}/api/report/events.json?patient_id=${encodeURIComponent(patientId)}&range_type=${rangeType}`;
        if (rangeType === "custom") {
            if ($startDate.value) url += `&start=${$startDate.value}`;
            if ($endDate.value) url += `&end=${$endDate.value}`;
        }

        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        const data = await res.json();
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
        const blobUrl = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = blobUrl;
        a.download = `CardioReport_events_${patientId}.json`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(blobUrl);
    } catch (err) {
        alert("Error exporting JSON: " + err.message);
    }
}

// ── Render Report ───────────────────────────────────────────────────────────

function renderReport(r) {
    $emptyState.style.display = "none";
    $reportContainer.style.display = "block";

    // Header — date formats matching PDF exactly
    document.getElementById("meta-patient").innerHTML = `<strong>Patient ID:</strong> ${r.patient_id}`;
    document.getElementById("meta-period").innerHTML = `<strong>Period:</strong> ${formatPeriod(r.window_start, r.window_end)}`;
    document.getElementById("meta-date").innerHTML = `<strong>Report Date:</strong> ${formatDateLong(r.report_date)}`;
    document.getElementById("meta-resolution").innerHTML = `<strong>Resolution:</strong> ${r.data_resolution}`;
    document.getElementById("meta-coverage").innerHTML = `<strong>Coverage:</strong> ${r.coverage_summary || "—"}`;
    
    // AI Badge or Style (optional, visually indicates if LLM was used)
    const $narrativeText = document.getElementById("narrative-text");
    if (r.use_ai && r.narrative_source && r.narrative_source.includes("AI")) {
        $narrativeText.classList.add("ai-powered");
    } else {
        $narrativeText.classList.remove("ai-powered");
    }
    // Narrative — handle structured dict (deterministic) or plain string (LLM)
    if (r.narrative && typeof r.narrative === "object" && r.narrative.opening !== undefined) {
        // Structured narrative: opening + phase_lines + closing. Append the
        // tier-consistent priority grade so the summary line matches the PDF.
        const grade = (r.report_priority || "").charAt(0) + (r.report_priority || "").slice(1).toLowerCase();
        const gradeStr = grade ? `  <strong>Priority grade: ${escapeHtml(grade)}</strong>` : "";
        let narrativeHtml = `<p>${escapeHtml(r.narrative.opening)}${gradeStr}</p>`;
        if (r.narrative.phase_lines && r.narrative.phase_lines.length > 0) {
            narrativeHtml += "<div style='margin: 4px 0 8px 16px; padding: 0;'>";
            r.narrative.phase_lines.forEach(line => {
                narrativeHtml += `<div style="margin-bottom: 4px;">${escapeHtml(line)}</div>`;
            });
            narrativeHtml += "</div>";
        }
        if (r.narrative.closing) {
            narrativeHtml += `<p>${escapeHtml(r.narrative.closing)}</p>`;
        }
        $narrativeText.innerHTML = narrativeHtml;
    } else {
        $narrativeText.textContent = (typeof r.narrative === "string" ? r.narrative : "") || "No narrative available.";
    }

    // Narrative source label at the bottom of Section 1 (Hidden per user request)
    const $sourceLabel = document.getElementById("narrative-source-label");
    if ($sourceLabel) {
        $sourceLabel.style.display = "none";
    }
    const badge = document.getElementById("triage-badge");
    const triageUpper = r.triage.toUpperCase();
    const TRIAGE_BADGE_TEXT = {
        "RED": "RED: Provider Review Recommended",
        "YELLOW": "YELLOW: Closer Observation Suggested",
        "GREEN": "GREEN: Routine Review",
    };
    badge.textContent = TRIAGE_BADGE_TEXT[triageUpper] || triageUpper;
    badge.className = `triage-badge ${r.triage.toLowerCase()}`;

    // Priority badge (Decision 5)
    const priorityBadge = document.getElementById("priority-badge");
    const priority = (r.report_priority || "LOW").toUpperCase();
    const PRIORITY_LABELS = {
        "HIGH": "⚡ HIGH Priority",
        "MEDIUM": "● MEDIUM Priority",
        "LOW": "○ LOW Priority",
        "SKIP": "— SKIP",
    };
    priorityBadge.textContent = PRIORITY_LABELS[priority] || priority;
    priorityBadge.className = `priority-badge ${priority.toLowerCase()}`;

    // Quality warnings banner
    const warningSection = document.getElementById("quality-warnings");
    const warningText = document.getElementById("warning-text");
    if (r.quality_warnings && r.quality_warnings.length > 0) {
        warningText.textContent = r.quality_warnings.join(" • ");
        warningSection.style.display = "flex";
    } else {
        warningSection.style.display = "none";
    }

    // Phase timeline (status timeline)
    const phaseTimeline = document.getElementById("phase-timeline");
    const phasesContainer = document.getElementById("phases-container");
    if (r.phases && r.phases.length > 0) {
        phasesContainer.innerHTML = "";
        r.phases.forEach(p => {
            const block = document.createElement("div");
            block.className = `phase-block ${p.type}`;
            block.style.flex = p.days;
            block.innerHTML = `
                <span class="phase-label">${p.label}</span>
                <span class="phase-dates">${p.date_range} (${p.days}d)</span>
            `;
            phasesContainer.appendChild(block);
        });
        phaseTimeline.style.display = "block";
    } else {
        phaseTimeline.style.display = "none";
    }

    // Actions
    const actionsList = document.getElementById("actions-list");
    actionsList.innerHTML = "";
    (r.suggested_actions || []).forEach((action) => {
        const li = document.createElement("li");
        li.textContent = action;
        actionsList.appendChild(li);
    });

    // 24-hour snapshot (matches the PDF block)
    const snapSection = document.getElementById("section-snapshot");
    const snap = r.snapshot_24h;
    if (snap) {
        const snapCell = (v, unit) => (typeof v === "number") ? `${Math.round(v)} ${unit}` : "—";
        document.getElementById("snapshot-tbody").innerHTML = `
            <tr><td><strong>Heart Rate</strong></td><td>${snapCell(snap.hr && snap.hr.avg, "bpm")}</td><td>${snapCell(snap.hr && snap.hr.min, "bpm")}</td><td>${snapCell(snap.hr && snap.hr.max, "bpm")}</td></tr>
            <tr><td><strong>Breathing</strong></td><td>${snapCell(snap.rr && snap.rr.avg, "brpm")}</td><td>${snapCell(snap.rr && snap.rr.min, "brpm")}</td><td>${snapCell(snap.rr && snap.rr.max, "brpm")}</td></tr>
        `;
        const snapNote = document.getElementById("snapshot-note");
        if (snap.coverage_pct != null && snap.coverage_pct < 75) {
            snapNote.textContent = `Limited data in the final 24h (${snap.hours_present}/${snap.expected_hours}h); values reflect available readings.`;
            snapNote.style.display = "block";
        } else {
            snapNote.style.display = "none";
        }
        renderSnapshotBanner(snap);
        renderSnapshotSummary(snap);
        renderSnapshotEvents(snap);
        snapSection.style.display = "block";
    } else {
        snapSection.style.display = "none";
    }

    // Unified episodic events table — same per-finding rows as the PDF
    // (narrative.phase_table_rows), burden-weighted; bare-number metrics.
    const tbody = document.getElementById("episodes-tbody");
    const noEp = document.getElementById("no-episodes");
    const epWrap = document.getElementById("episodes-table").closest(".table-wrapper");
    tbody.innerHTML = "";
    const ptRows = (r.narrative && r.narrative.phase_table_rows) ? r.narrative.phase_table_rows.slice() : [];
    // Severity tier first (most important), then chronological within a tier.
    const SEV = {very_high_hr:0, very_low_hr:0, very_high_rr:0, high_hr:1, high_rr:1,
                 low_hr:2, elevated_hr:3, elevated_rr:3};
    const sevRank = (row) => SEV[row.phase_type] != null ? SEV[row.phase_type] : 9;
    const dnum = (row) => { const t = Date.parse((row.date || "").split(" to ")[0] + " 2000"); return isNaN(t) ? 8.64e15 : t; };
    ptRows.sort((a, b) => (sevRank(a) - sevRank(b)) || (dnum(a) - dnum(b)));
    const bare = (s) => String(s == null ? "" : s).replace(" bpm", "").replace(" brpm", "").trim();
    if (ptRows.length > 0) {
        if (epWrap) epWrap.style.display = "block";
        noEp.style.display = "none";
        ptRows.forEach((row) => {
            const tr = document.createElement("tr");
            const cond = String(row.category || "").replace(" (brief)", "");
            // Bold linking: condition keyword + triggering metric (Max for high
            // conditions, Min for low) bold, so the eye connects condition->number.
            const isLow = String(row.phase_type || "").includes("low");
            const minCell = bare(row.min), maxCell = bare(row.max);
            tr.innerHTML = `
                <td>${escapeHtml(row.date || "")}</td>
                <td>${escapeHtml(row.time_span || "")}</td>
                <td>${row.total_hours != null ? row.total_hours : ""}h</td>
                <td>${row.episodes != null ? row.episodes : ""}</td>
                <td><strong>${escapeHtml(cond)}</strong></td>
                <td>${escapeHtml(bare(row.average))}</td>
                <td>${isLow ? "<strong>" + escapeHtml(minCell) + "</strong>" : escapeHtml(minCell)}</td>
                <td>${isLow ? escapeHtml(maxCell) : "<strong>" + escapeHtml(maxCell) + "</strong>"}</td>
                <td>${escapeHtml(row.comment || "")}</td>
            `;
            tbody.appendChild(tr);
        });
    } else {
        if (epWrap) epWrap.style.display = "none";
        noEp.style.display = "block";
    }

    // Stats table — use human-readable labels matching PDF
    const statsTbody = document.getElementById("stats-tbody");
    statsTbody.innerHTML = "";

    if (r.full_stats && r.full_stats.rows && r.full_stats.rows.length > 0) {
        // Update header to 6 columns
        const statsTable = document.getElementById("stats-table");
        if (statsTable) {
            const thead = statsTable.querySelector("thead");
            if (thead) {
                thead.innerHTML = `<tr><th>Metric</th><th>Mean</th><th>Min</th><th>Max</th><th>P5</th><th>P95</th></tr>`;
            }
        }
        r.full_stats.rows.forEach((row) => {
            const tr = document.createElement("tr");
            // Use clinical-friendly stat labels matching PDF
            const displayLabel = STATS_LABELS[row.label] || row.label;
            tr.innerHTML = `<td><strong>${escapeHtml(displayLabel)}</strong></td><td>${row.mean}</td><td>${row.min}</td><td>${row.max}</td><td>${row.p5}</td><td>${row.p95}</td>`;
            statsTbody.appendChild(tr);
        });
    }

    // NEW: Positional Stats — hide for single-sensor bed reports
    const posSection = document.getElementById("positional-section");
    const bedSumSection = document.getElementById("bed-summary-section");
    const isBedSensor = r.sensor_type === "bed";
    const isSingleSensor = r.positional_comparison && r.positional_comparison.rows && r.positional_comparison.rows.length <= 1;

    if (isBedSensor && isSingleSensor && r.bed_summary) {
        // Hide positional, show bed activity summary
        posSection.style.display = "none";
        bedSumSection.style.display = "block";
        const bedTbody = document.getElementById("bed-summary-tbody");
        bedTbody.innerHTML = "";
        const bs = r.bed_summary;
        const bedRows = [
            ["Mean Daily Bed Time", `${bs.mean_daily_hours.toFixed(1)} hours`],
            ["Range", `${bs.min_hours.toFixed(0)} – ${bs.max_hours.toFixed(0)} hours`],
            ["Days Above 16 Hours", `${bs.days_above_16h} days`],
            ["Low HR Alert Days", `${bs.alert_days} days (${bs.total_alerts} total alerts)`],
        ];
        if (bs.hr_min_high_bed_days > 0 && bs.hr_min_normal_days > 0) {
            bedRows.push(
                ["HR Min (High Bed Days)", `${bs.hr_min_high_bed_days.toFixed(0)} bpm`],
                ["HR Min (Normal Days)", `${bs.hr_min_normal_days.toFixed(0)} bpm`],
            );
        }
        bedRows.forEach(([label, value]) => {
            const tr = document.createElement("tr");
            tr.innerHTML = `<td><strong>${escapeHtml(label)}</strong></td><td>${escapeHtml(value)}</td>`;
            bedTbody.appendChild(tr);
        });
    } else if (r.positional_comparison && r.positional_comparison.rows && r.positional_comparison.rows.length > 0) {
        posSection.style.display = "block";
        bedSumSection.style.display = "none";
        const posTbody = document.getElementById("positional-tbody");
        posTbody.innerHTML = "";
        r.positional_comparison.rows.forEach(row => {
            const tr = document.createElement("tr");
            tr.innerHTML = `<td><strong>${escapeHtml(row.location)}</strong></td><td>${row.hr_avg}</td><td>${row.rr_avg}</td><td>${row.hours}</td>`;
            posTbody.appendChild(tr);
        });
        const posSum = document.getElementById("positional-summary");
        if (r.positional_comparison.br_diff_living_vs_chair !== 0) {
            const diff = r.positional_comparison.br_diff_living_vs_chair;
            posSum.textContent = `Respiratory rate in living room averaged ${diff > 0 ? '+' : ''}${diff} breaths/min versus chair. Positional respiratory difference can indicate fluid redistribution when patient changes position.`;
        } else {
            posSum.textContent = "";
        }
    } else {
        posSection.style.display = "none";
        bedSumSection.style.display = "none";
    }

    // Chart — Bed Hours (bed sensor only, shown first)
    const bedChartImg = document.getElementById("bed-hours-chart-image");
    if (r.chart_bed_hours_b64) {
        bedChartImg.src = `data:image/png;base64,${r.chart_bed_hours_b64}`;
        bedChartImg.style.display = "block";
    } else {
        bedChartImg.style.display = "none";
    }

    // Chart — candlestick
    const chartImg = document.getElementById("chart-image");
    if (r.chart_combined_b64) {
        chartImg.src = `data:image/png;base64,${r.chart_combined_b64}`;
        chartImg.style.display = "block";
    } else {
        chartImg.style.display = "none";
    }

    // Chart — histogram
    const histImg = document.getElementById("histogram-image");
    if (r.chart_histogram_b64) {
        histImg.src = `data:image/png;base64,${r.chart_histogram_b64}`;
        histImg.style.display = "block";
    } else {
        histImg.style.display = "none";
    }

    // NEW: Positional Chart
    const posChartImg = document.getElementById("positional-chart-image");
    if (r.chart_positional_b64 && !isBedSensor) {
        posChartImg.src = `data:image/png;base64,${r.chart_positional_b64}`;
        posChartImg.style.display = "block";
    } else {
        posChartImg.style.display = "none";
    }

    // NEW: Activity Chart
    const actChartImg = document.getElementById("activity-chart-image");
    if (r.chart_activity_b64) {
        actChartImg.src = `data:image/png;base64,${r.chart_activity_b64}`;
        actChartImg.style.display = "block";
    } else {
        actChartImg.style.display = "none";
    }

    // Trend assessment bar
    const trendBar = document.getElementById("section-trend");
    trendBar.className = `trend-assessment-bar ${r.triage.toLowerCase()}`;
    document.getElementById("trend-value").textContent = r.trend_assessment;
    document.getElementById("posture-value").textContent = r.overall_action_posture;

    // Footer — matching PDF format + sensor modality disclaimer
    const dq = r.data_quality;
    const sensorNote = r.sensor_type === "bed"
        ? "Sensor: Radar-based contactless bed monitor (not ECG or pulse oximetry)."
        : "Sensor: Radar-based contactless chair monitor (not ECG or pulse oximetry).";
    document.getElementById("footer-quality").textContent =
        `Data Quality: ${dq.total_hours} of ${dq.expected_hours} expected hours (${dq.quality_pct}% coverage). Low confidence hours: ${dq.low_confidence_hours}. ${sensorNote}`;
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function showLoading(show) {
    $loading.style.display = show ? "flex" : "none";
}

/**
 * Format period like PDF: "June 24 to June 30, 2024"
 */
function formatPeriod(startIso, endIso) {
    if (!startIso || !endIso) return "—";
    const s = new Date(startIso + "T00:00:00");
    const e = new Date(endIso + "T00:00:00");
    const sMonth = s.toLocaleDateString("en-US", { month: "long" });
    const eMonth = e.toLocaleDateString("en-US", { month: "long" });
    const sDay = s.getDate();
    const eDay = e.getDate();
    const eYear = e.getFullYear();

    if (s.getFullYear() === e.getFullYear()) {
        return `${sMonth} ${sDay} to ${eMonth} ${eDay}, ${eYear}`;
    }
    return `${sMonth} ${sDay}, ${s.getFullYear()} to ${eMonth} ${eDay}, ${eYear}`;
}

/**
 * Format date like PDF: "July 1, 2024"
 */
function formatDateLong(iso) {
    if (!iso) return "—";
    const d = new Date(iso + "T00:00:00");
    const month = d.toLocaleDateString("en-US", { month: "long" });
    return `${month} ${d.getDate()}, ${d.getFullYear()}`;
}

/**
 * Format episode time window in 12h AM/PM: "07/20 11:00 PM to 07/21 6:00 AM"
 * Single-hour episodes: "10/27 5:00 AM (1h)"
 */
function formatEpWindow(startIso, endIso, durationHours) {
    if (!startIso || !endIso) return "—";
    try {
        const s = new Date(startIso);
        const e = new Date(endIso);
        const fmt = (d) => {
            const mm = String(d.getMonth() + 1).padStart(2, "0");
            const dd = String(d.getDate()).padStart(2, "0");
            let h = d.getHours();
            const mi = String(d.getMinutes()).padStart(2, "0");
            const ampm = h >= 12 ? "PM" : "AM";
            h = h % 12 || 12;
            return `${mm}/${dd} ${h}:${mi} ${ampm}`;
        };
        // Single-hour episodes: show "10/27 5:00 AM (1h)" instead of repeated timestamps
        if (s.getTime() === e.getTime() || (durationHours && durationHours <= 1)) {
            return `${fmt(s)} (${durationHours || 1}h)`;
        }
        return `${fmt(s)} to ${fmt(e)}`;
    } catch {
        return `${startIso} – ${endIso}`;
    }
}

/**
 * Compute Night/Day from episode start hour.
 * 7 PM (19:00) to 7 AM (07:00) = N (Night), otherwise D (Day).
 */
function computeNightDay(startIso) {
    if (!startIso) return "—";
    try {
        const h = new Date(startIso).getHours();
        return (h >= 19 || h < 7) ? "N" : "D";
    } catch {
        return "—";
    }
}

/**
 * Parse key_vitals string into separate HR and RR values.
 * Input examples:
 *   "HR avg 42, Min HR 40"
 *   "HR avg 55, Min HR 46, RR avg 22"
 * Returns: { hr: "avg 42 / min 40", rr: "avg 22" }
 */
function parseKeyVitals(vitals) {
    if (!vitals) return { hr: "—", rr: "—" };
    
    const hrParts = [];
    const rrParts = [];
    
    // Split by comma or pipe
    const segments = vitals.split(/[,|]/).map(s => s.trim()).filter(Boolean);
    
    for (const seg of segments) {
        const lower = seg.toLowerCase();
        if (lower.includes("rr") || lower.includes("breathing")) {
            // Extract the numeric part
            rrParts.push(seg.replace(/RR\s*/i, "").trim());
        } else if (lower.includes("hr") || lower.includes("heart")) {
            hrParts.push(seg.replace(/HR\s*/i, "").trim());
        }
    }
    
    return {
        hr: hrParts.length ? hrParts.join(" / ") : "—",
        rr: rrParts.length ? rrParts.join(" / ") : "—",
    };
}

function escapeHtml(text) {
    if (!text) return "";
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

function truncate(str, max) {
    if (!str) return "";
    return str.length > max ? str.substring(0, max - 3) + "…" : str;
}

// ── Boot ────────────────────────────────────────────────────────────────────

init();
