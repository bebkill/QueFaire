"""Sélection du LLM utilisé pour l'extraction (html, clarify, discover) :
principal + bascule automatique sur un backup en cas de quota épuisé.

- QUEFAIRE_LLM  : provider principal (ex: "gemini:gemini-3.5-flash")
- QUEFAIRE_LLM2 : backup(s) optionnel(s)

Chaque variable accepte **une liste ordonnée séparée par des virgules** : on
peut donc empiler plusieurs backups sans multiplier les variables d'env, par
exemple `QUEFAIRE_LLM2="deepseek:deepseek-v4-flash,groq:llama-3.3-70b-versatile,
mistral:mistral-small-latest"`. La chaîne complète (principal puis backups, dans
l'ordre) est essayée jusqu'au premier provider qui répond.

Providers reconnus dans un spec `provider:modèle` :
- natifs autoagent-core : openai, anthropic, deepseek, gemini, groq
  (clés respectives OPENAI_API_KEY, ANTHROPIC_API_KEY, DEEPSEEK_API_KEY,
  GEMINI_API_KEY, GROQ_API_KEY) ;
- OpenAI-compatibles branchés par nous via l'adaptateur openai + base_url :
  mistral (MISTRAL_API_KEY), zai (ZAI_API_KEY), kimi/moonshot
  (MOONSHOT_API_KEY) — voir _OPENAI_COMPATIBLE.

Au premier appel du run, un test de connexion minimal (un seul échange court)
départage : le principal est utilisé si le quota répond, sinon on bascule sur
le backup suivant. La décision est mise en cache pour tout le run — pas de
retest par source.

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

# Providers OpenAI-compatibles absents du cœur autoagent-core (qui ne connaît
# nativement que openai/anthropic/deepseek/gemini/groq) : on les sert via
# l'adaptateur openai en surchargeant base_url et la variable de clé.
#   alias -> (base_url, variable d'environnement pour la clé)
_OPENAI_COMPATIBLE = {
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "zai": ("https://api.z.ai/api/paas/v4", "ZAI_API_KEY"),
    "kimi": ("https://api.moonshot.ai/v1", "MOONSHOT_API_KEY"),
    "moonshot": ("https://api.moonshot.ai/v1", "MOONSHOT_API_KEY"),
}


def _make_agent(provider: str, model: str):
    """Instancie un Agent autoagent-core pour (provider, modèle).

    Provider natif → Agent.from_model (clé résolue par autoagent). Provider
    OpenAI-compatible non natif (mistral, zai, kimi) → adaptateur openai avec
    base_url et clé dédiée, car from_model ne permet pas de fixer base_url.
    """
    from autoagent import Agent

    preset = _OPENAI_COMPATIBLE.get(provider.lower())
    if preset is None:
        return Agent.from_model(provider, model)

    from autoagent import ModelConfig, create_provider

    base_url, api_key_env = preset
    config = ModelConfig(
        provider="openai", model=model, base_url=base_url, api_key_env=api_key_env
    )
    return Agent(create_provider(config))


def is_quota_error(exc: BaseException) -> bool:
    """Erreur de quota/limite de débit (récupérable en changeant de provider)."""
    return bool(_QUOTA_RE.search(str(exc)))


def _all_specs() -> list[str]:
    """Chaîne ordonnée des specs : QUEFAIRE_LLM puis QUEFAIRE_LLM2, chacun
    pouvant lister plusieurs providers séparés par des virgules. Doublons ôtés
    en gardant le premier."""
    specs: list[str] = []
    for var in ("QUEFAIRE_LLM", "QUEFAIRE_LLM2"):
        for spec in (os.environ.get(var) or "").split(","):
            spec = spec.strip()
            if spec and spec not in specs:
                specs.append(spec)
    return specs


def _candidates() -> list[str]:
    return [s for s in _all_specs() if s not in _failed]


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
    try:
        agent = _make_agent(provider, model)
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

    specs = _all_specs()
    primary = specs[0] if specs else None
    for spec in _candidates():
        provider, _, model = spec.partition(":")
        label = "principal" if spec == primary else "backup"
        if _test(provider, model):
            log.info("[llm] %s (%s) sélectionné", spec, label)
            _resolved = (provider, model)
            return _resolved
        log.warning("[llm] %s (%s) indisponible, tentative suivante…", spec, label)

    log.error("[llm] aucun LLM disponible (principal et backup épuisés ou en erreur)")
    _resolved = None
    return None


def _blank(result) -> bool:
    """Réponse LLM vide/blanche. Ce n'est jamais une réponse valide : le
    modèle doit au minimum rendre `[]` / `{}`. Vécu avec gemini-3.5-flash sur
    les grosses pages agenda (sortie tronquée → complétion vide) — l'appel
    « réussit » mais ne rend rien, ce qui produisait silencieusement 0 fiche."""
    return not (getattr(result, "output", "") or "").strip()


def budget_healthy() -> bool:
    """True tant qu'aucun provider n'a été déclassé (quota mort) pendant ce run.

    Signal pour les consommateurs « confort » (clarify) : inutile de dépenser
    un quota déjà tendu sur du non-essentiel. Une réponse vide isolée ne
    déclasse pas (voir run_llm), donc n'entame pas ce budget."""
    return not _failed


def run_llm(prompt: str):
    """agent.run(prompt) avec deux garde-fous.

    - Erreur de quota (429, rate limit…) : déclasse le provider courant pour
      tout le reste du run et rejoue l'appel sur le candidat suivant.
    - Réponse vide : rejoue le MÊME appel sur les autres candidats, pour cet
      appel seulement et sans déclasser (le vide est souvent propre à la page —
      sortie trop longue —, le provider reste bon pour les autres sources) ;
      si tous rendent du vide, la réponse vide est renvoyée telle quelle et
      l'appelant gère (0 fiche).

    Les autres erreurs remontent telles quelles. Lève RuntimeError quand plus
    aucun LLM ne répond (quota).
    """
    while True:
        resolved = resolve()
        if resolved is None:
            raise RuntimeError("Aucun LLM disponible (QUEFAIRE_LLM / QUEFAIRE_LLM2)")
        provider, model = resolved
        spec = f"{provider}:{model}"
        try:
            result = _make_agent(provider, model).run(prompt)
        except Exception as exc:
            if not is_quota_error(exc):
                raise
            log.warning(
                "[llm] quota épuisé sur %s en cours de run — bascule sur le candidat suivant (%s)",
                spec, exc,
            )
            _demote(spec)
            continue

        if not _blank(result):
            return result

        log.warning(
            "[llm] réponse vide de %s — essai des candidats de secours pour cet appel", spec
        )
        for alt in _candidates():
            if alt == spec:
                continue
            alt_provider, _, alt_model = alt.partition(":")
            try:
                alt_result = _make_agent(alt_provider, alt_model).run(prompt)
            except Exception as exc:
                if is_quota_error(exc):
                    log.warning("[llm] secours %s : quota épuisé, déclassé (%s)", alt, exc)
                    _demote(alt)
                else:
                    log.warning("[llm] secours %s indisponible : %s", alt, exc)
                continue
            if not _blank(alt_result):
                log.info("[llm] secours %s a répondu pour cet appel", alt)
                return alt_result
        return result


def get_agent():
    """Instancie un Agent autoagent-core avec le LLM résolu en ce moment.

    Réservé aux usages qui exigent l'objet Agent (outils @agent.tool, comme
    discovery) : pas de bascule en cours d'exécution — préférer run_llm()
    partout ailleurs. Lève RuntimeError si aucun LLM n'est disponible.
    """
    resolved = resolve()
    if resolved is None:
        raise RuntimeError("Aucun LLM disponible (QUEFAIRE_LLM / QUEFAIRE_LLM2)")

    provider, model = resolved
    return _make_agent(provider, model)
