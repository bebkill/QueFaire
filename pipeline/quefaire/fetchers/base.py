"""Socle commun des fetchers : HTTP poli + interface."""

from __future__ import annotations

import logging
import time
from http.client import IncompleteRead
from typing import Protocol

import requests

from ..models import Event, Source

log = logging.getLogger("quefaire")

USER_AGENT = (
    "QueFaireBot/0.1 (+https://github.com/bebkill/QueFaire; agrégateur d'événements locaux)"
)
TIMEOUT = 20
RETRIES = 2  # tentatives supplémentaires sur aléa réseau transitoire


def http_get(url: str, **kwargs) -> requests.Response:
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    for attempt in range(RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=TIMEOUT, **kwargs)
        except (requests.RequestException, IncompleteRead) as exc:
            # Aléa réseau (connexion coupée, lecture incomplète, timeout) : on
            # rejoue quelques fois avant d'abandonner la source — sinon un
            # IncompleteRead ponctuel fait perdre toute une page agenda (vécu :
            # html-bourgoin, ~30 événements). raise_for_status (statut HTTP
            # explicite : 403, 404…) n'est PAS concerné : pas de retry inutile.
            if attempt < RETRIES:
                log.warning(
                    "[http] %s : %s — nouvelle tentative (%d/%d)",
                    url, exc, attempt + 1, RETRIES,
                )
                time.sleep(0.5 * (2 ** attempt))
                continue
            raise
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            # Le corps de la réponse dit souvent QUEL paramètre est refusé.
            raise requests.HTTPError(
                f"{exc} — réponse : {resp.text[:300]}", response=resp
            ) from None
        return resp


class Fetcher(Protocol):
    def fetch(self, source: Source, sector_id: str) -> list[Event]: ...
