"""Client / library registry — Round 28.

Privacy-preserving, folder-discovered client (library) scoping. Each
sub-folder under ``data/`` is one client (library). A patient belongs to
exactly one client; the pipeline NEVER loads a patient from outside the
selected client's folder.

Design rules (Round 28 spec, Item 3):
  * The library list is DISCOVERED from the folder structure, not hardcoded
    (R28_006). Adding a third client later is a new folder, no code change.
  * The loader is chosen by the SHAPE of the files in a folder (clean CSV ->
    MedHab loader; wide device Excel -> PAM loader), never by the client's
    name — consistent with the MH_004 dispatch philosophy.
  * ``load_client_data(client)`` reads ONLY that client's folder, so a patient
    under client A is never loadable under client B (R28_007).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

DATA_ROOT = Path(__file__).resolve().parent.parent / "data"

# Cosmetic display labels for known folders. Discovery is by folder either
# way; unknown folders fall back to a title-cased folder name, so a new client
# folder needs no entry here.
_CLIENT_LABELS = {"medhab": "MedHab", "pam_health": "PAM Health"}


def _has_data(folder: Path) -> bool:
    return any(folder.glob("*.csv")) or any(folder.glob("*.xlsx"))


def _is_client_folder(p: Path) -> bool:
    if not p.is_dir():
        return False
    if p.name.startswith(".") or p.name.startswith("_") or p.name.startswith("~"):
        return False
    return _has_data(p)


@lru_cache(maxsize=1)
def discover_clients() -> list[str]:
    """Client ids discovered from ``data/`` sub-folders (sorted, stable)."""
    if not DATA_ROOT.exists():
        return []
    return sorted(d.name for d in DATA_ROOT.iterdir() if _is_client_folder(d))


def client_label(client: str) -> str:
    """Human-facing label for a client id."""
    return _CLIENT_LABELS.get(client, client.replace("_", " ").title())


def client_folder(client: str) -> Path:
    """Resolve a client id to its data folder.

    Raises ``KeyError`` if the id is not a discovered client — this is the
    guard that prevents path traversal / cross-client reads. No caller can
    name a folder outside ``data/``.
    """
    if client not in discover_clients():
        raise KeyError(f"Unknown client '{client}'")
    return DATA_ROOT / client


def client_is_csv(client: str) -> bool:
    """True for clean-CSV (MedHab-shape) clients; False for wide-Excel clients.

    Decided by the files physically present in the folder, never by name.
    """
    return any(client_folder(client).glob("*.csv"))


@lru_cache(maxsize=8)
def load_client_data(client: str) -> dict:
    """``{patient_id: frame}`` for exactly one client.

    Reads ONLY that client's folder — never any other client's data. This is
    the privacy boundary (R28_007): a patient is loadable solely under its own
    client.
    """
    from .medhab_ingest import load_medhab_vitals
    from .excel_ingest import load_vitals

    folder = client_folder(client)
    if client_is_csv(client):
        return load_medhab_vitals(str(folder))
    return load_vitals(str(folder))


@lru_cache(maxsize=8)
def client_specs(client: str) -> list:
    """Per-patient-month report windows for a CSV (MedHab-shape) client.

    Empty for Excel clients, which report over rolling ranges, not month
    specs.
    """
    from .medhab_ingest import discover_report_windows

    if client_is_csv(client):
        return discover_report_windows(str(client_folder(client)))
    return []


def list_patients(client: str) -> list[str]:
    """Sorted patient ids belonging to ``client`` (and only ``client``)."""
    return sorted(load_client_data(client).keys())


def is_patient_in_client(patient_id: str, client: str) -> bool:
    """Whether ``patient_id`` is loadable under ``client``.

    The privacy invariant (R28_007) is that this is True for exactly one
    discovered client.
    """
    try:
        return patient_id in load_client_data(client)
    except Exception:
        return False


def resolve_client_for_patient(patient_id: str) -> str | None:
    """The single client a patient belongs to, or ``None``.

    Used only as a back-compat fallback when a request does not name a client.
    When a request DOES name a client, callers must honour it directly and
    never fall back across the boundary.
    """
    for c in discover_clients():
        if is_patient_in_client(patient_id, c):
            return c
    return None
