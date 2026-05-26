"""
CardioReport – Chart Generation
Produces candlestick-like daily trend charts and distribution histograms
for HR and RR using matplotlib. Returns base64-encoded PNGs.
"""

from __future__ import annotations
import base64
import io
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

from .config import settings, Conditions, ChartColors as CC, RENDER_CONFIG
from .config import (
    LABEL_STAGGER_GAP_PX, LABEL_STAGGER_ROW_PT, LABEL_STAGGER_MAX_ROWS,
    ASTERISK_LEGEND_Y_AXES_DAILY, ASTERISK_LEGEND_Y_AXES_WEEKLY,
    LEGEND_BOTTOM_AXES_FRACTION, BADGE_LEGEND_CLEARANCE_PT,
)


def place_hour_labels_with_stagger(ax, label_specs):
    """R20.A: place episode hour labels at top of bars with pixel-space
    collision avoidance.

    R23.B: bars that reach ymax push their badge into the HR severity legend
    strip (anchored above the axes at y=1.18). When the badge's final y in
    axes coords would cross LEGEND_BOTTOM_AXES_FRACTION minus the configured
    clearance, place the badge INSIDE the bar with white text instead — keeps
    the value visible without overlapping the legend.

    Args:
        ax: matplotlib Axes for the panel (HR or RR).
        label_specs: list of dicts, each with keys:
            x_data: bar x position in data coordinates (matplotlib date)
            y_top:  bar top in data coordinates
            text:   label string, e.g. "10h"
            color:  text color (matches episode tier)

    Behavior: sorts labels by x_data, iterates left-to-right, and staggers
    vertically when consecutive labels would overlap in pixel space. Resets
    stagger row when a label clears the gap. Operates in pixel space so it
    scales correctly across chart widths and period lengths.
    """
    if not label_specs:
        return
    fig = ax.figure
    dpi = fig.get_dpi() if fig is not None else 100
    # Legend-bottom pixel y for the legend-clearance check (R23.B). A badge
    # whose top in pixel space rises into this band is flipped to inside-bar.
    legend_bottom_px = ax.transAxes.transform((0, LEGEND_BOTTOM_AXES_FRACTION))[1]
    clearance_px = BADGE_LEGEND_CLEARANCE_PT * dpi / 72.0
    # R23 Hotfix B — right-margin pixel bound. Badges anchored ha='center' on
    # the rightmost bar push half their width past the axes right edge and get
    # clipped to a partial value (JB CriticalWeek Sep 30 audit showed "10h"
    # rendering as "0"). When the projected right edge exceeds this bound, the
    # badge flips to ha='right' so it sits inside the chart frame.
    #
    # R23 Hotfix B (symmetric) — same problem on the leftmost bar in mirror form:
    # JB CriticalWeek Sep 23 rendered as "h" because the leading digits clipped
    # past the y-axis frame. Flip to ha='left' when the projected left edge of
    # the badge would cross axes_left_px.
    axes_left_px = ax.transAxes.transform((0.0, 0.0))[0]
    axes_right_px = ax.transAxes.transform((1.0, 0.0))[0]

    specs_sorted = sorted(label_specs, key=lambda s: s["x_data"])
    last_x_px = float("-inf")
    row = 0
    for s in specs_sorted:
        # Coerce x_data to a numeric value the transData.transform can accept.
        # Pandas Timestamps and Python datetimes need matplotlib's date2num
        # conversion; ints/floats pass through unchanged.
        xv = s["x_data"]
        try:
            xv_num = mdates.date2num(xv)
        except Exception:
            xv_num = xv
        x_px = ax.transData.transform((xv_num, 0))[0]
        if x_px - last_x_px < LABEL_STAGGER_GAP_PX:
            row = (row + 1) % LABEL_STAGGER_MAX_ROWS
        else:
            row = 0
        fontsize = s.get("fontsize", 5.5)
        y_offset_pt = 4 + row * LABEL_STAGGER_ROW_PT

        # R23.B — Project the badge to pixel space and check legend intrusion.
        # Badge bottom in pixels = bar top in pixels + offset; badge top adds
        # roughly one font-height. If the top would cross the legend clearance
        # line, flip to inside-bar placement with white text.
        bar_top_px = ax.transData.transform((xv_num, s["y_top"]))[1]
        badge_bottom_px = bar_top_px + y_offset_pt * dpi / 72.0
        badge_top_px = badge_bottom_px + fontsize * dpi / 72.0

        # R23 Hotfix B — margin clipping check. Rough char width ~0.6 em at the
        # configured fontsize; centered anchor projects half the width to each
        # side of x. Flip to the matching anchor when either side would cross
        # the axes bound. Right edge flagged JB CriticalWeek Sep 30 ("10h" →
        # "0"); left edge flagged JB CriticalWeek Sep 23 (digits → "h").
        half_width_px = (len(s["text"]) * fontsize * 0.6 * dpi / 72.0) / 2.0
        if x_px + half_width_px > axes_right_px:
            ha = "right"
        elif x_px - half_width_px < axes_left_px:
            ha = "left"
        else:
            ha = "center"

        if badge_top_px > legend_bottom_px - clearance_px:
            # Inside-bar placement. va='top' so the badge sits just below the
            # bar top; white text contrasts against dark severe/critical fills.
            ax.annotate(
                s["text"],
                xy=(s["x_data"], s["y_top"]),
                xytext=(0, -(BADGE_LEGEND_CLEARANCE_PT + fontsize)),
                textcoords="offset points",
                ha=ha,
                va="top",
                fontsize=fontsize,
                color="white",
                fontweight="bold",
                zorder=7,
            )
        else:
            ax.annotate(
                s["text"],
                xy=(s["x_data"], s["y_top"]),
                xytext=(0, y_offset_pt),
                textcoords="offset points",
                ha=ha,
                va="bottom",
                fontsize=fontsize,
                color=s["color"],
                fontweight="bold",
                zorder=7,
            )
        last_x_px = x_px
from .models import Episode


# ── R12 Fix 8: Spread annotation with required-param signature check ──────────

def render_spread_annotation(
    ax,
    p5: float,
    p95: float,
    sample_hours: int,
    min_spread_bpm: int,     # required, no default
    min_sample_hours: int,   # required, no default
    y_max: float,
    tick_fs: int,
) -> bool:
    """R12 Fix 8: Gate spread annotation on BOTH min_spread AND min_sample_hours.
    Returns True if annotation was drawn, False otherwise.
    """
    spread = p95 - p5
    if spread < min_spread_bpm:
        return False
    if sample_hours < min_sample_hours:
        return False

    y_arrow = y_max * 1.15
    y_text = y_max * 1.25
    ax.annotate("", xy=(p5, y_arrow), xytext=(p95, y_arrow),
                arrowprops=dict(arrowstyle="<->", color="#DC2626", lw=1.2))
    ax.text((p5 + p95) / 2, y_text, f"Spread: {spread:.0f} bpm", color="#DC2626",
            fontsize=tick_fs + 1.5, fontweight="bold", ha="center")
    return True


# Import-time signature check — fails loudly if signature drifts
import inspect as _inspect
_sig = _inspect.signature(render_spread_annotation)
_required = {p.name for p in _sig.parameters.values() if p.default is _inspect.Parameter.empty}
assert _required == {"ax", "p5", "p95", "sample_hours", "min_spread_bpm", "min_sample_hours", "y_max", "tick_fs"}, (
    f"render_spread_annotation signature changed: {_required}"
)

# ── Determinism: fixed seed ensures identical charts for identical data ──────
np.random.seed(42)

# FIX 7: Global font defaults to prevent text stretching / distortion
plt.rcParams.update({
    'font.family': 'sans-serif',
    'font.sans-serif': ['Arial', 'Helvetica', 'DejaVu Sans'],
    'font.size': 8,
    'axes.labelsize': 7,
    'axes.titlesize': 8,
    'xtick.labelsize': 6,
    'ytick.labelsize': 6,
})

# ── Helpers ──────────────────────────────────────────────────────────────────

def _daily_agg(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate hourly data into daily min/avg/max."""
    df = df.copy()
    df["date"] = df["timestamp"].dt.date
    daily = df.groupby("date").agg(
        hr_min=("hr_min", "min") if "hr_min" in df.columns else ("hr_avg", "min"),
        hr_avg=("hr_avg", "mean"),
        hr_max=("hr_max", "max") if "hr_max" in df.columns else ("hr_avg", "max"),
        rr_min=("rr_min", "min") if "rr_min" in df.columns else ("rr_avg", "min"),
        rr_avg=("rr_avg", "mean"),
        rr_max=("rr_max", "max") if "rr_max" in df.columns else ("rr_avg", "max"),
        hours=("hr_avg", "count"),
    ).reset_index()
    daily["date"] = pd.to_datetime(daily["date"])
    return daily


def _episode_date_set(episodes: list[Episode]) -> set:
    """Build set of calendar dates that contain any detected episode."""
    from datetime import timedelta as td
    days = set()
    for ep in episodes:
        def _get(o, k):
            return getattr(o, k) if hasattr(o, k) else o.get(k)
        
        st_val = _get(ep, "start_time")
        en_val = _get(ep, "end_time")
        if not st_val or not en_val: continue
        
        start = pd.Timestamp(st_val).date()
        end = pd.Timestamp(en_val).date()
        d = start
        while d <= end:
            days.add(d)
            d = d + td(days=1)
    return days


# ── Chart A: Daily Candlestick (stacked panels) ─────────────────────────────

# Phase colors for chart background shading (from config palette)
_PHASE_CHART_COLORS = {
    'stable': '#FFFFFF',
    'low_hr': '#2C5F8A',
    'high_hr': '#C0392B',
    'mixed': '#F39C12',
    'mixed_low': '#2C5F8A',
    'mixed_high': '#C0392B',
}

# ── FIX 34: Day Severity Classification ──────────────────────────────────────

def classify_day_severity(date, episodes, prefix=None):
    """Classify a day's severity based on episodes overlapping that date."""
    day_eps = []
    for ep in episodes:
        st = getattr(ep, 'start_time', None) or (ep.get('start_time') if isinstance(ep, dict) else None)
        en = getattr(ep, 'end_time', None) or (ep.get('end_time') if isinstance(ep, dict) else None)
        if not st or not en:
            continue
        ep_start = pd.Timestamp(st).normalize()
        ep_end = pd.Timestamp(en).normalize()
        if ep_start <= date <= ep_end:
            cond = getattr(ep, 'condition', None) or (ep.get('condition') if isinstance(ep, dict) else None)
            is_rr_ep = cond == 'Tachypnea' or cond == 'Elevated Breathing'
            if prefix == 'rr' and not is_rr_ep:
                continue
            if prefix == 'hr' and is_rr_ep:
                continue
            day_eps.append(ep)

    if not day_eps:
        return 'normal', 0, False

    total_hours = sum(
        getattr(e, 'duration_hours', 0) if not isinstance(e, dict) else e.get('duration_hours', 0)
        for e in day_eps
    )
    is_coupled = any(
        getattr(e, 'cooccurrence', False) if not isinstance(e, dict) else e.get('cooccurrence', False)
        for e in day_eps
    )

    if total_hours >= settings.candlestick_critical_threshold_hours:
        return 'critical', total_hours, is_coupled
    elif total_hours >= settings.candlestick_severe_threshold_hours or (is_coupled and total_hours >= 3):
        return 'severe', total_hours, is_coupled
    elif total_hours >= settings.candlestick_moderate_threshold_hours:
        return 'moderate', total_hours, is_coupled
    elif total_hours >= settings.candlestick_mild_threshold_hours:
        return 'mild', total_hours, is_coupled
    else:
        return 'normal', 0, False


def get_severity_color_and_width(severity):
    """Map severity band to color and line width from settings."""
    return {
        'normal':   (settings.candlestick_color_normal,   settings.candlestick_normal_linewidth),
        'mild':     (settings.candlestick_color_mild,     settings.candlestick_mild_linewidth),
        'moderate': (settings.candlestick_color_moderate, settings.candlestick_moderate_linewidth),
        'severe':   (settings.candlestick_color_severe,   settings.candlestick_severe_linewidth),
        'critical': (settings.candlestick_color_critical, settings.candlestick_critical_linewidth),
    }[severity]


def choose_candlestick_strategy(reporting_days):
    from .config import settings
    if reporting_days <= settings.candlestick_daily_max_days:
        return 'daily'
    else:
        return 'weekly'

def aggregate_to_weekly(dly, eps):
    import pandas as pd
    dly = dly.copy()
    dly['date'] = pd.to_datetime(dly['date'])
    dly['week_start'] = dly['date'] - pd.to_timedelta(dly['date'].dt.dayofweek, unit='d')
    weekly = dly.groupby('week_start').agg(
        hr_min=('hr_min', 'min') if 'hr_min' in dly.columns else ('hr_avg', 'min'),
        hr_max=('hr_max', 'max') if 'hr_max' in dly.columns else ('hr_avg', 'max'),
        hr_avg=('hr_avg', 'mean'),
        rr_min=('rr_min', 'min') if 'rr_min' in dly.columns else ('rr_avg', 'min'),
        rr_max=('rr_max', 'max') if 'rr_max' in dly.columns else ('rr_avg', 'max'),
        rr_avg=('rr_avg', 'mean'),
    ).reset_index()
    
    weekly_episodes = {}
    for _, week_row in weekly.iterrows():
        week_start = week_row['week_start']
        week_end = week_start + pd.Timedelta(days=6)
        
        week_eps = []
        for e in eps:
            estart = getattr(e, 'start_time') if hasattr(e, 'start_time') else e.get('start_time')
            if not estart: continue
            ep_start = pd.Timestamp(estart).normalize()
            if week_start <= ep_start <= week_end:
                week_eps.append(e)
                
        total_hours = sum(getattr(e, 'duration_hours', 0) if not isinstance(e, dict) else e.get('duration_hours', 0) for e in week_eps)
        is_coupled = any(getattr(e, 'cooccurrence', False) if not isinstance(e, dict) else e.get('cooccurrence', False) for e in week_eps)
        
        weekly_episodes[week_start] = {
            'hours': total_hours,
            'count': len(week_eps),
            'coupled': is_coupled,
        }
    return weekly, weekly_episodes

def classify_weekly_severity(total_hours):
    if total_hours >= 40: return 'critical'
    elif total_hours >= 15: return 'severe'
    elif total_hours >= 5: return 'moderate'
    elif total_hours >= 1: return 'mild'
    else: return 'normal'

def chart_candlestick_weekly(dly, eps, phases, window_start, window_end):
    import pandas as pd
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from .config import settings
    
    weekly, weekly_episodes = aggregate_to_weekly(dly, eps)
    fig, (ax1, ax2) = plt.subplots(
        2, 1,
        figsize=(settings.plot_width_inches, settings.candlestick_long_period_height_inches),
        sharex=True,
        dpi=settings.candlestick_dpi
    )
    
    for y in settings.hr_gridline_values:
        ax1.axhline(y, color=settings.gridline_color, linewidth=settings.gridline_width, zorder=0, alpha=settings.gridline_alpha)
    for y in settings.rr_gridline_values:
        ax2.axhline(y, color=settings.gridline_color, linewidth=settings.gridline_width, zorder=0, alpha=settings.gridline_alpha)
        
    x = range(len(weekly))

    # R20.A: collect badge specs for pixel-space staggering after the loop
    weekly_badge_specs = []

    for i in range(len(weekly)):
        week_start = weekly['week_start'].iloc[i]
        week_ep = weekly_episodes.get(week_start, {'hours': 0, 'count': 0, 'coupled': False})
        severity = classify_weekly_severity(week_ep['hours'])
        
        # Color mapper
        color_map = {
            'normal':   (settings.candlestick_color_normal,   settings.candlestick_normal_linewidth),
            'mild':     (settings.candlestick_color_mild,     settings.candlestick_mild_linewidth),
            'moderate': (settings.candlestick_color_moderate, settings.candlestick_moderate_linewidth),
            'severe':   (settings.candlestick_color_severe,   settings.candlestick_severe_linewidth),
            'critical': (settings.candlestick_color_critical, settings.candlestick_critical_linewidth),
        }
        color, linewidth = color_map[severity]
        
        ax1.plot([x[i], x[i]], [weekly['hr_min'].iloc[i], weekly['hr_max'].iloc[i]], color=color, linewidth=linewidth, solid_capstyle='round', alpha=0.85)
        ax1.plot(x[i], weekly['hr_avg'].iloc[i], 'o', color="#1A2E44", markersize=3, zorder=5)
        
        rr_color = "#E8843C" if severity == 'normal' else color
        ax2.plot([x[i], x[i]], [weekly['rr_min'].iloc[i], weekly['rr_max'].iloc[i]], color=rr_color, linewidth=linewidth, solid_capstyle='round', alpha=0.85)
        ax2.plot(x[i], weekly['rr_avg'].iloc[i], 'o', color="#1A2E44", markersize=3, zorder=5)
        
        if severity in ('severe', 'critical'):
            badge_text = f"{int(week_ep['hours'])}h"
            if week_ep['coupled']: badge_text += "*"
            badge_y = max(weekly['hr_max'].iloc[i] + 5, 125)
            weekly_badge_specs.append({
                "x_data": x[i], "y_top": badge_y,
                "text": badge_text, "color": color,
                "fontsize": 6,
            })

    place_hour_labels_with_stagger(ax1, weekly_badge_specs)

    week_labels = [w.strftime('%b %d') for w in weekly['week_start']]
    label_interval = max(1, len(week_labels) // 12)
    displayed_labels = [lbl if i % label_interval == 0 else '' for i, lbl in enumerate(week_labels)]
    
    ax2.set_xticks(x)
    ax2.set_xticklabels(displayed_labels, rotation=30, ha='right', fontsize=settings.chart_tick_fontsize)
    
    ax1.axhline(settings.brady_hr_avg, color='#F39C12', linewidth=0.7, linestyle='--', zorder=1, alpha=0.7)
    ax1.axhline(settings.tachy_hr_avg, color='#C0392B', linewidth=0.7, linestyle='--', zorder=1, alpha=0.7)
    ax2.axhline(settings.tachy_rr_avg, color='#C0392B', linewidth=0.7, linestyle='--', zorder=1, alpha=0.7)

    # R22.B: RR y-axis no longer clamped at the physiologic ceiling. Sprint A's
    # ingestion-side noise filter handles RR-without-HR sensor garbage; any
    # residual high RR is shown as-is so data-quality issues stay visible.

    legend_elements = [
        Line2D([0], [0], color=settings.candlestick_color_normal, linewidth=2, label='Normal week'),
        Line2D([0], [0], color=settings.candlestick_color_mild, linewidth=2.5, label='Brief events (1-5h)'),
        Line2D([0], [0], color=settings.candlestick_color_moderate, linewidth=3, label='Moderate (5-15h)'),
        Line2D([0], [0], color=settings.candlestick_color_severe, linewidth=3.5, label='Severe (15-40h)'),
        Line2D([0], [0], color=settings.candlestick_color_critical, linewidth=4, label='Critical (40h+)'),
    ]
    ax1.legend(handles=legend_elements, loc='upper right', fontsize=settings.chart_legend_fontsize, frameon=True, framealpha=0.92, ncol=5, bbox_to_anchor=(1.0, 1.18))

    # R23.D — asterisk note also rendered on the weekly aggregate path. Pre-R23,
    # only the daily candlestick path (_generate_generic_candlestick) emitted it,
    # so FullPeriod reports that fell into weekly aggregation had no key for the
    # `*` suffix on coupled-week badges.
    # R23 Hotfix A — weekly aggregate path uses the deeper _WEEKLY constant; its
    # longer date strings rotate further below the chart frame than the daily path.
    ax2.text(
        0.0, ASTERISK_LEGEND_Y_AXES_WEEKLY,
        '* indicates concurrent HR and breathing abnormality',
        transform=ax2.transAxes,
        ha='left', va='top',
        fontsize=5, style='italic', color='#666666',
        clip_on=False,
    )

    ax1.set_ylabel('Heart Rate\n(bpm)', fontsize=settings.chart_axis_label_fontsize)
    ax2.set_ylabel('Resp Rate\n(breaths/min)', fontsize=settings.chart_axis_label_fontsize)

    fig.suptitle(f'Weekly Aggregated Trends ({len(weekly)} weeks)', fontsize=settings.chart_title_fontsize)
    plt.tight_layout()
    return fig


def choose_candlestick_strategy(reporting_days):
    """Decide how to render the candlestick based on reporting length."""
    if reporting_days <= settings.candlestick_daily_max_days:
        return 'daily'
    else:
        return 'weekly'


def aggregate_to_weekly(dly, eps):
    """
    Aggregate daily data into weekly buckets.
    Returns a DataFrame with one row per week and episode info per week.
    """
    dly = dly.copy()
    dly['date'] = pd.to_datetime(dly['date'])
    dly['week_start'] = dly['date'] - pd.to_timedelta(dly['date'].dt.dayofweek, unit='d')
    
    weekly = dly.groupby('week_start').agg(
        hr_min=('hr_min', 'min') if 'hr_min' in dly.columns else ('hr_avg', 'min'),
        hr_max=('hr_max', 'max') if 'hr_max' in dly.columns else ('hr_avg', 'max'),
        hr_avg=('hr_avg', 'mean'),
        rr_min=('rr_min', 'min') if 'rr_min' in dly.columns else ('rr_avg', 'min'),
        rr_max=('rr_max', 'max') if 'rr_max' in dly.columns else ('rr_avg', 'max'),
        rr_avg=('rr_avg', 'mean'),
    ).reset_index()
    
    # Build weekly episode aggregates
    weekly_episodes = {}
    for _, week_row in weekly.iterrows():
        week_start = week_row['week_start']
        week_end = week_start + pd.Timedelta(days=6)
        
        week_eps = []
        for e in eps:
            estart = getattr(e, 'start_time', None) or (e.get('start_time') if isinstance(e, dict) else None)
            if not estart: continue
            if pd.Timestamp(estart).normalize() >= week_start and pd.Timestamp(estart).normalize() <= week_end:
                week_eps.append(e)
        
        total_hours = sum(getattr(e, 'duration_hours', 0) if not isinstance(e, dict) else e.get('duration_hours', 0) for e in week_eps)
        is_coupled = any(getattr(e, 'cooccurrence', False) if not isinstance(e, dict) else e.get('cooccurrence', False) for e in week_eps)
        
        weekly_episodes[week_start] = {
            'hours': total_hours,
            'count': len(week_eps),
            'coupled': is_coupled,
        }
    
    return weekly, weekly_episodes


def classify_weekly_severity(total_hours):
    """Weekly severity classification using day-anchored config bands."""
    for band in reversed(settings.weekly_trend_severity_bands):
        if total_hours >= band["min_hours"]:
            # Map band labels to internal keys
            label = band["label"].lower()
            if "critical" in label:
                return 'critical'
            elif "severe" in label:
                return 'severe'
            elif "moderate" in label:
                return 'moderate'
            elif "brief" in label:
                return 'mild'
            else:
                return 'normal'
    return 'normal'


def chart_candlestick_weekly(dly, eps, phases, window_start, window_end):
    """Weekly aggregated candlestick for long reporting periods."""
    weekly, weekly_episodes = aggregate_to_weekly(dly, eps)
    
    # Use slightly shorter height for long periods
    fig, (ax1, ax2) = plt.subplots(
        2, 1,
        figsize=(settings.plot_width_inches, settings.candlestick_long_period_height_inches),
        sharex=True,
        dpi=settings.candlestick_dpi
    )

    # R24.1 — tighten x-axis padding so the bar band fills the chart frame.
    # See _generate_generic_candlestick comment; same rationale applies here.
    for ax in (ax1, ax2):
        ax.margins(x=0.01)

    # Gridlines
    for y in settings.hr_gridline_values:
        ax1.axhline(y, color=settings.gridline_color,
                    linewidth=settings.gridline_width, zorder=0,
                    alpha=settings.gridline_alpha)
    for y in settings.rr_gridline_values:
        ax2.axhline(y, color=settings.gridline_color,
                    linewidth=settings.gridline_width, zorder=0,
                    alpha=settings.gridline_alpha)

    x = range(len(weekly))

    # R20.A: collect badge specs for pixel-space staggering after the loop
    weekly_badge_specs = []

    # Track max hours for badge placement to prevent overlap
    for i in range(len(weekly)):
        week_start = weekly['week_start'].iloc[i]
        week_ep = weekly_episodes.get(week_start, {'hours': 0, 'count': 0, 'coupled': False})
        
        severity = classify_weekly_severity(week_ep['hours'])
        # Map severity to color and width
        color_map = {
            'normal':   (settings.candlestick_color_normal,   settings.candlestick_normal_linewidth),
            'mild':     (settings.candlestick_color_mild,     settings.candlestick_mild_linewidth),
            'moderate': (settings.candlestick_color_moderate, settings.candlestick_moderate_linewidth),
            'severe':   (settings.candlestick_color_severe,   settings.candlestick_severe_linewidth),
            'critical': (settings.candlestick_color_critical, settings.candlestick_critical_linewidth),
        }
        color, linewidth = color_map[severity]
        
        # HR candle
        ax1.plot(
            [x[i], x[i]],
            [weekly['hr_min'].iloc[i], weekly['hr_max'].iloc[i]],
            color=color,
            linewidth=linewidth,
            solid_capstyle='round',
            alpha=0.85
        )
        ax1.plot(x[i], weekly['hr_avg'].iloc[i], 'o',
                 color="#1A2E44", markersize=3, zorder=5)
        
        # RR candle
        rr_color = "#E8843C" if severity == 'normal' else color
        ax2.plot(
            [x[i], x[i]],
            [weekly['rr_min'].iloc[i], weekly['rr_max'].iloc[i]],
            color=rr_color,
            linewidth=linewidth,
            solid_capstyle='round',
            alpha=0.85
        )
        ax2.plot(x[i], weekly['rr_avg'].iloc[i], 'o',
                 color="#1A2E44", markersize=3, zorder=5)
        
        # Weekly badge for severe+ weeks only
        if severity in ('severe', 'critical'):
            badge_text = f"{int(week_ep['hours'])}h"
            if week_ep['coupled']:
                badge_text += "*"

            badge_y = max(weekly['hr_max'].iloc[i] + 5, 125)
            weekly_badge_specs.append({
                "x_data": x[i], "y_top": badge_y,
                "text": badge_text, "color": color,
                "fontsize": 6,
            })

    place_hour_labels_with_stagger(ax1, weekly_badge_specs)

    # X axis
    week_labels = [w.strftime('%b %d') for w in weekly['week_start']]
    label_interval = max(1, len(week_labels) // 12)
    displayed_labels = [lbl if i % label_interval == 0 else '' 
                        for i, lbl in enumerate(week_labels)]
    
    ax2.set_xticks(x)
    ax2.set_xticklabels(displayed_labels, rotation=30, ha='right',
                        fontsize=settings.chart_tick_fontsize)
    
    from matplotlib.lines import Line2D
    band_linewidths = [2, 2.5, 3, 3.5, 4]
    legend_elements = [
        Line2D([0], [0],
               color=getattr(settings, band["color_key"]),
               linewidth=band_linewidths[i] if i < len(band_linewidths) else 3,
               label=band["label"])
        for i, band in enumerate(settings.weekly_trend_severity_bands)
    ]
    ax1.legend(
        handles=legend_elements,
        loc='upper right',
        fontsize=settings.chart_legend_fontsize,
        frameon=True,
        framealpha=0.92,
        ncol=5,
        bbox_to_anchor=(1.0, 1.18)
    )

    # R23.D — asterisk note rendered on the weekly aggregate path too. Pre-R23,
    # only the daily candlestick path emitted it, so FullPeriod reports that fall
    # into weekly aggregation had no key for the `*` suffix on coupled weeks.
    # R23 Hotfix A — weekly aggregate uses the deeper _WEEKLY constant because its
    # longer date strings rotate further below the chart frame than the daily path.
    ax2.text(
        0.0, ASTERISK_LEGEND_Y_AXES_WEEKLY,
        '* indicates concurrent HR and breathing abnormality',
        transform=ax2.transAxes,
        ha='left', va='top',
        fontsize=5, style='italic', color='#666666',
        clip_on=False,
    )

    ax1.set_ylabel('Heart Rate\n(bpm)', fontsize=settings.chart_axis_label_fontsize)
    ax2.set_ylabel('Resp Rate\n(breaths/min)', fontsize=settings.chart_axis_label_fontsize)

    # R22.B: RR y-axis no longer clamped at the physiologic ceiling.

    fig.suptitle(
        f'Weekly Aggregated Trends ({len(weekly)} weeks)',
        fontsize=settings.chart_title_fontsize
    )
    plt.tight_layout()
    return fig


def _generate_generic_candlestick(daily: pd.DataFrame, ep_days: set,
                                  figsize: tuple[float, float], dpi: int,
                                  is_pdf: bool = False,
                                  phases: list = None,
                                  episodes: list = None) -> plt.Figure:
    """Core logic for the dual-panel candlestick chart.
    
    FIX 9: Red bars and triangle markers ONLY on actual episode dates.
    FIX 10: Phase number labels (P1, P2...) with background shading.
    """
    fig, (ax_hr, ax_rr) = plt.subplots(
        2, 1, figsize=figsize, dpi=dpi,
        sharex=True, gridspec_kw={"height_ratios": [1, 0.8], "hspace": 0.08}
    )
    # Background color from config (white for PDF, light gray for web)

    for y in settings.hr_gridline_values:
        ax_hr.axhline(y, color=settings.gridline_color,
                    linewidth=settings.gridline_width,
                    zorder=0, alpha=settings.gridline_alpha)

    for y in settings.rr_gridline_values:
        ax_rr.axhline(y, color=settings.gridline_color,
                    linewidth=settings.gridline_width,
                    zorder=0, alpha=settings.gridline_alpha)
                    
    bg = "white" if is_pdf else CC.BG
    fig.patch.set_facecolor(bg)

    dates = daily["date"]
    
    # Common styling constants
    txt_color = CC.TEXT if not is_pdf else "black"
    grid_color = CC.GRID
    label_fs = 7 if is_pdf else 10
    tick_fs = 6 if is_pdf else 8
    ep_color = CC.EPISODE  # from config palette

    # R24.1 — tighten the x-axis data-range padding. May 21 diagnostic showed
    # all three report types (FullPeriod/90Day/CriticalWeek) render at the same
    # 7.49 in chart width: there is no horizontal room to "widen". The crunched
    # perception on 90-day charts came from matplotlib's default 5% margin
    # leaving ~4.5 days of empty band on each side. Tighten to 1% so the bar
    # band fills the frame; CriticalWeek's wide bars are unaffected because 1%
    # of 7 days is negligible.
    for ax in (ax_hr, ax_rr):
        ax.margins(x=0.01)

    for ax, prefix, color, fill, label, unit in [
        (ax_hr, "hr", CC.HR, CC.HR_FILL, "Heart Rate", "bpm"),
        (ax_rr, "rr", CC.RR, CC.RR_FILL, "Resp Rate" if is_pdf else "Respiratory Rate", "breaths/min"),
    ]:
        ax.set_facecolor(bg)
        mins = daily[f"{prefix}_min"].values
        maxs = daily[f"{prefix}_max"].values
        avgs = daily[f"{prefix}_avg"].values

        # R20.A: collect badge specs first, place after the bar loop with
        # pixel-space staggering. Pre-R20, ax.text was called inline per bar
        # with no neighbor awareness — adjacent days collided into "108h26h8h".
        badge_specs = []

        # FIX 34: Draw candlestick bars with severity gradient
        for i, d in enumerate(dates):
            has_episode = d.date() in ep_days if hasattr(d, 'date') else d in ep_days

            # FIX 34: Use severity gradient for PDF, binary for web
            if is_pdf and episodes:
                d_norm = pd.Timestamp(d).normalize()
                severity, sev_hours, is_coupled = classify_day_severity(d_norm, episodes, prefix=prefix)
                bar_color, lw = get_severity_color_and_width(severity)
                alpha = 0.85

                # Severity badge above critical/severe days (or >=3h for RR)
                should_draw_badge = False
                if prefix == 'hr' and severity in ('critical', 'severe'):
                    should_draw_badge = True
                elif prefix == 'rr' and sev_hours >= 3:
                    should_draw_badge = True

                if should_draw_badge:
                    badge_y = maxs[i] + (3 if prefix == 'hr' else 1.5)
                    badge_text = f"{int(sev_hours)}h"
                    if is_coupled:
                        badge_text += "*"
                    badge_specs.append({
                        "x_data": d, "y_top": badge_y,
                        "text": badge_text, "color": bar_color,
                        "fontsize": 5.5,
                    })
            else:
                bar_color = ep_color if has_episode else color
                lw = 3.5 if has_episode else 2.5
                alpha = 0.8 if has_episode else 0.6

            ax.plot([d, d], [mins[i], maxs[i]], color=bar_color, 
                    linewidth=lw,
                    solid_capstyle="round", alpha=alpha)
            
            if not is_pdf:
                cap_w = pd.Timedelta(hours=8)
                ax.plot([d - cap_w, d + cap_w], [mins[i], mins[i]], color=bar_color, linewidth=1.5, alpha=0.4)
                ax.plot([d - cap_w, d + cap_w], [maxs[i], maxs[i]], color=bar_color, linewidth=1.5, alpha=0.4)
            
            # Triangle marker above bar for episode days (web only; PDF uses severity badges)
            if has_episode and not (is_pdf and episodes):
                offset = 2 if prefix == "hr" else 1
                ax.plot(d, maxs[i] + offset, 'v',
                        color=ep_color, markersize=4, zorder=6)

        # R20.A: place collected badges with pixel-space staggering. Done after
        # the per-day bar loop so x→pixel transform reflects the final axis range.
        place_hour_labels_with_stagger(ax, badge_specs)

        # Average line
        ax.plot(dates, avgs, color=color, linewidth=1.5 if is_pdf else 2, marker="o",
                markersize=2.5 if is_pdf else 4,
                markerfacecolor="#1A2E44" if is_pdf else "white",
                markeredgecolor=color,
                markeredgewidth=1 if is_pdf else 1.5, zorder=5)

        # Fill min/max range
        ax.fill_between(dates, mins, maxs, color=fill, alpha=0.15 if is_pdf else 0.2)

        # Reference Thresholds
        if prefix == "hr":
            ax.axhline(y=settings.brady_hr_avg, color=CC.WARNING, linestyle="--", linewidth=0.8, alpha=0.5)
            ax.axhline(y=settings.tachy_hr_avg, color=CC.EPISODE, linestyle="--", linewidth=0.8, alpha=0.5)
        else:
            ax.axhline(y=settings.tachy_rr_avg, color=CC.EPISODE, linestyle="--", linewidth=0.8, alpha=0.5)

        ax.set_ylabel(f"{label}\n({unit})" if is_pdf else f"{label} ({unit})", 
                      fontsize=label_fs, fontweight="600", color=txt_color)
        ax.grid(True, axis="y", color=grid_color, linewidth=0.4 if is_pdf else 0.5)
        ax.tick_params(colors=txt_color, labelsize=tick_fs)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color(grid_color)
        ax.spines["bottom"].set_color(grid_color)

    # FIX 10: Phase background shading and numbered labels
    if phases and is_pdf:
        date_list = daily["date"].tolist()
        for i, p in enumerate(phases):
            ptype = p.get("type", "mixed") if isinstance(p, dict) else getattr(p, "type", "mixed")
            p_color = _PHASE_CHART_COLORS.get(ptype, '#F39C12')
            
            p_start = pd.Timestamp(p.get("start_date") if isinstance(p, dict) else getattr(p, "start_date", ""))
            p_end = pd.Timestamp(p.get("end_date") if isinstance(p, dict) else getattr(p, "end_date", ""))
            
            start_idx = None
            end_idx = None
            for di, d in enumerate(date_list):
                d_ts = pd.Timestamp(d)
                if start_idx is None and d_ts >= p_start:
                    start_idx = di
                if d_ts <= p_end:
                    end_idx = di
            
            if start_idx is None or end_idx is None:
                continue
            
            # FIX 4: Keep phase shading bands, removed P1/P2/P3 text labels
            if ptype != 'stable':
                for ax in [ax_hr, ax_rr]:
                    ax.axvspan(date_list[max(0, start_idx)], date_list[min(len(date_list)-1, end_idx)],
                               alpha=0.06, color=p_color, zorder=0)

    ax_rr.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax_rr.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=12))
    plt.xticks(rotation=45, ha="right")
    
    if not is_pdf:
        fig.suptitle("Daily Vital Sign Trends", fontsize=13, fontweight="700", color=txt_color, y=0.98)
    from matplotlib.lines import Line2D
    
    if is_pdf and episodes:
        # FIX 34: Severity gradient legend
        hr_legend_elements = [
            Line2D([0], [0], color=settings.candlestick_color_normal, linewidth=2, label='Normal day'),
            Line2D([0], [0], color=settings.candlestick_color_mild, linewidth=2.5, label='Brief (1-2h)'),
            Line2D([0], [0], color=settings.candlestick_color_moderate, linewidth=3, label='Sustained (3-5h)'),
            Line2D([0], [0], color=settings.candlestick_color_severe, linewidth=3.5, label='Severe (6h+)'),
            Line2D([0], [0], color=settings.candlestick_color_critical, linewidth=4, label='Critical (12h+)'),
        ]
        ax_hr.legend(handles=hr_legend_elements, loc='upper right', fontsize=settings.chart_legend_fontsize, frameon=True, framealpha=0.92, edgecolor='#CCCCCC', ncol=5, bbox_to_anchor=(1.0, 1.18))
        # R21.A: Asterisk note for coupled events on the bottom subplot.
        # Pre-R21 used fig.text(0.99, 0.02) right-aligned which overlapped the
        # rightmost rotated dates (Wimberley FP Feb 01/08, SAllen CW Feb 23/24).
        # R23 Hotfix A — daily + short-period (CriticalWeek) charts use _DAILY;
        # weekly aggregate uses _WEEKLY because its date strings rotate further
        # below the frame. clip_on=False so text below the axis bottom isn't
        # clipped by the axes bounding box.
        ax_rr.text(
            0.0, ASTERISK_LEGEND_Y_AXES_DAILY,
            '* indicates concurrent HR and breathing abnormality',
            transform=ax_rr.transAxes,
            ha='left', va='top',
            fontsize=5, style='italic', color='#666666',
            clip_on=False,
        )
    else:
        hr_legend_elements = [
            Line2D([0], [0], color=CC.HR, linewidth=3, label='Daily HR range (min to max)'),
            Line2D([0], [0], color=CC.EPISODE, linewidth=3, label='Day with episodic event'),
            Line2D([0], [0], marker='v', color='w', markerfacecolor=CC.EPISODE, markersize=6, label='Episode marker'),
            Line2D([0], [0], color='#F39C12', linewidth=1, linestyle='--', label=f'Low HR threshold ({settings.brady_hr_avg} bpm)'),
            Line2D([0], [0], color='#C0392B', linewidth=1, linestyle='--', label=f'High HR threshold ({settings.tachy_hr_avg} bpm)'),
        ]
        ax_hr.legend(handles=hr_legend_elements, loc='upper right', fontsize=settings.chart_legend_fontsize, frameon=True, framealpha=0.92, edgecolor='#CCCCCC', ncol=2, bbox_to_anchor=(1.0, 1.15))
    
    rr_legend_elements = [
        Line2D([0], [0], color=CC.RR, linewidth=3, label='Daily breathing range'),
        Line2D([0], [0], color=CC.EPISODE, linewidth=3, label='Day with episodic event'),
        Line2D([0], [0], color='#C0392B', linewidth=1, linestyle='--', label=f'Elevated breathing (> {settings.tachy_rr_avg})'),
    ]
    # R18 A: legend below the RR subplot (was overlaid 'upper right' inside the
    # plot, blocking breathing-range bars per Sajol May 4 review). Anchored
    # below the x-axis labels so it doesn't compete with data.
    ax_rr.legend(
        handles=rr_legend_elements,
        loc='upper center', bbox_to_anchor=(0.5, -0.22),
        fontsize=settings.chart_legend_fontsize, frameon=True,
        framealpha=0.92, edgecolor='#CCCCCC', ncol=3,
    )

    # R22.B: RR y-axis no longer clamped at the physiologic ceiling.

    plt.tight_layout()
    return fig


def generate_combined_chart(df: pd.DataFrame, episodes: list[Episode],
                             width: float = 10, height: float = 5.5,
                             dpi: int = 150) -> str:
    """Generate candlestick chart for web preview. Returns base64 PNG."""
    daily = _daily_agg(df)
    ep_days = _episode_date_set(episodes)
    fig = _generate_generic_candlestick(daily, ep_days, (width, height), dpi, is_pdf=False)
    
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def generate_candlestick_for_pdf(df: pd.DataFrame, episodes: list[Episode],
                                  phases: list = None, window_start=None, window_end=None) -> bytes:
    import pandas as pd
    daily = _daily_agg(df)
    ep_days = _episode_date_set(episodes)
    dpi = settings.candlestick_dpi
    
    if window_start and window_end:
        reporting_days = (pd.Timestamp(window_end).normalize() - pd.Timestamp(window_start).normalize()).days + 1
        strategy = choose_candlestick_strategy(reporting_days)
        if strategy == 'weekly':
            fig = chart_candlestick_weekly(daily, episodes, phases, window_start, window_end)
        else:
            fig = _generate_generic_candlestick(daily, ep_days, (settings.plot_width_inches, settings.candlestick_height_inches), dpi, is_pdf=True, phases=phases, episodes=episodes)
    else:
        fig = _generate_generic_candlestick(daily, ep_days, (settings.plot_width_inches, settings.candlestick_height_inches), dpi, is_pdf=True, phases=phases, episodes=episodes)
    
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=dpi)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ── Chart B: Distribution Histogram ─────────────────────────────────────────

def _generate_generic_histogram(df: pd.DataFrame, figsize: tuple[float, float], 
                                dpi: int, is_pdf: bool = False) -> plt.Figure:
    fig, (ax_hr, ax_rr) = plt.subplots(1, 2, figsize=figsize, dpi=dpi, gridspec_kw={"wspace": 0.3})
    bg = "white" if is_pdf else CC.BG
    fig.patch.set_facecolor(bg)
    
    txt_color = CC.TEXT if not is_pdf else "black"
    label_fs = 6 if is_pdf else 7
    title_fs = 7 if is_pdf else 8
    tick_fs = 5 if is_pdf else 6

    # HR Data
    hr_data = df["hr_avg"].dropna().values
    if len(hr_data) > 0:
        ax_hr.hist(hr_data, bins=25, color=CC.HR, alpha=0.7, edgecolor="white", linewidth=0.5)
        mean_hr = np.mean(hr_data)
        ax_hr.axvline(x=mean_hr, color=CC.HR, linestyle="--", linewidth=1.2)
        
        # P5/P95 Spread - R12 Fix 8: required parameters, no silent defaults
        sa_cfg = RENDER_CONFIG["spread_annotation"]
        p5, p95 = np.quantile(hr_data, 0.05), np.quantile(hr_data, 0.95)
        spread = p95 - p5

        counts, _ = np.histogram(hr_data, bins=25)
        y_max = max(counts)

        annotated = render_spread_annotation(
            ax=ax_hr,
            p5=float(p5),
            p95=float(p95),
            sample_hours=int(len(hr_data)),
            min_spread_bpm=sa_cfg["min_spread_bpm"],
            min_sample_hours=sa_cfg["min_sample_hours"],
            y_max=float(y_max),
            tick_fs=tick_fs,
        )
        if annotated:
            ax_hr.set_ylim(0, y_max * 1.45)
        else:
            ax_hr.set_ylim(0, y_max * 1.15)

        ax_hr.axvline(x=settings.brady_hr_avg, color=CC.WARNING, linestyle=":", linewidth=0.8)
        ax_hr.axvline(x=settings.tachy_hr_avg, color=CC.EPISODE, linestyle=":", linewidth=0.8)

        for ax in [ax_hr, ax_rr]:
            ax.tick_params(axis='both', labelsize=settings.chart_tick_fontsize)
            ax.xaxis.label.set_size(settings.chart_axis_label_fontsize)
            ax.yaxis.label.set_size(settings.chart_axis_label_fontsize)
        
    ax_hr.set_title("HR Distribution", fontsize=title_fs, fontweight="600", color=txt_color)
    ax_hr.set_xlabel("HR (bpm)", fontsize=label_fs, color=txt_color)
    ax_hr.set_ylabel("Hours", fontsize=label_fs, color=txt_color)
    ax_hr.tick_params(labelsize=tick_fs)
    ax_hr.spines["top"].set_visible(False)
    ax_hr.spines["right"].set_visible(False)

    # RR Data — R22.B: no upper-bound filtering. Sprint A's ingestion-side
    # noise filter handles spurious RR-without-HR samples; the distribution
    # plot shows the actual cleaned range so any residual data quality issue
    # remains visible to the clinician.
    rr_data = df["rr_avg"].dropna().values
    if len(rr_data) > 0:
        ax_rr.hist(rr_data, bins=20, color=CC.RR, alpha=0.7, edgecolor="white", linewidth=0.5)
        ax_rr.axvline(x=np.mean(rr_data), color=CC.RR, linestyle="--", linewidth=1.2)
        ax_rr.axvline(x=settings.tachy_rr_avg, color=CC.EPISODE, linestyle=":", linewidth=0.8)

    ax_rr.set_title("RR Distribution", fontsize=title_fs, fontweight="600", color=txt_color)
    ax_rr.set_xlabel("RR (breaths/min)", fontsize=label_fs, color=txt_color)
    ax_rr.tick_params(labelsize=tick_fs)
    ax_rr.spines["top"].set_visible(False)
    ax_rr.spines["right"].set_visible(False)

    plt.tight_layout()
    return fig


def generate_histogram(df: pd.DataFrame) -> str:
    fig = _generate_generic_histogram(df, (7.2, 1.7), settings.chart_dpi, is_pdf=False)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def generate_histogram_for_pdf(df: pd.DataFrame) -> bytes:
    fig = _generate_generic_histogram(df, (settings.content_width_inches, settings.histogram_height_inches), settings.chart_dpi, is_pdf=True)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


# ── Chart C: Positional Comparison ──────────────────────────────────────────

def generate_positional_chart(df: pd.DataFrame) -> str:
    """Paired bar chart showing HR and RR by location."""
    if "location" not in df.columns:
        return ""
    
    daily = df.copy()
    daily["date"] = daily["timestamp"].dt.date
    # Only Living Room and Chair
    daily = daily[daily["location"].isin(["Living Room", "Chair"])]
    if daily.empty:
        return ""
        
    agg = daily.groupby(["date", "location"]).agg({"hr_avg": "mean", "rr_avg": "mean"}).reset_index()
    agg["date"] = pd.to_datetime(agg["date"])
    
    fig, (ax_hr, ax_rr) = plt.subplots(2, 1, figsize=(settings.plot_width_inches, settings.candlestick_height_inches), dpi=settings.candlestick_dpi, sharex=True)
    fig.patch.set_facecolor(CC.BG)
    
    dates = sorted(agg["date"].unique())
    x = np.arange(len(dates))
    width = 0.35
    
    for ax, col, label, color_lr, color_ch in [
        (ax_hr, "hr_avg", "Heart Rate (bpm)", CC.HR, CC.HR_FILL),
        (ax_rr, "rr_avg", "Breathing Rate (breaths/min)", CC.RR, CC.RR_FILL),
    ]:
        ax.set_facecolor(CC.BG)
        lr_vals = [agg[(agg["date"] == d) & (agg["location"] == "Living Room")][col].values[0] if not agg[(agg["date"] == d) & (agg["location"] == "Living Room")][col].empty else 0 for d in dates]
        ch_vals = [agg[(agg["date"] == d) & (agg["location"] == "Chair")][col].values[0] if not agg[(agg["date"] == d) & (agg["location"] == "Chair")][col].empty else 0 for d in dates]
        
        ax.bar(x - width/2, lr_vals, width, label="Living Room", color=color_lr, alpha=0.8)
        ax.bar(x + width/2, ch_vals, width, label="Chair", color=color_ch, alpha=0.8)
        ax.set_ylabel(label, fontsize=7, fontweight="bold")
        ax.grid(True, axis="y", color=CC.GRID, linewidth=0.5)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(labelsize=6)

    ax_rr.set_xticks(x)
    ax_rr.set_xticklabels([d.strftime("%b %d") for d in dates], rotation=45, ha="right")
    ax_hr.set_title("Positional Comparison: Living Room vs Chair", fontsize=9, fontweight="bold")
    ax_hr.legend(fontsize=6, loc="upper right")
    
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# ── Chart D: Activity Trend ─────────────────────────────────────────────────

def generate_activity_trend_chart(df: pd.DataFrame, figsize: tuple[float, float] = (7.5, 2.2)) -> str:
    """Hours detected per day with color coding."""
    df = df.copy()
    if "location" in df.columns:
        non_bed = df[df["location"] != "Bed"]
        if not non_bed.empty:
            df = non_bed
        # If all data is Bed-only, use it as-is rather than producing an empty chart

    if df.empty:
        # No data at all — return blank chart
        fig, ax = plt.subplots(figsize=figsize, dpi=settings.chart_dpi)
        ax.text(0.5, 0.5, "No monitoring data available", ha='center', va='center', fontsize=10)
        ax.set_axis_off()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        plt.close(fig)
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    df["date"] = df["timestamp"].dt.date
    daily = df.groupby("date").size().reset_index(name="hours")
    daily["date"] = pd.to_datetime(daily["date"])

    fig, ax = plt.subplots(figsize=figsize, dpi=settings.chart_dpi)
    fig.patch.set_facecolor(CC.BG)
    ax.set_facecolor(CC.BG)
    
    colors = []
    for h in daily["hours"]:
        if h >= settings.activity_high_min: colors.append(CC.ACTIVITY_HIGH)
        elif h >= settings.activity_medium_min: colors.append(CC.ACTIVITY_MEDIUM)
        else: colors.append(CC.ACTIVITY_LOW)
        
    ax.bar(daily["date"], daily["hours"], color=colors, alpha=0.7, width=0.8)
    
    # 7-day rolling average
    daily["rolling"] = daily["hours"].rolling(window=7, min_periods=1).mean()
    ax.plot(daily["date"], daily["rolling"], color="#4B5563", linewidth=1.5, label="7d Rolling Avg")
    
    # Baseline threshold line (clinical monitoring target)
    ax.axhline(y=settings.activity_medium_min, color=CC.ACTIVITY_LOW, linestyle="--", linewidth=1.0, alpha=0.5, label="Monitoring Target")
    
    ax.set_ylim(0, 24)
    ax.set_yticks([0, 4, 8, 12, 16, 20, 24])
    ax.set_ylabel('Hours Recorded', fontsize=settings.chart_axis_label_fontsize)
    ax.axhline(24, color='#CCCCCC', linewidth=0.5, linestyle=':', zorder=0)
    
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor=settings.activity_color_green, edgecolor='none', label=f'Good coverage (>= {settings.activity_green_threshold}h/day)'),
        Patch(facecolor=settings.activity_color_amber, edgecolor='none', label=f'Moderate ({settings.activity_amber_threshold}-{settings.activity_green_threshold}h/day)'),
        Patch(facecolor=settings.activity_color_red, edgecolor='none', label=f'Low coverage (< {settings.activity_amber_threshold}h/day)'),
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=settings.chart_legend_fontsize, frameon=True, framealpha=0.95, edgecolor='#CCCCCC', ncol=1, bbox_to_anchor=(1.0, 1.0))
    ax.tick_params(axis='both', labelsize=settings.chart_tick_fontsize)
    
    ax.set_title("Daily Monitoring Activity (hours/day recorded)", fontsize=9, fontweight="bold")

    # Period-aware date axis handling
    date_min = daily["date"].min()
    date_max = daily["date"].max()
    reporting_days = (date_max - date_min).days + 1

    if reporting_days <= 14:
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    elif reporting_days <= 60:
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=7))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    elif reporting_days <= 180:
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=14))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    else:
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))

    ax.set_xlim(date_min - pd.Timedelta(days=0.5), date_max + pd.Timedelta(days=0.5))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right', fontsize=6)
    ax.tick_params(labelsize=6)
    ax.grid(True, axis="y", color=CC.GRID, linewidth=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# ── Chart E: Bed Hours (bed sensor only) ────────────────────────────────────

def generate_bed_hours_chart(
    bed_summary_df: pd.DataFrame,
    alerts_df: pd.DataFrame | None = None,
    figsize: tuple[float, float] = (10, 4),
    dpi: int = 150,
    is_pdf: bool = False,
) -> str:
    """Generate the bed hours chart for bed sensor reports.

    Args:
        bed_summary_df: DataFrame with columns: date, hours_in_bed, hr_low
        alerts_df: Optional DataFrame with columns: timestamp, alert_hr
        figsize: Figure size
        dpi: DPI for the chart
        is_pdf: If True, generate for PDF (white background)

    Returns:
        Base64-encoded PNG string
    """
    if bed_summary_df is None or bed_summary_df.empty:
        return ""

    df = bed_summary_df.copy()
    df = df.sort_values("date").reset_index(drop=True)

    fig, ax1 = plt.subplots(figsize=figsize, dpi=dpi)
    bg = "white" if is_pdf else CC.BG
    fig.patch.set_facecolor(bg)
    ax1.set_facecolor(bg)

    txt_color = "black" if is_pdf else CC.TEXT
    n = len(df)

    # Color-code bars by hours in bed
    colors = []
    for h in df["hours_in_bed"]:
        if pd.isna(h):
            colors.append("#cccccc")
        elif h > 16:
            colors.append("#ef4444")   # Red
        elif h >= 13:
            colors.append("#f59e0b")   # Amber
        else:
            colors.append("#22c55e")   # Green

    x = np.arange(n)
    bars = ax1.bar(x, df["hours_in_bed"].fillna(0), color=colors, width=0.65,
                   edgecolor="white", linewidth=0.5, zorder=3)

    # 7-day rolling average line
    if n >= 3:
        roll_avg = df["hours_in_bed"].rolling(window=min(7, n), min_periods=1).mean()
        ax1.plot(x, roll_avg, color="#8b5cf6", linewidth=2.5, zorder=5,
                 label="7d rolling avg", marker="", linestyle="-")

    # Reference thresholds
    ax1.axhline(y=13, color="#f59e0b", linestyle=":", linewidth=1, alpha=0.6, zorder=2)
    ax1.axhline(y=16, color="#ef4444", linestyle=":", linewidth=1, alpha=0.6, zorder=2)
    ax1.text(n - 0.5, 13.2, "13h", fontsize=6, color="#f59e0b", ha="right")
    ax1.text(n - 0.5, 16.2, "16h", fontsize=6, color="#ef4444", ha="right")

    ax1.set_ylabel("Hours in Bed", fontsize=8, fontweight="600", color=txt_color)
    ax1.set_title("Daily Bed Time Activity", fontsize=10, fontweight="700", color=txt_color, pad=12)

    # X-axis formatting
    date_labels = [d.strftime("%b %d") for d in df["date"]]
    ax1.set_xticks(x)
    ax1.set_xticklabels(date_labels, rotation=45, ha="right", fontsize=6)
    ax1.tick_params(axis="y", labelsize=7)

    # Secondary y-axis: HR minimum
    if "hr_low" in df.columns and df["hr_low"].notna().any():
        ax2 = ax1.twinx()
        hr_min_vals = df["hr_low"].values
        valid_mask = ~pd.isna(hr_min_vals)
        ax2.scatter(x[valid_mask], hr_min_vals[valid_mask],
                    color="#3b82f6", s=30, zorder=6, marker="o",
                    edgecolors="white", linewidths=0.5, label="HR min (bpm)")
        # Connect with thin line
        ax2.plot(x[valid_mask], hr_min_vals[valid_mask],
                 color="#3b82f6", linewidth=1, alpha=0.4, zorder=4)

        ax2.set_ylabel("HR Min (bpm)", fontsize=8, fontweight="600", color="#3b82f6")
        ax2.tick_params(axis="y", labelsize=7, colors="#3b82f6")

        # Set HR axis limits
        hr_valid = hr_min_vals[valid_mask]
        if len(hr_valid) > 0:
            ax2.set_ylim(max(30, hr_valid.min() - 5), hr_valid.max() + 10)

    # Alert markers (red triangles on alert days)
    if alerts_df is not None and not alerts_df.empty:
        alert_dates = set(alerts_df["timestamp"].dt.normalize().unique())
        for i, row in df.iterrows():
            if row["date"] in alert_dates:
                ax1.scatter(i, row["hours_in_bed"] + 0.5, marker="v",
                           color="#ef4444", s=50, zorder=7, edgecolors="white", linewidths=0.5)

    # Legend
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    legend_elements = [
        Patch(facecolor="#22c55e", edgecolor="white", label="< 13h"),
        Patch(facecolor="#f59e0b", edgecolor="white", label="13-16h"),
        Patch(facecolor="#ef4444", edgecolor="white", label="> 16h"),
        Line2D([0], [0], color="#8b5cf6", lw=2, label="7d avg"),
        Line2D([0], [0], marker="o", color="#3b82f6", lw=0, markersize=5, label="HR min"),
    ]
    if alerts_df is not None and not alerts_df.empty:
        legend_elements.append(
            Line2D([0], [0], marker="v", color="#ef4444", lw=0, markersize=6, label="Low HR alert")
        )
    ax1.legend(handles=legend_elements, loc="upper right", fontsize=6,
               framealpha=0.9, edgecolor="#e5e7eb")

    ax1.spines["top"].set_visible(False)
    ax1.grid(axis="y", alpha=0.15, zorder=1)

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def generate_bed_hours_chart_for_pdf(bed_summary_df, alerts_df=None) -> bytes:
    b64 = generate_bed_hours_chart(bed_summary_df, alerts_df, is_pdf=True)
    return base64.b64decode(b64) if b64 else b""


# ── PDF Exports ──────────────────────────────────────────────────────────────

def generate_positional_chart_for_pdf(df: pd.DataFrame) -> bytes:
    b64 = generate_positional_chart(df)
    return base64.b64decode(b64) if b64 else b""

def generate_positional_chart_compact_for_pdf(df: pd.DataFrame) -> bytes:
    """Compact positional chart for page 2 of the PDF — fits within 0.8 inches."""
    if "location" not in df.columns:
        return b""

    daily = df.copy()
    daily["date"] = daily["timestamp"].dt.date
    locs_present = [l for l in ["Bed", "Chair", "Living Room"] if l in daily["location"].values]
    if len(locs_present) < 2:
        return b""

    agg = daily.groupby("location").agg(hr_avg=("hr_avg", "mean"), rr_avg=("rr_avg", "mean")).reset_index()

    fig, axes = plt.subplots(1, 2, figsize=(7.5, 0.9), dpi=settings.chart_dpi)
    fig.patch.set_facecolor("white")

    for ax, col, title, color in [
        (axes[0], "hr_avg", "Avg HR by Sensor",  CC.HR),
        (axes[1], "rr_avg", "Avg RR by Sensor",  CC.RR),
    ]:
        ax.set_facecolor("white")
        locs = agg["location"].tolist()
        vals = agg[col].tolist()
        bars = ax.barh(locs, vals, color=color, alpha=0.75, height=0.5)
        ax.set_title(title, fontsize=6, fontweight="bold", color="black", pad=2)
        ax.tick_params(labelsize=5.5)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#D1D5DB")
        ax.spines["bottom"].set_color("#D1D5DB")
        for bar, val in zip(bars, vals):
            ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height()/2,
                    f"{val:.1f}", va="center", fontsize=5.5, color="black")

    fig.subplots_adjust(left=0.2, right=0.95, wspace=0.45, top=0.78, bottom=0.15)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=settings.chart_dpi, facecolor="white")
    plt.close(fig)
    return buf.getvalue()

def generate_activity_trend_chart_for_pdf(df: pd.DataFrame) -> bytes:
    b64 = generate_activity_trend_chart(df)
    return base64.b64decode(b64) if b64 else b""


# ── FIX 6: Episode Timeline Bar ─────────────────────────────────────────────

def chart_episode_timeline_for_pdf(episodes, start_date, end_date) -> bytes:
    """Simple horizontal timeline with colored bars for episodic events.
    
    Gray background bar spanning full period, colored bars for each episode.
    Returns PNG bytes for PDF embedding.
    """
    fig, ax = plt.subplots(figsize=(7.5, 0.45))

    total_days = (end_date - start_date).days + 1
    if total_days < 1:
        total_days = 1

    # Gray background bar
    ax.barh(0, total_days, height=0.5, color='#E8E8E8', edgecolor='none')

    # Colored bars for each episode
    for ep in episodes:
        try:
            if hasattr(ep, 'start_time'):
                ep_start = pd.Timestamp(ep.start_time)
                ep_end = pd.Timestamp(ep.end_time)
                hours = ep.duration_hours
                condition = ep.condition
            else:
                ep_start = pd.Timestamp(ep['start_time'])
                ep_end = pd.Timestamp(ep['end_time'])
                hours = ep.get('duration_hours', 1)
                condition = ep.get('condition', '')

            day_offset = (ep_start.normalize() - pd.Timestamp(start_date)).days
            day_width = max(hours / 24.0, 0.15)

            # Color by severity
            if 'Severe' in condition or 'Very' in condition:
                color = '#991B1B'   # dark red
            elif condition in ('High HR', 'Low HR'):
                color = '#EF4444'   # red
            elif condition == 'Elevated HR':
                color = '#F59E0B'   # amber
            elif condition == 'Elevated Breathing':
                color = '#E67E22'   # orange
            else:
                color = '#6B7280'   # gray

            ax.barh(0, day_width, left=max(0, day_offset), height=0.5,
                    color=color, edgecolor='white', linewidth=0.3, alpha=0.85)
        except Exception:
            continue

    # Date labels
    ax.set_xlim(-0.5, total_days - 0.5)
    if total_days <= 14:
        tick_positions = list(range(0, total_days, max(1, total_days // 7)))
    else:
        tick_positions = list(range(0, total_days, max(1, total_days // 10)))
    tick_labels = [(pd.Timestamp(start_date) + pd.Timedelta(days=d)).strftime('%b %d')
                   for d in tick_positions]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, fontsize=5.5, rotation=45, ha='right')

    ax.set_yticks([])
    ax.set_ylim(-0.35, 0.35)
    for spine in ax.spines.values():
        spine.set_visible(False)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=settings.chart_dpi,
                bbox_inches='tight', facecolor='white', pad_inches=0.02)
    plt.close(fig)
    buf.seek(0)
    return buf.read()
