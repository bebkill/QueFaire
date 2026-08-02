"""Enrichissement heuristique : catégorie, public, gratuité.

Fonctionne sans LLM, sur le texte de l'événement. Volontairement simple :
des règles lisibles plutôt qu'un modèle — auditables et ajustables par secteur.
"""

from __future__ import annotations

import re
import unicodedata

from .models import Event

CATEGORY_RULES: list[tuple[str, str]] = [
    ("concert", r"concert|musique|musical|chorale|dj |recital|jazz|rock|rap\b"),
    ("festival", r"festival|les nuits |estivales"),
    ("spectacle", r"spectacle|theatre|danse|cirque|humour|improvisation|opera|ballet"),
    ("cinema", r"cinema|cine[ -]|projection|film|seance plein air"),
    ("expo", r"exposition|expo\b|vernissage|galerie|musee"),
    ("marche", r"marche (nocturne|de noel|des producteurs|artisanal)|braderie|brocante|vide[ -]grenier|foire|degustation|terroir"),
    ("nature", r"randonnee|rando\b|balade|sortie nature|observation|faune|flore|sentier|lac\b|montagne"),
    ("sport", r"tournoi|match|course|trail|marathon|competition|sportif|velo|cyclo|escalade|ski|natation"),
    ("atelier", r"atelier|stage\b|initiation|cours de|formation|do it yourself"),
    ("patrimoine", r"patrimoine|visite guidee|chateau|eglise|abbaye|historique|journees europeennes"),
    ("jeunesse", r"enfants?\b|jeunesse|famille|contes? pour|marionnettes|ludotheque|chasse au tresor"),
    ("conference", r"conference|rencontre avec|debat|table ronde|cafe philo|lecture publique"),
    ("fete", r"fete (de|du|des|votive|foraine)|carnaval|feu d'artifice|14 juillet|beaujolais|telethon"),
]

AUDIENCE_RULES: list[tuple[str, str]] = [
    ("famille", r"famille|familial|parents|tout[- ]petits|des \d+ ans"),
    ("enfants", r"enfants?\b|jeune public|3[- ]12 ans|marionnettes|conte"),
    ("ados", r"ados?\b|adolescents?|jeunes\b"),
    ("seniors", r"seniors?|aines\b|3e age"),
    ("tous", r"tout public|ouvert a tous|pour tous"),
]

FREE_RE = re.compile(r"gratuit|entree libre|acces libre|libre participation")
PRICE_RE = re.compile(r"(\d+(?:[.,]\d{1,2})?)\s?(?:€|euros?)")


def fold(text: str) -> str:
    return (
        unicodedata.normalize("NFKD", text or "")
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )


def enrich(event: Event) -> Event:
    raw = f"{event.title} {event.description} {event.price_text or ''}"
    text = fold(raw)

    if event.category == "autre":
        for cat, pattern in CATEGORY_RULES:
            if re.search(pattern, text):
                event.category = cat
                break

    if not event.audience:
        event.audience = [aud for aud, pattern in AUDIENCE_RULES if re.search(pattern, text)]
        if event.category == "jeunesse" and "famille" not in event.audience:
            event.audience.append("famille")

    if event.free is None:
        price = PRICE_RE.search(raw)  # sur le texte brut : le repli ASCII supprime «€»
        if FREE_RE.search(text):
            event.free = True
        elif price:
            event.free = False
            if not event.price_text:
                event.price_text = f"{price.group(1)} €"

    return event
