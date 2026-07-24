# Roadmap — QueFaire

## Fait

- [x] MVP secteur Isère : pipeline (RSS, iCal, OpenAgenda, HTML+LLM) + site
      statique Astro avec recherche en langage naturel
- [x] Automatisation CI : crawl 2×/jour, commit des données, build, GitHub Pages
- [x] Premières sources réelles Isère (OpenAgenda + extraction LLM des pages agenda)
- [x] Outils de découverte de sources : `discover-oa` (API OpenAgenda) et
      `discover` (agent LLM), validation humaine avant activation
- [x] Carte des événements (Leaflet/OSM) synchronisée avec les filtres, avec
      clustering et mise en évidence de la fiche au clic sur une bulle
- [x] Filtre temps de trajet « à moins de X min » (à pied / vélo / voiture), y
      compris en langage naturel — approximation à vol d'oiseau corrigé,
      cohérente entre liste et carte (les événements non localisés sont
      masqués et comptés)
- [x] Fiches « en clair » : une phrase LLM lève l'ambiguïté des titres
      (ex : « Faites-vous une terrasse » = dîners en terrasse, pas du bricolage)
- [x] LLM principal + backup (`QUEFAIRE_LLM` / `QUEFAIRE_LLM2`) avec bascule
      automatique quand le quota du principal est épuisé
- [x] Cycle de vie des sources : évaluateur d'URL (événements uniques,
      garde-fous anti-SSRF), découverte automatique hebdo → issues de
      suggestion (`discover.yml`), module « proposer une source » sur le site →
      issue GitHub pré-remplie, validation éditeur (label `approved` →
      `apply-source.yml`), et retrait auto des sources sans événement depuis
      > 1 mois (`health.py`)

## Ensuite

### Court terme

- [ ] Vrai moteur isochrone (OpenRouteService ou Valhalla) à la place de
      l'approximation à vol d'oiseau
- [ ] Élargir la couverture Isère : offices de tourisme Vercors / Oisans /
      Chartreuse
- [ ] Réseaux sociaux : pilote hashtag Instagram (`#quefaire38`) via la
      Hashtag Search API — compte pro QueFaire + App Review « Instagram
      Public Content Access », modération humaine des événements collectés ;
      voir `docs/RESEAUX-SOCIAUX.md` (RSS-Bridge relégué à l'expérimentation)
- [ ] Soumission directe d'événements : formulaire sur le site et/ou adresse
      mail (affiche → extraction LLM), avec file de modération

### Moyen terme

- [ ] Retours utilisateurs sur les événements (👍/👎, note) pour apprendre les
      goûts et afficher un **score de match** personnalisé, affiné au fil de l'eau
- [ ] Sources activités outdoor : **Décathlon Outdoor, Visorando, Wikiloc**
      (balades et itinéraires — pas des événements datés : premier cas d'usage
      du schéma `Place`, avec notes et avis existants pour « bien noté »)
- [ ] Nouveaux secteurs : `sources/<secteur>.yaml` + `data/communes_<secteur>.csv`

### Long terme

- [ ] Compte utilisateur : préférences, contributions, activités réalisées
- [ ] Extension aux professionnels et commerçants (« je cherche un électricien »,
      « un tailleur de pierre ») : même pipeline, schéma `Place`, même recherche
