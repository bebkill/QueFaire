"""Activités permanentes : découverte, fusion et présentation.

Un événement a une date, une activité permanente a des horaires. Le musée du
village ne « passe » pas : il est là toute l'année. D'où un cycle de vie
distinct de celui du crawl —

- **découverte** (`discover`) : une requête Overpass autour de l'épicentre
  ramène musées, monuments, parcs d'attraction, piscines, cinémas, ludothèques,
  marchés… OpenStreetMap est ici la bonne source : libre, structurée, avec le
  site officiel et les horaires d'ouverture dans les tags ;
- **fusion** (`merge`) : la découverte suivante réconcilie par `external_id`
  pour conserver la présentation LLM, la note et la date de première apparition
  — on ne repaie jamais deux fois le même enrichissement ;
- **présentation** (`present`) : à la PREMIÈRE découverte seulement, un LLM
  écrit une phrase qui donne envie et tranche le caractère « insolite ».

Cadence : hebdomadaire (`.github/workflows/places.yml`), pas 2×/jour. Ce qui
change d'une semaine à l'autre, ce n'est pas l'existence du musée, ce sont ses
horaires — et le fait qu'il ait fermé, détecté par son absence de la sweep.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from .cache import cache
from .geo import haversine_km, radius_km, travel_minutes
from .models import PLACE_CATEGORIES, Place
from .normalize import fold

log = logging.getLogger("quefaire")

# Instances Overpass, essayées dans l'ordre. L'instance principale est publique
# et gratuite : elle sature régulièrement et répond alors 504 en quelques
# secondes (vécu). Un miroir prend le relais plutôt que de perdre le run.
#
# La requête est identique, mais ce ne sont PAS tout à fait les mêmes données :
# les miroirs ont leur propre latence de réplication OSM. Mesuré le 2026-08-05 —
# `overpass-api.de` 7080 éléments, `overpass.kumi.systems` 6972, soit 1,5 %
# d'écart. Conséquence acceptée en connaissance de cause : le catalogue peut
# varier de deux ou trois fiches d'un run à l'autre sans que la source ait bougé
# (voir `merge`). Le repli vaut ce prix — il a déjà sauvé des runs entiers. Ce qui
# n'était pas acceptable, c'était de ne pas savoir QUI avait répondu.
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
OVERPASS_TIMEOUT = 180

# Sursis accordé à une activité qu'une sweep ne revoit plus, **en jours**. Une
# indisponibilité d'Overpass ou un contributeur OSM qui retouche un objet ne doit
# pas faire disparaître un musée du site.
#
# En jours et non en nombre de sweeps, et ça compte : le cycle nominal est
# hebdomadaire, mais un `workflow_dispatch` peut relancer six fois dans l'heure
# (vécu le 2026-08-05). Un compteur de sweeps aurait évincé en quelques minutes
# les 172 fiches qu'Overpass, tombé ce matin-là, ne livrait plus. Le nom précédent
# — MISSING_SWEEPS_BEFORE_DROP, multiplié par 7 à l'usage — le laissait justement
# croire, au point de me tromper à la relecture.
RETENTION_DAYS = 14

# --- Correspondance tags OpenStreetMap → catégories QueFaire -----------------
# Ordre significatif : la PREMIÈRE règle qui matche gagne. `historic=*` avant
# `tourism=attraction` par exemple, sinon un château taggé les deux finirait en
# « visite » au lieu de « patrimoine ».
_TAG_RULES: list[tuple[str, str, str]] = [
    # (clé OSM, valeurs séparées par |, catégorie QueFaire)
    # `memorial` retiré : ce sont les monuments aux morts de chaque village —
    # mesuré, 56 fiches dont 3 % avec site et 1 distinction. Respectables, mais
    # ce ne sont pas des sorties.
    ("tourism", "museum|gallery", "musee"),
    ("historic", "castle|fort|monument|ruins|archaeological_site|city_gate|tower", "patrimoine"),
    ("tourism", "theme_park|zoo|aquarium", "parc-attraction"),
    ("leisure", "water_park", "parc-aquatique"),
    ("leisure", "swimming_pool|beach_resort", "parc-aquatique"),
    ("natural", "beach", "parc-aquatique"),
    ("amenity", "cinema", "cinema"),
    ("amenity", "theatre|arts_centre", "spectacle"),
    ("amenity", "toy_library", "ludotheque"),
    # `sports_centre` a été RETIRÉ : c'est le fourre-tout d'OSM pour les
    # équipements sportifs municipaux — 112 fiches dont « Gymnase » cinq fois,
    # des complexes de tennis et des salles de remise en forme. Personne ne
    # cherche un gymnase pour un week-end. Les vraies activités de loisir ont
    # leurs propres tags (escape_game, climbing, adventure_park…), et l'offre
    # destinée aux visiteurs vient de DATAtourisme. Perte mesurée : 4 fiches
    # réelles (base ULM, club de canoë, une école de grimpe, un accrobranche)
    # contre 107 équipements retirés — dont l'accrobranche définitivement fermé.
    ("leisure", "amusement_arcade|escape_game|adventure_park|climbing|horse_riding|golf_course|bowling_alley|ice_rink", "sport-loisir"),
    ("amenity", "marketplace", "marche"),
    ("amenity", "public_bath", "bien-etre"),
    ("leisure", "spa", "bien-etre"),
    ("tourism", "farm", "ferme"),
    ("shop", "farm", "ferme"),
    ("tourism", "viewpoint|picnic_site", "nature"),
    ("leisure", "park|garden|nature_reserve", "nature"),
    ("tourism", "artwork", "visite"),
    ("tourism", "attraction", "visite"),
]

# Requête Overpass : un bloc par famille de tags, restreint à un rayon autour de
# l'épicentre. `nwr` couvre nodes, ways et relations (un château est une way, un
# cinéma un node) ; `center` donne un point unique pour les surfaces.
_OVERPASS_SELECTORS = [
    'nwr["tourism"~"^(museum|gallery|theme_park|zoo|aquarium|attraction|artwork|viewpoint|picnic_site|farm)$"]',
    'nwr["historic"~"^(castle|fort|monument|ruins|archaeological_site|city_gate|tower)$"]',
    # `sports_centre` retiré ici AUSSI : inutile de rapatrier 112 objets pour les
    # jeter ensuite, et la requête Overpass allège d'autant (elle sature déjà).
    'nwr["leisure"~"^(water_park|swimming_pool|beach_resort|amusement_arcade|escape_game|adventure_park|climbing|horse_riding|golf_course|bowling_alley|ice_rink|park|garden|nature_reserve|spa)$"]',
    'nwr["amenity"~"^(cinema|theatre|arts_centre|toy_library|marketplace|public_bath)$"]',
    'nwr["natural"="beach"]',
    'nwr["shop"="farm"]',
]

# Objets à écarter : trop nombreux, sans intérêt de sortie, ou privés.
_SKIP_IF = (
    ("access", {"private", "no"}),
    ("leisure", {"pitch", "fitness_station"}),
)

# Un parc de quartier sans nom n'est pas une activité. On exige un nom, et pour
# les catégories très denses (parcs, aires de pique-nique) un signal de qualité.
_NEEDS_SIGNAL = {"nature"}

# L'exigence de signal sur la fiche FUSIONNÉE est désormais GÉNÉRALE — voir
# `has_signal` et `filter_relevant`. Le test tardif reste délibéré : un château
# qu'OSM ne connaît que comme un point survit si DATAtourisme le décrit, c'est la
# fusion qui tranche et pas un fournisseur isolé.


def _category_of(tags: dict) -> str | None:
    return _category_and_tag(tags)[0]


def _category_and_tag(tags: dict) -> tuple[str | None, str | None]:
    """Catégorie ET tag OSM qui a déclenché la règle (traçabilité du classement)."""
    for key, values, category in _TAG_RULES:
        value = tags.get(key)
        if value and re.fullmatch(values, value):
            return category, f"{key}={value}"
    return None, None


def _is_interesting(tags: dict, category: str) -> bool:
    """Filtre le bruit : objets privés, sans nom, ou parcs de quartier anonymes."""
    for key, bad in _SKIP_IF:
        if tags.get(key) in bad:
            return False
    if not (tags.get("name") or "").strip():
        return False
    if category in _NEEDS_SIGNAL:
        # Un espace vert ne devient une « activité » que s'il porte un signe
        # d'intérêt : notoriété (wikipédia), site dédié, ou statut protégé.
        signals = ("wikidata", "wikipedia", "website", "contact:website", "protect_class", "heritage")
        if not any(tags.get(s) for s in signals):
            return False
    return True


def _looks_unusual(tags: dict, category: str) -> bool:
    """Heuristique « hors des sentiers battus », avant confirmation par le LLM.

    Un lieu très référencé (wikipédia/wikidata) ou une enseigne de chaîne
    (`brand`) n'est pas insolite. Une curiosité locale — œuvre d'art de bord de
    route, petit musée associatif, ferme de découverte — l'est souvent.
    """
    if tags.get("brand") or tags.get("wikipedia") or tags.get("wikidata"):
        return False
    return category in {"visite", "musee", "ferme", "patrimoine", "ludotheque"}


def _quality_of(tags: dict) -> list[str]:
    """Signaux de qualité LIBRES lisibles dans les tags OSM.

    Remplace la note d'avis : `heritage:operator=mhs` signale un Monument
    Historique, `heritage` un bien protégé (UNESCO au niveau 1), et une notice
    wikipédia/wikidata atteste d'une notoriété. Aucune de ces informations n'est
    soumise à des conditions d'affichage, contrairement aux notes Google.
    """
    found: list[str] = []
    operator = (tags.get("heritage:operator") or "").lower()
    if "mhs" in operator or tags.get("ref:mhs"):
        found.append("monument-historique")
    if tags.get("heritage") == "1" or "whc" in operator:
        found.append("unesco")
    if tags.get("museum") == "france" or (tags.get("operator:type") or "") == "museum_of_france":
        found.append("musee-de-france")
    if tags.get("garden:type") == "remarkable" or tags.get("jardin_remarquable") == "yes":
        found.append("jardin-remarquable")
    if tags.get("wheelchair") == "yes" and tags.get("tourism"):
        found.append("tourisme-handicap")
    if tags.get("wikipedia") or tags.get("wikidata"):
        found.append("notoriete")
    return found


def _fee_of(tags: dict) -> bool | None:
    fee = (tags.get("fee") or "").lower()
    if fee in {"yes", "true"}:
        return True
    if fee in {"no", "false", "none"}:
        return False
    return None


def _image_of(tags: dict) -> tuple[str | None, str | None, str | None]:
    """Illustration d'un objet OSM → (url, crédit, page de vérification).

    On ne retient que `wikimedia_commons`, qui pointe une médiathèque dont TOUT
    le contenu est sous licence libre, avec une page qui nomme l'auteur : on
    peut afficher la photo et créditer honnêtement.

    Le tag `image` d'OSM est délibérément IGNORÉ. Il contient une URL quelconque
    dont personne n'a vérifié la licence, et le crédit qu'on pourrait en tirer
    serait faux : OpenStreetMap héberge le lien, pas la photo — l'auteur reste
    inconnu. Republier l'image d'un tiers sous une attribution inventée est
    précisément ce que la page de détail s'interdit. Le tag ne rapportait que
    2 photos sur 1608 : le rapport ne se discute pas.
    """
    commons = (tags.get("wikimedia_commons") or "").strip()
    if not commons.startswith("File:"):
        return None, None, None
    # Special:FilePath redimensionne côté Wikimedia : inutile de tirer un
    # original de 12 Mo pour une vignette.
    fichier = quote(commons[len("File:"):].replace(" ", "_"))
    return (
        f"https://commons.wikimedia.org/wiki/Special:FilePath/{fichier}?width=960",
        "Wikimedia Commons",
        f"https://commons.wikimedia.org/wiki/{quote(commons.replace(' ', '_'))}",
    )


def _build_query(lat: float, lon: float, km: float) -> str:
    radius_m = int(km * 1000)
    body = "\n  ".join(f"{sel}(around:{radius_m},{lat},{lon});" for sel in _OVERPASS_SELECTORS)
    return f"[out:json][timeout:{OVERPASS_TIMEOUT}];\n(\n  {body}\n);\nout center tags;"


def _element_to_place(el: dict, sector_id: str, today: str) -> Place | None:
    tags = el.get("tags") or {}
    category, raw_tag = _category_and_tag(tags)
    if not category or not _is_interesting(tags, category):
        return None

    # `center` pour les ways/relations (surfaces), lat/lon direct pour les nodes.
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lon = el.get("lon") or (el.get("center") or {}).get("lon")
    if lat is None or lon is None:
        return None

    url = tags.get("website") or tags.get("contact:website") or tags.get("url")
    if url and not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    street = " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x)
    image_url, image_credit, image_page = _image_of(tags)
    return Place(
        name=tags["name"].strip(),
        category=category,
        source_id="osm",
        sector=sector_id,
        external_id=f"{el.get('type', 'node')}/{el.get('id')}",
        description=(tags.get("description") or "").strip(),
        commune=(tags.get("addr:city") or "").strip() or None,
        address=street or None,
        lat=float(lat),
        lon=float(lon),
        url=url,
        phone=tags.get("phone") or tags.get("contact:phone"),
        opening_hours=tags.get("opening_hours"),
        fee=_fee_of(tags),
        unusual_hint=_looks_unusual(tags, category),
        quality=_quality_of(tags),
        image_url=image_url,
        image_credit=image_credit,
        image_page=image_page,
        tags=[f"osm:{raw_tag}"] if raw_tag else [],
        providers=["osm"],
        first_seen=today,
        last_seen=today,
    )


def _overpass(query: str) -> list[dict]:
    """Interroge Overpass en basculant de miroir en miroir si besoin.

    Les instances publiques saturent : un 504 en quelques secondes est un
    engorgement passager, pas une erreur de requête. On réessaie ailleurs avant
    d'abandonner, plutôt que de perdre la découverte de la semaine.
    """
    import requests

    from .fetchers.base import http_get

    last: Exception | None = None
    for rang, url in enumerate(OVERPASS_URLS):
        try:
            resp = http_get(url, params={"data": query}, timeout=OVERPASS_TIMEOUT + 30)
            elements = resp.json().get("elements", [])
            # QUI a répondu, et non seulement qui a échoué. Les miroirs n'ont pas
            # la même latence de réplication OSM : ils ne rendent donc pas tout à
            # fait les mêmes données, et le fournisseur qui a servi la sweep est
            # une variable du résultat. Elle manquait au log — quatre runs de
            # suite ont publié 2747, 2745, 2745, 2747 activités (un musée et un
            # bain public qui vont et viennent) sans que rien ne permette de
            # rattacher l'écart à son origine.
            log.info(
                "[places] Overpass : %s a répondu (%s), %d éléments",
                url, "instance principale" if rang == 0 else f"miroir {rang}", len(elements),
            )
            return elements
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            last = exc
            if status in (429, 502, 503, 504):
                log.warning("[places] %s saturé (%s) — miroir suivant", url, status)
                continue
            raise
        except Exception as exc:  # réseau, JSON illisible…
            last = exc
            log.warning("[places] %s injoignable (%s) — miroir suivant", url, exc)
    raise RuntimeError(f"toutes les instances Overpass ont échoué — dernière : {last}")


def fetch_osm(sector, limit: int | None = None) -> list[Place]:
    """Interroge Overpass autour de l'épicentre et rend les activités du rayon.

    Le rayon Overpass est calculé depuis `radius_minutes` (geo.radius_km), puis
    chaque résultat est re-filtré au temps de trajet exact : un cercle en km est
    une approximation du disque isochrone, pas l'inverse.
    """
    km = radius_km(sector.radius_minutes)
    query = _build_query(sector.center_lat, sector.center_lon, km)
    log.info(
        "[places] Overpass : rayon %.0f km (%d min) autour de %s",
        km, int(sector.radius_minutes), sector.name,
    )
    elements = _overpass(query)
    log.info("[places] %d objets OSM bruts", len(elements))

    today = date.today().isoformat()
    places: list[Place] = []
    seen: set[str] = set()
    for el in elements:
        place = _element_to_place(el, sector.id, today)
        if place is None or place.external_id in seen:
            continue
        # Re-filtrage au temps de trajet réel (le cercle Overpass est plus large).
        dist = haversine_km(sector.center_lat, sector.center_lon, place.lat, place.lon)
        if travel_minutes(dist) > sector.radius_minutes:
            continue
        seen.add(place.external_id)
        places.append(place)

    places.sort(key=lambda p: fold(p.name))
    if limit:
        places = places[:limit]
    log.info("[places] %d activités retenues dans le rayon", len(places))
    return places


# --- Fusion inter-fournisseurs -----------------------------------------------

# Deux fiches désignent le même lieu si leurs noms concordent et qu'elles sont
# à moins de ce seuil. 400 m : assez large pour absorber l'écart entre le point
# OSM (entrée du bâtiment) et le point DATAtourisme (parfois la mairie qui a
# saisi la fiche), assez serré pour ne pas confondre deux commerces d'un bourg.
SAME_PLACE_KM = 0.4


_MOTS_VIDES = {
    "le", "la", "les", "l", "du", "de", "des", "d",
    "au", "aux", "a", "et", "en", "sur", "the",
}


def _name_key(name: str) -> str:
    """Nom réduit à ce qui identifie le lieu, quelle que soit la base d'origine.

    Trois écarts observés sur des doublons publiés, tous corrigés ici :

    - Les TRAITS D'UNION ne coupaient pas les mots : « Brousse-le-Château »
      restait un seul jeton, donc son « le » échappait aux mots vides et ne
      rejoignait jamais « Brousse ». Même cause pour « Denys-Puech » /
      « Denys Puech » ou « Calmont-d'Olt » / « Calmont d'Olt ».
    - Le SUFFIXE ENTRE PARENTHÈSES : DATAtourisme publie la variante
      groupes d'une même offre sous « … (groupes) ». C'est le même lieu.
    - Les MOTS RÉPÉTÉS : « Château de Brousse-le-Château » donne
      « chateau brousse chateau », que « chateau brousse » ne pouvait égaler.

    Mesuré sur pont-de-salars : 113 doublons rapprochés en plus, dont les 10 sans
    parenthèse relus un par un — tous de vrais doublons. Le filtre reste garanti
    par les deux autres conditions de `dedupe_providers` : même catégorie et
    moins de 400 m.
    """
    base = re.sub(r"\s*\([^)]*\)\s*$", " ", name)
    mots = [w for w in re.split(r"[^0-9a-z]+", fold(base)) if w and w not in _MOTS_VIDES]
    return " ".join(w for i, w in enumerate(mots) if w not in mots[:i])


def _richness(place: Place) -> int:
    """Score de complétude, pour choisir quelle fiche sert de base à la fusion."""
    return (
        bool(place.description) * 3
        + bool(place.url) * 3
        + bool(place.opening_hours) * 2
        + bool(place.quality) * 2
        + bool(place.tldr) * 2
        + bool(place.commune)
        + bool(place.phone)
    )


def dedupe_providers(places: list[Place], phase: str = "sweep") -> list[Place]:
    """Réunit les fiches d'un même lieu venues de fournisseurs différents.

    La plus complète sert de base ; les champs qui lui manquent sont pris chez
    l'autre. On préfère garder l'`external_id` OpenStreetMap quand il existe :
    c'est l'identifiant le plus stable dans le temps, et il fait la clé de
    réconciliation entre deux sweeps (voir `merge`).

    `phase` nomme la passe dans le log : la fonction est appelée deux fois par
    run (sur la sweep, puis sur l'ensemble fusionné) et deux lignes identiques
    aux totaux différents se lisaient comme un effondrement du rapprochement
    (« 111 fiches fusionnées » puis « 1 fiches fusionnées »).
    """
    groups: list[list[Place]] = []
    index: dict[str, list[list[Place]]] = {}

    # Ordre d'entrée FIGÉ. Le rapprochement compare chaque fiche à la TÊTE de
    # groupe, pas à tous ses membres : sur trois lieux voisins en chaîne (A-B et
    # B-C sous le seuil, A-C au-dessus), le résultat dépend de qui arrive en
    # premier. Overpass ne garantit pas l'ordre de ses éléments, d'où une dérive
    # de ±2 fiches entre deux runs à données identiques (2747 puis 2745, un musée
    # et un bain public), et des compteurs d'écartées qui bougeaient sans cause.
    # Un diff de données générées doit signifier « la source a changé », jamais
    # « le fournisseur a répondu dans un autre ordre ».
    places = sorted(
        places,
        key=lambda p: (
            _name_key(p.name),
            p.category,
            round(p.lat or 0, 5),
            round(p.lon or 0, 5),
            p.external_id or "",
        ),
    )

    for place in places:
        key = _name_key(place.name)
        target = None
        for group in index.get(key, []):
            head = group[0]
            if head.category != place.category:
                continue
            if head.lat is None or place.lat is None:
                continue
            if haversine_km(head.lat, head.lon, place.lat, place.lon) <= SAME_PLACE_KM:
                target = group
                break
        if target is None:
            group = [place]
            groups.append(group)
            index.setdefault(key, []).append(group)
        else:
            target.append(place)

    merged: list[Place] = []
    for group in groups:
        if len(group) == 1:
            merged.append(group[0])
            continue
        group.sort(key=_richness, reverse=True)
        base, *rest = group
        for other in rest:
            for attr in ("description", "url", "opening_hours", "phone", "address",
                         "commune", "tldr", "fee"):
                if getattr(base, attr) in (None, "") and getattr(other, attr) not in (None, ""):
                    setattr(base, attr, getattr(other, attr))
            for code in other.quality:
                if code not in base.quality:
                    base.quality.append(code)
            for prov in other.providers:
                if prov not in base.providers:
                    base.providers.append(prov)
            # L'identifiant OSM est le plus stable : il prime pour la
            # réconciliation d'une sweep à l'autre.
            if other.source_id == "osm" and base.source_id != "osm":
                base.external_id = other.external_id
                base.source_id = "osm"
            base.unusual = base.unusual or other.unusual
            base.unusual_hint = base.unusual_hint or other.unusual_hint
        base.id = base.compute_id()  # l'external_id a pu changer
        merged.append(base)

    dropped = len(places) - len(merged)
    if dropped:
        log.info("[places] %d fiches fusionnées entre fournisseurs (%s)", dropped, phase)
    # Ordre de SORTIE aligné sur celui de `merge` : `fold(name)`, et non l'ordre de
    # travail interne (clé de rapprochement, mots vides retirés). Sans ça, figer
    # l'ordre d'entrée a réécrit `places.json` en entier — 37 290 lignes changées
    # pour +2 fiches — et un diff de cette taille ne se relit pas, donc ne se
    # vérifie plus. L'ordre de sortie est un CONTRAT avec le lecteur du dépôt, il
    # ne doit pas suivre les besoins de l'algorithme.
    merged.sort(key=lambda p: fold(p.name))
    return merged


# --- Persistance et fusion ---------------------------------------------------

def store_path(sector_id: str, out: Path) -> Path:
    return out / "cities" / sector_id / "places.json"


def load(sector_id: str, out: Path) -> list[Place]:
    """Relit les activités déjà publiées (pour conserver leur enrichissement)."""
    path = store_path(sector_id, out)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        log.warning("[places] %s illisible — on repart de zéro", path)
        return []
    known = {f.name for f in Place.__dataclass_fields__.values()}
    return [Place(**{k: v for k, v in item.items() if k in known}) for item in raw]


def has_signal(place: Place) -> bool:
    """Au moins une source a-t-elle jugé ce lieu digne d'être documenté ?

    Description, site officiel, horaires, distinction, photo, ou présence chez
    deux fournisseurs : chacun atteste que quelqu'un s'est donné la peine.

    Le `tldr` n'en fait PAS partie — il est dérivé de ce qu'on a déjà, il ne
    prouve rien de plus, et l'inclure rendrait le filtre circulaire.
    """
    return bool(
        (place.description or "").strip()
        or place.url
        or place.opening_hours
        or place.quality
        or place.image_url
        or len(place.providers or []) > 1
    )


def filter_relevant(places: list[Place]) -> list[Place]:
    """Écarte les fiches muettes, dans TOUTES les catégories.

    Une fiche sans description, sans site, sans horaires, sans photo et sans
    distinction n'est pas exploitable par un visiteur : la tuile ne dit pas ce
    qu'on peut y faire et le lien ne mène nulle part. La publier est au mieux
    inutile, au pire trompeur — « Piscine », « Salle des Tilleuls » ou
    « Marché aux veaux » ne sont pas des sorties.

    La règle a d'abord été posée par catégorie (`patrimoine`), puis par tag
    (`tourism=artwork`), avant d'être généralisée : chaque resserrement partiel
    laissait passer le même bruit ailleurs — halles municipales en `spectacle`,
    piscines sans nom en `parc-aquatique`, galeries vides en `musee`.

    RIEN N'EST PERDU DÉFINITIVEMENT : le filtre s'applique à la sweep fraîche à
    chaque passage, donc une fiche que sa source enrichit plus tard réapparaît
    d'elle-même, sans intervention. C'est ce qui rend l'exigence tenable.

    Appelé AVANT `present()` : inutile de payer une présentation LLM pour une
    fiche qu'on ne publiera pas.
    """
    kept: list[Place] = []
    dropped: Counter = Counter()
    for place in places:
        if not has_signal(place):
            dropped[place.category] += 1
            continue
        kept.append(place)
    if dropped:
        log.info(
            "[places] %d fiches écartées : aucun signe d'intérêt (%s)",
            sum(dropped.values()),
            ", ".join(f"{cat} ×{n}" for cat, n in dropped.most_common()),
        )
    return kept


def _tag_still_mapped(tag: str) -> bool:
    """Ce tag de provenance donne-t-il ENCORE une catégorie avec les règles du jour ?

    `osm:historic=memorial`, `dt:SportsAndLeisurePlace`… : on rejoue la règle qui
    avait classé la fiche. Si elle ne classe plus rien, c'est qu'on a resserré
    volontairement. Un préfixe inconnu (fournisseur ajouté plus tard) renvoie
    True : dans le doute on garde, le sursis d'absence fera le tri.
    """
    if tag.startswith("osm:"):
        key, _, value = tag[4:].partition("=")
        return _category_of({key: value}) is not None
    if tag.startswith("dt:"):
        from . import datatourisme

        return datatourisme._category_of([tag[3:]]) is not None
    return True


def merge(
    previous: list[Place],
    found: list[Place],
    today: str | None = None,
    refuses: set[str] | None = None,
) -> list[Place]:
    """Réconcilie une nouvelle sweep avec l'existant.

    Règle : OSM fait autorité sur les faits (nom, horaires, site, position),
    l'existant fait autorité sur l'enrichissement (présentation LLM, note,
    date de découverte). Une activité absente de la sweep n'est pas supprimée
    tout de suite — elle est conservée `RETENTION_DAYS` jours, le temps de
    distinguer une fermeture d'un aléa Overpass.

    `refuses` porte les identifiants que la sweep a VUS et refusés. Un refus n'est
    pas une absence, et la distinction n'est pas théorique : l'exclusion des
    bibliothèques et des bars à vin n'a rien changé au catalogue de Villemoirieu
    au run suivant — 3775 activités avant, 3775 après, `dt:SportsAndLeisurePlace`
    toujours à 1440. Les fiches disparaissaient de la sweep, et la rétention les
    reprenait aussitôt pour quatorze jours.

    `_tag_still_mapped()` ne pouvait pas non plus les rattraper : il rejoue la
    règle sur le SEUL tag de provenance, alors que la décision d'origine voyait
    tous les types de la fiche. Un rejeu qui dispose de moins d'information que la
    décision ne peut pas la reproduire — d'où ce chemin explicite pour toute
    exclusion qui dépend de plusieurs types à la fois.

    LIMITE ASSUMÉE de « OSM fait autorité sur les faits » : la règle suppose que
    la sweep est toujours plus fraîche que ce qui est stocké. Un miroir en retard
    de réplication (voir `OVERPASS_URLS`) sert une révision plus ancienne, et les
    faits qu'elle ne porte pas sont donc effacés — un musée qui perd son site web
    perd son signal, donc sa publication. Mesuré le 2026-08-05 : deux fiches
    sorties du catalogue (2747 → 2745) alors qu'elles étaient bien dans la sweep,
    revenues au run suivant.

    La rétention ne protège pas de ça : elle couvre l'ABSENCE d'une fiche, pas son
    appauvrissement. Écart accepté (0,07 % du catalogue, autocorrigé), et rendu
    ATTRIBUABLE par le log qui nomme l'instance ayant répondu. Le correctif de fond
    demanderait `out center tags meta` et une comparaison de `version` par objet.
    """
    today = today or date.today().isoformat()
    prev_by_id = {p.external_id: p for p in previous if p.external_id}
    merged: list[Place] = []

    for place in found:
        old = prev_by_id.pop(place.external_id, None)
        if old:
            # Faits rafraîchis par OSM, enrichissement repris de l'existant.
            place.first_seen = old.first_seen or place.first_seen
            # La phrase et son EMPREINTE voyagent ensemble, toujours. Séparées, la
            # fiche fraîche arrive avec une phrase sans provenance : `present()` la
            # déclare périmée et la remet dans la file — les 1466 phrases de
            # Villemoirieu repassaient à CHAQUE run. Le cache masquait le coût (il
            # les restituait à l'identique, d'où un run de 1 min 32 et un diff de
            # 364 lignes), mais un cache perdu aurait signifié 4000 appels LLM par
            # passage, sans que rien ne le signale.
            place.tldr = old.tldr
            place.tldr_key = old.tldr_key
            place.rating = old.rating
            place.rating_count = old.rating_count
            place.rating_source = old.rating_source
            place.rating_url = old.rating_url
            # Union : la provenance fraîche prime, l'ancienne est conservée.
            for tag in old.tags:
                if tag not in place.tags:
                    place.tags.append(tag)
            # Union des labels et des fournisseurs : si le flux DATAtourisme est
            # indisponible ce jour-là, ses labels ne doivent pas disparaître de
            # la fiche pour autant.
            for code in old.quality:
                if code not in place.quality:
                    place.quality.append(code)
            for prov in old.providers:
                if prov not in place.providers:
                    place.providers.append(prov)
            # L'illustration suit la même règle que les labels : la fraîche
            # prime, mais l'absence n'efface pas — un fournisseur muet ce
            # jour-là ne doit pas dépouiller la fiche de sa photo. Les trois
            # champs bougent ensemble : une URL sans son crédit serait une
            # photo publiée sans son auteur.
            if not place.image_url and old.image_url:
                place.image_url = old.image_url
                place.image_credit = old.image_credit
                place.image_page = old.image_page
            # `unusual` n'existe que si le LLM l'a tranché : on le reprend tel
            # quel de l'existant, la nouvelle sweep n'en sait rien.
            place.unusual = old.unusual
            place.unusual_hint = place.unusual_hint or old.unusual_hint
        place.last_seen = today
        merged.append(place)

    # Les rescapées : vues avant, absentes aujourd'hui.
    excluded = 0
    refusees = 0
    for old in prev_by_id.values():
        # Refusée par la sweep du jour : ce n'est pas une absence, le sursis ne
        # s'applique pas. Testé AVANT la provenance, parce que le tag de la fiche
        # reste parfaitement valide — c'est un autre de ses types qui la disqualifie.
        if refuses and old.external_id in refuses:
            refusees += 1
            continue
        # Absente parce qu'on l'exclut DÉLIBÉRÉMENT, ou parce que le fournisseur
        # a hoqueté ? La provenance du classement tranche. Le sursis de deux
        # sweeps existe pour encaisser une panne, pas pour maintenir en vie ce
        # qu'une règle vient d'écarter — sinon un resserrement mettrait quinze
        # jours à produire son effet.
        if old.tags and not any(_tag_still_mapped(t) for t in old.tags):
            excluded += 1
            continue
        missing_days = _days_since(old.last_seen, today)
        if missing_days is not None and missing_days > RETENTION_DAYS:
            log.info("[places] « %s » retirée : absente depuis %d jours", old.name, missing_days)
            continue
        merged.append(old)
    if excluded:
        log.info("[places] %d fiches retirées : leur type n'est plus retenu", excluded)
    if refusees:
        log.info(
            "[places] %d fiches retirées : refusées par la sweep du jour "
            "(présentes à la source, disqualifiées — pas de sursis)", refusees,
        )

    merged.sort(key=lambda p: fold(p.name))
    return merged


def _days_since(iso: str | None, today: str) -> int | None:
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso).date()
        now = datetime.fromisoformat(today).date()
    except ValueError:
        return None
    return (now - then).days


def needs_recheck(place: Place, today: str | None = None, max_age_days: int = 30) -> bool:
    """Une activité déjà connue mérite-t-elle une revérification de ses horaires ?

    Sert au mode `--quick` : on ne rappelle ni le LLM ni l'API de notes pour
    des activités revues il y a trois jours.
    """
    today = today or date.today().isoformat()
    age = _days_since(place.last_seen, today)
    return age is None or age >= max_age_days


def save(places: list[Place], sector_id: str, out: Path) -> Path:
    path = store_path(sector_id, out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([p.to_dict() for p in places], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return path


# --- Présentation LLM (première découverte uniquement) -----------------------

PRESENT_PROMPT = """Voici des activités permanentes (musées, monuments, parcs, curiosités) d'un
territoire rural ou périurbain français. Pour CHACUNE, écris une phrase courte
qui donne envie d'y aller : ce qu'on y voit ou y fait concrètement, et ce qui la
rend intéressante. Ton factuel et engageant, pas publicitaire. 25 mots maximum.

Indique aussi si l'activité est INSOLITE : méconnue, atypique, hors des sentiers
battus (une collection excentrique, un site oublié, un savoir-faire rare). Un
cinéma de centre-ville, une piscine municipale ou un monument célèbre ne le sont
PAS.

N'invente RIEN. Si tu ne connais pas l'activité et que son nom ne suffit pas à
dire ce que c'est, réponds une chaîne vide pour sa phrase.

Réponds UNIQUEMENT en JSON : {{"<id>": {{"phrase": "...", "insolite": true|false}}, ...}}

ACTIVITÉS :
{items}
"""

BATCH_SIZE = 20

# Plafond de présentations NOUVELLES par run. C'est le SEUL limiteur : il ne
# reste plus de « budget total », le site affichant désormais tout le catalogue
# (le plafond de 300 tuiles a disparu avec le rendu côté navigateur).
#
# Sa valeur ne dit donc plus « combien de fiches méritent une phrase » mais
# « au-delà de combien s'agit-il forcément d'une anomalie ». Au premier run réel,
# un défaut de filtrage avait envoyé 7197 activités à la présentation : 21 minutes
# de LLM sur des données à jeter.
#
# 3000 était calé sur « 2232 à Pont-de-Salars, le plus dense mesuré » — et ce
# « plus dense » n'a tenu que le temps de traiter l'autre épicentre : Villemoirieu
# en compte 3775, dont 3373 à présenter, et le plafond a mordu pour de bon sur un
# catalogue parfaitement légitime. Un secteur n'avait jamais été mesuré, et une
# borne calée sur le seul territoire connu devient fausse dès qu'on en ajoute un.
#
# 8000 garde donc une marge sur le double du plus gros connu, sans rien perdre de
# sa fonction : une dérive de filtrage se compte en dizaines de milliers de fiches,
# pas en milliers.
#
# La file reste ordonnée par `display_order_key` : si le plafond mord malgré tout,
# ce sont les fiches les mieux documentées qui passent d'abord, et le reste suit au
# run suivant puisque le cache est persistant.
PRESENT_MAX_PER_RUN = 8000


def display_order_key(place: Place):
    """Clé de tri d'affichage — **miroir exact** de `rankPlaces()` (places.js).

    Les départages doivent être identiques des deux côtés, sinon le pipeline
    présente un ensemble légèrement différent de celui que le site affiche.
    """
    return (
        -display_score(place),
        not place.unusual,
        not place.tldr,
        fold(place.name),
    )


def display_score(place: Place) -> int:
    """Intérêt d'une activité — **miroir de `placeScore()`** (site/src/lib/places.js).

    Sert à ordonner la file de présentation sur le même critère que l'affichage :
    sans cela, on paie des appels LLM pour des fiches qui n'apparaîtront jamais.

    Le score est INTRINSÈQUE : ni `tldr` ni `unusual` n'y entrent. Les inclure
    rendait le classement circulaire — présenter une fiche la faisait monter et
    en délogeait une autre, restée sans phrase. Mesuré au run #6 : 385 phrases
    payées jamais affichées, 74 activités affichées sans phrase. Un score stable
    fige l'ensemble à remplir, que la file couvre en un passage.

    Toute modification ici doit être reportée dans places.js, et inversement.
    """
    from .models import NOTABLE_LABELS

    quality = place.quality or []
    return (
        (100 if set(quality) & NOTABLE_LABELS else 0)
        + (15 if place.opening_hours else 0)
        + (10 if place.url else 0)
        + (5 if place.description else 0)
        + (5 if "notoriete" in quality else 0)
        + (5 if len(place.providers or []) > 1 else 0)
    )


def present(places: list[Place]) -> list[Place]:
    """Remplit `tldr` (et affine `unusual`) pour les activités jamais présentées.

    Mise en cache par contenu, avec une portée à connaître : le cache est élagué
    aux clés VUES pendant le run (`cache.save`), et une fiche qui porte déjà sa
    phrase n'est pas interrogée. Sa clé disparaît donc du cache au passage
    suivant. Autrement dit c'est `places.json` qui mémorise les présentations ;
    le cache ne sert qu'à ne pas repayer, d'un run à l'autre, les fiches restées
    SANS phrase (LLM muet, plafond atteint, quota mort en cours de route) — 419
    fiches dans ce cas au dernier run, mémorisées comme « rien d'exploitable ».
    Supprimer `places.json` recoûte donc tout le catalogue en appels LLM.

    Sans LLM disponible, l'étape est sautée proprement — les fiches s'affichent
    sans phrase.

    DEUX RÈGLES issues d'une mesure du 2026-08-05 :

    1. **Une phrase dont la matière a changé est réécrite.** `tldr_key` mémorise
       l'empreinte des entrées qui l'ont produite ; si elle ne correspond plus, la
       fiche repasse dans la file. Sans ça, 3357 phrases écrites sur une URI (le
       défaut `_description_of`) restaient gelées à vie, puisque cette fonction ne
       regardait que les fiches SANS phrase. Une phrase sans provenance vérifiable
       est un texte non sourcé publié sous notre nom : les fiches dont l'empreinte
       est absente sont donc traitées comme suspectes, et repassent une fois.
    2. **Pas de matière, pas de phrase.** Une fiche sans description n'est plus
       soumise au modèle : il n'aurait que le nom, la catégorie et la commune, et
       c'est précisément ce régime qui a produit « Explorez une bachasse,
       embarcation traditionnelle des Dombes » pour un lieu dont la description
       réelle parle d'une rivière. Le modèle ne devine pas, il affirme.
    """
    from .clarify import _extract_json
    from .llm import clarify_chain

    def empreinte(p: Place) -> str:
        return cache.key("place", p.name, p.category, p.commune or "", p.description[:200])

    # Phrases dont la matière ne correspond plus (ou dont on ne peut rien prouver).
    perimees = 0
    for p in places:
        if p.tldr and p.tldr_key != empreinte(p):
            p.tldr = None
            perimees += 1
    if perimees:
        log.info(
            "[places] %d phrases à revoir : leur matière a changé depuis leur écriture",
            perimees,
        )

    # « Pas de matière, pas de phrase » — et on le DIT, sinon l'écart entre fiches
    # publiées et fiches présentées passerait pour un plafond ou un quota.
    todo = [p for p in places if not p.tldr and p.description]
    sans_matiere = sum(1 for p in places if not p.tldr and not p.description)
    if sans_matiere:
        log.info(
            "[places] %d fiches laissées sans phrase : aucune description à résumer",
            sans_matiere,
        )
    if not todo:
        return places

    misses: list[tuple[Place, str]] = []
    for p in todo:
        ckey = empreinte(p)
        val = cache.get(ckey)
        if val is None:
            misses.append((p, ckey))
        elif val:
            payload = json.loads(val) if val.startswith("{") else {"phrase": val}
            p.tldr = payload.get("phrase") or None
            p.tldr_key = ckey if p.tldr else None
            if "insolite" in payload:
                p.unusual = bool(payload["insolite"])

    if len(misses) > PRESENT_MAX_PER_RUN:
        # File ordonnée sur le score d'AFFICHAGE : on présente d'abord ce que le
        # visiteur verra, et ces fiches-là sont aussi les mieux documentées, donc
        # celles pour lesquelles le LLM a de la matière. À présomption d'insolite
        # égale, une fiche sans description ne produira qu'une réponse vide —
        # elle attend son tour derrière les autres.
        misses.sort(key=lambda pair: display_order_key(pair[0]))
        log.warning(
            "[places] %d activités à présenter, plafonné à %d pour ce run "
            "(les mieux documentées d'abord ; le reste suivra, le cache est persistant)",
            len(misses), PRESENT_MAX_PER_RUN,
        )
        misses = misses[:PRESENT_MAX_PER_RUN]

    chain = clarify_chain()
    if misses and chain.available() and chain.healthy():
        for i in range(0, len(misses), BATCH_SIZE):
            batch = misses[i : i + BATCH_SIZE]
            items = "\n".join(
                f"- id: {j}\n  nom: {p.name}\n  type: {PLACE_CATEGORIES[p.category]}"
                f"\n  commune: {p.commune or '?'}"
                + (f"\n  description: {p.description[:200]}" if p.description else "")
                for j, (p, _) in enumerate(batch)
            )
            try:
                result = chain.run(PRESENT_PROMPT.format(items=items))
                mapping = _extract_json(result.output)
            except RuntimeError as exc:
                log.warning("[places] présentation interrompue : %s", exc)
                break
            except Exception as exc:
                log.error("[places] lot %d : %s", i // BATCH_SIZE, exc)
                continue
            for j, (p, ckey) in enumerate(batch):
                entry = mapping.get(str(j)) or mapping.get(j) or {}
                if isinstance(entry, str):
                    entry = {"phrase": entry}
                phrase = (entry.get("phrase") or "").strip() if isinstance(entry, dict) else ""
                unusual = bool(entry.get("insolite")) if isinstance(entry, dict) else False
                if phrase and 10 < len(phrase) < 300:
                    p.tldr = phrase
                    p.tldr_key = ckey  # provenance : de quoi cette phrase est tirée
                    p.unusual = unusual
                    cache.put(ckey, json.dumps({"phrase": phrase, "insolite": unusual}, ensure_ascii=False))
                else:
                    cache.put(ckey, "")  # rien d'exploitable : mémorisé
    elif misses:
        log.info("[places] %d activités non présentées (pas de budget LLM)", len(misses))

    done = sum(1 for p in places if p.tldr)
    log.info("[places] %d fiches présentées sur %d activités", done, len(places))
    return places
