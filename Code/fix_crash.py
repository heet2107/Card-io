import re

path = '/Users/heetbarot/Documents/Cardio-io/Code/backend/pdf_render.py'
with open(path, 'r') as f:
    text = f.read()

# Fix the extremely tight cell width in pdf_render.py:
old_logical_block = """                if width_inches < settings.timeline_acronym_width_inches:
                    label_txt = settings.PHASE_ACRONYMS.get(p_type, '?')"""

new_logical_block = """                if width_inches < 0.25:
                    label_txt = ""
                elif width_inches < settings.timeline_acronym_width_inches:
                    label_txt = settings.PHASE_ACRONYMS.get(p_type, '?')"""

text = text.replace(old_logical_block, new_logical_block)

with open(path, 'w') as f:
    f.write(text)
