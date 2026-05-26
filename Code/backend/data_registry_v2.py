"""
CardioReport Data Registry v2.0
Dual parsing: filename first (reliable), sheet name fallback.
Handles PAMHealth study files where sheet names don't match patient identity.
Also groups multi-sensor patients (bed + chair from different device IDs).
"""

import os
import re
import pandas as pd
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


# ═══ SENSOR TYPE DETECTION FROM FILENAME ═══

SENSOR_KEYWORDS = {
    'bed':   ['bed', 'bdrm', 'bdrm2', 'bedroom'],
    'chair': ['chair', 'livroom', 'livrm', 'lr', 'lrchair'],
}

def detect_sensor_from_filename(filename: str) -> dict:
    """Parse filename to extract device ID, patient name, and sensor type."""
    base = Path(filename).stem  # e.g., '934298-0138_JB_P8_chair_PVitalsag_2024_AG'
    parts = base.split('_')

    if not parts:
        return None

    # Device ID is always first part
    device_id = parts[0]
    if not re.match(r'^\d{6}-\d{4}$', device_id):
        return None

    # Detect sensor type from filename keywords
    fname_lower = base.lower()
    sensor_type = 'unknown'
    for stype, keywords in SENSOR_KEYWORDS.items():
        if any(kw in fname_lower for kw in keywords):
            sensor_type = stype
            break

    # Extract patient name (parts between device ID and PVitalsag)
    name_parts = []
    for p in parts[1:]:
        if p.lower() in ('pvitalsag', '2024', 'ag', 'hourly'):
            break
        # Skip sensor type words and P-number labels
        if p.lower() in ('chair', 'bed', 'bdrm', 'bdrm2', 'lrchair'):
            continue
        if re.match(r'^P\d+$', p):  # P7, P8, P10
            continue
        name_parts.append(p)

    patient_name = '_'.join(name_parts) if name_parts else device_id

    # Determine location label
    if sensor_type == 'bed':
        location = 'Bedroom'
    elif sensor_type == 'chair':
        location = 'Living Room' if any(kw in fname_lower for kw in ['livroom', 'livrm', 'lr']) else 'Chair'
    else:
        location = 'Unknown'

    return {
        'device_id': device_id,
        'patient_name': patient_name,
        'sensor_type': sensor_type,
        'location': location,
    }


# ═══ SHEET NAME FALLBACK ═══

SHEET_PATTERN = re.compile(
    r'^(\d{6}-\d{4})\s+(\w+)[\.]?([\w]*)_PVitals'
)

LOCATION_MAP = {
    'LivRm': ('chair', 'Living Room'),
    'Chair': ('chair', 'Chair'),
    'BedRm': ('bed', 'Bedroom'),
    'Bdrm':  ('bed', 'Bedroom'),
    'Bdrm2': ('bed', 'Bedroom 2'),
}

def detect_sensor_from_sheet(sheet_name: str) -> Optional[dict]:
    """Fallback: parse sheet name for patient/sensor info."""
    match = SHEET_PATTERN.match(sheet_name)
    if not match:
        return None

    device_id = match.group(1)
    patient_name = match.group(2)
    location_code = match.group(3) if match.group(3) else ''

    sensor_type, location = 'unknown', location_code
    for code, (stype, loc) in LOCATION_MAP.items():
        if code.lower() in location_code.lower():
            sensor_type, location = stype, loc
            break

    return {
        'device_id': device_id,
        'patient_name': patient_name,
        'sensor_type': sensor_type,
        'location': location,
    }


# ═══ KNOWN MULTI SENSOR PATIENT GROUPS ═══
# Some patients use different device IDs for bed vs chair.
# This maps device IDs that belong to the same patient.

PATIENT_GROUPS = {
    'EG': {
        'patient_id': 'EG',
        'patient_name': 'EG',
        'devices': ['934298-0168', '934298-0279'],
    },
    'JB': {
        'patient_id': 'JB',
        'patient_name': 'JB',
        'devices': ['934298-0138', '934298-0293'],
    },
    'RSanchez': {
        'patient_id': 'RSanchez',
        'patient_name': 'RSanchez',
        'devices': ['934298-0019', '955288-0055'],
    },
}

def find_patient_group(device_id: str) -> Optional[str]:
    """Check if a device ID belongs to a known multi-sensor patient group."""
    for group_id, group in PATIENT_GROUPS.items():
        if device_id in group['devices']:
            return group_id
    return None


# ═══ DATA MODELS ═══

@dataclass
class SensorSource:
    file_path: str
    sheet_name: str
    device_id: str
    patient_name: str
    sensor_type: str   # 'chair' | 'bed'
    location: str
    date_start: Optional[datetime] = None
    date_end: Optional[datetime] = None
    total_hours: int = 0

@dataclass
class PatientRecord:
    patient_id: str
    patient_name: str
    sensors: list = field(default_factory=list)

    @property
    def sensor_types(self):
        return sorted(set(s.sensor_type for s in self.sensors))

    @property
    def has_bed(self):
        return any(s.sensor_type == 'bed' for s in self.sensors)

    @property
    def has_chair(self):
        return any(s.sensor_type == 'chair' for s in self.sensors)

    @property
    def has_both(self):
        return self.has_bed and self.has_chair

    @property
    def date_range(self):
        starts = [s.date_start for s in self.sensors if s.date_start]
        ends = [s.date_end for s in self.sensors if s.date_end]
        return (min(starts) if starts else None, max(ends) if ends else None)

    @property
    def overlapping_dates(self):
        if not self.has_both:
            return None
        bed = [s for s in self.sensors if s.sensor_type == 'bed']
        chair = [s for s in self.sensors if s.sensor_type == 'chair']
        bs, be = min(s.date_start for s in bed), max(s.date_end for s in bed)
        cs, ce = min(s.date_start for s in chair), max(s.date_end for s in chair)
        os, oe = max(bs, cs), min(be, ce)
        return (os, oe) if os < oe else None

    def get_sensor(self, sensor_type: str) -> Optional[SensorSource]:
        matches = [s for s in self.sensors if s.sensor_type == sensor_type]
        return matches[0] if matches else None


# ═══ FILE SCANNER ═══

def scan_file(filepath: str) -> Optional[SensorSource]:
    """Scan one Excel file. Uses filename parsing first, sheet name fallback."""
    filename = os.path.basename(filepath)

    # Strategy 1: Parse filename
    info = detect_sensor_from_filename(filename)

    # Strategy 2: Fallback to sheet name for missing info
    try:
        xl = pd.ExcelFile(filepath)
        sheet = xl.sheet_names[0]
        sheet_info = detect_sensor_from_sheet(sheet)
        if sheet_info:
            if not info:
                info = sheet_info
            else:
                # Fill in missing fields from sheet name
                if info['sensor_type'] == 'unknown' and sheet_info['sensor_type'] != 'unknown':
                    info['sensor_type'] = sheet_info['sensor_type']
                    info['location'] = sheet_info['location']
                # If sensor type is STILL unknown after both strategies,
                # default to 'chair' (most common sensor in the study)
                if info['sensor_type'] == 'unknown':
                    info['sensor_type'] = 'chair'
                    info['location'] = 'Chair'
    except Exception:
        pass

    if not info:
        return None
    
    # Final fallback for unknown sensor type
    if info['sensor_type'] == 'unknown':
        info['sensor_type'] = 'chair'
        info['location'] = 'Chair'

    # Read date range
    try:
        xl = pd.ExcelFile(filepath)
        sheet = xl.sheet_names[0]
        df = pd.read_excel(filepath, sheet_name=sheet, usecols=[0])
        df.columns = ['Date']
        df = df.dropna(subset=['Date'])
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
        df = df.dropna(subset=['Date'])
        date_start = df['Date'].min()
        date_end = df['Date'].max()
        total_hours = len(df)
    except Exception:
        date_start, date_end, total_hours = None, None, 0

    return SensorSource(
        file_path=filepath,
        sheet_name=sheet if 'sheet' in dir() else '',
        device_id=info['device_id'],
        patient_name=info['patient_name'],
        sensor_type=info['sensor_type'],
        location=info['location'],
        date_start=date_start,
        date_end=date_end,
        total_hours=total_hours,
    )


def scan_directory(data_dir: str, skip_patterns=None) -> dict:
    """Scan all Excel files and build patient registry with multi-sensor grouping."""
    skip_patterns = skip_patterns or ['HOURLY', '__1_', 'Juanita']
    xlsx_files = sorted(Path(data_dir).glob('*.xlsx'))

    print(f'[Registry] Scanning {len(xlsx_files)} Excel files in {data_dir}')

    # First pass: scan all files
    sources = []
    for fpath in xlsx_files:
        fname = fpath.name
        if any(skip in fname for skip in skip_patterns):
            continue
        src = scan_file(str(fpath))
        if src:
            sources.append(src)
            print(f'  {src.device_id} ({src.patient_name:15s}) [{src.sensor_type:5s}] '
                  f'{src.date_start.date() if src.date_start else "?"} to '
                  f'{src.date_end.date() if src.date_end else "?"} | {src.total_hours}h | {fname}')

    # Second pass: group by patient
    # Check if device belongs to a known multi-sensor group
    patients = {}
    for src in sources:
        group_id = find_patient_group(src.device_id)

        if group_id:
            # Multi-sensor patient: group under shared patient ID
            pid = group_id
            pname = PATIENT_GROUPS[group_id]['patient_name']
        else:
            # Single-device patient: use device_id + patient_name as key
            # This prevents merging different patients who share a device ID
            # (e.g., PHolst and Wimberley both on device 934297-0130)
            pid = f'{src.device_id}_{src.patient_name}'
            pname = src.patient_name

        if pid not in patients:
            patients[pid] = PatientRecord(patient_id=pid, patient_name=pname)
        patients[pid].sensors.append(src)

    # Report
    print(f'\n[Registry] {len(patients)} patients discovered:')
    for pid, rec in sorted(patients.items()):
        types = '+'.join(rec.sensor_types)
        dr = rec.date_range
        dr_str = f'{dr[0].strftime("%Y-%m-%d") if dr[0] else "?"} to {dr[1].strftime("%Y-%m-%d") if dr[1] else "?"}'
        total = sum(s.total_hours for s in rec.sensors)
        overlap = rec.overlapping_dates
        ov_str = f' | OVERLAP: {(overlap[1]-overlap[0]).days}d' if overlap else ''
        print(f'  {pid:15s} ({rec.patient_name:15s}) [{types:10s}] {dr_str} | {total:5d}h{ov_str}')

    return patients


# ═══ DATA LOADER ═══

def load_hourly_data(source: SensorSource, start=None, end=None) -> pd.DataFrame:
    """Load and clean hourly data from a sensor source."""
    df = pd.read_excel(source.file_path, sheet_name=source.sheet_name)

    # Dynamic column mapping
    cmap = {}
    for c in df.columns:
        cl = c.strip().lower()
        if cl == 'date': cmap[c] = 'Date'
        elif cl == 'avg_hr': cmap[c] = 'avg_hr'
        elif cl == 'max_hr': cmap[c] = 'max_hr'
        elif cl == 'min_hr': cmap[c] = 'min_hr'
        elif cl == 'avg_rr': cmap[c] = 'avg_rr'
        elif cl == 'max_rr': cmap[c] = 'max_rr'
        elif cl == 'min_rr': cmap[c] = 'min_rr'
        elif cl == 'cnt': cmap[c] = 'cnt'

    df = df[list(cmap.keys())].rename(columns=cmap)
    df = df.dropna(subset=['Date'])
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
    df = df.dropna(subset=['Date'])

    if 'cnt' not in df.columns:
        df['cnt'] = 60

    if start:
        df = df[df['Date'] >= pd.Timestamp(start)]
    if end:
        df = df[df['Date'] <= pd.Timestamp(end)]

    df['sensor_type'] = source.sensor_type
    df['location'] = source.location

    return df.sort_values('Date').reset_index(drop=True)


# ═══ TEST ═══

if __name__ == '__main__':
    # Test against the fixed files
    patients = scan_directory('/home/claude/fixed_files')

    print('\n' + '='*80)
    print('REPORT AVAILABILITY')
    print('='*80)
    for pid, rec in sorted(patients.items()):
        print(f'\n  {pid} ({rec.patient_name}):')
        print(f'    Sensors: {rec.sensor_types}')
        if rec.has_both:
            ov = rec.overlapping_dates
            if ov:
                print(f'    Overlap: {ov[0].date()} to {ov[1].date()} ({(ov[1]-ov[0]).days} days)')
                print(f'    Reports: Chair Weekly, Chair Monthly, Bed Activity, Combined Positional')
            else:
                print(f'    No overlap. Reports: Chair + Bed (separate)')
        elif rec.has_chair:
            print(f'    Reports: Chair Weekly, Chair Monthly')
        elif rec.has_bed:
            print(f'    Reports: Bed Activity')
