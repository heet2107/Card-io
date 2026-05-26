import re
import os

pdf_path = '/Users/heetbarot/Documents/Cardio-io/Code/backend/pdf_render.py'
charts_path = '/Users/heetbarot/Documents/Cardio-io/Code/backend/charts.py'

# 1. Update pdf_render.py for FIX 31
with open(pdf_path, 'r') as f:
    text = f.read()

plain_name_func = """PLAIN_NAMES = {
    'Severe bradycardia': 'Very Low Heart Rate',
    'Bradycardia': 'Low Heart Rate',
    'Tachycardia': 'High Heart Rate',
    'Elevated HR': 'Elevated Heart Rate',
    'Very High HR': 'Very High Heart Rate',
    'Tachypnea': 'Elevated Breathing',
}

def plain_name(condition_key):
    return PLAIN_NAMES.get(condition_key, condition_key)

def build_key_findings(eps, daily_summary):"""

text = text.replace("def build_key_findings(eps, daily_summary):", plain_name_func)
text = text.replace("cond = longest.get('condition', '') if isinstance(longest, dict) else getattr(longest, 'condition', '')",
                    "cond = longest.get('condition', '') if isinstance(longest, dict) else getattr(longest, 'condition', '')\n            cond = plain_name(cond)")

text = text.replace("Concurrent HR and RR abnormalities:", "Concurrent HR and breathing abnormalities:")

with open(pdf_path, 'w') as f:
    f.write(text)

# 2. Update charts.py for FIX 30 and remove "(out of 24)"
with open(charts_path, 'r') as f:
    c = f.read()

c = c.replace("ax.set_ylabel('Hours Recorded (out of 24)', fontsize=settings.chart_axis_label_fontsize)", 
              "ax.set_ylabel('Hours Recorded', fontsize=settings.chart_axis_label_fontsize)")

# Candlestick chart leged
candlestick_legend = """    from matplotlib.lines import Line2D
    
    hr_legend_elements = [
        Line2D([0], [0], color=CC.HR, linewidth=3, label='Daily HR range (min to max)'),
        Line2D([0], [0], color=CC.EPISODE, linewidth=3, label='Day with episodic event'),
        Line2D([0], [0], marker='v', color='w', markerfacecolor=CC.EPISODE, markersize=6, label='Episode marker'),
        Line2D([0], [0], color='#F39C12', linewidth=1, linestyle='--', label=f'Low HR threshold ({_v(settings, "brady_hr_avg", 50)} bpm)'),
        Line2D([0], [0], color='#C0392B', linewidth=1, linestyle='--', label=f'High HR threshold ({_v(settings, "tachy_hr_avg", 100)} bpm)'),
    ]
    ax_hr.legend(handles=hr_legend_elements, loc='upper right', fontsize=settings.chart_legend_fontsize, frameon=True, framealpha=0.92, edgecolor='#CCCCCC', ncol=2, bbox_to_anchor=(1.0, 1.15))
    
    rr_legend_elements = [
        Line2D([0], [0], color=CC.RR, linewidth=3, label='Daily breathing range'),
        Line2D([0], [0], color=CC.EPISODE, linewidth=3, label='Day with episodic event'),
        Line2D([0], [0], color='#C0392B', linewidth=1, linestyle='--', label=f'Elevated breathing (> {_v(settings, "tachy_rr_avg", 25)})'),
    ]
    ax_rr.legend(handles=rr_legend_elements, loc='upper right', fontsize=settings.chart_legend_fontsize, frameon=True, framealpha=0.92, edgecolor='#CCCCCC', ncol=3)
    
    plt.tight_layout()"""

# Replace `plt.tight_layout()` in `_generate_candlestick` safely
# We need to target the end of _generate_candlestick.
c = re.sub(r'(\s+)plt\.tight_layout\(\)\n\s+return fig\n\n\ndef generate_combined_chart', r'\n' + candlestick_legend + r'\n    return fig\n\n\ndef generate_combined_chart', c)

# Because we manually add legends here, we should strip the old ones we put in `_generate_candlestick`
c = re.sub(r'ax_hr\.legend\(loc=\'upper right\'.*?\n\s+ax_rr\.legend\(loc=\'upper right\'.*?\n', '', c)

with open(charts_path, 'w') as f:
    f.write(c)
