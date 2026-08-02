# Architecture — QueFaire

Deux moitiés indépendantes reliées par des fichiers JSON :

```
pipeline (Python)  ──export──▶  site/src/data/  ──build──▶  site (Astro, statique)
```

Le pipeline tourne 2×/jour en CI (`.github/workflows/refresh.yml`), committe
les JSON s'ils ont changé, puis le site est rebuilt et déployé sur GitHub
Pages. Aucun serveur, aucune base de données.

## Le modèle « épicentre »

Un secteur est une **commune de référence** (`center_lat` / `center_lon`) + un
**rayon en temps de trajet** (`radius_minutes`, ~1 h ≈ 48 km), déclarés dans le
bloc `sector:` du registre. On ne raisonne donc pas par département : un
événement est retenu s'il tombe dans le rayon, quel que soit le sien (le
nord-Isère est plus proche de Lyon et de l'Ain que du sud-Isère).

Ajouter une ville = ajouter `sources/<commune>.yaml` +
`data/communes_<commune>.csv` (ce dernier régénérable par `build-geo`). Tout le
reste — crawl, routes du site, portail — s'adapte automatiquement.

## Pipeline (`pipeline/`)

### Registre de sources

`sources/<commune>.yaml` est LE point d'entrée d'un secteur : une liste de
sources typées (`rss`, `ical`, `openagenda`, `html`), chacune avec
`enabled: true|false`. Les sources désactivées sont ignorées ; les outils de
découverte génèrent toujours `enabled: false` pour forcer une relecture
humaine.

### Étapes du crawl (`python -m quefaire crawl --sector <ville>`)

1. **Collecte** (`fetchers/`) — un fetcher par type de source :
   - `rss.py` / `ical.py` : flux structurés, parseurs internes ;
   - `openagenda.py` : API OpenAgenda (`OPENAGENDA_KEY`) ;
   - `html_llm.py` : pages agenda sans flux — le texte de la page est envoyé à
     un agent LLM qui en extrait les événements en JSON.
2. **Normalisation** (`normalize.py`) — catégorie, public visé et gratuité
   déduits par des règles lisibles (regex/mots-clés), pas de LLM.
3. **Géocodage** (`geocode.py`) — commune → lat/lon via
   `data/communes_<secteur>.csv`, entièrement hors-ligne. Une commune absente
   du CSV donne un événement sans coordonnées (le front le gère). Les
   événements OpenAgenda portent déjà leurs coordonnées : le CSV n'est qu'un
   secours pour les sources qui ne donnent qu'un nom de commune.
4. **Filtre de rayon** (`geo.py`) — on écarte les événements à plus de
   `radius_minutes` de l'épicentre. Le calcul (haversine + `travel_minutes`)
   reproduit **à l'identique** celui du front (`nlsearch.js`), pour que
   collecte et affichage s'accordent ; un événement sans coordonnées est
   conservé (on ne peut pas le situer). La table de communes du rayon se
   (re)génère avec `build-geo` (`geodata.py`, réseau requis, hors du crawl —
   `geo.api.gouv.fr`).
5. **Déduplication** (`dedupe.py`) — un événement relayé par N sources ne sort
   qu'une fois ; on garde la fiche la plus riche.
6. **Clarification** (`clarify.py`, optionnelle) — une phrase LLM « en clair »
   (`tldr`) lève l'ambiguïté des titres obscurs, uniquement pour ceux jugés
   ambigus, et un filtre anti-paraphrase écarte ce qui ne fait que recopier le
   titre.
7. **Export** (`export.py`) — écrit par ville :
   - `site/src/data/cities/<ville>/events.json` — les fiches ;
   - `site/src/data/cities/<ville>/sector.json` — métadonnées (communes,
     catégories, centre, `radius_minutes`, compteurs) ;
   - `site/src/data/cities.json` — annuaire des villes, **fusionné** entre
     crawls, qui alimente le portail.

### Cloisonnement par ville

Le **cache LLM** (`cache.py`) et l'**état de fraîcheur des sources**
(`health.py`) sont partitionnés par ville — `pipeline/cache/<ville>/` — via
`bind(sector_id)` appelé en début de crawl.

C'est indispensable : le cache est adressé par **hash du contenu d'entrée** et
n'est conservé qu'à la clé vue pendant le run (élagage automatique). Sans
cloisonnement, le crawl d'une ville évincerait le cache de toutes les autres,
et chaque run rappellerait le LLM pour rien.

Le cache donne trois choses : **répétabilité** (une page inchangée rend le même
résultat), **économie de quota** et **résilience** si le quota meurt en cours
de run.

### LLM : deux chaînes indépendantes (`llm.py`)

| Chaîne | Variables | Usage |
|---|---|---|
| CRAWL | `QUEFAIRE_LLM` + `QUEFAIRE_LLM2` | extraction `html`, `discover` |
| CLARIFY | `QUEFAIRE_LLM_CLARIFY` + `…2` | `clarify` uniquement |

Format `provider:modèle` ; chaque variable accepte une **liste séparée par des
virgules** pour empiler plusieurs backups. Donner un modèle dédié à clarify lui
offre son propre quota (ex. crawl sur DeepSeek, clarify sur Mistral) ; sans
lui, clarify réutilise la chaîne du crawl et se saute si celle-ci a déjà
basculé.

Un test de connexion minimal départage au premier appel de chaque chaîne, et la
décision est mise en cache. Le quota peut aussi mourir **en cours de run** : les
appels passent par `run_llm()`, qui distingue trois cas —

- **quota** (429, rate limit) → déclasse le provider pour tout le run ;
- **erreur serveur transitoire** (5xx, surcharge, timeout) → rejoue sur les
  backups **pour cet appel seulement**, sans déclasser ;
- **réponse vide** (fréquent sur les grosses pages) → idem, secours ponctuel.

`get_agent()` reste réservé à `discovery`, qui a besoin d'outils
(`@agent.tool`). Les agents sont créés à `temperature=0` pour la répétabilité :
la stabilité dépend surtout du provider **principal**, mieux vaut donc un
provider payant/stable en tête et reléguer les paliers gratuits en backup.

Lib : `autoagent-core`. Providers natifs : OpenAI, Anthropic, DeepSeek, Gemini,
Groq. Providers OpenAI-compatibles via `base_url` (`_OPENAI_COMPATIBLE`) :
Mistral, z.ai, Kimi/Moonshot.

### Dégradation gracieuse

Principe transverse : une intégration optionnelle indisponible (clé absente,
quota épuisé, réseau) produit un warning et un skip, jamais un crash. Sans
aucune source activée, le pipeline exporte le jeu de démo (`demo.py`) pour ne
jamais publier un site vide.

### Cycle de vie des sources

- `discover-oa` — interroge l'API OpenAgenda pour toutes les communes du
  secteur, déduplique par UID, classe les agendas officiels en premier.
- `discover` — agent LLM avec un outil `fetch_page` qui visite les sites
  communaux et propose flux RSS/iCal/pages agenda en YAML prêt à coller.
- `evaluate-source` — évalue une URL candidate et n'en retient que les
  événements **uniques** (dédupliqués contre le dataset publié). Brique commune
  à la découverte automatique et aux propositions du site. Les URL tierces
  passent par les garde-fous anti-SSRF de `security.py`.
- `health.py` — désactive automatiquement une source sans événement depuis plus
  de 30 jours (retrait des sources abandonnées, réversible).
- `registry.py` — édition du registre **ligne à ligne** plutôt que par
  round-trip YAML, pour préserver les commentaires (le fichier est très annoté).

## Site (`site/`)

Astro, généré statiquement, multi-villes.

| Route | Page | Rôle |
|---|---|---|
| `/` | `index.astro` | redirige vers le portail |
| `/villes/` | `villes.astro` | portail : carte des épicentres, « me localiser », recherche |
| `/<ville>/` | `[city]/index.astro` | agenda : recherche NL, filtres, carte, grille |
| `/<ville>/evenement/<id>/` | `[city]/evenement/[id].astro` | fiche détail |
| `/<ville>/a-propos/`, `/<ville>/proposer/` | — | à-propos, proposer une source |

`lib/sectors.js` énumère les villes crawlées via `import.meta.glob` sur
`data/cities/*/`, ce qui alimente les `getStaticPaths()` des routes `[city]`.
Ajouter une ville ne demande donc **aucune modification du site**.

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
commune ; l'affichage est arrondi honnêtement (`roundMinutes`). Les événements
**sans coordonnées** sont exclus du filtre temps (et de la carte) — ils
réapparaissent dès que le filtre est levé, avec un compteur explicite.

### Cartes (Leaflet)

Chargées à la demande par import dynamique. Deux contraintes apprises :

- **pas de `define:vars`** sur un `<script>` qui importe Leaflet : la directive
  force le script en `is:inline`, donc non bundlé, et le spécificateur nu
  `leaflet` n'est plus résolu. Les données transitent par un blob
  `<script type="application/json">` lu depuis le DOM ;
- **`L.circleMarker` plutôt que `L.marker`** : le marqueur par défaut charge
  une icône PNG dont le chemin, une fois bundlé, se résout en `file:///` et
  déclenche une erreur de sécurité. `circleMarker` est du SVG, sans image.

Sur l'agenda, un clustering maison par proximité écran (~70 px) regroupe les
bulles ; un clic met la fiche correspondante en évidence dans la grille. Liste
et carte affichent toujours exactement le même ensemble d'événements.

### Portail (`villes.astro`)

Carte des épicentres actifs, recherche par nom, et « me localiser » qui place
le point de l'utilisateur et cadre la vue sur lui + la ville la plus proche.
La géolocalisation est demandée en `enableHighAccuracy` ; sur un poste sans
GPS, le navigateur retombe sur l'IP et situe l'utilisateur au nœud régional de
son fournisseur — au-delà de 20 km de marge annoncée, le portail affiche un
avertissement et renvoie vers la recherche.

## Automatisation (`.github/workflows/`)

`refresh.yml` — cron 2×/jour, déclenchement manuel, push sur `main` : install →
tests → **boucle sur `sectors --active`** (un crawl par ville) → commit des JSON
si changés → build Astro unique (toutes les villes) → déploiement Pages. Le
commit des données passe par une deploy key en écriture (`DATA_DEPLOY_KEY`),
seule façon de franchir le ruleset de protection de `main` sur un compte perso.

`discover.yml` (hebdo) ouvre une issue par source candidate ;
`apply-source.yml` applique celles labellisées `approved` ;
`close-suggestions.yml` ferme les suggestions en lot (manuel, protégé).

Les fichiers `site/src/data/**` et `pipeline/cache/**` appartiennent donc au bot
CI : **ne pas committer le résultat d'un crawl local**.

## Pistes écartées

**Réseaux sociaux (Facebook, Instagram).** Beaucoup de petites communes
n'annoncent leurs événements que là. La voie a été implémentée puis retirée :

- Meta n'offre pas d'API publique de lecture des pages qu'on n'administre pas
  (la Graph API exige une app validée + « Page Public Content Access »), et le
  scraping direct est bloqué et contraire aux CGU ;
- le contournement praticable, une instance RSS-Bridge, tourne en CI sur une
  **IP de datacenter que Meta bloque** par un mur de consentement : les pages ne
  renvoient aucun post exploitable. Une instance auto-hébergée sur IP
  résidentielle fonctionnerait, mais impose une infrastructure permanente hors
  du modèle « tout en CI, zéro serveur » du projet ;
- même alimenté, un post n'est pas un événement structuré : il faut un passage
  LLM supplémentaire, pour un taux de faux positifs élevé.

Conclusion : coût d'exploitation réel, fiabilité nulle en CI, qualité douteuse.
Les agendas officiels (RSS, iCal, OpenAgenda, pages HTML) couvrent le besoin
avec une bien meilleure précision. Si le sujet revient, la seule voie sérieuse
est l'Instagram Hashtag Search API (compte pro + App Review), pas le scraping.
