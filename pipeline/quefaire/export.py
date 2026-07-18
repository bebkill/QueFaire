"""Export vers le site Astro : JSON consommés au build.

- events.json  : événements à venir, triés par date
- sector.json  : métadonnées du secteur (nom, centre carte, communes, sources)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from .geocode import commune_table
from .models import CATEGORIES, Event
from .registry import Sector

SITE_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "site" / "src" / "data"


def _upcoming(events: list[Event], horizon_days: int = 120) -> list[Event]:
    today = datetime.now().date()
    limit = today + timedelta(days=horizon_days)
    keep = []
    for ev in events:
        try:
            day = datetime.fromisoformat(ev.start.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        end_day = day
        if ev.end:
            try:
                end_day = datetime.fromisoformat(ev.end.replace("Z", "+00:00")).date()
            except ValueError:
                pass
        # On garde les événements en cours (expo longue durée) et à venir.
        if end_day >= today and day <= limit:
            keep.append(ev)
    return sorted(keep, key=lambda e: e.start)


def export(sector: Sector, events: list[Event], out_dir: Path | None = None) -> dict:
    out = out_dir or SITE_DATA_DIR
    out.mkdir(parents=True, exist_ok=True)

    upcoming = _upcoming(events)
    (out / "events.json").write_text(
        json.dumps([e.to_dict() for e in upcoming], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    meta = {
        "id": sector.id,
        "name": sector.name,
        "country": sector.country,
        "center": {"lat": sector.center_lat, "lon": sector.center_lon},
        "categories": CATEGORIES,
        "communes": sorted(
            {e.commune for e in upcoming if e.commune}
            | {name for name, _, _ in commune_table(sector.id).values()}
        ),
        "sources": [
            {"id": s.id, "name": s.name, "type": s.type, "url": s.url}
            for s in sector.sources
        ],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "event_count": len(upcoming),
    }
    (out / "sector.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return meta
