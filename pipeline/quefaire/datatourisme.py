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
- **API temps réel** (`DATATOURISME_API_KEY`) — `GET /v1/placeOfInterest`,
  paginé. Le périmètre est dérivé automatiquement de l'épicentre via le
  paramètre `geo_distance` : aucune liste de communes ni code départemental à
  maintenir, le rayon suit `radius_minutes` du secteur.

Sans l'un ni l'autre, le fournisseur est sauté et OSM reste seul.

AVERTISSEMENT : le flux est du JSON-LD adossé à l'ontologie DATAtourisme, dont
les producteurs remplissent inégalement les champs. L'extraction est donc
volontairement défensive (plusieurs noms de clés essayés, absence tolérée) et
`report()` sert à mesurer ce qui a réellement été reconnu sur un vrai flux.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from datetime import date

from .geo import haversine_km, travel_minutes
from .models import Place

log = logging.getLogger("quefaire")

FLUX_ENV = "DATATOURISME_FLUX_URL"
API_KEY_ENV = "DATATOURISME_API_KEY"
# Échappatoire brute : tout paramètre d'URL non modélisé ici (`sort`…). À laisser
# VIDE en temps normal — son contenu est collé tel quel à la requête, donc une
# valeur mal formée part sur chaque appel. Ne pas y mettre `lang` : le code le
# fixe à `fr`, et le doublon rendrait la langue de la réponse indéterminée.
API_PARAMS_ENV = "DATATOURISME_API_PARAMS"
# Expression `filters` optionnelle, syntaxe de l'API :
#     type=PlaceOfInterest AND isLocatedAt.address.hasAddressCity.insee=35238
# Opérateurs entre crochets ([in], [ne], [gte]…), combinables par AND/OR et
# parenthèses. Inutile ici par défaut : l'endpoint /placeOfInterest applique
# déjà le filtre de type, et le périmètre passe par geo_distance.
API_FILTERS_ENV = "DATATOURISME_API_FILTERS"
# Champs demandés : inutile de rapatrier toute l'ontologie pour chaque fiche.
#
# Deux corrections issues de la documentation de l'ontologie v3.1.0 :
# - §8.2 « Classement et labels » : les labels passent par [:hasReview], PAS par
#   un `hasLabel` — qui n'existe pas. D'où 0 label sur 1726 fiches, trois runs
#   durant, alors que c'était la promesse principale de DATAtourisme.
# - §8.5 « Localisation ET HORAIRE [:isLocatedAt] » : les horaires sont sous
#   isLocatedAt (schema:openingHoursSpecification), pas dans un champ racine.
#   Demander `isLocatedAt.geo,isLocatedAt.address` les excluait explicitement.
#
# `hasMainRepresentation` s'y ajoute pour les illustrations (§8.9) : une fiche
# sans site officiel n'a que sa page QueFaire pour donner envie, et une photo y
# vaut mieux qu'un paragraphe.
DEFAULT_FIELDS = (
    "uuid,uri,label,type,hasDescription,hasContact,hasReview,isLocatedAt,"
    "hasMainRepresentation"
)
# Endpoint par défaut : `/placeOfInterest` est un raccourci vers `/catalog` avec
# le filtre de type déjà appliqué — il ne rend que les lieux, sans les
# événements (que le crawl collecte par ailleurs), produits ni itinéraires. Il
# accepte exactement les mêmes paramètres.
API_URL_ENV = "DATATOURISME_API_URL"
API_URL = "https://api.datatourisme.fr/v1/placeOfInterest"
# Pagination : défaut 20, **maximum 250** côté API. On demande le maximum, c'est
# le levier le plus direct sur le quota (250 fiches par requête au lieu de 20).
MAX_PAGE_SIZE = 250
DEFAULT_PAGE_SIZE = MAX_PAGE_SIZE
TIMEOUT = 120

# Garde-fou de pagination. Avec geo_distance le volume est déjà borné au rayon
# de l'épicentre, mais un territoire très dense pourrait surprendre : on plafonne
# et on le DIT dans les logs plutôt que de tronquer en silence.
# 60 pages × 250 fiches = 15 000 activités, largement au-delà du réaliste.
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


def _request(url: str, headers: dict | None = None):
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
            return http_get(url, timeout=TIMEOUT, headers=headers or {})
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
    (("Producer", "ProducersGroup", "Harvest",  # ✓
      "Farm", "FarmHouse", "Craftsman", "WineCellar"), "ferme"),
    (("MegalithDolmenMenhir", "Mill", "Mine", "Monastery",  # ✓
      "Mosque", "Palace", "Lighthouse", "MilitaryCemetery", "IndustrialSite",  # ✓
      "Castle", "Church", "ReligiousSite", "RemarkableBuilding",
      "ArcheologicalSite", "DefenceSite", "Memorial"), "patrimoine"),
    (("Cinema",), "cinema"),
    (("Opera", "OperaHouse", "Recital",  # ✓
      "Theater", "Theatre", "ConcertHall", "PerformingArtsCentre"), "spectacle"),
    # `Library` retiré : une bibliothèque de village n'est pas une ludothèque.
    # Mesuré — 23 fiches, 0 distinction, 0 insolite, 34 % sans site : mal
    # rangées et sans valeur de sortie.
    (("Game", "GameRoom", "Casino"), "ludotheque"),
    (("Market", "LocalProductsShop",  # ✓
      ), "marche"),
    (("Hammam",  # ✓
      "SpaResort", "Spa", "ThermalBath", "Wellness"), "bien-etre"),
    (("Glacier", "Gorge", "Grassland", "HalophilicArea", "Hillsides", "Icefall",  # ✓
      "IslandPeninsula", "Lake", "Landes", "Mountain", "NaturalCuriosity",  # ✓
      "NaturalHeritage", "NaturalPark", "Orchard", "OutstandingTree",  # ✓
      "ParkAndGarden", "Peak", "PicnicArea", "Plain", "Plateau", "Pond",  # ✓
      "PointOfView",  # ✓
      "Garden", "Park", "NaturalSite", "Viewpoint", "Cave", "Forest"), "nature"),
    # Un PRESTATAIRE d'activités n'est pas un lieu : « Grimpe d'arbres »,
    # « Balade numérique », « séances de bien-être ». 597 fiches, soit 74 % de
    # sport-loisir. Ce n'est pas du bruit — ça répond bien à « que faire ? » —
    # mais ça relève d'une autre nature, d'où sa catégorie propre.
    (("LeisureSportActivityProvider",), "prestation"),
    (("GolfCourse", "Gymnasium", "IceSkatingRink", "LeisureComplex",  # ✓
      "Marina", "MiniGolf", "MultiActivity",  # ✓
      "NauticalCentre", "Racetrack", "RacingCircuit", "RailBike",  # ✓
      "PlayArea", "KidsClub", "HorseTour", "Rambling",  # ✓
      "SportsAndLeisurePlace", "ClimbingSpot", "EquestrianCentre",
      "BowlingAlley", "IceRink"), "sport-loisir"),
    (("Tour", "Visit", "CulturalRoute", "Itinerary"), "visite"),
]

# PAS de repli générique. `PlaceOfInterest` / `PointOfInterest` sont les types
# RACINE de l'ontologie : chaque fiche les porte, hôtels, restaurants et
# commerces compris. Une règle de repli sur ces types-là classait donc tout le
# territoire en « visite » — 6431 fiches retenues sur 6431, dont l'essentiel
# n'était pas une activité (vécu au premier run réel). Un type non listé
# ci-dessus est désormais IGNORÉ, ce qui est le comportement voulu : on
# référence des activités, pas un annuaire.

# Types que l'on sait exploiter — envoyés à l'API en filtre serveur pour ne pas
# rapatrier (ni faire présenter par le LLM) ce qu'on jetterait ensuite.
WANTED_TYPES = sorted({name for names, _ in _TYPE_RULES for name in names})

# Types RACINE et facettes transverses : les compter parmi les « non classés »
# n'apprendrait rien, chaque fiche les porte. Le décompte des types ignorés ne
# doit lister que des types PARLANTS, sinon il devient illisible et on cesse de
# le lire — c'est-à-dire inutile.
_RACINES_ONTOLOGIE = {
    "PlaceOfInterest", "PointOfInterest", "Place", "Product", "Thing",
    "schema:Place", "schema:Thing", "owl:Thing",
}

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
        # Liste de valeurs étiquetées par langue ({"@language": "en", "@value":…}) :
        # le français d'abord. Sans ce tri, l'ordre du flux décidait de la langue
        # affichée. Le paramètre `lang=fr` traite déjà le cas côté API, mais le
        # mode FLUX n'en dispose pas et un producteur peut toujours étiqueter
        # autrement : mieux vaut deux garde-fous qu'un site à moitié en anglais.
        francais = [
            item for item in node
            if isinstance(item, dict)
            and str(item.get("@language", "")).lower().startswith("fr")
        ]
        for item in francais or node:
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
    category, _ = _category_and_type(types)
    return category


def _category_and_type(types: list[str]) -> tuple[str | None, str | None]:
    """Catégorie QueFaire ET type d'ontologie qui a déclenché la règle.

    Garder le type qui a matché est ce qui permet de régler les règles sur des
    faits : sans lui, une catégorie anormalement grosse ne dit pas QUEL type la
    gonfle, et on ne peut qu'élaguer au jugé.
    """
    for names, category in _TYPE_RULES:
        for t in types:
            if t in names:
                return category, t
    return None, None


def _quality_of(node: dict) -> list[str]:
    """Labels de qualité, lus dans [:hasReview] (ontologie §8.2).

    Une :Review est un classement OU un label, adossé à un :ReviewSystem
    (« Gîtes de France », « Musée de France »…) et portant une valeur :Rating
    qui est soit un libellé, soit un nombre. On ne retient ici que le versant
    LABEL : le versant numérique est une classification (étoiles d'hôtel, épis),
    pas une note d'avis d'utilisateurs — l'afficher comme telle induirait en
    erreur.
    """
    from .normalize import fold

    blob = " ".join(_texts(_get(node, "hasReview", "review")))
    folded = fold(blob)
    found = []
    for needle, code in _LABEL_RULES:
        if needle in folded and code not in found:
            found.append(code)
    return found


def _image_of(node: dict) -> tuple[str | None, str | None]:
    """Illustration → (url, crédit), depuis [:hasMainRepresentation] (§8.9).

    La chaîne de l'ontologie est longue et le JSON-LD la rend de plusieurs
    façons selon les producteurs :

        :hasMainRepresentation → :Image → ebucore:hasRealisation
                               → ebucore:locator = l'URL

    Plutôt que d'imiter une forme précise qu'on ne peut pas vérifier ici (l'API
    n'est pas joignable depuis l'environnement de développement), on descend
    l'arbre à la recherche d'un `locator`. Un parcours tolérant vaut mieux qu'un
    chemin rigide qui rendrait 0 image pendant trois runs — c'est exactement ce
    qui était arrivé aux labels avec `hasLabel`.

    Le crédit vient de l'ebucore:Annotation, qui porte « le titre, le résumé,
    les droits » (§8.9). Sans crédit lisible on renvoie quand même l'URL : la
    page d'affichage sait dire « crédit non communiqué » plutôt que d'inventer
    un auteur.
    """
    repr_node = _get(node, "hasMainRepresentation", "hasRepresentation")
    if not repr_node:
        return None, None

    urls: list[str] = []
    credits: list[str] = []

    def descendre(n, profondeur=0):
        if profondeur > 6 or (urls and credits):
            return
        if isinstance(n, list):
            for item in n:
                descendre(item, profondeur + 1)
            return
        if not isinstance(n, dict):
            return
        for cle, valeur in n.items():
            court = cle.rsplit(":", 1)[-1]
            if court == "locator":
                for texte in _texts(valeur):
                    if texte.startswith("https://") and texte not in urls:
                        urls.append(texte)
            elif court in ("hasCredits", "credits", "rights", "hasRights"):
                credits.extend(t for t in _texts(valeur) if t.strip())
            else:
                descendre(valeur, profondeur + 1)

    descendre(repr_node)
    if not urls:
        return None, None
    return urls[0], (credits[0].strip()[:120] if credits else None)


# Jours schema.org → abrégé français, pour rendre les horaires lisibles.
_DAYS = {
    "Monday": "lun", "Tuesday": "mar", "Wednesday": "mer", "Thursday": "jeu",
    "Friday": "ven", "Saturday": "sam", "Sunday": "dim",
}


def _opening_of(located) -> str | None:
    """Horaires depuis isLocatedAt → schema:openingHoursSpecification (§8.5).

    On rend une chaîne déjà lisible en français plutôt que d'imiter la syntaxe
    OSM : la source est structurée, autant s'en servir directement.
    """
    if not isinstance(located, dict):
        return None
    spec = _get(located, "schema:openingHoursSpecification", "openingHoursSpecification")
    if not spec:
        return None
    if isinstance(spec, dict):
        spec = [spec]
    if not isinstance(spec, list):
        return None

    slots: list[str] = []
    for item in spec:
        if not isinstance(item, dict):
            continue
        days = [
            _DAYS.get(str(d).rsplit("/", 1)[-1].rsplit("#", 1)[-1], "")
            for d in _texts(_get(item, "schema:dayOfWeek", "dayOfWeek"))
        ]
        days = [d for d in days if d]
        opens = _first(_get(item, "schema:opens", "opens")) or ""
        closes = _first(_get(item, "schema:closes", "closes")) or ""
        hours = f"{opens[:5]}-{closes[:5]}" if opens and closes else ""
        label = " ".join(x for x in (", ".join(days), hours) if x)
        if label and label not in slots:
            slots.append(label)
    return " · ".join(slots[:4]) or None


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
    category, raw_type = _category_and_type(_type_names(node))
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

    image_url, image_credit = _image_of(node)
    return Place(
        name=name.strip(),
        category=category,
        source_id="datatourisme",
        sector=sector_id,
        # `@id` n'existe qu'en mode FLUX (JSON-LD complet). En mode API la fiche
        # s'identifie par `uri` / `uuid` — ne chercher que `@id` laissait 1633
        # fiches sur 2204 SANS identifiant de source, donc irréconciliables d'un
        # run à l'autre par merge() (qui indexe sur external_id) et vouées à
        # perdre leur `first_seen` à chaque passage.
        external_id=str(
            node.get("@id") or node.get("uri") or node.get("id") or node.get("uuid") or ""
        ).strip(),
        description=description.strip()[:600],
        commune=commune,
        address=street,
        lat=lat,
        lon=lon,
        url=url,
        phone=phone,
        opening_hours=_opening_of(located),
        quality=_quality_of(node),
        image_url=image_url,
        image_credit=image_credit,
        # La fiche DATAtourisme elle-même sert de page de vérification : c'est
        # là que le producteur déclare la photo et ses droits.
        image_page=str(node.get("@id") or "") or None if image_url else None,
        # Provenance du classement, conservée pour pouvoir auditer les règles
        # sur les données publiées sans relancer une découverte.
        tags=[f"dt:{raw_type}"] if raw_type else [],
        providers=["datatourisme"],
        first_seen=today,
        last_seen=today,
    )


def _nodes_from_flux(flux: str) -> list[dict]:
    """Mode FLUX (« API locale ») : une seule requête ramène tout le jeu."""
    # Le mode retenu doit se lire dans le log : les deux branches ne rendent PAS
    # les mêmes champs (le flux ignore `fields` et `lang`), donc un écart de
    # résultat s'explique d'abord par le mode, et il faut pouvoir le constater
    # au lieu de le déduire de l'absence de lignes de pagination.
    log.info("[datatourisme] mode FLUX (une requête, périmètre défini par le diffuseur)")
    payload = _request(flux).json()
    nodes = payload.get("@graph") if isinstance(payload, dict) else payload
    if not isinstance(nodes, list):
        log.warning("[datatourisme] format de flux inattendu — ni @graph ni liste")
        return []
    return [n for n in nodes if isinstance(n, dict)]


def _nodes_from_api(key: str, sector, filters: str = "") -> list[dict]:
    """Mode API temps réel : GET /v1/catalog (ou endpoint surchargé), paginé.

    On suit `meta.next` plutôt que d'incrémenter un numéro de page : c'est la
    méthode recommandée par DATAtourisme, la seule qui garantisse de ne rater
    aucun résultat en parcourant tout le catalogue.

    Le périmètre est dérivé de l'épicentre : `geo_distance=lat,lon,<rayon>km`.
    C'est exactement le modèle du projet — pas de liste de communes à maintenir,
    pas de code départemental à saisir, et le rayon suit `radius_minutes` du
    secteur. Aucune configuration manuelle n'est donc nécessaire.

    `filters` reste disponible pour affiner (syntaxe d'expression de l'API), mais
    n'est pas requis : l'endpoint /placeOfInterest applique déjà le type.
    """
    from urllib.parse import urlencode

    from .geo import radius_km

    base = os.environ.get(API_URL_ENV) or API_URL
    km = radius_km(sector.radius_minutes)

    params = {
        "geo_distance": f"{sector.center_lat},{sector.center_lon},{km:.0f}km",
        "page_size": DEFAULT_PAGE_SIZE,
        "fields": DEFAULT_FIELDS,
        # « Si le paramètre est omis, la réponse inclura le français ET l'anglais »
        # (doc API, syntaxe du paramètre lang). Le flux rendait donc les deux et
        # l'anglais arrivait le premier : 92 % des descriptions publiées étaient
        # en anglais sur un site français. Le demander explicitement règle le
        # problème à la source — et allège la réponse d'autant.
        "lang": "fr",
    }
    # Filtre de type côté SERVEUR : on ne rapatrie que ce qu'on sait classer.
    # Sans lui, /placeOfInterest rend aussi hôtels, restaurants et commerces —
    # 6431 fiches là où quelques centaines sont des activités.
    expr = (filters or os.environ.get(API_FILTERS_ENV) or "").strip()
    type_clause = f"type[in]={','.join(WANTED_TYPES)}"
    params["filters"] = f"{type_clause} AND ({expr})" if expr else type_clause
    url = f"{base}?{urlencode(params)}"
    # Échappatoire brute pour tout paramètre non modélisé ici (sort, lang…).
    extra = (os.environ.get(API_PARAMS_ENV) or "").strip().lstrip("?&")
    if extra:
        url += f"&{extra}"

    # Clé en en-tête plutôt qu'en paramètre d'URL : méthode recommandée par la
    # doc, et elle évite que la clé se retrouve dans les logs de requêtes.
    headers = {"X-API-Key": key}

    log.info("[datatourisme] mode API — rayon %.0f km autour de %s", km, sector.name)
    nodes: list[dict] = []
    for page in range(MAX_PAGES):
        payload = _request(url, headers).json()
        if not isinstance(payload, dict):
            log.warning("[datatourisme] réponse inattendue de l'API (pas un objet)")
            break
        batch = payload.get("objects") or payload.get("@graph") or []
        nodes.extend(n for n in batch if isinstance(n, dict))

        meta = payload.get("meta") or {}
        if page == 0:
            log.info(
                "[datatourisme] %s fiche(s) dans le rayon, %s page(s) à parcourir",
                meta.get("total", "?"), meta.get("total_pages", "?"),
            )
        nxt = meta.get("next")
        if not nxt:
            log.info("[datatourisme] parcours terminé : %d fiches, %d page(s)", len(nodes), page + 1)
            return nodes
        # On suit l'URL `next` telle quelle : au-delà de 10 000 résultats, l'accès
        # direct par numéro de page n'est plus possible côté API.
        url = nxt if str(nxt).startswith("http") else f"{base}{nxt}"
    else:
        log.warning(
            "[datatourisme] plafond de %d pages atteint (%d fiches) — résultat TRONQUÉ. "
            "Affinez via %s, ou utilisez un flux (%s).",
            MAX_PAGES, len(nodes), API_FILTERS_ENV, FLUX_ENV,
        )
    return nodes


def fetch(sector, limit: int | None = None) -> list[Place]:
    """Rend les activités DATAtourisme du rayon, par flux ou par API.

    Le **flux** est préféré quand il est configuré : une requête au lieu d'une
    par page, donc un coût dérisoire face au quota horaire. Mais il n'est pas
    plus fiable qu'autre chose — un flux dépublié ou mal partagé rend 403 —, et
    la clé d'API reste alors disponible : elle sert de **repli**, comme les
    miroirs Overpass et la chaîne de backups LLM. Sans ce repli, une URL de flux
    fautive faisait publier un jeu OSM seul, silencieusement amputé du tiers de
    ses fiches.

    Retourne [] (sans lever) si rien n'est configuré ou si les deux voies sont
    injoignables : c'est un complément d'OSM, son absence ne doit pas faire
    échouer la découverte.
    """
    flux = os.environ.get(FLUX_ENV)
    key = os.environ.get(API_KEY_ENV)
    if not flux and not key:
        log.info(
            "[datatourisme] ni %s ni %s — fournisseur sauté (OSM seul)", FLUX_ENV, API_KEY_ENV
        )
        return []

    nodes: list[dict] = []
    if flux:
        try:
            nodes = _nodes_from_flux(flux)
        except Exception as exc:
            log.warning("[datatourisme] flux injoignable (%s)", exc)
        if not nodes and key:
            log.warning(
                "[datatourisme] le flux n'a rien rendu — repli sur l'API (%s)", API_KEY_ENV
            )
    if not nodes and key:
        try:
            nodes = _nodes_from_api(key, sector, getattr(sector, "datatourisme_filters", ""))
        except Exception as exc:
            log.warning("[datatourisme] API injoignable (%s) — OSM seul", exc)
            return []
    if not nodes:
        log.warning("[datatourisme] aucune voie n'a rendu de fiche — OSM seul")
        return []

    today = date.today().isoformat()
    places: list[Place] = []
    seen: set[str] = set()
    # Types reçus que nos règles ne classent PAS. Ils étaient jetés en silence :
    # rien ne disait ce que la source apporte et qu'on laisse de côté, donc rien
    # ne permettait de décider d'ajouter une catégorie autrement qu'au jugé.
    # C'est ce compteur qui répond à « est-ce qu'on perd de la matière ? ».
    ignores: Counter = Counter()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        place = _to_place(node, sector.id, today)
        if place is None:
            for t in _type_names(node):
                if t not in _RACINES_ONTOLOGIE:
                    ignores[t] += 1
        if place is None or (place.external_id and place.external_id in seen):
            continue
        dist = haversine_km(sector.center_lat, sector.center_lon, place.lat, place.lon)
        if travel_minutes(dist) > sector.radius_minutes:
            continue
        if place.external_id:
            seen.add(place.external_id)
        places.append(place)

    log.info("[datatourisme] %d activités retenues sur %d fiches", len(places), len(nodes))
    if ignores:
        # À lire à chaque changement de flux : c'est l'inventaire de ce que la
        # source propose et qu'on écarte. Un type qui monte haut ici est un
        # candidat à ajouter dans _TYPE_RULES, pas une fatalité.
        log.info(
            "[datatourisme] types reçus NON classés (matière disponible, écartée) : %s",
            ", ".join(f"{t}×{n}" for t, n in ignores.most_common(12)),
        )
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


    # Types d'ontologie qui ont produit chaque catégorie : c'est CE tableau qui
    # permet de dire quel type gonfle une catégorie, et donc lequel retirer.
    par_type: dict[str, dict[str, int]] = {}
    for p in places:
        raw = next((t[3:] for t in (p.tags or []) if t.startswith("dt:")), "?")
        par_type.setdefault(p.category, Counter())[raw] += 1
    types_bruts = {
        cat: dict(Counter(c).most_common(6))
        for cat, c in sorted(par_type.items(), key=lambda kv: -sum(kv[1].values()))
    }

    total = len(places) or 1
    return {
        "total": len(places),
        "types_bruts": types_bruts,
        # Histogramme par catégorie : c'est lui qui permet de régler les règles
        # de type sur des faits plutôt qu'au jugé. Une catégorie anormalement
        # grosse signale un type d'ontologie trop large à retirer.
        "par_categorie": dict(Counter(p.category for p in places).most_common()),
        "avec_description": sum(1 for p in places if p.description),
        "avec_site": sum(1 for p in places if p.url),
        "avec_horaires": sum(1 for p in places if p.opening_hours),
        "avec_label": sum(1 for p in places if p.quality),
        # Les images, à surveiller de près en mode FLUX : le paramètre `fields`
        # n'y existe pas, le contenu est celui que l'export du diffuseur embarque.
        # Un flux sans `hasMainRepresentation` ferait disparaître d'un coup les
        # ~1600 illustrations sans autre signe qu'un chiffre qui tombe ici.
        "avec_image": sum(1 for p in places if p.image_url),
        "taux_description": round(100 * sum(1 for p in places if p.description) / total),
        "taux_site": round(100 * sum(1 for p in places if p.url) / total),
    }
