"""Sélection du LLM utilisé pour l'extraction (html, clarify, discover) :
principal + bascule automatique sur un backup en cas de quota épuisé.

- QUEFAIRE_LLM  : provider principal (ex: "gemini:gemini-3.5-flash")
- QUEFAIRE_LLM2 : backup optionnel (ex: "deepseek:deepseek-v4-flash")

Au premier appel du run, un test de connexion minimal (un seul échange court)
départage : le principal est utilisé si le quota répond, sinon on bascule sur
le backup. La décision est mise en cache pour tout le run — pas de retest par
source.

Le quota peut aussi mourir EN COURS de run (vécu : le palier gratuit Gemini
est de 20 requêtes/jour — le test de connexion passe, puis le quota s'épuise
quelques sources plus loin). D'où `run_llm()` : chaque appel passe par lui, et
une erreur de quota/limite déclasse le provider courant et rejoue l'appel sur
le candidat suivant. Les consommateurs sans outils (html, social, clarify)
doivent utiliser run_llm() plutôt que get_agent().run().
"""

from __future__ import annotations

import logging
import os
import re

log = logging.getLogger("quefaire")

_resolved: tuple[str, str] | None = None
_resolution_done = False
_failed: set[str] = set()  # specs "provider:modèle" déclassés pour ce run

_QUOTA_RE = re.compile(r"429|quota|rate.?limit|resource.?exhausted", re.I)


def is_quota_error(exc: BaseException) -> bool:
    """Erreur de quota/limite de débit (récupérable en changeant de provider)."""
    return bool(_QUOTA_RE.search(str(exc)))


def _candidates() -> list[str]:
    return [
        v
        for v in (os.environ.get("QUEFAIRE_LLM"), os.environ.get("QUEFAIRE_LLM2"))
        if v and v not in _failed
    ]


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


def _demote(spec: str) -> None:
    """Déclasse un provider pour le reste du run et invalide la décision."""
    global _resolved, _resolution_done
    _failed.add(spec)
    _resolved = None
    _resolution_done = False


def resolve() -> tuple[str, str] | None:
    """(provider, model) à utiliser en ce moment, ou None si aucun LLM dispo.

    Résultat mis en cache : un seul test de connexion par provider et par
    process, quel que soit le nombre de sources html/clarify/discover.
    La décision n'est réévaluée que si run_llm() déclasse le provider courant.
    """
    global _resolved, _resolution_done
    if _resolution_done:
        return _resolved
    _resolution_done = True

    if not llm_available():
        _resolved = None
        return None

    for spec in _candidates():
        provider, _, model = spec.partition(":")
        label = "principal" if spec == os.environ.get("QUEFAIRE_LLM") else "backup"
        if _test(provider, model):
            log.info("[llm] %s (%s) sélectionné", spec, label)
            _resolved = (provider, model)
            return _resolved
        log.warning("[llm] %s (%s) indisponible, tentative suivante…", spec, label)

    log.error("[llm] aucun LLM disponible (principal et backup épuisés ou en erreur)")
    _resolved = None
    return None


def run_llm(prompt: str):
    """agent.run(prompt) avec bascule automatique si le quota meurt en route.

    Une erreur de quota (429, rate limit…) déclasse le provider courant pour
    le reste du run et rejoue l'appel sur le candidat suivant. Les autres
    erreurs remontent telles quelles. Lève RuntimeError quand plus aucun LLM
    ne répond.
    """
    while True:
        resolved = resolve()
        if resolved is None:
            raise RuntimeError("Aucun LLM disponible (QUEFAIRE_LLM / QUEFAIRE_LLM2)")
        provider, model = resolved
        from autoagent import Agent

        try:
            return Agent.from_model(provider, model).run(prompt)
        except Exception as exc:
            if not is_quota_error(exc):
                raise
            spec = f"{provider}:{model}"
            log.warning(
                "[llm] quota épuisé sur %s en cours de run — bascule sur le candidat suivant (%s)",
                spec, exc,
            )
            _demote(spec)


def get_agent():
    """Instancie un Agent autoagent-core avec le LLM résolu en ce moment.

    Réservé aux usages qui exigent l'objet Agent (outils @agent.tool, comme
    discovery) : pas de bascule en cours d'exécution — préférer run_llm()
    partout ailleurs. Lève RuntimeError si aucun LLM n'est disponible.
    """
    resolved = resolve()
    if resolved is None:
        raise RuntimeError("Aucun LLM disponible (QUEFAIRE_LLM / QUEFAIRE_LLM2)")
    from autoagent import Agent

    provider, model = resolved
    return Agent.from_model(provider, model)
