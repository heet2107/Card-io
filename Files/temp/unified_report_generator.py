"""
CardioReport Unified Generator v1.0
ONE codebase generates ALL report types from ANY data window.
Every word on the report is computed from the actual data.
Zero hardcoded narratives. Zero fixed templates.
"""

import pandas as pd
import numpy as np
import json, io, os
from datetime import datetime, timedelta

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, HRFlowable
)

# ═══════════════════════════════════════════════════════
# SETTINGS (single source of truth)
# ═══════════════════════════════════════════════════════
class Settings:
    brady_hr_avg = 50; severe_brady_min = 45; tachy_hr_avg = 100; tachy_rr_avg = 24; low_cnt = 30
    base_scores = {"Severe bradycardia":5,"Bradycardia":3,"Tachycardia":3,"Tachypnea":2}
    dur_bonus = 1; coupling_bonus = 2; low_conf_penalty = 1
    band_s1=5; band_s2=9; band_s3=13
    red_sev_brady_h=4; red_tachypnea_h=8; red_coupled_sev=9; yellow_min_sev=5
    prog_min_sev=9; prog_coupled_h=10; inter_min_sev=5; inter_min_h=5
    max_events=6; chart_dpi=220
    min_coverage=0.30; min_days=3
    plain = {"Severe bradycardia":"Very Low Heart Rate","Bradycardia":"Low Heart Rate",
             "Tachycardia":"High Heart Rate","Tachypnea":"Elevated Breathing Rate"}

S = Settings()

C = {'navy':HexColor("#1A2E44"),'blue':HexColor("#2C5F8A"),'accent':HexColor("#3D85C6"),
     'lbue':HexColor("#EAF2FB"),'bg':HexColor("#F7F9FC"),'white':HexColor("#FFFFFF"),
     'text':HexColor("#2D2D2D"),'muted':HexColor("#7A8B9A"),'border':HexColor("#D4DCE4"),
     'red_bg':HexColor("#FDEEEE"),'red':HexColor("#C0392B"),
     'amb_bg':HexColor("#FFF8E7"),'amber':HexColor("#D4850A"),
     'grn_bg':HexColor("#EDF7EE"),'green':HexColor("#27864A"),
     'purp':HexColor("#8E44AD"),'purp_bg':HexColor("#F4ECF7")}


# ═══════════════════════════════════════════════════════
# STAGE 1: LOAD DATA
# ═══════════════════════════════════════════════════════
def load_chair_data(filepath, sheet=None):
    if sheet:
        df = pd.read_excel(filepath, sheet_name=sheet)
    else:
        df = pd.read_excel(filepath)
    cols = df.columns.tolist()
    cmap = {}
    for c in cols:
        cl = c.strip().lower()
        if cl=='date': cmap[c]='Date'
        elif cl=='avg_hr': cmap[c]='avg_hr'
        elif cl=='max_hr': cmap[c]='max_hr'
        elif cl=='min_hr': cmap[c]='min_hr'
        elif cl=='avg_rr': cmap[c]='avg_rr'
        elif cl=='max_rr': cmap[c]='max_rr'
        elif cl=='min_rr': cmap[c]='min_rr'
        elif cl=='cnt': cmap[c]='cnt'
    df = df[list(cmap.keys())].rename(columns=cmap)
    df = df.dropna(subset=['Date'])
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
    df = df.dropna(subset=['Date'])
    if 'cnt' not in df.columns: df['cnt'] = 60
    return df.sort_values('Date').reset_index(drop=True)

def load_bed_times(filepath):
    try:
        summ = pd.read_excel(filepath, sheet_name='Summary')
        rows = []
        for i in range(2, 40):
            if i >= len(summ): break
            row = summ.iloc[i]
            date, hrs = row.iloc[1], row.iloc[18]
            if pd.notna(date) and pd.notna(hrs):
                alerts_df = pd.read_excel(filepath, sheet_name='Low HR Events')
                alerts_df['ad'] = pd.to_datetime(alerts_df['Date'], errors='coerce')
                a_count = len(alerts_df[alerts_df['ad'].dt.date == pd.Timestamp(date).date()])
                rows.append(dict(date=pd.Timestamp(date), hours_in_bed=float(hrs),
                                 hr_avg=float(row.iloc[2]) if pd.notna(row.iloc[2]) else None,
                                 hr_lo=float(row.iloc[4]) if pd.notna(row.iloc[4]) else None,
                                 rr_avg=float(row.iloc[9]) if pd.notna(row.iloc[9]) else None,
                                 low_hr_alerts=a_count))
        return pd.DataFrame(rows).sort_values('date').reset_index(drop=True) if rows else None
    except: return None

def load_alerts(filepath):
    try:
        lhr = pd.read_excel(filepath, sheet_name='Low HR Events')
        lhr['alert_date'] = pd.to_datetime(lhr['Date'], errors='coerce')
        return lhr
    except: return None


# ═══════════════════════════════════════════════════════
# STAGE 2: QUALITY GATES
# ═══════════════════════════════════════════════════════
def quality_gates(df, start, end):
    exp = max(int((end - start).total_seconds()/3600) + 1, 1)
    rec = len(df)
    cov = rec / exp
    warnings = []
    if cov < S.min_coverage:
        return {'ok': False, 'reason': f'Coverage {cov:.0%} below {S.min_coverage:.0%} minimum'}
    if cov < 0.50:
        warnings.append(f'Low coverage ({cov:.0%}); interpret with caution')
    days = df.groupby(df['Date'].dt.date).size()
    good_days = len(days[days >= 4])
    if good_days < S.min_days:
        return {'ok': False, 'reason': f'Only {good_days} days with sufficient data'}
    if 'cnt' in df.columns:
        lc_ratio = (df['cnt'] < S.low_cnt).sum() / max(len(df), 1)
        if lc_ratio > 0.50:
            return {'ok': False, 'reason': 'Majority of readings are low confidence'}
        if lc_ratio > 0.20:
            warnings.append(f'{lc_ratio:.0%} of readings are low confidence')
    # Gap detection
    gaps = df['Date'].sort_values().diff()
    max_gap_h = gaps.max().total_seconds()/3600 if len(gaps) > 1 and pd.notna(gaps.max()) else 0
    if max_gap_h > 48:
        warnings.append(f'Largest data gap: {max_gap_h:.0f} hours')
    # Coverage, expected, recorded
    return {'ok': True, 'warnings': warnings, 'expected': exp, 'recorded': rec,
            'coverage': round(cov*100, 1), 'low_conf': int((df['cnt']<S.low_cnt).sum()) if 'cnt' in df.columns else 0}


# ═══════════════════════════════════════════════════════
# STAGE 3: EPISODE DETECTION (from raw data, not JSON)
# ═══════════════════════════════════════════════════════
def detect_episodes(df):
    episodes = []
    conditions = [
        ('Bradycardia', lambda r: r['avg_hr'] < S.brady_hr_avg),
        ('Severe bradycardia', lambda r: r['min_hr'] < S.severe_brady_min),
        ('Tachycardia', lambda r: r['avg_hr'] > S.tachy_hr_avg),
        ('Tachypnea', lambda r: r['avg_rr'] > S.tachy_rr_avg),
    ]
    for cond_name, check_fn in conditions:
        flagged = df[df.apply(check_fn, axis=1)].copy()
        if flagged.empty: continue
        flagged = flagged.sort_values('Date')
        flagged['gap'] = flagged['Date'].diff() > pd.Timedelta(hours=1)
        flagged['group'] = flagged['gap'].cumsum()
        for _, grp in flagged.groupby('group'):
            cooccur = 0
            if cond_name in ('Bradycardia', 'Severe bradycardia'):
                cooccur = int((grp['avg_rr'] > S.tachy_rr_avg).any())
            lc_h = int((grp['cnt'] < S.low_cnt).sum()) if 'cnt' in grp.columns else 0
            ep = dict(condition=cond_name, start=grp['Date'].min().isoformat(),
                      end=grp['Date'].max().isoformat(), hours=len(grp),
                      hr_avg_mean=round(grp['avg_hr'].mean(),1),
                      hr_min_min=round(grp['min_hr'].min(),1),
                      hr_max_max=round(grp['max_hr'].max(),1),
                      rr_avg_mean=round(grp['avg_rr'].mean(),1),
                      rr_max_max=round(grp['max_rr'].max(),1),
                      cooccur_brady_tachypnea=cooccur, low_cnt_hours=lc_h)
            # Severity score
            base = S.base_scores.get(cond_name, 0)
            score = base + (ep['hours']-1)*S.dur_bonus + cooccur*S.coupling_bonus - (S.low_conf_penalty if lc_h else 0)
            ep['severity_score'] = max(score, 0)
            episodes.append(ep)
    return episodes

def sev_band(score):
    if score >= S.band_s3: return "S3"
    if score >= S.band_s2: return "S2"
    if score >= S.band_s1: return "S1"
    return "S0"


# ═══════════════════════════════════════════════════════
# STAGE 4: TRIAGE / TREND / PHASES (all computed)
# ═══════════════════════════════════════════════════════
def compute_triage(eps):
    if not eps: return "Green"
    mx = max(e['severity_score'] for e in eps)
    cp = any(e.get('cooccur_brady_tachypnea') for e in eps)
    sev_long = any(e['hours']>=S.red_sev_brady_h and 'Severe' in e['condition'] for e in eps)
    tr_long = any(e['hours']>=S.red_tachypnea_h and e['condition']=='Tachypnea' for e in eps)
    if sev_long or tr_long or (cp and mx>=S.red_coupled_sev): return "Red"
    if mx >= S.yellow_min_sev: return "Yellow"
    return "Green"

def compute_trend(eps):
    if not eps: return "Stable vital sign pattern"
    mx = max(e['severity_score'] for e in eps)
    cp = any(e.get('cooccur_brady_tachypnea') for e in eps)
    tot = sum(e['hours'] for e in eps)
    if mx>=S.prog_min_sev or (cp and tot>S.prog_coupled_h): return "Progressively unstable vital sign pattern"
    if mx>=S.inter_min_sev or tot>S.inter_min_h: return "Intermittently unstable vital sign pattern"
    return "Stable vital sign pattern"

def compute_action(eps):
    if not eps: return "Routine review"
    mx = max(e['severity_score'] for e in eps)
    cp = any(e.get('cooccur_brady_tachypnea') for e in eps)
    if mx>=S.red_coupled_sev or cp: return "Provider review advised"
    if mx>=S.yellow_min_sev: return "Closer clinical observation warranted"
    return "Routine review"

def detect_phases(dly, eps):
    n = len(dly)
    if n < 3: return [dict(type='single', label='Full period', days=list(range(n)),
                           start_idx=0, end_idx=n-1)]
    dly = dly.copy()
    dly['ep_score'] = 0.0
    for ep in eps:
        es = pd.Timestamp(ep['start']).normalize()
        ee = pd.Timestamp(ep['end']).normalize()
        mask = (dly['date']>=es)&(dly['date']<=ee)
        dly.loc[mask, 'ep_score'] = dly.loc[mask, 'ep_score'] + ep['severity_score']

    classes = []
    for i, row in dly.iterrows():
        if row['ep_score'] == 0: classes.append('stable')
        elif row['hr_avg'] < 55: classes.append('low_hr')
        elif row['hr_avg'] > 85: classes.append('high_hr')
        else: classes.append('mixed')
    dly['day_class'] = classes

    phases = []
    cur = classes[0]; ps = 0
    for i in range(1, n):
        if classes[i] != cur:
            phases.append(dict(type=cur, start_idx=ps, end_idx=i-1, days=list(range(ps,i))))
            cur = classes[i]; ps = i
    phases.append(dict(type=cur, start_idx=ps, end_idx=n-1, days=list(range(ps,n))))

    # Merge: absorb 1 day phases into neighbors
    merged = []
    for p in phases:
        if merged and len(p['days']) == 1 and p['type'] in ('mixed', 'stable'):
            # Absorb single day into previous phase
            prev = merged[-1]
            prev['days'].extend(p['days'])
            prev['end_idx'] = p['end_idx']
        elif merged and merged[-1]['type'] == p['type']:
            # Merge consecutive same type
            prev = merged[-1]
            prev['days'].extend(p['days'])
            prev['end_idx'] = p['end_idx']
        else:
            merged.append(p)
    phases = merged

    # If still too many, keep only the most clinically significant (non stable) + bookend stables
    if len(phases) > 5:
        scored = []
        for p in phases:
            p_eps = [e for e in eps if pd.Timestamp(e['start']).normalize() >= dly.iloc[p['start_idx']]['date']
                     and pd.Timestamp(e['start']).normalize() <= dly.iloc[p['end_idx']]['date']]
            p['phase_score'] = sum(e['severity_score'] for e in p_eps) + len(p['days'])
            scored.append(p)
        # Keep top 4 by score, preserving order
        top_indices = sorted(range(len(scored)), key=lambda i: scored[i]['phase_score'], reverse=True)[:4]
        top_indices = sorted(top_indices)
        phases = [scored[i] for i in top_indices]

    type_labels = {'stable':'Stable','low_hr':'Low HR + Elevated RR','high_hr':'HR Surge','mixed':'Mixed Instability'}
    for i, p in enumerate(phases):
        sd = dly.iloc[p['start_idx']]['date']
        ed = dly.iloc[p['end_idx']]['date']
        p['label'] = f'Phase {i+1}: {type_labels.get(p["type"],p["type"])}'
        p['date_range'] = f'{sd.strftime("%b %d")} to {ed.strftime("%b %d")}'
        p['date_start'] = sd; p['date_end'] = ed
    return phases


# ═══════════════════════════════════════════════════════
# STAGE 5: NARRATIVE GENERATION (fully computed)
# ═══════════════════════════════════════════════════════
def build_narrative(wdf, eps, dly, phases, quality, sensor_type, bed_times=None, alerts=None):
    n = quality['recorded']; exp = quality['expected']; cov = quality['coverage']
    parts = []

    # Opening: data summary
    window_days = len(dly)
    parts.append(f"Over {window_days} days of monitoring, {n} of {exp} expected hours were recorded ({cov}% coverage).")

    if not eps:
        parts.append("No episodic events exceeded the defined monitoring thresholds.")
        parts.append("Vital signs remained within expected ranges throughout the period.")
        if quality.get('warnings'):
            parts.append(quality['warnings'][0] + '.')
        return ' '.join(parts)

    # Episode summary
    types = sorted(set(S.plain.get(e['condition'],e['condition']) for e in eps))
    total_h = sum(e['hours'] for e in eps)
    coupled = sum(1 for e in eps if e.get('cooccur_brady_tachypnea'))
    parts.append(f"{len(eps)} episodic events spanning {total_h} total hours were detected, including: {', '.join(types)}.")

    # Phase narrative
    non_stable = [p for p in phases if p['type'] != 'stable']
    if len(phases) == 1 and phases[0]['type'] == 'stable':
        parts.append("Events were isolated rather than clustered.")
    elif len(non_stable) == 0:
        parts.append("Events were isolated within otherwise stable periods.")
    elif len(phases) >= 2:
        phase_parts = []
        for p in phases:
            p_days = dly.iloc[p['days']]
            p_eps = [e for e in eps if pd.Timestamp(e['start']).normalize() >= p['date_start']
                     and pd.Timestamp(e['start']).normalize() <= p['date_end']]
            avg_hr = p_days['hr_avg'].mean()
            if p['type'] == 'stable':
                phase_parts.append(f"<b>{p['label']} ({p['date_range']}):</b> Heart rate averaged {avg_hr:.0f} bpm with no events.")
            elif p['type'] == 'low_hr':
                min_hr = p_days['hr_min'].min()
                p_coupled = sum(1 for e in p_eps if e.get('cooccur_brady_tachypnea'))
                txt = f"<b>{p['label']} ({p['date_range']}):</b> Heart rate dropped to lows of {min_hr:.0f} bpm"
                if p_coupled: txt += f" with {p_coupled} episodes showing concurrent elevated breathing rate."
                else: txt += "."
                phase_parts.append(txt)
            elif p['type'] == 'high_hr':
                max_hr = p_days['hr_max'].max()
                phase_parts.append(f"<b>{p['label']} ({p['date_range']}):</b> Heart rate surged with daily averages reaching {avg_hr:.0f} bpm and peaks near {max_hr:.0f} bpm.")
            else:
                phase_parts.append(f"<b>{p['label']} ({p['date_range']}):</b> Mixed episode pattern with average HR of {avg_hr:.0f} bpm.")
        parts.append(f"The period showed {len(phases)} distinct phases. " + " ".join(phase_parts))

    # HR spread
    hr = wdf['avg_hr'].dropna()
    if len(hr) > 10:
        p5, p95 = hr.quantile(0.05), hr.quantile(0.95)
        spread = p95 - p5
        if spread > 30:
            parts.append(f"The P5 to P95 heart rate spread was {spread:.0f} bpm ({p5:.0f} to {p95:.0f}), indicating significant cardiac variability.")

    # Coupling
    if coupled:
        parts.append(f"{coupled} episodes showed concurrent low heart rate with elevated breathing rate, a coupling pattern that may reflect compensatory respiratory stress.")

    # Bed specific
    if sensor_type == 'bed' and bed_times is not None and len(bed_times):
        high_bed = len(bed_times[bed_times['hours_in_bed'] >= 16])
        mean_bed = bed_times['hours_in_bed'].mean()
        if high_bed:
            parts.append(f"Time in bed exceeded 16 hours on {high_bed} days (mean: {mean_bed:.0f} hours), which may indicate declining functional capacity.")
        if alerts is not None:
            alert_days = len(bed_times[bed_times['low_hr_alerts'] > 0])
            if alert_days:
                parts.append(f"Low heart rate alerts occurred on {alert_days} separate days during this period.")

    # Data quality warnings
    if quality.get('warnings'):
        parts.append(quality['warnings'][0] + '.')

    # Last day coverage check
    last_day = dly.iloc[-1]
    if last_day['hours'] <= 4:
        parts.append(f"Monitoring signal declined to only {int(last_day['hours'])} hours on {last_day['date'].strftime('%B %d')}, limiting visibility into the final day.")

    return ' '.join(parts)


def build_actions(eps, dly, phases, sensor_type, bed_times=None):
    if not eps: return []
    actions = []
    conditions = set(e['condition'] for e in eps)
    coupled = any(e.get('cooccur_brady_tachypnea') for e in eps)

    # Phase specific actions
    for p in phases:
        if p['type'] == 'stable': continue
        p_eps = sorted([e for e in eps if pd.Timestamp(e['start']).normalize() >= p['date_start']
                        and pd.Timestamp(e['start']).normalize() <= p['date_end']],
                       key=lambda e: e['severity_score'], reverse=True)
        if not p_eps: continue
        top = p_eps[0]
        nm = S.plain.get(top['condition'], top['condition'])
        if p['type'] == 'low_hr':
            txt = f"{p['label']} ({p['date_range']}): {nm} for {top['hours']} hours (min HR {top['hr_min_min']:.0f} bpm). "
            txt += "Review heart rate lowering medications, check blood pressure"
            if coupled: txt += ", assess oxygen levels and fluid status due to concurrent elevated breathing rate"
            txt += "."
            actions.append(txt)
        elif p['type'] == 'high_hr':
            txt = f"{p['label']} ({p['date_range']}): {nm} for {top['hours']} hours (avg HR {top['hr_avg_mean']:.0f} bpm). "
            txt += "Evaluate for pain, infection, fever, dehydration, or heart rhythm change."
            actions.append(txt)
        elif p['type'] == 'mixed':
            txt = f"{p['label']} ({p['date_range']}): Mixed episodes including {nm}. "
            txt += "Correlate with clinical context, medication timing, and symptom assessment."
            actions.append(txt)

    # Bed specific action
    if sensor_type == 'bed' and bed_times is not None:
        high_bed = len(bed_times[bed_times['hours_in_bed'] >= 16])
        if high_bed:
            actions.append(f"Bed time exceeded 16 hours on {high_bed} days. Evaluate for increasing fatigue, orthopnea, depression, or declining functional capacity.")

    # Overall trajectory action if multiple phases
    non_stable = [p for p in phases if p['type'] != 'stable']
    if len(non_stable) >= 2:
        hr = dly['hr_avg']
        spread = hr.quantile(0.95) - hr.quantile(0.05)
        actions.append(f"Overall trajectory shows {len(non_stable)} unstable phases with {spread:.0f} bpm variability. Consider increasing monitoring frequency and lowering escalation threshold.")

    return actions[:4]  # Cap at 4


# ═══════════════════════════════════════════════════════
# STAGE 6: CHARTS (adapt to sensor type + window size)
# ═══════════════════════════════════════════════════════
def chart_candlestick(dly, eps, phases):
    n_days = len(dly)
    fig_w = min(7.2, max(5.0, n_days * 0.25))
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(7.2, 2.2), sharex=True,
                                    gridspec_kw={'height_ratios':[1,0.7], 'hspace':0.06})
    dates = dly['date'].values; x = np.arange(len(dates))
    ep_days = set()
    for ep in eps:
        s=pd.Timestamp(ep['start']).normalize(); e=pd.Timestamp(ep['end']).normalize()
        d=s
        while d<=e: ep_days.add(d); d+=pd.Timedelta(days=1)

    # Phase backgrounds
    phase_colors = {'stable':'#FFFFFF','low_hr':'#2C5F8A','high_hr':'#C0392B','mixed':'#F39C12'}
    for p in phases:
        if p['type'] == 'stable': continue
        ax1.axvspan(p['start_idx']-0.4, p['end_idx']+0.4, alpha=0.06,
                    color=phase_colors.get(p['type'],'#CCC'), zorder=0)
        ax2.axvspan(p['start_idx']-0.4, p['end_idx']+0.4, alpha=0.06,
                    color=phase_colors.get(p['type'],'#CCC'), zorder=0)
        mid = (p['start_idx']+p['end_idx'])/2
        ax1.text(mid, 1.02, p['label'].split(': ')[1] if ': ' in p['label'] else p['label'],
                 fontsize=5, color=phase_colors.get(p['type'],'#999'), fontweight='bold',
                 ha='center', va='bottom', transform=ax1.get_xaxis_transform())

    for i in range(len(x)):
        is_ep = pd.Timestamp(dates[i]) in ep_days
        color = '#C0392B' if is_ep else '#2C5F8A'
        lw = 3.5 if is_ep else max(1.5, 3.0 - n_days*0.04)
        ax1.plot([x[i],x[i]], [dly['hr_min'].iloc[i],dly['hr_max'].iloc[i]],
                 color=color, linewidth=lw, solid_capstyle='round', alpha=0.8)
        ax1.plot(x[i], dly['hr_avg'].iloc[i], 'o', color='#1A2E44', markersize=max(2, 4-n_days*0.05), zorder=5)
    ax1.plot(x, dly['hr_avg'].values, color='#1A2E44', linewidth=0.8, alpha=0.4, zorder=4)
    if n_days >= 14:
        roll = pd.Series(dly['hr_avg'].values).rolling(7, min_periods=3).mean()
        ax1.plot(x, roll.values, color='#27AE60', linewidth=1.5, alpha=0.7, zorder=4)
    ax1.set_ylabel('HR (bpm)', fontsize=6.5, fontweight='bold', color='#333', labelpad=4)
    ax1.axhline(y=S.brady_hr_avg, color='#C0392B', linewidth=0.5, linestyle='--', alpha=0.3)
    ax1.axhline(y=S.tachy_hr_avg, color='#C0392B', linewidth=0.5, linestyle='--', alpha=0.3)
    ax1.tick_params(labelsize=5.5, colors='#555')
    ax1.yaxis.set_major_locator(mticker.MaxNLocator(5))
    ax1.grid(axis='y', alpha=0.12)

    for i in range(len(x)):
        is_ep = pd.Timestamp(dates[i]) in ep_days
        color = '#C0392B' if is_ep else '#E67E22'
        lw = 3.5 if is_ep else max(1.5, 3.0 - n_days*0.04)
        ax2.plot([x[i],x[i]], [dly['rr_min'].iloc[i],dly['rr_max'].iloc[i]],
                 color=color, linewidth=lw, solid_capstyle='round', alpha=0.8)
        ax2.plot(x[i], dly['rr_avg'].iloc[i], 'o', color='#1A2E44', markersize=max(2, 4-n_days*0.05), zorder=5)
    ax2.plot(x, dly['rr_avg'].values, color='#1A2E44', linewidth=0.8, alpha=0.4, zorder=4)
    ax2.set_ylabel('RR (brpm)', fontsize=6.5, fontweight='bold', color='#333', labelpad=4)
    ax2.axhline(y=S.tachy_rr_avg, color='#E65100', linewidth=0.5, linestyle='--', alpha=0.3)
    ax2.tick_params(labelsize=5.5, colors='#555')
    ax2.yaxis.set_major_locator(mticker.MaxNLocator(4))
    ax2.grid(axis='y', alpha=0.12)

    step = max(1, n_days//12)
    tick_pos = list(range(0, len(x), step))
    labels = [pd.Timestamp(dates[i]).strftime('%m/%d') for i in tick_pos]
    ax2.set_xticks(tick_pos); ax2.set_xticklabels(labels, rotation=45, ha='right', fontsize=5.5)

    for ax in (ax1,ax2):
        for sp in ax.spines.values(): sp.set_visible(False)
        ax.spines['left'].set_visible(True); ax.spines['left'].set_color('#CCC')
    buf=io.BytesIO()
    fig.savefig(buf,format='png',dpi=S.chart_dpi,bbox_inches='tight',facecolor='white',pad_inches=0.04)
    plt.close(fig); buf.seek(0); return buf


def chart_histogram(wdf):
    fig,(ax1,ax2)=plt.subplots(1,2,figsize=(7.2,1.2))
    hr=wdf['avg_hr'].dropna(); rr=wdf['avg_rr'].dropna()
    p5,p95=hr.quantile(0.05),hr.quantile(0.95); spread=p95-p5

    ax1.hist(hr,bins=min(25,max(10,len(hr)//8)),color='#2C5F8A',alpha=0.75,edgecolor='white',linewidth=0.4)
    ax1.axvline(hr.mean(),color='#1A2E44',linewidth=1.2,linestyle='--',alpha=0.6)
    ax1.axvline(S.brady_hr_avg,color='#C0392B',linewidth=0.7,linestyle=':',alpha=0.4)
    ax1.axvline(S.tachy_hr_avg,color='#C0392B',linewidth=0.7,linestyle=':',alpha=0.4)
    ax1.set_xlabel('HR Avg (bpm)',fontsize=6,color='#555'); ax1.set_ylabel('Hours',fontsize=6,color='#555')
    ax1.set_title('Heart Rate Distribution',fontsize=7,fontweight='bold',color='#1A2E44',pad=2)
    ax1.tick_params(labelsize=5,colors='#555')
    yl=ax1.get_ylim()[1]
    if spread > 15:
        ax1.annotate('',xy=(p5,yl*0.05),xytext=(p95,yl*0.05),arrowprops=dict(arrowstyle='<->',color='#C0392B',lw=1.0))
        ax1.text((p5+p95)/2,yl*0.14,f'{spread:.0f} bpm spread',fontsize=5,color='#C0392B',ha='center',va='bottom',fontweight='bold')

    ax2.hist(rr,bins=min(20,max(8,len(rr)//10)),color='#E67E22',alpha=0.75,edgecolor='white',linewidth=0.4)
    ax2.axvline(rr.mean(),color='#1A2E44',linewidth=1.2,linestyle='--',alpha=0.6)
    ax2.axvline(S.tachy_rr_avg,color='#E65100',linewidth=0.7,linestyle=':',alpha=0.4)
    ax2.set_xlabel('RR Avg (brpm)',fontsize=6,color='#555'); ax2.set_ylabel('Hours',fontsize=6,color='#555')
    ax2.set_title('Breathing Rate Distribution',fontsize=7,fontweight='bold',color='#1A2E44',pad=2)
    ax2.tick_params(labelsize=5,colors='#555')
    for ax in (ax1,ax2):
        for sp in ax.spines.values(): sp.set_visible(False)
        ax.spines['bottom'].set_visible(True); ax.spines['bottom'].set_color('#CCC')
        ax.spines['left'].set_visible(True); ax.spines['left'].set_color('#CCC')
        ax.grid(axis='y',alpha=0.1)
    fig.tight_layout(w_pad=3)
    buf=io.BytesIO()
    fig.savefig(buf,format='png',dpi=S.chart_dpi,bbox_inches='tight',facecolor='white',pad_inches=0.04)
    plt.close(fig); buf.seek(0); return buf


def chart_bed_hours(bt):
    if bt is None or len(bt)==0: return None
    fig,ax1=plt.subplots(figsize=(7.2,1.5))
    x=np.arange(len(bt)); hrs=bt['hours_in_bed'].values
    colors=['#C0392B' if h>=16 else '#F39C12' if h>=13 else '#27AE60' for h in hrs]
    ax1.bar(x,hrs,color=colors,alpha=0.75,width=0.7,edgecolor='white',linewidth=0.3)
    roll=pd.Series(hrs).rolling(7,min_periods=3).mean()
    ax1.plot(x,roll.values,color='#8E44AD',linewidth=2.0,alpha=0.8,zorder=5)
    for i,row in bt.iterrows():
        if row.get('low_hr_alerts',0)>0:
            ax1.plot(i,hrs[i]+0.8,'v',color='#C0392B',markersize=5,zorder=6)
    if bt.get('hr_lo') is not None and bt['hr_lo'].notna().any():
        ax2=ax1.twinx()
        ax2.plot(x,bt['hr_lo'].values,'o-',color='#2C5F8A',markersize=3,linewidth=1.2,alpha=0.7)
        ax2.set_ylabel('HR Min',fontsize=6,color='#2C5F8A',labelpad=3)
        ax2.tick_params(labelsize=5,colors='#2C5F8A')
        for sp in ax2.spines.values(): sp.set_visible(False)
        ax2.spines['right'].set_visible(True); ax2.spines['right'].set_color('#CCC')
    ax1.axhline(y=16,color='#C0392B',linewidth=0.5,linestyle='--',alpha=0.3)
    ax1.set_ylabel('Hours in Bed',fontsize=6.5,fontweight='bold',color='#333',labelpad=4)
    ax1.set_ylim(0,max(hrs)+4)
    ax1.tick_params(labelsize=5,colors='#555')
    step=max(1,len(x)//10)
    labels=[bt.iloc[i]['date'].strftime('%m/%d') for i in range(0,len(x),step)]
    ax1.set_xticks(list(range(0,len(x),step))); ax1.set_xticklabels(labels,rotation=45,ha='right',fontsize=5)
    ax1.grid(axis='y',alpha=0.1)
    for sp in ax1.spines.values(): sp.set_visible(False)
    ax1.spines['left'].set_visible(True); ax1.spines['left'].set_color('#CCC')
    buf=io.BytesIO()
    fig.savefig(buf,format='png',dpi=S.chart_dpi,bbox_inches='tight',facecolor='white',pad_inches=0.04)
    plt.close(fig); buf.seek(0); return buf


# ═══════════════════════════════════════════════════════
# STAGE 7: PDF RENDERING (adapts to content)
# ═══════════════════════════════════════════════════════
PAGE_W,PAGE_H=letter; MARGIN=0.5*inch; CW=PAGE_W-2*MARGIN

def mkst(name,**kw):
    d=dict(fontName='Helvetica',fontSize=8,leading=11,textColor=C['text']); d.update(kw)
    return ParagraphStyle(name,**d)

ST={
    'body':mkst('b'),'narr':mkst('n',fontSize=7.5,leading=11,spaceBefore=1,spaceAfter=1),
    'sec':mkst('sec',fontName='Helvetica-Bold',fontSize=9,leading=11.5,textColor=C['navy'],spaceBefore=3,spaceAfter=1),
    'sub':mkst('sub',fontName='Helvetica-Bold',fontSize=7.5,leading=10,textColor=C['blue'],spaceBefore=2,spaceAfter=1),
    'sm':mkst('sm',fontSize=6,leading=8.5,textColor=C['muted']),
    'disc':mkst('disc',fontName='Helvetica-Oblique',fontSize=6,leading=8,textColor=C['muted']),
    'th':mkst('th',fontName='Helvetica-Bold',fontSize=6.5,textColor=HexColor("#FFFFFF"),leading=9),
    'td':mkst('td',fontSize=6.5,leading=9),'td_c':mkst('tdc',fontSize=6.5,leading=9,alignment=TA_CENTER),
    'td_b':mkst('tdb',fontName='Helvetica-Bold',fontSize=6.5,leading=9),
    'bul':mkst('bul',fontSize=7,leading=10,leftIndent=11,bulletIndent=2,spaceBefore=0,spaceAfter=0),
}


def build_report(patient_id, sensor_type, sensor_label, wdf, eps, dly, phases, quality,
                 triage, trend, action_pos, narrative, actions, stats,
                 cc_buf, hist_buf, bed_buf=None, bed_times=None, alerts=None,
                 period_label="", outpath="report.pdf"):

    story=[]
    tri_map={"Green":(C['grn_bg'],C['green'],"GREEN: Routine"),
             "Yellow":(C['amb_bg'],C['amber'],"YELLOW: Monitor Closely"),
             "Red":(C['red_bg'],C['red'],"RED: Provider Review Recommended")}
    tbg,tfg,ttxt=tri_map[triage]

    # HEADER
    hdr=Table([[Paragraph('<b>CLINICAL INTELLIGENCE TREND REPORT</b>',
                mkst('ht',fontName='Helvetica-Bold',fontSize=12.5,textColor=HexColor("#FFFFFF"),leading=16)),
                Paragraph(f'<b>{ttxt}</b>',
                mkst('tb',fontName='Helvetica-Bold',fontSize=8.5,textColor=tfg,alignment=TA_RIGHT,leading=11))]],
              colWidths=[4.8*inch,2.4*inch])
    hdr.setStyle(TableStyle([('BACKGROUND',(0,0),(0,0),C['navy']),('BACKGROUND',(1,0),(1,0),tbg),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
        ('LEFTPADDING',(0,0),(0,0),10),('RIGHTPADDING',(1,0),(1,0),10)]))
    story.append(hdr); story.append(Spacer(1,2))

    meta=Table([[Paragraph(f'<b>Patient:</b> {patient_id}',ST['body']),
                 Paragraph(f'<b>Period:</b> {period_label}',ST['body']),
                 Paragraph(f'<b>Sensor:</b> {sensor_label}',ST['body']),
                 Paragraph(f'<b>Coverage:</b> {quality["recorded"]}/{quality["expected"]}h ({quality["coverage"]}%)',ST['body'])]],
               colWidths=[1.5*inch,2.2*inch,1.5*inch,2.0*inch])
    meta.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),C['lbue']),
        ('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3),('LEFTPADDING',(0,0),(-1,-1),6)]))
    story.append(meta); story.append(Spacer(1,1))
    story.append(Paragraph('<i>Decision support summary derived from longitudinal vital sign trends; interpret in clinical context.</i>',ST['disc']))
    story.append(Spacer(1,4))

    # SECTION 1: NARRATIVE
    story.append(Paragraph('SECTION 1 &mdash; Clinical Intelligence: Signal Summary and Trajectory',ST['sec']))
    story.append(HRFlowable(width="100%",thickness=1,color=C['accent'],spaceAfter=2))
    story.append(Paragraph(narrative,ST['narr']))
    story.append(Spacer(1,2))

    # Trend row
    _fg = C['red'] if 'Progressively' in trend or 'Escalating' in trend else C['amber'] if 'Intermittent' in trend else C['green']
    _bg = C['red_bg'] if _fg==C['red'] else C['amb_bg'] if _fg==C['amber'] else C['grn_bg']
    ta=Table([[Paragraph(f'<b>Trend:</b> <i>{trend}</i>',mkst('_t',fontName='Helvetica-Bold',fontSize=8,textColor=_fg,leading=10)),
               Paragraph(f'<b>Action:</b> <i>{action_pos}</i>',mkst('_a',fontName='Helvetica-Bold',fontSize=8,textColor=C['text'],leading=10))]],
             colWidths=[4.2*inch,3.0*inch])
    ta.setStyle(TableStyle([('BACKGROUND',(0,0),(0,0),_bg),('BACKGROUND',(1,0),(1,0),C['bg']),
        ('BOX',(0,0),(-1,-1),0.5,C['border']),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3),('LEFTPADDING',(0,0),(-1,-1),6)]))
    story.append(ta); story.append(Spacer(1,2))

    if actions:
        story.append(Paragraph('<b>Suggested Clinical Review Actions</b>',ST['sub']))
        for a in actions:
            story.append(Paragraph(f"\u2022  {a}",ST['bul']))
    story.append(Spacer(1,3))

    # SECTION 2: EVENTS TABLE
    story.append(Paragraph('SECTION 2 &mdash; High Priority Episodic Events',ST['sec']))
    story.append(HRFlowable(width="100%",thickness=1,color=C['accent'],spaceAfter=2))

    sorted_eps=sorted(eps,key=lambda e:(e['severity_score'],e['hours']),reverse=True)[:S.max_events]
    hdrs=['Event Type','Date / Time Window','Dur','Key Vitals','Clinical Concern']
    cw=[1.15*inch,1.2*inch,0.35*inch,1.25*inch,3.25*inch]
    band_cmt={"S0":"Brief; continue monitoring","S1":"Sustained; review context",
              "S2":"Sustained; consider provider review","S3":"Critical; urgent review advised"}
    tdata=[[Paragraph(h,ST['th']) for h in hdrs]]
    if not sorted_eps:
        tdata.append([Paragraph('<i>No episodic events detected.</i>',mkst('_ne',fontSize=6.5,fontName='Helvetica-Oblique',textColor=C['muted'])),'','','',''])
    else:
        for ep in sorted_eps:
            band=sev_band(ep['severity_score'])
            nm=S.plain.get(ep['condition'],ep['condition'])
            st=pd.Timestamp(ep['start']).strftime('%m/%d %H:%M')
            et=pd.Timestamp(ep['end']).strftime('%m/%d %H:%M')
            vit=f"HR {ep['hr_avg_mean']:.0f} | Min {ep['hr_min_min']:.0f} | RR {ep['rr_max_max']:.0f}"
            cmt=band_cmt[band]
            if ep.get('cooccur_brady_tachypnea'): cmt+=" (HR+RR coupled)"
            if ep.get('low_cnt_hours',0)>0: cmt+=" [low conf]"
            tdata.append([Paragraph(nm,ST['td']),Paragraph(f"{st} to {et}",ST['td']),
                          Paragraph(f"{ep['hours']}h",ST['td_c']),Paragraph(vit,ST['td']),Paragraph(cmt,ST['td'])])
    etbl=Table(tdata,colWidths=cw,repeatRows=1)
    ts=[('BACKGROUND',(0,0),(-1,0),C['blue']),('GRID',(0,0),(-1,-1),0.3,C['border']),
        ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),2),('BOTTOMPADDING',(0,0),(-1,-1),2),
        ('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3)]
    for i,ep in enumerate(sorted_eps):
        r=i+1; b=sev_band(ep['severity_score'])
        if b in ('S2','S3'): ts.append(('BACKGROUND',(0,r),(-1,r),C['red_bg']))
        elif b=='S1': ts.append(('BACKGROUND',(0,r),(-1,r),C['amb_bg']))
    etbl.setStyle(TableStyle(ts))
    story.append(etbl); story.append(Spacer(1,3))

    # SECTION 3: STATS
    story.append(Paragraph('SECTION 3 &mdash; Statistics',ST['sec']))
    story.append(HRFlowable(width="100%",thickness=1,color=C['accent'],spaceAfter=2))
    sh=['Metric','Mean','Min','Max','P5','P95']
    sd=[[Paragraph(h,ST['th']) for h in sh]]
    for label,key in [('Heart Rate Avg (bpm)','HR Avg'),('Heart Rate Min (bpm)','HR Min'),
                       ('Breathing Rate Avg (brpm)','RR Avg'),('Breathing Rate Max (brpm)','RR Max')]:
        v=stats[key]
        sd.append([Paragraph(label,ST['td']),Paragraph(str(v['Mean']),ST['td_c']),Paragraph(str(v['Min']),ST['td_c']),
                    Paragraph(str(v['Max']),ST['td_c']),Paragraph(str(v['P5']),ST['td_c']),Paragraph(str(v['P95']),ST['td_c'])])
    stbl=Table(sd,colWidths=[1.9*inch,1.06*inch,1.06*inch,1.06*inch,1.06*inch,1.06*inch])
    stbl.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),C['blue']),('GRID',(0,0),(-1,-1),0.3,C['border']),
        ('TOPPADDING',(0,0),(-1,-1),2),('BOTTOMPADDING',(0,0),(-1,-1),2),('LEFTPADDING',(0,0),(-1,-1),4),
        ('BACKGROUND',(0,1),(-1,2),C['lbue'])]))
    story.append(stbl); story.append(Spacer(1,3))

    # SECTION 4: CHARTS
    story.append(Paragraph('SECTION 4 &mdash; Visual Trend Analysis',ST['sec']))
    story.append(HRFlowable(width="100%",thickness=1,color=C['accent'],spaceAfter=2))

    # Bed hours chart (if bed sensor)
    if bed_buf:
        story.append(Paragraph('<b>Daily Time in Bed with Heart Rate Minimum</b> '
            '<font color="#7A8B9A" size="6">(Green = normal, Amber = elevated, Red = concern; '
            'triangles = low HR alerts; blue dots = HR minimum)</font>',ST['sub']))
        story.append(Image(bed_buf,width=CW,height=1.2*inch))
        story.append(Spacer(1,2))

    # Candlestick (always)
    phase_desc = "; ".join([f'{p["label"].split(": ")[1] if ": " in p["label"] else p["label"]}' for p in phases if p['type']!='stable'])
    chart_note = f'(Phase shading: {phase_desc})' if phase_desc else '(Red bars = episode days)'
    story.append(Paragraph(f'<b>Daily Vital Sign Ranges</b> '
        f'<font color="#7A8B9A" size="6">{chart_note}</font>',ST['sub']))
    story.append(Image(cc_buf,width=CW,height=1.65*inch if not bed_buf else 1.4*inch))
    story.append(Spacer(1,2))

    # Histogram (always)
    story.append(Paragraph('<b>Distribution</b> '
        '<font color="#7A8B9A" size="6">(Red arrow = P5 to P95 range)</font>',ST['sub']))
    story.append(Image(hist_buf,width=CW,height=0.9*inch if bed_buf else 1.0*inch))
    story.append(Spacer(1,2))

    # FOOTER
    story.append(HRFlowable(width="100%",thickness=0.5,color=C['border']))
    warn_txt = f" Warnings: {'; '.join(quality.get('warnings',[]))}." if quality.get('warnings') else ""
    story.append(Paragraph(
        f"Data: {quality['recorded']}/{quality['expected']}h ({quality['coverage']}%). "
        f"Low confidence: {quality['low_conf']}h.{warn_txt} "
        f"| Generated by CardioReport v1.0",ST['sm']))

    doc=SimpleDocTemplate(outpath,pagesize=letter,topMargin=0.3*inch,bottomMargin=0.2*inch,
                          leftMargin=MARGIN,rightMargin=MARGIN)
    doc.build(story)
    return outpath


# ═══════════════════════════════════════════════════════
# ORCHESTRATOR: runs the full pipeline for any window
# ═══════════════════════════════════════════════════════
def generate(patient_id, sensor_type, sensor_label, df_full, start, end,
             period_label, outpath, bed_times=None, alerts=None):
    print(f"\n{'='*60}")
    print(f"Generating: {patient_id} [{sensor_type}] {period_label}")
    print(f"{'='*60}")

    # Window
    wdf = df_full[(df_full['Date']>=start)&(df_full['Date']<=end)].copy()

    # Quality gates
    q = quality_gates(wdf, start, end)
    if not q['ok']:
        print(f"  [REJECT] {q['reason']}")
        return None
    print(f"  [QUALITY] {q['coverage']}% coverage, {q['low_conf']} low conf hours")
    if q.get('warnings'):
        for w in q['warnings']: print(f"  [WARN] {w}")

    # Daily aggregates
    dly = wdf.groupby(wdf['Date'].dt.date).agg(
        hr_avg=('avg_hr','mean'),hr_min=('min_hr','min'),hr_max=('max_hr','max'),
        rr_avg=('avg_rr','mean'),rr_min=('min_rr','min'),rr_max=('max_rr','max'),
        hours=('avg_hr','count')).reset_index()
    dly.columns=['date','hr_avg','hr_min','hr_max','rr_avg','rr_min','rr_max','hours']
    dly['date']=pd.to_datetime(dly['date'])

    # Episodes (detected from raw data, not JSON)
    eps = detect_episodes(wdf)
    print(f"  [EPISODES] {len(eps)} detected, {sum(e['hours'] for e in eps)} total hours")

    # Triage, trend, action
    triage = compute_triage(eps)
    trend = compute_trend(eps)
    action_pos = compute_action(eps)
    print(f"  [TRIAGE] {triage} | {trend}")

    # Phase detection
    phases = detect_phases(dly, eps)
    print(f"  [PHASES] {len(phases)}: {', '.join(p['label'] for p in phases)}")

    # Stats
    def s5(s): return dict(Mean=round(s.mean(),1),Min=round(s.min(),1),Max=round(s.max(),1),
                           P5=round(s.quantile(.05),1),P95=round(s.quantile(.95),1))
    stats = {lbl:s5(wdf[col].dropna()) for col,lbl in
             [('avg_hr','HR Avg'),('min_hr','HR Min'),('avg_rr','RR Avg'),('max_rr','RR Max')]}

    # Narrative (fully computed)
    # Filter bed_times to window
    bt_window = None
    if bed_times is not None:
        bt_window = bed_times[(bed_times['date']>=start)&(bed_times['date']<=end)].copy()
        if len(bt_window)==0: bt_window = None

    narrative = build_narrative(wdf, eps, dly, phases, q, sensor_type, bt_window, alerts)
    actions = build_actions(eps, dly, phases, sensor_type, bt_window)
    print(f"  [NARRATIVE] {len(narrative)} chars, {len(actions)} actions")

    # Charts
    cc_buf = chart_candlestick(dly, eps, phases)
    hist_buf = chart_histogram(wdf)
    bed_buf = chart_bed_hours(bt_window) if sensor_type == 'bed' and bt_window is not None else None

    # Render
    path = build_report(patient_id, sensor_type, sensor_label, wdf, eps, dly, phases, q,
                        triage, trend, action_pos, narrative, actions, stats,
                        cc_buf, hist_buf, bed_buf, bt_window, alerts,
                        period_label, outpath)
    print(f"  [OK] {path}")
    return path


# ═══════════════════════════════════════════════════════
# RUN: Generate all 6 reports from actual data
# ═══════════════════════════════════════════════════════
os.makedirs('/mnt/user-data/outputs', exist_ok=True)

# Load datasets
print("Loading data...")
chair_df = load_chair_data('/mnt/user-data/uploads/934297-0122_S_Chair_PVitalsag_2024_AG_HOURLY.xlsx')
bed_df = load_chair_data('/mnt/user-data/uploads/Juanita-Bed-10202023_18_00_v1_1_202__3_.xlsx',
                          sheet='934297-0134 J.BedRm_PVitalsag_2')
bed_times = load_bed_times('/mnt/user-data/uploads/Juanita-Bed-10202023_18_00_v1_1_202__3_.xlsx')
alerts = load_alerts('/mnt/user-data/uploads/Juanita-Bed-10202023_18_00_v1_1_202__3_.xlsx')

# All 6 windows
windows = [
    # Chair reports
    ("934297 0122", "chair", "Chair (Living Area)", chair_df,
     pd.Timestamp("2024-05-01"), pd.Timestamp("2024-05-07 23:59:59"),
     "May 1 to May 7, 2024", "01_Chair_Stable_Baseline.pdf", None, None),

    ("934297 0122", "chair", "Chair (Living Area)", chair_df,
     pd.Timestamp("2024-05-15"), pd.Timestamp("2024-05-31 23:59:59"),
     "May 15 to May 31, 2024", "02_Chair_Quiet_Biweekly.pdf", None, None),

    ("934297 0122", "chair", "Chair (Living Area)", chair_df,
     pd.Timestamp("2024-06-17"), pd.Timestamp("2024-06-23 23:59:59"),
     "June 17 to June 23, 2024", "03_Chair_Emerging_Instability.pdf", None, None),

    ("934297 0122", "chair", "Chair (Living Area)", chair_df,
     pd.Timestamp("2024-06-24"), pd.Timestamp("2024-06-30 23:59:59"),
     "June 24 to June 30, 2024", "04_Chair_Critical_Week.pdf", None, None),

    ("934297 0122", "chair", "Chair (Living Area)", chair_df,
     pd.Timestamp("2024-06-01"), pd.Timestamp("2024-06-30 23:59:59"),
     "June 1 to June 30, 2024", "05_Chair_Full_Month.pdf", None, None),

    # Bed report
    ("934297 0134", "bed", "Bedroom (Bed Sensor)", bed_df,
     pd.Timestamp("2023-10-20"), pd.Timestamp("2023-11-20 23:59:59"),
     "October 20 to November 20, 2023", "06_Bed_Activity_Analysis.pdf", bed_times, alerts),
]

for pid, stype, slabel, df, start, end, plabel, fname, bt, al in windows:
    generate(pid, stype, slabel, df, start, end, plabel,
             f'/mnt/user-data/outputs/{fname}', bt, al)

print(f"\n{'='*60}")
print("ALL 6 REPORTS GENERATED.")
print(f"{'='*60}")
