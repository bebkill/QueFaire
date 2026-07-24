"""Fraîcheur des sources : retrait automatique de celles qui n'apportent plus
d'événements (source abandonnée, page fermée, flux mort).

État par source dans `pipeline/cache/source_health.json`, committé par la CI :
  {source_id: {"first_seen_day": "YYYY-MM-DD", "last_event_day": "YYYY-MM-DD"}}
- `last_event_day` : dernier jour de crawl où la source a produit ≥1 événement
  À VENIR (un flux qui ne renvoie que du passé ne « produit » plus rien).
- `first_seen_day` : période de grâce pour une source fraîchement ajoutée.

Une source `enabled` sans production depuis plus de `STALE_DAYS` jours est
désactivée (`enabled: false`) dans le registre — réversible, un humain peut la
réactiver. On ne supprime jamais la ligne (on garde l'historique et le commentaire).
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger("quefaire")

HEALTH_PATH = Path(__file__).resolve().parent.parent / "cache" / "source_health.json"
STALE_DAYS = 30


def _day(iso: str) -> date:
    return datetime.strptime(iso, "%Y-%m-%d").date()


class _Health:
    def __init__(self) -> None:
        self._data: dict = {}
        self._loaded = False
        self._touched = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            self._data = json.loads(HEALTH_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            self._data = {}

    def record(self, source_id: str, produced: bool, today: str | None = None) -> None:
        """Mémorise si la source a produit un événement à venir lors de ce crawl."""
        self._load()
        self._touched = True
        today = today or date.today().isoformat()
        entry = self._data.setdefault(source_id, {"first_seen_day": today, "last_event_day": None})
        if produced:
            entry["last_event_day"] = today

    def stale_ids(self, active_ids: list[str], today: str | None = None) -> list[str]:
        """IDs à désactiver : connus, sans production depuis > STALE_DAYS jours
        (la période de grâce court depuis first_seen tant qu'il n'y a rien eu)."""
        self._load()
        ref_today = _day(today or date.today().isoformat())
        stale = []
        for sid in active_ids:
            e = self._data.get(sid)
            if not e:
                continue
            ref = e.get("last_event_day") or e.get("first_seen_day")
            if ref and (ref_today - _day(ref)).days > STALE_DAYS:
                stale.append(sid)
        return stale

    def save(self, keep_ids: set[str] | None = None) -> None:
        """Écrit l'état (élague les sources disparues du registre). No-op si rien
        n'a été enregistré ce run (démo / pas de crawl)."""
        if not self._touched:
            return
        if keep_ids is not None:
            self._data = {k: v for k, v in self._data.items() if k in keep_ids}
        HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = HEALTH_PATH.with_name(HEALTH_PATH.name + ".tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, sort_keys=True, indent=0),
            encoding="utf-8",
        )
        tmp.replace(HEALTH_PATH)


health = _Health()
