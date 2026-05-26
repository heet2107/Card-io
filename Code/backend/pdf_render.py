"""
CardioReport – PDF Render
Generates a two-page clinical intelligence trend report.
Page 1: Header, status timeline, narrative + actions (consolidated), events table.
Page 2: Candlestick chart, histogram, stats table (+ positional), activity chart.
Both pages share header and footer.
"""

from __future__ import annotations
import io
import html as _html
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image,
    HRFlowable, KeepTogether, PageBreak
)

from .config import (
    settings, CONDITION_DISPLAY, STATS_LABELS, PHASE_LABELS, PHASE_COLORS,
    TriageLabels, Conditions, ChartColors as CC, RENDER_CONFIG
)
from .models import ReportResponse
from .charts import generate_candlestick_for_pdf, generate_histogram_for_pdf


# ── Color Palette ────────────────────────────────────────────────────────────

def _hex(h: str):
    return colors.HexColor(h)

def _v(o, k, d=None):
    """Robust value extractor for dict or object (Pydantic)."""
    if o is None: return d
    try:
        # 1. Try attribute access
        return getattr(o, k)
    except Exception:
        try:
            # 2. Try dict-style access
            return o[k]
        except (KeyError, TypeError, AttributeError):
            try:
                # 3. Try .get() method
                return o.get(k, d)
            except AttributeError:
                try:
                    # 4. Try as dict conversion
                    if hasattr(o, "dict"):
                        return o.dict().get(k, d)
                    elif hasattr(o, "model_dump"):
                        return o.model_dump().get(k, d)
                    return d
                except Exception:
                    return d

def _format_status_heading(report, days_count: int) -> str:
    """R22.D — render the per-patient summary header line including dates.

    Tolerates the legacy single-arg template (only `{days}`) so older
    overrides keep working — surfaces blank dates for back-compat callers
    that don't pass a window.
    """
    import pandas as pd
    template = settings.status_timeline_heading
    ws_v = _v(report, "window_start", "")
    we_v = _v(report, "window_end", "")
    try:
        start_str = pd.Timestamp(ws_v).strftime("%b %d") if ws_v else ""
        end_str = pd.Timestamp(we_v).strftime("%b %d") if we_v else ""
    except Exception:
        start_str, end_str = str(ws_v), str(we_v)
    try:
        return template.format(days=days_count, start=start_str, end=end_str)
    except KeyError:
        return template.format(days=days_count)


def _render_status_heading_with_index(report, days_count: int, page_w, st):
    """R24.2 — title row that pairs the status heading with an HR / Breathing
    color index right-aligned on the same line.

    Returns a single-row Table flowable. The swatch colors source from
    config.PHASE_COLORS via the phase_strip_index_swatch_family mapping so a
    palette change cascades automatically (no duplicate hex literals).

    Sajol May 19 call: "somehow we've got to say something about the red and
    blue." Heet committed to placing the index next to the title.
    """
    from .config import (
        PHASE_COLORS as _PC,
        phase_strip_index_swatch_family as _swatch_family,
    )
    heading = _format_status_heading(report, days_count)
    hr_color = _PC.get(_swatch_family.get("hr", "low_hr"), "#DC2626")
    rr_color = _PC.get(_swatch_family.get("rr", "elevated_rr"), "#3B82F6")
    # ReportLab Paragraph supports inline <font color="..."> markup. Right-
    # align the swatch cell so the index sits against the chart frame.
    index_html = (
        f'<font color="{hr_color}">■</font> HR'
        f'&nbsp;&nbsp;&nbsp;'
        f'<font color="{rr_color}">■</font> Breathing'
    )
    right_style = ParagraphStyle(
        "_status_heading_index", parent=st["section_head"], alignment=2,
    )
    tbl = Table(
        [[Paragraph(heading, st["section_head"]), Paragraph(index_html, right_style)]],
        colWidths=[page_w * 0.72, page_w * 0.28],
    )
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return tbl


_BRAND_BLUE   = _hex("#1E40AF")
_HEADER_BG    = _hex("#1E3A5F")
_DARK_TEXT    = _hex(CC.TEXT)
_GRAY_TEXT    = _hex("#6B7280")
_LIGHT_BG     = _hex("#F9FAFB")
_BORDER       = _hex("#D1D5DB")

# Triage Specific Colors (using ReportLab built-ins or hex)
_RED_BG      = _hex("#FEF2F2")
_RED_TEXT     = _hex("#991B1B")
_AMBER_BG    = _hex("#FFFBEB")
_AMBER_TEXT   = _hex("#92400E")
_GREEN_BG    = _hex("#F0FDF4")
_GREEN_TEXT   = _hex("#166534")

_TRIAGE_COLORS = {
    TriageLabels.RED:    (_RED_BG, _RED_TEXT),
    TriageLabels.YELLOW: (_AMBER_BG, _AMBER_TEXT),
    TriageLabels.GREEN:  (_GREEN_BG, _GREEN_TEXT),
}

_TRIAGE_BADGE_TEXT = {
    TriageLabels.RED:    "RED: Provider Review Recommended",
    TriageLabels.YELLOW: "YELLOW: Closer Observation Suggested",
    TriageLabels.GREEN:  "GREEN: Routine Review",
}

# Phase type colors for the status timeline bar — pulled from config
_PHASE_COLOR_MAP = {k: _hex(v) for k, v in PHASE_COLORS.items()}


# ── Styles ───────────────────────────────────────────────────────────────────

def _styles():
    ss = getSampleStyleSheet()

    return {
        "title": ParagraphStyle("cr_title", parent=ss["Normal"],
            fontSize=14, fontName="Helvetica-Bold", textColor=colors.white,
            spaceAfter=2, alignment=TA_LEFT),

        "meta_line": ParagraphStyle("cr_meta", parent=ss["Normal"],
            fontSize=8, fontName="Helvetica", textColor=_DARK_TEXT,
            leading=11, spaceAfter=0),

        "disclaimer": ParagraphStyle("cr_disclaimer", parent=ss["Normal"],
            fontSize=7, fontName="Helvetica-Oblique", textColor=_GRAY_TEXT,
            leading=9, spaceAfter=2),

        "section_head": ParagraphStyle("cr_sechead", parent=ss["Normal"],
            fontSize=10, fontName="Helvetica-Bold", textColor=_BRAND_BLUE,
            spaceBefore=6, spaceAfter=4),

        "body": ParagraphStyle("cr_body", parent=ss["Normal"],
            fontSize=8.5, fontName="Helvetica", textColor=_DARK_TEXT,
            leading=12, alignment=TA_JUSTIFY, spaceAfter=3),

        "body_bold": ParagraphStyle("cr_body_bold", parent=ss["Normal"],
            fontSize=8.5, fontName="Helvetica-Bold", textColor=_DARK_TEXT,
            leading=12, spaceAfter=2),

        "trend_line": ParagraphStyle("cr_trend_line", parent=ss["Normal"],
            fontSize=8.5, fontName="Helvetica", textColor=_DARK_TEXT,
            leading=12, spaceAfter=2),

        "action_head": ParagraphStyle("cr_action_head", parent=ss["Normal"],
            fontSize=8.5, fontName="Helvetica-Bold", textColor=_DARK_TEXT,
            leading=12, spaceBefore=3, spaceAfter=2),

        "action_item": ParagraphStyle("cr_action", parent=ss["Normal"],
            fontSize=8, fontName="Helvetica", textColor=_DARK_TEXT,
            leading=11, leftIndent=12, spaceAfter=2, bulletIndent=0),

        "caption": ParagraphStyle("cr_caption", parent=ss["Normal"],
            fontSize=7, fontName="Helvetica-Oblique", textColor=_GRAY_TEXT,
            leading=9, alignment=TA_LEFT, spaceAfter=4),

        "legend": ParagraphStyle("cr_legend", parent=ss["Normal"],
            fontSize=6.5, fontName="Helvetica-Oblique", textColor=_GRAY_TEXT,
            leading=8, alignment=TA_LEFT, spaceAfter=3),

        "footer": ParagraphStyle("cr_footer", parent=ss["Normal"],
            fontSize=6.5, fontName="Helvetica", textColor=_GRAY_TEXT,
            alignment=TA_CENTER, spaceBefore=4),

        "badge": ParagraphStyle("cr_badge", parent=ss["Normal"],
            fontSize=9, fontName="Helvetica-Bold", alignment=TA_CENTER),

        "phase_label": ParagraphStyle("cr_phase", parent=ss["Normal"],
            fontSize=7, fontName="Helvetica", textColor=colors.white,
            alignment=TA_CENTER, leading=9),

        "table_header": ParagraphStyle("cr_thdr", parent=ss["Normal"],
            fontSize=7.5, fontName="Helvetica-Bold", textColor=colors.white,
            leading=10),

        "table_cell": ParagraphStyle("cr_tcell", parent=ss["Normal"],
            fontSize=7.5, fontName="Helvetica", textColor=_DARK_TEXT,
            leading=10),

        "comment_cell": ParagraphStyle("cr_comment", parent=ss["Normal"],
            fontSize=7, fontName="Helvetica", textColor=_DARK_TEXT,
            leading=9, wordWrap='CJK'),

        "bullet": ParagraphStyle("cr_bullet", parent=ss["Normal"],
            fontSize=8, fontName="Helvetica", textColor=_DARK_TEXT,
            leading=11, leftIndent=14, spaceAfter=1, bulletIndent=0),
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _format_period(start: str, end: str) -> str:
    try:
        s = datetime.strptime(start, "%Y-%m-%d")
        e = datetime.strptime(end, "%Y-%m-%d")
        return f"{s.strftime('%B')} {s.day} to {e.strftime('%B')} {e.day}, {e.year}"
    except Exception:
        return f"{start} to {end}"


def _format_report_date(d: str) -> str:
    try:
        dt = datetime.strptime(d, "%Y-%m-%d")
        return f"{dt.strftime('%B')} {dt.day}, {dt.year}"
    except Exception:
        return d


# ── FIX 7: Compact time format ──────────────────────────────────────────────

def _fmt_time_compact(ts):
    """Format hour without spaces: '4AM', '11PM', '12PM'."""
    h = ts.hour
    ampm = 'AM' if h < 12 else 'PM'
    h12 = h % 12
    if h12 == 0:
        h12 = 12
    return f"{h12}{ampm}"


def _fmt_date_compact(ts):
    """Format date as MM/DD."""
    return ts.strftime('%m/%d')


def format_episode_window(start_str: str, end_str: str, hours: int = 0) -> str:
    """Compact time format: '03/29 4AM-5AM' or '03/29 11PM-03/30 6AM' for overnight."""
    try:
        s = datetime.fromisoformat(start_str)
        e = datetime.fromisoformat(end_str)
    except Exception:
        return f"{start_str} to {end_str}"

    if hours <= 1 or s == e:
        # Single hour: "03/29 4AM (1h)"
        return f"{_fmt_date_compact(s)} {_fmt_time_compact(s)} ({hours or 1}h)"

    if s.date() == e.date():
        # Same day: "03/29 4AM-5AM"
        return f"{_fmt_date_compact(s)} {_fmt_time_compact(s)}-{_fmt_time_compact(e)}"
    else:
        # Cross midnight: "03/29 11PM-03/30 6AM"
        return f"{_fmt_date_compact(s)} {_fmt_time_compact(s)}-{_fmt_date_compact(e)} {_fmt_time_compact(e)}"


def _compute_night_day(start_iso: str) -> str:
    """7 PM to 7 AM = N, else D. Uses settings for configurability."""
    try:
        h = datetime.fromisoformat(start_iso).hour
        return "N" if (h >= 19 or h < 7) else "D"
    except Exception:
        return "—"


def _parse_hr_rr(key_vitals: str) -> tuple[str, str]:
    if not key_vitals:
        return ("—", "—")
    
    hr_val, rr_val = "—", "—"
    # Expected: "HR avg 55 / min 45 | RR avg 18 / min 16"
    parts = key_vitals.split("|")
    for part in parts:
        part = part.strip()
        if part.lower().startswith("hr"):
            hr_val = part.replace("HR ", "").strip()
        elif part.lower().startswith("rr"):
            rr_val = part.replace("RR ", "").strip()
    
    return hr_val, rr_val


def _build_header(report, st, page_w):
    elements = []
    t = _v(report, "triage")
    triage_bg, triage_text = _TRIAGE_COLORS.get(t, (_GREEN_BG, _GREEN_TEXT))
    badge_label = _TRIAGE_BADGE_TEXT.get(t, str(t).upper())

    header_data = [[
        Paragraph("CLINICAL INTELLIGENCE TREND REPORT", st["title"]),
        Paragraph(f'<font color="#{triage_text.hexval()[2:]}">{_html.escape(badge_label)}</font>', st["badge"]),
    ]]
    header_table = Table(header_data, colWidths=[page_w * 0.65, page_w * 0.35])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (0, 0), _HEADER_BG),
        ("BACKGROUND", (1, 0), (1, 0), triage_bg),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (0, 0), 8),
        ("RIGHTPADDING", (1, 0), (1, 0), 8),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 4))

    period_str = _format_period(_v(report, "window_start"), _v(report, "window_end"))
    report_date_str = _format_report_date(_v(report, "report_date"))

    # FIX 3: Coverage in header only, using coverage_summary from pipeline
    cov_str = _html.escape(_v(report, 'coverage_summary')).replace(' | ', '<br/>')
    meta_table_data = [[
        Paragraph(f"<b>Patient ID:</b> {_html.escape(_v(report, 'patient_id'))}", st["meta_line"]),
        Paragraph(f"<b>Period:</b> {_html.escape(period_str)}", st["meta_line"]),
        Paragraph(f"<b>Report Date:</b> {_html.escape(report_date_str)}", st["meta_line"]),
        Paragraph(f"<b>Coverage:</b> {cov_str}", st["meta_line"]),
    ]]
    meta_table = Table(meta_table_data, colWidths=[page_w * 0.20, page_w * 0.30, page_w * 0.23, page_w * 0.27])
    meta_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    elements.append(meta_table)
    # R17 C: 90DayPeriod fallback note for under-90-day patients.
    # R24.3: 30DayPeriod fallback note for under-30-day patients; same flag
    # carrier (`is_fallback_90d` reused since both windows share the same
    # "requested window exceeded available data" semantic).
    # R18 N3 (revised): combine note + disclaimer into a single Paragraph so the
    # extra line doesn't push page-1 content (e.g. the "Red bars indicate..."
    # caption) onto a phantom page 2. PHolst 90DP / RSanchez 90DP previously
    # spilled to page 3 because of the separate fallback paragraph.
    disclaimer_text = _html.escape(_v(report, "disclaimer"))
    if _v(report, "is_fallback_90d"):
        report_label_v = _v(report, "report_label", "")
        note_key = (
            "fallback_note_30day" if report_label_v == "30DayPeriod"
            else "fallback_note_90day"
        )
        fallback_note = RENDER_CONFIG.get(note_key, "")
        if fallback_note:
            disclaimer_text = (
                f"<i>{_html.escape(fallback_note)}</i> {disclaimer_text}"
            )
    elements.append(Paragraph(disclaimer_text, st["disclaimer"]))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=_BORDER))
    elements.append(Spacer(1, 2))
    return elements


def _build_compact_header(report, st, page_w, page_num, total_pages=2):
    t = _v(report, "triage")
    badge_label = _TRIAGE_BADGE_TEXT.get(t, str(t).upper())
    triage_bg, triage_text = _TRIAGE_COLORS.get(t, (_GREEN_BG, _GREEN_TEXT))
    period_str = _format_period(_v(report, "window_start"), _v(report, "window_end"))
    
    header_text = (
        f"<b>Patient ID:</b> {_html.escape(_v(report, 'patient_id'))}  |  "
        f"<b>Period:</b> {_html.escape(period_str)}  |  "
        f"<b><font color='#{triage_text.hexval()[2:]}'>{_html.escape(badge_label)}</font></b>  |  "
        f"Page {page_num} of {total_pages}"
    )
    p = Paragraph(header_text, st["meta_line"])
    tbl = Table([[p]], colWidths=[page_w])
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.3, _BORDER),
    ]))
    return [tbl, Spacer(1, 8)]


# FIX 3: Footer no longer shows data quality/coverage — just the version
def _build_footer(report, st):
    return []


# ── PDF Builder ──────────────────────────────────────────────────────────────



def compute_reporting_period_days(window_start, window_end):
    import pandas as pd
    return (pd.Timestamp(window_end).normalize() - pd.Timestamp(window_start).normalize()).days + 1


def _build_episode_day_map(episodes):
    """R14 F2: Build per-day episode hours map from episode list.
    Returns: {normalized_date → {"hr_hours": N, "rr_hours": N, "hr_type": str}}
    """
    import pandas as pd
    day_map = {}  # date → {hr_hours, rr_hours, hr_type}
    if not episodes:
        return day_map

    # R19 A: include all RR conditions. Pre-R19 only "Tachypnea" was here, so
    # R15 A2 additions (High RR > 30, Very High RR > 40) fell through to the
    # HR branch with default hr_type="low_hr" — Wimberley's strip mislabeled
    # 77 of his 97 RR episodes as Low HR (Sajol May 4 review item 3c).
    rr_conditions = {"Tachypnea", "High RR", "Very High RR"}

    for ep in episodes:
        try:
            ep_start = pd.Timestamp(ep.start_time if hasattr(ep, 'start_time') else ep['start_time'])
            ep_end = pd.Timestamp(ep.end_time if hasattr(ep, 'end_time') else ep['end_time'])
            condition = ep.condition if hasattr(ep, 'condition') else ep['condition']
            duration = int(ep.duration_hours if hasattr(ep, 'duration_hours') else ep['duration_hours'])
        except Exception:
            continue

        is_rr = condition in rr_conditions

        # Map condition to phase type for HR episodes
        from .config import Conditions
        hr_type_map = {
            Conditions.SEVERE_BRADY: "very_low_hr",
            Conditions.BRADYCARDIAC: "low_hr",
            Conditions.ELEVATED_HR: "elevated_hr",
            Conditions.TACHYCARDIA: "high_hr",
            Conditions.VERY_HIGH_HR: "very_high_hr",
        }
        # Severity ranking: higher = more clinically urgent
        HR_SEVERITY_RANK = {
            "very_low_hr": 1, "low_hr": 2, "elevated_hr": 3,
            "high_hr": 4, "very_high_hr": 5,
        }
        hr_type = hr_type_map.get(condition, "low_hr")

        # Distribute episode hours across the days it spans
        current_day = ep_start.normalize()
        end_day = ep_end.normalize()
        span_days = max(1, (end_day - current_day).days + 1)
        hours_per_day = max(1, duration // span_days)

        while current_day <= end_day:
            if current_day not in day_map:
                day_map[current_day] = {"hr_hours": 0, "rr_hours": 0, "hr_type": "low_hr"}
            if is_rr:
                day_map[current_day]["rr_hours"] += hours_per_day
            else:
                day_map[current_day]["hr_hours"] += hours_per_day
                # Keep the most severe HR type seen on this day
                current_rank = HR_SEVERITY_RANK.get(day_map[current_day]["hr_type"], 0)
                new_rank = HR_SEVERITY_RANK.get(hr_type, 0)
                if new_rank > current_rank:
                    day_map[current_day]["hr_type"] = hr_type
            current_day += pd.Timedelta(days=1)

    return day_map


def build_status_timeline_segments(window_start, window_end, display_phases, recorded_dates=None, episode_day_map=None):
    import pandas as pd
    from .config import settings, PHASE_LABELS, _PHASE_COLOR_MAP as PHASE_COLORS
    reporting_days = compute_reporting_period_days(window_start, window_end)

    use_episode_hours = (settings.phase_strip_day_coloring_mode == "episode_hours"
                         and episode_day_map is not None)
    min_ep_hours = settings.phase_strip_min_episode_hours_per_day

    # Build a day-by-day type assignment
    day_types = []
    current = pd.Timestamp(window_start).normalize()
    we = pd.Timestamp(window_end).normalize()

    while current <= we:
        # Priority 1: no data recorded → white
        if recorded_dates is not None and current not in recorded_dates:
            day_types.append({
                'date': current,
                'type': 'no_data',
                'color': settings.phase_strip_no_data_color,
                'label': None,
            })
        elif use_episode_hours:
            # R14 F2: Episode-hours mode — color by actual episode presence
            day_info = episode_day_map.get(current)
            colored = False
            if day_info:
                hr_h = day_info.get('hr_hours', 0)
                rr_h = day_info.get('rr_hours', 0)
                total_h = hr_h + rr_h
                if total_h >= min_ep_hours:
                    if hr_h >= rr_h:
                        day_type = day_info.get('hr_type', 'low_hr')
                    else:
                        day_type = 'elevated_rr'
                    day_types.append({
                        'date': current,
                        'type': day_type,
                        'color': PHASE_COLORS.get(day_type, '#3B82F6'),
                        'label': PHASE_LABELS.get(day_type),
                    })
                    colored = True
            if not colored:
                day_types.append({
                    'date': current,
                    'type': 'normal',
                    'color': settings.phase_strip_no_episode_color,
                    'label': None,
                })
        else:
            # Legacy phase-window mode
            phase_on_day = None
            for p in display_phases:
                p_st = pd.Timestamp(p.get('start_date', '')).normalize()
                p_en = pd.Timestamp(p.get('end_date', '')).normalize()
                if p_st <= current <= p_en:
                    phase_on_day = p
                    break

            if phase_on_day:
                day_types.append({
                    'date': current,
                    'type': phase_on_day['type'],
                    'color': PHASE_COLORS.get(phase_on_day['type'], '#6B7280'),
                    'label': PHASE_LABELS.get(phase_on_day['type']),
                })
            else:
                day_types.append({
                    'date': current,
                    'type': 'normal',
                    'color': settings.phase_strip_no_episode_color,
                    'label': None,
                })

        current += pd.Timedelta(days=1)

    # R14 B2: Coalesce short no_data gaps into surrounding normal segments
    min_gap_days = max(1, settings.phase_strip_min_gap_hours // 24)
    coalesced = []
    for dt in day_types:
        coalesced.append(dt)
    # Two-pass: find short no_data runs and convert to normal
    i = 0
    while i < len(coalesced):
        if coalesced[i]['type'] == 'no_data':
            run_start = i
            while i < len(coalesced) and coalesced[i]['type'] == 'no_data':
                i += 1
            run_len = i - run_start
            if run_len < min_gap_days:
                for j in range(run_start, i):
                    coalesced[j]['type'] = 'normal'
                    coalesced[j]['color'] = settings.phase_strip_no_episode_color
        else:
            i += 1
    day_types = coalesced
    
    # Merge consecutive same-type days into segments
    segments = []
    for day in day_types:
        if segments and segments[-1]['type'] == day['type']:
            segments[-1]['days'] += 1
            segments[-1]['end_date'] = day['date']
        else:
            segments.append({
                'type': day['type'],
                'color': day['color'],
                'label': day['label'],
                'days': 1,
                'start_date': day['date'],
                'end_date': day['date'],
            })

    # R13 Fix 2 / R14 F5: Dual-bound phase merge.
    # In episode_hours mode, use tighter merge gap from config.
    # In phase_window mode, use period-relative gap.
    if use_episode_hours:
        period_gap_max = settings.phase_strip_episode_merge_max_gap_days
    else:
        period_gap_max = max(2, int(reporting_days * 0.05))
    merged = []
    for seg in segments:
        if (merged and merged[-1]['type'] != 'normal' and seg['type'] == 'normal'
                and seg['days'] <= period_gap_max):
            merged.append(seg)
            continue
        if (len(merged) >= 2 and seg['type'] != 'normal'
                and merged[-1]['type'] == 'normal'
                and merged[-2]['type'] == seg['type']
                and merged[-1]['days'] <= period_gap_max):
            prior = merged[-2]
            gap = merged[-1]
            # Phase-relative bound
            shorter = min(prior['days'], seg['days'])
            phase_gap_max = max(2, int(shorter * 0.5))
            if gap['days'] > phase_gap_max:
                # Gap is too large relative to the phases themselves — don't merge
                merged.append(seg)
                continue
            prior['days'] += gap['days'] + seg['days']
            prior['end_date'] = seg['end_date']
            merged.pop()
            continue
        merged.append(seg)

    return merged


def _get_phase_label_for_width(phase_type, segment_width_inches, full_label=''):
    """Choose the appropriate label format based on available segment width.

    Round 10 Fix 1: Config-driven label cascade. Never truncate mid-word.
    R14 D1: Never return blank — always show at least the abbreviation.
    Cascade: full label → config abbreviation → first word → abbreviation (forced).
    """
    cfg_ps = RENDER_CONFIG["phase_strip"]
    label = full_label or PHASE_LABELS.get(phase_type, '') or ''
    if not label:
        return ''

    # Rough estimate: each character needs ~0.09 inches at 8pt font
    CHAR_WIDTH = 0.09
    abbrev = cfg_ps["label_abbreviations"].get(label, '')

    # 1. Full label fits?
    if len(label) * CHAR_WIDTH <= segment_width_inches:
        return label

    # 2. Config abbreviation fits?
    if abbrev and len(abbrev) * CHAR_WIDTH <= segment_width_inches:
        return abbrev

    # 3. First word fits?
    first_word = label.split()[0] if label else ''
    if first_word and len(first_word) * CHAR_WIDTH <= segment_width_inches:
        return first_word

    # 4. R14 D1: Always return at least the abbreviation — never blank
    return abbrev or first_word or label


def _cap_phases(segments, reporting_days):
    """Round 10 Fix 1: Cap phase count by period length. Merge overflow."""
    cfg_ps = RENDER_CONFIG["phase_strip"]
    non_normal = [s for s in segments if s['type'] not in ('normal', 'no_data')]
    max_phases = 12
    for tier in cfg_ps["max_phases_by_period_days"]:
        if reporting_days <= tier["max_days"]:
            max_phases = tier["max_phases"]
            break

    if len(non_normal) <= max_phases:
        return segments

    # Merge: (a) adjacent same-condition first, (b) shortest into "Mixed activity"
    merged = []
    for s in segments:
        if merged and merged[-1]['type'] == s['type'] and s['type'] != 'normal':
            merged[-1]['days'] += s['days']
            merged[-1]['end_date'] = s['end_date']
        else:
            merged.append(dict(s))
    non_normal = [s for s in merged if s['type'] != 'normal']

    while len(non_normal) > max_phases:
        shortest = min(non_normal, key=lambda s: s['days'])
        shortest['type'] = '_mixed'
        shortest['label'] = cfg_ps["merge_label_for_overflow"]
        shortest['color'] = '#9CA3AF'
        # Re-merge adjacent _mixed segments
        re_merged = []
        for s in merged:
            if re_merged and re_merged[-1]['type'] == '_mixed' and s['type'] == '_mixed':
                re_merged[-1]['days'] += s['days']
                re_merged[-1]['end_date'] = s['end_date']
            else:
                re_merged.append(dict(s))
        merged = re_merged
        non_normal = [s for s in merged if s['type'] != 'normal']

    return merged


def render_status_timeline_bar(window_start, window_end, display_phases, content_width_inches, timeline_cell_style, recorded_dates=None, phase_number_map=None, phase_list_for_overlap=None, episode_day_map=None):
    import pandas as pd
    from reportlab.platypus import Table, TableStyle, Paragraph
    from reportlab.lib.colors import HexColor
    from reportlab.lib.units import inch
    from .config import settings

    cfg_ps = RENDER_CONFIG["phase_strip"]
    segments = build_status_timeline_segments(window_start, window_end, display_phases, recorded_dates=recorded_dates, episode_day_map=episode_day_map)
    reporting_days = compute_reporting_period_days(window_start, window_end)

    # Round 10 Fix 1: Empty strip fallback
    non_normal = [s for s in segments if s['type'] not in ('normal', 'no_data')]
    if len(non_normal) == 0:
        fallback = Paragraph(
            f"<i>{cfg_ps['empty_strip_fallback_text']}</i>", timeline_cell_style)
        bar_table = Table([[fallback]], colWidths=[content_width_inches * inch],
                          rowHeights=[settings.timeline_bar_height_inches * inch])
        bar_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('BACKGROUND', (0, 0), (0, 0), HexColor('#E5E7EB')),
        ]))
        return bar_table, segments

    # Round 10 Fix 1: Cap phases by period length
    # Skip in episode_hours mode — segments are narrow day-level slivers,
    # three-tier rendering handles width, numbering bounded by events table size.
    if settings.phase_strip_day_coloring_mode == "phase_window":
        segments = _cap_phases(segments, reporting_days)

    col_widths = [(s['days'] / reporting_days) * content_width_inches * inch for s in segments]

    # R12 Fix 4b: Select uniform label tier per phase_type.
    # For each type, find the narrowest same-type segment, and use the longest
    # label tier that fits that narrowest segment. Apply uniformly to all segs of that type.
    uniform_labels = {}
    for ptype in set(s['type'] for s in segments if s['type'] not in ('normal', 'no_data', '_mixed')):
        same_type_widths_in = [
            (s['days'] / reporting_days) * content_width_inches
            for s in segments if s['type'] == ptype
        ]
        if not same_type_widths_in:
            continue
        narrowest_in = min(same_type_widths_in)
        # Pick the widest label tier that fits the narrowest cell
        chosen = _get_phase_label_for_width(ptype, narrowest_in, PHASE_LABELS.get(ptype, ''))
        uniform_labels[ptype] = chosen

    # R14 E1: Resolve phase number via date-range overlap with pre-merge display_phases.
    # phase_number_map is keyed by phase index into display_phases.
    # For merged segments spanning multiple phases, pick the lowest number (highest priority).
    _overlap_phases = phase_list_for_overlap or []

    def _seg_number(seg):
        if not phase_number_map or not settings.phase_strip_show_numbers:
            return None
        if seg['type'] in ('normal', 'no_data', '_mixed'):
            return None
        seg_start = seg['start_date']
        seg_end = seg['end_date']
        seg_type = seg['type']
        best_num = None
        for idx, p in enumerate(_overlap_phases):
            if idx not in phase_number_map:
                continue
            # E1.1: Only match phases of the same condition type
            if p.get('type') != seg_type:
                continue
            p_start = pd.Timestamp(p.get('start_date', '')).normalize()
            p_end = pd.Timestamp(p.get('end_date', '')).normalize()
            if p_start <= seg_end and p_end >= seg_start:
                num = phase_number_map[idx]
                if best_num is None or num < best_num:
                    best_num = num
        return best_num

    # R14 E2: Narrow-segment styles (smaller font for tight fits)
    from reportlab.lib.styles import ParagraphStyle
    narrow_style = ParagraphStyle(
        'phase_narrow', parent=timeline_cell_style,
        fontSize=settings.phase_strip_narrow_font_size,
        leading=settings.phase_strip_narrow_font_size + 1,
    )
    indicator_style = ParagraphStyle(
        'phase_indicator', parent=timeline_cell_style,
        fontSize=settings.phase_strip_narrow_font_size,
        leading=settings.phase_strip_narrow_font_size + 1,
    )
    min_text_w = settings.phase_strip_min_text_width_inches
    narrow_max_w = 0.30  # between min_text_w and this, use narrow font
    indicator = settings.phase_strip_subthreshold_indicator

    cells = []
    for seg in segments:
        segment_width_inches = (seg['days'] / reporting_days) * content_width_inches
        if seg['type'] in ('normal', 'no_data'):
            cell_content = ''
        elif seg['type'] == '_mixed':
            # R21.B: mixed (overflow-merge) segments — render label if it fits,
            # else color band only. Was bullet placeholder pre-R21.B; now follows
            # the same "no bare bullet" rule as the typed-segment fallback.
            label = seg.get('label', cfg_ps["merge_label_for_overflow"])
            CHAR_WIDTH = 0.09
            if len(label) * CHAR_WIDTH <= segment_width_inches:
                cell_content = Paragraph(f"<b>{label}</b>", timeline_cell_style)
            else:
                cell_content = ''
        else:
            label = uniform_labels.get(seg['type'], '')
            num = _seg_number(seg)
            CHAR_WIDTH = 0.09
            nc = settings.phase_strip_number_color

            # R21.B: unified narrow-segment fallback — full label → (#N) → no glyph.
            # The bare bullet placeholder previously emitted in two places
            # (sub-threshold band, and narrow band when num is None) now drops
            # to "no glyph" instead. Sajol's Round 20 review flagged the
            # trailing bare-bullet on Wimberley FP as actively confusing now
            # that R20.B removed the legend hint that explained it.
            if segment_width_inches < 0.06:
                # Physically too narrow for any text — color band only
                cell_content = ''
            elif segment_width_inches < min_text_w:
                # Sub-threshold — color band only (was bullet placeholder pre-R21.B)
                cell_content = ''
            elif segment_width_inches < narrow_max_w:
                # Narrow — render "#N" cross-reference if available, else color
                # band only. R18 D: "#N" cross-references the events table row
                # so readers don't mis-read the number as an episode count.
                if num is not None:
                    cell_content = Paragraph(
                        f"<font color='{nc}'><b>#{num}</b></font>", narrow_style)
                else:
                    # No events-table row to cross-reference — color band only
                    # (was bullet placeholder pre-R21.B).
                    cell_content = ''
            else:
                # Normal width — full rendering cascade.
                # R18 D: full label renders as "{label} (#{N})" instead of "{N} {label}"
                # so the number is unambiguously a row reference, not a count.
                full_label = f"{label} (#{num})" if (num is not None and label) else None
                if full_label and (len(full_label) * CHAR_WIDTH <= segment_width_inches):
                    cell_content = Paragraph(
                        f"<b>{label}</b> <font color='{nc}'><b>(#{num})</b></font>",
                        timeline_cell_style)
                elif num is not None and label:
                    # Full "(#{N})" form doesn't fit — show "#N" alone
                    cell_content = Paragraph(
                        f"<font color='{nc}'><b>#{num}</b></font>", timeline_cell_style)
                elif num is not None:
                    cell_content = Paragraph(
                        f"<font color='{nc}'><b>#{num}</b></font>", timeline_cell_style)
                elif label and len(label) * CHAR_WIDTH <= segment_width_inches:
                    cell_content = Paragraph(f"<b>{label}</b>", timeline_cell_style)
                else:
                    # Label doesn't fit at full size — try abbreviation directly.
                    # R21.B: when even the abbreviation doesn't fit, fall through
                    # to color-band-only (was bullet placeholder pre-R21.B).
                    abbrev = settings.phase_strip_label_abbrev.get(seg['type'], '')
                    if abbrev and len(abbrev) * CHAR_WIDTH <= segment_width_inches:
                        cell_content = Paragraph(f"<b>{abbrev}</b>", timeline_cell_style)
                    else:
                        cell_content = ''
        cells.append(cell_content)

    bar_table = Table([cells], colWidths=col_widths, rowHeights=[settings.timeline_bar_height_inches * inch])
    style_commands = [
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.white),
    ]
    for i, seg in enumerate(segments):
        style_commands.append(('BACKGROUND', (i, 0), (i, 0), HexColor(seg['color'])))
    bar_table.setStyle(TableStyle(style_commands))
    return bar_table, segments


def render_timeline_date_axis(segments, reporting_days, content_width_inches, date_axis_style):
    """R12 Fix 3: Date axis scales format and interval to period length.

    Prevents vertical character stacking by choosing a format that fits the
    available cell width at every period length.
    """
    import pandas as pd
    from reportlab.platypus import Table, TableStyle, Paragraph
    from reportlab.lib.units import inch

    # R12 Fix 3: format and interval scale together with period length
    # R17 G: extended ≤ 90 cutoff to ≤ 95 to absorb the 90-day window's
    # inclusive-count off-by-one (best_end - best_start = 90 days → reporting_days
    # = 91). Without this, JB/Nancy/S(Chair)/TMiller 90DayPeriod reports fell into
    # the interval=30/%b branch and produced a 4-col axis whose trailing 1-day
    # cell ("Sep") was 0.08" wide — ReportLab returned INT_MAX row height and
    # crashed the page-2 layout.
    if reporting_days <= 14:
        interval = 1
        date_fmt = "%b %d"
    elif reporting_days <= 30:
        interval = 7
        date_fmt = "%b %d"
    elif reporting_days <= 95:
        interval = 14
        date_fmt = "%b %d"
    elif reporting_days <= 180:
        interval = 30
        date_fmt = "%b"
    else:
        interval = 60
        date_fmt = "%b %Y"

    # Estimated character width at 6pt italic font: ~0.045 inches/char
    CHAR_WIDTH_IN = 0.045
    MIN_LABEL_WIDTH_IN = 0.35

    current = segments[0]['start_date']
    end = segments[-1]['end_date']
    date_cells = []
    col_widths = []
    while current <= end:
        days_in_label = min(interval, (end - current).days + 1)
        label_width_in = (days_in_label / reporting_days) * content_width_inches
        label_text = current.strftime(date_fmt)
        label_px_in = len(label_text) * CHAR_WIDTH_IN

        # Skip label if cell too narrow for horizontal text (prevents char stacking)
        if label_width_in < max(MIN_LABEL_WIDTH_IN, label_px_in):
            date_cells.append(Paragraph("", date_axis_style))
        else:
            date_cells.append(Paragraph(f"<i>{label_text}</i>", date_axis_style))
        col_widths.append(label_width_in * inch)
        current += pd.Timedelta(days=days_in_label)

    date_axis_table = Table([date_cells], colWidths=col_widths)
    date_axis_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
    ]))
    return date_axis_table

PLAIN_NAMES = {
    'Severe bradycardia': 'Very Low Heart Rate',
    'Bradycardia': 'Low Heart Rate',
    'Tachycardia': 'High Heart Rate',
    'Elevated HR': 'Elevated Heart Rate',
    'Very High HR': 'Very High Heart Rate',
    'Tachypnea': 'Elevated Breathing',
}

def plain_name(condition_key):
    return PLAIN_NAMES.get(condition_key, condition_key)


def format_episode_date_phrase(ep_start_ts, ep_end_ts):
    """Return 'on [date]' for same-day episodes or 'beginning [date]' for multi-day."""
    import pandas as pd
    start_date = pd.Timestamp(ep_start_ts).normalize()
    end_date = pd.Timestamp(ep_end_ts).normalize()
    if start_date == end_date:
        return f"on {pd.Timestamp(ep_start_ts).strftime('%b %d')}"
    else:
        return f"beginning {pd.Timestamp(ep_start_ts).strftime('%b %d')}"

def build_key_findings(eps, daily_summary):
    import dateutil.parser
    from .config import settings
    findings = []
    # Note: build_key_findings (the legacy path) — leave unchanged below
    if eps:
        longest = None
        max_h = -1
        for e in eps:
            h = e.get('duration_hours', 0) if isinstance(e, dict) else getattr(e, 'duration_hours', 0)
            if h > max_h:
                max_h = h
                longest = e
        if longest and max_h > 0:
            cond = longest.get('condition', '') if isinstance(longest, dict) else getattr(longest, 'condition', '')
            cond = plain_name(cond)
            st_str = longest.get('start_time', '') if isinstance(longest, dict) else getattr(longest, 'start_time', '')
            dt_str = dateutil.parser.parse(str(st_str)).strftime('%b %d') if st_str else ''
            findings.append(f"Longest sustained event: {max_h:.1f}h {cond} on {dt_str}")

    coupled_count = 0
    for e in eps:
        c = e.get('cooccurrence', False) if isinstance(e, dict) else getattr(e, 'cooccurrence', False)
        if c: coupled_count += 1
    if coupled_count > 0:
        findings.append(f"Concurrent HR and breathing abnormalities: {coupled_count} episode(s)")
    
    if daily_summary is not None:
        hr_max = daily_summary['hr_max'].max() if 'hr_max' in daily_summary.columns else daily_summary['hr_avg'].max()
        hr_min = daily_summary['hr_min'].min() if 'hr_min' in daily_summary.columns else daily_summary['hr_avg'].min()
        if hr_max > settings.tachy_hr_avg:
            findings.append(f"Peak heart rate: {hr_max:.0f} bpm")
        if hr_min < settings.brady_hr_avg:
            findings.append(f"Minimum heart rate: {hr_min:.0f} bpm")
    return findings[:3]


def build_intelligent_key_findings(eps, daily_summary, trajectory=None, window_start=None, window_end=None, counts=None, patient_id=None):
    """FIX 37: Generate clinically meaningful pattern observations.

    Detects coupled, clustered, nocturnal, sustained, and variability patterns.
    Returns up to 3 findings, prioritized by clinical significance.
    """
    import logging
    import pandas as pd
    from .config import settings
    logger = logging.getLogger(__name__)
    # R12 Fix 9: Typed candidate list for deterministic priority ranking
    candidates = []  # each entry: {"type": str, "text": str}

    # Use reconciled total from counts when available, never len(eps) for display
    total_episodes = counts['display_episode_count'] if counts else len(eps)
    reporting_days = compute_reporting_period_days(window_start, window_end) if window_start and window_end else 0

    # Diagnostic logging
    logger.info(
        f"[PATTERN DETECTION DEBUG] len(eps)={len(eps)}, "
        f"counts.display_episode_count={counts.get('display_episode_count', 'N/A') if counts else 'N/A'}, "
        f"reporting_days={reporting_days}"
    )
    if counts and len(eps) != counts.get('display_episode_count', len(eps)):
        logger.error(
            f"EPISODE LIST MISMATCH: pattern detector received {len(eps)} episodes "
            f"but reconciled count says {counts['display_episode_count']}. "
            f"The wrong list is being passed to pattern detection."
        )

    # Pattern 1: R13 Fix 4 — Coupled pattern = temporal overlap of distinct conditions
    # Any two distinct condition types whose episodes overlap by >= min_overlap_hours
    # contribute to coupling signal. Not limited to bradycardia+tachypnea.
    po_cfg_coupled = RENDER_CONFIG["pattern_observations"]
    min_overlap_hours = po_cfg_coupled.get("coupled_min_overlap_hours", 2)
    min_overlap_count = po_cfg_coupled.get("coupled_min_overlap_count", 1)

    def _ep_field(e, name, default=None):
        return e.get(name, default) if isinstance(e, dict) else getattr(e, name, default)

    eps_with_times = []
    for e in eps:
        st = _ep_field(e, 'start_time', '')
        et = _ep_field(e, 'end_time', '')
        cond = _ep_field(e, 'condition', '')
        if st and et and cond:
            try:
                eps_with_times.append({
                    'condition': cond,
                    'start': pd.Timestamp(st),
                    'end': pd.Timestamp(et),
                })
            except Exception:
                pass
    eps_with_times.sort(key=lambda x: x['start'])

    overlap_pairs = []
    for i, a in enumerate(eps_with_times):
        for b in eps_with_times[i+1:]:
            if b['start'] > a['end']:
                break  # sorted — no further overlap possible
            if a['condition'] == b['condition']:
                continue
            ov_start = max(a['start'], b['start'])
            ov_end = min(a['end'], b['end'])
            ov_hours = (ov_end - ov_start).total_seconds() / 3600
            if ov_hours >= min_overlap_hours:
                overlap_pairs.append((a['condition'], b['condition'], ov_hours))

    if len(overlap_pairs) >= min_overlap_count:
        total_overlap_hours = int(sum(p[2] for p in overlap_pairs))
        # R12 Fix 9: Typed candidate for priority ranking
        candidates.append({"type": "coupled", "text": (
            f"<b>Coupled pattern:</b> {len(overlap_pairs)} cross-condition overlap(s) "
            f"({total_overlap_hours}h) across distinct condition types."
        )})

    # Pattern 2: Clustered vs distributed events
    # R13 Fix 3: Allow short-period intensity clustering (drop hard 14-day gate)
    if total_episodes >= 3 and reporting_days >= 3:
        # Collect ALL unique calendar days that any episode touches (not just start dates)
        unique_days_set = set()
        hours_by_date = {}
        for e in eps:
            st_str = e.get('start_time', '') if isinstance(e, dict) else getattr(e, 'start_time', '')
            et_str = e.get('end_time', '') if isinstance(e, dict) else getattr(e, 'end_time', '')
            dur_h = e.get('duration_hours', 0) if isinstance(e, dict) else getattr(e, 'duration_hours', 0)
            if st_str:
                ep_start = pd.Timestamp(st_str).normalize()
                ep_end = pd.Timestamp(et_str).normalize() if et_str else ep_start
                current = ep_start
                while current <= ep_end:
                    unique_days_set.add(current)
                    current += pd.Timedelta(days=1)
                # Track hours per start date for fragmentation detection
                hours_by_date[ep_start] = hours_by_date.get(ep_start, 0) + dur_h

        unique_episode_days = len(unique_days_set)

        # Sanity check — cannot have more episode days than reporting days
        if unique_episode_days > reporting_days:
            logger.error(
                f"Pattern math impossible: {unique_episode_days} episode days "
                f"in {reporting_days} day window. Data integrity issue upstream."
            )
            unique_episode_days = min(unique_episode_days, reporting_days)

        # Episode fragmentation diagnostic — detect upstream episode detection bugs
        eps_per_day = total_episodes / unique_episode_days if unique_episode_days > 0 else 0
        max_hours_one_day = max(hours_by_date.values()) if hours_by_date else 0
        if total_episodes >= 100:
            unique_start_dates = len(set(
                pd.Timestamp(e.get('start_time', '') if isinstance(e, dict) else getattr(e, 'start_time', '')).normalize()
                for e in eps if (e.get('start_time', '') if isinstance(e, dict) else getattr(e, 'start_time', ''))
            ))
            logger.info(
                f"Episode distribution: {total_episodes} episodes, "
                f"{unique_start_dates} unique start dates, "
                f"max hours/day = {max_hours_one_day:.0f}"
            )
            if max_hours_one_day > 24:
                logger.error(
                    f"EPISODE DETECTION BUG: {max_hours_one_day:.0f}h of episodes on a single "
                    f"day (max possible is 24h). Episode detector is fragmenting."
                )

        # Suppress distribution callout if episode-to-day ratio is implausible
        # (>15 episodes per day average suggests fragmented detection, not real clusters)
        # R13 Fix 3: Raise fragmentation ceiling to allow dense short-period patterns
        if eps_per_day > 15:
            logger.warning(
                f"Suppressing distribution callout: {total_episodes} episodes / "
                f"{unique_episode_days} days = {eps_per_day:.1f} eps/day (implausible)"
            )
        else:
            po_cfg = RENDER_CONFIG["pattern_observations"]
            cluster_ratio = unique_episode_days / reporting_days if reporting_days > 0 else 0
            events_per_active_day = eps_per_day

            continuous_min = po_cfg.get("continuous_min_ratio", 0.70)
            cluster_temporal_max = po_cfg.get("clustered_temporal_max_ratio", 0.30)
            cluster_intensity_min = po_cfg.get("clustered_intensity_min", 3.0)

            # R13 Fix 3: Clustered fires on EITHER temporal concentration OR intensity
            temporal_clustered = cluster_ratio <= cluster_temporal_max and reporting_days >= 14
            intensity_clustered = events_per_active_day >= cluster_intensity_min

            if cluster_ratio >= continuous_min:
                candidates.append({"type": "continuous", "text": (
                    f"<b>Continuous pattern:</b> Episodic events on "
                    f"{unique_episode_days} of {reporting_days} days ({int(cluster_ratio*100)}%)."
                )})
            elif temporal_clustered or intensity_clustered:
                if intensity_clustered and not temporal_clustered:
                    text = (
                        f"<b>Clustered pattern:</b> {events_per_active_day:.1f} events per "
                        f"active day across {unique_episode_days} days."
                    )
                else:
                    text = (
                        f"<b>Clustered pattern:</b> Episodic events on "
                        f"{unique_episode_days} of {reporting_days} days ({int(cluster_ratio*100)}%)."
                    )
                candidates.append({"type": "clustered", "text": text})

    # Pattern 3: Time of day pattern — Round 10 Fix 4: Nocturnal heuristic guard
    nh_cfg = RENDER_CONFIG["nocturnal_heuristic"]
    night_start, night_end = nh_cfg["night_hour_range"]
    max_dur_for_nocturnal = nh_cfg["exclude_episodes_longer_than_hours"]
    min_eps_for_pattern = nh_cfg["min_episodes_for_pattern_claim"]
    min_frac_for_pattern = nh_cfg["min_fraction_for_pattern_claim"]

    # Filter out episodes too long to have a meaningful "time of day"
    eligible_for_nocturnal = []
    for e in eps:
        dur = e.get('duration_hours', 0) if isinstance(e, dict) else getattr(e, 'duration_hours', 0)
        if dur <= max_dur_for_nocturnal:
            eligible_for_nocturnal.append(e)

    # R11 Fix 7: Diagnostic logging for nocturnal heuristic
    filtered_out = total_episodes - len(eligible_for_nocturnal)
    logger.info(
        f"[NOCTURNAL DIAG] total={total_episodes}, eligible={len(eligible_for_nocturnal)}, "
        f"filtered_out_long={filtered_out}, min_required={min_eps_for_pattern}"
    )

    if len(eligible_for_nocturnal) >= min_eps_for_pattern:
        night_episodes = 0
        for e in eligible_for_nocturnal:
            st_str = e.get('start_time', '') if isinstance(e, dict) else getattr(e, 'start_time', '')
            if st_str:
                h = pd.Timestamp(st_str).hour
                if h >= night_start or h < night_end:
                    night_episodes += 1
        night_ratio = night_episodes / len(eligible_for_nocturnal)
        # R12 Fix 9: Typed candidates
        if night_ratio >= min_frac_for_pattern:
            candidates.append({"type": "nocturnal", "text": (
                f"<b>Nocturnal pattern:</b> {night_episodes} of {len(eligible_for_nocturnal)} "
                f"eligible episodes during evening or night hours."
            )})
        elif night_ratio <= 0.2:
            candidates.append({"type": "daytime", "text": (
                f"<b>Daytime pattern:</b> Episodes concentrated during waking hours."
            )})

    # Pattern 4: Sustained event prominence
    if eps:
        longest = None
        max_h = 0
        for e in eps:
            h = e.get('duration_hours', 0) if isinstance(e, dict) else getattr(e, 'duration_hours', 0)
            if h > max_h:
                max_h = h
                longest = e
        if longest and max_h >= 6:
            cond = longest.get('condition', '') if isinstance(longest, dict) else getattr(longest, 'condition', '')
            cond = plain_name(cond)
            st_str = longest.get('start_time', '') if isinstance(longest, dict) else getattr(longest, 'start_time', '')
            et_str = longest.get('end_time', '') if isinstance(longest, dict) else getattr(longest, 'end_time', '')
            # Verify duration matches timestamps
            if st_str and et_str:
                computed_h = (pd.Timestamp(et_str) - pd.Timestamp(st_str)).total_seconds() / 3600 + 1
                display_h = max_h if abs(computed_h - max_h) <= 2 else int(computed_h)
            else:
                display_h = max_h
            date_display = format_episode_date_phrase(st_str, et_str) if st_str and et_str else f"on {pd.Timestamp(st_str).strftime('%b %d')}" if st_str else ''
            # R12 Fix 9: Typed candidate
            candidates.append({"type": "sustained_finding", "text": (
                f"<b>Sustained finding:</b> {display_h}h continuous "
                f"{cond} {date_display}."
            )})

    # Pattern 5: Variability indicator (HR)
    # R13 Fix 5: Use unified gate shared with chart and body text
    if daily_summary is not None and len(daily_summary) >= 5:
        from .narrative_ai import should_render_spread_annotation as _gate
        hr_p5 = daily_summary['hr_avg'].quantile(0.05)
        hr_p95 = daily_summary['hr_avg'].quantile(0.95)
        spread = hr_p95 - hr_p5
        # Sample hours from df row count (df has hourly rows)
        sample_hours = len(daily_summary) if daily_summary is not None else 0
        if _gate(int(sample_hours), float(hr_p5), float(hr_p95), metric="hr"):
            # R12 Fix 9: Typed candidate
            candidates.append({"type": "high_variability", "text": (
                f"<b>High variability:</b> HR spread {int(spread)} bpm "
                f"({int(hr_p5)} to {int(hr_p95)})."
            )})

    # R19 B: Pattern 5b — RR variability indicator. Threshold is 10 brpm (vs 20
    # for HR) per the same metric-specific gate. Sajol May 4 review asked why
    # RR spread observation never triggered with the unified 20-threshold.
    if daily_summary is not None and len(daily_summary) >= 5 and 'rr_avg' in daily_summary.columns:
        from .narrative_ai import should_render_spread_annotation as _gate
        rr_p5 = daily_summary['rr_avg'].quantile(0.05)
        rr_p95 = daily_summary['rr_avg'].quantile(0.95)
        rr_spread = rr_p95 - rr_p5
        sample_hours = len(daily_summary) if daily_summary is not None else 0
        if _gate(int(sample_hours), float(rr_p5), float(rr_p95), metric="rr"):
            candidates.append({"type": "high_rr_variability", "text": (
                f"<b>Breathing variability:</b> RR spread {int(rr_spread)} brpm "
                f"({int(rr_p5)} to {int(rr_p95)})."
            )})

    # Pattern 6: Trajectory — R12 Fix 7: single canonical phrase, no variation
    po_cfg = RENDER_CONFIG["pattern_observations"]
    if trajectory and not trajectory.get('insufficient'):
        direction = trajectory.get('direction', '')
        delta = trajectory.get('delta_episodes', 0)
        prior = trajectory.get('prior', {})
        prior_eps_count = prior.get('episode_count', 0)
        current_eps_count = trajectory.get('current', {}).get('episode_count', 0)

        skip = False
        if direction == 'worsening' and delta < po_cfg.get("skip_worsening_if_delta_under", 5):
            skip = True
        if prior_eps_count == 0:
            skip = True

        if not skip and direction == 'worsening':
            candidates.append({"type": "worsening_trajectory", "text": (
                f"<b>Worsening trajectory:</b> {prior_eps_count} → {current_eps_count} "
                f"events vs prior period."
            )})
        elif not skip and direction == 'improving':
            candidates.append({"type": "improving_trajectory", "text": (
                f"<b>Improving trajectory:</b> {prior_eps_count} → {current_eps_count} "
                f"events vs prior period."
            )})

    # R11 Fix 6: Pattern 7 — Monitoring decline (promoted to standard observation)
    md_cfg = RENDER_CONFIG.get("monitoring_decline", {})
    min_pct_drop = md_cfg.get("min_pct_drop", 30)
    min_period_days = md_cfg.get("min_period_days", 14)
    if daily_summary is not None and len(daily_summary) >= min_period_days:
        try:
            # Compute daily coverage hours from the dataframe
            if 'hr_avg' in daily_summary.columns:
                daily_hours = daily_summary.groupby(daily_summary.index if daily_summary.index.name == 'date' else daily_summary['date'] if 'date' in daily_summary.columns else daily_summary.index).size()
                if len(daily_hours) >= min_period_days:
                    midpoint = len(daily_hours) // 2
                    first_half_avg = daily_hours.iloc[:midpoint].mean()
                    second_half_avg = daily_hours.iloc[midpoint:].mean()
                    if first_half_avg > 0:
                        pct_drop = ((first_half_avg - second_half_avg) / first_half_avg) * 100
                        if pct_drop >= min_pct_drop:
                            candidates.append({"type": "monitoring_decline", "text": (
                                f"<b>Monitoring decline:</b> Daily hours {first_half_avg:.1f} → "
                                f"{second_half_avg:.1f} ({int(pct_drop)}% reduction)."
                            )})
        except Exception:
            pass

    # R12 Fix 9: Deterministic priority-based selection of top N candidates
    po_scores = RENDER_CONFIG["pattern_observations"].get("priority_scores", {})
    max_obs = RENDER_CONFIG["pattern_observations"].get("max_observations", 3)
    ranked = sorted(
        candidates,
        key=lambda c: (-po_scores.get(c["type"], 0), c["type"])
    )
    return [c["text"] for c in ranked[:max_obs]]


def generate_pdf(report: ReportResponse, df=None, episodes=None,
                 one_page_only: bool = False) -> bytes:
    """Build the patient PDF report.

    R15 E1: when ``one_page_only`` is True, the renderer stops after page 1
    content (no PageBreak, no histogram/activity-chart page). Used for the
    PAMHealth study packaging variant.
    """
    buf = io.BytesIO()
    st = _styles()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.4 * inch, bottomMargin=0.35 * inch,
    )
    elements = []
    page_w = letter[0] - 1.0 * inch

    pos_comp = _v(report, "positional_comparison")
    is_multisensor = pos_comp and len(_v(pos_comp, "rows", [])) > 1

    # PAGE 1 ──────────────────────────────────────────────────────────────────
    elements.extend(_build_header(report, st, page_w))

    # Status Timeline: display phases only (skip normal)
    phases = _v(report, "phases", [])
    display_phases = [p for p in phases if PHASE_LABELS.get(_v(p, 'type', 'normal')) is not None]

    # R14 E1: Pre-compute phase numbering keyed by phase index.
    # phase_table_rows are built 1:1 with display_phases (same order, same index).
    # After priority sort, the top N rows map back to their original index.
    # phase_number_map: {original_phase_index → display_number}
    phase_number_map = {}  # phase_index → display number
    narrative_pre = _v(report, "narrative") if isinstance(_v(report, "narrative"), dict) else {}
    _ptr = _v(narrative_pre, 'phase_table_rows', []) if narrative_pre else []
    if _ptr and settings.phase_strip_show_numbers:
        evt_cfg_pre = RENDER_CONFIG["events_table"]
        priority_order_pre = evt_cfg_pre.get("priority_order", [])
        max_rows_pre = evt_cfg_pre.get("max_rows", 6)

        # Tag each row with its original index before sorting
        indexed_rows = [(i, row) for i, row in enumerate(_ptr)]

        def _pre_sort_key(item):
            _, row = item
            cat = row.get('category', '')
            pt = None
            for k, v in PHASE_LABELS.items():
                if v == cat:
                    pt = k
                    break
            pri = priority_order_pre.index(pt) if pt and pt in priority_order_pre else 999
            return (pri, -row.get('longest_continuous', 0))

        _sorted_pre = sorted(indexed_rows, key=_pre_sort_key)
        for num_idx, (orig_idx, row) in enumerate(_sorted_pre[:max_rows_pre], start=1):
            phase_number_map[orig_idx] = num_idx

    # FIX 6: Episode timeline bar — red bars on gray for episodic events
    all_eps = _v(report, "episodes", [])
    if all_eps:
        try:
            ws_tmp = _v(report, 'window_start')
            we_tmp = _v(report, 'window_end')
            days_count = compute_reporting_period_days(ws_tmp, we_tmp)
        except Exception:
            days_count = 7
        # R24.2 — title row carries HR / Breathing color index right-aligned.
        elements.append(_render_status_heading_with_index(report, days_count, page_w, st))
        try:
            from .charts import chart_episode_timeline_for_pdf
            ws = _v(report, 'window_start', '')
            we = _v(report, 'window_end', '')
            ep_timeline = chart_episode_timeline_for_pdf(all_eps, ws, we)
            elements.append(Image(io.BytesIO(ep_timeline), width=page_w, height=0.45 * inch))
        except Exception:
            pass
        elements.append(Spacer(1, 4))

    # Phase timeline bar — three cases:
    # 1. display_phases exist → colored segment timeline
    # 2. Episodes exist but no display phases → amber "scattered events" bar
    # 3. No episodes at all → green "Within normal range" bar
    import pandas as pd
    ws_v = _v(report, 'window_start')
    we_v = _v(report, 'window_end')
    if pd.notna(ws_v) and pd.notna(we_v) and display_phases:
        # Case 1: Normal segmented timeline
        ws_tmp = pd.Timestamp(ws_v).normalize()
        we_tmp = pd.Timestamp(we_v).normalize()
        # R14 B2: Build recorded_dates set from df to distinguish no-data from no-episodes
        recorded_dates = None
        if df is not None and 'timestamp' in df.columns:
            recorded_dates = set(df['timestamp'].dt.normalize().unique())

        # R14 F2: Build per-day episode map for episode_hours coloring mode
        episode_day_map = _build_episode_day_map(episodes) if episodes else None

        # Strip width reads from the same shared symbol as the candlestick
        # chart Image directly below it, so the two always share a baseline
        # on the same page. Previously set to content_width * 0.5 (R22.D),
        # which left the strip visibly narrower than the chart.
        strip_width = settings.plot_width_inches
        bar_table, segments = render_status_timeline_bar(
            ws_tmp, we_tmp, display_phases,
            strip_width, st["phase_label"],
            recorded_dates=recorded_dates,
            phase_number_map=phase_number_map,
            phase_list_for_overlap=display_phases,
            episode_day_map=episode_day_map,
        )
        elements.append(bar_table)

        date_style = ParagraphStyle('date_ax', parent=st['legend'], alignment=0, fontSize=6)
        date_axis = render_timeline_date_axis(
            segments,
            compute_reporting_period_days(ws_tmp, we_tmp),
            strip_width,
            date_style
        )
        elements.append(date_axis)
        elements.append(Spacer(1, 4))

    elif all_eps:
        # Case 2: Episodes exist but no sustained phases — amber bar
        ep_count = len(all_eps)
        amber_cell = Paragraph(
            f"<i>{ep_count} episodic event(s) detected without sustained phases</i>",
            st["phase_label"]
        )
        amber_tbl = Table([[amber_cell]], colWidths=[page_w])
        amber_tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("BACKGROUND", (0, 0), (0, 0), _hex("#FEF3C7")),
            ("BOX", (0, 0), (-1, -1), 0.5, _hex("#F59E0B")),
        ]))
        elements.append(amber_tbl)
        elements.append(Spacer(1, 4))

    else:
        # Case 3: No episodes at all — green bar
        try:
            days_count = compute_reporting_period_days(_v(report, 'window_start'), _v(report, 'window_end'))
        except Exception:
            days_count = 7
        # R24.2 — title row carries HR / Breathing color index right-aligned.
        elements.append(_render_status_heading_with_index(report, days_count, page_w, st))
        green_cell = Paragraph("<b>Within normal range</b>", st["phase_label"])
        green_tbl = Table([[green_cell]], colWidths=[page_w])
        green_tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("BACKGROUND", (0, 0), (0, 0), _hex("#10B981")),
        ]))
        elements.append(green_tbl)
        elements.append(Spacer(1, 4))

    # R14 H1: Phase strip index legend — two-line layout with short labels
    if settings.phase_strip_index_enabled and display_phases:
        _idx_fs = settings.phase_strip_index_font_size
        _idx_tc = settings.phase_strip_index_text_color
        _idx_sw = settings.phase_strip_index_swatch_size_pt
        _color_map = {
            "hr": settings.phase_strip_color_by_condition_type.get("hr", "#3B82F6"),
            "rr": settings.phase_strip_color_by_condition_type.get("rr", "#F97316"),
            "no_episode": settings.phase_strip_no_episode_color,
            "no_data": settings.phase_strip_no_data_color,
        }
        _idx_style = ParagraphStyle('strip_idx', parent=st['legend'],
                                     fontSize=_idx_fs, leading=_idx_fs + 2,
                                     textColor=_hex(_idx_tc))

        # R14 H1: Single-line legend with short labels (no wrapping)
        parts = []
        for item in settings.phase_strip_index_line1:
            sc = _color_map.get(item["swatch_color"], "#CCCCCC")
            has_border = item.get("border", False)
            marker = f'<font color="#999999">\u25a1</font>' if has_border else f'<font color="{sc}">\u25a0</font>'
            parts.append(f'{marker} <i>{_html.escape(item["label"])}</i>')
        for item in settings.phase_strip_index_line2:
            parts.append(f'<b>{_html.escape(item["symbol"])}</b> <i>{_html.escape(item["label"])}</i>')

        # R15 C2: Strip index legend MOVED — built but not appended here.
        # Now rendered after the trends chart by _make_strip_index_legend below.
        _ = parts

    # R22.D — per-patient summary block. Replaces the four-paragraph
    # Section 1 ("Episodic Burden", "Trend", "Clinical Guidance", "Trajectory")
    # with a compact metrics table + a single auto-generated "Major Findings"
    # line. Sajol's May 5 review: paragraph form was "so much it takes a
    # long time to read"; the table is the same information in a faster-to-
    # scan layout.
    narrative = _v(report, "narrative") or "No narrative available."

    if isinstance(narrative, dict):
        from .batch_summary import build_findings_text, format_episodes_per_day

        counts = _v(narrative, 'counts', {}) or {}
        total_episodes = counts.get('total_episodes', 0) if isinstance(counts, dict) else 0
        total_hours = counts.get('total_hours', 0) if isinstance(counts, dict) else 0
        if not total_episodes:
            rollups_obj = _v(report, 'episode_rollups', None)
            total_episodes = _v(rollups_obj, 'total_events', 0) or 0

        try:
            ws_for_days = _v(report, 'window_start')
            we_for_days = _v(report, 'window_end')
            period_days = compute_reporting_period_days(ws_for_days, we_for_days) or 1
        except Exception:
            period_days = 1

        dominant_pt = _v(narrative, 'events_table_row_1_phase_type', None)
        if not dominant_pt:
            ptr_for_dom = _v(narrative, 'phase_table_rows', [])
            for r in ptr_for_dom:
                cat = r.get('category', '') if isinstance(r, dict) else ''
                for k, v in PHASE_LABELS.items():
                    if v == cat:
                        dominant_pt = k
                        break
                if dominant_pt:
                    break

        burden_label = PHASE_LABELS.get(dominant_pt) if dominant_pt else None
        if not burden_label:
            burden_label = "—"

        coverage_pct_val = ""
        try:
            dq = _v(report, 'data_quality', None)
            recorded_h = _v(dq, 'total_hours', None)
            expected_h = _v(dq, 'expected_hours', None)
            if recorded_h is not None and expected_h:
                coverage_pct_val = f"{int(round(min(100.0, 100.0 * recorded_h / expected_h)))}%"
        except Exception:
            coverage_pct_val = ""
        if not coverage_pct_val:
            coverage_pct_val = _v(report, 'coverage_summary', '') or "—"

        epd_str = format_episodes_per_day(total_episodes, period_days)

        metrics_header = [
            Paragraph("<b>Episodic Events</b>", st["table_header"]),
            Paragraph("<b>Total Hours</b>", st["table_header"]),
            Paragraph("<b>Highest Burden</b>", st["table_header"]),
            Paragraph("<b>Episodes/day</b>", st["table_header"]),
            Paragraph("<b>Coverage</b>", st["table_header"]),
        ]
        metrics_row = [
            Paragraph(str(total_episodes), st["table_cell"]),
            Paragraph(f"{int(round(total_hours))}h", st["table_cell"]),
            Paragraph(_html.escape(str(burden_label)), st["table_cell"]),
            Paragraph(epd_str, st["table_cell"]),
            Paragraph(_html.escape(str(coverage_pct_val)), st["table_cell"]),
        ]
        metrics_widths = [
            page_w * 0.18, page_w * 0.16, page_w * 0.26,
            page_w * 0.18, page_w * 0.22,
        ]
        metrics_tbl = Table([metrics_header, metrics_row], colWidths=metrics_widths)
        metrics_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), _HEADER_BG),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('GRID', (0, 0), (-1, -1), 0.5, _BORDER),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        elements.append(metrics_tbl)
        elements.append(Spacer(1, 4))

        hr_summ = _v(report, 'hr_summaries', None)
        rr_summ = _v(report, 'rr_summaries', None)
        # R23.A — prefer condition-window avg from narrative dict so the
        # Major Findings parenthetical stays consistent with the parent
        # "Sustained [tier]" claim. Fall back to overall mean if the narrative
        # didn't surface a scoped avg (green patient, or no dominant row).
        scoped_hr_avg = _v(narrative, 'findings_hr_avg', None) if isinstance(narrative, dict) else None
        scoped_rr_avg = _v(narrative, 'findings_rr_avg', None) if isinstance(narrative, dict) else None
        findings_text = build_findings_text(
            triage=_v(report, 'triage', 'GREEN'),
            dominant_phase_type=dominant_pt,
            hr_avg=scoped_hr_avg if scoped_hr_avg is not None else _v(hr_summ, 'mean', None),
            peak_hr=_v(hr_summ, 'max', None),
            min_hr=_v(hr_summ, 'min', None),
            rr_avg=scoped_rr_avg if scoped_rr_avg is not None else _v(rr_summ, 'mean', None),
            peak_rr=_v(rr_summ, 'max', None),
        )
        elements.append(Paragraph(
            f"<b>Major Findings:</b> {findings_text}",
            st["body"],
        ))

        phase_table_rows = _v(narrative, 'phase_table_rows', [])
        if phase_table_rows:
            elements.append(Spacer(1, 6))
            # Round 10 Fix 3: Two clear duration columns
            # Round 11 Fix 2a: Cap rows at max_rows with overflow indicator
            evt_cfg = RENDER_CONFIG["events_table"]
            max_rows = evt_cfg.get("max_rows", 8)
            priority_order = evt_cfg.get("priority_order", [])

            # R12 Fix 6: Three-key deterministic sort
            # (priority_class ASC, longest_continuous DESC, start_date ASC)
            def _row_sort_key(row):
                cat = row.get('category', '')
                phase_type = None
                for pt_key, pt_label in PHASE_LABELS.items():
                    if pt_label == cat:
                        phase_type = pt_key
                        break
                pri = priority_order.index(phase_type) if phase_type and phase_type in priority_order else 999
                # Parse date from "Mar 15" or "Mar 15 to Mar 22" format
                date_str = row.get('date', '')
                try:
                    first_date = date_str.split(' to ')[0].strip() if date_str else ''
                    # Add a fixed year so parse works
                    sort_date = pd.Timestamp(f"{first_date} 2000") if first_date else pd.Timestamp('2099-01-01')
                except Exception:
                    sort_date = pd.Timestamp('2099-01-01')
                return (pri, -row.get('longest_continuous', row.get('sustained_hours', 0)), sort_date)

            sorted_rows = sorted(phase_table_rows, key=_row_sort_key)
            # R18 N2: brief rows (R18 C3 aggregations) bypass the max_rows cap.
            # Capped at MAX_BRIEF_VISIBLE=1 — the single visible brief row is
            # the highest-priority condition by events_table priority_order so
            # the most clinically alarming brief signal surfaces. Remaining
            # briefs fall to overflow footnote.
            #
            # Fallback 90DayPeriod reports (under-90-day patients whose 90DP is
            # content-equivalent to their FullPeriod) suppress brief rows
            # entirely — those rows render in their FullPeriod report already,
            # and the fallback header note consumes the remaining page-1
            # vertical capacity. Without this suppression, PHolst 90DP and
            # RSanchez 90DP spill to page 3.
            TOTAL_VISIBLE_CAP = 7
            MAX_BRIEF_VISIBLE = 1
            real_rows = [r for r in sorted_rows if "(brief)" not in r.get('category', '')]
            brief_rows = [r for r in sorted_rows if "(brief)" in r.get('category', '')]
            if _v(report, "is_fallback_90d"):
                brief_rows = []
            else:
                def _brief_priority(r):
                    pt = r.get('brief_phase_type')
                    return priority_order.index(pt) if pt and pt in priority_order else 999
                brief_rows = sorted(brief_rows, key=_brief_priority)
            real_visible = real_rows[:max_rows]
            brief_capacity = min(MAX_BRIEF_VISIBLE,
                                 max(0, TOTAL_VISIBLE_CAP - len(real_visible)))
            brief_visible = brief_rows[:brief_capacity]
            display_rows = real_visible + brief_visible
            overflow_rows = real_rows[max_rows:] + brief_rows[brief_capacity:]
            
            num_events_table_rows = len(display_rows)

            table_columns = evt_cfg.get("columns", [])
            if not table_columns:
                raise ValueError("Missing 'columns' in events_table config")
            
            header_row = []
            for col in table_columns:
                header_row.append(Paragraph(f'<b>{col["label"]}</b>', st["table_header"]))
            pt_data = [header_row]

            for row_num, row in enumerate(display_rows, start=1):
                lc = row.get('longest_continuous', row.get('sustained_hours', 0))
                th = row.get('total_hours', row.get('sustained_hours', 0))
                lc_str = row.get('longest_continuous_str', f"{lc}h")
                th_str = row.get('total_hours_str', f"{th}h")
                if lc >= settings.sustained_bold_threshold_hours:
                    lc_p = Paragraph(f"<b>{lc_str}</b>", st["table_cell"])
                else:
                    lc_p = Paragraph(lc_str, st["table_cell"])
                if th >= settings.sustained_bold_threshold_hours:
                    th_p = Paragraph(f"<b>{th_str}</b>", st["table_cell"])
                else:
                    th_p = Paragraph(th_str, st["table_cell"])

                row_cells = []
                for col in table_columns:
                    k = col["key"]
                    if k == "number":
                        # R14 C2: Phase number column — uses same numbering as strip
                        row_cells.append(Paragraph(f"<b>{row_num}</b>", st["table_cell"]))
                    elif k == "category":
                        row_cells.append(Paragraph(row.get('category', ''), st["table_cell"]))
                    elif k == "peak":
                        row_cells.append(Paragraph(row.get('peak', ''), st["table_cell"]))
                    elif k == "longest_continuous":
                        row_cells.append(lc_p)
                    elif k == "total_hours":
                        row_cells.append(th_p)
                    elif k == "episodes_per_day":
                        # R22.D — episodes-per-day for this row. Same "0"/"<1"/int
                        # rule as the batch summary cell so the surfaces agree.
                        from .batch_summary import format_episodes_per_day
                        epd = format_episodes_per_day(
                            row.get('episodes', 0),
                            row.get('period_days', 0),
                        )
                        row_cells.append(Paragraph(epd, st["table_cell"]))
                    elif k == "average":
                        row_cells.append(Paragraph(row.get('average', ''), st["table_cell"]))
                    elif k == "date":
                        row_cells.append(Paragraph(row.get('date', ''), st["table_cell"]))
                    else:
                        row_cells.append(Paragraph("", st["table_cell"]))
                pt_data.append(row_cells)
            
            # Create Table with widths defined in config
            base_width = 7.0 * inch  # full page minus margins
            widths = [col["width"] * base_width for col in table_columns]
            pt = Table(pt_data, colWidths=widths)
            pt.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), _HEADER_BG),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('GRID', (0, 0), (-1, -1), 0.5, _BORDER),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, _hex("#F9FAFB")]),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ]))
            elements.append(pt)
            # R11 Fix 2a: Overflow line
            if overflow_rows:
                overflow_conditions = sorted(set(r.get('category', '') for r in overflow_rows))
                overflow_text = evt_cfg.get("overflow_template", "+ {n} additional conditions").format(
                    n=len(overflow_rows), conditions=", ".join(overflow_conditions))
                elements.append(Paragraph(f"<i>{_html.escape(overflow_text)}</i>", st["body"]))
            # R22.B: physiologic-clipping footnote removed. RR is no longer
            # clipped (Sprint A noise filter handles bad data); HR clipping
            # only fires for extreme sensor garbage that does not occur in the
            # current cohort. If an HR value ever does clip in future data,
            # the asterisk in the cell will be visible without a footnote
            # gloss — surface it then.

        closing = _v(narrative, 'closing', '')
        if closing:
            # R11 Fix 2: Truncate closing to max 2 sentences for page fit
            sentences = [s.strip() for s in closing.split('. ') if s.strip()]
            if len(sentences) > 2:
                closing = '. '.join(sentences[:2]) + '.'
            elements.append(Paragraph(_html.escape(closing), st["body"]))
    else:
        elements.append(Paragraph(_html.escape(str(narrative)), st["body"]))

    # R11 Fix 2: Two-page hard constraint — no 3-page layout, ever.
    is_long_full_period = False
    total_pages = 2

    # Pre-compute findings DATA and candlestick bytes (not ReportLab elements)
    findings_data = []      # list of finding strings
    candle_bytes = None      # raw PNG bytes
    candle_strategy = 'daily'
    episode_counts = None
    if df is not None:
        trajectory_data = _v(report, 'trajectory', None)
        window_start = _v(report, 'window_start')
        window_end = _v(report, 'window_end')
        from .narrative_ai import reconcile_counts as _reconcile_counts
        ep_objects = _v(report, 'episodes', [])
        try:
            episode_counts = _reconcile_counts(ep_objects, display_phases)
        except Exception:
            episode_counts = None
        findings_data = build_intelligent_key_findings(
            all_eps, df, trajectory=trajectory_data,
            window_start=window_start, window_end=window_end,
            counts=episode_counts,
            patient_id=_v(report, 'patient_id')
        )

        try:
            ws = pd.Timestamp(_v(report, 'window_start')).normalize()
            we = pd.Timestamp(_v(report, 'window_end')).normalize()
            reporting_days = compute_reporting_period_days(ws, we)
            candle_strategy = 'weekly' if reporting_days > settings.candlestick_daily_max_days else 'daily'
        except Exception:
            candle_strategy = 'daily'

        candle_bytes = generate_candlestick_for_pdf(df, all_eps, phases=phases, window_start=_v(report, 'window_start'), window_end=_v(report, 'window_end'))

    def _make_findings_elements():
        """Create fresh ReportLab elements for pattern observations."""
        elems = []
        if findings_data:
            fs = ParagraphStyle(
                'KeyFindingsBox', parent=st['body'],
                fontSize=8, leading=11, leftIndent=8, rightIndent=8,
                spaceBefore=4, spaceAfter=4,
                backColor=_hex('#F8F9FA'),
                borderColor=_hex('#DDDDDD'),
                borderWidth=0.5, borderPadding=6,
            )
            text = "<b>Clinical Pattern Observations:</b><br/>"
            for i, finding in enumerate(findings_data, 1):
                text += f"{i}. {finding}<br/>"
            elems.append(Paragraph(text, fs))
            elems.append(Spacer(1, 6))
        return elems

    def _make_candlestick_elements():
        """Create fresh ReportLab elements for candlestick chart."""
        elems = []
        if candle_bytes is not None:
            # R11: reduced height for weekly to fit 2-page constraint.
            # R15 E1: one_page_only also gets the smaller height to keep the
            # study packaging report on a single page even for high-event patients.
            if candle_strategy == 'weekly':
                c_height = min(settings.candlestick_long_period_height_inches, 2.5)
            elif one_page_only:
                c_height = min(settings.candlestick_height_inches, 2.5)
            else:
                c_height = settings.candlestick_height_inches
            elems.append(Image(io.BytesIO(candle_bytes), width=settings.plot_width_inches * inch, height=c_height * inch))
            if candle_strategy == 'weekly':
                elems.append(Paragraph("<i>Weekly bars; color = episode burden. Badges = episodic hours.</i>", st["legend"]))
            else:
                elems.append(Paragraph("<i>Red bars indicate days with detected episodic events.</i>", st["legend"]))
        return elems

    def _make_strip_index_legend():
        """R15 C2: Phase strip index legend, rendered after the trends chart on page 1.
        Returns a list of ReportLab elements (empty if disabled or no display_phases)."""
        elems = []
        if not (settings.phase_strip_index_enabled and display_phases):
            return elems
        _idx_fs = settings.phase_strip_index_font_size
        _idx_tc = settings.phase_strip_index_text_color
        _color_map = {
            "hr": settings.phase_strip_color_by_condition_type.get("hr", "#DC2626"),
            "rr": settings.phase_strip_color_by_condition_type.get("rr", "#3B82F6"),
            "no_episode": settings.phase_strip_no_episode_color,
            "no_data": settings.phase_strip_no_data_color,
        }
        _idx_style = ParagraphStyle('strip_idx', parent=st['legend'],
                                     fontSize=_idx_fs, leading=_idx_fs + 2,
                                     textColor=_hex(_idx_tc))
        parts = []
        for item in settings.phase_strip_index_line1:
            sc = _color_map.get(item["swatch_color"], "#CCCCCC")
            has_border = item.get("border", False)
            marker = f'<font color="#999999">□</font>' if has_border else f'<font color="{sc}">■</font>'
            parts.append(f'{marker} <i>{_html.escape(item["label"])}</i>')
        for item in settings.phase_strip_index_line2:
            parts.append(f'<b>{_html.escape(item["symbol"])}</b> <i>{_html.escape(item["label"])}</i>')
        elems.append(Paragraph(" &nbsp; ".join(parts), _idx_style))
        elems.append(Spacer(1, 2))
        return elems

    def _make_threshold_legend():
        elems = []
        elems.append(Spacer(1, 3))
        elems.append(Paragraph("Clinical Alerting Thresholds", st["body_bold"]))

        # Build 2-column color-swatch table
        # R15 A2: added High Breathing and Very High Breathing rows
        # R19 C: each threshold uses its own distinct shade from THRESHOLD_LEGEND_COLORS.
        # Pre-R19 recycled 5 candlestick severity colors across 8 swatches, so
        # Very Low HR and Very High HR rendered identical (Sajol flagged on May 4).
        from .config import THRESHOLD_LEGEND_COLORS as _TLC
        threshold_rows_data = [
            ('Very Low HR', f'< {int(settings.severe_brady_min)} bpm', _TLC["very_low_hr"]),
            ('Low HR', f'< {int(settings.brady_hr_avg)} bpm', _TLC["low_hr"]),
            ('Elevated HR', f'> {int(settings.elevated_hr_avg)} bpm', _TLC["elevated_hr"]),
            ('High HR', f'> {int(settings.tachy_hr_avg)} bpm', _TLC["high_hr"]),
            ('Very High HR', f'> {int(settings.very_high_hr_avg)} bpm', _TLC["very_high_hr"]),
            ('Elevated Breathing', f'> {int(settings.tachy_rr_avg)} brpm', _TLC["elevated_rr"]),
            ('High Breathing', f'> {int(settings.high_rr_avg)} brpm', _TLC["high_rr"]),
            ('Very High Breathing', f'> {int(settings.very_high_rr_avg)} brpm', _TLC["very_high_rr"]),
        ]

        # R15 C2/C3: laid out as 4 columns × 2 rows (8 cells) to fit on page 1
        # alongside the new strip-index legend, without regressing the 2-page invariant.
        swatch_style = ParagraphStyle('swatch_lbl', parent=st['legend'], fontSize=6, leading=7)
        # Pack 4 cells per row instead of 2 so 8 thresholds = 2 visual rows (was 4)
        cells_per_row = 4
        arranged_rows = []
        for i in range(0, len(threshold_rows_data), cells_per_row):
            row_cells = []
            for j in range(cells_per_row):
                idx = i + j
                if idx >= len(threshold_rows_data):
                    row_cells += [None, None, None]
                    continue
                cell = threshold_rows_data[idx]
                swatch = Table([['']],
                    colWidths=[0.14 * inch], rowHeights=[0.10 * inch],
                    style=TableStyle([
                        ('BACKGROUND', (0, 0), (-1, -1), _hex(cell[2])),
                        ('BOX', (0, 0), (-1, -1), 0.25, _hex('#666666')),
                    ]))
                row_cells += [
                    swatch,
                    Paragraph(f"<b>{cell[0]}</b>&nbsp;{cell[1]}", swatch_style),
                ]
            arranged_rows.append(row_cells)

        # 4 cells per row = 8 columns: (swatch, label) × 4
        col_widths = [0.18 * inch, 1.62 * inch] * cells_per_row
        thresh_tbl = Table(arranged_rows, colWidths=col_widths)
        thresh_tbl.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 1),
            ('RIGHTPADDING', (0, 0), (-1, -1), 1),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        elems.append(thresh_tbl)
        elems.append(Spacer(1, 2))
        elems.append(Paragraph(
            f"<i>Episodic event: threshold exceeded continuously for &ge;&nbsp;"
            f"{int(settings.episodic_event_min_hours)}&nbsp;hour. "
            f"Episodes separated by &le;&nbsp;{int(settings.episode_merge_gap_hours)}&nbsp;hour "
            f"of normal vitals are counted as the same event. "
            f"Brief threshold crossings that do not sustain are not flagged.</i>",
            st["legend"]
        ))
        return elems

    placed_threshold_legend = False

    if not is_long_full_period:
        # Standard layout: pattern observations and candlestick on page 1
        if df is not None:
            elements.append(Spacer(1, 2))
            elements.extend(_make_findings_elements())
            elements.extend(_make_candlestick_elements())

            # R15 C2: Strip index legend now sits just above Clinical Alerting Thresholds.
            # R15 E1: one_page_only forces both legends onto page 1 even if events table is full.
            if num_events_table_rows <= 3 or one_page_only:
                elements.extend(_make_strip_index_legend())
                elements.extend(_make_threshold_legend())
                placed_threshold_legend = True
    elif is_long_full_period:
        # 3-page layout: hint that visual trends continue on next page
        elements.append(Spacer(1, 12))
        elements.append(Paragraph(
            "<i>Clinical pattern observations and vital sign trend charts continue on page 2.</i>",
            ParagraphStyle("page_hint", parent=st["legend"], alignment=TA_RIGHT, textColor=_hex('#888888'))
        ))

    elements.extend(_build_footer(report, st))

    # R15 E1: one_page_only — finalize after page 1, skip histogram/activity page
    if one_page_only:
        doc.build(elements)
        buf.seek(0)
        return buf.read()

    elements.append(PageBreak())

    if is_long_full_period:
        # PAGE 2 (3-page layout): Pattern observations + candlestick
        elements.extend(_build_compact_header(report, st, page_w, 2, total_pages=total_pages))
        fe = _make_findings_elements()
        if fe:
            elements.extend(fe)
        ce = _make_candlestick_elements()
        if ce:
            elements.append(Paragraph("<b>Weekly Vital Sign Trends</b>", ParagraphStyle("sub2", parent=st["body_bold"], spaceAfter=6)))
            elements.extend(ce)
        elements.extend(_build_footer(report, st))
        elements.append(PageBreak())

    # FINAL PAGE (page 2 for standard, page 3 for long Full Period)
    final_page_num = 3 if is_long_full_period else 2
    elements.extend(_build_compact_header(report, st, page_w, final_page_num, total_pages=total_pages))

    if df is not None:
        # FIX 2: SECTION 4 — Histogram SECOND (distribution context)
        elements.append(Paragraph(
            "Vital Sign Distribution", st["section_head"]))
        hist = generate_histogram_for_pdf(df)
        hist_h = settings.histogram_height_inches * inch
        elements.append(Image(io.BytesIO(hist), width=settings.histogram_width_inches * inch, height=settings.histogram_height_inches * inch))
        elements.append(Spacer(1, 3))

        # FIX 2: SECTION 6 — Activity chart LAST
        elements.append(Paragraph(
            "Monitoring Activity", st["body_bold"]))
        from .charts import generate_activity_trend_chart_for_pdf
        act = generate_activity_trend_chart_for_pdf(df)
        act_h = 1.05 * inch if is_multisensor else 1.5 * inch
        elements.append(Image(io.BytesIO(act), width=settings.activity_width_inches * inch, height=settings.activity_height_inches * inch))
        elements.append(Paragraph(
            "Bars indicate monitoring hours/day. "
            "Red threshold line indicates clinical monitoring baseline.",
            st["caption"]
        ))
        
        # FIX 38: Threshold Legend as color-coded reference table
        # R15 C2: Strip index legend tracks with the threshold legend
        if not placed_threshold_legend:
            elements.extend(_make_strip_index_legend())
            elements.extend(_make_threshold_legend())

    elements.extend(_build_footer(report, st))
    doc.build(elements)

    # R11 Fix 2d: Page-count assertion — two pages, always.
    buf.seek(0)
    pdf_bytes = buf.read()
    try:
        from PyPDF2 import PdfReader as _PdfReader
        page_count = len(_PdfReader(io.BytesIO(pdf_bytes)).pages)
        if page_count > 2:
            import logging
            logging.getLogger(__name__).warning(
                f"Page overflow: {_v(report, 'patient_id')} rendered as {page_count} pages "
                f"(expected 2). Content may need further compression."
            )
    except ImportError:
        pass  # PyPDF2 not available — skip assertion

    return pdf_bytes
