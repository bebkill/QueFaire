# Architecture — QueFaire

Deux moitiés indépendantes reliées par deux fichiers JSON :

```
pipeline (Python)  ──export──▶  site/src/data/{events,sector}.json  ──build──▶  site (Astro, statique)
```

Le pipeline tourne 2×/jour en CI (`.github/workflows/refresh.yml`), committe
les JSON s'ils ont changé, puis le site est rebuilt et déployé sur GitHub
Pages. Aucun serveur, aucune base de données.

## Pipeline (`pipeline/`)

### Registre de sources

`sources/isere.yaml` est LE point d'entrée d'un secteur : une liste de sources
typées (`rss`, `ical`, `openagenda`, `html`, `facebook`, `instagram`), chacune
avec `enabled: true|false`. Les sources désactivées sont ignorées ; les outils
de découverte génèrent toujours `enabled: false` pour forcer une relecture
humaine. Ajouter une région = ajouter `sources/<secteur>.yaml` +
`data/communes_<secteur>.csv`.

### Étapes du crawl (`python -m quefaire crawl --sector isere`)

1. **Collecte** (`fetchers/`) — un fetcher par type de source :
   - `rss.py` / `ical.py` : flux structurés, parseurs internes ;
   - `openagenda.py` : API OpenAgenda (`OPENAGENDA_KEY`) ;
   - `html_llm.py` : pages agenda sans flux — le texte de la page est envoyé à
     un agent LLM qui en extrait les événements en JSON ;
   - `social.py` : pages Facebook/Instagram via une instance RSS-Bridge
     (`RSSBRIDGE_URL` ; le workflow CI en lance une éphémère par défaut, mais
     l'IP datacenter du runner est bloquée par Meta — voir
     `docs/RESEAUX-SOCIAUX.md`), posts transformés en événements par le LLM.
2. **Normalisation** (`normalize.py`) — catégorie, public visé et gratuité
   déduits par des règles lisibles (regex/mots-clés), pas de LLM.
3. **Géocodage** (`geocode.py`) — commune → lat/lon via
   `data/communes_<secteur>.csv`, entièrement hors-ligne. Une commune absente
   du CSV donne un événement sans coordonnées (le front le gère).
4. **Déduplication** (`dedupe.py`) — un événement relayé par N sources ne sort
   qu'une fois ; on garde la fiche la plus riche.
5. **Clarification** (`clarify.py`, optionnelle) — une phrase LLM « en clair »
   (`tldr`) lève l'ambiguïté des titres obscurs, par lots pour limiter les
   appels.
6. **Export** (`export.py`) — `events.json` (les fiches) et `sector.json`
   (métadonnées : communes, catégories, centre, compteurs).

### LLM principal + backup (`llm.py`)

`QUEFAIRE_LLM` (principal) et `QUEFAIRE_LLM2` (backup), format
`provider:modèle`. Au premier appel du run, un test de connexion minimal
départage ; la décision est mise en cache pour tout le process. Le quota peut
aussi s'épuiser en cours de run (palier gratuit Gemini : 20 requêtes/jour) :
les appels passent par `run_llm()`, qui déclasse le provider courant sur une
erreur de quota (429, rate limit…) et rejoue l'appel sur le candidat suivant.
Les consommateurs (html, social, clarify) sautent proprement si plus aucun
provider ne répond ; `discovery` utilise `get_agent()` (outils `@agent.tool`),
sans bascule en cours d'exécution. Lib : `autoagent-core` (OpenAI, Anthropic,
DeepSeek, Gemini).

### Dégradation gracieuse

Principe transverse : une intégration optionnelle indisponible (clé absente,
quota épuisé, réseau) produit un warning et un skip, jamais un crash. Sans
aucune source activée, le pipeline exporte le jeu de démo (`demo.py`) pour ne
jamais publier un site vide.

### Découverte de sources

- `discover-oa` : interroge l'API OpenAgenda pour toutes les communes du
  secteur, déduplique par UID, classe les agendas officiels en premier.
- `discover` : agent LLM avec un outil `fetch_page` qui visite les sites
  communaux et propose flux RSS/iCal/pages agenda en YAML prêt à coller.

## Site (`site/`)

Astro, généré statiquement. Pages : `index.astro` (recherche + filtres +
carte + grille), `evenement/[id].astro` (détail, une page par événement au
build), `a-propos.astro`.

### Recherche en langage naturel (`lib/nlsearch.js`)

`parseQuery()` transforme une requête libre FR en filtre structuré, côté
client, zéro dépendance : dates relatives (« ce week-end », « demain »…),
catégories et synonymes, public, gratuité, communes du secteur, « près de
moi », « à moins de X min à pied/vélo/voiture » ; le reste devient du plein
texte. `matches()` teste chaque carte (attributs `data-*` posés par
`EventCard.astro`) contre le filtre.

### Temps de trajet

`travelMinutes()` : approximation à vol d'oiseau corrigé — coefficient de
détour par mode et vitesse voiture progressive (urbain lent, route au-delà).
La précision est de toute façon bornée par le géocodage au centre de la
commune ; l'affichage est arrondi honnêtement (`roundMinutes`). Un vrai moteur
isochrone est en roadmap. Les événements **sans coordonnées** sont exclus du
filtre temps (et de la carte) — ils réapparaissent dès que le filtre est levé,
avec un compteur explicite dans la barre d'outils.

### Carte (Leaflet)

Chargée à la demande (import dynamique au clic sur « 🗺️ Carte »). Clustering
maison par proximité écran (~70 px), pastilles cliquables : zoom sur le
cluster, ou liste de liens si tout est au même endroit (géocodage à la
commune). Un clic sur la bulle d'un événement met la fiche correspondante en
évidence dans la grille (anneau + bandeau « Sélectionnée sur la carte ») et
la fait défiler à l'écran. La liste et la carte affichent exactement le même
ensemble d'événements.

## Automatisation (`.github/workflows/refresh.yml`)

Cron 2×/jour + déclenchement manuel + push sur `main` : install → tests →
crawl (avec les secrets/variables du dépôt) → commit des JSON si changés →
build Astro → déploiement GitHub Pages. Les fichiers
`site/src/data/*.json` appartiennent donc au bot CI — ne pas committer le
résultat d'un crawl local (voir `CLAUDE.md`).
