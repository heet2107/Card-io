import re
with open("/Users/heetbarot/Documents/Cardio-io/Code/backend/narrative_ai.py", "r") as f:
    text = f.read()

bad_block = """def compute_reporting_period_days(window_start, window_end):
    import pandas as pd
    return (pd.Timestamp(window_end).normalize() - pd.Timestamp(window_start).normalize()).days + 1

def compute_data_coverage_days(dly):
    return len(dly)

"""

text = text.replace(bad_block, "")

# Add it exactly once at the top of the file
import_block = """from .episodes import Episode, EpisodeRollups
from .config import settings, PHASE_LABELS"""

top_funcs = """def compute_reporting_period_days(window_start, window_end):
    import pandas as pd
    return (pd.Timestamp(window_end).normalize() - pd.Timestamp(window_start).normalize()).days + 1

def compute_data_coverage_days(dly):
    return len(dly)
"""

text = text.replace(import_block, import_block + "\n\n" + top_funcs)

with open("/Users/heetbarot/Documents/Cardio-io/Code/backend/narrative_ai.py", "w") as f:
    f.write(text)
print("Cleaned up narrative_ai.py")
