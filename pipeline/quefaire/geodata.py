"""Génération de la table de communes d'un épicentre (commune → lat/lon).

Interroge l'API publique officielle geo.api.gouv.fr pour les départements qui
débordent dans le rayon de l'épicentre, puis ne garde que les communes à moins
de `radius_minutes` du centre. Écrit `data/communes_<secteur>.csv`.

C'est le seul outil qui a besoin du réseau, et il ne tourne **qu'à la génération**
(pas au crawl) : une fois le CSV écrit, le géocodage reste 100 % hors-ligne. Il
sert surtout de **secours** — les événements OpenAgenda portent déjà leurs
coordonnées ; le CSV ne géocode que les sources qui ne donnent qu'un nom de commune
(RSS/HTML/iCal).

    python -m quefaire build-geo --sector villemoirieu --departments 38,69,01,73

Note : les hôtes data (geo.api.gouv.fr) peuvent être bloqués par une politique
proxy restreinte ; lancer alors la commande dans un environnement au réseau ouvert.
"""

from __future__ import annotations

import csv
import logging

from .fetchers.base import http_get
from .geo import haversine_km, travel_minutes

log = logging.getLogger("quefaire")

API_DEP = "https://geo.api.gouv.fr/departements/{dep}/communes"


def fetch_communes(department: str) -> list[tuple[str, float, float]]:
    """Communes d'un département avec leur centre : (nom, lat, lon)."""
    data = http_get(
        API_DEP.format(dep=department),
        params={"fields": "nom,centre", "format": "json"},
    ).json()
    out: list[tuple[str, float, float]] = []
    for c in data:
        centre = c.get("centre") or {}
        coords = centre.get("coordinates") or []
        if len(coords) == 2:  # GeoJSON : [lon, lat]
            out.append((c["nom"], float(coords[1]), float(coords[0])))
    return out


def build_table(
    center_lat: float,
    center_lon: float,
    radius_minutes: float,
    departments: list[str],
) -> list[tuple[str, float, float]]:
    """Communes des départements donnés qui tombent dans le rayon de l'épicentre,
    triées par distance croissante."""
    kept: list[tuple[float, str, float, float]] = []
    for dep in departments:
        try:
            communes = fetch_communes(dep)
        except Exception as exc:
            log.error("[build-geo] département %s : %s", dep, exc)
            continue
        for name, lat, lon in communes:
            km = haversine_km(center_lat, center_lon, lat, lon)
            if travel_minutes(km) <= radius_minutes:
                kept.append((km, name, lat, lon))
        log.info("[build-geo] département %s : %d communes lues", dep, len(communes))
    kept.sort()
    return [(name, lat, lon) for _, name, lat, lon in kept]


def write_csv(rows: list[tuple[str, float, float]], path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["commune", "lat", "lon"])
        for name, lat, lon in rows:
            writer.writerow([name, f"{lat:.4f}", f"{lon:.4f}"])
