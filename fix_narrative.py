import re

with open('/Users/heetbarot/Documents/Cardio-io/Code/backend/narrative_ai.py', 'r') as f:
    code = f.read()

# Fix 1: remove closing_parts coverage_str
code = re.sub(
    r"    # FIX 3: Coverage — use build_coverage_string with positional_stats\n.*?closing_parts\.append\(f\"Monitoring period: \{window_days\} days\. \{coverage_str\}\.\"\)\n",
    "",
    code,
    flags=re.DOTALL
)

# Fix 2: Modify phase lines table
# Replace the code generating phase_lines with phase_table_rows
old_phase_lines = """    # ── FIX 3+4+8: Build opening + numbered phase lines ──
    phase_lines = []

    if not display_phases:
        # No displayable phases — patient is essentially normal
        opening = (
            f"{rollups.total_events} episodic events spanning {total_ep_hours} total hours "
            f"detected: {', '.join(types_set)}. "
            f"Vital signs remained within expected ranges with isolated deviations."
        )
    elif len(display_phases) == 1:
        p = display_phases[0]
        label = PHASE_LABELS.get(p.get("type"), p.get("label", "Phase"))
        ph_hr = p.get("hr_avg", 0)
        date_range = p.get("date_range", "")
        opening = (
            f"{rollups.total_events} episodic events spanning {total_ep_hours} total hours "
            f"detected: {', '.join(types_set)}. "
            f"Single phase of {label.lower()} (average {ph_hr:.0f} bpm, {date_range})."
        )
    else:
        opening = (
            f"{rollups.total_events} episodic events spanning {total_ep_hours} total hours "
            f"detected: {', '.join(types_set)}. "
            f"Through {len(display_phases)} distinct phases:"
        )

        # FIX 4: Numbered phase descriptions (trimmed, type-specific)
        for idx, p in enumerate(display_phases, 1):
            p_type = p.get("type", "normal")
            date_range = p.get("date_range", "")
            label = PHASE_LABELS.get(p_type, p_type)
            if not label:
                continue
            ph_hr = p.get("hr_avg", 0)

            # Compute phase-specific stats from episodes
            p_start = pd.Timestamp(p["start_date"])
            p_end = pd.Timestamp(p["end_date"]) + pd.Timedelta(days=1)
            p_eps = [e for e in episodes
                     if pd.Timestamp(e.start_time) >= p_start
                     and pd.Timestamp(e.start_time) < p_end]

            desc = _phase_description(p_type, label, date_range, ph_hr, p_eps, hr_stats, rr_stats)
            if desc:
                phase_lines.append(f"{idx}. {desc}")"""

new_phase_table = """    # ── FIX 7: Calculate total episodes that actually appear in displayed phases
    total_eps_in_phases = sum(
        len([e for e in episodes if pd.Timestamp(e.start_time).normalize() >= pd.Timestamp(p['start_date'])
             and pd.Timestamp(e.start_time).normalize() <= pd.Timestamp(p['end_date'])])
        for p in display_phases
    )
    
    types_str = ', '.join(types_set)
    opening = f"{total_eps_in_phases} episodic events spanning {total_ep_hours} total hours detected: {types_str}."

    if not display_phases:
        opening += " Vital signs remained within expected ranges with isolated deviations."
    
    phase_table_rows = []
    for p in display_phases:
        p_start = pd.Timestamp(p["start_date"])
        p_end = pd.Timestamp(p["end_date"]) + pd.Timedelta(days=1)  # the phase logic
        p_eps = [e for e in episodes if pd.Timestamp(e.start_time) >= p_start and pd.Timestamp(e.start_time) < p_end]

        is_hr_phase = p.get('type') in ('low_hr', 'very_low_hr', 'elevated_hr', 'high_hr', 'very_high_hr')
        is_rr_phase = p.get('type') == 'elevated_rr'

        ph_hr = p.get('hr_avg', 0)
        ph_rr = p.get('rr_avg', 0)
        
        # We need peak HR or RR. For episodes, we extract Max HR/RR.
        if is_hr_phase:
            if 'low' in p.get('type', ''):
                hr_mins = _extract_vitals(p_eps, 'Min HR')
                min_hr = min(hr_mins) if hr_mins else hr_stats.min
                peak = f"{min_hr:.0f} bpm"
            else:
                hr_maxs = _extract_vitals(p_eps, 'Max HR') or _extract_vitals(p_eps, 'Max')
                max_hr = max(hr_maxs) if hr_maxs else hr_stats.max
                peak = f"{max_hr:.0f} bpm"
            avg = f"{ph_hr:.0f} bpm"
        elif is_rr_phase:
            rr_maxs = _extract_vitals(p_eps, 'Max RR') or _extract_vitals(p_eps, 'RR')
            max_rr = max(rr_maxs) if rr_maxs else rr_stats.max
            peak = f"{max_rr:.0f} brpm"
            avg = f"{ph_rr:.0f} brpm"
        else:
            peak = "—"
            avg = "—"

        sustained_hours = sum(e.duration_hours for e in p_eps)
        
        d_start = pd.Timestamp(p['start_date'])
        d_end = pd.Timestamp(p['end_date'])
        if d_start == d_end:
            date_str = d_start.strftime('%b %d')
        else:
            date_str = f"{d_start.strftime('%b %d')} to {d_end.strftime('%b %d')}"

        phase_table_rows.append({
            'category': PHASE_LABELS.get(p.get('type'), p.get('type')),
            'peak': peak,
            'sustained_hours': sustained_hours,
            'sustained_str': f"{sustained_hours}h",
            'average': avg,
            'date': date_str,
            'episodes': len(p_eps),
        })"""

code = code.replace(old_phase_lines, new_phase_table)

# Replace the output dictionary
old_dict = """    narrative_dict = {
        'opening': opening,
        'phase_lines': phase_lines,
        'closing': closing,
    }

    # ── FIX 2: Build phase-aware actions with per-phase vitals ──
    actions = _build_phase_actions("""

new_dict = """    narrative_dict = {
        'opening': opening,
        'phase_table_rows': phase_table_rows,
        'closing': closing,
        'trend': trend_assessment,
        'action_posture': action_posture,
    }

    # ── FIX 2: Build phase-aware actions with per-phase vitals ──
    actions = _build_phase_actions("""

code = code.replace(old_dict, new_dict)

with open('/Users/heetbarot/Documents/Cardio-io/Code/backend/narrative_ai.py', 'w') as f:
    f.write(code)

