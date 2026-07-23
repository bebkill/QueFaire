"""Cache adressé par contenu pour les appels LLM (extraction, clarify).

Clé = hash du texte d'entrée (page agenda, corpus social, ou titre+description
d'un événement). Si le contenu n'a pas changé depuis le dernier crawl, on
réutilise le résultat sans rappeler le LLM :

- **répétabilité** : deux crawls sur des pages inchangées rendent exactement le
  même résultat (le LLM, même à temperature=0, n'est pas parfaitement
  déterministe — surtout les modèles MoE) ;
- **quota** : le LLM n'est appelé que sur les pages nouvelles ou modifiées ;
- **résilience** : un quota mort en cours de run n'empêche pas de servir les
  pages inchangées (elles sortent du cache sans appel).

Le cache est un simple JSON committé par la CI (comme site/src/data). À chaque
run on ne conserve que les clés effectivement vues, ce qui élague
automatiquement les sources retirées ou les anciennes versions de page.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

log = logging.getLogger("quefaire")

CACHE_PATH = Path(__file__).resolve().parent.parent / "cache" / "content.json"


class _ContentCache:
    def __init__(self) -> None:
        self._store: dict = {}
        self._used: set[str] = set()
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            self._store = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            self._store = {}

    @staticmethod
    def key(namespace: str, *parts: str) -> str:
        """Clé stable = namespace + sha256 du contenu (séparateur non ambigu)."""
        digest = hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()
        return f"{namespace}:{digest}"

    def get(self, key: str):
        """Valeur mémorisée ou None. Marque la clé comme vue (rétention)."""
        self._load()
        self._used.add(key)
        return self._store.get(key)

    def put(self, key: str, value) -> None:
        self._load()
        self._used.add(key)
        self._store[key] = value

    def save(self) -> None:
        """Écrit le cache en ne gardant que les clés vues ce run (élagage).

        No-op si aucune clé n'a été touchée (ex. crawl en démo ou sans LLM) :
        on ne veut surtout pas effacer un cache existant dans ce cas."""
        if not self._used:
            return
        pruned = {k: v for k, v in self._store.items() if k in self._used}
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_name(CACHE_PATH.name + ".tmp")
        tmp.write_text(
            json.dumps(pruned, ensure_ascii=False, sort_keys=True, indent=0),
            encoding="utf-8",
        )
        tmp.replace(CACHE_PATH)
        log.info(
            "[cache] %d entrées conservées (%d clés vues ce run)", len(pruned), len(self._used)
        )


cache = _ContentCache()
