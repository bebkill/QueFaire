"""Distance et temps de trajet — épicentre + rayon.

QueFaire ne raisonne plus par département mais par **épicentre** : une commune
de référence (ex. Villemoirieu) et un rayon en **temps de trajet** (ex. 1 h de
voiture). Un événement est pertinent s'il tombe dans ce rayon, quel que soit son
département (le nord-Isère est plus proche de Lyon et de l'Ain que du sud-Isère).

Le calcul reproduit **à l'identique** celui du front (`site/src/lib/nlsearch.js`,
`distanceKm` + `travelMinutes`) pour que collecte et affichage s'accordent :
1 h de voiture ≈ 48 km à vol d'oiseau. Un vrai moteur isochrone est en roadmap ;
la précision reste de toute façon bornée par le géocodage au centre de la commune.
"""

from __future__ import annotations

import math

from .models import Event

# Mêmes constantes que le front (nlsearch.js).
SPEEDS = {"walk": 4.8, "bike": 15.0, "car": 35.0}
DETOUR = {"walk": 1.15, "bike": 1.25, "car": 1.35}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance à vol d'oiseau en km (haversine) — miroir de `distanceKm`."""
    rad = math.pi / 180
    a = (
        math.sin((lat2 - lat1) * rad / 2) ** 2
        + math.cos(lat1 * rad) * math.cos(lat2 * rad) * math.sin((lon2 - lon1) * rad / 2) ** 2
    )
    return 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def travel_minutes(km: float, mode: str = "car") -> float:
    """Estimation de temps de trajet « à vol d'oiseau corrigé » — miroir de
    `travelMinutes`. En voiture, vitesse moyenne croissante avec la distance
    (30 km/h en ville, jusqu'à ~65 km/h sur route)."""
    speed = SPEEDS.get(mode, SPEEDS["car"])
    if mode == "car":
        speed = min(65.0, 30.0 + km)
    return (km * DETOUR.get(mode, 1.3)) / speed * 60


def within_radius(
    event: Event, center_lat: float, center_lon: float, radius_minutes: float, mode: str = "car"
) -> bool:
    """L'événement est-il à moins de `radius_minutes` de l'épicentre ?

    Un événement **sans coordonnées** (commune absente du géocodage) est conservé
    par défaut : on ne peut pas le situer, on préfère l'afficher (le front le gère
    déjà — exclu du filtre trajet et de la carte, visible sinon) plutôt que de le
    perdre silencieusement.
    """
    if event.lat is None or event.lon is None:
        return True
    km = haversine_km(center_lat, center_lon, event.lat, event.lon)
    return travel_minutes(km, mode) <= radius_minutes
