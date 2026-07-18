"""Fetcher réseaux sociaux (Facebook, Instagram) — via RSS-Bridge + LLM.

Beaucoup d'événements locaux ne sont annoncés QUE sur les pages Facebook ou
Instagram des communes et associations. Contraintes assumées :

- Meta n'offre pas d'API publique de lecture des pages qu'on n'administre pas
  (la Graph API exige une app validée + « Page Public Content Access »), et le
  scraping direct est bloqué et contraire aux CGU.
- La voie praticable et répandue : une instance RSS-Bridge (auto-hébergée ou
  publique) qui transforme une page publique en flux RSS. On la configure via
  la variable d'environnement RSSBRIDGE_URL (ex: https://rss-bridge.example.org).
- Un post n'est pas un événement structuré : le texte des posts récents est
  passé à l'agent LLM (autoagent-core) qui en extrait les événements datés,
  comme pour les pages HTML. QUEFAIRE_LLM requis.

Le champ `url` de la source est l'identifiant de la page : "mairiedegrenoble"
(Facebook) ou "villedegrenoble" (Instagram).
Sans RSSBRIDGE_URL ou sans LLM, la source est ignorée proprement.
"""

from __future__ import annotations

import logging
import os

from ..models import Event, Source
from .base import http_get
from .html_llm import extract_events_llm, llm_available
from .rss import parse_feed, strip_html

log = logging.getLogger("quefaire")

BRIDGES = {
    "facebook": "{base}/?action=display&bridge=Facebook&context=User&u={handle}&format=Atom",
    "instagram": "{base}/?action=display&bridge=Instagram&context=Username&u={handle}&media_type=all&format=Atom",
}

MAX_POSTS = 25


class SocialFetcher:
    def __init__(self, platform: str):
        self.platform = platform

    def fetch(self, source: Source, sector_id: str) -> list[Event]:
        base = os.environ.get("RSSBRIDGE_URL", "").rstrip("/")
        if not base:
            log.warning(
                "[%s] %s ignoré : RSSBRIDGE_URL non configurée (instance RSS-Bridge requise)",
                self.platform, source.id,
            )
            return []
        if not llm_available():
            log.warning(
                "[%s] %s ignoré : QUEFAIRE_LLM non configuré ou autoagent-core absent "
                "(nécessaire pour transformer les posts en événements)",
                self.platform, source.id,
            )
            return []

        bridge_url = BRIDGES[self.platform].format(base=base, handle=source.url)
        posts = parse_feed(http_get(bridge_url).content)[:MAX_POSTS]
        if not posts:
            log.info("[%s] %s : aucun post", self.platform, source.id)
            return []

        # Un seul appel LLM pour tous les posts récents de la page.
        corpus = "\n\n---\n\n".join(
            f"POST du {p['start'] or 'date inconnue'} :\n{strip_html(p['description'] or p['title'])}"
            for p in posts
        )
        page_url = (
            f"https://www.facebook.com/{source.url}"
            if self.platform == "facebook"
            else f"https://www.instagram.com/{source.url}"
        )
        events = extract_events_llm(corpus, source, sector_id, page_url)
        log.info(
            "[%s+llm] %s : %d posts → %d événements",
            self.platform, source.id, len(posts), len(events),
        )
        return events
