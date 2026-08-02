"""Notes d'avis des activités permanentes — Google Places ou TripAdvisor.

Deux fournisseurs, une même interface, choisis par la présence d'une clé :

- `GOOGLE_PLACES_KEY` → Places API (New), endpoint `places:searchText`. Bonne
  couverture des petits lieux ruraux, ce qui compte ici.
- `TRIPADVISOR_API_KEY` → Content API, `location/search` puis `location/details`.
  Deux appels par activité, couverture plus touristique.

Aucune clé configurée = aucune note, et c'est un cas NORMAL : `rating` reste à
None et le site n'affiche simplement pas d'étoiles. Le pipeline ne doit jamais
dépendre d'un service payant pour produire un résultat exploitable.

Les notes bougent lentement : elles sont mises en cache et rafraîchies au plus
une fois par `TTL_DAYS`, ce qui garde la facture d'API négligeable même en
rejouant la découverte souvent.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime

from .cache import cache
from .models import Place

log = logging.getLogger("quefaire")

GOOGLE_URL = "https://places.googleapis.com/v1/places:searchText"
TRIPADVISOR_SEARCH = "https://api.content.tripadvisor.com/api/v1/location/search"
TRIPADVISOR_DETAILS = "https://api.content.tripadvisor.com/api/v1/location/{id}/details"

TTL_DAYS = 90


def provider() -> str | None:
    """Fournisseur actif, déduit des clés présentes. None = pas de notes."""
    if os.environ.get("GOOGLE_PLACES_KEY"):
        return "google"
    if os.environ.get("TRIPADVISOR_API_KEY"):
        return "tripadvisor"
    return None


def _fresh(place: Place, today: str) -> bool:
    """La note en place est-elle encore assez récente pour éviter un appel ?"""
    if place.rating is None or not place.last_seen:
        return False
    try:
        age = (datetime.fromisoformat(today).date() - datetime.fromisoformat(place.last_seen).date()).days
    except ValueError:
        return False
    return age < TTL_DAYS


def _query_of(place: Place) -> str:
    return " ".join(x for x in (place.name, place.commune, "France") if x)


def _fetch_google(place: Place, key: str) -> dict | None:
    import requests

    from .fetchers.base import USER_AGENT

    resp = requests.post(
        GOOGLE_URL,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": key,
            # Le masque de champs est OBLIGATOIRE sur l'API New et conditionne
            # la facturation : on ne demande que la note et le lien.
            "X-Goog-FieldMask": "places.rating,places.userRatingCount,places.googleMapsUri,places.displayName",
            "User-Agent": USER_AGENT,
        },
        json={
            "textQuery": _query_of(place),
            "languageCode": "fr",
            "maxResultCount": 1,
            # Biais géographique : deux « Musée de la Mine » existent, on veut
            # celui qui est à côté, pas son homonyme à 400 km.
            "locationBias": {
                "circle": {
                    "center": {"latitude": place.lat, "longitude": place.lon},
                    "radius": 2000.0,
                }
            },
        },
        timeout=20,
    )
    resp.raise_for_status()
    results = resp.json().get("places") or []
    if not results:
        return None
    top = results[0]
    if top.get("rating") is None:
        return None
    return {
        "rating": round(float(top["rating"]), 1),
        "count": int(top.get("userRatingCount") or 0),
        "source": "google",
        "url": top.get("googleMapsUri"),
    }


def _fetch_tripadvisor(place: Place, key: str) -> dict | None:
    import requests

    from .fetchers.base import USER_AGENT

    headers = {"accept": "application/json", "User-Agent": USER_AGENT}
    search = requests.get(
        TRIPADVISOR_SEARCH,
        params={
            "key": key,
            "searchQuery": place.name,
            "latLong": f"{place.lat},{place.lon}",
            "language": "fr",
        },
        headers=headers,
        timeout=20,
    )
    search.raise_for_status()
    hits = search.json().get("data") or []
    if not hits:
        return None
    location_id = hits[0].get("location_id")
    if not location_id:
        return None

    details = requests.get(
        TRIPADVISOR_DETAILS.format(id=location_id),
        params={"key": key, "language": "fr"},
        headers=headers,
        timeout=20,
    )
    details.raise_for_status()
    data = details.json()
    if not data.get("rating"):
        return None
    return {
        "rating": round(float(data["rating"]), 1),
        "count": int(str(data.get("num_reviews") or 0).replace(",", "")),
        "source": "tripadvisor",
        "url": data.get("web_url"),
    }


def enrich(places: list[Place], today: str | None = None) -> list[Place]:
    """Attache une note aux activités qui n'en ont pas (ou plus assez fraîche).

    Chaque échec est isolé : une activité introuvable chez le fournisseur ou une
    erreur réseau n'interrompt pas la boucle — on note l'absence en cache pour
    ne pas la redemander à chaque passage.
    """
    name = provider()
    if not name:
        log.info(
            "[notes] aucune clé (GOOGLE_PLACES_KEY / TRIPADVISOR_API_KEY) — "
            "activités publiées sans note"
        )
        return places

    key = os.environ["GOOGLE_PLACES_KEY" if name == "google" else "TRIPADVISOR_API_KEY"]
    fetch = _fetch_google if name == "google" else _fetch_tripadvisor
    today = today or date.today().isoformat()

    asked = hits = 0
    for place in places:
        if _fresh(place, today) or place.lat is None:
            continue
        ckey = cache.key("rating", name, place.external_id or place.name, place.commune or "")
        cached = cache.get(ckey)
        if cached is not None:
            if cached:
                _apply(place, json.loads(cached))
                hits += 1
            continue
        asked += 1
        try:
            found = fetch(place, key)
        except Exception as exc:
            log.warning("[notes] %s : %s", place.name, exc)
            continue  # pas de mise en cache : l'échec peut être transitoire
        if found:
            _apply(place, found)
            hits += 1
            cache.put(ckey, json.dumps(found, ensure_ascii=False))
        else:
            cache.put(ckey, "")  # absent du fournisseur : mémorisé

    log.info("[notes] %s : %d notes attachées (%d appels)", name, hits, asked)
    return places


def _apply(place: Place, found: dict) -> None:
    place.rating = found.get("rating")
    place.rating_count = found.get("count")
    place.rating_source = found.get("source")
    place.rating_url = found.get("url")
