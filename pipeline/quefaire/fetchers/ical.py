"""Fetcher iCal (.ics) — agendas Google/Outlook publiés par mairies et assos."""

from __future__ import annotations

import logging
from datetime import date, datetime

from icalendar import Calendar

from ..models import Event, Source
from .base import http_get

log = logging.getLogger("quefaire")


def _iso(value) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return None


class IcalFetcher:
    def fetch(self, source: Source, sector_id: str) -> list[Event]:
        resp = http_get(source.url)
        cal = Calendar.from_ical(resp.content)
        events: list[Event] = []
        for comp in cal.walk("VEVENT"):
            start = _iso(comp.get("dtstart") and comp.get("dtstart").dt)
            if not start:
                continue
            end = _iso(comp.get("dtend") and comp.get("dtend").dt)
            location = str(comp.get("location") or "") or None
            events.append(
                Event(
                    title=str(comp.get("summary") or "Sans titre"),
                    description=str(comp.get("description") or "")[:1200],
                    start=start,
                    end=end,
                    address=location,
                    commune=source.commune,
                    url=str(comp.get("url") or "") or None,
                    category=source.category_hint or "autre",
                    source_id=source.id,
                    sector=sector_id,
                )
            )
        log.info("[ical] %s : %d événements", source.id, len(events))
        return events
