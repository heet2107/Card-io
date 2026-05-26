import re

charts_path = '/Users/heetbarot/Documents/Cardio-io/Code/backend/charts.py'
with open(charts_path, 'r') as f:
    c = f.read()

c = c.replace('_v(settings, "brady_hr_avg", 50)', 'settings.brady_hr_avg')
c = c.replace('_v(settings, "tachy_hr_avg", 100)', 'settings.tachy_hr_avg')
c = c.replace('_v(settings, "tachy_rr_avg", 25)', 'settings.tachy_rr_avg')

with open(charts_path, 'w') as f:
    f.write(c)
