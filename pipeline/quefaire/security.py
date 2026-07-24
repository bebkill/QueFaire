"""Contrôles de sécurité pour l'accès à des URLs soumises par des tiers.

Le module « proposer une source » laisse un utilisateur coller un lien : cette
URL est potentiellement hostile. On la valide AVANT tout accès réseau, et le
fetch garde-fou (base.http_get sous `set_ssrf_guard(True)`) suit les
redirections à la main en revalidant chaque saut.

Menaces couvertes :
- **SSRF** : accès à des services internes / cloud metadata (169.254.169.254),
  loopback, réseaux privés → toutes les IP résolues doivent être publiques.
- **Schémas dangereux** : file://, gopher://, data:… → http/https uniquement.
- **Identifiants dans l'URL** (user:pass@host) → refusés.
- **Ports détournés** vers des services internes → 80/443 uniquement.

Le contenu récupéré n'est JAMAIS exécuté : il n'est que du texte passé au LLM
pour extraction. Limite connue : le rebinding DNS (TOCTOU entre la validation
et la connexion de requests) n'est pas totalement couvert sans épingler l'IP ;
le blocage systématique des IP privées/réservées en réduit fortement l'impact.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_PORTS = {None, 80, 443}


class UnsafeUrlError(ValueError):
    """URL refusée par les contrôles de sécurité (SSRF, schéma, port…)."""


def _is_public_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # inclut 169.254.0.0/16 (métadonnées cloud)
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_public_url(url: str) -> None:
    """Lève UnsafeUrlError si l'URL n'est pas sûre à récupérer. No-op sinon."""
    parts = urlsplit((url or "").strip())

    if parts.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"schéma non autorisé : {parts.scheme or '(vide)'} — http/https requis")
    if parts.username or parts.password:
        raise UnsafeUrlError("identifiants dans l'URL non autorisés")

    host = parts.hostname
    if not host:
        raise UnsafeUrlError("hôte manquant dans l'URL")

    try:
        port = parts.port
    except ValueError:
        raise UnsafeUrlError("port invalide") from None
    if port not in _ALLOWED_PORTS:
        raise UnsafeUrlError(f"port non autorisé : {port} — 80/443 uniquement")

    resolve_port = port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, resolve_port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"résolution DNS impossible : {exc}") from None

    ips = {info[4][0] for info in infos}
    if not ips:
        raise UnsafeUrlError("aucune IP résolue")
    for ip in ips:
        if not _is_public_ip(ip):
            raise UnsafeUrlError(f"cible interne bloquée (anti-SSRF) : {host} → {ip}")
