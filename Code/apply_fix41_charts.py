import re

CHARTS_FILE = "/Users/heetbarot/Documents/Cardio-io/Code/backend/charts.py"

with open(CHARTS_FILE, "r") as f:
    text = f.read()

new_logic = """def choose_candlestick_strategy(reporting_days):
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
        figsize=(settings.candlestick_width_inches, settings.candlestick_long_period_height_inches),
        sharex=True,
        dpi=settings.candlestick_dpi
    )
    
    for y in settings.hr_gridline_values:
        ax1.axhline(y, color=settings.gridline_color, linewidth=settings.gridline_width, zorder=0, alpha=settings.gridline_alpha)
    for y in settings.rr_gridline_values:
        ax2.axhline(y, color=settings.gridline_color, linewidth=settings.gridline_width, zorder=0, alpha=settings.gridline_alpha)
        
    x = range(len(weekly))
    
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
            ax1.text(x[i], badge_y, badge_text, ha='center', va='bottom', fontsize=6, color=color, fontweight='bold', rotation=0)
            
    week_labels = [w.strftime('%b %d') for w in weekly['week_start']]
    label_interval = max(1, len(week_labels) // 12)
    displayed_labels = [lbl if i % label_interval == 0 else '' for i, lbl in enumerate(week_labels)]
    
    ax2.set_xticks(x)
    ax2.set_xticklabels(displayed_labels, rotation=30, ha='right', fontsize=settings.chart_tick_fontsize)
    
    ax1.axhline(settings.brady_hr_avg, color='#F39C12', linewidth=0.7, linestyle='--', zorder=1, alpha=0.7)
    ax1.axhline(settings.tachy_hr_avg, color='#C0392B', linewidth=0.7, linestyle='--', zorder=1, alpha=0.7)
    ax2.axhline(settings.tachy_rr_avg, color='#C0392B', linewidth=0.7, linestyle='--', zorder=1, alpha=0.7)
    
    legend_elements = [
        Line2D([0], [0], color=settings.candlestick_color_normal, linewidth=2, label='Normal week'),
        Line2D([0], [0], color=settings.candlestick_color_mild, linewidth=2.5, label='Brief events (1-5h)'),
        Line2D([0], [0], color=settings.candlestick_color_moderate, linewidth=3, label='Moderate (5-15h)'),
        Line2D([0], [0], color=settings.candlestick_color_severe, linewidth=3.5, label='Severe (15-40h)'),
        Line2D([0], [0], color=settings.candlestick_color_critical, linewidth=4, label='Critical (40h+)'),
    ]
    ax1.legend(handles=legend_elements, loc='upper right', fontsize=settings.chart_legend_fontsize, frameon=True, framealpha=0.92, ncol=5, bbox_to_anchor=(1.0, 1.18))
    
    ax1.set_ylabel('Heart Rate\\n(bpm)', fontsize=settings.chart_axis_label_fontsize)
    ax2.set_ylabel('Resp Rate\\n(breaths/min)', fontsize=settings.chart_axis_label_fontsize)
    
    fig.suptitle(f'Weekly Aggregated Trends ({len(weekly)} weeks)', fontsize=settings.chart_title_fontsize)
    plt.tight_layout()
    return fig

"""

# Append to charts.py
if "def chart_candlestick_weekly" not in text:
    text = text.replace("def _generate_generic_candlestick", new_logic + "\ndef _generate_generic_candlestick")

# Update generate_candlestick_for_pdf
update_target = '''def generate_candlestick_for_pdf(df: pd.DataFrame, episodes: list[Episode],
                                  phases: list = None) -> bytes:
    """Generate candlestick chart for PDF. Returns raw bytes.
    
    FIX 9: Episode markers from explicit episode date lookup.
    FIX 10: Phase number annotations on the chart.
    """
    daily = _daily_agg(df)
    ep_days = _episode_date_set(episodes)
    dpi = settings.candlestick_dpi
    fig = _generate_generic_candlestick(daily, ep_days, (settings.candlestick_width_inches, settings.candlestick_height_inches), dpi, is_pdf=True, phases=phases, episodes=episodes)'''

update_replacement = '''def generate_candlestick_for_pdf(df: pd.DataFrame, episodes: list[Episode],
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
            fig = _generate_generic_candlestick(daily, ep_days, (settings.candlestick_width_inches, settings.candlestick_height_inches), dpi, is_pdf=True, phases=phases, episodes=episodes)
    else:
        fig = _generate_generic_candlestick(daily, ep_days, (settings.candlestick_width_inches, settings.candlestick_height_inches), dpi, is_pdf=True, phases=phases, episodes=episodes)'''

text = text.replace(update_target, update_replacement)

with open(CHARTS_FILE, "w") as f:
    f.write(text)

print("Updated charts.py")
