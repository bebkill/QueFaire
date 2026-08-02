"""Fetcher RSS/Atom — le format le plus répandu sur les sites communaux.

Parseur volontairement minimal (xml.etree, zéro dépendance) : il couvre
RSS 2.0 et Atom, plus les extensions d'agenda courantes (ev:startdate, dc:date).
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

from ..models import Event, Source, parse_when
from .base import http_get

log = logging.getLogger("quefaire")

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ev": "http://purl.org/rss/1.0/modules/event/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


def strip_html(text: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _text(node: ET.Element, *paths: str) -> str | None:
    for path in paths:
        found = node.find(path, NS)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return None


def _atom_link(entry: ET.Element) -> str | None:
    link = entry.find("atom:link[@rel='alternate']", NS) or entry.find("atom:link", NS)
    return link.get("href") if link is not None else None


def parse_feed(content: bytes) -> list[dict]:
    """Retourne des dicts {title, description, link, start, end}."""
    root = ET.fromstring(content)
    items: list[dict] = []

    for item in root.iter("item"):  # RSS 2.0
        items.append(
            {
                "title": _text(item, "title"),
                "description": _text(item, "description", "content:encoded"),
                "link": _text(item, "link"),
                "start": _text(item, "ev:startdate", "dc:date", "pubDate"),
                "end": _text(item, "ev:enddate"),
            }
        )

    for entry in root.iter(f"{{{NS['atom']}}}entry"):  # Atom
        items.append(
            {
                "title": _text(entry, "atom:title"),
                "description": _text(entry, "atom:summary", "atom:content"),
                "link": _atom_link(entry),
                "start": _text(entry, "ev:startdate", "dc:date", "atom:published", "atom:updated"),
                "end": _text(entry, "ev:enddate"),
            }
        )

    return items


class RssFetcher:
    def fetch(self, source: Source, sector_id: str) -> list[Event]:
        resp = http_get(source.url)
        events: list[Event] = []
        for item in parse_feed(resp.content):
            when = parse_when(item["start"] or "")
            if when is None or not item["title"]:
                continue
            end = parse_when(item["end"] or "")
            events.append(
                Event(
                    title=strip_html(item["title"]),
                    description=strip_html(item["description"])[:1200],
                    start=when.isoformat(),
                    end=end.isoformat() if end else None,
                    url=item["link"],
                    commune=source.commune,
                    category=source.category_hint or "autre",
                    source_id=source.id,
                    sector=sector_id,
                )
            )
        log.info("[rss] %s : %d entrées", source.id, len(events))
        return events
