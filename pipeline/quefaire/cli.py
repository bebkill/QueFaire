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
    if not demo:
        from .clarify import clarify

        events = clarify(events)  # fiches « en clair » — no-op sans LLM
    meta = export(sector, events, out)
    from .cache import cache

    cache.save()  # no-op en démo / sans LLM (aucune clé touchée)
    log.info(
        "Export OK : %d événements à venir pour « %s » (%d communes)",
        meta["event_count"], meta["name"], len(meta["communes"]),
    )
    return 0


def discover_openagenda(sector_id: str, communes: list[str] | None, strict: bool) -> str:
    """Cherche les agendas OpenAgenda des communes du secteur et émet le YAML
    des sources candidates, prêt à coller dans sources/<secteur>.yaml.

    « Beaucoup d'agendas pour Grenoble » : on interroge l'API agendas pour
    chaque commune, on déduplique par UID, et on trie les officiels d'abord.
    La validation humaine (enabled: true) reste la dernière étape.
    """
    import yaml

    from .fetchers.openagenda import search_agendas
    from .geocode import commune_table
    from .normalize import fold

    targets = communes or [name for name, _, _ in commune_table(sector_id).values()]
    seen: dict[int, dict] = {}
    for commune in targets:
        try:
            agendas = search_agendas(commune)
        except Exception as exc:
            log.error("[discover-oa] %s : %s", commune, exc)
            continue
        for ag in agendas:
            if not ag["uid"]:
                continue
            title_match = fold(commune) in fold(ag["title"] or "")
            if strict and not title_match:
                continue
            entry = seen.setdefault(
                ag["uid"],
                {
                    "id": f"oa-{ag['slug'] or ag['uid']}"[:40],
                    "name": f"{ag['title']} (OpenAgenda)",
                    "type": "openagenda",
                    "url": ag["uid"],
                    "commune": None,
                    "enabled": False,
                    "_official": ag["official"],
                    "_matches": [],
                },
            )
            entry["_matches"].append(commune)
            if title_match and entry["commune"] is None:
                entry["commune"] = commune

    # Le nombre d'événements à venir départage les agendas vivants des dormants
    # (leçon du premier crawl : 8 agendas activés, 8 × 0 événement).
    from .fetchers.openagenda import upcoming_count

    for e in seen.values():
        try:
            e["_count"] = upcoming_count(e["url"])
        except Exception:
            e["_count"] = -1  # inconnu

    candidates = sorted(
        seen.values(),
        key=lambda e: (not e["_official"], -e["_count"], not e["commune"], e["name"]),
    )
    for e in candidates:
        count = e.pop("_count")
        e["comment"] = (
            ("officiel, " if e.pop("_official") else "")
            + (f"{count} événements à venir, " if count >= 0 else "")
            + "trouvé via : " + ", ".join(sorted(set(e.pop("_matches"))))
        )
    live = sum(1 for e in candidates if "0 événements" not in e["comment"])
    log.info("[discover-oa] %d agendas candidats dont %d avec des événements à venir "
             "(%d communes interrogées)", len(candidates), live, len(targets))
    return yaml.safe_dump(candidates, allow_unicode=True, sort_keys=False)


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

    p_oa = sub.add_parser(
        "discover-oa",
        help="Trouver les agendas OpenAgenda des communes du secteur (OPENAGENDA_KEY requise)",
    )
    p_oa.add_argument("--sector", default="isere")
    p_oa.add_argument("--communes", default=None, help="Restreindre à ces communes (séparées par des virgules)")
    p_oa.add_argument("--strict", action="store_true",
                      help="Ne garder que les agendas dont le titre mentionne la commune")

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
    if args.cmd == "discover-oa":
        communes = [c.strip() for c in args.communes.split(",")] if args.communes else None
        print(discover_openagenda(args.sector, communes, args.strict))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
