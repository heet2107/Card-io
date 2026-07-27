"""Notable Days — generated page-1 panel content (redesign, R29).

New generated content for the redesigned Trend Report: a short panel (≤3 lines)
beside the "Episodic events per day" plot, calling out the days that stand out on
their own measured data. The rules are specified here (not inferred from the
mockup), and the language is measurement-framed and subject to the same banned-
string test as the rest of the report.

Pure function of the per-window rows — the same input shape yields the same
panel on every patient and both cohorts, so nothing patient-specific is baked in.
"""
from __future__ import annotations

import pandas as pd

# Heart-rate phase types; everything else in a row is a breathing window. Kept
# local (a plain constant) so this module has no import cycle with narrative_ai
# or pdf_render.
_HR_PHASE_TYPES = {"low_hr", "high_hr", "very_high_hr"}


def _is_hr(row) -> bool:
    return (row.get("phase_type") or "") in _HR_PHASE_TYPES


def compute_notable_days(rows, window_start, window_end, phrase_fn,
                         max_entries: int = 3) -> list[dict]:
    """Return up to ``max_entries`` notable-day dicts ``{"when", "what"}``.

    ``rows`` is the full per-window list (``narrative['episode_table_rows']``);
    each row carries ``start_time``, ``phase_type``, ``severity_score``,
    ``total_hours`` and ``reduced_coverage``. ``phrase_fn(row)`` formats a single
    window as e.g. ``"<Condition> (<descriptor>, <n>h, peak <value>)"`` (reuses the
    report's own ``_finding_phrase`` so the wording matches the banner and cards).

    Selection, in priority order (never padded):
      1. Days with BOTH a heart-rate and a breathing window on the same day,
         ranked by that day's total window-hours (longest first), then earliest.
      2. The busiest day (most windows, ≥ 2), if a slot remains and it is not
         already listed.

    Each entry states the date + its day number in the period, why it qualified,
    and the most-notable window that day. The reduced-coverage caveat is appended
    when the day overlaps reduced coverage. Empty / no qualifying days → ``[]``.
    """
    if not rows:
        return []

    start = pd.Timestamp(window_start).normalize()
    by_day: dict[int, list] = {}
    for r in rows:
        try:
            idx = (pd.Timestamp(r["start_time"]).normalize() - start).days
        except Exception:
            continue
        if idx < 0:
            continue
        by_day.setdefault(idx, []).append(r)
    if not by_day:
        return []

    def _when(idx: int) -> str:
        d = start + pd.Timedelta(days=idx)
        return f"{d.strftime('%b %d')} (day {idx + 1})"

    def _worst(rs):
        return max(rs, key=lambda r: (r.get("severity_score", 0) or 0,
                                      r.get("total_hours", 0) or 0))

    def _day_hours(rs) -> int:
        return sum(int(r.get("total_hours", 0) or 0) for r in rs)

    def _caveat(rs) -> str:
        return " during reduced coverage" if any(r.get("reduced_coverage") for r in rs) else ""

    entries: list[dict] = []
    used: set[int] = set()

    # Rule 1 — co-occurrence days (both vitals outside range the same day).
    cooc = [idx for idx, rs in by_day.items()
            if any(_is_hr(r) for r in rs) and any(not _is_hr(r) for r in rs)]
    cooc.sort(key=lambda idx: (-_day_hours(by_day[idx]), idx))
    for idx in cooc:
        if len(entries) >= max_entries:
            break
        rs = by_day[idx]
        entries.append({
            "when": _when(idx),
            "what": f"both vitals outside range; {phrase_fn(_worst(rs))}{_caveat(rs)}",
        })
        used.add(idx)

    # Rule 2 — the busiest day, if a slot remains and it is genuinely busy (≥2).
    if len(entries) < max_entries:
        remaining = [idx for idx in by_day if idx not in used]
        if remaining:
            busiest = max(remaining, key=lambda idx: (len(by_day[idx]), -idx))
            rs = by_day[busiest]
            if len(rs) >= 2:
                entries.append({
                    "when": _when(busiest),
                    "what": f"busiest day, {len(rs)} windows; {phrase_fn(_worst(rs))}{_caveat(rs)}",
                })

    return entries[:max_entries]
