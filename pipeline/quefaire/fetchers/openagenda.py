"""Fetcher OpenAgenda — beaucoup de communes et d'offices de tourisme isérois
publient leur agenda sur openagenda.com (API publique, clé gratuite).

L'URL de la source est l'UID de l'agenda (ex: "agenda-grenoble" ou un UID numérique).
Nécessite la variable d'environnement OPENAGENDA_KEY ; sinon la source est ignorée
proprement (le pipeline reste utilisable sans clé).
"""

from __future__ import annotations

import logging
import os

from ..models import Event, Source
from .base import http_get

log = logging.getLogger("quefaire")

API = "https://api.openagenda.com/v2/agendas/{uid}/events"
API_AGENDAS = "https://api.openagenda.com/v2/agendas"


def search_agendas(query: str, key: str | None = None, size: int = 20) -> list[dict]:
    """Recherche d'agendas OpenAgenda ({uid, title, slug, description, url}).

    Sert à la découverte automatique : trouver tous les agendas qui parlent
    d'une commune, sans copier les UID à la main depuis le JSON de l'API.
    """
    key = key or os.environ.get("OPENAGENDA_KEY")
    if not key:
        raise RuntimeError("OPENAGENDA_KEY absente")
    data = http_get(API_AGENDAS, params={"key": key, "search": query, "size": size}).json()
    out = []
    for ag in data.get("agendas", []):
        out.append(
            {
                "uid": ag.get("uid"),
                "title": ag.get("title"),
                "slug": ag.get("slug"),
                "description": (ag.get("description") or "")[:200],
                "url": f"https://openagenda.com/agendas/{ag.get('slug') or ag.get('uid')}",
                "official": bool(ag.get("official")),
            }
        )
    return out


def _lang(value, lang="fr") -> str:
    """Les champs OpenAgenda sont multilingues ({'fr': ..., 'en': ...})."""
    if isinstance(value, dict):
        return value.get(lang) or next(iter(value.values()), "") or ""
    return value or ""


class OpenAgendaFetcher:
    def fetch(self, source: Source, sector_id: str) -> list[Event]:
        key = os.environ.get("OPENAGENDA_KEY")
        if not key:
            log.warning("[openagenda] %s ignoré : OPENAGENDA_KEY absente", source.id)
            return []
        events: list[Event] = []
        after: list[str] = []
        for _ in range(10):  # pagination bornée
            # `relative` est le filtre documenté de l'API v2 ; `timings[gte]=now`
            # provoque un 400 (la valeur doit être une date, pas "now").
            params = {
                "key": key,
                "size": 100,
                "relative[]": ["current", "upcoming"],
            }
            for i, cursor in enumerate(after):
                params[f"after[{i}]"] = cursor
            data = http_get(API.format(uid=source.url), params=params).json()
            for ev in data.get("events", []):
                timings = ev.get("timings") or []
                nexts = ev.get("nextTiming") or (timings[0] if timings else None)
                if not nexts:
                    continue
                loc = ev.get("location") or {}
                events.append(
                    Event(
                        title=_lang(ev.get("title")),
                        description=_lang(ev.get("description"))[:1200],
                        start=nexts.get("begin"),
                        end=nexts.get("end"),
                        commune=loc.get("city") or source.commune,
                        address=loc.get("address"),
                        lat=loc.get("latitude"),
                        lon=loc.get("longitude"),
                        url=f"https://openagenda.com/agendas/{source.url}/events/{ev.get('slug')}",
                        image=(ev.get("image") or {}).get("base")
                        and (ev["image"]["base"] + (ev["image"].get("filename") or ""))
                        or None,
                        free=(ev.get("conditions") is None) or None,
                        price_text=_lang(ev.get("conditions")) or None,
                        category=source.category_hint or "autre",
                        audience=[],
                        source_id=source.id,
                        sector=sector_id,
                    )
                )
            after = data.get("after") or []
            if not after:
                break
        log.info("[openagenda] %s : %d événements", source.id, len(events))
        return events
