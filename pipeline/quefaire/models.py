"""Schéma commun des données QueFaire.

Tout fetcher, quel que soit le type de source (RSS, iCal, OpenAgenda, HTML+LLM),
produit des `Event`. C'est le contrat entre le pipeline et le site.
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
