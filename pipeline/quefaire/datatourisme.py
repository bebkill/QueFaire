"""DATAtourisme : second fournisseur d'activités permanentes.

OpenStreetMap dit « il y a un musée ici » ; DATAtourisme dit ce qu'on y voit et
quand c'est ouvert. La base nationale (pilotée par ADN Tourisme) est alimentée
par les offices de tourisme, ADT et CRT **eux-mêmes** : là où OSM dépend de la
densité de contributeurs bénévoles — faible en zone rurale — DATAtourisme tient
sa couverture des professionnels du territoire. Les deux sont complémentaires :
OSM couvre le non-touristique (cinéma de quartier, ludothèque, piscine),
DATAtourisme couvre le touristique en profondeur (description, horaires,
tarifs, labels).

Licence Ouverte Etalab : réutilisation libre, y compris commerciale, à condition
de citer la source et la date de mise à jour — d'où l'attribution affichée sur
la page « à propos ».

Accès : on lit un **flux** créé dans le diffuseur DATAtourisme, dont l'URL
complète (identifiant du flux + clé) est fournie par `DATATOURISME_FLUX_URL`.
C'est le mécanisme stable et documenté de la plateforme. Sans cette variable, le
fournisseur est simplement sauté — OSM reste la source par défaut.

AVERTISSEMENT : le flux est du JSON-LD adossé à l'ontologie DATAtourisme, dont
les producteurs remplissent inégalement les champs. L'extraction est donc
volontairement défensive (plusieurs noms de clés essayés, absence tolérée) et
`report()` sert à mesurer ce qui a réellement été reconnu sur un vrai flux.
"""

from __future__ import annotations

import logging
import os
from datetime import date

from .geo import haversine_km, travel_minutes
from .models import Place

log = logging.getLogger("quefaire")

FLUX_ENV = "DATATOURISME_FLUX_URL"
TIMEOUT = 120

# --- Quotas DATAtourisme -----------------------------------------------------
# La plateforme annonce : 20 à 30 requêtes concurrentes, ~10 req/s en régime
# prolongé, 1000 requêtes/heure.
#
# Notre mode d'accès est le FLUX (« API locale ») : une seule requête ramène
# tout le jeu d'une ville, contre une requête par fiche dans un usage temps
# réel. Le budget réel est donc d'UNE requête par ville et par passage
# hebdomadaire — trois ordres de grandeur sous le plafond horaire, même en
# multipliant les épicentres.
#
# RÈGLE DE CONCEPTION : rester en mode « lot ». Un enrichissement fiche par
# fiche (un appel par activité) consommerait ~500 requêtes pour une seule
# ville, soit la moitié du quota horaire — c'est ce qu'il ne faut jamais faire.
#
# Les garde-fous ci-dessous sont malgré tout appliqués : on partage l'API avec
# d'autres réutilisateurs, et un bug de boucle ne doit pas nous faire bannir.
MAX_REQUESTS_PER_HOUR = 1000
MIN_INTERVAL_S = 0.2  # ≤ 5 req/s, la moitié du régime prolongé toléré
MAX_RETRIES = 3

_last_request_at = 0.0
_requests_made = 0


def _throttle() -> None:
    """Espace les requêtes pour ne jamais approcher le régime toléré."""
    import time

    global _last_request_at
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _request(url: str):
    """GET avec throttle, plafond de sécurité et respect du 429.

    Un 429 (« trop de requêtes ») est rejoué en respectant l'en-tête
    `Retry-After` quand il est fourni : c'est la seule façon polie de réagir à
    un quota atteint, et ça évite l'escalade vers un blocage.
    """
    import time

    import requests

    from .fetchers.base import http_get

    global _requests_made
    if _requests_made >= MAX_REQUESTS_PER_HOUR:
        raise RuntimeError(
            f"plafond de sécurité atteint ({MAX_REQUESTS_PER_HOUR} requêtes) — "
            "signe d'une boucle anormale, on s'arrête avant le quota réel"
        )

    for attempt in range(MAX_RETRIES + 1):
        _throttle()
        _requests_made += 1
        try:
            return http_get(url, timeout=TIMEOUT)
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            if status not in (429, 503) or attempt == MAX_RETRIES:
                raise
            retry_after = (exc.response.headers or {}).get("Retry-After")
            delay = float(retry_after) if (retry_after or "").isdigit() else 2 ** (attempt + 1)
            log.warning(
                "[datatourisme] %s — quota/surcharge, nouvelle tentative dans %.0f s (%d/%d)",
                status, delay, attempt + 1, MAX_RETRIES,
            )
            time.sleep(delay)

# Types de l'ontologie DATAtourisme → catégories QueFaire. On teste par
# inclusion dans la liste @type (une fiche en porte plusieurs, du général au
# précis) ; l'ordre décide, le plus spécifique d'abord.
_TYPE_RULES: list[tuple[tuple[str, ...], str]] = [
    (("Museum", "Musee", "ArtGallery", "InterpretationCentre"), "musee"),
    (("ThemePark", "AmusementPark", "Zoo", "Aquarium"), "parc-attraction"),
    (("WaterPark", "SwimmingPool", "Beach", "BathingSpot"), "parc-aquatique"),
    (("Castle", "Church", "ReligiousSite", "RemarkableBuilding", "ArcheologicalSite",
      "DefenceSite", "Memorial", "CulturalSite", "IndustrialSite"), "patrimoine"),
    (("Cinema",), "cinema"),
    (("Theater", "Theatre", "ConcertHall", "PerformingArtsCentre"), "spectacle"),
    (("GameRoom", "Casino"), "ludotheque"),
    (("Market", "LocalProductsShop"), "marche"),
    (("Farm", "FarmHouse", "Craftsman", "WineCellar"), "ferme"),
    (("SpaResort", "Spa", "ThermalBath", "Wellness"), "bien-etre"),
    (("Garden", "Park", "NaturalHeritage", "NaturalSite", "Viewpoint", "Lake",
      "Cave", "Forest"), "nature"),
    (("SportsAndLeisurePlace", "ClimbingSpot", "EquestrianCentre", "GolfCourse",
      "BowlingAlley", "IceRink", "Practice"), "sport-loisir"),
    (("Tour", "Visit", "CulturalRoute", "Itinerary"), "visite"),
    (("PointOfInterest", "PlaceOfInterest"), "visite"),  # repli générique
]

# Libellés de labels DATAtourisme → codes QUALITY_LABELS. Comparaison sur le
# texte replié (sans accents, minuscules) : les producteurs écrivent
# « Musée de France », « musee de france », « Label Musée de France »…
_LABEL_RULES: list[tuple[str, str]] = [
    ("monument historique", "monument-historique"),
    ("musee de france", "musee-de-france"),
    ("patrimoine mondial", "unesco"),
    ("unesco", "unesco"),
    ("jardin remarquable", "jardin-remarquable"),
    ("maisons des illustres", "maisons-des-illustres"),
    ("maison des illustres", "maisons-des-illustres"),
    ("art et d histoire", "art-et-histoire"),
    ("qualite tourisme", "qualite-tourisme"),
    ("tourisme et handicap", "tourisme-handicap"),
    ("tourisme handicap", "tourisme-handicap"),
]


def available() -> bool:
    return bool(os.environ.get(FLUX_ENV))


# --- Extraction JSON-LD défensive --------------------------------------------

def _texts(node) -> list[str]:
    """Aplatit une valeur JSON-LD en liste de chaînes.

    Le même champ arrive selon les producteurs en chaîne nue, en objet
    {"@value": …}, en dictionnaire de langues {"fr": [...]}, ou en liste de
    tout ça. On ramène le tout à des chaînes plutôt que d'imposer une forme.
    """
    out: list[str] = []
    if node is None:
        return out
    if isinstance(node, str):
        return [node]
    if isinstance(node, (int, float)):
        return [str(node)]
    if isinstance(node, list):
        for item in node:
            out.extend(_texts(item))
        return out
    if isinstance(node, dict):
        if "@value" in node:
            return _texts(node["@value"])
        # Dictionnaire de langues : le français d'abord, sinon n'importe quoi.
        for key in ("fr", "fr-FR"):
            if key in node:
                return _texts(node[key])
        for value in node.values():
            out.extend(_texts(value))
    return out


def _first(node, default: str | None = None) -> str | None:
    values = [t.strip() for t in _texts(node) if isinstance(t, str) and t.strip()]
    return values[0] if values else default


def _get(node: dict, *keys):
    """Premier champ présent parmi plusieurs noms possibles.

    Le JSON-LD peut être compacté (« rdfs:label ») ou non (URI complète) selon
    la configuration du flux : on essaie les deux, plus le suffixe nu.
    """
    for key in keys:
        if key in node:
            return node[key]
        for actual in node:
            if actual.rsplit("#", 1)[-1].rsplit("/", 1)[-1] == key.rsplit(":", 1)[-1]:
                return node[actual]
    return None


def _type_names(node: dict) -> list[str]:
    raw = node.get("@type") or node.get("type") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(t).rsplit("#", 1)[-1].rsplit("/", 1)[-1] for t in raw]


def _category_of(types: list[str]) -> str | None:
    for names, category in _TYPE_RULES:
        if any(t in names for t in types):
            return category
    return None


def _quality_of(node: dict) -> list[str]:
    from .normalize import fold

    blob = " ".join(
        _texts(_get(node, "hasLabel", "label")) + _texts(_get(node, "hasQualityLabel"))
    )
    folded = fold(blob)
    found = []
    for needle, code in _LABEL_RULES:
        if needle in folded and code not in found:
            found.append(code)
    return found


def _coords(located) -> tuple[float | None, float | None]:
    if not isinstance(located, dict):
        return None, None
    geo = _get(located, "schema:geo", "geo") or {}
    if isinstance(geo, list) and geo:
        geo = geo[0]
    if not isinstance(geo, dict):
        return None, None
    lat = _first(_get(geo, "schema:latitude", "latitude"))
    lon = _first(_get(geo, "schema:longitude", "longitude"))
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None


def _address(located) -> tuple[str | None, str | None]:
    """(commune, rue) depuis le bloc adresse."""
    if not isinstance(located, dict):
        return None, None
    addr = _get(located, "schema:address", "address") or {}
    if isinstance(addr, list) and addr:
        addr = addr[0]
    if not isinstance(addr, dict):
        return None, None
    city = _first(_get(addr, "schema:addressLocality", "addressLocality"))
    street = _first(_get(addr, "schema:streetAddress", "streetAddress"))
    return city, street


def _to_place(node: dict, sector_id: str, today: str) -> Place | None:
    category = _category_of(_type_names(node))
    if not category:
        return None
    name = _first(_get(node, "rdfs:label", "label", "name"))
    if not name:
        return None

    located = _get(node, "isLocatedAt", "location") or {}
    if isinstance(located, list) and located:
        located = located[0]
    lat, lon = _coords(located)
    if lat is None:
        return None
    commune, street = _address(located)

    contact = _get(node, "hasContact", "contact") or {}
    if isinstance(contact, list) and contact:
        contact = contact[0]
    url = _first(_get(contact, "foaf:homepage", "homepage", "url")) if isinstance(contact, dict) else None
    phone = _first(_get(contact, "schema:telephone", "telephone")) if isinstance(contact, dict) else None

    description = _first(
        _get(node, "hasDescription", "description") if not isinstance(
            _get(node, "hasDescription", "description"), dict
        ) else _get(_get(node, "hasDescription", "description"), "shortDescription", "dc:description")
    ) or ""

    return Place(
        name=name.strip(),
        category=category,
        source_id="datatourisme",
        sector=sector_id,
        external_id=str(node.get("@id") or node.get("id") or "").strip(),
        description=description.strip()[:600],
        commune=commune,
        address=street,
        lat=lat,
        lon=lon,
        url=url,
        phone=phone,
        opening_hours=_first(_get(node, "hasBookingContact", "openingHoursSpecification")),
        quality=_quality_of(node),
        providers=["datatourisme"],
        first_seen=today,
        last_seen=today,
    )


def fetch(sector, limit: int | None = None) -> list[Place]:
    """Lit le flux DATAtourisme et rend les activités du rayon.

    Retourne [] (sans lever) si le flux n'est pas configuré ou injoignable :
    c'est un complément d'OSM, son absence ne doit pas faire échouer la
    découverte.
    """
    flux = os.environ.get(FLUX_ENV)
    if not flux:
        log.info("[datatourisme] %s non configurée — fournisseur sauté", FLUX_ENV)
        return []

    try:
        payload = _request(flux).json()
    except Exception as exc:
        log.warning("[datatourisme] flux injoignable (%s) — OSM seul", exc)
        return []

    nodes = payload.get("@graph") if isinstance(payload, dict) else payload
    if not isinstance(nodes, list):
        log.warning("[datatourisme] format inattendu — ni @graph ni liste")
        return []

    today = date.today().isoformat()
    places: list[Place] = []
    seen: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        place = _to_place(node, sector.id, today)
        if place is None or (place.external_id and place.external_id in seen):
            continue
        dist = haversine_km(sector.center_lat, sector.center_lon, place.lat, place.lon)
        if travel_minutes(dist) > sector.radius_minutes:
            continue
        if place.external_id:
            seen.add(place.external_id)
        places.append(place)

    log.info(
        "[datatourisme] %d activités retenues sur %d fiches du flux", len(places), len(nodes)
    )
    return places[:limit] if limit else places


def report(places: list[Place]) -> dict:
    """Mesure ce que le flux a réellement fourni — à regarder au premier vrai run.

    L'ontologie est riche mais inégalement remplie : ce compte-rendu dit si le
    mapping des champs tient face aux données réelles, plutôt que de le supposer.
    """
    total = len(places) or 1
    return {
        "total": len(places),
        "avec_description": sum(1 for p in places if p.description),
        "avec_site": sum(1 for p in places if p.url),
        "avec_horaires": sum(1 for p in places if p.opening_hours),
        "avec_label": sum(1 for p in places if p.quality),
        "taux_description": round(100 * sum(1 for p in places if p.description) / total),
        "taux_site": round(100 * sum(1 for p in places if p.url) / total),
    }
