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
from .registry import available_sectors, load_sector, set_enabled

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
        from datetime import date

        from .health import STALE_DAYS, health

        today = date.today().isoformat()
        failures = 0
        for source in sector.sources:
            evts: list[Event] = []
            try:
                evts = fetch_source(source, sector_id)
            except Exception as exc:
                failures += 1
                log.error("[%s] échec : %s", source.id, exc)
            events.extend(evts)
            # « Produit » = au moins un événement À VENIR (le passé ne compte pas).
            health.record(source.id, any(e.start[:10] >= today for e in evts), today)
        if failures and not events:
            log.error("Toutes les sources ont échoué — export annulé pour ne pas vider le site.")
            return 1

        # Retrait auto des sources abandonnées (plus rien depuis > 1 mois).
        for sid in health.stale_ids([s.id for s in sector.sources], today):
            if set_enabled(sector_id, sid, False):
                log.warning(
                    "[health] source « %s » désactivée : aucun événement depuis > %d jours",
                    sid, STALE_DAYS,
                )
        health.save(keep_ids={s.id for s in sector.sources})

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


def suggest(sector_id: str) -> list[dict]:
    """Candidates de sources NOUVELLES (URL non déjà référencée) via les outils
    de découverte (OpenAgenda + agent LLM si dispo). Pour le workflow qui ouvre
    une issue de suggestion par source — un humain confirme avant activation."""
    import yaml

    from .registry import _existing_urls

    existing = _existing_urls(sector_id)
    candidates: list[dict] = []
    try:
        candidates += yaml.safe_load(discover_openagenda(sector_id, None, False)) or []
    except Exception as exc:  # découverte best-effort : un échec ne bloque pas
        log.warning("[suggest] discover-oa indisponible : %s", exc)

    from .llm import llm_available

    if llm_available():
        try:
            from .discovery import discover as _discover

            candidates += yaml.safe_load(_discover(load_sector(sector_id))) or []
        except Exception as exc:
            log.warning("[suggest] discover (LLM) indisponible : %s", exc)

    seen: set[str] = set()
    new: list[dict] = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        url = str(c.get("url", "")).strip()
        if not url or url in existing or url in seen:
            continue
        seen.add(url)
        new.append({k: c[k] for k in ("id", "name", "type", "url", "commune") if c.get(k)})
    log.info("[suggest] %d source(s) candidate(s) nouvelle(s) pour %s", len(new), sector_id)
    return new


def add_source(sector_id: str, path: Path | None) -> int:
    """Ajoute au registre les sources décrites dans un bloc YAML (typiquement le
    corps d'une issue de suggestion approuvée). Valide chaque entrée (type connu,
    URL http/https sûre pour les sources web) avant ajout ; les entrées ajoutées
    sont `enabled: true`. Doublons d'URL ignorés."""
    import re

    import yaml

    from .registry import append_source
    from .security import UnsafeUrlError, validate_public_url

    text = path.read_text(encoding="utf-8") if path else sys.stdin.read()
    m = re.search(r"```(?:ya?ml)?\s*(.*?)```", text, re.S)  # bloc ```yaml``` si présent
    payload = m.group(1) if m else text
    try:
        data = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        log.error("YAML illisible : %s", exc)
        return 1
    entries = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    added = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") in ("html", "rss", "ical"):
            try:
                validate_public_url(str(entry.get("url", "")))
            except UnsafeUrlError as exc:
                log.error("URL refusée (%s) : %s", exc, entry.get("url"))
                continue
        try:
            if append_source(sector_id, entry):
                added += 1
                log.info("[add-source] ajoutée : %s (%s)", entry.get("id"), entry.get("url"))
            else:
                log.info("[add-source] ignorée (déjà présente) : %s", entry.get("url"))
        except ValueError as exc:
            log.error("[add-source] entrée invalide ignorée : %s", exc)
    log.info("[add-source] %d source(s) ajoutée(s) au secteur %s", added, sector_id)
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

    p_oa = sub.add_parser(
        "discover-oa",
        help="Trouver les agendas OpenAgenda des communes du secteur (OPENAGENDA_KEY requise)",
    )
    p_oa.add_argument("--sector", default="isere")
    p_oa.add_argument("--communes", default=None, help="Restreindre à ces communes (séparées par des virgules)")
    p_oa.add_argument("--strict", action="store_true",
                      help="Ne garder que les agendas dont le titre mentionne la commune")

    p_eval = sub.add_parser(
        "evaluate-source",
        help="Évaluer une URL candidate : événements uniques apportés (garde-fous SSRF)",
    )
    p_eval.add_argument("url")
    p_eval.add_argument("--sector", default="isere")
    p_eval.add_argument("--type", dest="source_type", default=None, choices=["html", "rss", "ical"])
    p_eval.add_argument("--commune", default=None, help="Commune par défaut si la source est communale")
    p_eval.add_argument("--json", action="store_true", help="Sortie JSON (pour un workflow)")

    p_sug = sub.add_parser(
        "suggest", help="Lister en JSON les sources candidates nouvelles (pour ouvrir des issues)"
    )
    p_sug.add_argument("--sector", default="isere")

    p_add = sub.add_parser(
        "add-source",
        help="Ajouter au registre les sources d'un bloc YAML (corps d'issue approuvée)",
    )
    p_add.add_argument("--sector", default="isere")
    p_add.add_argument("--file", type=Path, default=None, help="Fichier (sinon stdin)")

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
    if args.cmd == "suggest":
        import json

        print(json.dumps(suggest(args.sector), ensure_ascii=False))
        return 0
    if args.cmd == "add-source":
        return add_source(args.sector, args.file)
    if args.cmd == "evaluate-source":
        import json

        from .evaluate import evaluate_url
        from .security import UnsafeUrlError

        try:
            report = evaluate_url(
                args.url, args.sector, source_type=args.source_type, commune=args.commune
            )
        except UnsafeUrlError as exc:
            log.error("URL refusée par les contrôles de sécurité : %s", exc)
            return 1
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(
                f"{report['unique']} événement(s) unique(s) sur {report['fetched']} extrait(s) "
                f"— {report['duplicates']} doublon(s) avec l'existant\n{report['url']}"
            )
            for e in report["events"][:20]:
                print(f"  • {e['start'][:10]}  {e['title']}  [{e['commune'] or '?'}]")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
