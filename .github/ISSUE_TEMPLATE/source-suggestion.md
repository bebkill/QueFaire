---
name: Proposer une source
about: Suggérer un site/agenda d'événements locaux à ajouter au crawler
title: "Source : "
labels: source-suggestion
---

Merci de proposer une source d'événements locaux ! Un éditeur la validera
(label `approved`) avant qu'elle soit ajoutée au crawler.

Complétez le bloc ci-dessous en gardant la syntaxe ```yaml``` (c'est lui qui
est lu automatiquement à l'approbation) :

```yaml
- id: html-ma-source        # identifiant court et unique (préfixe html-/rss-/ical-/oa-)
  name: Nom lisible de la source
  type: html                # html | rss | ical | openagenda
  url: https://exemple.fr/agenda
  commune: Grenoble         # commune par défaut (facultatif)
```

- **type `html`** : page agenda classique (les événements sont extraits par IA).
- **type `rss`** : URL d'un flux RSS/Atom.
- **type `ical`** : URL d'un fichier `.ics`.
- **type `openagenda`** : identifiant (UID) de l'agenda OpenAgenda.

Les URLs sont contrôlées automatiquement (http/https, pas d'adresses internes).
