"""Découverte de nouvelles sources, assistée par agent (autoagent-core).

Objectif : rendre le référencement aussi automatique que possible. L'agent
explore le web (pages « agenda » des communes du secteur), repère des flux
RSS / iCal / agendas OpenAgenda, et propose des entrées prêtes à coller dans
sources/<secteur>.yaml. Un humain valide avant commit : la qualité du registre
reste maîtrisée.

Usage : python -m quefaire discover --sector isere
Nécessite QUEFAIRE_LLM (+ backup QUEFAIRE_LLM2, cf. quefaire.llm) et
autoagent-core installé.
"""

from __future__ import annotations

import re

import yaml

from .fetchers.base import http_get
from .geocode import commune_table
from .llm import get_agent, llm_available
from .registry import Sector

FEED_HINT_RE = re.compile(
    r'href="([^"]+(?:rss|atom|feed|\.ics|ical|webcal)[^"]*)"', re.I
)

DISCOVER_PROMPT = """Tu aides à référencer des sources d'agenda local pour le secteur "{sector}" (France).
Communes cibles : {communes}.
Pour chaque commune, utilise l'outil fetch_page sur des URLs plausibles de la mairie ou de
l'office de tourisme (ex: https://www.<commune>.fr/agenda) et repère :
- des flux RSS/Atom d'agenda,
- des liens iCal (.ics),
- des agendas openagenda.com (slug d'agenda),
- sinon la page HTML d'agenda elle-même.
Réponds UNIQUEMENT en YAML, une liste d'objets: {{id, name, type (rss|ical|openagenda|html), url, commune}}.
N'invente aucune URL : ne propose que des URLs que tu as vérifiées via fetch_page.
"""


def scan_feeds(url: str) -> list[str]:
    """Détection sans LLM : liens de flux dans la page d'accueil/agenda."""
    try:
        html = http_get(url).text
    except Exception:
        return []
    seen: list[str] = []
    for href in FEED_HINT_RE.findall(html):
        if href.startswith("/"):
            base = "/".join(url.split("/")[:3])
            href = base + href
        if href not in seen:
            seen.append(href)
    return seen


def discover(sector: Sector, communes: list[str] | None = None) -> str:
    """Retourne un bloc YAML de sources candidates (à relire par un humain)."""
    if not llm_available():
        raise RuntimeError("QUEFAIRE_LLM non configuré (ex: 'gemini:gemini-3.5-flash')")
    agent = get_agent()  # RuntimeError si principal ET backup indisponibles

    @agent.tool
    def fetch_page(url: str) -> str:
        """Récupère une page web et retourne son texte (tronqué) + les flux détectés."""
        try:
            resp = http_get(url)
        except Exception as exc:  # l'agent doit savoir que l'URL est mauvaise
            return f"ERREUR: {exc}"
        feeds = scan_feeds(url)
        text = re.sub(r"<[^>]+>", " ", resp.text)
        text = re.sub(r"\s+", " ", text)[:4000]
        return f"FLUX DÉTECTÉS: {feeds}\n\nTEXTE: {text}"

    targets = communes or [name for name, _, _ in commune_table(sector.id).values()][:12]
    result = agent.run(DISCOVER_PROMPT.format(sector=sector.name, communes=", ".join(targets)))
    raw = re.sub(r"^```(yaml)?|```$", "", result.output.strip(), flags=re.M).strip()
    # Validation : le YAML doit parser et chaque entrée avoir les clés attendues.
    entries = yaml.safe_load(raw) or []
    valid = [
        e
        for e in entries
        if isinstance(e, dict) and {"name", "type", "url"} <= set(e)
        and e["type"] in ("rss", "ical", "openagenda", "html")
    ]
    return yaml.safe_dump(valid, allow_unicode=True, sort_keys=False)
