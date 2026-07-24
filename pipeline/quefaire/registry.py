"""Chargement du registre de sources par secteur.

Un secteur (isere, savoie, …) = un fichier YAML dans pipeline/sources/.
Ajouter une région se résume à ajouter un fichier + un CSV de communes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from .models import Source

SOURCE_FIELDS = {f.name for f in fields(Source)}

SOURCES_DIR = Path(__file__).resolve().parent.parent / "sources"


@dataclass
class Sector:
    id: str
    name: str
    country: str
    center_lat: float
    center_lon: float
    sources: list[Source] = field(default_factory=list)


def available_sectors() -> list[str]:
    return sorted(p.stem for p in SOURCES_DIR.glob("*.yaml"))


def sources_path(sector_id: str) -> Path:
    return SOURCES_DIR / f"{sector_id}.yaml"


# --- Édition du registre (préserve les commentaires : on manipule le TEXTE) ---
# PyYAML perd les commentaires au dump ; le registre est très annoté. On édite
# donc le fichier ligne à ligne plutôt que via un round-trip yaml.

_ALLOWED_TYPES = {"rss", "ical", "openagenda", "html", "facebook", "instagram"}


def _existing_urls(sector_id: str) -> set[str]:
    raw = yaml.safe_load(sources_path(sector_id).read_text(encoding="utf-8")) or {}
    return {str(s.get("url", "")).strip() for s in raw.get("sources", []) if isinstance(s, dict)}


def append_source(sector_id: str, entry: dict) -> bool:
    """Ajoute une source validée au registre (enabled: true), en préservant les
    commentaires. No-op (False) si l'URL est déjà référencée ou l'entrée invalide."""
    required = {"id", "name", "type", "url"}
    if not required <= set(entry) or entry["type"] not in _ALLOWED_TYPES:
        raise ValueError(f"entrée de source invalide : {entry!r}")
    url = str(entry["url"]).strip()
    if url in _existing_urls(sector_id):
        return False

    clean = {
        "id": str(entry["id"]).strip(),
        "name": str(entry["name"]).strip(),
        "type": entry["type"],
        "url": url,
    }
    if entry.get("commune"):
        clean["commune"] = str(entry["commune"]).strip()
    if entry.get("category_hint"):
        clean["category_hint"] = str(entry["category_hint"]).strip()
    clean["enabled"] = True

    block = yaml.safe_dump([clean], allow_unicode=True, sort_keys=False)
    block = "".join("  " + line if line.strip() else line for line in block.splitlines(keepends=True))

    path = sources_path(sector_id)
    text = path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text + block, encoding="utf-8")
    return True


def set_enabled(sector_id: str, source_id: str, enabled: bool) -> bool:
    """Bascule le drapeau `enabled` d'une source dans le fichier (texte préservé).
    Retourne True si un changement a été appliqué."""
    path = sources_path(sector_id)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    target = f"enabled: {'true' if enabled else 'false'}"

    in_block = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"-\s*id:\s*", stripped):
            in_block = stripped.split("id:", 1)[1].strip() == source_id
            continue
        if in_block and stripped.startswith("enabled:"):
            indent = line[: len(line) - len(line.lstrip())]
            new = f"{indent}{target}"
            trailing = line[len(line.rstrip("\n")):]  # conserve le \n
            if line.rstrip("\n").rstrip() == new:
                return False  # déjà dans l'état voulu
            lines[i] = new + trailing
            path.write_text("".join(lines), encoding="utf-8")
            return True
    return False


def load_sector(sector_id: str) -> Sector:
    path = SOURCES_DIR / f"{sector_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"Secteur inconnu : {sector_id}. Disponibles : {', '.join(available_sectors())}"
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    meta = raw.get("sector", {})
    # Tolérant aux clés inconnues (ex: `comment` émis par discover-oa).
    sources = [
        Source(**{
            **{k: v for k, v in src.items() if k in SOURCE_FIELDS},
            "id": src.get("id") or f"{sector_id}-{i}",
            "url": str(src.get("url", "")),
        })
        for i, src in enumerate(raw.get("sources", []))
    ]
    return Sector(
        id=sector_id,
        name=meta.get("name", sector_id),
        country=meta.get("country", "FR"),
        center_lat=float(meta.get("center_lat", 45.2)),
        center_lon=float(meta.get("center_lon", 5.7)),
        sources=[s for s in sources if s.enabled],
    )
