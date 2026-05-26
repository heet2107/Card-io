import pandas as pd

EPISODES_FILE = "/Users/heetbarot/Documents/Cardio-io/Code/backend/episodes.py"

with open(EPISODES_FILE, "r") as f:
    text = f.read()

fix_44 = """    # ── FIX 44: Verify duration_hours matches start and end timestamps ──────────────
    for ep_dict in merged:
        expected_hours = (pd.Timestamp(ep_dict["end_time"]) - pd.Timestamp(ep_dict["start_time"])).total_seconds() / 3600 + 1
        if abs(expected_hours - ep_dict["duration_hours"]) > 1:
            ep_dict["duration_hours"] = int(expected_hours)

    # ── Step 5: Finalize and construct models ─────────────────────────────────"""

if "# ── Step 5: Finalize and construct models ─────────────────────────────────" in text:
    text = text.replace("# ── Step 5: Finalize and construct models ─────────────────────────────────", fix_44)

with open(EPISODES_FILE, "w") as f:
    f.write(text)

print("Updated episodes.py")
