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
from datetime import date, datetime, timedelta
from pathlib import Path

from .cache import cache
from .geo import haversine_km, radius_km, travel_minutes
from .models import PLACE_CATEGORIES, Place
from .normalize import fold

log = logging.getLogger("quefaire")

# Instances Overpass, essayées dans l'ordre. L'instance principale est publique
# et gratuite : elle sature régulièrement et répond alors 504 en quelques
# secondes (vécu). Un miroir prend le relais plutôt que de perdre le run — la
# requête est identique, ce sont les mêmes données OSM.
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
OVERPASS_TIMEOUT = 180

# Nombre de sweeps consécutifs sans revoir une activité avant de la retirer.
# Deux plutôt qu'un : une indisponibilité d'Overpass ou un contributeur OSM qui
# retouche un objet ne doit pas faire disparaître un musée du site.
MISSING_SWEEPS_BEFORE_DROP = 2

# --- Correspondance tags OpenStreetMap → catégories QueFaire -----------------
# Ordre significatif : la PREMIÈRE règle qui matche gagne. `historic=*` avant
# `tourism=attraction` par exemple, sinon un château taggé les deux finirait en
# « visite » au lieu de « patrimoine ».
_TAG_RULES: list[tuple[str, str, str]] = [
    # (clé OSM, valeurs séparées par |, catégorie QueFaire)
    ("tourism", "museum|gallery", "musee"),
    ("historic", "castle|fort|monument|memorial|ruins|archaeological_site|city_gate|tower", "patrimoine"),
    ("tourism", "theme_park|zoo|aquarium", "parc-attraction"),
    ("leisure", "water_park", "parc-aquatique"),
    ("leisure", "swimming_pool|beach_resort", "parc-aquatique"),
    ("natural", "beach", "parc-aquatique"),
    ("amenity", "cinema", "cinema"),
    ("amenity", "theatre|arts_centre", "spectacle"),
    ("amenity", "toy_library", "ludotheque"),
    ("leisure", "amusement_arcade|escape_game|adventure_park|sports_centre|climbing|horse_riding|golf_course|bowling_alley|ice_rink", "sport-loisir"),
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
    'nwr["historic"~"^(castle|fort|monument|memorial|ruins|archaeological_site|city_gate|tower)$"]',
    'nwr["leisure"~"^(water_park|swimming_pool|beach_resort|amusement_arcade|escape_game|adventure_park|sports_centre|climbing|horse_riding|golf_course|bowling_alley|ice_rink|park|garden|nature_reserve|spa)$"]',
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


def _category_of(tags: dict) -> str | None:
    for key, values, category in _TAG_RULES:
        value = tags.get(key)
        if value and re.fullmatch(values, value):
            return category
    return None


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


def _build_query(lat: float, lon: float, km: float) -> str:
    radius_m = int(km * 1000)
    body = "\n  ".join(f"{sel}(around:{radius_m},{lat},{lon});" for sel in _OVERPASS_SELECTORS)
    return f"[out:json][timeout:{OVERPASS_TIMEOUT}];\n(\n  {body}\n);\nout center tags;"


def _element_to_place(el: dict, sector_id: str, today: str) -> Place | None:
    tags = el.get("tags") or {}
    category = _category_of(tags)
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
        unusual=_looks_unusual(tags, category),
        quality=_quality_of(tags),
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
    for url in OVERPASS_URLS:
        try:
            resp = http_get(url, params={"data": query}, timeout=OVERPASS_TIMEOUT + 30)
            return resp.json().get("elements", [])
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


def _name_key(name: str) -> str:
    """Nom replié et débarrassé des mots vides qui varient d'une base à l'autre
    (« Musée du Rouergue » / « Le musée du Rouergue »)."""
    words = [w for w in fold(name).split() if w not in {"le", "la", "les", "l", "du", "de", "des", "d"}]
    return " ".join(words)


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


def dedupe_providers(places: list[Place]) -> list[Place]:
    """Réunit les fiches d'un même lieu venues de fournisseurs différents.

    La plus complète sert de base ; les champs qui lui manquent sont pris chez
    l'autre. On préfère garder l'`external_id` OpenStreetMap quand il existe :
    c'est l'identifiant le plus stable dans le temps, et il fait la clé de
    réconciliation entre deux sweeps (voir `merge`).
    """
    groups: list[list[Place]] = []
    index: dict[str, list[list[Place]]] = {}

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
        base.id = base.compute_id()  # l'external_id a pu changer
        merged.append(base)

    dropped = len(places) - len(merged)
    if dropped:
        log.info("[places] %d fiches fusionnées entre fournisseurs", dropped)
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


def merge(previous: list[Place], found: list[Place], today: str | None = None) -> list[Place]:
    """Réconcilie une nouvelle sweep avec l'existant.

    Règle : OSM fait autorité sur les faits (nom, horaires, site, position),
    l'existant fait autorité sur l'enrichissement (présentation LLM, note,
    date de découverte). Une activité absente de la sweep n'est pas supprimée
    tout de suite — elle est conservée `MISSING_SWEEPS_BEFORE_DROP` fois, le
    temps de distinguer une fermeture d'un aléa Overpass.
    """
    today = today or date.today().isoformat()
    prev_by_id = {p.external_id: p for p in previous if p.external_id}
    merged: list[Place] = []

    for place in found:
        old = prev_by_id.pop(place.external_id, None)
        if old:
            # Faits rafraîchis par OSM, enrichissement repris de l'existant.
            place.first_seen = old.first_seen or place.first_seen
            place.tldr = old.tldr
            place.rating = old.rating
            place.rating_count = old.rating_count
            place.rating_source = old.rating_source
            place.rating_url = old.rating_url
            place.tags = old.tags
            # Union des labels et des fournisseurs : si le flux DATAtourisme est
            # indisponible ce jour-là, ses labels ne doivent pas disparaître de
            # la fiche pour autant.
            for code in old.quality:
                if code not in place.quality:
                    place.quality.append(code)
            for prov in old.providers:
                if prov not in place.providers:
                    place.providers.append(prov)
            # L'insolite tranché par le LLM prime sur l'heuristique, mais une
            # activité jamais présentée garde le verdict heuristique du jour.
            if old.tldr:
                place.unusual = old.unusual
        place.last_seen = today
        merged.append(place)

    # Les rescapées : vues avant, absentes aujourd'hui.
    for old in prev_by_id.values():
        missing_days = _days_since(old.last_seen, today)
        if missing_days is not None and missing_days > 7 * MISSING_SWEEPS_BEFORE_DROP:
            log.info("[places] « %s » retirée : absente depuis %d jours", old.name, missing_days)
            continue
        merged.append(old)

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

# Plafond de présentations NOUVELLES par run. Garde-fou de coût : au premier run
# réel, une anomalie de filtrage a envoyé 7197 activités à la présentation —
# 21 minutes de LLM sur des données à jeter. Le cache étant persistant, ce qui
# n'est pas présenté aujourd'hui le sera au passage suivant : on étale au lieu
# de tout payer d'un coup.
PRESENT_MAX_PER_RUN = 400


def present(places: list[Place]) -> list[Place]:
    """Remplit `tldr` (et affine `unusual`) pour les activités jamais présentées.

    Mise en cache par contenu : une activité déjà présentée n'est jamais
    repayée, même si le fichier de sortie est supprimé. Sans LLM disponible,
    l'étape est sautée proprement — les fiches s'affichent sans phrase.
    """
    from .clarify import _extract_json
    from .llm import clarify_chain

    todo = [p for p in places if not p.tldr]
    if not todo:
        return places

    misses: list[tuple[Place, str]] = []
    for p in todo:
        ckey = cache.key("place", p.name, p.category, p.commune or "", p.description[:200])
        val = cache.get(ckey)
        if val is None:
            misses.append((p, ckey))
        elif val:
            payload = json.loads(val) if val.startswith("{") else {"phrase": val}
            p.tldr = payload.get("phrase") or None
            if "insolite" in payload:
                p.unusual = bool(payload["insolite"])

    if len(misses) > PRESENT_MAX_PER_RUN:
        # Les mieux distinguées d'abord : si on ne peut pas tout présenter
        # aujourd'hui, autant commencer par ce que le visiteur verra en tête.
        from .models import NOTABLE_LABELS

        misses.sort(key=lambda pair: not (set(pair[0].quality) & NOTABLE_LABELS))
        log.warning(
            "[places] %d activités à présenter, plafonné à %d pour ce run "
            "(le reste sera présenté aux passages suivants, le cache est persistant)",
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
                    p.unusual = unusual
                    cache.put(ckey, json.dumps({"phrase": phrase, "insolite": unusual}, ensure_ascii=False))
                else:
                    cache.put(ckey, "")  # rien d'exploitable : mémorisé
    elif misses:
        log.info("[places] %d activités non présentées (pas de budget LLM)", len(misses))

    done = sum(1 for p in places if p.tldr)
    log.info("[places] %d fiches présentées sur %d activités", done, len(places))
    return places
