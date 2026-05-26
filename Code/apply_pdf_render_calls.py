import re

PDF_RENDER_FILE = "/Users/heetbarot/Documents/Cardio-io/Code/backend/pdf_render.py"

with open(PDF_RENDER_FILE, "r") as f:
    text = f.read()

# Replace build_intelligent_key_findings calls
old_find = '''def build_intelligent_key_findings(eps, daily_summary, trajectory=None):'''
new_find = '''def build_intelligent_key_findings(eps, daily_summary, trajectory=None, window_start=None, window_end=None):'''
text = text.replace(old_find, new_find)

# Also fix the cluster_ratio inside build_intelligent_key_findings
old_cluster = '''            date_span_days = (episode_dates[-1] - episode_dates[0]).days + 1
            unique_episode_days = len(episode_dates)
            cluster_ratio = unique_episode_days / date_span_days if date_span_days > 0 else 0
            if cluster_ratio < 0.4 and date_span_days >= 5:'''
new_cluster = '''            if window_start and window_end:
                date_span_days = compute_reporting_period_days(window_start, window_end)
            else:
                date_span_days = (episode_dates[-1] - episode_dates[0]).days + 1
            unique_episode_days = len(episode_dates)
            cluster_ratio = unique_episode_days / date_span_days if date_span_days > 0 else 0
            if cluster_ratio < 0.15 and date_span_days >= 14:'''
text = text.replace(old_cluster, new_cluster)

# Update narrative_ai.py Clinical Pattern findings call
old_call = '''        trajectory_data = _v(report, 'trajectory', None)
        findings = build_intelligent_key_findings(all_eps, df, trajectory=trajectory_data)'''
new_call = '''        trajectory_data = _v(report, 'trajectory', None)
        window_start = _v(report, 'window_start')
        window_end = _v(report, 'window_end')
        findings = build_intelligent_key_findings(all_eps, df, trajectory=trajectory_data, window_start=window_start, window_end=window_end)'''
text = text.replace(old_call, new_call)

# Update candlestick generation call and caption
old_candle = '''        candle = generate_candlestick_for_pdf(df, all_eps, phases=phases)
        elements.append(Image(io.BytesIO(candle), width=settings.candlestick_width_inches * inch, height=settings.candlestick_height_inches * inch))
        elements.append(Paragraph("<i>Red bars and triangle markers indicate days with detected episodic events.</i>", st["legend"]))'''
new_candle = '''        try:
            ws = pd.Timestamp(_v(report, 'window_start')).normalize()
            we = pd.Timestamp(_v(report, 'window_end')).normalize()
            reporting_days = compute_reporting_period_days(ws, we)
            strategy = 'weekly' if reporting_days > settings.candlestick_daily_max_days else 'daily'
        except Exception:
            strategy = 'daily'
            
        candle = generate_candlestick_for_pdf(df, all_eps, phases=phases, window_start=_v(report, 'window_start'), window_end=_v(report, 'window_end'))
        
        c_height = settings.candlestick_long_period_height_inches if strategy == 'weekly' else settings.candlestick_height_inches
        elements.append(Image(io.BytesIO(candle), width=settings.candlestick_width_inches * inch, height=c_height * inch))
        
        if strategy == 'weekly':
            elements.append(Paragraph("<i>Each bar represents one week. Colors indicate episode burden during that week. Badges show total episodic hours for severe and critical weeks.</i>", st["legend"]))
        else:
            elements.append(Paragraph("<i>Red bars and triangle markers indicate days with detected episodic events.</i>", st["legend"]))'''
text = text.replace(old_candle, new_candle)

with open(PDF_RENDER_FILE, "w") as f:
    f.write(text)

print("Updated pdf_render.py")
