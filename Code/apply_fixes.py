import re
import os

pdf_path = '/Users/heetbarot/Documents/Cardio-io/Code/backend/pdf_render.py'
charts_path = '/Users/heetbarot/Documents/Cardio-io/Code/backend/charts.py'

# 1. Update charts.py figure sizes
with open(charts_path, 'r') as f:
    c = f.read()

# Replace hardcoded heights with the dynamic ones
c = re.sub(
    r'_generate_generic_histogram\(df, \(7\.5, [0-9.]+\)',
    r'_generate_generic_histogram(df, (settings.content_width_inches, settings.histogram_height_inches)',
    c
)

c = re.sub(
    r'generate_activity_trend_chart\(df, figsize=\(7\.5, [0-9.]+\)\)',
    r'generate_activity_trend_chart(df, figsize=(settings.content_width_inches, settings.activity_chart_height_inches))',
    c
)

with open(charts_path, 'w') as f:
    f.write(c)

# 2. Update pdf_render.py
with open(pdf_path, 'r') as f:
    p = f.read()

# Make histogram and activity chart heights use settings
p = re.sub(
    r'hist_h\s*=\s*1\.05 \* inch if is_multisensor else 1\.2 \* inch',
    r'hist_h = settings.histogram_height_inches * inch',
    p
)
p = re.sub(
    r'act_h\s*=\s*1\.5 \* inch if is_multisensor else 1\.8 \* inch',
    r'act_h = settings.activity_chart_height_inches * inch',
    p
)

# And now inject the render_status_timeline_bar logic and update pdf_render.py for FIX 20 and 21
with open(pdf_path, 'w') as f:
    f.write(p)
