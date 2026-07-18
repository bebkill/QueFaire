"""Point d'entrée du pipeline.

  python -m quefaire crawl    --sector isere [--demo] [--out DIR]
  python -m quefaire discover --sector isere [--communes A,B,C]
  python -m quefaire sectors
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .dedupe import dedupe
from .export import export
from .fetchers import fetch_source
from .geocode import geocode
from .models import Event
from .normalize import enrich
from .registry import available_sectors, load_sector

log = logging.getLogger("quefaire")


def crawl(sector_id: str, demo: bool, out: Path | None) -> int:
    sector = load_sector(sector_id)
    events: list[Event] = []

    if not demo and not sector.sources:
        log.warning(
            "Aucune source activée pour « %s » — bascule en mode démo pour ne pas publier un site vide. "
            "Activez des sources dans sources/%s.yaml.",
            sector.name, sector_id,
        )
        demo = True

    if demo:
        from .demo import demo_events

        events = demo_events(sector_id)
        log.info("[demo] %d événements de démonstration", len(events))
    else:
        failures = 0
        for source in sector.sources:
            try:
                events.extend(fetch_source(source, sector_id))
            except Exception as exc:
                failures += 1
                log.error("[%s] échec : %s", source.id, exc)
        if failures and not events:
            log.error("Toutes les sources ont échoué — export annulé pour ne pas vider le site.")
            return 1

    events = [enrich(geocode(e, sector_id)) for e in events]
    events = dedupe(events)
    meta = export(sector, events, out)
    log.info(
        "Export OK : %d événements à venir pour « %s » (%d communes)",
        meta["event_count"], meta["name"], len(meta["communes"]),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="quefaire")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_crawl = sub.add_parser("crawl", help="Crawler les sources et exporter vers le site")
    p_crawl.add_argument("--sector", default="isere")
    p_crawl.add_argument("--demo", action="store_true", help="Jeu de données de démonstration")
    p_crawl.add_argument("--out", type=Path, default=None)

    p_disc = sub.add_parser("discover", help="Proposer de nouvelles sources (agent LLM)")
    p_disc.add_argument("--sector", default="isere")
    p_disc.add_argument("--communes", default=None, help="Liste de communes, séparées par des virgules")

    sub.add_parser("sectors", help="Lister les secteurs disponibles")

    args = parser.parse_args(argv)

    if args.cmd == "sectors":
        print("\n".join(available_sectors()))
        return 0
    if args.cmd == "crawl":
        return crawl(args.sector, args.demo, args.out)
    if args.cmd == "discover":
        from .discovery import discover

        communes = args.communes.split(",") if args.communes else None
        print(discover(load_sector(args.sector), communes))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
