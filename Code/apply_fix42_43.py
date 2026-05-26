import os
import re

NARRATIVE_AI_FILE = "/Users/heetbarot/Documents/Cardio-io/Code/backend/narrative_ai.py"

with open(NARRATIVE_AI_FILE, 'r') as f:
    text = f.read()

# ADD compute functions
funcs = """
def compute_reporting_period_days(window_start, window_end):
    import pandas as pd
    return (pd.Timestamp(window_end).normalize() - pd.Timestamp(window_start).normalize()).days + 1

def compute_data_coverage_days(dly):
    return len(dly)

"""

if "def compute_reporting_period_days" not in text:
    text = text.replace("import pandas as pd\n", "import pandas as pd\n" + funcs)

# FIX 42 build_specific_action_posture
new_action = '''def build_specific_action_posture(eps, phases, triage, counts, trajectory=None):
    """
    Generate finding-specific action guidance that ESCALATES WITH TRIAGE.
    """
    if triage == 'GREEN':
        return "Routine monitoring. No specific intervention indicated."
    
    longest_episode = max(eps, key=lambda e: e.duration_hours) if eps else None
    coupled_count = sum(1 for e in eps if e.cooccurrence)
    total_episode_hours = counts.display_total_hours if hasattr(counts, 'display_total_hours') else sum(e.duration_hours for e in eps)
    total_episodes = counts.display_episode_count if hasattr(counts, 'display_episode_count') else len(eps)
    
    has_sustained_low_hr = any(
        e.condition in ('Bradycardia', 'Severe bradycardia') and e.duration_hours >= 4
        for e in eps
    )
    has_sustained_high_hr = any(
        e.condition in ('Tachycardia',) and e.duration_hours >= 4
        for e in eps
    )
    has_sustained_breathing = any(
        e.condition == 'Tachypnea' and e.duration_hours >= 4
        for e in eps
    )
    has_very_low_sustained = any(
        e.condition == 'Severe bradycardia' and e.duration_hours >= 6
        for e in eps
    )
    
    if triage == 'RED':
        if has_very_low_sustained and coupled_count > 0:
            return (
                f"Urgent: Sustained very low heart rate with concurrent breathing "
                f"abnormality across {coupled_count} episode(s). "
                f"Total episodic burden: {total_episode_hours}h. "
                f"Immediate provider review and medication reconciliation advised."
            )
        elif has_very_low_sustained:
            return (
                f"Urgent: Persistent very low heart rate pattern with "
                f"{longest_episode.duration_hours}h longest sustained event. "
                f"Total episodic burden: {total_episode_hours}h across {total_episodes} events. "
                f"Provider review and medication assessment advised."
            )
        elif has_sustained_high_hr and has_sustained_breathing:
            return (
                f"Urgent: Concurrent sustained elevated heart rate and breathing. "
                f"Total episodic burden: {total_episode_hours}h. "
                f"Evaluate for infection, fluid overload, or respiratory compromise. "
                f"Provider review advised."
            )
        elif has_sustained_low_hr:
            return (
                f"Urgent: Recurrent sustained low heart rate pattern "
                f"({total_episodes} episodes, {total_episode_hours}h total). "
                f"Provider review, medication reconciliation, and symptom assessment advised."
            )
        elif has_sustained_high_hr:
            return (
                f"Urgent: Sustained elevated heart rate pattern "
                f"({total_episodes} episodes, {total_episode_hours}h total). "
                f"Assess for infection, pain, hydration, cardiac workup advised."
            )
        else:
            return (
                f"Urgent: High episodic burden detected "
                f"({total_episodes} events, {total_episode_hours}h). "
                f"Provider review advised within 24 hours."
            )
    
    elif triage == 'YELLOW':
        if has_very_low_sustained and coupled_count > 0:
            return (
                f"Sustained very low heart rate with concurrent breathing abnormality "
                f"({coupled_count} coupled episode(s)). "
                f"Suggest provider review within 24 hours and medication assessment."
            )
        elif has_very_low_sustained:
            return (
                f"Sustained very low heart rate detected ({longest_episode.duration_hours}h duration). "
                f"Review heart rate lowering medications and consider provider consultation."
            )
        elif has_sustained_low_hr and coupled_count > 0:
            return (
                f"Recurrent low heart rate episodes with concurrent breathing changes. "
                f"Closer observation and medication review suggested."
            )
        elif has_sustained_low_hr:
            return (
                f"Recurrent low heart rate episodes ({total_episodes} events). "
                f"Review medication timing and assess patient symptoms."
            )
        elif has_sustained_high_hr and has_sustained_breathing:
            return (
                f"Concurrent elevated heart rate and breathing pattern. "
                f"Evaluate for infection, fluid status, or respiratory compromise."
            )
        elif has_sustained_high_hr:
            return (
                f"Sustained elevated heart rate ({longest_episode.duration_hours}h duration). "
                f"Assess for pain, infection, hydration, or activity correlation."
            )
        elif has_sustained_breathing:
            return (
                f"Sustained elevated breathing pattern. "
                f"Assess respiratory status and consider underlying cause."
            )
        else:
            return "Monitor for recurring patterns. Continue closer observation."
    
    if trajectory and trajectory.get('direction') == 'worsening' and trajectory.get('magnitude') == 'significant':
        return "Worsening episode burden detected. Consider earlier provider review."
    
    return "Monitor for recurring patterns. No immediate intervention indicated."'''

# Replace old specific action posture
import re
text = re.sub(r'def build_specific_action_posture\(.*?\):.*?(?=\n\n(?:#|def))', new_action, text, flags=re.DOTALL)

# FIX 43 build_trajectory_line
new_traj = '''def build_trajectory_line(trajectory):
    if trajectory is None:
        return "<i>Insufficient prior data for trajectory comparison.</i>"
    
    direction = trajectory['direction']
    prior_eps = trajectory['prior']['episode_count']
    current_eps = trajectory['current']['episode_count']
    prior_hrs = int(trajectory['prior']['episode_hours'])
    current_hrs = int(trajectory['current']['episode_hours'])
    
    prior_window_str = (
        trajectory['prior_window'][0].strftime('%b %d') + " to " +
        trajectory['prior_window'][1].strftime('%b %d')
    )
    
    if direction == 'worsening':
        color = settings.color_episode_red
        arrow = "↑"
        summary = f"Episodes: {prior_eps} → {current_eps} | Hours: {prior_hrs}h → {current_hrs}h"
    elif direction == 'improving':
        color = "#27864A"
        arrow = "↓"
        summary = f"Episodes: {prior_eps} → {current_eps} | Hours: {prior_hrs}h → {current_hrs}h"
    else:
        color = "#D4850A"
        arrow = "→"
        summary = f"Episodes: {prior_eps} → {current_eps} | Hours: {prior_hrs}h → {current_hrs}h"
    
    return (
        f"<font color='{color}'><b>Trajectory {arrow}:</b></font> "
        f"{summary} "
        f"<i>(vs. prior window {prior_window_str})</i>"
    )'''

text = re.sub(r'def build_trajectory_line\(.*?\):.*?(?=\n\n(?:#|def))', new_traj, text, flags=re.DOTALL)

with open(NARRATIVE_AI_FILE, 'w') as f:
    f.write(text)
print("Updated narrative_ai.py")
