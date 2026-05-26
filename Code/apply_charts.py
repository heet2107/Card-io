import re
import os

charts_path = '/Users/heetbarot/Documents/Cardio-io/Code/backend/charts.py'
pdf_path = '/Users/heetbarot/Documents/Cardio-io/Code/backend/pdf_render.py'

## Patch charts.py
with open(charts_path, 'r') as f:
    c = f.read()

# Replace candlestick size and apply fonts
c = re.sub(
    r'fig, \(ax_hr, ax_rr\) = plt.subplots\(2, 1, figsize=\(7\.2, 3\.5\), dpi=settings.chart_dpi, sharex=True\)',
    """fig, (ax_hr, ax_rr) = plt.subplots(
        2, 1,
        figsize=(settings.candlestick_width_inches, settings.candlestick_height_inches),
        sharex=True,
        dpi=settings.candlestick_dpi
    )
    for ax in [ax_hr, ax_rr]:
        ax.tick_params(axis='both', labelsize=settings.chart_tick_fontsize)
        ax.xaxis.label.set_size(settings.chart_axis_label_fontsize)
        ax.yaxis.label.set_size(settings.chart_axis_label_fontsize)""",
    c
)

c = re.sub(
    r'ax_hr\.set_title\("Heart Rate \(bpm\)"\)',
    r'ax_hr.set_title("Heart Rate (bpm)", fontsize=settings.chart_title_fontsize)',
    c
)

c = re.sub(
    r'ax_rr\.set_title\("Breathing Rate \(breaths/min\)"\)',
    r'ax_rr.set_title("Breathing Rate (breaths/min)", fontsize=settings.chart_title_fontsize)',
    c
)

# Candlestick thresholds + legend
c = re.sub(
    r'ax_hr\.axhline\(_v\(settings, "brady_hr_avg", 50\), color=CC\.AMBER, linewidth=0\.8, linestyle="--", zorder=1, alpha=0\.8\)',
    r'ax_hr.axhline(_v(settings, "brady_hr_avg", 50), color=CC.AMBER, linewidth=0.8, linestyle="--", zorder=1, alpha=0.8, label=f"Low HR threshold ({_v(settings, \'brady_hr_avg\', 50)} bpm)")\n    ax_hr.legend(loc="upper right", fontsize=settings.chart_legend_fontsize, frameon=True, framealpha=0.9, ncol=2)',
    c
)

c = re.sub(
    r'ax_hr\.axhline\(_v\(settings, "tachy_hr_avg", 100\), color=CC\.RED, linewidth=0\.8, linestyle="--", zorder=1, alpha=0\.8\)',
    r'ax_hr.axhline(_v(settings, "tachy_hr_avg", 100), color=CC.RED, linewidth=0.8, linestyle="--", zorder=1, alpha=0.8, label=f"High HR threshold ({_v(settings, \'tachy_hr_avg\', 100)} bpm)")',
    c
)

c = re.sub(
    r'ax_rr\.axhline\(_v\(settings, "tachy_rr_avg", 25\), color=CC\.RED, linewidth=0\.8, linestyle="--", zorder=1, alpha=0\.8\)',
    r'ax_rr.axhline(_v(settings, "tachy_rr_avg", 25), color=CC.RED, linewidth=0.8, linestyle="--", zorder=1, alpha=0.8, label=f"Elevated breathing ({_v(settings, \'tachy_rr_avg\', 25)})")\n    ax_rr.legend(loc="upper right", fontsize=settings.chart_legend_fontsize, frameon=True, framealpha=0.9)',
    c
)

# Date labels rotation
c = re.sub(
    r'ax_rr\.set_xticklabels\(\[d\.strftime\("%b %d"\) for d in dates\], rotation=0, ha="center"\)',
    r'ax_rr.set_xticklabels([d.strftime("%b %d") for d in dates], rotation=30, ha="right", fontsize=settings.chart_tick_fontsize)',
    c
)


# Histogram patches
c = re.sub(
    r'def _generate_generic_histogram.*?def ',
    r'def ',
    c, flags=re.DOTALL
) # we don't safely do this. Let's do it manually.
