"""
CardioReport — batch summary helpers.

Small pure formatting utilities shared by the batch summary table renderer
(`batch_generate.build_summary_pdf`) and the per-patient summary block /
events table column populators.
"""

from __future__ import annotations

from typing import Optional


def format_episodes_per_day(episode_count: int, period_days: float) -> str:
    """R22.C2 — render episodes-per-day as "0" / "<1" / integer.

    Sajol, May 5 call: "would be very confusing to say zero episodes for a
    day. The number is less than one... any time you say zero, everyone
    lights up. Your zero better be the right zero." So a literal zero
    episodes prints "0", a positive count whose per-day rate rounds below
    one prints "<1", and otherwise the rounded integer.
    """
    if episode_count == 0:
        return "0"
    if period_days is None or period_days <= 0:
        return "—"
    rate = episode_count / period_days
    if rate < 1:
        return "<1"
    return str(round(rate))


def _hr_for_display(value: Optional[float]) -> str:
    """Format an HR peak/min for the comment templates with physiologic guard."""
    if value is None:
        return ""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    from .narrative_ai import _clip_physiologic
    cv, was_clipped = _clip_physiologic(v, "hr_bpm")
    if not was_clipped:
        return f"{int(round(cv))}"
    return f"<{int(round(cv))}*" if v < cv else f">{int(round(cv))}*"


def _rr_for_display(value: Optional[float]) -> str:
    """R22.B — RR is no longer clipped; show raw integer."""
    if value is None:
        return ""
    try:
        return f"{int(round(float(value)))}"
    except (TypeError, ValueError):
        return ""


def _end_of_period_text(end_of_period: Optional[dict]) -> Optional[str]:
    """R26 Fix 4 — render the end-of-period clustering headline, or None when the
    patient is not end-loaded. Used to replace a bare "Stable baseline"."""
    if not end_of_period:
        return None
    from .config import END_OF_PERIOD_TEMPLATE
    return END_OF_PERIOD_TEMPLATE.format(
        condition=end_of_period.get("condition", "episodic"),
        date_range=end_of_period.get("date_range", ""),
        eps_per_day=end_of_period.get("eps_per_day", ""),
    )


def build_findings_text(
    triage: str,
    dominant_phase_type: Optional[str],
    *,
    hr_avg: Optional[float] = None,
    peak_hr: Optional[float] = None,
    min_hr: Optional[float] = None,
    rr_avg: Optional[float] = None,
    peak_rr: Optional[float] = None,
    end_of_period: Optional[dict] = None,
) -> str:
    """R22.D — single template-driven findings string.

    Same logic as the batch summary Comments cell so the per-patient summary
    block on the individual report and the cohort cell agree (no manual
    narrative). Shared here to avoid drift between the two surfaces — see
    Heet's "cross-surface count parity" note.

    R26 Fix 4 — a patient whose episodes cluster in the final window portion
    (``end_of_period`` present) never renders bare "Stable baseline"; the
    end-of-period clustering headline is surfaced instead.
    """
    from .config import settings

    templates = settings.batch_summary_comment_templates

    eop = _end_of_period_text(end_of_period)
    if str(triage).lower() == "green":
        # End-of-period clustering overrides the bare "Stable baseline".
        return eop or templates.get("stable", "Stable baseline").replace("<br/>", " ")

    tmpl = templates.get(dominant_phase_type) if dominant_phase_type else None
    if not tmpl:
        return eop or templates.get("stable", "Stable baseline").replace("<br/>", " ")

    rendered = tmpl.format(
        avg_hr=int(round(hr_avg)) if hr_avg is not None else 0,
        peak_hr=_hr_for_display(peak_hr),
        min_hr=_hr_for_display(min_hr),
        avg_rr=int(round(rr_avg)) if rr_avg is not None else 0,
        peak_rr=_rr_for_display(peak_rr),
    )
    return rendered.replace("<br/>", " ")
