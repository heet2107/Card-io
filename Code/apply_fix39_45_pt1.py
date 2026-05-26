import os

CONFIG_FILE = "backend/config.py"
PDF_RENDER_FILE = "backend/pdf_render.py"
NARRATIVE_AI_FILE = "backend/narrative_ai.py"
CHARTS_FILE = "backend/charts.py"
EPISODES_FILE = "backend/episodes.py"

def apply_config():
    with open(CONFIG_FILE, "r") as f:
        content = f.read()

    new_config = """    # Candlestick rendering strategy thresholds
    candlestick_daily_max_days: int = 21
    candlestick_weekly_max_days: int = 90
    candlestick_aggregate_above: int = 90
    candlestick_long_period_height_inches: float = 3.0
    full_period_allow_3_pages: bool = True
"""
    if "candlestick_daily_max_days" not in content:
        content = content.replace("    timeline_bar_height_inches: float = 0.5", "    timeline_bar_height_inches: float = 0.45")
        content = content.replace("    timeline_acronym_width_inches: float = 0.6", "    timeline_acronym_width_inches: float = 0.5")
        content = content.replace("    timeline_abbreviated_width_inches: float = 1.2", "    timeline_abbreviated_width_inches: float = 1.0")
        
        content = content.split("    PHASE_ACRONYMS: dict = {")[0] + new_config + "\n    PHASE_ACRONYMS: dict = {" + content.split("    PHASE_ACRONYMS: dict = {")[1]
        
        with open(CONFIG_FILE, "w") as f:
            f.write(content)
        print("Updated config.py")

apply_config()
