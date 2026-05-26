#!/usr/bin/env python3
"""Vitals → Clinical Intelligence Reporter (reusable template)

Generates:
  A) Nurse Dashboard (shift-friendly)
  B) Physician/Cardiology Summary
  C) Automated RPM Summary (daily/weekly)
Plus plot PNGs for portal display.

Input: hourly aggregates with datetime + HR/RR min/max/avg (and optional cnt).
"""

import argparse, os, yaml
import pandas as pd, numpy as np, matplotlib.pyplot as plt
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

def load_vitals(path: str) -> pd.DataFrame:
    if path.lower().endswith('.csv'):
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path)

    # Prefer XL-time if present (your current format)
    time_col=None
    for c in df.columns:
        if str(c).strip().lower()=='xl-time':
            time_col=c; break
    if time_col is None:
        for c in df.columns:
            s=str(c).strip().lower()
            if s in ['datetime','timestamp','date_time','date/time']:
                time_col=c; break
    if time_col is None:
        for c in df.columns:
            if 'time' in str(c).lower():
                time_col=c; break
    if time_col is None:
        time_col=df.columns[0]

    def find_col(keys):
        keys=[k.lower() for k in keys]
        for k in keys:
            for c in df.columns:
                if str(c).strip().lower()==k:
                    return c
        for k in keys:
            for c in df.columns:
                if k in str(c).strip().lower():
                    return c
        return None

    avg_hr=find_col(['avg_hr','avg hr','avg_hr'])
    min_hr=find_col(['min_hr','min hr'])
    max_hr=find_col(['max_hr','max hr'])
    avg_rr=find_col(['avg_rr','avg rr'])
    min_rr=find_col(['min_rr','min rr'])
    max_rr=find_col(['max_rr','max rr'])
    cnt=find_col(['cnt','count','samples','n'])

    keep=[time_col, avg_hr, min_hr, max_hr, avg_rr, min_rr, max_rr, cnt]
    keep=[c for c in keep if c is not None]
    d=df[keep].copy()
    rename={time_col:'datetime', avg_hr:'hr_avg', min_hr:'hr_min', max_hr:'hr_max',
            avg_rr:'rr_avg', min_rr:'rr_min', max_rr:'rr_max', cnt:'cnt'}
    d=d.rename(columns={k:v for k,v in rename.items() if k in d.columns})
    d['datetime']=pd.to_datetime(d['datetime'], errors='coerce')
    d=d.dropna(subset=['datetime']).sort_values('datetime')

    for c in ['hr_avg','hr_min','hr_max','rr_avg','rr_min','rr_max','cnt']:
        if c in d.columns:
            d[c]=pd.to_numeric(d[c], errors='coerce')
    return d

def contiguous_blocks(mask, times):
    blocks=[]; inb=False; start=None; prev=None
    for t,mk in zip(times,mask):
        if mk and not inb:
            start=t; prev=t; inb=True
        elif mk and inb:
            prev=t
        elif (not mk) and inb:
            blocks.append((start,prev)); inb=False
    if inb: blocks.append((start,prev))
    out=[]
    for s,e in blocks:
        s=pd.Timestamp(s); e=pd.Timestamp(e)
        out.append((s,e,int((e-s)/pd.Timedelta('1h'))+1))
    return out

def build_blocks(v, thr):
    v=v.copy()
    v['flag_brady']=v['hr_avg']<thr['brady_hr_avg']
    v['flag_severe_brady']=v['hr_min']<thr['severe_brady_hr_min']
    v['flag_tachy']=v['hr_avg']>thr['tachy_hr_avg']
    v['flag_tachyp']=v['rr_avg']>thr['tachy_rr_avg']
    v['flag_low_cnt']=v['cnt']<thr['low_cnt'] if 'cnt' in v.columns else False

    specs=[
        (f"Bradycardia (HR avg <{thr['brady_hr_avg']})", 'flag_brady'),
        (f"Severe bradycardia (HR min <{thr['severe_brady_hr_min']})", 'flag_severe_brady'),
        (f"Tachycardia (HR avg >{thr['tachy_hr_avg']})", 'flag_tachy'),
        (f"Tachypnea (RR avg >{thr['tachy_rr_avg']})", 'flag_tachyp'),
    ]
    blocks=[]
    for label,col in specs:
        for s,e,h in contiguous_blocks(v[col].fillna(False).values, v['datetime'].values):
            blocks.append((label,s,e,h))
    b=pd.DataFrame(blocks, columns=['condition','start','end','hours']).sort_values(['start','condition'])
    return v,b

def compute_baseline(v):
    full_index=pd.date_range(v['datetime'].min().floor('h'), v['datetime'].max().ceil('h'), freq='h')
    m=v.set_index('datetime').reindex(full_index)
    m.index.name='datetime'
    m['hr_roll7d']=m['hr_avg'].rolling(24*7, min_periods=24*3).mean()
    m['rr_roll7d']=m['rr_avg'].rolling(24*7, min_periods=24*3).mean()
    return m

def save_flag_plot(m, b, out_png, days=None):
    d=m.copy()
    if days is not None:
        end=d.index.max()
        start=end - pd.Timedelta(days=days)
        d=d[(d.index>=start)&(d.index<=end)]
        b=b[(b['end']>=d.index.min())&(b['start']<=d.index.max())]

    fig=plt.figure(figsize=(11,4))
    plt.plot(d.index, d['hr_avg'], linewidth=0.7, label='HR avg (hourly)')
    plt.plot(d.index, d['rr_avg'], linewidth=0.7, label='RR avg (hourly)')
    plt.plot(d.index, d['hr_roll7d'], linewidth=1.2, label='HR 7d baseline')
    plt.plot(d.index, d['rr_roll7d'], linewidth=1.2, label='RR 7d baseline')

    for _,r in b.iterrows():
        c='gray'
        s=r['condition'].lower()
        if 'severe brady' in s: c='purple'
        elif 'brady' in s: c='blue'
        elif 'tachycardia' in s: c='red'
        elif 'tachypnea' in s: c='orange'
        plt.axvspan(r['start'], r['end']+pd.Timedelta(hours=1), alpha=0.12, color=c)

    plt.title('Flagged Clinical Windows (shaded) + 7‑day baseline')
    plt.xlabel('Date'); plt.ylabel('Value (bpm / brpm)')
    plt.legend(fontsize=8, ncol=2)
    plt.tight_layout()
    fig.savefig(out_png, dpi=200)
    plt.close(fig)

def make_pdf_nurse(v, b, plot_all, plot_zoom, out_pdf):
    styles=getSampleStyleSheet()
    doc=SimpleDocTemplate(out_pdf, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story=[]
    story.append(Paragraph('A) Nurse Dashboard — Shift-Friendly Vital Review', styles['Title']))
    story.append(Paragraph(f"Reporting period: {v['datetime'].min().date()} → {v['datetime'].max().date()} (hourly)", styles['Normal']))
    story.append(Spacer(1,0.10*inch))
    story.append(Paragraph('<b>Legend:</b> Blue=Brady | Purple=Severe Brady | Red=Tachycardia | Orange=Tachypnea', styles['Normal']))
    story.append(Spacer(1,0.08*inch))
    story.append(Image(plot_all, width=7.2*inch, height=2.6*inch))
    story.append(Spacer(1,0.10*inch))
    story.append(Paragraph('<b>Last 14 days (zoom)</b>', styles['Heading2']))
    story.append(Image(plot_zoom, width=7.2*inch, height=2.6*inch))
    story.append(Spacer(1,0.10*inch))

    if len(b):
        w=np.where(b['condition'].str.lower().str.contains('severe'), 3, 1) * b['hours']
        q=b.assign(weight=w).sort_values(['weight','hours'], ascending=False).head(12)

        rows=[['Window','Flag','Hours','What to check','Notify']]
        for _,r in q.iterrows():
            s=r['condition'].lower()
            if 'brady' in s:
                action='BP, symptoms, meds; verify pulse; rhythm/ECG if available'
                notify='MD; Cardiology if symptomatic or persistent'
            elif 'tachycardia' in s:
                action='Pain/temp/infection/dehydration/anemia; BP/SpO₂ if available'
                notify='MD if sustained >2h or symptomatic'
            else:
                action='SpO₂, work of breathing, CHF/pulmonary signs'
                notify='MD/Resp if sustained or SpO₂ low'
            rows.append([f"{r['start']:%Y-%m-%d %H:%M}→{r['end']:%Y-%m-%d %H:%M}", r['condition'], str(int(r['hours'])), action, notify])

        t=Table(rows, colWidths=[2.0,1.6,0.6,2.4,1.6])
        t.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),colors.lightgrey),
            ('GRID',(0,0),(-1,-1),0.25,colors.grey),
            ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
            ('FONTSIZE',(0,0),(-1,-1),7.5),
            ('VALIGN',(0,0),(-1,-1),'TOP'),
        ]))
        story.append(Paragraph('<b>Shift review queue</b>', styles['Heading2']))
        story.append(t)
    else:
        story.append(Paragraph('No flagged windows detected at current thresholds.', styles['Normal']))

    doc.build(story)

def make_pdf_physician(v, b, plot_all, out_pdf):
    styles=getSampleStyleSheet()
    doc=SimpleDocTemplate(out_pdf, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story=[]
    story.append(Paragraph('B) Physician/Cardiology Summary — Vital Trend Intelligence', styles['Title']))
    story.append(Paragraph(f"Data window: {v['datetime'].min().date()} → {v['datetime'].max().date()} | Hourly", styles['Normal']))
    story.append(Spacer(1,0.10*inch))

    def q95(x): return float(np.nanquantile(x,0.95))
    rows=[['Metric','Mean','P95','Min','Max'],
          ['HR avg', f"{v['hr_avg'].mean():.1f}", f"{q95(v['hr_avg']):.1f}", f"{v['hr_avg'].min():.1f}", f"{v['hr_avg'].max():.1f}"],
          ['RR avg', f"{v['rr_avg'].mean():.1f}", f"{q95(v['rr_avg']):.1f}", f"{v['rr_avg'].min():.1f}", f"{v['rr_avg'].max():.1f}"],]
    t=Table(rows, colWidths=[1.6,1.0,1.0,1.0,1.0])
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.lightgrey),
        ('GRID',(0,0),(-1,-1),0.25,colors.grey),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
        ('FONTSIZE',(0,0),(-1,-1),9),
    ]))
    story.append(t)
    story.append(Spacer(1,0.10*inch))
    story.append(Image(plot_all, width=7.2*inch, height=2.6*inch))
    story.append(Spacer(1,0.10*inch))

    story.append(Paragraph('<b>Top episodes (by duration)</b>', styles['Heading2']))
    if len(b):
        q=b.sort_values('hours', ascending=False).head(12)
        rows=[['Flag','Start','End','Hours']]
        for _,r in q.iterrows():
            rows.append([r['condition'], f"{r['start']:%Y-%m-%d %H:%M}", f"{r['end']:%Y-%m-%d %H:%M}", str(int(r['hours']))])
        t=Table(rows, colWidths=[2.7,1.4,1.4,0.6])
        t.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),colors.lightgrey),
            ('GRID',(0,0),(-1,-1),0.25,colors.grey),
            ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
            ('FONTSIZE',(0,0),(-1,-1),8),
            ('VALIGN',(0,0),(-1,-1),'TOP'),
        ]))
        story.append(t)
    else:
        story.append(Paragraph('No episodes detected at current thresholds.', styles['Normal']))

    story.append(Spacer(1,0.10*inch))
    story.append(Paragraph(
        """<b>Interpretation prompts:</b><br/>
        • Bradycardia: sleep vs sinus node dysfunction/AV block vs medication effect; consider ECG/telemetry if symptomatic/new.<br/>
        • Tachycardia: pain, infection, dehydration, anemia, hypoxia, volume overload, atrial arrhythmia; correlate with rhythm/hemodynamics.<br/>
        • Tachypnea: correlate with SpO₂/work of breathing/CHF & pulmonary processes; co-occurrence with HR rise increases concern.<br/>
        • Data integrity: gaps/low sample-count can exaggerate extremes; hourly averages are more robust than max values.<br/>""",
        styles['Normal']
    ))
    doc.build(story)

def make_pdf_rpm(v, b, thr, out_pdf, cadence='weekly'):
    styles=getSampleStyleSheet()
    doc=SimpleDocTemplate(out_pdf, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story=[]
    story.append(Paragraph(f"C) Automated RPM {cadence.capitalize()} Intelligence Summary", styles['Title']))
    story.append(Paragraph(f"Window: {v['datetime'].min().date()} → {v['datetime'].max().date()} | Hourly", styles['Normal']))
    story.append(Spacer(1,0.10*inch))

    counts={
        'Brady hours': int((v['hr_avg']<thr['brady_hr_avg']).sum()),
        'Severe brady hours': int((v['hr_min']<thr['severe_brady_hr_min']).sum()),
        'Tachycardia hours': int((v['hr_avg']>thr['tachy_hr_avg']).sum()),
        'Tachypnea hours': int((v['rr_avg']>thr['tachy_rr_avg']).sum()),
        'Low-confidence hours': int((v['cnt']<thr['low_cnt']).sum()) if 'cnt' in v.columns else 0,
    }
    score=3*counts['Severe brady hours'] + 2*counts['Tachycardia hours'] + 2*counts['Tachypnea hours'] + 1*counts['Brady hours']
    triage='Green (routine review)'
    if score>=30: triage='Red (MD review recommended)'
    elif score>=10: triage='Yellow (nurse review + consider MD)'
    story.append(Paragraph(f"<b>Triage:</b> {triage}", styles['Heading2']))

    rows=[['Signal','Count (hours)']]+[[k,str(vv)] for k,vv in counts.items()]
    t=Table(rows, colWidths=[3.2,1.2])
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.lightgrey),
        ('GRID',(0,0),(-1,-1),0.25,colors.grey),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
        ('FONTSIZE',(0,0),(-1,-1),9),
    ]))
    story.append(t)
    story.append(Spacer(1,0.10*inch))

    story.append(Paragraph('<b>Top episodes to review</b>', styles['Heading2']))
    if len(b):
        q=b.sort_values('hours', ascending=False).head(8)
        rows=[['Flag','Window','Hours','Trigger']]
        for _,r in q.iterrows():
            s=r['condition'].lower()
            if 'severe brady' in s: trig='If symptomatic or HR<45 sustained → MD/Cardiology'
            elif 'brady' in s: trig='If persistent/new → MD; review meds'
            elif 'tachycardia' in s: trig='If sustained >2h or HR>120 → MD'
            else: trig='If SpO₂ low or concurrent HR rise → MD/Resp'
            rows.append([r['condition'], f"{r['start']:%Y-%m-%d %H:%M}→{r['end']:%Y-%m-%d %H:%M}", str(int(r['hours'])), trig])
        t=Table(rows, colWidths=[1.9,2.6,0.6,1.9])
        t.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),colors.lightgrey),
            ('GRID',(0,0),(-1,-1),0.25,colors.grey),
            ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
            ('FONTSIZE',(0,0),(-1,-1),8),
            ('VALIGN',(0,0),(-1,-1),'TOP'),
        ]))
        story.append(t)
    else:
        story.append(Paragraph('No episodes detected.', styles['Normal']))
    doc.build(story)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input', required=True, help='Vitals file (.xlsx or .csv)')
    ap.add_argument('--config', default='config.yaml', help='YAML thresholds')
    ap.add_argument('--outdir', default='outputs', help='Output directory')
    ap.add_argument('--cadence', default='weekly', choices=['daily','weekly'])
    args=ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    with open(args.config,'r') as f:
        cfg=yaml.safe_load(f)
    thr=cfg['thresholds']

    v=load_vitals(args.input)
    v,b=build_blocks(v, thr)
    m=compute_baseline(v)

    plot_all=os.path.join(args.outdir,'flag_plot_all.png')
    plot_zoom=os.path.join(args.outdir,'flag_plot_last14d.png')
    save_flag_plot(m,b,plot_all,days=None)
    save_flag_plot(m,b,plot_zoom,days=14)

    make_pdf_nurse(v,b,plot_all,plot_zoom, os.path.join(args.outdir,'A_Nurse_Dashboard.pdf'))
    make_pdf_physician(v,b,plot_all, os.path.join(args.outdir,'B_Physician_Summary.pdf'))
    make_pdf_rpm(v,b,thr, os.path.join(args.outdir,f"C_Automated_RPM_Report_{args.cadence.capitalize()}.pdf"), cadence=args.cadence)
    print('Done. Outputs in', args.outdir)

if __name__=='__main__':
    main()
