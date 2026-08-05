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

import itertools
import logging
import os
from collections import Counter
from datetime import date

from .geo import haversine_km, travel_minutes
from .models import Place

log = logging.getLogger("quefaire")

FLUX_ENV = "DATATOURISME_FLUX_URL"
API_KEY_ENV = "DATATOURISME_API_KEY"

# Inspecteur de source, à la demande : `QUEFAIRE_DUMP_TYPE=EntertainmentAndEvent`
# fait recopier dans le log les premières fiches brutes portant ce type.
#
# Le flux n'est atteignable que depuis la CI (URL signée en secret, et le proxy de
# l'environnement de développement bloque le domaine). Sans ça, toute question sur
# la FORME d'un type non encore exploité se réglerait de mémoire ou par analogie —
# et l'ontologie a déjà démenti trois suppositions de ce genre (`hasLabel`
# inexistant, horaires sous `isLocatedAt`, flux livré en ZIP). Regarder coûte un
# run ; deviner a coûté trois.
#
# Volontairement inerte hors demande explicite : aucun effet sur un run normal.
DUMP_TYPE_ENV = "QUEFAIRE_DUMP_TYPE"
DUMP_MAX = 2
DUMP_CHARS = 6000

# Sous-arbres élidés dans l'aperçu : métadonnées d'édition et de traduction. Ce
# n'est pas de la coquetterie — le premier essai tronquait le JSON brut à 3500
# caractères, et comme les clés arrivent dans l'ordre de la source, la coupe est
# tombée en plein `hasTranslatedProperty` sans avoir atteint `takesPlaceAt`. Une
# troncature aveugle sur du JSON ne montre pas ce qu'on cherche, elle montre ce qui
# vient en premier. On élide donc le bruit connu au lieu de couper au caractère.
_APERCU_BRUIT = frozenset({
    "hasTranslatedProperty", "hasBeenPublishedBy", "hasBeenCreatedBy",
    "dc:contributor", "@context", "hasAudience",
})
_APERCU_TEXTE = 120
_APERCU_LISTE = 3
_APERCU_PROFONDEUR = 5


def _apercu(valeur, profondeur: int = 0):
    """Aperçu structurel d'un nœud : la FORME, pas le volume.

    Textes tronqués, listes échantillonnées, bruit d'édition élidé. Ce qu'on veut
    voir d'un type non exploité, c'est quels champs existent et comment ils sont
    imbriqués — jamais les 2000 caractères d'une description.
    """
    if profondeur > _APERCU_PROFONDEUR:
        return "…"
    if isinstance(valeur, dict):
        return {
            cle: _apercu(sous, profondeur + 1)
            for cle, sous in valeur.items()
            if cle not in _APERCU_BRUIT
        }
    if isinstance(valeur, list):
        tete = [_apercu(v, profondeur + 1) for v in valeur[:_APERCU_LISTE]]
        reste = len(valeur) - _APERCU_LISTE
        return tete + [f"(+{reste} autres)"] if reste > 0 else tete
    if isinstance(valeur, str) and len(valeur) > _APERCU_TEXTE:
        return valeur[:_APERCU_TEXTE] + "…"
    return valeur
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

# Garde-fous de décompression du flux.
#
# La version précédente bornait le CUMUL décompressé à 500 Mo. Elle a tronqué un
# flux parfaitement légitime (19 685 fiches, 119,6 Mo compressés) : des centaines
# de fiches n'ont survécu au run que par la rétention, et auraient disparu du
# catalogue au bout de deux passages. Le cumul ne mesurait rien de réel — les
# membres sont lus et libérés un par un, jamais tous présents en mémoire.
#
# Ce qui coûte vraiment, c'est le NOMBRE de fiches retenues (la liste qu'on
# construit et qu'on garde) et la taille du plus GROS membre (seul pic possible).
# On borne donc ces deux grandeurs-là, et rien d'autre.
MAX_FLUX_RECORDS = 200_000
MAX_MEMBER_BYTES = 50_000_000

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

# Statuts rejoués avec un temps d'attente croissant : quota (429) et pannes de
# passerelle transitoires. Le 504 « Aucune réponse du serveur de traitement » de
# DATAtourisme y a été ajouté après l'avoir vu faire échouer un flux sans même
# une seconde tentative — alors que c'est l'archétype de l'erreur passagère, et
# que la génération du flux est justement une opération longue côté serveur. Les
# miroirs Overpass traitaient déjà le 504 comme transitoire ; l'incohérence entre
# les deux fournisseurs n'avait pas de raison d'être.
_STATUTS_REJOUES = (429, 502, 503, 504)

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
            if status not in _STATUTS_REJOUES or attempt == MAX_RETRIES:
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

# Types dont la présence DISQUALIFIE la fiche, quel que soit le reste.
#
# « Non classé » et « exclu » n'étaient pas la même chose, et le code ne
# connaissait que le premier. Une fiche portant plusieurs types passe dès qu'UN
# d'eux est classé : le retrait de `Library` des règles, décidé sur mesure
# (23 fiches en Aveyron, aucune valeur de sortie), n'a donc rien empêché à
# Villemoirieu — les 261 bibliothèques y entrent comme `SportsAndLeisurePlace`.
# Même mécanisme pour 211 bars à vin, alors que `FoodEstablishment` est le type
# le plus massivement rejeté du flux (4079 fiches). C'est le motif des 56
# monuments aux morts : une exclusion qu'une seconde voie contourne.
#
# Volontairement ÉTROIT. `LocalBusiness` serait tentant — 268 fiches — mais 98
# musées sur 98 le portent aussi : il ne sépare rien. `FoodEstablishment` et
# `Winery` sont écartés de cette liste pour la même raison inverse : une cave
# qui fait déguster est une visite légitime (`WineCellar` → ferme), et les trois
# types arrivent ensemble sur les mêmes 211 fiches. `BistroOrWineBar` est le
# terme précis qui désigne le débit de boisson, donc le seul retenu.
#
# Toute addition ici se chiffre AVANT d'être adoptée : le log compte ce que la
# liste écarte, par type.
_TYPES_EXCLUANTS = frozenset({
    "Library", "schema:Library",
    "BistroOrWineBar",
})

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
    # Un type disqualifiant l'emporte sur TOUTES les règles — sinon l'exclusion
    # ne serait qu'une non-inclusion, contournable par n'importe quel autre type
    # de la même fiche.
    if any(t in _TYPES_EXCLUANTS for t in types):
        return None, None
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


def _compte_categorie(places: list[Place], categorie: str) -> int:
    return sum(1 for p in places if p.category == categorie)


def _dans_le_rayon(node: dict, sector) -> bool:
    """Une fiche non classée tombe-t-elle dans le rayon de l'épicentre ?

    Sert à chiffrer honnêtement la matière écartée : le flux couvre le périmètre du
    DIFFUSEUR, jamais le nôtre. Compter un type sur tout le flux revient à annoncer
    un gisement dont la majeure partie est hors de portée.

    Même critère que pour une fiche retenue — temps de trajet, pas distance à vol
    d'oiseau — sinon le chiffre annoncé ne serait pas celui qu'on pourrait publier.
    """
    located = _get(node, "isLocatedAt", "location") or {}
    if isinstance(located, list) and located:
        located = located[0]
    lat, lon = _coords(located)
    if lat is None or lon is None:
        return False
    dist = haversine_km(sector.center_lat, sector.center_lon, lat, lon)
    return travel_minutes(dist) <= sector.radius_minutes


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
    # Le TÉLÉCHARGEMENT reste immédiat : c'est lui qui échoue (403, 503, 504), et
    # son exception doit remonter à l'appelant pour déclencher le repli sur l'API.
    # Dans un générateur pur, elle ne surviendrait qu'à la première itération,
    # bien après le `try` de `fetch()` — le repli ne jouerait plus.
    corps = _request(flux).content

    # Le PARCOURS, lui, est paresseux. Un flux élargi peut dépasser le
    # demi-gigaoctet décompressé, et matérialiser tous les documents parsés en même
    # temps coûte plusieurs gigaoctets de dictionnaires Python. `fetch()` convertit
    # chaque fiche au fil de l'eau et libère aussitôt celles qu'il n'exploite pas.
    # Un dépassement mémoire ne serait PAS rattrapable : le noyau tue le processus,
    # et le repli sur l'API ne jouerait pas davantage.
    flot = _fiches_du_corps(corps)
    try:
        premiere = next(flot)
    except StopIteration:
        # Une liste vide (donc fausse) plutôt qu'un générateur épuisé : c'est ce
        # que `fetch()` teste pour décider du repli.
        log.warning(
            "[datatourisme] flux téléchargé (%.1f Mo) mais aucune fiche exploitable "
            "— archive vide ou format inattendu",
            len(corps) / 1e6,
        )
        return []
    return itertools.chain([premiere], flot)


def _fiches_du_corps(corps: bytes):
    """Parcourt les documents du corps et rend les nœuds, un à un.

    On compte les **nœuds**, pas les « fiches » : un document peut porter un
    `@graph` de plusieurs entités, et l'archive complète en a livré 47 082 pour
    23 543 membres. Appeler ça des fiches laissait croire que le flux contenait
    deux fois plus d'activités qu'en réalité, et faussait le taux de sélectivité
    affiché juste après (« N retenues sur M »).

    Ce que trois runs de mesure ont établi, et qui n'a plus à être redécouvert :

    - **23 541 identifiants distincts, 0 répétition** — l'archive ne répète aucun
      POI, la conversion ne travaille jamais deux fois.
    - **la distribution des nœuds par document** (`1→23541, 23541→1`) a désigné le
      coupable des 23 541 nœuds anonymes : un **manifeste**, pas des fragments de
      fiche. D'où `_est_un_manifeste()`, et le témoin de complétude qui en découle.
    - **le manifeste ne porte aucun horaire** : l'anomalie `avec_horaires: 3` reste
      entière, et il faut la chercher ailleurs.

    Le compteur de répétitions est conservé même si la réponse est connue : c'est
    une propriété de la SOURCE, pas du code, et elle peut changer sans nous avertir.
    """
    documents = noeuds = sans_id = annoncees = 0
    vus: set[str] = set()
    for doc in _documents_du_flux(corps):
        documents += 1
        if _est_un_manifeste(doc):
            # L'index de l'archive : on retient ce qu'il annonce et on ne tente pas
            # d'en faire des activités.
            annoncees += len(doc)
            continue
        for node in _nodes_du_document(doc):
            noeuds += 1
            ident = str(node.get("@id") or node.get("uri") or node.get("uuid") or "")
            if ident:
                vus.add(ident)
            else:
                sans_id += 1
            yield node
    log.info(
        "[datatourisme] flux : %.1f Mo compressés, %d document(s), %d nœud(s), "
        "%d identifiant(s) distinct(s), %d répétition(s), %d sans identifiant",
        len(corps) / 1e6, documents, noeuds, len(vus),
        max(0, noeuds - sans_id - len(vus)), sans_id,
    )
    if annoncees and annoncees != len(vus):
        # Écart entre ce que la source déclare livrer et ce qu'on a lu. Jusqu'ici,
        # seule une chute inexpliquée du catalogue le révélait — deux semaines plus
        # tard, quand la rétention finissait par lâcher.
        log.warning(
            "[datatourisme] le manifeste annonce %d fiches, %d lues — écart de %d, "
            "archive amputée ou membres illisibles",
            annoncees, len(vus), annoncees - len(vus),
        )
    elif annoncees:
        log.info("[datatourisme] manifeste : %d fiches annoncées, autant lues", annoncees)


def _documents_du_flux(corps: bytes):
    """Rend les documents JSON contenus dans le corps du flux, quel qu'en soit
    l'emballage.

    Un flux DATAtourisme est livré en **archive ZIP** — c'est ce qui a fait
    échouer le premier vrai passage en mode flux, avec un
    `Expecting value: line 1 column 1` parfaitement opaque : le téléchargement
    réussissait, `.json()` recevait du binaire.

    On reconnaît l'emballage aux octets de tête plutôt qu'à l'extension de l'URL
    ou à l'en-tête `Content-Type`, ni l'un ni l'autre n'étant fiables sur un lien
    de téléchargement signé. Le JSON nu et le gzip restent acceptés : le mode
    flux doit survivre à un changement d'emballage côté diffuseur.

    La disposition interne de l'archive n'est PAS devinée : on tente chaque membre
    `.json`/`.jsonld` et on garde ce qui se lit. Celle du diffuseur, mesurée
    depuis, tient en un fichier par fiche **plus un manifeste** — voir
    `_est_un_manifeste()`. Ce parti d'ouverture reste le bon : le mode flux doit
    survivre à une réorganisation côté source.

    Un membre illisible n'est plus sauté en silence : c'est indiscernable d'une
    fiche absente du flux, et le manifeste permet désormais de le chiffrer.
    """
    import gzip
    import io
    import json as _json
    import zipfile

    if corps[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(corps)) as zf:
            membres = [
                info for info in zf.infolist()
                if not info.is_dir() and info.filename.lower().endswith((".json", ".jsonld"))
            ]
            # `file_size` vient de l'index de l'archive : le total décompressé est
            # connu AVANT d'avoir décompressé quoi que ce soit. On l'annonce, et
            # une troncature éventuelle se chiffre alors sur le total réel — au
            # lieu de laisser deviner combien de fiches manquent.
            log.info(
                "[datatourisme] archive : %d membre(s), %.0f Mo décompressés",
                len(membres), sum(info.file_size for info in membres) / 1e6,
            )
            if len(membres) > MAX_FLUX_RECORDS:
                log.warning(
                    "[datatourisme] archive TRONQUÉE à %d fiches sur %d (garde-fou) "
                    "— des fiches sont perdues. Relever MAX_FLUX_RECORDS après avoir "
                    "vérifié la mémoire du runner.",
                    MAX_FLUX_RECORDS, len(membres),
                )
                membres = membres[:MAX_FLUX_RECORDS]
            illisibles = 0
            for info in membres:
                if info.file_size > MAX_MEMBER_BYTES:
                    # Seul pic mémoire possible : un membre lu d'un bloc. Le sauter
                    # se DIT, sans quoi une fiche manquante passerait pour absente
                    # du flux.
                    # Taille en octets, pas en Mo : un « 0 Mo » dans un
                    # avertissement de dépassement serait un instrument qui mentirait.
                    log.warning(
                        "[datatourisme] membre %s ignoré : %d octets décompressés "
                        "(garde-fou à %d)",
                        info.filename, info.file_size, MAX_MEMBER_BYTES,
                    )
                    continue
                try:
                    yield _json.loads(zf.read(info))
                except (ValueError, OSError):
                    illisibles += 1
            if illisibles:
                log.warning(
                    "[datatourisme] %d membre(s) illisible(s) sur %d — autant de "
                    "fiches perdues, indiscernables d'une absence du flux",
                    illisibles, len(membres),
                )
        return

    if corps[:2] == b"\x1f\x8b":
        corps = gzip.decompress(corps)

    try:
        yield _json.loads(corps)
    except ValueError as exc:
        log.warning("[datatourisme] corps de flux illisible : %s", exc)


def _nodes_du_document(doc) -> list[dict]:
    """Fiches contenues dans un document : `@graph`, liste nue, ou fiche unique.

    Le cas « fiche unique » compte : si l'archive contient un fichier par POI,
    chaque document EST une fiche, sans enveloppe.
    """
    if isinstance(doc, dict):
        graph = doc.get("@graph")
        if isinstance(graph, list):
            return [n for n in graph if isinstance(n, dict)]
        # Une fiche seule se reconnaît à son type ou à son identifiant.
        if doc.get("@type") or doc.get("@id") or doc.get("uri"):
            return [doc]
        return []
    if isinstance(doc, list):
        return [n for n in doc if isinstance(n, dict)]
    return []


def _est_un_manifeste(doc) -> bool:
    """Vrai pour l'index de l'archive : une liste d'entrées `{label, file, …}`.

    Mesuré, pas supposé. La distribution des nœuds par document a livré
    `1→23541 doc(s), 23541→1 doc(s)` : 23 541 fichiers d'une fiche chacun, plus UN
    document de 23 541 entrées, dont les champs sont `label`,
    `lastUpdateDatatourisme` et `file`. C'est le manifeste de l'archive — la
    déclaration, par la source, de ce qu'elle livre.

    Deux conséquences. On cesse d'essayer d'en convertir les entrées en activités :
    sans `@type`, les 23 541 échouaient d'avance. Et on s'en sert de **témoin de
    complétude** : le nombre de fiches annoncées se compare à celui des fiches
    lues, ce qui rend visible un membre illisible ou une archive amputée — ce que
    seule une chute inexpliquée du catalogue signalait jusqu'ici.

    Note : le manifeste ne porte AUCUN horaire. Il n'explique donc pas
    `avec_horaires: 3` ; cette anomalie reste ouverte, et l'hypothèse d'un nœud
    voisin porteur d'horaires est écartée.
    """
    return (
        isinstance(doc, list)
        and bool(doc)
        and all(
            isinstance(entree, dict)
            and "file" in entree
            and not (entree.get("@id") or entree.get("@type") or entree.get("uri"))
            for entree in doc
        )
    )


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
            # Distinguer « pas joignable » de « joignable mais illisible » : le
            # premier vrai passage en mode flux a affiché « injoignable » pour un
            # JSONDecodeError, ce qui a fait chercher un problème d'accès alors
            # que le fichier était bien arrivé — en ZIP.
            import requests

            reseau = isinstance(exc, requests.RequestException)
            log.warning(
                "[datatourisme] flux %s (%s)",
                "injoignable" if reseau else "téléchargé mais inexploitable",
                exc,
            )
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
    # Le même inventaire, restreint au RAYON. Sans lui, la ligne « matière
    # disponible » se lit comme une promesse qu'elle ne tient pas : le flux couvre
    # le périmètre du diffuseur, pas le nôtre. Constaté sur le premier événement
    # inspecté — Les Sarmentelles, en Beaujolais, à 250 km de l'épicentre. Annoncer
    # 1542 événements exploitables sur cette base aurait été un chiffre faux, et
    # c'est exactement ce que j'ai fait avant de regarder.
    ignores_rayon: Counter = Counter()
    co_types: Counter = Counter()
    disqualifies: Counter = Counter()
    # Compté à la volée : `nodes` peut être un flot paresseux (mode flux), dont on
    # ne connaît pas la longueur avant de l'avoir parcouru.
    recues = 0
    a_montrer = os.environ.get(DUMP_TYPE_ENV, "").strip()
    montrees = 0
    for node in nodes:
        recues += 1
        if not isinstance(node, dict):
            continue
        if a_montrer and montrees < DUMP_MAX and a_montrer in _type_names(node):
            import json as _json

            montrees += 1
            # Les clés d'abord, à plat : c'est la réponse à « ce type porte-t-il ce
            # champ ? », et elle tient sur une ligne.
            log.info(
                "[datatourisme] fiche « %s » n°%d — champs : %s",
                a_montrer, montrees, ", ".join(sorted(node.keys())),
            )
            log.info(
                "[datatourisme] fiche « %s » n°%d — aperçu :\n%s",
                a_montrer, montrees,
                _json.dumps(_apercu(node), ensure_ascii=False, indent=1)[:DUMP_CHARS],
            )
        place = _to_place(node, sector.id, today)
        if place is None:
            dans_rayon = _dans_le_rayon(node, sector)
            noms = _type_names(node)
            # Une fiche disqualifiée n'est pas « de la matière écartée qu'on
            # pourrait ajouter » : c'est un refus assumé. La compter avec les types
            # inconnus rendrait la ligne d'inventaire trompeuse — un type y monte
            # comme candidat alors qu'il a déjà été jugé.
            exclue = [t for t in noms if t in _TYPES_EXCLUANTS]
            if exclue:
                if dans_rayon:
                    for t in exclue:
                        disqualifies[t] += 1
                continue
            for t in noms:
                if t not in _RACINES_ONTOLOGIE:
                    ignores[t] += 1
                    if dans_rayon:
                        ignores_rayon[t] += 1
        if place is None or (place.external_id and place.external_id in seen):
            continue
        dist = haversine_km(sector.center_lat, sector.center_lon, place.lat, place.lon)
        if travel_minutes(dist) > sector.radius_minutes:
            continue
        if place.external_id:
            seen.add(place.external_id)
        places.append(place)
        # Types CO-PORTÉS par une fiche retenue, en plus de celui qui l'a classée.
        # C'est l'instrument qui manquait pour auditer une catégorie devenue
        # suspecte : `sport-loisir` compte 1440 fiches `SportsAndLeisurePlace` à
        # Villemoirieu, et l'examen des noms y trouve 252 bibliothèques, 118 bars
        # et 68 galeries d'art. Le nom n'est pas un critère défendable ; le type
        # co-porté, si — et il dira s'il existe de quoi écrire une exclusion sur des
        # faits plutôt que sur des chaînes de caractères.
        for t in _type_names(node):
            if t not in _RACINES_ONTOLOGIE and t != place.tags[0].removeprefix("dt:"):
                co_types[(place.category, t)] += 1

    # « nœuds » et non « fiches » : en mode flux, chaque fiche arrive accompagnée
    # d'un second nœud anonyme, ce qui doublait le dénominateur — 2437 sur 47 082
    # laissait croire à une sélectivité deux fois plus sévère qu'en réalité.
    log.info("[datatourisme] %d activités retenues sur %d nœud(s)", len(places), recues)
    if ignores:
        # À lire à chaque changement de flux : c'est l'inventaire de ce que la
        # source propose et qu'on écarte. Un type qui monte haut ici est un
        # candidat à ajouter dans _TYPE_RULES, pas une fatalité.
        log.info(
            "[datatourisme] types reçus NON classés (matière disponible, écartée) "
            "— total flux, dont DANS LE RAYON : %s",
            ", ".join(
                f"{t}×{n} (dont {ignores_rayon.get(t, 0)})" for t, n in ignores.most_common(12)
            ),
        )
    if disqualifies:
        log.info(
            "[datatourisme] fiches DISQUALIFIÉES dans le rayon (type déjà jugé, pas "
            "un candidat) : %s",
            ", ".join(f"{t}×{n}" for t, n in disqualifies.most_common(8)),
        )
    if co_types:
        # Une ligne par catégorie, et seulement les types co-portés qui pèsent au
        # moins 5 % de ses fiches : au-delà de ce seuil, c'est une population qu'on
        # a rangée sous une étiquette qui ne lui va pas, pas un cas isolé.
        par_cat: Counter = Counter(cat for cat, _ in co_types)
        for cat in sorted(par_cat, key=lambda c: -_compte_categorie(places, c)):
            total = _compte_categorie(places, cat)
            gros = [
                (t, n) for (c, t), n in co_types.most_common() if c == cat and n >= 0.05 * total
            ]
            if gros:
                log.info(
                    "[datatourisme] %s (%d fiches) — types co-portés : %s",
                    cat, total, ", ".join(f"{t}×{n}" for t, n in gros[:8]),
                )
    if recues and not places:
        # Des fiches reçues mais aucune reconnue : c'est le symptôme d'un
        # mapping de champs à corriger (l'ontologie est riche et les
        # producteurs la remplissent inégalement), pas d'un territoire vide.
        log.warning(
            "[datatourisme] %d fiches reçues, AUCUNE exploitable — vérifier le mapping "
            "des types (@type) et des coordonnées (isLocatedAt/schema:geo) contre un "
            "échantillon réel du flux",
            recues,
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
