"""Évaluation d'une source candidate : combien d'événements UNIQUES elle
apporterait, dédupliqués contre le dataset déjà publié.

Brique commune à la découverte automatique et au module « proposer une source »
(un utilisateur colle un lien). Ne modifie rien : produit un rapport à relire.
Les URLs tierces sont récupérées sous garde-fou SSRF (voir quefaire.security) ;
le contenu n'est jamais exécuté, seulement lu par le LLM pour extraction.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .dedupe import dedupe
from .fetchers import base, fetch_source
from .geocode import geocode
from .models import Event, Source
from .normalize import enrich
from .security import validate_public_url

log = logging.getLogger("quefaire")

DEFAULT_EVENTS = (
    Path(__file__).resolve().parent.parent.parent / "site" / "src" / "data" / "events.json"
)


def _detect_type(url: str) -> str:
    u = url.lower().split("?")[0]
    if u.endswith(".ics") or "webcal" in u or "ical" in u:
        return "ical"
    if any(h in u for h in ("/rss", "rss.xml", "atom", "/feed")):
        return "rss"
    return "html"


def existing_keys(events_path: Path | None = None) -> set[str]:
    """Clés de déduplication du dataset publié, pour repérer les vrais nouveaux."""
    path = events_path or DEFAULT_EVENTS
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return set()
    keys: set[str] = set()
    for e in data if isinstance(data, list) else []:
        try:
            keys.add(
                Event(
                    title=e["title"], start=e["start"], commune=e.get("commune"),
                    source_id="", sector="",
                ).dedupe_key()
            )
        except (KeyError, TypeError):
            continue
    return keys


def evaluate_url(
    url: str,
    sector_id: str,
    *,
    source_type: str | None = None,
    commune: str | None = None,
    keys: set[str] | None = None,
) -> dict:
    """Récupère l'URL (garde-fou SSRF), en extrait les événements et compte ceux
    qui ne doublonnent pas le dataset existant. Retourne un rapport sérialisable.

    Lève UnsafeUrlError si l'URL est refusée par les contrôles de sécurité,
    ValueError si le type déduit n'est pas récupérable depuis une URL.
    """
    validate_public_url(url)  # échoue tôt et clairement, avant tout accès réseau
    stype = source_type or _detect_type(url)
    if stype not in ("html", "rss", "ical"):
        raise ValueError(f"type non supporté pour une URL : {stype}")
    source = Source(id="candidate", name=url, type=stype, url=url, commune=commune)

    base.set_ssrf_guard(True)
    try:
        raw = fetch_source(source, sector_id)
    finally:
        base.set_ssrf_guard(False)

    events = dedupe([enrich(geocode(e, sector_id)) for e in raw])
    known = keys if keys is not None else existing_keys()
    unique = [e for e in events if e.dedupe_key() not in known]
    log.info(
        "[evaluate] %s : %d extraits, %d uniques (%d doublons)",
        url, len(raw), len(unique), len(events) - len(unique),
    )
    return {
        "url": url,
        "type": stype,
        "fetched": len(raw),
        "unique": len(unique),
        "duplicates": len(events) - len(unique),
        "events": [
            {
                "title": e.title, "start": e.start, "commune": e.commune,
                "category": e.category, "url": e.url,
            }
            for e in unique
        ],
    }
