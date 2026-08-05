"""Schéma commun des données QueFaire.

Deux objets, deux temporalités :

- `Event` — un événement **daté** (concert, marché, expo temporaire). Produit par
  les fetchers du crawl (RSS, iCal, OpenAgenda, HTML+LLM), rafraîchi 2×/jour.
- `Place` — une activité **permanente** (musée, monument, parc d'attraction,
  cinéma, ludothèque…). Produite par la découverte OpenStreetMap, rafraîchie
  bien plus rarement : ce qui bouge, ce n'est pas l'existence du musée mais ses
  horaires. Voir `places.py`.

C'est le contrat entre le pipeline et le site.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

CATEGORIES = {
    "concert": "Concert & musique",
    "spectacle": "Spectacle & théâtre",
    "festival": "Festival",
    "expo": "Exposition & culture",
    "sport": "Sport",
    "nature": "Nature & randonnée",
    "atelier": "Atelier & stage",
    "marche": "Marché & terroir",
    "patrimoine": "Patrimoine & visites",
    "jeunesse": "Jeunesse & famille",
    "cinema": "Cinéma",
    "conference": "Conférence & rencontre",
    "fete": "Fête locale",
    "autre": "Autre",
}

AUDIENCES = ("famille", "enfants", "ados", "adultes", "seniors", "tous")

# Tags de provenance qui désignent une activité RÉSERVÉE AUX MAJEURS. Elle garde
# sa place au catalogue — un casino est une sortie — mais ne doit jamais remonter
# sous « En famille ». Le filtre existait déjà côté site ; il était inopérant sur
# les activités permanentes, dont le public était codé en dur à « tous ».
#
# La règle porte sur le TAG et non sur la catégorie : « Ludothèque & jeux »
# contient à la fois une ludothèque municipale et un casino, et seul le tag les
# distingue.
ADULTES_SEULEMENT = frozenset({
    "dt:Casino",
    "osm:amenity=casino",
    "osm:amenity=nightclub",
    "osm:leisure=adult_gaming_centre",
})

# Catégories des activités PERMANENTES. Volontairement distinctes de CATEGORIES
# (événements) : « marché » désigne ici le marché hebdomadaire qui a lieu tous
# les mardis, pas la brocante du 12 avril. Les deux jeux se recoupent (cinéma,
# patrimoine) mais ne se confondent pas.
PLACE_CATEGORIES = {
    "musee": "Musée",
    "patrimoine": "Monument & patrimoine",
    "parc-attraction": "Parc d'attraction & zoo",
    "parc-aquatique": "Parc aquatique & baignade",
    "nature": "Nature & plein air",
    "cinema": "Cinéma",
    "spectacle": "Théâtre & salle de spectacle",
    "ludotheque": "Ludothèque & jeux",
    "marche": "Marché",
    "visite": "Visite & curiosité",
    "sport-loisir": "Sport & loisirs",
    # Prestation à réserver (grimpe d'arbres, balade guidée, escape game) plutôt
    # qu'un lieu où l'on se rend librement. Distinguée parce qu'elle répond à
    # « que faire ? » sans être une adresse à visiter — et parce qu'à 597 fiches
    # elle noyait « sport & loisirs » (806) à elle seule.
    "prestation": "Activité encadrée",
    "ferme": "Ferme & artisanat",
    "bien-etre": "Thermes & bien-être",
    "autre": "Autre activité",
}

# Signaux de qualité LIBRES, en remplacement de la note d'avis : distinctions
# officielles et marques de notoriété, toutes issues de données ouvertes (tags
# OpenStreetMap, labels DATAtourisme). Contrairement aux notes Google/
# TripAdvisor, leur affichage n'est soumis à aucune condition contractuelle.
QUALITY_LABELS = {
    "monument-historique": "Monument Historique",
    "musee-de-france": "Musée de France",
    "unesco": "Patrimoine mondial UNESCO",
    "jardin-remarquable": "Jardin remarquable",
    "maisons-des-illustres": "Maison des Illustres",
    "art-et-histoire": "Ville et Pays d'art et d'histoire",
    "qualite-tourisme": "Qualité Tourisme",
    "tourisme-handicap": "Tourisme & Handicap",
    "notoriete": "Notice Wikipédia",
}

# Sous-ensemble qui vaut « valeur sûre » : une distinction officielle, décernée
# par un tiers. La simple notice Wikipédia n'en fait PAS partie — elle atteste
# d'une notoriété, pas d'une qualité d'accueil.
NOTABLE_LABELS = frozenset(QUALITY_LABELS) - {"notoriete"}


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return text[:80] or "evenement"


@dataclass
class Source:
    """Une source d'information référencée dans sources/<secteur>.yaml."""

    id: str
    name: str
    type: str  # rss | ical | openagenda | html
    url: str
    commune: Optional[str] = None  # commune par défaut si la source est communale
    category_hint: Optional[str] = None
    enabled: bool = True
    # Pour type=html : sélecteur CSS optionnel pour restreindre la zone à extraire
    scope_selector: Optional[str] = None


@dataclass
class Event:
    title: str
    start: str  # ISO 8601 (date ou datetime)
    source_id: str
    sector: str
    end: Optional[str] = None
    description: str = ""
    # « En clair » : une phrase générée qui dit concrètement de quoi il s'agit,
    # quand le titre est ambigu (ex: « Faites-vous une terrasse » = dîners en
    # terrasse, pas un atelier bricolage). Rempli par clarify.py si LLM dispo.
    tldr: Optional[str] = None
    category: str = "autre"
    tags: list[str] = field(default_factory=list)
    audience: list[str] = field(default_factory=list)
    commune: Optional[str] = None
    address: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    free: Optional[bool] = None
    price_text: Optional[str] = None
    url: Optional[str] = None
    image: Optional[str] = None
    id: str = ""

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            self.category = "autre"
        self.audience = [a for a in self.audience if a in AUDIENCES]
        if not self.id:
            self.id = self.compute_id()

    def compute_id(self) -> str:
        """Identifiant stable : slug + hash court (titre, date, commune)."""
        raw = f"{self.title}|{self.start[:10]}|{self.commune or ''}".lower()
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
        return f"{slugify(self.title)}-{digest}"

    def dedupe_key(self) -> str:
        """Clé de déduplication inter-sources (même événement relayé 2 fois)."""
        title = slugify(self.title)
        return f"{title}|{self.start[:10]}|{slugify(self.commune or '')}"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Place:
    """Une activité permanente : elle n'a pas de date, elle a des horaires.

    `external_id` (« node/1234 » côté OpenStreetMap) est la clé de réconciliation
    entre deux découvertes : c'est lui qui permet de retrouver une activité déjà
    connue pour lui conserver sa présentation LLM, sa note et son `first_seen`
    sans repayer d'appel.
    """

    name: str
    category: str
    source_id: str  # « osm » aujourd'hui ; d'autres fournisseurs plus tard
    sector: str
    external_id: str = ""
    description: str = ""
    # Phrase « donne envie » générée une seule fois à la découverte (places.py).
    tldr: Optional[str] = None
    # Empreinte des ENTRÉES qui ont produit `tldr` (clé de cache de `present()`).
    #
    # Sans elle, une phrase est un texte sans provenance vérifiable : on ne peut
    # pas dire de quoi elle a été tirée, donc pas savoir qu'elle ne correspond
    # plus. Mesuré le 2026-08-05 — 3357 phrases avaient été écrites alors que la
    # description lue était une URI (voir `datatourisme._description_of`), et
    # comme `present()` ne travaille que sur les fiches SANS phrase, elles étaient
    # gelées à vie. L'une affirmait qu'une « bachasse » est une embarcation là où
    # la vraie description parle d'une rivière.
    #
    # Avec l'empreinte, une phrase dont la matière a changé se détecte et se
    # réécrit — y compris pour tout futur correctif d'extraction. Non exportée
    # vers le navigateur (voir la liste `CHAMPS` du site) : c'est de la traçabilité
    # de production, pas une donnée d'affichage.
    tldr_key: Optional[str] = None
    commune: Optional[str] = None
    address: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    url: Optional[str] = None  # site officiel — c'est là qu'on envoie le visiteur
    phone: Optional[str] = None
    opening_hours: Optional[str] = None  # syntaxe OSM brute, affichée telle quelle
    fee: Optional[bool] = None  # True = payant, False = gratuit, None = inconnu
    # Note d'avis (Google ou TripAdvisor) — absente si aucune clé d'API n'est
    # configurée : le champ reste None et le site n'affiche simplement rien.
    rating: Optional[float] = None
    rating_count: Optional[int] = None
    rating_source: Optional[str] = None
    rating_url: Optional[str] = None
    # « Insolite » : activité méconnue ou hors des sentiers battus. N'est vrai
    # QUE si le LLM l'a confirmé — c'est une affirmation affichée au visiteur,
    # elle doit reposer sur un examen de la fiche. L'heuristique seule taguait
    # 23 % du corpus, dont 95 % que le LLM n'avait jamais regardés.
    unusual: bool = False
    # Présomption d'insolite (ni marque, ni notice wikipédia) : sert UNIQUEMENT
    # à faire passer ces fiches en tête de la file de présentation LLM, jamais
    # à afficher quoi que ce soit.
    unusual_hint: bool = False
    # Signaux de qualité LIBRES (codes de QUALITY_LABELS) : Monument Historique,
    # Musée de France, Qualité Tourisme… Ils remplacent la note d'avis, dont
    # l'affichage est contraint par les CGU des fournisseurs — voir ratings.py.
    quality: list[str] = field(default_factory=list)
    # Fournisseurs ayant contribué à cette fiche (« osm », « datatourisme ») :
    # une même activité est souvent connue des deux, on garde la trace.
    providers: list[str] = field(default_factory=list)
    # Illustration. Jamais fabriquée ni devinée : soit un fournisseur en publie
    # une, soit la fiche n'en a pas. `image_credit` et `image_page` existent
    # parce qu'une photo n'est pas une donnée comme les autres — elle a un
    # auteur et une licence propres, distincts de ceux du jeu de données.
    image_url: Optional[str] = None
    image_credit: Optional[str] = None
    image_page: Optional[str] = None  # page où vérifier auteur et licence
    tags: list[str] = field(default_factory=list)
    # Public visé, mêmes valeurs que pour un événement (`AUDIENCES`). « tous » par
    # défaut : une activité permanente s'adresse a priori à tout le monde. Dérivé
    # des tags, jamais saisi — voir `ADULTES_SEULEMENT`.
    audience: list[str] = field(default_factory=lambda: ["tous"])
    first_seen: Optional[str] = None  # date ISO de la première découverte
    last_seen: Optional[str] = None  # date ISO du dernier passage qui l'a revue
    id: str = ""

    def __post_init__(self) -> None:
        if self.category not in PLACE_CATEGORIES:
            self.category = "autre"
        # Le public se DÉDUIT des tags de provenance, il n'est pas stocké par le
        # fournisseur. Le recalculer ici plutôt que dans chaque `_to_place` garantit
        # qu'aucun des deux fournisseurs ne peut l'oublier — et le recalcul
        # s'applique aussi aux fiches relues depuis places.json, donc la règle
        # prend effet sans attendre une nouvelle découverte.
        if any(t in ADULTES_SEULEMENT for t in (self.tags or [])):
            self.audience = ["adultes"]
        self.audience = [a for a in self.audience if a in AUDIENCES] or ["tous"]
        if not self.id:
            self.id = self.compute_id()

    def compute_id(self) -> str:
        """Identifiant stable : slug + hash court de l'identifiant d'origine.

        Basé sur `external_id` (et non sur le nom + la commune) pour qu'un musée
        renommé garde son URL et son historique.

        À défaut d'identifiant de source, le nom seul ne suffit PAS : quatre
        « Point lecture » de quatre communes différentes recevaient le même id,
        donc la même page de détail — et trois visiteurs sur quatre y auraient
        lu les coordonnées d'une autre commune. On complète alors par la
        position, arrondie pour rester stable d'un run à l'autre.
        """
        ancre = self.external_id
        if not ancre:
            lieu = (
                f"{self.lat:.5f},{self.lon:.5f}"
                if self.lat is not None and self.lon is not None
                else ""
            )
            ancre = f"{self.name}|{lieu}"
        raw = f"{ancre}|{self.sector}".lower()
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
        return f"{slugify(self.name)}-{digest}"

    def to_dict(self) -> dict:
        return asdict(self)


def parse_when(value: str) -> Optional[datetime]:
    """Parse tolérant de dates rencontrées dans les flux."""
    if not value:
        return None
    value = value.strip()
    fmts = (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d",
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    )
    for fmt in fmts:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
