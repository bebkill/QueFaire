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
MAX_REDIRECTS = 5

# Mode garde-fou : activé UNIQUEMENT quand on récupère une URL soumise par un
# tiers (module « proposer une source »). Valide l'URL et chaque redirection
# contre les IP internes (anti-SSRF). Désactivé pendant le crawl normal (les
# sources du registre sont relues par un humain).
_ssrf_guard = False


def set_ssrf_guard(enabled: bool) -> None:
    global _ssrf_guard
    _ssrf_guard = enabled


def _get(url: str, headers: dict, allow_redirects: bool, **kwargs) -> requests.Response:
    """requests.get, avec suivi manuel + revalidation des redirections quand le
    garde-fou SSRF est actif (une redirection peut viser une IP interne)."""
    if not _ssrf_guard:
        return requests.get(
            url, headers=headers, timeout=TIMEOUT, allow_redirects=allow_redirects, **kwargs
        )
    from ..security import validate_public_url

    for _ in range(MAX_REDIRECTS + 1):
        validate_public_url(url)
        resp = requests.get(
            url, headers=headers, timeout=TIMEOUT, allow_redirects=False, **kwargs
        )
        if allow_redirects and resp.is_redirect and resp.headers.get("Location"):
            url = requests.compat.urljoin(url, resp.headers["Location"])
            continue
        return resp
    raise requests.TooManyRedirects(f"plus de {MAX_REDIRECTS} redirections")


def http_get(url: str, **kwargs) -> requests.Response:
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    allow_redirects = kwargs.pop("allow_redirects", True)
    for attempt in range(RETRIES + 1):
        try:
            resp = _get(url, headers, allow_redirects, **kwargs)
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
