import re

PDF_RENDER_FILE = "/Users/heetbarot/Documents/Cardio-io/Code/backend/pdf_render.py"

with open(PDF_RENDER_FILE, "r") as f:
    text = f.read()

# 1. Add compute_reporting_period_days to pdf_render.py
compute_days_code = """
def compute_reporting_period_days(window_start, window_end):
    import pandas as pd
    return (pd.Timestamp(window_end).normalize() - pd.Timestamp(window_start).normalize()).days + 1
"""
if "def compute_reporting_period_days" not in text:
    text = text.replace("def render_status_timeline_bar", compute_days_code + "\n\ndef build_status_timeline_segments")

# 2. Add build_status_timeline_segments, render_status_timeline_bar, render_timeline_date_axis
timeline_methods = """def build_status_timeline_segments(window_start, window_end, display_phases):
    import pandas as pd
    from .config import settings, PHASE_LABELS, _PHASE_COLOR_MAP as PHASE_COLORS
    reporting_days = compute_reporting_period_days(window_start, window_end)
    day_types = []
    current = pd.Timestamp(window_start).normalize()
    we = pd.Timestamp(window_end).normalize()
    
    while current <= we:
        phase_on_day = None
        for p in display_phases:
            if pd.Timestamp(p.get('start_date', '')).normalize() <= current <= pd.Timestamp(p.get('end_date', '')).normalize():
                phase_on_day = p
                break
        if phase_on_day:
            day_types.append({
                'date': current,
                'type': phase_on_day.get('type', 'mixed'),
                'color': PHASE_COLORS.get(phase_on_day.get('type', 'mixed'), '#F0F0F0'),
                'label': PHASE_LABELS.get(phase_on_day.get('type', 'mixed'), ''),
            })
        else:
            day_types.append({
                'date': current,
                'type': 'normal',
                'color': settings.color_normal_gap,
                'label': None,
            })
        current += pd.Timedelta(days=1)
    
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
    
    return segments

def render_status_timeline_bar(window_start, window_end, display_phases, content_width_inches, timeline_cell_style):
    from reportlab.platypus import Table, TableStyle, Paragraph
    from reportlab.lib.colors import HexColor
    from reportlab.lib.units import inch
    from .config import settings
    
    segments = build_status_timeline_segments(window_start, window_end, display_phases)
    reporting_days = compute_reporting_period_days(window_start, window_end)
    col_widths = [(s['days'] / reporting_days) * content_width_inches * inch for s in segments]
    
    cells = []
    for seg in segments:
        segment_width_inches = (seg['days'] / reporting_days) * content_width_inches
        if seg['type'] == 'normal':
            cells.append('')
        else:
            if segment_width_inches < settings.timeline_acronym_width_inches:
                label = settings.PHASE_ACRONYMS.get(seg['type'], '?')
            elif segment_width_inches < settings.timeline_abbreviated_width_inches:
                label = seg['label'].replace('Heart Rate', 'HR').replace('Breathing', 'Breath') if seg['label'] else ''
            else:
                label = seg['label']
            cells.append(Paragraph(f"<b>{label}</b>", timeline_cell_style))
    
    bar_table = Table([cells], colWidths=col_widths, rowHeights=[settings.timeline_bar_height_inches * inch])
    style_commands = [
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
    ]
    for i, seg in enumerate(segments):
        style_commands.append(('BACKGROUND', (i, 0), (i, 0), HexColor(seg['color'])))
    bar_table.setStyle(TableStyle(style_commands))
    return bar_table, segments

def render_timeline_date_axis(segments, reporting_days, content_width_inches, date_axis_style):
    import pandas as pd
    from reportlab.platypus import Table, TableStyle, Paragraph
    from reportlab.lib.units import inch
    
    if reporting_days <= 14:
        interval = 1
    elif reporting_days <= 60:
        interval = 7
    elif reporting_days <= 180:
        interval = 14
    else:
        interval = 30
        
    current = segments[0]['start_date']
    end = segments[-1]['end_date']
    date_cells = []
    col_widths = []
    
    while current <= end:
        days_in_label = min(interval, (end - current).days + 1)
        label_width = (days_in_label / reporting_days) * content_width_inches * inch
        date_cells.append(Paragraph(f"<i>{current.strftime('%b %d')}</i>", date_axis_style))
        col_widths.append(label_width)
        current += pd.Timedelta(days=days_in_label)
        
    date_axis_table = Table([date_cells], colWidths=col_widths)
    date_axis_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
    ]))
    return date_axis_table
"""

# Replace old render_status_timeline_bar with the new methods
text = re.sub(r'def render_status_timeline_bar\(.*?return merged', timeline_methods, text, flags=re.DOTALL)

# Now apply inside _build_header
# Need to replace lines 580 to 636 with the new calls.
old_implementation = r'''        ws_tmp = pd\.Timestamp\(ws_v\)\.normalize\(\)
        we_tmp = pd\.Timestamp\(we_v\)\.normalize\(\)
        total_days = max\(1, \(we_tmp - ws_tmp\)\.days \+ 1\)
        segments = render_status_timeline_bar\(ws_tmp, we_tmp, display_phases, settings\.content_width_inches\)
.*?elements\.append\(date_axis\)'''

new_implementation = '''        ws_tmp = pd.Timestamp(ws_v).normalize()
        we_tmp = pd.Timestamp(we_v).normalize()
        bar_table, segments = render_status_timeline_bar(
            ws_tmp, we_tmp, display_phases,
            settings.content_width_inches, st["phase_label"]
        )
        elements.append(bar_table)
        
        date_style = ParagraphStyle('date_ax', parent=st['legend'], alignment=0, fontSize=6)
        date_axis = render_timeline_date_axis(
            segments,
            compute_reporting_period_days(ws_tmp, we_tmp),
            settings.content_width_inches,
            date_style
        )
        elements.append(date_axis)'''

text = re.sub(old_implementation, new_implementation, text, flags=re.DOTALL)

with open(PDF_RENDER_FILE, "w") as f:
    f.write(text)

print("Updated pdf_render.py")
