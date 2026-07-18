"""Chargement du registre de sources par secteur.

Un secteur (isere, savoie, …) = un fichier YAML dans pipeline/sources/.
Ajouter une région se résume à ajouter un fichier + un CSV de communes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .models import Source

SOURCES_DIR = Path(__file__).resolve().parent.parent / "sources"


@dataclass
class Sector:
    id: str
    name: str
    country: str
    center_lat: float
    center_lon: float
    sources: list[Source] = field(default_factory=list)


def available_sectors() -> list[str]:
    return sorted(p.stem for p in SOURCES_DIR.glob("*.yaml"))


def load_sector(sector_id: str) -> Sector:
    path = SOURCES_DIR / f"{sector_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"Secteur inconnu : {sector_id}. Disponibles : {', '.join(available_sectors())}"
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    meta = raw.get("sector", {})
    sources = [
        Source(**{**src, "id": src.get("id") or f"{sector_id}-{i}"})
        for i, src in enumerate(raw.get("sources", []))
    ]
    return Sector(
        id=sector_id,
        name=meta.get("name", sector_id),
        country=meta.get("country", "FR"),
        center_lat=float(meta.get("center_lat", 45.2)),
        center_lon=float(meta.get("center_lon", 5.7)),
        sources=[s for s in sources if s.enabled],
    )
