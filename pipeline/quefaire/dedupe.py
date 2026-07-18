"""Déduplication : le même événement est souvent relayé par plusieurs sources
(mairie + office de tourisme + OpenAgenda). On garde la version la plus riche."""

from __future__ import annotations

from .models import Event


def _richness(event: Event) -> int:
    score = len(event.description)
    score += 200 if event.image else 0
    score += 100 if event.lat is not None else 0
    score += 50 if event.url else 0
    score += 50 if event.free is not None else 0
    return score


def dedupe(events: list[Event]) -> list[Event]:
    best: dict[str, Event] = {}
    for event in events:
        key = event.dedupe_key()
        current = best.get(key)
        if current is None or _richness(event) > _richness(current):
            best[key] = event
    return list(best.values())
