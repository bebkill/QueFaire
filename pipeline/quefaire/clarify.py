"""Fiches « en clair » : lever l'ambiguïté des titres d'événements.

Exemple vécu : « Cet été, faites-vous une terrasse » ressemble à un atelier
bricolage… mais ce sont des dîners en terrasse. Un agent LLM (autoagent-core)
relit chaque lot d'événements et, pour ceux dont le titre ne dit pas
clairement de quoi il s'agit, produit une phrase limpide affichée en tête de
fiche (champ `tldr`).

Optionnel : nécessite QUEFAIRE_LLM (+ backup QUEFAIRE_LLM2) et autoagent-core
(comme html_llm). Sans LLM, le pipeline saute cette étape sans erreur.
"""

from __future__ import annotations

import json
import logging
import re

from .models import Event

log = logging.getLogger("quefaire")

BATCH_SIZE = 25

PROMPT = """Voici des événements locaux (id, titre, description). Pour CHAQUE événement dont le
titre seul ne permet pas de comprendre immédiatement et sans ambiguïté de quoi il
s'agit, écris UNE phrase courte, concrète et factuelle qui l'explique (commençant
par un verbe ou un nom, pas de « Cet événement est... »). Ne réponds rien pour les
événements dont le titre est déjà limpide. N'invente aucun détail absent de la
description.

Réponds UNIQUEMENT en JSON : un objet {{"<id>": "<phrase>", ...}} (objet vide {{}} si
tout est limpide).

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


def clarify(events: list[Event]) -> list[Event]:
    """Remplit event.tldr pour les titres ambigus. No-op sans LLM configuré."""
    from .llm import clarify_chain

    chain = clarify_chain()
    if not chain.available():
        log.info("[clarify] sauté : pas de LLM configuré")
        return events
    # clarify est du confort (phrase « en clair »), pas des événements. Avec un
    # modèle dédié (QUEFAIRE_LLM_CLARIFY), il a son propre budget. Sinon il
    # partage la chaîne du crawl : dans ce cas, si une bascule quota a déjà eu
    # lieu ce run, on le saute pour préserver le quota restant.
    if not chain.healthy():
        log.info(
            "[clarify] sauté : bascule LLM déjà survenue sur la chaîne — on préserve le quota restant"
        )
        return events

    todo = [e for e in events if not e.tldr]
    done = 0
    for i in range(0, len(todo), BATCH_SIZE):
        batch = todo[i : i + BATCH_SIZE]
        items = "\n".join(
            f"- id: {e.id}\n  titre: {e.title}\n  description: {e.description[:300]}"
            for e in batch
        )
        try:
            # chain.run : bascule automatiquement sur le backup si le quota meurt ici.
            result = chain.run(PROMPT.format(items=items))
            mapping = _extract_json(result.output)
        except RuntimeError as exc:
            log.warning("[clarify] sauté : %s", exc)
            return events
        except Exception as exc:
            log.error("[clarify] lot %d : %s", i // BATCH_SIZE, exc)
            continue
        by_id = {e.id: e for e in batch}
        for event_id, sentence in mapping.items():
            ev = by_id.get(event_id)
            if ev and isinstance(sentence, str) and 10 < len(sentence) < 300:
                ev.tldr = sentence.strip()
                done += 1
    log.info("[clarify] %d fiches clarifiées sur %d événements", done, len(events))
    return events
