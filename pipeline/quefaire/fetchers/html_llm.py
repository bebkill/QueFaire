"""Fetcher HTML assisté par LLM, basé sur autoagent-core.

Pour les sites communaux sans flux structuré (ni RSS, ni iCal, ni OpenAgenda) :
on récupère la page agenda, on la nettoie, et un agent LLM en extrait les
événements au format JSON du schéma commun.

Optionnel : ne s'active que si autoagent-core est installé ET qu'un modèle est
configuré (QUEFAIRE_LLM, ex: "gemini:gemini-3.5-flash" ou "anthropic:claude-haiku-4-5",
avec la clé API du provider dans l'environnement ; QUEFAIRE_LLM2 sert de backup
si le principal ne répond pas — voir quefaire.llm). Sinon la source est
ignorée proprement — le pipeline reste 100 % fonctionnel sans LLM.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

from ..llm import llm_available, run_llm
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


def extract_events_llm(text: str, source: Source, sector_id: str, context_url: str) -> list[Event]:
    """Cœur réutilisable : texte brut (page agenda, posts sociaux…) → Events.

    Utilisé par le fetcher html et par le fetcher réseaux sociaux.
    """
    prompt = EXTRACTION_PROMPT.format(
        today=date.today().isoformat(),
        categories=list(CATEGORIES),
        url=context_url,
        text=text[:24000],
    )
    # run_llm : bascule automatiquement sur le backup si le quota meurt ici.
    result = run_llm(prompt)
    raw = result.output.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.M).strip()
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        # Dernier recours : premier tableau JSON trouvé dans la réponse
        # (les modèles ajoutent parfois du texte autour malgré la consigne).
        m = re.search(r"\[.*\]", raw, re.S)
        try:
            items = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            items = None
        if items is None:
            log.error("[llm] %s : réponse LLM non parsable (début : %.120s)", source.id, raw)
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
        try:
            events = extract_events_llm(
                _page_text(html, source.scope_selector), source, sector_id, source.url
            )
        except RuntimeError as exc:
            log.warning("[html] %s ignoré : %s", source.id, exc)
            return []
        log.info("[html+llm] %s : %d événements extraits", source.id, len(events))
        return events
