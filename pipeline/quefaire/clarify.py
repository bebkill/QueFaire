"""Fiches « en clair » : lever l'ambiguïté des titres d'événements.

Exemple vécu : « Cet été, faites-vous une terrasse » ressemble à un atelier
bricolage… mais ce sont des dîners en terrasse. Un agent LLM (autoagent-core)
relit les événements et, pour ceux dont le titre ne dit pas clairement de quoi
il s'agit, produit une phrase limpide affichée en tête de fiche (champ `tldr`).

La phrase n'a d'intérêt que si elle **apporte** de la compréhension : une simple
paraphrase du titre ou de la description n'aide pas le visiteur. On l'écarte
donc (prompt sélectif + filtre anti-redondance `_adds_value`).

Optionnel : nécessite un LLM (`QUEFAIRE_LLM_CLARIFY` dédié, sinon la chaîne du
crawl) et autoagent-core. Sans LLM, l'étape est sautée sans erreur. Les
clarifications sont mises en cache par contenu (titre+description) : rejouées à
l'identique et sans rappeler le LLM d'un crawl à l'autre.
"""

from __future__ import annotations

import json
import logging
import re

from .cache import cache
from .models import Event
from .normalize import fold

log = logging.getLogger("quefaire")

BATCH_SIZE = 25

PROMPT = """Voici des événements locaux (id, titre, description). Un titre est souvent clair à
lui seul. N'écris une phrase QUE pour les événements dont le titre est ambigu,
cryptique ou trompeur (jeu de mots, nom propre, référence obscure) et où le
visiteur ne devinerait PAS de quoi il s'agit.

La phrase doit APPORTER une information qui n'est pas déjà évidente dans le titre
ni dans la description : dis concrètement ce qu'est l'activité (ce qu'on y fait,
pour qui). INTERDITS : reformuler ou paraphraser le titre, répéter la
description, rester vague. Si le titre est déjà limpide, ne réponds RIEN pour cet
événement. N'invente aucun détail absent de la description.

Réponds UNIQUEMENT en JSON : un objet {{"<id>": "<phrase>", ...}} (objet vide {{}}
si tout est clair).

ÉVÉNEMENTS :
{items}
"""


def _extract_json(raw: str) -> dict:
    raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)  # dernier recours : premier objet trouvé
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def _adds_value(tldr: str, title: str, description: str) -> bool:
    """Vrai si la phrase apporte vraiment de l'info, faux si elle recopie le
    titre/la description. Heuristique : une part suffisante de ses mots pleins
    doit être NOUVELLE par rapport au titre + description."""
    if not (10 < len(tldr) < 300):
        return False
    src = {w for w in fold(f"{title} {description}").split() if len(w) > 3}
    words = [w for w in fold(tldr).split() if len(w) > 3]
    if len(words) < 3:
        return False  # trop court pour être une explication
    novel = sum(1 for w in words if w not in src)
    return novel / len(words) >= 0.35


def clarify(events: list[Event]) -> list[Event]:
    """Remplit event.tldr pour les titres ambigus (cache + LLM). No-op sans LLM."""
    from .llm import clarify_chain

    chain = clarify_chain()
    todo = [e for e in events if not e.tldr]

    # 1) Servir depuis le cache ce qui est déjà connu (déterministe, zéro quota).
    #    Valeur "" = déjà évalué, aucune clarification utile → ne pas redemander.
    misses: list[tuple[Event, str]] = []
    for e in todo:
        ckey = cache.key("clarify", e.title, e.description[:300])
        val = cache.get(ckey)
        if val is None:
            misses.append((e, ckey))
        elif val:
            e.tldr = val

    # 2) N'appeler le LLM que sur les nouveaux, si un budget est disponible.
    if misses and chain.available() and chain.healthy():
        for i in range(0, len(misses), BATCH_SIZE):
            batch = misses[i : i + BATCH_SIZE]
            items = "\n".join(
                f"- id: {j}\n  titre: {e.title}\n  description: {e.description[:300]}"
                for j, (e, _) in enumerate(batch)
            )
            try:
                result = chain.run(PROMPT.format(items=items))
                mapping = _extract_json(result.output)
            except RuntimeError as exc:
                log.warning("[clarify] interrompu : %s", exc)
                break
            except Exception as exc:
                log.error("[clarify] lot %d : %s", i // BATCH_SIZE, exc)
                continue
            for j, (e, ckey) in enumerate(batch):
                sentence = mapping.get(str(j)) or mapping.get(j) or ""
                sentence = sentence.strip() if isinstance(sentence, str) else ""
                if sentence and _adds_value(sentence, e.title, e.description):
                    e.tldr = sentence
                    cache.put(ckey, sentence)
                else:
                    cache.put(ckey, "")  # rien d'utile : mémorisé pour ne pas rappeler
    elif misses:
        log.info("[clarify] %d événements non clarifiés (pas de budget LLM) — cache servi", len(misses))

    done = sum(1 for e in events if e.tldr)
    log.info("[clarify] %d fiches enrichies sur %d événements", done, len(events))
    return events
