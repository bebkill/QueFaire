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

Deux accès possibles, et le choix pèse sur le quota (1000 requêtes/heure) :

- **flux** (`DATATOURISME_FLUX_URL`) — une seule requête ramène tout le jeu
  d'une ville. À privilégier ;
- **API temps réel** (`DATATOURISME_API_KEY`) — `GET /v1/catalog`, paginé. Le
  périmètre se déclare par une expression `filters` dans le registre du secteur
  (`datatourisme_filters`) : c'est une propriété du territoire, pas une variable
  globale partagée par toutes les villes.

Sans l'un ni l'autre, le fournisseur est sauté et OSM reste seul.

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
API_KEY_ENV = "DATATOURISME_API_KEY"
API_PARAMS_ENV = "DATATOURISME_API_PARAMS"  # échappatoire brute (sort, lang…)
# Le filtrage se fait par une EXPRESSION `filters`, pas par des paramètres
# dédiés. Syntaxe donnée par la doc de l'API :
#     type=PlaceOfInterest and isLocatedAt.address.hasAddressCity.insee=35238
API_FILTERS_ENV = "DATATOURISME_API_FILTERS"
# Par défaut on écarte au moins les événements, produits et itinéraires : le
# crawl collecte déjà les événements par ailleurs, ici on ne veut que des lieux.
DEFAULT_FILTERS = "type=PlaceOfInterest"
# Champs demandés : inutile de rapatrier toute l'ontologie pour chaque fiche.
DEFAULT_FIELDS = (
    "uuid,uri,label,type,hasDescription,hasContact,hasLabel,"
    "isLocatedAt.geo,isLocatedAt.address"
)
# Endpoint surchargeable : `/catalog` couvre tout (POI, événements, produits,
# itinéraires), `/placeOfInterest` ne rend que les lieux — exactement ce qu'on
# cherche, et donc bien moins de pages à parcourir. Les endpoints spécialisés
# acceptent les mêmes paramètres que /catalog.
API_URL_ENV = "DATATOURISME_API_URL"
API_URL = "https://api.datatourisme.fr/v1/catalog"
# La pagination par défaut de l'API est de 20 fiches ; demander de plus grandes
# pages est le levier le plus efficace sur le quota (500 fiches = 1 requête au
# lieu de 25). Surchargeable si la plateforme refuse cette taille.
DEFAULT_PAGE_SIZE = 500
TIMEOUT = 120

# Garde-fou de pagination. Le catalogue national compte >530 000 fiches : les
# parcourir entièrement coûterait des milliers de requêtes et pulvériserait le
# quota horaire. On plafonne, et on le DIT dans les logs plutôt que de tronquer
# en silence — c'est le signal qu'il faut restreindre le catalogue côté serveur
# (filtres API_PARAMS, ou périmètre de l'application DATAtourisme).
MAX_PAGES = 60

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
#
# Les noms marqués ✓ sont CONFIRMÉS par l'énumération `type` de la doc de l'API ;
# les autres restent des hypothèses conservées en filet (un nom inconnu ne
# matche simplement jamais, le coût d'une hypothèse erronée est donc nul).
_TYPE_RULES: list[tuple[tuple[str, ...], str]] = [
    (("Museum",  # ✓
      "InterpretationCentre",  # ✓
      "Musee", "ArtGallery"), "musee"),
    (("ThemePark", "AmusementPark", "Zoo", "Aquarium"), "parc-attraction"),
    (("WaterPark", "SwimmingPool", "Beach", "BathingSpot"), "parc-aquatique"),
    (("IndustrialSite", "MegalithDolmenMenhir", "Mill", "Mine", "Monastery",  # ✓
      "Mosque", "Palace", "Lighthouse", "MilitaryCemetery",  # ✓
      "Castle", "Church", "ReligiousSite", "RemarkableBuilding",
      "ArcheologicalSite", "DefenceSite", "Memorial", "CulturalSite"), "patrimoine"),
    (("Cinema",), "cinema"),
    (("Opera", "OperaHouse", "Recital",  # ✓
      "Theater", "Theatre", "ConcertHall", "PerformingArtsCentre"), "spectacle"),
    (("Game", "Library",  # ✓
      "GameRoom", "Casino"), "ludotheque"),
    (("Market", "LocalProductsShop",  # ✓
      ), "marche"),
    (("Producer", "ProducersGroup", "Harvest",  # ✓
      "Farm", "FarmHouse", "Craftsman", "WineCellar"), "ferme"),
    (("Hammam",  # ✓
      "SpaResort", "Spa", "ThermalBath", "Wellness"), "bien-etre"),
    (("Glacier", "Gorge", "Grassland", "HalophilicArea", "Hillsides", "Icefall",  # ✓
      "IslandPeninsula", "Lake", "Landes", "Mountain", "NaturalCuriosity",  # ✓
      "NaturalHeritage", "NaturalPark", "Orchard", "OutstandingTree",  # ✓
      "ParkAndGarden", "Peak", "PicnicArea", "Plain", "Plateau", "Pond",  # ✓
      "PointOfView",  # ✓
      "Garden", "Park", "NaturalSite", "Viewpoint", "Cave", "Forest"), "nature"),
    (("GolfCourse", "Gymnasium", "IceSkatingRink", "LeisureComplex",  # ✓
      "LeisureSportActivityProvider", "Marina", "MiniGolf", "MultiActivity",  # ✓
      "NauticalCentre", "Practice", "Racetrack", "RacingCircuit", "RailBike",  # ✓
      "PlayArea", "KidsClub", "HorseTour", "Rambling",  # ✓
      "SportsAndLeisurePlace", "ClimbingSpot", "EquestrianCentre",
      "BowlingAlley", "IceRink"), "sport-loisir"),
    (("Tour", "Visit", "CulturalRoute", "Itinerary"), "visite"),
    # Repli générique — TOUJOURS en dernier : chaque fiche porte l'un de ces
    # types en plus de son type précis, une règle placée plus haut les capterait
    # toutes et rendrait les précédentes inatteignables.
    (("PlaceOfInterest", "PointOfInterest"), "visite"),  # ✓
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
    return bool(os.environ.get(FLUX_ENV) or os.environ.get(API_KEY_ENV))


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


def _nodes_from_flux(flux: str) -> list[dict]:
    """Mode FLUX (« API locale ») : une seule requête ramène tout le jeu."""
    payload = _request(flux).json()
    nodes = payload.get("@graph") if isinstance(payload, dict) else payload
    if not isinstance(nodes, list):
        log.warning("[datatourisme] format de flux inattendu — ni @graph ni liste")
        return []
    return [n for n in nodes if isinstance(n, dict)]


def _nodes_from_api(key: str, filters: str = "") -> list[dict]:
    """Mode API temps réel : GET /v1/catalog (ou endpoint surchargé), paginé.

    On suit `meta.next` plutôt que d'incrémenter un numéro de page : c'est la
    méthode recommandée par DATAtourisme, la seule qui garantisse de ne rater
    aucun résultat en parcourant tout le catalogue.

    `filters` est une EXPRESSION au format DATAtourisme, pas une query string :
        type=PlaceOfInterest and isLocatedAt.address.hasAddressCity.insee=35238
    Elle vient du registre du secteur — le bon périmètre dépend du territoire,
    il n'a rien à faire dans une variable globale partagée par toutes les villes.
    """
    from urllib.parse import urlencode

    base = os.environ.get(API_URL_ENV) or API_URL
    expr = (filters or os.environ.get(API_FILTERS_ENV) or DEFAULT_FILTERS).strip()

    params = {"api_key": key, "page_size": DEFAULT_PAGE_SIZE}
    if expr:
        params["filters"] = expr
    # `fields` allège la réponse : on ne demande que ce que _to_place exploite.
    if DEFAULT_FIELDS:
        params["fields"] = DEFAULT_FIELDS
    url = f"{base}?{urlencode(params)}"
    # Échappatoire brute pour tout paramètre non modélisé ici (sort, lang…).
    extra = (os.environ.get(API_PARAMS_ENV) or "").strip().lstrip("?&")
    if extra:
        url += f"&{extra}"

    nodes: list[dict] = []
    for page in range(MAX_PAGES):
        payload = _request(url).json()
        if not isinstance(payload, dict):
            log.warning("[datatourisme] réponse inattendue de l'API (pas un objet)")
            break
        batch = payload.get("objects") or payload.get("@graph") or []
        nodes.extend(n for n in batch if isinstance(n, dict))

        meta = payload.get("meta") or {}
        nxt = meta.get("next") or meta.get("next_url")
        if not nxt:
            log.info("[datatourisme] catalogue parcouru : %d fiches, %d page(s)", len(nodes), page + 1)
            return nodes
        # L'URL `next` porte déjà la clé et les filtres ; on la suit telle quelle.
        url = nxt if str(nxt).startswith("http") else f"{base}{nxt}"
    else:
        log.warning(
            "[datatourisme] plafond de %d pages atteint (%d fiches) — catalogue TRONQUÉ. "
            "Restreignez-le côté serveur via %s (ex. un filtre départemental) "
            "ou utilisez un flux (%s).",
            MAX_PAGES, len(nodes), API_PARAMS_ENV, FLUX_ENV,
        )
    return nodes


def fetch(sector, limit: int | None = None) -> list[Place]:
    """Rend les activités DATAtourisme du rayon, par flux ou par API.

    Le **flux** est préféré quand il est configuré : une requête au lieu d'une
    par page, donc un coût dérisoire face au quota horaire. L'API sert de repli
    quand on ne dispose que d'une clé.

    Retourne [] (sans lever) si rien n'est configuré ou si la source est
    injoignable : c'est un complément d'OSM, son absence ne doit pas faire
    échouer la découverte.
    """
    flux = os.environ.get(FLUX_ENV)
    key = os.environ.get(API_KEY_ENV)
    if not flux and not key:
        log.info(
            "[datatourisme] ni %s ni %s — fournisseur sauté (OSM seul)", FLUX_ENV, API_KEY_ENV
        )
        return []

    try:
        nodes = _nodes_from_flux(flux) if flux else _nodes_from_api(
            key, getattr(sector, "datatourisme_filters", "")
        )
    except Exception as exc:
        log.warning("[datatourisme] source injoignable (%s) — OSM seul", exc)
        return []
    if not nodes:
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

    log.info("[datatourisme] %d activités retenues sur %d fiches", len(places), len(nodes))
    if nodes and not places:
        # Des fiches reçues mais aucune reconnue : c'est le symptôme d'un
        # mapping de champs à corriger (l'ontologie est riche et les
        # producteurs la remplissent inégalement), pas d'un territoire vide.
        log.warning(
            "[datatourisme] %d fiches reçues, AUCUNE exploitable — vérifier le mapping "
            "des types (@type) et des coordonnées (isLocatedAt/schema:geo) contre un "
            "échantillon réel du flux",
            len(nodes),
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
