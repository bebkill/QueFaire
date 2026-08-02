"""Géocodage hors-ligne : commune → coordonnées, via un CSV par secteur.

Pas d'appel réseau : la table des communes du secteur suffit pour trier
« près de moi ». Pour un nouveau secteur, ajouter data/communes_<secteur>.csv.
"""

from __future__ import annotations

import csv
import unicodedata
from functools import lru_cache
from pathlib import Path

from .models import Event

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _fold(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return name.lower().replace("-", " ").replace("'", " ").strip()


@lru_cache(maxsize=8)
def commune_table(sector_id: str) -> dict[str, tuple[str, float, float]]:
    path = DATA_DIR / f"communes_{sector_id}.csv"
    table: dict[str, tuple[str, float, float]] = {}
    if not path.exists():
        return table
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            table[_fold(row["commune"])] = (
                row["commune"],
                float(row["lat"]),
                float(row["lon"]),
            )
    return table


def geocode(event: Event, sector_id: str) -> Event:
    if event.lat is not None and event.lon is not None:
        return event
    if not event.commune:
        return event
    hit = commune_table(sector_id).get(_fold(event.commune))
    if hit:
        event.commune, event.lat, event.lon = hit
    return event
