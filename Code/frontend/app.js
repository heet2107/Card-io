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

// ── DOM Elements ────────────────────────────────────────────────────────────

const $loading       = document.getElementById("loading-overlay");
const $reportContainer = document.getElementById("report-container");
const $summaryContainer = document.getElementById("summary-container");
const $emptyState    = document.getElementById("empty-state");
const $aiToggle      = document.getElementById("ai-toggle");
const $btnSummary    = document.getElementById("btn-summary");
const $btnSummaryDownload = document.getElementById("btn-summary-download");
const $btnSummaryBack = document.getElementById("btn-summary-back");
const $btnDownload   = document.getElementById("btn-download");
const $btnExportJson = document.getElementById("btn-export-json");
const $btnSmartWeek  = document.getElementById("btn-smart-week");

// Status chip colors for the 24h library summary (match the PDF).
const SUMMARY_STATUS_FILL = {
    "RED": "#DC2626", "YELLOW": "#D97706", "ELEVATED": "#D97706",
    "GREEN": "#16A34A", "NORMAL": "#16A34A",
    "LOW DATA": "#6B7280", "NO DATA": "#6B7280",
};

// ── State ───────────────────────────────────────────────────────────────────

let currentClient = null;
let currentReport = null;
let patientMeta = {};  // Cache: { patientId: { locations, date_range, total_hours } }

// Summary back button
if ($btnSummaryBack) {
    $btnSummaryBack.addEventListener("click", () => {
        if ($summaryContainer) $summaryContainer.style.display = "none";
        if ($reportContainer) $reportContainer.style.display = "block";
    });
}
if ($btnSummaryDownload) {
    $btnSummaryDownload.addEventListener("click", downloadLibrarySummary);
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



// ── Render Report ───────────────────────────────────────────────────────────

function renderReport(r) {
    if ($emptyState) $emptyState.style.display = "none";
    if ($reportContainer) $reportContainer.style.display = "block";

    const setElHtml = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
    const setElText = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };

    // 1. Header Band & Metadata Bar
    const badgeBox = document.getElementById("pdf-badge-box");
    const badgeText = document.getElementById("triage-badge");
    const triageUpper = (r.triage || "").toUpperCase();
    if (badgeBox && badgeText) {
        if (triageUpper === "RED" || triageUpper === "YELLOW") {
            badgeText.textContent = "Review recommended";
            badgeBox.className = `pdf-badge-box ${r.triage.toLowerCase()}`;
        } else {
            badgeText.textContent = "Routine monitoring";
            badgeBox.className = "pdf-badge-box green";
        }
    }

    setElHtml("meta-patient", `<strong>Patient ID:</strong> ${escapeHtml(r.patient_id)}`);
    setElHtml("meta-period", `<strong>Period:</strong> ${escapeHtml(formatPeriod(r.window_start, r.window_end))}`);
    setElHtml("meta-date", `<strong>Report Date:</strong> ${escapeHtml(formatDateLong(r.report_date))}`);
    setElHtml("meta-coverage", `<strong>Coverage:</strong> ${escapeHtml(r.coverage_summary || "—")}`);

    // 2. Action Posture / Why-Flagged Banner
    const actionBanner = document.getElementById("pdf-action-banner");
    const actionText = document.getElementById("action-banner-text");
    let whySentence = "";
    if (r.narrative && typeof r.narrative === "object" && r.narrative.opening) {
        whySentence = r.narrative.opening;
    } else if (typeof r.narrative === "string" && r.narrative.length > 0) {
        whySentence = r.narrative;
    }
    if (whySentence && (triageUpper === "RED" || triageUpper === "YELLOW")) {
        if (actionText) actionText.innerHTML = escapeHtml(whySentence);
        if (actionBanner) actionBanner.style.display = "block";
    } else {
        if (actionBanner) actionBanner.style.display = "none";
    }

    // 3. Quality Warnings Banner
    const warningSection = document.getElementById("quality-warnings");
    const warningText = document.getElementById("warning-text");
    if (r.quality_warnings && r.quality_warnings.length > 0) {
        if (warningText) warningText.textContent = r.quality_warnings.join(" • ");
        if (warningSection) warningSection.style.display = "flex";
    } else {
        if (warningSection) warningSection.style.display = "none";
    }

    // 4. Last 24 Hours Dual Column Grid (HR vs RR)
    const snap = r.snapshot_24h || {};
    const hrSnap = snap.hr || {};
    const rrSnap = snap.rr || {};
    const snapEvents = snap.events || [];

    const hrEvents = snapEvents.filter(e => String(e.phase_type || "").includes("hr"));
    const rrEvents = snapEvents.filter(e => String(e.phase_type || "").includes("rr") || String(e.category || "").includes("Breathing"));

    const renderL24List = (containerId, evList, zeroMsg) => {
        const el = document.getElementById(containerId);
        if (!el) return;
        if (evList.length === 0) {
            el.innerHTML = `<div style="color:#6B7280; font-style:italic;">${zeroMsg}</div>`;
        } else {
            el.innerHTML = evList.map(e => `
                <div style="margin-bottom: 6px;">
                    <strong>${escapeHtml(e.category || "Event")}</strong> — ${escapeHtml(e.time_span || "")}<br/>
                    <span style="color:#6B7280">${escapeHtml(e.average || "")} (min: ${e.min}, max: ${e.max})</span>
                </div>
            `).join("");
        }
    };

    renderL24List("l24-hr-events", hrEvents, "No measured windows");
    renderL24List("l24-rr-events", rrEvents, "No measured windows");

    const formatSpread = (d, unit) => {
        if (d && d.min != null && d.max != null) {
            const avgStr = d.avg != null ? ` <span style="color:#6B7280; font-weight:normal;">(avg: ${Math.round(d.avg)})</span>` : "";
            return `Spread: ${Math.round(d.min)} to ${Math.round(d.max)}${avgStr} ${unit}`;
        }
        return `Spread: — ${unit}`;
    };

    setElHtml("l24-hr-spread", formatSpread(hrSnap, "bpm"));
    setElHtml("l24-rr-spread", formatSpread(rrSnap, "brpm"));

    // 5. Status Timeline / Episodic Events + EPD Chart
    const phaseTimeline = document.getElementById("phase-timeline");
    const phasesContainer = document.getElementById("phases-container");
    const epdChart = document.getElementById("epd-chart-image");
    const headingEl = document.getElementById("timeline-heading");

    if (headingEl && r.window_start && r.window_end) {
        headingEl.innerHTML = `<span class="pdf-blue-text">Episodic events per day</span> &middot; ${escapeHtml(formatPeriod(r.window_start, r.window_end))}`;
    }

    if (epdChart) {
        if (r.chart_epd_b64) {
            epdChart.src = `data:image/png;base64,${r.chart_epd_b64}`;
            epdChart.style.display = "block";
        } else {
            epdChart.style.display = "none";
        }
    }

    if (r.phases && r.phases.length > 0 && phasesContainer) {
        phasesContainer.innerHTML = "";
        r.phases.forEach(p => {
            const block = document.createElement("div");
            block.className = `phase-block ${p.type}`;
            block.style.flex = p.days;
            block.innerHTML = `
                <span class="phase-label">${escapeHtml(p.label)}</span>
                <span class="phase-dates">${escapeHtml(p.date_range)} (${p.days}d)</span>
            `;
            phasesContainer.appendChild(block);
        });
        if (phaseTimeline) phaseTimeline.style.display = "block";
    } else if (r.chart_epd_b64 && phaseTimeline) {
        if (phasesContainer) phasesContainer.innerHTML = "";
        phaseTimeline.style.display = "block";
    } else if (phaseTimeline) {
        phaseTimeline.style.display = "none";
    }

    // 6. Narrative / Signal Summary
    const $narrativeText = document.getElementById("narrative-text");
    if ($narrativeText) {
        if (r.narrative && typeof r.narrative === "object" && r.narrative.opening !== undefined) {
            const grade = (r.report_priority || "").charAt(0) + (r.report_priority || "").slice(1).toLowerCase();
            const gradeStr = grade ? `  <strong>Priority grade: ${escapeHtml(grade)}</strong>` : "";
            let narrativeHtml = `<p>${escapeHtml(r.narrative.opening)}${gradeStr}</p>`;
            if (r.narrative.phase_lines && r.narrative.phase_lines.length > 0) {
                narrativeHtml += "<div style='margin: 4px 0 8px 16px;'>";
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
    }

    const actionsList = document.getElementById("actions-list");
    if (actionsList) {
        actionsList.innerHTML = "";
        (r.suggested_actions || []).forEach((action) => {
            const li = document.createElement("li");
            li.textContent = action;
            actionsList.appendChild(li);
        });
    }

    // 7. Measured Windows List (Cards with Blue Left Accent)
    const mwList = document.getElementById("measured-windows-list");
    const noEp = document.getElementById("no-episodes");
    if (mwList) mwList.innerHTML = "";

    const episodeRows = (r.narrative && r.narrative.episode_table_rows) ? r.narrative.episode_table_rows.slice() : [];
    const ptRows = episodeRows.length > 0 ? episodeRows : ((r.narrative && r.narrative.phase_table_rows) ? r.narrative.phase_table_rows.slice() : []);

    ptRows.sort((a, b) => (b.severity_score || 0) - (a.severity_score || 0));

    if (ptRows.length > 0) {
        if (noEp) noEp.style.display = "none";
        if (mwList) {
            const displayRows = ptRows.slice(0, 4);
            displayRows.forEach((row) => {
                const card = document.createElement("div");
                card.className = "measured-window-card";
                const cond = String(row.category || "").replace(" (brief)", "");
                const th = row.total_hours || row.sustained_hours || 0;
                const durationDesc = row.duration_descriptor ? `${row.duration_descriptor}, ${th}h` : (th ? `sustained, ${th}h` : "");
                card.innerHTML = `
                    <div class="window-card-left">
                        <span class="window-condition">${escapeHtml(cond)} ${durationDesc}</span>
                        <span class="window-time">${escapeHtml(row.date || "")} ${escapeHtml(row.time_span || "")} · avg ${escapeHtml(String(row.average || ""))} (peak ${escapeHtml(String(row.max || ""))})</span>
                    </div>
                    <div class="window-note">${escapeHtml(row.comment || "")}</div>
                `;
                mwList.appendChild(card);
            });
            if (ptRows.length > 4) {
                const plusMore = document.createElement("div");
                plusMore.style.marginTop = "8px";
                plusMore.style.fontStyle = "italic";
                plusMore.style.color = "#6B7280";
                plusMore.style.fontSize = "0.78rem";
                plusMore.textContent = `plus ${ptRows.length - 4} more windows`;
                mwList.appendChild(plusMore);
            }
        }
    } else {
        if (noEp) noEp.style.display = "block";
    }

    // 8. Summary Stat Tiles Grid
    const hr = r.hr_summaries || {};
    const rr = r.rr_summaries || {};
    const dq = r.data_quality || {};

    const hrP5 = hr.p5 != null ? Math.round(hr.p5) : "—";
    const hrP95 = hr.p95 != null ? Math.round(hr.p95) : "—";
    const rrP5 = rr.p5 != null ? Math.round(rr.p5) : "—";
    const rrP95 = rr.p95 != null ? Math.round(rr.p95) : "—";

    setElText("tile-hr-range", `${hrP5} to ${hrP95}`);
    setElText("tile-rr-range", `${rrP5} to ${rrP95}`);
    setElText("tile-coverage", `${dq.coverage_pct != null ? Math.round(dq.coverage_pct) + "%" : "—"}`);
    setElText("tile-windows", ptRows.length > 0 ? String(ptRows.length) : "0");

    const phaseBanner = document.getElementById("pdf-phase-banner");
    if (phaseBanner) {
        if (r.phases && r.phases.length > 0) {
            const topPhase = r.phases[0];
            phaseBanner.textContent = `${topPhase.label} (${topPhase.date_range})`;
            phaseBanner.style.display = "block";
        } else {
            phaseBanner.style.display = "none";
        }
    }

    // Page 2 header bar — compact patient/period/badge + "Page 2 of 2"
    const page2Header = document.getElementById("page2-header");
    const page2Meta = document.getElementById("page2-meta");
    if (page2Header && page2Meta) {
        const triageBadge = (triageUpper === "RED" || triageUpper === "YELLOW") ? "Review recommended" : "Routine monitoring";
        page2Meta.innerHTML = `<strong>Patient ID:</strong> ${escapeHtml(r.patient_id)} &nbsp;|&nbsp; <strong>Period:</strong> ${escapeHtml(formatPeriod(r.window_start, r.window_end))} &nbsp;|&nbsp; <strong>${escapeHtml(triageBadge)}</strong> &nbsp;|&nbsp; Page 2 of 2`;
    }

    // Positional Stats — hide for single-sensor bed reports
    const posSection = document.getElementById("positional-section");
    const bedSumSection = document.getElementById("bed-summary-section");
    const isBedSensor = r.sensor_type === "bed";
    const isSingleSensor = r.positional_comparison && r.positional_comparison.rows && r.positional_comparison.rows.length <= 1;

    if (isBedSensor && isSingleSensor && r.bed_summary) {
        if (posSection) posSection.style.display = "none";
        if (bedSumSection) bedSumSection.style.display = "block";
        const bedTbody = document.getElementById("bed-summary-tbody");
        if (bedTbody) {
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
        }
    } else if (r.positional_comparison && r.positional_comparison.rows && r.positional_comparison.rows.length > 0) {
        if (posSection) posSection.style.display = "block";
        if (bedSumSection) bedSumSection.style.display = "none";
        const posTbody = document.getElementById("positional-tbody");
        if (posTbody) {
            posTbody.innerHTML = "";
            r.positional_comparison.rows.forEach(row => {
                const tr = document.createElement("tr");
                tr.innerHTML = `<td><strong>${escapeHtml(row.location)}</strong></td><td>${row.hr_avg}</td><td>${row.rr_avg}</td><td>${row.hours}</td>`;
                posTbody.appendChild(tr);
            });
        }
        const posSum = document.getElementById("positional-summary");
        if (posSum) {
            if (r.positional_comparison.br_diff_living_vs_chair !== 0) {
                const diff = r.positional_comparison.br_diff_living_vs_chair;
                posSum.textContent = `Respiratory rate in living room averaged ${diff > 0 ? '+' : ''}${diff} breaths/min versus chair. Positional respiratory difference can indicate fluid redistribution when patient changes position.`;
            } else {
                posSum.textContent = "";
            }
        }
    } else {
        if (posSection) posSection.style.display = "none";
        if (bedSumSection) bedSumSection.style.display = "none";
    }

    // Chart — Bed Hours (bed sensor only)
    const bedChartImg = document.getElementById("bed-hours-chart-image");
    if (bedChartImg) {
        if (r.chart_bed_hours_b64) {
            bedChartImg.src = `data:image/png;base64,${r.chart_bed_hours_b64}`;
            bedChartImg.style.display = "block";
        } else {
            bedChartImg.style.display = "none";
        }
    }

    // Chart — Daily trend (candlestick)
    const chartImg = document.getElementById("chart-image");
    if (chartImg) {
        if (r.chart_combined_b64) {
            chartImg.src = `data:image/png;base64,${r.chart_combined_b64}`;
            chartImg.style.display = "block";
        } else {
            chartImg.style.display = "none";
        }
    }

    // Chart — Distribution (histogram)
    const histImg = document.getElementById("histogram-image");
    if (histImg) {
        if (r.chart_histogram_b64) {
            histImg.src = `data:image/png;base64,${r.chart_histogram_b64}`;
            histImg.style.display = "block";
        } else {
            histImg.style.display = "none";
        }
    }

    // Positional Chart
    const posChartImg = document.getElementById("positional-chart-image");
    if (posChartImg) {
        if (r.chart_positional_b64 && !isBedSensor) {
            posChartImg.src = `data:image/png;base64,${r.chart_positional_b64}`;
            posChartImg.style.display = "block";
        } else {
            posChartImg.style.display = "none";
        }
    }

    // Activity Chart (Daily monitoring coverage)
    const actChartImg = document.getElementById("activity-chart-image");
    if (actChartImg) {
        if (r.chart_activity_b64) {
            actChartImg.src = `data:image/png;base64,${r.chart_activity_b64}`;
            actChartImg.style.display = "block";
        } else {
            actChartImg.style.display = "none";
        }
    }

    // Trend assessment bar
    const trendBar = document.getElementById("section-trend");
    if (trendBar) {
        trendBar.className = `trend-assessment-bar ${(r.triage || "").toLowerCase()}`;
        setElText("trend-value", r.trend_assessment || "Stable");
        setElText("posture-value", r.overall_action_posture || "Routine review");
    }

    // Footer — matching PDF format + sensor modality disclaimer
    const dqObj = r.data_quality || {};
    const sensorNote = r.sensor_type === "bed"
        ? "Sensor: Radar-based contactless bed monitor (not ECG or pulse oximetry)."
        : "Sensor: Radar-based contactless chair monitor (not ECG or pulse oximetry).";
    document.getElementById("footer-quality").textContent =
        `Data Quality: ${dqObj.total_hours || 0} of ${dqObj.expected_hours || 0} expected hours (${dqObj.quality_pct || 0}% coverage). ${sensorNote}`;
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

// ── Global Exports ──────────────────────────────────────────────────────────
window.renderFullReport = renderReport;
window.showLibrarySummary = showLibrarySummary;
window.downloadLibrarySummary = downloadLibrarySummary;
window.showLoading = showLoading;
