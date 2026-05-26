import re

charts_path = '/Users/heetbarot/Documents/Cardio-io/Code/backend/charts.py'
pdf_path = '/Users/heetbarot/Documents/Cardio-io/Code/backend/pdf_render.py'

with open(charts_path, 'r') as f:
    c = f.read()

# --- FIX 23: Dimensions & Fonts ---
# Candlestick
c = c.replace('figsize=(7.2, 3.5), dpi=settings.chart_dpi', 'figsize=(settings.candlestick_width_inches, settings.candlestick_height_inches), dpi=settings.candlestick_dpi')
c = c.replace('ax_hr.set_title("Heart Rate (bpm)")', 'ax_hr.set_title("Heart Rate (bpm)", fontsize=settings.chart_title_fontsize)')
c = c.replace('ax_rr.set_title("Breathing Rate (breaths/min)")', 'ax_rr.set_title("Breathing Rate (breaths/min)", fontsize=settings.chart_title_fontsize)')

font_injector_candle = """    for ax in [ax_hr, ax_rr]:
        ax.tick_params(axis='both', labelsize=settings.chart_tick_fontsize)
        ax.xaxis.label.set_size(settings.chart_axis_label_fontsize)
        ax.yaxis.label.set_size(settings.chart_axis_label_fontsize)
        
    ax_hr.set_title("Heart Rate (bpm)", fontsize=settings.chart_title_fontsize)"""
c = c.replace('ax_hr.set_title("Heart Rate (bpm)", fontsize=settings.chart_title_fontsize)', font_injector_candle)


# --- FIX 27: Candlestick Legends ---
label_brady_hr = r'ax_hr.axhline(_v(settings, "brady_hr_avg", 50), color=CC.AMBER, linewidth=0.8, linestyle="--", zorder=1, alpha=0.8, label=f"Low HR threshold ({_v(settings, \'brady_hr_avg\', 50)} bpm)")'
c = re.sub(r'ax_hr\.axhline.*?brady_hr_avg.*?\)', label_brady_hr, c, count=1)

label_tachy_hr = r'ax_hr.axhline(_v(settings, "tachy_hr_avg", 100), color=CC.RED, linewidth=0.8, linestyle="--", zorder=1, alpha=0.8, label=f"High HR threshold ({_v(settings, \'tachy_hr_avg\', 100)} bpm)")'
c = re.sub(r'ax_hr\.axhline.*?tachy_hr_avg.*?\)', label_tachy_hr, c, count=1)

label_tachy_rr = r'ax_rr.axhline(_v(settings, "tachy_rr_avg", 25), color=CC.RED, linewidth=0.8, linestyle="--", zorder=1, alpha=0.8, label=f"Elevated breathing ({_v(settings, \'tachy_rr_avg\', 25)})")'
c = re.sub(r'ax_rr\.axhline.*?tachy_rr_avg.*?\)', label_tachy_rr, c, count=1)

# Add candlestick legends
leg_candle = """    ax_hr.legend(loc='upper right', fontsize=settings.chart_legend_fontsize, frameon=True, framealpha=0.9, ncol=2)
    ax_rr.legend(loc='upper right', fontsize=settings.chart_legend_fontsize, frameon=True, framealpha=0.9)
    fig.patch.set_facecolor"""
c = c.replace("fig.patch.set_facecolor", leg_candle, 1)

# --- FIX 29: Candlestick Date Labels ---
c = re.sub(r'ax_rr\.set_xticklabels.*?\]\)', r'ax_rr.set_xticklabels([d.strftime("%b %d") for d in dates], rotation=30, ha="right", fontsize=settings.chart_tick_fontsize)', c)


# --- FIX 23: Histograms ---
c = c.replace('figsize=(settings.content_width_inches, settings.histogram_height_inches), dpi=dpi', 'figsize=(settings.histogram_width_inches, settings.histogram_height_inches), dpi=settings.histogram_dpi')
# In `_generate_generic_histogram`, we should make sure we inject the font sizes.
hist_font = """    for ax in [ax_hr, ax_rr]:
        ax.tick_params(axis='both', labelsize=settings.chart_tick_fontsize)
        ax.xaxis.label.set_size(settings.chart_axis_label_fontsize)
        ax.yaxis.label.set_size(settings.chart_axis_label_fontsize)
        
    ax_hr.set_title"""
c = c.replace("ax_hr.set_title", hist_font, 1)
c = c.replace("ax_hr.set_title('Heart Rate Distribution')", "ax_hr.set_title('Heart Rate Distribution', fontsize=settings.chart_title_fontsize)")
c = c.replace("ax_rr.set_title('Breathing Rate Distribution')", "ax_rr.set_title('Breathing Rate Distribution', fontsize=settings.chart_title_fontsize)")

# --- FIX 26: Histogram Legends ---
# Replace ax_hr.axvline lines
c = re.sub(r'ax_hr\.axvline\(hr\.mean\(\), color=CC\.HR_LINE, linestyle="--", linewidth=1\.5\)', 
          r'ax_hr.axvline(hr.mean(), color=CC.HR_LINE, linestyle="--", linewidth=1.5, label="Mean")', c)
c = re.sub(r'ax_hr\.axvline\(_v\(settings, "brady_hr_avg", 50\), color=CC\.AMBER, linestyle=":", linewidth=1\.2\)',
          r'ax_hr.axvline(_v(settings, "brady_hr_avg", 50), color=CC.AMBER, linestyle=":", linewidth=1.2, label=f"Low HR ({_v(settings, \'brady_hr_avg\', 50)})")', c)
c = re.sub(r'ax_hr\.axvline\(_v\(settings, "tachy_hr_avg", 100\), color=CC\.RED, linestyle=":", linewidth=1\.2\)',
          r'ax_hr.axvline(_v(settings, "tachy_hr_avg", 100), color=CC.RED, linestyle=":", linewidth=1.2, label=f"High HR ({_v(settings, \'tachy_hr_avg\', 100)})")', c)

# Replace ax_rr.axvline lines
c = re.sub(r'ax_rr\.axvline\(rr\.mean\(\), color=CC\.RR_LINE, linestyle="--", linewidth=1\.5\)',
          r'ax_rr.axvline(rr.mean(), color=CC.RR_LINE, linestyle="--", linewidth=1.5, label="Mean")', c)
c = re.sub(r'ax_rr\.axvline\(_v\(settings, "tachy_rr_avg", 25\), color=CC\.RED, linestyle=":", linewidth=1\.2\)',
          r'ax_rr.axvline(_v(settings, "tachy_rr_avg", 25), color=CC.RED, linestyle=":", linewidth=1.2, label=f"Elevated ({_v(settings, \'tachy_rr_avg\', 25)})")', c)

hist_leg = """    ax_hr.legend(fontsize=settings.chart_legend_fontsize, loc='upper right', frameon=True, framealpha=0.9)
    ax_rr.legend(fontsize=settings.chart_legend_fontsize, loc='upper right', frameon=True, framealpha=0.9)
    fig.patch.set_facecolor"""
c = c.replace("fig.patch.set_facecolor", hist_leg, 1)


# --- FIX 23 & 24 & 25 & 29: Activity Chart ---
c = c.replace('figsize=(settings.content_width_inches, settings.activity_chart_height_inches)', 'figsize=(settings.activity_width_inches, settings.activity_height_inches), dpi=settings.activity_dpi')

act_lock_y = """    ax.set_ylim(0, 24)
    ax.set_yticks([0, 4, 8, 12, 16, 20, 24])
    ax.set_ylabel('Hours Recorded (out of 24)', fontsize=settings.chart_axis_label_fontsize)
    ax.axhline(24, color='#CCCCCC', linewidth=0.5, linestyle=':', zorder=0)
    
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor=settings.activity_color_green, edgecolor='none', label=f'Good coverage (>= {settings.activity_green_threshold}h/day)'),
        Patch(facecolor=settings.activity_color_amber, edgecolor='none', label=f'Moderate ({settings.activity_amber_threshold}-{settings.activity_green_threshold}h/day)'),
        Patch(facecolor=settings.activity_color_red, edgecolor='none', label=f'Low coverage (< {settings.activity_amber_threshold}h/day)'),
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=settings.chart_legend_fontsize, frameon=True, framealpha=0.95, edgecolor='#CCCCCC', ncol=1, bbox_to_anchor=(1.0, 1.0))
    ax.tick_params(axis='both', labelsize=settings.chart_tick_fontsize)
    
    ax.set_title"""
c = c.replace("ax.set_title", act_lock_y, 1)
c = c.replace("ax.set_title('Daily Monitoring Activity (hours/day recorded)')", "ax.set_title('Daily Monitoring Activity (hours/day recorded)', fontsize=settings.chart_title_fontsize)")
c = c.replace("ax.set_ylabel('Hours', fontsize=9)", "")

# Activity Date Labels FIX 29
c = re.sub(r'ax\.set_xticklabels.*?\]\)', r'ax.set_xticklabels([d.strftime("%b %d") for d in dates], rotation=30, ha="right", fontsize=settings.chart_tick_fontsize)', c)


with open(charts_path, 'w') as f:
    f.write(c)


## Patch pdf_render.py for Image sizing
with open(pdf_path, 'r') as f:
    p = f.read()

p = re.sub(
    r'elements\.append\(Image\(io\.BytesIO\(candle\), width=settings\.content_width_inches \* inch, height=settings\.candlestick_page1_height_inches \* inch\)\)',
    r'elements.append(Image(io.BytesIO(candle), width=settings.candlestick_width_inches * inch, height=settings.candlestick_height_inches * inch))',
    p
)

p = re.sub(
    r'elements\.append\(Image\(io\.BytesIO\(hist\), width=page_w, height=hist_h\)\)',
    r'elements.append(Image(io.BytesIO(hist), width=settings.histogram_width_inches * inch, height=settings.histogram_height_inches * inch))',
    p
)

p = re.sub(
    r'elements\.append\(Image\(io\.BytesIO\(act\), width=page_w, height=act_h\)\)',
    r'elements.append(Image(io.BytesIO(act), width=settings.activity_width_inches * inch, height=settings.activity_height_inches * inch))',
    p
)

with open(pdf_path, 'w') as f:
    f.write(p)
