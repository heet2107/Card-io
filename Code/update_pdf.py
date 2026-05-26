import re

p_path = '/Users/heetbarot/Documents/Cardio-io/Code/backend/pdf_render.py'
with open(p_path, 'r') as f:
    text = f.read()

func_defs = """
def render_status_timeline_bar(window_start, window_end, display_phases, total_width_inches):
    import pandas as pd
    from .config import settings, PHASE_LABELS, _PHASE_COLOR_MAP
    from reportlab.lib.colors import HexColor as _hex
    ws = pd.Timestamp(window_start).normalize()
    we = pd.Timestamp(window_end).normalize()
    
    segments = []
    current_day = ws
    day_idx = 0
    
    while current_day <= we:
        phase_on_day = None
        for p in display_phases:
            p_st = pd.Timestamp(p.get('start_date')).normalize()
            p_en = pd.Timestamp(p.get('end_date')).normalize()
            if p_st <= current_day <= p_en:
                phase_on_day = p
                break
        
        if phase_on_day:
            segments.append({
                'type': phase_on_day['type'],
                'label': PHASE_LABELS.get(phase_on_day['type']),
                'color': _PHASE_COLOR_MAP.get(phase_on_day['type'], _hex("#6B7280")),
                'day_idx': day_idx,
                'days': 1,
                'date': current_day,
            })
        else:
            segments.append({
                'type': 'normal',
                'label': None,
                'color': settings.color_normal_gap,
                'day_idx': day_idx,
                'days': 1,
                'date': current_day,
            })
        
        current_day += pd.Timedelta(days=1)
        day_idx += 1
    
    merged = []
    for seg in segments:
        if merged and merged[-1]['type'] == seg['type']:
            merged[-1]['days'] += 1
        else:
            merged.append(seg.copy())
    return merged

def build_key_findings(eps, daily_summary):
    import dateutil.parser
    from .config import settings
    findings = []
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
            st_str = longest.get('start_time', '') if isinstance(longest, dict) else getattr(longest, 'start_time', '')
            dt_str = dateutil.parser.parse(str(st_str)).strftime('%b %d') if st_str else ''
            findings.append(f"Longest sustained event: {max_h:.1f}h {cond} on {dt_str}")
    
    coupled_count = 0
    for e in eps:
        c = e.get('cooccurrence', False) if isinstance(e, dict) else getattr(e, 'cooccurrence', False)
        if c: coupled_count += 1
    if coupled_count > 0:
        findings.append(f"Concurrent HR and RR abnormalities: {coupled_count} episode(s)")
    
    if daily_summary is not None:
        hr_max = daily_summary['hr_max'].max() if 'hr_max' in daily_summary.columns else daily_summary['hr_avg'].max()
        hr_min = daily_summary['hr_min'].min() if 'hr_min' in daily_summary.columns else daily_summary['hr_avg'].min()
        if hr_max > settings.tachy_hr_avg:
            findings.append(f"Peak heart rate: {hr_max:.0f} bpm")
        if hr_min < settings.brady_hr_avg:
            findings.append(f"Minimum heart rate: {hr_min:.0f} bpm")
    return findings[:3]

"""

# Insert functions right before generate_pdf
text = text.replace('def generate_pdf(', func_defs + '\ndef generate_pdf(')

# Replace Timeline Bar Logic
old_timeline_block = re.search(r'# FIX 8: Phase bar.*?elements\.append\(Spacer\(1, 10\)\)', text, re.DOTALL)
if not old_timeline_block:
    old_timeline_block = re.search(r'# FIX 8: Phase bar.*?elements\.append\(Spacer\(1, 10\)\)\n\s*elif not all_eps:.*?elements\.append\(Spacer\(1, 10\)\)', text, re.DOTALL)

new_timeline_block = """# FIX 20: Full period timeline bar
    import pandas as pd
    ws_v = _v(report, 'window_start')
    we_v = _v(report, 'window_end')
    if pd.notna(ws_v) and pd.notna(we_v):
        ws_tmp = pd.Timestamp(ws_v).normalize()
        we_tmp = pd.Timestamp(we_v).normalize()
        total_days = max(1, (we_tmp - ws_tmp).days + 1)
        segments = render_status_timeline_bar(ws_tmp, we_tmp, display_phases, settings.content_width_inches)
        
        bar_cells = []
        for seg in segments:
            cell_width = (seg['days'] / total_days) * page_w
            if seg['type'] == 'normal':
                cell = Paragraph(" ", st["phase_label"])
            else:
                p_type = seg['type']
                width_inches = cell_width / inch
                if width_inches < settings.timeline_acronym_width_inches:
                    label_txt = settings.PHASE_ACRONYMS.get(p_type, '?')
                elif width_inches < settings.timeline_abbreviated_width_inches:
                    label_txt = PHASE_LABELS.get(p_type, '?').replace('Heart Rate', 'HR').replace('Breathing', 'Breath')
                else:
                    label_txt = PHASE_LABELS.get(p_type, '?')
                cell = Paragraph(f"<b>{label_txt}</b>", st["phase_label"])
            bar_cells.append(cell)
            
        bar_table = Table([bar_cells], colWidths=[(s['days']/total_days)*page_w for s in segments])
        cmds = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
        for i, seg in enumerate(segments):
            cmds.append(("BACKGROUND", (i, 0), (i, 0), seg['color']))
        bar_table.setStyle(TableStyle(cmds))
        elements.append(bar_table)
        
        if settings.timeline_show_date_axis:
            date_cells = []
            for i in range(total_days):
                d = ws_tmp + pd.Timedelta(days=i)
                date_cells.append(Paragraph(d.strftime('%b %d'), ParagraphStyle('date', parent=st['legend'], alignment=1, fontSize=6)))
            date_axis = Table([date_cells], colWidths=[page_w/total_days]*total_days)
            date_axis.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]))
            elements.append(date_axis)
        elements.append(Spacer(1, 10))
"""
if old_timeline_block:
    text = text.replace(old_timeline_block.group(0), new_timeline_block)

# Add key findings
kf_insertion = """
        findings = build_key_findings(all_eps, df)
        if findings:
            findings_text = "<b>Key Findings:</b> " + " | ".join(findings)
            elements.append(Paragraph(findings_text, st["body_bold"]))
            elements.append(Spacer(1, 4))
        
        candle = generate_candlestick_for_pdf(df"""

text = text.replace('candle = generate_candlestick_for_pdf(df', kf_insertion)

with open(p_path, 'w') as f:
    f.write(text)
