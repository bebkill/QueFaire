"""Export vers le site Astro : JSON consommés au build.

- events.json  : événements à venir, triés par date
- sector.json  : métadonnées du secteur (nom, centre carte, communes, sources)
- cities.json  : annuaire des villes (épicentres) actives — pour le portail
                 « choisir sa ville » (localisation / recherche / carte)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from .geocode import commune_table
from .models import CATEGORIES, Event
from .registry import Sector, available_sectors, load_sector

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
        "radius_minutes": sector.radius_minutes,
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
    _write_cities(sector, len(upcoming), meta["generated_at"], out)
    return meta


def _write_cities(sector: Sector, event_count: int, generated_at: str, out: Path) -> None:
    """Met à jour cities.json : l'annuaire des villes (épicentres) actives.

    Chaque crawl ne connaît QUE son propre secteur ; on fusionne donc avec le
    fichier existant pour préserver le compteur et la date des autres villes
    (chacune est rafraîchie par son propre crawl). Le nom, le centre et le rayon
    sont relus du registre à chaque passage. `url` reste éditable à la main pour
    pointer vers un déploiement dédié (sinon la ville ouvre le site courant).
    """
    path = out / "cities.json"
    prev: dict[str, dict] = {}
    if path.exists():
        try:
            for c in json.loads(path.read_text(encoding="utf-8")).get("cities", []):
                if isinstance(c, dict) and c.get("id"):
                    prev[c["id"]] = c
        except (ValueError, KeyError):
            prev = {}

    cities = []
    for sid in available_sectors():
        s = load_sector(sid)
        p = prev.get(sid, {})
        current = sid == sector.id
        cities.append({
            "id": sid,
            "name": s.name,
            "center": {"lat": s.center_lat, "lon": s.center_lon},
            "radius_minutes": s.radius_minutes,
            "event_count": event_count if current else p.get("event_count"),
            "generated_at": generated_at if current else p.get("generated_at"),
            "url": p.get("url"),
        })

    latest = max((c["generated_at"] for c in cities if c["generated_at"]), default=generated_at)
    path.write_text(
        json.dumps({"generated_at": latest, "cities": cities}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
