import re
path = '/Users/heetbarot/Documents/Cardio-io/Code/backend/pdf_render.py'
with open(path, 'r') as f:
    text = f.read()

text = text.replace("from .config import settings, PHASE_LABELS, _PHASE_COLOR_MAP", "from .config import settings, PHASE_LABELS")

with open(path, 'w') as f:
    f.write(text)
