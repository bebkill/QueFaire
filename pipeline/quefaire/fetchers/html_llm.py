"""Fetcher HTML assisté par LLM, basé sur autoagent-core.

Pour les sites communaux sans flux structuré (ni RSS, ni iCal, ni OpenAgenda) :
on récupère la page agenda, on la nettoie, et un agent LLM en extrait les
événements au format JSON du schéma commun.

Optionnel : ne s'active que si autoagent-core est installé ET qu'un modèle est
configuré (QUEFAIRE_LLM, ex: "gemini:gemini-3.5-flash" ou "anthropic:claude-haiku-4-5",
avec la clé API du provider dans l'environnement). Sinon la source est ignorée
proprement — le pipeline reste 100 % fonctionnel sans LLM.
"""

from __future__ import annotations

import json
import logging
import os
import re

from ..models import CATEGORIES, Event, Source, parse_when
from .base import http_get

log = logging.getLogger("quefaire")

EXTRACTION_PROMPT = """Tu extrais des événements locaux (agenda) du texte d'une page web communale.

Réponds UNIQUEMENT avec un tableau JSON (éventuellement vide), sans texte autour.
Chaque élément : {{
  "title": str, "description": str (2 phrases max),
  "start": str ISO 8601 (date ou datetime, année incluse — nous sommes le {today}),
  "end": str ISO 8601 ou null,
  "commune": str ou null, "address": str ou null,
  "category": une valeur parmi {categories},
  "audience": liste parmi ["famille","enfants","ados","adultes","seniors","tous"],
  "free": bool ou null, "price_text": str ou null, "url": str ou null
}}
Ignore tout ce qui n'est pas un événement daté à venir.

TEXTE DE LA PAGE ({url}) :
{text}
"""


def _page_text(html: str, scope_selector: str | None) -> str:
    """Markdown grossier de la page, borné pour tenir dans un prompt."""
    if scope_selector:
        # Restriction naïve à la zone demandée si le sélecteur est un id.
        m = re.search(
            r'<[^>]+id="' + re.escape(scope_selector.lstrip("#")) + r'".*',
            html,
            re.S,
        )
        if m:
            html = m.group(0)
    html = re.sub(r"<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<br\s*/?>|</p>|</li>|</h[1-6]>|</div>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()[:24000]


def llm_available() -> bool:
    if not os.environ.get("QUEFAIRE_LLM"):
        return False
    try:
        import autoagent  # noqa: F401

        return True
    except ImportError:
        return False


def extract_events_llm(text: str, source: Source, sector_id: str, context_url: str) -> list[Event]:
    """Cœur réutilisable : texte brut (page agenda, posts sociaux…) → Events.

    Utilisé par le fetcher html et par le fetcher réseaux sociaux.
    """
    from datetime import date

    from autoagent import Agent

    provider, _, model = os.environ["QUEFAIRE_LLM"].partition(":")
    agent = Agent.from_model(provider, model)

    prompt = EXTRACTION_PROMPT.format(
        today=date.today().isoformat(),
        categories=list(CATEGORIES),
        url=context_url,
        text=text[:24000],
    )
    result = agent.run(prompt)
    raw = result.output.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.M).strip()
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        log.error("[llm] %s : réponse LLM non parsable", source.id)
        return []

    events: list[Event] = []
    for item in items if isinstance(items, list) else []:
        when = parse_when(str(item.get("start", "")))
        if not when or not item.get("title"):
            continue
        events.append(
            Event(
                title=item["title"],
                description=(item.get("description") or "")[:1200],
                start=when.isoformat(),
                end=item.get("end"),
                commune=item.get("commune") or source.commune,
                address=item.get("address"),
                category=item.get("category") or source.category_hint or "autre",
                audience=item.get("audience") or [],
                free=item.get("free"),
                price_text=item.get("price_text"),
                url=item.get("url") or context_url,
                source_id=source.id,
                sector=sector_id,
            )
        )
    return events


class HtmlLlmFetcher:
    def fetch(self, source: Source, sector_id: str) -> list[Event]:
        if not llm_available():
            log.warning(
                "[html] %s ignoré : QUEFAIRE_LLM non configuré ou autoagent-core absent",
                source.id,
            )
            return []
        html = http_get(source.url).text
        events = extract_events_llm(
            _page_text(html, source.scope_selector), source, sector_id, source.url
        )
        log.info("[html+llm] %s : %d événements extraits", source.id, len(events))
        return events
