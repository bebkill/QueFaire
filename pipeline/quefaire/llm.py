"""Sélection du LLM utilisé pour l'extraction (html, clarify, discover) :
principal + bascule automatique sur un backup en cas de quota épuisé.

- QUEFAIRE_LLM  : provider principal (ex: "gemini:gemini-3.5-flash")
- QUEFAIRE_LLM2 : backup optionnel (ex: "deepseek:deepseek-v4-flash")

Au premier appel du run, un test de connexion minimal (un seul échange court)
départage : le principal est utilisé si le quota répond, sinon on bascule sur
le backup. La décision est mise en cache pour tout le run — pas de retest par
source, donc pas de surcoût sur le budget qu'on vient justement d'épargner.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("quefaire")

_resolved: tuple[str, str] | None = None
_resolution_done = False


def _candidates() -> list[str]:
    return [v for v in (os.environ.get("QUEFAIRE_LLM"), os.environ.get("QUEFAIRE_LLM2")) if v]


def llm_available() -> bool:
    """Un LLM est potentiellement utilisable (config présente + lib installée).

    Ne dit rien sur le quota — voir resolve() pour le test réel.
    """
    if not _candidates():
        return False
    try:
        import autoagent  # noqa: F401

        return True
    except ImportError:
        return False


def _test(provider: str, model: str) -> bool:
    """Échange minimal pour vérifier que le provider répond (auth + quota).

    Coût négligeable (quelques dizaines de tokens) comparé à la source
    d'extraction qu'on évite de gâcher sur un quota déjà mort.
    """
    from autoagent import Agent

    try:
        agent = Agent.from_model(provider, model)
        result = agent.run("Réponds uniquement par le mot ok.")
        return bool(result.output)
    except Exception as exc:
        log.warning("[llm] %s:%s indisponible : %s", provider, model, exc)
        return False


def resolve() -> tuple[str, str] | None:
    """(provider, model) à utiliser pour ce run, ou None si aucun LLM dispo.

    Résultat mis en cache : un seul test de connexion par process, quel que
    soit le nombre de sources html/clarify/discover qui l'utilisent ensuite.
    """
    global _resolved, _resolution_done
    if _resolution_done:
        return _resolved
    _resolution_done = True

    if not llm_available():
        _resolved = None
        return None

    candidates = _candidates()
    for i, spec in enumerate(candidates):
        provider, _, model = spec.partition(":")
        label = "principal" if i == 0 else f"backup {i}"
        if _test(provider, model):
            log.info("[llm] %s (%s) sélectionné pour ce run", spec, label)
            _resolved = (provider, model)
            return _resolved
        log.warning("[llm] %s (%s) indisponible, tentative suivante…", spec, label)

    log.error("[llm] aucun LLM disponible (principal et backup épuisés ou en erreur)")
    _resolved = None
    return None


def get_agent():
    """Instancie un Agent autoagent-core avec le LLM résolu pour ce run.

    Lève RuntimeError si aucun LLM n'est disponible — les appelants doivent
    avoir vérifié resolve() (ou llm_available(), en amont, pour un skip
    propre) avant d'appeler cette fonction.
    """
    resolved = resolve()
    if resolved is None:
        raise RuntimeError("Aucun LLM disponible (QUEFAIRE_LLM / QUEFAIRE_LLM2)")
    from autoagent import Agent

    provider, model = resolved
    return Agent.from_model(provider, model)
