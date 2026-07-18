"""Socle commun des fetchers : HTTP poli + interface."""

from __future__ import annotations

import logging
from typing import Protocol

import requests

from ..models import Event, Source

log = logging.getLogger("quefaire")

USER_AGENT = (
    "QueFaireBot/0.1 (+https://github.com/bebkill/QueFaire; agrégateur d'événements locaux)"
)
TIMEOUT = 20


def http_get(url: str, **kwargs) -> requests.Response:
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    resp = requests.get(url, headers=headers, timeout=TIMEOUT, **kwargs)
    resp.raise_for_status()
    return resp


class Fetcher(Protocol):
    def fetch(self, source: Source, sector_id: str) -> list[Event]: ...
