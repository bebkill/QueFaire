"""Sélection du LLM utilisé pour l'extraction (html, clarify, discover) :
principal + bascule automatique sur un backup en cas de quota épuisé.

Deux chaînes indépendantes :
- CRAWL   : QUEFAIRE_LLM (principal) + QUEFAIRE_LLM2 (backups) — extraction
  html/social et discover.
- CLARIFY : QUEFAIRE_LLM_CLARIFY (+ QUEFAIRE_LLM_CLARIFY2) — dédié à clarify,
  pour lui donner son propre quota (ex. crawl sur deepseek, clarify sur
  mistral) et ne pas piocher dans le budget de l'extraction. Si non défini,
  clarify réutilise la chaîne CRAWL (budget partagé).

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

Au premier appel d'une chaîne, un test de connexion minimal (un seul échange
court) départage : le principal est utilisé si le quota répond, sinon on
bascule sur le backup suivant. La décision est mise en cache pour tout le run
— pas de retest par source.

Le quota peut aussi mourir EN COURS de run (vécu : le palier gratuit Gemini
est de 20 requêtes/jour — le test de connexion passe, puis le quota s'épuise
quelques sources plus loin). D'où `run_llm()` : chaque appel passe par lui, et
une erreur de quota/limite déclasse le provider courant et rejoue l'appel sur
le candidat suivant. Une réponse vide bascule aussi, mais pour cet appel
seulement (sans déclasser). Les consommateurs sans outils (html, social,
clarify) doivent utiliser run_llm() plutôt que get_agent().run().
"""

from __future__ import annotations

import logging
import os
import re

log = logging.getLogger("quefaire")

_QUOTA_RE = re.compile(r"429|quota|rate.?limit|resource.?exhausted", re.I)
# Erreurs serveur transitoires (surcharge/indispo/timeout) : récupérables en
# rejouant l'appel sur un autre provider, mais SANS déclasser (elles passent
# souvent au coup d'après). Vécu : Gemini renvoie des HTTP 503 par salves et
# faisait perdre la source (ex. balcons-dauphine) faute de bascule.
_TRANSIENT_RE = re.compile(r"HTTP\s*5\d\d|overloaded|unavailable|timeout|timed out|temporarily", re.I)

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


def is_quota_error(exc: BaseException) -> bool:
    """Erreur de quota/limite de débit (récupérable en changeant de provider)."""
    return bool(_QUOTA_RE.search(str(exc)))


def is_transient_error(exc: BaseException) -> bool:
    """Erreur serveur transitoire (5xx, surcharge, timeout) — à rejouer sur un
    autre provider pour cet appel, sans déclasser le provider courant."""
    return bool(_TRANSIENT_RE.search(str(exc)))


def _make_agent(provider: str, model: str):
    """Instancie un Agent autoagent-core pour (provider, modèle).

    Provider natif → Agent.from_model (clé résolue par autoagent). Provider
    OpenAI-compatible non natif (mistral, zai, kimi) → adaptateur openai avec
    base_url et clé dédiée, car from_model ne permet pas de fixer base_url.
    """
    from autoagent import Agent

    # temperature=0 : extraction/clarify déterministes autant que possible, pour
    # que deux crawls rapprochés donnent le même résultat (répétabilité).
    preset = _OPENAI_COMPATIBLE.get(provider.lower())
    if preset is None:
        return Agent.from_model(provider, model, temperature=0)

    from autoagent import ModelConfig, create_provider

    base_url, api_key_env = preset
    config = ModelConfig(
        provider="openai", model=model, base_url=base_url, api_key_env=api_key_env
    )
    return Agent(create_provider(config), temperature=0)


def _blank(result) -> bool:
    """Réponse LLM vide/blanche. Ce n'est jamais une réponse valide : le
    modèle doit au minimum rendre `[]` / `{}`. Vécu avec gemini-3.5-flash sur
    les grosses pages agenda (sortie tronquée → complétion vide) — l'appel
    « réussit » mais ne rend rien, ce qui produisait silencieusement 0 fiche."""
    return not (getattr(result, "output", "") or "").strip()


def _test(provider: str, model: str) -> bool:
    """Échange minimal pour vérifier que le provider répond (auth + quota).

    Coût négligeable (quelques dizaines de tokens) comparé à la source
    d'extraction qu'on évite de gâcher sur un quota déjà mort.
    """
    try:
        result = _make_agent(provider, model).run("Réponds uniquement par le mot ok.")
        return bool(result.output)
    except Exception as exc:
        log.warning("[llm] %s:%s indisponible : %s", provider, model, exc)
        return False


class _Chain:
    """Une chaîne de candidats LLM (principal + backups) avec sélection en
    cache, bascule sur quota et récupération sur réponse vide.

    L'état (décision résolue, providers déclassés) est propre à la chaîne :
    deux chaînes (crawl, clarify) n'entament donc pas le même budget.
    """

    def __init__(self, env_vars: tuple[str, ...]):
        self.env_vars = env_vars
        self._resolved: tuple[str, str] | None = None
        self._resolution_done = False
        self._failed: set[str] = set()  # specs déclassés pour ce run

    def configured(self) -> bool:
        return any((os.environ.get(v) or "").strip() for v in self.env_vars)

    def _all_specs(self) -> list[str]:
        """Specs ordonnés des variables de la chaîne, chacune pouvant lister
        plusieurs providers séparés par des virgules. Doublons ôtés (1er gardé)."""
        specs: list[str] = []
        for var in self.env_vars:
            for spec in (os.environ.get(var) or "").split(","):
                spec = spec.strip()
                if spec and spec not in specs:
                    specs.append(spec)
        return specs

    def _candidates(self) -> list[str]:
        return [s for s in self._all_specs() if s not in self._failed]

    def available(self) -> bool:
        """Chaîne potentiellement utilisable (config présente + lib installée).
        Ne dit rien sur le quota — voir resolve() pour le test réel."""
        if not self._candidates():
            return False
        try:
            import autoagent  # noqa: F401

            return True
        except ImportError:
            return False

    def healthy(self) -> bool:
        """True tant qu'aucun provider n'a été déclassé (quota mort) ce run.
        Une réponse vide isolée ne déclasse pas, donc n'entame pas ce budget."""
        return not self._failed

    def _demote(self, spec: str) -> None:
        """Déclasse un provider pour le reste du run et invalide la décision."""
        self._failed.add(spec)
        self._resolved = None
        self._resolution_done = False

    def resolve(self) -> tuple[str, str] | None:
        """(provider, model) à utiliser en ce moment, ou None si aucun dispo.

        Résultat mis en cache : un seul test de connexion par provider et par
        run, quel que soit le nombre de sources. Réévalué seulement si un
        provider est déclassé."""
        if self._resolution_done:
            return self._resolved
        self._resolution_done = True

        if not self.available():
            self._resolved = None
            return None

        specs = self._all_specs()
        primary = specs[0] if specs else None
        for spec in self._candidates():
            provider, _, model = spec.partition(":")
            label = "principal" if spec == primary else "backup"
            if _test(provider, model):
                log.info("[llm] %s (%s) sélectionné", spec, label)
                self._resolved = (provider, model)
                return self._resolved
            log.warning("[llm] %s (%s) indisponible, tentative suivante…", spec, label)

        log.error("[llm] aucun LLM disponible (principal et backup épuisés ou en erreur)")
        self._resolved = None
        return None

    def _no_llm(self) -> RuntimeError:
        return RuntimeError(f"Aucun LLM disponible ({' / '.join(self.env_vars)})")

    def _try_alternates(self, spec: str, prompt: str):
        """Rejoue l'appel sur les autres candidats (pour cet appel seulement,
        sans déclasser le provider courant). Renvoie la 1re réponse non vide,
        ou None si aucun secours n'aboutit."""
        for alt in self._candidates():
            if alt == spec:
                continue
            alt_provider, _, alt_model = alt.partition(":")
            try:
                alt_result = _make_agent(alt_provider, alt_model).run(prompt)
            except Exception as exc:
                if is_quota_error(exc):
                    log.warning("[llm] secours %s : quota épuisé, déclassé (%s)", alt, exc)
                    self._demote(alt)
                else:
                    log.warning("[llm] secours %s indisponible : %s", alt, exc)
                continue
            if not _blank(alt_result):
                log.info("[llm] secours %s a répondu pour cet appel", alt)
                return alt_result
        return None

    def run(self, prompt: str):
        """agent.run(prompt) avec trois garde-fous.

        - Erreur de quota (429, rate limit…) : déclasse le provider courant
          pour tout le reste du run et rejoue sur le candidat suivant.
        - Erreur serveur transitoire (5xx, surcharge, timeout) : rejoue l'appel
          sur les autres candidats pour cet appel, SANS déclasser (elle passe
          souvent au coup d'après) ; à défaut, l'erreur remonte.
        - Réponse vide : idem (secours pour cet appel, sans déclasser) ; si tous
          rendent du vide, la réponse vide est renvoyée et l'appelant gère.

        Les autres erreurs remontent. Lève RuntimeError si plus aucun LLM."""
        while True:
            resolved = self.resolve()
            if resolved is None:
                raise self._no_llm()
            provider, model = resolved
            spec = f"{provider}:{model}"
            try:
                result = _make_agent(provider, model).run(prompt)
            except Exception as exc:
                if is_quota_error(exc):
                    log.warning(
                        "[llm] quota épuisé sur %s en cours de run — bascule sur le candidat suivant (%s)",
                        spec, exc,
                    )
                    self._demote(spec)
                    continue
                if is_transient_error(exc):
                    log.warning(
                        "[llm] erreur transitoire sur %s (%s) — essai des candidats de secours pour cet appel",
                        spec, exc,
                    )
                    alt = self._try_alternates(spec, prompt)
                    if alt is not None:
                        return alt
                raise

            if not _blank(result):
                return result

            log.warning(
                "[llm] réponse vide de %s — essai des candidats de secours pour cet appel", spec
            )
            alt = self._try_alternates(spec, prompt)
            return alt if alt is not None else result

    def agent(self):
        """Objet Agent avec le LLM résolu (usages @agent.tool, sans bascule)."""
        resolved = self.resolve()
        if resolved is None:
            raise self._no_llm()
        provider, model = resolved
        return _make_agent(provider, model)


# Chaîne d'extraction (crawl/discover) et chaîne dédiée à clarify.
_CRAWL = _Chain(("QUEFAIRE_LLM", "QUEFAIRE_LLM2"))
_CLARIFY = _Chain(("QUEFAIRE_LLM_CLARIFY", "QUEFAIRE_LLM_CLARIFY2"))


def clarify_chain() -> _Chain:
    """Chaîne à utiliser pour clarify : la chaîne dédiée si QUEFAIRE_LLM_CLARIFY
    est défini, sinon la chaîne du crawl (budget partagé)."""
    return _CLARIFY if _CLARIFY.configured() else _CRAWL


# --- API publique : opère sur la chaîne d'extraction (crawl) -----------------
def llm_available() -> bool:
    return _CRAWL.available()


def resolve() -> tuple[str, str] | None:
    return _CRAWL.resolve()


def run_llm(prompt: str):
    return _CRAWL.run(prompt)


def get_agent():
    return _CRAWL.agent()


def budget_healthy() -> bool:
    return _CRAWL.healthy()
